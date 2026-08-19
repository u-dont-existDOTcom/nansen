from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from programs.nansen_parallel_strategy_v1.budget import (
    RECONCILIATION_KIND,
    REQUIRED_OPERATIONAL_PROGRAMS,
    ParallelStrategyBudget,
    reconstruct_operational_balances,
)
from programs.nansen_parallel_strategy_v1.evidence import (
    EvidenceCutoff,
    EvidenceFatal,
    EvidenceRequestFailed,
    EvidenceTransport,
)
from programs.nansen_parallel_strategy_v1.schema import OPENAPI_SOURCE_RELATIVE_PATH
from src.nansen_signal_lab.budget import BudgetGuard
from src.nansen_signal_lab.client import NansenEvidenceResponse, NansenRequestFailure


REPO_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_BYTES = (REPO_ROOT / OPENAPI_SOURCE_RELATIVE_PATH).read_bytes()
NOW = datetime(2026, 10, 15, 12, 5, 2, tzinfo=timezone.utc)


def _reconciliation() -> dict:
    return {
        "schema_version": 1,
        "kind": RECONCILIATION_KIND,
        "opening_balance_candidates": [50_064],
        "operational_ledgers": [
            {
                "program_id": program_id,
                "terminal_stage": "completed",
                "operational_ledger_sha256": f"{index + 1:064x}",
                "confirmed_spend_credits": spend,
                "reserved_spend_candidates": [0],
            }
            for index, (program_id, spend) in enumerate(
                zip(REQUIRED_OPERATIONAL_PROGRAMS, (537, 4_829, 1), strict=True)
            )
        ],
    }


def _response(
    body: dict,
    *,
    cost: int | None,
    used: int | None,
    remaining: int | None,
    status: int = 200,
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
        status_code=status,
        request_started_at="2026-10-15T12:05:00Z",
        response_retrieved_at="2026-10-15T12:05:01Z",
        response_headers=headers,
        request_id="provider-request-1",
        credit_cost=cost,
        credit_used=used,
        credit_remaining=remaining,
        credit_header_errors=(),
    )


class FakeNansen:
    def __init__(self, *results, openapi: bytes = OPENAPI_BYTES) -> None:
        self.openapi = openapi
        self.results = list(results)
        self.events: list[str] = []
        self.auth_calls = 0

    def fetch_openapi(self) -> bytes:
        self.events.append("public")
        return self.openapi

    def request_evidence(self, method, endpoint, payload, *, caller_request_id):
        self.events.append("auth")
        self.auth_calls += 1
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _transport(tmp_path, fake: FakeNansen):
    budget = ParallelStrategyBudget(tmp_path, _reconciliation())
    return budget, EvidenceTransport(tmp_path, budget, fake, lambda: NOW)


def _account_call(transport: EvidenceTransport, callback=lambda: True):
    return transport.call(
        1,
        "predecision",
        "account",
        "GET",
        "account",
        None,
        0,
        callback,
    )


def _paid_call(
    transport: EvidenceTransport,
    callback=lambda: True,
    *,
    logical: str = "holders-001",
):
    return transport.call(
        1,
        "predecision",
        logical,
        "POST",
        "profiler/address/current-balance",
        {"address": "0x0000000000000000000000000000000000000001"},
        1,
        callback,
    )


def _account_response() -> NansenEvidenceResponse:
    baseline = reconstruct_operational_balances(_reconciliation())[0]
    return _response(
        {"plan": "pro", "credits_remaining": baseline},
        cost=0,
        used=None,
        remaining=None,
    )


def _paid_response(*, status: int = 200, used: int = 1) -> NansenEvidenceResponse:
    baseline = reconstruct_operational_balances(_reconciliation())[0]
    return _response(
        {"data": [{"balance": "1"}]},
        cost=1,
        used=used,
        remaining=baseline - used,
        status=status,
    )


def test_account_and_paid_success_archive_before_settlement_and_count_every_call(tmp_path):
    fake = FakeNansen(_account_response(), _paid_response())
    budget, transport = _transport(tmp_path, fake)
    events: list[str] = []

    account, account_paths = _account_call(
        transport, lambda: events.append("cutoff")
    )
    paid, paid_paths = _paid_call(transport, lambda: events.append("cutoff"))

    assert account.body["plan"] == "pro"
    assert paid.body["data"]
    assert fake.events == ["public", "auth", "auth"]
    assert events == ["cutoff", "cutoff", "cutoff"]
    assert all(path.is_file() for path in account_paths)
    assert all(path.is_file() for path in paid_paths)
    assert budget.summary().attempts == 2
    assert budget.summary().credits == 1


def test_explicit_verify_is_consumed_by_first_call_without_second_public_fetch(tmp_path):
    fake = FakeNansen(_account_response())
    _, transport = _transport(tmp_path, fake)
    public_cutoffs = 0

    def public_allowed():
        nonlocal public_cutoffs
        public_cutoffs += 1

    path = transport.verify_openapi(1, "predecision", public_allowed)
    _account_call(transport, lambda: True)

    assert path.is_file()
    assert public_cutoffs == 1
    assert fake.events == ["public", "auth"]


def test_pre_auth_openapi_crash_refetches_and_durably_chooses_one_observation(
    tmp_path,
):
    fake = FakeNansen(_account_response())
    budget, first = _transport(tmp_path, fake)
    first.verify_openapi(1, "predecision", lambda: True)

    # Simulate process loss after the complete public observation but before
    # account reservation.  A later process obtains a new public observation,
    # chooses it durably, and performs the account call exactly once.
    resumed = EvidenceTransport(tmp_path, budget, fake, lambda: NOW)
    _account_call(resumed)
    assert fake.events == ["public", "public", "auth"]
    assert fake.auth_calls == 1

    replay = EvidenceTransport(tmp_path, budget, fake, lambda: NOW)
    chosen = replay.adopt_openapi(1, "predecision")
    assert len(chosen) == 3
    assert chosen[-1].name == "chosen-observation.json"
    assert all(path.is_file() for path in chosen)


def test_incomplete_zero_credit_openapi_observation_does_not_wedge_retry(tmp_path):
    fake = FakeNansen(_account_response())
    budget, transport = _transport(tmp_path, fake)
    root = budget.epoch_guard(1, "predecision").root / "raw/contracts"
    root.mkdir(parents=True, exist_ok=True)
    (root / "openapi-observation-001.json").write_bytes(OPENAPI_BYTES)
    # Atomic create-once may leave a fully fsynced hidden staging inode after
    # power loss; it is never an admissible observation name.
    (root / ".openapi-observation-001.json.crash-temp").write_bytes(
        OPENAPI_BYTES
    )

    _account_call(transport)
    assert fake.events == ["public", "auth"]
    assert (root / "openapi-observation-002.json").is_file()
    assert (root / "chosen-observation.json").is_file()
    assert len(transport.adopt_openapi(1, "predecision")) == 3


def test_resume_openapi_adoption_rejects_tampered_contract_before_auth(tmp_path):
    fake = FakeNansen(_account_response())
    budget, transport = _transport(tmp_path, fake)
    _account_call(transport)
    contract_root = (
        budget.epoch_guard(1, "predecision").root / "raw" / "contracts"
    )
    raw_path = next(
        path
        for path in contract_root.iterdir()
        if path.name.startswith("openapi-observation-")
        and not path.name.endswith(".metadata.json")
    )
    raw_path.write_bytes(b"{}\n")
    resumed = EvidenceTransport(tmp_path, budget, fake, lambda: NOW)

    with pytest.raises(EvidenceFatal, match="metadata is invalid or tampered"):
        resumed.adopt_openapi(1, "predecision")
    assert fake.auth_calls == 1


def test_auth_cutoff_is_checked_after_binding_and_is_terminal_without_global_halt(tmp_path):
    fake = FakeNansen(_account_response())
    budget, transport = _transport(tmp_path, fake)
    decisions = iter((True, False))

    with pytest.raises(EvidenceCutoff):
        _account_call(transport, lambda: next(decisions))

    entry = budget.epoch_guard(1, "predecision").replay().entries[0]
    assert entry.request_artifact_sha256 is not None
    assert entry.state == "failed_before_pricing"
    assert budget.summary().attempts == 1
    assert budget.summary().halted_reason is None
    assert fake.auth_calls == 0


def test_crash_after_cutoff_marker_before_budget_transition_recovers_locally(
    tmp_path, monkeypatch
):
    fake = FakeNansen(_account_response())
    budget, transport = _transport(tmp_path, fake)
    real_fail = budget.fail

    def crash_before_failure_transition(*args, **kwargs):
        raise RuntimeError("simulated crash before cutoff budget transition")

    monkeypatch.setattr(budget, "fail", crash_before_failure_transition)
    decisions = iter((True, False))
    with pytest.raises(RuntimeError, match="simulated crash"):
        _account_call(transport, lambda: next(decisions))
    entry = budget.epoch_guard(1, "predecision").replay().entries[0]
    assert entry.state == "reserved"
    assert fake.auth_calls == 0

    monkeypatch.setattr(budget, "fail", real_fail)
    resumed = EvidenceTransport(tmp_path, budget, fake, lambda: NOW)
    with pytest.raises(EvidenceCutoff, match="recovered before transmission"):
        _account_call(
            resumed,
            lambda: pytest.fail("cutoff recovery must not reach transport"),
        )
    assert budget.epoch_guard(1, "predecision").replay().entries[0].state == (
        "failed_before_pricing"
    )
    assert budget.summary().halted_reason is None
    assert fake.auth_calls == 0
    resumed = EvidenceTransport(tmp_path, budget, fake, lambda: NOW)
    with pytest.raises(EvidenceCutoff, match="durably recorded"):
        _account_call(
            resumed,
            lambda: pytest.fail("durable cutoff replay must not reach transport"),
        )
    assert fake.auth_calls == 0


def test_completed_response_is_adopted_after_crash_before_confirm(tmp_path, monkeypatch):
    fake = FakeNansen(_account_response(), _paid_response())
    budget, transport = _transport(tmp_path, fake)
    _account_call(transport)
    real_confirm = budget.confirm_paid

    def crash_after_archive(*args, **kwargs):
        raise RuntimeError("simulated process crash after archive")

    monkeypatch.setattr(budget, "confirm_paid", crash_after_archive)
    with pytest.raises(RuntimeError, match="simulated process crash"):
        _paid_call(transport)
    assert fake.auth_calls == 2
    monkeypatch.setattr(budget, "confirm_paid", real_confirm)

    adopted, paths = _paid_call(
        EvidenceTransport(tmp_path, budget, fake, lambda: NOW),
        lambda: pytest.fail("resume must not reach the transport cutoff"),
    )
    assert adopted.body["data"]
    assert all(path.is_file() for path in paths)
    assert fake.auth_calls == 2
    assert budget.summary().credits == 1


def test_exact_request_is_adopted_after_crash_before_journal_bind(tmp_path, monkeypatch):
    fake = FakeNansen(_account_response(), _paid_response())
    budget, transport = _transport(tmp_path, fake)
    _account_call(transport)
    real_bind = BudgetGuard.bind_request_artifact

    def crash_before_bind(self, reservation, request_artifact_sha256):
        if reservation.endpoint != "account":
            raise KeyboardInterrupt("simulated crash before journal bind")
        return real_bind(self, reservation, request_artifact_sha256)

    monkeypatch.setattr(BudgetGuard, "bind_request_artifact", crash_before_bind)
    with pytest.raises(KeyboardInterrupt, match="before journal bind"):
        _paid_call(transport)
    entry = budget.epoch_guard(1, "predecision").replay().entries[-1]
    assert entry.request_artifact_sha256 is None
    request_path = (
        budget.epoch_guard(1, "predecision").root
        / "raw"
        / "nansen"
        / entry.reservation_id
        / "attempt-1-request.json"
    )
    assert request_path.is_file()
    assert fake.auth_calls == 1
    monkeypatch.setattr(BudgetGuard, "bind_request_artifact", real_bind)

    response, _ = _paid_call(
        EvidenceTransport(tmp_path, budget, fake, lambda: NOW)
    )
    assert response.body["data"]
    assert fake.auth_calls == 2
    assert budget.summary().credits == 1


def test_pretransmission_failure_is_accounted_once_and_never_retried(tmp_path):
    failure = NansenRequestFailure("connect failed", transmitted=False)
    fake = FakeNansen(_account_response(), failure)
    budget, transport = _transport(tmp_path, fake)
    _account_call(transport)

    with pytest.raises(EvidenceRequestFailed, match="connect failed"):
        _paid_call(transport)
    assert budget.summary().attempts == 2
    assert budget.summary().credits == 0
    assert budget.summary().halted_reason is None
    with pytest.raises(EvidenceRequestFailed, match="already failed"):
        _paid_call(transport)
    assert fake.auth_calls == 2


def test_transmitted_without_response_globally_halts_and_never_retries(tmp_path):
    failure = NansenRequestFailure("socket lost", transmitted=True)
    fake = FakeNansen(_account_response(), failure)
    budget, transport = _transport(tmp_path, fake)
    _account_call(transport)

    with pytest.raises(EvidenceFatal, match="ambiguous pricing"):
        _paid_call(transport)
    assert budget.summary().halted_reason is not None
    assert budget.summary().credits == 1  # conservative reservation accounting
    with pytest.raises(EvidenceFatal):
        _paid_call(transport)
    assert fake.auth_calls == 2


def test_real_crash_after_transmission_is_rejected_on_resume_without_retry(tmp_path):
    fake = FakeNansen(_account_response(), KeyboardInterrupt("crash"))
    budget, transport = _transport(tmp_path, fake)
    _account_call(transport)
    with pytest.raises(KeyboardInterrupt):
        _paid_call(transport)
    assert budget.epoch_guard(1, "predecision").replay().entries[-1].state == "reserved"

    resumed = EvidenceTransport(tmp_path, budget, fake, lambda: NOW)
    with pytest.raises(EvidenceFatal, match="no response evidence"):
        _paid_call(resumed)
    assert budget.summary().halted_reason is not None
    assert fake.auth_calls == 2


def test_charged_failure_response_is_archived_then_globally_halts(tmp_path):
    failed_response = _paid_response(status=500, used=1)
    failure = NansenRequestFailure(
        "server failed", transmitted=True, response=failed_response
    )
    fake = FakeNansen(_account_response(), failure)
    budget, transport = _transport(tmp_path, fake)
    _account_call(transport)

    with pytest.raises(EvidenceFatal, match="charged"):
        _paid_call(transport)
    entry = budget.epoch_guard(1, "predecision").replay().entries[-1]
    assert entry.response_artifact_sha256 is not None
    response_path = (
        budget.epoch_guard(1, "predecision").root
        / "raw"
        / "nansen"
        / entry.reservation_id
        / "attempt-1-response.json"
    )
    assert response_path.read_bytes() == failed_response.raw_body
    assert budget.summary().halted_reason is not None


@pytest.mark.parametrize("which", ["request", "response"])
def test_completed_evidence_tamper_is_fatal_and_cannot_trigger_transport(tmp_path, which):
    fake = FakeNansen(_account_response(), _paid_response())
    budget, transport = _transport(tmp_path, fake)
    _account_call(transport)
    _, paths = _paid_call(transport)
    request_path, response_path, _ = paths[-3:]
    target = request_path if which == "request" else response_path
    target.write_bytes(target.read_bytes() + b"tamper")

    with pytest.raises(EvidenceFatal, match="tamper|differs|invalid|not JSON"):
        _paid_call(transport, lambda: pytest.fail("tamper must stop before cutoff"))
    assert fake.auth_calls == 2


def test_wrong_openapi_hash_is_archived_and_stops_before_auth(tmp_path):
    fake = FakeNansen(_account_response(), openapi=b"{}\n")
    budget, transport = _transport(tmp_path, fake)

    with pytest.raises(EvidenceFatal, match="hash differs"):
        _account_call(transport)
    assert fake.events == ["public"]
    assert not budget.epoch_guard(1, "predecision").replay().entries
    root = budget.epoch_guard(1, "predecision").root / "raw" / "contracts"
    assert (root / "openapi-observation-001.json").read_bytes() == b"{}\n"
    assert (root / "openapi-observation-001.metadata.json").is_file()
