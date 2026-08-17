from __future__ import annotations

import copy
import hashlib
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .evaluation import (
    EvaluationBundle,
    Predicate,
    TheorySpec,
    load_evaluation_manifest,
    predicate_matches,
)


class ComparatorError(RuntimeError):
    pass


@dataclass(frozen=True)
class ComparatorDecision:
    decision_id: str
    theory_id: str
    role: str
    variant: str
    action: str | None
    availability: str
    applicable: bool
    veto_theory_id: str | None
    veto_triggered: bool | None
    reasons: tuple[str, ...]


def load_frozen_records(
    manifest_path: str | Path,
    expected_sha256: str,
) -> tuple[dict[str, Any], ...]:
    """Verify the frozen manifest bytes before invoking the semantic loader."""

    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ComparatorError("expected manifest checksum must be lowercase SHA-256")

    requested = Path(os.path.abspath(os.fspath(manifest_path)))
    if requested.is_symlink() or not requested.is_file():
        raise ComparatorError(f"frozen manifest must be a regular non-symlink file: {requested}")
    try:
        raw = requested.read_bytes()
    except OSError as exc:
        raise ComparatorError(f"cannot read frozen manifest {requested}: {exc}") from exc
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ComparatorError(
            "frozen manifest checksum mismatch: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )

    try:
        bundle = load_evaluation_manifest(requested)
    except Exception as exc:
        raise ComparatorError(str(exc)) from exc
    records = bundle.manifest.get("theories")
    if not isinstance(records, list) or len(records) != 6:
        raise ComparatorError("frozen comparator manifest must contain exactly six theories")
    return tuple(copy.deepcopy(record) for record in records)


def _utc_timestamp(value: Any, *, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ComparatorError(f"{label} must be a valid timestamp") from exc
    else:
        raise ComparatorError(f"{label} must be a timezone-aware timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ComparatorError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _scalar_kind(value: Any) -> str | None:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if _finite_number(value):
        return "number"
    return None


def _unavailable_reason(
    predicate: Predicate,
    *,
    current_at: datetime,
    rows: dict[datetime, dict[str, Any]],
) -> str | None:
    target_at = current_at - timedelta(hours=predicate.lag_hours)
    row = rows.get(target_at)
    if row is None:
        return f"missing exact lag-{predicate.lag_hours}h row at {target_at.isoformat()}"
    if predicate.feature not in row or row[predicate.feature] is None:
        return f"missing feature {predicate.feature} at lag {predicate.lag_hours}h"
    actual = row[predicate.feature]
    if actual == "unavailable":
        return f"feature {predicate.feature} is unavailable"
    if isinstance(actual, float) and not math.isfinite(actual):
        return f"feature {predicate.feature} is non-finite"
    if predicate.operator in {"gt", "gte", "lt", "lte"}:
        if not _finite_number(actual):
            return f"feature {predicate.feature} is not a finite number"
    elif predicate.operator == "eq":
        if _scalar_kind(actual) != _scalar_kind(predicate.value):
            return f"feature {predicate.feature} has an incompatible scalar type"
    elif predicate.operator == "in":
        if not isinstance(predicate.value, (list, tuple)) or not any(
            _scalar_kind(actual) == _scalar_kind(candidate)
            for candidate in predicate.value
        ):
            return f"feature {predicate.feature} has an incompatible scalar type"
    return None


def _unavailable_decision(theory: TheorySpec, reason: str) -> ComparatorDecision:
    return ComparatorDecision(
        decision_id=f"{theory.id}::base",
        theory_id=theory.id,
        role=theory.role,
        variant="base",
        action=None,
        availability="UNAVAILABLE",
        applicable=False,
        veto_theory_id=None,
        veto_triggered=None,
        reasons=(reason,),
    )


def evaluate_comparators(
    bundle: EvaluationBundle,
    current: dict[str, Any],
    prior: dict[str, Any] | None,
    *,
    available_at: datetime,
) -> tuple[ComparatorDecision, ...]:
    if not isinstance(bundle, EvaluationBundle):
        raise ComparatorError("bundle must be an EvaluationBundle")
    available_utc = _utc_timestamp(available_at, label="available_at")
    required_current_at = available_utc.replace(minute=0, second=0, microsecond=0)

    if not isinstance(current, dict) or "timestamp" not in current:
        current_error = "current feature row or timestamp is missing"
        current_at = None
    else:
        try:
            current_at = _utc_timestamp(current["timestamp"], label="current timestamp")
        except ComparatorError as exc:
            current_error = str(exc)
            current_at = None
        else:
            current_error = (
                "current feature row is not the latest completed hourly bucket: "
                f"expected {required_current_at.isoformat()}, got {current_at.isoformat()}"
                if current_at != required_current_at
                else None
            )

    rows: dict[datetime, dict[str, Any]] = {}
    if current_at is not None:
        rows[current_at] = current
    if prior is not None and isinstance(prior, dict) and "timestamp" in prior:
        try:
            prior_at = _utc_timestamp(prior["timestamp"], label="prior timestamp")
        except ComparatorError:
            pass
        else:
            rows[prior_at] = prior

    decisions: list[ComparatorDecision] = []
    for theory in bundle.theories:
        if current_error is not None or current_at is None:
            decisions.append(_unavailable_decision(theory, current_error))
            continue
        unavailable = next(
            (
                reason
                for predicate in theory.predicates
                if (
                    reason := _unavailable_reason(
                        predicate,
                        current_at=current_at,
                        rows=rows,
                    )
                )
                is not None
            ),
            None,
        )
        if unavailable is not None:
            decisions.append(_unavailable_decision(theory, unavailable))
            continue

        matches = tuple(
            predicate_matches(predicate, current=current, by_timestamp=rows)
            for predicate in theory.predicates
        )
        fires = all(matches)
        if theory.role == "veto":
            action = None
            applicable = False
            veto_triggered = fires
        else:
            action = "LONG" if fires else "ABSTAIN"
            applicable = fires
            veto_triggered = None
        failed = tuple(
            predicate.feature
            for predicate, matched in zip(theory.predicates, matches)
            if not matched
        )
        reasons = (
            ("all frozen predicates matched",)
            if fires
            else ("frozen predicates did not match: " + ", ".join(failed),)
        )
        decisions.append(
            ComparatorDecision(
                decision_id=f"{theory.id}::base",
                theory_id=theory.id,
                role=theory.role,
                variant="base",
                action=action,
                availability="AVAILABLE",
                applicable=applicable,
                veto_theory_id=None,
                veto_triggered=veto_triggered,
                reasons=reasons,
            )
        )

    for blocked in bundle.blocked_theories:
        decisions.append(
            ComparatorDecision(
                decision_id=f"{blocked.id}::blocked",
                theory_id=blocked.id,
                role="blocked",
                variant="blocked",
                action=None,
                availability="BLOCKED",
                applicable=False,
                veto_theory_id=None,
                veto_triggered=None,
                reasons=(blocked.reason, *blocked.missing_roles),
            )
        )
    return tuple(decisions)


def pair_distribution_veto(
    decisions: tuple[ComparatorDecision, ...] | list[ComparatorDecision],
) -> tuple[ComparatorDecision, ...]:
    originals = tuple(decisions)
    vetoes = [
        item
        for item in originals
        if item.variant == "base" and item.role == "veto"
    ]
    if len(vetoes) != 1:
        raise ComparatorError("exactly one base veto decision is required")
    veto = vetoes[0]

    variants: list[ComparatorDecision] = []
    for base in originals:
        if base.variant != "base" or base.role == "veto" or base.action != "LONG":
            continue
        decision_id = f"{base.theory_id}::paired::{veto.theory_id}"
        if veto.availability != "AVAILABLE" or veto.veto_triggered is None:
            action = None
            availability = "UNAVAILABLE"
            veto_triggered = None
            reason = "paired veto outcome is unavailable"
        elif veto.veto_triggered:
            action = "ABSTAIN"
            availability = "AVAILABLE"
            veto_triggered = True
            reason = "paired distribution veto suppressed the base LONG"
        else:
            action = "LONG"
            availability = "AVAILABLE"
            veto_triggered = False
            reason = "paired distribution veto was available and did not fire"
        variants.append(
            ComparatorDecision(
                decision_id=decision_id,
                theory_id=base.theory_id,
                role=base.role,
                variant="distribution_veto",
                action=action,
                availability=availability,
                applicable=True,
                veto_theory_id=veto.theory_id,
                veto_triggered=veto_triggered,
                reasons=(reason,),
            )
        )

    combined = originals + tuple(variants)
    ids = [item.decision_id for item in combined]
    if len(ids) != len(set(ids)):
        raise ComparatorError("comparator decision IDs must be unique")
    return combined
