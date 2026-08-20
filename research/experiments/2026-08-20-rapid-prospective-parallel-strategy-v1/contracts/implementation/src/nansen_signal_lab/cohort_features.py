from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from .cohort_schema import WBS_LABELS, parse_utc, utc_text
from .signals import SUPPORTED_FEATURE_SET, build_signal_features


class CohortFeatureError(RuntimeError):
    """Raised when predecision evidence is incomplete or internally inconsistent."""


@dataclass(frozen=True)
class BreadthEvidence:
    side: str
    available: bool
    reason: str | None
    rows: tuple[dict[str, Any], ...]
    distinct_addresses: int | None
    directional_volume_usd: float | None


_HOUR = timedelta(hours=1)
_MICROSECOND = timedelta(microseconds=1)
_EXCHANGE_COMPONENTS = (
    "total_inflows_dex",
    "total_outflows_dex",
    "total_inflows_cex",
    "total_outflows_cex",
)


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CohortFeatureError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _identity(candidate: Any) -> tuple[str, str]:
    chain = getattr(candidate, "chain", None)
    address = getattr(candidate, "token_address", None)
    if not isinstance(candidate, dict):
        if not isinstance(chain, str) or not isinstance(address, str):
            raise CohortFeatureError("candidate identity is missing")
    else:
        chain = candidate.get("chain")
        address = candidate.get("token_address")
    if not isinstance(chain, str) or not chain or not isinstance(address, str) or not address:
        raise CohortFeatureError("candidate identity is missing")
    return chain, address


def _finite(value: Any, *, field: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CohortFeatureError(f"{field} must be finite")
    number = float(value)
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise CohortFeatureError(f"{field} must be finite and >= {minimum}")
    return number


def _directional_flow(value: Any, *, field: str, outflow: bool) -> float:
    number = _finite(value, field=field)
    if (outflow and number > 0) or (not outflow and number < 0):
        direction = "nonpositive" if outflow else "nonnegative"
        raise CohortFeatureError(f"{field} must be finite and {direction}")
    return number


def flow_payload(candidate: Any, cutoff: datetime, label: str) -> dict[str, Any]:
    chain, address = _identity(candidate)
    boundary = _utc(cutoff, field="cutoff")
    if boundary.minute != 5 or boundary.second or boundary.microsecond:
        raise CohortFeatureError("collection start must be aligned to HH:05:00 UTC")
    completed_boundary = boundary.replace(minute=0)
    if label not in {"smart_money", "exchange"}:
        raise CohortFeatureError("flow label must be smart_money or exchange")
    return {
        "chain": chain,
        "token_address": address,
        "date": {
            "from": utc_text(completed_boundary - timedelta(hours=26)),
            "to": utc_text(completed_boundary - _MICROSECOND),
        },
        "label": label,
        "pagination": {"page": 1, "per_page": 1000},
        "order_by": [{"field": "date", "direction": "ASC"}],
    }


def wbs_payload(
    candidate: Any,
    cutoff: datetime,
    side: str,
    page: int,
) -> dict[str, Any]:
    chain, address = _identity(candidate)
    collection_start = _utc(cutoff, field="collection start")
    if collection_start.minute != 5 or collection_start.second or collection_start.microsecond:
        raise CohortFeatureError("collection start must be aligned to HH:05:00 UTC")
    boundary = collection_start
    if side not in {"BUY", "SELL"}:
        raise CohortFeatureError("WBS side must be BUY or SELL")
    if not isinstance(page, int) or isinstance(page, bool) or page not in {1, 2}:
        raise CohortFeatureError("WBS page must be 1 or 2")
    volume_field = "bought_volume_usd" if side == "BUY" else "sold_volume_usd"
    return {
        "chain": chain,
        "token_address": address,
        "buy_or_sell": side,
        "date": {
            "from": utc_text(boundary - timedelta(hours=24)),
            "to": utc_text(boundary - _MICROSECOND),
        },
        "pagination": {"page": page, "per_page": 1000},
        "filters": {
            "include_smart_money_labels": list(WBS_LABELS),
            "trade_volume_usd": {"min": 0},
        },
        "order_by": [{"field": volume_field, "direction": "DESC"}],
    }


def _validate_optional_identity(body: dict[str, Any], candidate: Any) -> None:
    expected_chain, expected_address = _identity(candidate)
    if "chain" in body and body["chain"] != expected_chain:
        raise CohortFeatureError("response chain differs from the selected candidate")
    if "token_address" in body:
        actual = body["token_address"]
        expected = expected_address
        if expected.lower().startswith("0x"):
            actual = actual.lower() if isinstance(actual, str) else actual
            expected = expected.lower()
        if actual != expected:
            raise CohortFeatureError("response token address differs from the selected candidate")


def validate_flow_body(
    body: Any,
    *,
    candidate: Any,
    label: str,
    cutoff: datetime,
) -> tuple[dict[str, Any], ...]:
    collection_start = _utc(cutoff, field="collection start")
    if collection_start.minute != 5 or collection_start.second or collection_start.microsecond:
        raise CohortFeatureError("collection start must be aligned to HH:05:00 UTC")
    boundary = collection_start.replace(minute=0)
    if label not in {"smart_money", "exchange"}:
        raise CohortFeatureError("flow label must be smart_money or exchange")
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise CohortFeatureError(f"{label} flow response data must be a list")
    if len(body["data"]) < 25:
        raise CohortFeatureError(f"{label} flow response has fewer than 25 completed rows")
    if len(body["data"]) > 26:
        raise CohortFeatureError(
            f"{label} flow response must contain only the 25-hour grid and one-hour buffer"
        )
    _validate_optional_identity(body, candidate)
    pagination = body.get("pagination")
    if (
        not isinstance(pagination, dict)
        or type(pagination.get("page")) is not int
        or pagination.get("page") != 1
        or type(pagination.get("per_page")) is not int
        or pagination.get("per_page") != 1000
        or pagination.get("is_last_page") is not True
    ):
        raise CohortFeatureError(f"{label} flow response must be complete page one")

    validated: list[dict[str, Any]] = []
    previous_end: datetime | None = None
    for index, source in enumerate(body["data"]):
        if not isinstance(source, dict):
            raise CohortFeatureError(f"{label} flow row {index} must be an object")
        date = parse_utc(source.get("date"), field=f"{label} row date")
        bucket_end = parse_utc(source.get("bucket_end"), field=f"{label} row bucket_end")
        if source.get("is_complete") is not True:
            raise CohortFeatureError(f"{label} flow row {index} is incomplete")
        if bucket_end - date != _HOUR:
            raise CohortFeatureError(f"{label} flow row {index} is not an hourly bucket")
        if previous_end is not None and bucket_end <= previous_end:
            raise CohortFeatureError(f"{label} flow rows are not strictly ordered")
        if date < boundary - timedelta(hours=26) or bucket_end > boundary:
            raise CohortFeatureError(f"{label} flow row is outside the requested bounds")
        previous_end = bucket_end
        row = dict(source)
        row["price_usd"] = _finite(row.get("price_usd"), field="flow price_usd", minimum=0.0)
        if row["price_usd"] <= 0:
            raise CohortFeatureError("flow price_usd must be positive")
        row["token_amount"] = _finite(
            row.get("token_amount"), field="flow token_amount", minimum=0.0
        )
        row["value_usd"] = _finite(row.get("value_usd"), field="flow value_usd", minimum=0.0)
        holders = source.get("holders_count")
        if (
            isinstance(holders, bool)
            or not isinstance(holders, int)
            or holders < 0
        ):
            raise CohortFeatureError("flow holders_count must be a nonnegative integer")
        row["holders_count"] = holders
        row["total_inflows_count"] = _directional_flow(
            source.get("total_inflows_count"),
            field="total_inflows_count",
            outflow=False,
        )
        row["total_outflows_count"] = _directional_flow(
            source.get("total_outflows_count"),
            field="total_outflows_count",
            outflow=True,
        )
        if label == "exchange":
            for field in _EXCHANGE_COMPONENTS:
                if source.get(field) is not None:
                    row[field] = _directional_flow(
                        source[field], field=field, outflow="outflows" in field
                    )
        row["date"] = utc_text(date)
        row["bucket_end"] = utc_text(bucket_end)
        validated.append(row)

    admitted = validated[-25:]
    admitted_ends = [parse_utc(row["bucket_end"], field="bucket_end") for row in admitted]
    required = [boundary - (24 - index) * _HOUR for index in range(25)]
    if admitted_ends != required:
        raise CohortFeatureError(
            f"{label} flow response does not end in the exact trailing 25-hour grid"
        )
    return tuple(admitted)


def validate_wbs_pages(
    pages: Sequence[dict[str, Any]],
    *,
    candidate: Any,
    side: str,
) -> BreadthEvidence:
    if side not in {"BUY", "SELL"}:
        raise CohortFeatureError("WBS side must be BUY or SELL")
    if not isinstance(pages, (tuple, list)) or not 1 <= len(pages) <= 2:
        raise CohortFeatureError("WBS evidence must contain one or two pages")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    directional = "bought_volume_usd" if side == "BUY" else "sold_volume_usd"
    previous_volume: float | None = None
    incomplete = False
    for page_number, body in enumerate(pages, start=1):
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise CohortFeatureError(f"WBS {side} page {page_number} data must be a list")
        if len(body["data"]) > 1000:
            raise CohortFeatureError(f"WBS {side} page exceeds per_page=1000")
        _validate_optional_identity(body, candidate)
        pagination = body.get("pagination")
        if (
            not isinstance(pagination, dict)
            or type(pagination.get("page")) is not int
            or pagination.get("page") != page_number
            or type(pagination.get("per_page")) is not int
            or pagination.get("per_page") != 1000
            or not isinstance(pagination.get("is_last_page"), bool)
        ):
            raise CohortFeatureError(f"WBS {side} page {page_number} pagination is invalid")
        last = pagination["is_last_page"]
        if page_number == 1 and last != (len(pages) == 1):
            raise CohortFeatureError(f"WBS {side} page sequence is inconsistent")
        if page_number == 2 and not last:
            incomplete = True
        for row_number, source in enumerate(body["data"]):
            if not isinstance(source, dict):
                raise CohortFeatureError(f"WBS {side} row {row_number} must be an object")
            address = source.get("address")
            if not isinstance(address, str) or not address:
                raise CohortFeatureError(f"WBS {side} address must be non-empty")
            normalized = address.lower() if address.lower().startswith("0x") else address
            if normalized in seen:
                raise CohortFeatureError(f"WBS {side} addresses must be unique across pages")
            seen.add(normalized)
            row = dict(source)
            for field in (
                "bought_token_volume", "sold_token_volume", "bought_volume_usd",
                "sold_volume_usd",
            ):
                if source.get(field) is not None:
                    row[field] = _finite(source[field], field=f"WBS {field}", minimum=0.0)
            for field in ("token_trade_volume", "trade_volume_usd"):
                if source.get(field) is not None:
                    row[field] = _finite(source[field], field=f"WBS {field}")
            if source.get(directional) is None:
                raise CohortFeatureError(
                    f"WBS {side} row is missing required {directional}"
                )
            volume = _finite(source[directional], field=directional, minimum=0.0)
            if previous_volume is not None and volume > previous_volume:
                raise CohortFeatureError(f"WBS {side} rows must be volume-descending")
            previous_volume = volume
            row["normalized_address"] = normalized
            row[directional] = volume
            rows.append(row)
    if incomplete:
        return BreadthEvidence(side, False, "page_two_not_final", tuple(rows), None, None)
    return BreadthEvidence(
        side=side,
        available=True,
        reason=None,
        rows=tuple(rows),
        distinct_addresses=len(rows),
        directional_volume_usd=sum(float(row[directional]) for row in rows),
    )


def signal_feature_rows(
    rows: Sequence[dict[str, Any]], *, source_id: str
) -> tuple[dict[str, Any], ...]:
    if len(rows) != 25:
        raise CohortFeatureError("signal feature input must contain exactly 25 rows")
    source = tuple(
        {
            "timestamp": row["bucket_end"],
            "price_usd": row["price_usd"],
            "token_amount": row["token_amount"],
            "holders_count": row["holders_count"],
        }
        for row in rows
    )
    try:
        return build_signal_features(
            source,
            horizons=(1, 4, 12),
            source_experiment_id=source_id,
            feature_set_version=SUPPORTED_FEATURE_SET,
        )
    except Exception as exc:
        raise CohortFeatureError(f"cannot build cohort signal features: {exc}") from exc


def build_predecision_features(
    *,
    smart_money_rows: Sequence[dict[str, Any]],
    exchange_rows: Sequence[dict[str, Any]],
    buyers: BreadthEvidence,
    sellers: BreadthEvidence,
    source_id: str,
) -> dict[str, Any]:
    smart_features = signal_feature_rows(smart_money_rows, source_id=f"{source_id}:sm")
    exchange_features = signal_feature_rows(exchange_rows, source_id=f"{source_id}:exchange")
    return {
        "schema_version": 1,
        "smart_money": {
            "final_feature": smart_features[-1],
            "prior_hour_feature": smart_features[-2],
            "row_count": len(smart_money_rows),
        },
        "exchange": {
            "final_feature": exchange_features[-1],
            "prior_hour_feature": exchange_features[-2],
            "row_count": len(exchange_rows),
            "component_rows": [
                {
                    key: row.get(key)
                    for key in ("bucket_end", *_EXCHANGE_COMPONENTS)
                    if key in row
                }
                for row in exchange_rows
            ],
        },
        "buyer_breadth": {
            "available": buyers.available,
            "reason": buyers.reason,
            "distinct_addresses": buyers.distinct_addresses,
            "directional_volume_usd": buyers.directional_volume_usd,
        },
        "seller_breadth": {
            "available": sellers.available,
            "reason": sellers.reason,
            "distinct_addresses": sellers.distinct_addresses,
            "directional_volume_usd": sellers.directional_volume_usd,
        },
    }


def h5_decision(features: dict[str, Any]) -> dict[str, Any]:
    try:
        smart = features["smart_money"]["final_feature"]
        exchange = features["exchange"]["final_feature"]
        buyer = features["buyer_breadth"]
        seller = features["seller_breadth"]
    except (KeyError, TypeError) as exc:
        raise CohortFeatureError("predecision features are incomplete") from exc
    required = {
        "smart_money_holdings_change_4h_pct": smart.get("holdings_change_4h_pct"),
        "exchange_inventory_change_4h_pct": exchange.get("holdings_change_4h_pct"),
        "buyer_addresses": buyer.get("distinct_addresses"),
        "seller_addresses": seller.get("distinct_addresses"),
        "buyer_volume_usd": buyer.get("directional_volume_usd"),
        "seller_volume_usd": seller.get("directional_volume_usd"),
    }
    unavailable = [
        name
        for name, value in required.items()
        if isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ]
    for name in ("buyer_addresses", "seller_addresses"):
        value = required[name]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            unavailable.append(name)
    if buyer.get("available") is not True or seller.get("available") is not True:
        unavailable.append("complete_buyer_seller_pagination")
    if unavailable:
        return {
            "rule_id": "buyer-breadth-exchange-comovement-v1",
            "availability": "UNAVAILABLE",
            "action": None,
            "predicates": required,
            "reasons": ["unavailable: " + ", ".join(sorted(set(unavailable)))],
        }
    matches = {
        "smart_money_accumulating_4h": float(required["smart_money_holdings_change_4h_pct"]) > 0,
        "buyer_breadth_positive": int(required["buyer_addresses"]) > int(required["seller_addresses"]),
        "buyer_volume_positive": float(required["buyer_volume_usd"]) > float(required["seller_volume_usd"]),
        "exchange_inventory_declining_4h": float(required["exchange_inventory_change_4h_pct"]) < 0,
    }
    fires = all(matches.values())
    return {
        "rule_id": "buyer-breadth-exchange-comovement-v1",
        "availability": "AVAILABLE",
        "action": "LONG" if fires else "ABSTAIN",
        "predicates": required,
        "matches": matches,
        "reasons": [
            "all frozen predicates matched"
            if fires
            else "frozen predicates did not match: "
            + ", ".join(name for name, matched in matches.items() if not matched)
        ],
    }
