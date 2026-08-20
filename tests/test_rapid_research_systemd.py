from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from programs.nansen_rapid_research_v1 import design
from scripts import nansen_rapid_research as cli
from scripts import nansen_rapid_research_timer as timer_script
from scripts.nansen_rapid_research_timer import (
    DueAction,
    choose_due_action,
    drain_due_actions,
)


REPO = Path(__file__).resolve().parents[1]
MANIFEST = (
    REPO
    / "research/experiments/2026-08-20-rapid-prospective-parallel-strategy-v1/program.json"
)
SERVICE = REPO / "operations/nansen-signal-lab-rapid-research.service"
TIMER = REPO / "operations/nansen-signal-lab-rapid-research.timer"


def _manifest(tmp_path: Path, stage: str = "activated") -> Path:
    root = tmp_path / "program"
    root.mkdir(parents=True)
    path = root / "program.json"
    path.write_text(json.dumps({"stage": stage}))
    if stage == "activated":
        reconciliation = root / "activation/operational-reconciliation.json"
        reconciliation.parent.mkdir()
        reconciliation.write_text("{}")
    return path.absolute()


def _state(path: Path, cycle_index: int, stage: str) -> Path:
    state = path.parent / "cycles" / f"cycle-{cycle_index:03d}" / "state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"cycle_index": cycle_index, "stage": stage}))
    return state


def _discovery_cycles() -> tuple:
    return tuple(cycle for cycle in design.SCHEDULE if cycle.phase == "discovery")


def test_rapid_schedule_and_manifest_are_exactly_future_bound():
    assert design.SCHEDULE[0].scheduled_at == datetime(
        2026, 8, 22, 12, 5, tzinfo=timezone.utc
    )
    assert MANIFEST.is_absolute()
    assert str(MANIFEST).endswith(
        "research/experiments/2026-08-20-rapid-prospective-parallel-strategy-v1/program.json"
    )


def test_activation_and_reconciliation_wait_for_their_lead_times(tmp_path: Path):
    path = _manifest(tmp_path, "preregistered")
    first = design.SCHEDULE[0].scheduled_at
    activate_at = first - timedelta(hours=20)
    assert choose_due_action(path, activate_at - timedelta(microseconds=1)) is None
    action = choose_due_action(path, activate_at)
    assert action == DueAction("activate", None, activate_at, False)

    path.write_text(json.dumps({"stage": "activated"}))
    assert choose_due_action(path, activate_at) is None
    reconcile_at = first - timedelta(hours=19)
    action = choose_due_action(path, reconcile_at)
    assert action == DueAction("reconcile", None, reconcile_at, False)


def test_predecision_network_gate_is_exact_and_recovers_prestate_crash(
    tmp_path: Path,
):
    path = _manifest(tmp_path)
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


def test_planned_late_cycle_is_offline_but_panel_resume_is_live(tmp_path: Path):
    path = _manifest(tmp_path)
    cycle = design.SCHEDULE[0]
    late = cycle.scheduled_at + design.START_GRACE + timedelta(seconds=1)
    _state(path, cycle.index, "planned")
    action = choose_due_action(path, late)
    assert action is not None and action.requires_network is False

    _state(path, cycle.index, "panel_sealed")
    action = choose_due_action(path, late)
    assert action is not None and action.requires_network is True


def test_settlement_network_gate_is_half_open(tmp_path: Path):
    path = _manifest(tmp_path)
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


def test_discovery_freeze_requires_document_and_seal(tmp_path: Path):
    path = _manifest(tmp_path)
    discovery = _discovery_cycles()
    for cycle in discovery:
        _state(path, cycle.index, "unscorable")
    action = choose_due_action(path, discovery[-1].scheduled_at)
    assert action is not None and action.command == "freeze-discovery"

    family = path.parent / "derived/discovery-family.json"
    family.parent.mkdir()
    family.write_text(json.dumps({"stage": "unscorable"}))
    assert choose_due_action(path, discovery[-1].scheduled_at) == action
    family_seal = path.parent / "seals/discovery-family.json"
    family_seal.parent.mkdir()
    family_seal.write_text("{}")
    action = choose_due_action(path, discovery[-1].scheduled_at)
    assert action is not None and action.command == "finalize"


def test_fatal_intent_repairs_without_cycle_or_network(tmp_path: Path):
    path = _manifest(tmp_path)
    cycle = design.SCHEDULE[0]
    intent = {
        "schema_version": 1,
        "program_id": design.PROGRAM_ID,
        "cycle_index": cycle.index,
        "recorded_at": cycle.scheduled_at.isoformat().replace("+00:00", "Z"),
        "reason": "simulated crash after durable intent",
    }
    intent_path = path.parent / "intents/program-fatal.json"
    intent_path.parent.mkdir()
    intent_path.write_text(json.dumps(intent))
    action = choose_due_action(path, cycle.scheduled_at)
    assert action is not None
    assert (action.command, action.cycle_index, action.requires_network) == (
        "repair-fatal",
        None,
        False,
    )

    seal = path.parent / "seals/program-fatal.json"
    seal.parent.mkdir()
    seal.write_text(json.dumps(intent))
    _state(path, cycle.index, "unscorable")
    assert choose_due_action(path, cycle.scheduled_at) is None


def test_catch_up_drains_missed_cycle_and_rejects_no_progress(tmp_path: Path):
    path = _manifest(tmp_path)
    first = design.SCHEDULE[0]
    now = first.scheduled_at + design.START_GRACE + timedelta(seconds=1)

    def terminalize(action: DueAction) -> None:
        assert action.requires_network is False
        assert action.cycle_index is not None
        _state(path, action.cycle_index, "unscorable")

    completed = drain_due_actions(path, clock=lambda: now, executor=terminalize)
    assert [(item.command, item.cycle_index) for item in completed] == [
        ("predecision", first.index)
    ]

    fresh = _manifest(tmp_path / "fresh")
    with pytest.raises(RuntimeError, match="no durable progress"):
        drain_due_actions(
            fresh,
            clock=lambda: now,
            executor=lambda _action: None,
            max_actions=1,
        )


def test_execute_gates_network_only_for_live_actions_and_always_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manifest = (tmp_path / "program.json").absolute()
    calls: list[tuple[str, ...]] = []

    def run(command, **_kwargs):
        normalized = tuple(str(item) for item in command)
        calls.append(normalized)
        if normalized[0] == "/usr/bin/systemctl":
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "LoadState=loaded\nActiveState=inactive\n"
                    "UnitFileState=disabled\nMainPID=0\n"
                ),
            )
        if normalized[0] == "/usr/bin/ps":
            return SimpleNamespace(returncode=0, stdout="1 /usr/lib/systemd/systemd\n")
        if normalized[0] == "/usr/bin/timedatectl":
            return SimpleNamespace(returncode=0, stdout="yes\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(timer_script.subprocess, "run", run)
    cycle = design.SCHEDULE[0]
    timer_script.execute(
        manifest,
        DueAction("predecision", cycle.index, cycle.scheduled_at, False),
    )
    assert len(calls) == 2
    assert "predecision" in calls[0]
    assert "check-cycle" in calls[1]
    assert calls[1][-2:] == ("--cycle", str(cycle.index))

    calls.clear()
    timer_script.execute(
        manifest,
        DueAction("predecision", cycle.index, cycle.scheduled_at, True),
    )
    assert len(calls) == 9
    assert all(call[0] == "/usr/bin/systemctl" for call in calls[:4])
    assert calls[4][0] == "/usr/bin/ps"
    assert calls[5][0] == "/usr/bin/timedatectl"
    assert calls[6][0] == "/usr/bin/nm-online"
    assert "predecision" in calls[7]
    assert "check-cycle" in calls[8]


def test_activation_and_live_work_fail_closed_while_old_authority_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[tuple[str, ...]] = []

    def run(command, **_kwargs):
        normalized = tuple(str(item) for item in command)
        calls.append(normalized)
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "LoadState=loaded\nActiveState=active\n"
                "UnitFileState=enabled\nMainPID=123\n"
            ),
        )

    monkeypatch.setattr(timer_script.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="still active"):
        timer_script.execute(
            (tmp_path / "program.json").absolute(),
            DueAction(
                "activate",
                None,
                datetime(2026, 8, 21, 16, 5, tzinfo=timezone.utc),
                False,
            ),
        )
    assert len(calls) == 1


def test_retirement_gate_rejects_manual_old_provider_process(
    monkeypatch: pytest.MonkeyPatch,
):
    def run(command, **_kwargs):
        normalized = tuple(str(item) for item in command)
        if normalized[0] == "/usr/bin/systemctl":
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "LoadState=loaded\nActiveState=inactive\n"
                    "UnitFileState=disabled\nMainPID=0\n"
                ),
            )
        return SimpleNamespace(
            returncode=0,
            stdout="22 python scripts/prospective_cohort_timer.py --program old\n",
        )

    monkeypatch.setattr(timer_script.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="retired Nansen process"):
        timer_script.require_retired_authority()


def test_retired_timer_need_not_expose_a_main_pid(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        timer_script.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                "LoadState=loaded\nActiveState=inactive\n"
                "UnitFileState=disabled\n"
            ),
        ),
    )

    assert timer_script._unit_properties("retired.timer") == {
        "LoadState": "loaded",
        "ActiveState": "inactive",
        "UnitFileState": "disabled",
    }


def test_execute_fails_closed_when_post_action_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    returns = iter((0, 1))

    def run(_command, **_kwargs):
        return SimpleNamespace(returncode=next(returns))

    monkeypatch.setattr(timer_script.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="post-action integrity check failed"):
        timer_script.execute(
            (tmp_path / "program.json").absolute(),
            DueAction(
                "repair-fatal",
                None,
                datetime(2026, 8, 20, tzinfo=timezone.utc),
                False,
            ),
        )


def test_cli_activation_and_finalize_include_required_offline_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manifest = (tmp_path / "program.json").absolute()
    calls: list[tuple[str, Path]] = []
    monkeypatch.setattr(cli, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli,
        "require_retired_authority",
        lambda: calls.append(("authority", manifest)),
    )
    monkeypatch.setattr(
        cli,
        "produce_stopped_v1_attestation",
        lambda subject: calls.append(("stop", subject)) or {"stopped": True},
    )
    monkeypatch.setattr(
        cli,
        "seal_operational_reconciliation",
        lambda subject: calls.append(("reconcile", subject))
        or tmp_path / "reconciliation.json",
    )
    assert cli.main(("activate", "--manifest", str(manifest))) == 0
    assert calls == [
        ("authority", manifest),
        ("stop", manifest),
        ("reconcile", manifest),
    ]

    calls.clear()
    result = tmp_path / "final-result.json"
    monkeypatch.setattr(
        cli,
        "finalize_program",
        lambda subject: calls.append(("finalize", subject)) or result,
    )
    monkeypatch.setattr(
        cli,
        "check_program",
        lambda subject: calls.append(("check", subject)) or {"finalized": True},
    )
    assert cli.main(("finalize", "--manifest", str(manifest))) == 0
    assert calls == [("finalize", manifest), ("check", manifest)]


def test_direct_live_cli_checks_authority_and_network_before_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    manifest = (tmp_path / "program.json").absolute()
    events: list[str] = []
    transport = object()
    monkeypatch.setattr(cli, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli, "require_retired_authority", lambda: events.append("authority")
    )
    monkeypatch.setattr(
        cli, "require_network_ready", lambda: events.append("network")
    )
    monkeypatch.setattr(
        cli,
        "_live_transport",
        lambda subject: events.append(f"client:{subject}") or transport,
    )
    monkeypatch.setattr(
        cli,
        "run_predecision",
        lambda subject, cycle, *, transport: events.append(
            f"run:{subject}:{cycle}:{transport is not None}"
        )
        or {"stage": "unscorable"},
    )
    assert (
        cli.main(
            (
                "predecision",
                "--manifest",
                str(manifest),
                "--cycle",
                "1",
            )
        )
        == 0
    )
    assert events == [
        "authority",
        "network",
        f"client:{manifest}",
        f"run:{manifest}:1:True",
    ]


def test_units_are_persistent_hardened_home_copy_sources():
    service = SERVICE.read_text()
    timer = TIMER.read_text()
    runner = (REPO / "programs/nansen_rapid_research_v1/runner.py").read_text()
    assert SERVICE.is_file() and not SERVICE.is_symlink()
    assert TIMER.is_file() and not TIMER.is_symlink()
    assert "copy this unit into ~/.config/systemd/user/" in service
    assert "copy this unit into ~/.config/systemd/user/" in timer
    assert "scripts/nansen_rapid_research_timer.py" in service
    assert str(MANIFEST) in service
    assert "RuntimeDirectory=nansen-signal-lab-rapid-research nansen-signal-lab-provider" in service
    assert "RuntimeDirectoryPreserve=yes" in service
    assert "ReadWritePaths=%t/nansen-signal-lab-provider" in service
    assert '"nansen-signal-lab-provider"' in runner
    assert '"provider.lock"' in runner
    assert "EnvironmentFile=" in service
    assert "NANSEN_API_KEY=" not in service
    assert "ExecStartPre=" not in service
    assert "Restart=" not in service
    assert "nansen-signal-lab-provider" in service
    assert "Persistent=true" in timer
    assert "OnCalendar=*-*-* *:*:00 UTC" in timer
    assert "AccuracySec=1s" in timer
    assert "RandomizedDelaySec=0" in timer
    assert "Unit=nansen-signal-lab-rapid-research.service" in timer
    assert "WantedBy=timers.target" in timer


def test_rapid_names_avoid_every_frozen_source_glob():
    paths = (
        "scripts/nansen_rapid_research.py",
        "scripts/nansen_rapid_research_timer.py",
        "operations/nansen-signal-lab-rapid-research.service",
        "operations/nansen-signal-lab-rapid-research.timer",
        "tests/test_rapid_research_systemd.py",
    )
    assert not any(
        fnmatch.fnmatch(Path(path).name, "*parallel_strategy*.py")
        for path in paths
    )
    assert not any(
        fnmatch.fnmatch(Path(path).name, "nansen-signal-lab-parallel-strategy*")
        for path in paths
    )
    assert not any(
        fnmatch.fnmatch(Path(path).name, "test_parallel_strategy*.py")
        for path in paths
    )
    assert not any(path.startswith("ops/systemd/") for path in paths)

    from programs.nansen_parallel_strategy_v1 import schema as october_schema
    from programs.nansen_theory_portfolio import runner as program_a
    from programs.nansen_theory_portfolio_a2 import runtime as program_a2

    frozen_sources = (
        *october_schema._source_paths(REPO),
        *program_a._source_paths(REPO),
        *program_a2._source_paths(REPO),
    )
    assert not any("rapid_research" in path.as_posix() for path in frozen_sources)
    assert not any("rapid-research" in path.as_posix() for path in frozen_sources)


def test_systemd_units_verify():
    executable = shutil.which("systemd-analyze")
    if executable is None:
        pytest.skip("systemd-analyze is unavailable")
    completed = subprocess.run(
        (executable, "verify", str(SERVICE), str(TIMER)),
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "script",
    (
        "scripts/nansen_rapid_research.py",
        "scripts/nansen_rapid_research_timer.py",
    ),
)
def test_installed_script_entrypoints_import_from_repository(script: str):
    completed = subprocess.run(
        (sys.executable, str(REPO / script), "--help"),
        cwd=Path("/tmp"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
