from __future__ import annotations

import copy
import hashlib
import json
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


PROGRAM_ID = "2026-08-19-prospective-parallel-strategy-v1"
PORTFOLIO_ID = "2026-08-19-independent-prospective-strategy-portfolio-v1"
DESIGN_PATH = (
    "docs/superpowers/specs/"
    "2026-08-19-nansen-parallel-strategy-prospective-v1.md"
)

CHAINS = ("ethereum", "solana", "base", "bnb")
PARTITIONS = ("DISCOVERY", "VALIDATION", "CONFIRMATION", "REPLICATION")
PARTITION_SALT = (
    b"2026-08-19-independent-prospective-strategy-portfolio-v1|identity-partition-v1"
)
PARTITION_SALT_SHA256 = (
    "ca6fa944de849b6eae83dde43964d80bba05eb331e0212201b1d781702634099"
)

FIRST_CYCLE_AT = datetime(2026, 10, 15, 12, 5, tzinfo=timezone.utc)
DISCOVERY_CYCLES = 42
VALIDATION_CYCLES = 43
TOKENS_PER_CYCLE = 13
WITHIN_PHASE_SPACING = timedelta(hours=8)
PHASE_START_GAP = timedelta(hours=32)
START_GRACE = timedelta(minutes=15)
DECISION_DEADLINE = timedelta(minutes=45)
PREDECISION_TRANSPORT_CUTOFF = timedelta(minutes=43, seconds=30)
SETTLEMENT_OFFSET = timedelta(hours=4, minutes=21)
SETTLEMENT_TRANSPORT_CUTOFF = timedelta(hours=7, minutes=58, seconds=30)
SETTLEMENT_HARD_STOP = timedelta(hours=7, minutes=59, seconds=40)

PREDECISION_MAX_ATTEMPTS = 80
PREDECISION_MAX_CREDITS = 79
SETTLEMENT_MAX_ATTEMPTS = 66
SETTLEMENT_MAX_CREDITS = 65
MAX_CYCLE_ATTEMPTS = 146
MAX_CYCLE_CREDITS = 144
MAX_PROGRAM_ATTEMPTS = 12_410
MAX_PROGRAM_CREDITS = 12_240

BASE_COST_RATE = 0.01
STRESS_COST_RATE = 0.025


class B2DesignError(ValueError):
    """Raised when evidence or state differs from the parallel-strategy contract."""


@dataclass(frozen=True)
class ScheduledCycle:
    index: int
    phase: str
    phase_index: int
    scheduled_at: datetime
    block: int


@dataclass(frozen=True)
class EligibleCandidate:
    chain: str
    token_address: str
    token_symbol: str
    price_usd: float
    price_change_raw: float
    volume_usd: float
    liquidity_usd: float
    market_cap_usd: float
    token_age_days: float
    netflow_usd: float
    flow_mcap_ratio: float
    source_rank: int
    selected_row: dict[str, Any]

    @property
    def identity(self) -> tuple[str, str]:
        return self.chain, self.token_address


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_sha256(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise B2DesignError("evidence is not canonical JSON data") from exc
    return _sha256(encoded)


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise B2DesignError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite(
    value: Any,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> float | None:
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


def schedule() -> tuple[ScheduledCycle, ...]:
    cycles: list[ScheduledCycle] = []
    for phase_index in range(DISCOVERY_CYCLES):
        cycles.append(
            ScheduledCycle(
                index=phase_index + 1,
                phase="discovery",
                phase_index=phase_index + 1,
                scheduled_at=FIRST_CYCLE_AT + phase_index * WITHIN_PHASE_SPACING,
                block=phase_index // 7 + 1,
            )
        )
    validation_start = cycles[-1].scheduled_at + PHASE_START_GAP
    validation_block_sizes = (7, 7, 7, 7, 7, 8)
    boundaries: list[int] = []
    running = 0
    for size in validation_block_sizes:
        running += size
        boundaries.append(running)
    for phase_index in range(VALIDATION_CYCLES):
        block = next(
            index + 1
            for index, boundary in enumerate(boundaries)
            if phase_index < boundary
        )
        cycles.append(
            ScheduledCycle(
                index=DISCOVERY_CYCLES + phase_index + 1,
                phase="validation",
                phase_index=phase_index + 1,
                scheduled_at=validation_start + phase_index * WITHIN_PHASE_SPACING,
                block=block,
            )
        )
    if len(cycles) != 85:
        raise AssertionError("parallel-strategy schedule must contain 85 paid cycles")
    return tuple(cycles)


SCHEDULE = schedule()


def budget_contract() -> dict[str, int]:
    contract = {
        "tokens_per_cycle": TOKENS_PER_CYCLE,
        "predecision_attempts": 1 + 1 + TOKENS_PER_CYCLE * 6,
        "predecision_credits": 1 + TOKENS_PER_CYCLE * 6,
        "settlement_attempts": 1 + TOKENS_PER_CYCLE * 5,
        "settlement_credits": TOKENS_PER_CYCLE * 5,
    }
    contract["cycle_attempts"] = (
        contract["predecision_attempts"] + contract["settlement_attempts"]
    )
    contract["cycle_credits"] = (
        contract["predecision_credits"] + contract["settlement_credits"]
    )
    contract["program_attempts"] = contract["cycle_attempts"] * len(SCHEDULE)
    contract["program_credits"] = contract["cycle_credits"] * len(SCHEDULE)
    expected = {
        "predecision_attempts": PREDECISION_MAX_ATTEMPTS,
        "predecision_credits": PREDECISION_MAX_CREDITS,
        "settlement_attempts": SETTLEMENT_MAX_ATTEMPTS,
        "settlement_credits": SETTLEMENT_MAX_CREDITS,
        "cycle_attempts": MAX_CYCLE_ATTEMPTS,
        "cycle_credits": MAX_CYCLE_CREDITS,
        "program_attempts": MAX_PROGRAM_ATTEMPTS,
        "program_credits": MAX_PROGRAM_CREDITS,
    }
    if any(contract[name] != value for name, value in expected.items()):
        raise AssertionError(
            "parallel-strategy request-plan arithmetic differs from the frozen budget"
        )
    return contract


def _normalize_chain(chain: str) -> str:
    if not isinstance(chain, str) or not chain or chain != chain.strip():
        raise B2DesignError("candidate chain must be nonempty")
    normalized_chain = chain.lower()
    if normalized_chain == "bsc":
        normalized_chain = "bnb"
    if normalized_chain not in CHAINS:
        raise B2DesignError("candidate chain is outside the frozen four-chain domain")
    return normalized_chain


def normalize_identity(chain: str, token_address: str) -> tuple[str, str]:
    normalized_chain = _normalize_chain(chain)
    if (
        not isinstance(token_address, str)
        or not token_address
        or token_address != token_address.strip()
        or any(character.isspace() for character in token_address)
    ):
        raise B2DesignError("candidate token address must be nonempty")
    if normalized_chain != "solana":
        if (
            len(token_address) != 42
            or not token_address.lower().startswith("0x")
            or any(character not in "0123456789abcdefABCDEF" for character in token_address[2:])
        ):
            raise B2DesignError("EVM token address must be 20-byte hexadecimal")
        address = token_address.lower()
    else:
        address = token_address
    return normalized_chain, address


def identity_partition(chain: str, token_address: str) -> str:
    normalized_chain, address = normalize_identity(chain, token_address)
    digest = hashlib.sha256(
        PARTITION_SALT
        + b"\0"
        + normalized_chain.encode("utf-8")
        + b"\0"
        + address.encode("utf-8")
    ).digest()
    return PARTITIONS[digest[0] >> 6]


def selection_hash(cycle: ScheduledCycle, chain: str, token_address: str) -> str:
    normalized_chain, address = normalize_identity(chain, token_address)
    identity_text = f"{normalized_chain}:{address}"
    return _sha256(
        b"\0".join(
            (
                PROGRAM_ID.encode(),
                cycle.phase.encode(),
                str(cycle.index).encode(),
                identity_text.encode(),
            )
        )
    )


def _event_binding(
    candidate: Mapping[str, Any], cycle: ScheduledCycle
) -> tuple[str, int, str, str, str]:
    if cycle not in SCHEDULE:
        raise B2DesignError("cycle is outside the frozen parallel-strategy schedule")
    event_id = candidate.get("event_id")
    cycle_index = candidate.get("cycle_index")
    phase = candidate.get("phase")
    rank_band = candidate.get("rank_band")
    if (
        not isinstance(event_id, str)
        or cycle_index != cycle.index
        or phase != cycle.phase
        or not isinstance(rank_band, int)
        or isinstance(rank_band, bool)
        or not 1 <= rank_band <= TOKENS_PER_CYCLE
        or event_id != f"ps-c{cycle.index:03d}-b{rank_band:02d}"
    ):
        raise B2DesignError("candidate is not bound to the scheduled cycle")
    chain, address = normalize_identity(
        candidate.get("chain"), candidate.get("token_address")
    )
    required_partition = "DISCOVERY" if cycle.phase == "discovery" else "VALIDATION"
    if (
        candidate.get("status") != "selected"
        or candidate.get("partition") != required_partition
        or identity_partition(chain, address) != required_partition
        or candidate.get("selection_hash") != selection_hash(cycle, chain, address)
    ):
        raise B2DesignError("candidate selection provenance differs")
    return event_id, cycle.index, cycle.phase, chain, address


def screener_payload() -> dict[str, Any]:
    return {
        "chains": list(CHAINS),
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


def flow_intelligence_payload(
    candidate: Mapping[str, Any], cycle: ScheduledCycle
) -> dict[str, Any]:
    _event_binding(candidate, cycle)
    chain, address = normalize_identity(
        candidate.get("chain"), candidate.get("token_address")
    )
    return {"chain": chain, "token_address": address, "timeframe": "1d"}


def smart_money_payload(
    candidate: Mapping[str, Any], cycle: ScheduledCycle
) -> dict[str, Any]:
    from src.nansen_signal_lab.cohort_features import flow_payload

    _event_binding(candidate, cycle)
    return flow_payload(candidate, cycle.scheduled_at, "smart_money")


def breadth_payload(
    candidate: Mapping[str, Any], cycle: ScheduledCycle, side: str, page: int
) -> dict[str, Any]:
    from src.nansen_signal_lab.cohort_features import wbs_payload

    _event_binding(candidate, cycle)
    return wbs_payload(candidate, cycle.scheduled_at, side, page)


def validate_screener(body: Any) -> tuple[EligibleCandidate, ...]:
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        raise B2DesignError("screener response must contain a data list")
    pagination = body.get("pagination")
    if (
        not isinstance(pagination, dict)
        or type(pagination.get("page")) is not int
        or pagination.get("page") != 1
        or type(pagination.get("per_page")) is not int
        or pagination.get("per_page") != 1000
        or pagination.get("is_last_page") is not True
        or len(body["data"]) > 1000
    ):
        raise B2DesignError("screener response is not complete frozen page one")

    result: list[EligibleCandidate] = []
    seen: set[tuple[str, str]] = set()
    previous_netflow: float | None = None
    saw_null_netflow = False
    for source_rank, raw in enumerate(body["data"], start=1):
        if not isinstance(raw, dict):
            raise B2DesignError("screener row must be an object")
        for field in (
            "price_usd",
            "price_change",
            "volume",
            "liquidity",
            "market_cap_usd",
            "token_age_days",
            "netflow",
        ):
            if raw.get(field) is not None and _finite(raw.get(field)) is None:
                raise B2DesignError(f"screener {field} is non-null but not finite numeric")
        raw_netflow = _finite(raw.get("netflow"))
        if raw_netflow is not None:
            if saw_null_netflow or (
                previous_netflow is not None and raw_netflow > previous_netflow
            ):
                raise B2DesignError("screener rows are not raw-netflow descending")
            previous_netflow = raw_netflow
        else:
            saw_null_netflow = True
        price_change = _finite(raw.get("price_change"))
        if price_change is not None and abs(price_change) > 20:
            raise B2DesignError("screener price_change provider semantics are corrupt")
        try:
            chain, address = normalize_identity(raw.get("chain"), raw.get("token_address"))
        except B2DesignError:
            continue
        identity = (chain, address)
        if identity in seen:
            raise B2DesignError("screener contains a duplicate normalized identity")
        seen.add(identity)
        symbol = raw.get("token_symbol")
        price = _finite(raw.get("price_usd"), positive=True)
        volume = _finite(raw.get("volume"), positive=True)
        liquidity = _finite(raw.get("liquidity"), positive=True)
        market_cap = _finite(raw.get("market_cap_usd"), positive=True)
        age = _finite(raw.get("token_age_days"), nonnegative=True)
        if (
            not isinstance(symbol, str)
            or not symbol
            or price is None
            or price_change is None
            or volume is None
            or liquidity is None
            or liquidity < 250_000
            or market_cap is None
            or market_cap < 1_000_000
            or age is None
            or age < 3
            or raw_netflow is None
        ):
            continue
        row = copy.deepcopy(raw)
        result.append(
            EligibleCandidate(
                chain=chain,
                token_address=address,
                token_symbol=symbol,
                price_usd=price,
                price_change_raw=price_change,
                volume_usd=volume,
                liquidity_usd=liquidity,
                market_cap_usd=market_cap,
                token_age_days=age,
                netflow_usd=raw_netflow,
                flow_mcap_ratio=raw_netflow / market_cap,
                source_rank=source_rank,
                selected_row=row,
            )
        )
    return tuple(result)


def _validated_counts(
    values: Mapping[tuple[str, str], int], *, label: str
) -> dict[tuple[str, str], int]:
    if not isinstance(values, Mapping):
        raise B2DesignError(f"{label} must be a mapping")
    result: dict[tuple[str, str], int] = {}
    for identity, count in values.items():
        if (
            not isinstance(identity, tuple)
            or len(identity) != 2
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise B2DesignError(f"{label} contains an invalid entry")
        normalized = normalize_identity(identity[0], identity[1])
        if normalized in result:
            raise B2DesignError(f"{label} contains duplicate normalized identities")
        result[normalized] = count
    return result


def _validated_chain_counts(values: Mapping[str, int]) -> dict[str, int]:
    if not isinstance(values, Mapping):
        raise B2DesignError("prior chain counts must be a mapping")
    result = {chain: 0 for chain in CHAINS}
    seen: set[str] = set()
    for chain, count in values.items():
        normalized = _normalize_chain(chain)
        if (
            normalized not in result
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise B2DesignError("prior chain counts contains an invalid entry")
        if normalized in seen:
            raise B2DesignError("prior chain counts contains duplicate normalized chains")
        seen.add(normalized)
        result[normalized] = count
    return result


def select_cycle(
    candidates: Sequence[EligibleCandidate],
    *,
    cycle: ScheduledCycle,
    prior_identity_counts: Mapping[tuple[str, str], int],
    prior_chain_counts: Mapping[str, int],
) -> tuple[dict[str, Any], ...]:
    if cycle not in SCHEDULE:
        raise B2DesignError("cycle is outside the frozen parallel-strategy schedule")
    required_partition = "DISCOVERY" if cycle.phase == "discovery" else "VALIDATION"
    counts = _validated_counts(prior_identity_counts, label="prior identity counts")
    chain_counts = _validated_chain_counts(prior_chain_counts)
    admitted = [
        candidate
        for candidate in candidates
        if identity_partition(candidate.chain, candidate.token_address)
        == required_partition
    ]
    admitted_identities = [candidate.identity for candidate in admitted]
    if len(set(admitted_identities)) != len(admitted_identities):
        raise B2DesignError("candidate input contains duplicate normalized identities")
    if any(
        candidate.identity != normalize_identity(*candidate.identity)
        or not math.isfinite(candidate.flow_mcap_ratio)
        for candidate in admitted
    ):
        raise B2DesignError("candidate input is not normalized finite screener evidence")
    admitted.sort(
        key=lambda candidate: (
            -candidate.flow_mcap_ratio,
            candidate.chain,
            candidate.token_address,
        )
    )
    if len(admitted) < TOKENS_PER_CYCLE:
        raise B2DesignError("partition contains fewer than 13 eligible identities")
    selected: list[dict[str, Any]] = []
    for band in range(TOKENS_PER_CYCLE):
        start = math.floor(band * len(admitted) / TOKENS_PER_CYCLE)
        end = math.floor((band + 1) * len(admitted) / TOKENS_PER_CYCLE)
        pool = admitted[start:end]
        if not pool:
            raise B2DesignError("frozen rank band is empty")

        def key(candidate: EligibleCandidate) -> tuple[Any, ...]:
            identity_text = f"{candidate.chain}:{candidate.token_address}"
            digest = _sha256(
                b"\0".join(
                    (
                        PROGRAM_ID.encode(),
                        cycle.phase.encode(),
                        str(cycle.index).encode(),
                        identity_text.encode(),
                    )
                )
            )
            return (
                counts.get(candidate.identity, 0),
                chain_counts[candidate.chain],
                digest,
                candidate.chain,
                candidate.token_address,
            )

        candidate = min(pool, key=key)
        selected_hash = selection_hash(
            cycle, candidate.chain, candidate.token_address
        )
        record = {
            **asdict(candidate),
            "event_id": f"ps-c{cycle.index:03d}-b{band + 1:02d}",
            "cycle_index": cycle.index,
            "phase": cycle.phase,
            "rank_band": band + 1,
            "partition": required_partition,
            "partition_rank": admitted.index(candidate) + 1,
            "band_start_zero_based": start,
            "band_end_exclusive_zero_based": end,
            "prior_identity_count": counts.get(candidate.identity, 0),
            "prior_chain_count": chain_counts[candidate.chain],
            "selection_hash": selected_hash,
            "virtual_notional_usd": min(1000.0, 0.001 * candidate.liquidity_usd),
            "status": "selected",
        }
        selected.append(record)
    return tuple(selected)


PROSPECTIVE_PRIMITIVES: dict[str, dict[str, Any]] = {
    "screen_positive": {"source": "selection.flow_mcap_ratio", "operator": "gt", "value": 0.0},
    "buyer_breadth": {"sources": ["features.buy.address_count", "features.sell.address_count"], "operator": "gt"},
    "buyer_volume": {"sources": ["features.buy.volume_usd", "features.sell.volume_usd"], "operator": "gt"},
    "seller_breadth": {"sources": ["features.sell.address_count", "features.buy.address_count"], "operator": "gt"},
    "seller_volume": {"sources": ["features.sell.volume_usd", "features.buy.volume_usd"], "operator": "gt"},
    "exchange_outflow": {"source": "features.flow_intelligence.exchange_net_flow_usd", "operator": "lt", "value": 0.0},
    "smart_trader_positive": {"source": "features.flow_intelligence.smart_trader_net_flow_usd", "operator": "gt", "value": 0.0},
    "top_pnl_positive": {"source": "features.flow_intelligence.top_pnl_net_flow_usd", "operator": "gt", "value": 0.0},
    "whale_positive": {"source": "features.flow_intelligence.whale_net_flow_usd", "operator": "gt", "value": 0.0},
    "fresh_latest_asof_positive": {"source": "features.flow_intelligence.fresh_wallets_net_flow_usd", "operator": "gt", "value": 0.0},
    "price_nonpositive": {"source": "selection.price_change_raw", "operator": "lte", "value": 0.0},
    "price_momentum": {"source": "selection.price_change_raw", "operator": "range_open_closed", "values": [0.0, 0.15]},
}

PARENT_CANDIDATE_CONTRACT_SHA256 = (
    "aa4d1085a0b3594a8a255584e0aec7a0bdab0a6438bcc63b7af0076a9f5d056a"
)
PARENT_PROGRAM_ID = "2026-08-18-historical-theory-discovery-a-v1"
PARENT_SOURCE_COMMIT = "610f31c"
PARENT_NONCASH_CANDIDATE_IDS = tuple(
    f"c{index:02d}-{suffix}"
    for index, suffix in enumerate(
        (
            "buyer-breadth-exchange",
            "buyer-volume-exchange",
            "early-breadth-divergence",
            "early-exchange-divergence",
            "breadth-continuation",
            "top-pnl-confirmation",
            "smart-trader-confirmation",
            "three-segment-consensus",
            "fresh-wallet-confirmation",
            "buyer-breadth-benchmark",
            "screener-accumulation-benchmark",
        ),
        start=1,
    )
)
PARENT_CANDIDATE_PREDICATES = {
    "c01-buyer-breadth-exchange": ("screen_positive", "buyer_breadth", "exchange_outflow"),
    "c02-buyer-volume-exchange": ("screen_positive", "buyer_volume", "exchange_outflow"),
    "c03-early-breadth-divergence": ("screen_positive", "buyer_breadth", "price_nonpositive"),
    "c04-early-exchange-divergence": ("screen_positive", "exchange_outflow", "price_nonpositive"),
    "c05-breadth-continuation": ("screen_positive", "buyer_breadth", "price_momentum"),
    "c06-top-pnl-confirmation": ("screen_positive", "exchange_outflow", "top_pnl_positive"),
    "c07-smart-trader-confirmation": ("screen_positive", "exchange_outflow", "smart_trader_positive"),
    "c08-three-segment-consensus": ("exchange_outflow", "smart_trader_positive", "whale_positive"),
    "c09-fresh-wallet-confirmation": ("screen_positive", "exchange_outflow", "fresh_latest_asof_positive"),
    "c10-buyer-breadth-benchmark": ("screen_positive", "buyer_breadth"),
    "c11-screener-accumulation-benchmark": ("screen_positive",),
}

HISTORICAL_VETO_PREDICATES = (
    "seller_breadth",
    "seller_volume",
    "price_nonpositive",
)
PROSPECTIVE_VETO_PREDICATES = (
    "distribution_phase",
    "distribution_persistence",
    "negative_holdings_acceleration",
    "negative_holder_count_change",
)


def full_candidate_crosswalk(parent_contract: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(parent_contract, Mapping):
        raise B2DesignError("parent candidate contract must be an object")
    if parent_contract.get("program_id") != PARENT_PROGRAM_ID:
        raise B2DesignError("parent candidate contract program differs")
    definitions = parent_contract.get("candidates")
    if not isinstance(definitions, list):
        raise B2DesignError("parent candidate contract omits candidates")
    by_id = {
        item.get("id"): item
        for item in definitions
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    noncash_ids = tuple(
        item.get("id")
        for item in definitions
        if isinstance(item, dict) and item.get("cash") is not True
    )
    if noncash_ids != PARENT_NONCASH_CANDIDATE_IDS or len(by_id) != len(definitions):
        raise B2DesignError("parent candidate set or ordering differs")
    mapped: list[dict[str, Any]] = []
    for candidate_id in PARENT_NONCASH_CANDIDATE_IDS:
        definition = by_id[candidate_id]
        predicates = definition.get("predicates")
        if (
            not isinstance(predicates, list)
            or tuple(predicates) != PARENT_CANDIDATE_PREDICATES[candidate_id]
            or any(predicate not in PROSPECTIVE_PRIMITIVES for predicate in predicates)
        ):
            raise B2DesignError("candidate predicate sequence differs")
        mapped.append(
            {
                "candidate_id": candidate_id,
                "predicates": list(predicates),
                "primitives": {
                    predicate: copy.deepcopy(PROSPECTIVE_PRIMITIVES[predicate])
                    for predicate in predicates
                },
            }
        )
    return {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "source_candidate_contract_program_id": parent_contract.get("program_id"),
        "source_candidate_contract_sha256": PARENT_CANDIDATE_CONTRACT_SHA256,
        "candidates": mapped,
        "historical_veto_predicates": list(HISTORICAL_VETO_PREDICATES),
        "prospective_veto_predicates": list(PROSPECTIVE_VETO_PREDICATES),
        "composition": "strong_kleene_candidate_then_historical_veto_then_prospective_veto",
    }


def candidate_crosswalk(
    parent_contract: Mapping[str, Any], candidate_ids: Sequence[str]
) -> dict[str, Any]:
    full = full_candidate_crosswalk(parent_contract)
    if (
        not isinstance(candidate_ids, Sequence)
        or isinstance(candidate_ids, (str, bytes))
        or tuple(candidate_ids) != PARENT_NONCASH_CANDIDATE_IDS
    ):
        raise B2DesignError(
            "parallel strategy registry must contain the exact eleven-rule family"
        )
    return {
        **full,
        "candidate_ids": list(PARENT_NONCASH_CANDIDATE_IDS),
        "source_commit": PARENT_SOURCE_COMMIT,
        "independence": "candidate definitions only; no Program-A result ingress",
    }


def parallel_strategy_contract(parent_contract: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact outcome-blind eleven-rule prospective registry."""

    return candidate_crosswalk(parent_contract, PARENT_NONCASH_CANDIDATE_IDS)


def kleene_and(values: Iterable[bool | None]) -> bool | None:
    materialized = tuple(values)
    if any(value is not True and value is not False and value is not None for value in materialized):
        raise B2DesignError("tri-state conjunction received a non-tri-state value")
    if any(value is False for value in materialized):
        return False
    if all(value is True for value in materialized):
        return True
    return None


def compose_decision(
    candidate_values: Iterable[bool | None],
    historical_veto_values: Iterable[bool | None],
    prospective_veto_values: Iterable[bool | None],
) -> str:
    candidate = kleene_and(candidate_values)
    if candidate is False:
        return "abstain"
    if candidate is None:
        return "unavailable"
    historical_veto = kleene_and(historical_veto_values)
    if historical_veto is True:
        return "abstain"
    if historical_veto is None:
        return "unavailable"
    prospective_veto = kleene_and(prospective_veto_values)
    if prospective_veto is True:
        return "abstain"
    if prospective_veto is None:
        return "unavailable"
    return "long"


def prospective_distribution_veto(final_feature: Mapping[str, Any]) -> bool | None:
    if not isinstance(final_feature, Mapping):
        return None
    phase = final_feature.get("market_phase_4h")
    persistence = _finite(final_feature.get("distribution_persistence_4h"))
    acceleration = _finite(final_feature.get("holdings_acceleration_4h_pct_per_hour"))
    holders = _finite(final_feature.get("holder_count_change_4h"))
    allowed_phases = {
        "accumulation_divergence",
        "markup",
        "distribution_divergence",
        "markdown",
        "flat",
    }
    values: tuple[bool | None, ...] = (
        phase in {"markdown", "distribution_divergence"}
        if isinstance(phase, str) and phase in allowed_phases
        else None,
        None if persistence is None else persistence >= 0.75,
        None if acceleration is None else acceleration < 0,
        None if holders is None else holders < 0,
    )
    return kleene_and(values)


FLOW_INTELLIGENCE_FIELDS = tuple(
    f"{segment}_{metric}"
    for segment in (
        "public_figure",
        "top_pnl",
        "whale",
        "smart_trader",
        "exchange",
        "fresh_wallets",
    )
    for metric in ("net_flow_usd", "avg_flow_usd", "wallet_count")
)


def validate_flow_intelligence(
    body: Any,
    *,
    candidate: Mapping[str, Any],
    cycle: ScheduledCycle,
    cache_hit: bool,
    retrieved_at: datetime,
) -> dict[str, Any]:
    """Normalize the prospective singleton without treating nullable segments as zero."""

    event_id, cycle_index, phase, expected_chain, expected_address = _event_binding(
        candidate, cycle
    )
    retrieved = _utc(retrieved_at, field="flow-intelligence retrieved_at")
    not_before = cycle.scheduled_at
    deadline = cycle.scheduled_at + DECISION_DEADLINE
    if not not_before <= retrieved < deadline:
        return {"available": False, "reason": "retrieval_outside_decision_window"}
    if cache_hit is not False:
        return {"available": False, "reason": "cache_hit_not_admissible"}
    if not isinstance(body, dict):
        raise B2DesignError("flow-intelligence response must be an object")
    if "chain" in body:
        actual_chain, _ = normalize_identity(body.get("chain"), expected_address)
        if actual_chain != expected_chain:
            raise B2DesignError("flow-intelligence response chain differs")
    if "token_address" in body:
        _, actual_address = normalize_identity(expected_chain, body.get("token_address"))
        if actual_address != expected_address:
            raise B2DesignError("flow-intelligence response token differs")
    warnings = body.get("warnings", [])
    if warnings is None:
        warnings = []
    if not isinstance(warnings, list) or any(not isinstance(item, str) for item in warnings):
        raise B2DesignError("flow-intelligence warnings are malformed")
    if warnings:
        return {"available": False, "reason": "provider_warnings", "warning_count": len(warnings)}
    data = body.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise B2DesignError("flow-intelligence data must contain exactly one object")
    source = data[0]
    metrics: dict[str, float | int | None] = {}
    for field in FLOW_INTELLIGENCE_FIELDS:
        value = source.get(field)
        if value is None:
            metrics[field] = None
            continue
        number = _finite(value)
        if number is None:
            raise B2DesignError(f"flow-intelligence {field} must be finite or null")
        if field.endswith("wallet_count"):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise B2DesignError(
                    f"flow-intelligence {field} must be a nonnegative integer"
                )
            metrics[field] = value
        else:
            metrics[field] = number
    return {
        "schema_version": 1,
        "source_kind": "validated_flow_intelligence",
        "identity": {"chain": expected_chain, "token_address": expected_address},
        "event_id": event_id,
        "cycle_index": cycle_index,
        "phase": phase,
        "scheduled_at": cycle.scheduled_at.isoformat().replace("+00:00", "Z"),
        "available": True,
        "reason": None,
        "warnings_present": False,
        "cache_hit": False,
        "retrieved_at": retrieved.isoformat().replace("+00:00", "Z"),
        **metrics,
    }


def predicate_values(
    selection: Mapping[str, Any], features: Mapping[str, Any]
) -> dict[str, bool | None]:
    buy = features.get("buy") if isinstance(features, Mapping) else None
    sell = features.get("sell") if isinstance(features, Mapping) else None
    flow = features.get("flow_intelligence") if isinstance(features, Mapping) else None

    def directional(field: str, *, buyer_on_left: bool) -> bool | None:
        if (
            not isinstance(buy, Mapping)
            or not isinstance(sell, Mapping)
            or buy.get("available") is not True
            or sell.get("available") is not True
            or buy.get("schema_version") != 1
            or sell.get("schema_version") != 1
            or buy.get("source_kind") != "validated_wbs"
            or sell.get("source_kind") != "validated_wbs"
        ):
            return None
        left = buy.get(field) if buyer_on_left else sell.get(field)
        right = sell.get(field) if buyer_on_left else buy.get(field)
        if field == "address_count":
            if any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in (left, right)
            ):
                return None
            return left > right
        left_number = _finite(left, nonnegative=True)
        right_number = _finite(right, nonnegative=True)
        if left_number is None or right_number is None:
            return None
        return left_number > right_number

    def signed(field: str, operator: str) -> bool | None:
        if (
            not isinstance(flow, Mapping)
            or flow.get("available") is not True
            or flow.get("schema_version") != 1
            or flow.get("source_kind") != "validated_flow_intelligence"
            or flow.get("warnings_present") is not False
            or flow.get("cache_hit") is not False
        ):
            return None
        number = _finite(flow.get(field))
        if number is None:
            return None
        return number < 0 if operator == "lt" else number > 0

    ratio = _finite(selection.get("flow_mcap_ratio"))
    price_change = _finite(selection.get("price_change_raw"))
    return {
        "screen_positive": None if ratio is None else ratio > 0,
        "buyer_breadth": directional("address_count", buyer_on_left=True),
        "buyer_volume": directional("volume_usd", buyer_on_left=True),
        "seller_breadth": directional("address_count", buyer_on_left=False),
        "seller_volume": directional("volume_usd", buyer_on_left=False),
        "exchange_outflow": signed("exchange_net_flow_usd", "lt"),
        "smart_trader_positive": signed("smart_trader_net_flow_usd", "gt"),
        "top_pnl_positive": signed("top_pnl_net_flow_usd", "gt"),
        "whale_positive": signed("whale_net_flow_usd", "gt"),
        "fresh_latest_asof_positive": signed("fresh_wallets_net_flow_usd", "gt"),
        "price_nonpositive": None if price_change is None else price_change <= 0,
        "price_momentum": None if price_change is None else 0 < price_change <= 0.15,
    }


def normalized_wbs_evidence(
    evidence: Any,
    *,
    candidate: Mapping[str, Any],
    cycle: ScheduledCycle,
    side: str,
) -> dict[str, Any]:
    if side not in {"BUY", "SELL"}:
        raise B2DesignError("WBS evidence side must be BUY or SELL")
    event_id, cycle_index, phase, chain, address = _event_binding(candidate, cycle)
    if getattr(evidence, "side", None) != side:
        raise B2DesignError("validated WBS evidence side differs")
    available = getattr(evidence, "available", None)
    if not isinstance(available, bool):
        raise B2DesignError("validated WBS evidence availability is invalid")
    count = getattr(evidence, "distinct_addresses", None)
    volume = getattr(evidence, "directional_volume_usd", None)
    if available:
        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            or _finite(volume, nonnegative=True) is None
        ):
            raise B2DesignError("validated WBS evidence totals are invalid")
    else:
        count = None
        volume = None
    return {
        "schema_version": 1,
        "source_kind": "validated_wbs",
        "identity": {"chain": chain, "token_address": address},
        "event_id": event_id,
        "cycle_index": cycle_index,
        "phase": phase,
        "scheduled_at": cycle.scheduled_at.isoformat().replace("+00:00", "Z"),
        "side": side,
        "available": available,
        "reason": getattr(evidence, "reason", None),
        "address_count": count,
        "volume_usd": volume,
    }


def validate_wbs_evidence(
    pages: Sequence[dict[str, Any]],
    *,
    candidate: Mapping[str, Any],
    cycle: ScheduledCycle,
    side: str,
) -> dict[str, Any]:
    from src.nansen_signal_lab.cohort_features import validate_wbs_pages

    normalized_pages = copy.deepcopy(list(pages))
    for page in normalized_pages:
        if isinstance(page, dict) and page.get("chain") == "bsc":
            page["chain"] = "bnb"
    evidence = validate_wbs_pages(normalized_pages, candidate=candidate, side=side)
    return normalized_wbs_evidence(
        evidence, candidate=candidate, cycle=cycle, side=side
    )


def validate_smart_money_evidence(
    body: Any,
    *,
    candidate: Mapping[str, Any],
    cycle: ScheduledCycle,
    source_id: str,
) -> dict[str, Any]:
    from src.nansen_signal_lab.cohort_features import (
        signal_feature_rows,
        validate_flow_body,
    )

    event_id, cycle_index, phase, chain, address = _event_binding(candidate, cycle)
    normalized_body = copy.deepcopy(body)
    if isinstance(normalized_body, dict) and normalized_body.get("chain") == "bsc":
        normalized_body["chain"] = "bnb"
    rows = validate_flow_body(
        normalized_body,
        candidate=candidate,
        label="smart_money",
        cutoff=cycle.scheduled_at,
    )
    features = signal_feature_rows(rows, source_id=source_id)
    return {
        "schema_version": 1,
        "source_kind": "validated_smart_money_flow",
        "identity": {"chain": chain, "token_address": address},
        "event_id": event_id,
        "cycle_index": cycle_index,
        "phase": phase,
        "scheduled_at": cycle.scheduled_at.isoformat().replace("+00:00", "Z"),
        "row_count": len(rows),
        "final_feature": features[-1],
    }


def candidate_decision(
    *,
    selection: Mapping[str, Any],
    features: Mapping[str, Any],
    candidate: Mapping[str, Any],
    cycle: ScheduledCycle,
    sealed_crosswalk: Mapping[str, Any],
) -> dict[str, Any]:
    predicates = candidate.get("predicates")
    candidate_id = candidate.get("candidate_id", candidate.get("id"))
    expected_definitions = [
        {
            "candidate_id": registered_id,
            "predicates": list(PARENT_CANDIDATE_PREDICATES[registered_id]),
            "primitives": {
                predicate: copy.deepcopy(PROSPECTIVE_PRIMITIVES[predicate])
                for predicate in PARENT_CANDIDATE_PREDICATES[registered_id]
            },
        }
        for registered_id in PARENT_NONCASH_CANDIDATE_IDS
    ]
    if (
        not isinstance(candidate_id, str)
        or candidate_id not in PARENT_CANDIDATE_PREDICATES
        or not isinstance(predicates, list)
        or tuple(predicates) != PARENT_CANDIDATE_PREDICATES[candidate_id]
        or candidate != expected_definitions[
            PARENT_NONCASH_CANDIDATE_IDS.index(candidate_id)
        ]
    ):
        raise B2DesignError("candidate definition is invalid")
    event_id, cycle_index, phase, chain, address = _event_binding(selection, cycle)
    definitions = sealed_crosswalk.get("candidates")
    crosswalk_fields = {
        "schema_version",
        "program_id",
        "source_candidate_contract_program_id",
        "source_candidate_contract_sha256",
        "candidates",
        "historical_veto_predicates",
        "prospective_veto_predicates",
        "composition",
        "candidate_ids",
        "source_commit",
        "independence",
    }
    source = sealed_crosswalk.get("source")
    if (
        set(sealed_crosswalk) not in (crosswalk_fields, crosswalk_fields | {"source"})
        or sealed_crosswalk.get("schema_version") != 1
        or sealed_crosswalk.get("program_id") != PROGRAM_ID
        or sealed_crosswalk.get("source_candidate_contract_program_id")
        != PARENT_PROGRAM_ID
        or sealed_crosswalk.get("source_candidate_contract_sha256")
        != PARENT_CANDIDATE_CONTRACT_SHA256
        or sealed_crosswalk.get("candidate_ids")
        != list(PARENT_NONCASH_CANDIDATE_IDS)
        or definitions != expected_definitions
        or sealed_crosswalk.get("historical_veto_predicates")
        != list(HISTORICAL_VETO_PREDICATES)
        or sealed_crosswalk.get("prospective_veto_predicates")
        != list(PROSPECTIVE_VETO_PREDICATES)
        or sealed_crosswalk.get("composition")
        != "strong_kleene_candidate_then_historical_veto_then_prospective_veto"
        or sealed_crosswalk.get("source_commit") != PARENT_SOURCE_COMMIT
        or sealed_crosswalk.get("independence")
        != "candidate definitions only; no Program-A result ingress"
        or (
            "source" in sealed_crosswalk
            and source
            != {
                "repository_relative_path": (
                    "research/experiments/"
                    "2026-08-18-historical-theory-discovery-a-v1/"
                    "contracts/candidates.json"
                ),
                "source_commit": PARENT_SOURCE_COMMIT,
                "sha256": PARENT_CANDIDATE_CONTRACT_SHA256,
                "scope": "candidate definitions only",
            }
        )
    ):
        raise B2DesignError("candidate definition is not in the sealed crosswalk")
    values = predicate_values(selection, features)
    smart = features.get("smart_money") if isinstance(features, Mapping) else None
    selection_identity = normalize_identity(
        selection.get("chain"), selection.get("token_address")
    )

    scheduled_text = cycle.scheduled_at.isoformat().replace("+00:00", "Z")

    def evidence_matches(value: Any, source_kind: str, *, side: str | None = None) -> bool:
        if (
            not isinstance(value, Mapping)
            or value.get("schema_version") != 1
            or value.get("source_kind") != source_kind
            or not isinstance(value.get("identity"), Mapping)
            or value.get("event_id") != event_id
            or value.get("cycle_index") != cycle_index
            or value.get("phase") != phase
            or value.get("scheduled_at") != scheduled_text
            or (side is not None and value.get("side") != side)
        ):
            return False
        identity = value["identity"]
        try:
            return normalize_identity(
                identity.get("chain"), identity.get("token_address")
            ) == selection_identity
        except B2DesignError:
            return False

    if not evidence_matches(features.get("buy"), "validated_wbs", side="BUY"):
        values["buyer_breadth"] = values["buyer_volume"] = None
        values["seller_breadth"] = values["seller_volume"] = None
    if not evidence_matches(features.get("sell"), "validated_wbs", side="SELL"):
        values["buyer_breadth"] = values["buyer_volume"] = None
        values["seller_breadth"] = values["seller_volume"] = None
    if not evidence_matches(
        features.get("flow_intelligence"), "validated_flow_intelligence"
    ):
        for name in (
            "exchange_outflow",
            "smart_trader_positive",
            "top_pnl_positive",
            "whale_positive",
            "fresh_latest_asof_positive",
        ):
            values[name] = None
    final_feature = (
        smart.get("final_feature")
        if evidence_matches(smart, "validated_smart_money_flow")
        else None
    )
    prospective_veto = prospective_distribution_veto(final_feature)
    action = compose_decision(
        (values[predicate] for predicate in predicates),
        (values[predicate] for predicate in HISTORICAL_VETO_PREDICATES),
        (prospective_veto,),
    )
    return {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "source_kind": "sealed_candidate_decision",
        "event_id": event_id,
        "cycle_index": cycle_index,
        "phase": phase,
        "scheduled_at": scheduled_text,
        "identity": {"chain": chain, "token_address": address},
        "candidate_contract_sha256": PARENT_CANDIDATE_CONTRACT_SHA256,
        "input_sha256": {
            "selection": canonical_sha256(selection),
            "features": canonical_sha256(features),
            "candidate_definition": canonical_sha256(candidate),
            "sealed_crosswalk": canonical_sha256(sealed_crosswalk),
        },
        "candidate_id": candidate_id,
        "decision": action,
        "candidate_predicates": {predicate: values[predicate] for predicate in predicates},
        "historical_veto_predicates": {
            predicate: values[predicate] for predicate in HISTORICAL_VETO_PREDICATES
        },
        "prospective_veto": prospective_veto,
    }


def _execution_result(
    *,
    notional_usd: float,
    entry_fill: Any,
    exit_fill: Any | None,
) -> dict[str, Any]:
    notional = _finite(notional_usd, positive=True)
    if notional is None:
        raise B2DesignError("notional must be positive and finite")
    if getattr(entry_fill, "side", None) != "BUY":
        raise B2DesignError("entry fill side must be BUY")
    entry_requested = _finite(getattr(entry_fill, "requested_amount", None), positive=True)
    entry_tokens = _finite(getattr(entry_fill, "filled_token_amount", None), nonnegative=True)
    entry_observed = _finite(getattr(entry_fill, "observed_usd", None), nonnegative=True)
    entry_ratio = _finite(getattr(entry_fill, "fill_ratio", None), nonnegative=True)
    entry_vwap = getattr(entry_fill, "vwap_usd", None)
    entry_trades = getattr(entry_fill, "trade_count", None)
    entry_complete = getattr(entry_fill, "is_complete", None)
    if (
        entry_tokens is None
        or entry_requested is None
        or entry_observed is None
        or entry_ratio is None
        or entry_ratio > 1
        or (entry_vwap is not None and _finite(entry_vwap, positive=True) is None)
        or not isinstance(entry_trades, int)
        or isinstance(entry_trades, bool)
        or entry_trades < 0
        or not isinstance(entry_complete, bool)
    ):
        raise B2DesignError("entry fill is invalid")
    tolerance = max(1e-8, notional * 1e-10)
    if (
        not math.isclose(entry_requested, notional, rel_tol=1e-10, abs_tol=tolerance)
        or not math.isclose(
            entry_observed / notional,
            entry_ratio,
            rel_tol=1e-10,
            abs_tol=1e-10,
        )
        or entry_complete
        != math.isclose(entry_ratio, 1.0, rel_tol=1e-10, abs_tol=1e-10)
        or (entry_complete and entry_tokens <= 0)
        or (entry_tokens == 0) != (entry_observed == 0)
        or (entry_tokens == 0) != (entry_vwap is None)
        or (entry_tokens == 0) != (entry_trades == 0)
        or (
            entry_tokens > 0
            and not math.isclose(
                float(entry_vwap),
                entry_observed / entry_tokens,
                rel_tol=1e-10,
                abs_tol=max(1e-12, entry_observed * 1e-10),
            )
        )
    ):
        raise B2DesignError("entry fill invariants differ")
    result: dict[str, Any] = {
        "available": True,
        "notional_usd": notional,
        "status": "unfilled_entry" if entry_tokens == 0 else "partial",
        "entry": {
            "requested_amount": entry_requested,
            "filled_token_amount": entry_tokens,
            "observed_usd": entry_observed,
            "vwap_usd": entry_vwap,
            "trade_count": entry_trades,
            "fill_ratio": entry_ratio,
            "is_complete": entry_complete,
        },
        "exit": None,
    }
    if entry_tokens == 0:
        return result
    if exit_fill is None:
        raise B2DesignError("positive entry acquisition requires exit evidence")
    if getattr(exit_fill, "side", None) != "SELL":
        raise B2DesignError("exit fill side must be SELL")
    exit_requested = _finite(getattr(exit_fill, "requested_amount", None), positive=True)
    exit_tokens = _finite(getattr(exit_fill, "filled_token_amount", None), nonnegative=True)
    exit_observed = _finite(getattr(exit_fill, "observed_usd", None), nonnegative=True)
    exit_ratio = _finite(getattr(exit_fill, "fill_ratio", None), nonnegative=True)
    exit_vwap = getattr(exit_fill, "vwap_usd", None)
    exit_trades = getattr(exit_fill, "trade_count", None)
    exit_complete = getattr(exit_fill, "is_complete", None)
    if (
        exit_tokens is None
        or exit_requested is None
        or exit_observed is None
        or exit_ratio is None
        or exit_ratio > 1
        or (exit_vwap is not None and _finite(exit_vwap, positive=True) is None)
        or not isinstance(exit_trades, int)
        or isinstance(exit_trades, bool)
        or exit_trades < 0
        or not isinstance(exit_complete, bool)
    ):
        raise B2DesignError("exit fill is invalid")
    if (
        not math.isclose(
            exit_requested, entry_tokens, rel_tol=1e-10, abs_tol=max(1e-12, entry_tokens * 1e-10)
        )
        or not math.isclose(
            exit_tokens / exit_requested,
            exit_ratio,
            rel_tol=1e-10,
            abs_tol=1e-10,
        )
        or exit_complete
        != math.isclose(exit_ratio, 1.0, rel_tol=1e-10, abs_tol=1e-10)
        or (exit_tokens == 0) != (exit_observed == 0)
        or (exit_tokens == 0) != (exit_vwap is None)
        or (exit_tokens == 0) != (exit_trades == 0)
        or (
            exit_tokens > 0
            and not math.isclose(
                float(exit_vwap),
                exit_observed / exit_tokens,
                rel_tol=1e-10,
                abs_tol=max(1e-12, exit_observed * 1e-10),
            )
        )
    ):
        raise B2DesignError("exit fill invariants differ")
    result["exit"] = {
        "requested_amount": exit_requested,
        "sold_token_amount": exit_tokens,
        "observed_usd": exit_observed,
        "vwap_usd": exit_vwap,
        "trade_count": exit_trades,
        "fill_ratio": exit_ratio,
        "is_complete": exit_complete,
    }
    if entry_complete and exit_complete:
        multiple = exit_observed / notional
        result.update(
            {
                "status": "filled",
                "gross_return": multiple - 1,
                "base_return": multiple * (1 - BASE_COST_RATE) ** 2 - 1,
                "stress_return": multiple * (1 - STRESS_COST_RATE) ** 2 - 1,
            }
        )
    return result


def build_counterfactual_outcome(
    *,
    candidate: Mapping[str, Any],
    cycle: ScheduledCycle,
    t0: datetime,
    buy_pages: Sequence[dict[str, Any]],
    sell_pages: Sequence[dict[str, Any]],
    ohlcv_body: dict[str, Any],
    retrieved_at: datetime,
) -> dict[str, Any]:
    from src.nansen_signal_lab.cohort_execution import (
        build_entry_fill,
        build_exit_fill,
        execution_windows,
        validate_ohlcv,
        validate_trade_pages,
    )

    event_id, cycle_index, phase, normalized_chain, normalized_address = _event_binding(
        candidate, cycle
    )
    admitted_t0 = _utc(t0, field="t0")
    retrieved = _utc(retrieved_at, field="retrieved_at")
    if (
        admitted_t0 < cycle.scheduled_at + timedelta(minutes=5)
        or admitted_t0 > cycle.scheduled_at + timedelta(minutes=50)
        or admitted_t0.minute % 5
        or admitted_t0.second
        or admitted_t0.microsecond
    ):
        raise B2DesignError("counterfactual t0 is not bound to the scheduled cycle")
    if (
        retrieved < admitted_t0 + SETTLEMENT_OFFSET
        or retrieved >= cycle.scheduled_at + SETTLEMENT_TRANSPORT_CUTOFF
    ):
        raise B2DesignError("counterfactual retrieval is outside the settlement window")
    notional = _finite(candidate.get("virtual_notional_usd"), positive=True)
    if notional is None:
        raise B2DesignError("selected candidate omits virtual notional")
    windows = execution_windows(admitted_t0)
    entry = build_entry_fill(
        buy_pages,
        candidate=candidate,
        notional_usd=notional,
        start=windows["entry_start"],
        end=windows["entry_end"],
    )
    if entry.filled_token_amount > 0:
        exit_fill = build_exit_fill(
            sell_pages,
            candidate=candidate,
            token_amount=entry.filled_token_amount,
            start=windows["exit_start"],
            end=windows["exit_end"],
        )
    else:
        # SELL evidence is still common and must be contract-valid even when
        # the entry acquired no tokens.
        validate_trade_pages(
            sell_pages,
            candidate=candidate,
            side="SELL",
            start=windows["exit_start"],
            end=windows["exit_end"],
        )
        exit_fill = None
    normalized_ohlcv = copy.deepcopy(ohlcv_body)
    if isinstance(normalized_ohlcv, dict) and normalized_ohlcv.get("chain") == "bsc":
        normalized_ohlcv["chain"] = "bnb"
    ohlcv = validate_ohlcv(
        normalized_ohlcv,
        candidate=candidate,
        start=windows["ohlcv_start"],
        end=windows["ohlcv_end"],
        retrieved_at=retrieved,
    )
    result = _execution_result(
        notional_usd=notional,
        entry_fill=entry,
        exit_fill=exit_fill,
    )
    result.update(
        {
            "schema_version": 1,
            "source_kind": "validated_counterfactual_outcome",
            "event_id": event_id,
            "cycle_index": cycle_index,
            "phase": phase,
            "scheduled_at": cycle.scheduled_at.isoformat().replace("+00:00", "Z"),
            "identity": {
                "chain": normalized_chain,
                "token_address": normalized_address,
            },
            "ohlcv_available": True,
            "ohlcv_row_count": len(ohlcv),
            "t0": admitted_t0.isoformat().replace("+00:00", "Z"),
            "retrieved_at": retrieved.isoformat().replace("+00:00", "Z"),
            "evidence_sha256": {
                "buy_pages": [canonical_sha256(page) for page in buy_pages],
                "sell_pages": [canonical_sha256(page) for page in sell_pages],
                "ohlcv": canonical_sha256(ohlcv_body),
            },
        }
    )
    return result


def token_equal_mean(events: Sequence[Mapping[str, Any]], field: str) -> float:
    by_token: dict[tuple[str, str], list[float]] = {}
    for event in events:
        chain, address = normalize_identity(event.get("chain"), event.get("token_address"))
        value = _finite(event.get(field))
        if value is None:
            raise B2DesignError(f"scored event omits finite {field}")
        by_token.setdefault((chain, address), []).append(value)
    if not by_token:
        raise B2DesignError("token-equal mean requires at least one event")
    return statistics.fmean(statistics.fmean(values) for values in by_token.values())
