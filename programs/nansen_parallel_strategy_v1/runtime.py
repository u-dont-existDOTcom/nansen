from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .schema import (
    PROGRAM_ID,
    V1_PROGRAM_ID,
    V1_PROGRAM_RELATIVE_PATH,
    ParallelStrategyProgram,
    ParallelStrategySchemaError,
    _confined,
    _read_object,
    _record_path,
    _regular_file,
    _write_once,
    assert_preregistration_committed,
    atomic_replace_json,
    canonical_json_bytes,
    load_program,
    sha256_bytes,
    sha256_file,
    utc_text,
)


CheckFunction = Callable[[Any], dict[str, Any]]
FinalizeFunction = Callable[[Any], Path]

ATTESTATION_RELATIVE_PATH = "activation/terminal-v1-operational-attestation.json"
TERMINAL_V1_CYCLES = 32
V1_MAX_ATTEMPTS = 1_824
V1_MAX_CREDITS = 1_792
_TERMINAL_STAGES = frozenset({"outcome_sealed", "unscorable"})


def _strict_int(value: Any, *, field: str, minimum: int = 0) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
    ):
        raise ParallelStrategySchemaError(f"{field} must be an integer >= {minimum}")
    return value


def _sanitize_check(value: Any) -> dict[str, Any]:
    """Reduce the cohort replay to operational state before it crosses programs."""

    if not isinstance(value, dict):
        raise ParallelStrategySchemaError("terminal-v1 check did not return an object")
    if value.get("program_id") != V1_PROGRAM_ID:
        raise ParallelStrategySchemaError("terminal-v1 check program identity differs")
    terminal_cycles = _strict_int(
        value.get("terminal_cycles"), field="terminal-v1 terminal cycles"
    )
    attempts = _strict_int(
        value.get("authenticated_attempts"), field="terminal-v1 attempts"
    )
    credits = _strict_int(value.get("credits"), field="terminal-v1 credits")
    if terminal_cycles != TERMINAL_V1_CYCLES:
        raise ParallelStrategySchemaError("terminal-v1 is not fully terminal")
    if attempts > V1_MAX_ATTEMPTS or credits > V1_MAX_CREDITS:
        raise ParallelStrategySchemaError("terminal-v1 accounting exceeds its ceiling")
    if value.get("authorized_credit_ceiling_breached") is not False:
        raise ParallelStrategySchemaError("terminal-v1 reports an authorized ceiling breach")
    raw_cycles = value.get("cycles")
    if not isinstance(raw_cycles, list) or len(raw_cycles) != TERMINAL_V1_CYCLES:
        raise ParallelStrategySchemaError("terminal-v1 cycle ledger differs")

    cycles: list[dict[str, Any]] = []
    for expected_index, item in enumerate(raw_cycles, start=1):
        if not isinstance(item, dict) or item.get("cycle_index") != expected_index:
            raise ParallelStrategySchemaError("terminal-v1 cycle identity differs")
        stage = item.get("stage")
        reason = item.get("terminal_reason")
        if stage not in _TERMINAL_STAGES:
            raise ParallelStrategySchemaError("terminal-v1 contains a nonterminal cycle")
        if stage == "outcome_sealed" and reason is not None:
            raise ParallelStrategySchemaError("completed terminal-v1 cycle has a reason")
        if stage == "unscorable" and (
            not isinstance(reason, str) or not reason
        ):
            raise ParallelStrategySchemaError("unscorable terminal-v1 cycle lacks a reason")
        cycle_attempts = _strict_int(
            item.get("attempts"), field="terminal-v1 cycle attempts"
        )
        cycle_credits = _strict_int(
            item.get("credits"), field="terminal-v1 cycle credits"
        )
        provider_remaining = item.get("provider_remaining")
        if provider_remaining is not None:
            provider_remaining = _strict_int(
                provider_remaining, field="terminal-v1 provider remaining"
            )
        cycles.append(
            {
                "cycle_index": expected_index,
                "stage": stage,
                "terminal_reason": reason,
                "authenticated_attempts": cycle_attempts,
                "billable_credits": cycle_credits,
                "provider_remaining": provider_remaining,
            }
        )
    if sum(item["authenticated_attempts"] for item in cycles) != attempts:
        raise ParallelStrategySchemaError("terminal-v1 attempt total differs from cycles")
    if sum(item["billable_credits"] for item in cycles) != credits:
        raise ParallelStrategySchemaError("terminal-v1 credit total differs from cycles")
    return {
        "terminal_cycles": terminal_cycles,
        "authenticated_attempts": attempts,
        "billable_credits": credits,
        "cycles": cycles,
    }


def _state_binding(
    v1_root: Path,
    operational_cycle: dict[str, Any],
) -> dict[str, Any]:
    index = operational_cycle["cycle_index"]
    cycle_root = v1_root / "cycles" / f"cycle-{index:02d}"
    state_path = _regular_file(
        _confined(
            v1_root,
            f"cycles/cycle-{index:02d}/state.json",
            field="terminal-v1 state path",
        ),
        label="terminal-v1 state",
    )
    state = _read_object(state_path, label="terminal-v1 state")
    if (
        state.get("cycle_index") != index
        or state.get("stage") != operational_cycle["stage"]
        or state.get("terminal_reason") != operational_cycle["terminal_reason"]
    ):
        raise ParallelStrategySchemaError("terminal-v1 state differs from full check")
    seals = state.get("seals")
    if not isinstance(seals, list) or not seals:
        raise ParallelStrategySchemaError("terminal-v1 state has no terminal seal")
    seal_bindings: list[dict[str, str]] = []
    observed_stages: set[str] = set()
    for reference in seals:
        if (
            not isinstance(reference, dict)
            or set(reference) != {"path", "sha256", "stage"}
            or not isinstance(reference.get("stage"), str)
            or not isinstance(reference.get("sha256"), str)
        ):
            raise ParallelStrategySchemaError("terminal-v1 seal reference differs")
        seal = _confined(cycle_root, reference.get("path"), field="terminal-v1 seal path")
        _regular_file(seal, label="terminal-v1 seal")
        digest = sha256_file(seal)
        if digest != reference["sha256"]:
            raise ParallelStrategySchemaError("terminal-v1 seal SHA-256 differs")
        if reference["stage"] in observed_stages:
            raise ParallelStrategySchemaError("terminal-v1 seal stage is duplicated")
        observed_stages.add(reference["stage"])
        seal_bindings.append(
            {"stage": reference["stage"], "sha256": reference["sha256"]}
        )
    if operational_cycle["stage"] not in observed_stages:
        raise ParallelStrategySchemaError("terminal-v1 terminal seal is absent")
    return {
        **operational_cycle,
        "state_sha256": sha256_file(state_path),
        "seals": seal_bindings,
    }


def _default_cohort_functions(
    manifest_path: Path,
) -> tuple[Any, CheckFunction, FinalizeFunction]:
    # Import only after the independent program has passed its own runtime gate.
    from src.nansen_signal_lab.cohort_runner import check_program, finalize_program
    from src.nansen_signal_lab.cohort_schema import load_cohort_program

    cohort = load_cohort_program(manifest_path)
    return cohort, check_program, finalize_program


def _checked_operational_replay(
    v1_manifest_path: Path,
    *,
    cohort_check: CheckFunction | None,
    cohort_finalize: FinalizeFunction | None,
) -> tuple[dict[str, Any], Path]:
    if (cohort_check is None) != (cohort_finalize is None):
        raise ParallelStrategySchemaError(
            "terminal-v1 test seam requires both check and finalize functions"
        )
    if cohort_check is None:
        subject, check, finalize = _default_cohort_functions(v1_manifest_path)
    else:
        subject = v1_manifest_path
        check = cohort_check
        finalize = cohort_finalize
        assert finalize is not None

    before = _sanitize_check(check(subject))
    aggregate_path = Path(finalize(subject)).absolute()
    expected_aggregate = v1_manifest_path.parent / "derived/aggregate.json"
    if aggregate_path != expected_aggregate.absolute():
        raise ParallelStrategySchemaError("terminal-v1 finalizer returned another artifact")
    _regular_file(aggregate_path, label="terminal-v1 aggregate")
    after = _sanitize_check(check(subject))
    if before != after:
        raise ParallelStrategySchemaError(
            "terminal-v1 operational replay changed across finalization"
        )
    return after, aggregate_path


def _attestation_document(
    program: ParallelStrategyProgram,
    operational: dict[str, Any],
    aggregate_path: Path,
    *,
    recorded_at: datetime,
) -> dict[str, Any]:
    prerequisite = program.manifest["activation_prerequisite"]
    v1_manifest_path = program.repo_root / V1_PROGRAM_RELATIVE_PATH
    v1_root = v1_manifest_path.parent
    source_digest = sha256_file(v1_manifest_path)
    if source_digest != prerequisite["program_sha256"]:
        raise ParallelStrategySchemaError("terminal-v1 program changed during activation")
    cycles = [
        _state_binding(v1_root, cycle) for cycle in operational["cycles"]
    ]
    sanitized = {
        "terminal_cycles": operational["terminal_cycles"],
        "authenticated_attempts": operational["authenticated_attempts"],
        "billable_credits": operational["billable_credits"],
        "cycles": cycles,
    }
    return {
        "schema_version": 1,
        "kind": "terminal-v1-operational-attestation-v1",
        "program_id": PROGRAM_ID,
        "recorded_at": utc_text(recorded_at),
        "source_program_id": V1_PROGRAM_ID,
        "source_program_sha256": source_digest,
        "source_aggregate_sha256": sha256_file(aggregate_path),
        "operational_replay_sha256": sha256_bytes(canonical_json_bytes(sanitized)),
        "terminal_cycles": operational["terminal_cycles"],
        "authenticated_attempts": operational["authenticated_attempts"],
        "billable_credits": operational["billable_credits"],
        "cycles": cycles,
    }


def validate_terminal_v1_attestation(
    program: ParallelStrategyProgram,
) -> dict[str, Any]:
    activation_path = _record_path(
        program, program.manifest["activation"], field="activation"
    )
    value = _read_object(activation_path, label="terminal-v1 activation attestation")
    expected_keys = {
        "schema_version",
        "kind",
        "program_id",
        "recorded_at",
        "source_program_id",
        "source_program_sha256",
        "source_aggregate_sha256",
        "operational_replay_sha256",
        "terminal_cycles",
        "authenticated_attempts",
        "billable_credits",
        "cycles",
    }
    if (
        set(value) != expected_keys
        or value.get("schema_version") != 1
        or value.get("kind") != "terminal-v1-operational-attestation-v1"
        or value.get("program_id") != PROGRAM_ID
        or value.get("source_program_id") != V1_PROGRAM_ID
        or value.get("source_program_sha256")
        != program.manifest["activation_prerequisite"]["program_sha256"]
        or value.get("terminal_cycles") != TERMINAL_V1_CYCLES
    ):
        raise ParallelStrategySchemaError("terminal-v1 activation attestation differs")
    # Validate timestamp syntax without making wall-clock freshness a replay input.
    try:
        parsed = datetime.fromisoformat(str(value.get("recorded_at")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ParallelStrategySchemaError("terminal-v1 attestation time is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ParallelStrategySchemaError("terminal-v1 attestation time is not aware")

    attempts = _strict_int(
        value.get("authenticated_attempts"), field="attested v1 attempts"
    )
    credits = _strict_int(value.get("billable_credits"), field="attested v1 credits")
    if attempts > V1_MAX_ATTEMPTS or credits > V1_MAX_CREDITS:
        raise ParallelStrategySchemaError("attested terminal-v1 budget exceeds its ceiling")
    cycles = value.get("cycles")
    if not isinstance(cycles, list) or len(cycles) != TERMINAL_V1_CYCLES:
        raise ParallelStrategySchemaError("attested terminal-v1 cycles differ")
    operational_cycles: list[dict[str, Any]] = []
    for expected_index, item in enumerate(cycles, start=1):
        expected_cycle_keys = {
            "cycle_index",
            "stage",
            "terminal_reason",
            "authenticated_attempts",
            "billable_credits",
            "provider_remaining",
            "state_sha256",
            "seals",
        }
        if not isinstance(item, dict) or set(item) != expected_cycle_keys:
            raise ParallelStrategySchemaError("attested terminal-v1 cycle schema differs")
        operational = {
            key: item[key]
            for key in (
                "cycle_index",
                "stage",
                "terminal_reason",
                "authenticated_attempts",
                "billable_credits",
                "provider_remaining",
            )
        }
        if operational["cycle_index"] != expected_index:
            raise ParallelStrategySchemaError("attested terminal-v1 cycle order differs")
        rebound = _state_binding(
            program.repo_root / V1_PROGRAM_RELATIVE_PATH.parent,
            operational,
        )
        if rebound != item:
            raise ParallelStrategySchemaError("attested terminal-v1 state binding drifted")
        operational_cycles.append(item)
    if sum(item["authenticated_attempts"] for item in cycles) != attempts:
        raise ParallelStrategySchemaError("attested terminal-v1 attempts differ")
    if sum(item["billable_credits"] for item in cycles) != credits:
        raise ParallelStrategySchemaError("attested terminal-v1 credits differ")

    v1_root = program.repo_root / V1_PROGRAM_RELATIVE_PATH.parent
    aggregate = _regular_file(
        _confined(v1_root, "derived/aggregate.json", field="terminal-v1 aggregate path"),
        label="terminal-v1 aggregate",
    )
    if sha256_file(aggregate) != value["source_aggregate_sha256"]:
        raise ParallelStrategySchemaError("terminal-v1 aggregate binding drifted")
    sanitized = {
        "terminal_cycles": value["terminal_cycles"],
        "authenticated_attempts": attempts,
        "billable_credits": credits,
        "cycles": cycles,
    }
    if sha256_bytes(canonical_json_bytes(sanitized)) != value["operational_replay_sha256"]:
        raise ParallelStrategySchemaError("terminal-v1 operational replay hash differs")
    return value


def produce_terminal_v1_attestation(
    manifest_path: Path,
    *,
    cohort_check: CheckFunction | None = None,
    cohort_finalize: FinalizeFunction | None = None,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    """Run check/finalize/check and activate without exporting rule metrics."""

    program = load_program(manifest_path)
    if program.stage == "activated":
        return validate_terminal_v1_attestation(program)
    assert_preregistration_committed(manifest_path)

    v1_manifest_path = _regular_file(
        _confined(
            program.repo_root,
            V1_PROGRAM_RELATIVE_PATH.as_posix(),
            field="terminal-v1 program path",
        ),
        label="terminal-v1 program manifest",
    )
    before = v1_manifest_path.read_bytes()
    operational, aggregate_path = _checked_operational_replay(
        v1_manifest_path,
        cohort_check=cohort_check,
        cohort_finalize=cohort_finalize,
    )
    if v1_manifest_path.read_bytes() != before:
        raise ParallelStrategySchemaError(
            "terminal-v1 program manifest changed during activation"
        )
    now = datetime.now(timezone.utc) if recorded_at is None else recorded_at
    attestation = _attestation_document(
        program,
        operational,
        aggregate_path,
        recorded_at=now,
    )
    path = _write_once(
        program.root / ATTESTATION_RELATIVE_PATH,
        canonical_json_bytes(attestation),
    )
    updated = dict(program.manifest)
    updated["stage"] = "activated"
    updated["activation"] = {
        "path": ATTESTATION_RELATIVE_PATH,
        "sha256": sha256_file(path),
    }
    atomic_replace_json(program.manifest_path, updated)
    activated = load_program(program.manifest_path)
    return validate_terminal_v1_attestation(activated)


def require_terminal_v1_activation(manifest_path: Path) -> ParallelStrategyProgram:
    """Gate called by every action before constructing a provider client."""

    program = load_program(manifest_path)
    if program.stage != "activated":
        raise ParallelStrategySchemaError(
            "terminal-v1 operational attestation is required before first action"
        )
    validate_terminal_v1_attestation(program)
    return program
