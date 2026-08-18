from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.nansen_signal_lab.cohort_aggregate import aggregate_rule


RULE = "buyer-breadth-exchange-comovement-v1"


def _records(*, long_count=160, base=0.05):
    rows = []
    first = datetime(2026, 8, 24, 12, 5, tzinfo=timezone.utc)
    for index in range(160):
        cycle = index // 5 + 1
        scheduled = first + timedelta(hours=44 * (cycle - 1))
        decision_t0 = scheduled + timedelta(minutes=5)
        token = index % 20
        action = "LONG" if index < long_count else "ABSTAIN"
        rows.append({
            "cycle_index": cycle,
            "scheduled_at": scheduled.isoformat().replace("+00:00", "Z"),
            "decision_t0": decision_t0.isoformat().replace("+00:00", "Z"),
            "chain": "base",
            "token_address": f"0x{token:040x}",
            "rule_id": RULE,
            "availability": "AVAILABLE",
            "action": action,
            "outcome": {
                "status": "SCORED",
                "gross_return": base + 0.01,
                "base_return_100bps": base,
                "stress_return_250bps": base - 0.015,
            },
        })
    return rows


def test_positive_diversified_160_signal_holdout_advances():
    result = aggregate_rule(
        _records(),
        rule_id=RULE,
        program_id="fixture-program",
        terminal_cycle_count=32,
        outcome_cycle_count=32,
        availability_integrity_ok=True,
        advance_eligible=True,
        bootstrap_replicates=100,
    )
    assert result["selection_status"] == "advances"
    assert result["counts"]["filled_strategy_signals"] == 160
    assert result["counts"]["represented_utc_weeks"] == 9
    assert result["counts"]["observation_span_seconds"] >= 56 * 24 * 60 * 60
    assert result["bootstrap"]["lower_95"] > 0


def test_counterfactual_fills_never_count_as_strategy_fills():
    result = aggregate_rule(
        _records(long_count=10),
        rule_id=RULE,
        program_id="fixture-program",
        terminal_cycle_count=32,
        outcome_cycle_count=32,
        availability_integrity_ok=True,
        advance_eligible=True,
        bootstrap_replicates=10,
    )
    assert result["counts"]["counterfactual_scored"] == 160
    assert result["counts"]["filled_strategy_signals"] == 10
    assert "insufficient_strategy_fills" in result["gate_reasons"]
    assert result["selection_status"] == "does_not_advance"


def test_nonterminal_and_negative_stress_fail_frozen_gates():
    rows = _records()
    for row in rows:
        row["outcome"]["stress_return_250bps"] = -0.01
    result = aggregate_rule(
        rows,
        rule_id=RULE,
        program_id="fixture-program",
        terminal_cycle_count=31,
        outcome_cycle_count=30,
        availability_integrity_ok=False,
        advance_eligible=True,
        bootstrap_replicates=10,
    )
    assert "not_all_32_cycles_terminal" in result["gate_reasons"]
    assert "one_or_more_cycles_unscorable" in result["gate_reasons"]
    assert "availability_or_budget_integrity_failure" in result["gate_reasons"]
    assert "nonpositive_token_equal_stress_return" in result["gate_reasons"]


def test_indexed_cycles_without_eight_elapsed_weeks_do_not_advance():
    rows = _records()
    first = datetime(2026, 8, 24, 12, 5, tzinfo=timezone.utc)
    for row in rows:
        compressed = first + timedelta(hours=42 * (row["cycle_index"] - 1))
        row["scheduled_at"] = compressed.isoformat().replace("+00:00", "Z")
        row["decision_t0"] = (compressed + timedelta(minutes=5)).isoformat().replace(
            "+00:00", "Z"
        )
    result = aggregate_rule(
        rows,
        rule_id=RULE,
        program_id="fixture-program",
        terminal_cycle_count=32,
        outcome_cycle_count=32,
        availability_integrity_ok=True,
        advance_eligible=True,
        bootstrap_replicates=10,
    )
    assert "observation_span_below_8_weeks" in result["gate_reasons"]
    assert result["selection_status"] == "does_not_advance"


def test_descriptive_rule_cannot_advance_even_when_all_numeric_gates_pass():
    result = aggregate_rule(
        _records(),
        rule_id=RULE,
        program_id="fixture-program",
        terminal_cycle_count=32,
        outcome_cycle_count=32,
        availability_integrity_ok=True,
        advance_eligible=False,
        bootstrap_replicates=10,
    )
    assert "descriptive_rule_not_advance_eligible" in result["gate_reasons"]
    assert result["selection_status"] == "does_not_advance"


def test_unavailable_decision_cannot_be_relabelled_as_an_abstention_or_ignored():
    rows = _records()
    rows[0]["availability"] = "UNAVAILABLE"
    rows[0]["action"] = None
    result = aggregate_rule(
        rows,
        rule_id=RULE,
        program_id="fixture-program",
        terminal_cycle_count=32,
        outcome_cycle_count=32,
        availability_integrity_ok=True,
        advance_eligible=True,
        bootstrap_replicates=10,
    )
    assert result["counts"]["opportunities"] == 160
    assert result["counts"]["unavailable_opportunities"] == 1
    assert "one_or_more_rule_decisions_unavailable" in result["gate_reasons"]
    assert result["selection_status"] == "does_not_advance"


def test_reached_unscorable_evidence_keeps_selection_and_attempt_counts():
    rows = _records(long_count=5)[:5]
    for row in rows:
        row["outcome"] = {"status": "UNAVAILABLE"}
    result = aggregate_rule(
        rows,
        rule_id=RULE,
        program_id="fixture-program",
        terminal_cycle_count=32,
        outcome_cycle_count=31,
        availability_integrity_ok=False,
        advance_eligible=True,
        bootstrap_replicates=10,
        selected_opportunity_count=160,
        attempted_counterfactual_fill_count=1,
    )
    assert result["counts"]["opportunities"] == 160
    assert result["counts"]["decision_opportunities"] == 5
    assert result["counts"]["strategy_signals"] == 5
    assert result["counts"]["attempted_counterfactual_fills"] == 1
    assert result["counts"]["completed_counterfactual_outcomes"] == 0
    assert result["counts"]["unavailable_opportunities"] == 155
