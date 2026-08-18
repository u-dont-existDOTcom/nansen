from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from .artifacts import _flow_artifact_paths, write_api_artifacts, write_flow_artifacts
from .client import NansenClient
from .openai_client import OpenAIClient
from .prospective_runner import (
    check_pilot,
    initialize_model_successor,
    initialize_pilot,
    replay_pilot,
    settle_pilot,
    start_pilot,
)
from .prospective_schema import load_prospective_manifest
from .evaluation import evaluate_manifest
from .experiment import analyze_manifest
from .metrics import accumulation_class, flow_market_cap_ratio
from .historical_discovery import (
    DESIGN_PATH as HISTORICAL_DESIGN_PATH,
    MAX_CALLS as HISTORICAL_MAX_CALLS,
    MAX_CREDITS as HISTORICAL_MAX_CREDITS,
    check_historical_discovery,
    initialize_historical_discovery,
    load_historical_manifest,
    start_historical_discovery,
)
from .historical_recovery import (
    DESIGN_PATH as HISTORICAL_RECOVERY_DESIGN_PATH,
    MAX_CALLS as HISTORICAL_RECOVERY_MAX_CALLS,
    MAX_CREDITS as HISTORICAL_RECOVERY_MAX_CREDITS,
    check_historical_recovery,
    initialize_historical_recovery,
    load_historical_recovery_manifest,
    start_historical_recovery,
)

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
    if args.days <= 0:
        raise ValueError("--days must be positive")
    if not 1 <= args.limit <= 100:
        raise ValueError("--limit must be between 1 and 100")
    end = datetime.now(timezone.utc) if args.to is None else _parse_utc_timestamp(args.to)
    start = (
        end - timedelta(days=args.days)
        if args.from_ is None
        else _parse_utc_timestamp(args.from_)
    )
    if start >= end:
        raise ValueError("--from must be before --to")

    safe = args.token[:12].replace("/", "_")
    filename = (
        f"flows-{args.chain}-{safe}.json"
        if args.label == "smart_money"
        else f"flows-exchange-{args.chain}-{safe}.json"
    )
    path = args.output if args.output is not None else Path("results") / filename
    response_path, request_path = _flow_artifact_paths(path)
    if args.output is not None and not args.force_output and (
        response_path.exists() or request_path.exists()
    ):
        raise FileExistsError(
            f"refusing to overwrite explicit flow output: {response_path} or {request_path}"
        )
    c = NansenClient()
    payload = {
        "chain": args.chain,
        "token_address": args.token,
        "date": {"from": start.isoformat().replace("+00:00", "Z"), "to": end.isoformat().replace("+00:00", "Z")},
        "label": args.label,
        "pagination": {"page": 1, "per_page": args.limit},
        "order_by": [{"field": "date", "direction": "ASC"}],
    }
    response = c.post_with_provenance("tgm/flows", payload, refresh=args.refresh)
    body = response.body
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ValueError("API response data must be a list")
    artifact_written_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    rows = body["data"]
    response_path, _ = write_flow_artifacts(
        body=body,
        payload=payload,
        output_path=path,
        cache_hit=response.cache_hit,
        response_retrieved_at=response.response_retrieved_at,
        artifact_written_at=artifact_written_at,
        overwrite=args.output is None or args.force_output,
    )
    print(f"received {len(rows)} flow snapshots; saved {response_path}")
    for row in rows[:10]:
        print(json.dumps(row, ensure_ascii=False))


def _parse_utc_timestamp(value):
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return timestamp.astimezone(timezone.utc)


def cmd_who_bought_sold(args):
    start = _parse_utc_timestamp(args.from_)
    end = _parse_utc_timestamp(args.to)
    labels = [label.strip() for label in args.labels]
    if start >= end:
        raise ValueError("--from must be before --to")
    if not 1 <= args.limit <= 100:
        raise ValueError("--limit must be between 1 and 100")
    if not math.isfinite(args.min_volume_usd):
        raise ValueError("--min-volume-usd must be finite")
    if args.min_volume_usd < 0:
        raise ValueError("--min-volume-usd must be non-negative")
    if any(not label for label in labels):
        raise ValueError("--labels cannot contain empty values")
    if len(set(labels)) != len(labels):
        raise ValueError("--labels cannot contain duplicates")

    safe = args.token[:12].replace("/", "_")
    path = (
        args.output
        if args.output is not None
        else Path("results") / f"who-bought-sold-{args.side}-{args.chain}-{safe}.json"
    )
    response_path, request_path = _flow_artifact_paths(path)
    if args.output is not None and not args.force_output and (
        response_path.exists() or request_path.exists()
    ):
        raise FileExistsError(
            f"refusing to overwrite explicit buyer/seller output: {response_path} or {request_path}"
        )

    payload = {
        "chain": args.chain,
        "token_address": args.token,
        "buy_or_sell": args.side,
        "date": {
            "from": start.isoformat().replace("+00:00", "Z"),
            "to": end.isoformat().replace("+00:00", "Z"),
        },
        "pagination": {"page": 1, "per_page": args.limit},
        "filters": {
            "include_smart_money_labels": labels,
            "trade_volume_usd": {"min": args.min_volume_usd},
        },
        "order_by": [{
            "field": "bought_volume_usd" if args.side == "BUY" else "sold_volume_usd",
            "direction": "DESC",
        }],
    }
    client = NansenClient()
    response = client.post_with_provenance(
        "tgm/who-bought-sold", payload, refresh=args.refresh
    )
    body = response.body
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise ValueError("API response data must be a list")
    pagination = body.get("pagination")
    if not isinstance(pagination, dict) or not isinstance(pagination.get("is_last_page"), bool):
        raise ValueError("API response pagination.is_last_page must be a boolean")

    pagination_complete = pagination["is_last_page"]
    artifact_written_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    response_path, _ = write_api_artifacts(
        body=body,
        payload=payload,
        endpoint="tgm/who-bought-sold",
        output_path=path,
        cache_hit=response.cache_hit,
        response_retrieved_at=response.response_retrieved_at,
        artifact_written_at=artifact_written_at,
        response_metadata={
            "row_count": len(body["data"]),
            "pagination_complete": pagination_complete,
        },
        overwrite=args.output is None or args.force_output,
    )
    print(f"received {len(body['data'])} {args.side} rows; saved {response_path}")
    if not pagination_complete:
        print("warning: pagination_complete: false; archived page is not complete breadth")


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


def cmd_evaluate(args):
    paths = evaluate_manifest(args.manifest, check=args.check)
    prefix = "verified: " if args.check else ""
    for path in paths:
        print(f"{prefix}{path}")


def cmd_pilot_init(args):
    model_successor_protocols = {
        "schema-subset-v5",
        "citation-enum-v6",
        "pass2-budget-v7",
    }
    if args.protocol_version in model_successor_protocols:
        if not args.source_manifest:
            raise ValueError(f"{args.protocol_version} requires --source-manifest")
        bundle = initialize_model_successor(
            Path(args.experiment_dir),
            source_manifest=Path(args.source_manifest),
            created_at=datetime.now(timezone.utc),
            protocol_version=args.protocol_version,
        )
    else:
        if args.source_manifest:
            raise ValueError("--source-manifest is only valid for model-successor protocols")
        bundle = initialize_pilot(
            Path(args.experiment_dir),
            created_at=datetime.now(timezone.utc),
            protocol_version=args.protocol_version,
        )
    print(f"initialized: {bundle.manifest_path}")
    print(f"stage: {bundle.manifest['stage']}")


def _pilot_bundle(manifest):
    return load_prospective_manifest(Path(manifest))


def cmd_pilot_start(args):
    if args.max_nansen_calls != 10 or args.max_nansen_credits != 10:
        raise ValueError("prospective pilot ceilings are fixed at ten calls and ten credits")
    print("Nansen hard ceiling: 10 calls / 10 credits")
    bundle = _pilot_bundle(args.manifest)
    result = start_pilot(
        bundle,
        nansen=NansenClient(),
        openai=OpenAIClient(),
        clock=lambda: datetime.now(timezone.utc),
        sleep=time.sleep,
    )
    print(f"preflight and start result: {result.manifest['stage']}")
    if result.manifest["stage"] == "decision_sealed":
        decision = json.loads((result.root / "derived/decision.json").read_text())
        print(f"entry window: {decision['entry_window']['from']} to {decision['entry_window']['to']}")
        print(f"earliest settlement: {decision['earliest_settlement_at']}")


def cmd_pilot_settle(args):
    result = settle_pilot(
        _pilot_bundle(args.manifest),
        nansen=NansenClient(),
        clock=lambda: datetime.now(timezone.utc),
    )
    print(f"stage: {result.manifest['stage']}")


def cmd_pilot_replay(args):
    result = replay_pilot(_pilot_bundle(args.manifest))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


def cmd_pilot_check(args):
    for path in check_pilot(_pilot_bundle(args.manifest)):
        print(f"verified: {path}")


def cmd_historical_init(args):
    path = initialize_historical_discovery(
        Path(args.experiment_dir),
        created_at=datetime.now(timezone.utc),
        design_path=Path(__file__).resolve().parents[2] / HISTORICAL_DESIGN_PATH,
    )
    print(f"initialized: {path}")
    print("stage: preregistered")


def cmd_historical_start(args):
    load_historical_manifest(Path(args.manifest))
    if (
        args.max_nansen_calls != HISTORICAL_MAX_CALLS
        or args.max_nansen_credits != HISTORICAL_MAX_CREDITS
    ):
        raise ValueError(
            "historical discovery ceilings are fixed at "
            f"{HISTORICAL_MAX_CALLS} request attempts and "
            f"{HISTORICAL_MAX_CREDITS} credits"
        )
    print(
        "Nansen hard ceiling: "
        f"{HISTORICAL_MAX_CALLS} authenticated request attempts / "
        f"{HISTORICAL_MAX_CREDITS} credits"
    )
    result = start_historical_discovery(
        Path(args.manifest),
        nansen=NansenClient(),
        clock=lambda: datetime.now(timezone.utc),
        sleep=time.sleep,
    )
    print(f"stage: {result['stage']}")
    if result["terminal_reason"]:
        print(f"reason: {result['terminal_reason']}")


def cmd_historical_check(args):
    for path in check_historical_discovery(Path(args.manifest)):
        print(f"verified: {path}")


def cmd_historical_recovery_init(args):
    path = initialize_historical_recovery(
        Path(args.experiment_dir),
        created_at=datetime.now(timezone.utc),
        design_path=Path(__file__).resolve().parents[2] / HISTORICAL_RECOVERY_DESIGN_PATH,
    )
    print(f"initialized: {path}")
    print("stage: preregistered")


def cmd_historical_recovery_start(args):
    load_historical_recovery_manifest(Path(args.manifest))
    if (
        args.max_nansen_calls != HISTORICAL_RECOVERY_MAX_CALLS
        or args.max_nansen_credits != HISTORICAL_RECOVERY_MAX_CREDITS
    ):
        raise ValueError(
            "historical recovery ceilings are fixed at "
            f"{HISTORICAL_RECOVERY_MAX_CALLS} additional request attempts and "
            f"{HISTORICAL_RECOVERY_MAX_CREDITS} additional credits"
        )
    print(
        "Nansen incremental hard ceiling: "
        f"{HISTORICAL_RECOVERY_MAX_CALLS} authenticated request attempts / "
        f"{HISTORICAL_RECOVERY_MAX_CREDITS} credits"
    )
    result = start_historical_recovery(
        Path(args.manifest),
        nansen=NansenClient(),
        clock=lambda: datetime.now(timezone.utc),
        sleep=time.sleep,
    )
    print(f"stage: {result['stage']}")
    if result["terminal_reason"]:
        print(f"reason: {result['terminal_reason']}")


def cmd_historical_recovery_check(args):
    for path in check_historical_recovery(Path(args.manifest)):
        print(f"verified: {path}")


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
    s.add_argument("--label", choices=("smart_money", "exchange"), default="smart_money")
    s.add_argument("--output")
    s.add_argument("--force-output", action="store_true")
    s.add_argument("--refresh", action="store_true")
    s.set_defaults(func=cmd_flows)

    s = sub.add_parser("who-bought-sold", help="collect buyer or seller evidence for one token")
    s.add_argument("--chain", required=True)
    s.add_argument("--token", required=True)
    s.add_argument("--side", choices=("BUY", "SELL"), required=True)
    s.add_argument("--from", dest="from_", required=True)
    s.add_argument("--to", required=True)
    s.add_argument("--labels", nargs="+", default=["Fund", "Smart Trader", "30D Smart Trader"])
    s.add_argument("--min-volume-usd", type=float, default=0.0)
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--output")
    s.add_argument("--force-output", action="store_true")
    s.add_argument("--refresh", action="store_true")
    s.set_defaults(func=cmd_who_bought_sold)

    s = sub.add_parser("plan", help="show call/credit budget without calling Nansen")
    s.add_argument("--tokens", type=int, default=10)
    s.set_defaults(func=cmd_plan)

    s = sub.add_parser("analyze", help="generate or verify a committed research bundle")
    s.add_argument("--manifest", required=True)
    s.add_argument("--check", action="store_true")
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("evaluate", help="evaluate fixed paper-only strategy theories offline")
    s.add_argument("--manifest", required=True)
    s.add_argument("--check", action="store_true")
    s.set_defaults(func=cmd_evaluate)

    s = sub.add_parser("pilot-init", help="initialize the prospective GPT pilot offline")
    s.add_argument("--experiment-dir", required=True)
    s.add_argument(
        "--protocol-version",
        choices=(
            "strict-v1",
            "account-baseline-v2",
            "completed-flow-v3",
            "contract-context-v4",
            "schema-subset-v5",
            "citation-enum-v6",
            "pass2-budget-v7",
        ),
        default="strict-v1",
    )
    s.add_argument("--source-manifest")
    s.set_defaults(func=cmd_pilot_init)

    s = sub.add_parser("pilot-start", help="collect and seal the prospective decision")
    s.add_argument("--manifest", required=True)
    s.add_argument("--max-nansen-calls", type=int, default=10)
    s.add_argument("--max-nansen-credits", type=int, default=10)
    s.set_defaults(func=cmd_pilot_start)

    s = sub.add_parser("pilot-settle", help="collect the sealed pilot outcome")
    s.add_argument("--manifest", required=True)
    s.set_defaults(func=cmd_pilot_settle)

    s = sub.add_parser("pilot-replay", help="replay a prospective pilot offline")
    s.add_argument("--manifest", required=True)
    s.set_defaults(func=cmd_pilot_replay)

    s = sub.add_parser("pilot-check", help="verify a prospective pilot offline")
    s.add_argument("--manifest", required=True)
    s.set_defaults(func=cmd_pilot_check)

    s = sub.add_parser("historical-init", help="initialize holder-breadth discovery offline")
    s.add_argument("--experiment-dir", required=True)
    s.set_defaults(func=cmd_historical_init)

    s = sub.add_parser("historical-start", help="collect bounded historical discovery evidence")
    s.add_argument("--manifest", required=True)
    s.add_argument("--max-nansen-calls", type=int, default=HISTORICAL_MAX_CALLS)
    s.add_argument("--max-nansen-credits", type=int, default=HISTORICAL_MAX_CREDITS)
    s.set_defaults(func=cmd_historical_start)

    s = sub.add_parser("historical-check", help="verify historical discovery offline")
    s.add_argument("--manifest", required=True)
    s.set_defaults(func=cmd_historical_check)

    s = sub.add_parser(
        "historical-recovery-init",
        help="initialize the source-bound holder-breadth recovery offline",
    )
    s.add_argument("--experiment-dir", required=True)
    s.set_defaults(func=cmd_historical_recovery_init)

    s = sub.add_parser(
        "historical-recovery-start",
        help="collect only holdings and outcomes for the frozen recovery cohort",
    )
    s.add_argument("--manifest", required=True)
    s.add_argument("--max-nansen-calls", type=int, default=HISTORICAL_RECOVERY_MAX_CALLS)
    s.add_argument("--max-nansen-credits", type=int, default=HISTORICAL_RECOVERY_MAX_CREDITS)
    s.set_defaults(func=cmd_historical_recovery_start)

    s = sub.add_parser(
        "historical-recovery-check",
        help="verify the source-bound historical recovery offline",
    )
    s.add_argument("--manifest", required=True)
    s.set_defaults(func=cmd_historical_recovery_check)
    return p


def main():
    args = build_parser().parse_args()
    offline_commands = {
        "evaluate", "pilot-init", "pilot-replay", "pilot-check",
        "historical-init", "historical-check",
        "historical-recovery-init", "historical-recovery-check",
    }
    if args.cmd not in offline_commands:
        load_dotenv()
    args.func(args)


if __name__ == "__main__":
    main()
