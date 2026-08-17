from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

import src.nansen_signal_lab.budget as budget_module
from src.nansen_signal_lab.artifacts import (
    canonical_json_bytes,
    write_bytes_once,
    write_json_once,
)
from src.nansen_signal_lab.budget import (
    BudgetCorruption,
    BudgetError,
    BudgetGuard,
    canonical_request_sha256,
)
from src.nansen_signal_lab.client import NansenEvidenceResponse, NansenRequestFailure


def evidence(
    *,
    status: int = 200,
    cost: int | None = 1,
    used: int | None = 1,
    remaining: int | None = 9,
    errors: tuple[str, ...] = (),
    retrieved_at: str = "2026-08-17T10:00:00Z",
    retry_after: str | None = "30",
) -> NansenEvidenceResponse:
    raw = b'{"data":[]}'
    headers = {}
    for name, value in (
        ("X-Nansen-Credits-Cost", cost),
        ("X-Nansen-Credits-Used", used),
        ("X-Nansen-Credits-Remaining", remaining),
    ):
        if value is not None:
            headers[name] = str(value)
    if status == 429 and retry_after is not None:
        headers["Retry-After"] = retry_after
    return NansenEvidenceResponse(
        body={"data": []},
        body_parse_status="json_object",
        raw_body=raw,
        status_code=status,
        request_started_at="2026-08-17T09:59:59Z",
        response_retrieved_at=retrieved_at,
        response_headers=headers,
        request_id="nansen-1",
        credit_cost=cost,
        credit_used=used,
        credit_remaining=remaining,
        credit_header_errors=errors,
    )


def request_hash(index: int) -> str:
    return hashlib.sha256(f"request-{index}".encode()).hexdigest()


def response_artifact(
    guard: BudgetGuard,
    reservation,
    response: NansenEvidenceResponse,
) -> str:
    path = (
        guard.root
        / "raw"
        / "nansen"
        / reservation.reservation_id
        / f"attempt-{reservation.attempt_count}-response.json"
    )
    write_bytes_once(path, response.raw_body)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def establish_account_baseline(guard: BudgetGuard, remaining: int = 10) -> None:
    reservation = guard.reserve("account", request_hash(0), "account", 1)
    response = evidence(cost=0, used=0, remaining=remaining)
    guard.confirm(
        reservation,
        response,
        response_artifact_sha256=response_artifact(guard, reservation, response),
    )


def test_initial_head_is_exact_canonical_budget_document(tmp_path):
    BudgetGuard(tmp_path)

    assert (tmp_path / "budget" / "head.json").read_bytes() == (
        b'{"entries":[],"journal_head_sha256":null,"max_calls":10,'
        b'"max_credits":10,"schema_version":1}\n'
    )


def test_reserve_is_persistent_idempotent_and_rejects_changed_identity(tmp_path):
    guard = BudgetGuard(tmp_path)
    first = guard.reserve("screen", request_hash(1), "token-screener", 1)
    repeated = BudgetGuard(tmp_path).reserve(
        "screen", request_hash(1), "token-screener", 1
    )

    assert repeated == first
    assert first.attempt_count == 1
    assert first.state == "reserved"
    assert guard.replay().calls == 1
    assert guard.replay().credits == 1
    assert len(list((tmp_path / "budget" / "journal").glob("*.json"))) == 1

    with pytest.raises(BudgetCorruption):
        guard.reserve("screen", request_hash(2), "token-screener", 1)
    with pytest.raises(BudgetCorruption):
        guard.reserve("screen", request_hash(1), "tgm/flows", 1)


def test_confirmed_zero_releases_and_positive_use_consumes_exact_totals(tmp_path):
    guard = BudgetGuard(tmp_path)
    establish_account_baseline(guard, remaining=10)
    assert (guard.replay().calls, guard.replay().credits) == (0, 0)

    reservation = guard.reserve("screen", request_hash(1), "token-screener", 1)
    response = evidence(cost=1, used=1, remaining=9)
    response_sha256 = response_artifact(guard, reservation, response)
    guard.confirm(
        reservation,
        response,
        response_artifact_sha256=response_sha256,
    )

    totals = guard.replay()
    assert (totals.calls, totals.credits, totals.provider_remaining) == (1, 1, 9)
    assert totals.entries[-1].state == "confirmed_used"
    assert totals.entries[-1].response_artifact_sha256 == response_sha256


def test_confirmed_billable_zero_releases_both_totals(tmp_path):
    guard = BudgetGuard(tmp_path)
    establish_account_baseline(guard)
    reservation = guard.reserve("screen", request_hash(1), "token-screener", 1)
    response = evidence(cost=1, used=0, remaining=10)
    guard.confirm(
        reservation,
        response,
        response_artifact_sha256=response_artifact(guard, reservation, response),
    )

    assert (guard.replay().calls, guard.replay().credits) == (0, 0)
    assert guard.replay().entries[-1].state == "confirmed_zero"


def test_rejects_eleventh_call_and_reservation_above_ten_credits(tmp_path):
    call_guard = BudgetGuard(tmp_path / "calls")
    for index in range(10):
        call_guard.reserve(f"call-{index}", request_hash(index), "tgm/flows", 1)
    with pytest.raises(BudgetError, match="call ceiling"):
        call_guard.reserve("call-10", request_hash(10), "tgm/flows", 0)

    credit_guard = BudgetGuard(tmp_path / "credits")
    credit_guard.reserve("large", request_hash(20), "tgm/flows", 10)
    with pytest.raises(BudgetError, match="credit ceiling"):
        credit_guard.reserve("extra", request_hash(21), "tgm/flows", 1)


def test_fail_releases_zero_consumes_positive_and_conserves_ambiguous(tmp_path):
    zero_guard = BudgetGuard(tmp_path / "zero")
    zero = zero_guard.reserve("zero", request_hash(1), "tgm/flows", 1)
    zero_failure = NansenRequestFailure(
        "Nansen HTTP 429", transmitted=True, response=evidence(status=429, cost=1, used=0, remaining=10)
    )
    zero_guard.fail(
        zero,
        zero_failure,
        failure_artifact_sha256=response_artifact(zero_guard, zero, zero_failure.response),
    )
    assert (zero_guard.replay().calls, zero_guard.replay().credits) == (0, 0)
    assert zero_guard.replay().entries[0].state == "failed_before_pricing"

    used_guard = BudgetGuard(tmp_path / "used")
    used = used_guard.reserve("used", request_hash(2), "tgm/flows", 1)
    used_failure = NansenRequestFailure(
        "Nansen HTTP 500", transmitted=True, response=evidence(status=500, cost=1, used=2, remaining=8)
    )
    used_guard.fail(
        used,
        used_failure,
        failure_artifact_sha256=response_artifact(used_guard, used, used_failure.response),
    )
    assert (used_guard.replay().calls, used_guard.replay().credits) == (1, 2)
    assert used_guard.replay().entries[0].state == "confirmed_used"

    ambiguous_guard = BudgetGuard(tmp_path / "ambiguous")
    ambiguous = ambiguous_guard.reserve("ambiguous", request_hash(3), "tgm/flows", 1)
    ambiguous_guard.fail(
        ambiguous,
        NansenRequestFailure("timeout", transmitted=True),
        failure_artifact_sha256=None,
    )
    assert (ambiguous_guard.replay().calls, ambiguous_guard.replay().credits) == (1, 1)
    assert ambiguous_guard.replay().entries[0].state == "ambiguous"
    with pytest.raises(BudgetError, match="halted"):
        ambiguous_guard.reserve("later", request_hash(4), "tgm/flows", 1)


def test_retryable_zero_is_persisted_once_and_deadline_is_enforced(tmp_path):
    guard = BudgetGuard(tmp_path)
    reservation = guard.reserve("screen", request_hash(1), "token-screener", 1)
    failure = NansenRequestFailure(
        "Nansen HTTP 429",
        transmitted=True,
        response=evidence(status=429, cost=1, used=0, remaining=10),
    )
    deadline = datetime(2026, 8, 17, 10, 0, 30, tzinfo=timezone.utc)
    failure_sha256 = response_artifact(guard, reservation, failure.response)
    guard.mark_retryable_zero(
        reservation,
        failure,
        failure_artifact_sha256=failure_sha256,
        retry_not_before=deadline,
    )

    persisted = BudgetGuard(tmp_path).replay().entries[0]
    assert persisted.state == "retryable_zero"
    assert persisted.retry_not_before == "2026-08-17T10:00:30Z"
    assert (guard.replay().calls, guard.replay().credits) == (1, 1)
    with pytest.raises(BudgetError, match="deadline"):
        guard.begin_retry(
            persisted,
            now=datetime(2026, 8, 17, 10, 0, 29, tzinfo=timezone.utc),
        )

    retried = guard.begin_retry(persisted, now=deadline)
    assert retried.state == "reserved"
    assert retried.attempt_count == 2
    guard.fail(
        retried,
        NansenRequestFailure("timeout", transmitted=True),
        failure_artifact_sha256=None,
    )
    with pytest.raises(BudgetError):
        guard.begin_retry(retried, now=deadline + timedelta(seconds=1))


@pytest.mark.parametrize("seconds", [-1, 61])
def test_retry_deadline_must_be_after_response_and_within_sixty_seconds(tmp_path, seconds):
    guard = BudgetGuard(tmp_path)
    reservation = guard.reserve("screen", request_hash(1), "token-screener", 1)
    failure = NansenRequestFailure(
        "Nansen HTTP 429",
        transmitted=True,
        response=evidence(status=429, cost=1, used=0, remaining=10),
    )
    failure_sha256 = response_artifact(guard, reservation, failure.response)

    with pytest.raises(BudgetError, match="retry deadline"):
        guard.mark_retryable_zero(
            reservation,
            failure,
            failure_artifact_sha256=failure_sha256,
            retry_not_before=datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
            + timedelta(seconds=seconds),
        )


@pytest.mark.parametrize("retry_after", [None, "not-an-integer", "31", "-1", "61"])
def test_retry_deadline_requires_matching_integer_retry_after(tmp_path, retry_after):
    guard = BudgetGuard(tmp_path)
    reservation = guard.reserve("screen", request_hash(1), "token-screener", 1)
    response = evidence(
        status=429,
        cost=1,
        used=0,
        remaining=10,
        retry_after=retry_after,
    )
    failure = NansenRequestFailure(
        "Nansen HTTP 429", transmitted=True, response=response
    )

    with pytest.raises(BudgetError, match="Retry-After"):
        guard.mark_retryable_zero(
            reservation,
            failure,
            failure_artifact_sha256=response_artifact(
                guard, reservation, response
            ),
            retry_not_before=datetime(
                2026, 8, 17, 10, 0, 30, tzinfo=timezone.utc
            ),
        )


def test_begin_retry_refuses_a_persistently_halted_ledger(tmp_path):
    guard = BudgetGuard(tmp_path)
    retry = guard.reserve("retry", request_hash(1), "token-screener", 1)
    doomed = guard.reserve("doomed", request_hash(2), "tgm/flows", 1)
    response = evidence(status=429, cost=1, used=0, remaining=10)
    failure = NansenRequestFailure(
        "Nansen HTTP 429", transmitted=True, response=response
    )
    guard.mark_retryable_zero(
        retry,
        failure,
        failure_artifact_sha256=response_artifact(guard, retry, response),
        retry_not_before=datetime(2026, 8, 17, 10, 0, 30, tzinfo=timezone.utc),
    )
    persisted_retry = guard.replay().entries[0]
    guard.fail(
        doomed,
        NansenRequestFailure("timeout", transmitted=True),
        failure_artifact_sha256=None,
    )

    with pytest.raises(BudgetError, match="halted"):
        guard.begin_retry(
            persisted_retry,
            now=datetime(2026, 8, 17, 10, 0, 30, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize("operation", ["confirm", "fail", "retryable_zero"])
def test_received_response_settlement_rejects_a_missing_artifact(tmp_path, operation):
    guard = BudgetGuard(tmp_path)
    endpoint = "account" if operation == "confirm" else "token-screener"
    reservation = guard.reserve("request", request_hash(1), endpoint, 1)
    response = evidence(
        status=429 if operation == "retryable_zero" else 500,
        cost=0 if operation == "confirm" else 1,
        used=0,
        remaining=10,
    )
    missing_sha256 = hashlib.sha256(b"missing response artifact").hexdigest()

    with pytest.raises(BudgetError, match="artifact"):
        if operation == "confirm":
            guard.confirm(
                reservation,
                response,
                response_artifact_sha256=missing_sha256,
            )
        elif operation == "fail":
            guard.fail(
                reservation,
                NansenRequestFailure(
                    "Nansen HTTP 500", transmitted=True, response=response
                ),
                failure_artifact_sha256=missing_sha256,
            )
        else:
            guard.mark_retryable_zero(
                reservation,
                NansenRequestFailure(
                    "Nansen HTTP 429", transmitted=True, response=response
                ),
                failure_artifact_sha256=missing_sha256,
                retry_not_before=datetime(
                    2026, 8, 17, 10, 0, 30, tzinfo=timezone.utc
                ),
            )


def test_confirm_rejects_a_response_artifact_hash_mismatch(tmp_path):
    guard = BudgetGuard(tmp_path)
    reservation = guard.reserve("account", request_hash(1), "account", 1)
    response = evidence(cost=0, used=0, remaining=10)
    path = (
        tmp_path
        / "raw"
        / "nansen"
        / reservation.reservation_id
        / "attempt-1-response.json"
    )
    write_bytes_once(path, b"wrong installed bytes")

    with pytest.raises(BudgetError, match="artifact"):
        guard.confirm(
            reservation,
            response,
            response_artifact_sha256=hashlib.sha256(response.raw_body).hexdigest(),
        )


@pytest.mark.parametrize("defect", ["fabricated_identity", "extra_key", "noncanonical"])
def test_bind_request_artifact_requires_exact_canonical_identity(tmp_path, defect):
    guard = BudgetGuard(tmp_path)
    method = "POST"
    endpoint = "token-screener"
    payload = {"chains": ["base"]}
    identity_sha256 = canonical_request_sha256(method, endpoint, payload)
    reservation_sha256 = request_hash(9) if defect == "fabricated_identity" else identity_sha256
    reservation = guard.reserve("screen", reservation_sha256, endpoint, 1)
    document = {
        "method": method,
        "endpoint": endpoint,
        "payload": payload,
        "request_sha256": reservation_sha256,
        "caller_request_id": "screen",
        "request_started_at": "2026-08-17T10:00:00Z",
        "artifact_written_at": "2026-08-17T10:00:00Z",
        "transmission_may_begin": True,
    }
    if defect == "extra_key":
        document["unsealed"] = True
    content = canonical_json_bytes(document)
    if defect == "noncanonical":
        content = (json.dumps(document, indent=2) + "\n").encode()
    path = (
        tmp_path
        / "raw"
        / "nansen"
        / reservation.reservation_id
        / "attempt-1-request.json"
    )
    write_bytes_once(path, content)

    with pytest.raises(BudgetError, match="request artifact"):
        guard.bind_request_artifact(
            reservation, hashlib.sha256(path.read_bytes()).hexdigest()
        )


def test_reconcile_request_artifact_without_response_to_ambiguous(tmp_path):
    guard = BudgetGuard(tmp_path)
    identity_sha256 = canonical_request_sha256("POST", "token-screener", {})
    reservation = guard.reserve("screen", identity_sha256, "token-screener", 1)
    request_path = (
        tmp_path
        / "raw"
        / "nansen"
        / reservation.reservation_id
        / "attempt-1-request.json"
    )
    request_path.parent.mkdir(parents=True)
    write_json_once(
        request_path,
        {
            "method": "POST",
            "endpoint": "token-screener",
            "payload": {},
            "request_sha256": identity_sha256,
            "caller_request_id": "screen",
            "request_started_at": "2026-08-17T10:00:00Z",
            "artifact_written_at": "2026-08-17T10:00:00Z",
            "transmission_may_begin": True,
        },
    )
    reservation = guard.bind_request_artifact(
        reservation, hashlib.sha256(request_path.read_bytes()).hexdigest()
    )

    BudgetGuard(tmp_path).reconcile_inflight()

    entry = guard.replay().entries[0]
    assert entry.state == "ambiguous"
    assert (guard.replay().calls, guard.replay().credits) == (1, 1)


def test_bind_request_artifact_accepts_and_requires_durable_write_timestamp(tmp_path):
    guard = BudgetGuard(tmp_path)
    identity = canonical_request_sha256("POST", "token-screener", {})
    reservation = guard.reserve("screen", identity, "token-screener", 1)
    path = tmp_path / "raw/nansen" / reservation.reservation_id / "attempt-1-request.json"
    write_json_once(path, {
        "method": "POST",
        "endpoint": "token-screener",
        "payload": {},
        "request_sha256": identity,
        "caller_request_id": "screen",
        "request_started_at": "2026-08-17T10:00:00Z",
        "artifact_written_at": "2026-08-17T10:00:01Z",
        "transmission_may_begin": True,
    })
    bound = guard.bind_request_artifact(
        reservation, hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert bound.request_artifact_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_journal_is_hash_linked_and_replay_rebuilds_an_exact_prefix_head(tmp_path):
    guard = BudgetGuard(tmp_path)
    guard.reserve("one", request_hash(1), "tgm/flows", 1)
    guard.reserve("two", request_hash(2), "tgm/flows", 1)
    journal_paths = sorted((tmp_path / "budget" / "journal").glob("*.json"))
    first = json.loads(journal_paths[0].read_text())
    second = json.loads(journal_paths[1].read_text())
    first_hash = hashlib.sha256(journal_paths[0].read_bytes()).hexdigest()
    second_hash = hashlib.sha256(journal_paths[1].read_bytes()).hexdigest()

    assert first["previous_transition_sha256"] is None
    assert second["previous_transition_sha256"] == first_hash
    assert journal_paths[0].name == f"000001-{first_hash}.json"
    assert journal_paths[1].name == f"000002-{second_hash}.json"

    prefix_head = dict(json.loads((tmp_path / "budget" / "head.json").read_text()))
    prefix_head["entries"] = prefix_head["entries"][:1]
    prefix_head["journal_head_sha256"] = first_hash
    (tmp_path / "budget" / "head.json").write_bytes(canonical_json_bytes(prefix_head))

    totals = BudgetGuard(tmp_path).replay()

    assert (totals.calls, totals.credits) == (2, 2)
    assert json.loads((tmp_path / "budget" / "head.json").read_text())["journal_head_sha256"] == second_hash


def test_replay_rejects_divergent_head_and_missing_journal_sequence(tmp_path):
    divergent_root = tmp_path / "divergent"
    divergent = BudgetGuard(divergent_root)
    divergent.reserve("one", request_hash(1), "tgm/flows", 1)
    head_path = divergent_root / "budget" / "head.json"
    head = json.loads(head_path.read_text())
    head["entries"][0]["endpoint"] = "changed"
    head_path.write_bytes(canonical_json_bytes(head))
    with pytest.raises(BudgetCorruption, match="head"):
        divergent.replay()

    missing_root = tmp_path / "missing"
    missing = BudgetGuard(missing_root)
    missing.reserve("one", request_hash(1), "tgm/flows", 1)
    missing.reserve("two", request_hash(2), "tgm/flows", 1)
    second = sorted((missing_root / "budget" / "journal").glob("*.json"))[1]
    second.rename(second.with_name(second.name.replace("000002", "000003")))
    with pytest.raises(BudgetCorruption, match="sequence"):
        missing.replay()


def test_settlement_does_not_change_earlier_snapshot_bytes(tmp_path):
    guard = BudgetGuard(tmp_path)
    establish_account_baseline(guard)
    reservation = guard.reserve("screen", request_hash(1), "token-screener", 1)
    snapshot = guard.snapshot("decision_sealed", recorded_at="2026-08-17T10:00:00Z")
    before = snapshot.read_bytes()

    response = evidence(cost=1, used=1, remaining=9)
    guard.confirm(
        reservation,
        response,
        response_artifact_sha256=response_artifact(guard, reservation, response),
    )

    assert snapshot.read_bytes() == before
    document = json.loads(before)
    assert document["stage"] == "decision_sealed"
    assert document["totals"] == {"calls": 1, "credits": 1}
    assert document["journal_head_sha256"] == document["transition_sha256s"][-1]


@pytest.mark.parametrize("operation", ["reserve", "confirm"])
def test_crash_after_journal_install_rebuilds_head_without_duplicate_transition(
    tmp_path, monkeypatch, operation
):
    guard = BudgetGuard(tmp_path)
    if operation == "confirm":
        establish_account_baseline(guard)
        reservation = guard.reserve("screen", request_hash(1), "token-screener", 1)
        response = evidence(cost=1, used=1, remaining=9)
        response_sha256 = response_artifact(guard, reservation, response)
    operations_before = [
        json.loads(path.read_text())["operation"]
        for path in sorted((tmp_path / "budget" / "journal").glob("*.json"))
    ]
    real_replace = budget_module.atomic_replace_bytes

    def crash(path, content):
        raise RuntimeError("injected head crash")

    monkeypatch.setattr(budget_module, "atomic_replace_bytes", crash)
    with pytest.raises(RuntimeError, match="injected head crash"):
        if operation == "reserve":
            guard.reserve("screen", request_hash(1), "token-screener", 1)
        else:
            guard.confirm(
                reservation,
                response,
                response_artifact_sha256=response_sha256,
            )
    monkeypatch.setattr(budget_module, "atomic_replace_bytes", real_replace)

    recovered = BudgetGuard(tmp_path).replay()

    assert (recovered.calls, recovered.credits) == (1, 1)
    operations = [
        json.loads(path.read_text())["operation"]
        for path in sorted((tmp_path / "budget" / "journal").glob("*.json"))
    ]
    assert operations.count(operation) == operations_before.count(operation) + 1


@pytest.mark.parametrize(
    ("response", "state"),
    [
        (evidence(cost=None, used=1, remaining=9), "ambiguous"),
        (
            evidence(
                cost=1,
                used=None,
                remaining=9,
                errors=("X-Nansen-Credits-Used",),
            ),
            "ambiguous",
        ),
        (evidence(cost=1, used=1, remaining=8), "ambiguous"),
        (evidence(cost=2, used=1, remaining=9), "confirmed_used"),
    ],
)
def test_pricing_drift_is_ledgered_with_evidence_and_halts_next_request(
    tmp_path, response, state
):
    guard = BudgetGuard(tmp_path)
    establish_account_baseline(guard)
    reservation = guard.reserve("screen", request_hash(1), "token-screener", 1)
    response_sha256 = response_artifact(guard, reservation, response)

    with pytest.raises(BudgetError, match="pricing"):
        guard.confirm(
            reservation,
            response,
            response_artifact_sha256=response_sha256,
        )

    entry = guard.replay().entries[-1]
    assert entry.state == state
    assert entry.response_artifact_sha256 == response_sha256
    with pytest.raises(BudgetError, match="halted"):
        guard.reserve("later", request_hash(2), "tgm/flows", 1)


def test_actual_used_credits_above_ceiling_are_persisted_then_halt(tmp_path):
    guard = BudgetGuard(tmp_path)
    establish_account_baseline(guard, remaining=20)
    remaining = 20
    for index in range(1, 10):
        reservation = guard.reserve(f"call-{index}", request_hash(index), "tgm/flows", 1)
        remaining -= 1
        response = evidence(cost=1, used=1, remaining=remaining)
        guard.confirm(
            reservation,
            response,
            response_artifact_sha256=response_artifact(guard, reservation, response),
        )
    final = guard.reserve("call-10", request_hash(10), "tgm/flows", 1)
    response = evidence(cost=1, used=2, remaining=9)

    with pytest.raises(BudgetError, match="credit ceiling"):
        guard.confirm(
            final,
            response,
            response_artifact_sha256=response_artifact(guard, final, response),
        )

    assert guard.replay().credits == 11
    assert guard.replay().entries[-1].state == "confirmed_used"
    with pytest.raises(BudgetError, match="halted"):
        guard.reserve("call-11", request_hash(11), "tgm/flows", 1)
