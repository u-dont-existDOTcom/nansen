#!/usr/bin/env python3
"""Advance every currently due action for the frozen prospective cohort."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence


TERMINAL_STAGES = frozenset({"outcome_sealed", "unscorable"})
START_STAGES = frozenset({"planned", "universe_sealed", "features_sealed"})


@dataclass(frozen=True)
class DueAction:
    command: str
    cycle_index: int
    due_at: datetime
    requires_network: bool


def _utc(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _object(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular non-symlink file: {path}")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def choose_due_action(program_path: Path, now: datetime) -> DueAction | None:
    if not program_path.is_absolute():
        raise ValueError("the cohort timer requires an absolute program path")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("the cohort timer clock must be timezone-aware")
    current = now.astimezone(timezone.utc)
    program = _object(program_path, label="cohort program")
    schedule = program.get("schedule")
    if not isinstance(schedule, list) or len(schedule) != 32:
        raise ValueError("cohort program must contain exactly 32 scheduled cycles")

    previous_scheduled: datetime | None = None
    for expected_index, item in enumerate(schedule, start=1):
        if not isinstance(item, dict):
            raise ValueError("cohort schedule entries must be objects")
        cycle_index = item.get("cycle_index")
        cycle_id = item.get("cycle_id")
        if cycle_index != expected_index or cycle_id != f"cycle-{expected_index:02d}":
            raise ValueError("cohort schedule identity differs from its fixed order")
        scheduled = _utc(item.get("scheduled_at"), field="scheduled_at")
        if previous_scheduled is not None and scheduled <= previous_scheduled:
            raise ValueError("cohort schedule is not strictly increasing")
        previous_scheduled = scheduled

        state_path = program_path.parent / "cycles" / cycle_id / "state.json"
        if not state_path.exists():
            if current >= scheduled:
                return DueAction(
                    "cohort-start-cycle",
                    cycle_index,
                    scheduled,
                    current <= scheduled + timedelta(minutes=15),
                )
            return None

        state = _object(state_path, label=f"{cycle_id} state")
        if state.get("cycle_index") != cycle_index:
            raise ValueError(f"{cycle_id} state identity differs")
        stage = state.get("stage")
        if stage in TERMINAL_STAGES:
            continue
        if stage in START_STAGES:
            if current >= scheduled:
                live_deadline = scheduled + timedelta(
                    minutes=15 if stage == "planned" else 45
                )
                return DueAction(
                    "cohort-start-cycle",
                    cycle_index,
                    scheduled,
                    current <= live_deadline,
                )
            return None
        if stage == "decisions_sealed":
            decisions = _object(
                state_path.parent / "derived" / "decisions.json",
                label=f"{cycle_id} decisions",
            )
            windows = decisions.get("windows")
            if not isinstance(windows, dict):
                raise ValueError(f"{cycle_id} decisions have no execution windows")
            earliest = _utc(
                windows.get("earliest_settlement"),
                field="earliest_settlement",
            )
            if current >= earliest:
                return DueAction(
                    "cohort-settle-cycle",
                    cycle_index,
                    earliest,
                    True,
                )
            return None
        raise ValueError(f"{cycle_id} has an unsupported stage: {stage!r}")
    return None


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_checked(
    command: Sequence[str],
    *,
    cwd: Path,
    quiet_stdout: bool = False,
) -> None:
    completed = subprocess.run(
        tuple(command),
        cwd=cwd,
        check=False,
        stdout=subprocess.DEVNULL if quiet_stdout else None,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"cohort command failed with exit code {completed.returncode}: "
            + " ".join(command)
        )


def _require_synchronized_clock() -> None:
    completed = subprocess.run(
        ("/usr/bin/timedatectl", "show", "-p", "NTPSynchronized", "--value"),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0 or completed.stdout.strip() != "yes":
        raise RuntimeError("host clock is not NTP-synchronized")


def _require_network_online() -> None:
    completed = subprocess.run(
        ("/usr/bin/nm-online", "-q", "--timeout=30"),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("NetworkManager did not confirm network readiness")


def execute_action(
    program_path: Path,
    *,
    action: DueAction,
    now: datetime,
) -> None:
    _require_synchronized_clock()
    if action.requires_network:
        _require_network_online()

    repository = Path(__file__).resolve().parents[1]
    cli = repository / "nansen-lab"
    if cli.is_symlink() or not cli.is_file():
        raise RuntimeError(f"cohort CLI is unavailable: {cli}")
    live_command = (
        str(cli),
        action.command,
        "--program",
        str(program_path),
        "--cycle",
        str(action.cycle_index),
    )
    print(
        json.dumps(
            {
                "action": action.command,
                "cycle_index": action.cycle_index,
                "due_at": _timestamp(action.due_at),
                "invoked_at": _timestamp(now),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    _run_checked(live_command, cwd=repository)
    _run_checked(
        (
            str(cli),
            "cohort-check",
            "--program",
            str(program_path),
        ),
        cwd=repository,
        quiet_stdout=True,
    )


def _state_fingerprint(program_path: Path, cycle_index: int) -> bytes | None:
    path = (
        program_path.parent
        / "cycles"
        / f"cycle-{cycle_index:02d}"
        / "state.json"
    )
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"cycle state must be a regular file: {path}")
    return path.read_bytes()


def drain_due_actions(
    program_path: Path,
    *,
    clock: Callable[[], datetime],
    executor: Callable[[Path, DueAction, datetime], None],
    max_actions: int = 64,
) -> tuple[DueAction, ...]:
    if max_actions < 1:
        raise ValueError("max_actions must be positive")
    completed: list[DueAction] = []
    for _ in range(max_actions):
        now = clock()
        action = choose_due_action(program_path, now)
        if action is None:
            return tuple(completed)
        before = _state_fingerprint(program_path, action.cycle_index)
        executor(program_path, action, now)
        after = _state_fingerprint(program_path, action.cycle_index)
        if after == before:
            raise RuntimeError(
                f"cohort action made no state progress: {action.command} "
                f"cycle {action.cycle_index}"
            )
        completed.append(action)
    if choose_due_action(program_path, clock()) is not None:
        raise RuntimeError("cohort catch-up exceeded its bounded action limit")
    return tuple(completed)


def run_once(
    program_path: Path,
    *,
    now: datetime,
    dry_run: bool,
) -> DueAction | None:
    action = choose_due_action(program_path, now)
    if action is not None and not dry_run:
        execute_action(program_path, action=action, now=now)
    return action


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one currently due action for a frozen cohort program."
    )
    parser.add_argument("--program", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--now",
        help="RFC 3339 clock override; accepted only with --dry-run",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.now is not None and not args.dry_run:
        raise SystemExit("--now is permitted only with --dry-run")
    now = (
        datetime.now(timezone.utc)
        if args.now is None
        else _utc(args.now, field="--now")
    )
    if args.dry_run:
        action = run_once(args.program, now=now, dry_run=True)
        print(
            json.dumps(
                {
                    "action": None if action is None else action.command,
                    "cycle_index": None if action is None else action.cycle_index,
                    "due_at": None if action is None else _timestamp(action.due_at),
                    "requires_network": (
                        None if action is None else action.requires_network
                    ),
                    "observed_at": _timestamp(now),
                },
                sort_keys=True,
            )
        )
        return 0

    runtime_root = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    lock_path = runtime_root / "nansen-signal-lab-cohort" / "supervisor.lock"
    provider_lock_path = runtime_root / "nansen-signal-lab-provider" / "provider.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    provider_lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        with provider_lock_path.open("a", encoding="utf-8") as provider_lock:
            try:
                fcntl.flock(
                    provider_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB
                )
            except BlockingIOError:
                return 0
            drain_due_actions(
                args.program,
                clock=lambda: datetime.now(timezone.utc),
                executor=lambda program, action, observed: execute_action(
                    program,
                    action=action,
                    now=observed,
                ),
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
