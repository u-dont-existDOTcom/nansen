from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.nansen_signal_lab.artifacts import canonical_json_bytes
from src.nansen_signal_lab.budget import (
    BudgetError,
    BudgetReservation,
    canonical_request_sha256,
)
from src.nansen_signal_lab.client import (
    NansenClient,
    NansenEvidenceResponse,
    NansenRequestFailure,
)

from .budget import ParallelBudgetError, ParallelStrategyBudget
from .schema import (
    EXPECTED_OPENAPI_SHA256,
    ParallelStrategySchemaError,
    atomic_write_once,
)


class EvidenceError(RuntimeError):
    """Base error for the prospective program's authenticated evidence boundary."""


class EvidenceCutoff(EvidenceError):
    """Raised when the underlying timing callback forbids a transport."""


class EvidenceRequestFailed(EvidenceError):
    """Raised after an exactly accounted request failed without reusable success."""


class EvidenceFatal(EvidenceError):
    """Raised when continuation or retransmission would be unsafe."""


TransportAllowed = Callable[[], bool | None]
Clock = Callable[[], datetime]

_REQUEST_KEYS = {
    "method",
    "endpoint",
    "payload",
    "request_sha256",
    "caller_request_id",
    "request_started_at",
    "artifact_written_at",
    "transmission_may_begin",
}
_RESPONSE_KEYS = {
    "schema_version",
    "attempt",
    "status_code",
    "request_started_at",
    "response_retrieved_at",
    "artifact_written_at",
    "response_headers",
    "request_id",
    "credit_cost",
    "credit_used",
    "credit_remaining",
    "credit_header_errors",
    "body_parse_status",
    "response_file",
    "response_sha256",
}
_OPENAPI_KEYS = {
    "schema_version",
    "kind",
    "cycle_index",
    "epoch",
    "observed_at",
    "artifact_written_at",
    "response_file",
    "response_sha256",
    "expected_sha256",
    "matched",
}
_OPENAPI_CHOICE_KEYS = {
    "schema_version",
    "kind",
    "cycle_index",
    "epoch",
    "raw_file",
    "raw_sha256",
    "metadata_file",
    "metadata_sha256",
}
_CUTOFF_KEYS = {
    "schema_version",
    "kind",
    "cycle_index",
    "epoch",
    "reservation_id",
    "request_sha256",
}


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceFatal("evidence clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise EvidenceFatal(f"{field} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceFatal(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EvidenceFatal(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _parse_body(raw: bytes) -> tuple[Any | None, str]:
    if not raw:
        return None, "empty"
    try:
        body = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None, "non_json"
    return body, "json_object" if isinstance(body, dict) else "json_other"


def _cutoff(callback: TransportAllowed) -> None:
    if not callable(callback):
        raise EvidenceFatal("transport_allowed must be callable")
    if callback() is False:
        raise EvidenceCutoff("the underlying timing boundary forbids transport")


def _write_exact(path: Path, content: bytes, *, label: str) -> Path:
    try:
        return atomic_write_once(path, content)
    except ParallelStrategySchemaError as exc:
        raise EvidenceFatal(f"existing {label} differs or is unsafe") from exc


class EvidenceTransport:
    """Durable, single-attempt boundary around ``NansenClient.request_evidence``.

    The caller serializes provider transports. This class supplies the narrower
    crash invariant: a bound durable request exists before transmission, and a
    durable raw response plus metadata exists before budget settlement. A bound
    request without complete response evidence is never retransmitted.
    """

    def __init__(
        self,
        program_root: Path,
        budget: ParallelStrategyBudget,
        nansen: NansenClient,
        clock: Clock | None = None,
    ) -> None:
        self.program_root = Path(program_root)
        self.budget = budget
        self.nansen = nansen
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if self.program_root.absolute() != budget.root.absolute():
            raise EvidenceFatal("evidence and budget roots differ")
        # A verification is intentionally process-local and one-use. A crash
        # before the first auth request therefore forces another fresh fetch.
        self._pending_openapi: dict[tuple[int, str], tuple[Path, Path]] = {}

    @staticmethod
    def _logical_id(
        cycle_index: int,
        epoch: str,
        logical_request_id: str,
        endpoint: str,
    ) -> str:
        epoch_name = f"c{cycle_index:03d}-{epoch}"
        if endpoint == "account":
            return f"rr-{epoch_name}-account"
        return f"rr-{epoch_name}-{logical_request_id}"

    def _now(self) -> str:
        return _utc_text(self.clock())

    def _epoch_contract_root(self, cycle_index: int, epoch: str) -> Path:
        return self.budget.epoch_guard(cycle_index, epoch).root / "raw" / "contracts"

    def _validate_openapi_pair(self, raw_path: Path, metadata_path: Path) -> None:
        if (
            raw_path.is_symlink()
            or metadata_path.is_symlink()
            or not raw_path.is_file()
            or not metadata_path.is_file()
        ):
            raise EvidenceFatal("OpenAPI evidence is incomplete or non-regular")
        raw = raw_path.read_bytes()
        metadata_raw = metadata_path.read_bytes()
        try:
            metadata = json.loads(metadata_raw)
        except json.JSONDecodeError as exc:
            raise EvidenceFatal("OpenAPI evidence metadata is not JSON") from exc
        digest = hashlib.sha256(raw).hexdigest()
        if (
            not isinstance(metadata, dict)
            or set(metadata) != _OPENAPI_KEYS
            or canonical_json_bytes(metadata) != metadata_raw
            or metadata.get("schema_version") != 1
            or metadata.get("kind") != "parallel-strategy-fresh-openapi-v1"
            or metadata.get("response_file") != raw_path.name
            or metadata.get("response_sha256") != digest
            or metadata.get("expected_sha256") != EXPECTED_OPENAPI_SHA256
            or metadata.get("matched") is not (digest == EXPECTED_OPENAPI_SHA256)
        ):
            raise EvidenceFatal("OpenAPI evidence metadata is invalid or tampered")
        observed = _parse_time(metadata.get("observed_at"), field="OpenAPI observation")
        written = _parse_time(
            metadata.get("artifact_written_at"), field="OpenAPI durable-write time"
        )
        if observed > written:
            raise EvidenceFatal("OpenAPI evidence contains a timestamp reversal")
        if digest != EXPECTED_OPENAPI_SHA256:
            raise EvidenceFatal("fresh public OpenAPI hash differs from the frozen hash")

    def _existing_openapi_pairs(
        self, cycle_index: int, epoch: str
    ) -> tuple[tuple[Path, Path], ...]:
        root = self._epoch_contract_root(cycle_index, epoch)
        if not root.exists():
            return ()
        if root.is_symlink() or not root.is_dir():
            raise EvidenceFatal("OpenAPI evidence root is invalid")
        raw_by_stem: dict[str, Path] = {}
        meta_by_stem: dict[str, Path] = {}
        for path in root.iterdir():
            if path.is_symlink() or not path.is_file():
                raise EvidenceFatal("OpenAPI evidence contains an invalid entry")
            if path.name.startswith(".openapi-observation-") or path.name.startswith(
                ".chosen-observation.json."
            ):
                # A power loss after fsync but before the atomic hard-link (or
                # before temporary cleanup) may leave a hidden, unselected
                # complete staging file.  It never authorized authentication
                # and cannot be adopted by name, so retain it as audit debris
                # without wedging a later fresh observation.
                continue
            if path.name == "chosen-observation.json":
                continue
            if path.name.endswith(".metadata.json"):
                meta_by_stem[path.name.removesuffix(".metadata.json")] = path
            elif path.name.startswith("openapi-observation-") and path.suffix == ".json":
                raw_by_stem[path.stem] = path
            else:
                raise EvidenceFatal("OpenAPI evidence contains an unexpected entry")
        # A process/power loss may leave the last public, zero-credit
        # observation with only one member. It was never selectable and never
        # authorized an authenticated request; retain it as audit evidence and
        # allow a later complete observation to be chosen.
        complete = set(raw_by_stem) & set(meta_by_stem)
        pairs = tuple(
            (raw_by_stem[name], meta_by_stem[name]) for name in sorted(complete)
        )
        for raw_path, metadata_path in pairs:
            self._validate_openapi_pair(raw_path, metadata_path)
            metadata = json.loads(metadata_path.read_bytes())
            if (
                metadata.get("cycle_index") != cycle_index
                or metadata.get("epoch") != epoch
            ):
                raise EvidenceFatal("OpenAPI evidence belongs to another epoch")
        return pairs

    def _chosen_openapi_pair(
        self, cycle_index: int, epoch: str
    ) -> tuple[Path, Path] | None:
        root = self._epoch_contract_root(cycle_index, epoch)
        path = root / "chosen-observation.json"
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise EvidenceFatal("chosen OpenAPI observation is unsafe")
        raw = path.read_bytes()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EvidenceFatal("chosen OpenAPI observation is not JSON") from exc
        if (
            not isinstance(value, dict)
            or set(value) != _OPENAPI_CHOICE_KEYS
            or canonical_json_bytes(value) != raw
            or value.get("schema_version") != 1
            or value.get("kind") != "parallel-strategy-openapi-choice-v1"
            or value.get("cycle_index") != cycle_index
            or value.get("epoch") != epoch
            or not all(
                isinstance(value.get(name), str)
                and "/" not in value[name]
                and "\\" not in value[name]
                for name in ("raw_file", "metadata_file")
            )
        ):
            raise EvidenceFatal("chosen OpenAPI observation is invalid")
        raw_path = root / value["raw_file"]
        metadata_path = root / value["metadata_file"]
        self._validate_openapi_pair(raw_path, metadata_path)
        if (
            hashlib.sha256(raw_path.read_bytes()).hexdigest()
            != value.get("raw_sha256")
            or hashlib.sha256(metadata_path.read_bytes()).hexdigest()
            != value.get("metadata_sha256")
        ):
            raise EvidenceFatal("chosen OpenAPI observation hash differs")
        return raw_path, metadata_path

    def _choose_openapi_pair(
        self,
        cycle_index: int,
        epoch: str,
        pair: tuple[Path, Path],
    ) -> tuple[Path, Path]:
        raw_path, metadata_path = pair
        self._validate_openapi_pair(raw_path, metadata_path)
        root = self._epoch_contract_root(cycle_index, epoch)
        value = {
            "schema_version": 1,
            "kind": "parallel-strategy-openapi-choice-v1",
            "cycle_index": cycle_index,
            "epoch": epoch,
            "raw_file": raw_path.name,
            "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
            "metadata_file": metadata_path.name,
            "metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
        }
        _write_exact(
            root / "chosen-observation.json",
            canonical_json_bytes(value),
            label="chosen OpenAPI observation",
        )
        chosen = self._chosen_openapi_pair(cycle_index, epoch)
        if chosen != pair:
            raise EvidenceFatal("chosen OpenAPI observation differs")
        return pair

    def verify_openapi(
        self,
        cycle_index: int,
        epoch: str,
        transport_allowed: TransportAllowed,
    ) -> Path:
        """Fetch and durably verify a fresh public contract for an empty epoch."""

        guard = self.budget.epoch_guard(cycle_index, epoch)
        if guard.replay().entries:
            raise EvidenceFatal("fresh OpenAPI verification is only valid before epoch auth")
        chosen = self._chosen_openapi_pair(cycle_index, epoch)
        if chosen is not None:
            self._pending_openapi[(cycle_index, epoch)] = chosen
            return chosen[0]
        existing = self._existing_openapi_pairs(cycle_index, epoch)
        root = self._epoch_contract_root(cycle_index, epoch)
        observed_sequences = []
        if root.is_dir() and not root.is_symlink():
            for path in root.iterdir():
                if path.name.startswith("openapi-observation-"):
                    try:
                        observed_sequences.append(int(path.name.split("-")[2].split(".")[0]))
                    except (IndexError, ValueError):
                        raise EvidenceFatal("OpenAPI observation name is invalid")
        sequence = (max(observed_sequences) if observed_sequences else 0) + 1
        _cutoff(transport_allowed)
        try:
            raw = self.nansen.fetch_openapi()
        except EvidenceError:
            raise
        except Exception as exc:
            raise EvidenceRequestFailed("public OpenAPI fetch failed") from exc
        if not isinstance(raw, bytes):
            raise EvidenceFatal("public OpenAPI transport did not return exact bytes")

        stem = f"openapi-observation-{sequence:03d}"
        raw_path = root / f"{stem}.json"
        metadata_path = root / f"{stem}.metadata.json"
        observed_at = self._now()
        _write_exact(raw_path, raw, label="OpenAPI response")
        digest = hashlib.sha256(raw).hexdigest()
        metadata = {
            "schema_version": 1,
            "kind": "parallel-strategy-fresh-openapi-v1",
            "cycle_index": cycle_index,
            "epoch": epoch,
            "observed_at": observed_at,
            "artifact_written_at": self._now(),
            "response_file": raw_path.name,
            "response_sha256": digest,
            "expected_sha256": EXPECTED_OPENAPI_SHA256,
            "matched": digest == EXPECTED_OPENAPI_SHA256,
        }
        _write_exact(
            metadata_path,
            canonical_json_bytes(metadata),
            label="OpenAPI metadata",
        )
        self._validate_openapi_pair(raw_path, metadata_path)
        self._pending_openapi[(cycle_index, epoch)] = (raw_path, metadata_path)
        return raw_path

    def adopt_openapi(self, cycle_index: int, epoch: str) -> tuple[Path, ...]:
        """Replay-validate the single frozen contract observation for an epoch.

        This is the crash-resume path after authenticated journal entries already
        exist.  It never performs a public fetch and refuses extra, incomplete,
        mismatched, or tampered observations.
        """

        chosen = self._chosen_openapi_pair(cycle_index, epoch)
        if chosen is None:
            raise EvidenceFatal("resumed epoch has no chosen OpenAPI observation")
        return (*chosen, self._epoch_contract_root(cycle_index, epoch) / "chosen-observation.json")

    @staticmethod
    def _paths(guard: Any, reservation: BudgetReservation) -> tuple[Path, Path, Path]:
        root = guard.root / "raw" / "nansen" / reservation.reservation_id
        prefix = f"attempt-{reservation.attempt_count}"
        return (
            root / f"{prefix}-request.json",
            root / f"{prefix}-response.json",
            root / f"{prefix}-response-metadata.json",
        )

    def _request_document(
        self,
        reservation: BudgetReservation,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        started_at = self._now()
        return {
            "method": method,
            "endpoint": endpoint,
            "payload": payload,
            "request_sha256": reservation.request_sha256,
            "caller_request_id": reservation.reservation_id,
            "request_started_at": started_at,
            "artifact_written_at": self._now(),
            "transmission_may_begin": True,
        }

    def _cutoff_marker(
        self,
        path: Path,
        *,
        cycle_index: int,
        epoch: str,
        reservation: BudgetReservation,
        create: bool,
    ) -> bool:
        expected = {
            "schema_version": 1,
            "kind": "parallel-strategy-pretransport-cutoff-v1",
            "cycle_index": cycle_index,
            "epoch": epoch,
            "reservation_id": reservation.reservation_id,
            "request_sha256": reservation.request_sha256,
        }
        if create:
            _write_exact(
                path,
                canonical_json_bytes(expected),
                label="pretransport cutoff marker",
            )
        if not path.exists():
            return False
        if path.is_symlink() or not path.is_file():
            raise EvidenceFatal("pretransport cutoff marker is unsafe")
        raw = path.read_bytes()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EvidenceFatal("pretransport cutoff marker is not JSON") from exc
        if (
            not isinstance(value, dict)
            or set(value) != _CUTOFF_KEYS
            or value != expected
            or canonical_json_bytes(value) != raw
        ):
            raise EvidenceFatal("pretransport cutoff marker differs")
        return True

    def _validate_request(
        self,
        path: Path,
        reservation: BudgetReservation,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None,
    ) -> str:
        if path.is_symlink() or not path.is_file():
            raise EvidenceFatal("bound request evidence is missing or non-regular")
        raw = path.read_bytes()
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EvidenceFatal("request evidence is not JSON") from exc
        if (
            not isinstance(document, dict)
            or set(document) != _REQUEST_KEYS
            or canonical_json_bytes(document) != raw
            or document.get("method") != method
            or document.get("endpoint") != endpoint
            or document.get("payload") != payload
            or document.get("request_sha256") != reservation.request_sha256
            or document.get("caller_request_id") != reservation.reservation_id
            or document.get("transmission_may_begin") is not True
        ):
            raise EvidenceFatal("request evidence differs from the reservation")
        started = _parse_time(document.get("request_started_at"), field="request start")
        written = _parse_time(
            document.get("artifact_written_at"), field="request durable-write time"
        )
        if started > written:
            raise EvidenceFatal("request evidence contains a timestamp reversal")
        return hashlib.sha256(raw).hexdigest()

    def _archive_response(
        self,
        reservation: BudgetReservation,
        response: NansenEvidenceResponse,
        response_path: Path,
        metadata_path: Path,
    ) -> str:
        _write_exact(
            response_path,
            response.raw_body,
            label="response body",
        )
        metadata = {
            "schema_version": 1,
            "attempt": reservation.attempt_count,
            "status_code": response.status_code,
            "request_started_at": response.request_started_at,
            "response_retrieved_at": response.response_retrieved_at,
            "artifact_written_at": self._now(),
            "response_headers": dict(response.response_headers),
            "request_id": response.request_id,
            "credit_cost": response.credit_cost,
            "credit_used": response.credit_used,
            "credit_remaining": response.credit_remaining,
            "credit_header_errors": list(response.credit_header_errors),
            "body_parse_status": response.body_parse_status,
            "response_file": response_path.name,
            "response_sha256": hashlib.sha256(response.raw_body).hexdigest(),
        }
        metadata_raw = canonical_json_bytes(metadata)
        _write_exact(
            metadata_path,
            metadata_raw,
            label="response metadata",
        )
        return hashlib.sha256(metadata_raw).hexdigest()

    def _load_response(
        self,
        reservation: BudgetReservation,
        response_path: Path,
        metadata_path: Path,
        expected_metadata_sha256: str | None,
    ) -> tuple[NansenEvidenceResponse, str]:
        if (
            response_path.is_symlink()
            or metadata_path.is_symlink()
            or not response_path.is_file()
            or not metadata_path.is_file()
        ):
            raise EvidenceFatal("response evidence is incomplete or non-regular")
        raw = response_path.read_bytes()
        metadata_raw = metadata_path.read_bytes()
        metadata_sha256 = hashlib.sha256(metadata_raw).hexdigest()
        if expected_metadata_sha256 is not None and metadata_sha256 != expected_metadata_sha256:
            raise EvidenceFatal("response evidence hash differs from the budget journal")
        try:
            metadata = json.loads(metadata_raw)
        except json.JSONDecodeError as exc:
            raise EvidenceFatal("response evidence metadata is not JSON") from exc
        body, parse_status = _parse_body(raw)
        if (
            not isinstance(metadata, dict)
            or set(metadata) != _RESPONSE_KEYS
            or canonical_json_bytes(metadata) != metadata_raw
            or metadata.get("schema_version") != 1
            or metadata.get("attempt") != reservation.attempt_count
            or metadata.get("response_file") != response_path.name
            or metadata.get("response_sha256") != hashlib.sha256(raw).hexdigest()
            or metadata.get("body_parse_status") != parse_status
            or isinstance(metadata.get("status_code"), bool)
            or not isinstance(metadata.get("status_code"), int)
            or not isinstance(metadata.get("response_headers"), dict)
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in metadata.get("response_headers", {}).items()
            )
            or (
                metadata.get("request_id") is not None
                and not isinstance(metadata.get("request_id"), str)
            )
            or not isinstance(metadata.get("credit_header_errors"), list)
            or any(
                not isinstance(value, str)
                for value in metadata.get("credit_header_errors", [])
            )
        ):
            raise EvidenceFatal("response evidence metadata is invalid or tampered")
        for name in ("credit_cost", "credit_used", "credit_remaining"):
            value = metadata.get(name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise EvidenceFatal("response pricing metadata is invalid")
        started = _parse_time(metadata.get("request_started_at"), field="response request start")
        retrieved = _parse_time(
            metadata.get("response_retrieved_at"), field="response retrieval"
        )
        written = _parse_time(
            metadata.get("artifact_written_at"), field="response durable-write time"
        )
        if started > retrieved or retrieved > written:
            raise EvidenceFatal("response evidence contains a timestamp reversal")
        return (
            NansenEvidenceResponse(
                body=body,
                body_parse_status=parse_status,
                raw_body=raw,
                status_code=metadata["status_code"],
                request_started_at=metadata["request_started_at"],
                response_retrieved_at=metadata["response_retrieved_at"],
                response_headers=dict(metadata["response_headers"]),
                request_id=metadata["request_id"],
                credit_cost=metadata["credit_cost"],
                credit_used=metadata["credit_used"],
                credit_remaining=metadata["credit_remaining"],
                credit_header_errors=tuple(metadata["credit_header_errors"]),
            ),
            metadata_sha256,
        )

    def _settle(
        self,
        cycle_index: int,
        epoch: str,
        reservation: BudgetReservation,
        response: NansenEvidenceResponse,
        metadata_sha256: str,
    ) -> None:
        if 200 <= response.status_code < 300 and response.body_parse_status == "json_object":
            if reservation.endpoint == "account":
                self.budget.confirm_account(
                    cycle_index,
                    epoch,
                    reservation,
                    response,
                    response_artifact_sha256=metadata_sha256,
                )
            else:
                self.budget.confirm_paid(
                    cycle_index,
                    epoch,
                    reservation,
                    response,
                    response_artifact_sha256=metadata_sha256,
                )
            return
        failure = NansenRequestFailure(
            f"Nansen HTTP/body failure {response.status_code}",
            transmitted=True,
            response=response,
        )
        self.budget.fail(
            cycle_index,
            epoch,
            reservation,
            failure,
            failure_artifact_sha256=metadata_sha256,
        )
        raise EvidenceRequestFailed("authenticated request returned a failed response")

    def _mark_ambiguous(
        self,
        cycle_index: int,
        epoch: str,
        reservation: BudgetReservation,
        message: str,
    ) -> None:
        try:
            self.budget.fail(
                cycle_index,
                epoch,
                reservation,
                NansenRequestFailure(message, transmitted=True),
                failure_artifact_sha256=None,
            )
        except ParallelBudgetError as exc:
            raise EvidenceFatal(message) from exc
        raise EvidenceFatal(message)

    def call(
        self,
        cycle_index: int,
        epoch: str,
        logical_request_id: str,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None,
        expected_credits: int,
        transport_allowed: TransportAllowed,
    ) -> tuple[NansenEvidenceResponse, tuple[Path, ...]]:
        """Execute or exactly adopt one authenticated request, without retry."""

        if not isinstance(logical_request_id, str) or not logical_request_id:
            raise EvidenceFatal("logical request ID must be non-empty")
        if not isinstance(method, str) or not method:
            raise EvidenceFatal("method must be non-empty")
        method = method.upper()
        endpoint = endpoint.strip("/") if isinstance(endpoint, str) else ""
        if not endpoint:
            raise EvidenceFatal("endpoint must be a normalized relative endpoint")
        if payload is not None and not isinstance(payload, dict):
            raise EvidenceFatal("payload must be a JSON object or null")
        required_credits = 0 if endpoint == "account" else 1
        if (
            not isinstance(expected_credits, int)
            or isinstance(expected_credits, bool)
            or expected_credits != required_credits
        ):
            raise EvidenceFatal("expected credits must be exactly zero for account or one for paid")
        if endpoint == "account" and logical_request_id != "account":
            raise EvidenceFatal("the account request must use logical request ID 'account'")
        try:
            request_sha256 = canonical_request_sha256(method, endpoint, payload)
        except BudgetError as exc:
            raise EvidenceFatal("request is not canonical") from exc

        guard = self.budget.epoch_guard(cycle_index, epoch)
        qualified_id = self._logical_id(
            cycle_index, epoch, logical_request_id, endpoint
        )
        existing = next(
            (
                entry
                for entry in guard.replay().entries
                if entry.logical_request_id == qualified_id
            ),
            None,
        )
        openapi_paths: tuple[Path, ...] = ()
        if existing is None and endpoint == "account":
            pair = self._pending_openapi.pop((cycle_index, epoch), None)
            if pair is None:
                raw_path = self.verify_openapi(cycle_index, epoch, transport_allowed)
                pair = self._pending_openapi.pop((cycle_index, epoch))
                assert pair[0] == raw_path
            pair = self._choose_openapi_pair(cycle_index, epoch, pair)
            openapi_paths = (
                *pair,
                self._epoch_contract_root(cycle_index, epoch)
                / "chosen-observation.json",
            )

        if existing is None:
            try:
                reservation = (
                    self.budget.reserve_account(cycle_index, epoch, request_sha256)
                    if endpoint == "account"
                    else self.budget.reserve_paid(
                        cycle_index,
                        epoch,
                        logical_request_id,
                        request_sha256,
                        endpoint,
                    )
                )
            except ParallelBudgetError as exc:
                raise EvidenceFatal(str(exc)) from exc
        else:
            reservation = existing
            if (
                reservation.request_sha256 != request_sha256
                or reservation.endpoint != endpoint
                or reservation.expected_credits != expected_credits
                or reservation.attempt_count != 1
            ):
                raise EvidenceFatal("existing reservation differs from the requested call")

        request_path, response_path, metadata_path = self._paths(guard, reservation)
        cutoff_path = request_path.with_name(
            f"attempt-{reservation.attempt_count}-pretransport-cutoff.json"
        )
        artifact_paths = openapi_paths + (request_path, response_path, metadata_path)
        request_bound_now = False

        if reservation.request_artifact_sha256 is not None:
            try:
                request_artifact_sha256 = self._validate_request(
                    request_path, reservation, method, endpoint, payload
                )
            except EvidenceFatal:
                if reservation.state == "reserved":
                    self._mark_ambiguous(
                        cycle_index, epoch, reservation, "bound request evidence is tampered"
                    )
                raise
            if request_artifact_sha256 != reservation.request_artifact_sha256:
                if reservation.state == "reserved":
                    self._mark_ambiguous(
                        cycle_index, epoch, reservation, "bound request hash differs"
                    )
                raise EvidenceFatal("bound request hash differs from the budget journal")

        elif request_path.exists():
            # A process may die after the durable write and before the journal
            # bind. Since this implementation never transports before binding,
            # the exact request is safe to adopt and transmit once.
            if response_path.exists() or metadata_path.exists():
                raise EvidenceFatal(
                    "response evidence exists for a request that was never bound"
                )
            request_artifact_sha256 = self._validate_request(
                request_path, reservation, method, endpoint, payload
            )
            try:
                reservation = guard.bind_request_artifact(
                    reservation, request_artifact_sha256
                )
            except BudgetError as exc:
                raise EvidenceFatal("could not bind recovered request evidence") from exc
            request_bound_now = True

        response_complete = response_path.is_file() and metadata_path.is_file()
        response_partial = response_path.exists() != metadata_path.exists()
        if response_partial:
            if reservation.state == "reserved" and reservation.request_artifact_sha256:
                self._mark_ambiguous(
                    cycle_index,
                    epoch,
                    reservation,
                    "transmitted request has incomplete response evidence",
                )
            raise EvidenceFatal("response evidence is incomplete")

        if response_complete:
            response, metadata_sha256 = self._load_response(
                reservation,
                response_path,
                metadata_path,
                reservation.response_artifact_sha256,
            )
            if reservation.state == "reserved":
                try:
                    self._settle(
                        cycle_index, epoch, reservation, response, metadata_sha256
                    )
                except ParallelBudgetError as exc:
                    raise EvidenceFatal(str(exc)) from exc
            elif reservation.state in {"ambiguous", "failed_before_pricing"}:
                raise EvidenceRequestFailed(
                    f"archived request is terminal in state {reservation.state}"
                )
            elif not (
                200 <= response.status_code < 300
                and response.body_parse_status == "json_object"
            ):
                raise EvidenceRequestFailed("archived response is not a reusable success")
            return response, artifact_paths

        if reservation.state == "ambiguous":
            raise EvidenceFatal("authenticated request is globally ambiguous")
        if reservation.state == "failed_before_pricing":
            if self._cutoff_marker(
                cutoff_path,
                cycle_index=cycle_index,
                epoch=epoch,
                reservation=reservation,
                create=False,
            ):
                raise EvidenceCutoff(
                    "authenticated transport cutoff was durably recorded"
                )
            raise EvidenceRequestFailed("authenticated request already failed before pricing")
        if reservation.state != "reserved":
            raise EvidenceFatal("terminal request is missing its response evidence")
        if (
            reservation.request_artifact_sha256 is not None
            and self._cutoff_marker(
                cutoff_path,
                cycle_index=cycle_index,
                epoch=epoch,
                reservation=reservation,
                create=False,
            )
        ):
            try:
                self.budget.fail(
                    cycle_index,
                    epoch,
                    reservation,
                    NansenRequestFailure(
                        "recovered durable pretransport cutoff",
                        transmitted=False,
                    ),
                    failure_artifact_sha256=None,
                )
            except ParallelBudgetError as exc:
                raise EvidenceFatal(str(exc)) from exc
            raise EvidenceCutoff(
                "authenticated transport cutoff was recovered before transmission"
            )
        if reservation.request_artifact_sha256 is not None and not request_bound_now:
            self._mark_ambiguous(
                cycle_index, epoch, reservation, "transmissible request has no response evidence"
            )

        if not request_bound_now:
            request_document = self._request_document(
                reservation, method, endpoint, payload
            )
            request_raw = canonical_json_bytes(request_document)
            _write_exact(
                request_path,
                request_raw,
                label="request artifact",
            )
            request_artifact_sha256 = self._validate_request(
                request_path, reservation, method, endpoint, payload
            )
            try:
                reservation = guard.bind_request_artifact(
                    reservation, request_artifact_sha256
                )
            except BudgetError as exc:
                raise EvidenceFatal("could not bind durable request evidence") from exc

        try:
            _cutoff(transport_allowed)
        except Exception as exc:
            self._cutoff_marker(
                cutoff_path,
                cycle_index=cycle_index,
                epoch=epoch,
                reservation=reservation,
                create=True,
            )
            failure = NansenRequestFailure(
                "authenticated transport forbidden before transmission",
                transmitted=False,
            )
            try:
                self.budget.fail(
                    cycle_index,
                    epoch,
                    reservation,
                    failure,
                    failure_artifact_sha256=None,
                )
            except ParallelBudgetError as budget_exc:
                raise EvidenceFatal(str(budget_exc)) from budget_exc
            if isinstance(exc, EvidenceCutoff):
                raise
            raise EvidenceCutoff("authenticated transport cutoff failed") from exc

        try:
            response = self.nansen.request_evidence(
                method,
                endpoint,
                payload,
                caller_request_id=reservation.reservation_id,
            )
        except NansenRequestFailure as failure:
            failure_sha256: str | None = None
            if failure.response is not None:
                failure_sha256 = self._archive_response(
                    reservation, failure.response, response_path, metadata_path
                )
            try:
                self.budget.fail(
                    cycle_index,
                    epoch,
                    reservation,
                    failure,
                    failure_artifact_sha256=failure_sha256,
                )
            except ParallelBudgetError as exc:
                raise EvidenceFatal(str(exc)) from exc
            raise EvidenceRequestFailed(str(failure)) from failure
        except Exception as exc:
            self._mark_ambiguous(
                cycle_index,
                epoch,
                reservation,
                f"authenticated transport raised {type(exc).__name__} without response evidence",
            )

        metadata_sha256 = self._archive_response(
            reservation, response, response_path, metadata_path
        )
        try:
            self._settle(cycle_index, epoch, reservation, response, metadata_sha256)
        except ParallelBudgetError as exc:
            raise EvidenceFatal(str(exc)) from exc
        return response, artifact_paths
