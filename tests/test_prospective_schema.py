from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

import src.nansen_signal_lab.prospective_schema as prospective_schema
from src.nansen_signal_lab.artifacts import canonical_json_bytes
from src.nansen_signal_lab.budget import BudgetGuard
from src.nansen_signal_lab.prospective_schema import (
    ProspectiveError,
    commit_stage,
    load_prospective_manifest,
    recover_stage_transaction,
    verify_hash_chain,
)


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_NAMES = (
    "2026-08-16-seven-token-pilot",
    "2026-08-16-community-signal-shadow",
    "2026-08-17-paper-strategy-feasibility",
)
DESIGN = "docs/superpowers/specs/2026-08-17-gpt-prospective-pilot-design.md"
CONTRACT = "docs/superpowers/specs/2026-08-17-nansen-api-contract-snapshot.json"
STAGES = (
    "preregistered",
    "snapshot_collected",
    "decision_sealed",
    "entry_observed",
    "settled",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _repo_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    experiments = repo / "research/experiments"
    for name in EXPERIMENT_NAMES:
        shutil.copytree(ROOT / "research/experiments" / name, experiments / name)
    for relative in (DESIGN, CONTRACT):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return repo


def _manifest(repo: Path, experiment_id: str = "fixture-prospective") -> dict:
    root = repo / "research/experiments" / experiment_id
    source = root.parent / "2026-08-17-paper-strategy-feasibility/manifest.json"
    preregistration = root / "preregistration.json"
    _write_json(
        preregistration,
        {
            "schema_version": 1,
            "experiment_id": experiment_id,
            "artifact_written_at": "2026-08-17T09:00:00Z",
        },
    )
    return {
        "schema_version": 4,
        "experiment_id": experiment_id,
        "title": "Prospective fixture",
        "created_at": "2026-08-17T09:00:00Z",
        "hypothesis": "A sealed prospective decision can be evaluated without leakage.",
        "stage": "preregistered",
        "source_strategy_manifest": "../2026-08-17-paper-strategy-feasibility/manifest.json",
        "source_strategy_manifest_sha256": _sha256(source),
        "preregistration_path": "preregistration.json",
        "preregistration_sha256": _sha256(preregistration),
        "design_path": "../../../" + DESIGN,
        "design_sha256": _sha256(repo / DESIGN),
        "nansen_contract_path": "../../../" + CONTRACT,
        "nansen_contract_sha256": _sha256(repo / CONTRACT),
        "max_nansen_calls": 10,
        "max_nansen_credits": 10,
        "budget_root": "budget",
        "seals": [],
        "artifacts": [],
    }


def _bundle(tmp_path: Path):
    repo = _repo_fixture(tmp_path)
    manifest = _manifest(repo)
    path = repo / "research/experiments/fixture-prospective/manifest.json"
    _write_json(path, manifest)
    return load_prospective_manifest(path)


def _stage_files(bundle, stage: str, minute: int):
    written_at = f"2026-08-17T10:{minute:02d}:03Z"
    artifact = bundle.root / "derived" / f"{stage}.json"
    _write_json(
        artifact,
        {
            "schema_version": 1,
            "request_started_at": f"2026-08-17T10:{minute:02d}:00Z",
            "provider_created_at": f"2026-08-17T10:{minute:02d}:01Z",
            "response_retrieved_at": f"2026-08-17T10:{minute:02d}:02Z",
            "artifact_written_at": written_at,
        },
    )
    snapshot = BudgetGuard(bundle.root).snapshot(stage, recorded_at=written_at)
    return artifact, snapshot, f"2026-08-17T10:{minute:02d}:04Z"


def _advance(bundle, target: str, minute: int):
    artifact, snapshot, recorded_at = _stage_files(bundle, target, minute)
    return commit_stage(bundle, target, recorded_at, (artifact,), snapshot)


def _rewrite_last_seal(bundle, mutation) -> None:
    manifest = copy.deepcopy(bundle.manifest)
    reference = manifest["seals"][-1]
    seal_path = bundle.root / reference["path"]
    seal = json.loads(seal_path.read_text())
    mutation(seal, manifest)
    _write_json(seal_path, seal)
    reference["sha256"] = _sha256(seal_path)
    _write_json(bundle.manifest_path, manifest)


def _at_stage(tmp_path: Path, stage: str):
    bundle = _bundle(tmp_path)
    for minute, target in enumerate(STAGES[1:], start=1):
        if bundle.manifest["stage"] == stage:
            break
        bundle = _advance(bundle, target, minute)
    assert bundle.manifest["stage"] == stage
    return bundle


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("preregistered", "snapshot_collected"),
        ("snapshot_collected", "decision_sealed"),
        ("decision_sealed", "entry_observed"),
        ("entry_observed", "settled"),
    ],
)
def test_schema_v4_accepts_only_next_lifecycle_transition(tmp_path, source, target):
    bundle = _at_stage(tmp_path, source)
    updated = _advance(bundle, target, STAGES.index(target))

    reloaded = load_prospective_manifest(updated.manifest_path)
    assert reloaded.manifest["stage"] == target
    assert [item["stage"] for item in reloaded.manifest["seals"]] == list(
        STAGES[1 : STAGES.index(target) + 1]
    )
    verify_hash_chain(reloaded)


def test_schema_v4_allows_unscorable_from_decision_sealed(tmp_path):
    bundle = _at_stage(tmp_path, "decision_sealed")
    updated = _advance(bundle, "unscorable", 8)
    assert updated.manifest["stage"] == "unscorable"
    verify_hash_chain(updated)


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("preregistered", "decision_sealed"),
        ("snapshot_collected", "entry_observed"),
        ("decision_sealed", "snapshot_collected"),
        ("entry_observed", "decision_sealed"),
        ("settled", "unscorable"),
    ],
)
def test_schema_v4_rejects_stage_skips_reversals_and_terminal_changes(
    tmp_path, source, target
):
    bundle = _at_stage(tmp_path, source)
    if target in STAGES and STAGES.index(target) <= STAGES.index(source):
        artifact = bundle.root / "unused-artifact.json"
        snapshot = bundle.root / "unused-snapshot.json"
        recorded_at = "2026-08-17T10:08:04Z"
    else:
        artifact, snapshot, recorded_at = _stage_files(bundle, target, 8)
    with pytest.raises(ProspectiveError, match="transition"):
        commit_stage(bundle, target, recorded_at, (artifact,), snapshot)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda manifest: manifest.update(unexpected=True), "unknown keys"),
        (lambda manifest: manifest.update(max_nansen_calls=11), "must equal 10"),
        (lambda manifest: manifest.update(budget_root="../budget"), "budget_root"),
        (
            lambda manifest: manifest.update(preregistration_path="../preregistration.json"),
            "preregistration",
        ),
    ],
)
def test_schema_v4_rejects_unknown_keys_ceilings_and_path_escapes(
    tmp_path, mutation, message
):
    bundle = _bundle(tmp_path)
    manifest = copy.deepcopy(bundle.manifest)
    mutation(manifest)
    _write_json(bundle.manifest_path, manifest)
    with pytest.raises(ProspectiveError, match=message):
        load_prospective_manifest(bundle.manifest_path)


def test_schema_v4_rejects_symlink_escape(tmp_path):
    bundle = _bundle(tmp_path)
    design = bundle.root.parents[2] / DESIGN
    outside = tmp_path / "outside-design.md"
    shutil.copy2(design, outside)
    design.unlink()
    design.symlink_to(outside)

    with pytest.raises(ProspectiveError, match="symlink"):
        load_prospective_manifest(bundle.manifest_path)


def test_schema_v4_rejects_non_sibling_schema_v3_source(tmp_path):
    bundle = _bundle(tmp_path)
    manifest = copy.deepcopy(bundle.manifest)
    manifest["source_strategy_manifest"] = "../not-the-frozen-source/manifest.json"
    _write_json(bundle.manifest_path, manifest)
    with pytest.raises(ProspectiveError, match="committed direct sibling"):
        load_prospective_manifest(bundle.manifest_path)


def test_schema_v4_rejects_source_hash_drift(tmp_path):
    bundle = _bundle(tmp_path)
    source = bundle.root.parent / "2026-08-17-paper-strategy-feasibility/manifest.json"
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(ProspectiveError, match="source strategy manifest checksum"):
        load_prospective_manifest(bundle.manifest_path)


def test_schema_v4_wraps_malformed_stage_types_in_public_error(tmp_path):
    bundle = _bundle(tmp_path)
    manifest = copy.deepcopy(bundle.manifest)
    manifest["stage"] = []
    _write_json(bundle.manifest_path, manifest)
    with pytest.raises(ProspectiveError, match="stage"):
        load_prospective_manifest(bundle.manifest_path)


def test_commit_wraps_malformed_stage_type_in_public_error(tmp_path):
    bundle = _bundle(tmp_path)
    artifact, snapshot, recorded_at = _stage_files(bundle, "snapshot_collected", 1)
    with pytest.raises(ProspectiveError, match="stage|transition"):
        commit_stage(bundle, [], recorded_at, (artifact,), snapshot)


def test_commit_refuses_unrecorded_exact_seal_collision(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    artifact, snapshot, recorded_at = _stage_files(bundle, "snapshot_collected", 1)

    original_replace = prospective_schema.atomic_replace_bytes
    calls = 0

    def crash_before_manifest(path, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected after seal install")
        return original_replace(path, content)

    monkeypatch.setattr(prospective_schema, "atomic_replace_bytes", crash_before_manifest)
    with pytest.raises(OSError, match="injected"):
        commit_stage(bundle, "snapshot_collected", recorded_at, (artifact,), snapshot)
    marker = bundle.root / ".transactions/stage.json"
    marker.unlink()
    monkeypatch.setattr(prospective_schema, "atomic_replace_bytes", original_replace)

    with pytest.raises(ProspectiveError, match="unrecorded seal"):
        commit_stage(bundle, "snapshot_collected", recorded_at, (artifact,), snapshot)


def test_hash_chain_rejects_prior_seal_hash_drift(tmp_path):
    bundle = _at_stage(tmp_path, "decision_sealed")
    first_seal = bundle.root / bundle.manifest["seals"][0]["path"]
    first_seal.write_bytes(first_seal.read_bytes() + b"\n")
    with pytest.raises(ProspectiveError, match="seal.*checksum|hash chain"):
        load_prospective_manifest(bundle.manifest_path)


def test_hash_chain_rejects_manifest_stage_without_seal(tmp_path):
    bundle = _bundle(tmp_path)
    manifest = copy.deepcopy(bundle.manifest)
    manifest["stage"] = "snapshot_collected"
    _write_json(bundle.manifest_path, manifest)
    with pytest.raises(ProspectiveError, match="stage.*seal"):
        load_prospective_manifest(bundle.manifest_path)


def test_commit_rejects_seal_time_before_prior_seal(tmp_path):
    bundle = _at_stage(tmp_path, "snapshot_collected")
    artifact, snapshot, _ = _stage_files(bundle, "decision_sealed", 2)
    with pytest.raises(ProspectiveError, match="prior seal"):
        commit_stage(
            bundle,
            "decision_sealed",
            "2026-08-17T10:00:00Z",
            (artifact,),
            snapshot,
        )


@pytest.mark.parametrize(
    "field",
    ["request_started_at", "response_retrieved_at", "artifact_written_at"],
)
def test_commit_rejects_seal_time_before_referenced_local_timestamp(tmp_path, field):
    bundle = _bundle(tmp_path)
    artifact, snapshot, _ = _stage_files(bundle, "snapshot_collected", 1)
    document = json.loads(artifact.read_text())
    document[field] = "2026-08-17T10:02:00Z"
    if field == "request_started_at":
        document["response_retrieved_at"] = "2026-08-17T10:02:00Z"
        document["artifact_written_at"] = "2026-08-17T10:02:00Z"
    elif field == "response_retrieved_at":
        document["artifact_written_at"] = "2026-08-17T10:02:00Z"
    _write_json(artifact, document)

    with pytest.raises(ProspectiveError, match="recorded_at.*referenced"):
        commit_stage(
            bundle,
            "snapshot_collected",
            "2026-08-17T10:01:30Z",
            (artifact,),
            snapshot,
        )


def test_commit_rejects_provider_timestamp_after_local_durable_write(tmp_path):
    bundle = _bundle(tmp_path)
    artifact, snapshot, recorded_at = _stage_files(bundle, "snapshot_collected", 1)
    document = json.loads(artifact.read_text())
    document["provider_created_at"] = "2026-08-17T10:01:04Z"
    _write_json(artifact, document)
    with pytest.raises(ProspectiveError, match="provider.*durable"):
        commit_stage(
            bundle, "snapshot_collected", recorded_at, (artifact,), snapshot
        )


def test_commit_rejects_provider_timestamp_after_response_retrieval(tmp_path):
    bundle = _bundle(tmp_path)
    artifact, snapshot, recorded_at = _stage_files(bundle, "snapshot_collected", 1)
    document = json.loads(artifact.read_text())
    document["provider_created_at"] = "2026-08-17T10:01:02.500000Z"
    _write_json(artifact, document)
    with pytest.raises(ProspectiveError, match="internal timestamp reversal"):
        commit_stage(
            bundle, "snapshot_collected", recorded_at, (artifact,), snapshot
        )


def test_commit_rejects_derived_json_without_durable_write_timestamp(tmp_path):
    bundle = _bundle(tmp_path)
    artifact, snapshot, recorded_at = _stage_files(bundle, "snapshot_collected", 1)
    document = json.loads(artifact.read_text())
    del document["artifact_written_at"]
    _write_json(artifact, document)
    with pytest.raises(ProspectiveError, match="artifact_written_at"):
        commit_stage(
            bundle, "snapshot_collected", recorded_at, (artifact,), snapshot
        )


@pytest.mark.parametrize(
    ("name", "missing", "message"),
    [
        ("attempt-1-request.json", "request_started_at", "request_started_at"),
        ("attempt-1-response.json", "response_retrieved_at", "response_retrieved_at"),
    ],
)
def test_commit_requires_role_specific_sidecar_timestamps(
    tmp_path, name, missing, message
):
    bundle = _bundle(tmp_path)
    artifact, snapshot, recorded_at = _stage_files(bundle, "snapshot_collected", 1)
    role_path = bundle.root / "model/pass-1" / name
    document = json.loads(artifact.read_text())
    del document[missing]
    _write_json(role_path, document)
    with pytest.raises(ProspectiveError, match=message):
        commit_stage(
            bundle, "snapshot_collected", recorded_at, (role_path,), snapshot
        )


def test_loader_rejects_noncanonical_stage_budget_snapshot_path(tmp_path):
    bundle = _at_stage(tmp_path, "snapshot_collected")
    original = bundle.root / "budget/snapshots/snapshot_collected.json"
    replacement = bundle.root / "other/snapshot.json"
    replacement.parent.mkdir()
    shutil.copy2(original, replacement)

    def mutate(seal, _manifest):
        seal["budget_snapshot_path"] = "other/snapshot.json"
        seal["budget_snapshot_sha256"] = _sha256(replacement)

    _rewrite_last_seal(bundle, mutate)
    with pytest.raises(ProspectiveError, match="budget snapshot path"):
        load_prospective_manifest(bundle.manifest_path)


def test_loader_rejects_reserved_mutable_artifact_path(tmp_path):
    bundle = _at_stage(tmp_path, "snapshot_collected")
    head = bundle.root / "budget/head.json"

    def mutate(seal, manifest):
        replacement = {"path": "budget/head.json", "sha256": _sha256(head)}
        seal["artifacts"] = [replacement]
        manifest["artifacts"] = [{"stage": "snapshot_collected", **replacement}]

    _rewrite_last_seal(bundle, mutate)
    with pytest.raises(ProspectiveError, match="artifact path is reserved"):
        load_prospective_manifest(bundle.manifest_path)


@pytest.mark.parametrize("boundary", ["marker", "seal", "manifest"])
def test_stage_transaction_recovers_each_crash_boundary(tmp_path, monkeypatch, boundary):
    bundle = _bundle(tmp_path)
    artifact, snapshot, recorded_at = _stage_files(bundle, "snapshot_collected", 1)
    original_write_once = prospective_schema.write_json_once
    original_replace = prospective_schema.atomic_replace_bytes
    original_remove = prospective_schema._remove_transaction_marker

    if boundary == "marker":
        monkeypatch.setattr(
            prospective_schema,
            "write_json_once",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                OSError("injected after marker")
            ),
        )
    elif boundary == "seal":
        calls = 0

        def crash_after_seal(path, content):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected after seal")
            return original_replace(path, content)

        monkeypatch.setattr(prospective_schema, "atomic_replace_bytes", crash_after_seal)
    else:
        monkeypatch.setattr(
            prospective_schema,
            "_remove_transaction_marker",
            lambda path: (_ for _ in ()).throw(OSError("injected after manifest")),
        )

    with pytest.raises(OSError, match="injected"):
        commit_stage(
            bundle, "snapshot_collected", recorded_at, (artifact,), snapshot
        )

    monkeypatch.setattr(prospective_schema, "write_json_once", original_write_once)
    monkeypatch.setattr(prospective_schema, "atomic_replace_bytes", original_replace)
    monkeypatch.setattr(prospective_schema, "_remove_transaction_marker", original_remove)
    recovered = recover_stage_transaction(bundle)
    assert recovered.manifest["stage"] == "snapshot_collected"
    assert not (bundle.root / ".transactions/stage.json").exists()
    verify_hash_chain(recovered)
    assert recover_stage_transaction(recovered) == recovered


def test_commit_rejects_dangling_transaction_marker_symlink(tmp_path):
    bundle = _bundle(tmp_path)
    marker = bundle.root / ".transactions/stage.json"
    marker.parent.mkdir(parents=True)
    marker.symlink_to(bundle.root / "missing-marker.json")
    artifact, snapshot, recorded_at = _stage_files(bundle, "snapshot_collected", 1)
    with pytest.raises(ProspectiveError, match="transaction|marker"):
        commit_stage(
            bundle, "snapshot_collected", recorded_at, (artifact,), snapshot
        )


@pytest.mark.parametrize("changed", ["manifest", "artifact", "budget", "proposed"])
def test_recovery_rejects_changed_transaction_inputs(tmp_path, monkeypatch, changed):
    bundle = _bundle(tmp_path)
    artifact, snapshot, recorded_at = _stage_files(bundle, "snapshot_collected", 1)
    original_write_once = prospective_schema.write_json_once
    monkeypatch.setattr(
        prospective_schema,
        "write_json_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected after marker")),
    )
    with pytest.raises(OSError):
        commit_stage(
            bundle, "snapshot_collected", recorded_at, (artifact,), snapshot
        )
    monkeypatch.setattr(prospective_schema, "write_json_once", original_write_once)

    marker = bundle.root / ".transactions/stage.json"
    if changed == "manifest":
        manifest = json.loads(bundle.manifest_path.read_text())
        manifest["title"] = "changed"
        _write_json(bundle.manifest_path, manifest)
    elif changed == "artifact":
        artifact.write_bytes(artifact.read_bytes() + b"\n")
    elif changed == "budget":
        snapshot.write_bytes(snapshot.read_bytes() + b"\n")
    else:
        transaction = json.loads(marker.read_text())
        transaction["proposed_manifest"]["title"] = "changed"
        _write_json(marker, transaction)

    with pytest.raises(ProspectiveError, match="corrupt|changed|checksum|hash"):
        recover_stage_transaction(bundle)
    assert not (bundle.root / "seals/snapshot.json").exists()


def test_recovery_rejects_coherently_rewritten_noncanonical_snapshot_path(
    tmp_path, monkeypatch
):
    bundle = _bundle(tmp_path)
    artifact, snapshot, recorded_at = _stage_files(bundle, "snapshot_collected", 1)
    original_write_once = prospective_schema.write_json_once
    monkeypatch.setattr(
        prospective_schema,
        "write_json_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected after marker")),
    )
    with pytest.raises(OSError):
        commit_stage(
            bundle, "snapshot_collected", recorded_at, (artifact,), snapshot
        )
    monkeypatch.setattr(prospective_schema, "write_json_once", original_write_once)

    replacement = bundle.root / "other/snapshot.json"
    replacement.parent.mkdir()
    shutil.copy2(snapshot, replacement)
    marker = bundle.root / ".transactions/stage.json"
    transaction = json.loads(marker.read_text())
    transaction["budget_snapshot_path"] = "other/snapshot.json"
    transaction["seal"]["budget_snapshot_path"] = "other/snapshot.json"
    seal_hash = hashlib.sha256(canonical_json_bytes(transaction["seal"])).hexdigest()
    transaction["seal_sha256"] = seal_hash
    transaction["proposed_manifest"]["seals"][-1]["sha256"] = seal_hash
    transaction["proposed_manifest_sha256"] = hashlib.sha256(
        canonical_json_bytes(transaction["proposed_manifest"])
    ).hexdigest()
    _write_json(marker, transaction)

    with pytest.raises(ProspectiveError, match="budget snapshot path"):
        recover_stage_transaction(bundle)
    assert not (bundle.root / "seals/snapshot.json").exists()


def test_recovery_rejects_changed_orphan_seal(tmp_path, monkeypatch):
    bundle = _bundle(tmp_path)
    artifact, snapshot, recorded_at = _stage_files(bundle, "snapshot_collected", 1)
    original_replace = prospective_schema.atomic_replace_bytes
    calls = 0

    def crash_after_seal(path, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected after seal")
        return original_replace(path, content)

    monkeypatch.setattr(prospective_schema, "atomic_replace_bytes", crash_after_seal)
    with pytest.raises(OSError):
        commit_stage(
            bundle, "snapshot_collected", recorded_at, (artifact,), snapshot
        )
    monkeypatch.setattr(prospective_schema, "atomic_replace_bytes", original_replace)
    seal = bundle.root / "seals/snapshot.json"
    seal.write_bytes(seal.read_bytes() + b"\n")

    with pytest.raises(ProspectiveError, match="seal.*corrupt|seal.*checksum"):
        recover_stage_transaction(bundle)


def test_recovery_rejects_changed_prior_seal_before_installing_new_seal(
    tmp_path, monkeypatch
):
    bundle = _at_stage(tmp_path, "snapshot_collected")
    artifact, snapshot, recorded_at = _stage_files(bundle, "decision_sealed", 2)
    original_write_once = prospective_schema.write_json_once
    monkeypatch.setattr(
        prospective_schema,
        "write_json_once",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected after marker")),
    )
    with pytest.raises(OSError):
        commit_stage(bundle, "decision_sealed", recorded_at, (artifact,), snapshot)
    monkeypatch.setattr(prospective_schema, "write_json_once", original_write_once)
    prior_seal = bundle.root / "seals/snapshot.json"
    prior_seal.write_bytes(prior_seal.read_bytes() + b"\n")

    with pytest.raises(ProspectiveError, match="seal.*checksum|hash chain"):
        recover_stage_transaction(bundle)
    assert not (bundle.root / "seals/decision.json").exists()
