from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from math import isfinite
from pathlib import Path
from typing import Any

from .artifacts import canonical_json_bytes
from .experiment import ExperimentError, prepare_flow_rows
from .signals import SUPPORTED_FEATURE_SET, build_signal_features


class SnapshotError(RuntimeError):
    """Raised when point-in-time selection or evidence cannot be used safely."""


@dataclass(frozen=True)
class Candidate:
    chain: str
    token_address: str
    token_symbol: str
    liquidity_usd: float
    row: dict[str, Any]


_SCREENER_PAYLOAD = {
    "chains": ["solana", "ethereum", "base", "bnb", "arbitrum"],
    "timeframe": "24h",
    "pagination": {"page": 1, "per_page": 1000},
    "filters": {
        "trader_type": "sm",
        "include_stablecoins": False,
        "token_age_days": {"min": 3},
        "market_cap_usd": {"min": 1_000_000},
        "liquidity": {"min": 250_000},
    },
    "order_by": [{"field": "netflow", "direction": "DESC"}],
}
_NOTIONAL_FORMULA = "min(1000, 0.001 * liquidity_usd)"
_FLOW_FIELDS = (
    "date", "bucket_end", "price_usd", "token_amount", "value_usd", "holders_count",
    "total_inflows", "total_outflows", "total_inflows_count", "total_outflows_count",
    "is_complete",
)
_CONTEXT_METRICS = frozenset({
    "price_usd", "market_cap_usd", "liquidity_usd", "liquidity", "volume_usd",
    "holders_count", "holder_count", "smart_money_holders_count", "smart_money_holdings",
    "smart_money_holding_usd", "smart_money_netflow_usd", "netflow_usd", "inflow_usd",
    "outflow_usd", "buy_volume_usd", "sell_volume_usd", "buy_count", "sell_count",
    "trader_count", "smart_money_trader_count",
})


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SnapshotError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise SnapshotError(f"{field} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SnapshotError(f"{field} must be an RFC 3339 timestamp") from exc
    return _utc(parsed, field=field)


def _utc_text(value: datetime) -> str:
    return _utc(value, field="timestamp").isoformat().replace("+00:00", "Z")


def _finite_positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) and number > 0 else None


def _address_identity(chain: Any, address: Any) -> tuple[str, str]:
    if not isinstance(chain, str) or not chain or not isinstance(address, str) or not address:
        raise SnapshotError("token identity must contain non-empty chain and address")
    normalized_chain = chain.lower()
    normalized_address = address.lower() if address.lower().startswith("0x") else address
    return normalized_chain, normalized_address


def _row_field(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def screener_payload() -> dict[str, Any]:
    """Return a fresh exact page-one screener contract payload."""
    return json.loads(json.dumps(_SCREENER_PAYLOAD))


def prior_token_identities(experiments_root: Path) -> frozenset[tuple[str, str]]:
    """Load all prior cohort identities, normalizing only EVM-style addresses."""
    root = Path(experiments_root)
    identities: set[tuple[str, str]] = set()
    if not root.exists():
        raise SnapshotError(f"experiments root does not exist: {root}")
    for manifest_path in sorted(root.glob("*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise SnapshotError(f"cannot read prior experiment manifest {manifest_path}") from exc
        if not isinstance(manifest, dict):
            raise SnapshotError(f"prior experiment manifest must be an object: {manifest_path}")
        cohort = manifest.get("cohort", [])
        if not isinstance(cohort, list):
            raise SnapshotError(f"prior experiment cohort must be a list: {manifest_path}")
        for member in cohort:
            if not isinstance(member, dict):
                raise SnapshotError(f"prior experiment cohort member must be an object: {manifest_path}")
            chain = member.get("chain")
            address = _row_field(member, "token_address", "address")
            identities.add(_address_identity(chain, address))
    return frozenset(identities)


def _candidate_from_row(row: Any) -> Candidate | None:
    if not isinstance(row, dict):
        return None
    chain = row.get("chain")
    address = _row_field(row, "token_address", "address")
    symbol = _row_field(row, "symbol", "token_symbol")
    if not isinstance(chain, str) or not chain or not isinstance(address, str) or not address:
        return None
    if not isinstance(symbol, str) or not symbol:
        return None
    liquidity = _finite_positive(row.get("liquidity"))
    metrics = (
        _finite_positive(_row_field(row, "price_usd", "price")),
        _finite_positive(_row_field(row, "volume_usd", "volume")),
        liquidity,
        _finite_positive(_row_field(row, "market_cap_usd", "market_cap")),
        _finite_positive(row.get("netflow")),
    )
    if any(value is None for value in metrics):
        return None
    return Candidate(chain, address, symbol, float(liquidity), dict(row))


def select_candidate(body: dict[str, Any], excluded: frozenset[tuple[str, str]]) -> Candidate:
    """Choose only the stable, page-local top eligible screener row."""
    if not isinstance(body, dict):
        raise SnapshotError("screener response must be an object")
    rows = body.get("data")
    pagination = body.get("pagination")
    if not isinstance(rows, list):
        raise SnapshotError("screener response data must be a list")
    if (
        not isinstance(pagination, dict)
        or pagination.get("page") != 1
        or not isinstance(pagination.get("per_page"), int)
        or isinstance(pagination.get("per_page"), bool)
        or pagination["per_page"] <= 0
    ):
        raise SnapshotError("screener response must declare page-one pagination metadata")
    eligible = []
    for row in rows:
        candidate = _candidate_from_row(row)
        if candidate is None:
            continue
        if _address_identity(candidate.chain, candidate.token_address) in excluded:
            continue
        eligible.append(candidate)
    if not eligible:
        raise SnapshotError("no eligible screener candidates on requested page")
    return min(
        eligible,
        key=lambda item: (
            -float(item.row["netflow"]),
            item.chain.lower(),
            _address_identity(item.chain, item.token_address)[1],
        ),
    )


def freeze_selection(
    candidate: Candidate, *, screener_response_sha256: str, screener_retrieved_at: str
) -> dict[str, Any]:
    """Freeze selection identity, source liquidity, and virtual notional separately."""
    if not isinstance(candidate.row, dict):
        raise SnapshotError("selected screener row must be an object")
    if (
        not isinstance(screener_response_sha256, str)
        or len(screener_response_sha256) != 64
        or any(character not in "0123456789abcdef" for character in screener_response_sha256)
    ):
        raise SnapshotError("screener response hash must be a SHA-256")
    _timestamp(screener_retrieved_at, field="screener retrieval time")
    liquidity = candidate.row.get("liquidity")
    try:
        decimal_liquidity = Decimal(str(liquidity))
    except (InvalidOperation, ValueError) as exc:
        raise SnapshotError("selected screener liquidity must be a finite decimal") from exc
    if not decimal_liquidity.is_finite() or decimal_liquidity <= 0:
        raise SnapshotError("selected screener liquidity must be a finite positive decimal")
    virtual_notional = min(Decimal("1000"), Decimal("0.001") * decimal_liquidity)
    return {
        "schema_version": 1,
        "selection_status": "page_local_top_eligible",
        "identity": {
            "chain": candidate.chain,
            "token_address": candidate.token_address,
            "token_symbol": candidate.token_symbol,
        },
        "screener": {
            "selected_row": dict(candidate.row),
            "selected_row_sha256": hashlib.sha256(canonical_json_bytes(candidate.row)).hexdigest(),
            "response_sha256": screener_response_sha256,
            "retrieved_at": screener_retrieved_at,
        },
        "liquidity": {"screener_liquidity_usd": float(decimal_liquidity)},
        "notional": {
            "formula": _NOTIONAL_FORMULA,
            "virtual_notional_usd": float(virtual_notional),
        },
    }


def predecision_requests(
    candidate: Candidate, available_at: datetime
) -> tuple[tuple[str, str, dict[str, Any]], ...]:
    available = _utc(available_at, field="available_at")
    date = {"from": _utc_text(available - timedelta(hours=25)), "to": _utc_text(available)}
    base = {"chain": candidate.chain, "token_address": candidate.token_address}
    def flow_request(label: str) -> dict[str, Any]:
        return {
            **base,
            "date": dict(date),
            "label": label,
            "pagination": {"page": 1, "per_page": 1000},
            "order_by": [{"field": "date", "direction": "ASC"}],
        }
    return (
        ("POST", "tgm/token-information", {**base, "timeframe": "1d"}),
        ("POST", "tgm/flow-intelligence", {**base, "timeframe": "1d"}),
        ("POST", "tgm/flows", flow_request("smart_money")),
        ("POST", "tgm/flows", flow_request("exchange")),
    )


def _validate_final_flow_response(body: Any, *, label: str, available_at: datetime) -> tuple[dict[str, Any], ...]:
    if not isinstance(body, dict):
        raise SnapshotError(f"{label} flow response must be an object")
    rows = body.get("data")
    pagination = body.get("pagination")
    if not isinstance(rows, list):
        raise SnapshotError(f"{label} flow response data must be a list")
    if (
        not isinstance(pagination, dict)
        or pagination.get("page") != 1
        or pagination.get("is_last_page") is not True
    ):
        raise SnapshotError(f"{label} flow response must declare is_last_page=true")
    validated = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SnapshotError(f"{label} flow row {index} must be an object")
        if row.get("is_complete") is not True:
            raise SnapshotError(f"{label} flow row {index} must declare is_complete=true")
        bucket_end = _timestamp(row.get("bucket_end"), field=f"{label} flow row {index} bucket_end")
        if bucket_end > available_at:
            raise SnapshotError(f"{label} flow row {index} bucket_end exceeds available_at")
        validated.append(dict(row))
    return tuple(validated)


def _freshness(response: dict[str, Any]) -> dict[str, Any]:
    value = response.get("response_retrieved_at")
    if value is not None:
        _timestamp(value, field="response retrieval time")
    cache_hit = response.get("cache_hit")
    if cache_hit is not None and not isinstance(cache_hit, bool):
        raise SnapshotError("cache_hit must be a boolean when present")
    return {"response_retrieved_at": value, "cache_hit": cache_hit}


def _warnings(response: dict[str, Any]) -> dict[str, Any]:
    warnings = response.get("warnings", [])
    if not isinstance(warnings, list):
        raise SnapshotError("response warnings must be a list")
    return {"present": bool(warnings), "count": len(warnings)}


def _context_section(response: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise SnapshotError(f"{label} response must be an object")
    data = response.get("data", {})
    if not isinstance(data, dict):
        raise SnapshotError(f"{label} response data must be an object")
    metrics = {}
    for key in _CONTEXT_METRICS:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            continue
        metrics[key] = value
    return {"data": metrics, "freshness": _freshness(response), "warnings": _warnings(response)}


def _flow_row_whitelist(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in _FLOW_FIELDS if field in row}


def normalize_snapshot(
    selection: dict[str, Any],
    token_information: dict[str, Any],
    flow_intelligence: dict[str, Any],
    smart_money_flows: dict[str, Any],
    exchange_flows: dict[str, Any],
    *,
    available_at: datetime,
) -> dict[str, Any]:
    """Normalize strict pre-decision evidence without bridging gaps or leaking identity."""
    available = _utc(available_at, field="available_at")
    if not isinstance(selection, dict):
        raise SnapshotError("selection must be an object")
    try:
        identity = selection["identity"]
        notional = selection["notional"]
        liquidity = selection["liquidity"]
    except KeyError as exc:
        raise SnapshotError("selection is missing frozen fields") from exc
    if not isinstance(identity, dict) or not isinstance(identity.get("chain"), str):
        raise SnapshotError("selection identity is invalid")
    if not isinstance(notional, dict) or notional.get("formula") != _NOTIONAL_FORMULA:
        raise SnapshotError("selection notional formula is invalid")
    if _finite_positive(notional.get("virtual_notional_usd")) is None:
        raise SnapshotError("selection virtual notional is invalid")
    if not isinstance(liquidity, dict) or _finite_positive(liquidity.get("screener_liquidity_usd")) is None:
        raise SnapshotError("selection screener liquidity is invalid")

    smart_rows = _validate_final_flow_response(
        smart_money_flows, label="smart_money", available_at=available
    )
    exchange_rows = _validate_final_flow_response(
        exchange_flows, label="exchange", available_at=available
    )
    try:
        prepared = prepare_flow_rows({"data": list(smart_rows)})
    except ExperimentError as exc:
        raise SnapshotError(f"invalid smart_money flow rows: {exc}") from exc
    signal_source = tuple({
        "timestamp": row["bucket_end"],
        "price_usd": row["price_usd"],
        "token_amount": row["token_amount"],
        "holders_count": row.get("holders_count"),
    } for row in prepared.rows)
    try:
        features = build_signal_features(
            signal_source,
            horizons=(1, 4, 12),
            source_experiment_id="prospective-snapshot",
            feature_set_version=SUPPORTED_FEATURE_SET,
        )
    except Exception as exc:
        raise SnapshotError(f"cannot build smart_money signal features: {exc}") from exc
    final_feature = features[-1] if features else None
    prior_feature = None
    if final_feature is not None:
        final_time = _timestamp(final_feature["timestamp"], field="final feature timestamp")
        prior_time = final_time - timedelta(hours=1)
        prior_feature = next(
            (feature for feature in features if _timestamp(feature["timestamp"], field="feature timestamp") == prior_time),
            None,
        )
    return {
        "schema_version": 1,
        "candidate": {"chain": identity["chain"]},
        "available_at": _utc_text(available),
        "selection": {
            "formula": notional["formula"],
            "virtual_notional_usd": float(notional["virtual_notional_usd"]),
            "screener_liquidity_usd": float(liquidity["screener_liquidity_usd"]),
        },
        "token_information": _context_section(token_information, label="token_information"),
        "flow_intelligence": _context_section(flow_intelligence, label="flow_intelligence"),
        "smart_money": {
            "feature_set_version": SUPPORTED_FEATURE_SET,
            "raw_row_count": len(smart_rows),
            "prepared_row_count": len(prepared.rows),
            "incomplete_row_count": prepared.incomplete_count,
            "invalid_metric_row_count": prepared.invalid_metric_count,
            "gaps": [list(gap) for gap in prepared.gaps],
            "final_feature": final_feature,
            "prior_hour_feature": prior_feature,
            "freshness": _freshness(smart_money_flows),
            "warnings": _warnings(smart_money_flows),
        },
        "exchange": {
            "rows": [_flow_row_whitelist(row) for row in exchange_rows],
            "freshness": _freshness(exchange_flows),
            "warnings": _warnings(exchange_flows),
        },
    }


def blind_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build a fresh GPT-safe whitelist; never redact by deleting from a copy."""
    if not isinstance(snapshot, dict):
        raise SnapshotError("snapshot must be an object")
    candidate = snapshot.get("candidate")
    selection = snapshot.get("selection")
    if not isinstance(candidate, dict) or not isinstance(candidate.get("chain"), str):
        raise SnapshotError("snapshot candidate is invalid")
    if not isinstance(selection, dict):
        raise SnapshotError("snapshot selection is invalid")
    return {
        "schema_version": 1,
        "candidate": {"identity": "candidate-1", "chain": candidate["chain"]},
        "available_at": snapshot.get("available_at"),
        "selection": {
            "formula": selection.get("formula"),
            "virtual_notional_usd": selection.get("virtual_notional_usd"),
        },
        "token_information": _blind_context(snapshot.get("token_information")),
        "flow_intelligence": _blind_context(snapshot.get("flow_intelligence")),
        "smart_money": _blind_smart_money(snapshot.get("smart_money")),
        "exchange": _blind_exchange(snapshot.get("exchange")),
    }


def _blind_context(section: Any) -> dict[str, Any]:
    if not isinstance(section, dict):
        raise SnapshotError("snapshot context section is invalid")
    data = section.get("data")
    freshness = section.get("freshness")
    warnings = section.get("warnings")
    if not isinstance(data, dict) or not isinstance(freshness, dict) or not isinstance(warnings, dict):
        raise SnapshotError("snapshot context section is invalid")
    return {
        "data": {key: data[key] for key in _CONTEXT_METRICS if key in data},
        "freshness": {
            "response_retrieved_at": freshness.get("response_retrieved_at"),
            "cache_hit": freshness.get("cache_hit"),
        },
        "warnings": {"present": warnings.get("present"), "count": warnings.get("count")},
    }


def _blind_smart_money(section: Any) -> dict[str, Any]:
    if not isinstance(section, dict):
        raise SnapshotError("snapshot smart_money section is invalid")
    return {
        "feature_set_version": section.get("feature_set_version"),
        "raw_row_count": section.get("raw_row_count"),
        "prepared_row_count": section.get("prepared_row_count"),
        "incomplete_row_count": section.get("incomplete_row_count"),
        "invalid_metric_row_count": section.get("invalid_metric_row_count"),
        "gaps": section.get("gaps"),
        "final_feature": section.get("final_feature"),
        "prior_hour_feature": section.get("prior_hour_feature"),
        "freshness": _blind_context({"data": {}, "freshness": section.get("freshness"), "warnings": section.get("warnings")})["freshness"],
        "warnings": _blind_context({"data": {}, "freshness": section.get("freshness"), "warnings": section.get("warnings")})["warnings"],
    }


def _blind_exchange(section: Any) -> dict[str, Any]:
    if not isinstance(section, dict) or not isinstance(section.get("rows"), list):
        raise SnapshotError("snapshot exchange section is invalid")
    metadata = _blind_context({"data": {}, "freshness": section.get("freshness"), "warnings": section.get("warnings")})
    rows = []
    for row in section["rows"]:
        if not isinstance(row, dict):
            raise SnapshotError("snapshot exchange row is invalid")
        rows.append({field: row[field] for field in _FLOW_FIELDS if field in row})
    return {"rows": rows, "freshness": metadata["freshness"], "warnings": metadata["warnings"]}
