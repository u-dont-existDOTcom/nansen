from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from .client import NansenClient
from .experiment import analyze_manifest
from .metrics import accumulation_class, flow_market_cap_ratio

DEFAULT_CHAINS = ["solana", "ethereum", "base", "bnb", "arbitrum"]


def normalize_price_change(x):
    if x is None:
        return None
    # Nansen Token Screener returns price_change as a decimal return:
    # 0.167... means +16.7%, 2.163... means +216.3%.
    return float(x) * 100.0


def cmd_smoke(args):
    c = NansenClient()
    payload = {
        "chains": [args.chain],
        "timeframe": "24h",
        "pagination": {"page": 1, "per_page": 5},
        "filters": {"only_smart_money": True, "token_age_days": {"min": 1}},
        "order_by": [{"field": "volume", "direction": "DESC"}],
    }
    body = c.post("token-screener", payload, refresh=args.refresh)
    rows = body.get("data", [])
    print(f"API OK: received {len(rows)} rows")
    for row in rows[:5]:
        print(json.dumps(row, ensure_ascii=False))


def cmd_candidates(args):
    c = NansenClient()
    payload = {
        "chains": args.chains,
        "timeframe": "24h",
        "pagination": {"page": 1, "per_page": args.limit},
        "filters": {
            "only_smart_money": True,
            "token_age_days": {"min": args.min_age_days},
            "market_cap_usd": {"min": args.min_market_cap},
        },
        "order_by": [{"field": "netflow", "direction": "DESC"}],
    }
    body = c.post("token-screener", payload, refresh=args.refresh)
    out = []
    for r in body.get("data", []):
        price_pct = normalize_price_change(r.get("price_change"))
        ratio = flow_market_cap_ratio(r.get("netflow"), r.get("market_cap_usd"))
        out.append({
            "chain": r.get("chain"),
            "token_symbol": r.get("token_symbol"),
            "token_address": r.get("token_address"),
            "price_usd": r.get("price_usd"),
            "price_change_pct": price_pct,
            "netflow_usd": r.get("netflow"),
            "market_cap_usd": r.get("market_cap_usd"),
            "flow_mcap_ratio": ratio,
            "bucket": accumulation_class(price_pct),
            "volume_usd": r.get("volume"),
            "liquidity_usd": r.get("liquidity"),
            "token_age_days": r.get("token_age_days"),
        })
    df = pd.DataFrame(out)
    Path("results").mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path("results") / f"candidates-{stamp}.csv"
    df.to_csv(path, index=False)
    if len(df):
        print(df.to_string(index=False))
    print(f"\nSaved: {path}")


def cmd_flows(args):
    c = NansenClient()
    end = datetime.now(timezone.utc) if args.to is None else datetime.fromisoformat(args.to.replace("Z", "+00:00"))
    start = end - timedelta(days=args.days) if args.from_ is None else datetime.fromisoformat(args.from_.replace("Z", "+00:00"))
    payload = {
        "chain": args.chain,
        "token_address": args.token,
        "date": {"from": start.isoformat().replace("+00:00", "Z"), "to": end.isoformat().replace("+00:00", "Z")},
        "label": "smart_money",
        "pagination": {"page": 1, "per_page": args.limit},
        "order_by": [{"field": "date", "direction": "ASC"}],
    }
    body = c.post("tgm/flows", payload, refresh=args.refresh)
    rows = body.get("data", [])
    Path("results").mkdir(exist_ok=True)
    safe = args.token[:12].replace("/", "_")
    path = Path("results") / f"flows-{args.chain}-{safe}.json"
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False))
    print(f"received {len(rows)} flow snapshots; saved {path}")
    for row in rows[:10]:
        print(json.dumps(row, ensure_ascii=False))


def cmd_plan(args):
    calls = 1 + args.tokens
    print("Pilot call budget (conservative estimate):")
    print(f"  1 token-screener discovery call")
    print(f"  {args.tokens} TGM /flows calls (one per selected token)")
    print(f"  ~= {calls} Pro credits at current documented 1-credit/call pricing")
    print("No API calls were made.")


def cmd_analyze(args):
    paths = analyze_manifest(args.manifest, check=args.check)
    prefix = "verified: " if args.check else ""
    for path in paths:
        print(f"{prefix}{path}")


def build_parser():
    p = argparse.ArgumentParser(prog="nansen-lab")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("smoke", help="one cheap API/authentication test")
    s.add_argument("--chain", default="solana")
    s.add_argument("--refresh", action="store_true")
    s.set_defaults(func=cmd_smoke)

    s = sub.add_parser("candidates", help="live Smart-Money token-screening candidates")
    s.add_argument("--chains", nargs="+", default=DEFAULT_CHAINS)
    s.add_argument("--limit", type=int, default=50)
    s.add_argument("--min-market-cap", type=float, default=1_000_000)
    s.add_argument("--min-age-days", type=int, default=3)
    s.add_argument("--refresh", action="store_true")
    s.set_defaults(func=cmd_candidates)

    s = sub.add_parser("flows", help="historical Smart-Money flow snapshots for one token")
    s.add_argument("--chain", required=True)
    s.add_argument("--token", required=True)
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--from", dest="from_")
    s.add_argument("--to")
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--refresh", action="store_true")
    s.set_defaults(func=cmd_flows)

    s = sub.add_parser("plan", help="show call/credit budget without calling Nansen")
    s.add_argument("--tokens", type=int, default=10)
    s.set_defaults(func=cmd_plan)

    s = sub.add_parser("analyze", help="generate or verify a committed research bundle")
    s.add_argument("--manifest", required=True)
    s.add_argument("--check", action="store_true")
    s.set_defaults(func=cmd_analyze)
    return p


def main():
    load_dotenv()
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
