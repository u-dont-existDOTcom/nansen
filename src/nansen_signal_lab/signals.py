from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any


SUPPORTED_FEATURE_SET = "community-signals-v1"
_IDENTITY_FIELDS = {"timestamp", "asset_id", "token_address", "chain", "wallet_address", "holder_address", "entity_id", "token_id", "symbol"}
_RAW_AVAILABILITY_FIELDS = {"price_usd_available", "token_amount_available", "holders_count_available"}


class SignalError(ValueError):
    pass


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise SignalError("timestamp must be datetime or ISO string")
    if value.tzinfo is None:
        raise SignalError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _safe_percent_change(start: float, end: float) -> float | None:
    if start == 0:
        return None
    return 100 * (end / start - 1)


def _contiguous_rows(rows: tuple[dict[str, Any], ...], end: int, horizon: int) -> tuple[dict[str, Any], ...] | None:
    if end < horizon:
        return None
    window = rows[end - horizon : end + 1]
    if any(window[i]["_timestamp"] - window[i - 1]["_timestamp"] != timedelta(hours=1) for i in range(1, len(window))):
        return None
    return window


def _market_phase(change: float | None, price_return: float | None) -> str:
    if change is None or price_return is None:
        return "unavailable"
    if change > 0 and price_return <= 0:
        return "accumulation_divergence"
    if change > 0 and price_return > 0:
        return "markup"
    if change < 0 and price_return >= 0:
        return "distribution_divergence"
    if change < 0 and price_return < 0:
        return "markdown"
    return "flat"


def _validate_rows(features: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    rows = []
    seen = set()
    for source in features:
        row = dict(source)
        timestamp = _parse_timestamp(row.get("timestamp"))
        if timestamp in seen:
            raise SignalError("duplicate timestamp")
        if seen and timestamp <= rows[-1]["_timestamp"]:
            raise SignalError("timestamps must be ordered")
        seen.add(timestamp)
        for field in ("price_usd", "token_amount"):
            value = row.get(field)
            if not isinstance(value, (int, float)) or not isfinite(value) or (field == "price_usd" and value <= 0) or (field == "token_amount" and value < 0):
                raise SignalError(f"invalid {field}")
        row["_timestamp"] = timestamp
        rows.append(row)
    return tuple(rows)


def _metrics(rows: tuple[dict[str, Any], ...], end: int, horizon: int) -> dict[str, Any]:
    window = _contiguous_rows(rows, end, horizon)
    def key(name: str) -> str:
        suffix = {"holdings_change": "_pct", "price_return": "_pct", "holdings_velocity": "_pct_per_hour", "holdings_acceleration": "_pct_per_hour", "flow_price_divergence": "_pct"}.get(name, "")
        return f"{name}_{horizon}h{suffix}"

    names = ("holdings_change_pct", "price_return_pct", "positive_holdings_delta_hours", "negative_holdings_delta_hours", "accumulation_persistence", "distribution_persistence", "holdings_velocity_pct_per_hour", "holdings_acceleration_pct_per_hour", "holder_count_change", "accumulation_retention", "flow_price_divergence_pct")
    names = tuple(name.replace("holdings_change_pct", "holdings_change").replace("price_return_pct", "price_return").replace("holdings_velocity_pct_per_hour", "holdings_velocity").replace("holdings_acceleration_pct_per_hour", "holdings_acceleration").replace("flow_price_divergence_pct", "flow_price_divergence") for name in names)
    if window is None:
        return {key(name): None for name in names} | {key("market_phase"): "unavailable"}
    start, finish = window[0], window[-1]
    holding_change = _safe_percent_change(start["token_amount"], finish["token_amount"])
    price_return = _safe_percent_change(start["price_usd"], finish["price_usd"])
    deltas = [b["token_amount"] - a["token_amount"] for a, b in zip(window, window[1:])]
    gross_positive = sum(max(delta, 0) for delta in deltas)
    prior = _contiguous_rows(rows, end - horizon, horizon)
    prior_velocity = None if prior is None else _safe_percent_change(prior[0]["token_amount"], prior[-1]["token_amount"])
    prior_velocity = None if prior_velocity is None else prior_velocity / horizon
    breadth = None if any(item.get("holders_count") is None for item in window) else finish["holders_count"] - start["holders_count"]
    return {
        key("holdings_change"): holding_change, key("price_return"): price_return,
        key("positive_holdings_delta_hours"): sum(delta > 0 for delta in deltas),
        key("negative_holdings_delta_hours"): sum(delta < 0 for delta in deltas),
        key("accumulation_persistence"): sum(delta > 0 for delta in deltas) / horizon,
        key("distribution_persistence"): sum(delta < 0 for delta in deltas) / horizon,
        key("holdings_velocity"): None if holding_change is None else holding_change / horizon,
        key("holdings_acceleration"): None if holding_change is None or prior_velocity is None else holding_change / horizon - prior_velocity,
        key("holder_count_change"): breadth,
        key("accumulation_retention"): None if gross_positive == 0 else max(finish["token_amount"] - start["token_amount"], 0) / gross_positive,
        key("flow_price_divergence"): None if holding_change is None or price_return is None else holding_change - price_return,
        key("market_phase"): _market_phase(holding_change, price_return),
    }


def build_signal_features(features: tuple[dict[str, Any], ...], *, horizons: tuple[int, ...], source_experiment_id: str, feature_set_version: str) -> tuple[dict[str, Any], ...]:
    if feature_set_version != SUPPORTED_FEATURE_SET:
        raise SignalError(f"unsupported feature set: {feature_set_version}")
    if not horizons or any(not isinstance(h, int) or h <= 0 for h in horizons) or len(set(horizons)) != len(horizons):
        raise SignalError("horizons must be positive and unique")
    rows = _validate_rows(features)
    output = []
    for index, source in enumerate(rows):
        result = {key: value for key, value in source.items() if key in _IDENTITY_FIELDS or key in _RAW_AVAILABILITY_FIELDS}
        result.update({"source_experiment_id": source_experiment_id, "feature_set_version": feature_set_version})
        for horizon in horizons:
            result.update(_metrics(rows, index, horizon))
        output.append(result)
    return tuple(output)
