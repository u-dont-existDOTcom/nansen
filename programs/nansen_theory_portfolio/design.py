from __future__ import annotations

import hashlib
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable


PORTFOLIO_ID = "2026-08-18-nansen-theory-portfolio-v1"
PROGRAM_A_ID = "2026-08-18-historical-theory-discovery-a-v1"
DESIGN_PATH = "docs/superpowers/specs/2026-08-18-nansen-theory-portfolio-v1.md"
FULL_OPENAPI_SHA256 = "d01160d54c375f022d839d4c0619b51928bfcc652a5308fdd8c927a1e53e7548"
FULL_OPENAPI_SOURCE = (
    "research/experiments/2026-08-18-prospective-multi-cycle-cohort-v1/"
    "contracts/nansen-openapi.json"
)
ACTIVE_PROGRAM_PATH = (
    "research/experiments/2026-08-18-prospective-multi-cycle-cohort-v1/program.json"
)

PROVED_BALANCE = 50_063
ACTIVE_COHORT_RESERVE = 1_736
PORTFOLIO_MAX_CREDITS = 48_000
PORTFOLIO_SAFETY_CREDITS = 327
PROGRAM_ALLOCATIONS = {
    "historical_pit_discovery": 8_000,
    "prospective_discovery_validation": 12_240,
    "primary_prospective_holdout": 13_786,
    "temporal_replication": 13_974,
}

PROGRAM_A_MAX_CREDITS = 7_375
PROGRAM_A_ALLOCATION = 8_000
PROGRAM_A_MAX_CALLS = 1_860
PROGRAM_A_STOP_BEFORE = datetime(2026, 8, 20, 10, 45, tzinfo=timezone.utc)

SCREENER_ENDPOINT = "v1beta1/token-screener/historical"
FLOW_ENDPOINT = "v1beta1/tgm/historical-token-flow-summary"
WBS_ENDPOINT = "v1beta1/tgm/historical-who-bought-sold"
DEX_ENDPOINT = "v1beta1/tgm/historical-dex-trades"
OHLCV_ENDPOINT = "tgm/token-ohlcv"
ACCOUNT_ENDPOINT = "account"

CHAINS = ("ethereum", "solana", "base", "bnb")
STRATA = ("upper_tail", "upper_middle", "near_zero", "lower_tail")
SMART_MONEY_LABELS = (
    "Fund",
    "Smart Trader",
    "30D Smart Trader",
    "90D Smart Trader",
    "180D Smart Trader",
    "Smart Dex Trader",
    "30D Smart Dex Trader",
    "90D Smart Dex Trader",
    "180D Smart Dex Trader",
    "Smart HL Perps Trader",
)
ANCHORS = tuple(date(2025, 5, 18) + timedelta(days=7 * index) for index in range(65))
BASE_BPS = 100
STRESS_BPS = 250


class DesignError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlannedSlot:
    event_id: str
    anchor_index: int
    anchor: date
    slot_index: int
    chain: str
    stratum: str
    execution_calibration: bool


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_address(address: str) -> str:
    if not isinstance(address, str) or not address:
        raise DesignError("token address must be nonempty")
    return address.lower() if address.lower().startswith("0x") else address


def utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise DesignError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def anchor_t0(anchor: date) -> datetime:
    return datetime.combine(anchor + timedelta(days=1), time(), tzinfo=timezone.utc)


def _slot_counts() -> tuple[int, ...]:
    counts = (7,) * 10 + (6,) * 55
    if len(counts) != len(ANCHORS) or sum(counts) != 400:
        raise AssertionError("program-A slot schedule is inconsistent")
    return counts


def planned_slots() -> tuple[PlannedSlot, ...]:
    preliminary: list[tuple[int, date, int, str, str]] = []
    global_slot = 0
    for anchor_index, (anchor, count) in enumerate(zip(ANCHORS, _slot_counts(), strict=True)):
        per_chain: dict[str, int] = {chain: 0 for chain in CHAINS}
        for slot_index in range(count):
            chain_index = global_slot % len(CHAINS)
            chain = CHAINS[chain_index]
            occurrence = per_chain[chain]
            per_chain[chain] += 1
            stratum = STRATA[(anchor_index + chain_index + occurrence) % len(STRATA)]
            preliminary.append((anchor_index, anchor, slot_index, chain, stratum))
            global_slot += 1
    if global_slot != 400:
        raise AssertionError("program-A schedule does not have 400 slots")
    if any(sum(item[3] == chain for item in preliminary) != 100 for chain in CHAINS):
        raise AssertionError("program-A schedule is not chain-balanced")

    calibration_by_anchor: dict[int, tuple[int, date, int, str, str]] = {}
    for item in preliminary:
        anchor_index, anchor, slot_index, chain, stratum = item
        current = calibration_by_anchor.get(anchor_index)
        identity = f"{anchor.isoformat()}|{chain}|{stratum}"
        if current is None:
            calibration_by_anchor[anchor_index] = item
            continue
        current_identity = f"{current[1].isoformat()}|{current[3]}|{current[4]}"
        if (_sha256_text(identity), identity) < (
            _sha256_text(current_identity), current_identity
        ):
            calibration_by_anchor[anchor_index] = item

    result = []
    for anchor_index, anchor, slot_index, chain, stratum in preliminary:
        event_id = f"a{anchor_index + 1:02d}-s{slot_index + 1:02d}-{chain}-{stratum}"
        result.append(
            PlannedSlot(
                event_id=event_id,
                anchor_index=anchor_index,
                anchor=anchor,
                slot_index=slot_index,
                chain=chain,
                stratum=stratum,
                execution_calibration=(
                    calibration_by_anchor[anchor_index]
                    == (anchor_index, anchor, slot_index, chain, stratum)
                ),
            )
        )
    if sum(slot.execution_calibration for slot in result) != 65:
        raise AssertionError("program-A calibration schedule is inconsistent")
    return tuple(result)


PLANNED_SLOTS = planned_slots()


def screener_payload(anchor: date) -> dict[str, Any]:
    return {
        "to_date": anchor.isoformat(),
        "timeframe_days": 1,
        "chains": list(CHAINS),
        "trader_type": "sm",
        "filters": {
            "market_cap_usd": {"min": 1_000_000},
            "liquidity_usd": {"min": 250_000},
            "token_age_days": {"min": 3},
        },
        "pagination": {"page": 1, "per_page": 1000},
        "order_by": [{"field": "netflow", "direction": "DESC"}],
        "apply_blacklist_filter": False,
    }


def _day_range(anchor: date) -> dict[str, str]:
    return {
        "from": f"{anchor.isoformat()}T00:00:00Z",
        "to": f"{anchor.isoformat()}T23:59:59.999999Z",
    }


def flow_payload(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "chain": event["chain"],
        "token_address": event["token_address"],
        "date_range": _day_range(date.fromisoformat(event["anchor"])),
        "apply_blacklist_filter": False,
    }


def wbs_payload(event: dict[str, Any], direction: str) -> dict[str, Any]:
    if direction not in {"BUY", "SELL"}:
        raise DesignError("WBS direction must be BUY or SELL")
    field = "bought_volume_usd" if direction == "BUY" else "sold_volume_usd"
    return {
        "chain": event["chain"],
        "token_address": event["token_address"],
        "date_range": _day_range(date.fromisoformat(event["anchor"])),
        "buy_or_sell": direction,
        "pagination": {"page": 1, "per_page": 1000},
        "filters": {
            "include_labels": list(SMART_MONEY_LABELS),
            "trade_volume_usd": {"min": 0},
        },
        "order_by": [{"field": field, "direction": "DESC"}],
    }


def ohlcv_payload(event: dict[str, Any]) -> dict[str, Any]:
    t0 = anchor_t0(date.fromisoformat(event["anchor"]))
    return {
        "chain": event["chain"],
        "token_address": event["token_address"],
        "date": {
            "from": utc_text(t0),
            "to": utc_text(t0 + timedelta(hours=4, minutes=15)),
        },
        "timeframe": "5m",
    }


def dex_payload(event: dict[str, Any], direction: str) -> dict[str, Any]:
    if direction not in {"BUY", "SELL"}:
        raise DesignError("DEX direction must be BUY or SELL")
    t0 = anchor_t0(date.fromisoformat(event["anchor"]))
    start = t0 + (timedelta(minutes=5) if direction == "BUY" else timedelta(hours=4, minutes=5))
    end = start + timedelta(minutes=10)
    return {
        "chain": event["chain"],
        "token_address": event["token_address"],
        "date_range": {
            "from": utc_text(start),
            "to": utc_text(end - timedelta(microseconds=1)),
        },
        "pagination": {"page": 1, "per_page": 1000},
        "filters": {"action": direction},
        "order_by": [{"field": "block_timestamp", "direction": "ASC"}],
        "apply_blacklist_filter": False,
    }


def _finite(value: Any, *, positive: bool = False, nonnegative: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    if nonnegative and number < 0:
        return None
    return number


def validate_screener(body: Any, anchor: date) -> list[dict[str, Any]]:
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise DesignError("historical screener response is not an object with data")
    pagination = body.get("pagination")
    if (
        not isinstance(pagination, dict)
        or pagination.get("page") != 1
        or pagination.get("per_page") != 1000
        or not isinstance(pagination.get("is_last_page"), bool)
        or len(body["data"]) > 1000
    ):
        raise DesignError("historical screener pagination differs from page-one contract")
    eligible: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    previous_netflow: float | None = None
    saw_null_netflow = False
    for raw in body["data"]:
        if not isinstance(raw, dict):
            raise DesignError("historical screener row is not an object")
        raw_netflow = raw.get("netflow")
        if raw_netflow is None:
            saw_null_netflow = True
            netflow = None
        else:
            netflow = _finite(raw_netflow)
            if netflow is None:
                raise DesignError("historical screener raw netflow is invalid")
            if saw_null_netflow or (
                previous_netflow is not None and netflow > previous_netflow
            ):
                raise DesignError(
                    "historical screener rows are not raw-netflow descending"
                )
            previous_netflow = netflow

        raw_chain = raw.get("chain")
        chain = "bnb" if raw_chain == "bsc" else raw_chain
        address = raw.get("token_address")
        if not isinstance(chain, str) or chain not in CHAINS or not isinstance(address, str):
            continue
        identity = (chain, normalize_address(address))
        if identity in seen:
            raise DesignError("historical screener contains duplicate token identity")
        seen.add(identity)
        price_change = _finite(raw.get("price_change"))
        if price_change is not None and abs(price_change) > 20:
            raise DesignError("historical screener price_change semantics exceed magnitude 20")
        price = _finite(raw.get("price_usd"), positive=True)
        market_cap = _finite(raw.get("market_cap_usd"), positive=True)
        liquidity = _finite(raw.get("liquidity"), positive=True)
        volume = _finite(raw.get("volume"), positive=True)
        age = raw.get("token_age_days")
        symbol = raw.get("token_symbol")
        if (
            price is None
            or market_cap is None
            or market_cap < 1_000_000
            or liquidity is None
            or liquidity < 250_000
            or volume is None
            or netflow is None
            or price_change is None
            or not isinstance(age, int)
            or isinstance(age, bool)
            or age < 3
            or not isinstance(symbol, str)
            or not symbol.strip()
        ):
            continue
        eligible.append(
            {
                "anchor": anchor.isoformat(),
                "chain": chain,
                "chain_provenance": {
                    "raw": raw_chain,
                    "normalized": chain,
                    "normalization": "bsc_to_bnb" if raw_chain == "bsc" else "identity",
                },
                "token_address": identity[1],
                "token_symbol": symbol,
                "price_usd": price,
                "price_change": price_change,
                "market_cap_usd": market_cap,
                "liquidity_usd": liquidity,
                "volume_usd": volume,
                "netflow_usd": netflow,
                "netflow_to_market_cap": netflow / market_cap,
                "token_age_days": age,
            }
        )
    return eligible


def select_anchor_events(
    slots: Iterable[PlannedSlot], eligible: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_chain: dict[str, list[dict[str, Any]]] = {chain: [] for chain in CHAINS}
    for row in eligible:
        by_chain[row["chain"]].append(row)
    for rows in by_chain.values():
        rows.sort(key=lambda row: (row["netflow_to_market_cap"], row["token_address"]))

    used: set[tuple[str, str]] = set()
    selected: list[dict[str, Any]] = []
    for slot in slots:
        rows = by_chain[slot.chain]
        row: dict[str, Any] | None = None
        if rows:
            if slot.stratum == "upper_tail":
                row = rows[-1]
            elif slot.stratum == "lower_tail":
                row = rows[0]
            elif slot.stratum == "near_zero":
                row = min(rows, key=lambda item: (abs(item["netflow_to_market_cap"]), item["token_address"]))
            elif slot.stratum == "upper_middle":
                row = rows[math.floor(0.75 * (len(rows) - 1))]
            else:
                raise DesignError("unknown selection stratum")
        identity = None if row is None else (row["chain"], row["token_address"])
        if row is None or identity in used:
            selected.append(
                {
                    "event_id": slot.event_id,
                    "anchor": slot.anchor.isoformat(),
                    "chain": slot.chain,
                    "stratum": slot.stratum,
                    "execution_calibration": slot.execution_calibration,
                    "status": "unavailable",
                    "reason": "empty_or_duplicate_stratum",
                }
            )
            continue
        assert identity is not None
        used.add(identity)
        selected.append(
            {
                "event_id": slot.event_id,
                "stratum": slot.stratum,
                "execution_calibration": slot.execution_calibration,
                "status": "selected",
                **row,
                "virtual_notional_usd": min(1000.0, 0.001 * row["liquidity_usd"]),
            }
        )
    return selected


FLOW_FIELDS = (
    "public_figure_net_flow_usd",
    "public_figure_wallet_count",
    "top_pnl_net_flow_usd",
    "top_pnl_wallet_count",
    "whale_net_flow_usd",
    "whale_wallet_count",
    "exchange_net_flow_usd",
    "smart_trader_net_flow_usd",
    "smart_trader_wallet_count",
    "fresh_wallets_net_flow_usd",
)


def validate_flow(body: Any) -> dict[str, float | int | None]:
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise DesignError("historical flow response is invalid")
    if len(body["data"]) != 1 or not isinstance(body["data"][0], dict):
        raise DesignError("historical flow response must contain one row")
    row = body["data"][0]
    result: dict[str, float | int | None] = {}
    for field in FLOW_FIELDS:
        value = row.get(field)
        if value is None:
            result[field] = None
            continue
        if field.endswith("wallet_count"):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise DesignError(f"historical flow {field} is invalid")
            result[field] = value
        else:
            number = _finite(value)
            if number is None:
                raise DesignError(f"historical flow {field} is invalid")
            result[field] = number
    warnings = body.get("warnings")
    if warnings is not None and (
        not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings)
    ):
        raise DesignError("historical flow warnings are invalid")
    result["warnings_present"] = bool(warnings)
    return result


def validate_wbs(body: Any, direction: str) -> dict[str, Any]:
    if direction not in {"BUY", "SELL"}:
        raise DesignError("WBS direction must be BUY or SELL")
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise DesignError("historical WBS response is invalid")
    pagination = body.get("pagination")
    if (
        not isinstance(pagination, dict)
        or pagination.get("page") != 1
        or pagination.get("per_page") != 1000
        or not isinstance(pagination.get("is_last_page"), bool)
        or len(body["data"]) > 1000
    ):
        raise DesignError("historical WBS pagination is invalid")
    if pagination["is_last_page"] is not True:
        return {"available": False, "reason": "nonfinal_page_one"}
    address_seen: set[str] = set()
    usd_field = "bought_volume_usd" if direction == "BUY" else "sold_volume_usd"
    token_field = "bought_token_volume" if direction == "BUY" else "sold_token_volume"
    usd_total = 0.0
    token_total = 0.0
    address_usd: list[float] = []
    for row in body["data"]:
        if not isinstance(row, dict) or not isinstance(row.get("address"), str):
            raise DesignError("historical WBS row identity is invalid")
        address = normalize_address(row["address"])
        if address in address_seen:
            raise DesignError("historical WBS response contains duplicate address")
        address_seen.add(address)
        if row.get("is_smart_money") is not True:
            raise DesignError("historical WBS row is outside requested Smart-Money labels")
        usd = _finite(row.get(usd_field), nonnegative=True)
        token = _finite(row.get(token_field), nonnegative=True)
        if usd is None or token is None:
            raise DesignError("historical WBS directional volume is invalid")
        usd_total += usd
        token_total += token
        address_usd.append(usd)
    concentration = 0.0 if usd_total == 0 else max(address_usd, default=0.0) / usd_total
    return {
        "available": True,
        "address_count": len(address_seen),
        "volume_usd": usd_total,
        "token_volume": token_total,
        "largest_address_share": concentration,
    }


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise DesignError("provider timestamp is not a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DesignError("provider timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_ohlcv(body: Any, event: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict) or body.get("chain") != event["chain"]:
        raise DesignError("OHLCV response chain differs")
    if normalize_address(body.get("token_address")) != event["token_address"]:
        raise DesignError("OHLCV response token differs")
    if body.get("timeframe") != "5m" or body.get("truncated") is not False:
        raise DesignError("OHLCV response timeframe or truncation differs")
    data = body.get("data")
    if not isinstance(data, list):
        raise DesignError("OHLCV data is missing")
    t0 = anchor_t0(date.fromisoformat(event["anchor"]))
    expected = [t0 + timedelta(minutes=5 * index) for index in range(52)]
    if len(data) != len(expected):
        raise DesignError("OHLCV grid length differs")
    closes: dict[datetime, float] = {}
    for row, expected_time in zip(data, expected, strict=True):
        if not isinstance(row, dict) or _parse_datetime(row.get("interval_start")) != expected_time:
            raise DesignError("OHLCV grid is not exact and contiguous")
        values = {field: _finite(row.get(field), positive=True) for field in ("open", "high", "low", "close")}
        if any(value is None for value in values.values()):
            raise DesignError("OHLCV price is unavailable or nonpositive")
        assert all(value is not None for value in values.values())
        if values["high"] < max(values["open"], values["close"]) or values["low"] > min(values["open"], values["close"]) or values["high"] < values["low"]:
            raise DesignError("OHLCV candle bounds are invalid")
        if _finite(row.get("volume"), nonnegative=True) is None:
            raise DesignError("OHLCV volume is invalid")
        closes[expected_time] = float(values["close"])
    entry = closes[t0 + timedelta(minutes=10)]
    exit_price = closes[t0 + timedelta(hours=4, minutes=10)]
    gross_multiple = exit_price / entry
    return {
        "available": True,
        "entry_price_usd": entry,
        "exit_price_usd": exit_price,
        "gross_return": gross_multiple - 1.0,
        "base_return": gross_multiple * (1 - BASE_BPS / 10_000) ** 2 - 1.0,
        "stress_return": gross_multiple * (1 - STRESS_BPS / 10_000) ** 2 - 1.0,
    }


def validate_dex(body: Any, event: dict[str, Any], direction: str) -> list[dict[str, Any]]:
    if direction not in {"BUY", "SELL"}:
        raise DesignError("DEX direction must be BUY or SELL")
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise DesignError("historical DEX response is invalid")
    pagination = body.get("pagination")
    if (
        not isinstance(pagination, dict)
        or pagination.get("page") != 1
        or pagination.get("per_page") != 1000
        or not isinstance(pagination.get("is_last_page"), bool)
        or len(body["data"]) > 1000
    ):
        raise DesignError("historical DEX pagination is invalid")
    if pagination["is_last_page"] is not True:
        raise DesignError("historical DEX page one is non-final")
    t0 = anchor_t0(date.fromisoformat(event["anchor"]))
    start = t0 + (timedelta(minutes=5) if direction == "BUY" else timedelta(hours=4, minutes=5))
    end = start + timedelta(minutes=10)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in body["data"]:
        if not isinstance(raw, dict) or raw.get("action") != direction:
            raise DesignError("historical DEX row direction differs")
        timestamp = _parse_datetime(raw.get("block_timestamp"))
        transaction_hash = raw.get("transaction_hash")
        if not start <= timestamp < end or not isinstance(transaction_hash, str) or not transaction_hash:
            raise DesignError("historical DEX row identity or bounds differ")
        if transaction_hash in seen:
            raise DesignError("historical DEX response contains duplicate transaction hash")
        seen.add(transaction_hash)
        token_amount = _finite(raw.get("token_amount"), positive=True)
        price = _finite(raw.get("estimated_swap_price_usd"), positive=True)
        value = _finite(raw.get("estimated_value_usd"), positive=True)
        if token_amount is None or price is None or value is None:
            raise DesignError("historical DEX row amount or price is invalid")
        if abs(token_amount * price - value) > max(0.01, 0.01 * value):
            raise DesignError("historical DEX amount, price, and value are inconsistent")
        rows.append(
            {
                "timestamp": utc_text(timestamp),
                "transaction_hash": transaction_hash,
                "token_amount": token_amount,
                "price_usd": price,
                "value_usd": value,
            }
        )
    rows.sort(key=lambda row: (row["timestamp"], row["transaction_hash"]))
    return rows


def execution_outcome(
    event: dict[str, Any], buys: list[dict[str, Any]], sells: list[dict[str, Any]]
) -> dict[str, Any]:
    notional = float(event["virtual_notional_usd"])
    spent = 0.0
    tokens = 0.0
    for trade in buys:
        remaining = notional - spent
        if remaining <= 0:
            break
        available_value = float(trade["value_usd"])
        used = min(available_value, remaining)
        spent += used
        tokens += float(trade["token_amount"]) * used / available_value
    entry_ratio = 0.0 if notional == 0 else spent / notional
    if tokens <= 0:
        return {
            "status": "unfilled_entry",
            "entry_fill_ratio": entry_ratio,
            "exit_fill_ratio": 0.0,
            "filled_token_amount": 0.0,
        }
    sold_tokens = 0.0
    proceeds = 0.0
    for trade in sells:
        remaining = tokens - sold_tokens
        if remaining <= 0:
            break
        available_tokens = float(trade["token_amount"])
        sold = min(available_tokens, remaining)
        sold_tokens += sold
        proceeds += float(trade["value_usd"]) * sold / available_tokens
    exit_ratio = sold_tokens / tokens
    result: dict[str, Any] = {
        "status": "filled" if entry_ratio >= 1 and exit_ratio >= 1 else "partial",
        "entry_fill_ratio": entry_ratio,
        "exit_fill_ratio": exit_ratio,
        "filled_token_amount": tokens,
        "sold_token_amount": sold_tokens,
        "entry_spent_usd": spent,
        "exit_proceeds_usd": proceeds,
    }
    if entry_ratio >= 1 and exit_ratio >= 1 and spent > 0:
        multiple = proceeds / spent
        result.update(
            {
                "gross_return": multiple - 1.0,
                "base_return": multiple * (1 - BASE_BPS / 10_000) ** 2 - 1.0,
                "stress_return": multiple * (1 - STRESS_BPS / 10_000) ** 2 - 1.0,
            }
        )
    return result


VETO_PREDICATES = ("seller_breadth", "seller_volume", "price_nonpositive")
TRI_STATE_SEMANTICS = {
    "unavailable": "one or more availability requirements fail",
    "true": "all availability requirements pass and the comparison is true",
    "false": "all availability requirements pass and the comparison is false",
}
CANDIDATE_PRIMITIVES: dict[str, dict[str, Any]] = {
    "screen_positive": {
        "sources": ["event.netflow_to_market_cap"],
        "availability": [
            {"path": "event.netflow_to_market_cap", "condition": "finite_number"}
        ],
        "comparison": {
            "left": "event.netflow_to_market_cap",
            "operator": "gt",
            "right": 0.0,
        },
    },
    "buyer_breadth": {
        "sources": [
            "feature.buy.address_count",
            "feature.sell.address_count",
        ],
        "availability": [
            {"path": "feature.buy.available", "condition": "is_true"},
            {"path": "feature.sell.available", "condition": "is_true"},
            {"path": "feature.buy.address_count", "condition": "nonnegative_integer"},
            {"path": "feature.sell.address_count", "condition": "nonnegative_integer"},
        ],
        "comparison": {
            "left": "feature.buy.address_count",
            "operator": "gt",
            "right": "feature.sell.address_count",
        },
    },
    "buyer_volume": {
        "sources": ["feature.buy.volume_usd", "feature.sell.volume_usd"],
        "availability": [
            {"path": "feature.buy.available", "condition": "is_true"},
            {"path": "feature.sell.available", "condition": "is_true"},
            {"path": "feature.buy.volume_usd", "condition": "finite_nonnegative"},
            {"path": "feature.sell.volume_usd", "condition": "finite_nonnegative"},
        ],
        "comparison": {
            "left": "feature.buy.volume_usd",
            "operator": "gt",
            "right": "feature.sell.volume_usd",
        },
    },
    "seller_breadth": {
        "sources": [
            "feature.sell.address_count",
            "feature.buy.address_count",
        ],
        "availability": [
            {"path": "feature.buy.available", "condition": "is_true"},
            {"path": "feature.sell.available", "condition": "is_true"},
            {"path": "feature.sell.address_count", "condition": "nonnegative_integer"},
            {"path": "feature.buy.address_count", "condition": "nonnegative_integer"},
        ],
        "comparison": {
            "left": "feature.sell.address_count",
            "operator": "gt",
            "right": "feature.buy.address_count",
        },
    },
    "seller_volume": {
        "sources": ["feature.sell.volume_usd", "feature.buy.volume_usd"],
        "availability": [
            {"path": "feature.buy.available", "condition": "is_true"},
            {"path": "feature.sell.available", "condition": "is_true"},
            {"path": "feature.sell.volume_usd", "condition": "finite_nonnegative"},
            {"path": "feature.buy.volume_usd", "condition": "finite_nonnegative"},
        ],
        "comparison": {
            "left": "feature.sell.volume_usd",
            "operator": "gt",
            "right": "feature.buy.volume_usd",
        },
    },
    "exchange_outflow": {
        "sources": ["feature.flow.exchange_net_flow_usd"],
        "availability": [
            {"path": "feature.flow", "condition": "object"},
            {"path": "feature.flow.warnings_present", "condition": "is_false"},
            {"path": "feature.flow.exchange_net_flow_usd", "condition": "finite_number"},
        ],
        "comparison": {
            "left": "feature.flow.exchange_net_flow_usd",
            "operator": "lt",
            "right": 0.0,
        },
    },
    "smart_trader_positive": {
        "sources": ["feature.flow.smart_trader_net_flow_usd"],
        "availability": [
            {"path": "feature.flow", "condition": "object"},
            {"path": "feature.flow.warnings_present", "condition": "is_false"},
            {"path": "feature.flow.smart_trader_net_flow_usd", "condition": "finite_number"},
        ],
        "comparison": {
            "left": "feature.flow.smart_trader_net_flow_usd",
            "operator": "gt",
            "right": 0.0,
        },
    },
    "top_pnl_positive": {
        "sources": ["feature.flow.top_pnl_net_flow_usd"],
        "availability": [
            {"path": "feature.flow", "condition": "object"},
            {"path": "feature.flow.warnings_present", "condition": "is_false"},
            {"path": "feature.flow.top_pnl_net_flow_usd", "condition": "finite_number"},
        ],
        "comparison": {
            "left": "feature.flow.top_pnl_net_flow_usd",
            "operator": "gt",
            "right": 0.0,
        },
    },
    "whale_positive": {
        "sources": ["feature.flow.whale_net_flow_usd"],
        "availability": [
            {"path": "feature.flow", "condition": "object"},
            {"path": "feature.flow.warnings_present", "condition": "is_false"},
            {"path": "feature.flow.whale_net_flow_usd", "condition": "finite_number"},
        ],
        "comparison": {
            "left": "feature.flow.whale_net_flow_usd",
            "operator": "gt",
            "right": 0.0,
        },
    },
    "fresh_latest_asof_positive": {
        "sources": ["feature.flow.fresh_wallets_net_flow_usd"],
        "availability": [
            {"path": "feature.flow", "condition": "object"},
            {"path": "feature.flow.warnings_present", "condition": "is_false"},
            {"path": "feature.flow.fresh_wallets_net_flow_usd", "condition": "finite_number"},
        ],
        "comparison": {
            "left": "feature.flow.fresh_wallets_net_flow_usd",
            "operator": "gt",
            "right": 0.0,
        },
        "as_of_semantics": (
            "most recent 24-hour fresh-wallet snapshot on or before date_to; "
            "the response does not expose snapshot freshness"
        ),
    },
    "price_nonpositive": {
        "sources": ["event.price_change"],
        "availability": [
            {"path": "event.price_change", "condition": "finite_number"}
        ],
        "comparison": {
            "left": "event.price_change",
            "operator": "lte",
            "right": 0.0,
        },
    },
    "price_momentum": {
        "sources": ["event.price_change"],
        "availability": [
            {"path": "event.price_change", "condition": "finite_number"}
        ],
        "comparison": {
            "all": [
                {"left": "event.price_change", "operator": "gt", "right": 0.0},
                {"left": "event.price_change", "operator": "lte", "right": 0.15},
            ]
        },
    },
}
CANDIDATES: tuple[dict[str, Any], ...] = (
    {"id": "c01-buyer-breadth-exchange", "predicates": ["screen_positive", "buyer_breadth", "exchange_outflow"], "apriori": True},
    {"id": "c02-buyer-volume-exchange", "predicates": ["screen_positive", "buyer_volume", "exchange_outflow"]},
    {"id": "c03-early-breadth-divergence", "predicates": ["screen_positive", "buyer_breadth", "price_nonpositive"]},
    {"id": "c04-early-exchange-divergence", "predicates": ["screen_positive", "exchange_outflow", "price_nonpositive"]},
    {"id": "c05-breadth-continuation", "predicates": ["screen_positive", "buyer_breadth", "price_momentum"]},
    {"id": "c06-top-pnl-confirmation", "predicates": ["screen_positive", "exchange_outflow", "top_pnl_positive"]},
    {"id": "c07-smart-trader-confirmation", "predicates": ["screen_positive", "exchange_outflow", "smart_trader_positive"]},
    {"id": "c08-three-segment-consensus", "predicates": ["exchange_outflow", "smart_trader_positive", "whale_positive"]},
    {"id": "c09-fresh-wallet-confirmation", "predicates": ["screen_positive", "exchange_outflow", "fresh_latest_asof_positive"]},
    {"id": "c10-buyer-breadth-benchmark", "predicates": ["screen_positive", "buyer_breadth"]},
    {"id": "c11-screener-accumulation-benchmark", "predicates": ["screen_positive"]},
    {"id": "c12-cash-no-signal-benchmark", "predicates": [], "cash": True},
)


def candidate_contract() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "portfolio_id": PORTFOLIO_ID,
        "program_id": PROGRAM_A_ID,
        "tri_state_semantics": dict(TRI_STATE_SEMANTICS),
        "primitives": {
            name: dict(definition) for name, definition in CANDIDATE_PRIMITIVES.items()
        },
        "veto": {
            "id": "historical-selling-pressure-v1",
            "not_equivalent_to": "frozen-four-hour-distribution-veto",
            "predicates": list(VETO_PREDICATES),
        },
        "composition": {
            "candidate_conjunction": "strong_kleene_and_false_dominates_unavailable",
            "veto_conjunction": "strong_kleene_and_false_dominates_unavailable",
            "evaluation_order": "candidate_then_veto_if_candidate_true",
            "decision_truth_table": [
                {"candidate": False, "veto": "not_evaluated", "decision": "abstain"},
                {"candidate": None, "veto": "not_evaluated", "decision": "unavailable"},
                {"candidate": True, "veto": False, "decision": "long"},
                {"candidate": True, "veto": None, "decision": "unavailable"},
                {"candidate": True, "veto": True, "decision": "abstain"},
            ],
        },
        "ranking": {
            "direction": "descending",
            "lexicographic_fields": [
                "token_equal_stress_mean_positive",
                "event_base_median_positive",
                "positive_calendar_blocks",
                "token_equal_stress_mean",
                "token_equal_base_mean",
                "event_base_median",
                "negative_max_token_share",
                "negative_max_week_share",
                "negative_max_chain_share",
                "scored_signals",
            ],
            "final_tie_break": "candidate_id_ascending",
        },
        "candidates": [dict(candidate) for candidate in CANDIDATES],
    }


def predicate_values(event: dict[str, Any], feature: dict[str, Any]) -> dict[str, bool | None]:
    buy = feature.get("buy")
    sell = feature.get("sell")
    flow = feature.get("flow")
    buy_available = isinstance(buy, dict) and buy.get("available") is True
    sell_available = isinstance(sell, dict) and sell.get("available") is True
    flow_available = isinstance(flow, dict)

    def directional(field: str, *, buyer_on_left: bool) -> bool | None:
        if not (buy_available and sell_available):
            return None
        assert isinstance(buy, dict) and isinstance(sell, dict)
        buy_value = buy.get(field)
        sell_value = sell.get(field)
        if field == "address_count":
            if (
                not isinstance(buy_value, int)
                or isinstance(buy_value, bool)
                or buy_value < 0
                or not isinstance(sell_value, int)
                or isinstance(sell_value, bool)
                or sell_value < 0
            ):
                return None
        else:
            buy_value = _finite(buy_value, nonnegative=True)
            sell_value = _finite(sell_value, nonnegative=True)
            if buy_value is None or sell_value is None:
                return None
        return buy_value > sell_value if buyer_on_left else sell_value > buy_value

    def signed(field: str, comparator: str) -> bool | None:
        value = (
            None
            if not flow_available or flow.get("warnings_present") is not False
            else flow.get(field)
        )
        number = _finite(value)
        if number is None:
            return None
        return number < 0 if comparator == "negative" else number > 0

    score = _finite(event.get("netflow_to_market_cap"))
    price_change = _finite(event.get("price_change"))
    result: dict[str, bool | None] = {
        "screen_positive": None if score is None else score > 0,
        "buyer_breadth": directional("address_count", buyer_on_left=True),
        "buyer_volume": directional("volume_usd", buyer_on_left=True),
        "seller_breadth": directional("address_count", buyer_on_left=False),
        "seller_volume": directional("volume_usd", buyer_on_left=False),
        "exchange_outflow": signed("exchange_net_flow_usd", "negative"),
        "smart_trader_positive": signed("smart_trader_net_flow_usd", "positive"),
        "top_pnl_positive": signed("top_pnl_net_flow_usd", "positive"),
        "whale_positive": signed("whale_net_flow_usd", "positive"),
        "fresh_latest_asof_positive": signed("fresh_wallets_net_flow_usd", "positive"),
        "price_nonpositive": None if price_change is None else price_change <= 0,
        "price_momentum": None if price_change is None else 0 < price_change <= 0.15,
    }
    return result


def _kleene_and(values: Iterable[bool | None]) -> bool | None:
    """Strong Kleene conjunction: a decisive false dominates missing evidence."""

    materialized = tuple(values)
    if any(value is False for value in materialized):
        return False
    if all(value is True for value in materialized):
        return True
    if any(value is not None for value in materialized):
        if any(value is not True for value in materialized):
            return None
    return None


def _candidate_decision(
    values: dict[str, bool | None], predicates: Iterable[str]
) -> str:
    candidate_value = _kleene_and(values[name] for name in predicates)
    if candidate_value is False:
        return "abstain"
    if candidate_value is None:
        return "unavailable"
    veto_value = _kleene_and(values[name] for name in VETO_PREDICATES)
    if veto_value is None:
        return "unavailable"
    return "abstain" if veto_value is True else "long"


def _candidate_rank_key(score: dict[str, Any]) -> tuple[Any, ...]:
    return (
        score["token_equal_stress_mean"] > 0,
        score["event_base_median"] > 0,
        score["positive_calendar_blocks"],
        score["token_equal_stress_mean"],
        score["token_equal_base_mean"],
        score["event_base_median"],
        -score["max_token_share"],
        -score["max_week_share"],
        -score["max_chain_share"],
        score["scored_signals"],
    )


def score_candidates(
    records: Iterable[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]
) -> dict[str, Any]:
    materialized = list(records)
    selected_records = [
        record for record in materialized if record[0].get("status") == "selected"
    ]
    planned_opportunities = len(materialized)
    selected_opportunities = len(selected_records)
    selection_coverage = (
        0.0
        if planned_opportunities == 0
        else selected_opportunities / planned_opportunities
    )
    scores: list[dict[str, Any]] = []
    for candidate in CANDIDATES:
        if candidate.get("cash"):
            scores.append(
                {
                    "candidate_id": candidate["id"],
                    "role": "cash",
                    "signals": 0,
                    "planned_opportunities": planned_opportunities,
                    "selected_opportunities": selected_opportunities,
                    "selection_coverage": selection_coverage,
                }
            )
            continue
        events: list[tuple[dict[str, Any], float, float]] = []
        unavailable_decisions = 0
        signals = 0
        signal_outcomes_missing = 0
        for event, feature, outcome in selected_records:
            values = predicate_values(event, feature)
            decision = _candidate_decision(values, candidate["predicates"])
            if decision == "unavailable":
                unavailable_decisions += 1
                continue
            if decision == "abstain":
                continue
            signals += 1
            if outcome.get("available") is True:
                events.append((event, float(outcome["base_return"]), float(outcome["stress_return"])))
            else:
                signal_outcomes_missing += 1
        by_token: dict[tuple[str, str], list[tuple[float, float]]] = {}
        by_week: dict[str, int] = {}
        by_chain: dict[str, int] = {}
        by_block: dict[int, list[float]] = {}
        for event, base, stress in events:
            key = (event["chain"], event["token_address"])
            by_token.setdefault(key, []).append((base, stress))
            week = date.fromisoformat(event["anchor"]).strftime("%G-W%V")
            by_week[week] = by_week.get(week, 0) + 1
            chain = event["chain"]
            by_chain[chain] = by_chain.get(chain, 0) + 1
            anchor_offset = (
                date.fromisoformat(event["anchor"]) - ANCHORS[0]
            ).days // 7
            by_block.setdefault(anchor_offset // 13, []).append(base)
        token_base = [statistics.fmean(value[0] for value in values) for values in by_token.values()]
        token_stress = [statistics.fmean(value[1] for value in values) for values in by_token.values()]
        base_values = [base for _, base, _ in events]
        max_token_share = 0.0
        max_week_share = 0.0
        max_chain_share = 0.0
        if events:
            max_token_share = max(len(values) for values in by_token.values()) / len(events)
            max_week_share = max(by_week.values()) / len(events)
            max_chain_share = max(by_chain.values()) / len(events)
        decision_availability = (
            0.0
            if selected_opportunities == 0
            else (selected_opportunities - unavailable_decisions)
            / selected_opportunities
        )
        common_outcome_count = sum(
            1 for _, _, outcome in selected_records if outcome.get("available") is True
        )
        common_outcome_coverage = (
            0.0
            if selected_opportunities == 0
            else common_outcome_count / selected_opportunities
        )
        positive_blocks = sum(
            statistics.median(values) > 0 for values in by_block.values()
        )
        missing_decision_event_mean_break_even_return = None
        if unavailable_decisions > 0 and base_values:
            missing_decision_event_mean_break_even_return = (
                -sum(base_values) / unavailable_decisions
            )
        eligible = (
            len(events) >= 20
            and len(by_token) >= 10
            and len(by_week) >= 8
            and bool(token_base)
            and decision_availability >= 0.80
            and common_outcome_coverage >= 0.90
            and signal_outcomes_missing == 0
        )
        score = {
            "candidate_id": candidate["id"],
            "apriori": candidate.get("apriori", False),
            "planned_opportunities": planned_opportunities,
            "selected_opportunities": selected_opportunities,
            "selection_coverage": selection_coverage,
            "signals": signals,
            "scored_signals": len(events),
            "unavailable_decisions": unavailable_decisions,
            "decision_availability": decision_availability,
            "common_outcome_coverage": common_outcome_coverage,
            "signal_outcomes_missing": signal_outcomes_missing,
            "positive_calendar_blocks": positive_blocks,
            "represented_calendar_blocks": len(by_block),
            "missing_decision_event_mean_break_even_return": (
                missing_decision_event_mean_break_even_return
            ),
            "physical_tokens": len(by_token),
            "weeks": len(by_week),
            "chains": len(by_chain),
            "token_equal_base_mean": None if not token_base else statistics.fmean(token_base),
            "event_base_median": None if not base_values else statistics.median(base_values),
            "token_equal_stress_mean": None if not token_stress else statistics.fmean(token_stress),
            "max_token_share": max_token_share,
            "max_week_share": max_week_share,
            "max_chain_share": max_chain_share,
            "support_eligible": eligible,
        }
        scores.append(score)

    selectable = [score for score in scores if score.get("support_eligible")]

    ranked = sorted(selectable, key=lambda score: score["candidate_id"])
    ranked.sort(key=_candidate_rank_key, reverse=True)
    apriori = next(score for score in scores if score.get("apriori"))
    advanced: list[str] = [apriori["candidate_id"]]
    for score in ranked:
        if score["candidate_id"] not in advanced:
            advanced.append(score["candidate_id"])
        if len(advanced) == 5:
            break
    return {
        "schema_version": 1,
        "program_id": PROGRAM_A_ID,
        "planned_opportunities": planned_opportunities,
        "selected_opportunities": selected_opportunities,
        "selection_coverage": selection_coverage,
        "scores": scores,
        "program_b_candidate_ids": advanced,
        "discovery_only": True,
    }
