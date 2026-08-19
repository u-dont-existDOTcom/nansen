from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from .prospective_comparators import ComparatorDecision
from .prospective_snapshot import Candidate


class ExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ObservedFill:
    side: str
    notional_usd: float
    token_amount: float
    observed_usd: float
    vwap_usd: float
    trade_count: int


_FIVE_MINUTES = timedelta(minutes=5)
_ONE_MINUTE = timedelta(minutes=1)


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ExecutionError(f"{field} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExecutionError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExecutionError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return _utc(value, field="timestamp").isoformat().replace("+00:00", "Z")


def _finite_positive(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExecutionError(f"{field} must be finite and positive")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ExecutionError(f"{field} must be finite and positive")
    return number


def _finite_nonnegative(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExecutionError(f"{field} must be finite and non-negative")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ExecutionError(f"{field} must be finite and non-negative")
    return number


def _validate_candidate(candidate: Candidate) -> None:
    if (
        not isinstance(candidate, Candidate)
        or not candidate.chain
        or not candidate.token_address
    ):
        raise ExecutionError("candidate must contain a chain and token address")


def dex_trade_payload(
    candidate: Candidate,
    action: str,
    start: datetime,
    end: datetime,
    page: int,
) -> dict[str, Any]:
    _validate_candidate(candidate)
    start_utc = _utc(start, field="DEX start")
    end_utc = _utc(end, field="DEX end")
    if end_utc <= start_utc:
        raise ExecutionError("DEX end must be after start")
    if action not in {"BUY", "SELL"}:
        raise ExecutionError("DEX action must be BUY or SELL")
    if not isinstance(page, int) or isinstance(page, bool) or page not in {1, 2}:
        raise ExecutionError("DEX page must be 1 or 2")
    return {
        "chain": candidate.chain,
        "token_address": candidate.token_address,
        "only_smart_money": False,
        "date": {"from": _utc_text(start_utc), "to": _utc_text(end_utc)},
        "pagination": {"page": page, "per_page": 1000},
        "filters": {"action": action},
        "order_by": [
            {"field": "block_timestamp", "direction": "ASC"},
            {"field": "transaction_hash", "direction": "ASC"},
        ],
    }


def ohlcv_payload(
    candidate: Candidate,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    _validate_candidate(candidate)
    start_utc = _utc(start, field="OHLCV start")
    end_utc = _utc(end, field="OHLCV end")
    if end_utc <= start_utc:
        raise ExecutionError("OHLCV end must be after start")
    if start_utc.second or start_utc.microsecond or start_utc.minute % 5:
        raise ExecutionError("OHLCV start must be aligned to five minutes")
    if end_utc.second or end_utc.microsecond or end_utc.minute % 5:
        raise ExecutionError("OHLCV end must be aligned to five minutes")
    return {
        "chain": candidate.chain,
        "token_address": candidate.token_address,
        "date": {"from": _utc_text(start_utc), "to": _utc_text(end_utc)},
        "timeframe": "5m",
    }


def _floor_five(value: datetime) -> datetime:
    utc = _utc(value, field="timestamp")
    return utc.replace(minute=(utc.minute // 5) * 5, second=0, microsecond=0)


def ohlcv_bounds(t0: datetime, exit_window_end: datetime) -> tuple[datetime, datetime]:
    decision_at = _utc(t0, field="t0")
    exit_at = _utc(exit_window_end, field="exit_window_end")
    if exit_at <= decision_at:
        raise ExecutionError("exit window must end after t0")
    start = _floor_five(decision_at)
    exit_floor = _floor_five(exit_at)
    exclusive_end = exit_floor if exit_at == exit_floor else exit_floor + _FIVE_MINUTES
    return start, exclusive_end


def earliest_settlement_at(t0: datetime, exit_window_end: datetime) -> datetime:
    return ohlcv_bounds(t0, exit_window_end)[1] + _ONE_MINUTE


def _validated_trade_rows(
    pages: Sequence[dict[str, Any]],
    *,
    side: str,
    start: datetime,
    end: datetime,
) -> tuple[dict[str, Any], ...]:
    start_utc = _utc(start, field="trade window start")
    end_utc = _utc(end, field="trade window end")
    if end_utc <= start_utc:
        raise ExecutionError("trade window end must be after start")
    if not isinstance(pages, (tuple, list)) or not 1 <= len(pages) <= 2:
        raise ExecutionError("trade window must contain one or two pages")

    rows: list[dict[str, Any]] = []
    previous_key: tuple[datetime, str] | None = None
    for index, body in enumerate(pages, start=1):
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise ExecutionError(f"DEX page {index} data must be a list")
        pagination = body.get("pagination")
        if not isinstance(pagination, dict):
            raise ExecutionError(f"DEX page {index} pagination is missing")
        if (
            pagination.get("page") != index
            or pagination.get("per_page") != 1000
            or not isinstance(pagination.get("is_last_page"), bool)
        ):
            raise ExecutionError(f"DEX page {index} pagination is invalid")
        is_last = pagination["is_last_page"]
        if index == 1 and is_last and len(pages) != 1:
            raise ExecutionError("a final page 1 cannot be followed by page 2")
        if index == 1 and not is_last and len(pages) != 2:
            raise ExecutionError("non-final page 1 requires page 2")
        if index == 2 and not is_last:
            raise ExecutionError("DEX page 2 must be final")

        for row_number, raw_row in enumerate(body["data"]):
            if not isinstance(raw_row, dict):
                raise ExecutionError(f"DEX row {row_number} on page {index} must be an object")
            timestamp = _timestamp(raw_row.get("block_timestamp"), field="block_timestamp")
            tx_hash = raw_row.get("transaction_hash")
            if not isinstance(tx_hash, str) or not tx_hash:
                raise ExecutionError("transaction_hash must be a non-empty string")
            key = (timestamp, tx_hash)
            if previous_key is not None and key <= previous_key:
                raise ExecutionError("DEX rows must be strictly increasing across all pages")
            previous_key = key
            if not start_utc <= timestamp < end_utc:
                raise ExecutionError("DEX row is outside the half-open observation window")
            if raw_row.get("action") != side:
                raise ExecutionError(f"DEX row has wrong action for {side} window")

            amount = _finite_positive(raw_row.get("token_amount"), field="token_amount")
            price = _finite_positive(
                raw_row.get("estimated_swap_price_usd"),
                field="estimated_swap_price_usd",
            )
            value = _finite_positive(
                raw_row.get("estimated_value_usd"),
                field="estimated_value_usd",
            )
            tolerance = max(0.01, 0.01 * value)
            difference = abs(amount * price - value)
            if difference > tolerance and not math.isclose(
                difference, tolerance, rel_tol=1e-12, abs_tol=1e-12
            ):
                raise ExecutionError("DEX trade amount, price, and value are inconsistent")
            row = dict(raw_row)
            row["_amount"] = amount
            row["_price"] = price
            row["_value"] = value
            rows.append(row)
    return tuple(rows)


def build_entry_fill(
    pages: Sequence[dict[str, Any]],
    virtual_notional_usd: float,
    *,
    start: datetime,
    end: datetime,
) -> ObservedFill | None:
    target = _finite_positive(virtual_notional_usd, field="virtual_notional_usd")
    rows = _validated_trade_rows(pages, side="BUY", start=start, end=end)
    remaining = target
    observed = 0.0
    tokens = 0.0
    count = 0
    for row in rows:
        used_usd = min(remaining, row["_value"])
        fraction = used_usd / row["_value"]
        observed += used_usd
        tokens += row["_amount"] * fraction
        remaining -= used_usd
        count += 1
        if remaining <= max(1e-12, target * 1e-12):
            break
    if remaining > max(1e-12, target * 1e-12):
        return None
    return ObservedFill("BUY", target, tokens, observed, observed / tokens, count)


def build_exit_fill(
    pages: Sequence[dict[str, Any]],
    entry_token_amount: float,
    *,
    start: datetime,
    end: datetime,
) -> ObservedFill | None:
    target_tokens = _finite_positive(entry_token_amount, field="entry_token_amount")
    rows = _validated_trade_rows(pages, side="SELL", start=start, end=end)
    remaining = target_tokens
    observed = 0.0
    tokens = 0.0
    count = 0
    for row in rows:
        used_tokens = min(remaining, row["_amount"])
        fraction = used_tokens / row["_amount"]
        tokens += used_tokens
        observed += row["_value"] * fraction
        remaining -= used_tokens
        count += 1
        if remaining <= max(1e-12, target_tokens * 1e-12):
            break
    if remaining > max(1e-12, target_tokens * 1e-12):
        return None
    return ObservedFill("SELL", observed, tokens, observed, observed / tokens, count)


def _required_grid(start: datetime, exclusive_end: datetime) -> tuple[datetime, ...]:
    result: list[datetime] = []
    cursor = start
    while cursor < exclusive_end:
        result.append(cursor)
        cursor += _FIVE_MINUTES
    return tuple(result)


def validate_closed_ohlcv(
    body: dict[str, Any],
    *,
    required_start: datetime,
    required_exit: datetime,
    retrieved_at: datetime,
) -> tuple[dict[str, Any], ...]:
    start = _utc(required_start, field="required_start")
    exclusive_end = _utc(required_exit, field="required_exit")
    retrieved = _utc(retrieved_at, field="retrieved_at")
    if exclusive_end <= start:
        raise ExecutionError("required_exit must be after required_start")
    if _floor_five(start) != start or _floor_five(exclusive_end) != exclusive_end:
        raise ExecutionError("OHLCV bounds must align to five minutes")
    if not isinstance(body, dict) or body.get("truncated") is not False:
        raise ExecutionError("OHLCV response is truncated or lacks explicit completeness")
    raw_rows = body.get("data")
    if not isinstance(raw_rows, list):
        raise ExecutionError("OHLCV data must be a list")

    admitted: list[dict[str, Any]] = []
    admitted_times: list[datetime] = []
    previous: datetime | None = None
    for index, raw_row in enumerate(raw_rows):
        if not isinstance(raw_row, dict):
            raise ExecutionError(f"OHLCV row {index} must be an object")
        interval = _timestamp(raw_row.get("interval_start"), field="interval_start")
        if previous is not None and interval <= previous:
            raise ExecutionError("OHLCV intervals must be strictly increasing")
        previous = interval
        if interval < start or interval > exclusive_end:
            raise ExecutionError("OHLCV interval is outside requested bounds")
        # Providers may treat the request's upper bound as inclusive. That
        # boundary candle is outside our half-open grid and may still be open.
        if interval == exclusive_end:
            continue
        if interval + _FIVE_MINUTES > retrieved:
            raise ExecutionError("OHLCV interval is not closed at retrieval time")

        row = dict(raw_row)
        for field in ("open", "high", "low", "close"):
            row[field] = _finite_positive(row.get(field), field=f"OHLCV {field}")
        row["volume"] = _finite_nonnegative(row.get("volume"), field="OHLCV volume")
        row["market_cap"] = _finite_nonnegative(
            row.get("market_cap"), field="OHLCV market_cap"
        )
        if row["high"] < max(row["open"], row["close"], row["low"]):
            raise ExecutionError("OHLCV high is inconsistent with candle prices")
        if row["low"] > min(row["open"], row["close"], row["high"]):
            raise ExecutionError("OHLCV low is inconsistent with candle prices")
        admitted.append(row)
        admitted_times.append(interval)

    if tuple(admitted_times) != _required_grid(start, exclusive_end):
        raise ExecutionError("OHLCV intervals must form the exact contiguous five-minute grid")
    return tuple(admitted)


def _score_action(
    action: str,
    *,
    entry_fill: ObservedFill | None,
    exit_fill: ObservedFill | None,
    virtual_notional_usd: float,
) -> dict[str, Any]:
    if action == "ABSTAIN":
        return {"action": action, "status": "SCORED", "net_return": 0.0}
    if action != "LONG":
        raise ExecutionError(f"unsupported scored action: {action}")
    if entry_fill is None or exit_fill is None:
        return {"action": action, "status": "UNFILLED", "net_return": None}
    if entry_fill.side != "BUY" or exit_fill.side != "SELL":
        raise ExecutionError("observed fills have the wrong side")
    exit_usd = _finite_positive(exit_fill.observed_usd, field="exit observed_usd")
    return {
        "action": action,
        "status": "SCORED",
        "net_return": exit_usd / virtual_notional_usd - 1,
    }


def _ohlcv_summary(ohlcv: Iterable[dict[str, Any]]) -> dict[str, float | None]:
    rows = tuple(ohlcv)
    if not rows:
        return {
            "gross_ohlcv_return": None,
            "ohlcv_mfe": None,
            "ohlcv_mae": None,
            "ohlcv_volume": None,
        }
    first_open = _finite_positive(rows[0].get("open"), field="first OHLCV open")
    last_close = _finite_positive(rows[-1].get("close"), field="last OHLCV close")
    highs = [_finite_positive(row.get("high"), field="OHLCV high") for row in rows]
    lows = [_finite_positive(row.get("low"), field="OHLCV low") for row in rows]
    volumes = [_finite_nonnegative(row.get("volume"), field="OHLCV volume") for row in rows]
    return {
        "gross_ohlcv_return": last_close / first_open - 1,
        "ohlcv_mfe": max(highs) / first_open - 1,
        "ohlcv_mae": min(lows) / first_open - 1,
        "ohlcv_volume": sum(volumes),
    }


def score_decisions(
    *,
    pass1_action: str,
    pass2_action: str,
    comparator_decisions: tuple[ComparatorDecision, ...],
    entry_fill: ObservedFill | None,
    exit_fill: ObservedFill | None,
    ohlcv: tuple[dict[str, Any], ...],
    virtual_notional_usd: float,
) -> dict[str, Any]:
    notional = _finite_positive(virtual_notional_usd, field="virtual_notional_usd")
    pass1 = _score_action(
        pass1_action,
        entry_fill=entry_fill,
        exit_fill=exit_fill,
        virtual_notional_usd=notional,
    )
    pass2 = _score_action(
        pass2_action,
        entry_fill=entry_fill,
        exit_fill=exit_fill,
        virtual_notional_usd=notional,
    )

    comparator_scores: list[dict[str, Any]] = []
    for decision in comparator_decisions:
        record: dict[str, Any] = {
            "decision_id": decision.decision_id,
            "theory_id": decision.theory_id,
            "variant": decision.variant,
            "availability": decision.availability,
            "applicable": decision.applicable,
            "action": decision.action,
            "status": decision.availability,
            "net_return": None,
            "reasons": list(decision.reasons),
        }
        if decision.availability == "AVAILABLE":
            if decision.role == "veto" and decision.variant == "base":
                record["status"] = "METADATA"
            elif not decision.applicable:
                record["status"] = "NOT_APPLICABLE"
            elif decision.action in {"LONG", "ABSTAIN"}:
                scored = _score_action(
                    decision.action,
                    entry_fill=entry_fill,
                    exit_fill=exit_fill,
                    virtual_notional_usd=notional,
                )
                record["status"] = scored["status"]
                record["net_return"] = scored["net_return"]
            else:
                record["status"] = "UNSCORABLE"
        comparator_scores.append(record)

    comparison_roles = {"entry", "reference", "comparison"}
    unresolved_base = any(
        decision.variant == "base"
        and decision.role in comparison_roles
        and decision.availability != "AVAILABLE"
        for decision in comparator_decisions
    )
    applicable = [
        record for record in comparator_scores
        if record["applicable"] and record["availability"] != "BLOCKED"
    ]
    applicable_unscorable = any(record["status"] != "SCORED" for record in applicable)
    if pass2["status"] != "SCORED" or unresolved_base or applicable_unscorable:
        headline: bool | str = "unscorable"
    elif not applicable:
        headline = "not_tested"
    else:
        comparator_max = max(float(record["net_return"]) for record in applicable)
        headline = float(pass2["net_return"]) > comparator_max

    market = _ohlcv_summary(ohlcv)
    gross = market["gross_ohlcv_return"]
    divergence = (
        float(pass2["net_return"]) - float(gross)
        if pass2["net_return"] is not None and gross is not None
        else None
    )
    return {
        "pass1": pass1,
        "pass2": pass2,
        "comparators": comparator_scores,
        "cash_benchmark_return": 0.0,
        **market,
        "dex_ohlcv_divergence": divergence,
        "gpt_beats_frozen_strategies": headline,
    }
