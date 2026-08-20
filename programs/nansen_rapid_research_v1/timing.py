from __future__ import annotations

from datetime import datetime, timedelta

from .design import (
    B2DesignError,
    DECISION_DEADLINE,
    PREDECISION_TRANSPORT_CUTOFF,
    SCHEDULE,
    SETTLEMENT_HARD_STOP,
    SETTLEMENT_OFFSET,
    SETTLEMENT_TRANSPORT_CUTOFF,
    START_GRACE,
    ScheduledCycle,
    _utc,
)


def start_state(cycle: ScheduledCycle, now: datetime) -> str:
    if cycle not in SCHEDULE:
        raise B2DesignError("cycle is outside the frozen schedule")
    current = _utc(now, field="current time")
    if current < cycle.scheduled_at:
        return "wait"
    if current <= cycle.scheduled_at + START_GRACE:
        return "start"
    return "missed"


def predecision_transport_allowed(cycle: ScheduledCycle, now: datetime) -> bool:
    current = _utc(now, field="current time")
    return (
        cycle.scheduled_at <= current
        < cycle.scheduled_at + PREDECISION_TRANSPORT_CUTOFF
    )


def decision_t0(cycle: ScheduledCycle, decision_sealed_at: datetime) -> datetime:
    sealed = _utc(decision_sealed_at, field="decision_sealed_at")
    if not cycle.scheduled_at <= sealed <= cycle.scheduled_at + DECISION_DEADLINE:
        raise B2DesignError("decision seal is outside the frozen cycle window")
    boundary = sealed.replace(second=0, microsecond=0)
    boundary -= timedelta(minutes=boundary.minute % 5)
    return boundary + timedelta(minutes=5)


def settlement_state(
    cycle: ScheduledCycle, *, t0: datetime, now: datetime
) -> str:
    admitted_t0 = _utc(t0, field="t0")
    earliest_t0 = cycle.scheduled_at + timedelta(minutes=5)
    latest_t0 = cycle.scheduled_at + timedelta(minutes=50)
    if (
        admitted_t0 < earliest_t0
        or admitted_t0 > latest_t0
        or admitted_t0.second
        or admitted_t0.microsecond
        or admitted_t0.minute % 5
    ):
        raise B2DesignError("t0 is not an admissible boundary for this cycle")
    start = admitted_t0 + SETTLEMENT_OFFSET
    current = _utc(now, field="current time")
    cutoff = cycle.scheduled_at + SETTLEMENT_TRANSPORT_CUTOFF
    if current >= cutoff:
        return "missed"
    if current < start:
        return "wait"
    return "settle"


def settlement_hard_stop(cycle: ScheduledCycle) -> datetime:
    return cycle.scheduled_at + SETTLEMENT_HARD_STOP
