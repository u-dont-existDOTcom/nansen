from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.nansen_signal_lab.prospective_comparators import ComparatorDecision
from src.nansen_signal_lab.prospective_snapshot import Candidate


UTC = timezone.utc
ENTRY_START = datetime(2026, 8, 17, 10, 5, tzinfo=UTC)
ENTRY_END = datetime(2026, 8, 17, 10, 10, tzinfo=UTC)


def _candidate() -> Candidate:
    return Candidate("solana", "So111", "SOL", 1_000_000.0, {"source": "frozen"})


def _trade(
    second: int,
    tx: str,
    *,
    action: str = "BUY",
    amount: float = 1.0,
    price: float = 100.0,
    value: float | None = None,
    timestamp: str | None = None,
) -> dict:
    return {
        "block_timestamp": timestamp or f"2026-08-17T10:05:{second:02d}Z",
        "transaction_hash": tx,
        "action": action,
        "token_amount": amount,
        "estimated_swap_price_usd": price,
        "estimated_value_usd": amount * price if value is None else value,
    }


def _pages(rows, *, is_last=True, second_rows=None, second_last=True):
    result = [
        {
            "data": rows,
            "pagination": {"page": 1, "per_page": 1000, "is_last_page": is_last},
        }
    ]
    if second_rows is not None:
        result.append(
            {
                "data": second_rows,
                "pagination": {
                    "page": 2,
                    "per_page": 1000,
                    "is_last_page": second_last,
                },
            }
        )
    return result


def _candle(at: datetime, **changes) -> dict:
    row = {
        "interval_start": at.isoformat().replace("+00:00", "Z"),
        "open": 100.0,
        "high": 103.0,
        "low": 99.0,
        "close": 102.0,
        "volume": 1000.0,
        "market_cap": 1_000_000.0,
    }
    row.update(changes)
    return row


def _decision(
    theory_id: str,
    *,
    action: str | None,
    availability: str = "AVAILABLE",
    applicable: bool,
    variant: str = "base",
    role: str = "entry",
) -> ComparatorDecision:
    return ComparatorDecision(
        decision_id=f"{theory_id}::{variant}",
        theory_id=theory_id,
        role=role,
        variant=variant,
        action=action,
        availability=availability,
        applicable=applicable,
        veto_theory_id=("veto" if variant == "distribution_veto" else None),
        veto_triggered=(True if variant == "distribution_veto" else None),
        reasons=("fixture",),
    )


def test_exact_dex_and_ohlcv_payloads():
    from src.nansen_signal_lab.prospective_execution import dex_trade_payload, ohlcv_payload

    assert dex_trade_payload(_candidate(), "BUY", ENTRY_START, ENTRY_END, 1) == {
        "chain": "solana",
        "token_address": "So111",
        "only_smart_money": False,
        "date": {"from": "2026-08-17T10:05:00Z", "to": "2026-08-17T10:10:00Z"},
        "pagination": {"page": 1, "per_page": 1000},
        "filters": {"action": "BUY"},
        "order_by": [
            {"field": "block_timestamp", "direction": "ASC"},
            {"field": "transaction_hash", "direction": "ASC"},
        ],
    }
    assert dex_trade_payload(
        _candidate(),
        "SELL",
        datetime(2026, 8, 17, 14, 5, tzinfo=UTC),
        datetime(2026, 8, 17, 14, 10, tzinfo=UTC),
        1,
    )["filters"] == {"action": "SELL"}
    assert ohlcv_payload(
        _candidate(),
        datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
        datetime(2026, 8, 17, 14, 10, tzinfo=UTC),
    ) == {
        "chain": "solana",
        "token_address": "So111",
        "date": {"from": "2026-08-17T10:00:00Z", "to": "2026-08-17T14:10:00Z"},
        "timeframe": "5m",
    }


def test_non_aligned_ohlcv_bounds_and_earliest_settlement():
    from src.nansen_signal_lab.prospective_execution import (
        earliest_settlement_at,
        ohlcv_bounds,
    )

    t0 = datetime(2026, 8, 17, 10, 2, 37, tzinfo=UTC)
    exit_end = datetime(2026, 8, 17, 14, 12, 37, tzinfo=UTC)
    start, exclusive_end = ohlcv_bounds(t0, exit_end)
    assert start == datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    assert exclusive_end == datetime(2026, 8, 17, 14, 15, tzinfo=UTC)
    assert earliest_settlement_at(t0, exit_end) == datetime(2026, 8, 17, 14, 16, tzinfo=UTC)


def test_entry_fill_fractionally_consumes_last_trade():
    from src.nansen_signal_lab.prospective_execution import build_entry_fill

    rows = [
        _trade(1, "a", amount=2.0, price=100.0),
        _trade(2, "b", amount=3.0, price=110.0),
        _trade(3, "c", amount=10.0, price=120.0),
    ]
    fill = build_entry_fill(
        _pages(rows), 1000.0, start=ENTRY_START, end=ENTRY_END
    )
    assert fill is not None
    assert fill.side == "BUY"
    assert fill.notional_usd == pytest.approx(1000.0)
    assert fill.observed_usd == pytest.approx(1000.0)
    assert fill.token_amount == pytest.approx(2 + 3 + 470 / 120)
    assert fill.vwap_usd == pytest.approx(1000 / fill.token_amount)
    assert fill.trade_count == 3


def test_exit_fill_uses_token_target_and_returns_none_for_insufficient_volume():
    from src.nansen_signal_lab.prospective_execution import build_exit_fill

    rows = [
        _trade(1, "a", action="SELL", amount=2, price=90),
        _trade(2, "b", action="SELL", amount=8, price=80),
    ]
    fill = build_exit_fill(
        _pages(rows), 5.0, start=ENTRY_START, end=ENTRY_END
    )
    assert fill is not None
    assert fill.token_amount == pytest.approx(5.0)
    assert fill.observed_usd == pytest.approx(420.0)
    assert fill.vwap_usd == pytest.approx(84.0)
    assert build_exit_fill(
        _pages(rows[:1]), 5.0, start=ENTRY_START, end=ENTRY_END
    ) is None


@pytest.mark.parametrize(
    "pages,match",
    [
        (_pages([_trade(1, "a"), _trade(1, "a")]), "strictly increasing"),
        (_pages([_trade(2, "b"), _trade(1, "a")]), "strictly increasing"),
        (_pages([_trade(1, "a", action="SELL")]), "wrong action"),
        (_pages([_trade(1, "a", timestamp="2026-08-17T10:10:00Z")]), "outside"),
        (_pages([_trade(1, "a", amount=0)]), "positive"),
        (_pages([_trade(1, "a", price=-1)]), "positive"),
        (_pages([_trade(1, "a", value=float("inf"))]), "positive"),
        (_pages([_trade(1, "a", amount=1, price=100, value=101.02)]), "inconsistent"),
        (_pages([_trade(1, "a")], is_last=False), "page 2"),
        (_pages([_trade(1, "a")], is_last=False, second_rows=[_trade(2, "b")], second_last=False), "page 2 must be final"),
    ],
)
def test_invalid_or_incomplete_trade_windows_are_terminal(pages, match):
    from src.nansen_signal_lab.prospective_execution import ExecutionError, build_entry_fill

    with pytest.raises(ExecutionError, match=match):
        build_entry_fill(pages, 50.0, start=ENTRY_START, end=ENTRY_END)


def test_trade_value_tolerance_boundary_is_accepted_and_page_boundary_is_strict():
    from src.nansen_signal_lab.prospective_execution import ExecutionError, build_entry_fill

    accepted = build_entry_fill(
        _pages([_trade(1, "a", amount=1, price=100, value=100 / 0.99)]),
        50,
        start=ENTRY_START,
        end=ENTRY_END,
    )
    assert accepted is not None
    pages = _pages(
        [_trade(2, "same")],
        is_last=False,
        second_rows=[_trade(2, "same")],
    )
    with pytest.raises(ExecutionError, match="strictly increasing"):
        build_entry_fill(pages, 50, start=ENTRY_START, end=ENTRY_END)


def test_complete_but_insufficient_entry_volume_is_unfilled():
    from src.nansen_signal_lab.prospective_execution import build_entry_fill

    assert build_entry_fill(
        _pages([_trade(1, "a", amount=1, price=100)]),
        101,
        start=ENTRY_START,
        end=ENTRY_END,
    ) is None


def test_closed_ohlcv_exact_grid_and_exclusive_provider_boundary():
    from src.nansen_signal_lab.prospective_execution import validate_closed_ohlcv

    start = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    end = datetime(2026, 8, 17, 10, 15, tzinfo=UTC)
    rows = [_candle(start + timedelta(minutes=5 * index)) for index in range(4)]
    admitted = validate_closed_ohlcv(
        {"data": rows, "truncated": False},
        required_start=start,
        required_exit=end,
        retrieved_at=datetime(2026, 8, 17, 10, 16, tzinfo=UTC),
    )
    assert len(admitted) == 3
    assert admitted[-1]["interval_start"] == "2026-08-17T10:10:00Z"


@pytest.mark.parametrize(
    "body,retrieved,match",
    [
        ({"data": [], "truncated": True}, datetime(2026, 8, 17, 10, 16, tzinfo=UTC), "truncated"),
        ({"data": [_candle(datetime(2026, 8, 17, 10, 0, tzinfo=UTC))] * 2, "truncated": False}, datetime(2026, 8, 17, 10, 16, tzinfo=UTC), "strictly increasing"),
        ({"data": [_candle(datetime(2026, 8, 17, 10, 0, tzinfo=UTC)), _candle(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))], "truncated": False}, datetime(2026, 8, 17, 10, 16, tzinfo=UTC), "contiguous"),
        ({"data": [_candle(datetime(2026, 8, 17, 10, 0, tzinfo=UTC), close=0)], "truncated": False}, datetime(2026, 8, 17, 10, 16, tzinfo=UTC), "positive"),
        ({"data": [_candle(datetime(2026, 8, 17, 10, 10, tzinfo=UTC))], "truncated": False}, datetime(2026, 8, 17, 10, 14, tzinfo=UTC), "not closed"),
    ],
)
def test_invalid_ohlcv_is_terminal(body, retrieved, match):
    from src.nansen_signal_lab.prospective_execution import ExecutionError, validate_closed_ohlcv

    start = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    with pytest.raises(ExecutionError, match=match):
        validate_closed_ohlcv(
            body,
            required_start=start,
            required_exit=datetime(2026, 8, 17, 10, 15, tzinfo=UTC),
            retrieved_at=retrieved,
        )


@pytest.mark.parametrize("missing_index", [0, 2])
def test_missing_entry_or_exit_candle_is_terminal(missing_index):
    from src.nansen_signal_lab.prospective_execution import ExecutionError, validate_closed_ohlcv

    start = datetime(2026, 8, 17, 10, 0, tzinfo=UTC)
    rows = [_candle(start + timedelta(minutes=5 * index)) for index in range(3)]
    rows.pop(missing_index)
    with pytest.raises(ExecutionError, match="exact contiguous"):
        validate_closed_ohlcv(
            {"data": rows, "truncated": False},
            required_start=start,
            required_exit=datetime(2026, 8, 17, 10, 15, tzinfo=UTC),
            retrieved_at=datetime(2026, 8, 17, 10, 16, tzinfo=UTC),
        )


def _fills(exit_usd: float):
    from src.nansen_signal_lab.prospective_execution import ObservedFill

    return (
        ObservedFill("BUY", 1000.0, 10.0, 1000.0, 100.0, 1),
        ObservedFill("SELL", exit_usd, 10.0, exit_usd, exit_usd / 10, 1),
    )


def _score(*, pass1="LONG", pass2="LONG", decisions=(), exit_usd=1100.0):
    from src.nansen_signal_lab.prospective_execution import score_decisions

    entry, exit_fill = _fills(exit_usd)
    candles = (
        _candle(datetime(2026, 8, 17, 10, 0, tzinfo=UTC), open=100, close=101),
        _candle(datetime(2026, 8, 17, 10, 5, tzinfo=UTC), open=101, close=105),
    )
    return score_decisions(
        pass1_action=pass1,
        pass2_action=pass2,
        comparator_decisions=tuple(decisions),
        entry_fill=entry,
        exit_fill=exit_fill,
        ohlcv=candles,
        virtual_notional_usd=1000.0,
    )


def test_score_formula_distinct_gpt_actions_and_ohlcv_divergence():
    result = _score(pass1="ABSTAIN", pass2="LONG", exit_usd=1100)
    assert result["pass1"] == {"action": "ABSTAIN", "status": "SCORED", "net_return": 0.0}
    assert result["pass2"]["net_return"] == pytest.approx(0.1)
    assert result["gross_ohlcv_return"] == pytest.approx(0.05)
    assert result["dex_ohlcv_divergence"] == pytest.approx(0.05)


def test_no_applicable_baseline_is_not_tested_not_a_gpt_win():
    result = _score(
        decisions=[_decision("false-base", action="ABSTAIN", applicable=False)]
    )
    assert result["comparators"][0]["status"] == "NOT_APPLICABLE"
    assert result["gpt_beats_frozen_strategies"] == "not_tested"


def test_veto_suppressed_abstain_is_applicable_and_can_be_beaten():
    paired = _decision(
        "paired-base", action="ABSTAIN", applicable=True, variant="distribution_veto"
    )
    result = _score(decisions=[paired])
    assert result["comparators"][0]["net_return"] == 0.0
    assert result["gpt_beats_frozen_strategies"] is True


def test_gpt_cash_beats_losing_long_but_tie_is_not_a_win():
    baseline = _decision("long-base", action="LONG", applicable=True)
    losing = _score(pass2="ABSTAIN", decisions=[baseline], exit_usd=900)
    assert losing["gpt_beats_frozen_strategies"] is True
    tie = _score(pass2="LONG", decisions=[baseline], exit_usd=1100)
    assert tie["gpt_beats_frozen_strategies"] is False


def test_unavailable_applicable_or_any_unavailable_comparison_base_is_unscorable():
    applicable = _decision(
        "paired", action=None, availability="UNAVAILABLE", applicable=True,
        variant="distribution_veto",
    )
    assert _score(decisions=[applicable])["gpt_beats_frozen_strategies"] == "unscorable"

    unknown_base = _decision(
        "unknown", action=None, availability="UNAVAILABLE", applicable=False
    )
    firing = _decision("firing", action="LONG", applicable=True)
    assert _score(decisions=[unknown_base, firing])["gpt_beats_frozen_strategies"] == "unscorable"


def test_unfilled_long_has_no_return_and_cannot_produce_headline_win():
    from src.nansen_signal_lab.prospective_execution import score_decisions

    baseline = _decision("long-base", action="LONG", applicable=True)
    result = score_decisions(
        pass1_action="LONG",
        pass2_action="LONG",
        comparator_decisions=(baseline,),
        entry_fill=None,
        exit_fill=None,
        ohlcv=(),
        virtual_notional_usd=1000,
    )
    assert result["pass2"] == {"action": "LONG", "status": "UNFILLED", "net_return": None}
    assert result["gpt_beats_frozen_strategies"] == "unscorable"
