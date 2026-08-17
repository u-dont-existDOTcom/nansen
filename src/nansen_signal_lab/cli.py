from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
from contextlib import contextmanager
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
    return _write_sibling_staged(path, content, suffix=".tmp")


def _write_sibling_backup(path, content):
    return _write_sibling_staged(path, content, suffix=".bak")


def _sibling_staged_path(path, *, suffix):
    path = Path(path)
    return path.with_name(f".{path.name}{suffix}")


def _write_sibling_staged(path, content, *, suffix):
    temporary = _sibling_staged_path(path, suffix=suffix)
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


@contextmanager
def _artifact_pair_lock(output_path):
    response_path, _ = _flow_artifact_paths(output_path)
    response_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(response_path.parent, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _install_artifact(temporary, target):
    temporary.replace(target)


def _fsync_directory(path):
    descriptor = os.open(Path(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _artifact_transaction_paths(response_path, request_path):
    marker = response_path.with_name(f".{response_path.name}.transaction.json")
    return {
        "response_temporary": _sibling_staged_path(response_path, suffix=".tmp"),
        "request_temporary": _sibling_staged_path(request_path, suffix=".tmp"),
        "response_backup": _sibling_staged_path(response_path, suffix=".bak"),
        "request_backup": _sibling_staged_path(request_path, suffix=".bak"),
        "marker": marker,
        "marker_temporary": _sibling_staged_path(marker, suffix=".tmp"),
    }


def _cleanup_artifact_transaction(paths):
    removed = False
    for name in (
        "response_temporary",
        "request_temporary",
        "response_backup",
        "request_backup",
        "marker_temporary",
        "marker",
    ):
        path = paths[name]
        if path.exists():
            path.unlink()
            removed = True
    if removed:
        _fsync_directory(paths["marker"].parent)


def _validate_installed_artifact_pair(
    response_path,
    request_path,
    *,
    response_sha256,
    request_sha256,
):
    if (
        not response_path.is_file()
        or hashlib.sha256(response_path.read_bytes()).hexdigest() != response_sha256
        or not request_path.is_file()
        or hashlib.sha256(request_path.read_bytes()).hexdigest() != request_sha256
    ):
        raise RuntimeError("incomplete evidence artifact transaction")
    try:
        metadata = json.loads(request_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("invalid evidence artifact sidecar after transaction") from exc
    if (
        not isinstance(metadata, dict)
        or metadata.get("response_file") != response_path.name
        or metadata.get("response_sha256") != response_sha256
    ):
        raise RuntimeError("evidence response does not match installed sidecar")


def _restore_artifact_transaction(response_path, request_path, transaction, paths):
    for backup_name, target, digest_name in (
        ("response_backup", response_path, "original_response_sha256"),
        ("request_backup", request_path, "original_request_sha256"),
    ):
        digest = transaction[digest_name]
        backup = paths[backup_name]
        if digest is None:
            if target.exists():
                target.unlink()
                _fsync_directory(target.parent)
            continue
        if backup.exists():
            backup.replace(target)
            _fsync_directory(target.parent)
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise RuntimeError("cannot roll back evidence artifact transaction")


def _recover_artifact_transaction(response_path, request_path):
    paths = _artifact_transaction_paths(response_path, request_path)
    marker_path = paths["marker"]
    if not marker_path.exists():
        _cleanup_artifact_transaction(paths)
        return
    try:
        transaction = json.loads(marker_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot recover evidence artifact transaction: {exc}") from exc
    expected = {
        "schema_version": 1,
        "response_file": response_path.name,
        "request_file": request_path.name,
    }
    if not isinstance(transaction, dict) or any(
        transaction.get(key) != value for key, value in expected.items()
    ):
        raise RuntimeError("invalid evidence artifact transaction marker")
    phase = transaction.get("phase")
    response_sha256 = transaction.get("response_sha256")
    request_sha256 = transaction.get("request_sha256")
    original_response_sha256 = transaction.get("original_response_sha256")
    original_request_sha256 = transaction.get("original_request_sha256")
    if phase not in {"commit", "rollback"}:
        raise RuntimeError("invalid evidence artifact transaction phase")
    if not all(
        isinstance(digest, str) and len(digest) == 64
        for digest in (response_sha256, request_sha256)
    ):
        raise RuntimeError("invalid evidence artifact transaction hashes")
    if not all(
        digest is None or (isinstance(digest, str) and len(digest) == 64)
        for digest in (original_response_sha256, original_request_sha256)
    ):
        raise RuntimeError("invalid original evidence artifact transaction hashes")

    if phase == "rollback":
        _restore_artifact_transaction(response_path, request_path, transaction, paths)
        if original_response_sha256 is not None and original_request_sha256 is not None:
            _validate_installed_artifact_pair(
                response_path,
                request_path,
                response_sha256=original_response_sha256,
                request_sha256=original_request_sha256,
            )
    else:
        for temporary_name, target, digest in (
            ("response_temporary", response_path, response_sha256),
            ("request_temporary", request_path, request_sha256),
        ):
            temporary = paths[temporary_name]
            if temporary.exists():
                _install_artifact(temporary, target)
                _fsync_directory(target.parent)
            if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise RuntimeError("cannot complete evidence artifact transaction")

        _validate_installed_artifact_pair(
            response_path,
            request_path,
            response_sha256=response_sha256,
            request_sha256=request_sha256,
        )
    _cleanup_artifact_transaction(paths)


def _write_artifact_transaction_marker(
    response_path,
    request_path,
    *,
    phase,
    response_sha256,
    request_sha256,
    original_response_sha256,
    original_request_sha256,
):
    paths = _artifact_transaction_paths(response_path, request_path)
    marker_bytes = (
        json.dumps(
            {
                "schema_version": 1,
                "phase": phase,
                "response_file": response_path.name,
                "request_file": request_path.name,
                "response_sha256": response_sha256,
                "request_sha256": request_sha256,
                "original_response_sha256": original_response_sha256,
                "original_request_sha256": original_request_sha256,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    marker_temporary = _write_sibling_temporary(paths["marker"], marker_bytes)
    _install_artifact(marker_temporary, paths["marker"])
    _fsync_directory(response_path.parent)


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
    response_sha256 = hashlib.sha256(response_bytes).hexdigest()
    request_sha256 = hashlib.sha256(request_bytes).hexdigest()
    with _artifact_pair_lock(response_path):
        _recover_artifact_transaction(response_path, request_path)
        response_temporary = None
        request_temporary = None
        backups = {}
        installation_started = False
        cleanup_transaction = True
        transaction_paths = _artifact_transaction_paths(response_path, request_path)
        try:
            if not overwrite and (response_path.exists() or request_path.exists()):
                raise FileExistsError(
                    f"refusing to overwrite explicit flow output: {response_path} or {request_path}"
                )
            original_bytes = {
                response_path: response_path.read_bytes() if response_path.exists() else None,
                request_path: request_path.read_bytes() if request_path.exists() else None,
            }
            original_response_sha256 = (
                None
                if original_bytes[response_path] is None
                else hashlib.sha256(original_bytes[response_path]).hexdigest()
            )
            original_request_sha256 = (
                None
                if original_bytes[request_path] is None
                else hashlib.sha256(original_bytes[request_path]).hexdigest()
            )
            response_temporary = _write_sibling_temporary(response_path, response_bytes)
            request_temporary = _write_sibling_temporary(request_path, request_bytes)
            for path, original in original_bytes.items():
                if original is not None:
                    backups[path] = _write_sibling_backup(path, original)
            _write_artifact_transaction_marker(
                response_path,
                request_path,
                phase="commit",
                response_sha256=response_sha256,
                request_sha256=request_sha256,
                original_response_sha256=original_response_sha256,
                original_request_sha256=original_request_sha256,
            )
            installation_started = True
            _install_artifact(response_temporary, response_path)
            response_temporary = None
            _fsync_directory(response_path.parent)
            _install_artifact(request_temporary, request_path)
            request_temporary = None
            _fsync_directory(response_path.parent)
            _validate_installed_artifact_pair(
                response_path,
                request_path,
                response_sha256=response_sha256,
                request_sha256=request_sha256,
            )
        except BaseException:
            if installation_started:
                try:
                    _write_artifact_transaction_marker(
                        response_path,
                        request_path,
                        phase="rollback",
                        response_sha256=response_sha256,
                        request_sha256=request_sha256,
                        original_response_sha256=original_response_sha256,
                        original_request_sha256=original_request_sha256,
                    )
                    _restore_artifact_transaction(
                        response_path,
                        request_path,
                        {
                            "original_response_sha256": original_response_sha256,
                            "original_request_sha256": original_request_sha256,
                        },
                        transaction_paths,
                    )
                except BaseException:
                    cleanup_transaction = False
                    raise
            raise
        finally:
            if cleanup_transaction:
                if response_temporary is not None:
                    response_temporary.unlink(missing_ok=True)
                if request_temporary is not None:
                    request_temporary.unlink(missing_ok=True)
                for backup in backups.values():
                    backup.unlink(missing_ok=True)
                _cleanup_artifact_transaction(transaction_paths)
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
