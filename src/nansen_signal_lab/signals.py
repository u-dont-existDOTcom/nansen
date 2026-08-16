from __future__ import annotations
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Any

SUPPORTED_FEATURE_SET = "community-signals-v1"
_IDENTITY = {"timestamp", "asset_id", "token_address", "chain", "wallet_address", "holder_address", "entity_id", "token_id", "symbol"}
_AVAILABILITY = {"price_usd_available", "token_amount_available", "holders_count_available"}

class SignalError(ValueError):
    pass

def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, str): value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None: raise SignalError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)

def _safe_percent_change(start: float, end: float) -> float | None:
    return None if start == 0 else 100 * (end / start - 1)

def _contiguous_rows(rows, end: int, horizon: int):
    if end < horizon: return None
    window = rows[end-horizon:end+1]
    return window if all(b["_timestamp"]-a["_timestamp"] == timedelta(hours=1) for a,b in zip(window, window[1:])) else None

def _market_phase(change, price):
    if change is None or price is None: return "unavailable"
    if change > 0 and price <= 0: return "accumulation_divergence"
    if change > 0: return "markup"
    if change < 0 and price >= 0: return "distribution_divergence"
    if change < 0: return "markdown"
    return "flat"

def _validate(features):
    rows, seen = [], set()
    for source in features:
        row = dict(source); timestamp = _parse_timestamp(row.get("timestamp"))
        if timestamp in seen or (rows and timestamp <= rows[-1]["_timestamp"]): raise SignalError("timestamps must be unique and ordered")
        for name, lower in (("price_usd", 0), ("token_amount", 0)):
            value = row.get(name)
            if not isinstance(value, (int, float)) or not isfinite(value) or value <= lower if name == "price_usd" else not isinstance(value, (int, float)) or not isfinite(value) or value < lower:
                raise SignalError(f"invalid {name}")
        row["_timestamp"] = timestamp; rows.append(row); seen.add(timestamp)
    return tuple(rows)

def _metrics(rows, end, horizon):
    key = lambda name: f"{name}_{horizon}h" + {"holdings_change":"_pct", "price_return":"_pct", "holdings_velocity":"_pct_per_hour", "holdings_acceleration":"_pct_per_hour", "flow_price_divergence":"_pct"}.get(name, "")
    names = ("holdings_change", "price_return", "positive_holdings_delta_hours", "negative_holdings_delta_hours", "accumulation_persistence", "distribution_persistence", "holdings_velocity", "holdings_acceleration", "holder_count_change", "accumulation_retention", "flow_price_divergence")
    window = _contiguous_rows(rows, end, horizon)
    if window is None: return {**{key(name): None for name in names}, key("market_phase"): "unavailable"}
    start, finish = window[0], window[-1]; change = _safe_percent_change(start["token_amount"], finish["token_amount"]); price = _safe_percent_change(start["price_usd"], finish["price_usd"])
    deltas = [b["token_amount"]-a["token_amount"] for a,b in zip(window, window[1:])]; positive = sum(max(x, 0) for x in deltas)
    prior = _contiguous_rows(rows, end-horizon, horizon); prior_change = None if prior is None else _safe_percent_change(prior[0]["token_amount"], prior[-1]["token_amount"])
    breadth = None if any(item.get("holders_count") is None for item in window) else finish["holders_count"]-start["holders_count"]
    return {key("holdings_change"): change, key("price_return"): price, key("positive_holdings_delta_hours"): sum(x>0 for x in deltas), key("negative_holdings_delta_hours"): sum(x<0 for x in deltas), key("accumulation_persistence"): sum(x>0 for x in deltas)/horizon, key("distribution_persistence"): sum(x<0 for x in deltas)/horizon, key("holdings_velocity"): None if change is None else change/horizon, key("holdings_acceleration"): None if change is None or prior_change is None else change/horizon-prior_change/horizon, key("holder_count_change"): breadth, key("accumulation_retention"): None if positive == 0 else max(finish["token_amount"]-start["token_amount"], 0)/positive, key("flow_price_divergence"): None if change is None or price is None else change-price, key("market_phase"): _market_phase(change, price)}

def build_signal_features(features: tuple[dict[str, Any], ...], *, horizons: tuple[int, ...], source_experiment_id: str, feature_set_version: str):
    if feature_set_version != SUPPORTED_FEATURE_SET or not horizons or any(not isinstance(h, int) or h <= 0 for h in horizons) or len(set(horizons)) != len(horizons): raise SignalError("unsupported feature set or horizons")
    rows = _validate(features); output = []
    for index, source in enumerate(rows):
        row = {k:v for k,v in source.items() if k in _IDENTITY or k in _AVAILABILITY}; row.update(source_experiment_id=source_experiment_id, feature_set_version=feature_set_version)
        for horizon in horizons: row.update(_metrics(rows, index, horizon))
        output.append(row)
    return tuple(output)
