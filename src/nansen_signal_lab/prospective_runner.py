from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .artifacts import (
    canonical_json_bytes,
    write_bytes_once_or_adopt_exact,
)
from .budget import BudgetGuard, BudgetReservation, canonical_request_sha256
from .client import NansenEvidenceResponse, NansenRequestFailure
from .evaluation import load_evaluation_manifest
from .gpt_protocol import (
    GPTArtifactWriter,
    GPTPassResult,
    archive_model_preflight,
    run_pass1,
    run_pass2,
)
from .prospective_comparators import (
    ComparatorDecision,
    evaluate_comparators,
    load_frozen_records,
    pair_distribution_veto,
)
from .prospective_execution import (
    ExecutionError,
    ObservedFill,
    build_entry_fill,
    build_exit_fill,
    dex_trade_payload,
    earliest_settlement_at,
    ohlcv_bounds,
    ohlcv_payload,
    score_decisions,
    validate_closed_ohlcv,
)
from .prospective_schema import (
    ProspectiveBundle,
    ProspectiveError,
    commit_stage,
    load_prospective_manifest,
    recover_stage_transaction,
    verify_hash_chain,
)
from .prospective_snapshot import (
    Candidate,
    blind_snapshot,
    freeze_selection,
    normalize_snapshot,
    predecision_requests,
    prior_token_identities,
    screener_payload,
    select_candidate,
)


class PilotError(RuntimeError):
    pass


_SOURCE_PATH = "../2026-08-17-paper-strategy-feasibility/manifest.json"
_DESIGN_PATH = "../../../docs/superpowers/specs/2026-08-17-gpt-prospective-pilot-design.md"
_DESIGN_V2_PATH = "../../../docs/superpowers/specs/2026-08-17-gpt-prospective-pilot-account-baseline-v2.md"
_PROTOCOL_DESIGNS = {
    "strict-v1": _DESIGN_PATH,
    "account-baseline-v2": _DESIGN_V2_PATH,
}
_CONTRACT_PATH = "../../../docs/superpowers/specs/2026-08-17-nansen-api-contract-snapshot.json"
_EVIDENCE_TIMESTAMP_FIELDS = {
    "request_started_at",
    "response_retrieved_at",
    "provider_created_at",
    "artifact_written_at",
}


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PilotError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return _utc(value, field="timestamp").isoformat().replace("+00:00", "Z")


def _parse_time(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise PilotError(f"{field} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PilotError(f"{field} must be a timestamp") from exc
    return _utc(parsed, field=field)


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _install_bytes(path: Path, content: bytes, *, kind: str) -> Path:
    try:
        return write_bytes_once_or_adopt_exact(
            path,
            content,
            metadata={"kind": kind, "received_sha256": _sha256_bytes(content)},
        )
    except (FileExistsError, RuntimeError) as exc:
        raise PilotError(str(exc)) from exc


def _install_json(path: Path, value: dict[str, Any], *, kind: str) -> Path:
    return _install_bytes(path, canonical_json_bytes(value), kind=kind)


def _install_timestamped_json(
    path: Path,
    value: dict[str, Any],
    *,
    kind: str,
    clock: Callable[[], datetime],
) -> Path:
    document = dict(value)
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise PilotError(f"existing deterministic artifact is not a regular file: {path}")
        try:
            existing = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise PilotError(f"existing deterministic artifact is unreadable: {path}") from exc
        written_at = existing.get("artifact_written_at") if isinstance(existing, dict) else None
        if not isinstance(written_at, str):
            raise PilotError(f"existing deterministic artifact lacks its write time: {path}")
        document["artifact_written_at"] = written_at
    else:
        document["artifact_written_at"] = _utc_text(_clock_value(clock))
    return _install_json(path, document, kind=kind)


def _stage_recorded_at(
    guard: BudgetGuard,
    stage: str,
    *,
    clock: Callable[[], datetime],
) -> str:
    snapshot = guard.snapshot_root / f"{stage}.json"
    if snapshot.exists():
        if snapshot.is_symlink() or not snapshot.is_file():
            raise PilotError(f"existing budget snapshot is not a regular file: {snapshot}")
        try:
            value = json.loads(snapshot.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise PilotError(f"existing budget snapshot is unreadable: {snapshot}") from exc
        recorded_at = value.get("recorded_at") if isinstance(value, dict) else None
        _parse_time(recorded_at, field=f"{stage} budget snapshot time")
        return recorded_at
    return _utc_text(_clock_value(clock))


def _clock_value(clock: Callable[[], datetime]) -> datetime:
    return _utc(clock(), field="pilot clock")


def _evidence_times(value: Any, *, field: str) -> Iterable[datetime]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _EVIDENCE_TIMESTAMP_FIELDS and child is not None:
                yield _parse_time(child, field=f"{field}.{key}")
            else:
                yield from _evidence_times(child, field=f"{field}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _evidence_times(child, field=f"{field}[{index}]")


def _assert_decision_t0(
    t0: datetime,
    current: ProspectiveBundle,
    decision_artifacts: Iterable[Path],
) -> None:
    required: list[datetime] = []
    if current.manifest["seals"]:
        latest_seal = current.root / current.manifest["seals"][-1]["path"]
        try:
            seal = json.loads(latest_seal.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise PilotError("latest stage seal is unreadable") from exc
        required.append(_parse_time(seal.get("recorded_at"), field="latest seal time"))
    for path in decision_artifacts:
        if path.suffix != ".json":
            continue
        try:
            value = json.loads(path.read_text())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            # Exact raw response bytes are paired with JSON timing metadata.
            continue
        required.extend(_evidence_times(value, field=path.as_posix()))
    if required and _utc(t0, field="decision t0") < max(required):
        raise PilotError("decision t0 precedes sealed evidence or its durable-write time")


def initialize_pilot(
    experiment_root: Path,
    *,
    created_at: datetime,
    protocol_version: str = "strict-v1",
) -> ProspectiveBundle:
    root = Path(os.path.abspath(os.fspath(experiment_root)))
    created = _utc(created_at, field="created_at")
    if root.name in {"", ".", ".."} or root.parent.name != "experiments":
        raise PilotError("experiment_root must be a direct experiments bundle")
    manifest_path = root / "manifest.json"
    if manifest_path.exists():
        return load_prospective_manifest(manifest_path)
    root.mkdir(parents=True, exist_ok=True)

    source = root / _SOURCE_PATH
    if protocol_version not in _PROTOCOL_DESIGNS:
        raise PilotError(f"unsupported prospective protocol: {protocol_version}")
    design_path = _PROTOCOL_DESIGNS[protocol_version]
    design = root / design_path
    contract = root / _CONTRACT_PATH
    for label, path in (
        ("source strategy manifest", source),
        ("prospective design", design),
        ("Nansen contract snapshot", contract),
    ):
        if not path.is_file() or path.is_symlink():
            raise PilotError(f"{label} is missing or redirected: {path}")

    timestamp = _utc_text(created)
    experiment_id = root.name
    source_sha256 = _sha256_file(source)
    contract_value = json.loads(contract.read_text())
    openapi_sha256 = contract_value.get("source_sha256")
    if not isinstance(openapi_sha256, str) or len(openapi_sha256) != 64:
        raise PilotError("pinned Nansen contract lacks the full OpenAPI SHA-256")
    preregistration_md = (
        "# Prospective GPT pilot preregistration\n\n"
        "Status: preregistered; no paid call or GPT inference has run.\n\n"
        "The identity-blinded two-pass `gpt-5.6-sol` decision is compared with all "
        "six frozen records on one common four-hour paper outcome. A tie is not a "
        "win; unavailable comparison evidence is not zero. This is a one-token, "
        "one-observation pilot and cannot establish advancement.\n\n"
        f"Protocol: `{protocol_version}`.\n\n"
        f"Design: `{design_path}`.\n"
    ).encode("utf-8")
    preregistration_text = _install_bytes(
        root / "PREREGISTRATION.md",
        preregistration_md,
        kind="preregistration_text",
    )
    preregistration = {
        "schema_version": 1,
        "experiment_id": experiment_id,
        "created_at": timestamp,
        "artifact_written_at": timestamp,
        "status": "preregistered",
        "protocol_version": protocol_version,
        "model": {
            "id": "gpt-5.6-sol",
            "passes": 2,
            "reasoning_effort": "high",
            "tools": [],
        },
        "selection": {
            "page": 1,
            "rule": "page-local highest eligible Smart-Money netflow",
            "screener_request": screener_payload(),
            "virtual_notional": "min(1000, 0.001 * screener_liquidity_usd)",
            "prior_cohort_excluded": True,
        },
        "frozen_comparators": {
            "record_count": 6,
            "source_strategy_manifest_sha256": source_sha256,
            "paired_distribution_veto": True,
        },
        "budget": {"max_nansen_calls": 10, "max_nansen_credits": 10},
        "nansen_openapi_source_sha256": openapi_sha256,
        "lifecycle": {
            "stages": [
                "preregistered",
                "snapshot_collected",
                "decision_sealed",
                "entry_observed",
                "settled",
            ],
            "terminal_failure_stage": "unscorable",
            "earliest_settlement": (
                "first UTC five-minute boundary after exit_window.to plus 60 seconds"
            ),
        },
        "execution": {
            "entry_window": "[t0+5m,t0+10m)",
            "exit_window": "[entry_start+4h,entry_start+4h+5m)",
            "paper_only": True,
            "orders_or_wallet_actions": False,
        },
        "scoring": {
            "fill_source": "common observed DEX trades",
            "unfilled_is_zero": False,
            "unavailable_is_zero": False,
            "cash_benchmark_return": 0.0,
            "strict_win": (
                "Pass 2 net return is strictly greater than every applicable "
                "scorable frozen comparator"
            ),
            "ties_are_wins": False,
        },
        "headline_rule": (
            "Pass 2 must be strictly greater than every applicable scorable frozen "
            "comparator; ties are not wins and unavailable baselines are unscorable."
        ),
        "preregistration_markdown": {
            "path": preregistration_text.name,
            "sha256": _sha256_file(preregistration_text),
        },
    }
    preregistration_path = _install_json(
        root / "preregistration.json", preregistration, kind="preregistration"
    )
    manifest = {
        "schema_version": 4,
        "experiment_id": experiment_id,
        "title": "Prospective identity-blinded GPT signal pilot",
        "created_at": timestamp,
        "hypothesis": (
            "A sealed identity-blinded GPT decision can be compared prospectively "
            "with frozen deterministic strategies using common observed paper fills."
        ),
        "stage": "preregistered",
        "source_strategy_manifest": _SOURCE_PATH,
        "source_strategy_manifest_sha256": source_sha256,
        "preregistration_path": "preregistration.json",
        "preregistration_sha256": _sha256_file(preregistration_path),
        "design_path": design_path,
        "design_sha256": _sha256_file(design),
        "nansen_contract_path": _CONTRACT_PATH,
        "nansen_contract_sha256": _sha256_file(contract),
        "max_nansen_calls": 10,
        "max_nansen_credits": 10,
        "budget_root": "budget",
        "seals": [],
        "artifacts": [],
    }
    _install_json(manifest_path, manifest, kind="prospective_manifest")
    BudgetGuard(root, max_calls=10, max_credits=10)
    return load_prospective_manifest(manifest_path)


def _nansen_paths(
    root: Path, reservation: BudgetReservation
) -> tuple[Path, Path, Path]:
    base = root / "raw/nansen" / reservation.reservation_id
    prefix = f"attempt-{reservation.attempt_count}"
    return (
        base / f"{prefix}-request.json",
        base / f"{prefix}-response.json",
        base / f"{prefix}-response-metadata.json",
    )


def _response_metadata(
    response: NansenEvidenceResponse,
    *,
    attempt: int,
    response_path: Path,
    artifact_written_at: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "attempt": attempt,
        "status_code": response.status_code,
        "request_started_at": response.request_started_at,
        "response_retrieved_at": response.response_retrieved_at,
        "artifact_written_at": artifact_written_at,
        "response_headers": dict(response.response_headers),
        "request_id": response.request_id,
        "credit_cost": response.credit_cost,
        "credit_used": response.credit_used,
        "credit_remaining": response.credit_remaining,
        "credit_header_errors": list(response.credit_header_errors),
        "body_parse_status": response.body_parse_status,
        "response_file": response_path.name,
        "response_sha256": _sha256_bytes(response.raw_body),
    }


def _archive_nansen_response(
    root: Path,
    reservation: BudgetReservation,
    response: NansenEvidenceResponse,
    *,
    clock: Callable[[], datetime],
) -> tuple[Path, Path]:
    _, response_path, metadata_path = _nansen_paths(root, reservation)
    metadata = _response_metadata(
        response,
        attempt=reservation.attempt_count,
        response_path=response_path,
        artifact_written_at=_utc_text(_clock_value(clock)),
    )
    # Install headers/timing first. If interrupted before the raw body install,
    # recovery is conservative and never retransmits. Once the body is visible,
    # its complete companion evidence is already durable.
    _install_json(metadata_path, metadata, kind="nansen_response_metadata")
    _install_bytes(response_path, response.raw_body, kind="nansen_response")
    return response_path, metadata_path


def _load_nansen_response(
    response_path: Path,
    metadata_path: Path,
) -> NansenEvidenceResponse:
    if not response_path.is_file() or not metadata_path.is_file():
        raise PilotError("archived Nansen response evidence is incomplete")
    raw = response_path.read_bytes()
    metadata = json.loads(metadata_path.read_text())
    if (
        not isinstance(metadata, dict)
        or metadata.get("response_file") != response_path.name
        or metadata.get("response_sha256") != _sha256_bytes(raw)
    ):
        raise PilotError("archived Nansen response evidence is corrupt")
    try:
        body = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        body = None
    return NansenEvidenceResponse(
        body=body,
        body_parse_status=metadata["body_parse_status"],
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
    )


def _entry_for(guard: BudgetGuard, logical_request_id: str) -> BudgetReservation | None:
    return next(
        (
            entry
            for entry in guard.replay().entries
            if entry.logical_request_id == logical_request_id
        ),
        None,
    )


def _settle_nansen_failure(
    guard: BudgetGuard,
    reservation: BudgetReservation,
    failure: NansenRequestFailure,
    *,
    failure_artifact_sha256: str | None,
) -> BudgetReservation:
    response = failure.response
    retry_after = (
        None if response is None else response.response_headers.get("Retry-After")
    )
    if (
        response is not None
        and response.status_code == 429
        and response.credit_used == 0
        and not response.credit_header_errors
        and isinstance(response.credit_remaining, int)
        and not isinstance(response.credit_remaining, bool)
        and isinstance(retry_after, str)
        and retry_after.isascii()
        and retry_after.isdigit()
        and int(retry_after) <= 60
        and reservation.attempt_count == 1
    ):
        if failure_artifact_sha256 is None:
            raise PilotError("retryable Nansen failure response was not archived")
        deadline = _parse_time(
            response.response_retrieved_at,
            field="Nansen response retrieval time",
        ) + timedelta(seconds=int(retry_after))
        guard.mark_retryable_zero(
            reservation,
            failure,
            failure_artifact_sha256=failure_artifact_sha256,
            retry_not_before=deadline,
        )
        current = _entry_for(guard, reservation.logical_request_id)
        if current is None:
            raise PilotError("retryable Nansen reservation disappeared from the ledger")
        return current
    guard.fail(
        reservation,
        failure,
        failure_artifact_sha256=failure_artifact_sha256,
    )
    raise PilotError(str(failure)) from failure


def _account_baseline_derivation(
    *,
    root: Path,
    response: NansenEvidenceResponse,
    response_metadata_path: Path,
    openapi_sha256: str,
    clock: Callable[[], datetime],
) -> Path | None:
    body = response.body
    body_remaining = (
        body.get("credits_remaining") if isinstance(body, dict) else None
    )
    eligible = (
        response.body_parse_status == "json_object"
        and isinstance(body, dict)
        and body.get("plan") in {"free", "pro"}
        and isinstance(body_remaining, int)
        and not isinstance(body_remaining, bool)
        and body_remaining >= 10
        and not response.credit_header_errors
        and response.credit_cost == 0
        and response.credit_used in {None, 0}
        and response.credit_remaining in {None, body_remaining}
    )
    if not eligible:
        return None
    if len(openapi_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in openapi_sha256
    ):
        raise PilotError("account fallback requires the matched OpenAPI SHA-256")
    assert isinstance(body, dict)
    assert isinstance(body_remaining, int)
    return _install_timestamped_json(
        root / "derived/account-baseline.json",
        {
            "schema_version": 1,
            "rule_version": "account-baseline-v2",
            "openapi_sha256": openapi_sha256,
            "response_metadata_path": response_metadata_path.relative_to(root).as_posix(),
            "response_metadata_sha256": _sha256_file(response_metadata_path),
            "body": {
                "plan": body["plan"],
                "credits_remaining": body_remaining,
            },
            "observed": {
                "credit_cost": response.credit_cost,
                "credit_used": response.credit_used,
                "credit_remaining": response.credit_remaining,
            },
            "effective": {
                "credit_cost": 0,
                "credit_used": 0,
                "credit_remaining": body_remaining,
            },
        },
        kind="account_baseline_derivation",
        clock=clock,
    )


def _confirm_nansen_success(
    *,
    root: Path,
    guard: BudgetGuard,
    reservation: BudgetReservation,
    response: NansenEvidenceResponse,
    response_metadata_path: Path,
    account_baseline_version: str | None,
    openapi_sha256: str | None,
    clock: Callable[[], datetime],
) -> tuple[Path, ...]:
    response_hash = _sha256_file(response_metadata_path)
    fallback_needed = (
        account_baseline_version == "account-baseline-v2"
        and reservation.endpoint == "account"
        and (response.credit_used is None or response.credit_remaining is None)
    )
    if not fallback_needed:
        guard.confirm(
            reservation,
            response,
            response_artifact_sha256=response_hash,
        )
        return ()

    if openapi_sha256 is None:
        raise PilotError("account fallback requires a matched OpenAPI contract")
    derivation = _account_baseline_derivation(
        root=root,
        response=response,
        response_metadata_path=response_metadata_path,
        openapi_sha256=openapi_sha256,
        clock=clock,
    )
    guard.confirm_account_baseline(
        reservation,
        response,
        response_artifact_sha256=response_hash,
        minimum_remaining=10,
    )
    if derivation is None:
        raise PilotError("account fallback did not produce its derivation artifact")
    return (derivation,)


def _nansen_call(
    *,
    root: Path,
    guard: BudgetGuard,
    nansen: Any,
    logical_request_id: str,
    method: str,
    endpoint: str,
    payload: dict[str, Any] | None,
    expected_credits: int,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    account_baseline_version: str | None = None,
    openapi_sha256: str | None = None,
) -> tuple[NansenEvidenceResponse, tuple[Path, ...]]:
    request_sha256 = canonical_request_sha256(method, endpoint, payload)
    reservation = guard.reserve(
        logical_request_id, request_sha256, endpoint, expected_credits
    )
    while True:
        request_path, response_path, metadata_path = _nansen_paths(root, reservation)
        if reservation.state in {"confirmed_zero", "confirmed_used"}:
            response = _load_nansen_response(response_path, metadata_path)
            extra: tuple[Path, ...] = ()
            if (
                account_baseline_version == "account-baseline-v2"
                and reservation.endpoint == "account"
                and (response.credit_used is None or response.credit_remaining is None)
            ):
                derivation = root / "derived/account-baseline.json"
                if not derivation.is_file() or derivation.is_symlink():
                    raise PilotError("confirmed account fallback lacks its derivation artifact")
                extra = (derivation,)
            return response, (request_path, response_path, metadata_path, *extra)
        if reservation.state == "retryable_zero":
            deadline = _parse_time(
                reservation.retry_not_before, field="Nansen retry deadline"
            )
            now = _clock_value(clock)
            if now < deadline:
                sleep((deadline - now).total_seconds())
            reservation = guard.begin_retry(reservation, now=deadline)
            continue
        if reservation.state != "reserved":
            raise PilotError(
                f"Nansen logical request {logical_request_id} is terminal: {reservation.state}"
            )

        created_request = reservation.request_artifact_sha256 is None
        if created_request:
            request_preexisted = request_path.exists()
            if request_preexisted:
                if request_path.is_symlink() or not request_path.is_file():
                    raise PilotError("existing Nansen request artifact is not a regular file")
                try:
                    request_document = json.loads(request_path.read_text())
                except (OSError, json.JSONDecodeError) as exc:
                    raise PilotError("existing Nansen request artifact is unreadable") from exc
                if (
                    not isinstance(request_document, dict)
                    or request_document.get("method") != method.upper()
                    or request_document.get("endpoint") != endpoint.strip("/")
                    or request_document.get("payload") != payload
                    or request_document.get("request_sha256") != request_sha256
                    or request_document.get("caller_request_id") != logical_request_id
                ):
                    raise PilotError("existing Nansen request artifact has different identity")
            else:
                started_at = _utc_text(_clock_value(clock))
                request_document = {
                    "method": method.upper(),
                    "endpoint": endpoint.strip("/"),
                    "payload": payload,
                    "request_sha256": request_sha256,
                    "caller_request_id": logical_request_id,
                    "request_started_at": started_at,
                    "artifact_written_at": _utc_text(_clock_value(clock)),
                    "transmission_may_begin": True,
                }
                _install_json(request_path, request_document, kind="nansen_request")
            reservation = guard.bind_request_artifact(
                reservation, _sha256_file(request_path)
            )
            if request_preexisted:
                guard.reconcile_inflight()
                raise PilotError(
                    "pre-existing transmissible Nansen request has no complete response; "
                    "automatic retransmission is forbidden"
                )
        else:
            if response_path.is_file() and metadata_path.is_file():
                response = _load_nansen_response(response_path, metadata_path)
                response_hash = _sha256_file(metadata_path)
                if reservation.response_artifact_sha256 not in {None, response_hash}:
                    raise PilotError("recovered Nansen response hash does not match its ledger")
                if response.status_code >= 400:
                    reservation = _settle_nansen_failure(
                        guard,
                        reservation,
                        NansenRequestFailure(
                            f"Nansen HTTP {response.status_code}",
                            transmitted=True,
                            response=response,
                        ),
                        failure_artifact_sha256=response_hash,
                    )
                    continue
                _confirm_nansen_success(
                    root=root,
                    guard=guard,
                    reservation=reservation,
                    response=response,
                    response_metadata_path=metadata_path,
                    account_baseline_version=account_baseline_version,
                    openapi_sha256=openapi_sha256,
                    clock=clock,
                )
                reservation = _entry_for(guard, logical_request_id) or reservation
                continue
            guard.reconcile_inflight()
            terminal = _entry_for(guard, logical_request_id)
            raise PilotError(
                "Nansen request was already transmissible without complete response evidence: "
                f"{None if terminal is None else terminal.state}"
            )

        try:
            response = nansen.request_evidence(
                method, endpoint, payload, caller_request_id=logical_request_id
            )
        except NansenRequestFailure as failure:
            failure_hash: str | None = None
            if failure.response is not None:
                archived, metadata = _archive_nansen_response(
                    root, reservation, failure.response, clock=clock
                )
                failure_hash = _sha256_file(metadata)
            reservation = _settle_nansen_failure(
                guard,
                reservation,
                failure,
                failure_artifact_sha256=failure_hash,
            )
            continue

        archived, metadata = _archive_nansen_response(
            root, reservation, response, clock=clock
        )
        extra = _confirm_nansen_success(
            root=root,
            guard=guard,
            reservation=reservation,
            response=response,
            response_metadata_path=metadata,
            account_baseline_version=account_baseline_version,
            openapi_sha256=openapi_sha256,
            clock=clock,
        )
        return response, (request_path, archived, metadata, *extra)


def _snapshot_body(response: NansenEvidenceResponse) -> dict[str, Any]:
    if not isinstance(response.body, dict):
        raise PilotError("successful Nansen snapshot response must be an object")
    value = dict(response.body)
    value["response_retrieved_at"] = response.response_retrieved_at
    value["cache_hit"] = False
    value.setdefault("warnings", [])
    return value


def _render_terminal_report(
    *,
    stage: str,
    verdict: bool | str,
    reason: str,
    totals: Any,
    score: dict[str, Any] | None = None,
) -> bytes:
    lines = [
        f"# Verdict: {str(verdict).lower()}",
        "",
        f"Stage: {stage}",
        f"Reason: {reason}",
        f"Nansen billable calls: {totals.calls}",
        f"Nansen credits: {totals.credits}",
        "",
    ]
    if score is not None:
        lines.extend(
            [
                "## Decision comparison",
                "",
                f"Pass 1: {json.dumps(score['pass1'], sort_keys=True)}",
                f"Pass 2: {json.dumps(score['pass2'], sort_keys=True)}",
                f"Cash benchmark return: {score['cash_benchmark_return']}",
                f"Gross OHLCV return: {score['gross_ohlcv_return']}",
                f"Observed/OHLCV divergence: {score['dex_ohlcv_divergence']}",
                "",
                "Comparators:",
                "",
                *(
                    f"- {item['decision_id']}: {item['status']} / {item['net_return']}"
                    for item in score["comparators"]
                ),
                "",
            ]
        )
    lines.extend(
        [
            "This is one paper-only observation. It includes no order, wallet action, "
            "venue submission, gas estimate, or executable-route claim.",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def _unsealed_evidence_paths(bundle: ProspectiveBundle) -> tuple[Path, ...]:
    sealed = {item["path"] for item in bundle.manifest["artifacts"]}
    candidates: list[Path] = []
    for directory in (
        bundle.root / "raw",
        bundle.root / "model",
        bundle.root / "derived",
        bundle.root / "normalized",
    ):
        if directory.exists():
            candidates.extend(path for path in directory.rglob("*.json") if path.is_file())
    return tuple(
        sorted(
            path
            for path in candidates
            if path.relative_to(bundle.root).as_posix() not in sealed
            and ".conflicts" not in path.relative_to(bundle.root).parts
        )
    )


def _conflict_references(bundle: ProspectiveBundle) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    for directory in (
        bundle.root / "raw",
        bundle.root / "model",
        bundle.root / "derived",
        bundle.root / "normalized",
    ):
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            relative = path.relative_to(bundle.root)
            if ".conflicts" not in relative.parts:
                continue
            if path.is_symlink() or not path.is_file():
                raise PilotError("collision quarantine contains a redirected artifact")
            references.append(
                {"path": relative.as_posix(), "sha256": _sha256_file(path)}
            )
    return sorted(references, key=lambda item: item["path"])


def _terminal_unscorable(
    bundle: ProspectiveBundle,
    guard: BudgetGuard,
    *,
    reason: str,
    artifacts: Iterable[Path],
    clock: Callable[[], datetime],
) -> ProspectiveBundle:
    failure_path = (
        bundle.root / "derived" / f"unscorable-{bundle.manifest['stage']}.json"
    )
    if failure_path.exists():
        if failure_path.is_symlink() or not failure_path.is_file():
            raise PilotError("existing terminal reason is not a regular file")
        try:
            durable_failure = json.loads(failure_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise PilotError("existing terminal reason is unreadable") from exc
        if (
            not isinstance(durable_failure, dict)
            or set(durable_failure) != {
                "schema_version",
                "reason",
                "collision_quarantine",
                "artifact_written_at",
            }
            or durable_failure.get("schema_version") != 1
            or not isinstance(durable_failure.get("reason"), str)
            or not durable_failure["reason"]
            or not isinstance(durable_failure.get("collision_quarantine"), list)
        ):
            raise PilotError("existing terminal reason has an invalid shape")
        _parse_time(
            durable_failure.get("artifact_written_at"),
            field="terminal reason write time",
        )
        reason = durable_failure["reason"]
        failure = failure_path
    else:
        failure = _install_timestamped_json(
            failure_path,
            {
                "schema_version": 1,
                "reason": reason,
                "collision_quarantine": _conflict_references(bundle),
            },
            kind="unscorable_reason",
            clock=clock,
        )
    report = _install_bytes(
        bundle.root / "REPORT.md",
        _render_terminal_report(
            stage="unscorable",
            verdict="unscorable",
            reason=reason,
            totals=guard.replay(),
        ),
        kind="terminal_report",
    )
    current_artifacts = tuple(
        dict.fromkeys((*artifacts, *_unsealed_evidence_paths(bundle), failure, report))
    )
    written_at = _stage_recorded_at(guard, "unscorable", clock=clock)
    snapshot = guard.snapshot("unscorable", recorded_at=written_at)
    return commit_stage(
        bundle, "unscorable", written_at, current_artifacts, snapshot
    )


def _model_paths(root: Path, scope: str) -> tuple[Path, ...]:
    directory = root / "model" / scope
    return tuple(sorted(path for path in directory.glob("*.json") if path.is_file()))


def _seal_decision(
    current: ProspectiveBundle,
    guard: BudgetGuard,
    *,
    openai: Any,
    selection_path: Path,
    snapshot_path: Path,
    selection: dict[str, Any],
    blinded: dict[str, Any],
    available_at: datetime,
    clock: Callable[[], datetime],
) -> ProspectiveBundle:
    source_path = (current.root / current.manifest["source_strategy_manifest"]).resolve()
    decision_artifacts: list[Path] = []
    try:
        theory_records = load_frozen_records(
            source_path, current.manifest["source_strategy_manifest_sha256"]
        )
        evaluation_bundle = load_evaluation_manifest(source_path)
        base_decisions = evaluate_comparators(
            evaluation_bundle,
            blinded["smart_money"]["final_feature"],
            blinded["smart_money"]["prior_hour_feature"],
            available_at=available_at,
        )
        decisions = pair_distribution_veto(base_decisions)
        comparator_path = _install_timestamped_json(
            current.root / "derived/comparators.json",
            {
                "schema_version": 1,
                "decisions": [asdict(item) for item in decisions],
            },
            kind="comparator_decisions",
            clock=clock,
        )
        decision_artifacts.append(comparator_path)
        writer = GPTArtifactWriter(current.root, now=lambda: _clock_value(clock))
        pass1 = run_pass1(openai, snapshot_path, writer)
        pass2 = run_pass2(openai, snapshot_path, pass1, theory_records, writer)
        decision_artifacts.extend(_model_paths(current.root, "pass-1"))
        decision_artifacts.extend(_model_paths(current.root, "pass-2"))
    except Exception as exc:
        decision_artifacts.extend(_model_paths(current.root, "pass-1"))
        decision_artifacts.extend(_model_paths(current.root, "pass-2"))
        return _terminal_unscorable(
            current,
            guard,
            reason=f"decision protocol failed: {exc}",
            artifacts=decision_artifacts,
            clock=clock,
        )

    decision_path = current.root / "derived/decision.json"
    if decision_path.exists():
        decision_document = json.loads(decision_path.read_text())
        if (
            not isinstance(decision_document, dict)
            or decision_document.get("selection_sha256") != _sha256_file(selection_path)
            or decision_document.get("snapshot_sha256") != pass1.snapshot_sha256
            or decision_document.get("pass1_action") != pass1.value["action"]
            or decision_document.get("pass2_action") != pass2.value["final_action"]
        ):
            raise PilotError("existing decision artifact does not match the sealed inputs")
        t0 = _parse_time(decision_document.get("t0"), field="existing decision t0")
    else:
        t0 = _clock_value(clock)
        entry_start = t0 + timedelta(minutes=5)
        entry_end = t0 + timedelta(minutes=10)
        exit_start = entry_start + timedelta(hours=4)
        exit_end = exit_start + timedelta(minutes=5)
        decision_document = {
            "schema_version": 1,
            "t0": _utc_text(t0),
            "entry_window": {"from": _utc_text(entry_start), "to": _utc_text(entry_end)},
            "exit_window": {"from": _utc_text(exit_start), "to": _utc_text(exit_end)},
            "earliest_settlement_at": _utc_text(earliest_settlement_at(t0, exit_end)),
            "selection_path": "derived/selection.json",
            "selection_sha256": _sha256_file(selection_path),
            "snapshot_path": "normalized/snapshot.json",
            "snapshot_sha256": pass1.snapshot_sha256,
            "virtual_notional_usd": selection["notional"]["virtual_notional_usd"],
            "pass1_action": pass1.value["action"],
            "pass2_action": pass2.value["final_action"],
        }
    _assert_decision_t0(t0, current, decision_artifacts)
    decision_path = _install_timestamped_json(
        decision_path,
        decision_document,
        kind="sealed_decision",
        clock=clock,
    )
    decision_artifacts.append(decision_path)
    decision_recorded = _stage_recorded_at(guard, "decision_sealed", clock=clock)
    if _parse_time(decision_recorded, field="decision seal time") < t0:
        raise PilotError("decision seal clock moved backward before t0")
    decision_budget = guard.snapshot("decision_sealed", recorded_at=decision_recorded)
    return commit_stage(
        current,
        "decision_sealed",
        decision_recorded,
        tuple(dict.fromkeys(decision_artifacts)),
        decision_budget,
    )


def start_pilot(
    bundle: ProspectiveBundle,
    *,
    nansen: Any,
    openai: Any,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> ProspectiveBundle:
    current = load_prospective_manifest(bundle.manifest_path)
    current = recover_stage_transaction(current)
    if current.manifest["stage"] in {"settled", "unscorable", "decision_sealed", "entry_observed"}:
        return current
    if current.manifest["stage"] not in {"preregistered", "snapshot_collected"}:
        raise PilotError(f"pilot-start cannot run from {current.manifest['stage']}")
    guard = BudgetGuard(current.root, 10, 10)
    guard.reconcile_inflight()
    if guard.replay().halted_reason is not None:
        return _terminal_unscorable(
            current,
            guard,
            reason=guard.replay().halted_reason or "budget recovery halted",
            artifacts=(),
            clock=clock,
        )

    if current.manifest["stage"] == "snapshot_collected":
        selection_path = current.root / "derived/selection.json"
        snapshot_path = current.root / "normalized/snapshot.json"
        selection = _sealed_json(current, "derived/selection.json")
        blinded = _sealed_json(current, "normalized/snapshot.json")
        available_at = _parse_time(
            blinded.get("available_at"), field="sealed snapshot available_at"
        )
        return _seal_decision(
            current,
            guard,
            openai=openai,
            selection_path=selection_path,
            snapshot_path=snapshot_path,
            selection=selection,
            blinded=blinded,
            available_at=available_at,
            clock=clock,
        )

    contract = json.loads((current.root / current.manifest["nansen_contract_path"]).read_text())
    expected_openapi = contract.get("source_sha256")
    try:
        openapi_raw = nansen.fetch_openapi()
    except Exception as exc:
        raise PilotError(f"public Nansen OpenAPI preflight failed safely: {exc}") from exc
    if not isinstance(openapi_raw, bytes):
        raise PilotError("public Nansen OpenAPI preflight must return exact bytes")
    contract_raw_path = _install_bytes(
        current.root / "raw/contracts/nansen-openapi.json",
        openapi_raw,
        kind="nansen_openapi",
    )
    contract_metadata_path = _install_timestamped_json(
        current.root / "raw/contracts/nansen-openapi-metadata.json",
        {
            "schema_version": 1,
            "source_sha256": _sha256_bytes(openapi_raw),
        },
        kind="nansen_openapi_metadata",
        clock=clock,
    )
    pre_snapshot_artifacts: list[Path] = [contract_raw_path, contract_metadata_path]
    if _sha256_bytes(openapi_raw) != expected_openapi:
        return _terminal_unscorable(
            current,
            guard,
            reason="live Nansen OpenAPI checksum differs from preregistration",
            artifacts=pre_snapshot_artifacts,
            clock=clock,
        )
    matched_openapi_sha256 = _sha256_bytes(openapi_raw)
    account_baseline_version = (
        "account-baseline-v2"
        if current.manifest["design_path"] == _DESIGN_V2_PATH
        else None
    )

    writer = GPTArtifactWriter(current.root, now=lambda: _clock_value(clock))
    try:
        archive_model_preflight(openai, writer)
    except Exception as exc:
        return _terminal_unscorable(
            current,
            guard,
            reason=f"model-access preflight failed: {exc}",
            artifacts=(*pre_snapshot_artifacts, *_model_paths(current.root, "preflight")),
            clock=clock,
        )
    pre_snapshot_artifacts.extend(_model_paths(current.root, "preflight"))

    try:
        account, paths = _nansen_call(
            root=current.root,
            guard=guard,
            nansen=nansen,
            logical_request_id="account-preflight",
            method="GET",
            endpoint="account",
            payload=None,
            expected_credits=1,
            clock=clock,
            sleep=sleep,
            account_baseline_version=account_baseline_version,
            openapi_sha256=matched_openapi_sha256,
        )
        pre_snapshot_artifacts.extend(paths)
    except Exception as exc:
        return _terminal_unscorable(
            current,
            guard,
            reason=f"Nansen account preflight failed: {exc}",
            artifacts=pre_snapshot_artifacts,
            clock=clock,
        )
    body = account.body
    account_entry = _entry_for(guard, "account-preflight")
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("plan"), str)
        or body["plan"].casefold() not in {"free", "pro"}
        or not isinstance(body.get("credits_remaining"), int)
        or isinstance(body.get("credits_remaining"), bool)
        or body["credits_remaining"] < 10
        or account_entry is None
        or account_entry.state != "confirmed_zero"
        or account_entry.credit_cost != 0
        or account_entry.credit_used != 0
        or account_entry.credit_remaining != body["credits_remaining"]
    ):
        return _terminal_unscorable(
            current,
            guard,
            reason="Nansen account plan, balance, or zero-cost evidence is invalid",
            artifacts=pre_snapshot_artifacts,
            clock=clock,
        )

    cutoff_path = current.root / "derived/snapshot-cutoff.json"
    if cutoff_path.exists():
        if cutoff_path.is_symlink() or not cutoff_path.is_file():
            raise PilotError("existing snapshot cutoff is not a regular file")
        try:
            cutoff = json.loads(cutoff_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise PilotError("existing snapshot cutoff is unreadable") from exc
        if (
            not isinstance(cutoff, dict)
            or set(cutoff)
            != {"schema_version", "available_at", "artifact_written_at"}
            or cutoff.get("schema_version") != 1
        ):
            raise PilotError("existing snapshot cutoff has an invalid shape")
        available_at = _parse_time(
            cutoff.get("available_at"), field="snapshot cutoff available_at"
        )
        cutoff_written_at = _parse_time(
            cutoff.get("artifact_written_at"), field="snapshot cutoff write time"
        )
    else:
        available_at = _clock_value(clock)
        cutoff_path = _install_timestamped_json(
            cutoff_path,
            {
                "schema_version": 1,
                "available_at": _utc_text(available_at),
            },
            kind="snapshot_cutoff",
            clock=clock,
        )
        cutoff_written_at = _parse_time(
            json.loads(cutoff_path.read_text()).get("artifact_written_at"),
            field="snapshot cutoff write time",
        )
    if cutoff_written_at < available_at:
        raise PilotError("snapshot cutoff durable-write time precedes available_at")
    pre_snapshot_artifacts.append(cutoff_path)
    try:
        screener, paths = _nansen_call(
            root=current.root,
            guard=guard,
            nansen=nansen,
            logical_request_id="screener-page-1",
            method="POST",
            endpoint="token-screener",
            payload=screener_payload(),
            expected_credits=1,
            clock=clock,
            sleep=sleep,
        )
        pre_snapshot_artifacts.extend(paths)
        candidate = select_candidate(
            screener.body,
            prior_token_identities(current.root.parent),
        )
        screener_response_path = paths[1]
        selection = freeze_selection(
            candidate,
            screener_response_sha256=_sha256_file(screener_response_path),
            screener_retrieved_at=screener.response_retrieved_at,
        )

        responses: list[NansenEvidenceResponse] = []
        for index, (method, endpoint, payload) in enumerate(
            predecision_requests(candidate, available_at), start=1
        ):
            label = (
                payload.get("label")
                if endpoint == "tgm/flows"
                else endpoint.rsplit("/", 1)[-1]
            )
            response, response_paths = _nansen_call(
                root=current.root,
                guard=guard,
                nansen=nansen,
                logical_request_id=f"predecision-{index}-{label}",
                method=method,
                endpoint=endpoint,
                payload=payload,
                expected_credits=1,
                clock=clock,
                sleep=sleep,
            )
            responses.append(response)
            pre_snapshot_artifacts.extend(response_paths)
        normalized = normalize_snapshot(
            selection,
            *(_snapshot_body(response) for response in responses),
            available_at=available_at,
        )
        blinded = blind_snapshot(normalized)
    except Exception as exc:
        return _terminal_unscorable(
            current,
            guard,
            reason=f"snapshot collection failed: {exc}",
            artifacts=pre_snapshot_artifacts,
            clock=clock,
        )

    selection_path = _install_timestamped_json(
        current.root / "derived/selection.json",
        selection,
        kind="frozen_selection",
        clock=clock,
    )
    snapshot_path = _install_timestamped_json(
        current.root / "normalized/snapshot.json",
        blinded,
        kind="blinded_snapshot",
        clock=clock,
    )
    pre_snapshot_artifacts.extend((selection_path, snapshot_path))
    pre_snapshot_artifacts.extend(_unsealed_evidence_paths(current))
    snapshot_recorded = _stage_recorded_at(guard, "snapshot_collected", clock=clock)
    snapshot_budget = guard.snapshot(
        "snapshot_collected", recorded_at=snapshot_recorded
    )
    current = commit_stage(
        current,
        "snapshot_collected",
        snapshot_recorded,
        tuple(dict.fromkeys(pre_snapshot_artifacts)),
        snapshot_budget,
    )

    return _seal_decision(
        current,
        guard,
        openai=openai,
        selection_path=selection_path,
        snapshot_path=snapshot_path,
        selection=selection,
        blinded=blinded,
        available_at=available_at,
        clock=clock,
    )


def _sealed_json(bundle: ProspectiveBundle, relative: str) -> dict[str, Any]:
    references = {item["path"]: item["sha256"] for item in bundle.manifest["artifacts"]}
    if relative not in references:
        raise PilotError(f"required artifact is not sealed: {relative}")
    path = bundle.root / relative
    if not path.is_file() or _sha256_file(path) != references[relative]:
        raise PilotError(f"sealed artifact changed: {relative}")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise PilotError(f"sealed artifact must be an object: {relative}")
    return value


def _load_comparators(bundle: ProspectiveBundle) -> tuple[ComparatorDecision, ...]:
    document = _sealed_json(bundle, "derived/comparators.json")
    rows = document.get("decisions")
    if not isinstance(rows, list):
        raise PilotError("sealed comparator decisions are invalid")
    try:
        return tuple(
            ComparatorDecision(
                decision_id=row["decision_id"],
                theory_id=row["theory_id"],
                role=row["role"],
                variant=row["variant"],
                action=row["action"],
                availability=row["availability"],
                applicable=row["applicable"],
                veto_theory_id=row["veto_theory_id"],
                veto_triggered=row["veto_triggered"],
                reasons=tuple(row["reasons"]),
            )
            for row in rows
        )
    except (KeyError, TypeError) as exc:
        raise PilotError("sealed comparator decisions are invalid") from exc


def _candidate_from_selection(selection: dict[str, Any]) -> tuple[Candidate, float]:
    try:
        identity = selection["identity"]
        row = selection["screener"]["selected_row"]
        notional = float(selection["notional"]["virtual_notional_usd"])
        candidate = Candidate(
            identity["chain"],
            identity["token_address"],
            identity["token_symbol"],
            float(selection["liquidity"]["screener_liquidity_usd"]),
            dict(row),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PilotError("sealed selection is invalid") from exc
    if notional <= 0:
        raise PilotError("sealed selection notional is invalid")
    return candidate, notional


def _collect_trade_pages(
    *,
    bundle: ProspectiveBundle,
    guard: BudgetGuard,
    nansen: Any,
    candidate: Candidate,
    side: str,
    start: datetime,
    end: datetime,
    clock: Callable[[], datetime],
) -> tuple[list[dict[str, Any]], tuple[Path, ...]]:
    pages: list[dict[str, Any]] = []
    artifacts: list[Path] = []
    for page in (1, 2):
        response, paths = _nansen_call(
            root=bundle.root,
            guard=guard,
            nansen=nansen,
            logical_request_id=f"{side.lower()}-dex-page-{page}",
            method="POST",
            endpoint="tgm/dex-trades",
            payload=dex_trade_payload(candidate, side, start, end, page),
            expected_credits=1,
            clock=clock,
            sleep=lambda _seconds: None,
        )
        if not isinstance(response.body, dict):
            raise PilotError("DEX response must be an object")
        pages.append(response.body)
        artifacts.extend(paths)
        pagination = response.body.get("pagination")
        if isinstance(pagination, dict) and pagination.get("is_last_page") is True:
            break
    return pages, tuple(artifacts)


def settle_pilot(
    bundle: ProspectiveBundle,
    *,
    nansen: Any,
    clock: Callable[[], datetime],
) -> ProspectiveBundle:
    current = recover_stage_transaction(load_prospective_manifest(bundle.manifest_path))
    if current.manifest["stage"] in {"settled", "unscorable"}:
        return current
    if current.manifest["stage"] not in {"decision_sealed", "entry_observed"}:
        raise PilotError(
            "pilot-settle requires decision_sealed or entry_observed, "
            f"got {current.manifest['stage']}"
        )
    entry_already_sealed = current.manifest["stage"] == "entry_observed"
    guard = BudgetGuard(current.root, 10, 10)
    guard.reconcile_inflight()
    decision = _sealed_json(current, "derived/decision.json")
    selection = _sealed_json(current, "derived/selection.json")
    candidate, notional = _candidate_from_selection(selection)
    decisions = _load_comparators(current)
    now = _clock_value(clock)
    deadline = _parse_time(
        decision["earliest_settlement_at"], field="earliest settlement"
    )
    if now < deadline:
        raise PilotError(f"settlement is early; earliest settlement is {_utc_text(deadline)}")
    entry_start = _parse_time(decision["entry_window"]["from"], field="entry start")
    entry_end = _parse_time(decision["entry_window"]["to"], field="entry end")
    exit_start = _parse_time(decision["exit_window"]["from"], field="exit start")
    exit_end = _parse_time(decision["exit_window"]["to"], field="exit end")
    all_actions = (
        decision["pass1_action"],
        decision["pass2_action"],
        *(item.action for item in decisions),
    )
    fill_required = any(action == "LONG" for action in all_actions)

    if entry_already_sealed:
        entry_document = _sealed_json(current, "derived/entry-observation.json")
        if (
            entry_document.get("fill_required") is not fill_required
            or entry_document.get("actions") != list(all_actions)
        ):
            raise PilotError("sealed entry observation does not match the decisions")
        entry_value = entry_document.get("entry_fill")
        if entry_value is None:
            entry_fill = None
        elif isinstance(entry_value, dict):
            try:
                entry_fill = ObservedFill(**entry_value)
            except TypeError as exc:
                raise PilotError("sealed entry fill is invalid") from exc
        else:
            raise PilotError("sealed entry fill is invalid")
    else:
        entry_artifacts: list[Path] = []
        entry_fill = None
        if fill_required:
            try:
                pages, paths = _collect_trade_pages(
                    bundle=current,
                    guard=guard,
                    nansen=nansen,
                    candidate=candidate,
                    side="BUY",
                    start=entry_start,
                    end=entry_end,
                    clock=clock,
                )
                entry_artifacts.extend(paths)
                entry_fill = build_entry_fill(
                    pages, notional, start=entry_start, end=entry_end
                )
            except Exception as exc:
                return _terminal_unscorable(
                    current,
                    guard,
                    reason=f"entry DEX evidence is invalid: {exc}",
                    artifacts=entry_artifacts,
                    clock=clock,
                )
        entry_document = {
            "schema_version": 1,
            "fill_required": fill_required,
            "actions": list(all_actions),
            "entry_fill": None if entry_fill is None else asdict(entry_fill),
        }
        entry_path = _install_timestamped_json(
            current.root / "derived/entry-observation.json",
            entry_document,
            kind="entry_observation",
            clock=clock,
        )
        entry_artifacts.append(entry_path)
        entry_artifacts.extend(_unsealed_evidence_paths(current))
        entry_recorded = _stage_recorded_at(guard, "entry_observed", clock=clock)
        entry_budget = guard.snapshot("entry_observed", recorded_at=entry_recorded)
        current = commit_stage(
            current,
            "entry_observed",
            entry_recorded,
            tuple(dict.fromkeys(entry_artifacts)),
            entry_budget,
        )

    outcome_artifacts: list[Path] = []
    exit_fill: ObservedFill | None = None
    if fill_required and entry_fill is not None:
        try:
            pages, paths = _collect_trade_pages(
                bundle=current,
                guard=guard,
                nansen=nansen,
                candidate=candidate,
                side="SELL",
                start=exit_start,
                end=exit_end,
                clock=clock,
            )
            outcome_artifacts.extend(paths)
            exit_fill = build_exit_fill(
                pages,
                entry_fill.token_amount,
                start=exit_start,
                end=exit_end,
            )
        except Exception as exc:
            return _terminal_unscorable(
                current,
                guard,
                reason=f"exit DEX evidence is invalid: {exc}",
                artifacts=outcome_artifacts,
                clock=clock,
            )

    t0 = _parse_time(decision["t0"], field="decision t0")
    required_start, required_exit = ohlcv_bounds(t0, exit_end)
    try:
        ohlcv_response, paths = _nansen_call(
            root=current.root,
            guard=guard,
            nansen=nansen,
            logical_request_id="outcome-ohlcv",
            method="POST",
            endpoint="tgm/token-ohlcv",
            payload=ohlcv_payload(candidate, required_start, required_exit),
            expected_credits=1,
            clock=clock,
            sleep=lambda _seconds: None,
        )
        outcome_artifacts.extend(paths)
        ohlcv = validate_closed_ohlcv(
            ohlcv_response.body,
            required_start=required_start,
            required_exit=required_exit,
            retrieved_at=_parse_time(
                ohlcv_response.response_retrieved_at,
                field="OHLCV response retrieval",
            ),
        )
    except Exception as exc:
        return _terminal_unscorable(
            current,
            guard,
            reason=f"OHLCV evidence is invalid: {exc}",
            artifacts=outcome_artifacts,
            clock=clock,
        )

    score = score_decisions(
        pass1_action=decision["pass1_action"],
        pass2_action=decision["pass2_action"],
        comparator_decisions=decisions,
        entry_fill=entry_fill,
        exit_fill=exit_fill,
        ohlcv=ohlcv,
        virtual_notional_usd=notional,
    )
    comparison_path = _install_timestamped_json(
        current.root / "derived/comparison.json",
        {
            "schema_version": 1,
            "selection_sha256": decision["selection_sha256"],
            "snapshot_sha256": decision["snapshot_sha256"],
            "entry_fill": None if entry_fill is None else asdict(entry_fill),
            "exit_fill": None if exit_fill is None else asdict(exit_fill),
            "ohlcv": list(ohlcv),
            "score": score,
        },
        kind="prospective_comparison",
        clock=clock,
    )
    report_path = _install_bytes(
        current.root / "REPORT.md",
        _render_terminal_report(
            stage="settled",
            verdict=score["gpt_beats_frozen_strategies"],
            reason="Prospective outcome evidence was collected and scored.",
            totals=guard.replay(),
            score=score,
        ),
        kind="terminal_report",
    )
    outcome_artifacts.extend((comparison_path, report_path))
    outcome_artifacts.extend(_unsealed_evidence_paths(current))
    written_at = _stage_recorded_at(guard, "settled", clock=clock)
    outcome_budget = guard.snapshot("settled", recorded_at=written_at)
    final = commit_stage(
        current,
        "settled",
        written_at,
        tuple(dict.fromkeys(outcome_artifacts)),
        outcome_budget,
    )
    check_pilot(final)
    return final


def replay_pilot(bundle: ProspectiveBundle) -> dict[str, Any]:
    current = recover_stage_transaction(load_prospective_manifest(bundle.manifest_path))
    verify_hash_chain(current)
    totals = BudgetGuard(current.root, 10, 10).replay()
    comparison_path = current.root / "derived/comparison.json"
    verdict = None
    if comparison_path.is_file():
        comparison = _sealed_json(current, "derived/comparison.json")
        verdict = comparison["score"]["gpt_beats_frozen_strategies"]
    return {
        "experiment_id": current.experiment_id,
        "stage": current.manifest["stage"],
        "nansen_calls": totals.calls,
        "nansen_credits": totals.credits,
        "provider_remaining": totals.provider_remaining,
        "journal_head_sha256": totals.journal_head_sha256,
        "artifact_count": len(current.manifest["artifacts"]),
        "gpt_beats_frozen_strategies": verdict,
    }


def check_pilot(bundle: ProspectiveBundle) -> tuple[Path, ...]:
    current = recover_stage_transaction(load_prospective_manifest(bundle.manifest_path))
    verify_hash_chain(current)
    totals = BudgetGuard(current.root, 10, 10).replay()
    if totals.calls > 10 or totals.credits > 10:
        raise PilotError("verified budget exceeds the preregistered ceiling")
    paths = [
        current.manifest_path,
        current.root / "preregistration.json",
        current.root / "PREREGISTRATION.md",
    ]
    paths.extend(current.root / item["path"] for item in current.manifest["seals"])
    paths.extend(current.root / item["path"] for item in current.manifest["artifacts"])
    for path in paths:
        if not path.is_file():
            raise PilotError(f"verified pilot path is missing: {path}")
    if current.manifest["stage"] in {"settled", "unscorable"}:
        report = current.root / "REPORT.md"
        if not report.is_file():
            raise PilotError("terminal pilot is missing REPORT.md")
    elif (current.root / "REPORT.md").exists():
        raise PilotError("nonterminal pilot must not contain REPORT.md")
    return tuple(dict.fromkeys(paths))
