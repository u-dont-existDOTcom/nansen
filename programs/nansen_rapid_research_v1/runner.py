from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Protocol, Sequence

from src.nansen_signal_lab.cohort_execution import dex_payload, execution_windows, ohlcv_payload
from src.nansen_signal_lab.cohort_execution import CohortExecutionError
from src.nansen_signal_lab.cohort_features import CohortFeatureError
from src.nansen_signal_lab.budget import BudgetGuard, canonical_request_sha256

from .activation import ParallelStrategyActivationError, operational_reconciliation
from .aggregate import freeze_discovery_family, phase_analysis, validation_result
from .budget import ParallelBudgetError, ParallelStrategyBudget
from .contract import load_parallel_strategy_contract
from .design import (
    B2DesignError,
    DECISION_DEADLINE,
    PREDECISION_TRANSPORT_CUTOFF,
    PREDECISION_MAX_ATTEMPTS,
    PREDECISION_MAX_CREDITS,
    PARENT_NONCASH_CANDIDATE_IDS,
    PROGRAM_ID,
    SCHEDULE,
    SETTLEMENT_MAX_ATTEMPTS,
    SETTLEMENT_MAX_CREDITS,
    SETTLEMENT_TRANSPORT_CUTOFF,
    TOKENS_PER_CYCLE,
    ScheduledCycle,
    breadth_payload,
    build_counterfactual_outcome,
    canonical_sha256,
    candidate_decision,
    flow_intelligence_payload,
    screener_payload,
    select_cycle,
    smart_money_payload,
    validate_flow_intelligence,
    validate_screener,
    validate_smart_money_evidence,
    validate_wbs_evidence,
)
from .runtime import require_stopped_v1_activation
from .schema import (
    ParallelStrategyProgram,
    ParallelStrategySchemaError,
    atomic_replace_json,
    atomic_write_once,
    canonical_json_bytes,
)
from .timing import (
    decision_t0,
    predecision_transport_allowed,
    settlement_hard_stop,
    settlement_state,
    start_state,
)
from .evidence import EvidenceCutoff, EvidenceError


class ParallelStrategyRunnerError(RuntimeError):
    """Raised when a live or replay action cannot preserve the frozen protocol."""


@dataclass(frozen=True)
class EvidenceResult:
    body: dict[str, Any]
    retrieved_at: datetime
    artifacts: tuple[Path, ...]


class EvidenceTransport(Protocol):
    def verify_openapi(
        self,
        cycle_index: int,
        epoch: str,
        transport_allowed: Callable[[], bool],
    ) -> Path: ...

    def call(
        self,
        *,
        cycle_index: int,
        epoch: str,
        logical_request_id: str,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None,
        expected_credits: int,
        transport_allowed: Callable[[], bool],
    ) -> tuple[Any, tuple[Path, ...]]: ...

    def adopt_openapi(self, cycle_index: int, epoch: str) -> tuple[Path, ...]: ...


_TERMINAL = frozenset({"outcome_sealed", "unscorable"})
_STAGES = (
    "planned",
    "panel_sealed",
    "decisions_sealed",
    "outcome_sealed",
    "unscorable",
)


def _utc(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ParallelStrategyRunnerError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _utc_text(value: datetime) -> str:
    return _utc(value, field="timestamp").isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ParallelStrategyRunnerError(f"{field} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ParallelStrategyRunnerError(f"{field} is not a timestamp") from exc
    return _utc(parsed, field=field)


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ParallelStrategyRunnerError(f"artifact is absent or unsafe: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _confined_path(root: Path, value: Any, *, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ParallelStrategyRunnerError(
            f"{label} is not a normalized relative path"
        )
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ParallelStrategyRunnerError(f"{label} escapes the program root")
    cursor = root
    if cursor.is_symlink():
        raise ParallelStrategyRunnerError(f"{label} root is unsafe")
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ParallelStrategyRunnerError(f"{label} traverses a symlink")
    try:
        cursor.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise ParallelStrategyRunnerError(
            f"{label} escapes the program root"
        ) from exc
    return cursor


def _read(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ParallelStrategyRunnerError(f"{label} is absent or unsafe")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ParallelStrategyRunnerError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise ParallelStrategyRunnerError(f"{label} is not an object")
    return value


def _write_once(path: Path, value: Any) -> Path:
    content = canonical_json_bytes(value)
    try:
        return atomic_write_once(path, content)
    except ParallelStrategySchemaError as exc:
        raise ParallelStrategyRunnerError(
            f"existing immutable artifact differs: {path}"
        ) from exc


def _artifact(root: Path, path: Path) -> dict[str, str]:
    try:
        relative = path.absolute().relative_to(root.absolute()).as_posix()
    except ValueError as exc:
        raise ParallelStrategyRunnerError("sealed artifact escapes the cycle") from exc
    confined = _confined_path(root, relative, label="sealed artifact")
    return {"path": relative, "sha256": _sha256(confined)}


def _cycle_root(program: ParallelStrategyProgram, cycle: ScheduledCycle) -> Path:
    return program.root / "cycles" / f"cycle-{cycle.index:03d}"


def _initial_state(cycle: ScheduledCycle) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "cycle_index": cycle.index,
        "phase": cycle.phase,
        "scheduled_at": _utc_text(cycle.scheduled_at),
        "stage": "planned",
        "terminal_reason": None,
        "seals": [],
    }


def _load_state(program: ParallelStrategyProgram, cycle: ScheduledCycle) -> tuple[Path, dict[str, Any]]:
    root = _cycle_root(program, cycle)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "state.json"
    if not path.exists():
        atomic_replace_json(path, _initial_state(cycle))
    state = _read(path, label="cycle state")
    if (
        set(state)
        != {
            "schema_version",
            "program_id",
            "cycle_index",
            "phase",
            "scheduled_at",
            "stage",
            "terminal_reason",
            "seals",
        }
        or state.get("schema_version") != 1
        or state.get("program_id") != PROGRAM_ID
        or state.get("cycle_index") != cycle.index
        or state.get("phase") != cycle.phase
        or state.get("scheduled_at") != _utc_text(cycle.scheduled_at)
        or state.get("stage") not in _STAGES
        or not isinstance(state.get("seals"), list)
        or (
            state.get("stage") == "unscorable"
            and (
                not isinstance(state.get("terminal_reason"), str)
                or not state["terminal_reason"]
            )
        )
        or (
            state.get("stage") != "unscorable"
            and state.get("terminal_reason") is not None
        )
    ):
        raise ParallelStrategyRunnerError("cycle state identity differs")
    next_stages = {
        "planned": ("panel_sealed", "unscorable"),
        "panel_sealed": ("decisions_sealed", "unscorable"),
        "decisions_sealed": ("outcome_sealed", "unscorable"),
        "outcome_sealed": (),
        "unscorable": (),
    }[state["stage"]]
    orphaned = [
        stage
        for stage in next_stages
        if (root / "seals" / f"{stage}.json").exists()
        and not any(item.get("stage") == stage for item in state["seals"])
    ]
    if len(orphaned) > 1:
        raise ParallelStrategyRunnerError("multiple orphan cycle seals exist")
    if orphaned:
        stage = orphaned[0]
        seal_path = root / "seals" / f"{stage}.json"
        seal = _read(seal_path, label=f"orphan {stage} seal")
        reference = {
            "stage": stage,
            "path": f"seals/{stage}.json",
            "sha256": _sha256(seal_path),
        }
        adopted = {
            **state,
            "stage": stage,
            "terminal_reason": seal.get("terminal_reason"),
            "seals": [*state["seals"], reference],
        }
        _validate_seals(program, root, adopted)
        atomic_replace_json(path, adopted)
        state = adopted
    return root, state


def _seal(
    program: ParallelStrategyProgram,
    cycle: ScheduledCycle,
    *,
    stage: str,
    artifacts: Sequence[Path],
    clock: Callable[[], datetime],
    terminal_reason: str | None = None,
) -> dict[str, Any]:
    root, state = _load_state(program, cycle)
    if stage not in _STAGES[1:]:
        raise ParallelStrategyRunnerError("seal stage is invalid")
    intent_path = root / "intents" / f"{stage}.json"
    if intent_path.exists():
        intent = _read(intent_path, label=f"{stage} seal intent")
    else:
        intent = {
            "schema_version": 1,
            "stage": stage,
            "recorded_at": _utc_text(clock()),
            "terminal_reason": terminal_reason,
        }
        _write_once(intent_path, intent)
    if intent.get("terminal_reason") != terminal_reason:
        raise ParallelStrategyRunnerError("seal intent terminal reason differs")
    prior_seals = [
        _confined_path(root, reference["path"], label="prior cycle seal path")
        for reference in state["seals"]
    ]
    terminal_epoch_files: list[Path] = []
    sealed_epoch = (
        "predecision"
        if stage == "decisions_sealed"
        else "settlement" if stage == "outcome_sealed" else None
    )
    if sealed_epoch is not None:
        epoch_root = (
            program.root
            / "budget/parallel-strategy-v1/epochs"
            / f"c{cycle.index:03d}-{sealed_epoch}"
        )
        if epoch_root.is_dir() and not epoch_root.is_symlink():
            terminal_epoch_files.extend(
                path
                for path in epoch_root.rglob("*")
                if path.is_file() and not path.is_symlink()
            )
    unique = sorted(
        {
            path.absolute()
            for path in (*artifacts, *prior_seals, *terminal_epoch_files)
        },
        key=str,
    )
    seal = {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "cycle_index": cycle.index,
        "stage": stage,
        "recorded_at": intent["recorded_at"],
        "terminal_reason": terminal_reason,
        "artifacts": [_artifact(program.root, path) for path in unique],
    }
    seal_path = _write_once(root / "seals" / f"{stage}.json", seal)
    reference = {"stage": stage, "path": seal_path.relative_to(root).as_posix(), "sha256": _sha256(seal_path)}
    seals = [item for item in state["seals"] if item.get("stage") != stage]
    seals.append(reference)
    updated = {**state, "stage": stage, "terminal_reason": terminal_reason, "seals": seals}
    atomic_replace_json(root / "state.json", updated)
    return updated


def _validate_seals(
    program: ParallelStrategyProgram, root: Path, state: Mapping[str, Any]
) -> None:
    seen: set[str] = set()
    ordered_stages: list[str] = []
    for reference in state.get("seals", []):
        if (
            not isinstance(reference, Mapping)
            or set(reference) != {"stage", "path", "sha256"}
            or reference.get("stage") in seen
        ):
            raise ParallelStrategyRunnerError("cycle seal reference differs")
        reference_stage = str(reference["stage"])
        seen.add(reference_stage)
        ordered_stages.append(reference_stage)
        if reference.get("path") != f"seals/{reference_stage}.json":
            raise ParallelStrategyRunnerError("cycle seal path differs")
        path = _confined_path(root, reference["path"], label="cycle seal path")
        if _sha256(path) != reference["sha256"]:
            raise ParallelStrategyRunnerError("cycle seal hash differs")
        seal = _read(path, label="cycle seal")
        if (
            set(seal)
            != {
                "schema_version",
                "program_id",
                "cycle_index",
                "stage",
                "recorded_at",
                "terminal_reason",
                "artifacts",
            }
            or seal.get("schema_version") != 1
            or seal.get("program_id") != PROGRAM_ID
            or seal.get("stage") != reference_stage
            or seal.get("cycle_index") != state["cycle_index"]
            or not isinstance(seal.get("artifacts"), list)
        ):
            raise ParallelStrategyRunnerError("cycle seal identity differs")
        _parse_utc(seal.get("recorded_at"), field="cycle seal time")
        reason = seal.get("terminal_reason")
        if (reference_stage == "unscorable") is not (
            isinstance(reason, str) and bool(reason)
        ):
            if not (reference_stage != "unscorable" and reason is None):
                raise ParallelStrategyRunnerError("cycle seal terminal reason differs")
        intent = _read(
            root / "intents" / f"{reference_stage}.json",
            label=f"{reference_stage} seal intent",
        )
        if intent != {
            "schema_version": 1,
            "stage": reference_stage,
            "recorded_at": seal["recorded_at"],
            "terminal_reason": reason,
        }:
            raise ParallelStrategyRunnerError("cycle seal intent differs")
        for artifact in seal.get("artifacts", []):
            if not isinstance(artifact, Mapping) or set(artifact) != {"path", "sha256"}:
                raise ParallelStrategyRunnerError("sealed artifact reference differs")
            target = _confined_path(
                program.root, artifact["path"], label="sealed artifact path"
            )
            if _sha256(target) != artifact["sha256"]:
                raise ParallelStrategyRunnerError("sealed artifact hash differs")
    stage = state["stage"]
    if stage == "planned":
        expected_chains = ([],)
    elif stage == "panel_sealed":
        expected_chains = (["panel_sealed"],)
    elif stage == "decisions_sealed":
        expected_chains = (["panel_sealed", "decisions_sealed"],)
    elif stage == "outcome_sealed":
        expected_chains = (
            ["panel_sealed", "decisions_sealed", "outcome_sealed"],
        )
    else:
        expected_chains = (
            ["unscorable"],
            ["panel_sealed", "unscorable"],
            ["panel_sealed", "decisions_sealed", "unscorable"],
        )
    if ordered_stages not in expected_chains:
        raise ParallelStrategyRunnerError("cycle seal chain differs from its stage")
    if stage != "planned":
        current_seal = _read(
            root / "seals" / f"{stage}.json", label="current cycle seal"
        )
        if current_seal.get("terminal_reason") != state.get("terminal_reason"):
            raise ParallelStrategyRunnerError(
                "cycle state terminal reason differs from its seal"
            )
    for index, reference_stage in enumerate(ordered_stages[1:], start=1):
        previous = root / "seals" / f"{ordered_stages[index - 1]}.json"
        current = _read(
            root / "seals" / f"{reference_stage}.json", label="cycle seal"
        )
        prior_relative = previous.absolute().relative_to(program.root.absolute()).as_posix()
        if {
            "path": prior_relative,
            "sha256": _sha256(previous),
        } not in current["artifacts"]:
            raise ParallelStrategyRunnerError("cycle seal does not bind its predecessor")


@contextmanager
def provider_lock(program: ParallelStrategyProgram):
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    directory = runtime / "nansen-signal-lab-provider"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "provider.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _call(
    transport: EvidenceTransport,
    *,
    cycle: ScheduledCycle,
    epoch: str,
    logical_id: str,
    endpoint: str,
    payload: dict[str, Any] | None,
    expected_credits: int = 1,
    method: str = "POST",
    allowed: Callable[[], bool],
) -> EvidenceResult:
    response, artifacts = transport.call(
        cycle_index=cycle.index,
        epoch=epoch,
        logical_request_id=logical_id,
        method=method,
        endpoint=endpoint,
        payload=payload,
        expected_credits=expected_credits,
        transport_allowed=allowed,
    )
    body = getattr(response, "body", None)
    retrieved_text = getattr(response, "response_retrieved_at", None)
    if not isinstance(body, dict) or not isinstance(retrieved_text, str):
        raise ParallelStrategyRunnerError("evidence transport returned an invalid result")
    return EvidenceResult(
        body=body,
        retrieved_at=_parse_utc(retrieved_text, field="provider retrieval time"),
        artifacts=tuple(artifacts),
    )


def _open_epoch(
    transport: EvidenceTransport,
    *,
    cycle: ScheduledCycle,
    epoch: str,
    allowed: Callable[[], bool],
) -> tuple[Path, ...]:
    contract_paths: tuple[Path, ...]
    budget = getattr(transport, "budget", None)
    if budget is not None:
        guard = budget.epoch_guard(cycle.index, epoch)
        entries = guard.replay().entries
        if entries:
            adopter = getattr(transport, "adopt_openapi", None)
            if not callable(adopter):
                raise ParallelStrategyRunnerError(
                    "resumed epoch cannot replay its OpenAPI evidence"
                )
            contract_paths = tuple(adopter(cycle.index, epoch))
        else:
            contract_paths = (
                transport.verify_openapi(cycle.index, epoch, allowed),
            )
    else:
        contract_paths = (
            transport.verify_openapi(cycle.index, epoch, allowed),
        )
    account = _call(
        transport,
        cycle=cycle,
        epoch=epoch,
        logical_id="account",
        endpoint="account",
        payload=None,
        expected_credits=0,
        method="GET",
        allowed=allowed,
    )
    return (*contract_paths, *account.artifacts)


def _needs_second_page(body: Mapping[str, Any], *, expected_page: int) -> bool:
    pagination = body.get("pagination")
    if (
        not isinstance(pagination, Mapping)
        or type(pagination.get("page")) is not int
        or pagination.get("page") != expected_page
        or type(pagination.get("per_page")) is not int
        or pagination.get("per_page") != 1000
        or not isinstance(pagination.get("is_last_page"), bool)
        or not isinstance(body.get("data"), list)
        or len(body["data"]) > 1000
    ):
        raise B2DesignError("provider pagination differs from the frozen contract")
    if expected_page == 2 and pagination["is_last_page"] is not True:
        raise B2DesignError("second page is not terminal")
    return pagination["is_last_page"] is False


def _result_reference(
    program: ParallelStrategyProgram, result: EvidenceResult
) -> dict[str, Any]:
    request_paths = [
        path for path in result.artifacts if path.name.endswith("-request.json")
    ]
    response_paths = [
        path
        for path in result.artifacts
        if path.name.endswith("-response.json")
        and not path.name.endswith("-response-metadata.json")
    ]
    metadata_paths = [
        path
        for path in result.artifacts
        if path.name.endswith("-response-metadata.json")
    ]
    if not (
        len(request_paths) == len(response_paths) == len(metadata_paths) == 1
    ):
        raise ParallelStrategyRunnerError(
            "evidence lacks a unique request/response/metadata binding"
        )

    def record(path: Path, *, body: bool = False) -> dict[str, str]:
        relative = path.absolute().relative_to(program.root.absolute()).as_posix()
        target = _confined_path(program.root, relative, label="raw evidence path")
        value = {"path": relative, "sha256": _sha256(target)}
        if body:
            value["body_sha256"] = canonical_sha256(result.body)
        return value

    request_path = request_paths[0]
    relative = request_path.absolute().relative_to(program.root.absolute())
    parts = relative.parts
    try:
        epoch_position = parts.index("epochs")
        epoch_id = parts[epoch_position + 1]
    except (ValueError, IndexError) as exc:
        raise ParallelStrategyRunnerError(
            "evidence request is outside the frozen budget epoch"
        ) from exc
    if not epoch_id.startswith("c") or "-" not in epoch_id:
        raise ParallelStrategyRunnerError("evidence epoch identity differs")
    cycle_text, epoch = epoch_id.split("-", 1)
    try:
        cycle_index = int(cycle_text[1:])
    except ValueError as exc:
        raise ParallelStrategyRunnerError("evidence cycle identity differs") from exc
    return {
        "schema_version": 1,
        "cycle_index": cycle_index,
        "epoch": epoch,
        "request": record(request_path),
        "response": record(response_paths[0], body=True),
        "metadata": record(metadata_paths[0]),
    }


def _load_result_reference(
    program: ParallelStrategyProgram,
    reference: Mapping[str, Any],
    *,
    expected_cycle: ScheduledCycle | None = None,
    expected_epoch: str | None = None,
    expected_method: str | None = None,
    expected_endpoint: str | None = None,
    expected_payload: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], datetime, Path]:
    if (
        not isinstance(reference, Mapping)
        or set(reference)
        != {"schema_version", "cycle_index", "epoch", "request", "response", "metadata"}
        or reference.get("schema_version") != 1
        or type(reference.get("cycle_index")) is not int
        or reference.get("epoch") not in {"predecision", "settlement"}
    ):
        raise ParallelStrategyRunnerError(
            "raw request/response metadata binding differs"
        )
    cycle_index = int(reference["cycle_index"])
    epoch = str(reference["epoch"])
    if (
        expected_cycle is not None and cycle_index != expected_cycle.index
    ) or (expected_epoch is not None and epoch != expected_epoch):
        raise ParallelStrategyRunnerError("raw evidence belongs to another epoch")

    def artifact(name: str, *, body: bool = False) -> Path:
        record = reference.get(name)
        keys = {"path", "sha256", "body_sha256"} if body else {"path", "sha256"}
        if not isinstance(record, Mapping) or set(record) != keys:
            raise ParallelStrategyRunnerError(f"raw {name} reference differs")
        target = _confined_path(
            program.root, record["path"], label=f"raw {name} path"
        )
        if _sha256(target) != record["sha256"]:
            raise ParallelStrategyRunnerError(f"raw {name} reference hash differs")
        return target

    request_path = artifact("request")
    path = artifact("response", body=True)
    metadata_path = artifact("metadata")
    try:
        body = json.loads(path.read_bytes())
        request = json.loads(request_path.read_bytes())
        metadata = json.loads(metadata_path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ParallelStrategyRunnerError("raw evidence binding is unreadable") from exc
    response_record = reference["response"]
    if not isinstance(body, dict) or canonical_sha256(body) != response_record["body_sha256"]:
        raise ParallelStrategyRunnerError("raw response parsed body differs")
    if (
        not isinstance(request, dict)
        or canonical_json_bytes(request) != request_path.read_bytes()
        or not isinstance(metadata, dict)
        or canonical_json_bytes(metadata) != metadata_path.read_bytes()
        or metadata.get("attempt") != 1
        or metadata.get("response_file") != path.name
        or metadata.get("response_sha256") != _sha256(path)
        or metadata.get("body_parse_status") != "json_object"
        or type(metadata.get("status_code")) is not int
        or not 200 <= metadata["status_code"] < 300
    ):
        raise ParallelStrategyRunnerError("raw response metadata binding differs")
    method = request.get("method")
    endpoint = request.get("endpoint")
    payload = request.get("payload")
    if (
        not isinstance(method, str)
        or not isinstance(endpoint, str)
        or request.get("transmission_may_begin") is not True
        or request.get("request_sha256")
        != canonical_request_sha256(method, endpoint, payload)
        or (expected_method is not None and method != expected_method)
        or (expected_endpoint is not None and endpoint != expected_endpoint)
        or (expected_endpoint == "account" and payload is not None)
        or (
            expected_payload is not None
            and payload != dict(expected_payload)
        )
    ):
        raise ParallelStrategyRunnerError("raw request endpoint/payload binding differs")
    retrieved = _parse_utc(
        metadata.get("response_retrieved_at"), field="raw response retrieval time"
    )
    started = _parse_utc(
        metadata.get("request_started_at"), field="raw request start time"
    )
    written = _parse_utc(
        metadata.get("artifact_written_at"), field="raw metadata write time"
    )
    if started > retrieved or retrieved > written:
        raise ParallelStrategyRunnerError("raw response receipt timestamps differ")

    epoch_id = f"c{cycle_index:03d}-{epoch}"
    expected_prefix = (
        program.root
        / "budget/parallel-strategy-v1/epochs"
        / epoch_id
    )
    try:
        request_path.absolute().relative_to(expected_prefix.absolute())
        path.absolute().relative_to(expected_prefix.absolute())
        metadata_path.absolute().relative_to(expected_prefix.absolute())
    except ValueError as exc:
        raise ParallelStrategyRunnerError("raw evidence escapes its budget epoch") from exc
    limits = (
        (PREDECISION_MAX_ATTEMPTS, PREDECISION_MAX_CREDITS)
        if epoch == "predecision"
        else (SETTLEMENT_MAX_ATTEMPTS, SETTLEMENT_MAX_CREDITS)
    )
    if not (expected_prefix / "budget/head.json").is_file():
        raise ParallelStrategyRunnerError("raw evidence budget head is absent")
    guard = BudgetGuard(expected_prefix, max_calls=limits[0], max_credits=limits[1])
    totals = guard.replay()
    caller_id = request.get("caller_request_id")
    entry = next(
        (
            item
            for item in totals.entries
            if item.reservation_id == caller_id
        ),
        None,
    )
    if (
        entry is None
        or entry.endpoint != endpoint
        or entry.request_sha256 != request.get("request_sha256")
        or entry.request_artifact_sha256 != _sha256(request_path)
        or entry.response_artifact_sha256 != _sha256(metadata_path)
        or entry.attempt_count != 1
        or entry.state not in {"confirmed_zero", "confirmed_used"}
    ):
        raise ParallelStrategyRunnerError("raw evidence budget journal binding differs")
    if endpoint == "account":
        body_remaining = body.get("credits_remaining")
        if (
            body.get("plan") not in {"free", "pro"}
            or type(body_remaining) is not int
            or entry.state != "confirmed_zero"
            or entry.credit_cost != 0
            or entry.credit_used != 0
            or entry.credit_remaining != body_remaining
            or metadata.get("credit_cost") != 0
            or metadata.get("credit_used") not in {None, 0}
            or metadata.get("credit_remaining") not in {None, body_remaining}
        ):
            raise ParallelStrategyRunnerError(
                "account body/metadata differs from the budget journal"
            )
    elif (
        metadata.get("credit_cost") != entry.credit_cost
        or metadata.get("credit_used") != entry.credit_used
        or metadata.get("credit_remaining") != entry.credit_remaining
    ):
        raise ParallelStrategyRunnerError(
            "response pricing metadata differs from the budget journal"
        )
    return body, retrieved, path


def _reference_for_entry(
    program: ParallelStrategyProgram,
    *,
    cycle: ScheduledCycle,
    epoch: str,
    entry: Any,
) -> dict[str, Any]:
    guard_root = (
        program.root
        / "budget/parallel-strategy-v1/epochs"
        / f"c{cycle.index:03d}-{epoch}"
    )
    raw_root = guard_root / "raw/nansen" / entry.reservation_id
    prefix = f"attempt-{entry.attempt_count}"
    request = raw_root / f"{prefix}-request.json"
    response = raw_root / f"{prefix}-response.json"
    metadata = raw_root / f"{prefix}-response-metadata.json"
    try:
        body = json.loads(response.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ParallelStrategyRunnerError(
            "confirmed budget entry has no readable response body"
        ) from exc
    if not isinstance(body, dict):
        raise ParallelStrategyRunnerError(
            "confirmed budget entry response is not an object"
        )

    def record(path: Path, *, parsed_body: bool = False) -> dict[str, str]:
        try:
            relative = path.absolute().relative_to(program.root.absolute()).as_posix()
        except ValueError as exc:
            raise ParallelStrategyRunnerError(
                "budget entry artifact escapes program root"
            ) from exc
        value = {"path": relative, "sha256": _sha256(path)}
        if parsed_body:
            value["body_sha256"] = canonical_sha256(body)
        return value

    return {
        "schema_version": 1,
        "cycle_index": cycle.index,
        "epoch": epoch,
        "request": record(request),
        "response": record(response, parsed_body=True),
        "metadata": record(metadata),
    }


def _validate_complete_epoch_budget(
    program: ParallelStrategyProgram,
    *,
    cycle: ScheduledCycle,
    epoch: str,
    references: Sequence[Mapping[str, Any]],
) -> None:
    guard_root = (
        program.root
        / "budget/parallel-strategy-v1/epochs"
        / f"c{cycle.index:03d}-{epoch}"
    )
    limits = (
        (PREDECISION_MAX_ATTEMPTS, PREDECISION_MAX_CREDITS)
        if epoch == "predecision"
        else (SETTLEMENT_MAX_ATTEMPTS, SETTLEMENT_MAX_CREDITS)
    )
    guard = BudgetGuard(guard_root, max_calls=limits[0], max_credits=limits[1])
    totals = guard.replay()
    accounts = [entry for entry in totals.entries if entry.endpoint == "account"]
    if len(accounts) != 1:
        raise ParallelStrategyRunnerError(
            "complete epoch must contain exactly one account baseline"
        )
    account = accounts[0]
    account_reference = _reference_for_entry(
        program, cycle=cycle, epoch=epoch, entry=account
    )
    _load_result_reference(
        program,
        account_reference,
        expected_cycle=cycle,
        expected_epoch=epoch,
        expected_method="GET",
        expected_endpoint="account",
    )
    referenced_ids = {account.reservation_id}
    for reference in references:
        request_record = reference.get("request")
        if not isinstance(request_record, Mapping):
            raise ParallelStrategyRunnerError("epoch request reference differs")
        request_path = _confined_path(
            program.root,
            request_record.get("path"),
            label="epoch request reference path",
        )
        request = _read(request_path, label="epoch request reference")
        caller = request.get("caller_request_id")
        if not isinstance(caller, str) or not caller:
            raise ParallelStrategyRunnerError("epoch caller request ID differs")
        referenced_ids.add(caller)
    if referenced_ids != {entry.reservation_id for entry in totals.entries}:
        raise ParallelStrategyRunnerError(
            "complete epoch contains an orphan or unreferenced budget entry"
        )


def _outcome_attestation(
    program: ParallelStrategyProgram,
    outcome: Mapping[str, Any],
    evidence_results: Sequence[EvidenceResult],
) -> dict[str, Any]:
    references: list[dict[str, Any]] = []
    for result in evidence_results:
        references.append(_result_reference(program, result))
    value = dict(outcome)
    value["raw_evidence"] = references
    value["replay_attestation_sha256"] = canonical_sha256(
        {"outcome": outcome, "raw_evidence": references}
    )
    return value


def _validate_outcome_attestation(
    program: ParallelStrategyProgram, outcome: Mapping[str, Any]
) -> None:
    references = outcome.get("raw_evidence")
    if not isinstance(references, list) or len(references) < 3:
        raise ParallelStrategyRunnerError("outcome raw replay evidence differs")
    unsigned = {
        key: value
        for key, value in outcome.items()
        if key not in {"raw_evidence", "replay_attestation_sha256"}
    }
    expected = canonical_sha256(
        {"outcome": unsigned, "raw_evidence": references}
    )
    if outcome.get("replay_attestation_sha256") != expected:
        raise ParallelStrategyRunnerError("outcome replay attestation differs")


def _replay_outcome(
    program: ParallelStrategyProgram,
    *,
    cycle: ScheduledCycle,
    selection: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> None:
    _validate_outcome_attestation(program, outcome)
    references = outcome["raw_evidence"]
    evidence = outcome.get("evidence_sha256")
    if not isinstance(evidence, Mapping):
        raise ParallelStrategyRunnerError("outcome evidence partition is absent")
    buy_count = len(evidence.get("buy_pages", []))
    sell_count = len(evidence.get("sell_pages", []))
    if len(references) != buy_count + sell_count + 1:
        raise ParallelStrategyRunnerError("outcome evidence partition differs")
    t0 = _parse_utc(outcome.get("t0"), field="outcome t0")
    windows = execution_windows(t0)
    parsed: list[tuple[dict[str, Any], datetime, Path]] = []
    for index, reference in enumerate(references):
        if index < buy_count:
            side = "BUY"
            page = index + 1
            payload = dex_payload(
                selection,
                side=side,
                start=windows["entry_start"],
                end=windows["entry_end"],
                page=page,
            )
            endpoint = "tgm/dex-trades"
        elif index < buy_count + sell_count:
            side = "SELL"
            page = index - buy_count + 1
            payload = dex_payload(
                selection,
                side=side,
                start=windows["exit_start"],
                end=windows["exit_end"],
                page=page,
            )
            endpoint = "tgm/dex-trades"
        else:
            payload = ohlcv_payload(
                selection,
                start=windows["ohlcv_start"],
                end=windows["ohlcv_end"],
            )
            endpoint = "tgm/token-ohlcv"
        parsed.append(
            _load_result_reference(
                program,
                reference,
                expected_cycle=cycle,
                expected_epoch="settlement",
                expected_method="POST",
                expected_endpoint=endpoint,
                expected_payload=payload,
            )
        )
    retrieved = parsed[-1][1]
    if retrieved != _parse_utc(
        outcome.get("retrieved_at"), field="outcome retrieved_at"
    ):
        raise ParallelStrategyRunnerError("outcome retrieval is not metadata-bound")
    rebuilt = build_counterfactual_outcome(
        candidate=selection,
        cycle=cycle,
        t0=t0,
        buy_pages=[body for body, _, _ in parsed[:buy_count]],
        sell_pages=[
            body for body, _, _ in parsed[buy_count : buy_count + sell_count]
        ],
        ohlcv_body=parsed[-1][0],
        retrieved_at=retrieved,
    )
    rebuilt_attested = dict(rebuilt)
    rebuilt_attested["raw_evidence"] = references
    rebuilt_attested["replay_attestation_sha256"] = canonical_sha256(
        {"outcome": rebuilt, "raw_evidence": references}
    )
    if rebuilt_attested != dict(outcome):
        raise ParallelStrategyRunnerError("outcome does not replay from raw evidence")


def _program_seal(
    program: ParallelStrategyProgram,
    *,
    name: str,
    stage: str,
    artifacts: Sequence[Path],
) -> Path:
    references = [
        _artifact(program.root, path)
        for path in sorted({path.absolute() for path in artifacts}, key=str)
    ]
    return _write_once(
        program.root / "seals" / f"{name}.json",
        {
            "schema_version": 1,
            "program_id": PROGRAM_ID,
            "stage": stage,
            "artifacts": references,
        },
    )


def _validate_program_seal(
    program: ParallelStrategyProgram, *, name: str, stage: str
) -> dict[str, Any]:
    seal = _read(program.root / "seals" / f"{name}.json", label=f"{name} seal")
    if (
        seal.get("schema_version") != 1
        or seal.get("program_id") != PROGRAM_ID
        or seal.get("stage") != stage
        or not isinstance(seal.get("artifacts"), list)
    ):
        raise ParallelStrategyRunnerError(f"{name} seal identity differs")
    for reference in seal["artifacts"]:
        if not isinstance(reference, Mapping) or set(reference) != {"path", "sha256"}:
            raise ParallelStrategyRunnerError(f"{name} seal artifact differs")
        target = _confined_path(
            program.root, reference["path"], label=f"{name} seal artifact path"
        )
        if _sha256(target) != reference["sha256"]:
            raise ParallelStrategyRunnerError(f"{name} sealed artifact hash differs")
    return seal


def _prior_counts(
    program: ParallelStrategyProgram,
    cycle: ScheduledCycle,
    *,
    replay_prior: bool = True,
) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    identities: dict[tuple[str, str], int] = {}
    chains: dict[str, int] = {}
    for prior in SCHEDULE:
        if prior.index >= cycle.index or prior.phase != cycle.phase:
            continue
        root, state = _load_state(program, prior)
        if state["stage"] not in _TERMINAL:
            raise ParallelStrategyRunnerError("an earlier phase cycle is not terminal")
        if replay_prior:
            state = check_cycle(program, prior.index)
        else:
            _validate_seals(program, root, state)
        retained_stages = {
            reference.get("stage")
            for reference in state.get("seals", [])
            if isinstance(reference, Mapping)
        }
        if "panel_sealed" not in retained_stages:
            continue
        panel_path = root / "derived/panel.json"
        if not panel_path.exists():
            raise ParallelStrategyRunnerError(
                "a retained prior panel seal has no panel artifact"
            )
        panel = _read(panel_path, label="prior panel")
        for selected in panel.get("selected", []):
            if not isinstance(selected, Mapping):
                continue
            identity = (str(selected.get("chain")), str(selected.get("token_address")))
            identities[identity] = identities.get(identity, 0) + 1
            chain = identity[0]
            chains[chain] = chains.get(chain, 0) + 1
    return identities, chains


def _terminalize(
    program: ParallelStrategyProgram,
    cycle: ScheduledCycle,
    reason: str,
    *,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    root, state = _load_state(program, cycle)
    if state["stage"] in _TERMINAL:
        check_cycle(program, cycle.index)
        return state
    artifacts = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and "seals" not in path.parts
        and "intents" not in path.parts
        and path.name != "state.json"
    ]
    epoch_prefix = f"c{cycle.index:03d}-"
    epochs_root = program.root / "budget/parallel-strategy-v1/epochs"
    if epochs_root.is_dir() and not epochs_root.is_symlink():
        for epoch_root in sorted(epochs_root.iterdir()):
            if (
                epoch_root.name.startswith(epoch_prefix)
                and epoch_root.is_dir()
                and not epoch_root.is_symlink()
            ):
                artifacts.extend(
                    path
                    for path in epoch_root.rglob("*")
                    if path.is_file() and not path.is_symlink()
                )
    return _seal(
        program,
        cycle,
        stage="unscorable",
        artifacts=artifacts,
        terminal_reason=reason,
        clock=clock,
    )


def _program_fatal(
    program: ParallelStrategyProgram,
    cycle: ScheduledCycle,
    reason: str,
    *,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    intent_path = program.root / "intents/program-fatal.json"
    if intent_path.exists():
        intent = _read(intent_path, label="program fatal intent")
    else:
        intent = {
            "schema_version": 1,
            "program_id": PROGRAM_ID,
            "cycle_index": cycle.index,
            "recorded_at": _utc_text(clock()),
            "reason": reason,
        }
        _write_once(intent_path, intent)
    _write_once(program.root / "seals/program-fatal.json", intent)
    return _terminalize(
        program,
        cycle,
        f"program_fatal:{intent['reason']}",
        clock=clock,
    )


def _program_fatal_reason(program: ParallelStrategyProgram) -> str | None:
    intent_path = program.root / "intents/program-fatal.json"
    seal_path = program.root / "seals/program-fatal.json"
    if not intent_path.exists() and not seal_path.exists():
        return None
    if not intent_path.is_file() or intent_path.is_symlink():
        raise ParallelStrategyRunnerError("program fatal intent is absent or unsafe")
    intent = _read(intent_path, label="program fatal intent")
    if (
        intent.get("schema_version") != 1
        or intent.get("program_id") != PROGRAM_ID
        or type(intent.get("cycle_index")) is not int
        or not isinstance(intent.get("recorded_at"), str)
        or not isinstance(intent.get("reason"), str)
        or not intent["reason"]
    ):
        raise ParallelStrategyRunnerError("program fatal intent differs")
    _parse_utc(intent["recorded_at"], field="program fatal time")
    if seal_path.exists():
        if _read(seal_path, label="program fatal seal") != intent:
            raise ParallelStrategyRunnerError("program fatal seal differs from intent")
    return str(intent["reason"])


def repair_program_fatal(manifest_path: Path) -> dict[str, Any]:
    """Finish the durable fatal transition without constructing a provider client."""

    program = require_stopped_v1_activation(manifest_path)
    reason = _program_fatal_reason(program)
    if reason is None:
        raise ParallelStrategyRunnerError("program has no fatal intent to repair")
    intent = _read(
        program.root / "intents/program-fatal.json", label="program fatal intent"
    )
    cycle_index = intent["cycle_index"]
    if not 1 <= cycle_index <= len(SCHEDULE):
        raise ParallelStrategyRunnerError("program fatal cycle is outside 1..85")
    _write_once(program.root / "seals/program-fatal.json", intent)
    return _terminalize(
        program,
        SCHEDULE[cycle_index - 1],
        f"program_fatal:{reason}",
        clock=lambda: _parse_utc(intent["recorded_at"], field="program fatal time"),
    )


def run_predecision(
    manifest_path: Path,
    cycle_index: int,
    *,
    transport: EvidenceTransport,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    program = require_stopped_v1_activation(manifest_path)
    if not 1 <= cycle_index <= len(SCHEDULE):
        raise ParallelStrategyRunnerError("cycle index is outside 1..85")
    cycle = SCHEDULE[cycle_index - 1]
    if _program_fatal_reason(program) is not None:
        raise ParallelStrategyRunnerError("program is globally fatal")
    if cycle.phase == "validation":
        discovery_statuses, discovery_records = _phase_records(program, "discovery")
        expected_family = freeze_discovery_family(
            cycle_statuses=discovery_statuses, records=discovery_records
        )
        family_path = program.root / "derived/discovery-family.json"
        if (
            expected_family.get("stage") != "validation_family_frozen"
            or not family_path.is_file()
            or _read(family_path, label="discovery family") != expected_family
        ):
            raise ParallelStrategyRunnerError(
                "validation requires the exact frozen discovery family"
            )
        try:
            _validate_program_seal(
                program,
                name="discovery-family",
                stage="validation_family_frozen",
            )
        except ParallelStrategyRunnerError as exc:
            raise ParallelStrategyRunnerError(
                "validation requires the exact frozen discovery family seal"
            ) from exc
    root, state = _load_state(program, cycle)
    _validate_seals(program, root, state)
    if state["stage"] in _TERMINAL or state["stage"] == "decisions_sealed":
        return state
    now = _utc(clock(), field="current time")
    epoch_root = (
        program.root
        / "budget/parallel-strategy-v1/epochs"
        / f"c{cycle.index:03d}-predecision"
    )
    resuming = state["stage"] == "panel_sealed" or epoch_root.exists()
    if resuming:
        if now >= cycle.scheduled_at + timedelta(minutes=43, seconds=30):
            return _terminalize(
                program, cycle, "predecision_cutoff_elapsed", clock=clock
            )
    else:
        admission = start_state(cycle, now)
        if admission == "wait":
            raise ParallelStrategyRunnerError("cycle has not reached its scheduled start")
        if admission == "missed":
            return _terminalize(program, cycle, "missed_start_window", clock=clock)
    allowed = lambda: predecision_transport_allowed(cycle, clock())
    try:
        with provider_lock(program):
            epoch_paths = _open_epoch(
                transport, cycle=cycle, epoch="predecision", allowed=allowed
            )
            screener = _call(
                transport,
                cycle=cycle,
                epoch="predecision",
                logical_id="screener",
                endpoint="token-screener",
                payload=screener_payload(),
                allowed=allowed,
            )
            eligible = validate_screener(screener.body)
            identity_counts, chain_counts = _prior_counts(program, cycle)
            selected = select_cycle(
                eligible,
                cycle=cycle,
                prior_identity_counts=identity_counts,
                prior_chain_counts=chain_counts,
            )
            panel_path = _write_once(
                root / "derived/panel.json",
                {
                    "schema_version": 1,
                    "program_id": PROGRAM_ID,
                    "cycle_index": cycle.index,
                    "phase": cycle.phase,
                    "scheduled_at": _utc_text(cycle.scheduled_at),
                    "screener_evidence": _result_reference(program, screener),
                    "selected": selected,
                },
            )
            state = _seal(
                program,
                cycle,
                stage="panel_sealed",
                artifacts=(*epoch_paths, *screener.artifacts, panel_path),
                clock=clock,
            )
            evidence_paths: list[Path] = []
            feature_rows: list[dict[str, Any]] = []
            crosswalk = load_parallel_strategy_contract(program.repo_root)
            for token_index, selection in enumerate(selected, start=1):
                prefix = f"token-{token_index:02d}"
                smart = _call(
                    transport,
                    cycle=cycle,
                    epoch="predecision",
                    logical_id=f"{prefix}/smart-money",
                    endpoint="tgm/flows",
                    payload=smart_money_payload(selection, cycle),
                    allowed=allowed,
                )
                evidence_paths.extend(smart.artifacts)
                fi = _call(
                    transport,
                    cycle=cycle,
                    epoch="predecision",
                    logical_id=f"{prefix}/flow-intelligence",
                    endpoint="tgm/flow-intelligence",
                    payload=flow_intelligence_payload(selection, cycle),
                    allowed=allowed,
                )
                evidence_paths.extend(fi.artifacts)
                breadth: dict[str, dict[str, Any]] = {}
                breadth_results: dict[str, list[EvidenceResult]] = {}
                for side in ("BUY", "SELL"):
                    pages: list[dict[str, Any]] = []
                    first = _call(
                        transport,
                        cycle=cycle,
                        epoch="predecision",
                        logical_id=f"{prefix}/wbs-{side.lower()}-1",
                        endpoint="tgm/who-bought-sold",
                        payload=breadth_payload(selection, cycle, side, 1),
                        allowed=allowed,
                    )
                    evidence_paths.extend(first.artifacts)
                    pages.append(first.body)
                    page_results = [first]
                    if _needs_second_page(first.body, expected_page=1):
                        second = _call(
                            transport,
                            cycle=cycle,
                            epoch="predecision",
                            logical_id=f"{prefix}/wbs-{side.lower()}-2",
                            endpoint="tgm/who-bought-sold",
                            payload=breadth_payload(selection, cycle, side, 2),
                            allowed=allowed,
                        )
                        evidence_paths.extend(second.artifacts)
                        _needs_second_page(second.body, expected_page=2)
                        pages.append(second.body)
                        page_results.append(second)
                    breadth[side] = validate_wbs_evidence(
                        pages, candidate=selection, cycle=cycle, side=side
                    )
                    breadth_results[side] = page_results
                features = {
                    "buy": breadth["BUY"],
                    "sell": breadth["SELL"],
                    "flow_intelligence": validate_flow_intelligence(
                        fi.body,
                        candidate=selection,
                        cycle=cycle,
                        cache_hit=False,
                        retrieved_at=fi.retrieved_at,
                    ),
                    "smart_money": validate_smart_money_evidence(
                        smart.body,
                        candidate=selection,
                        cycle=cycle,
                        source_id=f"{PROGRAM_ID}:{selection['event_id']}",
                    ),
                }
                decisions = {
                    candidate["candidate_id"]: candidate_decision(
                        selection=selection,
                        features=features,
                        candidate=candidate,
                        cycle=cycle,
                        sealed_crosswalk=crosswalk,
                    )
                    for candidate in crosswalk["candidates"]
                }
                feature_rows.append(
                    {
                        "token_index": token_index,
                        "selection": selection,
                        "raw_evidence": {
                            "smart_money": _result_reference(program, smart),
                            "flow_intelligence": _result_reference(program, fi),
                            "buy": [
                                _result_reference(program, result)
                                for result in breadth_results["BUY"]
                            ],
                            "sell": [
                                _result_reference(program, result)
                                for result in breadth_results["SELL"]
                            ],
                        },
                        "features": features,
                        "decisions": decisions,
                    }
                )
            decision_intent_path = root / "intents/decisions_sealed.json"
            if decision_intent_path.exists():
                decision_intent = _read(
                    decision_intent_path, label="decision seal intent"
                )
                if (
                    decision_intent.get("schema_version") != 1
                    or decision_intent.get("stage") != "decisions_sealed"
                    or decision_intent.get("terminal_reason") is not None
                ):
                    raise ParallelStrategyRunnerError(
                        "decision seal intent identity differs"
                    )
                observed = _parse_utc(
                    decision_intent.get("recorded_at"),
                    field="decision seal intent time",
                )
            else:
                observed = _utc(clock(), field="decision seal time")
                _write_once(
                    decision_intent_path,
                    {
                        "schema_version": 1,
                        "stage": "decisions_sealed",
                        "recorded_at": _utc_text(observed),
                        "terminal_reason": None,
                    },
                )
            if observed > cycle.scheduled_at + DECISION_DEADLINE:
                raise EvidenceCutoff("decision deadline elapsed")
            t0 = decision_t0(cycle, observed)
            decision_path = _write_once(
                root / "derived/decisions.json",
                {
                    "schema_version": 1,
                    "program_id": PROGRAM_ID,
                    "cycle_index": cycle.index,
                    "phase": cycle.phase,
                    "scheduled_at": _utc_text(cycle.scheduled_at),
                    "decision_t0": _utc_text(t0),
                    "tokens": feature_rows,
                },
            )
            return _seal(
                program,
                cycle,
                stage="decisions_sealed",
                artifacts=(*evidence_paths, panel_path, decision_path),
                clock=clock,
            )
    except B2DesignError as exc:
        message = str(exc)
        if "fewer than 13 eligible" in message or "page is not terminal" in message:
            return _terminalize(program, cycle, f"data_unavailable:{exc}", clock=clock)
        return _program_fatal(program, cycle, f"data_contract:{exc}", clock=clock)
    except CohortFeatureError as exc:
        return _program_fatal(program, cycle, f"feature_contract:{exc}", clock=clock)
    except EvidenceCutoff as exc:
        return _terminalize(program, cycle, f"predecision_cutoff:{exc}", clock=clock)
    except (EvidenceError, ParallelBudgetError) as exc:
        return _program_fatal(program, cycle, str(exc), clock=clock)


def run_settlement(
    manifest_path: Path,
    cycle_index: int,
    *,
    transport: EvidenceTransport,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    program = require_stopped_v1_activation(manifest_path)
    if not 1 <= cycle_index <= len(SCHEDULE):
        raise ParallelStrategyRunnerError("cycle index is outside 1..85")
    cycle = SCHEDULE[cycle_index - 1]
    if _program_fatal_reason(program) is not None:
        raise ParallelStrategyRunnerError("program is globally fatal")
    root, state = _load_state(program, cycle)
    _validate_seals(program, root, state)
    if state["stage"] in _TERMINAL:
        return state
    if state["stage"] != "decisions_sealed":
        raise ParallelStrategyRunnerError("settlement requires sealed decisions")
    decisions = _read(root / "derived/decisions.json", label="cycle decisions")
    t0 = _parse_utc(decisions.get("decision_t0"), field="decision_t0")
    current = _utc(clock(), field="current time")
    admission = settlement_state(cycle, t0=t0, now=current)
    if admission == "wait":
        raise ParallelStrategyRunnerError("settlement is not mature")
    if admission == "missed":
        return _terminalize(program, cycle, "missed_settlement_window", clock=clock)
    allowed = lambda: settlement_state(cycle, t0=t0, now=clock()) == "settle"
    hard_stop = settlement_hard_stop(cycle)

    def require_before_hard_stop() -> None:
        if _utc(clock(), field="settlement hard-stop time") >= hard_stop:
            raise EvidenceCutoff("settlement absolute hard stop elapsed")

    try:
        with provider_lock(program):
            epoch_paths = _open_epoch(
                transport, cycle=cycle, epoch="settlement", allowed=allowed
            )
            outcomes: list[dict[str, Any]] = []
            evidence_paths: list[Path] = []
            windows = execution_windows(t0)
            for token in decisions["tokens"]:
                token_index = token["token_index"]
                selection = token["selection"]
                prefix = f"token-{token_index:02d}"
                token_evidence: list[EvidenceResult] = []
                pages_by_side: dict[str, list[dict[str, Any]]] = {}
                for side, start_key, end_key in (
                    ("BUY", "entry_start", "entry_end"),
                    ("SELL", "exit_start", "exit_end"),
                ):
                    pages: list[dict[str, Any]] = []
                    first = _call(
                        transport,
                        cycle=cycle,
                        epoch="settlement",
                        logical_id=f"{prefix}/dex-{side.lower()}-1",
                        endpoint="tgm/dex-trades",
                        payload=dex_payload(selection, side=side, start=windows[start_key], end=windows[end_key], page=1),
                        allowed=allowed,
                    )
                    require_before_hard_stop()
                    evidence_paths.extend(first.artifacts)
                    token_evidence.append(first)
                    pages.append(first.body)
                    if _needs_second_page(first.body, expected_page=1):
                        second = _call(
                            transport,
                            cycle=cycle,
                            epoch="settlement",
                            logical_id=f"{prefix}/dex-{side.lower()}-2",
                            endpoint="tgm/dex-trades",
                            payload=dex_payload(selection, side=side, start=windows[start_key], end=windows[end_key], page=2),
                            allowed=allowed,
                        )
                        require_before_hard_stop()
                        evidence_paths.extend(second.artifacts)
                        token_evidence.append(second)
                        _needs_second_page(second.body, expected_page=2)
                        pages.append(second.body)
                    pages_by_side[side] = pages
                candle = _call(
                    transport,
                    cycle=cycle,
                    epoch="settlement",
                    logical_id=f"{prefix}/ohlcv",
                    endpoint="tgm/token-ohlcv",
                    payload=ohlcv_payload(selection, start=windows["ohlcv_start"], end=windows["ohlcv_end"]),
                    allowed=allowed,
                )
                require_before_hard_stop()
                evidence_paths.extend(candle.artifacts)
                token_evidence.append(candle)
                built = build_counterfactual_outcome(
                        candidate=selection,
                        cycle=cycle,
                        t0=t0,
                        buy_pages=pages_by_side["BUY"],
                        sell_pages=pages_by_side["SELL"],
                        ohlcv_body=candle.body,
                        retrieved_at=candle.retrieved_at,
                    )
                outcomes.append(
                    _outcome_attestation(program, built, token_evidence)
                )
            require_before_hard_stop()
            outcome_path = _write_once(
                root / "derived/outcomes.json",
                {
                    "schema_version": 1,
                    "program_id": PROGRAM_ID,
                    "cycle_index": cycle.index,
                    "phase": cycle.phase,
                    "decision_t0": _utc_text(t0),
                    "tokens": outcomes,
                },
            )
            require_before_hard_stop()
            return _seal(
                program,
                cycle,
                stage="outcome_sealed",
                artifacts=(*epoch_paths, *evidence_paths, outcome_path),
                clock=clock,
            )
    except (B2DesignError, CohortExecutionError) as exc:
        return _program_fatal(program, cycle, f"outcome_contract:{exc}", clock=clock)
    except EvidenceCutoff as exc:
        return _terminalize(program, cycle, f"settlement_cutoff:{exc}", clock=clock)
    except (EvidenceError, ParallelBudgetError) as exc:
        return _program_fatal(program, cycle, str(exc), clock=clock)


def _replay_panel(
    program: ParallelStrategyProgram, cycle: ScheduledCycle, panel: Mapping[str, Any]
) -> None:
    body, _, _ = _load_result_reference(
        program,
        panel.get("screener_evidence", {}),
        expected_cycle=cycle,
        expected_epoch="predecision",
        expected_method="POST",
        expected_endpoint="token-screener",
        expected_payload=screener_payload(),
    )
    eligible = validate_screener(body)
    # The caller of this replay validates prior cycles chronologically.  Avoid
    # recursively replaying the entire prefix from each prior panel, which
    # would otherwise grow exponentially; exact prior seal chains are still
    # validated here and each prior panel is replayed once by the outer pass.
    identity_counts, chain_counts = _prior_counts(
        program, cycle, replay_prior=False
    )
    selected = select_cycle(
        eligible,
        cycle=cycle,
        prior_identity_counts=identity_counts,
        prior_chain_counts=chain_counts,
    )
    expected = {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "cycle_index": cycle.index,
        "phase": cycle.phase,
        "scheduled_at": _utc_text(cycle.scheduled_at),
        "screener_evidence": panel["screener_evidence"],
        "selected": list(selected),
    }
    if dict(panel) != expected:
        raise ParallelStrategyRunnerError("sealed panel does not replay from raw evidence")


def _replay_decisions(
    program: ParallelStrategyProgram,
    cycle: ScheduledCycle,
    decisions_document: Mapping[str, Any],
) -> None:
    expected_keys = {
        "schema_version",
        "program_id",
        "cycle_index",
        "phase",
        "scheduled_at",
        "decision_t0",
        "tokens",
    }
    if (
        set(decisions_document) != expected_keys
        or decisions_document.get("schema_version") != 1
        or decisions_document.get("program_id") != PROGRAM_ID
        or decisions_document.get("cycle_index") != cycle.index
        or decisions_document.get("phase") != cycle.phase
        or decisions_document.get("scheduled_at") != _utc_text(cycle.scheduled_at)
    ):
        raise ParallelStrategyRunnerError("decision document identity differs")
    sealed_t0 = _parse_utc(
        decisions_document.get("decision_t0"), field="decision t0"
    )
    decision_seal = _read(
        _cycle_root(program, cycle) / "seals/decisions_sealed.json",
        label="decision seal",
    )
    sealed_at = _parse_utc(
        decision_seal.get("recorded_at"), field="decision seal time"
    )
    if decision_t0(cycle, sealed_at) != sealed_t0 or not sealed_at < sealed_t0:
        raise ParallelStrategyRunnerError(
            "decision timing does not replay from its seal"
        )
    tokens = decisions_document.get("tokens")
    if not isinstance(tokens, list) or len(tokens) != TOKENS_PER_CYCLE:
        raise ParallelStrategyRunnerError("decision token denominator differs")
    panel = _read(
        _cycle_root(program, cycle) / "derived/panel.json",
        label="decision source panel",
    )
    selections = panel.get("selected")
    if not isinstance(selections, list) or len(selections) != TOKENS_PER_CYCLE:
        raise ParallelStrategyRunnerError("decision source panel denominator differs")
    crosswalk = load_parallel_strategy_contract(program.repo_root)
    for token_index, token in enumerate(tokens, start=1):
        if (
            not isinstance(token, Mapping)
            or set(token)
            != {"token_index", "selection", "raw_evidence", "features", "decisions"}
            or token.get("token_index") != token_index
            or token.get("selection") != selections[token_index - 1]
        ):
            raise ParallelStrategyRunnerError("decision token identity differs")
        selection = token.get("selection")
        raw = token.get("raw_evidence")
        if not isinstance(selection, Mapping) or not isinstance(raw, Mapping):
            raise ParallelStrategyRunnerError("decision replay evidence is absent")
        smart_body, _, _ = _load_result_reference(
            program,
            raw.get("smart_money", {}),
            expected_cycle=cycle,
            expected_epoch="predecision",
            expected_method="POST",
            expected_endpoint="tgm/flows",
            expected_payload=smart_money_payload(selection, cycle),
        )
        fi_body, fi_retrieved, _ = _load_result_reference(
            program,
            raw.get("flow_intelligence", {}),
            expected_cycle=cycle,
            expected_epoch="predecision",
            expected_method="POST",
            expected_endpoint="tgm/flow-intelligence",
            expected_payload=flow_intelligence_payload(selection, cycle),
        )

        def pages(name: str) -> list[dict[str, Any]]:
            references = raw.get(name)
            if not isinstance(references, list) or not 1 <= len(references) <= 2:
                raise ParallelStrategyRunnerError("WBS replay page set differs")
            side = "BUY" if name == "buy" else "SELL"
            return [
                _load_result_reference(
                    program,
                    reference,
                    expected_cycle=cycle,
                    expected_epoch="predecision",
                    expected_method="POST",
                    expected_endpoint="tgm/who-bought-sold",
                    expected_payload=breadth_payload(
                        selection, cycle, side, page
                    ),
                )[0]
                for page, reference in enumerate(references, start=1)
            ]

        features = {
            "buy": validate_wbs_evidence(
                pages("buy"), candidate=selection, cycle=cycle, side="BUY"
            ),
            "sell": validate_wbs_evidence(
                pages("sell"), candidate=selection, cycle=cycle, side="SELL"
            ),
            "flow_intelligence": validate_flow_intelligence(
                fi_body,
                candidate=selection,
                cycle=cycle,
                cache_hit=False,
                retrieved_at=fi_retrieved,
            ),
            "smart_money": validate_smart_money_evidence(
                smart_body,
                candidate=selection,
                cycle=cycle,
                source_id=f"{PROGRAM_ID}:{selection['event_id']}",
            ),
        }
        replayed_decisions = {
            candidate["candidate_id"]: candidate_decision(
                selection=selection,
                features=features,
                candidate=candidate,
                cycle=cycle,
                sealed_crosswalk=crosswalk,
            )
            for candidate in crosswalk["candidates"]
        }
        if token.get("features") != features or token.get("decisions") != replayed_decisions:
            raise ParallelStrategyRunnerError(
                "sealed decisions do not replay from raw evidence"
            )


def _unscorable_seal(
    root: Path, state: Mapping[str, Any]
) -> tuple[dict[str, Any], datetime, str]:
    if state.get("stage") != "unscorable":
        raise ParallelStrategyRunnerError("cycle is not unscorable")
    seal = _read(root / "seals/unscorable.json", label="unscorable cycle seal")
    reason = state.get("terminal_reason")
    if not isinstance(reason, str) or seal.get("terminal_reason") != reason:
        raise ParallelStrategyRunnerError("unscorable reason differs from its seal")
    recorded_at = _parse_utc(
        seal.get("recorded_at"), field="unscorable seal time"
    )
    return seal, recorded_at, reason


def _epoch_replay(
    program: ParallelStrategyProgram,
    *,
    cycle: ScheduledCycle,
    epoch: str,
    terminal_seal: Mapping[str, Any],
) -> tuple[Path, Any]:
    root = (
        program.root
        / "budget/parallel-strategy-v1/epochs"
        / f"c{cycle.index:03d}-{epoch}"
    )
    if root.is_symlink() or not root.is_dir():
        raise ParallelStrategyRunnerError(
            f"{epoch} unscorable claim has no budget epoch"
        )
    limits = (
        (PREDECISION_MAX_ATTEMPTS, PREDECISION_MAX_CREDITS)
        if epoch == "predecision"
        else (SETTLEMENT_MAX_ATTEMPTS, SETTLEMENT_MAX_CREDITS)
    )
    try:
        totals = BudgetGuard(root, max_calls=limits[0], max_credits=limits[1]).replay()
    except Exception as exc:
        raise ParallelStrategyRunnerError(
            f"{epoch} unscorable budget does not replay"
        ) from exc
    sealed = {
        reference.get("path"): reference.get("sha256")
        for reference in terminal_seal.get("artifacts", [])
        if isinstance(reference, Mapping)
    }
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ParallelStrategyRunnerError(
                f"{epoch} unscorable budget contains a symlink"
            )
        if not path.is_file():
            continue
        relative = path.absolute().relative_to(program.root.absolute()).as_posix()
        if sealed.get(relative) != _sha256(path):
            raise ParallelStrategyRunnerError(
                f"{epoch} unscorable budget is not fully sealed"
            )
    return root, totals


def _has_valid_cutoff_marker(
    epoch_root: Path,
    *,
    cycle: ScheduledCycle,
    epoch: str,
    entries: Sequence[Any],
) -> bool:
    found = False
    for entry in entries:
        marker = (
            epoch_root
            / "raw/nansen"
            / entry.reservation_id
            / f"attempt-{entry.attempt_count}-pretransport-cutoff.json"
        )
        if not marker.exists():
            continue
        expected = {
            "schema_version": 1,
            "kind": "parallel-strategy-pretransport-cutoff-v1",
            "cycle_index": cycle.index,
            "epoch": epoch,
            "reservation_id": entry.reservation_id,
            "request_sha256": entry.request_sha256,
        }
        if _read(marker, label=f"{epoch} cutoff marker") != expected:
            raise ParallelStrategyRunnerError(
                f"{epoch} cutoff marker does not replay"
            )
        found = True
    return found


def _replay_data_unavailable_reason(
    program: ParallelStrategyProgram,
    cycle: ScheduledCycle,
    *,
    terminal_seal: Mapping[str, Any],
    reason: str,
) -> None:
    _, totals = _epoch_replay(
        program,
        cycle=cycle,
        epoch="predecision",
        terminal_seal=terminal_seal,
    )
    accounts = [entry for entry in totals.entries if entry.endpoint == "account"]
    screeners = [
        entry for entry in totals.entries if entry.endpoint == "token-screener"
    ]
    if len(accounts) != 1 or len(screeners) != 1 or len(totals.entries) != 2:
        raise ParallelStrategyRunnerError(
            "data-unavailable claim has a different request prefix"
        )
    screener_reference = _reference_for_entry(
        program,
        cycle=cycle,
        epoch="predecision",
        entry=screeners[0],
    )
    body, _, _ = _load_result_reference(
        program,
        screener_reference,
        expected_cycle=cycle,
        expected_epoch="predecision",
        expected_method="POST",
        expected_endpoint="token-screener",
        expected_payload=screener_payload(),
    )
    _validate_complete_epoch_budget(
        program,
        cycle=cycle,
        epoch="predecision",
        references=(screener_reference,),
    )
    try:
        eligible = validate_screener(body)
        identity_counts, chain_counts = _prior_counts(
            program, cycle, replay_prior=False
        )
        select_cycle(
            eligible,
            cycle=cycle,
            prior_identity_counts=identity_counts,
            prior_chain_counts=chain_counts,
        )
    except B2DesignError as exc:
        message = str(exc)
        if not (
            "fewer than 13 eligible" in message
            or "page is not terminal" in message
        ):
            raise ParallelStrategyRunnerError(
                "data-unavailable claim is a fatal contract error"
            ) from exc
        expected_reason = f"data_unavailable:{message}"
        if reason != expected_reason:
            raise ParallelStrategyRunnerError(
                "data-unavailable reason does not replay from the screener"
            )
        return
    raise ParallelStrategyRunnerError(
        "data-unavailable reason does not replay from the screener"
    )


def _validate_unscorable_claim(
    program: ParallelStrategyProgram,
    cycle: ScheduledCycle,
    *,
    root: Path,
    state: Mapping[str, Any],
    retained_stages: set[Any],
) -> None:
    terminal_seal, recorded_at, reason = _unscorable_seal(root, state)
    has_panel = "panel_sealed" in retained_stages
    has_decisions = "decisions_sealed" in retained_stages
    predecision_root = (
        program.root
        / "budget/parallel-strategy-v1/epochs"
        / f"c{cycle.index:03d}-predecision"
    )
    settlement_root = (
        program.root
        / "budget/parallel-strategy-v1/epochs"
        / f"c{cycle.index:03d}-settlement"
    )

    if reason == "missed_start_window":
        if (
            has_panel
            or has_decisions
            or predecision_root.exists()
            or settlement_root.exists()
            or start_state(cycle, recorded_at) != "missed"
        ):
            raise ParallelStrategyRunnerError(
                "missed-start reason does not replay from timing and evidence"
            )
        return

    if reason.startswith("data_unavailable:"):
        if has_panel or has_decisions or settlement_root.exists():
            raise ParallelStrategyRunnerError(
                "data-unavailable reason has an impossible predecessor stage"
            )
        _replay_data_unavailable_reason(
            program,
            cycle,
            terminal_seal=terminal_seal,
            reason=reason,
        )
        return

    if reason == "predecision_cutoff_elapsed":
        if has_decisions or settlement_root.exists() or recorded_at < (
            cycle.scheduled_at + PREDECISION_TRANSPORT_CUTOFF
        ):
            raise ParallelStrategyRunnerError(
                "predecision-cutoff reason does not replay from timing"
            )
        _epoch_replay(
            program,
            cycle=cycle,
            epoch="predecision",
            terminal_seal=terminal_seal,
        )
        return

    if reason.startswith("predecision_cutoff:"):
        if has_decisions or settlement_root.exists():
            raise ParallelStrategyRunnerError(
                "predecision-cutoff reason has an impossible predecessor stage"
            )
        epoch_root, totals = _epoch_replay(
            program,
            cycle=cycle,
            epoch="predecision",
            terminal_seal=terminal_seal,
        )
        cutoff_marker = _has_valid_cutoff_marker(
            epoch_root,
            cycle=cycle,
            epoch="predecision",
            entries=totals.entries,
        )
        deadline_intent = root / "intents/decisions_sealed.json"
        deadline_elapsed = False
        if deadline_intent.exists():
            intent = _read(deadline_intent, label="decision cutoff intent")
            deadline_elapsed = (
                intent.get("schema_version") == 1
                and intent.get("stage") == "decisions_sealed"
                and intent.get("terminal_reason") is None
                and _parse_utc(
                    intent.get("recorded_at"), field="decision cutoff time"
                )
                > cycle.scheduled_at + DECISION_DEADLINE
            )
        if not (
            cutoff_marker
            or deadline_elapsed
            or recorded_at >= cycle.scheduled_at + PREDECISION_TRANSPORT_CUTOFF
        ):
            raise ParallelStrategyRunnerError(
                "predecision-cutoff reason lacks a frozen cutoff proof"
            )
        return

    if reason == "missed_settlement_window":
        if not has_decisions:
            raise ParallelStrategyRunnerError(
                "missed-settlement reason lacks sealed decisions"
            )
        decisions = _read(root / "derived/decisions.json", label="cycle decisions")
        t0 = _parse_utc(decisions.get("decision_t0"), field="decision_t0")
        if settlement_state(cycle, t0=t0, now=recorded_at) != "missed":
            raise ParallelStrategyRunnerError(
                "missed-settlement reason does not replay from timing"
            )
        if settlement_root.exists():
            raise ParallelStrategyRunnerError(
                "missed-settlement reason has an unexpected settlement epoch"
            )
        return

    if reason.startswith("settlement_cutoff:"):
        if not has_decisions:
            raise ParallelStrategyRunnerError(
                "settlement-cutoff reason lacks sealed decisions"
            )
        epoch_root, totals = _epoch_replay(
            program,
            cycle=cycle,
            epoch="settlement",
            terminal_seal=terminal_seal,
        )
        cutoff_marker = _has_valid_cutoff_marker(
            epoch_root,
            cycle=cycle,
            epoch="settlement",
            entries=totals.entries,
        )
        if not (
            cutoff_marker
            or recorded_at >= cycle.scheduled_at + SETTLEMENT_TRANSPORT_CUTOFF
        ):
            raise ParallelStrategyRunnerError(
                "settlement-cutoff reason lacks a frozen cutoff proof"
            )
        return

    if reason.startswith("program_fatal:"):
        fatal_reason = _program_fatal_reason(program)
        intent = _read(
            program.root / "intents/program-fatal.json",
            label="program fatal intent",
        )
        if (
            not (program.root / "seals/program-fatal.json").is_file()
            or intent.get("cycle_index") != cycle.index
            or reason != f"program_fatal:{fatal_reason}"
        ):
            raise ParallelStrategyRunnerError(
                "program-fatal reason does not replay from its global intent"
            )
        return

    raise ParallelStrategyRunnerError("unscorable reason is not frozen")


def check_cycle(program_or_manifest: ParallelStrategyProgram | Path, cycle_index: int) -> dict[str, Any]:
    program = (
        program_or_manifest
        if isinstance(program_or_manifest, ParallelStrategyProgram)
        else require_stopped_v1_activation(program_or_manifest)
    )
    if not 1 <= cycle_index <= len(SCHEDULE):
        raise ParallelStrategyRunnerError("cycle index is outside 1..85")
    cycle = SCHEDULE[cycle_index - 1]
    root, state = _load_state(program, cycle)
    _validate_seals(program, root, state)
    retained_stages = {
        reference.get("stage")
        for reference in state.get("seals", [])
        if isinstance(reference, Mapping)
    }
    if state["stage"] == "unscorable":
        _validate_unscorable_claim(
            program,
            cycle,
            root=root,
            state=state,
            retained_stages=retained_stages,
        )
    if state["stage"] == "outcome_sealed":
        decisions = _read(root / "derived/decisions.json", label="cycle decisions")
        outcomes = _read(root / "derived/outcomes.json", label="cycle outcomes")
        if (
            set(outcomes)
            != {
                "schema_version",
                "program_id",
                "cycle_index",
                "phase",
                "decision_t0",
                "tokens",
            }
            or outcomes.get("schema_version") != 1
            or outcomes.get("program_id") != PROGRAM_ID
            or outcomes.get("cycle_index") != cycle.index
            or outcomes.get("phase") != cycle.phase
            or outcomes.get("decision_t0") != decisions.get("decision_t0")
            or len(decisions.get("tokens", [])) != TOKENS_PER_CYCLE
            or not isinstance(outcomes.get("tokens"), list)
            or len(outcomes["tokens"]) != TOKENS_PER_CYCLE
        ):
            raise ParallelStrategyRunnerError("terminal cycle denominator differs")
        for token, outcome in zip(
            decisions["tokens"], outcomes["tokens"], strict=True
        ):
            if not isinstance(outcome, Mapping):
                raise ParallelStrategyRunnerError("terminal outcome is malformed")
            if outcome.get("t0") != decisions.get("decision_t0"):
                raise ParallelStrategyRunnerError(
                    "terminal outcome t0 differs from sealed decisions"
                )
            _replay_outcome(
                program,
                cycle=cycle,
                selection=token["selection"],
                outcome=outcome,
            )
    if "panel_sealed" in retained_stages:
        panel = _read(root / "derived/panel.json", label="cycle panel")
        _replay_panel(program, cycle, panel)
    if "decisions_sealed" in retained_stages:
        decisions = _read(root / "derived/decisions.json", label="cycle decisions")
        _replay_decisions(program, cycle, decisions)
        panel = _read(root / "derived/panel.json", label="cycle panel")
        predecision_references: list[Mapping[str, Any]] = [
            panel["screener_evidence"]
        ]
        for token in decisions["tokens"]:
            raw = token["raw_evidence"]
            predecision_references.extend(
                (raw["smart_money"], raw["flow_intelligence"])
            )
            predecision_references.extend(raw["buy"])
            predecision_references.extend(raw["sell"])
        _validate_complete_epoch_budget(
            program,
            cycle=cycle,
            epoch="predecision",
            references=predecision_references,
        )
    if state["stage"] == "outcome_sealed":
        outcomes = _read(root / "derived/outcomes.json", label="cycle outcomes")
        settlement_references = [
            reference
            for outcome in outcomes["tokens"]
            for reference in outcome["raw_evidence"]
        ]
        _validate_complete_epoch_budget(
            program,
            cycle=cycle,
            epoch="settlement",
            references=settlement_references,
        )
    return state


def _phase_records(program: ParallelStrategyProgram, phase: str) -> tuple[dict[int, str], list[dict[str, Any]]]:
    statuses: dict[int, str] = {}
    records: list[dict[str, Any]] = []
    for cycle in (item for item in SCHEDULE if item.phase == phase):
        state = check_cycle(program, cycle.index)
        if state["stage"] not in _TERMINAL:
            raise ParallelStrategyRunnerError("phase aggregation requires every cycle terminal")
        statuses[cycle.index] = "complete" if state["stage"] == "outcome_sealed" else "unscorable"
        root = _cycle_root(program, cycle)
        if state["stage"] == "outcome_sealed":
            decisions = _read(root / "derived/decisions.json", label="cycle decisions")
            outcomes = _read(root / "derived/outcomes.json", label="cycle outcomes")
            for token, outcome in zip(decisions["tokens"], outcomes["tokens"], strict=True):
                records.append(
                    {
                        "cycle_index": cycle.index,
                        "selection": token["selection"],
                        "decisions": token["decisions"],
                        "outcome": outcome,
                    }
                )
        else:
            retained_stages = {
                reference.get("stage")
                for reference in state.get("seals", [])
                if isinstance(reference, Mapping)
            }
            if "decisions_sealed" in retained_stages:
                decisions = _read(
                    root / "derived/decisions.json", label="retained cycle decisions"
                )
                for token in decisions["tokens"]:
                    records.append(
                        {
                            "cycle_index": cycle.index,
                            "selection": token["selection"],
                            "decisions": token["decisions"],
                            "outcome": {},
                        }
                    )
                continue
            if "panel_sealed" in retained_stages:
                panel = _read(
                    root / "derived/panel.json", label="retained cycle panel"
                )
                for selection in panel["selected"]:
                    records.append(
                        {
                            "cycle_index": cycle.index,
                            "selection": selection,
                            "decisions": {},
                            "outcome": {},
                        }
                    )
                continue
            for band in range(1, TOKENS_PER_CYCLE + 1):
                records.append(
                    {
                        "cycle_index": cycle.index,
                        "selection": {
                            "status": "unavailable",
                            "event_id": f"ps-c{cycle.index:03d}-b{band:02d}",
                            "cycle_index": cycle.index,
                            "phase": cycle.phase,
                            "rank_band": band,
                            "reason": state["terminal_reason"],
                        },
                        "decisions": {},
                        "outcome": {},
                    }
                )
    return statuses, records


def freeze_discovery(manifest_path: Path) -> Path:
    program = require_stopped_v1_activation(manifest_path)
    statuses, records = _phase_records(program, "discovery")
    seal = freeze_discovery_family(cycle_statuses=statuses, records=records)
    path = _write_once(program.root / "derived/discovery-family.json", seal)
    cycle_seals = [
        _cycle_root(program, cycle) / "seals" / f"{check_cycle(program, cycle.index)['stage']}.json"
        for cycle in SCHEDULE[:42]
    ]
    _program_seal(
        program,
        name="discovery-family",
        stage=str(seal["stage"]),
        artifacts=(*cycle_seals, path),
    )
    return path


def finalize_program(manifest_path: Path) -> Path:
    program = require_stopped_v1_activation(manifest_path)
    discovery_statuses, discovery_records = _phase_records(program, "discovery")
    family = _read(freeze_discovery(manifest_path), label="discovery family")
    if family.get("stage") == "unscorable":
        result = {
            "schema_version": 1,
            "program_id": PROGRAM_ID,
            "stage": "unscorable",
            "terminal_reason": family.get("terminal_reason"),
            "formal_family_ids": [],
            "validated_candidate_ids": [],
            "advance_candidate_id": None,
        }
        result_path = _write_once(program.root / "derived/final-result.json", result)
        _program_seal(
            program,
            name="final",
            stage="unscorable",
            artifacts=(program.root / "derived/discovery-family.json", result_path),
        )
        return result_path
    validation_statuses, validation_records = _phase_records(program, "validation")
    result = validation_result(
        discovery_cycle_statuses=discovery_statuses,
        discovery_records=discovery_records,
        validation_cycle_statuses=validation_statuses,
        validation_records=validation_records,
        family_seal=family,
    )
    analysis = phase_analysis(
        phase="validation",
        cycle_statuses=validation_statuses,
        records=validation_records,
    )
    result_path = _write_once(program.root / "derived/final-result.json", result)
    analysis_path = _write_once(program.root / "derived/validation-analysis.json", analysis)
    validation_cycle_seals = [
        _cycle_root(program, cycle) / "seals" / f"{check_cycle(program, cycle.index)['stage']}.json"
        for cycle in SCHEDULE[42:]
    ]
    _program_seal(
        program,
        name="final",
        stage=str(result["stage"]),
        artifacts=(
            program.root / "derived/discovery-family.json",
            *validation_cycle_seals,
            analysis_path,
            result_path,
        ),
    )
    return result_path


def _validate_program_fatal_chain(
    program: ParallelStrategyProgram,
    states: Mapping[int, Mapping[str, Any]],
) -> None:
    intent_path = program.root / "intents/program-fatal.json"
    seal_path = program.root / "seals/program-fatal.json"
    fatal_states = [
        state
        for state in states.values()
        if isinstance(state.get("terminal_reason"), str)
        and str(state["terminal_reason"]).startswith("program_fatal:")
    ]
    if not intent_path.exists() and not seal_path.exists():
        if fatal_states:
            raise ParallelStrategyRunnerError(
                "program-fatal cycle exists without a global intent"
            )
        return
    reason = _program_fatal_reason(program)
    if reason is None or not seal_path.is_file() or seal_path.is_symlink():
        raise ParallelStrategyRunnerError(
            "program fatal transition is not durably sealed"
        )
    intent = _read(intent_path, label="program fatal intent")
    cycle_index = intent.get("cycle_index")
    if type(cycle_index) is not int or not 1 <= cycle_index <= len(SCHEDULE):
        raise ParallelStrategyRunnerError("program fatal cycle is outside 1..85")
    fatal_state = states.get(cycle_index)
    if (
        fatal_state is None
        or fatal_state.get("stage") != "unscorable"
        or fatal_state.get("terminal_reason") != f"program_fatal:{reason}"
        or len(fatal_states) != 1
    ):
        raise ParallelStrategyRunnerError(
            "program fatal intent does not bind exactly one terminal cycle"
        )
    epochs_root = program.root / "budget/parallel-strategy-v1/epochs"
    for later in SCHEDULE[cycle_index:]:
        state = states[later.index]
        if (
            state.get("stage") != "planned"
            or state.get("terminal_reason") is not None
            or state.get("seals") != []
        ):
            raise ParallelStrategyRunnerError(
                "program activity exists after the global fatal cycle"
            )
        cycle_root = _cycle_root(program, later)
        extra_files = [
            path
            for path in cycle_root.rglob("*")
            if path.is_file() and path != cycle_root / "state.json"
        ]
        if extra_files:
            raise ParallelStrategyRunnerError(
                "cycle artifacts exist after the global fatal cycle"
            )
        if epochs_root.is_dir() and any(
            path.name.startswith(f"c{later.index:03d}-")
            for path in epochs_root.iterdir()
        ):
            raise ParallelStrategyRunnerError(
                "budget activity exists after the global fatal cycle"
            )


def check_program(manifest_path: Path) -> dict[str, Any]:
    program = require_stopped_v1_activation(manifest_path)
    stages = {stage: 0 for stage in _STAGES}
    states: dict[int, dict[str, Any]] = {}
    for cycle in SCHEDULE:
        state = check_cycle(program, cycle.index)
        states[cycle.index] = state
        stages[state["stage"]] += 1
    _validate_program_fatal_chain(program, states)
    result_path = program.root / "derived/final-result.json"
    family_path = program.root / "derived/discovery-family.json"
    if family_path.exists():
        family = _read(family_path, label="discovery family")
        discovery_statuses, discovery_records = _phase_records(
            program, "discovery"
        )
        expected_family = freeze_discovery_family(
            cycle_statuses=discovery_statuses,
            records=discovery_records,
        )
        if family != expected_family:
            raise ParallelStrategyRunnerError(
                "discovery family does not replay from cycle evidence"
            )
        _validate_program_seal(
            program, name="discovery-family", stage=str(family["stage"])
        )
    if result_path.exists():
        expected = finalize_program(manifest_path)
        if expected != result_path:
            raise ParallelStrategyRunnerError("final result replay path differs")
        result = _read(result_path, label="final result")
        _validate_program_seal(program, name="final", stage=str(result["stage"]))
    budget_reconciliation = (
        program.root
        / "budget/parallel-strategy-v1/operational-reconciliation.json"
    )
    activation_reconciliation = (
        program.root / "activation/operational-reconciliation.json"
    )
    epochs_root = program.root / "budget/parallel-strategy-v1/epochs"
    epochs_exist = epochs_root.exists() and any(epochs_root.iterdir())
    if activation_reconciliation.exists() or budget_reconciliation.exists() or epochs_exist:
        try:
            expected_reconciliation = canonical_json_bytes(
                operational_reconciliation(manifest_path)
            )
        except ParallelStrategyActivationError as exc:
            raise ParallelStrategyRunnerError(
                "operational reconciliation does not replay"
            ) from exc
        if not activation_reconciliation.exists():
            raise ParallelStrategyRunnerError(
                "budget evidence exists without activation reconciliation"
            )
        if activation_reconciliation.read_bytes() != expected_reconciliation:
            raise ParallelStrategyRunnerError(
                "activation reconciliation does not replay"
            )
        if budget_reconciliation.exists() or epochs_exist:
            if not budget_reconciliation.exists():
                raise ParallelStrategyRunnerError(
                    "program budget epochs exist without reconciliation"
                )
            if budget_reconciliation.read_bytes() != expected_reconciliation:
                raise ParallelStrategyRunnerError(
                    "budget reconciliation does not replay"
                )
    if budget_reconciliation.exists():
        reconciliation = _read(
            budget_reconciliation, label="budget operational reconciliation"
        )
        try:
            budget_totals = ParallelStrategyBudget(
                program.root, reconciliation
            ).summary()
        except ParallelBudgetError as exc:
            raise ParallelStrategyRunnerError(
                "program budget journals do not replay"
            ) from exc
    else:
        budget_totals = None
    return {
        "schema_version": 1,
        "program_id": PROGRAM_ID,
        "cycles": len(SCHEDULE),
        "stages": stages,
        "terminal_cycles": stages["outcome_sealed"] + stages["unscorable"],
        "finalized": result_path.exists(),
        "authenticated_attempts": 0 if budget_totals is None else budget_totals.attempts,
        "billable_credits": 0 if budget_totals is None else budget_totals.credits,
        "budget_halted_reason": (
            None if budget_totals is None else budget_totals.halted_reason
        ),
    }
