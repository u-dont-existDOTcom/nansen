from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.prospective_cohort_timer import choose_due_action, drain_due_actions


FIRST = datetime(2026, 8, 18, 15, 5, tzinfo=timezone.utc)


def _program(tmp_path: Path) -> Path:
    root = tmp_path / "program"
    root.mkdir()
    path = root / "program.json"
    path.write_text(
        json.dumps(
            {
                "schedule": [
                    {
                        "cycle_index": index,
                        "cycle_id": f"cycle-{index:02d}",
                        "scheduled_at": (
                            FIRST + timedelta(hours=44 * (index - 1))
                        ).isoformat().replace("+00:00", "Z"),
                    }
                    for index in range(1, 33)
                ]
            }
        )
    )
    return path


def _state(program: Path, index: int, stage: str) -> Path:
    root = program.parent / "cycles" / f"cycle-{index:02d}"
    root.mkdir(parents=True)
    (root / "state.json").write_text(
        json.dumps({"cycle_index": index, "stage": stage})
    )
    return root


def test_future_cycle_has_no_due_action(tmp_path):
    program = _program(tmp_path).absolute()
    assert choose_due_action(program, FIRST - timedelta(seconds=1)) is None


def test_missing_due_cycle_starts(tmp_path):
    program = _program(tmp_path).absolute()
    action = choose_due_action(program, FIRST)
    assert action is not None
    assert (action.command, action.cycle_index, action.due_at) == (
        "cohort-start-cycle",
        1,
        FIRST,
    )
    assert action.requires_network is True


def test_late_missing_cycle_terminalizes_without_network(tmp_path):
    program = _program(tmp_path).absolute()
    action = choose_due_action(program, FIRST + timedelta(minutes=15, seconds=1))
    assert action is not None
    assert (action.command, action.cycle_index) == ("cohort-start-cycle", 1)
    assert action.requires_network is False


def test_terminal_cycle_advances_to_next_due_cycle(tmp_path):
    program = _program(tmp_path).absolute()
    _state(program, 1, "unscorable")
    second = FIRST + timedelta(hours=44)
    action = choose_due_action(program, second)
    assert action is not None
    assert (action.command, action.cycle_index) == ("cohort-start-cycle", 2)
    assert action.requires_network is True


def test_decision_cycle_waits_for_archived_settlement_boundary(tmp_path):
    program = _program(tmp_path).absolute()
    root = _state(program, 1, "decisions_sealed")
    earliest = FIRST + timedelta(hours=4, minutes=26)
    (root / "derived").mkdir()
    (root / "derived/decisions.json").write_text(
        json.dumps(
            {
                "windows": {
                    "earliest_settlement": earliest.isoformat().replace(
                        "+00:00", "Z"
                    )
                }
            }
        )
    )
    assert choose_due_action(program, earliest - timedelta(seconds=1)) is None
    action = choose_due_action(program, earliest)
    assert action is not None
    assert (action.command, action.cycle_index, action.due_at) == (
        "cohort-settle-cycle",
        1,
        earliest,
    )
    assert action.requires_network is True


def test_relative_manifest_is_rejected(tmp_path, monkeypatch):
    program = _program(tmp_path)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="absolute program path"):
        choose_due_action(program.relative_to(tmp_path), FIRST)


def test_catch_up_reaches_current_live_cycle_in_one_invocation(tmp_path):
    program = _program(tmp_path).absolute()
    current_index = 20
    now = FIRST + timedelta(hours=44 * (current_index - 1))
    observed = []

    def terminalize(_program, action, _now):
        observed.append(action)
        _state(program, action.cycle_index, "unscorable")

    completed = drain_due_actions(
        program,
        clock=lambda: now,
        executor=terminalize,
    )
    assert completed == tuple(observed)
    assert len(completed) == current_index
    assert all(not action.requires_network for action in completed[:-1])
    assert completed[-1].cycle_index == current_index
    assert completed[-1].requires_network is True


def test_catch_up_stops_when_command_makes_no_progress(tmp_path):
    program = _program(tmp_path).absolute()
    with pytest.raises(RuntimeError, match="made no state progress"):
        drain_due_actions(
            program,
            clock=lambda: FIRST,
            executor=lambda _program, _action, _now: None,
        )
