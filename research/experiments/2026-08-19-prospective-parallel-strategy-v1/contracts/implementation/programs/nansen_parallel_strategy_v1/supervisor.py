from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .design import (
    PREDECISION_TRANSPORT_CUTOFF,
    SCHEDULE,
    SETTLEMENT_OFFSET,
    SETTLEMENT_TRANSPORT_CUTOFF,
    START_GRACE,
)


@dataclass(frozen=True)
class DueAction:
    command: str
    cycle_index: int | None
    due_at: datetime
    requires_network: bool


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("supervisor time must be timezone-aware")
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


def _cycle_state(root: Path, index: int) -> dict[str, Any] | None:
    return _object(root / "cycles" / f"cycle-{index:03d}" / "state.json", required=False)


def _predecision_epoch_exists(root: Path, index: int) -> bool:
    path = (
        root
        / "budget/parallel-strategy-v1/epochs"
        / f"c{index:03d}-predecision"
    )
    if not path.exists():
        return False
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"predecision epoch must be a directory: {path}")
    return True


def _parse(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("sealed decision t0 is absent")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("sealed decision t0 is invalid") from exc
    return _utc(parsed)


def choose_due_action(manifest_path: Path, now: datetime) -> DueAction | None:
    if not manifest_path.is_absolute():
        raise ValueError("supervisor manifest path must be absolute")
    current = _utc(now)
    program = _object(manifest_path)
    assert program is not None
    root = manifest_path.parent
    stage = program.get("stage")
    if stage == "preregistered":
        # Activation is operational-only and safely retries until v1 is terminal.
        due = SCHEDULE[0].scheduled_at - timedelta(hours=20)
        return None if current < due else DueAction("activate", None, due, False)
    if stage != "activated":
        raise ValueError("parallel-strategy manifest stage is unsupported")
    fatal_intent = root / "intents/program-fatal.json"
    fatal_seal = root / "seals/program-fatal.json"
    if fatal_intent.exists():
        intent = _object(fatal_intent)
        assert intent is not None
        cycle_index = intent.get("cycle_index")
        if (
            not isinstance(cycle_index, int)
            or isinstance(cycle_index, bool)
            or not 1 <= cycle_index <= len(SCHEDULE)
        ):
            raise ValueError("program fatal intent cycle is invalid")
        if fatal_seal.exists() and _object(fatal_seal) != intent:
            raise ValueError("program fatal seal differs from its intent")
        state = _cycle_state(root, cycle_index)
        if not fatal_seal.exists() or state is None or state.get("stage") not in {
            "outcome_sealed",
            "unscorable",
        }:
            # The repair command reads the durable fatal intent and therefore
            # takes no caller-supplied cycle argument.  Keeping this action
            # program-scoped also prevents the timer from constructing an
            # unsupported ``--cycle`` CLI argument.
            return DueAction("repair-fatal", None, current, False)
        return None
    if fatal_seal.exists():
        raise ValueError("program fatal seal exists without its intent")
    reconciliation = root / "activation/operational-reconciliation.json"
    if not reconciliation.exists():
        return DueAction("reconcile", None, SCHEDULE[0].scheduled_at - timedelta(hours=19), False)

    discovery_terminal = True
    for cycle in SCHEDULE[:42]:
        state = _cycle_state(root, cycle.index)
        if state is None:
            discovery_terminal = False
            if current >= cycle.scheduled_at:
                resumed = _predecision_epoch_exists(root, cycle.index)
                return DueAction(
                    "predecision",
                    cycle.index,
                    cycle.scheduled_at,
                    (
                        current < cycle.scheduled_at
                        + PREDECISION_TRANSPORT_CUTOFF
                        if resumed
                        else current <= cycle.scheduled_at + START_GRACE
                    ),
                )
            return None
        if state.get("cycle_index") != cycle.index:
            raise ValueError("cycle state identity differs")
        cycle_stage = state.get("stage")
        if cycle_stage in {"outcome_sealed", "unscorable"}:
            continue
        discovery_terminal = False
        if cycle_stage in {"planned", "panel_sealed"}:
            if current >= cycle.scheduled_at:
                return DueAction(
                    "predecision",
                    cycle.index,
                    cycle.scheduled_at,
                    current < cycle.scheduled_at + PREDECISION_TRANSPORT_CUTOFF,
                )
            return None
        if cycle_stage == "decisions_sealed":
            decisions = _object(root / "cycles" / f"cycle-{cycle.index:03d}" / "derived/decisions.json")
            assert decisions is not None
            t0 = _parse(decisions.get("decision_t0"))
            earliest = t0 + SETTLEMENT_OFFSET
            if current >= earliest:
                return DueAction(
                    "settlement",
                    cycle.index,
                    earliest,
                    current < cycle.scheduled_at + SETTLEMENT_TRANSPORT_CUTOFF,
                )
            return None
        raise ValueError("cycle stage is unsupported")

    family_path = root / "derived/discovery-family.json"
    family_seal_path = root / "seals/discovery-family.json"
    if discovery_terminal and (
        not family_path.exists() or not family_seal_path.exists()
    ):
        return DueAction("freeze-discovery", None, SCHEDULE[41].scheduled_at, False)
    family = _object(family_path) if family_path.exists() else None
    if family is not None and family.get("stage") == "unscorable":
        final_path = root / "derived/final-result.json"
        final_seal = root / "seals/final.json"
        return (
            None
            if final_path.exists() and final_seal.exists()
            else DueAction("finalize", None, SCHEDULE[41].scheduled_at, False)
        )

    for cycle in SCHEDULE[42:]:
        state = _cycle_state(root, cycle.index)
        if state is None:
            if current >= cycle.scheduled_at:
                resumed = _predecision_epoch_exists(root, cycle.index)
                return DueAction(
                    "predecision",
                    cycle.index,
                    cycle.scheduled_at,
                    (
                        current < cycle.scheduled_at
                        + PREDECISION_TRANSPORT_CUTOFF
                        if resumed
                        else current <= cycle.scheduled_at + START_GRACE
                    ),
                )
            return None
        if state.get("cycle_index") != cycle.index:
            raise ValueError("cycle state identity differs")
        cycle_stage = state.get("stage")
        if cycle_stage in {"outcome_sealed", "unscorable"}:
            continue
        if cycle_stage in {"planned", "panel_sealed"}:
            return DueAction(
                "predecision",
                cycle.index,
                cycle.scheduled_at,
                current < cycle.scheduled_at + PREDECISION_TRANSPORT_CUTOFF,
            ) if current >= cycle.scheduled_at else None
        if cycle_stage == "decisions_sealed":
            decisions = _object(root / "cycles" / f"cycle-{cycle.index:03d}" / "derived/decisions.json")
            assert decisions is not None
            t0 = _parse(decisions.get("decision_t0"))
            earliest = t0 + SETTLEMENT_OFFSET
            return DueAction(
                "settlement",
                cycle.index,
                earliest,
                current < cycle.scheduled_at + SETTLEMENT_TRANSPORT_CUTOFF,
            ) if current >= earliest else None
        raise ValueError("cycle stage is unsupported")

    final_path = root / "derived/final-result.json"
    final_seal = root / "seals/final.json"
    if not final_path.exists() or not final_seal.exists():
        return DueAction("finalize", None, SCHEDULE[-1].scheduled_at, False)
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
        state_before = tuple(
            path.read_bytes()
            for path in sorted(manifest_path.parent.rglob("state.json"))
            if path.is_file() and not path.is_symlink()
        )
        manifest_before = manifest_path.read_bytes()
        executor(action)
        state_after = tuple(
            path.read_bytes()
            for path in sorted(manifest_path.parent.rglob("state.json"))
            if path.is_file() and not path.is_symlink()
        )
        if state_before == state_after and manifest_before == manifest_path.read_bytes():
            # Offline actions write non-state artifacts; acknowledge those exact cases.
            if action.command not in {
                "reconcile",
                "repair-fatal",
                "freeze-discovery",
                "finalize",
            }:
                raise RuntimeError("parallel-strategy action made no durable progress")
        completed.append(action)
    if choose_due_action(manifest_path, clock()) is not None:
        raise RuntimeError("parallel-strategy catch-up exceeded its bounded action limit")
    return tuple(completed)
