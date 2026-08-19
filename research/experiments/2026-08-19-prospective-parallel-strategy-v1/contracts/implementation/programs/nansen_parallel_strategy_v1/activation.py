from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .budget import (
    MINIMUM_FIRST_BASELINE,
    RECONCILIATION_KIND,
    reconstruct_operational_balances,
)
from .runtime import require_terminal_v1_activation, validate_terminal_v1_attestation
from .schema import _write_once, canonical_json_bytes


class ParallelStrategyActivationError(RuntimeError):
    """Raised when predecessor operational accounting cannot be sealed exactly."""


OPENING_PROVIDER_BALANCE = 50_063
# This balance was proved after cohort-v1 cycle 1 and immediately before
# Program A.  Do not subtract pre-snapshot work or cohort cycle 1 again.
SNAPSHOT_V1_CYCLE_ONE_CREDITS = 1
PREDECESSORS = (
    {
        "program_id": "2026-08-18-historical-theory-discovery-a-v1",
        "relative_path": "research/experiments/2026-08-18-historical-theory-discovery-a-v1/seals/final.json",
        "sha256": "3132f1bfaa5e99d535bd6ded819f9751a44bede2f6dbf0bf60689cd0c9c49230",
        "attempts": 135,
        "credits": 537,
        # A's final 537 is conservative: 532 were confirmed and its five-credit
        # ambiguity was later resolved as uncharged by A2's 49,531 baseline.
        "confirmed_after_snapshot": 532,
        "reserved_after_snapshot": [0],
    },
    {
        "program_id": "2026-08-18-historical-theory-discovery-a2-v1",
        "relative_path": "research/experiments/2026-08-18-historical-theory-discovery-a2-v1/seals/final.json",
        "sha256": "5f58ce65563be7f4ab909b3b00fa2bbac5eba590d9b534f8216b14043181d230",
        "attempts": 1_219,
        "credits": 4_829,
        # A2 proved 4,828 settled credits.  Its terminal HTTP 500 advertised a
        # one-credit cost without used/remaining, so retain both possibilities.
        "confirmed_after_snapshot": 4_828,
        "reserved_after_snapshot": [0, 1],
    },
)


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ParallelStrategyActivationError(f"predecessor seal is absent: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ParallelStrategyActivationError("predecessor seal is unreadable") from exc
    if not isinstance(value, dict):
        raise ParallelStrategyActivationError("predecessor seal must be an object")
    return value


def operational_reconciliation(manifest_path: Path) -> dict[str, Any]:
    """Build the exact operational-only A/A2/v1 balance chain.

    This deliberately does not open any predecessor panel, feature, decision,
    outcome, ranking, or performance artifact.
    """

    program = require_terminal_v1_activation(manifest_path)
    ledgers: list[dict[str, Any]] = []
    for predecessor in PREDECESSORS:
        path = program.repo_root / predecessor["relative_path"]
        digest = _sha256(path)
        seal = _read(path)
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
                "confirmed_spend_credits": predecessor["confirmed_after_snapshot"],
                "reserved_spend_candidates": predecessor["reserved_after_snapshot"],
            }
        )
    attestation = validate_terminal_v1_attestation(program)
    cycles = attestation.get("cycles")
    if (
        not isinstance(cycles, list)
        or not cycles
        or cycles[0].get("cycle_index") != 1
        or cycles[0].get("billable_credits") != SNAPSHOT_V1_CYCLE_ONE_CREDITS
        or attestation["billable_credits"] < SNAPSHOT_V1_CYCLE_ONE_CREDITS
    ):
        raise ParallelStrategyActivationError(
            "terminal v1 does not reproduce the balance-snapshot cycle"
        )
    ledgers.append(
        {
            "program_id": attestation["source_program_id"],
            "terminal_stage": "completed",
            "operational_ledger_sha256": attestation["operational_replay_sha256"],
            "confirmed_spend_credits": (
                attestation["billable_credits"] - SNAPSHOT_V1_CYCLE_ONE_CREDITS
            ),
            "reserved_spend_candidates": [0],
        }
    )
    document = {
        "schema_version": 1,
        "kind": RECONCILIATION_KIND,
        "opening_balance_candidates": [OPENING_PROVIDER_BALANCE],
        "operational_ledgers": ledgers,
    }
    reconstructed = reconstruct_operational_balances(document)
    # A2's terminal one-credit response remains conservatively ambiguous, so
    # two adjacent provider balances are legitimate until the first live
    # account call resolves the branch.  Every branch must independently fund
    # the frozen future authority.
    if not reconstructed or reconstructed[0] < MINIMUM_FIRST_BASELINE:
        raise ParallelStrategyActivationError(
            "operational reconciliation cannot fund the frozen program"
        )
    return document


def seal_operational_reconciliation(manifest_path: Path) -> Path:
    program = require_terminal_v1_activation(manifest_path)
    path = program.root / "activation/operational-reconciliation.json"
    content = canonical_json_bytes(operational_reconciliation(manifest_path))
    try:
        return _write_once(path, content)
    except Exception as exc:
        raise ParallelStrategyActivationError(
            "existing operational reconciliation differs"
        ) from exc
