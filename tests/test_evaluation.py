from __future__ import annotations

import copy
import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.nansen_signal_lab.evaluation import (
    BlockedTheorySpec,
    CostScenario,
    EvaluationError,
    Predicate,
    TheorySpec,
    TimeBlock,
    build_theory_events,
    entry_objective_pct,
    load_evaluation_manifest,
    predicate_matches,
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
