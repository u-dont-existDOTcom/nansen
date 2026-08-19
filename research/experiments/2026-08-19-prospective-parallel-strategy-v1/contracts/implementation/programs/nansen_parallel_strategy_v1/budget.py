from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from src.nansen_signal_lab.artifacts import canonical_json_bytes
from src.nansen_signal_lab.budget import (
    BudgetCorruption,
    BudgetError,
    BudgetGuard,
    BudgetReservation,
)
from src.nansen_signal_lab.client import NansenEvidenceResponse, NansenRequestFailure

from .design import (
    MAX_PROGRAM_ATTEMPTS,
    MAX_PROGRAM_CREDITS,
    PREDECISION_MAX_ATTEMPTS,
    PREDECISION_MAX_CREDITS,
    SETTLEMENT_MAX_ATTEMPTS,
    SETTLEMENT_MAX_CREDITS,
)
from .schema import ParallelStrategySchemaError, atomic_write_once


# The first observed provider balance must preserve every separately frozen
# future authority.  Keep the arithmetic executable so changing one component
# cannot silently leave a stale magic threshold behind.
LATER_PRIMARY_HOLDOUT_CREDITS = 13_786
LATER_TEMPORAL_REPLICATION_CREDITS = 16_592
PERMANENT_SAFETY_MARGIN_CREDITS = 327
MINIMUM_FIRST_BASELINE = (
    MAX_PROGRAM_CREDITS
    + LATER_PRIMARY_HOLDOUT_CREDITS
    + LATER_TEMPORAL_REPLICATION_CREDITS
    + PERMANENT_SAFETY_MARGIN_CREDITS
)
RECONCILIATION_KIND = "parallel-strategy-operational-ledger-reconciliation-v1"
REQUIRED_OPERATIONAL_PROGRAMS = (
    "2026-08-18-historical-theory-discovery-a-v1",
    "2026-08-18-historical-theory-discovery-a2-v1",
    "2026-08-18-prospective-multi-cycle-cohort-v1",
)
TERMINAL_STAGES = frozenset({"completed", "unscorable"})
EPOCHS = ("predecision", "settlement")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_EPOCH_DIRECTORY = re.compile(r"^c(\d{3})-(predecision|settlement)$")
_JOURNAL_FILE = re.compile(r"^(\d{6})-([0-9a-f]{64})\.json$")
_MAX_RECONSTRUCTED_BALANCES = 16_384


class OperationalReconciliationError(ValueError):
    """Raised when operational-only predecessor accounting is not exact."""


class ParallelBudgetError(BudgetError):
    """Raised when the parallel-strategy budget cannot safely continue."""


class ParallelBudgetCorruption(BudgetCorruption):
    """Raised when persisted parallel-strategy accounting is inconsistent."""


@dataclass(frozen=True)
class EpochBudgetTotals:
    epoch_id: str
    attempts: int
    credits: int
    provider_remaining: int | None
    halted_reason: str | None


@dataclass(frozen=True)
class ProgramBudgetTotals:
    attempts: int
    credits: int
    remaining_attempt_authority: int
    remaining_credit_authority: int
    frozen_first_baseline: int | None
    expected_next_baseline: int | None
    reconstructed_first_baselines: tuple[int, ...]
    reconciliation_sha256: str
    halted_reason: str | None
    epochs: tuple[EpochBudgetTotals, ...]


def _strict_nonnegative_integer(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise OperationalReconciliationError(
            f"{field} must be a non-negative integer"
        )
    return value


def _strict_sorted_integer_set(value: Any, *, field: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise OperationalReconciliationError(f"{field} must be a non-empty list")
    numbers = tuple(
        _strict_nonnegative_integer(item, field=f"{field} entry") for item in value
    )
    if numbers != tuple(sorted(set(numbers))):
        raise OperationalReconciliationError(
            f"{field} must be sorted, unique, and non-empty"
        )
    return numbers


def reconstruct_operational_balances(document: Mapping[str, Any]) -> tuple[int, ...]:
    """Reconstruct possible provider balances from operational ledgers only.

    The schema deliberately contains no token, feature, decision, outcome, score,
    ranking, or return field. Each source contributes an exact confirmed spend
    plus a finite set of conservative unresolved-reservation totals.
    """

    if not isinstance(document, Mapping) or set(document) != {
        "schema_version",
        "kind",
        "opening_balance_candidates",
        "operational_ledgers",
    }:
        raise OperationalReconciliationError(
            "operational reconciliation has invalid top-level keys"
        )
    if document.get("schema_version") != 1 or document.get("kind") != RECONCILIATION_KIND:
        raise OperationalReconciliationError(
            "operational reconciliation identity differs"
        )
    balances = set(
        _strict_sorted_integer_set(
            document.get("opening_balance_candidates"),
            field="opening_balance_candidates",
        )
    )
    ledgers = document.get("operational_ledgers")
    if not isinstance(ledgers, list) or len(ledgers) != len(
        REQUIRED_OPERATIONAL_PROGRAMS
    ):
        raise OperationalReconciliationError(
            "operational reconciliation must contain exact A, A2, and v1 ledgers"
        )
    for index, (ledger, expected_program) in enumerate(
        zip(ledgers, REQUIRED_OPERATIONAL_PROGRAMS, strict=True)
    ):
        if not isinstance(ledger, Mapping) or set(ledger) != {
            "program_id",
            "terminal_stage",
            "operational_ledger_sha256",
            "confirmed_spend_credits",
            "reserved_spend_candidates",
        }:
            raise OperationalReconciliationError(
                f"operational ledger {index} has invalid keys"
            )
        if ledger.get("program_id") != expected_program:
            raise OperationalReconciliationError(
                "operational ledgers are missing or out of frozen order"
            )
        if ledger.get("terminal_stage") not in TERMINAL_STAGES:
            raise OperationalReconciliationError(
                f"operational ledger {expected_program} is not terminal"
            )
        digest = ledger.get("operational_ledger_sha256")
        if not isinstance(digest, str) or _HASH.fullmatch(digest) is None:
            raise OperationalReconciliationError(
                f"operational ledger {expected_program} hash is invalid"
            )
        confirmed = _strict_nonnegative_integer(
            ledger.get("confirmed_spend_credits"),
            field=f"{expected_program} confirmed_spend_credits",
        )
        reserved = _strict_sorted_integer_set(
            ledger.get("reserved_spend_candidates"),
            field=f"{expected_program} reserved_spend_candidates",
        )
        next_balances = {
            balance - confirmed - unresolved
            for balance in balances
            for unresolved in reserved
        }
        if any(balance < 0 for balance in next_balances):
            raise OperationalReconciliationError(
                "operational reconciliation produces a negative balance"
            )
        if not next_balances or len(next_balances) > _MAX_RECONSTRUCTED_BALANCES:
            raise OperationalReconciliationError(
                "operational reconciliation balance set is empty or excessive"
            )
        balances = next_balances
    return tuple(sorted(balances))


def assert_budget_ceiling(
    attempts: Any,
    credits: Any,
    *,
    maximum_attempts: int,
    maximum_credits: int,
) -> None:
    """Validate an exact attempt/credit ceiling without granting surplus authority."""

    if (
        not isinstance(maximum_attempts, int)
        or isinstance(maximum_attempts, bool)
        or maximum_attempts <= 0
        or not isinstance(maximum_credits, int)
        or isinstance(maximum_credits, bool)
        or maximum_credits <= 0
    ):
        raise ParallelBudgetError("budget ceilings must be positive integers")
    if (
        not isinstance(attempts, int)
        or isinstance(attempts, bool)
        or attempts < 0
        or not isinstance(credits, int)
        or isinstance(credits, bool)
        or credits < 0
    ):
        raise ParallelBudgetError("budget totals must be non-negative integers")
    if attempts > maximum_attempts:
        raise ParallelBudgetError("authenticated-attempt ceiling would be exceeded")
    if credits > maximum_credits:
        raise ParallelBudgetError("billable-credit ceiling would be exceeded")


def _epoch_limits(epoch: str) -> tuple[int, int]:
    if epoch == "predecision":
        return PREDECISION_MAX_ATTEMPTS, PREDECISION_MAX_CREDITS
    if epoch == "settlement":
        return SETTLEMENT_MAX_ATTEMPTS, SETTLEMENT_MAX_CREDITS
    raise ParallelBudgetError("epoch must be predecision or settlement")


def _epoch_id(cycle_index: Any, epoch: str) -> str:
    if (
        not isinstance(cycle_index, int)
        or isinstance(cycle_index, bool)
        or not 1 <= cycle_index <= 85
    ):
        raise ParallelBudgetError("cycle index must be in the frozen range 1..85")
    _epoch_limits(epoch)
    return f"c{cycle_index:03d}-{epoch}"


class ParallelStrategyBudget:
    """Program/epoch guard composed from isolated ``BudgetGuard`` journals.

    Each epoch gets its own provider-balance baseline so account responses that
    rely on the audited zero-cost body fallback remain usable. This coordinator
    adds the attempt accounting that ``BudgetGuard.calls`` intentionally omits
    for confirmed-zero requests and enforces cross-epoch balance continuity.
    """

    def __init__(
        self,
        program_root: Path,
        operational_reconciliation: Mapping[str, Any],
    ) -> None:
        self.root = Path(program_root)
        try:
            reconciliation_bytes = canonical_json_bytes(operational_reconciliation)
        except (TypeError, ValueError) as exc:
            raise OperationalReconciliationError(
                "operational reconciliation is not canonical-JSON compatible"
            ) from exc
        self.reconstructed_first_baselines = reconstruct_operational_balances(
            operational_reconciliation
        )
        self.reconciliation_sha256 = hashlib.sha256(reconciliation_bytes).hexdigest()
        self.budget_root = self.root / "budget" / "parallel-strategy-v1"
        self.epochs_root = self.budget_root / "epochs"
        self.lock_path = self.budget_root / "coordinator.lock"
        self.reconciliation_path = self.budget_root / "operational-reconciliation.json"
        self.global_halt_path = self.budget_root / "global-halt.json"
        self.epochs_root.mkdir(parents=True, exist_ok=True)
        self.lock_path.touch(exist_ok=True)
        try:
            atomic_write_once(self.reconciliation_path, reconciliation_bytes)
        except ParallelStrategySchemaError as exc:
            raise ParallelBudgetCorruption(
                "operational reconciliation artifact differs"
            ) from exc

    @contextmanager
    def _lock(self) -> Iterator[None]:
        descriptor = os.open(self.lock_path, os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _guard(self, cycle_index: int, epoch: str) -> BudgetGuard:
        epoch_name = _epoch_id(cycle_index, epoch)
        max_attempts, max_credits = _epoch_limits(epoch)
        root = self.epochs_root / epoch_name
        self._recover_uncommitted_partial_journal(root)
        return BudgetGuard(
            root,
            max_calls=max_attempts,
            max_credits=max_credits,
        )

    @staticmethod
    def _recover_uncommitted_partial_journal(root: Path) -> None:
        """Quarantine only a provably uncommitted, torn final transition.

        Frozen ``BudgetGuard`` installs a journal filename before advancing its
        atomic head.  A power loss during that immutable write can leave a
        malformed final file.  If the head does not reference that filename
        and it is exactly the last sequence, preserving it under
        ``recovered-incomplete`` and replaying the verified prefix is safe.
        Complete hash-valid transitions are left for BudgetGuard's native head
        adoption path; any non-tail or head-referenced corruption remains fatal.
        """

        budget_root = root / "budget"
        journal_root = budget_root / "journal"
        head_path = budget_root / "head.json"
        if not journal_root.is_dir() or not head_path.is_file():
            return
        descriptor = os.open(budget_root, os.O_RDONLY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            paths = sorted(journal_root.glob("*.json"))
            if not paths:
                return
            last = paths[-1]
            match = _JOURNAL_FILE.fullmatch(last.name)
            if match is None or int(match.group(1)) != len(paths):
                return
            raw = last.read_bytes()
            named_digest = match.group(2)
            valid = hashlib.sha256(raw).hexdigest() == named_digest
            if valid:
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    valid = False
                else:
                    valid = isinstance(value, dict) and canonical_json_bytes(value) == raw
            if valid:
                return
            try:
                head_raw = head_path.read_bytes()
                head = json.loads(head_raw)
            except (OSError, json.JSONDecodeError) as exc:
                raise ParallelBudgetCorruption(
                    "budget head is unreadable during tail recovery"
                ) from exc
            if (
                not isinstance(head, dict)
                or canonical_json_bytes(head) != head_raw
                or head.get("journal_head_sha256") == named_digest
            ):
                raise ParallelBudgetCorruption(
                    "torn journal may already be referenced by the budget head"
                )
            recovered_root = budget_root / "recovered-incomplete"
            recovered_root.mkdir(parents=True, exist_ok=True)
            recovered = recovered_root / (
                f"{last.name}.{hashlib.sha256(raw).hexdigest()}.partial"
            )
            if recovered.exists():
                if recovered.is_symlink() or not recovered.is_file() or recovered.read_bytes() != raw:
                    raise ParallelBudgetCorruption(
                        "recovered torn-journal artifact differs"
                    )
                last.unlink()
            else:
                os.replace(last, recovered)
            directory = os.open(journal_root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def epoch_guard(self, cycle_index: int, epoch: str) -> BudgetGuard:
        """Return the underlying guard for artifact paths and offline replay."""

        return self._guard(cycle_index, epoch)

    def _existing_epoch_guards(self) -> tuple[tuple[str, BudgetGuard], ...]:
        result: list[tuple[str, BudgetGuard]] = []
        for path in self.epochs_root.iterdir():
            match = _EPOCH_DIRECTORY.fullmatch(path.name)
            if match is None or path.is_symlink() or not path.is_dir():
                raise ParallelBudgetCorruption("epoch budget directory is invalid")
            cycle_index = int(match.group(1))
            epoch = match.group(2)
            if not 1 <= cycle_index <= 85:
                raise ParallelBudgetCorruption("epoch budget cycle is outside 1..85")
            result.append((path.name, self._guard(cycle_index, epoch)))
        order = {"predecision": 0, "settlement": 1}
        result.sort(
            key=lambda item: (
                int(_EPOCH_DIRECTORY.fullmatch(item[0]).group(1)),  # type: ignore[union-attr]
                order[_EPOCH_DIRECTORY.fullmatch(item[0]).group(2)],  # type: ignore[union-attr]
            )
        )
        return tuple(result)

    def _read_global_halt(self) -> str | None:
        if not self.global_halt_path.exists():
            return None
        try:
            raw = self.global_halt_path.read_bytes()
            value = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise ParallelBudgetCorruption("global budget halt is unreadable") from exc
        if (
            not isinstance(value, dict)
            or set(value) != {
                "schema_version",
                "kind",
                "reason",
                "reconciliation_sha256",
            }
            or value.get("schema_version") != 1
            or value.get("kind") != "parallel-strategy-budget-global-halt"
            or not isinstance(value.get("reason"), str)
            or not value["reason"]
            or value.get("reconciliation_sha256") != self.reconciliation_sha256
            or canonical_json_bytes(value) != raw
        ):
            raise ParallelBudgetCorruption("global budget halt is invalid")
        return value["reason"]

    def _halt_locked(self, reason: str) -> None:
        if self.global_halt_path.exists():
            self._read_global_halt()
            return
        document = {
                "schema_version": 1,
                "kind": "parallel-strategy-budget-global-halt",
                "reason": reason,
                "reconciliation_sha256": self.reconciliation_sha256,
            }
        try:
            atomic_write_once(
                self.global_halt_path, canonical_json_bytes(document)
            )
        except ParallelStrategySchemaError as exc:
            raise ParallelBudgetCorruption("global budget halt differs") from exc

    def _summary_locked(self) -> ProgramBudgetTotals:
        attempts = 0
        credits = 0
        frozen_first: int | None = None
        spent_before_epoch = 0
        derived_halt: str | None = self._read_global_halt()
        epochs: list[EpochBudgetTotals] = []
        for epoch_name, guard in self._existing_epoch_guards():
            totals = guard.replay()
            epoch_attempts = sum(entry.attempt_count for entry in totals.entries)
            # BudgetGuard deliberately prices an ambiguous reservation at its
            # expected amount. Account reservations expect zero, so a malformed
            # charged account needs this stricter program-level conservative
            # accounting to retain its observed use even though the program is
            # immediately halted.
            epoch_credits = sum(
                (
                    entry.credit_used or 0
                    if entry.state == "confirmed_used"
                    else max(entry.expected_credits, entry.credit_used or 0)
                )
                for entry in totals.entries
                if entry.state
                in {"reserved", "retryable_zero", "confirmed_used", "ambiguous"}
            )
            assert_budget_ceiling(
                epoch_attempts,
                epoch_credits,
                maximum_attempts=guard.max_calls,
                maximum_credits=guard.max_credits,
            )
            account_id = f"ps-{epoch_name}-account"
            accounts = [entry for entry in totals.entries if entry.endpoint == "account"]
            paid = [entry for entry in totals.entries if entry.endpoint != "account"]
            if len(accounts) > 1 or any(
                account.logical_request_id != account_id
                or account.expected_credits != 0
                or account.attempt_count != 1
                for account in accounts
            ):
                raise ParallelBudgetCorruption("epoch account reservation is invalid")
            if any(
                entry.expected_credits != 1 or entry.attempt_count != 1
                for entry in paid
            ):
                raise ParallelBudgetCorruption(
                    "paid reservation is not an exact single-credit first attempt"
                )
            if paid and (
                not accounts or accounts[0].state != "confirmed_zero"
            ):
                raise ParallelBudgetCorruption(
                    "paid reservation exists without a confirmed epoch account"
                )
            if any(entry.state == "retryable_zero" for entry in totals.entries):
                derived_halt = derived_halt or "automatic retry state is forbidden"
            if any(entry.state == "ambiguous" for entry in totals.entries):
                derived_halt = derived_halt or "ambiguous budget entry"
            if totals.halted_reason is not None:
                derived_halt = derived_halt or totals.halted_reason
            if accounts and accounts[0].state == "confirmed_zero":
                observed = accounts[0].credit_remaining
                if not isinstance(observed, int) or isinstance(observed, bool):
                    raise ParallelBudgetCorruption(
                        "confirmed epoch account omits its frozen balance"
                    )
                if frozen_first is None:
                    if (
                        observed < MINIMUM_FIRST_BASELINE
                        or observed not in self.reconstructed_first_baselines
                    ):
                        raise ParallelBudgetCorruption(
                            "frozen first baseline is outside operational reconstruction"
                        )
                    frozen_first = observed
                elif observed != frozen_first - spent_before_epoch:
                    raise ParallelBudgetCorruption(
                        "later epoch account breaks exact program balance continuity"
                    )
            attempts += epoch_attempts
            credits += epoch_credits
            spent_before_epoch += epoch_credits
            epochs.append(
                EpochBudgetTotals(
                    epoch_id=epoch_name,
                    attempts=epoch_attempts,
                    credits=epoch_credits,
                    provider_remaining=totals.provider_remaining,
                    halted_reason=totals.halted_reason,
                )
            )
        assert_budget_ceiling(
            attempts,
            credits,
            maximum_attempts=MAX_PROGRAM_ATTEMPTS,
            maximum_credits=MAX_PROGRAM_CREDITS,
        )
        return ProgramBudgetTotals(
            attempts=attempts,
            credits=credits,
            remaining_attempt_authority=MAX_PROGRAM_ATTEMPTS - attempts,
            remaining_credit_authority=MAX_PROGRAM_CREDITS - credits,
            frozen_first_baseline=frozen_first,
            expected_next_baseline=(
                None if frozen_first is None else frozen_first - credits
            ),
            reconstructed_first_baselines=self.reconstructed_first_baselines,
            reconciliation_sha256=self.reconciliation_sha256,
            halted_reason=derived_halt,
            epochs=tuple(epochs),
        )

    def summary(self) -> ProgramBudgetTotals:
        with self._lock():
            return self._summary_locked()

    def _require_active_locked(self) -> ProgramBudgetTotals:
        summary = self._summary_locked()
        if summary.halted_reason is not None:
            self._halt_locked(summary.halted_reason)
            raise ParallelBudgetError(
                f"parallel-strategy budget is globally halted: {summary.halted_reason}"
            )
        return summary

    def reserve_account(
        self, cycle_index: int, epoch: str, request_sha256: str
    ) -> BudgetReservation:
        epoch_name = _epoch_id(cycle_index, epoch)
        with self._lock():
            summary = self._require_active_locked()
            guard = self._guard(cycle_index, epoch)
            logical_id = f"ps-{epoch_name}-account"
            totals = guard.replay()
            existing = next(
                (
                    entry
                    for entry in totals.entries
                    if entry.logical_request_id == logical_id
                ),
                None,
            )
            if existing is not None:
                return guard.reserve(logical_id, request_sha256, "account", 0)
            if totals.entries:
                raise ParallelBudgetError("account must be the first epoch reservation")
            if any(
                entry.state in {"reserved", "retryable_zero"}
                for _, other in self._existing_epoch_guards()
                for entry in other.replay().entries
            ):
                raise ParallelBudgetError(
                    "another program reservation is unresolved before account baseline"
                )
            assert_budget_ceiling(
                summary.attempts + 1,
                summary.credits,
                maximum_attempts=MAX_PROGRAM_ATTEMPTS,
                maximum_credits=MAX_PROGRAM_CREDITS,
            )
            return guard.reserve(logical_id, request_sha256, "account", 0)

    def reserve_paid(
        self,
        cycle_index: int,
        epoch: str,
        logical_request_id: str,
        request_sha256: str,
        endpoint: str,
    ) -> BudgetReservation:
        if not isinstance(logical_request_id, str) or not logical_request_id:
            raise ParallelBudgetError("logical request ID must be non-empty")
        if endpoint.strip("/") == "account":
            raise ParallelBudgetError("paid reservation cannot target account")
        epoch_name = _epoch_id(cycle_index, epoch)
        qualified_id = f"ps-{epoch_name}-{logical_request_id}"
        with self._lock():
            summary = self._require_active_locked()
            guard = self._guard(cycle_index, epoch)
            totals = guard.replay()
            existing = next(
                (
                    entry
                    for entry in totals.entries
                    if entry.logical_request_id == qualified_id
                ),
                None,
            )
            if existing is not None:
                return guard.reserve(qualified_id, request_sha256, endpoint, 1)
            account_id = f"ps-{epoch_name}-account"
            account = next(
                (
                    entry
                    for entry in totals.entries
                    if entry.logical_request_id == account_id
                ),
                None,
            )
            if account is None or account.state != "confirmed_zero":
                raise ParallelBudgetError(
                    "paid reservation requires the confirmed epoch account baseline"
                )
            epoch_attempts = sum(entry.attempt_count for entry in totals.entries)
            assert_budget_ceiling(
                epoch_attempts + 1,
                totals.credits + 1,
                maximum_attempts=guard.max_calls,
                maximum_credits=guard.max_credits,
            )
            assert_budget_ceiling(
                summary.attempts + 1,
                summary.credits + 1,
                maximum_attempts=MAX_PROGRAM_ATTEMPTS,
                maximum_credits=MAX_PROGRAM_CREDITS,
            )
            return guard.reserve(qualified_id, request_sha256, endpoint, 1)

    def confirm_account(
        self,
        cycle_index: int,
        epoch: str,
        reservation: BudgetReservation,
        response: NansenEvidenceResponse,
        *,
        response_artifact_sha256: str,
    ) -> None:
        with self._lock():
            summary = self._summary_locked()
            guard = self._guard(cycle_index, epoch)
            body_remaining = (
                response.body.get("credits_remaining")
                if isinstance(response.body, dict)
                else None
            )
            valid_observed = (
                isinstance(body_remaining, int)
                and not isinstance(body_remaining, bool)
            )
            if summary.frozen_first_baseline is None:
                admitted = bool(
                    valid_observed
                    and body_remaining >= MINIMUM_FIRST_BASELINE
                    and body_remaining in self.reconstructed_first_baselines
                )
                expected_text = "the reconstructed first-balance set"
            else:
                admitted = bool(
                    valid_observed
                    and body_remaining == summary.expected_next_baseline
                )
                expected_text = f"exact later balance {summary.expected_next_baseline}"
            forced_minimum = (
                MINIMUM_FIRST_BASELINE
                if admitted or not valid_observed
                else max(MINIMUM_FIRST_BASELINE, body_remaining + 1)
            )
            try:
                guard.confirm_account_baseline(
                    reservation,
                    response,
                    response_artifact_sha256=response_artifact_sha256,
                    minimum_remaining=forced_minimum,
                )
            except BudgetError as exc:
                reason = (
                    f"account baseline did not match {expected_text}"
                    if not admitted
                    else f"account baseline pricing is invalid: {exc}"
                )
                self._halt_locked(reason)
                raise ParallelBudgetError(reason) from exc
            if not admitted:
                reason = f"account baseline did not match {expected_text}"
                self._halt_locked(reason)
                raise ParallelBudgetError(reason)
            checked = self._summary_locked()
            if checked.halted_reason is not None:
                self._halt_locked(checked.halted_reason)
                raise ParallelBudgetError(checked.halted_reason)

    def confirm_paid(
        self,
        cycle_index: int,
        epoch: str,
        reservation: BudgetReservation,
        response: NansenEvidenceResponse,
        *,
        response_artifact_sha256: str,
    ) -> None:
        malformed = (
            not 200 <= response.status_code < 300
            or response.body_parse_status != "json_object"
            or not isinstance(response.body, dict)
        )
        with self._lock():
            guard = self._guard(cycle_index, epoch)
            try:
                guard.confirm(
                    reservation,
                    response,
                    response_artifact_sha256=response_artifact_sha256,
                )
            except BudgetError as exc:
                reason = f"paid pricing evidence is malformed or discontinuous: {exc}"
                self._halt_locked(reason)
                raise ParallelBudgetError(reason) from exc
            if malformed:
                reason = "charged response is non-successful or malformed"
                self._halt_locked(reason)
                raise ParallelBudgetError(reason)

    def fail(
        self,
        cycle_index: int,
        epoch: str,
        reservation: BudgetReservation,
        failure: NansenRequestFailure,
        *,
        failure_artifact_sha256: str | None,
    ) -> None:
        with self._lock():
            guard = self._guard(cycle_index, epoch)
            before = guard.replay().provider_remaining
            try:
                guard.fail(
                    reservation,
                    failure,
                    failure_artifact_sha256=failure_artifact_sha256,
                )
            except BudgetError as exc:
                reason = f"failed request pricing evidence is malformed: {exc}"
                self._halt_locked(reason)
                raise ParallelBudgetError(reason) from exc
            response = failure.response
            fatal_reason: str | None = None
            if failure.transmitted and response is None:
                fatal_reason = "transmitted request has ambiguous pricing"
            elif failure.transmitted and response is not None:
                incomplete = bool(response.credit_header_errors) or any(
                    value is None
                    for value in (
                        response.credit_cost,
                        response.credit_used,
                        response.credit_remaining,
                    )
                )
                if incomplete:
                    fatal_reason = "failed request pricing is incomplete or malformed"
                elif response.credit_used > 0:
                    fatal_reason = "failed request was charged"
                elif before is None or response.credit_remaining != before:
                    fatal_reason = "failed request breaks provider balance continuity"
                elif (
                    response.body_parse_status not in {"json_object", "empty"}
                    or (
                        response.body_parse_status == "json_object"
                        and not isinstance(response.body, dict)
                    )
                ):
                    fatal_reason = "failed response body is malformed"
            if fatal_reason is not None:
                self._halt_locked(fatal_reason)
                raise ParallelBudgetError(fatal_reason)
