from __future__ import annotations

import copy
import hashlib
import itertools
from datetime import date, datetime, timedelta, timezone

import pytest

from programs.nansen_theory_portfolio.design import (
    CANDIDATES,
    FLOW_FIELDS,
    PLANNED_SLOTS,
    DesignError,
    VETO_PREDICATES,
    _candidate_decision,
    _candidate_rank_key,
    _kleene_and,
    candidate_contract,
    dex_payload,
    execution_outcome,
    flow_payload,
    ohlcv_payload,
    predicate_values,
    score_candidates,
    validate_dex,
    validate_ohlcv,
    validate_screener,
    wbs_payload,
)


def _screener_row(
    chain: str = "ethereum",
    address: str = "0xabc",
    netflow: float | None = 10.0,
) -> dict[str, object]:
    return {
        "chain": chain,
        "token_address": address,
        "token_symbol": "TOKEN",
        "price_usd": 1.0,
        "price_change": 0.01,
        "market_cap_usd": 2_000_000.0,
        "liquidity": 500_000.0,
        "volume": 100_000.0,
        "netflow": netflow,
        "token_age_days": 30,
    }


def _event(**updates: object) -> dict[str, object]:
    event: dict[str, object] = {
        "event_id": "event",
        "status": "selected",
        "anchor": "2025-05-18",
        "chain": "ethereum",
        "token_address": "0xabc",
        "token_symbol": "TOKEN",
        "virtual_notional_usd": 50.0,
        "price_change": 0.01,
        "netflow_to_market_cap": 0.01,
    }
    event.update(updates)
    return event


def _ohlcv_body(event: dict[str, object]) -> dict[str, object]:
    start = datetime(2025, 5, 19, tzinfo=timezone.utc)
    data = []
    for index in range(52):
        price = 100.0 + index
        data.append(
            {
                "interval_start": (
                    start + timedelta(minutes=5 * index)
                ).isoformat().replace("+00:00", "Z"),
                "open": price,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
                "volume": 0.0,
            }
        )
    return {
        "chain": event["chain"],
        "token_address": event["token_address"],
        "timeframe": "5m",
        "truncated": False,
        "data": data,
    }


def _dex_body(direction: str, **row_updates: object) -> dict[str, object]:
    timestamp = (
        "2025-05-19T00:05:00Z"
        if direction == "BUY"
        else "2025-05-19T04:05:00Z"
    )
    row: dict[str, object] = {
        "action": direction,
        "block_timestamp": timestamp,
        "transaction_hash": f"{direction.lower()}-transaction",
        "token_amount": 1.0,
        "estimated_swap_price_usd": 2.0,
        "estimated_value_usd": 2.0,
    }
    row.update(row_updates)
    return {
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
        "data": [row],
    }


def _complete_feature() -> dict[str, object]:
    flow = {field: 1.0 for field in FLOW_FIELDS}
    flow["exchange_net_flow_usd"] = -1.0
    flow["warnings_present"] = False
    return {
        "buy": {"available": True, "address_count": 3, "volume_usd": 30.0},
        "sell": {"available": True, "address_count": 2, "volume_usd": 20.0},
        "flow": flow,
    }


def test_calibration_ids_are_exactly_sealed_from_pre_token_identity():
    calibration_ids = [
        slot.event_id for slot in PLANNED_SLOTS if slot.execution_calibration
    ]
    assert len(calibration_ids) == len(set(calibration_ids)) == 65
    assert hashlib.sha256("\n".join(calibration_ids).encode()).hexdigest() == (
        "3b3416daea6c0a61ec4a66bcb400d7faebe0d65b8a1c7ad05d62b35d853f4714"
    )

    for anchor in {slot.anchor for slot in PLANNED_SLOTS}:
        anchor_slots = [slot for slot in PLANNED_SLOTS if slot.anchor == anchor]
        expected = min(
            anchor_slots,
            key=lambda slot: hashlib.sha256(
                f"{slot.anchor.isoformat()}|{slot.chain}|{slot.stratum}".encode()
            ).hexdigest(),
        )
        selected = [slot for slot in anchor_slots if slot.execution_calibration]
        assert selected == [expected]


def test_screener_normalizes_bsc_with_provenance_and_native_downstream_payloads():
    body = {
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
        "data": [_screener_row(chain="bsc")],
    }
    [event] = validate_screener(body, date(2025, 5, 18))
    assert event["chain"] == "bnb"
    assert event["chain_provenance"] == {
        "raw": "bsc",
        "normalized": "bnb",
        "normalization": "bsc_to_bnb",
    }

    payloads = [
        flow_payload(event),
        wbs_payload(event, "BUY"),
        wbs_payload(event, "SELL"),
        ohlcv_payload(event),
        dex_payload(event, "BUY"),
        dex_payload(event, "SELL"),
    ]
    assert {payload["chain"] for payload in payloads} == {"bnb"}


def test_screener_rejects_response_that_is_not_raw_netflow_descending():
    body = {
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
        "data": [
            _screener_row(address="0x01", netflow=1.0),
            _screener_row(address="0x02", netflow=2.0),
        ],
    }
    with pytest.raises(DesignError, match="raw-netflow descending"):
        validate_screener(body, date(2025, 5, 18))

    body["data"] = [
        _screener_row(address="0x01", netflow=None),
        _screener_row(address="0x02", netflow=1.0),
    ]
    with pytest.raises(DesignError, match="raw-netflow descending"):
        validate_screener(body, date(2025, 5, 18))


def test_ohlcv_requires_literal_false_truncation_and_finite_nonnegative_volume():
    event = _event()
    valid = _ohlcv_body(event)
    assert validate_ohlcv(valid, event)["available"] is True

    missing_truncation = copy.deepcopy(valid)
    del missing_truncation["truncated"]
    with pytest.raises(DesignError, match="truncation"):
        validate_ohlcv(missing_truncation, event)

    falsey_non_boolean = copy.deepcopy(valid)
    falsey_non_boolean["truncated"] = 0
    with pytest.raises(DesignError, match="truncation"):
        validate_ohlcv(falsey_non_boolean, event)

    missing_volume = copy.deepcopy(valid)
    del missing_volume["data"][0]["volume"]
    with pytest.raises(DesignError, match="volume"):
        validate_ohlcv(missing_volume, event)

    nonfinite_volume = copy.deepcopy(valid)
    nonfinite_volume["data"][0]["volume"] = float("nan")
    with pytest.raises(DesignError, match="volume"):
        validate_ohlcv(nonfinite_volume, event)

    negative_volume = copy.deepcopy(valid)
    negative_volume["data"][0]["volume"] = -1.0
    with pytest.raises(DesignError, match="volume"):
        validate_ohlcv(negative_volume, event)


def test_dex_value_consistency_and_proportional_partial_trade_math():
    event = _event()
    inconsistent = _dex_body(
        "BUY",
        token_amount=1.0,
        estimated_swap_price_usd=1.0,
        estimated_value_usd=100.0,
    )
    with pytest.raises(DesignError, match="inconsistent"):
        validate_dex(inconsistent, event, "BUY")

    buys = validate_dex(
        _dex_body(
            "BUY",
            token_amount=40.0,
            estimated_swap_price_usd=2.49,
            estimated_value_usd=100.0,
        ),
        event,
        "BUY",
    )
    sells = validate_dex(
        _dex_body(
            "SELL",
            token_amount=20.0,
            estimated_swap_price_usd=3.0,
            estimated_value_usd=60.5,
        ),
        event,
        "SELL",
    )
    outcome = execution_outcome(event, buys, sells)
    assert outcome["status"] == "filled"
    assert outcome["entry_fill_ratio"] == 1.0
    assert outcome["exit_fill_ratio"] == 1.0
    assert outcome["filled_token_amount"] == 20.0
    assert outcome["exit_proceeds_usd"] == 60.5


def test_candidate_contract_defines_every_primitive_and_tri_state_dependency():
    contract = candidate_contract()
    predicate_names = {
        predicate
        for candidate in contract["candidates"]
        for predicate in candidate["predicates"]
    } | set(contract["veto"]["predicates"])
    assert predicate_names == set(contract["primitives"])
    assert set(contract["tri_state_semantics"]) == {"true", "false", "unavailable"}
    assert contract["composition"]["evaluation_order"] == (
        "candidate_then_veto_if_candidate_true"
    )
    assert contract["ranking"]["lexicographic_fields"][3:5] == [
        "token_equal_stress_mean",
        "token_equal_base_mean",
    ]
    for primitive in contract["primitives"].values():
        assert primitive["sources"]
        assert primitive["availability"]
        assert primitive["comparison"]

    assert "fresh_latest_asof_positive" in contract["primitives"]
    assert "fresh_positive" not in contract["primitives"]
    fresh = contract["primitives"]["fresh_latest_asof_positive"]
    assert "on or before date_to" in fresh["as_of_semantics"]
    c09 = next(candidate for candidate in CANDIDATES if candidate["id"].startswith("c09-"))
    assert "fresh_latest_asof_positive" in c09["predicates"]

    values = predicate_values(_event(), _complete_feature())
    assert values["fresh_latest_asof_positive"] is True
    assert "fresh_positive" not in values
    warning_feature = _complete_feature()
    warning_feature["flow"]["warnings_present"] = True
    warning_values = predicate_values(_event(), warning_feature)
    assert warning_values["exchange_outflow"] is None
    assert warning_values["fresh_latest_asof_positive"] is None
    missing_warning_feature = _complete_feature()
    del missing_warning_feature["flow"]["warnings_present"]
    assert predicate_values(_event(), missing_warning_feature)["exchange_outflow"] is None
    for invalid_warning_state in (None, 0, "false"):
        nonboolean_warning_feature = _complete_feature()
        nonboolean_warning_feature["flow"]["warnings_present"] = invalid_warning_state
        assert (
            predicate_values(_event(), nonboolean_warning_feature)["exchange_outflow"]
            is None
        )
    missing_values = predicate_values({}, {})
    assert all(value is None for value in missing_values.values())


def test_candidate_composition_uses_strong_kleene_and_gates_the_veto():
    states = (False, None, True)
    for combination in itertools.product(states, repeat=3):
        expected = (
            False
            if False in combination
            else True
            if all(value is True for value in combination)
            else None
        )
        assert _kleene_and(combination) is expected

    base = {name: None for name in VETO_PREDICATES}
    assert _candidate_decision({**base, "a": False, "b": None}, ("a", "b")) == (
        "abstain"
    )
    assert _candidate_decision(
        {
            **base,
            "a": True,
            "b": True,
            VETO_PREDICATES[0]: False,
        },
        ("a", "b"),
    ) == "long"
    assert _candidate_decision(
        {**base, "a": True, "b": True}, ("a", "b")
    ) == "unavailable"
    assert _candidate_decision(
        {
            **{name: True for name in VETO_PREDICATES},
            "a": True,
            "b": True,
        },
        ("a", "b"),
    ) == "abstain"


def test_candidate_rank_uses_numeric_stress_before_conflicting_base_mean():
    common = {
        "event_base_median": 0.01,
        "positive_calendar_blocks": 3,
        "max_token_share": 0.10,
        "max_week_share": 0.10,
        "max_chain_share": 0.25,
        "scored_signals": 30,
    }
    higher_stress = {
        **common,
        "token_equal_stress_mean": 0.20,
        "token_equal_base_mean": 0.05,
    }
    higher_base = {
        **common,
        "token_equal_stress_mean": 0.10,
        "token_equal_base_mean": 0.50,
    }
    assert _candidate_rank_key(higher_stress) > _candidate_rank_key(higher_base)


def test_candidate_scoring_reports_planned_selected_and_concentration_denominators():
    feature = _complete_feature()
    records = [
        (
            _event(event_id="e1", token_address="0xa"),
            feature,
            {"available": True, "base_return": 0.10, "stress_return": 0.05},
        ),
        (
            _event(event_id="e2", token_address="0xa"),
            feature,
            {"available": True, "base_return": 0.20, "stress_return": 0.10},
        ),
        (
            _event(
                event_id="e3",
                anchor="2025-05-25",
                chain="solana",
                token_address="token-b",
            ),
            feature,
            {"available": True, "base_return": -0.05, "stress_return": -0.10},
        ),
        (
            _event(
                event_id="e4",
                anchor="2025-06-01",
                chain="base",
                token_address="0xc",
                price_change=-0.01,
            ),
            {},
            {"available": True, "base_return": 0.30, "stress_return": 0.20},
        ),
        (
            {
                "event_id": "e5",
                "status": "unavailable",
                "anchor": "2025-06-08",
                "chain": "bnb",
            },
            {},
            {},
        ),
    ]

    result = score_candidates(records)
    score = next(
        item
        for item in result["scores"]
        if item["candidate_id"] == "c11-screener-accumulation-benchmark"
    )
    assert result["planned_opportunities"] == score["planned_opportunities"] == 5
    assert result["selected_opportunities"] == score["selected_opportunities"] == 4
    assert result["selection_coverage"] == score["selection_coverage"] == 0.8
    assert score["unavailable_decisions"] == 1
    assert score["decision_availability"] == 0.75
    assert score["common_outcome_coverage"] == 1.0
    assert score["signals"] == score["scored_signals"] == 3
    assert score["physical_tokens"] == score["weeks"] == score["chains"] == 2
    assert score["max_token_share"] == pytest.approx(2 / 3)
    assert score["max_week_share"] == pytest.approx(2 / 3)
    assert score["max_chain_share"] == pytest.approx(2 / 3)
    assert score["missing_decision_event_mean_break_even_return"] == pytest.approx(
        -0.25
    )
    assert "missing_signal_break_even_return" not in score
