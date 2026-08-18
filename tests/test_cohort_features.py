from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.nansen_signal_lab.cohort_features import (
    CohortFeatureError,
    build_predecision_features,
    flow_payload,
    h5_decision,
    validate_flow_body,
    validate_wbs_pages,
    wbs_payload,
)


CUTOFF = datetime(2026, 8, 24, 12, 5, tzinfo=timezone.utc)
CANDIDATE = {"chain": "base", "token_address": "0xAbC"}


def _flow_body(*, change: float = 0.01, final=True):
    rows = []
    for index in range(26):
        end = CUTOFF.replace(minute=0) - timedelta(hours=25 - index)
        amount = 100.0 * ((1 + change) ** index)
        rows.append({
            "date": (end - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "bucket_end": end.isoformat().replace("+00:00", "Z"),
            "is_complete": True,
            "price_usd": 1.0,
            "token_amount": amount,
            "value_usd": amount,
            "holders_count": 10 + index,
            "total_inflows_count": 1,
            "total_outflows_count": -1,
        })
    return {
        "data": rows,
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": final},
    }


def _wbs(side: str, *, page=1, final=True, count=2, start=0):
    directional = "bought_volume_usd" if side == "BUY" else "sold_volume_usd"
    return {
        "data": [
            {
                "address": f"0x{start + index + 1:040x}",
                directional: float(100 - start - index),
                "trade_volume_usd": float(100 - start - index),
            }
            for index in range(count)
        ],
        "pagination": {"page": page, "per_page": 1000, "is_last_page": final},
    }


def test_payloads_are_pinned_half_open_and_use_frozen_labels():
    flow = flow_payload(CANDIDATE, CUTOFF, "smart_money")
    assert flow["date"] == {
        "from": "2026-08-23T10:00:00Z",
        "to": "2026-08-24T11:59:59.999999Z",
    }
    wbs = wbs_payload(CANDIDATE, CUTOFF, "BUY", 2)
    assert wbs["filters"]["include_smart_money_labels"] == [
        "Fund", "Smart Trader", "30D Smart Trader"
    ]
    assert wbs["pagination"] == {"page": 2, "per_page": 1000}


def test_flow_admits_exact_trailing_25_rows_and_builds_h5_long():
    smart = validate_flow_body(
        _flow_body(change=0.02), candidate=CANDIDATE, label="smart_money", cutoff=CUTOFF
    )
    exchange = validate_flow_body(
        _flow_body(change=-0.01), candidate=CANDIDATE, label="exchange", cutoff=CUTOFF
    )
    buyers = validate_wbs_pages([_wbs("BUY", count=3)], candidate=CANDIDATE, side="BUY")
    sellers = validate_wbs_pages([_wbs("SELL", count=1)], candidate=CANDIDATE, side="SELL")
    features = build_predecision_features(
        smart_money_rows=smart,
        exchange_rows=exchange,
        buyers=buyers,
        sellers=sellers,
        source_id="fixture",
    )
    decision = h5_decision(features)
    assert len(smart) == 25
    assert smart[-1]["bucket_end"] == "2026-08-24T12:00:00Z"
    assert decision["availability"] == "AVAILABLE"
    assert decision["action"] == "LONG"


def test_exchange_outflows_preserve_the_provider_signed_direction():
    body = _flow_body(change=-0.01)
    for row in body["data"]:
        row.update(
            total_outflows_count=-2.0,
            total_inflows_dex=3.0,
            total_outflows_dex=-1.0,
            total_inflows_cex=4.0,
            total_outflows_cex=-1.0,
        )
    rows = validate_flow_body(
        body, candidate=CANDIDATE, label="exchange", cutoff=CUTOFF
    )
    assert rows[-1]["total_outflows_count"] == -2.0
    assert rows[-1]["total_outflows_dex"] == -1.0


def test_flow_rejects_a_leading_row_outside_the_requested_buffer():
    body = _flow_body()
    stale = dict(body["data"][0])
    stale_end = CUTOFF.replace(minute=0) - timedelta(hours=26)
    stale["date"] = (stale_end - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    stale["bucket_end"] = stale_end.isoformat().replace("+00:00", "Z")
    body["data"].insert(0, stale)
    with pytest.raises(CohortFeatureError, match="one-hour buffer"):
        validate_flow_body(
            body, candidate=CANDIDATE, label="smart_money", cutoff=CUTOFF
        )


def test_incomplete_second_wbs_page_is_unavailable_not_zero():
    evidence = validate_wbs_pages(
        [_wbs("BUY", final=False), _wbs("BUY", page=2, final=False, start=2)],
        candidate=CANDIDATE,
        side="BUY",
    )
    assert evidence.available is False
    assert evidence.distinct_addresses is None
    assert evidence.reason == "page_two_not_final"


@pytest.mark.parametrize(
    "body,match",
    (
        ({"data": [], "pagination": {"page": 1, "per_page": 1000, "is_last_page": True}}, "fewer than 25"),
        (_flow_body(final=False), "complete page one"),
    ),
)
def test_flow_missingness_fails_closed(body, match):
    with pytest.raises(CohortFeatureError, match=match):
        validate_flow_body(body, candidate=CANDIDATE, label="smart_money", cutoff=CUTOFF)


def test_flow_count_fields_are_required_not_silently_omitted():
    body = _flow_body()
    body["data"][0].pop("total_inflows_count")
    with pytest.raises(CohortFeatureError, match="total_inflows_count"):
        validate_flow_body(
            body, candidate=CANDIDATE, label="smart_money", cutoff=CUTOFF
        )


def test_boolean_pagination_page_is_not_integer_page_one():
    body = _flow_body()
    body["pagination"]["page"] = True
    with pytest.raises(CohortFeatureError, match="complete page one"):
        validate_flow_body(
            body, candidate=CANDIDATE, label="smart_money", cutoff=CUTOFF
        )


def test_float_pagination_values_are_not_exact_integers():
    body = _flow_body()
    body["pagination"]["per_page"] = 1000.0
    with pytest.raises(CohortFeatureError, match="complete page one"):
        validate_flow_body(
            body, candidate=CANDIDATE, label="smart_money", cutoff=CUTOFF
        )


def test_duplicate_wbs_addresses_fail_closed():
    page = _wbs("BUY", count=2)
    page["data"][1]["address"] = page["data"][0]["address"]
    with pytest.raises(CohortFeatureError, match="unique"):
        validate_wbs_pages([page], candidate=CANDIDATE, side="BUY")


def test_missing_directional_breadth_volume_is_not_coerced_to_zero():
    page = _wbs("BUY", count=1)
    page["data"][0].pop("bought_volume_usd")
    with pytest.raises(CohortFeatureError, match="missing required"):
        validate_wbs_pages([page], candidate=CANDIDATE, side="BUY")


def test_wbs_page_cannot_exceed_declared_page_size():
    page = _wbs("BUY", count=1001)
    with pytest.raises(CohortFeatureError, match="exceeds"):
        validate_wbs_pages([page], candidate=CANDIDATE, side="BUY")
