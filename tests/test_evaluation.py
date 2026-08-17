from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from src.nansen_signal_lab.evaluation import (
    BlockedTheorySpec,
    EvaluationError,
    load_evaluation_manifest,
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
