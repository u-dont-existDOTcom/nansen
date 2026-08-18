from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .artifacts import (
    atomic_replace_bytes,
    canonical_json_bytes,
    write_bytes_once_or_adopt_exact,
    write_json_once,
)
from .budget import BudgetError, BudgetGuard, canonical_request_sha256
from .client import NansenRequestFailure
from .cohort_aggregate import aggregate_rule
from .cohort_execution import (
    CohortExecutionError,
    build_entry_fill,
    build_exit_fill,
    dex_payload,
    execution_windows,
    ohlcv_payload,
    score_counterfactual,
    validate_trade_pages,
    validate_ohlcv,
)
from .cohort_features import (
    CohortFeatureError,
    build_predecision_features,
    flow_payload,
    h5_decision,
    validate_flow_body,
    validate_wbs_pages,
    wbs_payload,
)
from .cohort_schema import (
    COMPARATOR_PATH,
    CYCLE_COUNT,
    MAX_CYCLE_ATTEMPTS,
    MAX_CYCLE_CREDITS,
    MAX_PROGRAM_CREDITS,
    PRIMARY_RULE_ID,
    CohortProgram,
    CohortSchemaError,
    EXPECTED_CONTRACT_SHA256,
    EXPECTED_STRATEGY_SHA256,
    load_cohort_program,
    parse_utc,
    remaining_required_credits,
    utc_text,
    validate_runtime_implementation,
)
from .cohort_selection import (
    CohortSelectionError,
    SelectedCandidate,
    normalized_identity,
    screener_payload,
    select_cohort,
)
from .historical_discovery import _response_for, _verified_request_attempt_count
from .prospective_comparators import (
    ComparatorError,
    ComparatorDecision,
    evaluate_comparators,
    load_cohort_comparators,
    pair_distribution_veto,
)
from .prospective_runner import PilotError, _nansen_call


class CohortRunnerError(RuntimeError):
    """Raised when a prospective cohort cycle cannot progress safely."""


_STAGE_ORDER = (
    "planned",
    "universe_sealed",
    "features_sealed",
    "decisions_sealed",
    "outcome_sealed",
)
_TERMINAL = {"outcome_sealed", "unscorable"}
_H5 = "buyer-breadth-exchange-comovement-v1"
_H5_PAIRED = PRIMARY_RULE_ID
_DECISION_DEADLINE = timedelta(minutes=45)


def _cohort_pair_distribution_veto(
    decisions: Sequence[ComparatorDecision],
) -> tuple[ComparatorDecision, ...]:
    """Complete every non-veto base rule without changing schema-v4 semantics."""

    originals = tuple(decisions)
    combined = list(pair_distribution_veto(originals))
    vetoes = [
        item for item in originals if item.variant == "base" and item.role == "veto"
    ]
    if len(vetoes) != 1:
        raise ComparatorError("exactly one base veto decision is required")
    veto = vetoes[0]
    paired_theories = {
        item.theory_id for item in combined if item.variant == "distribution_veto"
    }
    for base in originals:
        if (
            base.variant != "base"
            or base.role == "veto"
            or base.theory_id in paired_theories
        ):
            continue
        if base.availability == "AVAILABLE" and base.action == "ABSTAIN":
            action = "ABSTAIN"
            availability = "AVAILABLE"
            reason = "paired strategy abstained because the base rule abstained"
        else:
            action = None
            availability = "UNAVAILABLE"
            reason = "paired base-rule outcome is unavailable"
        combined.append(
            ComparatorDecision(
                decision_id=f"{base.theory_id}::paired::{veto.theory_id}",
                theory_id=base.theory_id,
                role=base.role,
                variant="distribution_veto",
                action=action,
                availability=availability,
                applicable=base.applicable,
                veto_theory_id=veto.theory_id,
                veto_triggered=None,
                reasons=(reason,),
            )
        )
    ids = [item.decision_id for item in combined]
    if len(ids) != len(set(ids)):
        raise ComparatorError("cohort comparator decision IDs must be unique")
    return tuple(combined)


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise CohortRunnerError(f"artifact must be a regular non-symlink file: {path}")
    return _sha256_bytes(path.read_bytes())


def _assert_no_symlinks(root: Path, *, label: str) -> None:
    if root.is_symlink():
        raise CohortRunnerError(f"{label} cannot be a symlink")
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_symlink():
            raise CohortRunnerError(f"{label} cannot contain symlinks: {path}")


def _validate_program_container(program: CohortProgram) -> None:
    validate_runtime_implementation(program)
    _assert_no_symlinks(program.root, label="cohort program")
    expected_top = {"program.json", "contracts"}
    if (program.root / "cycles").exists():
        expected_top.add("cycles")
    if (program.root / "derived").exists():
        expected_top.add("derived")
    if {path.name for path in program.root.iterdir()} != expected_top:
        raise CohortRunnerError("cohort program contains unexpected top-level entries")
    contracts = program.root / "contracts"
    if {path.name for path in contracts.iterdir()} != {
        "nansen-openapi.json",
        "frozen-strategy-manifest.json",
        "frozen-comparator-definitions.json",
        "protocol-implementation.json",
        "implementation",
    }:
        raise CohortRunnerError("cohort program contracts directory differs")
    derived = program.root / "derived"
    if derived.exists():
        entries = list(derived.iterdir())
        if any(path.name != "aggregate.json" or not path.is_file() for path in entries):
            raise CohortRunnerError("cohort program derived directory differs")


def _cycle_file(root: Path, path: Path, *, label: str) -> Path:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise CohortRunnerError(f"{label} escapes the cycle root") from exc
    cursor = root
    if cursor.is_symlink():
        raise CohortRunnerError(f"{label} cannot traverse a symlink")
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise CohortRunnerError(f"{label} cannot traverse a symlink")
    if not cursor.is_file():
        raise CohortRunnerError(f"{label} must be a regular file")
    return cursor


def _clock_value(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CohortRunnerError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _cycle_schedule(program: CohortProgram, cycle_index: int) -> dict[str, Any]:
    if not isinstance(cycle_index, int) or isinstance(cycle_index, bool):
        raise CohortRunnerError("cycle index must be an integer")
    if not 1 <= cycle_index <= CYCLE_COUNT:
        raise CohortRunnerError("cycle index is outside the 32-cycle program")
    return dict(program.manifest["schedule"][cycle_index - 1])


def _cycle_root(program: CohortProgram, cycle_index: int) -> Path:
    schedule = _cycle_schedule(program, cycle_index)
    cycles = program.root / "cycles"
    if cycles.exists() and cycles.is_symlink():
        raise CohortRunnerError("program cycles directory cannot be a symlink")
    return cycles / schedule["cycle_id"]


def _initial_state(program: CohortProgram, cycle_index: int) -> dict[str, Any]:
    schedule = _cycle_schedule(program, cycle_index)
    return {
        "schema_version": 1,
        "program_id": program.program_id,
        "program_manifest_sha256": _sha256_file(program.manifest_path),
        "cycle_index": cycle_index,
        "cycle_id": schedule["cycle_id"],
        "scheduled_at": schedule["scheduled_at"],
        "stage": "planned",
        "terminal_reason": None,
        "seals": [],
    }


def initialize_cycle(program: CohortProgram, cycle_index: int) -> Path:
    root = _cycle_root(program, cycle_index)
    state_path = root / "state.json"
    if root.exists():
        _load_cycle(program, cycle_index)
        return state_path
    root.mkdir(parents=True)
    try:
        write_json_once(state_path, _initial_state(program, cycle_index))
        BudgetGuard(root, MAX_CYCLE_CREDITS, MAX_CYCLE_CREDITS)
    except BaseException:
        # No external action occurs during initialization. Remove only an empty
        # or just-created cycle skeleton; never delete response evidence.
        if not (root / "raw").exists():
            for path in sorted(root.rglob("*"), reverse=True):
                if path.is_file() and not path.is_symlink():
                    path.unlink()
                elif path.is_dir() and not path.is_symlink():
                    try:
                        path.rmdir()
                    except OSError:
                        pass
            try:
                root.rmdir()
            except OSError:
                pass
        raise
    return state_path


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise CohortRunnerError(f"{label} must be a regular non-symlink file")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise CohortRunnerError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise CohortRunnerError(f"{label} must be an object")
    return value


def _seal_path(root: Path, stage: str) -> Path:
    return root / "seals" / f"{stage}.json"


def _validate_seal(root: Path, seal_path: Path, *, expected_stage: str) -> dict[str, Any]:
    _cycle_file(root, seal_path, label=f"{expected_stage} seal")
    seal = _read_json(seal_path, label=f"{expected_stage} seal")
    if (
        set(seal)
        != {
            "schema_version", "program_id", "cycle_id", "stage", "recorded_at",
            "previous_seal_sha256", "budget_snapshot", "artifacts",
        }
        or seal.get("schema_version") != 1
        or seal.get("program_id") != root.parents[1].name
        or seal.get("cycle_id") != root.name
        or seal.get("stage") != expected_stage
        or not isinstance(seal.get("artifacts"), list)
    ):
        raise CohortRunnerError(f"{expected_stage} seal has invalid schema")
    previous = seal.get("previous_seal_sha256")
    if previous is not None and (
        not isinstance(previous, str)
        or len(previous) != 64
        or any(character not in "0123456789abcdef" for character in previous)
    ):
        raise CohortRunnerError(f"{expected_stage} previous seal hash is invalid")
    sealed_at = parse_utc(
        seal.get("recorded_at"), field=f"{expected_stage} seal recorded_at"
    )
    records = [seal["budget_snapshot"], *seal["artifacts"]]
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            raise CohortRunnerError(f"{expected_stage} seal artifact record is invalid")
        relative = record["path"]
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or Path(relative).as_posix() != relative
        ):
            raise CohortRunnerError(f"{expected_stage} seal artifact path is unsafe")
        path = root / relative
        _cycle_file(root, path, label=f"{expected_stage} sealed artifact")
        if _sha256_file(path) != record["sha256"]:
            raise CohortRunnerError(f"{expected_stage} sealed artifact hash differs")
        if (
            path.name.endswith("-request.json")
            or path.name.endswith("-response-metadata.json")
            or path.name == "account-baseline.json"
        ):
            timestamped = _read_json(
                path, label=f"{expected_stage} timestamped artifact"
            )
            written_at = parse_utc(
                timestamped.get("artifact_written_at"),
                field=f"{expected_stage} artifact_written_at",
            )
            if written_at > sealed_at:
                raise CohortRunnerError(
                    f"{expected_stage} seal predates a bound artifact"
                )
    return seal


def _load_cycle(program: CohortProgram, cycle_index: int) -> tuple[Path, dict[str, Any]]:
    root = _cycle_root(program, cycle_index)
    if root.is_symlink() or not root.is_dir():
        raise CohortRunnerError("cycle root must be a regular directory")
    _assert_no_symlinks(root, label="cycle tree")
    for name in ("budget", "raw", "derived", "seals"):
        candidate = root / name
        if candidate.exists() and candidate.is_symlink():
            raise CohortRunnerError(f"cycle {name} directory cannot be a symlink")
    state_path = root / "state.json"
    state = _read_json(state_path, label="cycle state")
    expected = _initial_state(program, cycle_index)
    fixed = {
        key: expected[key]
        for key in (
            "schema_version", "program_id", "program_manifest_sha256", "cycle_index",
            "cycle_id", "scheduled_at",
        )
    }
    if any(state.get(key) != value for key, value in fixed.items()):
        raise CohortRunnerError("cycle state identity differs from the program")
    if set(state) != set(expected) or state.get("stage") not in {*_STAGE_ORDER, "unscorable"}:
        raise CohortRunnerError("cycle state schema is invalid")
    if not isinstance(state.get("seals"), list):
        raise CohortRunnerError("cycle seal references must be a list")

    # Recover a crash between an immutable seal and the small mutable state
    # pointer. A seal is authoritative only after every bound hash verifies.
    recovered_stage = "planned"
    recovered_refs: list[dict[str, str]] = []
    present_normal: list[str] = []
    expected_previous: str | None = None
    for stage in (*_STAGE_ORDER[1:], "unscorable"):
        path = _seal_path(root, stage)
        if not path.exists():
            continue
        if stage == "unscorable" and any(
            _seal_path(root, terminal).exists() for terminal in ("outcome_sealed",)
        ):
            raise CohortRunnerError("cycle contains conflicting terminal seals")
        seal = _validate_seal(root, path, expected_stage=stage)
        if seal["previous_seal_sha256"] != expected_previous:
            raise CohortRunnerError("cycle seal hash chain is broken")
        if stage != "unscorable":
            present_normal.append(stage)
        expected_previous = _sha256_file(path)
        recovered_refs.append({
            "stage": stage,
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256_file(path),
        })
        recovered_stage = stage
    if present_normal and present_normal != list(_STAGE_ORDER[1 : 1 + len(present_normal)]):
        raise CohortRunnerError("cycle seals do not form a contiguous stage prefix")
    claimed_refs = state["seals"]
    if any(
        not isinstance(reference, dict)
        or set(reference) != {"stage", "path", "sha256"}
        for reference in claimed_refs
    ):
        raise CohortRunnerError("cycle state seal reference is invalid")
    claimed_stage = claimed_refs[-1]["stage"] if claimed_refs else "planned"
    if state["stage"] != claimed_stage:
        raise CohortRunnerError("cycle state claims a stage without its seal")
    if claimed_refs != recovered_refs[: len(claimed_refs)]:
        raise CohortRunnerError("cycle state seal prefix differs from immutable seals")
    if len(claimed_refs) > len(recovered_refs):
        raise CohortRunnerError("cycle state refers to a missing seal")
    if recovered_stage == "unscorable":
        intent = _read_json(
            root / "derived/unscorable-intent.json", label="unscorable intent"
        )
        if (
            set(intent) != {"schema_version", "reason"}
            or intent.get("schema_version") != 1
            or not isinstance(intent.get("reason"), str)
            or not intent["reason"]
        ):
            raise CohortRunnerError("unscorable intent is invalid")
        recovered_reason = intent["reason"]
    else:
        recovered_reason = None
    if len(claimed_refs) == len(recovered_refs) and state.get("terminal_reason") != recovered_reason:
        raise CohortRunnerError("cycle terminal reason differs from the sealed intent")
    if recovered_stage != state["stage"] or recovered_refs != claimed_refs:
        state["stage"] = recovered_stage
        state["seals"] = recovered_refs
        state["terminal_reason"] = recovered_reason
        atomic_replace_bytes(state_path, canonical_json_bytes(state))
    BudgetGuard(root, MAX_CYCLE_CREDITS, MAX_CYCLE_CREDITS).replay()
    return root, state


def _artifact_record(root: Path, path: Path) -> dict[str, str]:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise CohortRunnerError("artifact escapes the cycle root") from exc
    _cycle_file(root, path, label="cycle artifact")
    return {"path": relative, "sha256": _sha256_file(path)}


def _seal_stage(
    program: CohortProgram,
    cycle_index: int,
    *,
    stage: str,
    artifacts: Iterable[Path],
    recorded_at: datetime,
    terminal_reason: str | None = None,
) -> dict[str, Any]:
    root, state = _load_cycle(program, cycle_index)
    path = _seal_path(root, stage)
    if path.exists():
        _validate_seal(root, path, expected_stage=stage)
        return _load_cycle(program, cycle_index)[1]
    guard = BudgetGuard(root, MAX_CYCLE_CREDITS, MAX_CYCLE_CREDITS)
    orphan_snapshot = root / "budget" / "snapshots" / f"{stage}.json"
    if orphan_snapshot.exists():
        snapshot_document = _read_json(
            orphan_snapshot, label=f"{stage} orphan budget snapshot"
        )
        recorded = snapshot_document.get("recorded_at")
        parse_utc(recorded, field=f"{stage} orphan snapshot recorded_at")
    else:
        recorded = utc_text(recorded_at)
    snapshot = guard.snapshot(stage, recorded_at=recorded)
    unique = sorted({Path(item) for item in artifacts}, key=lambda item: item.as_posix())
    seal = {
        "schema_version": 1,
        "program_id": program.program_id,
        "cycle_id": state["cycle_id"],
        "stage": stage,
        "recorded_at": recorded,
        "previous_seal_sha256": (
            None if not state["seals"] else state["seals"][-1]["sha256"]
        ),
        "budget_snapshot": _artifact_record(root, snapshot),
        "artifacts": [_artifact_record(root, item) for item in unique],
    }
    write_bytes_once_or_adopt_exact(
        path,
        canonical_json_bytes(seal),
        metadata={"kind": "cohort_cycle_seal", "stage": stage},
    )
    reference = {
        "stage": stage,
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256_file(path),
    }
    state["stage"] = stage
    state["terminal_reason"] = terminal_reason
    state["seals"] = [*state["seals"], reference]
    atomic_replace_bytes(root / "state.json", canonical_json_bytes(state))
    return state


def _write_exact(path: Path, value: Any, *, kind: str) -> Path:
    try:
        return write_bytes_once_or_adopt_exact(
            path, canonical_json_bytes(value), metadata={"kind": kind}
        )
    except (FileExistsError, RuntimeError) as exc:
        raise CohortRunnerError(f"immutable {kind} artifact collided at {path}") from exc


def _prior_counts(program: CohortProgram, cycle_index: int) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for prior in range(1, cycle_index):
        prior_root = _cycle_root(program, prior)
        if not prior_root.exists():
            continue
        _, prior_state = _load_cycle(program, prior)
        if prior_state["stage"] not in _TERMINAL:
            raise CohortRunnerError("all earlier cycles must be terminal before selection")
        sealed_stages = {reference["stage"] for reference in prior_state["seals"]}
        if "universe_sealed" not in sealed_stages:
            continue
        panel_path = prior_root / "derived/panel.json"
        _cycle_file(prior_root, panel_path, label="prior sealed panel")
        panel = _read_json(panel_path, label="prior cycle panel")
        members = panel.get("members")
        if not isinstance(members, list):
            raise CohortRunnerError("prior cycle panel is invalid")
        for member in members:
            if not isinstance(member, dict):
                raise CohortRunnerError("prior cycle panel member is invalid")
            identity = normalized_identity(member.get("chain"), member.get("token_address"))
            counts[identity] = counts.get(identity, 0) + 1
    return counts


def _panel_document(
    program: CohortProgram,
    cycle_index: int,
    selected: Sequence[SelectedCandidate],
    *,
    response_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "program_id": program.program_id,
        "cycle_index": cycle_index,
        "selection_rule": "five-strata-outcome-blind-v1",
        "screener_response_sha256": _sha256_file(response_path),
        "members": [
            {
                **asdict(candidate),
                "selected_row_sha256": _sha256_bytes(
                    canonical_json_bytes(candidate.selected_row)
                ),
                "virtual_notional_usd": min(1000.0, 0.001 * candidate.liquidity_usd),
            }
            for candidate in selected
        ],
    }


def _candidate(member: dict[str, Any]) -> dict[str, Any]:
    return {
        "chain": member["chain"],
        "token_address": member["token_address"],
        "token_symbol": member["token_symbol"],
    }


def _token_week(candidate: dict[str, Any], decision_t0: datetime) -> dict[str, str]:
    chain, address = normalized_identity(
        candidate["chain"], candidate["token_address"]
    )
    iso_year, iso_week, _ = decision_t0.isocalendar()
    week = f"{iso_year:04d}-W{iso_week:02d}"
    return {
        "utc_week": week,
        "token_week_id": f"{chain}:{address}:{week}",
    }


def _second_page_required(body: Any, *, label: str) -> bool:
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("data"), list)
        or len(body["data"]) > 1000
        or not isinstance(body.get("pagination"), dict)
    ):
        raise CohortRunnerError(f"{label} response pagination is missing")
    pagination = body["pagination"]
    if (
        type(pagination.get("page")) is not int
        or pagination.get("page") != 1
        or type(pagination.get("per_page")) is not int
        or pagination.get("per_page") != 1000
        or not isinstance(pagination.get("is_last_page"), bool)
    ):
        raise CohortRunnerError(f"{label} response pagination is invalid")
    return not pagination["is_last_page"]


def _call(
    *,
    root: Path,
    guard: BudgetGuard,
    nansen: Any,
    logical_id: str,
    endpoint: str,
    payload: dict[str, Any] | None,
    expected: int,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    minimum_remaining: int = 0,
    not_after: datetime | None = None,
) -> tuple[Any, tuple[Path, ...], Any]:
    if not_after is not None and _clock_value(clock) > not_after:
        raise CohortRunnerError("predecision evidence deadline elapsed before provider access")
    response, paths = _nansen_call(
        root=root,
        guard=guard,
        nansen=nansen,
        logical_request_id=logical_id,
        method="GET" if endpoint == "account" else "POST",
        endpoint=endpoint,
        payload=payload,
        expected_credits=expected,
        clock=clock,
        sleep=sleep,
        account_baseline_version="account-baseline-v2" if endpoint == "account" else None,
        openapi_sha256=EXPECTED_CONTRACT_SHA256 if endpoint == "account" else None,
        account_minimum_remaining=minimum_remaining,
        allow_retry=False,
    )
    if not isinstance(response.body, dict):
        raise CohortRunnerError(f"{endpoint} response body must be an object")
    if endpoint == "account":
        body_remaining = response.body.get("credits_remaining")
        if (
            response.status_code != 200
            or response.body_parse_status != "json_object"
            or response.body.get("plan") not in {"free", "pro"}
            or isinstance(body_remaining, bool)
            or not isinstance(body_remaining, int)
            or body_remaining < minimum_remaining
            or response.credit_header_errors
            or response.credit_cost != 0
            or response.credit_used not in {None, 0}
            or response.credit_remaining not in {None, body_remaining}
        ):
            raise CohortRunnerError(
                "account response does not prove the full required funding"
            )
    return response.body, paths, response


def _match_live_contract(root: Path, nansen: Any) -> Path:
    path = root / "raw/contracts/nansen-openapi.json"
    if path.exists():
        _cycle_file(root, path, label="cycle OpenAPI contract")
        content = path.read_bytes()
    else:
        try:
            content = nansen.fetch_openapi()
        except Exception as exc:
            raise CohortRunnerError(f"cannot fetch the public Nansen contract: {exc}") from exc
        if not isinstance(content, bytes):
            raise CohortRunnerError("public Nansen contract response must be bytes")
        write_bytes_once_or_adopt_exact(
            path,
            content,
            metadata={"kind": "cohort_nansen_openapi"},
        )
    if _sha256_bytes(content) != EXPECTED_CONTRACT_SHA256:
        raise CohortRunnerError("public Nansen contract hash differs from the pinned bytes")
    return path


def _unscorable(
    program: CohortProgram,
    cycle_index: int,
    *,
    reason: str,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    root, state = _load_cycle(program, cycle_index)
    if state["stage"] in _TERMINAL:
        check_cycle(program, cycle_index)
        return state
    guard = BudgetGuard(root, MAX_CYCLE_CREDITS, MAX_CYCLE_CREDITS)
    for entry in guard.replay().entries:
        if entry.state != "reserved":
            continue
        request_path = (
            root
            / "raw/nansen"
            / entry.reservation_id
            / f"attempt-{entry.attempt_count}-request.json"
        )
        if entry.request_artifact_sha256 is None and not request_path.exists():
            guard.fail(
                entry,
                NansenRequestFailure(
                    "cycle terminalized before request transmission",
                    transmitted=False,
                ),
                failure_artifact_sha256=None,
            )
            continue
        if entry.request_artifact_sha256 is None:
            if request_path.is_symlink() or not request_path.is_file():
                raise CohortRunnerError("unbound request artifact is not a regular file")
            entry = guard.bind_request_artifact(entry, _sha256_file(request_path))
        guard.reconcile_inflight()
    intent_path = root / "derived/unscorable-intent.json"
    if intent_path.exists():
        intent = _read_json(intent_path, label="unscorable intent")
    else:
        intent = {"schema_version": 1, "reason": str(reason)}
        _write_exact(intent_path, intent, kind="cohort_unscorable_intent")
    artifacts = [
        path
        for directory in (root / "raw", root / "derived")
        if directory.exists()
        for path in directory.rglob("*")
        if path.is_file()
    ]
    return _seal_stage(
        program,
        cycle_index,
        stage="unscorable",
        artifacts=artifacts,
        recorded_at=_clock_value(clock),
        terminal_reason=str(intent["reason"]),
    )


def start_cycle(
    program: CohortProgram,
    cycle_index: int,
    *,
    nansen: Any,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    _validate_program_container(program)
    initialize_cycle(program, cycle_index)
    root, state = _load_cycle(program, cycle_index)
    if state["stage"] in _TERMINAL:
        check_cycle(program, cycle_index)
        return state
    if state["stage"] == "decisions_sealed":
        check_cycle(program, cycle_index)
        return state
    if state["stage"] != "planned":
        try:
            guard = BudgetGuard(root, MAX_CYCLE_CREDITS, MAX_CYCLE_CREDITS)
            _validate_archived_request_contracts(
                program, cycle_index, root, guard
            )
            _semantic_replay(program, cycle_index, root, state, guard)
        except Exception as exc:
            return _unscorable(
                program,
                cycle_index,
                reason=f"sealed-prefix replay failed before resume: {type(exc).__name__}: {exc}",
                clock=clock,
            )
    scheduled = parse_utc(state["scheduled_at"], field="scheduled_at")
    decision_deadline = scheduled + _DECISION_DEADLINE
    now = _clock_value(clock)
    if state["stage"] == "planned" and now < scheduled:
        raise CohortRunnerError(
            f"cycle collection cannot start before {utc_text(scheduled)}"
        )
    for prior in range(1, cycle_index):
        prior_root = _cycle_root(program, prior)
        if not prior_root.exists():
            return _unscorable(
                program,
                cycle_index,
                reason="an earlier scheduled cycle was never initialized",
                clock=clock,
            )
        _, prior_state = _load_cycle(program, prior)
        if prior_state["stage"] not in _TERMINAL:
            return _unscorable(
                program,
                cycle_index,
                reason="an earlier cycle is not terminal",
                clock=clock,
            )
        check_cycle(program, prior)
    if state["stage"] == "planned" and now > scheduled + timedelta(minutes=15):
        return _unscorable(
            program,
            cycle_index,
            reason="cycle collection did not start within the frozen 15-minute window",
            clock=clock,
        )
    if state["stage"] in {"planned", "universe_sealed", "features_sealed"} and now > decision_deadline:
        return _unscorable(
            program,
            cycle_index,
            reason="decision deadline elapsed before the decision seal",
            clock=clock,
        )
    guard = BudgetGuard(root, MAX_CYCLE_CREDITS, MAX_CYCLE_CREDITS)
    try:
        bundle = load_cohort_comparators(
            program.root / COMPARATOR_PATH,
            program.manifest["comparator_sha256"],
            expected_source_sha256=EXPECTED_STRATEGY_SHA256,
        )
        if state["stage"] == "planned":
            live_contract_path = _match_live_contract(root, nansen)
            account_body, account_paths, _ = _call(
                root=root,
                guard=guard,
                nansen=nansen,
                logical_id=f"cycle-{cycle_index:02d}/account",
                endpoint="account",
                payload=None,
                expected=0,
                clock=clock,
                sleep=sleep,
                minimum_remaining=remaining_required_credits(program, cycle_index),
                not_after=decision_deadline,
            )
            del account_body
            body, screener_paths, _ = _call(
                root=root,
                guard=guard,
                nansen=nansen,
                logical_id=f"cycle-{cycle_index:02d}/screener",
                endpoint="token-screener",
                payload=screener_payload(),
                expected=1,
                clock=clock,
                sleep=sleep,
                not_after=decision_deadline,
            )
            selected = select_cohort(body, _prior_counts(program, cycle_index))
            response_path = next(path for path in screener_paths if path.name.endswith("response.json"))
            panel_path = _write_exact(
                root / "derived/panel.json",
                _panel_document(
                    program, cycle_index, selected, response_path=response_path
                ),
                kind="cohort_panel",
            )
            state = _seal_stage(
                program,
                cycle_index,
                stage="universe_sealed",
                artifacts=(live_contract_path, *account_paths, *screener_paths, panel_path),
                recorded_at=_clock_value(clock),
            )
        panel = _read_json(root / "derived/panel.json", label="cohort panel")
        members = panel.get("members")
        if not isinstance(members, list) or len(members) != 5:
            raise CohortRunnerError("cohort panel must contain exactly five members")

        if state["stage"] == "universe_sealed":
            feature_documents: list[dict[str, Any]] = []
            feature_paths: list[Path] = []
            evidence_paths: list[Path] = []
            for token_index, member in enumerate(members, start=1):
                candidate = _candidate(member)
                prefix = f"cycle-{cycle_index:02d}/token-{token_index:02d}"
                smart_body, paths, _ = _call(
                    root=root, guard=guard, nansen=nansen,
                    logical_id=f"{prefix}/flow-smart-money", endpoint="tgm/flows",
                    payload=flow_payload(candidate, scheduled, "smart_money"), expected=1,
                    clock=clock, sleep=sleep,
                    not_after=decision_deadline,
                )
                evidence_paths.extend(paths)
                smart = validate_flow_body(
                    smart_body, candidate=candidate, label="smart_money", cutoff=scheduled
                )
                exchange_body, paths, _ = _call(
                    root=root, guard=guard, nansen=nansen,
                    logical_id=f"{prefix}/flow-exchange", endpoint="tgm/flows",
                    payload=flow_payload(candidate, scheduled, "exchange"), expected=1,
                    clock=clock, sleep=sleep,
                    not_after=decision_deadline,
                )
                evidence_paths.extend(paths)
                exchange = validate_flow_body(
                    exchange_body, candidate=candidate, label="exchange", cutoff=scheduled
                )
                breadth: dict[str, Any] = {}
                for side in ("BUY", "SELL"):
                    pages = []
                    page1, paths, _ = _call(
                        root=root, guard=guard, nansen=nansen,
                        logical_id=f"{prefix}/wbs-{side.lower()}-page-1",
                        endpoint="tgm/who-bought-sold",
                        payload=wbs_payload(candidate, scheduled, side, 1), expected=1,
                        clock=clock, sleep=sleep,
                        not_after=decision_deadline,
                    )
                    evidence_paths.extend(paths)
                    pages.append(page1)
                    if _second_page_required(page1, label=f"WBS {side}"):
                        page2, paths, _ = _call(
                            root=root, guard=guard, nansen=nansen,
                            logical_id=f"{prefix}/wbs-{side.lower()}-page-2",
                            endpoint="tgm/who-bought-sold",
                            payload=wbs_payload(candidate, scheduled, side, 2), expected=1,
                            clock=clock, sleep=sleep,
                            not_after=decision_deadline,
                        )
                        evidence_paths.extend(paths)
                        pages.append(page2)
                    breadth[side] = validate_wbs_pages(
                        pages, candidate=candidate, side=side
                    )
                features = build_predecision_features(
                    smart_money_rows=smart,
                    exchange_rows=exchange,
                    buyers=breadth["BUY"],
                    sellers=breadth["SELL"],
                    source_id=f"{program.program_id}:cycle-{cycle_index:02d}:token-{token_index:02d}",
                )
                document = {
                    "schema_version": 1,
                    "cycle_index": cycle_index,
                    "token_index": token_index,
                    "identity": candidate,
                    "features": features,
                }
                path = _write_exact(
                    root / f"derived/features/token-{token_index:02d}.json",
                    document,
                    kind="cohort_token_features",
                )
                feature_documents.append(document)
                feature_paths.append(path)
            features_path = _write_exact(
                root / "derived/features.json",
                {"schema_version": 1, "cycle_index": cycle_index, "tokens": feature_documents},
                kind="cohort_features",
            )
            state = _seal_stage(
                program,
                cycle_index,
                stage="features_sealed",
                artifacts=(*evidence_paths, *feature_paths, features_path),
                recorded_at=_clock_value(clock),
            )

        if state["stage"] == "features_sealed":
            features_document = _read_json(root / "derived/features.json", label="cycle features")
            decisions: list[dict[str, Any]] = []
            veto_triggered: bool | None = None
            for token in features_document["tokens"]:
                features = token["features"]
                comparators = _cohort_pair_distribution_veto(
                    evaluate_comparators(
                        bundle,
                        features["smart_money"]["final_feature"],
                        features["smart_money"]["prior_hour_feature"],
                        available_at=scheduled,
                    )
                )
                vetoes = [
                    item for item in comparators
                    if item.variant == "base" and item.role == "veto"
                ]
                if len(vetoes) != 1:
                    raise CohortRunnerError("frozen comparator bundle has no unique veto")
                h5 = h5_decision(features)
                if h5["availability"] != "AVAILABLE":
                    paired_action = None
                    paired_availability = "UNAVAILABLE"
                elif h5["action"] == "ABSTAIN":
                    paired_action = "ABSTAIN"
                    paired_availability = "AVAILABLE"
                elif vetoes[0].availability != "AVAILABLE" or vetoes[0].veto_triggered is None:
                    paired_action = None
                    paired_availability = "UNAVAILABLE"
                else:
                    paired_action = "ABSTAIN" if vetoes[0].veto_triggered else "LONG"
                    paired_availability = "AVAILABLE"
                decisions.append({
                    "token_index": token["token_index"],
                    "identity": token["identity"],
                    "h5": h5,
                    "h5_distribution_veto": {
                        "rule_id": _H5_PAIRED,
                        "action": paired_action,
                        "availability": paired_availability,
                        "base_action": h5["action"],
                        "veto_triggered": vetoes[0].veto_triggered,
                    },
                    "comparators": [asdict(item) for item in comparators],
                })
            clock_path = root / "derived/decision-clock.json"
            if clock_path.exists():
                clock_document = _read_json(clock_path, label="decision clock")
                t0 = parse_utc(clock_document["decision_t0"], field="decision_t0")
            else:
                observed = _clock_value(clock)
                floor = observed.replace(
                    minute=(observed.minute // 5) * 5, second=0, microsecond=0
                )
                t0 = floor + timedelta(minutes=5)
                clock_document = {
                    "schema_version": 1,
                    "computed_at": utc_text(observed),
                    "decision_t0": utc_text(t0),
                }
                _write_exact(clock_path, clock_document, kind="cohort_decision_clock")
            windows = execution_windows(t0)
            decision_path = _write_exact(
                root / "derived/decisions.json",
                {
                    "schema_version": 1,
                    "cycle_index": cycle_index,
                    "decision_t0": utc_text(t0),
                    "windows": {key: utc_text(value) for key, value in windows.items()},
                    "tokens": decisions,
                },
                kind="cohort_decisions",
            )
            decision_seal_time = _clock_value(clock)
            if decision_seal_time >= t0:
                raise CohortRunnerError(
                    "decision t0 became stale before the decision seal"
                )
            if decision_seal_time > decision_deadline:
                raise CohortRunnerError(
                    "decision deadline elapsed before the decision seal"
                )
            state = _seal_stage(
                program,
                cycle_index,
                stage="decisions_sealed",
                artifacts=(clock_path, decision_path),
                recorded_at=decision_seal_time,
            )
            decision_seal = _validate_seal(
                root,
                _seal_path(root, "decisions_sealed"),
                expected_stage="decisions_sealed",
            )
            if parse_utc(
                decision_seal["recorded_at"], field="decision seal recorded_at"
            ) >= t0:
                raise CohortRunnerError("decision seal was not recorded before t0")
        return state
    except CohortSelectionError as exc:
        return _unscorable(
            program,
            cycle_index,
            reason=exc.reason_code or f"{type(exc).__name__}: {exc}",
            clock=clock,
        )
    except (CohortRunnerError, CohortSchemaError,
            CohortFeatureError, CohortExecutionError, BudgetError, PilotError,
            ComparatorError, ValueError, KeyError, TypeError) as exc:
        return _unscorable(
            program, cycle_index, reason=f"{type(exc).__name__}: {exc}", clock=clock
        )


def settle_cycle(
    program: CohortProgram,
    cycle_index: int,
    *,
    nansen: Any,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    _validate_program_container(program)
    root, state = _load_cycle(program, cycle_index)
    if state["stage"] in _TERMINAL:
        check_cycle(program, cycle_index)
        return state
    if state["stage"] != "decisions_sealed":
        raise CohortRunnerError("cycle must have sealed decisions before settlement")
    guard = BudgetGuard(root, MAX_CYCLE_CREDITS, MAX_CYCLE_CREDITS)
    try:
        _validate_archived_request_contracts(program, cycle_index, root, guard)
        _semantic_replay(program, cycle_index, root, state, guard)
    except Exception as exc:
        return _unscorable(
            program,
            cycle_index,
            reason=f"sealed-prefix replay failed before settlement: {type(exc).__name__}: {exc}",
            clock=clock,
        )
    decisions = _read_json(root / "derived/decisions.json", label="cycle decisions")
    windows = {
        key: parse_utc(value, field=f"window {key}")
        for key, value in decisions["windows"].items()
    }
    if _clock_value(clock) < windows["earliest_settlement"]:
        raise CohortRunnerError(
            f"settlement is too early; wait until {utc_text(windows['earliest_settlement'])}"
        )
    panel = _read_json(root / "derived/panel.json", label="cohort panel")
    try:
        outcomes: list[dict[str, Any]] = []
        evidence_paths: list[Path] = []
        outcome_paths: list[Path] = []
        for token_index, member in enumerate(panel["members"], start=1):
            candidate = _candidate(member)
            prefix = f"cycle-{cycle_index:02d}/token-{token_index:02d}"
            def collect_dex(
                side: str, start_key: str, end_key: str
            ) -> list[dict[str, Any]]:
                pages: list[dict[str, Any]] = []
                first, paths, _ = _call(
                    root=root, guard=guard, nansen=nansen,
                    logical_id=f"{prefix}/dex-{side.lower()}-page-1",
                    endpoint="tgm/dex-trades",
                    payload=dex_payload(
                        candidate, side=side, start=windows[start_key],
                        end=windows[end_key], page=1
                    ),
                    expected=1, clock=clock, sleep=sleep,
                )
                evidence_paths.extend(paths)
                pages.append(first)
                if _second_page_required(first, label=f"DEX {side}"):
                    second, paths, _ = _call(
                        root=root, guard=guard, nansen=nansen,
                        logical_id=f"{prefix}/dex-{side.lower()}-page-2",
                        endpoint="tgm/dex-trades",
                        payload=dex_payload(
                            candidate, side=side, start=windows[start_key],
                            end=windows[end_key], page=2
                        ),
                        expected=1, clock=clock, sleep=sleep,
                    )
                    evidence_paths.extend(paths)
                    pages.append(second)
                validate_trade_pages(
                    pages,
                    candidate=candidate,
                    side=side,
                    start=windows[start_key],
                    end=windows[end_key],
                )
                return pages

            buy_pages = collect_dex("BUY", "entry_start", "entry_end")
            entry = build_entry_fill(
                buy_pages, candidate=candidate,
                notional_usd=member["virtual_notional_usd"],
                start=windows["entry_start"], end=windows["entry_end"],
            )
            sell_pages = collect_dex("SELL", "exit_start", "exit_end")
            exit_fill = None
            if entry.filled_token_amount > 0:
                exit_fill = build_exit_fill(
                    sell_pages, candidate=candidate,
                    token_amount=entry.filled_token_amount,
                    start=windows["exit_start"], end=windows["exit_end"],
                )
            body, paths, response = _call(
                root=root, guard=guard, nansen=nansen,
                logical_id=f"{prefix}/ohlcv", endpoint="tgm/token-ohlcv",
                payload=ohlcv_payload(
                    candidate, start=windows["ohlcv_start"], end=windows["ohlcv_end"]
                ),
                expected=1, clock=clock, sleep=sleep,
            )
            evidence_paths.extend(paths)
            candles = validate_ohlcv(
                body, candidate=candidate, start=windows["ohlcv_start"],
                end=windows["ohlcv_end"],
                retrieved_at=parse_utc(
                    response.response_retrieved_at, field="OHLCV response retrieval"
                ),
            )
            outcome = {
                "token_index": token_index,
                "identity": candidate,
                **_token_week(candidate, windows["ohlcv_start"]),
                "outcome": score_counterfactual(
                    entry_fill=entry,
                    exit_fill=exit_fill,
                    ohlcv=candles,
                    notional_usd=member["virtual_notional_usd"],
                ),
            }
            path = _write_exact(
                root / f"derived/outcomes/token-{token_index:02d}.json",
                outcome,
                kind="cohort_token_outcome",
            )
            outcomes.append(outcome)
            outcome_paths.append(path)
        outcomes_path = _write_exact(
            root / "derived/outcomes.json",
            {"schema_version": 1, "cycle_index": cycle_index, "tokens": outcomes},
            kind="cohort_outcomes",
        )
        return _seal_stage(
            program,
            cycle_index,
            stage="outcome_sealed",
            artifacts=(*evidence_paths, *outcome_paths, outcomes_path),
            recorded_at=_clock_value(clock),
        )
    except (CohortRunnerError, CohortSchemaError, CohortExecutionError,
            BudgetError, PilotError, ValueError, KeyError, TypeError) as exc:
        return _unscorable(
            program, cycle_index, reason=f"{type(exc).__name__}: {exc}", clock=clock
        )


def _archived_body(root: Path, guard: BudgetGuard, logical_id: str) -> dict[str, Any]:
    try:
        response = _response_for(root, guard, logical_id)
    except Exception as exc:
        raise CohortRunnerError(f"cannot load archived response {logical_id}: {exc}") from exc
    if not isinstance(response.body, dict):
        raise CohortRunnerError(f"archived response {logical_id} must be an object")
    return response.body


def _archived_response(root: Path, guard: BudgetGuard, logical_id: str) -> Any:
    try:
        return _response_for(root, guard, logical_id)
    except Exception as exc:
        raise CohortRunnerError(f"cannot load archived response {logical_id}: {exc}") from exc


def _assert_request_artifact(
    root: Path,
    guard: BudgetGuard,
    logical_id: str,
    *,
    method: str,
    endpoint: str,
    payload: dict[str, Any] | None,
    expected_credits: int,
) -> None:
    entry = next(
        (item for item in guard.replay().entries if item.logical_request_id == logical_id),
        None,
    )
    if entry is None:
        raise CohortRunnerError(f"missing archived request contract for {logical_id}")
    path = (
        root
        / "raw/nansen"
        / entry.reservation_id
        / f"attempt-{entry.attempt_count}-request.json"
    )
    unbound_preexisting = False
    if entry.request_artifact_sha256 is None:
        response_path = path.with_name(
            f"attempt-{entry.attempt_count}-response.json"
        )
        metadata_path = path.with_name(
            f"attempt-{entry.attempt_count}-response-metadata.json"
        )
        if (
            entry.state in {"reserved", "failed_before_pricing"}
            and not path.exists()
            and not response_path.exists()
            and not metadata_path.exists()
            and entry.endpoint == endpoint
            and entry.expected_credits == expected_credits
            and entry.request_sha256
            == canonical_request_sha256(method, endpoint, payload)
        ):
            return
        if entry.state == "reserved" and path.is_file() and not path.is_symlink():
            unbound_preexisting = True
        else:
            raise CohortRunnerError(
                f"request contract for {logical_id} is unbound after possible transmission"
            )
    request = _read_json(path, label=f"request artifact {logical_id}")
    if not unbound_preexisting and _sha256_file(path) != entry.request_artifact_sha256:
        raise CohortRunnerError(f"request artifact hash differs for {logical_id}")
    if (
        request.get("method") != method
        or request.get("endpoint") != endpoint
        or canonical_json_bytes(request.get("payload")) != canonical_json_bytes(payload)
        or request.get("caller_request_id") != logical_id
        or entry.endpoint != endpoint
        or entry.expected_credits != expected_credits
        or entry.request_sha256 != canonical_request_sha256(method, endpoint, payload)
    ):
        raise CohortRunnerError(f"archived request contract differs for {logical_id}")
    request_started = parse_utc(
        request.get("request_started_at"), field="request artifact start"
    )
    request_written = parse_utc(
        request.get("artifact_written_at"), field="request artifact write"
    )
    if request.get("transmission_may_begin") is not True or request_started > request_written:
        raise CohortRunnerError(f"request artifact timing is invalid for {logical_id}")
    metadata_path = path.with_name(
        f"attempt-{entry.attempt_count}-response-metadata.json"
    )
    response_path = path.with_name(
        f"attempt-{entry.attempt_count}-response.json"
    )
    if metadata_path.exists():
        metadata = _read_json(metadata_path, label=f"response metadata {logical_id}")
        if canonical_json_bytes(metadata) != metadata_path.read_bytes():
            raise CohortRunnerError(f"response metadata is not canonical for {logical_id}")
        if (
            entry.response_artifact_sha256 is not None
            and _sha256_file(metadata_path) != entry.response_artifact_sha256
        ):
            raise CohortRunnerError(f"response metadata hash differs for {logical_id}")
        if metadata.get("response_file") != response_path.name:
            raise CohortRunnerError(f"response metadata filename differs for {logical_id}")
        if response_path.exists():
            if _sha256_file(response_path) != metadata.get("response_sha256"):
                raise CohortRunnerError(f"response body hash differs for {logical_id}")
        elif entry.response_artifact_sha256 is not None:
            raise CohortRunnerError(f"bound response body is missing for {logical_id}")
        response_started = parse_utc(
            metadata.get("request_started_at"), field="response request start"
        )
        response_retrieved = parse_utc(
            metadata.get("response_retrieved_at"), field="response retrieval"
        )
        metadata_written = parse_utc(
            metadata.get("artifact_written_at"), field="response metadata write"
        )
        if not (
            request_started
            <= request_written
            <= response_started
            <= response_retrieved
            <= metadata_written
        ):
            raise CohortRunnerError(
                f"request/response evidence timing is reversed for {logical_id}"
            )
    elif response_path.exists() or entry.response_artifact_sha256 is not None:
        raise CohortRunnerError(f"response evidence is incomplete for {logical_id}")


def _validate_archived_request_contracts(
    program: CohortProgram,
    cycle_index: int,
    root: Path,
    guard: BudgetGuard,
    *,
    allow_account_failure: bool = False,
) -> None:
    entries = guard.replay().entries
    if not entries:
        return
    prefix = f"cycle-{cycle_index:02d}/"
    scheduled = parse_utc(
        _cycle_schedule(program, cycle_index)["scheduled_at"], field="scheduled_at"
    )
    panel_path = root / "derived/panel.json"
    members = None
    has_token_requests = any("/token-" in entry.logical_request_id for entry in entries)
    if panel_path.exists() and has_token_requests:
        panel = _read_json(panel_path, label="cohort panel")
        members = panel.get("members")
        if not isinstance(members, list) or len(members) != 5:
            raise CohortRunnerError("cohort panel is unavailable for request verification")
    decisions_path = root / "derived/decisions.json"
    windows = None
    if decisions_path.exists():
        decisions = _read_json(decisions_path, label="cycle decisions")
        if not isinstance(decisions.get("windows"), dict):
            raise CohortRunnerError("decision windows are unavailable for request verification")
        windows = {
            key: parse_utc(value, field=f"window {key}")
            for key, value in decisions["windows"].items()
        }

    logical_ids = {entry.logical_request_id for entry in entries}
    token_pattern = re.compile(
        rf"^{re.escape(prefix)}token-(0[1-5])/(flow-smart-money|flow-exchange|"
        r"wbs-buy-page-[12]|wbs-sell-page-[12]|dex-buy-page-[12]|"
        r"dex-sell-page-[12]|ohlcv)$"
    )
    for entry in entries:
        logical_id = entry.logical_request_id
        if logical_id == prefix + "account":
            expected = ("GET", "account", None)
        elif logical_id == prefix + "screener":
            expected = ("POST", "token-screener", screener_payload())
        else:
            match = token_pattern.fullmatch(logical_id)
            if match is None or members is None:
                raise CohortRunnerError(f"unexpected cohort request identity: {logical_id}")
            token_index = int(match.group(1))
            kind = match.group(2)
            candidate = _candidate(members[token_index - 1])
            if kind == "flow-smart-money":
                expected = ("POST", "tgm/flows", flow_payload(candidate, scheduled, "smart_money"))
            elif kind == "flow-exchange":
                expected = ("POST", "tgm/flows", flow_payload(candidate, scheduled, "exchange"))
            elif kind.startswith("wbs-"):
                _, side, _, page_text = kind.split("-")
                expected = (
                    "POST",
                    "tgm/who-bought-sold",
                    wbs_payload(candidate, scheduled, side.upper(), int(page_text)),
                )
            else:
                if windows is None:
                    raise CohortRunnerError("outcome request exists without sealed decision windows")
                if kind == "ohlcv":
                    expected = (
                        "POST",
                        "tgm/token-ohlcv",
                        ohlcv_payload(
                            candidate,
                            start=windows["ohlcv_start"],
                            end=windows["ohlcv_end"],
                        ),
                    )
                else:
                    _, side, _, page_text = kind.split("-")
                    start_key = "entry_start" if side == "buy" else "exit_start"
                    end_key = "entry_end" if side == "buy" else "exit_end"
                    expected = (
                        "POST",
                        "tgm/dex-trades",
                        dex_payload(
                            candidate,
                            side=side.upper(),
                            start=windows[start_key],
                            end=windows[end_key],
                            page=int(page_text),
                        ),
                    )
        _assert_request_artifact(
            root,
            guard,
            logical_id,
            method=expected[0],
            endpoint=expected[1],
            payload=expected[2],
            expected_credits=0 if logical_id == prefix + "account" else 1,
        )

    account_id = prefix + "account"
    if account_id in logical_ids:
        account_entry = next(
            item for item in entries if item.logical_request_id == account_id
        )
        if account_entry.state == "confirmed_zero":
            account = _archived_response(root, guard, account_id)
            remaining = account.body.get("credits_remaining")
            account_valid = not (
                account.status_code != 200
                or account.body_parse_status != "json_object"
                or account.body.get("plan") not in {"free", "pro"}
                or account.credit_header_errors
                or account.credit_cost != 0
                or account.credit_used not in {None, 0}
                or isinstance(remaining, bool)
                or not isinstance(remaining, int)
                or remaining < remaining_required_credits(program, cycle_index)
                or account.credit_remaining not in {None, remaining}
                or account_entry.credit_cost != 0
                or account_entry.credit_used != 0
                or account_entry.credit_remaining != remaining
            )
            if not account_valid and not allow_account_failure:
                raise CohortRunnerError("archived account preflight does not prove funding")
            if not account_valid:
                if logical_ids != {account_id}:
                    raise CohortRunnerError(
                        "paid requests followed an invalid account preflight"
                    )
                return
            derivation_path = root / "derived/account-baseline.json"
            fallback = account.credit_used is None or account.credit_remaining is None
            if fallback:
                derivation = _read_json(
                    derivation_path, label="account baseline derivation"
                )
                metadata_relative = (
                    Path("raw/nansen")
                    / account_entry.reservation_id
                    / f"attempt-{account_entry.attempt_count}-response-metadata.json"
                ).as_posix()
                if (
                    set(derivation)
                    != {
                        "schema_version", "rule_version", "openapi_sha256",
                        "response_metadata_path", "response_metadata_sha256",
                        "body", "observed", "effective", "artifact_written_at",
                    }
                    or derivation.get("schema_version") != 1
                    or derivation.get("rule_version") != "account-baseline-v2"
                    or derivation.get("openapi_sha256") != EXPECTED_CONTRACT_SHA256
                    or derivation.get("response_metadata_path") != metadata_relative
                    or derivation.get("response_metadata_sha256")
                    != account_entry.response_artifact_sha256
                    or derivation.get("body")
                    != {"plan": account.body["plan"], "credits_remaining": remaining}
                    or derivation.get("observed")
                    != {
                        "credit_cost": account.credit_cost,
                        "credit_used": account.credit_used,
                        "credit_remaining": account.credit_remaining,
                    }
                    or derivation.get("effective")
                    != {
                        "credit_cost": 0,
                        "credit_used": 0,
                        "credit_remaining": remaining,
                    }
                ):
                    raise CohortRunnerError("account baseline derivation does not replay")
                parse_utc(
                    derivation.get("artifact_written_at"),
                    field="account baseline artifact_written_at",
                )
            elif derivation_path.exists():
                raise CohortRunnerError("account baseline derivation was not required")

    for logical_id in sorted(logical_ids):
        if not logical_id.endswith("page-2"):
            continue
        page1_id = logical_id[:-1] + "1"
        if page1_id not in logical_ids:
            raise CohortRunnerError("second-page request has no first-page request")
        page1_entry = next(item for item in entries if item.logical_request_id == page1_id)
        if page1_entry.state in {"confirmed_zero", "confirmed_used"} and not _second_page_required(
            _archived_body(root, guard, page1_id), label=page1_id
        ):
            raise CohortRunnerError("unnecessary second-page request was archived")


def _validate_budget_archive(
    root: Path,
    state: dict[str, Any],
    totals: Any,
) -> None:
    budget_root = root / "budget"
    journal_root = budget_root / "journal"
    expected_files = {budget_root / "head.json"}
    transition_documents: list[dict[str, Any]] = []
    for sequence, digest in enumerate(totals.transition_sha256s, start=1):
        path = journal_root / f"{sequence:06d}-{digest}.json"
        expected_files.add(path)
        transition_documents.append(_read_json(path, label="budget transition"))

    expected_snapshot_paths: set[Path] = set()
    for reference in state["seals"]:
        seal = _validate_seal(
            root,
            root / reference["path"],
            expected_stage=reference["stage"],
        )
        snapshot_record = seal["budget_snapshot"]
        expected_relative = f"budget/snapshots/{reference['stage']}.json"
        if snapshot_record["path"] != expected_relative:
            raise CohortRunnerError("seal budget snapshot path differs from its stage")
        snapshot_path = root / expected_relative
        expected_snapshot_paths.add(snapshot_path)
        snapshot = _read_json(snapshot_path, label="budget snapshot")
        if (
            set(snapshot)
            != {
                "schema_version", "stage", "recorded_at", "totals",
                "provider_remaining", "journal_head_sha256",
                "transition_sha256s", "halted_reason",
            }
            or snapshot.get("schema_version") != 1
            or snapshot.get("stage") != reference["stage"]
            or snapshot.get("recorded_at") != seal["recorded_at"]
            or not isinstance(snapshot.get("transition_sha256s"), list)
        ):
            raise CohortRunnerError("budget snapshot schema or identity differs")
        prefix_hashes = snapshot["transition_sha256s"]
        if prefix_hashes != list(totals.transition_sha256s[: len(prefix_hashes)]):
            raise CohortRunnerError("budget snapshot is not a verified journal prefix")
        prefix = transition_documents[: len(prefix_hashes)]
        latest: dict[str, dict[str, Any]] = {}
        provider_remaining = None
        halted_reason = None
        for transition in prefix:
            entry = transition["entry"]
            latest[entry["logical_request_id"]] = entry
            provider_remaining = transition["provider_remaining"]
            halted_reason = transition["halted_reason"]
        counted = {"reserved", "retryable_zero", "confirmed_used", "ambiguous"}
        calls = sum(entry["state"] in counted for entry in latest.values())
        credits = sum(
            (entry["credit_used"] or 0)
            if entry["state"] == "confirmed_used"
            else entry["expected_credits"]
            for entry in latest.values()
            if entry["state"] in counted
        )
        expected_head = None if not prefix_hashes else prefix_hashes[-1]
        if (
            snapshot.get("totals") != {"calls": calls, "credits": credits}
            or snapshot.get("provider_remaining") != provider_remaining
            or snapshot.get("halted_reason") != halted_reason
            or snapshot.get("journal_head_sha256") != expected_head
        ):
            raise CohortRunnerError("budget snapshot totals do not match journal replay")
    expected_files.update(expected_snapshot_paths)

    actual_files = {
        path
        for path in budget_root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual_files != expected_files:
        raise CohortRunnerError("budget archive contains missing or unexpected files")
    expected_dirs = {budget_root, journal_root}
    if expected_snapshot_paths:
        expected_dirs.add(budget_root / "snapshots")
    actual_dirs = {budget_root, *(path for path in budget_root.rglob("*") if path.is_dir())}
    if actual_dirs != expected_dirs:
        raise CohortRunnerError("budget archive contains unexpected directories")


def _semantic_replay(
    program: CohortProgram,
    cycle_index: int,
    root: Path,
    state: dict[str, Any],
    guard: BudgetGuard,
) -> None:
    semantic_stage = state["stage"]
    if semantic_stage == "unscorable":
        prior_stages = [
            reference["stage"]
            for reference in state["seals"]
            if reference["stage"] != "unscorable"
        ]
        semantic_stage = prior_stages[-1] if prior_stages else "planned"
    if semantic_stage == "planned":
        return
    scheduled = parse_utc(state["scheduled_at"], field="scheduled_at")
    panel_path = root / "derived/panel.json"
    panel = _read_json(panel_path, label="cohort panel")
    screener_id = f"cycle-{cycle_index:02d}/screener"
    screener = _archived_response(root, guard, screener_id)
    selected = select_cohort(screener.body, _prior_counts(program, cycle_index))
    entry = next(
        item for item in guard.replay().entries if item.logical_request_id == screener_id
    )
    response_path = (
        root / "raw/nansen" / entry.reservation_id
        / f"attempt-{entry.attempt_count}-response.json"
    )
    rebuilt_panel = _panel_document(
        program, cycle_index, selected, response_path=response_path
    )
    if canonical_json_bytes(rebuilt_panel) != canonical_json_bytes(panel):
        raise CohortRunnerError("sealed cohort panel does not replay from the screener")
    if semantic_stage == "universe_sealed":
        return

    feature_documents: list[dict[str, Any]] = []
    for token_index, member in enumerate(panel["members"], start=1):
        candidate = _candidate(member)
        prefix = f"cycle-{cycle_index:02d}/token-{token_index:02d}"
        smart = validate_flow_body(
            _archived_body(root, guard, f"{prefix}/flow-smart-money"),
            candidate=candidate,
            label="smart_money",
            cutoff=scheduled,
        )
        exchange = validate_flow_body(
            _archived_body(root, guard, f"{prefix}/flow-exchange"),
            candidate=candidate,
            label="exchange",
            cutoff=scheduled,
        )
        breadth = {}
        for side in ("BUY", "SELL"):
            pages = [_archived_body(root, guard, f"{prefix}/wbs-{side.lower()}-page-1")]
            if _second_page_required(pages[0], label=f"WBS {side}"):
                pages.append(
                    _archived_body(root, guard, f"{prefix}/wbs-{side.lower()}-page-2")
                )
            breadth[side] = validate_wbs_pages(
                pages, candidate=candidate, side=side
            )
        document = {
            "schema_version": 1,
            "cycle_index": cycle_index,
            "token_index": token_index,
            "identity": candidate,
            "features": build_predecision_features(
                smart_money_rows=smart,
                exchange_rows=exchange,
                buyers=breadth["BUY"],
                sellers=breadth["SELL"],
                source_id=f"{program.program_id}:cycle-{cycle_index:02d}:token-{token_index:02d}",
            ),
        }
        feature_documents.append(document)
        stored = _read_json(
            root / f"derived/features/token-{token_index:02d}.json",
            label="token features",
        )
        if canonical_json_bytes(document) != canonical_json_bytes(stored):
            raise CohortRunnerError("sealed token features do not replay")
    rebuilt_features = {
        "schema_version": 1,
        "cycle_index": cycle_index,
        "tokens": feature_documents,
    }
    stored_features = _read_json(root / "derived/features.json", label="cycle features")
    if canonical_json_bytes(rebuilt_features) != canonical_json_bytes(stored_features):
        raise CohortRunnerError("sealed cycle features do not replay")
    if semantic_stage == "features_sealed":
        return

    bundle = load_cohort_comparators(
        program.root / COMPARATOR_PATH,
        program.manifest["comparator_sha256"],
        expected_source_sha256=EXPECTED_STRATEGY_SHA256,
    )
    clock_document = _read_json(root / "derived/decision-clock.json", label="decision clock")
    if set(clock_document) != {"schema_version", "computed_at", "decision_t0"} or clock_document.get("schema_version") != 1:
        raise CohortRunnerError("decision clock schema differs")
    computed_at = parse_utc(clock_document.get("computed_at"), field="computed_at")
    t0 = parse_utc(clock_document.get("decision_t0"), field="decision_t0")
    computed_floor = computed_at.replace(
        minute=(computed_at.minute // 5) * 5, second=0, microsecond=0
    )
    if t0 != computed_floor + timedelta(minutes=5):
        raise CohortRunnerError("decision t0 is not the next boundary after computation")
    features_seal = _validate_seal(
        root,
        _seal_path(root, "features_sealed"),
        expected_stage="features_sealed",
    )
    decision_seal = _validate_seal(
        root,
        _seal_path(root, "decisions_sealed"),
        expected_stage="decisions_sealed",
    )
    feature_sealed_at = parse_utc(
        features_seal["recorded_at"], field="feature seal recorded_at"
    )
    decision_sealed_at = parse_utc(
        decision_seal["recorded_at"], field="decision seal recorded_at"
    )
    if not feature_sealed_at <= computed_at <= decision_sealed_at < t0:
        raise CohortRunnerError("decision seal was not recorded before t0")
    if decision_sealed_at > scheduled + _DECISION_DEADLINE:
        raise CohortRunnerError("decision seal exceeded the frozen decision deadline")
    windows = execution_windows(t0)
    rebuilt_decisions = []
    for token in feature_documents:
        features = token["features"]
        comparators = _cohort_pair_distribution_veto(
            evaluate_comparators(
                bundle,
                features["smart_money"]["final_feature"],
                features["smart_money"]["prior_hour_feature"],
                available_at=scheduled,
            )
        )
        vetoes = [
            item for item in comparators
            if item.variant == "base" and item.role == "veto"
        ]
        if len(vetoes) != 1:
            raise CohortRunnerError("frozen comparator bundle has no unique veto")
        h5 = h5_decision(features)
        if h5["availability"] != "AVAILABLE":
            paired_action = None
            paired_availability = "UNAVAILABLE"
        elif h5["action"] == "ABSTAIN":
            paired_action = "ABSTAIN"
            paired_availability = "AVAILABLE"
        elif vetoes[0].availability != "AVAILABLE" or vetoes[0].veto_triggered is None:
            paired_action = None
            paired_availability = "UNAVAILABLE"
        else:
            paired_action = "ABSTAIN" if vetoes[0].veto_triggered else "LONG"
            paired_availability = "AVAILABLE"
        rebuilt_decisions.append({
            "token_index": token["token_index"],
            "identity": token["identity"],
            "h5": h5,
            "h5_distribution_veto": {
                "rule_id": _H5_PAIRED,
                "action": paired_action,
                "availability": paired_availability,
                "base_action": h5["action"],
                "veto_triggered": vetoes[0].veto_triggered,
            },
            "comparators": [asdict(item) for item in comparators],
        })
    rebuilt_decision_document = {
        "schema_version": 1,
        "cycle_index": cycle_index,
        "decision_t0": utc_text(t0),
        "windows": {key: utc_text(value) for key, value in windows.items()},
        "tokens": rebuilt_decisions,
    }
    stored_decisions = _read_json(root / "derived/decisions.json", label="cycle decisions")
    if canonical_json_bytes(rebuilt_decision_document) != canonical_json_bytes(stored_decisions):
        raise CohortRunnerError("sealed decisions do not replay")
    if semantic_stage == "decisions_sealed":
        return
    if semantic_stage != "outcome_sealed":
        return

    rebuilt_outcomes: list[dict[str, Any]] = []
    for token_index, member in enumerate(panel["members"], start=1):
        candidate = _candidate(member)
        prefix = f"cycle-{cycle_index:02d}/token-{token_index:02d}"
        pages = {}
        for side, start_key, end_key in (
            ("BUY", "entry_start", "entry_end"),
            ("SELL", "exit_start", "exit_end"),
        ):
            side_pages = [
                _archived_body(root, guard, f"{prefix}/dex-{side.lower()}-page-1")
            ]
            if _second_page_required(side_pages[0], label=f"DEX {side}"):
                side_pages.append(
                    _archived_body(root, guard, f"{prefix}/dex-{side.lower()}-page-2")
                )
            validate_trade_pages(
                side_pages,
                candidate=candidate,
                side=side,
                start=windows[start_key],
                end=windows[end_key],
            )
            pages[side] = side_pages
        entry_fill = build_entry_fill(
            pages["BUY"],
            candidate=candidate,
            notional_usd=member["virtual_notional_usd"],
            start=windows["entry_start"],
            end=windows["entry_end"],
        )
        exit_fill = None
        if entry_fill.filled_token_amount > 0:
            exit_fill = build_exit_fill(
                pages["SELL"],
                candidate=candidate,
                token_amount=entry_fill.filled_token_amount,
                start=windows["exit_start"],
                end=windows["exit_end"],
            )
        ohlcv_response = _archived_response(root, guard, f"{prefix}/ohlcv")
        candles = validate_ohlcv(
            ohlcv_response.body,
            candidate=candidate,
            start=windows["ohlcv_start"],
            end=windows["ohlcv_end"],
            retrieved_at=parse_utc(
                ohlcv_response.response_retrieved_at,
                field="OHLCV response retrieval",
            ),
        )
        document = {
            "token_index": token_index,
            "identity": candidate,
            **_token_week(candidate, windows["ohlcv_start"]),
            "outcome": score_counterfactual(
                entry_fill=entry_fill,
                exit_fill=exit_fill,
                ohlcv=candles,
                notional_usd=member["virtual_notional_usd"],
            ),
        }
        rebuilt_outcomes.append(document)
        stored = _read_json(
            root / f"derived/outcomes/token-{token_index:02d}.json",
            label="token outcome",
        )
        if canonical_json_bytes(document) != canonical_json_bytes(stored):
            raise CohortRunnerError("sealed token outcome does not replay")
    rebuilt_outcome_document = {
        "schema_version": 1,
        "cycle_index": cycle_index,
        "tokens": rebuilt_outcomes,
    }
    stored_outcomes = _read_json(root / "derived/outcomes.json", label="cycle outcomes")
    if canonical_json_bytes(rebuilt_outcome_document) != canonical_json_bytes(stored_outcomes):
        raise CohortRunnerError("sealed cycle outcomes do not replay")


def check_cycle(program: CohortProgram, cycle_index: int) -> dict[str, Any]:
    root, state = _load_cycle(program, cycle_index)
    guard = BudgetGuard(root, MAX_CYCLE_CREDITS, MAX_CYCLE_CREDITS)
    totals = guard.replay()
    attempts = _verified_request_attempt_count(root, guard)
    if attempts > MAX_CYCLE_ATTEMPTS:
        raise CohortRunnerError("cycle authenticated-attempt ceiling exceeded")
    if totals.credits > MAX_CYCLE_CREDITS and state["stage"] != "unscorable":
        raise CohortRunnerError("completed cycle credit ceiling exceeded")
    _validate_archived_request_contracts(
        program,
        cycle_index,
        root,
        guard,
        allow_account_failure=(state["stage"] == "unscorable"),
    )
    _semantic_replay(program, cycle_index, root, state, guard)
    sealed_evidence: set[Path] = set()
    expected_seals: set[Path] = set()
    for reference in state["seals"]:
        seal_path = root / reference["path"]
        expected_seals.add(seal_path.absolute())
        seal = _validate_seal(root, seal_path, expected_stage=reference["stage"])
        for record in seal["artifacts"]:
            sealed_evidence.add((root / record["path"]).absolute())
    _validate_budget_archive(root, state, totals)
    actual_evidence: set[Path] = set()
    for directory in (root / "raw", root / "derived"):
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise CohortRunnerError("cycle evidence cannot contain symlinks")
            if not path.is_file():
                continue
            actual_evidence.add(path.absolute())
    if actual_evidence != sealed_evidence and state["stage"] != "planned":
        raise CohortRunnerError("cycle contains unsealed or missing evidence")
    actual_seals = {
        path.absolute() for path in (root / "seals").iterdir()
    } if (root / "seals").exists() else set()
    if actual_seals != expected_seals:
        raise CohortRunnerError("cycle seal directory differs from state")
    expected_files = {
        (root / "state.json").absolute(),
        *sealed_evidence,
        *expected_seals,
        *{
            path.absolute()
            for path in (root / "budget").rglob("*")
            if path.is_file()
        },
    }
    actual_files = {
        path.absolute() for path in root.rglob("*") if path.is_file()
    }
    if actual_files != expected_files:
        raise CohortRunnerError("cycle tree contains missing or unexpected files")
    expected_dirs = {
        root.absolute(),
        (root / "budget").absolute(),
        (root / "budget/journal").absolute(),
    }
    for path in expected_files:
        parent = path.parent
        while parent != root.parent:
            expected_dirs.add(parent.absolute())
            if parent == root:
                break
            parent = parent.parent
    actual_dirs = {
        root.absolute(),
        *(path.absolute() for path in root.rglob("*") if path.is_dir()),
    }
    if actual_dirs != expected_dirs:
        raise CohortRunnerError("cycle tree contains unexpected directories")
    return {
        "cycle_index": cycle_index,
        "stage": state["stage"],
        "terminal_reason": state["terminal_reason"],
        "attempts": attempts,
        "credits": totals.credits,
        "provider_remaining": totals.provider_remaining,
    }


def replay_program(program: CohortProgram) -> dict[str, Any]:
    _validate_program_container(program)
    cycles = []
    for index in range(1, CYCLE_COUNT + 1):
        root = _cycle_root(program, index)
        if not root.exists():
            cycles.append({"cycle_index": index, "stage": "not_initialized", "attempts": 0, "credits": 0})
        else:
            cycles.append(check_cycle(program, index))
    result = {
        "schema_version": 1,
        "program_id": program.program_id,
        "cycles": cycles,
        "terminal_cycles": sum(item["stage"] in _TERMINAL for item in cycles),
        "authenticated_attempts": sum(item["attempts"] for item in cycles),
        "credits": sum(item["credits"] for item in cycles),
    }
    result["authorized_credit_ceiling_breached"] = (
        result["credits"] > MAX_PROGRAM_CREDITS
    )
    return result


def _aggregate_records(
    program: CohortProgram,
) -> tuple[list[dict[str, Any]], set[str], int, int]:
    records: list[dict[str, Any]] = []
    rule_ids: set[str] = {_H5, _H5_PAIRED}
    comparator_bundle = load_cohort_comparators(
        program.root / COMPARATOR_PATH,
        program.manifest["comparator_sha256"],
        expected_source_sha256=EXPECTED_STRATEGY_SHA256,
    )
    veto_ids = [theory.id for theory in comparator_bundle.theories if theory.role == "veto"]
    if len(veto_ids) != 1:
        raise CohortRunnerError("frozen comparator definitions have no unique veto")
    for theory in comparator_bundle.theories:
        if theory.role == "veto":
            continue
        rule_ids.add(f"{theory.id}::base")
        rule_ids.add(f"{theory.id}::paired::{veto_ids[0]}")
    selected_opportunities = 0
    attempted_counterfactual_fills = 0
    for index in range(1, CYCLE_COUNT + 1):
        root = _cycle_root(program, index)
        if not root.exists():
            continue
        state = _read_json(root / "state.json", label="cycle state")
        sealed_stages = {reference["stage"] for reference in state["seals"]}
        if "universe_sealed" not in sealed_stages:
            continue
        panel = _read_json(root / "derived/panel.json", label="cohort panel")
        members = panel.get("members")
        if not isinstance(members, list) or len(members) != 5:
            raise CohortRunnerError("sealed cohort panel is invalid during aggregation")
        selected_opportunities += len(members)
        guard = BudgetGuard(root, MAX_CYCLE_CREDITS, MAX_CYCLE_CREDITS)
        attempted_counterfactual_fills += sum(
            entry.logical_request_id.endswith("/dex-buy-page-1")
            and entry.request_artifact_sha256 is not None
            for entry in guard.replay().entries
        )
        if "decisions_sealed" not in sealed_stages:
            continue
        decisions = _read_json(root / "derived/decisions.json", label="cycle decisions")
        scheduled_at = state["scheduled_at"]
        decision_t0 = decisions["decision_t0"]
        by_index: dict[int, dict[str, Any]] = {}
        if state["stage"] == "outcome_sealed":
            outcomes = _read_json(root / "derived/outcomes.json", label="cycle outcomes")
            by_index = {
                row["token_index"]: row["outcome"] for row in outcomes["tokens"]
            }
        else:
            outcomes_root = root / "derived/outcomes"
            if outcomes_root.exists():
                for path in sorted(outcomes_root.glob("token-*.json")):
                    document = _read_json(path, label="partial cycle outcome")
                    token_index = document.get("token_index")
                    if (
                        not isinstance(token_index, int)
                        or isinstance(token_index, bool)
                        or not 1 <= token_index <= 5
                        or token_index in by_index
                        or not isinstance(document.get("outcome"), dict)
                    ):
                        raise CohortRunnerError("partial cycle outcome is invalid")
                    by_index[token_index] = document["outcome"]
        for decision in decisions["tokens"]:
            identity = decision["identity"]
            outcome = by_index.get(
                decision["token_index"],
                {"schema_version": 1, "status": "UNAVAILABLE"},
            )
            for rule_id, action, availability in (
                (
                    _H5,
                    decision["h5"]["action"],
                    decision["h5"]["availability"],
                ),
                (
                    _H5_PAIRED,
                    decision["h5_distribution_veto"]["action"],
                    decision["h5_distribution_veto"]["availability"],
                ),
            ):
                records.append({
                    "cycle_index": index, "scheduled_at": scheduled_at,
                    "decision_t0": decision_t0, **identity, "rule_id": rule_id,
                    "action": action, "availability": availability,
                    "outcome": outcome,
                })
            for comparator in decision["comparators"]:
                action = comparator.get("action")
                role = comparator.get("role")
                if role in {"veto", "blocked"}:
                    continue
                rule_id = comparator["decision_id"]
                rule_ids.add(rule_id)
                records.append({
                    "cycle_index": index, "scheduled_at": scheduled_at,
                    "decision_t0": decision_t0, **identity, "rule_id": rule_id,
                    "action": action,
                    "availability": comparator.get("availability"),
                    "outcome": outcome,
                })
    return (
        records,
        rule_ids,
        selected_opportunities,
        attempted_counterfactual_fills,
    )


def finalize_program(program: CohortProgram) -> Path:
    _validate_program_container(program)
    replay = replay_program(program)
    if replay["terminal_cycles"] != CYCLE_COUNT:
        raise CohortRunnerError("all 32 cycles must be terminal before aggregation")
    outcome_cycle_count = sum(
        item["stage"] == "outcome_sealed" for item in replay["cycles"]
    )
    has_unscorable = outcome_cycle_count != CYCLE_COUNT
    if replay["credits"] > MAX_PROGRAM_CREDITS and not has_unscorable:
        raise CohortRunnerError("program credit ceiling exceeded without terminal failure")
    (
        records,
        rule_ids,
        selected_opportunities,
        attempted_counterfactual_fills,
    ) = _aggregate_records(program)
    results = [
        aggregate_rule(
            records,
            rule_id=rule_id,
            program_id=program.program_id,
            terminal_cycle_count=CYCLE_COUNT,
            outcome_cycle_count=outcome_cycle_count,
            availability_integrity_ok=(
                not has_unscorable
                and not replay["authorized_credit_ceiling_breached"]
            ),
            advance_eligible=(rule_id == _H5_PAIRED),
            selected_opportunity_count=selected_opportunities,
            attempted_counterfactual_fill_count=attempted_counterfactual_fills,
        )
        for rule_id in sorted(rule_ids)
    ]
    path = program.root / "derived/aggregate.json"
    return _write_exact(
        path,
        {
            "schema_version": 1,
            "program_id": program.program_id,
            "budget": replay,
            "rules": results,
        },
        kind="cohort_aggregate",
    )


def check_program(program: CohortProgram) -> dict[str, Any]:
    _validate_program_container(program)
    replay = replay_program(program)
    if replay["credits"] > MAX_PROGRAM_CREDITS and not any(
        item["stage"] == "unscorable" for item in replay["cycles"]
    ):
        raise CohortRunnerError("program credit ceiling exceeded without terminal failure")
    cycles_root = program.root / "cycles"
    if cycles_root.exists():
        expected = {
            f"cycle-{index:02d}"
            for index in range(1, CYCLE_COUNT + 1)
            if (_cycle_root(program, index)).exists()
        }
        actual = {path.name for path in cycles_root.iterdir()}
        if actual != expected or any(path.is_symlink() for path in cycles_root.iterdir()):
            raise CohortRunnerError("program cycles directory contains unexpected entries")
    aggregate = program.root / "derived/aggregate.json"
    if aggregate.exists():
        expected = finalize_program(program)
        if expected != aggregate:  # pragma: no cover - both are the same fixed path
            raise CohortRunnerError("aggregate path differs")
    return replay
