from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from programs.nansen_theory_portfolio.design import PROGRAM_A_STOP_BEFORE
from programs.nansen_theory_portfolio.runner import REQUEST_START_CUTOFF


REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_ROOT = REPO_ROOT / "ops/systemd"
PROGRAM_A_SERVICE = SYSTEMD_ROOT / "nansen-signal-lab-program-a.service"
COHORT_SERVICE = SYSTEMD_ROOT / "nansen-signal-lab-cohort.service"
COHORT_TIMER = SYSTEMD_ROOT / "nansen-signal-lab-cohort.timer"
SUPERVISOR = REPO_ROOT / "scripts/prospective_cohort_timer.py"


def _unit_value(text: str, key: str) -> list[str]:
    prefix = f"{key}="
    return [line.removeprefix(prefix) for line in text.splitlines() if line.startswith(prefix)]


def test_program_a_handoff_never_conflict_kills_cohort_service() -> None:
    text = PROGRAM_A_SERVICE.read_text()
    conflicts = _unit_value(text, "Conflicts")
    assert conflicts == ["nansen-signal-lab-cohort.timer"]
    assert "After=nansen-signal-lab-cohort.service" in text

    preflights = _unit_value(text, "ExecStartPre")
    assert len(preflights) == 1
    preflight = preflights[0]
    assert "LoadState" in preflight
    assert "ActiveState" in preflight
    assert '"$$load" = loaded' in preflight
    assert "inactive) exit 0" in preflight
    assert "active|activating|deactivating|reloading" in preflight
    assert "*) exit 2" in preflight


def test_program_a_has_one_shot_timer_restore_without_retry_gap() -> None:
    text = PROGRAM_A_SERVICE.read_text()
    assert _unit_value(text, "Restart") == ["no"]
    assert "RestartSec=" not in text
    assert "RestartPreventExitStatus=" not in text
    assert _unit_value(text, "ExecStopPost") == [
        "/usr/bin/systemctl --user --no-block start nansen-signal-lab-cohort.timer"
    ]


def test_program_a_absolute_cutoff_precedes_frozen_boundary_by_request_margin() -> None:
    text = PROGRAM_A_SERVICE.read_text()
    cutoff = datetime.fromisoformat("2026-08-20T10:43:30+00:00")
    boundary = datetime.fromisoformat("2026-08-20T10:45:00+00:00")
    assert cutoff.tzinfo == timezone.utc
    assert (boundary - cutoff).total_seconds() >= 60
    assert PROGRAM_A_STOP_BEFORE == boundary
    assert REQUEST_START_CUTOFF == datetime.fromisoformat("2026-08-20T10:42:00+00:00")
    assert (cutoff - REQUEST_START_CUTOFF).total_seconds() == 90
    assert text.count("2026-08-20T10:43:30Z") == 2
    assert "/usr/bin/timeout --foreground --signal=TERM --kill-after=5s" in text
    assert "TimeoutStartSec=2d" in text
    assert "TimeoutStopSec=10s" in text


def test_cohort_and_program_a_share_the_same_provider_lock() -> None:
    program_unit = PROGRAM_A_SERVICE.read_text()
    cohort_unit = COHORT_SERVICE.read_text()
    supervisor = SUPERVISOR.read_text()
    for unit in (program_unit, cohort_unit):
        assert "RuntimeDirectory=nansen-signal-lab-provider" in unit or (
            "RuntimeDirectory=nansen-signal-lab-cohort nansen-signal-lab-provider"
            in unit
        )
        assert "ReadWritePaths=%t/nansen-signal-lab-provider" in unit
    assert '"nansen-signal-lab-provider" / "provider.lock"' in supervisor


def test_program_a_unit_keeps_secret_out_of_unit_text() -> None:
    text = PROGRAM_A_SERVICE.read_text()
    assert "EnvironmentFile=" in text
    assert "NANSEN_API_KEY=" not in text


def test_systemd_units_verify() -> None:
    executable = shutil.which("systemd-analyze")
    if executable is None:
        pytest.skip("systemd-analyze is unavailable")
    subprocess.run(
        [
            executable,
            "verify",
            str(COHORT_SERVICE),
            str(COHORT_TIMER),
            str(PROGRAM_A_SERVICE),
        ],
        check=True,
    )
