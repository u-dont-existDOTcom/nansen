from __future__ import annotations

import hashlib
import json

import pytest

from programs.nansen_parallel_strategy_v1.budget import (
    LATER_PRIMARY_HOLDOUT_CREDITS,
    LATER_TEMPORAL_REPLICATION_CREDITS,
    MINIMUM_FIRST_BASELINE,
    PERMANENT_SAFETY_MARGIN_CREDITS,
    RECONCILIATION_KIND,
    REQUIRED_OPERATIONAL_PROGRAMS,
    OperationalReconciliationError,
    ParallelBudgetError,
    ParallelStrategyBudget,
    assert_budget_ceiling,
    reconstruct_operational_balances,
)
from programs.nansen_parallel_strategy_v1.design import (
    MAX_PROGRAM_ATTEMPTS,
    MAX_PROGRAM_CREDITS,
    PREDECISION_MAX_ATTEMPTS,
    PREDECISION_MAX_CREDITS,
    SETTLEMENT_MAX_ATTEMPTS,
    SETTLEMENT_MAX_CREDITS,
)
from src.nansen_signal_lab.artifacts import write_bytes_once, write_json_once
from src.nansen_signal_lab.client import NansenEvidenceResponse, NansenRequestFailure


def _reconciliation(
    *,
    a2_alternatives: tuple[int, ...] = (0, 1),
    opening: int = 50_063,
    v1_total_credits: int = 321,
) -> dict:
    return {
        "schema_version": 1,
        "kind": RECONCILIATION_KIND,
        "opening_balance_candidates": [opening],
        "operational_ledgers": [
            {
                "program_id": program_id,
                "terminal_stage": "unscorable" if index < 2 else "completed",
                "operational_ledger_sha256": f"{index + 1:064x}",
                "confirmed_spend_credits": spend,
                "reserved_spend_candidates": (
                    list(a2_alternatives) if index == 1 else [0]
                ),
            }
            for index, (program_id, spend) in enumerate(
                zip(
                    REQUIRED_OPERATIONAL_PROGRAMS,
                    (532, 4_828, v1_total_credits - 1),
                    strict=True,
                )
            )
        ],
    }


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _response(
    *,
    body: dict | None,
    cost: int | None,
    used: int | None,
    remaining: int | None,
    status: int = 200,
    parse_status: str = "json_object",
    errors: tuple[str, ...] = (),
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
        body_parse_status=parse_status,
        raw_body=raw,
        status_code=status,
        request_started_at="2026-10-15T12:05:00Z",
        response_retrieved_at="2026-10-15T12:05:01Z",
        response_headers=headers,
        request_id="request-1",
        credit_cost=cost,
        credit_used=used,
        credit_remaining=remaining,
        credit_header_errors=errors,
    )


def _account(balance: int, *, cost: int | None = 0, used: int | None = None):
    return _response(
        body={"plan": "pro", "credits_remaining": balance},
        cost=cost,
        used=used,
        remaining=None,
    )


def _artifact(guard, reservation, response: NansenEvidenceResponse) -> str:
    root = (
        guard.root
        / "raw"
        / "nansen"
        / reservation.reservation_id
    )
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
    budget: ParallelStrategyBudget,
    balance: int,
    *,
    cycle: int = 1,
    epoch: str = "predecision",
) -> None:
    reservation = budget.reserve_account(cycle, epoch, _hash(f"{cycle}-{epoch}-account"))
    response = _account(balance)
    budget.confirm_account(
        cycle,
        epoch,
        reservation,
        response,
        response_artifact_sha256=_artifact(
            budget.epoch_guard(cycle, epoch), reservation, response
        ),
    )


def test_operational_reconciliation_is_finite_exact_and_outcome_free():
    assert reconstruct_operational_balances(
        _reconciliation(a2_alternatives=(0, 2))
    ) == (44_381, 44_383)
    forbidden = {
        "token",
        "feature",
        "outcome",
        "score",
        "ranking",
        "return",
    }
    assert not forbidden.intersection(
        json.dumps(_reconciliation()).lower().replace('"', " ").split()
    )


def test_minimum_first_baseline_is_exact_sum_of_frozen_authorities():
    assert MINIMUM_FIRST_BASELINE == (
        MAX_PROGRAM_CREDITS
        + LATER_PRIMARY_HOLDOUT_CREDITS
        + LATER_TEMPORAL_REPLICATION_CREDITS
        + PERMANENT_SAFETY_MARGIN_CREDITS
    ) == 42_945


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(extra=True),
        lambda value: value.update(opening_balance_candidates=[True]),
        lambda value: value["operational_ledgers"].reverse(),
        lambda value: value["operational_ledgers"][0].update(
            terminal_stage="preregistered"
        ),
        lambda value: value["operational_ledgers"][0].update(
            operational_ledger_sha256="bad"
        ),
        lambda value: value["operational_ledgers"][0].update(
            reserved_spend_candidates=[1, 0]
        ),
    ],
)
def test_operational_reconciliation_rejects_nonexact_schema(mutation):
    value = _reconciliation()
    mutation(value)
    with pytest.raises(OperationalReconciliationError):
        reconstruct_operational_balances(value)


@pytest.mark.parametrize(
    ("maximum_attempts", "maximum_credits"),
    [
        (PREDECISION_MAX_ATTEMPTS, PREDECISION_MAX_CREDITS),
        (SETTLEMENT_MAX_ATTEMPTS, SETTLEMENT_MAX_CREDITS),
        (MAX_PROGRAM_ATTEMPTS, MAX_PROGRAM_CREDITS),
    ],
)
def test_every_frozen_ceiling_accepts_minus_one_and_exact_but_rejects_plus_one(
    maximum_attempts, maximum_credits
):
    for attempts in (maximum_attempts - 1, maximum_attempts):
        assert_budget_ceiling(
            attempts,
            maximum_credits,
            maximum_attempts=maximum_attempts,
            maximum_credits=maximum_credits,
        )
    for credits in (maximum_credits - 1, maximum_credits):
        assert_budget_ceiling(
            maximum_attempts,
            credits,
            maximum_attempts=maximum_attempts,
            maximum_credits=maximum_credits,
        )
    with pytest.raises(ParallelBudgetError, match="attempt"):
        assert_budget_ceiling(
            maximum_attempts + 1,
            maximum_credits,
            maximum_attempts=maximum_attempts,
            maximum_credits=maximum_credits,
        )
    with pytest.raises(ParallelBudgetError, match="credit"):
        assert_budget_ceiling(
            maximum_attempts,
            maximum_credits + 1,
            maximum_attempts=maximum_attempts,
            maximum_credits=maximum_credits,
        )


def test_account_attempt_is_counted_and_first_baseline_is_frozen(tmp_path):
    budget = ParallelStrategyBudget(tmp_path, _reconciliation())
    reconstructed = reconstruct_operational_balances(_reconciliation())
    assert reconstructed == (44_382, 44_383)
    assert reconstructed[0] >= MINIMUM_FIRST_BASELINE
    _establish(budget, reconstructed[0])

    totals = budget.summary()
    assert totals.attempts == 1
    assert totals.credits == 0
    assert totals.frozen_first_baseline == 44_382
    assert totals.expected_next_baseline == 44_382
    assert totals.remaining_attempt_authority == MAX_PROGRAM_ATTEMPTS - 1
    assert totals.remaining_credit_authority == MAX_PROGRAM_CREDITS


def test_power_loss_torn_uncommitted_journal_tail_is_preserved_and_recovered(
    tmp_path,
):
    budget = ParallelStrategyBudget(tmp_path, _reconciliation())
    first = reconstruct_operational_balances(_reconciliation())[0]
    _establish(budget, first)
    guard = budget.epoch_guard(1, "predecision")
    prior = guard.replay()
    sequence = len(prior.transition_sha256s) + 1
    named_digest = "a" * 64
    torn = (
        guard.journal_root / f"{sequence:06d}-{named_digest}.json"
    )
    torn.write_bytes(b'{"incomplete":')

    totals = budget.summary()

    assert totals.attempts == 1
    assert not torn.exists()
    recovered = tuple(
        (guard.budget_root / "recovered-incomplete").glob(
            f"{torn.name}.*.partial"
        )
    )
    assert len(recovered) == 1
    assert recovered[0].read_bytes() == b'{"incomplete":'


@pytest.mark.parametrize("candidate_index", [0, 1])
def test_first_account_accepts_each_exact_a2_terminal_branch(
    tmp_path, candidate_index
):
    reconciliation = _reconciliation()
    candidates = reconstruct_operational_balances(reconciliation)
    budget = ParallelStrategyBudget(tmp_path, reconciliation)

    _establish(budget, candidates[candidate_index])

    assert budget.summary().frozen_first_baseline == candidates[candidate_index]


@pytest.mark.parametrize("delta", [-1, 2])
def test_first_account_rejects_balances_just_outside_reconstructed_set(
    tmp_path, delta
):
    budget = ParallelStrategyBudget(tmp_path, _reconciliation())
    balance = reconstruct_operational_balances(_reconciliation())[0] + delta
    reservation = budget.reserve_account(1, "predecision", _hash("account"))
    response = _account(balance)
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
    with pytest.raises(ParallelBudgetError, match="halted"):
        budget.reserve_account(2, "predecision", _hash("later"))


@pytest.mark.parametrize(
    ("reconstructed", "accepted"),
    [
        (MINIMUM_FIRST_BASELINE - 1, False),
        (MINIMUM_FIRST_BASELINE, True),
        (MINIMUM_FIRST_BASELINE + 1, True),
    ],
)
def test_first_baseline_minimum_is_exact(tmp_path, reconstructed, accepted):
    # This fixture removes A2's one-credit ambiguity.  The post-snapshot
    # confirmed chain is A=532, A2=4,828, and v1=(321-1)=320.
    reconciliation = _reconciliation(
        opening=reconstructed + 5_680,
        a2_alternatives=(0,),
    )
    budget = ParallelStrategyBudget(tmp_path, reconciliation)
    reservation = budget.reserve_account(1, "predecision", _hash("account"))
    response = _account(reconstructed)
    kwargs = {
        "response_artifact_sha256": _artifact(
            budget.epoch_guard(1, "predecision"), reservation, response
        )
    }
    if accepted:
        budget.confirm_account(1, "predecision", reservation, response, **kwargs)
        assert budget.summary().frozen_first_baseline == reconstructed
    else:
        with pytest.raises(ParallelBudgetError, match="reconstructed"):
            budget.confirm_account(1, "predecision", reservation, response, **kwargs)
        assert budget.summary().halted_reason is not None


@pytest.mark.parametrize("candidate_index", [0, 1])
def test_reserved_and_confirmed_spend_reduce_exact_later_baseline(
    tmp_path, candidate_index
):
    reconciliation = _reconciliation()
    budget = ParallelStrategyBudget(tmp_path, reconciliation)
    first = reconstruct_operational_balances(reconciliation)[candidate_index]
    _establish(budget, first)
    paid = budget.reserve_paid(
        1, "predecision", "screen", _hash("screen"), "token-screener"
    )
    assert budget.summary().expected_next_baseline == first - 1
    response = _response(body={"data": []}, cost=1, used=1, remaining=first - 1)
    budget.confirm_paid(
        1,
        "predecision",
        paid,
        response,
        response_artifact_sha256=_artifact(
            budget.epoch_guard(1, "predecision"), paid, response
        ),
    )
    _establish(budget, first - 1, epoch="settlement")
    totals = budget.summary()
    assert totals.attempts == 3
    assert totals.credits == 1
    assert totals.expected_next_baseline == first - 1


@pytest.mark.parametrize("candidate_index", [0, 1])
@pytest.mark.parametrize("delta", [-1, 1])
def test_later_account_rejects_exact_balance_plus_or_minus_one(
    tmp_path, candidate_index, delta
):
    reconciliation = _reconciliation()
    budget = ParallelStrategyBudget(tmp_path, reconciliation)
    first = reconstruct_operational_balances(reconciliation)[candidate_index]
    _establish(budget, first)
    paid = budget.reserve_paid(
        1, "predecision", "screen", _hash("screen"), "token-screener"
    )
    response = _response(body={"data": []}, cost=1, used=1, remaining=first - 1)
    budget.confirm_paid(
        1,
        "predecision",
        paid,
        response,
        response_artifact_sha256=_artifact(
            budget.epoch_guard(1, "predecision"), paid, response
        ),
    )
    reservation = budget.reserve_account(1, "settlement", _hash("settlement"))
    account = _account(first - 1 + delta)
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


@pytest.mark.parametrize(
    ("epoch", "paid_limit"),
    [("predecision", 79), ("settlement", 65)],
)
def test_epoch_attempt_and_credit_ceiling_counts_zero_cost_account(
    tmp_path, epoch, paid_limit
):
    budget = ParallelStrategyBudget(tmp_path, _reconciliation())
    first = reconstruct_operational_balances(_reconciliation())[0]
    _establish(budget, first, epoch=epoch)
    for index in range(paid_limit):
        budget.reserve_paid(
            1,
            epoch,
            f"paid-{index}",
            _hash(f"{epoch}-{index}"),
            "tgm/flows",
        )
    epoch_totals = budget.summary().epochs[0]
    assert epoch_totals.attempts == paid_limit + 1
    assert epoch_totals.credits == paid_limit
    with pytest.raises(ParallelBudgetError, match="attempt|credit"):
        budget.reserve_paid(
            1,
            epoch,
            "overflow",
            _hash(f"{epoch}-overflow"),
            "tgm/flows",
        )


def test_charged_account_globally_halts(tmp_path):
    budget = ParallelStrategyBudget(tmp_path, _reconciliation())
    first = reconstruct_operational_balances(_reconciliation())[0]
    reservation = budget.reserve_account(1, "predecision", _hash("account"))
    response = _account(first, cost=1, used=1)
    with pytest.raises(ParallelBudgetError, match="pricing"):
        budget.confirm_account(
            1,
            "predecision",
            reservation,
            response,
            response_artifact_sha256=_artifact(
                budget.epoch_guard(1, "predecision"), reservation, response
            ),
        )
    totals = budget.summary()
    assert totals.attempts == 1
    assert totals.credits == 1
    assert totals.halted_reason is not None


def test_malformed_charged_response_is_counted_then_globally_halts(tmp_path):
    budget = ParallelStrategyBudget(tmp_path, _reconciliation())
    first = reconstruct_operational_balances(_reconciliation())[0]
    _establish(budget, first)
    reservation = budget.reserve_paid(
        1, "predecision", "screen", _hash("screen"), "token-screener"
    )
    response = _response(
        body=None,
        parse_status="non_json",
        cost=1,
        used=1,
        remaining=first - 1,
    )
    with pytest.raises(ParallelBudgetError, match="malformed"):
        budget.confirm_paid(
            1,
            "predecision",
            reservation,
            response,
            response_artifact_sha256=_artifact(
                budget.epoch_guard(1, "predecision"), reservation, response
            ),
        )
    totals = budget.summary()
    assert totals.credits == 1
    assert totals.halted_reason is not None


def test_ambiguous_transmitted_failure_globally_halts_and_retains_reserve(tmp_path):
    budget = ParallelStrategyBudget(tmp_path, _reconciliation())
    first = reconstruct_operational_balances(_reconciliation())[0]
    _establish(budget, first)
    reservation = budget.reserve_paid(
        1, "predecision", "screen", _hash("screen"), "token-screener"
    )
    with pytest.raises(ParallelBudgetError, match="ambiguous"):
        budget.fail(
            1,
            "predecision",
            reservation,
            NansenRequestFailure("lost response", transmitted=True),
            failure_artifact_sha256=None,
        )
    totals = budget.summary()
    assert totals.attempts == 2
    assert totals.credits == 1
    assert totals.halted_reason is not None


def test_ambiguous_success_pricing_globally_halts_and_retains_reserve(tmp_path):
    budget = ParallelStrategyBudget(tmp_path, _reconciliation())
    first = reconstruct_operational_balances(_reconciliation())[0]
    _establish(budget, first)
    reservation = budget.reserve_paid(
        1, "predecision", "screen", _hash("screen"), "token-screener"
    )
    response = _response(
        body={"data": []},
        cost=1,
        used=None,
        remaining=None,
        errors=("missing X-Nansen-Credits-Used",),
    )
    with pytest.raises(ParallelBudgetError, match="malformed|discontinuous"):
        budget.confirm_paid(
            1,
            "predecision",
            reservation,
            response,
            response_artifact_sha256=_artifact(
                budget.epoch_guard(1, "predecision"), reservation, response
            ),
        )
    totals = budget.summary()
    assert totals.attempts == 2
    assert totals.credits == 1
    assert totals.halted_reason is not None
