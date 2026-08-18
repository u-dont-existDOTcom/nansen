from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from .cohort_schema import parse_utc, utc_text


class CohortExecutionError(RuntimeError):
    """Raised when counterfactual execution evidence is unsafe to score."""


@dataclass(frozen=True)
class ObservedFill:
    side: str
    requested_amount: float
    filled_token_amount: float
    observed_usd: float
    vwap_usd: float | None
    trade_count: int
    fill_ratio: float
    is_complete: bool


_FIVE_MINUTES = timedelta(minutes=5)
_MICROSECOND = timedelta(microseconds=1)


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CohortExecutionError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite(value: Any, *, field: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CohortExecutionError(f"{field} must be finite")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0) or (not positive and number < 0):
        qualifier = "positive" if positive else "nonnegative"
        raise CohortExecutionError(f"{field} must be finite and {qualifier}")
    return number


def _identity(candidate: Any) -> tuple[str, str]:
    if isinstance(candidate, dict):
        chain = candidate.get("chain")
        address = candidate.get("token_address")
    else:
        chain = getattr(candidate, "chain", None)
        address = getattr(candidate, "token_address", None)
    if not isinstance(chain, str) or not chain or not isinstance(address, str) or not address:
        raise CohortExecutionError("candidate identity is missing")
    return chain, address


def _same_address(expected: str, actual: Any) -> bool:
    if not isinstance(actual, str):
        return False
    if expected.lower().startswith("0x"):
        return expected.lower() == actual.lower()
    return expected == actual


def execution_windows(cutoff: datetime) -> dict[str, datetime]:
    t0 = _utc(cutoff, field="cutoff")
    if t0.minute % 5 or t0.second or t0.microsecond:
        raise CohortExecutionError("decision t0 must be aligned to a five-minute boundary")
    return {
        "entry_start": t0 + timedelta(minutes=5),
        "entry_end": t0 + timedelta(minutes=15),
        "exit_start": t0 + timedelta(hours=4, minutes=5),
        "exit_end": t0 + timedelta(hours=4, minutes=15),
        "ohlcv_start": t0.replace(minute=(t0.minute // 5) * 5),
        "ohlcv_end": t0 + timedelta(hours=4, minutes=15),
        "earliest_settlement": t0 + timedelta(hours=4, minutes=21),
    }


def dex_payload(
    candidate: Any,
    *,
    side: str,
    start: datetime,
    end: datetime,
    page: int,
) -> dict[str, Any]:
    chain, address = _identity(candidate)
    start_at = _utc(start, field="DEX start")
    end_at = _utc(end, field="DEX end")
    if end_at <= start_at:
        raise CohortExecutionError("DEX end must be after start")
    if side not in {"BUY", "SELL"}:
        raise CohortExecutionError("DEX side must be BUY or SELL")
    if not isinstance(page, int) or isinstance(page, bool) or page not in {1, 2}:
        raise CohortExecutionError("DEX page must be 1 or 2")
    return {
        "chain": chain,
        "token_address": address,
        "only_smart_money": False,
        "date": {
            "from": utc_text(start_at),
            "to": utc_text(end_at - _MICROSECOND),
        },
        "pagination": {"page": page, "per_page": 1000},
        "filters": {"action": side},
        "order_by": [
            {"field": "block_timestamp", "direction": "ASC"},
            {"field": "transaction_hash", "direction": "ASC"},
        ],
    }


def ohlcv_payload(candidate: Any, *, start: datetime, end: datetime) -> dict[str, Any]:
    chain, address = _identity(candidate)
    start_at = _utc(start, field="OHLCV start")
    end_at = _utc(end, field="OHLCV end")
    if end_at <= start_at or any(
        (value.minute % 5 or value.second or value.microsecond)
        for value in (start_at, end_at)
    ):
        raise CohortExecutionError("OHLCV bounds must be increasing five-minute boundaries")
    return {
        "chain": chain,
        "token_address": address,
        "date": {"from": utc_text(start_at), "to": utc_text(end_at)},
        "timeframe": "5m",
    }


def validate_trade_pages(
    pages: Sequence[dict[str, Any]],
    *,
    candidate: Any,
    side: str,
    start: datetime,
    end: datetime,
) -> tuple[dict[str, Any], ...]:
    chain, token = _identity(candidate)
    del chain  # The response schema does not expose chain; the request archive binds it.
    start_at = _utc(start, field="trade start")
    end_at = _utc(end, field="trade end")
    if side not in {"BUY", "SELL"}:
        raise CohortExecutionError("trade side must be BUY or SELL")
    if not isinstance(pages, (list, tuple)) or not 1 <= len(pages) <= 2:
        raise CohortExecutionError("DEX evidence must contain one or two pages")
    rows: list[dict[str, Any]] = []
    previous: tuple[datetime, str] | None = None
    for page_number, body in enumerate(pages, start=1):
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise CohortExecutionError(f"DEX {side} page {page_number} data must be a list")
        pagination = body.get("pagination")
        if (
            not isinstance(pagination, dict)
            or pagination.get("page") != page_number
            or isinstance(pagination.get("page"), bool)
            or pagination.get("per_page") != 1000
            or isinstance(pagination.get("per_page"), bool)
            or not isinstance(pagination.get("is_last_page"), bool)
        ):
            raise CohortExecutionError(f"DEX {side} pagination is invalid")
        last = pagination["is_last_page"]
        if page_number == 1 and last != (len(pages) == 1):
            raise CohortExecutionError(f"DEX {side} page sequence is inconsistent")
        if page_number == 2 and not last:
            raise CohortExecutionError(f"DEX {side} exceeds the two-page ceiling")
        for row_number, source in enumerate(body["data"]):
            if not isinstance(source, dict):
                raise CohortExecutionError(f"DEX {side} row {row_number} must be an object")
            timestamp = parse_utc(source.get("block_timestamp"), field="block_timestamp")
            transaction = source.get("transaction_hash")
            if not isinstance(transaction, str) or not transaction:
                raise CohortExecutionError("DEX transaction_hash must be non-empty")
            key = (timestamp, transaction)
            if previous is not None and key <= previous:
                raise CohortExecutionError(
                    "DEX rows must be strictly ordered and cannot contain ambiguous multi-leg keys"
                )
            previous = key
            if not start_at <= timestamp < end_at:
                raise CohortExecutionError("DEX row is outside the half-open window")
            if source.get("action") != side:
                raise CohortExecutionError("DEX row action differs from the requested side")
            if not _same_address(token, source.get("token_address")):
                raise CohortExecutionError("DEX row token differs from the selected token")
            for field in (
                "trader_address", "token_name", "traded_token_address",
                "traded_token_name",
            ):
                if not isinstance(source.get(field), str) or not source[field]:
                    raise CohortExecutionError(f"DEX {field} must be non-empty")
            amount = _finite(source.get("token_amount"), field="token_amount", positive=True)
            _finite(
                source.get("traded_token_amount"),
                field="traded_token_amount",
                positive=True,
            )
            price = _finite(
                source.get("estimated_swap_price_usd"),
                field="estimated_swap_price_usd",
                positive=True,
            )
            value = _finite(
                source.get("estimated_value_usd"),
                field="estimated_value_usd",
                positive=True,
            )
            tolerance = max(0.01, 0.01 * value)
            if abs(amount * price - value) > tolerance:
                raise CohortExecutionError("DEX amount, price and value are inconsistent")
            row = dict(source)
            row["_amount"] = amount
            row["_price"] = price
            row["_value"] = value
            rows.append(row)
    return tuple(rows)


def build_entry_fill(
    pages: Sequence[dict[str, Any]],
    *,
    candidate: Any,
    notional_usd: float,
    start: datetime,
    end: datetime,
) -> ObservedFill:
    target = _finite(notional_usd, field="notional_usd", positive=True)
    rows = validate_trade_pages(
        pages, candidate=candidate, side="BUY", start=start, end=end
    )
    remaining = target
    tokens = 0.0
    observed = 0.0
    count = 0
    for row in rows:
        used = min(remaining, row["_value"])
        fraction = used / row["_value"]
        tokens += row["_amount"] * fraction
        observed += used
        remaining -= used
        count += 1
        if remaining <= max(1e-12, target * 1e-12):
            break
    complete = remaining <= max(1e-12, target * 1e-12)
    return ObservedFill(
        "BUY",
        target,
        tokens,
        observed,
        None if tokens == 0 else observed / tokens,
        count,
        min(1.0, observed / target),
        complete,
    )


def build_exit_fill(
    pages: Sequence[dict[str, Any]],
    *,
    candidate: Any,
    token_amount: float,
    start: datetime,
    end: datetime,
) -> ObservedFill:
    target = _finite(token_amount, field="token_amount", positive=True)
    rows = validate_trade_pages(
        pages, candidate=candidate, side="SELL", start=start, end=end
    )
    remaining = target
    tokens = 0.0
    observed = 0.0
    count = 0
    for row in rows:
        used = min(remaining, row["_amount"])
        fraction = used / row["_amount"]
        tokens += used
        observed += row["_value"] * fraction
        remaining -= used
        count += 1
        if remaining <= max(1e-12, target * 1e-12):
            break
    complete = remaining <= max(1e-12, target * 1e-12)
    return ObservedFill(
        "SELL",
        target,
        tokens,
        observed,
        None if tokens == 0 else observed / tokens,
        count,
        min(1.0, tokens / target),
        complete,
    )


def _market_cap(value: Any) -> dict[str, float | None]:
    if not isinstance(value, dict):
        raise CohortExecutionError("OHLCV market_cap must be an object")
    result: dict[str, float | None] = {}
    for field in ("open", "high", "low", "close"):
        raw = value.get(field)
        result[field] = None if raw is None else _finite(raw, field=f"market_cap.{field}")
    present = [number for number in result.values() if number is not None]
    if result["high"] is not None and result["high"] < max(present):
        raise CohortExecutionError("OHLCV market_cap high is inconsistent")
    if result["low"] is not None and result["low"] > min(present):
        raise CohortExecutionError("OHLCV market_cap low is inconsistent")
    return result


def validate_ohlcv(
    body: Any,
    *,
    candidate: Any,
    start: datetime,
    end: datetime,
    retrieved_at: datetime,
) -> tuple[dict[str, Any], ...]:
    chain, address = _identity(candidate)
    start_at = _utc(start, field="OHLCV start")
    end_at = _utc(end, field="OHLCV end")
    retrieved = _utc(retrieved_at, field="OHLCV retrieved_at")
    if not isinstance(body, dict) or body.get("truncated") is not False:
        raise CohortExecutionError("OHLCV response must explicitly be complete")
    if (
        body.get("chain") != chain
        or not _same_address(address, body.get("token_address"))
        or body.get("timeframe") != "5m"
    ):
        raise CohortExecutionError("OHLCV response identity differs from the request")
    if not isinstance(body.get("data"), list):
        raise CohortExecutionError("OHLCV data must be a list")
    required: list[datetime] = []
    cursor = start_at
    while cursor <= end_at:
        required.append(cursor)
        cursor += _FIVE_MINUTES
    admitted: list[dict[str, Any]] = []
    actual: list[datetime] = []
    for index, source in enumerate(body["data"]):
        if not isinstance(source, dict):
            raise CohortExecutionError(f"OHLCV row {index} must be an object")
        interval = parse_utc(source.get("interval_start"), field="interval_start")
        if not start_at <= interval <= end_at:
            raise CohortExecutionError("OHLCV row is outside the requested grid")
        if interval + _FIVE_MINUTES > retrieved:
            raise CohortExecutionError("OHLCV row was not closed at retrieval")
        row = dict(source)
        for field in ("open", "high", "low", "close"):
            row[field] = _finite(source.get(field), field=f"OHLCV {field}", positive=True)
        row["volume"] = _finite(source.get("volume"), field="OHLCV volume")
        row["volume_usd"] = _finite(source.get("volume_usd"), field="OHLCV volume_usd")
        row["market_cap"] = _market_cap(source.get("market_cap"))
        if row["high"] < max(row["open"], row["close"], row["low"]):
            raise CohortExecutionError("OHLCV high is inconsistent")
        if row["low"] > min(row["open"], row["close"], row["high"]):
            raise CohortExecutionError("OHLCV low is inconsistent")
        actual.append(interval)
        admitted.append(row)
    if actual != required:
        raise CohortExecutionError("OHLCV rows must form the exact contiguous grid")
    return tuple(admitted)


def score_counterfactual(
    *,
    entry_fill: ObservedFill | None,
    exit_fill: ObservedFill | None,
    ohlcv: Sequence[dict[str, Any]],
    notional_usd: float,
) -> dict[str, Any]:
    notional = _finite(notional_usd, field="notional_usd", positive=True)
    if entry_fill is None or not entry_fill.is_complete:
        status = "UNFILLED_ENTRY"
        gross = None
    elif exit_fill is None or not exit_fill.is_complete:
        status = "UNFILLED_EXIT"
        gross = None
    else:
        status = "SCORED"
        gross = exit_fill.observed_usd / notional - 1.0
    rows = tuple(ohlcv)
    if not rows:
        raise CohortExecutionError("OHLCV evidence cannot be empty")
    opening = _finite(rows[0].get("open"), field="first OHLCV open", positive=True)
    closes = [_finite(row.get("close"), field="OHLCV close", positive=True) for row in rows]
    highs = [_finite(row.get("high"), field="OHLCV high", positive=True) for row in rows]
    lows = [_finite(row.get("low"), field="OHLCV low", positive=True) for row in rows]
    return {
        "schema_version": 1,
        "status": status,
        "notional_usd": notional,
        "entry_fill": None if entry_fill is None else asdict(entry_fill),
        "exit_fill": None if exit_fill is None else asdict(exit_fill),
        "gross_return": gross,
        "base_return_100bps": (
            None if gross is None else (1.0 + gross) * (1.0 - 0.0100) ** 2 - 1.0
        ),
        "stress_return_250bps": (
            None if gross is None else (1.0 + gross) * (1.0 - 0.0250) ** 2 - 1.0
        ),
        "ohlcv_return": closes[-1] / opening - 1.0,
        "mfe": max(highs) / opening - 1.0,
        "mae": min(lows) / opening - 1.0,
        "ohlcv_candles": len(rows),
    }
