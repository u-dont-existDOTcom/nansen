from __future__ import annotations

"""Pure, point-in-time candidate selection for prospective cohort cycles."""

import copy
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class CohortSelectionError(ValueError):
    """Raised when a screener page cannot produce the frozen five-token cohort."""


SCREENER_CHAINS = ("solana", "ethereum", "base", "bnb", "arbitrum")
STRATA = (
    "early_accumulation",
    "middle_accumulation",
    "momentum_accumulation",
    "neutral_control",
    "distribution_control",
)

_SCREENER_PAYLOAD = {
    "chains": list(SCREENER_CHAINS),
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


@dataclass(frozen=True)
class SelectedCandidate:
    stratum: str
    chain: str
    token_address: str
    token_symbol: str
    price_usd: float
    price_change_raw: float
    price_change_pct: float
    volume_usd: float
    liquidity_usd: float
    market_cap_usd: float
    token_age_days: float
    netflow_usd: float
    flow_mcap_ratio: float
    prior_selection_count: int
    selected_row: dict[str, Any]


@dataclass(frozen=True)
class _EligibleCandidate:
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
    row: dict[str, Any]

    @property
    def identity(self) -> tuple[str, str]:
        return normalized_identity(self.chain, self.token_address)

    @property
    def flow_mcap_ratio(self) -> float:
        return self.netflow_usd / self.market_cap_usd


def screener_payload() -> dict[str, Any]:
    """Return a fresh copy of the exact preregistered page-one request."""

    return json.loads(json.dumps(_SCREENER_PAYLOAD))


def normalized_identity(chain: str, token_address: str) -> tuple[str, str]:
    """Normalize chains and only EVM-style addresses; preserve non-EVM case."""

    if not isinstance(chain, str) or not chain:
        raise CohortSelectionError("candidate chain must be a non-empty string")
    if not isinstance(token_address, str) or not token_address:
        raise CohortSelectionError("candidate token_address must be a non-empty string")
    address = (
        token_address.lower()
        if token_address.lower().startswith("0x")
        else token_address
    )
    return chain.lower(), address


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _eligible_candidate(row: Any) -> _EligibleCandidate | None:
    if not isinstance(row, dict):
        return None
    chain = row.get("chain")
    address = row.get("token_address")
    symbol = row.get("token_symbol")
    if (
        not isinstance(chain, str)
        or chain not in SCREENER_CHAINS
        or not isinstance(address, str)
        or not address
        or not isinstance(symbol, str)
        or not symbol
    ):
        return None

    price = _finite_number(row.get("price_usd"))
    price_change = _finite_number(row.get("price_change"))
    volume = _finite_number(row.get("volume"))
    liquidity = _finite_number(row.get("liquidity"))
    market_cap = _finite_number(row.get("market_cap_usd"))
    age = _finite_number(row.get("token_age_days"))
    netflow = _finite_number(row.get("netflow"))
    if (
        price is None
        or price <= 0
        or price_change is None
        or abs(price_change) > 20
        or volume is None
        or volume <= 0
        or liquidity is None
        or liquidity < 250_000
        or market_cap is None
        or market_cap < 1_000_000
        or age is None
        or age < 3
        or netflow is None
    ):
        return None
    return _EligibleCandidate(
        chain=chain,
        token_address=address,
        token_symbol=symbol,
        price_usd=price,
        price_change_raw=price_change,
        volume_usd=volume,
        liquidity_usd=liquidity,
        market_cap_usd=market_cap,
        token_age_days=age,
        netflow_usd=netflow,
        row=copy.deepcopy(row),
    )


def _complete_page_one(body: Any) -> list[Any]:
    if not isinstance(body, dict):
        raise CohortSelectionError("screener response must be an object")
    rows = body.get("data")
    pagination = body.get("pagination")
    if not isinstance(rows, list):
        raise CohortSelectionError("screener response data must be a list")
    if not isinstance(pagination, dict):
        raise CohortSelectionError("screener pagination must be an object")
    if (
        pagination.get("page") != 1
        or isinstance(pagination.get("page"), bool)
        or pagination.get("per_page") != 1000
        or isinstance(pagination.get("per_page"), bool)
        or pagination.get("is_last_page") is not True
    ):
        raise CohortSelectionError(
            "screener response must be complete page 1 with per_page=1000"
        )
    return rows


def _prior_counts(
    value: Mapping[tuple[str, str], int],
) -> dict[tuple[str, str], int]:
    if not isinstance(value, Mapping):
        raise CohortSelectionError("prior_counts must be a mapping")
    normalized: dict[tuple[str, str], int] = {}
    for identity, count in value.items():
        if (
            not isinstance(identity, tuple)
            or len(identity) != 2
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise CohortSelectionError("prior_counts contains an invalid entry")
        key = normalized_identity(identity[0], identity[1])
        if key in normalized:
            raise CohortSelectionError("prior_counts contains duplicate identities")
        normalized[key] = count
    return normalized


def _identity_sort(candidate: _EligibleCandidate) -> tuple[str, str]:
    return candidate.identity


def _signal_key(
    candidate: _EligibleCandidate, counts: Mapping[tuple[str, str], int]
) -> tuple[int, float, str, str]:
    chain, address = _identity_sort(candidate)
    return (
        counts.get(candidate.identity, 0),
        -candidate.flow_mcap_ratio,
        chain,
        address,
    )


def _distribution_key(
    candidate: _EligibleCandidate, counts: Mapping[tuple[str, str], int]
) -> tuple[int, float, str, str]:
    chain, address = _identity_sort(candidate)
    return (
        counts.get(candidate.identity, 0),
        candidate.flow_mcap_ratio,
        chain,
        address,
    )


def _neutral_key(
    candidate: _EligibleCandidate, counts: Mapping[tuple[str, str], int]
) -> tuple[int, float, str, str]:
    chain, address = _identity_sort(candidate)
    return (
        counts.get(candidate.identity, 0),
        abs(candidate.flow_mcap_ratio),
        chain,
        address,
    )


def _selected(
    candidate: _EligibleCandidate,
    stratum: str,
    counts: Mapping[tuple[str, str], int],
) -> SelectedCandidate:
    return SelectedCandidate(
        stratum=stratum,
        chain=candidate.chain,
        token_address=candidate.token_address,
        token_symbol=candidate.token_symbol,
        price_usd=candidate.price_usd,
        price_change_raw=candidate.price_change_raw,
        price_change_pct=100.0 * candidate.price_change_raw,
        volume_usd=candidate.volume_usd,
        liquidity_usd=candidate.liquidity_usd,
        market_cap_usd=candidate.market_cap_usd,
        token_age_days=candidate.token_age_days,
        netflow_usd=candidate.netflow_usd,
        flow_mcap_ratio=candidate.flow_mcap_ratio,
        prior_selection_count=counts.get(candidate.identity, 0),
        selected_row=copy.deepcopy(candidate.row),
    )


def select_cohort(
    body: Any,
    prior_counts: Mapping[tuple[str, str], int],
) -> tuple[SelectedCandidate, ...]:
    """Select five disjoint strata from one explicitly complete screener page.

    Prior selection count is the first ranking key in every stratum. It rotates
    exposure without consulting fills, outcomes, or returns.
    """

    rows = _complete_page_one(body)
    counts = _prior_counts(prior_counts)
    eligible: list[_EligibleCandidate] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        candidate = _eligible_candidate(row)
        if candidate is None:
            continue
        if candidate.identity in seen:
            raise CohortSelectionError("screener page contains duplicate token identities")
        seen.add(candidate.identity)
        eligible.append(candidate)
    if not eligible:
        raise CohortSelectionError("screener page contains no eligible candidates")

    pools = {
        "early_accumulation": [
            item
            for item in eligible
            if item.netflow_usd > 0 and item.price_change_raw <= 0.05
        ],
        "middle_accumulation": [
            item
            for item in eligible
            if item.netflow_usd > 0 and 0.05 < item.price_change_raw <= 0.15
        ],
        "momentum_accumulation": [
            item
            for item in eligible
            if item.netflow_usd > 0 and item.price_change_raw > 0.15
        ],
    }
    chosen: dict[str, _EligibleCandidate] = {}
    used: set[tuple[str, str]] = set()
    for stratum in STRATA[:3]:
        available = [item for item in pools[stratum] if item.identity not in used]
        if not available:
            raise CohortSelectionError(f"screener page has no candidate for {stratum}")
        candidate = min(available, key=lambda item: _signal_key(item, counts))
        chosen[stratum] = candidate
        used.add(candidate.identity)

    # Reserve a negative-flow token before selecting the neutral control so a
    # single distribution candidate cannot be consumed by both definitions.
    distribution = [
        item
        for item in eligible
        if item.identity not in used and item.netflow_usd < 0
    ]
    if not distribution:
        raise CohortSelectionError("screener page has no candidate for distribution_control")
    distribution_candidate = min(
        distribution, key=lambda item: _distribution_key(item, counts)
    )
    chosen["distribution_control"] = distribution_candidate
    used.add(distribution_candidate.identity)

    neutral = [item for item in eligible if item.identity not in used]
    if not neutral:
        raise CohortSelectionError("screener page has no candidate for neutral_control")
    chosen["neutral_control"] = min(
        neutral, key=lambda item: _neutral_key(item, counts)
    )

    return tuple(_selected(chosen[stratum], stratum, counts) for stratum in STRATA)
