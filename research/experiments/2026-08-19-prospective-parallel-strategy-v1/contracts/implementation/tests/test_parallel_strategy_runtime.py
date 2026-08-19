from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from programs.nansen_parallel_strategy_v1 import runtime, schema


REPOSITORY = Path(__file__).resolve().parents[1]
CREATED_AT = datetime(2026, 8, 19, 3, 0, tzinfo=timezone.utc)


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    package = REPOSITORY / "programs/nansen_parallel_strategy_v1"
    for source in package.glob("*.py"):
        _copy(source, repo / source.relative_to(REPOSITORY))
    for source in (REPOSITORY / "src/nansen_signal_lab").glob("*.py"):
        _copy(source, repo / source.relative_to(REPOSITORY))
    _copy(REPOSITORY / "programs/__init__.py", repo / "programs/__init__.py")
    _copy(Path(__file__), repo / "tests/test_parallel_strategy_runtime.py")
    _copy(REPOSITORY / "requirements.txt", repo / "requirements.txt")
    _copy(
        REPOSITORY / schema.DESIGN_PATH,
        repo / schema.DESIGN_PATH,
    )
    _copy(
        REPOSITORY / schema.OPENAPI_SOURCE_RELATIVE_PATH,
        repo / schema.OPENAPI_SOURCE_RELATIVE_PATH,
    )
    _copy(
        REPOSITORY / "research/experiments/2026-08-18-historical-theory-discovery-a-v1/contracts/candidates.json",
        repo / "research/experiments/2026-08-18-historical-theory-discovery-a-v1/contracts/candidates.json",
    )
    v1 = repo / schema.V1_PROGRAM_RELATIVE_PATH
    v1.parent.mkdir(parents=True, exist_ok=True)
    v1.write_bytes(
        schema.canonical_json_bytes(
            {
                "schema_version": 1,
                "program_id": schema.V1_PROGRAM_ID,
                "stage": "preregistered",
            }
        )
    )
    return repo


def _git_commit(repo: Path) -> None:
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
    subprocess.run(
        ("git", "config", "user.email", "runtime@example.invalid"),
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ("git", "config", "user.name", "Runtime Test"), cwd=repo, check=True
    )
    subprocess.run(("git", "add", "."), cwd=repo, check=True)
    subprocess.run(
        ("git", "commit", "-q", "-m", "freeze test preregistration"),
        cwd=repo,
        check=True,
    )


def _terminal_v1(repo: Path) -> dict:
    v1_root = (repo / schema.V1_PROGRAM_RELATIVE_PATH).parent
    cycles = []
    for index in range(1, 33):
        root = v1_root / "cycles" / f"cycle-{index:02d}"
        seal = root / "seals/unscorable.json"
        seal.parent.mkdir(parents=True, exist_ok=True)
        seal.write_bytes(
            schema.canonical_json_bytes(
                {
                    "schema_version": 1,
                    "stage": "unscorable",
                    "reason": "synthetic_terminal",
                }
            )
        )
        state = {
            "schema_version": 1,
            "cycle_index": index,
            "stage": "unscorable",
            "terminal_reason": "synthetic_terminal",
            "seals": [
                {
                    "path": "seals/unscorable.json",
                    "sha256": schema.sha256_file(seal),
                    "stage": "unscorable",
                }
            ],
        }
        (root / "state.json").write_bytes(schema.canonical_json_bytes(state))
        cycles.append(
            {
                "cycle_index": index,
                "stage": "unscorable",
                "terminal_reason": "synthetic_terminal",
                "attempts": 2 if index == 1 else 0,
                "credits": 1 if index == 1 else 0,
                "provider_remaining": 50_063 if index == 1 else None,
            }
        )
    return {
        "schema_version": 1,
        "program_id": schema.V1_PROGRAM_ID,
        "cycles": cycles,
        "terminal_cycles": 32,
        "authenticated_attempts": 2,
        "credits": 1,
        "authorized_credit_ceiling_breached": False,
        # The transition must never export arbitrary result content.
        "rules": [{"secret_metric": 123.0}],
    }


def test_immutable_write_installs_only_complete_bytes(tmp_path, monkeypatch):
    path = tmp_path / "sealed/artifact.json"
    content = b'{"complete":true}\n'
    real_link = schema.os.link

    def fail_install(*args, **kwargs):
        raise OSError("simulated atomic install failure")

    monkeypatch.setattr(schema.os, "link", fail_install)
    with pytest.raises(OSError, match="simulated atomic install failure"):
        schema.atomic_write_once(path, content)
    assert not path.exists()
    assert not tuple(path.parent.glob(f".{path.name}.*"))

    monkeypatch.setattr(schema.os, "link", real_link)
    assert schema.atomic_write_once(path, content).read_bytes() == content
    assert schema.atomic_write_once(path, content) == path
    with pytest.raises(schema.ParallelStrategySchemaError, match="differs"):
        schema.atomic_write_once(path, b"different\n")


def test_initialization_archives_exact_authorities_and_runtime_offline(tmp_path):
    repo = _fixture_repo(tmp_path)
    manifest_path = schema.initialize_program(repo, created_at=CREATED_AT)
    program = schema.load_program(manifest_path)
    replay = schema.replay_program(manifest_path)

    assert program.stage == "preregistered"
    assert replay == {
        "schema_version": 1,
        "program_id": schema.PROGRAM_ID,
        "stage": "preregistered",
        "scheduled_cycles": 85,
        "maximum_authenticated_attempts": 12_410,
        "maximum_billable_credits": 12_240,
        "terminal_v1_attested": False,
    }
    schedule = json.loads((program.root / "schedule.json").read_text())
    assert len(schedule["cycles"]) == 85
    assert schedule["cycles"][0]["scheduled_at"] == "2026-10-15T12:05:00Z"
    assert schedule["cycles"][-1]["scheduled_at"] == "2026-11-13T12:05:00Z"
    frozen = json.loads(
        (program.root / "contracts/runtime-manifest.json").read_text()
    )
    assert frozen["dependencies"]
    assert any(item["path"].endswith("schema.py") for item in frozen["sources"])
    assert any(item["path"].endswith("test_parallel_strategy_runtime.py") for item in frozen["sources"])
    assert (
        schema.sha256_file(program.root / "contracts/nansen-openapi.json")
        == schema.EXPECTED_OPENAPI_SHA256
    )
    assert not (program.root / "activation").exists()


def test_init_rejects_symlinked_authority(tmp_path):
    repo = _fixture_repo(tmp_path)
    candidate = repo / schema.SOURCE_RELATIVE_PATH
    replacement = repo / "candidate-copy.json"
    replacement.write_bytes(candidate.read_bytes())
    candidate.unlink()
    candidate.symlink_to(replacement)
    with pytest.raises(schema.ParallelStrategySchemaError, match="symlink"):
        schema.initialize_program(repo, created_at=CREATED_AT)


def test_runtime_rejects_live_archive_and_source_set_drift(tmp_path):
    repo = _fixture_repo(tmp_path)
    manifest_path = schema.initialize_program(repo, created_at=CREATED_AT)
    program = schema.load_program(manifest_path)
    runtime_manifest = json.loads(
        (program.root / "contracts/runtime-manifest.json").read_text()
    )
    design_record = next(
        item for item in runtime_manifest["sources"] if item["path"].endswith("design.py")
    )

    live = repo / design_record["path"]
    original = live.read_bytes()
    live.write_bytes(original + b"\n# drift\n")
    with pytest.raises(schema.ParallelStrategySchemaError, match="live runtime source drifted"):
        schema.load_program(manifest_path)
    live.write_bytes(original)

    archived = program.root / design_record["archived_path"]
    archived.write_bytes(archived.read_bytes() + b"\n")
    with pytest.raises(schema.ParallelStrategySchemaError, match="archived runtime source drifted"):
        schema.load_program(manifest_path)
    archived.write_bytes(original)
    schema.load_program(manifest_path)

    extra = repo / "programs/nansen_parallel_strategy_v1/unregistered.py"
    extra.write_text("VALUE = 1\n")
    with pytest.raises(schema.ParallelStrategySchemaError, match="source set drifted"):
        schema.load_program(manifest_path)


def test_preregistration_requires_every_exact_head_byte(tmp_path):
    repo = _fixture_repo(tmp_path)
    manifest_path = schema.initialize_program(repo, created_at=CREATED_AT)
    with pytest.raises(schema.ParallelStrategySchemaError, match="absent from HEAD"):
        schema.assert_preregistration_committed(manifest_path)
    _git_commit(repo)
    schema.assert_preregistration_committed(manifest_path)

    preregistration = manifest_path.parent / "PREREGISTRATION.md"
    preregistration.write_bytes(preregistration.read_bytes() + b"drift\n")
    with pytest.raises(schema.ParallelStrategySchemaError, match="SHA-256 differs"):
        schema.assert_preregistration_committed(manifest_path)


def test_manifest_artifact_paths_are_strictly_confined(tmp_path):
    repo = _fixture_repo(tmp_path)
    manifest_path = schema.initialize_program(repo, created_at=CREATED_AT)
    manifest = json.loads(manifest_path.read_text())
    manifest["schedule"]["path"] = "../schedule.json"
    manifest_path.write_bytes(schema.canonical_json_bytes(manifest))
    with pytest.raises(schema.ParallelStrategySchemaError, match="normalized relative"):
        schema.load_program(manifest_path)


def test_terminal_v1_check_finalize_check_emits_only_operational_attestation(tmp_path):
    repo = _fixture_repo(tmp_path)
    manifest_path = schema.initialize_program(repo, created_at=CREATED_AT)
    _git_commit(repo)
    check_result = _terminal_v1(repo)
    calls = {"check": 0, "finalize": 0}

    def check(subject):
        assert subject == repo / schema.V1_PROGRAM_RELATIVE_PATH
        calls["check"] += 1
        return check_result

    def finalize(subject):
        assert subject == repo / schema.V1_PROGRAM_RELATIVE_PATH
        calls["finalize"] += 1
        path = subject.parent / "derived/aggregate.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"rules":[{"secret_metric":123.0}]}\n')
        return path

    attestation = runtime.produce_terminal_v1_attestation(
        manifest_path,
        cohort_check=check,
        cohort_finalize=finalize,
        recorded_at=datetime(2026, 10, 14, 17, 0, tzinfo=timezone.utc),
    )
    assert calls == {"check": 2, "finalize": 1}
    assert attestation["terminal_cycles"] == 32
    assert attestation["authenticated_attempts"] == 2
    assert attestation["billable_credits"] == 1
    assert len(attestation["cycles"]) == 32
    encoded = json.dumps(attestation, sort_keys=True)
    assert "rules" not in encoded
    assert "secret_metric" not in encoded

    activated = runtime.require_terminal_v1_activation(manifest_path)
    assert activated.stage == "activated"
    replay = schema.replay_program(manifest_path)
    assert replay["terminal_v1"] == {
        "source_program_sha256": attestation["source_program_sha256"],
        "source_aggregate_sha256": attestation["source_aggregate_sha256"],
        "terminal_cycles": 32,
        "authenticated_attempts": 2,
        "billable_credits": 1,
    }
    # Activation is one-way and idempotent; it does not rerun the transition.
    assert runtime.produce_terminal_v1_attestation(
        manifest_path, cohort_check=check, cohort_finalize=finalize
    ) == attestation
    assert calls == {"check": 2, "finalize": 1}


def test_nonterminal_v1_never_finalizes_or_activates(tmp_path):
    repo = _fixture_repo(tmp_path)
    manifest_path = schema.initialize_program(repo, created_at=CREATED_AT)
    _git_commit(repo)
    result = _terminal_v1(repo)
    result["terminal_cycles"] = 31
    result["cycles"][-1] = {
        "cycle_index": 32,
        "stage": "not_initialized",
        "attempts": 0,
        "credits": 0,
    }
    finalized = False

    def finalize(_subject):
        nonlocal finalized
        finalized = True
        raise AssertionError("nonterminal v1 reached finalization")

    with pytest.raises(schema.ParallelStrategySchemaError, match="not fully terminal"):
        runtime.produce_terminal_v1_attestation(
            manifest_path,
            cohort_check=lambda _subject: result,
            cohort_finalize=finalize,
        )
    assert finalized is False
    assert schema.load_program(manifest_path).stage == "preregistered"
    assert not (manifest_path.parent / runtime.ATTESTATION_RELATIVE_PATH).exists()
    with pytest.raises(schema.ParallelStrategySchemaError, match="required before first action"):
        runtime.require_terminal_v1_activation(manifest_path)


def test_activation_replay_rejects_v1_state_or_aggregate_tamper(tmp_path):
    repo = _fixture_repo(tmp_path)
    manifest_path = schema.initialize_program(repo, created_at=CREATED_AT)
    _git_commit(repo)
    check_result = _terminal_v1(repo)

    def finalize(subject):
        path = subject.parent / "derived/aggregate.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
        return path

    runtime.produce_terminal_v1_attestation(
        manifest_path,
        cohort_check=lambda _subject: check_result,
        cohort_finalize=finalize,
        recorded_at=datetime(2026, 10, 14, 17, 0, tzinfo=timezone.utc),
    )
    aggregate = (repo / schema.V1_PROGRAM_RELATIVE_PATH).parent / "derived/aggregate.json"
    aggregate.write_text('{"tampered":true}\n')
    with pytest.raises(schema.ParallelStrategySchemaError, match="aggregate binding drifted"):
        schema.load_program(manifest_path)
    aggregate.write_text("{}\n")
    schema.load_program(manifest_path)

    state = (
        (repo / schema.V1_PROGRAM_RELATIVE_PATH).parent
        / "cycles/cycle-01/state.json"
    )
    document = json.loads(state.read_text())
    document["terminal_reason"] = "changed"
    state.write_bytes(schema.canonical_json_bytes(document))
    with pytest.raises(schema.ParallelStrategySchemaError, match="state differs|binding drifted"):
        schema.load_program(manifest_path)
