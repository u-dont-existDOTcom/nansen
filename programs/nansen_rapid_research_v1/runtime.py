from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
UnitProbe = Callable[[str], Mapping[str, str]]

OWNER_ATTESTATION_RELATIVE_PATH = "activation/owner-aborted-v1-attestation.json"
STOP_ATTESTATION_RELATIVE_PATH = "activation/operator-stop-attestation.json"
RECONCILIATION_RELATIVE_PATH = "activation/operational-reconciliation.json"
ACTIVATION_INTENT_RELATIVE_PATH = "activation/activation-intent.json"

EXPECTED_V1_PROGRAM_SHA256 = (
    "afc3978f1d3e98f22f93c4fba92b0189f7d77249bba746318dae331a0c2be0ab"
)
EXPECTED_CYCLE1_STATE_SHA256 = (
    "e5eff4eb9f7f6cf5091b08d8fa8a5c652357ea2549247923193de6c6b35cac8d"
)
EXPECTED_CYCLE1_SEAL_SHA256 = (
    "ddd8469e3722bc366002d6a20aa7b20089febd711c766e1f8bb765700c1207a1"
)
EXPECTED_CYCLE1_REASON = "insufficient_strata"

LEGACY_UNITS = (
    "nansen-signal-lab-cohort.service",
    "nansen-signal-lab-cohort.timer",
    "nansen-signal-lab-parallel-strategy.service",
    "nansen-signal-lab-parallel-strategy.timer",
)


def _strict_int(value: Any, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ParallelStrategySchemaError(f"{field} must be a nonnegative integer")
    return value


def _default_cohort_check(manifest_path: Path) -> dict[str, Any]:
    # Imported only while producing the one-time activation proof. The rapid
    # program never imports cohort scientific outputs.
    from src.nansen_signal_lab.cohort_runner import check_program
    from src.nansen_signal_lab.cohort_schema import load_cohort_program

    return check_program(load_cohort_program(manifest_path))


def _sanitize_owner_abort(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("program_id") != V1_PROGRAM_ID:
        raise ParallelStrategySchemaError("owner-aborted v1 replay identity differs")
    if (
        _strict_int(value.get("terminal_cycles"), field="terminal cycles") != 1
        or _strict_int(value.get("authenticated_attempts"), field="attempts") != 2
        or _strict_int(value.get("credits"), field="credits") != 1
        or value.get("authorized_credit_ceiling_breached") is not False
    ):
        raise ParallelStrategySchemaError("owner-aborted v1 replay totals differ")
    cycles = value.get("cycles")
    if not isinstance(cycles, list) or len(cycles) != 32:
        raise ParallelStrategySchemaError("owner-aborted v1 cycle ledger differs")
    first = cycles[0]
    if (
        not isinstance(first, dict)
        or first.get("cycle_index") != 1
        or first.get("stage") != "unscorable"
        or first.get("terminal_reason") != EXPECTED_CYCLE1_REASON
        or _strict_int(first.get("attempts"), field="cycle-one attempts") != 2
        or _strict_int(first.get("credits"), field="cycle-one credits") != 1
        or first.get("provider_remaining") != 50_063
    ):
        raise ParallelStrategySchemaError("owner-aborted v1 cycle one differs")
    for expected_index, cycle in enumerate(cycles[1:], start=2):
        if (
            not isinstance(cycle, dict)
            or cycle.get("cycle_index") != expected_index
            or cycle.get("stage") != "not_initialized"
            or _strict_int(cycle.get("attempts"), field="unused cycle attempts") != 0
            or _strict_int(cycle.get("credits"), field="unused cycle credits") != 0
        ):
            raise ParallelStrategySchemaError("owner-aborted v1 has later activity")
    return {
        "terminal_cycles": 1,
        "authenticated_attempts": 2,
        "billable_credits": 1,
        "cycle": {
            "cycle_index": 1,
            "stage": "unscorable",
            "terminal_reason": EXPECTED_CYCLE1_REASON,
            "authenticated_attempts": 2,
            "billable_credits": 1,
            "provider_remaining": 50_063,
        },
    }


def _state_binding(program: ParallelStrategyProgram, operational: Mapping[str, Any]) -> dict[str, Any]:
    v1_root = program.repo_root / V1_PROGRAM_RELATIVE_PATH.parent
    state_path = _regular_file(
        _confined(v1_root, "cycles/cycle-01/state.json", field="cycle-one state path"),
        label="cycle-one state",
    )
    seal_path = _regular_file(
        _confined(
            v1_root,
            "cycles/cycle-01/seals/unscorable.json",
            field="cycle-one seal path",
        ),
        label="cycle-one terminal seal",
    )
    if sha256_file(state_path) != EXPECTED_CYCLE1_STATE_SHA256:
        raise ParallelStrategySchemaError("owner-aborted v1 state drifted")
    if sha256_file(seal_path) != EXPECTED_CYCLE1_SEAL_SHA256:
        raise ParallelStrategySchemaError("owner-aborted v1 seal drifted")
    state = _read_object(state_path, label="cycle-one state")
    if (
        state.get("cycle_index") != 1
        or state.get("stage") != "unscorable"
        or state.get("terminal_reason") != EXPECTED_CYCLE1_REASON
        or state.get("program_manifest_sha256") != EXPECTED_V1_PROGRAM_SHA256
    ):
        raise ParallelStrategySchemaError("owner-aborted v1 state semantics drifted")
    cycle = dict(operational["cycle"])
    cycle["state_sha256"] = EXPECTED_CYCLE1_STATE_SHA256
    cycle["seals"] = [
        {"stage": "unscorable", "sha256": EXPECTED_CYCLE1_SEAL_SHA256}
    ]
    return cycle


def _real_unit_probe(unit: str) -> Mapping[str, str]:
    result = subprocess.run(
        (
            "systemctl",
            "--user",
            "show",
            unit,
            "--property=ActiveState",
            "--property=UnitFileState",
        ),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise ParallelStrategySchemaError(f"cannot prove stopped legacy unit: {unit}")
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return {
        "active_state": values.get("ActiveState", ""),
        "unit_file_state": values.get("UnitFileState", ""),
    }


def _stopped_units(probe: UnitProbe) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for unit in LEGACY_UNITS:
        observed = dict(probe(unit))
        if observed != {"active_state": "inactive", "unit_file_state": "disabled"}:
            raise ParallelStrategySchemaError(f"legacy provider unit retains authority: {unit}")
        result[unit] = observed
    return result


def _validate_recorded_at(value: Any, *, field: str) -> None:
    if not isinstance(value, str):
        raise ParallelStrategySchemaError(f"{field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ParallelStrategySchemaError(f"{field} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ParallelStrategySchemaError(f"{field} is not timezone-aware")


def _activation_intent(program: ParallelStrategyProgram) -> dict[str, Any]:
    value = _read_object(
        program.root / ACTIVATION_INTENT_RELATIVE_PATH,
        label="activation intent",
    )
    if (
        set(value)
        != {
            "schema_version",
            "kind",
            "program_id",
            "recorded_at",
            "source_program_id",
            "source_program_sha256",
        }
        or value.get("schema_version") != 1
        or value.get("kind") != "rapid-owner-abort-activation-intent-v1"
        or value.get("program_id") != PROGRAM_ID
        or value.get("source_program_id") != V1_PROGRAM_ID
        or value.get("source_program_sha256") != EXPECTED_V1_PROGRAM_SHA256
    ):
        raise ParallelStrategySchemaError("activation intent differs")
    _validate_recorded_at(value.get("recorded_at"), field="activation intent time")
    return value


def _validate_owner_attestation(program: ParallelStrategyProgram) -> dict[str, Any]:
    path = program.root / OWNER_ATTESTATION_RELATIVE_PATH
    value = _read_object(path, label="owner-aborted v1 attestation")
    expected_keys = {
        "schema_version",
        "kind",
        "program_id",
        "recorded_at",
        "source_program_id",
        "source_program_sha256",
        "status",
        "terminal_cycles",
        "authenticated_attempts",
        "billable_credits",
        "cycles",
        "operational_replay_sha256",
    }
    if (
        set(value) != expected_keys
        or value.get("schema_version") != 1
        or value.get("kind") != "owner-aborted-v1-operational-attestation-v1"
        or value.get("program_id") != PROGRAM_ID
        or value.get("source_program_id") != V1_PROGRAM_ID
        or value.get("source_program_sha256") != EXPECTED_V1_PROGRAM_SHA256
        or value.get("status") != "owner_aborted"
        or value.get("terminal_cycles") != 1
        or value.get("authenticated_attempts") != 2
        or value.get("billable_credits") != 1
        or not isinstance(value.get("cycles"), list)
        or len(value["cycles"]) != 1
    ):
        raise ParallelStrategySchemaError("owner-aborted v1 attestation differs")
    _validate_recorded_at(value.get("recorded_at"), field="owner attestation time")
    cycle = value["cycles"][0]
    expected_cycle = _state_binding(
        program,
        {
            "cycle": {
                "cycle_index": 1,
                "stage": "unscorable",
                "terminal_reason": EXPECTED_CYCLE1_REASON,
                "authenticated_attempts": 2,
                "billable_credits": 1,
                "provider_remaining": 50_063,
            }
        },
    )
    if cycle != expected_cycle:
        raise ParallelStrategySchemaError("owner-aborted v1 state binding differs")
    replay = {
        "terminal_cycles": 1,
        "authenticated_attempts": 2,
        "billable_credits": 1,
        "cycles": [cycle],
    }
    if sha256_bytes(canonical_json_bytes(replay)) != value["operational_replay_sha256"]:
        raise ParallelStrategySchemaError("owner-aborted v1 replay hash differs")
    return value


def _validate_stop_attestation(
    program: ParallelStrategyProgram,
    *,
    probe: UnitProbe | None = None,
) -> dict[str, Any]:
    value = _read_object(
        program.root / STOP_ATTESTATION_RELATIVE_PATH,
        label="operator stop attestation",
    )
    expected_units = (
        _stopped_units(probe)
        if probe is not None
        else {
            unit: {"active_state": "inactive", "unit_file_state": "disabled"}
            for unit in LEGACY_UNITS
        }
    )
    if (
        set(value) != {"schema_version", "kind", "program_id", "recorded_at", "units"}
        or value.get("schema_version") != 1
        or value.get("kind") != "legacy-automation-stop-attestation-v1"
        or value.get("program_id") != PROGRAM_ID
        or value.get("units") != expected_units
    ):
        raise ParallelStrategySchemaError("operator stop attestation differs")
    _validate_recorded_at(value.get("recorded_at"), field="operator stop time")
    return value


def validate_stopped_v1_activation(
    program: ParallelStrategyProgram,
    *,
    unit_probe: UnitProbe | None = None,
) -> dict[str, Any]:
    if program.stage != "activated":
        raise ParallelStrategySchemaError("rapid program is not activated")
    activation_path = _record_path(program, program.manifest["activation"], field="activation")
    if activation_path.relative_to(program.root).as_posix() != RECONCILIATION_RELATIVE_PATH:
        raise ParallelStrategySchemaError("rapid activation path differs")
    intent = _activation_intent(program)
    owner = _validate_owner_attestation(program)
    stop = _validate_stop_attestation(program, probe=unit_probe)
    if not (
        intent["recorded_at"] == owner["recorded_at"] == stop["recorded_at"]
    ):
        raise ParallelStrategySchemaError("activation transaction timestamps differ")
    from .activation import operational_reconciliation

    expected = operational_reconciliation(program)
    if activation_path.read_bytes() != canonical_json_bytes(expected):
        raise ParallelStrategySchemaError("operational reconciliation differs")
    return expected


def produce_stopped_v1_attestation(
    manifest_path: Path,
    *,
    cohort_check: CheckFunction | None = None,
    unit_probe: UnitProbe | None = None,
    recorded_at: datetime | None = None,
) -> dict[str, Any]:
    """Activate from the exact owner-aborted v1 and stopped legacy units."""

    program = load_program(manifest_path)
    if program.stage == "activated":
        return validate_stopped_v1_activation(program, unit_probe=unit_probe)
    assert_preregistration_committed(manifest_path)
    v1_manifest = _regular_file(
        _confined(
            program.repo_root,
            V1_PROGRAM_RELATIVE_PATH.as_posix(),
            field="owner-aborted v1 manifest path",
        ),
        label="owner-aborted v1 manifest",
    )
    if (
        sha256_file(v1_manifest) != EXPECTED_V1_PROGRAM_SHA256
        or program.manifest["activation_prerequisite"]["program_sha256"]
        != EXPECTED_V1_PROGRAM_SHA256
    ):
        raise ParallelStrategySchemaError("owner-aborted v1 program binding differs")
    check = _default_cohort_check if cohort_check is None else cohort_check
    operational = _sanitize_owner_abort(check(v1_manifest))
    cycle = _state_binding(program, operational)
    intent_path = program.root / ACTIVATION_INTENT_RELATIVE_PATH
    if intent_path.exists():
        intent = _activation_intent(program)
    else:
        now = datetime.now(timezone.utc) if recorded_at is None else recorded_at
        intent = {
            "schema_version": 1,
            "kind": "rapid-owner-abort-activation-intent-v1",
            "program_id": PROGRAM_ID,
            "recorded_at": utc_text(now),
            "source_program_id": V1_PROGRAM_ID,
            "source_program_sha256": EXPECTED_V1_PROGRAM_SHA256,
        }
        _write_once(intent_path, canonical_json_bytes(intent))
        intent = _activation_intent(program)
    recorded_text = str(intent["recorded_at"])
    replay = {
        "terminal_cycles": 1,
        "authenticated_attempts": 2,
        "billable_credits": 1,
        "cycles": [cycle],
    }
    owner = {
        "schema_version": 1,
        "kind": "owner-aborted-v1-operational-attestation-v1",
        "program_id": PROGRAM_ID,
        "recorded_at": recorded_text,
        "source_program_id": V1_PROGRAM_ID,
        "source_program_sha256": EXPECTED_V1_PROGRAM_SHA256,
        "status": "owner_aborted",
        "terminal_cycles": 1,
        "authenticated_attempts": 2,
        "billable_credits": 1,
        "cycles": [cycle],
        "operational_replay_sha256": sha256_bytes(canonical_json_bytes(replay)),
    }
    _write_once(
        program.root / OWNER_ATTESTATION_RELATIVE_PATH,
        canonical_json_bytes(owner),
    )
    stop = {
        "schema_version": 1,
        "kind": "legacy-automation-stop-attestation-v1",
        "program_id": PROGRAM_ID,
        "recorded_at": recorded_text,
        "units": _stopped_units(unit_probe or _real_unit_probe),
    }
    _write_once(
        program.root / STOP_ATTESTATION_RELATIVE_PATH,
        canonical_json_bytes(stop),
    )
    from .activation import operational_reconciliation

    reconciliation = operational_reconciliation(program)
    reconciliation_path = _write_once(
        program.root / RECONCILIATION_RELATIVE_PATH,
        canonical_json_bytes(reconciliation),
    )
    updated = dict(program.manifest)
    updated["stage"] = "activated"
    updated["activation"] = {
        "path": RECONCILIATION_RELATIVE_PATH,
        "sha256": sha256_file(reconciliation_path),
    }
    atomic_replace_json(program.manifest_path, updated)
    activated = load_program(program.manifest_path)
    return validate_stopped_v1_activation(activated, unit_probe=unit_probe)


def require_stopped_v1_activation(manifest_path: Path) -> ParallelStrategyProgram:
    """Gate every live action on owner abort, reconciliation, and stopped units."""

    program = load_program(manifest_path)
    if program.stage != "activated":
        raise ParallelStrategySchemaError(
            "owner-abort and stopped-automation activation is required"
        )
    validate_stopped_v1_activation(program)
    _stopped_units(_real_unit_probe)
    return program


# Explicit aliases ease inspection tools while keeping the public rapid names.
produce_owner_abort_activation = produce_stopped_v1_attestation
require_owner_abort_activation = require_stopped_v1_activation
