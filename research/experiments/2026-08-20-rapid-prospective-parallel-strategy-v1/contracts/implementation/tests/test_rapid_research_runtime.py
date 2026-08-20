from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from programs.nansen_rapid_research_v1 import runtime, schema


REPOSITORY = Path(__file__).resolve().parents[1]
CREATED_AT = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    package = REPOSITORY / "programs/nansen_rapid_research_v1"
    for source in package.glob("*.py"):
        _copy(source, repo / source.relative_to(REPOSITORY))
    for source in (REPOSITORY / "src/nansen_signal_lab").glob("*.py"):
        _copy(source, repo / source.relative_to(REPOSITORY))
    _copy(REPOSITORY / "programs/__init__.py", repo / "programs/__init__.py")
    _copy(Path(__file__), repo / "tests/test_rapid_research_runtime.py")
    _copy(REPOSITORY / "requirements.txt", repo / "requirements.txt")
    _copy(REPOSITORY / schema.DESIGN_PATH, repo / schema.DESIGN_PATH)
    _copy(
        REPOSITORY / schema.OPENAPI_SOURCE_RELATIVE_PATH,
        repo / schema.OPENAPI_SOURCE_RELATIVE_PATH,
    )
    _copy(
        REPOSITORY / schema.SOURCE_RELATIVE_PATH,
        repo / schema.SOURCE_RELATIVE_PATH,
    )
    for relative in (
        schema.V1_PROGRAM_RELATIVE_PATH,
        schema.V1_PROGRAM_RELATIVE_PATH.parent / "cycles/cycle-01/state.json",
        schema.V1_PROGRAM_RELATIVE_PATH.parent
        / "cycles/cycle-01/seals/unscorable.json",
        Path(
            "research/experiments/2026-08-18-historical-theory-discovery-a-v1/"
            "seals/final.json"
        ),
        Path(
            "research/experiments/2026-08-18-historical-theory-discovery-a2-v1/"
            "seals/final.json"
        ),
    ):
        _copy(REPOSITORY / relative, repo / relative)
    return repo


def _git_commit(repo: Path) -> None:
    subprocess.run(("git", "init", "-q"), cwd=repo, check=True)
    subprocess.run(
        ("git", "config", "user.email", "rapid-runtime@example.invalid"),
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ("git", "config", "user.name", "Rapid Runtime Test"),
        cwd=repo,
        check=True,
    )
    subprocess.run(("git", "add", "."), cwd=repo, check=True)
    subprocess.run(("git", "commit", "-q", "-m", "freeze rapid test"), cwd=repo, check=True)


def _owner_abort_replay(_subject: Path) -> dict:
    cycles = [
        {
            "cycle_index": 1,
            "stage": "unscorable",
            "terminal_reason": "insufficient_strata",
            "attempts": 2,
            "credits": 1,
            "provider_remaining": 50_063,
        }
    ]
    cycles.extend(
        {
            "cycle_index": index,
            "stage": "not_initialized",
            "attempts": 0,
            "credits": 0,
        }
        for index in range(2, 33)
    )
    return {
        "schema_version": 1,
        "program_id": schema.V1_PROGRAM_ID,
        "cycles": cycles,
        "terminal_cycles": 1,
        "authenticated_attempts": 2,
        "credits": 1,
        "authorized_credit_ceiling_breached": False,
        "scientific_result_that_must_not_cross": {"return": 999},
    }


def _stopped(_unit: str) -> dict[str, str]:
    return {"active_state": "inactive", "unit_file_state": "disabled"}


def test_atomic_write_never_exposes_partial_protocol_file(tmp_path, monkeypatch):
    path = tmp_path / "sealed/artifact.json"
    content = b'{"complete":true}\n'
    real_link = schema.os.link

    def fail_install(*_args, **_kwargs):
        raise OSError("simulated install failure")

    monkeypatch.setattr(schema.os, "link", fail_install)
    with pytest.raises(OSError, match="simulated install failure"):
        schema.atomic_write_once(path, content)
    assert not path.exists()
    assert not tuple(path.parent.glob(f".{path.name}.*"))
    monkeypatch.setattr(schema.os, "link", real_link)
    assert schema.atomic_write_once(path, content).read_bytes() == content


def test_initialization_archives_rapid_runtime_and_exact_august_schedule(tmp_path):
    repo = _fixture_repo(tmp_path)
    manifest_path = schema.initialize_program(repo, created_at=CREATED_AT)
    program = schema.load_program(manifest_path)
    assert program.stage == "preregistered"
    assert schema.replay_program(manifest_path) == {
        "schema_version": 1,
        "program_id": schema.PROGRAM_ID,
        "stage": "preregistered",
        "scheduled_cycles": 85,
        "maximum_authenticated_attempts": 12_410,
        "maximum_billable_credits": 12_240,
        "owner_abort_attested": False,
    }
    schedule = json.loads((program.root / "schedule.json").read_bytes())
    assert schedule["cycles"][0]["scheduled_at"] == "2026-08-22T12:05:00Z"
    assert schedule["cycles"][-1]["scheduled_at"] == "2026-09-20T12:05:00Z"
    frozen = json.loads(
        (program.root / "contracts/runtime-manifest.json").read_bytes()
    )
    assert frozen["kind"] == "rapid-research-runtime-freeze-v1"
    assert any(
        item["path"].endswith("test_rapid_research_runtime.py")
        for item in frozen["sources"]
    )
    assert not any("nansen_parallel_strategy_v1" in item["path"] for item in frozen["sources"])


def test_runtime_and_head_gates_refuse_source_drift(tmp_path):
    repo = _fixture_repo(tmp_path)
    manifest_path = schema.initialize_program(repo, created_at=CREATED_AT)
    with pytest.raises(schema.ParallelStrategySchemaError, match="absent from HEAD"):
        schema.assert_preregistration_committed(manifest_path)
    _git_commit(repo)
    schema.assert_preregistration_committed(manifest_path)
    live = repo / "programs/nansen_rapid_research_v1/design.py"
    original = live.read_bytes()
    live.write_bytes(original + b"\n# drift\n")
    with pytest.raises(schema.ParallelStrategySchemaError, match="live runtime source drifted"):
        schema.load_program(manifest_path)
    live.write_bytes(original)
    schema.load_program(manifest_path)


def test_owner_abort_activation_is_operational_only_and_replay_exact(tmp_path):
    repo = _fixture_repo(tmp_path)
    manifest_path = schema.initialize_program(repo, created_at=CREATED_AT)
    _git_commit(repo)
    reconciliation = runtime.produce_stopped_v1_attestation(
        manifest_path,
        cohort_check=_owner_abort_replay,
        unit_probe=_stopped,
        recorded_at=CREATED_AT,
    )
    assert reconciliation["opening_balance_candidates"] == [50_063]
    assert "scientific_result_that_must_not_cross" not in json.dumps(reconciliation)
    activated = schema.load_program(manifest_path)
    assert activated.stage == "activated"
    assert runtime.validate_stopped_v1_activation(activated, unit_probe=_stopped) == reconciliation
    replay = schema.replay_program(manifest_path)
    assert replay["owner_abort_attested"] is True
    assert replay["activation"] == reconciliation


@pytest.mark.parametrize("crash_after_write", (1, 2, 3, 4))
def test_activation_adopts_partial_transaction_with_original_timestamp(
    tmp_path, monkeypatch, crash_after_write
):
    repo = _fixture_repo(tmp_path)
    manifest_path = schema.initialize_program(repo, created_at=CREATED_AT)
    _git_commit(repo)
    original_write = runtime._write_once
    calls = 0

    def write_then_crash(path, content):
        nonlocal calls
        result = original_write(path, content)
        calls += 1
        if calls == crash_after_write:
            raise RuntimeError("simulated activation crash")
        return result

    monkeypatch.setattr(runtime, "_write_once", write_then_crash)
    with pytest.raises(RuntimeError, match="simulated activation crash"):
        runtime.produce_stopped_v1_attestation(
            manifest_path,
            cohort_check=_owner_abort_replay,
            unit_probe=_stopped,
            recorded_at=CREATED_AT,
        )
    assert schema.load_program(manifest_path).stage == "preregistered"

    monkeypatch.setattr(runtime, "_write_once", original_write)
    later = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
    runtime.produce_stopped_v1_attestation(
        manifest_path,
        cohort_check=_owner_abort_replay,
        unit_probe=_stopped,
        recorded_at=later,
    )
    owner = json.loads(
        (manifest_path.parent / runtime.OWNER_ATTESTATION_RELATIVE_PATH).read_bytes()
    )
    assert owner["recorded_at"] == "2026-08-20T10:00:00Z"
    assert schema.load_program(manifest_path).stage == "activated"


def test_activation_refuses_later_cohort_activity_or_live_legacy_unit(tmp_path):
    repo = _fixture_repo(tmp_path)
    manifest_path = schema.initialize_program(repo, created_at=CREATED_AT)
    _git_commit(repo)
    later = _owner_abort_replay(Path("unused"))
    later["cycles"][1] = {
        "cycle_index": 2,
        "stage": "unscorable",
        "terminal_reason": "late",
        "attempts": 0,
        "credits": 0,
    }
    with pytest.raises(schema.ParallelStrategySchemaError, match="later activity"):
        runtime.produce_stopped_v1_attestation(
            manifest_path,
            cohort_check=lambda _subject: later,
            unit_probe=_stopped,
            recorded_at=CREATED_AT,
        )
    with pytest.raises(schema.ParallelStrategySchemaError, match="retains authority"):
        runtime.produce_stopped_v1_attestation(
            manifest_path,
            cohort_check=_owner_abort_replay,
            unit_probe=lambda unit: {
                "active_state": "active" if unit.endswith("cohort.timer") else "inactive",
                "unit_file_state": "disabled",
            },
            recorded_at=CREATED_AT,
        )
