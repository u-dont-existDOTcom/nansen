from __future__ import annotations

import copy
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from programs.nansen_rapid_research_v1 import aggregate, contract, design, runner


REPO_ROOT = Path(__file__).resolve().parents[1]


def _selected_discovery() -> dict:
    cycle = design.SCHEDULE[0]
    chain = "solana"
    token = "TokenCase1"
    assert design.identity_partition(chain, token) == "DISCOVERY"
    return {
        "status": "selected",
        "partition": "DISCOVERY",
        "selection_hash": design.selection_hash(cycle, chain, token),
        "event_id": "ps-c001-b01",
        "rank_band": 1,
        "cycle_index": 1,
        "phase": "discovery",
        "chain": chain,
        "token_address": token,
        "flow_mcap_ratio": 0.1,
        "price_change_raw": 0.1,
    }


def _claimed_filled_outcome(selection: dict) -> dict:
    cycle = design.SCHEDULE[0]
    t0 = cycle.scheduled_at + timedelta(minutes=5)
    multiple = 1.10
    return {
        "schema_version": 1,
        "source_kind": "validated_counterfactual_outcome",
        "event_id": selection["event_id"],
        "cycle_index": selection["cycle_index"],
        "phase": selection["phase"],
        "scheduled_at": cycle.scheduled_at.isoformat().replace("+00:00", "Z"),
        "identity": {
            "chain": selection["chain"],
            "token_address": selection["token_address"],
        },
        # These are merely well-formed claims. No raw page or artifact is supplied
        # from which aggregation could recompute either the hashes or the fills.
        "evidence_sha256": {
            "buy_pages": ["0" * 64],
            "sell_pages": ["1" * 64],
            "ohlcv": "2" * 64,
        },
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


def test_decision_rejects_crosswalk_that_only_claims_the_frozen_provenance() -> None:
    selection = _selected_discovery()
    crosswalk = contract.load_parallel_strategy_contract(REPO_ROOT)
    tampered = copy.deepcopy(crosswalk)
    candidate = tampered["candidates"][0]
    candidate["predicates"] = ["screen_positive"]
    candidate["primitives"] = {
        "screen_positive": copy.deepcopy(design.PROSPECTIVE_PRIMITIVES["screen_positive"])
    }

    with pytest.raises(
        design.B2DesignError, match="candidate definition|sealed crosswalk|predicate"
    ):
        design.candidate_decision(
            selection=selection,
            features={},
            candidate=candidate,
            cycle=design.SCHEDULE[0],
            sealed_crosswalk=tampered,
        )


def test_aggregation_rejects_a_long_that_conflicts_with_sealed_predicates() -> None:
    selection = _selected_discovery()
    cycle = design.SCHEDULE[0]
    candidate_id = "c11-screener-accumulation-benchmark"
    marker = {
        "schema_version": 1,
        "program_id": design.PROGRAM_ID,
        "source_kind": "sealed_candidate_decision",
        "event_id": selection["event_id"],
        "cycle_index": cycle.index,
        "phase": cycle.phase,
        "scheduled_at": cycle.scheduled_at.isoformat().replace("+00:00", "Z"),
        "identity": {
            "chain": selection["chain"],
            "token_address": selection["token_address"],
        },
        "candidate_contract_sha256": design.PARENT_CANDIDATE_CONTRACT_SHA256,
        "input_sha256": {
            "selection": design.canonical_sha256(selection),
            "features": "1" * 64,
            "candidate_definition": "2" * 64,
            "sealed_crosswalk": "3" * 64,
        },
        "candidate_id": candidate_id,
        "decision": "long",
        "candidate_predicates": {"screen_positive": False},
        "historical_veto_predicates": {
            "seller_breadth": False,
            "seller_volume": False,
            "price_nonpositive": False,
        },
        "prospective_veto": False,
    }
    record = {
        "cycle_index": cycle.index,
        "selection": selection,
        "decisions": {candidate_id: marker},
    }

    with pytest.raises(design.B2DesignError, match="decision|predicate|replay"):
        aggregate._decision(record, candidate_id)


@pytest.mark.parametrize("attestation", [None, "not-a-sha256"])
def test_aggregation_rejects_outcome_without_valid_replay_attestation(
    attestation: str | None,
) -> None:
    selection = _selected_discovery()
    outcome = _claimed_filled_outcome(selection)
    if attestation is not None:
        outcome["replay_attestation_sha256"] = attestation

    assert aggregate._validated_outcome_marker(selection, outcome) is False


def test_aggregation_rejects_nonexact_ohlcv_grid_count() -> None:
    selection = _selected_discovery()
    outcome = _claimed_filled_outcome(selection)
    outcome["replay_attestation_sha256"] = "a" * 64
    outcome["ohlcv_row_count"] = 0

    assert aggregate._validated_outcome_marker(selection, outcome) is False


def test_raw_reference_requires_request_metadata_and_receipt_time_binding(
    tmp_path: Path,
) -> None:
    body = {"data": []}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    response = tmp_path / "raw/nansen/reservation/attempt-1-response.json"
    response.parent.mkdir(parents=True)
    response.write_bytes(raw)
    program = SimpleNamespace(root=tmp_path)
    reference = {
        "path": response.relative_to(tmp_path).as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "body_sha256": design.canonical_sha256(body),
        # This is only a claim; no response metadata or bound request supports it.
        "retrieved_at": "2026-08-22T12:10:00Z",
    }

    with pytest.raises(
        runner.ParallelStrategyRunnerError,
        match="request|metadata|receipt|retrieval|provenance|binding",
    ):
        runner._load_result_reference(program, reference)


def test_raw_reference_is_confined_to_the_new_program_root(tmp_path: Path) -> None:
    program_root = tmp_path / "program"
    program_root.mkdir()
    outside = tmp_path / "forbidden-predecessor-artifact.json"
    body = {"outcome": "must-not-be-readable"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    outside.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    reference = {
        "schema_version": 1,
        "cycle_index": 1,
        "epoch": "predecision",
        "request": {
            "path": "../forbidden-predecessor-artifact.json",
            "sha256": digest,
        },
        "response": {
            "path": "../forbidden-predecessor-artifact.json",
            "sha256": digest,
            "body_sha256": design.canonical_sha256(body),
        },
        "metadata": {
            "path": "../forbidden-predecessor-artifact.json",
            "sha256": digest,
        },
    }

    with pytest.raises(
        runner.ParallelStrategyRunnerError,
        match="escape|confined|relative|program root|unsafe",
    ):
        runner._load_result_reference(SimpleNamespace(root=program_root), reference)


def test_postdecision_unscorable_cycle_retains_selected_long_denominators(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = []
    for band in range(1, design.TOKENS_PER_CYCLE + 1):
        tokens.append(
            {
                "token_index": band,
                "selection": {
                    "status": "selected",
                    "event_id": f"ps-c001-b{band:02d}",
                    "cycle_index": 1,
                    "phase": "discovery",
                    "rank_band": band,
                },
                "decisions": {
                    candidate_id: {}
                    for candidate_id in design.PARENT_NONCASH_CANDIDATE_IDS
                },
            }
        )
    decisions_path = tmp_path / "cycles/cycle-001/derived/decisions.json"
    decisions_path.parent.mkdir(parents=True)
    decisions_path.write_text(json.dumps({"tokens": tokens}), encoding="utf-8")
    program = SimpleNamespace(root=tmp_path)

    def terminal_state(_program, cycle_index: int) -> dict:
        seals = [{"stage": "decisions_sealed"}] if cycle_index == 1 else []
        return {
            "stage": "unscorable",
            "terminal_reason": "missed_settlement_window",
            "seals": seals,
        }

    monkeypatch.setattr(runner, "check_cycle", terminal_state)
    _, records = runner._phase_records(program, "discovery")

    cycle_one = records[: design.TOKENS_PER_CYCLE]
    assert all(record["selection"]["status"] == "selected" for record in cycle_one)
    assert all(record["decisions"] for record in cycle_one)
    assert all(record["outcome"] == {} for record in cycle_one)
