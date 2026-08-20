from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import programs.nansen_rapid_research_v1.activation as activation
from programs.nansen_rapid_research_v1.budget import (
    LATER_PRIMARY_HOLDOUT_CREDITS,
    LATER_TEMPORAL_REPLICATION_CREDITS,
    MINIMUM_FIRST_BASELINE,
    OPERATOR_ATTESTATION_PATH,
    OWNER_ATTESTATION_PATH,
    PERMANENT_SAFETY_MARGIN_CREDITS,
    RAPID_PROGRAM_CREDITS,
    RECONCILIATION_KIND,
    OperationalReconciliationError,
    ParallelBudgetCorruption,
    ParallelBudgetError,
    RapidResearchBudget,
    reconstruct_operational_balances,
)
from programs.nansen_rapid_research_v1.design import (
    MAX_PROGRAM_ATTEMPTS,
    MAX_PROGRAM_CREDITS,
    PROGRAM_ID,
)
from programs.nansen_rapid_research_v1.schema import canonical_json_bytes
from src.nansen_signal_lab.artifacts import write_bytes_once, write_json_once
from src.nansen_signal_lab.client import NansenEvidenceResponse


REPO = Path(__file__).resolve().parents[1]


def _cycle() -> dict:
    return {
        "cycle_index": 1,
        "stage": "unscorable",
        "terminal_reason": "insufficient_strata",
        "authenticated_attempts": 2,
        "billable_credits": 1,
        "provider_remaining": 50_063,
        "state_sha256": activation.V1_CYCLE_ONE_STATE_SHA256,
        "seals": [
            {
                "stage": "unscorable",
                "sha256": activation.V1_CYCLE_ONE_SEAL_SHA256,
            }
        ],
    }


def _owner_attestation() -> dict:
    replay = {
        "terminal_cycles": 1,
        "authenticated_attempts": 2,
        "billable_credits": 1,
        "cycles": [_cycle()],
    }
    return {
        "schema_version": 1,
        "kind": "owner-aborted-v1-operational-attestation-v1",
        "program_id": PROGRAM_ID,
        "recorded_at": "2026-08-20T10:00:00Z",
        "source_program_id": activation.V1_PROGRAM_ID,
        "source_program_sha256": activation.V1_PROGRAM_SHA256,
        "status": "owner_aborted",
        **replay,
        "operational_replay_sha256": hashlib.sha256(
            canonical_json_bytes(replay)
        ).hexdigest(),
    }


def _operator_attestation() -> dict:
    return {
        "schema_version": 1,
        "kind": "legacy-automation-stop-attestation-v1",
        "program_id": PROGRAM_ID,
        "recorded_at": "2026-08-20T10:00:00Z",
        "units": {
            unit: {
                "active_state": "inactive",
                "unit_file_state": "disabled",
            }
            for unit in activation.LEGACY_UNITS
        },
    }


def _program(tmp_path: Path) -> SimpleNamespace:
    root = tmp_path / "rapid"
    (root / "activation").mkdir(parents=True)
    (root / OWNER_ATTESTATION_PATH).write_bytes(
        canonical_json_bytes(_owner_attestation())
    )
    (root / OPERATOR_ATTESTATION_PATH).write_bytes(
        canonical_json_bytes(_operator_attestation())
    )
    return SimpleNamespace(
        repo_root=REPO,
        root=root,
        manifest={
            "program_id": PROGRAM_ID,
            "activation_prerequisite": {
                "kind": "owner-aborted-v1-operational-attestation-v1",
                "program_id": activation.V1_PROGRAM_ID,
                "path": activation.V1_PROGRAM_RELATIVE_PATH,
                "program_sha256": activation.V1_PROGRAM_SHA256,
                "required_terminal_cycles": 1,
                "maximum_authenticated_attempts": 2,
                "maximum_billable_credits": 1,
            },
        },
    )


def _reconciliation() -> dict:
    return {
        "schema_version": 1,
        "kind": RECONCILIATION_KIND,
        "opening_balance_candidates": [50_063],
        "owner_aborted_v1_attestation": {
            "path": OWNER_ATTESTATION_PATH,
            "sha256": "a" * 64,
            "operational_replay_sha256": "b" * 64,
        },
        "operator_stop_attestation": {
            "path": OPERATOR_ATTESTATION_PATH,
            "sha256": "c" * 64,
        },
        "operational_ledgers": [
            {
                "program_id": "2026-08-18-historical-theory-discovery-a-v1",
                "terminal_stage": "unscorable",
                "operational_ledger_sha256": (
                    "3132f1bfaa5e99d535bd6ded819f9751a44bede2f6dbf0bf60689cd0c9c49230"
                ),
                "confirmed_spend_credits": 532,
                "reserved_spend_candidates": [0],
            },
            {
                "program_id": "2026-08-18-historical-theory-discovery-a2-v1",
                "terminal_stage": "unscorable",
                "operational_ledger_sha256": (
                    "5f58ce65563be7f4ab909b3b00fa2bbac5eba590d9b534f8216b14043181d230"
                ),
                "confirmed_spend_credits": 4_828,
                "reserved_spend_candidates": [0, 1],
            },
            {
                "program_id": activation.V1_PROGRAM_ID,
                "terminal_stage": "owner_aborted",
                "operational_ledger_sha256": "b" * 64,
                "confirmed_spend_credits": 0,
                "reserved_spend_candidates": [0],
            },
        ],
    }


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _response(
    body: dict,
    *,
    cost: int | None,
    used: int | None,
    remaining: int | None,
) -> NansenEvidenceResponse:
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    headers = {
        name: str(value)
        for name, value in (
            ("X-Nansen-Credits-Cost", cost),
            ("X-Nansen-Credits-Used", used),
            ("X-Nansen-Credits-Remaining", remaining),
        )
        if value is not None
    }
    return NansenEvidenceResponse(
        body=body,
        body_parse_status="json_object",
        raw_body=raw,
        status_code=200,
        request_started_at="2026-08-22T12:05:00Z",
        response_retrieved_at="2026-08-22T12:05:01Z",
        response_headers=headers,
        request_id="request-1",
        credit_cost=cost,
        credit_used=used,
        credit_remaining=remaining,
        credit_header_errors=(),
    )


def _account(balance: int) -> NansenEvidenceResponse:
    return _response(
        {"plan": "pro", "credits_remaining": balance},
        cost=0,
        used=None,
        remaining=None,
    )


def _artifact(guard, reservation, response: NansenEvidenceResponse) -> str:
    root = guard.root / "raw" / "nansen" / reservation.reservation_id
    response_path = root / f"attempt-{reservation.attempt_count}-response.json"
    write_bytes_once(response_path, response.raw_body)
    metadata_path = root / (
        f"attempt-{reservation.attempt_count}-response-metadata.json"
    )
    write_json_once(
        metadata_path,
        {
            "schema_version": 1,
            "attempt": reservation.attempt_count,
            "status_code": response.status_code,
            "request_started_at": response.request_started_at,
            "response_retrieved_at": response.response_retrieved_at,
            "artifact_written_at": response.response_retrieved_at,
            "response_headers": dict(response.response_headers),
            "request_id": response.request_id,
            "credit_cost": response.credit_cost,
            "credit_used": response.credit_used,
            "credit_remaining": response.credit_remaining,
            "credit_header_errors": list(response.credit_header_errors),
            "body_parse_status": response.body_parse_status,
            "response_file": response_path.name,
            "response_sha256": hashlib.sha256(response.raw_body).hexdigest(),
        },
    )
    return hashlib.sha256(metadata_path.read_bytes()).hexdigest()


def _establish(
    budget: RapidResearchBudget,
    balance: int,
    *,
    epoch: str = "predecision",
) -> None:
    reservation = budget.reserve_account(1, epoch, _hash(f"{epoch}-account"))
    response = _account(balance)
    budget.confirm_account(
        1,
        epoch,
        reservation,
        response,
        response_artifact_sha256=_artifact(
            budget.epoch_guard(1, epoch), reservation, response
        ),
    )


def test_activation_binds_exact_stopped_sources_and_balance_set(tmp_path):
    program = _program(tmp_path)

    document = activation.operational_reconciliation(program)

    assert document["opening_balance_candidates"] == [50_063]
    assert reconstruct_operational_balances(document) == (44_702, 44_703)
    assert [entry["confirmed_spend_credits"] for entry in document["operational_ledgers"]] == [
        532,
        4_828,
        0,
    ]
    assert [entry["reserved_spend_candidates"] for entry in document["operational_ledgers"]] == [
        [0],
        [0, 1],
        [0],
    ]
    assert document["owner_aborted_v1_attestation"]["sha256"] == hashlib.sha256(
        (program.root / OWNER_ATTESTATION_PATH).read_bytes()
    ).hexdigest()
    assert document["operator_stop_attestation"]["sha256"] == hashlib.sha256(
        (program.root / OPERATOR_ATTESTATION_PATH).read_bytes()
    ).hexdigest()
    assert MINIMUM_FIRST_BASELINE == (
        RAPID_PROGRAM_CREDITS
        + LATER_PRIMARY_HOLDOUT_CREDITS
        + LATER_TEMPORAL_REPLICATION_CREDITS
        + PERMANENT_SAFETY_MARGIN_CREDITS
    ) == 42_945

    sealed = activation.seal_operational_reconciliation(program)
    assert sealed.read_bytes() == canonical_json_bytes(document)
    assert activation.seal_operational_reconciliation(program) == sealed


@pytest.mark.parametrize(
    ("target", "mutation", "match"),
    [
        (
            OWNER_ATTESTATION_PATH,
            lambda value: value.update(authenticated_attempts=3),
            "identity or accounting",
        ),
        (
            OWNER_ATTESTATION_PATH,
            lambda value: value["cycles"][0].update(provider_remaining=50_064),
            "cycle-one replay",
        ),
        (
            OPERATOR_ATTESTATION_PATH,
            lambda value: value["units"][
                "nansen-signal-lab-cohort.timer"
            ].update(unit_file_state="enabled"),
            "inactive/disabled",
        ),
        (
            OPERATOR_ATTESTATION_PATH,
            lambda value: value.update(recorded_at="2026-08-20T10:00:01Z"),
            "one activation event",
        ),
    ],
)
def test_activation_rejects_tampered_abort_or_stop_proof(
    tmp_path, target, mutation, match
):
    program = _program(tmp_path)
    path = program.root / target
    value = json.loads(path.read_bytes())
    mutation(value)
    path.write_bytes(canonical_json_bytes(value))

    with pytest.raises(activation.ParallelStrategyActivationError, match=match):
        activation.operational_reconciliation(program)


def test_activation_rejects_exact_predecessor_seal_drift(
    tmp_path, monkeypatch
):
    program = _program(tmp_path)
    real_sha256 = activation._sha256

    def drift(path: Path, *, label: str) -> str:
        if "historical-theory-discovery-a-v1" in path.as_posix():
            return "0" * 64
        return real_sha256(path, label=label)

    monkeypatch.setattr(activation, "_sha256", drift)
    with pytest.raises(
        activation.ParallelStrategyActivationError,
        match="operational predecessor differs",
    ):
        activation.operational_reconciliation(program)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(opening_balance_candidates=[50_064]),
        lambda value: value["operational_ledgers"][0].update(
            confirmed_spend_credits=537
        ),
        lambda value: value["operational_ledgers"][1].update(
            reserved_spend_candidates=[0]
        ),
        lambda value: value["operational_ledgers"][2].update(
            confirmed_spend_credits=1
        ),
        lambda value: value["operational_ledgers"][2].update(
            operational_ledger_sha256="d" * 64
        ),
        lambda value: value["operator_stop_attestation"].update(
            path="../operator-stop-attestation.json"
        ),
        lambda value: value["operational_ledgers"][2].update(
            confirmed_spend_credits=False
        ),
    ],
)
def test_reconstruction_rejects_accounting_drift(mutation):
    document = _reconciliation()
    mutation(document)
    with pytest.raises(OperationalReconciliationError):
        reconstruct_operational_balances(document)


@pytest.mark.parametrize("candidate", [44_702, 44_703])
def test_budget_freezes_either_exact_first_branch_and_later_continuity(
    tmp_path, candidate
):
    budget = RapidResearchBudget(tmp_path, _reconciliation())
    _establish(budget, candidate)
    paid = budget.reserve_paid(
        1,
        "predecision",
        "screen",
        _hash("screen"),
        "token-screener",
    )
    assert budget.summary().expected_next_baseline == candidate - 1
    response = _response({"data": []}, cost=1, used=1, remaining=candidate - 1)
    budget.confirm_paid(
        1,
        "predecision",
        paid,
        response,
        response_artifact_sha256=_artifact(
            budget.epoch_guard(1, "predecision"), paid, response
        ),
    )
    _establish(budget, candidate - 1, epoch="settlement")

    totals = budget.summary()
    assert totals.frozen_first_baseline == candidate
    assert totals.expected_next_baseline == candidate - 1
    assert totals.attempts == 3
    assert totals.credits == 1
    assert totals.remaining_attempt_authority == MAX_PROGRAM_ATTEMPTS - 3
    assert totals.remaining_credit_authority == MAX_PROGRAM_CREDITS - 1


@pytest.mark.parametrize("bad_balance", [44_701, 44_704])
def test_budget_rejects_first_balance_outside_exact_two_branch_set(
    tmp_path, bad_balance
):
    budget = RapidResearchBudget(tmp_path, _reconciliation())
    reservation = budget.reserve_account(1, "predecision", _hash("account"))
    response = _account(bad_balance)

    with pytest.raises(ParallelBudgetError, match="reconstructed"):
        budget.confirm_account(
            1,
            "predecision",
            reservation,
            response,
            response_artifact_sha256=_artifact(
                budget.epoch_guard(1, "predecision"), reservation, response
            ),
        )
    assert budget.summary().halted_reason is not None


@pytest.mark.parametrize("delta", [-1, 1])
def test_budget_rejects_later_balance_discontinuity(tmp_path, delta):
    budget = RapidResearchBudget(tmp_path, _reconciliation())
    _establish(budget, 44_703)
    paid = budget.reserve_paid(
        1,
        "predecision",
        "screen",
        _hash("screen"),
        "token-screener",
    )
    response = _response({"data": []}, cost=1, used=1, remaining=44_702)
    budget.confirm_paid(
        1,
        "predecision",
        paid,
        response,
        response_artifact_sha256=_artifact(
            budget.epoch_guard(1, "predecision"), paid, response
        ),
    )
    reservation = budget.reserve_account(1, "settlement", _hash("later"))
    account = _account(44_702 + delta)

    with pytest.raises(ParallelBudgetError, match="exact later balance"):
        budget.confirm_account(
            1,
            "settlement",
            reservation,
            account,
            response_artifact_sha256=_artifact(
                budget.epoch_guard(1, "settlement"), reservation, account
            ),
        )
    assert budget.summary().halted_reason is not None


def test_budget_persists_exact_rapid_reconciliation_and_rejects_replacement(
    tmp_path,
):
    document = _reconciliation()
    budget = RapidResearchBudget(tmp_path, document)
    assert budget.reconciliation_path.read_bytes() == canonical_json_bytes(document)
    changed = copy.deepcopy(document)
    changed["operator_stop_attestation"]["sha256"] = "d" * 64

    with pytest.raises(ParallelBudgetCorruption, match="differs"):
        RapidResearchBudget(tmp_path, changed)


def test_torn_uncommitted_budget_tail_is_preserved_and_recovered(tmp_path):
    budget = RapidResearchBudget(tmp_path, _reconciliation())
    _establish(budget, 44_702)
    guard = budget.epoch_guard(1, "predecision")
    sequence = len(guard.replay().transition_sha256s) + 1
    torn = guard.journal_root / f"{sequence:06d}-{'a' * 64}.json"
    torn.write_bytes(b'{"incomplete":')

    assert budget.summary().attempts == 1
    assert not torn.exists()
    recovered = tuple(
        (guard.budget_root / "recovered-incomplete").glob(
            f"{torn.name}.*.partial"
        )
    )
    assert len(recovered) == 1
    assert recovered[0].read_bytes() == b'{"incomplete":'
