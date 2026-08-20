#!/usr/bin/env python3
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
from typing import Any, Callable, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from programs.nansen_rapid_research_v1.design import (
    PREDECISION_TRANSPORT_CUTOFF,
    SCHEDULE,
    SETTLEMENT_OFFSET,
    SETTLEMENT_TRANSPORT_CUTOFF,
    START_GRACE,
)


RETIRED_UNITS = (
    "nansen-signal-lab-cohort.timer",
    "nansen-signal-lab-cohort.service",
    "nansen-signal-lab-parallel-strategy.timer",
    "nansen-signal-lab-parallel-strategy.service",
)
RETIRED_PROCESS_MARKERS = (
    "scripts/prospective_cohort_timer.py",
    "scripts/nansen_parallel_strategy_timer.py",
    "scripts/nansen_parallel_strategy.py predecision",
    "scripts/nansen_parallel_strategy.py settlement",
    "nansen-lab cohort-start-cycle",
    "nansen-lab cohort-settle-cycle",
)


@dataclass(frozen=True)
class DueAction:
    command: str
    cycle_index: int | None
    due_at: datetime
    requires_network: bool


def _utc(value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("rapid-research supervisor time must be timezone-aware")
    return value.astimezone(timezone.utc)


def _object(path: Path, *, required: bool = True) -> dict[str, Any] | None:
    if not path.exists() and not required:
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"supervisor input must be a regular file: {path}")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"supervisor input is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"supervisor input must be an object: {path}")
    return value


def _phase_cycles(phase: str) -> tuple[Any, ...]:
    cycles = tuple(cycle for cycle in SCHEDULE if cycle.phase == phase)
    if not cycles:
        raise ValueError(f"rapid-research schedule has no {phase} cycles")
    return cycles


def _cycle_state(root: Path, index: int) -> dict[str, Any] | None:
    return _object(
        root / "cycles" / f"cycle-{index:03d}" / "state.json",
        required=False,
    )


def _predecision_epoch_exists(root: Path, index: int) -> bool:
    budget_root = root / "budget"
    if not budget_root.exists():
        return False
    if budget_root.is_symlink() or not budget_root.is_dir():
        raise ValueError("rapid-research budget root must be a directory")
    found = False
    for namespace in budget_root.iterdir():
        if namespace.is_symlink():
            raise ValueError("rapid-research budget namespace cannot be a symlink")
        if not namespace.is_dir():
            continue
        epoch = namespace / "epochs" / f"c{index:03d}-predecision"
        if not epoch.exists():
            continue
        if epoch.is_symlink() or not epoch.is_dir():
            raise ValueError("rapid-research predecision epoch must be a directory")
        found = True
    return found


def _parse_time(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} is absent")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is invalid") from exc
    return _utc(parsed)


def _predecision_action(
    root: Path,
    cycle: Any,
    state: dict[str, Any] | None,
    current: datetime,
) -> DueAction | None:
    if current < cycle.scheduled_at:
        return None
    stage = None if state is None else state.get("stage")
    if state is not None and state.get("cycle_index") != cycle.index:
        raise ValueError("cycle state identity differs")
    if stage == "panel_sealed":
        live = current < cycle.scheduled_at + PREDECISION_TRANSPORT_CUTOFF
    elif stage in {None, "planned"}:
        resumed = _predecision_epoch_exists(root, cycle.index)
        live = (
            current < cycle.scheduled_at + PREDECISION_TRANSPORT_CUTOFF
            if resumed
            else current <= cycle.scheduled_at + START_GRACE
        )
    else:
        raise ValueError("predecision cycle stage is unsupported")
    return DueAction("predecision", cycle.index, cycle.scheduled_at, live)


def _settlement_action(
    root: Path,
    cycle: Any,
    current: datetime,
) -> DueAction | None:
    decisions = _object(
        root
        / "cycles"
        / f"cycle-{cycle.index:03d}"
        / "derived/decisions.json"
    )
    assert decisions is not None
    t0 = _parse_time(decisions.get("decision_t0"), field="sealed decision t0")
    earliest = t0 + SETTLEMENT_OFFSET
    if current < earliest:
        return None
    return DueAction(
        "settlement",
        cycle.index,
        earliest,
        current < cycle.scheduled_at + SETTLEMENT_TRANSPORT_CUTOFF,
    )


def _first_due_cycle(
    root: Path,
    cycles: Iterable[Any],
    current: datetime,
) -> tuple[DueAction | None, bool]:
    terminal = True
    for cycle in cycles:
        state = _cycle_state(root, cycle.index)
        stage = None if state is None else state.get("stage")
        if state is not None and state.get("cycle_index") != cycle.index:
            raise ValueError("cycle state identity differs")
        if stage in {"outcome_sealed", "unscorable"}:
            continue
        terminal = False
        if stage in {None, "planned", "panel_sealed"}:
            return _predecision_action(root, cycle, state, current), terminal
        if stage == "decisions_sealed":
            return _settlement_action(root, cycle, current), terminal
        raise ValueError("cycle stage is unsupported")
    return None, terminal


def choose_due_action(manifest_path: Path, now: datetime) -> DueAction | None:
    if not manifest_path.is_absolute():
        raise ValueError("supervisor manifest path must be absolute")
    current = _utc(now)
    program = _object(manifest_path)
    assert program is not None
    root = manifest_path.parent
    discovery = _phase_cycles("discovery")
    validation = _phase_cycles("validation")
    if discovery[-1].index >= validation[0].index:
        raise ValueError("rapid-research phases are not ordered")

    stage = program.get("stage")
    if stage == "preregistered":
        due = SCHEDULE[0].scheduled_at - timedelta(hours=20)
        return None if current < due else DueAction("activate", None, due, False)
    if stage != "activated":
        raise ValueError("rapid-research manifest stage is unsupported")

    fatal_intent = root / "intents/program-fatal.json"
    fatal_seal = root / "seals/program-fatal.json"
    if fatal_intent.exists():
        intent = _object(fatal_intent)
        assert intent is not None
        cycle_index = intent.get("cycle_index")
        if (
            type(cycle_index) is not int
            or not 1 <= cycle_index <= len(SCHEDULE)
        ):
            raise ValueError("program fatal intent cycle is invalid")
        if fatal_seal.exists() and _object(fatal_seal) != intent:
            raise ValueError("program fatal seal differs from its intent")
        state = _cycle_state(root, cycle_index)
        if (
            not fatal_seal.exists()
            or state is None
            or state.get("stage") not in {"outcome_sealed", "unscorable"}
        ):
            return DueAction("repair-fatal", None, current, False)
        return None
    if fatal_seal.exists():
        raise ValueError("program fatal seal exists without its intent")

    reconciliation = root / "activation/operational-reconciliation.json"
    if not reconciliation.exists():
        due = SCHEDULE[0].scheduled_at - timedelta(hours=19)
        return (
            None
            if current < due
            else DueAction("reconcile", None, due, False)
        )

    action, discovery_terminal = _first_due_cycle(root, discovery, current)
    if action is not None or not discovery_terminal:
        return action

    family_path = root / "derived/discovery-family.json"
    family_seal = root / "seals/discovery-family.json"
    if not family_path.exists() or not family_seal.exists():
        return DueAction(
            "freeze-discovery", None, discovery[-1].scheduled_at, False
        )
    family = _object(family_path)
    assert family is not None
    if family.get("stage") == "unscorable":
        result_path = root / "derived/final-result.json"
        result_seal = root / "seals/final.json"
        return (
            None
            if result_path.exists() and result_seal.exists()
            else DueAction("finalize", None, discovery[-1].scheduled_at, False)
        )

    action, validation_terminal = _first_due_cycle(root, validation, current)
    if action is not None or not validation_terminal:
        return action
    result_path = root / "derived/final-result.json"
    result_seal = root / "seals/final.json"
    if not result_path.exists() or not result_seal.exists():
        return DueAction("finalize", None, validation[-1].scheduled_at, False)
    return None


def drain_due_actions(
    manifest_path: Path,
    *,
    clock: Callable[[], datetime],
    executor: Callable[[DueAction], None],
    max_actions: int = 256,
) -> tuple[DueAction, ...]:
    completed: list[DueAction] = []
    for _ in range(max_actions):
        action = choose_due_action(manifest_path, clock())
        if action is None:
            return tuple(completed)
        executor(action)
        following = choose_due_action(manifest_path, clock())
        if (
            following is not None
            and (following.command, following.cycle_index)
            == (action.command, action.cycle_index)
        ):
            raise RuntimeError("rapid-research action made no durable progress")
        completed.append(action)
    if choose_due_action(manifest_path, clock()) is not None:
        raise RuntimeError("rapid-research catch-up exceeded its bounded action limit")
    return tuple(completed)


def require_network_ready() -> None:
    clock = subprocess.run(
        ("/usr/bin/timedatectl", "show", "-p", "NTPSynchronized", "--value"),
        check=False,
        capture_output=True,
        text=True,
    )
    if clock.returncode or clock.stdout.strip() != "yes":
        raise RuntimeError("host clock is not NTP-synchronized")
    network = subprocess.run(
        ("/usr/bin/nm-online", "-q", "--timeout=30"), check=False
    )
    if network.returncode:
        raise RuntimeError("NetworkManager did not confirm network readiness")


def _unit_properties(unit: str) -> dict[str, str]:
    completed = subprocess.run(
        (
            "/usr/bin/systemctl",
            "--user",
            "show",
            unit,
            "--property=LoadState",
            "--property=ActiveState",
            "--property=UnitFileState",
            "--property=MainPID",
            "--no-pager",
        ),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise RuntimeError(f"cannot prove retired unit state: {unit}")
    properties: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    if set(properties) != {
        "LoadState",
        "ActiveState",
        "UnitFileState",
        "MainPID",
    }:
        raise RuntimeError(f"retired unit state is incomplete: {unit}")
    return properties


def require_retired_authority() -> None:
    for unit in RETIRED_UNITS:
        properties = _unit_properties(unit)
        if properties["LoadState"] == "not-found":
            continue
        if properties["LoadState"] != "loaded":
            raise RuntimeError(f"retired unit load state is unsafe: {unit}")
        if properties["ActiveState"] not in {"inactive", "failed"}:
            raise RuntimeError(f"retired unit is still active: {unit}")
        if properties["MainPID"] != "0":
            raise RuntimeError(f"retired unit still has a process: {unit}")
        if unit.endswith(".timer") and properties["UnitFileState"] not in {
            "disabled",
            "masked",
            "masked-runtime",
        }:
            raise RuntimeError(f"retired timer still has provider authority: {unit}")

    processes = subprocess.run(
        ("/usr/bin/ps", "-eo", "pid=,args="),
        check=False,
        capture_output=True,
        text=True,
    )
    if processes.returncode:
        raise RuntimeError("cannot prove retired provider processes are absent")
    for line in processes.stdout.splitlines():
        if any(marker in line for marker in RETIRED_PROCESS_MARKERS):
            raise RuntimeError("a retired Nansen process still has provider authority")


def execute(manifest: Path, action: DueAction) -> None:
    if action.command == "activate" or action.requires_network:
        require_retired_authority()
    if action.requires_network:
        require_network_ready()
    cli = REPO_ROOT / "scripts/nansen_rapid_research.py"
    command = [
        str(REPO_ROOT / ".venv/bin/python"),
        str(cli),
        action.command,
        "--manifest",
        str(manifest),
    ]
    if action.cycle_index is not None:
        command.extend(("--cycle", str(action.cycle_index)))
    print(
        json.dumps(
            {
                "action": action.command,
                "cycle_index": action.cycle_index,
                "due_at": action.due_at.isoformat().replace("+00:00", "Z"),
                "requires_network": action.requires_network,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
    if completed.returncode:
        raise RuntimeError(f"rapid-research action failed: {action.command}")

    verification = [
        str(REPO_ROOT / ".venv/bin/python"),
        str(cli),
        "check-cycle" if action.cycle_index is not None else "check",
        "--manifest",
        str(manifest),
    ]
    if action.cycle_index is not None:
        verification.extend(("--cycle", str(action.cycle_index)))
    checked = subprocess.run(verification, cwd=REPO_ROOT, check=False)
    if checked.returncode:
        raise RuntimeError(
            f"rapid-research post-action integrity check failed: {action.command}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Advance all due rapid parallel-research actions"
    )
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    manifest = args.manifest.resolve()
    if args.dry_run:
        action = choose_due_action(manifest, datetime.now(timezone.utc))
        print(
            json.dumps(
                None if action is None else action.__dict__,
                default=str,
                sort_keys=True,
            )
        )
        return 0

    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    directory = runtime / "nansen-signal-lab-rapid-research"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "supervisor.lock").open("a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        drain_due_actions(
            manifest,
            clock=lambda: datetime.now(timezone.utc),
            executor=lambda action: execute(manifest, action),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
