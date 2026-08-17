from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .artifacts import (
    atomic_replace_bytes,
    canonical_json_bytes,
    write_bytes_once_or_adopt_exact,
    write_json_once,
)
from .client import NansenEvidenceResponse, NansenRequestFailure


class BudgetError(RuntimeError):
    pass


class BudgetCorruption(BudgetError):
    pass


@dataclass(frozen=True)
class BudgetReservation:
    logical_request_id: str
    reservation_id: str
    request_sha256: str
    endpoint: str
    expected_credits: int
    attempt_count: int
    state: str
    retry_not_before: str | None
    request_artifact_sha256: str | None
    response_artifact_sha256: str | None
    credit_cost: int | None
    credit_used: int | None
    credit_remaining: int | None


@dataclass(frozen=True)
class BudgetTotals:
    calls: int
    credits: int
    provider_remaining: int | None
    entries: tuple[BudgetReservation, ...]
    transition_sha256s: tuple[str, ...]
    journal_head_sha256: str | None
    halted_reason: str | None


_ENTRY_KEYS = {
    "logical_request_id",
    "reservation_id",
    "request_sha256",
    "endpoint",
    "expected_credits",
    "attempt_count",
    "state",
    "retry_not_before",
    "request_artifact_sha256",
    "response_artifact_sha256",
    "credit_cost",
    "credit_used",
    "credit_remaining",
}
_COUNTED_STATES = {"reserved", "retryable_zero", "confirmed_used", "ambiguous"}
_STATES = _COUNTED_STATES | {"confirmed_zero", "failed_before_pricing"}
_JOURNAL_PATTERN = re.compile(r"^(\d{6})-([0-9a-f]{64})\.json$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _normalized_endpoint(endpoint: str) -> str:
    normalized = endpoint.strip("/")
    if not normalized or "://" in normalized or "?" in normalized or "#" in normalized:
        raise BudgetError("endpoint must be a normalized relative endpoint ID")
    return normalized


def canonical_request_sha256(
    method: str, endpoint: str, payload: dict[str, Any] | None
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "method": method.upper(),
                "normalized_relative_endpoint": _normalized_endpoint(endpoint),
                "payload": payload,
            }
        )
    ).hexdigest()


def _require_hash(value: str, label: str) -> None:
    if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
        raise BudgetError(f"{label} must be a lowercase SHA-256")


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BudgetError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str, label: str) -> datetime:
    if not isinstance(value, str):
        raise BudgetError(f"{label} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BudgetError(f"{label} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BudgetError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _reservation_id(logical_request_id: str) -> str:
    return hashlib.sha256(logical_request_id.encode("utf-8")).hexdigest()[:24]


def _entry_dict(reservation: BudgetReservation) -> dict[str, Any]:
    return {
        key: getattr(reservation, key)
        for key in (
            "logical_request_id",
            "reservation_id",
            "request_sha256",
            "endpoint",
            "expected_credits",
            "attempt_count",
            "state",
            "retry_not_before",
            "request_artifact_sha256",
            "response_artifact_sha256",
            "credit_cost",
            "credit_used",
            "credit_remaining",
        )
    }


def _reservation_from_dict(value: Any) -> BudgetReservation:
    if not isinstance(value, dict) or set(value) != _ENTRY_KEYS:
        raise BudgetCorruption("journal entry has invalid keys")
    if not isinstance(value["logical_request_id"], str) or not value["logical_request_id"]:
        raise BudgetCorruption("journal logical request ID is invalid")
    if value["reservation_id"] != _reservation_id(value["logical_request_id"]):
        raise BudgetCorruption("journal reservation ID is invalid")
    if not isinstance(value["request_sha256"], str) or _HASH_PATTERN.fullmatch(value["request_sha256"]) is None:
        raise BudgetCorruption("journal request hash is invalid")
    if not isinstance(value["endpoint"], str) or _normalized_endpoint(value["endpoint"]) != value["endpoint"]:
        raise BudgetCorruption("journal endpoint is invalid")
    if (
        not isinstance(value["expected_credits"], int)
        or isinstance(value["expected_credits"], bool)
        or value["expected_credits"] < 0
    ):
        raise BudgetCorruption("journal expected credits are invalid")
    if value["attempt_count"] not in {1, 2} or value["state"] not in _STATES:
        raise BudgetCorruption("journal attempt or state is invalid")
    if value["retry_not_before"] is not None:
        _parse_time(value["retry_not_before"], "retry deadline")
    for name in ("request_artifact_sha256", "response_artifact_sha256"):
        digest = value[name]
        if digest is not None and (not isinstance(digest, str) or _HASH_PATTERN.fullmatch(digest) is None):
            raise BudgetCorruption(f"journal {name} is invalid")
    for name in ("credit_cost", "credit_used", "credit_remaining"):
        number = value[name]
        if number is not None and (
            not isinstance(number, int) or isinstance(number, bool) or number < 0
        ):
            raise BudgetCorruption(f"journal {name} is invalid")
    return BudgetReservation(**value)


def _initial_head(max_calls: int, max_credits: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "max_calls": max_calls,
        "max_credits": max_credits,
        "journal_head_sha256": None,
        "entries": [],
    }


def _head_document(
    max_calls: int,
    max_credits: int,
    entries: list[BudgetReservation],
    journal_head_sha256: str | None,
    provider_remaining: int | None,
    halted_reason: str | None,
) -> dict[str, Any]:
    head = _initial_head(max_calls, max_credits)
    head["journal_head_sha256"] = journal_head_sha256
    head["entries"] = [_entry_dict(entry) for entry in entries]
    if provider_remaining is not None:
        head["provider_remaining"] = provider_remaining
    if halted_reason is not None:
        head["halted_reason"] = halted_reason
    return head


def _replace_entry(
    entries: list[BudgetReservation], new_entry: BudgetReservation
) -> list[BudgetReservation]:
    updated = list(entries)
    for index, existing in enumerate(updated):
        if existing.logical_request_id == new_entry.logical_request_id:
            updated[index] = new_entry
            return updated
    updated.append(new_entry)
    return updated


def _validate_transition(
    operation: str,
    previous: BudgetReservation | None,
    current: BudgetReservation,
) -> None:
    expected_states = {
        "reserve": (None, "reserved"),
        "bind_request_artifact": ("reserved", "reserved"),
        "bind_recovered_response": ("reserved", "reserved"),
        "confirm": ("reserved", {"confirmed_zero", "confirmed_used", "ambiguous"}),
        "fail": ("reserved", {"failed_before_pricing", "confirmed_used", "ambiguous"}),
        "mark_retryable_zero": ("reserved", "retryable_zero"),
        "begin_retry": ("retryable_zero", "reserved"),
        "reconcile_inflight": ("reserved", "ambiguous"),
    }
    if operation not in expected_states:
        raise BudgetCorruption("journal operation is invalid")
    before_state, after_state = expected_states[operation]
    actual_before = None if previous is None else previous.state
    allowed_after = {after_state} if isinstance(after_state, str) else after_state
    if actual_before != before_state or current.state not in allowed_after:
        raise BudgetCorruption("illegal budget state transition")
    if operation == "reserve":
        if current.attempt_count != 1:
            raise BudgetCorruption("initial reservation has invalid attempt count")
        return
    assert previous is not None
    immutable = (
        "logical_request_id",
        "reservation_id",
        "request_sha256",
        "endpoint",
        "expected_credits",
    )
    if any(getattr(previous, name) != getattr(current, name) for name in immutable):
        raise BudgetCorruption("journal changed immutable reservation identity")
    if operation == "begin_retry":
        if previous.attempt_count != 1 or current.attempt_count != 2:
            raise BudgetCorruption("journal contains an illegal retry")
    elif current.attempt_count != previous.attempt_count:
        raise BudgetCorruption("journal changed attempt count outside retry")


class BudgetGuard:
    def __init__(self, root: Path, max_calls: int = 10, max_credits: int = 10):
        if (
            not isinstance(max_calls, int)
            or isinstance(max_calls, bool)
            or max_calls <= 0
            or not isinstance(max_credits, int)
            or isinstance(max_credits, bool)
            or max_credits <= 0
        ):
            raise BudgetError("budget ceilings must be positive integers")
        self.root = Path(root)
        self.max_calls = max_calls
        self.max_credits = max_credits
        self.budget_root = self.root / "budget"
        self.journal_root = self.budget_root / "journal"
        self.head_path = self.budget_root / "head.json"
        self.snapshot_root = self.budget_root / "snapshots"
        self.budget_root.mkdir(parents=True, exist_ok=True)
        self.journal_root.mkdir(parents=True, exist_ok=True)
        with self._lock():
            if not self.head_path.exists():
                atomic_replace_bytes(
                    self.head_path,
                    canonical_json_bytes(_initial_head(max_calls, max_credits)),
                )

    @contextmanager
    def _lock(self) -> Iterator[None]:
        descriptor = os.open(self.budget_root, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read_head_locked(self) -> dict[str, Any]:
        try:
            raw = self.head_path.read_bytes()
            head = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise BudgetCorruption("budget head is unreadable") from exc
        if not isinstance(head, dict) or canonical_json_bytes(head) != raw:
            raise BudgetCorruption("budget head is not canonical JSON")
        if head.get("schema_version") != 1:
            raise BudgetCorruption("budget head schema is invalid")
        if head.get("max_calls") != self.max_calls or head.get("max_credits") != self.max_credits:
            raise BudgetCorruption("budget head ceiling mismatch")
        return head

    def _replay_locked(
        self,
    ) -> tuple[list[BudgetReservation], list[str], int | None, str | None]:
        paths = sorted(self.journal_root.glob("*.json"))
        entries: list[BudgetReservation] = []
        hashes: list[str] = []
        provider_remaining: int | None = None
        halted_reason: str | None = None
        prefix_heads = [_initial_head(self.max_calls, self.max_credits)]
        for sequence, path in enumerate(paths, start=1):
            match = _JOURNAL_PATTERN.fullmatch(path.name)
            if match is None or int(match.group(1)) != sequence:
                raise BudgetCorruption("budget journal sequence is missing or invalid")
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != match.group(2):
                raise BudgetCorruption("budget journal filename hash mismatch")
            try:
                transition = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise BudgetCorruption("budget journal contains invalid JSON") from exc
            if canonical_json_bytes(transition) != raw:
                raise BudgetCorruption("budget journal transition is not canonical JSON")
            if set(transition) != {
                "schema_version",
                "sequence",
                "previous_transition_sha256",
                "operation",
                "entry",
                "provider_remaining",
                "halted_reason",
            }:
                raise BudgetCorruption("budget journal transition has invalid keys")
            if transition["schema_version"] != 1 or transition["sequence"] != sequence:
                raise BudgetCorruption("budget journal transition identity is invalid")
            previous_hash = None if not hashes else hashes[-1]
            if transition["previous_transition_sha256"] != previous_hash:
                raise BudgetCorruption("budget journal hash link is broken")
            current = _reservation_from_dict(transition["entry"])
            previous = next(
                (entry for entry in entries if entry.logical_request_id == current.logical_request_id),
                None,
            )
            _validate_transition(transition["operation"], previous, current)
            next_remaining = transition["provider_remaining"]
            if next_remaining is not None and (
                not isinstance(next_remaining, int)
                or isinstance(next_remaining, bool)
                or next_remaining < 0
            ):
                raise BudgetCorruption("budget provider remaining is invalid")
            next_halted = transition["halted_reason"]
            if next_halted is not None and (not isinstance(next_halted, str) or not next_halted):
                raise BudgetCorruption("budget halt reason is invalid")
            if halted_reason is not None and next_halted != halted_reason:
                raise BudgetCorruption("budget journal cleared or changed a terminal halt")
            if transition["operation"] not in {"confirm", "fail"} and next_remaining != provider_remaining:
                raise BudgetCorruption("budget journal changed provider balance illegally")
            entries = _replace_entry(entries, current)
            provider_remaining = next_remaining
            halted_reason = next_halted
            hashes.append(digest)
            prefix_heads.append(
                _head_document(
                    self.max_calls,
                    self.max_credits,
                    entries,
                    digest,
                    provider_remaining,
                    halted_reason,
                )
            )

        head = self._read_head_locked()
        head_hash = head.get("journal_head_sha256")
        if head_hash is None:
            prefix_index = 0
        else:
            try:
                prefix_index = hashes.index(head_hash) + 1
            except ValueError as exc:
                raise BudgetCorruption("budget head does not name a journal prefix") from exc
        if head != prefix_heads[prefix_index]:
            raise BudgetCorruption("budget head diverges from its verified journal prefix")
        if prefix_index != len(hashes):
            atomic_replace_bytes(self.head_path, canonical_json_bytes(prefix_heads[-1]))
        return entries, hashes, provider_remaining, halted_reason

    def _totals(
        self,
        entries: list[BudgetReservation],
        hashes: list[str],
        provider_remaining: int | None,
        halted_reason: str | None,
    ) -> BudgetTotals:
        calls = sum(entry.state in _COUNTED_STATES for entry in entries)
        credits = sum(
            (entry.credit_used or 0)
            if entry.state == "confirmed_used"
            else entry.expected_credits
            for entry in entries
            if entry.state in _COUNTED_STATES
        )
        return BudgetTotals(
            calls=calls,
            credits=credits,
            provider_remaining=provider_remaining,
            entries=tuple(entries),
            transition_sha256s=tuple(hashes),
            journal_head_sha256=None if not hashes else hashes[-1],
            halted_reason=halted_reason,
        )

    def replay(self) -> BudgetTotals:
        with self._lock():
            return self._totals(*self._replay_locked())

    def _commit_locked(
        self,
        operation: str,
        entry: BudgetReservation,
        entries: list[BudgetReservation],
        hashes: list[str],
        provider_remaining: int | None,
        halted_reason: str | None,
    ) -> BudgetReservation:
        previous = next(
            (item for item in entries if item.logical_request_id == entry.logical_request_id),
            None,
        )
        _validate_transition(operation, previous, entry)
        sequence = len(hashes) + 1
        transition = {
            "schema_version": 1,
            "sequence": sequence,
            "previous_transition_sha256": None if not hashes else hashes[-1],
            "operation": operation,
            "entry": _entry_dict(entry),
            "provider_remaining": provider_remaining,
            "halted_reason": halted_reason,
        }
        transition_bytes = canonical_json_bytes(transition)
        digest = hashlib.sha256(transition_bytes).hexdigest()
        journal_path = self.journal_root / f"{sequence:06d}-{digest}.json"
        write_json_once(journal_path, transition)
        new_entries = _replace_entry(entries, entry)
        atomic_replace_bytes(
            self.head_path,
            canonical_json_bytes(
                _head_document(
                    self.max_calls,
                    self.max_credits,
                    new_entries,
                    digest,
                    provider_remaining,
                    halted_reason,
                )
            ),
        )
        return entry

    @staticmethod
    def _current(
        reservation: BudgetReservation,
        entries: list[BudgetReservation],
        required_state: str,
    ) -> BudgetReservation:
        current = next(
            (entry for entry in entries if entry.logical_request_id == reservation.logical_request_id),
            None,
        )
        if current is None or current.reservation_id != reservation.reservation_id:
            raise BudgetCorruption("reservation is not present in the journal")
        if current.state != required_state or current != reservation:
            raise BudgetError(f"reservation is not currently {required_state}")
        return current

    def reserve(
        self,
        logical_request_id: str,
        request_sha256: str,
        endpoint: str,
        expected_credits: int,
    ) -> BudgetReservation:
        if not isinstance(logical_request_id, str) or not logical_request_id:
            raise BudgetError("logical request ID must be non-empty")
        _require_hash(request_sha256, "request_sha256")
        endpoint = _normalized_endpoint(endpoint)
        if (
            not isinstance(expected_credits, int)
            or isinstance(expected_credits, bool)
            or expected_credits < 0
        ):
            raise BudgetError("expected credits must be a non-negative integer")
        with self._lock():
            entries, hashes, provider_remaining, halted_reason = self._replay_locked()
            existing = next(
                (entry for entry in entries if entry.logical_request_id == logical_request_id),
                None,
            )
            if existing is not None:
                if (
                    existing.request_sha256 != request_sha256
                    or existing.endpoint != endpoint
                    or existing.expected_credits != expected_credits
                ):
                    raise BudgetCorruption("logical request ID was reused for a different request")
                return existing
            if halted_reason is not None:
                raise BudgetError(f"budget is halted: {halted_reason}")
            totals = self._totals(entries, hashes, provider_remaining, halted_reason)
            if totals.calls + 1 > self.max_calls:
                raise BudgetError("Nansen call ceiling would be exceeded")
            if totals.credits + expected_credits > self.max_credits:
                raise BudgetError("Nansen credit ceiling would be exceeded")
            reservation = BudgetReservation(
                logical_request_id=logical_request_id,
                reservation_id=_reservation_id(logical_request_id),
                request_sha256=request_sha256,
                endpoint=endpoint,
                expected_credits=expected_credits,
                attempt_count=1,
                state="reserved",
                retry_not_before=None,
                request_artifact_sha256=None,
                response_artifact_sha256=None,
                credit_cost=None,
                credit_used=None,
                credit_remaining=None,
            )
            return self._commit_locked(
                "reserve",
                reservation,
                entries,
                hashes,
                provider_remaining,
                halted_reason,
            )

    def _artifact_path(self, reservation: BudgetReservation, kind: str) -> Path:
        return (
            self.root
            / "raw"
            / "nansen"
            / reservation.reservation_id
            / f"attempt-{reservation.attempt_count}-{kind}.json"
        )

    def bind_request_artifact(
        self, reservation: BudgetReservation, request_artifact_sha256: str
    ) -> BudgetReservation:
        _require_hash(request_artifact_sha256, "request_artifact_sha256")
        path = self._artifact_path(reservation, "request")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != request_artifact_sha256:
            raise BudgetError("request artifact is missing or does not match its SHA-256")
        try:
            document = json.loads(path.read_bytes())
        except json.JSONDecodeError as exc:
            raise BudgetError("request artifact is not JSON") from exc
        if (
            not isinstance(document, dict)
            or document.get("request_sha256") != reservation.request_sha256
            or document.get("endpoint") != reservation.endpoint
            or document.get("transmission_may_begin") is not True
            or not isinstance(document.get("method"), str)
            or "payload" not in document
            or not isinstance(document.get("caller_request_id"), str)
            or not isinstance(document.get("request_started_at"), str)
        ):
            raise BudgetError("request artifact does not match the reservation")
        with self._lock():
            entries, hashes, provider_remaining, halted_reason = self._replay_locked()
            current = self._current(reservation, entries, "reserved")
            updated = replace(current, request_artifact_sha256=request_artifact_sha256)
            return self._commit_locked(
                "bind_request_artifact",
                updated,
                entries,
                hashes,
                provider_remaining,
                halted_reason,
            )

    def _settlement_entry(
        self,
        current: BudgetReservation,
        response: NansenEvidenceResponse,
        response_artifact_sha256: str,
        state: str,
    ) -> BudgetReservation:
        return replace(
            current,
            state=state,
            retry_not_before=None,
            response_artifact_sha256=response_artifact_sha256,
            credit_cost=response.credit_cost,
            credit_used=response.credit_used,
            credit_remaining=response.credit_remaining,
        )

    def confirm(
        self,
        reservation: BudgetReservation,
        response: NansenEvidenceResponse,
        *,
        response_artifact_sha256: str,
    ) -> None:
        _require_hash(response_artifact_sha256, "response_artifact_sha256")
        with self._lock():
            entries, hashes, provider_remaining, halted_reason = self._replay_locked()
            current = self._current(reservation, entries, "reserved")
            incomplete = bool(response.credit_header_errors) or any(
                value is None
                for value in (response.credit_cost, response.credit_used, response.credit_remaining)
            )
            reason: str | None = None
            next_remaining = provider_remaining
            if incomplete:
                state = "ambiguous"
                reason = "pricing evidence is incomplete or malformed"
            elif current.endpoint == "account" and provider_remaining is None:
                if response.credit_cost != 0 or response.credit_used != 0:
                    state = "confirmed_used" if (response.credit_used or 0) > 0 else "ambiguous"
                    reason = "account baseline pricing drift"
                else:
                    state = "confirmed_zero"
                next_remaining = response.credit_remaining
            elif provider_remaining is None:
                state = "ambiguous"
                reason = "pricing baseline is not established"
            elif response.credit_remaining != provider_remaining - response.credit_used:
                state = "ambiguous"
                reason = "pricing remaining-balance mismatch"
            elif current.expected_credits > 0 and response.credit_cost != 1:
                state = "confirmed_used" if response.credit_used > 0 else "ambiguous"
                reason = "pricing cost drift"
                next_remaining = response.credit_remaining
            else:
                state = "confirmed_zero" if response.credit_used == 0 else "confirmed_used"
                next_remaining = response.credit_remaining

            updated = self._settlement_entry(
                current, response, response_artifact_sha256, state
            )
            projected_entries = _replace_entry(entries, updated)
            projected = self._totals(
                projected_entries, hashes, next_remaining, reason or halted_reason
            )
            if projected.credits > self.max_credits:
                reason = "actual credit ceiling exceeded"
            terminal = halted_reason or reason
            self._commit_locked(
                "confirm",
                updated,
                entries,
                hashes,
                next_remaining,
                terminal,
            )
            if reason is not None:
                if "credit ceiling" in reason:
                    raise BudgetError(reason)
                raise BudgetError(f"Nansen pricing validation failed: {reason}")

    def fail(
        self,
        reservation: BudgetReservation,
        failure: NansenRequestFailure,
        *,
        failure_artifact_sha256: str | None,
    ) -> None:
        if failure_artifact_sha256 is not None:
            _require_hash(failure_artifact_sha256, "failure_artifact_sha256")
        if failure.response is not None and failure_artifact_sha256 is None:
            raise BudgetError("received failure response must be archived before settlement")
        with self._lock():
            entries, hashes, provider_remaining, halted_reason = self._replay_locked()
            current = self._current(reservation, entries, "reserved")
            response = failure.response
            if not failure.transmitted or (response is not None and response.credit_used == 0):
                state = "failed_before_pricing"
            elif response is not None and response.credit_used is not None and response.credit_used > 0:
                state = "confirmed_used"
            else:
                state = "ambiguous"
            updated = replace(
                current,
                state=state,
                retry_not_before=None,
                response_artifact_sha256=failure_artifact_sha256,
                credit_cost=None if response is None else response.credit_cost,
                credit_used=None if response is None else response.credit_used,
                credit_remaining=None if response is None else response.credit_remaining,
            )
            terminal = halted_reason
            if state == "ambiguous":
                terminal = terminal or "ambiguous transmitted request"
            projected = self._totals(
                _replace_entry(entries, updated), hashes, provider_remaining, terminal
            )
            if projected.credits > self.max_credits:
                terminal = terminal or "actual credit ceiling exceeded"
            self._commit_locked(
                "fail",
                updated,
                entries,
                hashes,
                provider_remaining,
                terminal,
            )

    def mark_retryable_zero(
        self,
        reservation: BudgetReservation,
        failure: NansenRequestFailure,
        *,
        failure_artifact_sha256: str,
        retry_not_before: datetime,
    ) -> None:
        _require_hash(failure_artifact_sha256, "failure_artifact_sha256")
        response = failure.response
        if (
            not failure.transmitted
            or response is None
            or response.status_code != 429
            or response.credit_used != 0
        ):
            raise BudgetError("only an explicit zero-use 429 is retryable")
        retrieved_at = _parse_time(response.response_retrieved_at, "response retrieval time")
        deadline = retry_not_before.astimezone(timezone.utc) if retry_not_before.tzinfo else None
        if deadline is None or deadline < retrieved_at or (deadline - retrieved_at).total_seconds() > 60:
            raise BudgetError("retry deadline must be within 0 through 60 seconds after response")
        deadline_text = _utc_text(deadline)
        with self._lock():
            entries, hashes, provider_remaining, halted_reason = self._replay_locked()
            current = self._current(reservation, entries, "reserved")
            if current.attempt_count != 1:
                raise BudgetError("a second retry is forbidden")
            updated = replace(
                current,
                state="retryable_zero",
                retry_not_before=deadline_text,
                response_artifact_sha256=failure_artifact_sha256,
                credit_cost=response.credit_cost,
                credit_used=0,
                credit_remaining=response.credit_remaining,
            )
            self._commit_locked(
                "mark_retryable_zero",
                updated,
                entries,
                hashes,
                provider_remaining,
                halted_reason,
            )

    def begin_retry(
        self, reservation: BudgetReservation, *, now: datetime
    ) -> BudgetReservation:
        now_value = now.astimezone(timezone.utc) if now.tzinfo else None
        if now_value is None:
            raise BudgetError("retry clock must be timezone-aware")
        with self._lock():
            entries, hashes, provider_remaining, halted_reason = self._replay_locked()
            current = self._current(reservation, entries, "retryable_zero")
            if current.attempt_count != 1 or current.retry_not_before is None:
                raise BudgetError("a second retry is forbidden")
            if now_value < _parse_time(current.retry_not_before, "retry deadline"):
                raise BudgetError("retry deadline has not passed")
            updated = replace(
                current,
                state="reserved",
                attempt_count=2,
                retry_not_before=None,
                request_artifact_sha256=None,
                response_artifact_sha256=None,
                credit_cost=None,
                credit_used=None,
                credit_remaining=None,
            )
            return self._commit_locked(
                "begin_retry",
                updated,
                entries,
                hashes,
                provider_remaining,
                halted_reason,
            )

    def reconcile_inflight(self) -> None:
        with self._lock():
            entries, hashes, provider_remaining, halted_reason = self._replay_locked()
            for candidate in list(entries):
                request_path = self._artifact_path(candidate, "request")
                response_path = self._artifact_path(candidate, "response")
                if candidate.state in {"confirmed_zero", "confirmed_used", "retryable_zero"}:
                    if candidate.response_artifact_sha256 is not None and (
                        not response_path.is_file()
                        or hashlib.sha256(response_path.read_bytes()).hexdigest()
                        != candidate.response_artifact_sha256
                    ):
                        raise BudgetCorruption("confirmed ledger entry has missing response evidence")
                    continue
                if candidate.state != "reserved" or candidate.request_artifact_sha256 is None:
                    continue
                if (
                    not request_path.is_file()
                    or hashlib.sha256(request_path.read_bytes()).hexdigest()
                    != candidate.request_artifact_sha256
                ):
                    raise BudgetCorruption("reserved entry has missing request evidence")
                current = next(
                    item
                    for item in entries
                    if item.logical_request_id == candidate.logical_request_id
                )
                if response_path.is_file():
                    response_hash = hashlib.sha256(response_path.read_bytes()).hexdigest()
                    if current.response_artifact_sha256 is not None:
                        if current.response_artifact_sha256 != response_hash:
                            raise BudgetCorruption("recovered response evidence hash mismatch")
                        continue
                    updated = replace(current, response_artifact_sha256=response_hash)
                    self._commit_locked(
                        "bind_recovered_response",
                        updated,
                        entries,
                        hashes,
                        provider_remaining,
                        halted_reason,
                    )
                else:
                    updated = replace(current, state="ambiguous")
                    halted_reason = halted_reason or "in-flight request has no response evidence"
                    self._commit_locked(
                        "reconcile_inflight",
                        updated,
                        entries,
                        hashes,
                        provider_remaining,
                        halted_reason,
                    )
                entries, hashes, provider_remaining, halted_reason = self._replay_locked()

    def snapshot(self, stage: str, *, recorded_at: str) -> Path:
        if (
            not isinstance(stage, str)
            or not stage
            or Path(stage).name != stage
            or stage in {".", ".."}
        ):
            raise BudgetError("snapshot stage must be a single path-safe name")
        _parse_time(recorded_at, "snapshot recorded_at")
        with self._lock():
            totals = self._totals(*self._replay_locked())
            document = {
                "schema_version": 1,
                "stage": stage,
                "recorded_at": recorded_at,
                "totals": {"calls": totals.calls, "credits": totals.credits},
                "provider_remaining": totals.provider_remaining,
                "journal_head_sha256": totals.journal_head_sha256,
                "transition_sha256s": list(totals.transition_sha256s),
                "halted_reason": totals.halted_reason,
            }
            path = self.snapshot_root / f"{stage}.json"
            return write_bytes_once_or_adopt_exact(
                path,
                canonical_json_bytes(document),
                metadata={"kind": "budget_snapshot", "stage": stage},
            )
