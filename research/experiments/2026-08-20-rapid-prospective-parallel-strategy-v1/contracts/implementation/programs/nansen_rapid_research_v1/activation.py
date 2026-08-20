from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .budget import (
    MINIMUM_FIRST_BASELINE,
    OPERATOR_ATTESTATION_PATH,
    OWNER_ATTESTATION_PATH,
    RECONCILIATION_KIND,
    reconstruct_operational_balances,
)
from .design import PROGRAM_ID
from .schema import (
    ParallelStrategyProgram,
    ParallelStrategySchemaError,
    atomic_write_once,
    canonical_json_bytes,
)


class ParallelStrategyActivationError(RuntimeError):
    """Raised when the rapid program's operational activation is not exact."""


V1_PROGRAM_ID = "2026-08-18-prospective-multi-cycle-cohort-v1"
V1_PROGRAM_RELATIVE_PATH = (
    "research/experiments/2026-08-18-prospective-multi-cycle-cohort-v1/program.json"
)
V1_PROGRAM_SHA256 = (
    "afc3978f1d3e98f22f93c4fba92b0189f7d77249bba746318dae331a0c2be0ab"
)
V1_CYCLE_ONE_STATE_SHA256 = (
    "e5eff4eb9f7f6cf5091b08d8fa8a5c652357ea2549247923193de6c6b35cac8d"
)
V1_CYCLE_ONE_SEAL_SHA256 = (
    "ddd8469e3722bc366002d6a20aa7b20089febd711c766e1f8bb765700c1207a1"
)
OPENING_PROVIDER_BALANCE = 50_063
PREDECESSORS = (
    {
        "program_id": "2026-08-18-historical-theory-discovery-a-v1",
        "relative_path": (
            "research/experiments/"
            "2026-08-18-historical-theory-discovery-a-v1/seals/final.json"
        ),
        "sha256": (
            "3132f1bfaa5e99d535bd6ded819f9751a44bede2f6dbf0bf60689cd0c9c49230"
        ),
        "attempts": 135,
        "credits": 537,
        "confirmed_after_snapshot": 532,
        "reserved_after_snapshot": [0],
    },
    {
        "program_id": "2026-08-18-historical-theory-discovery-a2-v1",
        "relative_path": (
            "research/experiments/"
            "2026-08-18-historical-theory-discovery-a2-v1/seals/final.json"
        ),
        "sha256": (
            "5f58ce65563be7f4ab909b3b00fa2bbac5eba590d9b534f8216b14043181d230"
        ),
        "attempts": 1_219,
        "credits": 4_829,
        "confirmed_after_snapshot": 4_828,
        "reserved_after_snapshot": [0, 1],
    },
)
LEGACY_UNITS = (
    "nansen-signal-lab-cohort.service",
    "nansen-signal-lab-cohort.timer",
    "nansen-signal-lab-parallel-strategy.service",
    "nansen-signal-lab-parallel-strategy.timer",
)


def _sha256(path: Path, *, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise ParallelStrategyActivationError(f"{label} is absent")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise ParallelStrategyActivationError(f"{label} is absent")
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ParallelStrategyActivationError(f"{label} is unreadable") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise ParallelStrategyActivationError(
            f"{label} must be a canonical JSON object"
        )
    return value, raw


def _timestamp(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ParallelStrategyActivationError(f"{field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ParallelStrategyActivationError(f"{field} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ParallelStrategyActivationError(f"{field} is not timezone-aware")
    return value


def _resolve_program(
    program_or_manifest: ParallelStrategyProgram | Path,
) -> ParallelStrategyProgram:
    if (
        hasattr(program_or_manifest, "repo_root")
        and hasattr(program_or_manifest, "root")
        and hasattr(program_or_manifest, "manifest")
    ):
        return program_or_manifest  # type: ignore[return-value]
    manifest_path = Path(program_or_manifest)
    if manifest_path.is_dir():
        manifest_path = manifest_path / "program.json"
    # Lazy import prevents the runtime -> activation dependency during the
    # activation transaction.  A path input is admitted only after activation.
    from .runtime import require_stopped_v1_activation

    return require_stopped_v1_activation(manifest_path)


def _validate_owner_attestation(
    program: ParallelStrategyProgram,
) -> tuple[dict[str, Any], str]:
    path = program.root / OWNER_ATTESTATION_PATH
    value, raw = _read(path, label="owner-aborted-v1 attestation")
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
        or value.get("source_program_sha256") != V1_PROGRAM_SHA256
        or value.get("status") != "owner_aborted"
        or value.get("terminal_cycles") != 1
        or value.get("authenticated_attempts") != 2
        or value.get("billable_credits") != 1
    ):
        raise ParallelStrategyActivationError(
            "owner-aborted-v1 attestation identity or accounting differs"
        )
    _timestamp(value.get("recorded_at"), field="owner-aborted-v1 recorded_at")
    prerequisite = program.manifest.get("activation_prerequisite")
    if (
        not isinstance(prerequisite, dict)
        or prerequisite.get("program_id") != V1_PROGRAM_ID
        or prerequisite.get("path") != V1_PROGRAM_RELATIVE_PATH
        or prerequisite.get("program_sha256") != V1_PROGRAM_SHA256
        or prerequisite.get("required_terminal_cycles") != 1
        or prerequisite.get("maximum_authenticated_attempts") != 2
        or prerequisite.get("maximum_billable_credits") != 1
    ):
        raise ParallelStrategyActivationError(
            "owner-aborted-v1 manifest prerequisite differs"
        )
    source = program.repo_root / V1_PROGRAM_RELATIVE_PATH
    if _sha256(source, label="owner-aborted-v1 source program") != V1_PROGRAM_SHA256:
        raise ParallelStrategyActivationError(
            "owner-aborted-v1 source program changed"
        )
    expected_cycle = {
        "cycle_index": 1,
        "stage": "unscorable",
        "terminal_reason": "insufficient_strata",
        "authenticated_attempts": 2,
        "billable_credits": 1,
        "provider_remaining": OPENING_PROVIDER_BALANCE,
        "state_sha256": V1_CYCLE_ONE_STATE_SHA256,
        "seals": [
            {
                "stage": "unscorable",
                "sha256": V1_CYCLE_ONE_SEAL_SHA256,
            }
        ],
    }
    if value.get("cycles") != [expected_cycle]:
        raise ParallelStrategyActivationError(
            "owner-aborted-v1 cycle-one replay differs"
        )
    cycle_state = source.parent / "cycles/cycle-01/state.json"
    cycle_seal = source.parent / "cycles/cycle-01/seals/unscorable.json"
    if (
        _sha256(cycle_state, label="cohort cycle-one state")
        != V1_CYCLE_ONE_STATE_SHA256
        or _sha256(cycle_seal, label="cohort cycle-one terminal seal")
        != V1_CYCLE_ONE_SEAL_SHA256
    ):
        raise ParallelStrategyActivationError(
            "owner-aborted-v1 cycle-one source binding differs"
        )
    replay = {
        "terminal_cycles": 1,
        "authenticated_attempts": 2,
        "billable_credits": 1,
        "cycles": [expected_cycle],
    }
    replay_sha256 = hashlib.sha256(canonical_json_bytes(replay)).hexdigest()
    if value.get("operational_replay_sha256") != replay_sha256:
        raise ParallelStrategyActivationError(
            "owner-aborted-v1 operational replay hash differs"
        )
    return value, hashlib.sha256(raw).hexdigest()


def _validate_operator_attestation(
    program: ParallelStrategyProgram,
) -> tuple[dict[str, Any], str]:
    path = program.root / OPERATOR_ATTESTATION_PATH
    value, raw = _read(path, label="operator-stop attestation")
    expected_units = {
        unit: {
            "active_state": "inactive",
            "unit_file_state": "disabled",
        }
        for unit in LEGACY_UNITS
    }
    if (
        set(value) != {"schema_version", "kind", "program_id", "recorded_at", "units"}
        or value.get("schema_version") != 1
        or value.get("kind") != "legacy-automation-stop-attestation-v1"
        or value.get("program_id") != PROGRAM_ID
        or value.get("units") != expected_units
    ):
        raise ParallelStrategyActivationError(
            "operator-stop attestation is not exact inactive/disabled proof"
        )
    _timestamp(value.get("recorded_at"), field="operator-stop recorded_at")
    return value, hashlib.sha256(raw).hexdigest()


def operational_reconciliation(
    program_or_manifest: ParallelStrategyProgram | Path,
) -> dict[str, Any]:
    """Build the exact operational-only A/A2/owner-aborted-v1 balance chain."""

    program = _resolve_program(program_or_manifest)
    owner, owner_sha256 = _validate_owner_attestation(program)
    operator, operator_sha256 = _validate_operator_attestation(program)
    if operator["recorded_at"] != owner["recorded_at"]:
        raise ParallelStrategyActivationError(
            "owner-abort and operator-stop proofs are not one activation event"
        )
    ledgers: list[dict[str, Any]] = []
    for predecessor in PREDECESSORS:
        path = program.repo_root / predecessor["relative_path"]
        digest = _sha256(path, label=f"{predecessor['program_id']} final seal")
        seal, _ = _read(path, label=f"{predecessor['program_id']} final seal")
        if (
            digest != predecessor["sha256"]
            or seal.get("stage") != "unscorable"
            or seal.get("authenticated_attempts") != predecessor["attempts"]
            or seal.get("billable_credits") != predecessor["credits"]
        ):
            raise ParallelStrategyActivationError(
                f"operational predecessor differs: {predecessor['program_id']}"
            )
        ledgers.append(
            {
                "program_id": predecessor["program_id"],
                "terminal_stage": "unscorable",
                "operational_ledger_sha256": digest,
                "confirmed_spend_credits": predecessor[
                    "confirmed_after_snapshot"
                ],
                "reserved_spend_candidates": predecessor[
                    "reserved_after_snapshot"
                ],
            }
        )
    ledgers.append(
        {
            "program_id": V1_PROGRAM_ID,
            "terminal_stage": "owner_aborted",
            "operational_ledger_sha256": owner["operational_replay_sha256"],
            # The only stopped-v1 credit is cycle one's screener, which the
            # 50,063 opening snapshot already reflects.
            "confirmed_spend_credits": 0,
            "reserved_spend_candidates": [0],
        }
    )
    document = {
        "schema_version": 1,
        "kind": RECONCILIATION_KIND,
        "opening_balance_candidates": [OPENING_PROVIDER_BALANCE],
        "owner_aborted_v1_attestation": {
            "path": OWNER_ATTESTATION_PATH,
            "sha256": owner_sha256,
            "operational_replay_sha256": owner["operational_replay_sha256"],
        },
        "operator_stop_attestation": {
            "path": OPERATOR_ATTESTATION_PATH,
            "sha256": operator_sha256,
        },
        "operational_ledgers": ledgers,
    }
    reconstructed = reconstruct_operational_balances(document)
    if reconstructed != (44_702, 44_703) or reconstructed[0] < MINIMUM_FIRST_BASELINE:
        raise ParallelStrategyActivationError(
            "operational reconciliation cannot fund the frozen rapid program"
        )
    return document


def seal_operational_reconciliation(
    program_or_manifest: ParallelStrategyProgram | Path,
) -> Path:
    program = _resolve_program(program_or_manifest)
    path = program.root / "activation/operational-reconciliation.json"
    content = canonical_json_bytes(operational_reconciliation(program))
    try:
        return atomic_write_once(path, content)
    except ParallelStrategySchemaError as exc:
        raise ParallelStrategyActivationError(
            "existing operational reconciliation differs"
        ) from exc


__all__ = [
    "LEGACY_UNITS",
    "OPENING_PROVIDER_BALANCE",
    "ParallelStrategyActivationError",
    "V1_PROGRAM_SHA256",
    "operational_reconciliation",
    "seal_operational_reconciliation",
]
