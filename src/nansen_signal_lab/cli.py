from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
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


def _flow_artifact_paths(output_path):
    response_path = Path(output_path)
    request_path = response_path.with_name(f"{response_path.stem}.request.json")
    return response_path, request_path


def _write_sibling_temporary(path, content):
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def write_api_artifacts(
    *,
    body,
    payload,
    endpoint,
    output_path,
    cache_hit,
    response_retrieved_at,
    artifact_written_at,
    response_metadata=None,
    overwrite=True,
):
    response_path, request_path = _flow_artifact_paths(output_path)
    response_path.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite and (response_path.exists() or request_path.exists()):
        raise FileExistsError(
            f"refusing to overwrite explicit flow output: {response_path} or {request_path}"
        )
    response_bytes = (json.dumps(body, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    metadata = {
        "schema_version": 2,
        "endpoint": endpoint,
        "payload": payload,
        "cache_hit": bool(cache_hit),
        "response_retrieved_at": response_retrieved_at,
        "artifact_written_at": artifact_written_at,
        "response_file": response_path.name,
        "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
    }
    if response_metadata is not None:
        metadata["response_metadata"] = dict(response_metadata)
    request_bytes = (
        json.dumps(metadata, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    response_temporary = None
    request_temporary = None
    try:
        response_temporary = _write_sibling_temporary(response_path, response_bytes)
        request_temporary = _write_sibling_temporary(request_path, request_bytes)
        if not overwrite and (response_path.exists() or request_path.exists()):
            raise FileExistsError(
                f"refusing to overwrite explicit flow output: {response_path} or {request_path}"
            )
        response_temporary.replace(response_path)
        response_temporary = None
        request_temporary.replace(request_path)
        request_temporary = None
    finally:
        if response_temporary is not None:
            response_temporary.unlink(missing_ok=True)
        if request_temporary is not None:
            request_temporary.unlink(missing_ok=True)
    return response_path, request_path


def write_flow_artifacts(
    *,
    body,
    payload,
    output_path,
    cache_hit,
    response_retrieved_at,
    artifact_written_at,
    overwrite=True,
):
    return write_api_artifacts(
        body=body,
        payload=payload,
        endpoint="tgm/flows",
        output_path=output_path,
        cache_hit=cache_hit,
        response_retrieved_at=response_retrieved_at,
        artifact_written_at=artifact_written_at,
        overwrite=overwrite,
    )


def cmd_flows(args):
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
    end = datetime.now(timezone.utc) if args.to is None else datetime.fromisoformat(args.to.replace("Z", "+00:00"))
    start = end - timedelta(days=args.days) if args.from_ is None else datetime.fromisoformat(args.from_.replace("Z", "+00:00"))
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
    artifact_written_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    rows = body.get("data", [])
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
    if start >= end:
        raise ValueError("--from must be before --to")
    if not 1 <= args.limit <= 100:
        raise ValueError("--limit must be between 1 and 100")
    if args.min_volume_usd < 0:
        raise ValueError("--min-volume-usd must be non-negative")
    if any(not label.strip() for label in args.labels):
        raise ValueError("--labels cannot contain empty values")
    if len(set(args.labels)) != len(args.labels):
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
            "include_smart_money_labels": args.labels,
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
    return p


def main():
    load_dotenv()
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
