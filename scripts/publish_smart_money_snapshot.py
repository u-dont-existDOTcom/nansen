#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from nansen_signal_lab.client import NansenClient
from nansen_signal_lab.cli import normalize_price_change

DEFAULT_CHAINS = ["solana", "ethereum", "base", "bnb", "arbitrum"]
DEFAULT_TIMEFRAMES = ["24h", "7d", "30d"]


def screener(
    client: NansenClient,
    timeframe: str,
    direction: str,
    limit: int = 25,
    *,
    refresh: bool = False,
):
    payload = {
        "chains": DEFAULT_CHAINS,
        "timeframe": timeframe,
        "pagination": {"page": 1, "per_page": limit},
        "filters": {
            "only_smart_money": True,
            "token_age_days": {"min": 3},
            "market_cap_usd": {"min": 1_000_000},
        },
        "order_by": [{"field": "netflow", "direction": direction}],
    }
    body = client.post("token-screener", payload, refresh=refresh)
    rows = []
    for r in body.get("data", []):
        rows.append(
            {
                "chain": r.get("chain"),
                "symbol": r.get("token_symbol"),
                "token_address": r.get("token_address"),
                "price_usd": r.get("price_usd"),
                "price_change_pct": normalize_price_change(r.get("price_change")),
                "netflow_usd": r.get("netflow"),
                "market_cap_usd": r.get("market_cap_usd"),
                "volume_usd": r.get("volume"),
                "liquidity_usd": r.get("liquidity"),
                "token_age_days": r.get("token_age_days"),
            }
        )
    return rows


def safe_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    # Never publish environment-style secrets if an upstream error unexpectedly echoes them.
    text = re.sub(r"(?i)(NANSEN_API_KEY\s*[=:]\s*)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(apikey\s*[=:]\s*)\S+", r"\1[REDACTED]", text)
    return f"{type(exc).__name__}: {text[:800]}"


def git(*args: str, check: bool = True):
    return subprocess.run(["git", *args], check=check, text=True, capture_output=True)


def publish(path: Path, remote: str, branch: str):
    git("add", str(path))
    diff = git("diff", "--cached", "--quiet", check=False)
    if diff.returncode == 0:
        print("Snapshot unchanged; nothing to commit.")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    git("commit", "-m", f"Update Smart Money snapshot ({stamp})")
    git("push", remote, f"HEAD:{branch}")
    print(f"Published {path} to {remote}/{branch}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="signals/latest-market.json")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="deliberately bypass the request cache and make fresh paid/current Nansen calls",
    )
    args = parser.parse_args()

    generated_at = datetime.now(timezone.utc).isoformat()
    snapshot = {
        "status": "diagnostic",
        "generated_at": generated_at,
        "source": "Nansen token-screener; Smart Money only",
        "chains": DEFAULT_CHAINS,
        "method": {
            "purpose": "sell-watch fallback when ChatGPT Nansen connector is unavailable",
            "filters": {"min_token_age_days": 3, "min_market_cap_usd": 1_000_000},
            "cache_mode": "refresh" if args.refresh else "cache-first/resumable",
            "note": "This file intentionally contains no API key and no raw cache responses.",
        },
        "requests": {},
        "distribution": {},
        "accumulation_24h": [],
    }

    load_dotenv()
    client = None
    try:
        client = NansenClient()
        snapshot["requests"]["client"] = {"ok": True}
    except Exception as exc:
        snapshot["requests"]["client"] = {"ok": False, "error": safe_error(exc)}

    if client is not None:
        for timeframe in DEFAULT_TIMEFRAMES:
            key = f"distribution_{timeframe}"
            try:
                rows = screener(client, timeframe, "ASC", refresh=args.refresh)
                snapshot["distribution"][timeframe] = rows
                snapshot["requests"][key] = {"ok": True, "rows": len(rows)}
            except Exception as exc:
                snapshot["distribution"][timeframe] = []
                snapshot["requests"][key] = {"ok": False, "error": safe_error(exc)}

        try:
            rows = screener(client, "24h", "DESC", limit=15, refresh=args.refresh)
            snapshot["accumulation_24h"] = rows
            snapshot["requests"]["accumulation_24h"] = {"ok": True, "rows": len(rows)}
        except Exception as exc:
            snapshot["requests"]["accumulation_24h"] = {"ok": False, "error": safe_error(exc)}

    request_states = [v.get("ok") for k, v in snapshot["requests"].items() if k != "client"]
    if client is None or not request_states or not any(request_states):
        snapshot["status"] = "error"
    elif all(request_states):
        snapshot["status"] = "live"
    else:
        snapshot["status"] = "partial"

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {output}; status={snapshot['status']}")
    for name, state in snapshot["requests"].items():
        if state.get("ok"):
            suffix = f" rows={state['rows']}" if "rows" in state else ""
            print(f"  OK   {name}{suffix}")
        else:
            print(f"  FAIL {name}: {state.get('error')}")

    # Publish even an error/partial diagnostic snapshot so remote inspection can diagnose
    # the local worker without exposing the API key.
    if args.push:
        publish(output, args.remote, args.branch)


if __name__ == "__main__":
    main()
