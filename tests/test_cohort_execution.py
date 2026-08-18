from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.nansen_signal_lab.cohort_execution import (
    CohortExecutionError,
    build_entry_fill,
    build_exit_fill,
    dex_payload,
    execution_windows,
    ohlcv_payload,
    score_counterfactual,
    validate_ohlcv,
)


CUTOFF = datetime(2026, 8, 24, 12, 5, tzinfo=timezone.utc)
CANDIDATE = {"chain": "base", "token_address": "0xAbC"}


def _trade(side: str, at: datetime, tx: str, *, amount=10.0, price=10.0):
    return {
        "block_timestamp": at.isoformat().replace("+00:00", "Z"),
        "transaction_hash": tx,
        "trader_address": "0xtrader",
        "action": side,
        "token_address": "0xabc",
        "token_name": "TOKEN",
        "token_amount": amount,
        "traded_token_address": "0xusd",
        "traded_token_name": "USD",
        "traded_token_amount": amount * price,
        "estimated_swap_price_usd": price,
        "estimated_value_usd": amount * price,
    }


def _page(*rows, page=1, final=True):
    return {
        "data": list(rows),
        "pagination": {"page": page, "per_page": 1000, "is_last_page": final},
    }


def _ohlcv(start, end):
    rows = []
    cursor = start
    index = 0
    while cursor <= end:
        price = 10 + index * 0.01
        rows.append({
            "interval_start": cursor.isoformat().replace("+00:00", "Z"),
            "open": price,
            "high": price + 0.1,
            "low": price - 0.1,
            "close": price + 0.05,
            "volume": 100,
            "volume_usd": 1000,
            "market_cap": {"open": 1000, "high": 1010, "low": 990, "close": 1005},
        })
        cursor += timedelta(minutes=5)
        index += 1
    return {
        "chain": "base",
        "token_address": "0xABC",
        "timeframe": "5m",
        "truncated": False,
        "data": rows,
    }


def test_windows_and_payloads_are_half_open_and_exact():
    windows = execution_windows(CUTOFF)
    payload = dex_payload(
        CANDIDATE,
        side="BUY",
        start=windows["entry_start"],
        end=windows["entry_end"],
        page=1,
    )
    assert payload["date"]["to"] == "2026-08-24T12:19:59.999999Z"
    assert ohlcv_payload(
        CANDIDATE, start=windows["ohlcv_start"], end=windows["ohlcv_end"]
    )["timeframe"] == "5m"


def test_always_observed_fill_and_object_market_cap_score():
    windows = execution_windows(CUTOFF)
    entry = build_entry_fill(
        [_page(_trade("BUY", windows["entry_start"], "0x1"))],
        candidate=CANDIDATE,
        notional_usd=100,
        start=windows["entry_start"],
        end=windows["entry_end"],
    )
    assert entry is not None and entry.filled_token_amount == 10
    exit_fill = build_exit_fill(
        [_page(_trade("SELL", windows["exit_start"], "0x2", price=11))],
        candidate=CANDIDATE,
        token_amount=entry.filled_token_amount,
        start=windows["exit_start"],
        end=windows["exit_end"],
    )
    candles = validate_ohlcv(
        _ohlcv(windows["ohlcv_start"], windows["ohlcv_end"]),
        candidate=CANDIDATE,
        start=windows["ohlcv_start"],
        end=windows["ohlcv_end"],
        retrieved_at=windows["earliest_settlement"],
    )
    score = score_counterfactual(
        entry_fill=entry, exit_fill=exit_fill, ohlcv=candles, notional_usd=100
    )
    assert score["status"] == "SCORED"
    assert score["gross_return"] == pytest.approx(0.10)
    assert score["base_return_100bps"] == pytest.approx(1.1 * 0.99**2 - 1)
    assert score["stress_return_250bps"] == pytest.approx(1.1 * 0.975**2 - 1)


def test_insufficient_entry_liquidity_is_unfilled_not_fabricated():
    windows = execution_windows(CUTOFF)
    fill = build_entry_fill(
        [_page(_trade("BUY", windows["entry_start"], "0x1", amount=1, price=10))],
        candidate=CANDIDATE,
        notional_usd=100,
        start=windows["entry_start"],
        end=windows["entry_end"],
    )
    assert fill.is_complete is False
    assert fill.fill_ratio == pytest.approx(0.10)
    score = score_counterfactual(
        entry_fill=fill,
        exit_fill=None,
        ohlcv=validate_ohlcv(
            _ohlcv(windows["ohlcv_start"], windows["ohlcv_end"]),
            candidate=CANDIDATE,
            start=windows["ohlcv_start"],
            end=windows["ohlcv_end"],
            retrieved_at=windows["earliest_settlement"],
        ),
        notional_usd=100,
    )
    assert score["status"] == "UNFILLED_ENTRY"
    assert score["entry_fill"]["fill_ratio"] == pytest.approx(0.10)


def test_wrong_identity_and_ambiguous_multi_leg_rows_fail_closed():
    windows = execution_windows(CUTOFF)
    wrong = _trade("BUY", windows["entry_start"], "0x1")
    wrong["token_address"] = "0xwrong"
    with pytest.raises(CohortExecutionError, match="token differs"):
        build_entry_fill(
            [_page(wrong)], candidate=CANDIDATE, notional_usd=100,
            start=windows["entry_start"], end=windows["entry_end"]
        )
    duplicate = _trade("BUY", windows["entry_start"], "0x1")
    with pytest.raises(CohortExecutionError, match="ambiguous"):
        build_entry_fill(
            [_page(duplicate, duplicate)], candidate=CANDIDATE, notional_usd=100,
            start=windows["entry_start"], end=windows["entry_end"]
        )


def test_ohlcv_requires_and_validates_exactly_one_inclusive_end_candle():
    windows = execution_windows(CUTOFF)
    body = _ohlcv(windows["ohlcv_start"], windows["ohlcv_end"])
    assert body["data"][-1]["interval_start"] == windows["ohlcv_end"].isoformat().replace(
        "+00:00", "Z"
    )
    missing = {**body, "data": body["data"][:-1]}
    with pytest.raises(CohortExecutionError, match="exact contiguous grid"):
        validate_ohlcv(
            missing,
            candidate=CANDIDATE,
            start=windows["ohlcv_start"],
            end=windows["ohlcv_end"],
            retrieved_at=windows["earliest_settlement"],
        )
    malformed_duplicate = {**body, "data": [*body["data"], {
        "interval_start": windows["ohlcv_end"].isoformat().replace("+00:00", "Z")
    }]}
    with pytest.raises(CohortExecutionError, match="OHLCV open"):
        validate_ohlcv(
            malformed_duplicate,
            candidate=CANDIDATE,
            start=windows["ohlcv_start"],
            end=windows["ohlcv_end"],
            retrieved_at=windows["earliest_settlement"],
        )
