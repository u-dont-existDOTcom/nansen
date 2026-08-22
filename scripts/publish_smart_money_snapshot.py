#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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

    load_dotenv()
    client = NansenClient()

    snapshot = {
        "status": "live",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Nansen token-screener; Smart Money only",
        "chains": DEFAULT_CHAINS,
        "method": {
            "purpose": "sell-watch fallback when ChatGPT Nansen connector is unavailable",
            "filters": {"min_token_age_days": 3, "min_market_cap_usd": 1_000_000},
            "cache_mode": "refresh" if args.refresh else "cache-first/resumable",
            "note": "This file intentionally contains no API key and no raw cache responses.",
        },
        "distribution": {},
        "accumulation_24h": [],
    }

    for timeframe in DEFAULT_TIMEFRAMES:
        snapshot["distribution"][timeframe] = screener(
            client, timeframe, "ASC", refresh=args.refresh
        )
    snapshot["accumulation_24h"] = screener(
        client, "24h", "DESC", limit=15, refresh=args.refresh
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {output}")
    print("Snapshot requests: 4 (served from cache when available unless --refresh is used)")

    if args.push:
        publish(output, args.remote, args.branch)


if __name__ == "__main__":
    main()
