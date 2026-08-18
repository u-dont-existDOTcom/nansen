from __future__ import annotations

import copy

import pytest

from src.nansen_signal_lab.cohort_selection import (
    CohortSelectionError,
    SCREENER_CHAINS,
    STRATA,
    normalized_identity,
    screener_payload,
    select_cohort,
)


def _row(
    symbol: str,
    address: str,
    *,
    chain: str = "base",
    price_change: float = 0.0,
    netflow: float = 1_000.0,
    market_cap: float = 1_000_000.0,
    liquidity: float = 250_000.0,
    volume: float = 10_000.0,
    age: float = 3.0,
    price: float = 1.0,
) -> dict:
    return {
        "chain": chain,
        "token_address": address,
        "token_symbol": symbol,
        "token_age_days": age,
        "market_cap_usd": market_cap,
        "liquidity": liquidity,
        "price_usd": price,
        "price_change": price_change,
        "volume": volume,
        "netflow": netflow,
    }


def _body(*rows: dict, page: int = 1, per_page: int = 1000, final=True) -> dict:
    return {
        "data": list(rows),
        "pagination": {
            "page": page,
            "per_page": per_page,
            "is_last_page": final,
        },
    }


def _five_rows() -> tuple[dict, ...]:
    return (
        _row("EARLY", "0x01", price_change=0.05, netflow=500),
        _row("MIDDLE", "0x02", price_change=0.10, netflow=400),
        _row("MOMENTUM", "0x03", price_change=0.16, netflow=300),
        _row("NEUTRAL", "0x04", price_change=-0.50, netflow=0),
        _row("DISTRIBUTION", "0x05", price_change=0.50, netflow=-600),
    )


def test_exact_screener_payload_is_fresh_and_literal():
    first = screener_payload()
    assert first == {
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
    assert tuple(first["chains"]) == SCREENER_CHAINS
    first["filters"]["liquidity"]["min"] = 0
    assert screener_payload()["filters"]["liquidity"]["min"] == 250_000


@pytest.mark.parametrize(
    "body",
    (
        None,
        {},
        {"data": [], "pagination": None},
        _body(*_five_rows(), page=2),
        _body(*_five_rows(), per_page=999),
        _body(*_five_rows(), final=False),
        _body(*_five_rows(), final=1),
    ),
)
def test_selection_requires_one_explicitly_complete_1000_row_page(body):
    with pytest.raises(CohortSelectionError):
        select_cohort(body, {})


def test_selects_exact_disjoint_strata_and_preserves_raw_and_percent_price():
    selected = select_cohort(_body(*_five_rows()), {})
    assert tuple(item.stratum for item in selected) == STRATA
    assert tuple(item.token_symbol for item in selected) == (
        "EARLY",
        "MIDDLE",
        "MOMENTUM",
        "NEUTRAL",
        "DISTRIBUTION",
    )
    assert len({normalized_identity(item.chain, item.token_address) for item in selected}) == 5
    assert selected[0].price_change_raw == 0.05
    assert selected[0].price_change_pct == 5.0
    assert selected[0].flow_mcap_ratio == 0.0005
    assert selected[0].prior_selection_count == 0
    assert selected[0].selected_row == _five_rows()[0]


def test_prior_count_rotates_before_signal_strength_and_normalizes_evm_identity():
    rows = (
        _row("USED-STRONG", "0xAbC", price_change=0.01, netflow=9_000),
        _row("FRESH-WEAK", "0xdef", price_change=0.01, netflow=100),
        *_five_rows()[1:],
    )
    selected = select_cohort(_body(*rows), {("BASE", "0xabc"): 1})
    assert selected[0].token_symbol == "FRESH-WEAK"
    assert selected[0].prior_selection_count == 0

    equally_fresh = select_cohort(_body(*rows), {})
    assert equally_fresh[0].token_symbol == "USED-STRONG"


def test_distribution_is_reserved_before_neutral_and_solana_case_is_preserved():
    rows = (
        *_five_rows()[:3],
        _row("ONLY-NEGATIVE", "CaseSensitive", chain="solana", netflow=-1),
        _row("POSITIVE-NEUTRAL", "casesensitive", chain="solana", netflow=1),
    )
    selected = select_cohort(_body(*rows), {("solana", "CaseSensitive"): 2})
    assert selected[3].token_symbol == "POSITIVE-NEUTRAL"
    assert selected[4].token_symbol == "ONLY-NEGATIVE"
    assert selected[4].prior_selection_count == 2


@pytest.mark.parametrize(
    "mutation",
    (
        lambda row: row.update(price_usd=0),
        lambda row: row.update(price_change=float("nan")),
        lambda row: row.update(price_change=20.01),
        lambda row: row.update(volume=0),
        lambda row: row.update(liquidity=249_999),
        lambda row: row.update(market_cap_usd=999_999),
        lambda row: row.update(token_age_days=2.99),
        lambda row: row.update(netflow=float("inf")),
        lambda row: row.update(chain="polygon"),
        lambda row: row.update(chain="BASE"),
    ),
)
def test_ineligible_rows_are_never_selected(mutation):
    bad = _row("BAD", "0xbad", price_change=0.01, netflow=1_000_000)
    mutation(bad)
    selected = select_cohort(_body(bad, *_five_rows()), {})
    assert "BAD" not in {item.token_symbol for item in selected}


def test_missing_stratum_and_duplicate_identity_fail_closed():
    missing = tuple(row for row in _five_rows() if row["token_symbol"] != "MIDDLE")
    with pytest.raises(CohortSelectionError, match="middle_accumulation"):
        select_cohort(_body(*missing), {})

    duplicate = copy.deepcopy(_five_rows()[0])
    duplicate["token_symbol"] = "DUPLICATE"
    duplicate["token_address"] = "0X01"
    with pytest.raises(CohortSelectionError, match="duplicate token identities"):
        select_cohort(_body(*_five_rows(), duplicate), {})


@pytest.mark.parametrize(
    "prior_counts",
    (
        [],
        {("base",): 1},
        {("base", "0x01"): -1},
        {("base", "0x01"): True},
        {("base", "0x01"): 1, ("BASE", "0X01"): 2},
    ),
)
def test_invalid_prior_count_ledger_is_rejected(prior_counts):
    with pytest.raises(CohortSelectionError, match="prior_counts"):
        select_cohort(_body(*_five_rows()), prior_counts)
