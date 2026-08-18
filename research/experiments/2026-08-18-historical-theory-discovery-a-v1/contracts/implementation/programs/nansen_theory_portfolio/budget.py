from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from src.nansen_signal_lab.artifacts import (
    canonical_json_bytes,
    write_bytes_once_or_adopt_exact,
)
from src.nansen_signal_lab.budget import (
    BudgetError,
    BudgetGuard,
    BudgetReservation,
    BudgetTotals,
    _replace_entry,
)
from src.nansen_signal_lab.client import NansenEvidenceResponse

from .design import (
    DEX_ENDPOINT,
    FLOW_ENDPOINT,
    FULL_OPENAPI_SHA256,
    SCREENER_ENDPOINT,
    WBS_ENDPOINT,
)


MISSING_QUOTE_ENDPOINTS = {
    SCREENER_ENDPOINT,
    FLOW_ENDPOINT,
    WBS_ENDPOINT,
    DEX_ENDPOINT,
}


class HistoricalPricingGuard(BudgetGuard):
    """Budget journal with one narrow, raw-preserving beta-cost derivation.

    The provider has previously omitted the quoted-cost header from a historical
    beta response while still returning exact used/remaining evidence. The base
    guard correctly rejects that in general. This subclass accepts only the
    preregistered five-credit proof and installs a separate derivation before the
    normal journal transition. Raw response metadata remains unchanged.
    """

    def _totals(
        self,
        entries: list[BudgetReservation],
        hashes: list[str],
        provider_remaining: int | None,
        halted_reason: str | None,
    ) -> BudgetTotals:
        """Count every Program-A reservation as an authenticated attempt.

        The shared guard intentionally excludes confirmed zero-cost calls and
        failures known to precede pricing from its generic call metric. Program
        A has a stricter, preregistered attempt ceiling: its account preflight
        and every logical request that reached reservation consume one slot.
        Counting even an unbound crash reservation is conservative and prevents
        an interrupted or zero-cost call from reopening capacity.
        """

        base = super()._totals(entries, hashes, provider_remaining, halted_reason)
        return replace(base, calls=len(entries))

    def _pricing_derivation_path(self, reservation: BudgetReservation) -> Path:
        return (
            self.root
            / "derived"
            / "pricing"
            / f"{reservation.reservation_id}-attempt-{reservation.attempt_count}.json"
        )

    def confirm(
        self,
        reservation: BudgetReservation,
        response: NansenEvidenceResponse,
        *,
        response_artifact_sha256: str,
    ) -> None:
        fallback_shape = (
            reservation.endpoint in MISSING_QUOTE_ENDPOINTS
            and reservation.expected_credits == 5
            and response.credit_cost is None
            and response.credit_used == 5
            and isinstance(response.credit_remaining, int)
            and not isinstance(response.credit_remaining, bool)
            and not response.credit_header_errors
        )
        if not fallback_shape:
            super().confirm(
                reservation,
                response,
                response_artifact_sha256=response_artifact_sha256,
            )
            return

        with self._lock():
            entries, hashes, provider_remaining, halted_reason = self._replay_locked()
            current = self._current(reservation, entries, "reserved")
            self._verify_response_artifact(
                current, response_artifact_sha256, response
            )
            reason: str | None = None
            if provider_remaining is None:
                reason = "pricing baseline is not established"
            elif response.credit_remaining != provider_remaining - 5:
                reason = "pricing remaining-balance mismatch"

            if reason is not None:
                updated = replace(
                    current,
                    state="ambiguous",
                    response_artifact_sha256=response_artifact_sha256,
                    credit_cost=None,
                    credit_used=5,
                    credit_remaining=response.credit_remaining,
                )
                self._commit_locked(
                    "confirm",
                    updated,
                    entries,
                    hashes,
                    provider_remaining,
                    halted_reason or reason,
                )
                raise BudgetError(f"Nansen pricing validation failed: {reason}")

            assert provider_remaining is not None
            derivation = {
                "schema_version": 1,
                "policy": "historical-beta-missing-quote-v1",
                "openapi_sha256": FULL_OPENAPI_SHA256,
                "endpoint": current.endpoint,
                "logical_request_id": current.logical_request_id,
                "reservation_id": current.reservation_id,
                "attempt": current.attempt_count,
                "response_metadata_sha256": response_artifact_sha256,
                "raw": {
                    "credit_cost": None,
                    "credit_used": 5,
                    "credit_remaining": response.credit_remaining,
                },
                "effective": {
                    "credit_cost": 5,
                    "credit_used": 5,
                    "credit_remaining": response.credit_remaining,
                },
                "proof": {
                    "pinned_contract_cost": 5,
                    "reserved_cost": current.expected_credits,
                    "previous_remaining": provider_remaining,
                    "remaining_delta": provider_remaining - response.credit_remaining,
                },
            }
            path = self._pricing_derivation_path(current)
            write_bytes_once_or_adopt_exact(
                path,
                canonical_json_bytes(derivation),
                metadata={
                    "kind": "historical_beta_pricing_derivation",
                    "logical_request_id": current.logical_request_id,
                },
            )
            updated = replace(
                current,
                state="confirmed_used",
                retry_not_before=None,
                response_artifact_sha256=response_artifact_sha256,
                credit_cost=5,
                credit_used=5,
                credit_remaining=response.credit_remaining,
            )
            projected = self._totals(
                _replace_entry(entries, updated),
                hashes,
                response.credit_remaining,
                halted_reason,
            )
            if projected.credits > self.max_credits:
                reason = "actual credit ceiling exceeded"
            self._commit_locked(
                "confirm",
                updated,
                entries,
                hashes,
                response.credit_remaining,
                halted_reason or reason,
            )
            if reason is not None:
                raise BudgetError(reason)


def pricing_derivation_paths(root: Path) -> tuple[Path, ...]:
    directory = root / "derived" / "pricing"
    return tuple(sorted(directory.glob("*.json"))) if directory.is_dir() else ()
