from __future__ import annotations

import itertools
import hashlib
import json
import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from programs.nansen_parallel_strategy_v1 import aggregate
from programs.nansen_parallel_strategy_v1 import contract
from programs.nansen_parallel_strategy_v1 import design
from programs.nansen_parallel_strategy_v1 import timing


def _row(index: int, *, chain: str = "ethereum") -> dict:
    address = f"0x{index:040x}"
    return {
        "chain": chain,
        "token_address": address,
        "token_symbol": f"T{index}",
        "price_usd": 1.0,
        "price_change": 0.01,
        "volume": 1_000_000.0,
        "liquidity": 500_000.0,
        "market_cap_usd": 2_000_000.0,
        "token_age_days": 10,
        "netflow": 100_000.0 - index,
    }


def _body(rows: list[dict]) -> dict:
    return {
        "data": copy.deepcopy(rows),
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
    }


def _selected(cycle, chain: str, token: str, band: int = 1, **extra) -> dict:
    return {
        "status": "selected",
        "partition": "DISCOVERY" if cycle.phase == "discovery" else "VALIDATION",
        "selection_hash": design.selection_hash(cycle, chain, token),
        "event_id": f"ps-c{cycle.index:03d}-b{band:02d}",
        "rank_band": band,
        "cycle_index": cycle.index,
        "phase": cycle.phase,
        "chain": chain,
        "token_address": token,
        **extra,
    }


def test_schedule_purge_blocks_and_budget_are_exact():
    cycles = design.SCHEDULE
    assert len(cycles) == 85
    assert cycles[0].scheduled_at.isoformat() == "2026-10-15T12:05:00+00:00"
    assert cycles[41].scheduled_at.isoformat() == "2026-10-29T04:05:00+00:00"
    assert cycles[42].scheduled_at - cycles[41].scheduled_at == timedelta(hours=32)
    assert cycles[-1].scheduled_at.isoformat() == "2026-11-13T12:05:00+00:00"
    assert [sum(c.block == block for c in cycles[:42]) for block in range(1, 7)] == [7] * 6
    assert [sum(c.block == block for c in cycles[42:]) for block in range(1, 7)] == [7, 7, 7, 7, 7, 8]
    assert design.SETTLEMENT_OFFSET == timedelta(hours=4, minutes=21)
    assert design.SETTLEMENT_TRANSPORT_CUTOFF == timedelta(hours=7, minutes=58, seconds=30)
    contract = design.budget_contract()
    assert contract["predecision_attempts"] == 80
    assert contract["predecision_credits"] == 79
    assert contract["settlement_attempts"] == 66
    assert contract["settlement_credits"] == 65
    assert contract["program_attempts"] == 12_410
    assert contract["program_credits"] == 12_240


def test_partition_hash_and_golden_vectors():
    assert design._sha256(design.PARTITION_SALT) == design.PARTITION_SALT_SHA256
    vectors = {
        ("ethereum", "0x0000000000000000000000000000000000000001"): "VALIDATION",
        ("bnb", "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"): "REPLICATION",
        ("solana", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"): "DISCOVERY",
        ("base", "0x1111111111111111111111111111111111111111"): "VALIDATION",
    }
    for identity, expected in vectors.items():
        assert design.identity_partition(*identity) == expected
    assert design.normalize_identity(
        "BSC", "0x0000000000000000000000000000000000ABCDEF"
    ) == ("bnb", "0x0000000000000000000000000000000000abcdef")
    assert design.normalize_identity("solana", "AbCd") == ("solana", "AbCd")


def test_screener_is_complete_ordered_unique_and_semantically_bounded():
    rows = [_row(index) for index in range(20)]
    candidates = design.validate_screener(_body(rows))
    assert len(candidates) == 20
    assert candidates[0].source_rank == 1
    bad = _body(rows)
    bad["pagination"]["is_last_page"] = False
    with pytest.raises(design.B2DesignError, match="complete"):
        design.validate_screener(bad)
    bad = _body(rows)
    bad["data"][1]["netflow"] = bad["data"][0]["netflow"] + 1
    with pytest.raises(design.B2DesignError, match="descending"):
        design.validate_screener(bad)
    bad = _body(rows)
    bad["data"][0]["price_change"] = 20.01
    with pytest.raises(design.B2DesignError, match="semantics"):
        design.validate_screener(bad)
    for invalid in (True, "1", float("nan"), float("inf")):
        bad = _body(rows)
        bad["data"][0]["liquidity"] = invalid
        with pytest.raises(design.B2DesignError, match="finite numeric"):
            design.validate_screener(bad)
    bad = _body(rows)
    bad["data"][0]["netflow"] = None
    with pytest.raises(design.B2DesignError, match="descending"):
        design.validate_screener(bad)


def test_thirteen_band_selection_is_deterministic_and_partition_local():
    rows: list[dict] = []
    index = 1
    while len(rows) < 80:
        row = _row(index, chain=design.CHAINS[index % 4])
        candidate = design.validate_screener(_body([row]))[0]
        if design.identity_partition(*candidate.identity) == "DISCOVERY":
            rows.append(row)
        index += 1
    candidates = design.validate_screener(_body(rows))
    selected = design.select_cycle(
        candidates,
        cycle=design.SCHEDULE[0],
        prior_identity_counts={},
        prior_chain_counts={},
    )
    replay = design.select_cycle(
        candidates,
        cycle=design.SCHEDULE[0],
        prior_identity_counts={},
        prior_chain_counts={},
    )
    assert selected == replay
    assert len(selected) == 13
    assert [row["rank_band"] for row in selected] == list(range(1, 14))
    assert len({(row["chain"], row["token_address"]) for row in selected}) == 13
    assert all(row["partition"] == "DISCOVERY" for row in selected)
    with pytest.raises(design.B2DesignError, match="fewer than 13"):
        design.select_cycle(
            candidates[:12],
            cycle=design.SCHEDULE[0],
            prior_identity_counts={},
            prior_chain_counts={},
        )
    with pytest.raises(design.B2DesignError, match="duplicate normalized chains"):
        design.select_cycle(
            candidates,
            cycle=design.SCHEDULE[0],
            prior_identity_counts={},
            prior_chain_counts={"bnb": 1, "bsc": 1},
        )


def test_point_in_time_feature_payload_bounds_are_exact():
    cycle = design.SCHEDULE[0]
    candidate = _selected(
        cycle,
        "ethereum",
        "0x0000000000000000000000000000000000000003",
    )
    flow = design.smart_money_payload(candidate, cycle)
    assert flow["date"] == {
        "from": "2026-10-14T10:00:00Z",
        "to": "2026-10-15T11:59:59.999999Z",
    }
    wbs = design.breadth_payload(candidate, cycle, "BUY", 1)
    assert wbs["date"] == {
        "from": "2026-10-14T12:05:00Z",
        "to": "2026-10-15T12:04:59.999999Z",
    }
    assert design.flow_intelligence_payload(candidate, cycle) == {
        "chain": "ethereum",
        "token_address": "0x0000000000000000000000000000000000000003",
        "timeframe": "1d",
    }


def test_strong_kleene_and_staged_decision_truth_tables():
    values = (True, False, None)
    for combination in itertools.product(values, repeat=3):
        expected = False if False in combination else True if set(combination) == {True} else None
        assert design.kleene_and(combination) is expected
    assert design.compose_decision([False], [None], [None]) == "abstain"
    assert design.compose_decision([None], [False], [False]) == "unavailable"
    assert design.compose_decision([True], [True], [False]) == "abstain"
    assert design.compose_decision([True], [None], [False]) == "unavailable"
    assert design.compose_decision([True], [False], [True]) == "abstain"
    assert design.compose_decision([True], [False], [None]) == "unavailable"
    assert design.compose_decision([True], [False], [False]) == "long"
    with pytest.raises(design.B2DesignError, match="non-tri-state"):
        design.kleene_and([1])


def test_distribution_veto_uses_all_four_required_predicates():
    feature = {
        "market_phase_4h": "markdown",
        "distribution_persistence_4h": 0.75,
        "holdings_acceleration_4h_pct_per_hour": -0.1,
        "holder_count_change_4h": -1,
    }
    assert design.prospective_distribution_veto(feature) is True
    feature["holder_count_change_4h"] = 0
    assert design.prospective_distribution_veto(feature) is False
    del feature["holder_count_change_4h"]
    assert design.prospective_distribution_veto(feature) is None
    for phase in ("accumulation_divergence", "markup", "flat"):
        feature.update(
            {
                "market_phase_4h": phase,
                "distribution_persistence_4h": 1.0,
                "holdings_acceleration_4h_pct_per_hour": -0.1,
                "holder_count_change_4h": -1,
            }
        )
        assert design.prospective_distribution_veto(feature) is False


def test_parallel_contract_is_exact_full_pre_live_family():
    path = (
        "research/experiments/2026-08-18-historical-theory-discovery-a-v1/"
        "contracts/candidates.json"
    )
    raw = open(path, "rb").read()
    assert hashlib.sha256(raw).hexdigest() == design.PARENT_CANDIDATE_CONTRACT_SHA256
    parent = json.loads(raw)
    full = design.full_candidate_crosswalk(parent)
    assert tuple(item["candidate_id"] for item in full["candidates"]) == (
        design.PARENT_NONCASH_CANDIDATE_IDS
    )
    crosswalk = design.parallel_strategy_contract(parent)
    assert crosswalk["candidate_ids"] == list(design.PARENT_NONCASH_CANDIDATE_IDS)
    assert crosswalk["source_commit"] == "610f31c"
    with pytest.raises(design.B2DesignError, match="exact eleven-rule"):
        design.candidate_crosswalk(parent, ["c12-cash-no-signal-benchmark"])
    with pytest.raises(design.B2DesignError, match="exact eleven-rule"):
        design.candidate_crosswalk(parent, ["missing"])
    parent["candidates"][0]["predicates"].append("unmapped")
    with pytest.raises(design.B2DesignError, match="predicate sequence"):
        design.parallel_strategy_contract(parent)
    parent = json.loads(raw)
    parent["candidates"][0]["predicates"][1] = "buyer_volume"
    with pytest.raises(design.B2DesignError, match="predicate sequence"):
        design.full_candidate_crosswalk(parent)


def test_flow_intelligence_is_fresh_warning_free_singleton_and_nullable():
    cycle = design.SCHEDULE[0]
    candidate = _selected(
        cycle,
        "ethereum",
        "0x0000000000000000000000000000000000000003",
    )
    now = cycle.scheduled_at + timedelta(minutes=5)
    body = {
        "chain": "ethereum",
        "token_address": "0x0000000000000000000000000000000000000003",
        "data": [{"exchange_net_flow_usd": -10.0, "smart_trader_wallet_count": 2}],
        "warnings": [],
    }
    normalized = design.validate_flow_intelligence(
        body,
        candidate=candidate,
        cycle=cycle,
        cache_hit=False,
        retrieved_at=now,
    )
    assert normalized["available"] is True
    assert normalized["exchange_net_flow_usd"] == -10.0
    assert normalized["whale_net_flow_usd"] is None
    assert design.validate_flow_intelligence(
        body,
        candidate=candidate,
        cycle=cycle,
        cache_hit=True,
        retrieved_at=now,
    ) == {"available": False, "reason": "cache_hit_not_admissible"}
    warning = {**body, "warnings": ["stale segment"]}
    assert design.validate_flow_intelligence(
        warning,
        candidate=candidate,
        cycle=cycle,
        cache_hit=False,
        retrieved_at=now,
    )["available"] is False
    with pytest.raises(design.B2DesignError, match="exactly one"):
        design.validate_flow_intelligence(
            {**body, "data": []},
            candidate=candidate,
            cycle=cycle,
            cache_hit=False,
            retrieved_at=now,
        )


def test_wbs_normalizes_bsc_echo_and_preserves_two_page_unavailability():
    cycle = design.SCHEDULE[0]
    candidate = _selected(
        cycle,
        "bnb",
        "0x0000000000000000000000000000000000000005",
    )
    empty = {
        "chain": "bsc",
        "token_address": candidate["token_address"],
        "data": [],
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
    }
    admitted = design.validate_wbs_evidence(
        [empty], candidate=candidate, cycle=cycle, side="BUY"
    )
    assert admitted["available"] is True
    assert admitted["identity"]["chain"] == "bnb"
    page1 = copy.deepcopy(empty)
    page1["pagination"]["is_last_page"] = False
    page2 = copy.deepcopy(empty)
    page2["pagination"] = {"page": 2, "per_page": 1000, "is_last_page": False}
    unavailable = design.validate_wbs_evidence(
        [page1, page2], candidate=candidate, cycle=cycle, side="BUY"
    )
    assert unavailable["available"] is False
    assert unavailable["reason"] == "page_two_not_final"


def test_candidate_evaluation_is_candidate_then_both_vetoes():
    cycle = design.SCHEDULE[0]
    event = _selected(
        cycle,
        "ethereum",
        "0x0000000000000000000000000000000000000003",
    )
    scheduled_text = cycle.scheduled_at.isoformat().replace("+00:00", "Z")
    selection = {**event, "flow_mcap_ratio": 0.1, "price_change_raw": 0.1}
    features = {
        "buy": {
            "schema_version": 1,
            "source_kind": "validated_wbs",
            "identity": {"chain": "ethereum", "token_address": "0x0000000000000000000000000000000000000003"},
            "event_id": event["event_id"],
            "cycle_index": 1,
            "phase": "discovery",
            "scheduled_at": scheduled_text,
            "side": "BUY",
            "available": True,
            "address_count": 5,
            "volume_usd": 100.0,
        },
        "sell": {
            "schema_version": 1,
            "source_kind": "validated_wbs",
            "identity": {"chain": "ethereum", "token_address": "0x0000000000000000000000000000000000000003"},
            "event_id": event["event_id"],
            "cycle_index": 1,
            "phase": "discovery",
            "scheduled_at": scheduled_text,
            "side": "SELL",
            "available": True,
            "address_count": 2,
            "volume_usd": 50.0,
        },
        "flow_intelligence": {
            "schema_version": 1,
            "source_kind": "validated_flow_intelligence",
            "identity": {"chain": "ethereum", "token_address": "0x0000000000000000000000000000000000000003"},
            "event_id": event["event_id"],
            "cycle_index": 1,
            "phase": "discovery",
            "scheduled_at": scheduled_text,
            "available": True,
            "warnings_present": False,
            "cache_hit": False,
            "exchange_net_flow_usd": -5.0,
        },
        "smart_money": {
            "schema_version": 1,
            "source_kind": "validated_smart_money_flow",
            "identity": {"chain": "ethereum", "token_address": "0x0000000000000000000000000000000000000003"},
            "event_id": event["event_id"],
            "cycle_index": 1,
            "phase": "discovery",
            "scheduled_at": scheduled_text,
            "final_feature": {
                "market_phase_4h": "markup",
                "distribution_persistence_4h": 0.0,
                "holdings_acceleration_4h_pct_per_hour": 0.1,
                "holder_count_change_4h": 1,
            }
        },
    }
    path = (
        "research/experiments/2026-08-18-historical-theory-discovery-a-v1/"
        "contracts/candidates.json"
    )
    crosswalk = design.parallel_strategy_contract(json.loads(open(path, "rb").read()))
    candidate = crosswalk["candidates"][0]
    assert design.candidate_decision(
        selection=selection,
        features=features,
        candidate=candidate,
        cycle=cycle,
        sealed_crosswalk=crosswalk,
    )["decision"] == "long"
    features["sell"]["address_count"] = 6
    features["sell"]["volume_usd"] = 200.0
    selection["price_change_raw"] = -0.1
    assert design.candidate_decision(
        selection=selection,
        features=features,
        candidate=candidate,
        cycle=cycle,
        sealed_crosswalk=crosswalk,
    )["decision"] == "abstain"


@dataclass
class _Fill:
    side: str
    requested_amount: float
    filled_token_amount: float
    observed_usd: float
    fill_ratio: float
    is_complete: bool
    vwap_usd: float | None = None
    trade_count: int = 0


def test_execution_preserves_partial_entry_and_exit_and_scores_only_complete():
    partial_entry = _Fill("BUY", 1000.0, 5.0, 500.0, 0.5, False, 100.0, 1)
    partial_exit = _Fill("SELL", 5.0, 2.5, 300.0, 0.5, False, 120.0, 1)
    result = design._execution_result(
        notional_usd=1000.0,
        entry_fill=partial_entry,
        exit_fill=partial_exit,
    )
    assert result["status"] == "partial"
    assert result["entry"]["filled_token_amount"] == 5.0
    assert result["exit"]["sold_token_amount"] == 2.5
    assert "base_return" not in result
    full = design._execution_result(
        notional_usd=1000.0,
        entry_fill=_Fill("BUY", 1000.0, 10.0, 1000.0, 1.0, True, 100.0, 1),
        exit_fill=_Fill("SELL", 10.0, 10.0, 1100.0, 1.0, True, 110.0, 1),
    )
    assert full["status"] == "filled"
    assert full["gross_return"] == pytest.approx(0.1)
    unfilled = design._execution_result(
        notional_usd=1000.0,
        entry_fill=_Fill("BUY", 1000.0, 0.0, 0.0, 0.0, False),
        exit_fill=None,
    )
    assert unfilled["status"] == "unfilled_entry"
    assert unfilled["exit"] is None
    with pytest.raises(design.B2DesignError, match="exit fill invariants"):
        design._execution_result(
            notional_usd=1000.0,
            entry_fill=_Fill("BUY", 1000.0, 10.0, 1000.0, 1.0, True, 100.0, 1),
            exit_fill=_Fill("SELL", 10.0, 5.0, 550.0, 1.0, True, 110.0, 1),
        )


def test_raw_counterfactual_replay_preserves_partial_round_trip():
    candidate = _selected(
        design.SCHEDULE[0],
        "solana",
        "TokenCase",
        virtual_notional_usd=1000.0,
    )
    t0 = datetime(2026, 10, 15, 12, 20, tzinfo=timezone.utc)
    entry_at = t0 + timedelta(minutes=5, seconds=1)
    exit_at = t0 + timedelta(hours=4, minutes=5, seconds=1)

    def page(side: str, at: datetime, amount: float, price: float) -> dict:
        return {
            "data": [
                {
                    "block_timestamp": at.isoformat().replace("+00:00", "Z"),
                    "transaction_hash": f"{side.lower()}-1",
                    "action": side,
                    "token_address": "TokenCase",
                    "trader_address": "Trader",
                    "token_name": "Token",
                    "traded_token_address": "USD",
                    "traded_token_name": "USD",
                    "token_amount": amount,
                    "traded_token_amount": amount * price,
                    "estimated_swap_price_usd": price,
                    "estimated_value_usd": amount * price,
                }
            ],
            "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
        }

    rows = []
    cursor = t0
    while cursor <= t0 + timedelta(hours=4, minutes=15):
        rows.append(
            {
                "interval_start": cursor.isoformat().replace("+00:00", "Z"),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 1.0,
            }
        )
        cursor += timedelta(minutes=5)
    ohlcv = {
        "chain": "solana",
        "token_address": "TokenCase",
        "timeframe": "5m",
        "truncated": False,
        "data": rows,
    }
    result = design.build_counterfactual_outcome(
        candidate=candidate,
        cycle=design.SCHEDULE[0],
        t0=t0,
        buy_pages=[page("BUY", entry_at, 5.0, 100.0)],
        sell_pages=[page("SELL", exit_at, 2.0, 120.0)],
        ohlcv_body=ohlcv,
        retrieved_at=t0 + timedelta(hours=4, minutes=21),
    )
    assert result["status"] == "partial"
    assert result["entry"]["fill_ratio"] == 0.5
    assert result["exit"]["fill_ratio"] == 0.4
    assert result["ohlcv_row_count"] == 52


def test_token_equal_mean_does_not_event_weight_repeated_token():
    events = [
        {"chain": "ethereum", "token_address": "0x0000000000000000000000000000000000000001", "base_return": 1.0},
        {"chain": "ethereum", "token_address": "0x0000000000000000000000000000000000000001", "base_return": 1.0},
        {"chain": "solana", "token_address": "S", "base_return": -0.5},
    ]
    assert design.token_equal_mean(events, "base_return") == pytest.approx(0.25)


def test_nonconstant_frozen_ten_thousand_bootstrap_golden_vector():
    events = [
        {
            "block": block,
            "chain": "ethereum",
            "token_address": f"0x{block:040x}",
            "base_return": value,
        }
        for block, value in enumerate((-0.2, -0.1, 0.0, 0.1, 0.2, 0.3), start=1)
    ]
    assert aggregate.bootstrap_lower_bound(
        events,
        phase="discovery",
        candidate_id="c01-buyer-breadth-exchange",
    ) == pytest.approx(-0.15)
    with pytest.raises(TypeError):
        aggregate.bootstrap_lower_bound(
            events,
            phase="discovery",
            candidate_id="c01-buyer-breadth-exchange",
            replicates=300,
        )


def _decision_marker(cycle, selection, candidate_id):
    event_id = selection["event_id"]
    chain = selection["chain"]
    token = selection["token_address"]
    return {
        "schema_version": 1,
        "program_id": design.PROGRAM_ID,
        "source_kind": "sealed_candidate_decision",
        "event_id": event_id,
        "cycle_index": cycle.index,
        "phase": cycle.phase,
        "scheduled_at": cycle.scheduled_at.isoformat().replace("+00:00", "Z"),
        "identity": {"chain": chain, "token_address": token},
        "candidate_contract_sha256": design.PARENT_CANDIDATE_CONTRACT_SHA256,
        "input_sha256": {
            "selection": design.canonical_sha256(selection),
            "features": "a" * 64,
            "candidate_definition": "b" * 64,
            "sealed_crosswalk": "c" * 64,
        },
        "candidate_id": candidate_id,
        "decision": "long",
        "candidate_predicates": {
            predicate: True
            for predicate in design.PARENT_CANDIDATE_PREDICATES[candidate_id]
        },
        "historical_veto_predicates": {
            predicate: False for predicate in design.HISTORICAL_VETO_PREDICATES
        },
        "prospective_veto": False,
    }


def _filled_outcome(cycle, event_id, chain, token) -> dict:
    t0 = cycle.scheduled_at + timedelta(minutes=5)
    multiple = 1.10
    return {
        "schema_version": 1,
        "source_kind": "validated_counterfactual_outcome",
        "event_id": event_id,
        "cycle_index": cycle.index,
        "phase": cycle.phase,
        "scheduled_at": cycle.scheduled_at.isoformat().replace("+00:00", "Z"),
        "identity": {"chain": chain, "token_address": token},
        "evidence_sha256": {
            "buy_pages": ["0" * 64],
            "sell_pages": ["1" * 64],
            "ohlcv": "2" * 64,
        },
        "replay_attestation_sha256": "f" * 64,
        "ohlcv_available": True,
        "ohlcv_row_count": 52,
        "t0": t0.isoformat().replace("+00:00", "Z"),
        "retrieved_at": (t0 + design.SETTLEMENT_OFFSET)
        .isoformat()
        .replace("+00:00", "Z"),
        "available": True,
        "notional_usd": 1000.0,
        "status": "filled",
        "entry": {
            "requested_amount": 1000.0,
            "filled_token_amount": 10.0,
            "observed_usd": 1000.0,
            "vwap_usd": 100.0,
            "trade_count": 1,
            "fill_ratio": 1.0,
            "is_complete": True,
        },
        "exit": {
            "requested_amount": 10.0,
            "sold_token_amount": 10.0,
            "observed_usd": 1100.0,
            "vwap_usd": 110.0,
            "trade_count": 1,
            "fill_ratio": 1.0,
            "is_complete": True,
        },
        "gross_return": multiple - 1,
        "base_return": multiple * (1 - design.BASE_COST_RATE) ** 2 - 1,
        "stress_return": multiple * (1 - design.STRESS_COST_RATE) ** 2 - 1,
    }


def _phase_records(phase: str) -> list[dict]:
    records = []
    for cycle in (item for item in design.SCHEDULE if item.phase == phase):
        for band in range(13):
            event_id = f"ps-c{cycle.index:03d}-b{band + 1:02d}"
            chain = design.CHAINS[band % 4]
            required = "DISCOVERY" if phase == "discovery" else "VALIDATION"
            nonce = cycle.index * 1000 + band
            while True:
                token = f"0x{nonce:040x}"
                if design.identity_partition(chain, token) == required:
                    break
                nonce += 1
            selection = _selected(cycle, chain, token, band + 1)
            records.append(
                {
                    "cycle_index": cycle.index,
                    "selection": selection,
                    "decisions": {
                        candidate_id: _decision_marker(
                            cycle, selection, candidate_id
                        )
                        for candidate_id in design.PARENT_NONCASH_CANDIDATE_IDS
                    },
                    "outcome": _filled_outcome(
                        cycle, event_id, chain, token
                    ),
                }
            )
    return records


def test_phase_support_retains_all_planned_denominators():
    records = _phase_records("discovery")
    statuses = {cycle.index: "complete" for cycle in design.SCHEDULE[:42]}
    support = aggregate.phase_support(
        phase="discovery", cycle_statuses=statuses, records=records
    )
    assert support["passed"] is True
    assert support["planned_opportunities"] == 546
    records.pop()
    with pytest.raises(design.B2DesignError, match="denominator"):
        aggregate.phase_support(
            phase="discovery", cycle_statuses=statuses, records=records
        )
    records = _phase_records("discovery")
    records[0]["selection"]["event_id"] = records[1]["selection"]["event_id"]
    with pytest.raises(design.B2DesignError, match="event IDs"):
        aggregate.phase_support(
            phase="discovery", cycle_statuses=statuses, records=records
        )


def test_candidate_gates_and_anchor_challenger_freeze_are_deterministic():
    records = _phase_records("discovery")
    statuses = {cycle.index: "complete" for cycle in design.SCHEDULE[:42]}
    first = aggregate.candidate_score(
        phase="discovery",
        candidate_id="c01-buyer-breadth-exchange",
        records=records,
    )
    replay = aggregate.candidate_score(
        phase="discovery",
        candidate_id="c01-buyer-breadth-exchange",
        records=records,
    )
    assert first == replay
    assert first["passed"] is True
    assert first["bootstrap_lower_bound"] == pytest.approx(
        1.10 * (1 - design.BASE_COST_RATE) ** 2 - 1
    )
    frozen = aggregate.freeze_discovery_family(
        cycle_statuses=statuses, records=records
    )
    assert frozen["stage"] == "validation_family_frozen"
    assert frozen["validation_family_ids"][0] == "c01-buyer-breadth-exchange"
    assert len(frozen["validation_family_ids"]) == 2
    assert frozen["challenger_id"] == "c02-buyer-volume-exchange"
    with pytest.raises(design.B2DesignError, match="denominator|wrong phase|identity"):
        aggregate.candidate_score(
            phase="discovery",
            candidate_id="c01-buyer-breadth-exchange",
            records=_phase_records("validation"),
        )
    with pytest.raises(design.B2DesignError, match="sealed eleven-rule"):
        aggregate.candidate_score(
            phase="discovery", candidate_id="rogue", records=records
        )


def test_no_qualifying_challenger_freezes_anchor_only():
    records = _phase_records("discovery")
    statuses = {cycle.index: "complete" for cycle in design.SCHEDULE[:42]}
    for record in records:
        for candidate_id in design.PARENT_NONCASH_CANDIDATE_IDS[1:]:
            marker = record["decisions"][candidate_id]
            first_predicate = design.PARENT_CANDIDATE_PREDICATES[candidate_id][0]
            marker["candidate_predicates"][first_predicate] = False
            marker["decision"] = "abstain"
    frozen = aggregate.freeze_discovery_family(
        cycle_statuses=statuses, records=records
    )
    assert frozen["challenger_id"] is None
    assert frozen["validation_family_ids"] == ["c01-buyer-breadth-exchange"]


def test_validation_requires_positive_bootstrap_lower_bound():
    records = _phase_records("validation")
    statuses = {cycle.index: "complete" for cycle in design.SCHEDULE[42:]}
    discovery_records = _phase_records("discovery")
    discovery_statuses = {
        cycle.index: "complete" for cycle in design.SCHEDULE[:42]
    }
    score = aggregate.candidate_score(
        phase="validation",
        candidate_id="c01-buyer-breadth-exchange",
        records=records,
    )
    assert score["passed"] is True
    assert score["checks"]["bootstrap_lower_bound"] is True
    family = aggregate.freeze_discovery_family(
        cycle_statuses=discovery_statuses, records=discovery_records
    )
    result = aggregate.validation_result(
        discovery_cycle_statuses=discovery_statuses,
        discovery_records=discovery_records,
        validation_cycle_statuses=statuses,
        validation_records=records,
        family_seal=family,
    )
    assert result["outcome"] == "validated_rule"


def test_validation_refuses_rogue_or_non_anchor_family():
    records = _phase_records("validation")
    statuses = {cycle.index: "complete" for cycle in design.SCHEDULE[42:]}
    discovery_records = _phase_records("discovery")
    discovery_statuses = {
        cycle.index: "complete" for cycle in design.SCHEDULE[:42]
    }
    family = aggregate.freeze_discovery_family(
        cycle_statuses=discovery_statuses, records=discovery_records
    )
    for candidate_ids in (["c02-buyer-volume-exchange"], ["rogue"]):
        tampered = {**family, "validation_family_ids": candidate_ids}
        with pytest.raises(design.B2DesignError, match="does not replay"):
            aggregate.validation_result(
                discovery_cycle_statuses=discovery_statuses,
                discovery_records=discovery_records,
                validation_cycle_statuses=statuses,
                validation_records=records,
                family_seal=tampered,
            )


def test_contract_loader_reads_definitions_only():
    loaded = contract.load_parallel_strategy_contract(Path("."))
    assert loaded["candidate_ids"] == list(design.PARENT_NONCASH_CANDIDATE_IDS)
    assert loaded["source"]["sha256"] == design.PARENT_CANDIDATE_CONTRACT_SHA256
    serialized = json.dumps(loaded, sort_keys=True)
    for forbidden in ("ranking", "outcome", "panel", "program_support"):
        assert forbidden not in serialized


def test_timing_boundaries_are_exact_and_never_backfill():
    cycle = design.SCHEDULE[0]
    scheduled = cycle.scheduled_at
    assert timing.start_state(cycle, scheduled - timedelta(microseconds=1)) == "wait"
    assert timing.start_state(cycle, scheduled) == "start"
    assert timing.start_state(cycle, scheduled + timedelta(minutes=15)) == "start"
    assert timing.start_state(
        cycle, scheduled + timedelta(minutes=15, microseconds=1)
    ) == "missed"
    assert timing.predecision_transport_allowed(
        cycle, scheduled + timedelta(minutes=43, seconds=29)
    )
    assert not timing.predecision_transport_allowed(
        cycle, scheduled + timedelta(minutes=43, seconds=30)
    )
    t0 = timing.decision_t0(cycle, scheduled + timedelta(minutes=45))
    assert t0 == scheduled + timedelta(minutes=50)
    earliest = t0 + timedelta(hours=4, minutes=21)
    assert timing.settlement_state(cycle, t0=t0, now=earliest) == "settle"
    assert timing.settlement_state(
        cycle,
        t0=t0,
        now=scheduled + timedelta(hours=7, minutes=58, seconds=29),
    ) == "settle"
    assert timing.settlement_state(
        cycle,
        t0=t0,
        now=scheduled + timedelta(hours=7, minutes=58, seconds=30),
    ) == "missed"
    assert timing.settlement_hard_stop(cycle) == scheduled + timedelta(
        hours=7, minutes=59, seconds=40
    )
    with pytest.raises(design.B2DesignError, match="admissible boundary"):
        timing.settlement_state(
            cycle,
            t0=scheduled - timedelta(hours=4, minutes=21),
            now=scheduled,
        )
    with pytest.raises(design.B2DesignError, match="admissible boundary"):
        timing.settlement_state(
            cycle,
            t0=design.SCHEDULE[1].scheduled_at + timedelta(minutes=5),
            now=scheduled,
        )
