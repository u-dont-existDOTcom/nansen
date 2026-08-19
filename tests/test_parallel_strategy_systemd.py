from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from programs.nansen_parallel_strategy_v1 import design
from programs.nansen_parallel_strategy_v1.supervisor import (
    DueAction,
    choose_due_action,
    drain_due_actions,
)
from scripts import nansen_parallel_strategy as cli
from scripts import nansen_parallel_strategy_timer as timer_script


REPO = Path(__file__).resolve().parents[1]


def _manifest(tmp_path: Path, stage: str) -> Path:
    root = tmp_path / "program"
    root.mkdir()
    path = root / "program.json"
    path.write_text(json.dumps({"stage": stage}))
    return path.absolute()


def _state(path: Path, cycle_index: int, stage: str) -> Path:
    state = path.parent / "cycles" / f"cycle-{cycle_index:03d}" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"cycle_index": cycle_index, "stage": stage}))
    return state


def _fatal_intent(path: Path, cycle_index: int = 1) -> tuple[Path, dict]:
    document = {
        "schema_version": 1,
        "program_id": design.PROGRAM_ID,
        "cycle_index": cycle_index,
        "recorded_at": design.SCHEDULE[cycle_index - 1]
        .scheduled_at.isoformat()
        .replace("+00:00", "Z"),
        "reason": "simulated crash after durable fatal intent",
    }
    fatal = path.parent / "intents/program-fatal.json"
    fatal.parent.mkdir(parents=True, exist_ok=True)
    fatal.write_text(json.dumps(document))
    return fatal, document


def test_supervisor_is_idle_until_activation_or_a_due_cycle(tmp_path: Path):
    path = _manifest(tmp_path, "preregistered")
    before = design.SCHEDULE[0].scheduled_at - timedelta(days=1)
    assert choose_due_action(path, before) is None
    action = choose_due_action(
        path, design.SCHEDULE[0].scheduled_at - timedelta(hours=19)
    )
    assert action is not None and action.command == "activate"

    path.write_text(json.dumps({"stage": "activated"}))
    reconciliation = path.parent / "activation/operational-reconciliation.json"
    reconciliation.parent.mkdir()
    reconciliation.write_text("{}")
    assert choose_due_action(path, before) is None
    action = choose_due_action(path, design.SCHEDULE[0].scheduled_at)
    assert action is not None
    assert (action.command, action.cycle_index, action.requires_network) == (
        "predecision",
        1,
        True,
    )
    late = choose_due_action(
        path, design.SCHEDULE[0].scheduled_at + timedelta(minutes=16)
    )
    assert late is not None and late.requires_network is False


def test_supervisor_schedules_program_scoped_fatal_repair_until_transition_complete(
    tmp_path: Path,
):
    path = _manifest(tmp_path, "activated")
    reconciliation = path.parent / "activation/operational-reconciliation.json"
    reconciliation.parent.mkdir()
    reconciliation.write_text("{}")
    fatal, intent = _fatal_intent(path)
    action = choose_due_action(path, design.SCHEDULE[0].scheduled_at)
    assert action is not None
    assert (action.command, action.cycle_index, action.requires_network) == (
        "repair-fatal",
        None,
        False,
    )

    seal = path.parent / "seals/program-fatal.json"
    seal.parent.mkdir()
    seal.write_text(json.dumps(intent))
    assert choose_due_action(path, design.SCHEDULE[0].scheduled_at) == action

    _state(path, 1, "unscorable")
    assert choose_due_action(path, design.SCHEDULE[0].scheduled_at) is None

    seal.write_text(json.dumps({**intent, "reason": "different"}))
    with pytest.raises(ValueError, match="seal differs"):
        choose_due_action(path, design.SCHEDULE[0].scheduled_at)

    fatal.unlink()
    seal.unlink()
    for cycle in design.SCHEDULE[:42]:
        _state(path, cycle.index, "unscorable")
    family = path.parent / "derived/discovery-family.json"
    family.parent.mkdir()
    family.write_text(json.dumps({"stage": "unscorable"}))
    action = choose_due_action(path, design.SCHEDULE[41].scheduled_at)
    assert action is not None and action.command == "freeze-discovery"

    family_seal = path.parent / "seals/discovery-family.json"
    family_seal.parent.mkdir(exist_ok=True)
    family_seal.write_text("{}")
    result = path.parent / "derived/final-result.json"
    result.write_text("{}")
    action = choose_due_action(path, design.SCHEDULE[41].scheduled_at)
    assert action is not None and action.command == "finalize"


def test_fatal_repair_with_only_seal_progress_is_accepted(tmp_path: Path):
    path = _manifest(tmp_path, "activated")
    reconciliation = path.parent / "activation/operational-reconciliation.json"
    reconciliation.parent.mkdir()
    reconciliation.write_text("{}")
    _, intent = _fatal_intent(path)
    _state(path, 1, "unscorable")
    calls: list[str] = []

    def execute(action: DueAction) -> None:
        calls.append(action.command)
        seal = path.parent / "seals/program-fatal.json"
        seal.parent.mkdir(parents=True)
        seal.write_text(json.dumps(intent))

    completed = drain_due_actions(
        path,
        clock=lambda: design.SCHEDULE[0].scheduled_at,
        executor=execute,
    )
    assert [action.command for action in completed] == ["repair-fatal"]
    assert calls == ["repair-fatal"]


def test_supervisor_network_boundaries_include_crash_resume(tmp_path: Path):
    path = _manifest(tmp_path, "activated")
    reconciliation = path.parent / "activation/operational-reconciliation.json"
    reconciliation.parent.mkdir()
    reconciliation.write_text("{}")
    cycle = design.SCHEDULE[0]
    scheduled = cycle.scheduled_at

    at_grace = choose_due_action(path, scheduled + design.START_GRACE)
    assert at_grace is not None and at_grace.requires_network is True
    after_grace = choose_due_action(
        path, scheduled + design.START_GRACE + timedelta(microseconds=1)
    )
    assert after_grace is not None and after_grace.requires_network is False

    epoch = (
        path.parent
        / "budget/parallel-strategy-v1/epochs/c001-predecision"
    )
    epoch.mkdir(parents=True)
    resumed = choose_due_action(
        path, scheduled + design.START_GRACE + timedelta(microseconds=1)
    )
    assert resumed is not None and resumed.requires_network is True
    before_cutoff = choose_due_action(
        path,
        scheduled
        + design.PREDECISION_TRANSPORT_CUTOFF
        - timedelta(microseconds=1),
    )
    assert before_cutoff is not None and before_cutoff.requires_network is True
    at_cutoff = choose_due_action(
        path, scheduled + design.PREDECISION_TRANSPORT_CUTOFF
    )
    assert at_cutoff is not None and at_cutoff.requires_network is False


def test_supervisor_settlement_network_cutoff_is_half_open(tmp_path: Path):
    path = _manifest(tmp_path, "activated")
    reconciliation = path.parent / "activation/operational-reconciliation.json"
    reconciliation.parent.mkdir()
    reconciliation.write_text("{}")
    cycle = design.SCHEDULE[0]
    _state(path, cycle.index, "decisions_sealed")
    decisions = (
        path.parent
        / "cycles"
        / f"cycle-{cycle.index:03d}"
        / "derived/decisions.json"
    )
    decisions.parent.mkdir()
    t0 = cycle.scheduled_at + timedelta(minutes=5)
    decisions.write_text(
        json.dumps({"decision_t0": t0.isoformat().replace("+00:00", "Z")})
    )
    earliest = t0 + design.SETTLEMENT_OFFSET
    action = choose_due_action(path, earliest)
    assert action is not None and action.requires_network is True
    before_cutoff = choose_due_action(
        path,
        cycle.scheduled_at
        + design.SETTLEMENT_TRANSPORT_CUTOFF
        - timedelta(microseconds=1),
    )
    assert before_cutoff is not None and before_cutoff.requires_network is True
    at_cutoff = choose_due_action(
        path, cycle.scheduled_at + design.SETTLEMENT_TRANSPORT_CUTOFF
    )
    assert at_cutoff is not None and at_cutoff.requires_network is False


def test_timer_executes_repair_fatal_without_cycle_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manifest = (tmp_path / "program.json").absolute()
    captured: list[tuple[str, ...]] = []

    def run(command, **_kwargs):
        captured.append(tuple(str(item) for item in command))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(timer_script.subprocess, "run", run)
    timer_script.execute(
        manifest,
        DueAction(
            "repair-fatal",
            None,
            datetime(2026, 8, 19, tzinfo=timezone.utc),
            False,
        ),
    )
    assert len(captured) == 2
    assert "repair-fatal" in captured[0]
    assert "--cycle" not in captured[0]
    assert "check" in captured[1]
    assert "--cycle" not in captured[1]


def test_timer_runs_cycle_scoped_integrity_check_after_live_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manifest = (tmp_path / "program.json").absolute()
    captured: list[tuple[str, ...]] = []

    def run(command, **_kwargs):
        captured.append(tuple(str(item) for item in command))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(timer_script.subprocess, "run", run)
    timer_script.execute(
        manifest,
        DueAction(
            "predecision",
            1,
            design.SCHEDULE[0].scheduled_at,
            False,
        ),
    )

    assert len(captured) == 2
    assert "predecision" in captured[0]
    assert "check-cycle" in captured[1]
    assert captured[1][-2:] == ("--cycle", "1")


def test_finalize_cli_always_runs_semantic_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manifest = (tmp_path / "program.json").absolute()
    result = tmp_path / "derived/final-result.json"
    calls: list[tuple[str, Path]] = []
    monkeypatch.setattr(cli, "load_dotenv", lambda *_args, **_kwargs: None)

    def finalize(subject: Path) -> Path:
        calls.append(("finalize", subject))
        return result

    def check(subject: Path) -> dict:
        calls.append(("check", subject))
        return {"finalized": True}

    monkeypatch.setattr(cli, "finalize_program", finalize)
    monkeypatch.setattr(cli, "check_program", check)
    assert cli.main(("finalize", "--manifest", str(manifest))) == 0
    assert calls == [("finalize", manifest), ("check", manifest)]


def test_repair_fatal_cli_is_program_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manifest = (tmp_path / "program.json").absolute()
    calls: list[Path] = []
    monkeypatch.setattr(cli, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "repair_program_fatal",
        lambda subject: calls.append(subject) or {"stage": "unscorable"},
    )
    assert cli.main(("repair-fatal", "--manifest", str(manifest))) == 0
    assert calls == [manifest]
    with pytest.raises(SystemExit):
        cli.main(
            (
                "repair-fatal",
                "--manifest",
                str(manifest),
                "--cycle",
                "1",
            )
        )


def test_units_use_persistent_shared_lock_and_home_installed_copy_contract():
    service = (
        REPO / "operations/nansen-signal-lab-parallel-strategy.service"
    ).read_text()
    timer = (
        REPO / "operations/nansen-signal-lab-parallel-strategy.timer"
    ).read_text()
    cohort_dropin = (
        REPO
        / "operations/nansen-signal-lab-cohort-parallel-strategy-dropin.conf"
    ).read_text()
    assert "RuntimeDirectory=nansen-signal-lab-parallel-strategy nansen-signal-lab-provider" in service
    assert "RuntimeDirectoryPreserve=yes" in service
    assert "RuntimeDirectoryPreserve=yes" in cohort_dropin
    assert "EnvironmentFile=" in service
    assert "NANSEN_API_KEY" not in service
    assert "Persistent=true" in timer
    assert "AccuracySec=1s" in timer
    assert "RandomizedDelaySec=0" in timer
    assert "research/experiments/2026-08-19-prospective-parallel-strategy-v1" in service


def test_systemd_units_verify():
    executable = shutil.which("systemd-analyze")
    if executable is None:
        pytest.skip("systemd-analyze is unavailable")
    completed = subprocess.run(
        (
            executable,
            "verify",
            str(REPO / "operations/nansen-signal-lab-parallel-strategy.service"),
            str(REPO / "operations/nansen-signal-lab-parallel-strategy.timer"),
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
