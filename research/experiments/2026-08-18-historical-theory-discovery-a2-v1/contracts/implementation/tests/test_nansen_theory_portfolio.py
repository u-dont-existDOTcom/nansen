from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from programs.nansen_theory_portfolio.budget import HistoricalPricingGuard
from programs.nansen_theory_portfolio.design import (
    ANCHORS,
    CANDIDATES,
    FLOW_FIELDS,
    PLANNED_SLOTS,
    PROGRAM_A_MAX_CALLS,
    PROGRAM_A_MAX_CREDITS,
    SCREENER_ENDPOINT,
    WBS_ENDPOINT,
    DesignError,
    candidate_contract,
    execution_outcome,
    ohlcv_payload,
    predicate_values,
    screener_payload,
    select_anchor_events,
    validate_dex,
    validate_ohlcv,
    validate_screener,
    validate_wbs,
)
from programs.nansen_theory_portfolio.runner import _epoch_limits
from src.nansen_signal_lab.budget import BudgetError
from src.nansen_signal_lab.client import NansenEvidenceResponse
from src.nansen_signal_lab.prospective_runner import PilotError, _nansen_call


def _response(
    body,
    *,
    cost,
    used,
    remaining,
    status=200,
    started="2026-08-18T18:00:00Z",
    retrieved="2026-08-18T18:00:01Z",
):
    headers = {}
    if cost is not None:
        headers["X-Nansen-Credits-Cost"] = str(cost)
    if used is not None:
        headers["X-Nansen-Credits-Used"] = str(used)
    if remaining is not None:
        headers["X-Nansen-Credits-Remaining"] = str(remaining)
    import json

    raw = json.dumps(body, separators=(",", ":")).encode()
    return NansenEvidenceResponse(
        body=body,
        body_parse_status="json_object",
        raw_body=raw,
        status_code=status,
        request_started_at=started,
        response_retrieved_at=retrieved,
        response_headers=headers,
        request_id="request-id",
        credit_cost=cost,
        credit_used=used,
        credit_remaining=remaining,
        credit_header_errors=(),
    )


class _FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def request_evidence(self, method, endpoint, payload, *, caller_request_id):
        self.calls.append((method, endpoint, payload, caller_request_id))
        return self.responses.pop(0)


def _call(root, guard, client, logical_id, endpoint, payload, expected):
    return _nansen_call(
        root=root,
        guard=guard,
        nansen=client,
        logical_request_id=logical_id,
        method="GET" if endpoint == "account" else "POST",
        endpoint=endpoint,
        payload=payload,
        expected_credits=expected,
        clock=lambda: datetime(2026, 8, 18, 18, 0, 2, tzinfo=timezone.utc),
        sleep=lambda _: None,
        account_baseline_version="account-baseline-v2" if endpoint == "account" else None,
        openapi_sha256=(
            "d01160d54c375f022d839d4c0619b51928bfcc652a5308fdd8c927a1e53e7548"
            if endpoint == "account"
            else None
        ),
        account_minimum_remaining=0,
        allow_retry=False,
    )


def test_schedule_and_exhaustive_request_plan_match_frozen_ceiling():
    assert len(ANCHORS) == 65
    assert ANCHORS[0] == date(2025, 5, 18)
    assert ANCHORS[-1] == date(2026, 8, 9)
    assert len(PLANNED_SLOTS) == 400
    assert sum(slot.execution_calibration for slot in PLANNED_SLOTS) == 65
    assert {
        chain: sum(slot.chain == chain for slot in PLANNED_SLOTS)
        for chain in ("ethereum", "solana", "base", "bnb")
    } == {"ethereum": 100, "solana": 100, "base": 100, "bnb": 100}
    assert sum(_epoch_limits(index)[0] for index in range(65)) == PROGRAM_A_MAX_CALLS == 1860
    assert sum(_epoch_limits(index)[1] for index in range(65)) == PROGRAM_A_MAX_CREDITS == 7375
    assert [_epoch_limits(index) for index in range(10)] == [(32, 127)] * 10
    assert [_epoch_limits(index) for index in range(10, 65)] == [(28, 111)] * 55


def test_screener_payload_is_one_page_prefix_without_current_sector_filter():
    payload = screener_payload(date(2025, 5, 18))
    assert payload["pagination"] == {"page": 1, "per_page": 1000}
    assert payload["order_by"] == [{"field": "netflow", "direction": "DESC"}]
    assert "exclude_sectors" not in payload
    assert payload["apply_blacklist_filter"] is False


def _screener_row(chain, address, netflow, price_change=0.01):
    return {
        "chain": chain,
        "token_address": address,
        "token_symbol": address.upper(),
        "price_usd": 1.0,
        "price_change": price_change,
        "market_cap_usd": 2_000_000.0,
        "liquidity": 500_000.0,
        "volume": 100_000.0,
        "netflow": netflow,
        "token_age_days": 30,
    }


def test_nonfinal_screener_is_explicitly_accepted_as_page_one_prefix():
    body = {
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": False},
        "data": [_screener_row("ethereum", "0x01", 10.0)],
    }
    rows = validate_screener(body, date(2025, 5, 18))
    assert [row["token_address"] for row in rows] == ["0x01"]


def test_screener_semantics_failure_and_row_ceiling_are_rejected():
    body = {
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
        "data": [_screener_row("ethereum", "0x01", 10.0, price_change=20.01)],
    }
    with pytest.raises(DesignError, match="magnitude 20"):
        validate_screener(body, date(2025, 5, 18))
    body["data"] = [_screener_row("ethereum", f"0x{index:04x}", 10.0) for index in range(1001)]
    with pytest.raises(DesignError, match="pagination"):
        validate_screener(body, date(2025, 5, 18))


def test_prefix_relative_selection_is_deterministic_and_never_replaces_collision():
    rows = [
        {
            **_screener_row("ethereum", f"0x{index:02x}", float(index - 2)),
            "anchor": "2025-05-18",
            "token_address": f"0x{index:02x}",
            "liquidity_usd": 500_000.0,
            "volume_usd": 100_000.0,
            "netflow_usd": float(index - 2),
            "netflow_to_market_cap": float(index - 2) / 2_000_000,
        }
        for index in range(4)
    ]
    slots = tuple(slot for slot in PLANNED_SLOTS if slot.anchor_index == 0 and slot.chain == "ethereum")
    selected = select_anchor_events(slots, rows)
    identities = [item.get("token_address") for item in selected if item["status"] == "selected"]
    assert identities == list(dict.fromkeys(identities))


def test_wbs_page_one_must_be_final_and_unique():
    nonfinal = {
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": False},
        "data": [],
    }
    assert validate_wbs(nonfinal, "BUY") == {
        "available": False,
        "reason": "nonfinal_page_one",
    }
    duplicate = {
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
        "data": [
            {
                "address": "0xA",
                "is_smart_money": True,
                "bought_volume_usd": 10.0,
                "bought_token_volume": 1.0,
            },
            {
                "address": "0xa",
                "is_smart_money": True,
                "bought_volume_usd": 5.0,
                "bought_token_volume": 0.5,
            },
        ],
    }
    with pytest.raises(DesignError, match="duplicate"):
        validate_wbs(duplicate, "BUY")


def _event():
    return {
        "event_id": "event",
        "anchor": "2025-05-18",
        "chain": "ethereum",
        "token_address": "0xabc",
        "token_symbol": "ABC",
        "virtual_notional_usd": 100.0,
        "price_change": -0.01,
        "netflow_to_market_cap": 0.01,
    }


def test_ohlcv_grid_and_proxy_costs_are_exact():
    event = _event()
    start = datetime(2025, 5, 19, tzinfo=timezone.utc)
    data = []
    for index in range(52):
        price = 100.0 + index
        data.append(
            {
                "interval_start": (start + timedelta(minutes=5 * index)).isoformat().replace("+00:00", "Z"),
                "open": price,
                "high": price + 1,
                "low": price - 1,
                "close": price,
                "volume": 0,
                "market_cap": {},
            }
        )
    body = {
        "chain": "ethereum",
        "token_address": "0xABC",
        "timeframe": "5m",
        "truncated": False,
        "data": data,
    }
    outcome = validate_ohlcv(body, event)
    assert outcome["entry_price_usd"] == 102.0
    assert outcome["exit_price_usd"] == 150.0
    assert outcome["stress_return"] < outcome["base_return"] < outcome["gross_return"]
    assert ohlcv_payload(event)["date"] == {
        "from": "2025-05-19T00:00:00Z",
        "to": "2025-05-19T04:15:00Z",
    }


def test_dex_page_one_must_be_final_and_partial_exit_is_preserved():
    event = _event()
    with pytest.raises(DesignError, match="non-final"):
        validate_dex(
            {
                "pagination": {"page": 1, "per_page": 1000, "is_last_page": False},
                "data": [],
            },
            event,
            "BUY",
        )
    result = execution_outcome(
        event,
        [{"value_usd": 100.0, "price_usd": 2.0, "token_amount": 50.0}],
        [{"value_usd": 40.0, "price_usd": 4.0, "token_amount": 10.0}],
    )
    assert result["status"] == "partial"
    assert result["entry_fill_ratio"] == 1.0
    assert result["exit_fill_ratio"] == 0.2
    assert result["sold_token_amount"] == 10.0


def test_candidate_contract_is_finite_satisfiable_and_names_distinct_veto():
    contract = candidate_contract()
    assert contract["veto"] == {
        "id": "historical-selling-pressure-v1",
        "not_equivalent_to": "frozen-four-hour-distribution-veto",
        "predicates": ["seller_breadth", "seller_volume", "price_nonpositive"],
    }
    assert len(contract["candidates"]) == len(CANDIDATES) == 12
    feature = {
        "buy": {"available": True, "address_count": 3, "volume_usd": 30.0},
        "sell": {"available": True, "address_count": 2, "volume_usd": 20.0},
        "flow": {field: 1.0 for field in FLOW_FIELDS},
    }
    feature["flow"]["exchange_net_flow_usd"] = -1.0
    feature["flow"]["warnings_present"] = False
    values = predicate_values(_event(), feature)
    required = {
        predicate
        for candidate in CANDIDATES
        for predicate in candidate.get("predicates", [])
    } | set(contract["veto"]["predicates"])
    assert required <= set(values)
    assert all(values[predicate] is not None for predicate in required)


def test_missing_quote_fallback_preserves_raw_and_derives_exact_five(tmp_path):
    root = tmp_path / "epoch"
    guard = HistoricalPricingGuard(root, 3, 5)
    client = _FakeClient(
        _response({"plan": "free", "credits_remaining": 100}, cost=0, used=0, remaining=100),
        _response({"data": [], "pagination": {}}, cost=None, used=5, remaining=95),
    )
    _call(root, guard, client, "account", "account", None, 0)
    _call(root, guard, client, "screen", SCREENER_ENDPOINT, {"page": 1}, 5)
    totals = guard.replay()
    assert totals.credits == 5
    assert totals.provider_remaining == 95
    screen = next(entry for entry in totals.entries if entry.logical_request_id == "screen")
    assert screen.credit_cost == 5
    derivation = root / "derived/pricing" / f"{screen.reservation_id}-attempt-1.json"
    assert derivation.is_file()
    metadata = (
        root
        / "raw/nansen"
        / screen.reservation_id
        / "attempt-1-response-metadata.json"
    )
    import json

    assert json.loads(metadata.read_text())["credit_cost"] is None


@pytest.mark.parametrize(
    ("endpoint", "used", "remaining"),
    [
        (SCREENER_ENDPOINT, 4, 96),
        (SCREENER_ENDPOINT, 5, 94),
        ("tgm/token-ohlcv", 5, 95),
    ],
)
def test_missing_quote_fallback_rejects_any_incomplete_proof(
    tmp_path, endpoint, used, remaining
):
    root = tmp_path / "epoch"
    guard = HistoricalPricingGuard(root, 3, 5)
    client = _FakeClient(
        _response({"plan": "free", "credits_remaining": 100}, cost=0, used=0, remaining=100),
        _response({"data": []}, cost=None, used=used, remaining=remaining),
    )
    _call(root, guard, client, "account", "account", None, 0)
    with pytest.raises((PilotError, BudgetError)):
        _call(root, guard, client, "paid", endpoint, {"page": 1}, 5)
    assert guard.replay().halted_reason is not None
