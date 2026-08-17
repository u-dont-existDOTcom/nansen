from __future__ import annotations

import copy
import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.nansen_signal_lab.evaluation import (
    BlockedTheorySpec,
    ComparisonSpec,
    CostScenario,
    EvaluationBundle,
    EvaluationError,
    EvaluationTables,
    Predicate,
    TheorySpec,
    TimeBlock,
    build_comparison_results,
    build_evaluation,
    build_paper_selection,
    build_theory_events,
    build_theory_summaries,
    entry_objective_pct,
    evaluate_manifest,
    load_evaluation_manifest,
    predicate_matches,
    render_evaluation_outputs,
    veto_objective_pct,
)

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "research/experiments/2026-08-16-seven-token-pilot"
SHADOW = ROOT / "research/experiments/2026-08-16-community-signal-shadow"


def _manifest(root: Path) -> dict:
    shadow = root / "2026-08-16-community-signal-shadow/manifest.json"
    return {
        "schema_version": 3,
        "experiment_id": "fixture-evaluation",
        "title": "fixture",
        "status": "discovery",
        "created_at": "2026-08-17T00:00:00Z",
        "hypothesis": "fixed theories",
        "source_signal_manifest": "../2026-08-16-community-signal-shadow/manifest.json",
        "source_signal_manifest_sha256": hashlib.sha256(shadow.read_bytes()).hexdigest(),
        "evaluation_window": {
            "from": "2026-08-13T11:00:00Z",
            "to": "2026-08-16T10:00:00Z",
        },
        "time_blocks": [
            {"id": "b1", "from": "2026-08-13T11:00:00Z", "to": "2026-08-14T11:00:00Z"},
            {"id": "b2", "from": "2026-08-14T11:00:00Z", "to": "2026-08-16T10:00:00Z"},
        ],
        "execution": {
            "proxy_version": "next-hour-fixed-exit-v1",
            "entry_lag_hours": 1,
            "non_overlap": "one_open_episode_per_token",
        },
        "cost_scenarios": [
            {"id": "base", "per_side_bps": 100},
            {"id": "stress", "per_side_bps": 250},
        ],
        "theories": [{
            "id": "entry-v1",
            "role": "entry",
            "objective": "positive_return",
            "holding_period_hours": 4,
            "all": [{"feature": "market_phase_4h", "operator": "eq", "value": "markup", "lag_hours": 0}],
        }],
        "comparisons": [],
        "blocked_theories": [],
        "paper_feasibility_gates": {
            "entry_min_events": 5,
            "entry_min_tokens": 3,
            "entry_min_positive_blocks": 2,
            "veto_min_events": 3,
            "veto_min_tokens": 3,
        },
        "prospective_advancement_gates": {
            "min_calendar_weeks": 8,
            "min_fills": 100,
            "min_tokens": 20,
            "min_fill_rate": 0.70,
            "max_token_pnl_contribution": 0.20,
        },
    }


def _bundle(tmp_path: Path) -> tuple[Path, dict]:
    experiments = tmp_path / "experiments"
    shutil.copytree(PILOT, experiments / PILOT.name)
    shutil.copytree(SHADOW, experiments / SHADOW.name)
    root = experiments / "fixture-evaluation"
    root.mkdir()
    manifest = _manifest(experiments)
    path = root / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path, manifest


def _write(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, indent=2) + "\n")


def test_load_evaluation_manifest_binds_valid_signal_lineage(tmp_path):
    path, _ = _bundle(tmp_path)
    bundle = load_evaluation_manifest(path)
    assert bundle.experiment_id == "fixture-evaluation"
    assert bundle.source_bundle.manifest["schema_version"] == 2
    assert bundle.source_bundle.manifest["point_in_time_guarantee"] == "unknown"
    assert bundle.theories[0].predicates[0].feature == "market_phase_4h"
    assert bundle.costs[0].per_side_bps == 100.0


@pytest.mark.parametrize("mutation, message", [
    (lambda m: m.update(schema_version=4), "unsupported evaluation schema"),
    (lambda m: m.update(status="holdout"), "unknown point-in-time source"),
    (lambda m: m.update(source_signal_manifest_sha256="0" * 64), "source signal manifest checksum"),
    (lambda m: m["cost_scenarios"].append({"id": "base", "per_side_bps": 1}), "duplicate cost scenario"),
    (lambda m: m["time_blocks"].append({"id": "overlap", "from": "2026-08-14T00:00:00Z", "to": "2026-08-15T00:00:00Z"}), "time blocks must be ordered and non-overlapping"),
])
def test_load_evaluation_manifest_rejects_invalid_contract(tmp_path, mutation, message):
    path, manifest = _bundle(tmp_path)
    mutation(manifest)
    _write(path, manifest)
    with pytest.raises(EvaluationError, match=message):
        load_evaluation_manifest(path)


@pytest.mark.parametrize(
    ("scenario_id", "per_side_bps"),
    [("base", 99), ("stress", 251)],
)
def test_load_evaluation_manifest_requires_frozen_cost_values(
    tmp_path, scenario_id, per_side_bps
):
    path, manifest = _bundle(tmp_path)
    next(item for item in manifest["cost_scenarios"] if item["id"] == scenario_id)[
        "per_side_bps"
    ] = per_side_bps
    _write(path, manifest)
    with pytest.raises(EvaluationError, match=rf"cost scenario {scenario_id} must be exactly"):
        load_evaluation_manifest(path)


@pytest.mark.parametrize("mutation, message", [
    (lambda m: m.pop("title"), "missing keys: title"),
    (lambda m: m.update(unexpected=True), "unknown keys: unexpected"),
    (lambda m: m["evaluation_window"].update({"from": "2026-08-13T11:00:00"}), "timezone-aware"),
    (lambda m: m["time_blocks"][0].update({"from": "2026-08-13T10:00:00Z"}), "within evaluation window"),
    (lambda m: m["cost_scenarios"].__setitem__(0, {"id": "other", "per_side_bps": 100}), "exactly base and stress"),
    (lambda m: m["cost_scenarios"][0].update({"per_side_bps": -1}), "finite non-negative"),
    (lambda m: m["cost_scenarios"][0].update({"per_side_bps": float("inf")}), "finite non-negative"),
    (lambda m: m["theories"].append(copy.deepcopy(m["theories"][0])), "duplicate theory id"),
    (lambda m: m["theories"][0].update({"role": "other"}), "invalid theory role"),
    (lambda m: m["theories"][0].update({"objective": "other"}), "invalid theory objective"),
    (lambda m: m["theories"][0].update({"holding_period_hours": 0}), "positive integer"),
    (lambda m: m["paper_feasibility_gates"].update({"entry_min_events": -1}), "feasibility gate"),
    (lambda m: m["prospective_advancement_gates"].update({"min_fill_rate": 2}), "advancement gate"),
])
def test_load_evaluation_manifest_rejects_malformed_contract(tmp_path, mutation, message):
    path, manifest = _bundle(tmp_path)
    mutation(manifest)
    _write(path, manifest)
    with pytest.raises(EvaluationError, match=message):
        load_evaluation_manifest(path)


def test_load_evaluation_manifest_rejects_source_outside_trusted_sibling_root(tmp_path):
    path, manifest = _bundle(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    manifest["source_signal_manifest"] = "../../outside.json"
    manifest["source_signal_manifest_sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
    _write(path, manifest)
    with pytest.raises(EvaluationError, match="source manifest must be a sibling bundle"):
        load_evaluation_manifest(path)


def test_load_evaluation_manifest_rejects_evaluation_manifest_symlink_escape(tmp_path):
    external_path, _ = _bundle(tmp_path / "external")
    trusted_experiments = tmp_path / "trusted" / "experiments"
    trusted_experiments.mkdir(parents=True)
    linked_bundle = trusted_experiments / "linked-evaluation"
    linked_bundle.symlink_to(external_path.parent, target_is_directory=True)

    with pytest.raises(EvaluationError, match="evaluation manifest.*trusted experiments root"):
        load_evaluation_manifest(linked_bundle / "manifest.json")


def test_load_evaluation_manifest_rejects_symlinked_source(tmp_path):
    path, manifest = _bundle(tmp_path)
    source = path.parent.parent / "2026-08-16-community-signal-shadow" / "manifest.json"
    external = tmp_path / "external-manifest.json"
    shutil.copy2(source, external)
    source.unlink()
    source.symlink_to(external)
    manifest["source_signal_manifest_sha256"] = hashlib.sha256(external.read_bytes()).hexdigest()
    _write(path, manifest)
    with pytest.raises(EvaluationError, match="real file|symlink"):
        load_evaluation_manifest(path)


def test_load_evaluation_manifest_rejects_non_scalar_or_invalid_predicates(tmp_path):
    path, manifest = _bundle(tmp_path)
    feature = manifest["theories"][0]["all"][0]
    feature.update({"feature": "token_address"})
    _write(path, manifest)
    with pytest.raises(EvaluationError, match="predicate feature"):
        load_evaluation_manifest(path)

    path, manifest = _bundle(tmp_path / "second")
    feature = manifest["theories"][0]["all"][0]
    feature.update({"operator": "in", "value": []})
    _write(path, manifest)
    with pytest.raises(EvaluationError, match="non-empty unique scalar"):
        load_evaluation_manifest(path)


def test_load_evaluation_manifest_preserves_structured_blocked_theory_reason(tmp_path):
    path, manifest = _bundle(tmp_path)
    manifest["blocked_theories"] = [{
        "id": "buyer-breadth-exchange-confirmation-v1",
        "reason": "frozen evidence lacks point-in-time buyer and exchange flow history",
        "missing_roles": ["wallet_buyer_breadth", "exchange_labelled_flow"],
    }]
    _write(path, manifest)

    bundle = load_evaluation_manifest(path)

    assert bundle.blocked_theories == (
        BlockedTheorySpec(
            id="buyer-breadth-exchange-confirmation-v1",
            reason="frozen evidence lacks point-in-time buyer and exchange flow history",
            missing_roles=("wallet_buyer_breadth", "exchange_labelled_flow"),
        ),
    )


@pytest.mark.parametrize(
    "blocked, message",
    [
        (["buyer-breadth-exchange-confirmation-v1"], "blocked theory"),
        ([{"id": "h5", "reason": "missing", "missing_roles": []}], "missing_roles"),
        ([{"id": "h5", "reason": "", "missing_roles": ["wallet_buyer_breadth"]}], "reason"),
        ([{"id": "h5", "reason": "missing", "missing_roles": ["wallet_buyer_breadth", "wallet_buyer_breadth"]}], "unique"),
        ([{"id": "h5", "reason": "missing", "missing_roles": ["wallet_buyer_breadth"], "extra": True}], "unknown keys"),
    ],
)
def test_load_evaluation_manifest_rejects_unstructured_or_malformed_blocked_theories(
    tmp_path, blocked, message
):
    path, manifest = _bundle(tmp_path)
    manifest["blocked_theories"] = blocked
    _write(path, manifest)
    with pytest.raises(EvaluationError, match=message):
        load_evaluation_manifest(path)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_load_evaluation_manifest_rejects_nonfinite_predicate_numbers(tmp_path, value):
    path, manifest = _bundle(tmp_path)
    manifest["theories"][0]["all"][0].update({"operator": "gt", "value": value})
    _write(path, manifest)
    with pytest.raises(EvaluationError, match="finite"):
        load_evaluation_manifest(path)


def test_load_evaluation_manifest_rejects_invalid_predicate_lag_and_operator(tmp_path):
    path, manifest = _bundle(tmp_path)
    predicate = manifest["theories"][0]["all"][0]
    predicate.update({"operator": "contains"})
    _write(path, manifest)
    with pytest.raises(EvaluationError, match="predicate operator"):
        load_evaluation_manifest(path)

    path, manifest = _bundle(tmp_path / "second")
    manifest["theories"][0]["all"][0]["lag_hours"] = True
    _write(path, manifest)
    with pytest.raises(EvaluationError, match="lag_hours"):
        load_evaluation_manifest(path)


@pytest.mark.parametrize("value", [True, "positive", float("nan"), float("inf")])
def test_load_evaluation_manifest_requires_numeric_ordered_predicates(tmp_path, value):
    """Fails if an ordered predicate can compare categorical or non-finite values."""
    path, manifest = _bundle(tmp_path)
    manifest["theories"][0]["all"][0].update({"operator": "gt", "value": value})
    _write(path, manifest)
    with pytest.raises(EvaluationError, match="ordered predicate value"):
        load_evaluation_manifest(path)


def test_load_evaluation_manifest_accepts_huge_integer_ordered_predicate(tmp_path):
    """Fails if finite integer thresholds are unnecessarily converted to float."""
    path, manifest = _bundle(tmp_path)
    huge = 10**400
    manifest["theories"][0]["all"][0].update({"operator": "gt", "value": huge})
    _write(path, manifest)
    assert load_evaluation_manifest(path).theories[0].predicates[0].value == huge


@pytest.mark.parametrize(
    ("role", "objective"),
    [
        ("veto", "positive_return"),
        ("entry", "avoided_loss"),
        ("reference", "avoided_loss"),
        ("comparison", "avoided_loss"),
    ],
)
def test_load_evaluation_manifest_rejects_role_objective_mismatches(tmp_path, role, objective):
    """Fails if a theory role can silently invert its declared objective."""
    path, manifest = _bundle(tmp_path)
    manifest["theories"][0].update({"role": role, "objective": objective})
    _write(path, manifest)
    with pytest.raises(EvaluationError, match="requires objective"):
        load_evaluation_manifest(path)


def test_load_evaluation_manifest_rejects_blocked_theory_that_is_evaluable(tmp_path):
    """Fails if a blocked theory can still reach the event-building input."""
    path, manifest = _bundle(tmp_path)
    manifest["blocked_theories"] = [{
        "id": "entry-v1",
        "reason": "missing required point-in-time evidence",
        "missing_roles": ["wallet_buyer_breadth"],
    }]
    _write(path, manifest)
    with pytest.raises(EvaluationError, match="must not also be evaluable"):
        load_evaluation_manifest(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda manifest: manifest["comparisons"].append({
                "id": "broken-comparison",
                "positive_arm": "missing-theory",
                "reference_arm": "entry-v1",
            }),
            "must reference existing theories",
        ),
        (
            lambda manifest: manifest["comparisons"].append({
                "id": "broken-comparison",
                "positive_arm": "entry-v1",
                "reference_arm": "entry-v1",
            }),
            "must use distinct arms",
        ),
        (
            lambda manifest: manifest["comparisons"].append({
                "id": "broken-comparison",
                "positive_arm": "entry-v1",
                "reference_arm": "comparison-v1",
            }),
            "must reference comparison-role theories",
        ),
    ],
)
def test_load_evaluation_manifest_rejects_invalid_comparison_arm_references(
    tmp_path, mutate, message
):
    """Fails if malformed comparison references become plausible empty evidence."""
    path, manifest = _bundle(tmp_path)
    manifest["theories"].append({
        "id": "comparison-v1",
        "role": "comparison",
        "objective": "positive_return",
        "holding_period_hours": 4,
        "all": [{
            "feature": "market_phase_4h",
            "operator": "eq",
            "value": "markup",
            "lag_hours": 0,
        }],
    })
    mutate(manifest)
    _write(path, manifest)

    with pytest.raises(EvaluationError, match=message):
        load_evaluation_manifest(path)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda manifest: manifest["time_blocks"][0].update({"from": "2026-08-13T12:00:00Z"}),
            "contiguous full partition",
        ),
        (
            lambda manifest: manifest["time_blocks"][1].update({"from": "2026-08-14T12:00:00Z"}),
            "contiguous full partition",
        ),
        (
            lambda manifest: manifest["time_blocks"][1].update({"to": "2026-08-16T09:00:00Z"}),
            "contiguous full partition",
        ),
    ],
)
def test_load_evaluation_manifest_requires_complete_contiguous_time_blocks(tmp_path, mutation, message):
    """Fails if an unassigned portion of the evaluation window is omitted from blocks."""
    path, manifest = _bundle(tmp_path)
    mutation(manifest)
    _write(path, manifest)
    with pytest.raises(EvaluationError, match=message):
        load_evaluation_manifest(path)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _timestamp(hour: int) -> str:
    return (datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(hours=hour)).isoformat().replace("+00:00", "Z")


def _entry_theory(*, holding_period_hours: int = 4) -> TheorySpec:
    return TheorySpec(
        id="entry-v1",
        role="entry",
        objective="positive_return",
        holding_period_hours=holding_period_hours,
        predicates=(Predicate("market_phase_4h", "eq", "markup", 0),),
    )


def _veto_theory() -> TheorySpec:
    return TheorySpec(
        id="veto-v1",
        role="veto",
        objective="avoided_loss",
        holding_period_hours=4,
        predicates=(Predicate("market_phase_4h", "eq", "distribution_divergence", 0),),
    )


def _literal_signal_rows(*, second_token: bool = False) -> tuple[dict, ...]:
    rows = tuple(
        {
            "chain": "base",
            "symbol": "X",
            "token_address": "0xtoken",
            "timestamp": _timestamp(hour),
            "market_phase_4h": "markup",
        }
        for hour in range(9)
    )
    if not second_token:
        return rows
    return rows + ({
        "chain": "base",
        "symbol": "Y",
        "token_address": "0xother",
        "timestamp": _timestamp(1),
        "market_phase_4h": "markup",
    },)


def _literal_source_rows(*, prices: dict[int, float] | None = None, second_token: bool = False) -> tuple[dict, ...]:
    prices = prices or {}
    rows = tuple(
        {
            "chain": "base",
            "symbol": "X",
            "address": "0xtoken",
            "timestamp": _timestamp(hour),
            "price_usd": prices.get(hour, 100.0),
        }
        for hour in range(9)
    )
    if not second_token:
        return rows
    return rows + tuple({
        "chain": "base",
        "symbol": "Y",
        "address": "0xother",
        "timestamp": _timestamp(hour),
        "price_usd": 100.0,
    } for hour in range(9))


_COSTS = (CostScenario("base", 100), CostScenario("stress", 250))
_WINDOW_START = _utc("2026-08-01T00:00:00Z")
_WINDOW_END = _utc("2026-08-02T00:00:00Z")
_BLOCK = TimeBlock("b", _WINDOW_START, _WINDOW_END)


def _events(signal_rows, source_rows, theories):
    return build_theory_events(
        signal_rows=signal_rows,
        source_rows=source_rows,
        theories=theories,
        evaluation_id="e",
        evaluation_start=_WINDOW_START,
        evaluation_end=_WINDOW_END,
        blocks=(_BLOCK,),
        entry_lag_hours=1,
        costs=_COSTS,
    )


def test_entry_objective_applies_two_multiplicative_sides():
    """Fails if entry and exit costs are added rather than compounded."""
    assert entry_objective_pct(10.0, 100) == pytest.approx(7.811)


def test_veto_objective_subtracts_exit_and_reentry_costs():
    """Fails if a veto omits either simulated exit or re-entry cost."""
    assert veto_objective_pct(-5.0, 100) == pytest.approx(3.0)


def test_exact_lag_predicate_rejects_missing_prior_hour():
    """Fails if absent exact-lag rows are treated as matching history."""
    predicate = Predicate("holdings_acceleration_4h_pct_per_hour", "lte", 0, 1)
    assert predicate_matches(
        predicate,
        current={"timestamp": "2026-08-01T02:00:00Z"},
        by_timestamp={},
    ) is False


@pytest.mark.parametrize(
    ("operator", "value", "actual", "expected"),
    [
        ("eq", 2, 2, True),
        ("in", (1, 2), 2, True),
        ("gt", 2, 3, True),
        ("gte", 2, 2, True),
        ("lt", 3, 2, True),
        ("lte", 2, 2, True),
        ("gt", 3, 2, False),
    ],
)
def test_predicate_matches_all_manifest_operators(operator, value, actual, expected):
    """Fails if a permitted manifest operator is applied with reversed semantics."""
    timestamp = _utc("2026-08-01T01:00:00Z")
    predicate = Predicate("holdings_acceleration_4h_pct_per_hour", operator, value, 0)
    assert predicate_matches(
        predicate,
        current={"timestamp": "2026-08-01T01:00:00Z"},
        by_timestamp={timestamp: {"holdings_acceleration_4h_pct_per_hour": actual}},
    ) is expected


def test_predicate_matches_rejects_unavailable_value():
    """Fails if unavailable trailing features become an implicit match."""
    timestamp = _utc("2026-08-01T01:00:00Z")
    predicate = Predicate("holdings_acceleration_4h_pct_per_hour", "gt", 0, 0)
    assert not predicate_matches(
        predicate,
        current={"timestamp": "2026-08-01T01:00:00Z"},
        by_timestamp={timestamp: {"holdings_acceleration_4h_pct_per_hour": None}},
    )


@pytest.mark.parametrize(
    ("operator", "value", "actual", "expected"),
    [
        ("eq", True, 1, False),
        ("eq", 1, True, False),
        ("in", (True,), 1, False),
        ("in", (1,), True, False),
        ("eq", 1, 1.0, True),
        ("in", (1.0,), 1, True),
    ],
)
def test_predicate_matches_keeps_boolean_and_numeric_scalars_disjoint(operator, value, actual, expected):
    """Fails if Python bool-as-int equality lets a boolean bypass a numeric predicate."""
    timestamp = _utc("2026-08-01T01:00:00Z")
    predicate = Predicate("holdings_acceleration_4h_pct_per_hour", operator, value, 0)
    assert predicate_matches(
        predicate,
        current={"timestamp": "2026-08-01T01:00:00Z"},
        by_timestamp={timestamp: {"holdings_acceleration_4h_pct_per_hour": actual}},
    ) is expected


@pytest.mark.parametrize(
    ("operator", "value", "actual", "expected"),
    [
        ("gt", False, True, False),
        ("lt", "z", "a", False),
        ("gte", 1.0, 1, True),
        ("lt", 2.0, 1, True),
    ],
)
def test_ordered_predicates_require_nonboolean_numeric_operands(operator, value, actual, expected):
    """Fails if ordered predicates use Python boolean or lexical ordering."""
    timestamp = _utc("2026-08-01T01:00:00Z")
    predicate = Predicate("holdings_acceleration_4h_pct_per_hour", operator, value, 0)
    assert predicate_matches(
        predicate,
        current={"timestamp": "2026-08-01T01:00:00Z"},
        by_timestamp={timestamp: {"holdings_acceleration_4h_pct_per_hour": actual}},
    ) is expected


def test_ordered_predicate_matches_huge_integers_without_float_overflow():
    """Fails if runtime ordered matching converts finite integers to float."""
    timestamp = _utc("2026-08-01T01:00:00Z")
    huge = 10**400
    predicate = Predicate("holdings_acceleration_4h_pct_per_hour", "gt", huge, 0)
    assert predicate_matches(
        predicate,
        current={"timestamp": "2026-08-01T01:00:00Z"},
        by_timestamp={timestamp: {"holdings_acceleration_4h_pct_per_hour": huge + 1}},
    )


def test_build_theory_events_enters_next_hour_and_exits_after_fixed_horizon():
    """Fails if an event uses the signal-hour price or an off-by-one fixed exit."""
    events = _events(
        (next(row for row in _literal_signal_rows() if row["timestamp"] == _timestamp(1)),),
        _literal_source_rows(prices={2: 100.0, 6: 110.0}),
        (_entry_theory(),),
    )
    assert events == ({
        "evaluation_id": "e",
        "theory_id": "entry-v1",
        "theory_role": "entry",
        "block_id": "b",
        "chain": "base",
        "symbol": "X",
        "token_address": "0xtoken",
        "signal_timestamp": "2026-08-01T01:00:00Z",
        "entry_timestamp": "2026-08-01T02:00:00Z",
        "exit_timestamp": "2026-08-01T06:00:00Z",
        "holding_period_hours": 4,
        "gross_return_pct": 10.0,
        "gross_objective_pct": 10.0,
        "base_objective_pct": pytest.approx(7.811),
        "stress_objective_pct": pytest.approx(4.56875),
    },)


def test_build_theory_events_preserves_signal_only_predicates_despite_label_poison():
    """Fails if an outcome-side label field is read to choose an event."""
    signals = list(_literal_signal_rows())
    signals[0]["forward_price_return_4h_pct"] = {"poison": "outcome-side"}
    assert _events(tuple(signals), _literal_source_rows(), (_entry_theory(),))


@pytest.mark.parametrize(
    "source_rows",
    [
        tuple(row for row in _literal_source_rows() if row["timestamp"] != _timestamp(1)),
        tuple(row for row in _literal_source_rows() if row["timestamp"] != _timestamp(5)),
        _literal_source_rows(prices={1: 0.0}),
        _literal_source_rows(prices={5: float("inf")}),
    ],
)
def test_build_theory_events_skips_missing_or_unexecutable_prices(source_rows):
    """Fails if missing, zero, or non-finite exact execution prices create an episode."""
    signals = (next(row for row in _literal_signal_rows() if row["timestamp"] == _timestamp(0)),)
    assert _events(signals, source_rows, (_entry_theory(),)) == ()


def test_build_theory_events_rejects_an_intermediate_hourly_price_gap():
    """Fails if exact endpoints bridge a missing hourly execution history row."""
    signals = (next(row for row in _literal_signal_rows() if row["timestamp"] == _timestamp(0)),)
    source_rows = tuple(
        row for row in _literal_source_rows() if row["timestamp"] != _timestamp(3)
    )

    assert _events(signals, source_rows, (_entry_theory(),)) == ()


def test_build_theory_events_excludes_window_outside_and_censored_signals():
    """Fails if signals at the end boundary or after a censored exit are retained."""
    signals = (
        {**_literal_signal_rows()[0], "timestamp": _timestamp(24)},
        {**_literal_signal_rows()[0], "timestamp": _timestamp(23)},
    )
    source_rows = _literal_source_rows() + (
        {"chain": "base", "symbol": "X", "address": "0xtoken", "timestamp": _timestamp(24), "price_usd": 100.0},
        {"chain": "base", "symbol": "X", "address": "0xtoken", "timestamp": _timestamp(28), "price_usd": 110.0},
    )
    assert _events(signals, source_rows, (_entry_theory(),)) == ()


def test_build_theory_events_uses_exact_block_and_deterministic_theory_token_order():
    """Fails if block assignment is boundary-inclusive at the end or output order is input order."""
    first = TimeBlock("first", _WINDOW_START, _utc("2026-08-01T01:00:00Z"))
    second = TimeBlock("second", _utc("2026-08-01T01:00:00Z"), _WINDOW_END)
    events = build_theory_events(
        signal_rows=tuple(reversed(_literal_signal_rows(second_token=True))),
        source_rows=_literal_source_rows(second_token=True),
        theories=(TheorySpec("z", "entry", "positive_return", 4, _entry_theory().predicates), TheorySpec("a", "entry", "positive_return", 4, _entry_theory().predicates)),
        evaluation_id="e",
        evaluation_start=_WINDOW_START,
        evaluation_end=_WINDOW_END,
        blocks=(first, second),
        entry_lag_hours=1,
        costs=_COSTS,
    )
    assert [(event["theory_id"], event["token_address"], event["block_id"]) for event in events[:4]] == [
        ("a", "0xtoken", "first"),
        ("a", "0xother", "second"),
        ("z", "0xtoken", "first"),
        ("z", "0xother", "second"),
    ]


def test_build_theory_events_uses_veto_objective():
    """Fails if a veto uses the long-return rather than avoided-loss objective."""
    signal = {**_literal_signal_rows()[0], "market_phase_4h": "distribution_divergence"}
    events = _events((signal,), _literal_source_rows(prices={1: 100, 5: 95}), (_veto_theory(),))
    assert events[0]["gross_return_pct"] == pytest.approx(-5.0)
    assert events[0]["gross_objective_pct"] == pytest.approx(5.0)
    assert events[0]["base_objective_pct"] == pytest.approx(3.0)


def test_build_theory_events_enforces_same_theory_cooldown_but_allows_exit_boundary():
    """Fails if cooldown is theory-agnostic, skips another token, or excludes the exit boundary."""
    signals = tuple(
        row
        for row in _literal_signal_rows(second_token=True)
        if (row["token_address"] == "0xtoken" and row["timestamp"] in {_timestamp(0), _timestamp(1), _timestamp(5)})
        or (row["token_address"] == "0xother" and row["timestamp"] == _timestamp(1))
    )
    source_rows = _literal_source_rows(second_token=True) + tuple(
        {
            "chain": "base",
            "symbol": "X",
            "address": "0xtoken",
            "timestamp": _timestamp(hour),
            "price_usd": 100.0,
        }
        for hour in (9, 10)
    )
    events = _events(signals, source_rows, (_entry_theory(),))
    assert [(event["token_address"], event["signal_timestamp"]) for event in events] == [
        ("0xtoken", _timestamp(0)),
        ("0xother", _timestamp(1)),
        ("0xtoken", _timestamp(5)),
    ]


def _theory_record(theory: TheorySpec) -> dict:
    return {
        "id": theory.id,
        "role": theory.role,
        "objective": theory.objective,
        "holding_period_hours": theory.holding_period_hours,
        "all": [
            {
                "feature": predicate.feature,
                "operator": predicate.operator,
                "value": list(predicate.value) if predicate.operator == "in" else predicate.value,
                "lag_hours": predicate.lag_hours,
            }
            for predicate in theory.predicates
        ],
    }


def _summary_bundle(
    *,
    theories: tuple[TheorySpec, ...] | None = None,
    blocks: tuple[TimeBlock, ...] | None = None,
    comparisons: tuple[ComparisonSpec, ...] = (),
    blocked: tuple[BlockedTheorySpec, ...] = (),
) -> EvaluationBundle:
    theories = theories or (_entry_theory(),)
    blocks = blocks or (
        TimeBlock("b1", _WINDOW_START, _utc("2026-08-01T12:00:00Z")),
        TimeBlock("b2", _utc("2026-08-01T12:00:00Z"), _WINDOW_END),
    )
    manifest = {
        "experiment_id": "e",
        "status": "discovery",
        "execution": {
            "proxy_version": "next-hour-fixed-exit-v1",
            "entry_lag_hours": 1,
            "non_overlap": "one_open_episode_per_token",
        },
        "evaluation_window": {
            "from": "2026-08-01T00:00:00Z",
            "to": "2026-08-02T00:00:00Z",
        },
        "theories": [_theory_record(theory) for theory in theories],
        "paper_feasibility_gates": {
            "entry_min_events": 5,
            "entry_min_tokens": 3,
            "entry_min_positive_blocks": 2,
            "veto_min_events": 3,
            "veto_min_tokens": 3,
        },
        "prospective_advancement_gates": {
            "min_calendar_weeks": 8,
            "min_fills": 100,
            "min_tokens": 20,
            "min_fill_rate": 0.70,
            "max_token_pnl_contribution": 0.20,
        },
    }
    source_bundle = SimpleNamespace(
        experiment_id="signal-source",
        manifest={
            "experiment_id": "signal-source",
            "point_in_time_guarantee": "unknown",
        },
        source_bundle=None,
    )
    return EvaluationBundle(
        root=Path("/tmp/e"),
        manifest_path=Path("/tmp/e/manifest.json"),
        manifest=manifest,
        source_bundle=source_bundle,
        theories=theories,
        blocks=blocks,
        costs=_COSTS,
        comparisons=comparisons,
        blocked_theories=blocked,
    )


def _summary_event(
    *,
    theory_id: str = "entry-v1",
    role: str = "entry",
    block_id: str = "b1",
    token: str = "0xa",
    gross: float = 2.0,
    base: float = 2.0,
    stress: float = 1.0,
) -> dict:
    return {
        "evaluation_id": "e",
        "theory_id": theory_id,
        "theory_role": role,
        "block_id": block_id,
        "chain": "base",
        "symbol": token.upper(),
        "token_address": token,
        "signal_timestamp": "2026-08-01T01:00:00Z",
        "entry_timestamp": "2026-08-01T02:00:00Z",
        "exit_timestamp": "2026-08-01T06:00:00Z",
        "holding_period_hours": 4,
        "gross_return_pct": gross,
        "gross_objective_pct": gross,
        "base_objective_pct": base,
        "stress_objective_pct": stress,
    }


def _events_for_token_equal_test() -> tuple[dict, ...]:
    return (
        _summary_event(token="0xa", gross=3, base=2, stress=1),
        _summary_event(token="0xa", gross=3, base=2, stress=1),
        _summary_event(token="0xa", gross=3, base=2, stress=1),
        _summary_event(token="0xb", block_id="b2", gross=-1, base=-2, stress=-3),
    )


def test_summary_is_token_equal_not_event_frequency_weighted():
    """Fails if event frequency rather than equal-token means drives the headline."""
    summary = build_theory_summaries(_events_for_token_equal_test(), _summary_bundle())[0]
    assert summary["event_mean_base_objective_pct"] == pytest.approx(1.0)
    assert summary["token_equal_mean_base_objective_pct"] == pytest.approx(0.0)


def test_summary_reports_literal_event_and_concentration_metrics():
    """Fails if medians, wins, token means, or positive-PnL concentration drift."""
    summary = build_theory_summaries(_events_for_token_equal_test(), _summary_bundle())[0]
    assert summary == {
        "evaluation_id": "e",
        "theory_id": "entry-v1",
        "theory_role": "entry",
        "block_id": "all",
        "event_count": 4,
        "token_count": 2,
        "event_mean_gross_objective_pct": pytest.approx(2.0),
        "event_median_gross_objective_pct": pytest.approx(3.0),
        "event_mean_base_objective_pct": pytest.approx(1.0),
        "event_median_base_objective_pct": pytest.approx(2.0),
        "event_mean_stress_objective_pct": pytest.approx(0.0),
        "event_median_stress_objective_pct": pytest.approx(1.0),
        "event_win_rate_base": pytest.approx(0.75),
        "token_equal_mean_gross_objective_pct": pytest.approx(1.0),
        "token_equal_mean_base_objective_pct": pytest.approx(0.0),
        "token_equal_mean_stress_objective_pct": pytest.approx(-1.0),
        "max_token_positive_pnl_contribution": pytest.approx(1.0),
        "gate_status": "ineligible",
        "gate_reason_codes": "insufficient_events;insufficient_positive_blocks;insufficient_tokens;nonpositive_token_equal_mean_base",
    }


def test_summaries_include_every_theory_and_declared_block_even_with_no_events():
    """Fails if sparse theories or blocks disappear from the evidence table."""
    theories = (
        _entry_theory(),
        TheorySpec("reference-v1", "reference", "positive_return", 4, _entry_theory().predicates),
    )
    summaries = build_theory_summaries((), _summary_bundle(theories=theories))
    assert [(row["theory_id"], row["block_id"]) for row in summaries] == [
        ("entry-v1", "all"),
        ("entry-v1", "b1"),
        ("entry-v1", "b2"),
        ("reference-v1", "all"),
        ("reference-v1", "b1"),
        ("reference-v1", "b2"),
    ]
    zero = summaries[1]
    assert zero["event_count"] == 0
    assert zero["token_count"] == 0
    assert all(
        zero[key] is None
        for key in (
            "event_mean_gross_objective_pct",
            "event_median_gross_objective_pct",
            "event_mean_base_objective_pct",
            "event_median_base_objective_pct",
            "event_mean_stress_objective_pct",
            "event_median_stress_objective_pct",
            "event_win_rate_base",
            "token_equal_mean_gross_objective_pct",
            "token_equal_mean_base_objective_pct",
            "token_equal_mean_stress_objective_pct",
            "max_token_positive_pnl_contribution",
        )
    )
    assert zero["gate_status"] == "not_applicable"
    assert zero["gate_reason_codes"] == ""


def _eligible_events(theory_id: str, role: str, count: int) -> tuple[dict, ...]:
    tokens = ("0xa", "0xb", "0xc")
    return tuple(
        _summary_event(
            theory_id=theory_id,
            role=role,
            block_id="b1" if index % 2 == 0 else "b2",
            token=tokens[index % len(tokens)],
            gross=3,
            base=2,
            stress=1,
        )
        for index in range(count)
    )


def test_entry_and_veto_gates_cover_every_success_and_failure_reason():
    """Fails if any frozen feasibility criterion is omitted or role-inverted."""
    entry = TheorySpec("entry-good", "entry", "positive_return", 4, _entry_theory().predicates)
    entry_bad = TheorySpec("entry-bad", "entry", "positive_return", 4, _entry_theory().predicates)
    veto = TheorySpec("veto-good", "veto", "avoided_loss", 4, _veto_theory().predicates)
    veto_bad = TheorySpec("veto-bad", "veto", "avoided_loss", 4, _veto_theory().predicates)
    bad_entry_events = tuple(
        _summary_event(
            theory_id="entry-bad",
            role="entry",
            block_id="b1",
            token="0xa" if index % 2 == 0 else "0xb",
            gross=-1,
            base=-1,
            stress=-2,
        )
        for index in range(4)
    )
    bad_veto_events = tuple(
        _summary_event(
            theory_id="veto-bad",
            role="veto",
            block_id="b1",
            token="0xa" if index % 2 == 0 else "0xb",
            gross=-1,
            base=-1,
            stress=-2,
        )
        for index in range(2)
    )
    summaries = build_theory_summaries(
        _eligible_events("entry-good", "entry", 6)
        + bad_entry_events
        + _eligible_events("veto-good", "veto", 3)
        + bad_veto_events,
        _summary_bundle(theories=(entry, entry_bad, veto, veto_bad)),
    )
    overall = {row["theory_id"]: row for row in summaries if row["block_id"] == "all"}
    assert overall["entry-good"]["gate_status"] == "eligible"
    assert overall["entry-good"]["gate_reason_codes"] == ""
    assert overall["entry-bad"]["gate_reason_codes"] == (
        "insufficient_events;insufficient_positive_blocks;insufficient_tokens;"
        "nonpositive_event_median_base;nonpositive_token_equal_mean_base"
    )
    assert overall["veto-good"]["gate_status"] == "eligible"
    assert overall["veto-good"]["gate_reason_codes"] == ""
    assert overall["veto-bad"]["gate_reason_codes"] == (
        "insufficient_events;insufficient_tokens;nonpositive_event_median_base;"
        "nonpositive_token_equal_mean_base"
    )


def test_reference_and_comparison_arms_are_never_selection_eligible():
    """Fails if benchmark or descriptive comparison roles enter selection."""
    reference = TheorySpec("benchmark", "reference", "positive_return", 4, _entry_theory().predicates)
    comparison = TheorySpec("arm", "comparison", "positive_return", 4, _entry_theory().predicates)
    summaries = build_theory_summaries(
        _eligible_events("benchmark", "reference", 6) + _eligible_events("arm", "comparison", 6),
        _summary_bundle(theories=(reference, comparison)),
    )
    overall = {row["theory_id"]: row for row in summaries if row["block_id"] == "all"}
    assert (overall["benchmark"]["gate_status"], overall["benchmark"]["gate_reason_codes"]) == (
        "ineligible",
        "benchmark_only",
    )
    assert (overall["arm"]["gate_status"], overall["arm"]["gate_reason_codes"]) == (
        "ineligible",
        "comparison_only",
    )


def test_comparison_reports_h3_mean_and_median_spreads_and_conservative_status():
    """Fails if comparison arms are reversed or either required spread is ignored."""
    summaries = (
        {"theory_id": "positive", "block_id": "all", "token_equal_mean_base_objective_pct": 3.5, "event_median_base_objective_pct": 2.0},
        {"theory_id": "reference", "block_id": "all", "token_equal_mean_base_objective_pct": 1.0, "event_median_base_objective_pct": 2.5},
    )
    result = build_comparison_results(
        summaries,
        (ComparisonSpec("holder-breadth-incremental-v1", "positive", "reference"),),
    )
    assert result == ({
        "comparison_id": "holder-breadth-incremental-v1",
        "positive_arm_theory_id": "positive",
        "reference_arm_theory_id": "reference",
        "token_equal_mean_base_spread_pct": pytest.approx(2.5),
        "event_median_base_spread_pct": pytest.approx(-0.5),
        "comparison_status": "does_not_advance",
        "reason_codes": "nonpositive_event_median_spread",
    },)

    missing = ({**summaries[0], "event_median_base_objective_pct": None}, summaries[1])
    assert build_comparison_results(
        missing,
        (ComparisonSpec("holder-breadth-incremental-v1", "positive", "reference"),),
    )[0]["comparison_status"] == "insufficient_evidence"

    advancing = (
        {**summaries[0], "event_median_base_objective_pct": 3.0},
        summaries[1],
    )
    advanced = build_comparison_results(
        advancing,
        (ComparisonSpec("holder-breadth-incremental-v1", "positive", "reference"),),
    )[0]
    assert advanced["comparison_status"] == "advances_for_paper_discovery"
    assert advanced["token_equal_mean_base_spread_pct"] == pytest.approx(2.5)
    assert advanced["event_median_base_spread_pct"] == pytest.approx(0.5)
    assert advanced["reason_codes"] == ""


def _eligible_summary(theory_id: str, role: str, score: float = 2.0) -> dict:
    return {
        "evaluation_id": "e",
        "theory_id": theory_id,
        "theory_role": role,
        "block_id": "all",
        "event_count": 6,
        "token_count": 3,
        "token_equal_mean_base_objective_pct": score,
        "gate_status": "eligible",
        "gate_reason_codes": "",
    }


def test_entry_gate_selects_only_best_eligible_theory_with_lexical_tie_break():
    """Fails if ties are input-order dependent or more than one entry is selected."""
    theories = tuple(
        TheorySpec(theory_id, "entry", "positive_return", 4, _entry_theory().predicates)
        for theory_id in ("entry-b-v1", "entry-a-v1")
    )
    selection = build_paper_selection(
        _summary_bundle(theories=theories),
        (_eligible_summary("entry-b-v1", "entry"), _eligible_summary("entry-a-v1", "entry")),
        (),
    )
    assert selection["selected_entry_theory_id"] == "entry-a-v1"
    assert selection["selection_status"] == "selected_for_paper_discovery"
    assert [item["id"] for item in selection["selected_theories"]] == ["entry-a-v1"]


def test_no_eligible_entry_does_not_force_a_winner_or_an_unpaired_veto():
    """Fails if missing metrics force a candidate or permit a standalone veto."""
    theories = (_entry_theory(), _veto_theory())
    entry = {**_eligible_summary("entry-v1", "entry"), "gate_status": "ineligible", "token_equal_mean_base_objective_pct": None}
    selection = build_paper_selection(
        _summary_bundle(theories=theories),
        (entry, _eligible_summary("veto-v1", "veto")),
        (),
    )
    assert selection["selected_entry_theory_id"] is None
    assert selection["selected_veto_theory_id"] is None
    assert selection["selection_status"] == "no_paper_strategy_selected"
    assert next(item for item in selection["unselected_theories"] if item["id"] == "veto-v1")["reason_codes"] == ["requires_selected_entry"]


def test_paper_selection_preserves_manifest_definitions_policies_and_blocked_reasons():
    """Fails if the paper artifact invents strategy inputs or loses evidence limitations."""
    entry = TheorySpec(
        "entry-v1",
        "entry",
        "positive_return",
        4,
        (Predicate("market_phase_4h", "in", ("markup", "accumulation_divergence"), 0),),
    )
    veto = _veto_theory()
    blocked = (BlockedTheorySpec("h5", "missing point-in-time inputs", ("buyer_breadth", "exchange_flow")),)
    selection = build_paper_selection(
        _summary_bundle(theories=(entry, veto), blocked=blocked),
        (_eligible_summary("entry-v1", "entry", 3), _eligible_summary("veto-v1", "veto", 2)),
        (),
    )
    assert selection["mode"] == "paper_only"
    assert selection["source"] == {
        "signal_experiment_id": "signal-source",
        "point_in_time_guarantee": "unknown",
        "evidence_status": "discovery",
    }
    assert selection["warning"] == "Unvalidated discovery shortlist for prospective paper testing only."
    assert selection["selected_entry_theory_id"] == "entry-v1"
    assert selection["selected_veto_theory_id"] == "veto-v1"
    assert selection["selected_theories"] == [
        _theory_record(entry),
        _theory_record(veto),
    ]
    assert selection["evaluation_execution"] == _summary_bundle().manifest["execution"]
    assert selection["paper_execution_policy"] == {
        "signal_time": "max(bucket_end, provider_available_at)",
        "minimum_quote_delay_minutes": 5,
        "maximum_quote_age_seconds": 60,
        "fixed_exit_hours": 4,
        "non_overlap": "one_open_episode_per_token",
        "virtual_notional_usd": "min(1000, 0.001 * point_in_time_liquidity_usd)",
        "unfilled_conditions": ["missing_route", "one_way_quoted_cost_above_2_5_pct"],
        "recorded_costs": ["fee", "gas", "spread", "slippage"],
        "real_execution_enabled": False,
    }
    for key, value in _summary_bundle().manifest["prospective_advancement_gates"].items():
        assert selection["prospective_advancement_gates"][key] == value
    assert selection["blocked_theories"] == [{
        "id": "h5",
        "reason": "missing point-in-time inputs",
        "missing_roles": ["buyer_breadth", "exchange_flow"],
    }]

    forbidden = ("order", "wallet", "account", "venue", "credential", "submit", "live_trade")

    def visit(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                assert not any(marker in key.lower() for marker in forbidden)
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(selection)


def test_paper_selection_emits_every_mandatory_prospective_advancement_condition():
    """Fails if the artifact reduces advancement to only numeric sample thresholds."""
    selection = build_paper_selection(
        _summary_bundle(),
        (_eligible_summary("entry-v1", "entry"),),
        (),
    )
    assert selection["prospective_advancement_gates"] == {
        "min_calendar_weeks": 8,
        "min_fills": 100,
        "min_tokens": 20,
        "min_fill_rate": 0.70,
        "max_token_pnl_contribution": 0.20,
        "timestamped_quotes_required": True,
        "point_in_time_liquidity_required": True,
        "actual_simulated_cost_mean_must_be_positive": True,
        "actual_simulated_cost_median_must_be_positive": True,
        "token_week_block_bootstrap_lower_one_sided_95_pct_must_be_positive": True,
        "stress_expectancy_must_be_non_negative": True,
    }


def test_build_evaluation_runs_the_real_frozen_lineage_offline(tmp_path):
    """Fails if orchestration skips an evaluation stage or cannot rebuild the source rows."""
    path, _ = _bundle(tmp_path)
    bundle = load_evaluation_manifest(path)
    tables = build_evaluation(bundle)
    assert isinstance(tables, EvaluationTables)
    assert tables.events
    assert tables.summaries == build_theory_summaries(tables.events, bundle)
    assert tables.comparisons == ()
    assert tables.paper_selection["mode"] == "paper_only"


def test_render_evaluation_outputs_has_stable_public_contract(tmp_path):
    """Fails if a public output header, JSON format, or output name drifts."""
    path, _ = _bundle(tmp_path)
    bundle = load_evaluation_manifest(path)

    rendered = render_evaluation_outputs(bundle, build_evaluation(bundle))

    assert tuple(rendered) == (
        "theory-events.csv",
        "theory-summary.csv",
        "paper-strategies.json",
    )
    assert rendered["theory-events.csv"].decode().splitlines()[0].split(",") == [
        "evaluation_id", "theory_id", "theory_role", "block_id", "chain", "symbol",
        "token_address", "signal_timestamp", "entry_timestamp", "exit_timestamp",
        "holding_period_hours", "gross_return_pct", "gross_objective_pct",
        "base_objective_pct", "stress_objective_pct",
    ]
    assert rendered["theory-summary.csv"].decode().splitlines()[0].split(",") == [
        "evaluation_id", "theory_id", "theory_role", "block_id", "event_count",
        "token_count", "event_mean_gross_objective_pct", "event_median_gross_objective_pct",
        "event_mean_base_objective_pct", "event_median_base_objective_pct",
        "event_mean_stress_objective_pct", "event_median_stress_objective_pct",
        "event_win_rate_base", "token_equal_mean_gross_objective_pct",
        "token_equal_mean_base_objective_pct", "token_equal_mean_stress_objective_pct",
        "max_token_positive_pnl_contribution", "gate_status", "gate_reason_codes",
    ]
    paper = rendered["paper-strategies.json"].decode()
    assert paper.endswith("\n")
    assert paper == json.dumps(json.loads(paper), indent=2, sort_keys=True) + "\n"


def test_evaluate_manifest_writes_reproducibly_and_check_detects_one_byte_drift(tmp_path):
    """Fails if a derived output is not reproducible or check ignores byte drift."""
    path, _ = _bundle(tmp_path)

    paths = evaluate_manifest(path)
    first = {item.name: item.read_bytes() for item in paths}
    assert evaluate_manifest(path) == paths
    assert {item.name: item.read_bytes() for item in paths} == first
    assert evaluate_manifest(path, check=True) == paths

    paths[0].write_bytes(first[paths[0].name][:-1] + b"x")
    with pytest.raises(EvaluationError, match="derived output differs"):
        evaluate_manifest(path, check=True)


def test_evaluate_manifest_check_never_creates_derived_outputs(tmp_path):
    """Fails if verification can create a derived directory or an output file."""
    path, _ = _bundle(tmp_path)

    with pytest.raises(EvaluationError, match="derived output differs"):
        evaluate_manifest(path, check=True)

    assert not (path.parent / "derived").exists()
