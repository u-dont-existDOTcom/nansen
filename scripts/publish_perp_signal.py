#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from nansen_signal_lab.client import NansenClient


def utc_text(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    text = re.sub(r"(?i)(NANSEN_API_KEY\s*[=:]\s*)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(apikey\s*[=:]\s*)\S+", r"\1[REDACTED]", text)
    return f"{type(exc).__name__}: {text[:800]}"


def git(*args: str, check: bool = True):
    return subprocess.run(["git", *args], check=check, text=True, capture_output=True)


def publish(path: Path, remote: str, branch: str):
    git("add", str(path))
    diff = git("diff", "--cached", "--quiet", check=False)
    if diff.returncode == 0:
        print("Signal unchanged; nothing to commit.")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    git("commit", "-m", f"Update tracked perp signal {path.stem} ({stamp})")
    git("push", remote, f"HEAD:{branch}")
    print(f"Published {path} to {remote}/{branch}")


def perp_screener(client: NansenClient, symbol: str, hours: int, *, refresh: bool):
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    payload = {
        "date": {"from": utc_text(start), "to": utc_text(end)},
        "pagination": {"page": 1, "per_page": 10},
        "filters": {
            "token_symbol": symbol,
            "only_smart_money": True,
        },
        "order_by": [{"field": "net_position_change", "direction": "DESC"}],
    }
    response = client.post_with_provenance("perp-screener", payload, refresh=refresh)
    body = response.body
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ValueError("perp-screener response data must be a list")
    return {
        "response_retrieved_at": response.response_retrieved_at,
        "cache_hit": response.cache_hit,
        "rows": body.get("data", []),
        "pagination": body.get("pagination"),
    }


def recent_trades(client: NansenClient, symbol: str, *, refresh: bool):
    payload = {
        "filters": {"token_symbol": symbol},
        "only_new_positions": False,
        "pagination": {"page": 1, "per_page": 50},
        "order_by": [{"field": "block_timestamp", "direction": "DESC"}],
    }
    response = client.post_with_provenance("smart-money/perp-trades", payload, refresh=refresh)
    body = response.body
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ValueError("perp-trades response data must be a list")
    return {
        "response_retrieved_at": response.response_retrieved_at,
        "cache_hit": response.cache_hit,
        "rows": body.get("data", []),
        "pagination": body.get("pagination"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("--output")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--include-trades", action="store_true")
    args = parser.parse_args()

    symbol = args.symbol.strip().upper()
    if not symbol or not re.fullmatch(r"[A-Z0-9._-]{1,20}", symbol):
        raise ValueError("symbol must be a simple uppercase perp symbol")

    # Resolve the credential from the operator's current repository checkout,
    # even when this script itself is executed from /tmp or another location.
    env_path = Path.cwd() / ".env"
    load_dotenv(dotenv_path=env_path)
    client = NansenClient()
    output = Path(args.output or f"signals/perps/{symbol}.json")
    snapshot = {
        "status": "diagnostic",
        "symbol": symbol,
        "generated_at": utc_text(datetime.now(timezone.utc)),
        "source": "Nansen Hyperliquid Smart Money perp-screener",
        "note": "No API key or raw request headers are stored in this file.",
        "requests": {},
        "periods": {},
        "recent_trades": None,
    }

    for label, hours in (("24h", 24), ("7d", 24 * 7)):
        try:
            result = perp_screener(client, symbol, hours, refresh=args.refresh)
            snapshot["periods"][label] = result
            snapshot["requests"][f"perp_screener_{label}"] = {
                "ok": True,
                "rows": len(result["rows"]),
                "cache_hit": result["cache_hit"],
            }
        except Exception as exc:
            snapshot["periods"][label] = None
            snapshot["requests"][f"perp_screener_{label}"] = {"ok": False, "error": safe_error(exc)}

    if args.include_trades:
        try:
            result = recent_trades(client, symbol, refresh=args.refresh)
            snapshot["recent_trades"] = result
            snapshot["requests"]["recent_trades"] = {
                "ok": True,
                "rows": len(result["rows"]),
                "cache_hit": result["cache_hit"],
            }
        except Exception as exc:
            snapshot["requests"]["recent_trades"] = {"ok": False, "error": safe_error(exc)}

    states = [state.get("ok") for state in snapshot["requests"].values()]
    snapshot["status"] = "live" if states and all(states) else ("partial" if any(states) else "error")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {output}; status={snapshot['status']}")
    for name, state in snapshot["requests"].items():
        if state.get("ok"):
            print(f"  OK   {name}: rows={state.get('rows')} cache_hit={state.get('cache_hit')}")
        else:
            print(f"  FAIL {name}: {state.get('error')}")

    if args.push:
        publish(output, args.remote, args.branch)


if __name__ == "__main__":
    main()
