from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.nansen_signal_lab.evaluation import load_evaluation_manifest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "research/experiments/2026-08-17-paper-strategy-feasibility/manifest.json"
_DEFAULT = object()


def _bundle():
    return load_evaluation_manifest(MANIFEST)


def _features(**changes):
    value = {
        "timestamp": "2026-08-17T10:00:00Z",
        "holdings_change_1h_pct": 1.0,
        "market_phase_4h": "markdown",
        "distribution_persistence_4h": 0.8,
        "holdings_acceleration_4h_pct_per_hour": -1.0,
        "holder_count_change_4h": -1.0,
        "accumulation_persistence_4h": 0.6,
        "holdings_change_4h_pct": 2.0,
        "accumulation_retention_4h": 0.9,
        "market_phase_12h": "markup",
        "accumulation_persistence_12h": 0.6,
        "holder_count_change_12h": 2.0,
        "accumulation_retention_12h": 0.9,
        "holdings_acceleration_12h_pct_per_hour": 1.0,
    }
    value.update(changes)
    return value


def _prior(**changes):
    value = _features(timestamp="2026-08-17T09:00:00Z")
    value.update(changes)
    return value


def _decisions(current=_DEFAULT, prior=_DEFAULT, *, available_at=None):
    from src.nansen_signal_lab.prospective_comparators import evaluate_comparators

    return evaluate_comparators(
        _bundle(),
        _features() if current is _DEFAULT else current,
        _prior(holdings_acceleration_4h_pct_per_hour=1.0) if prior is _DEFAULT else prior,
        available_at=(
            datetime(2026, 8, 17, 10, 37, tzinfo=timezone.utc)
            if available_at is None
            else available_at
        ),
    )


def test_frozen_loader_hashes_before_loading_and_returns_exact_six(tmp_path, monkeypatch):
    import src.nansen_signal_lab.prospective_comparators as comparators

    expected = hashlib.sha256(MANIFEST.read_bytes()).hexdigest()
    calls = []
    real_loader = comparators.load_evaluation_manifest
    monkeypatch.setattr(
        comparators,
        "load_evaluation_manifest",
        lambda path: calls.append(Path(path)) or real_loader(path),
    )
    records = comparators.load_frozen_records(MANIFEST, expected)
    assert len(records) == 6
    assert {record["id"] for record in records} == {
        "flow-only-benchmark-v1",
        "distribution-risk-off-veto-v1",
        "breadth-acceleration-inflection-v1",
        "holder-breadth-positive-arm-v1",
        "holder-breadth-nonpositive-arm-v1",
        "sustained-markup-confirmation-v1",
    }
    assert calls == [MANIFEST]

    changed = tmp_path / "manifest.json"
    changed.write_bytes(MANIFEST.read_bytes() + b" ")
    calls.clear()
    with pytest.raises(comparators.ComparatorError, match="checksum"):
        comparators.load_frozen_records(changed, expected)
    assert calls == []


def test_all_six_base_decisions_and_blocked_record_are_distinct():
    decisions = _decisions()
    bases = [item for item in decisions if item.variant == "base"]
    blocked = [item for item in decisions if item.variant == "blocked"]
    assert len(bases) == 6
    assert len({item.decision_id for item in bases}) == 6
    assert {item.decision_id for item in bases} == {
        f"{theory.id}::base" for theory in _bundle().theories
    }
    assert [(item.theory_id, item.availability, item.action) for item in blocked] == [
        ("buyer-breadth-exchange-confirmation-v1", "BLOCKED", None)
    ]

    by_id = {item.theory_id: item for item in bases}
    assert by_id["flow-only-benchmark-v1"].action == "LONG"
    assert by_id["flow-only-benchmark-v1"].applicable is True
    assert by_id["distribution-risk-off-veto-v1"].action is None
    assert by_id["distribution-risk-off-veto-v1"].veto_triggered is True
    assert by_id["distribution-risk-off-veto-v1"].applicable is False
    assert by_id["breadth-acceleration-inflection-v1"].action == "ABSTAIN"
    assert by_id["breadth-acceleration-inflection-v1"].applicable is False
    assert by_id["holder-breadth-positive-arm-v1"].action == "ABSTAIN"
    assert by_id["holder-breadth-nonpositive-arm-v1"].action == "LONG"
    assert by_id["sustained-markup-confirmation-v1"].action == "LONG"


def test_veto_pairs_are_distinct_and_suppressed_when_veto_fires():
    from src.nansen_signal_lab.prospective_comparators import pair_distribution_veto

    paired = pair_distribution_veto(_decisions())
    base_ids = {item.decision_id for item in paired if item.variant == "base"}
    variants = [item for item in paired if item.variant == "distribution_veto"]
    assert len(variants) == 3
    assert len({item.decision_id for item in paired}) == len(paired)
    assert all(item.decision_id not in base_ids for item in variants)
    assert all("::paired::" in item.decision_id for item in variants)
    assert all(item.action == "ABSTAIN" for item in variants)
    assert all(item.availability == "AVAILABLE" for item in variants)
    assert all(item.applicable is True for item in variants)
    assert all(item.veto_theory_id == "distribution-risk-off-veto-v1" for item in variants)
    assert all(item.veto_triggered is True for item in variants)


def test_clear_veto_preserves_base_long_and_unavailable_veto_propagates():
    from src.nansen_signal_lab.prospective_comparators import pair_distribution_veto

    clear = _features(distribution_persistence_4h=0.2)
    clear_pairs = [
        item for item in pair_distribution_veto(_decisions(current=clear))
        if item.variant == "distribution_veto"
    ]
    assert clear_pairs and all(item.action == "LONG" for item in clear_pairs)
    assert all(item.veto_triggered is False for item in clear_pairs)

    unavailable = _features(distribution_persistence_4h=None)
    unavailable_pairs = [
        item for item in pair_distribution_veto(_decisions(current=unavailable))
        if item.variant == "distribution_veto"
    ]
    assert unavailable_pairs
    assert all(item.action is None for item in unavailable_pairs)
    assert all(item.availability == "UNAVAILABLE" for item in unavailable_pairs)
    assert all(item.applicable is True for item in unavailable_pairs)
    assert all(item.veto_triggered is None for item in unavailable_pairs)


def test_missing_lag_only_makes_the_dependent_theory_unavailable():
    decisions = _decisions(prior=None)
    by_id = {item.theory_id: item for item in decisions if item.variant == "base"}
    assert by_id["breadth-acceleration-inflection-v1"].availability == "UNAVAILABLE"
    assert by_id["breadth-acceleration-inflection-v1"].action is None
    assert by_id["breadth-acceleration-inflection-v1"].applicable is False
    assert by_id["flow-only-benchmark-v1"].availability == "AVAILABLE"
    assert by_id["sustained-markup-confirmation-v1"].availability == "AVAILABLE"


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (_features(holdings_change_1h_pct=None), "flow-only-benchmark-v1"),
        (_features(holdings_change_1h_pct=float("nan")), "flow-only-benchmark-v1"),
        (_features(market_phase_4h="unavailable"), "distribution-risk-off-veto-v1"),
    ],
)
def test_missing_nonfinite_and_unavailable_inputs_never_become_false(current, expected):
    decisions = _decisions(current=current)
    by_id = {item.theory_id: item for item in decisions if item.variant == "base"}
    assert by_id[expected].availability == "UNAVAILABLE"
    assert by_id[expected].action is None


def test_stale_or_future_feature_timestamp_makes_every_evaluable_base_unavailable():
    for timestamp in ("2026-08-17T09:00:00Z", "2026-08-17T11:00:00Z"):
        decisions = _decisions(current=_features(timestamp=timestamp))
        bases = [item for item in decisions if item.variant == "base"]
        assert all(item.availability == "UNAVAILABLE" for item in bases)
        assert all(item.action is None and item.applicable is False for item in bases)


def test_exact_prior_hour_timestamp_is_required():
    prior = _prior(timestamp="2026-08-17T08:59:59Z")
    decisions = _decisions(prior=prior)
    by_id = {item.theory_id: item for item in decisions if item.variant == "base"}
    assert by_id["breadth-acceleration-inflection-v1"].availability == "UNAVAILABLE"
