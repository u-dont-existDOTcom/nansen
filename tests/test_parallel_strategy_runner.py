from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import programs.nansen_parallel_strategy_v1.runner as runner
from programs.nansen_parallel_strategy_v1.budget import (
    RECONCILIATION_KIND,
    REQUIRED_OPERATIONAL_PROGRAMS,
    ParallelStrategyBudget,
    reconstruct_operational_balances,
)
from programs.nansen_parallel_strategy_v1.design import (
    FLOW_INTELLIGENCE_FIELDS,
    PARENT_NONCASH_CANDIDATE_IDS,
    SCHEDULE,
    TOKENS_PER_CYCLE,
    identity_partition,
)
from programs.nansen_parallel_strategy_v1.evidence import (
    EvidenceTransport as DurableEvidenceTransport,
)
from programs.nansen_parallel_strategy_v1.schema import (
    OPENAPI_SOURCE_RELATIVE_PATH,
    ParallelStrategyProgram,
)
from src.nansen_signal_lab.client import NansenEvidenceResponse


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture
def activated_program(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo_root = Path(__file__).resolve().parents[1]
    program_root = tmp_path / "parallel-program"
    program_root.mkdir()
    manifest_path = program_root / "program.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    program = ParallelStrategyProgram(
        repo_root=repo_root,
        root=program_root,
        manifest_path=manifest_path,
        manifest={"stage": "activated"},
    )
    monkeypatch.setattr(runner, "require_terminal_v1_activation", lambda _: program)
    monkeypatch.setattr(runner, "provider_lock", lambda _: nullcontext())
    monkeypatch.setattr(
        runner, "operational_reconciliation", lambda _: _reconciliation()
    )
    activation_path = program.root / "activation/operational-reconciliation.json"
    activation_path.parent.mkdir(parents=True)
    activation_path.write_bytes(runner.canonical_json_bytes(_reconciliation()))
    return program, manifest_path


def _flow_body(cutoff: datetime) -> dict[str, Any]:
    boundary = cutoff.replace(minute=0, second=0, microsecond=0)
    rows = []
    for index in range(26):
        end = boundary - timedelta(hours=25 - index)
        amount = 100.0 * (1.01**index)
        rows.append(
            {
                "date": _utc_text(end - timedelta(hours=1)),
                "bucket_end": _utc_text(end),
                "is_complete": True,
                "price_usd": 10.0,
                "token_amount": amount,
                "value_usd": amount * 10.0,
                "holders_count": 100 + index,
                "total_inflows_count": 2,
                "total_outflows_count": -1,
                "total_inflows_dex": 2,
                "total_outflows_dex": -1,
            }
        )
    return {
        "data": rows,
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
    }


def _discovery_screener() -> dict[str, Any]:
    addresses: list[str] = []
    nonce = 1
    while len(addresses) < TOKENS_PER_CYCLE:
        address = f"0x{nonce:040x}"
        if identity_partition("ethereum", address) == "DISCOVERY":
            addresses.append(address)
        nonce += 1
    rows = [
        {
            "chain": "ethereum",
            "token_address": address,
            "token_symbol": f"T{index:02d}",
            "price_usd": 10.0,
            "price_change": 0.10,
            "volume": 1_000_000.0,
            "liquidity": 500_000.0,
            "market_cap_usd": 10_000_000.0,
            "token_age_days": 30.0,
            "netflow": 100_000.0 - index,
        }
        for index, address in enumerate(addresses)
    ]
    return {
        "data": rows,
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
    }


def _flow_intelligence(payload: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for field in FLOW_INTELLIGENCE_FIELDS:
        if field.endswith("wallet_count"):
            row[field] = 1
        elif field.startswith("exchange_"):
            row[field] = -10.0
        else:
            row[field] = 10.0
    return {
        "chain": payload["chain"],
        "token_address": payload["token_address"],
        "data": [row],
        "warnings": [],
    }


def _last_page() -> dict[str, Any]:
    return {
        "data": [],
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
    }


def _ohlcv(payload: dict[str, Any]) -> dict[str, Any]:
    start = datetime.fromisoformat(payload["date"]["from"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(payload["date"]["to"].replace("Z", "+00:00"))
    rows: list[dict[str, Any]] = []
    cursor = start
    while cursor <= end:
        rows.append(
            {
                "interval_start": _utc_text(cursor),
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 100.0,
            }
        )
        cursor += timedelta(minutes=5)
    return {
        "chain": payload["chain"],
        "token_address": payload["token_address"],
        "timeframe": "5m",
        "truncated": False,
        "data": rows,
    }


class OfflineTransport:
    def __init__(self, root: Path, cycle_index: int = 1, *, mutate=None):
        self.root = root
        self.cycle = SCHEDULE[cycle_index - 1]
        self.mutate = mutate
        self.logical_calls: list[tuple[str, str]] = []
        self._seen: set[tuple[str, str]] = set()
        self.openapi_checks: list[str] = []
        self.openapi = (
            Path(__file__).resolve().parents[1] / OPENAPI_SOURCE_RELATIVE_PATH
        ).read_bytes()
        self.budget = ParallelStrategyBudget(root, _reconciliation())
        self.baseline = reconstruct_operational_balances(_reconciliation())[0]
        self.spent = 0
        self._active_epoch: str | None = None
        self._active_logical_id: str | None = None
        self._delegate = DurableEvidenceTransport(
            root,
            self.budget,
            self,
            clock=self._clock,
        )

    def _clock(self) -> datetime:
        if self._active_epoch == "settlement":
            return self.cycle.scheduled_at + timedelta(hours=4, minutes=31, seconds=1)
        return self.cycle.scheduled_at + timedelta(minutes=5, seconds=1)

    def fetch_openapi(self) -> bytes:
        assert self._active_epoch is not None
        self.openapi_checks.append(self._active_epoch)
        return self.openapi

    def request_evidence(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None,
        *,
        caller_request_id: str,
    ) -> NansenEvidenceResponse:
        del method
        assert self._active_epoch is not None
        assert self._active_logical_id is not None
        key = (self._active_epoch, self._active_logical_id)
        assert key not in self._seen, f"duplicate logical provider call: {key}"
        self._seen.add(key)
        self.logical_calls.append(key)
        request = payload or {}
        if endpoint == "account":
            body = {"plan": "pro", "credits_remaining": self.baseline - self.spent}
            cost = 0
            used = None
            remaining = None
        elif endpoint == "token-screener":
            body = _discovery_screener()
            cost = used = 1
        elif endpoint == "tgm/flows":
            body = _flow_body(self.cycle.scheduled_at)
            cost = used = 1
        elif endpoint == "tgm/flow-intelligence":
            body = _flow_intelligence(request)
            cost = used = 1
        elif endpoint == "tgm/who-bought-sold":
            body = _last_page()
            cost = used = 1
        elif endpoint == "tgm/dex-trades":
            body = _last_page()
            cost = used = 1
        elif endpoint == "tgm/token-ohlcv":
            body = _ohlcv(request)
            cost = used = 1
        else:  # pragma: no cover - a new endpoint must be added deliberately
            raise AssertionError(endpoint)
        if endpoint != "account":
            self.spent += 1
            remaining = self.baseline - self.spent
        if self.mutate is not None:
            body = self.mutate(endpoint, self._active_epoch, request, body)
        retrieved_at = self._clock()
        started_at = retrieved_at - timedelta(seconds=1)
        headers = {
            name: str(value)
            for name, value in (
                ("X-Nansen-Credits-Cost", cost),
                ("X-Nansen-Credits-Used", used),
                ("X-Nansen-Credits-Remaining", remaining),
            )
            if value is not None
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        return NansenEvidenceResponse(
            body=body,
            body_parse_status="json_object",
            raw_body=raw,
            status_code=200,
            request_started_at=_utc_text(started_at),
            response_retrieved_at=_utc_text(retrieved_at),
            response_headers=headers,
            request_id=caller_request_id,
            credit_cost=cost,
            credit_used=used,
            credit_remaining=remaining,
            credit_header_errors=(),
        )

    def verify_openapi(self, cycle_index: int, epoch: str, transport_allowed) -> Path:
        assert cycle_index == self.cycle.index
        self._active_epoch = epoch
        try:
            return self._delegate.verify_openapi(
                cycle_index, epoch, transport_allowed
            )
        finally:
            self._active_epoch = None

    def adopt_openapi(self, cycle_index: int, epoch: str):
        return self._delegate.adopt_openapi(cycle_index, epoch)

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
        transport_allowed,
    ):
        assert cycle_index == self.cycle.index
        self._active_epoch = epoch
        self._active_logical_id = logical_request_id
        try:
            return self._delegate.call(
                cycle_index=cycle_index,
                epoch=epoch,
                logical_request_id=logical_request_id,
                method=method,
                endpoint=endpoint,
                payload=payload,
                expected_credits=expected_credits,
                transport_allowed=transport_allowed,
            )
        finally:
            self._active_logical_id = None
            self._active_epoch = None


class RejectCalls:
    def verify_openapi(self, *args, **kwargs):  # pragma: no cover - assertion path
        raise AssertionError("missed cycles must not touch provider transport")

    def call(self, *args, **kwargs):  # pragma: no cover - assertion path
        raise AssertionError("missed cycles must not touch provider transport")


def _reconciliation() -> dict[str, Any]:
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


class DurableOfflineNansen:
    """Offline provider whose caller IDs expose any retransmission."""

    def __init__(self, repo_root: Path, cycle_index: int = 1):
        self.cycle = SCHEDULE[cycle_index - 1]
        self.openapi = (repo_root / OPENAPI_SOURCE_RELATIVE_PATH).read_bytes()
        self.baseline = reconstruct_operational_balances(_reconciliation())[0]
        self.spent = 0
        self.auth_calls = 0
        self.public_calls = 0
        self.caller_ids: set[str] = set()

    def fetch_openapi(self) -> bytes:
        self.public_calls += 1
        return self.openapi

    def request_evidence(
        self, method, endpoint, payload, *, caller_request_id
    ) -> NansenEvidenceResponse:
        del method
        assert caller_request_id not in self.caller_ids, (
            f"authenticated request was retransmitted: {caller_request_id}"
        )
        self.caller_ids.add(caller_request_id)
        self.auth_calls += 1
        request = payload or {}
        if endpoint == "account":
            body = {"plan": "pro", "credits_remaining": self.baseline}
            cost = 0
            used = None
            remaining = None
        else:
            if endpoint == "token-screener":
                body = _discovery_screener()
            elif endpoint == "tgm/flows":
                body = _flow_body(self.cycle.scheduled_at)
            elif endpoint == "tgm/flow-intelligence":
                body = _flow_intelligence(request)
            elif endpoint == "tgm/who-bought-sold":
                body = _last_page()
            else:  # pragma: no cover - predecision has a frozen endpoint set
                raise AssertionError(endpoint)
            self.spent += 1
            cost = used = 1
            remaining = self.baseline - self.spent
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        started = self.cycle.scheduled_at + timedelta(minutes=4, seconds=59)
        retrieved = self.cycle.scheduled_at + timedelta(minutes=5)
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
            request_started_at=_utc_text(started),
            response_retrieved_at=_utc_text(retrieved),
            response_headers=headers,
            request_id=caller_request_id,
            credit_cost=cost,
            credit_used=used,
            credit_remaining=remaining,
            credit_header_errors=(),
        )


def _terminalize_cycles(program: ParallelStrategyProgram, cycles) -> None:
    for cycle in cycles:
        runner._terminalize(
            program,
            cycle,
            "offline_fixture",
            clock=lambda: datetime(2026, 8, 19, tzinfo=timezone.utc),
        )


def test_missed_start_window_terminalizes_without_transport(activated_program):
    program, manifest_path = activated_program
    cycle = SCHEDULE[0]

    state = runner.run_predecision(
        manifest_path,
        cycle.index,
        transport=RejectCalls(),
        clock=lambda: cycle.scheduled_at + timedelta(minutes=15, microseconds=1),
    )

    assert state["stage"] == "unscorable"
    assert state["terminal_reason"] == "missed_start_window"
    assert runner.check_cycle(program, cycle.index) == state


def test_orphan_terminal_seal_is_adopted_after_state_update_crash(
    activated_program, monkeypatch: pytest.MonkeyPatch
):
    program, _ = activated_program
    cycle = SCHEDULE[0]
    runner._load_state(program, cycle)
    real_replace = runner.atomic_replace_json

    def crash_on_terminal_state(path: Path, value: dict):
        if path.name == "state.json" and value.get("stage") == "unscorable":
            raise RuntimeError("simulated crash after terminal seal")
        return real_replace(path, value)

    monkeypatch.setattr(runner, "atomic_replace_json", crash_on_terminal_state)
    with pytest.raises(RuntimeError, match="after terminal seal"):
        runner._terminalize(
            program,
            cycle,
            "simulated_cutoff",
            clock=lambda: cycle.scheduled_at,
        )
    raw_state = json.loads(
        (program.root / "cycles/cycle-001/state.json").read_text()
    )
    assert raw_state["stage"] == "planned"
    assert (program.root / "cycles/cycle-001/seals/unscorable.json").is_file()

    monkeypatch.setattr(runner, "atomic_replace_json", real_replace)
    adopted = runner.check_cycle(program, cycle.index)
    assert adopted["stage"] == "unscorable"
    assert adopted["terminal_reason"] == "simulated_cutoff"

    state_path = program.root / "cycles/cycle-001/state.json"
    forged = json.loads(state_path.read_text())
    forged["terminal_reason"] = "different_reason"
    state_path.write_bytes(runner.canonical_json_bytes(forged))
    with pytest.raises(
        runner.ParallelStrategyRunnerError, match="terminal reason differs"
    ):
        runner.check_cycle(program, cycle.index)


def test_missed_settlement_window_terminalizes_without_new_transport(
    activated_program,
):
    program, manifest_path = activated_program
    cycle = SCHEDULE[0]
    transport = OfflineTransport(program.root)
    assert runner.run_predecision(
        manifest_path,
        cycle.index,
        transport=transport,
        clock=lambda: cycle.scheduled_at + timedelta(minutes=5),
    )["stage"] == "decisions_sealed"
    calls_before_settlement = tuple(transport.logical_calls)

    state = runner.run_settlement(
        manifest_path,
        cycle.index,
        transport=transport,
        clock=lambda: cycle.scheduled_at
        + timedelta(hours=7, minutes=58, seconds=30),
    )

    assert state["stage"] == "unscorable"
    assert state["terminal_reason"] == "missed_settlement_window"
    assert tuple(transport.logical_calls) == calls_before_settlement
    assert runner.check_cycle(program, cycle.index) == state


def test_mid_epoch_crash_resumes_real_evidence_without_retransmission(
    activated_program, monkeypatch: pytest.MonkeyPatch
):
    program, manifest_path = activated_program
    cycle = SCHEDULE[0]
    provider = DurableOfflineNansen(program.repo_root)
    budget = ParallelStrategyBudget(program.root, _reconciliation())
    evidence_clock = lambda: cycle.scheduled_at + timedelta(minutes=5, seconds=1)
    transport = DurableEvidenceTransport(
        program.root, budget, provider, evidence_clock
    )
    real_confirm = budget.confirm_paid
    crashed = False

    def crash_after_screener_archive(
        cycle_index,
        epoch,
        reservation,
        response,
        *,
        response_artifact_sha256,
    ):
        nonlocal crashed
        if reservation.endpoint == "token-screener" and not crashed:
            crashed = True
            raise RuntimeError("simulated crash after screener response archive")
        return real_confirm(
            cycle_index,
            epoch,
            reservation,
            response,
            response_artifact_sha256=response_artifact_sha256,
        )

    monkeypatch.setattr(budget, "confirm_paid", crash_after_screener_archive)
    with pytest.raises(RuntimeError, match="simulated crash"):
        runner.run_predecision(
            manifest_path,
            cycle.index,
            transport=transport,
            clock=lambda: cycle.scheduled_at + timedelta(minutes=5),
        )
    assert provider.auth_calls == 2
    assert provider.public_calls == 1
    assert runner._load_state(program, cycle)[1]["stage"] == "planned"

    monkeypatch.setattr(budget, "confirm_paid", real_confirm)
    resumed = DurableEvidenceTransport(
        program.root, budget, provider, evidence_clock
    )
    state = runner.run_predecision(
        manifest_path,
        cycle.index,
        transport=resumed,
        clock=lambda: cycle.scheduled_at + timedelta(minutes=5),
    )

    assert state["stage"] == "decisions_sealed"
    assert provider.public_calls == 1
    assert provider.auth_calls == 2 + TOKENS_PER_CYCLE * 4
    assert len(provider.caller_ids) == provider.auth_calls
    assert budget.summary().attempts == provider.auth_calls
    assert budget.summary().credits == provider.auth_calls - 1


def test_validation_refuses_without_exact_frozen_discovery_family(
    activated_program, monkeypatch: pytest.MonkeyPatch
):
    program, manifest_path = activated_program
    _terminalize_cycles(
        program, (cycle for cycle in SCHEDULE if cycle.phase == "discovery")
    )
    expected_family = {
        "schema_version": 1,
        "program_id": runner.PROGRAM_ID,
        "stage": "validation_family_frozen",
        "anchor_id": PARENT_NONCASH_CANDIDATE_IDS[0],
        "validation_family_ids": [PARENT_NONCASH_CANDIDATE_IDS[0]],
    }
    monkeypatch.setattr(
        runner, "freeze_discovery_family", lambda **_: expected_family
    )
    validation = next(cycle for cycle in SCHEDULE if cycle.phase == "validation")

    with pytest.raises(
        runner.ParallelStrategyRunnerError,
        match="exact frozen discovery family",
    ):
        runner.run_predecision(
            manifest_path,
            validation.index,
            transport=RejectCalls(),
            clock=lambda: validation.scheduled_at,
        )


def test_validation_refuses_family_file_without_atomic_program_seal(
    activated_program, monkeypatch: pytest.MonkeyPatch
):
    program, manifest_path = activated_program
    _terminalize_cycles(
        program, (cycle for cycle in SCHEDULE if cycle.phase == "discovery")
    )
    expected_family = {
        "schema_version": 1,
        "program_id": runner.PROGRAM_ID,
        "stage": "validation_family_frozen",
        "anchor_id": PARENT_NONCASH_CANDIDATE_IDS[0],
        "validation_family_ids": [PARENT_NONCASH_CANDIDATE_IDS[0]],
    }
    monkeypatch.setattr(
        runner, "freeze_discovery_family", lambda **_: expected_family
    )
    runner._write_once(
        program.root / "derived/discovery-family.json", expected_family
    )
    validation = next(cycle for cycle in SCHEDULE if cycle.phase == "validation")

    with pytest.raises(
        runner.ParallelStrategyRunnerError,
        match="exact frozen discovery family seal",
    ):
        runner.run_predecision(
            manifest_path,
            validation.index,
            transport=RejectCalls(),
            clock=lambda: validation.scheduled_at,
        )


def test_check_program_rederives_a_resealed_discovery_family(activated_program):
    program, manifest_path = activated_program
    _terminalize_cycles(
        program, (cycle for cycle in SCHEDULE if cycle.phase == "discovery")
    )
    family_path = runner.freeze_discovery(manifest_path)
    family = json.loads(family_path.read_bytes())
    family["anchor_id"] = PARENT_NONCASH_CANDIDATE_IDS[1]
    family_path.write_bytes(runner.canonical_json_bytes(family))

    seal_path = program.root / "seals/discovery-family.json"
    seal = json.loads(seal_path.read_bytes())
    relative = family_path.relative_to(program.root).as_posix()
    for artifact in seal["artifacts"]:
        if artifact["path"] == relative:
            artifact["sha256"] = runner._sha256(family_path)
            break
    else:  # pragma: no cover - a valid discovery seal always binds the family
        raise AssertionError("discovery seal omitted its family artifact")
    seal_path.write_bytes(runner.canonical_json_bytes(seal))

    with pytest.raises(
        runner.ParallelStrategyRunnerError,
        match="discovery family does not replay",
    ):
        runner.check_program(manifest_path)


def test_check_program_requires_and_rederives_budget_reconciliation(
    activated_program,
):
    program, manifest_path = activated_program
    ParallelStrategyBudget(program.root, _reconciliation())
    activation_path = program.root / "activation/operational-reconciliation.json"
    activation_path.unlink()
    with pytest.raises(
        runner.ParallelStrategyRunnerError,
        match="without activation reconciliation",
    ):
        runner.check_program(manifest_path)


def test_check_program_rejects_matching_forged_reconciliation_copies(
    activated_program,
):
    program, manifest_path = activated_program
    forged = _reconciliation()
    forged["opening_balance_candidates"] = [50_065]
    activation_path = program.root / "activation/operational-reconciliation.json"
    activation_path.write_bytes(runner.canonical_json_bytes(forged))
    ParallelStrategyBudget(program.root, forged)

    with pytest.raises(
        runner.ParallelStrategyRunnerError,
        match="activation reconciliation does not replay",
    ):
        runner.check_program(manifest_path)


def test_feature_contract_fatal_stops_every_later_cycle(activated_program):
    program, manifest_path = activated_program
    cycle = SCHEDULE[0]

    def malformed_flow(endpoint, epoch, request, body):
        del epoch, request
        if endpoint == "tgm/flows":
            return {
                "data": [],
                "pagination": {
                    "page": 1,
                    "per_page": 1000,
                    "is_last_page": True,
                },
            }
        return body

    transport = OfflineTransport(program.root, mutate=malformed_flow)
    state = runner.run_predecision(
        manifest_path,
        cycle.index,
        transport=transport,
        clock=lambda: cycle.scheduled_at + timedelta(minutes=5),
    )

    assert state["stage"] == "unscorable"
    assert state["terminal_reason"].startswith(
        "program_fatal:feature_contract:smart_money flow response"
    )
    assert (program.root / "seals/program-fatal.json").is_file()
    calls_at_fatal = tuple(transport.logical_calls)
    later = SCHEDULE[1]
    with pytest.raises(runner.ParallelStrategyRunnerError, match="globally fatal"):
        runner.run_predecision(
            manifest_path,
            later.index,
            transport=transport,
            clock=lambda: later.scheduled_at,
        )
    assert tuple(transport.logical_calls) == calls_at_fatal


def test_fatal_intent_alone_refuses_every_later_provider_action(activated_program):
    program, manifest_path = activated_program
    cycle = SCHEDULE[0]
    runner._write_once(
        program.root / "intents/program-fatal.json",
        {
            "schema_version": 1,
            "program_id": runner.PROGRAM_ID,
            "cycle_index": cycle.index,
            "recorded_at": _utc_text(cycle.scheduled_at),
            "reason": "simulated crash after durable fatal intent",
        },
    )

    with pytest.raises(runner.ParallelStrategyRunnerError, match="globally fatal"):
        runner.run_predecision(
            manifest_path,
            cycle.index,
            transport=RejectCalls(),
            clock=lambda: cycle.scheduled_at,
        )


def test_execution_contract_error_is_program_fatal(activated_program):
    program, manifest_path = activated_program
    cycle = SCHEDULE[0]

    def malformed_ohlcv(endpoint, epoch, request, body):
        del epoch, request
        if endpoint == "tgm/token-ohlcv":
            return {**body, "data": []}
        return body

    transport = OfflineTransport(program.root, mutate=malformed_ohlcv)
    assert runner.run_predecision(
        manifest_path,
        cycle.index,
        transport=transport,
        clock=lambda: cycle.scheduled_at + timedelta(minutes=5),
    )["stage"] == "decisions_sealed"

    state = runner.run_settlement(
        manifest_path,
        cycle.index,
        transport=transport,
        clock=lambda: cycle.scheduled_at + timedelta(hours=4, minutes=31),
    )

    assert state["stage"] == "unscorable"
    assert state["terminal_reason"].startswith(
        "program_fatal:outcome_contract:OHLCV rows"
    )
    assert (program.root / "seals/program-fatal.json").is_file()
    assert runner.check_cycle(program, cycle.index) == state


def test_settlement_absolute_hard_stop_halts_post_response_processing(
    activated_program,
):
    program, manifest_path = activated_program
    cycle = SCHEDULE[0]
    transport = OfflineTransport(program.root)
    assert runner.run_predecision(
        manifest_path,
        cycle.index,
        transport=transport,
        clock=lambda: cycle.scheduled_at + timedelta(minutes=5),
    )["stage"] == "decisions_sealed"
    current = [cycle.scheduled_at + timedelta(hours=4, minutes=31)]
    original_call = transport.call

    def cross_hard_stop(**kwargs):
        result = original_call(**kwargs)
        if (
            kwargs["epoch"] == "settlement"
            and kwargs["logical_request_id"] == "token-01/dex-buy-1"
        ):
            current[0] = runner.settlement_hard_stop(cycle)
        return result

    transport.call = cross_hard_stop  # type: ignore[method-assign]
    calls_before = len(transport.logical_calls)
    state = runner.run_settlement(
        manifest_path,
        cycle.index,
        transport=transport,
        clock=lambda: current[0],
    )

    assert state["stage"] == "unscorable"
    assert "absolute hard stop" in state["terminal_reason"]
    # Exactly the settlement account and first DEX request were admitted.
    assert len(transport.logical_calls) - calls_before == 2
    assert runner.check_cycle(program, cycle.index) == state


def test_complete_lifecycle_is_exact_resumable_and_tamper_evident(activated_program):
    program, manifest_path = activated_program
    cycle = SCHEDULE[0]
    transport = OfflineTransport(program.root)

    predecision = runner.run_predecision(
        manifest_path,
        cycle.index,
        transport=transport,
        clock=lambda: cycle.scheduled_at + timedelta(minutes=5),
    )
    assert predecision["stage"] == "decisions_sealed"

    decisions_path = program.root / "cycles/cycle-001/derived/decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    assert len(decisions["tokens"]) == TOKENS_PER_CYCLE
    assert all(
        tuple(token["decisions"]) == PARENT_NONCASH_CANDIDATE_IDS
        for token in decisions["tokens"]
    )
    assert sum(len(token["decisions"]) for token in decisions["tokens"]) == 13 * 11

    calls_after_predecision = tuple(transport.logical_calls)
    assert runner.run_predecision(
        manifest_path,
        cycle.index,
        transport=transport,
        clock=lambda: cycle.scheduled_at + timedelta(minutes=6),
    ) == predecision
    assert tuple(transport.logical_calls) == calls_after_predecision

    settled = runner.run_settlement(
        manifest_path,
        cycle.index,
        transport=transport,
        clock=lambda: cycle.scheduled_at + timedelta(hours=4, minutes=31),
    )
    assert settled["stage"] == "outcome_sealed"
    outcomes = json.loads(
        (program.root / "cycles/cycle-001/derived/outcomes.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(outcomes["tokens"]) == TOKENS_PER_CYCLE
    assert [item["event_id"] for item in outcomes["tokens"]] == [
        item["selection"]["event_id"] for item in decisions["tokens"]
    ]
    assert all(len(item["raw_evidence"]) == 3 for item in outcomes["tokens"])
    assert all(
        len(item["replay_attestation_sha256"]) == 64 for item in outcomes["tokens"]
    )

    calls_after_settlement = tuple(transport.logical_calls)
    assert runner.run_settlement(
        manifest_path,
        cycle.index,
        transport=transport,
        clock=lambda: cycle.scheduled_at + timedelta(hours=4, minutes=32),
    ) == settled
    assert tuple(transport.logical_calls) == calls_after_settlement
    assert len(calls_after_predecision) == 2 + TOKENS_PER_CYCLE * 4
    assert len(calls_after_settlement) - len(calls_after_predecision) == (
        1 + TOKENS_PER_CYCLE * 3
    )
    assert runner.check_cycle(program, cycle.index)["stage"] == "outcome_sealed"
    replay = runner.check_program(manifest_path)
    assert replay["authenticated_attempts"] == 94
    assert replay["billable_credits"] == 92
    assert replay["budget_halted_reason"] is None

    forged_decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    forged_decisions["tokens"][0]["selection"] = {
        **forged_decisions["tokens"][0]["selection"],
        "event_id": "ps-c001-b99",
    }
    with pytest.raises(
        runner.ParallelStrategyRunnerError, match="decision token identity"
    ):
        runner._replay_decisions(program, cycle, forged_decisions)

    state_path = program.root / "cycles/cycle-001/state.json"
    original_state = state_path.read_bytes()
    state_document = json.loads(original_state)
    state_document["seals"] = [
        item
        for item in state_document["seals"]
        if item["stage"] != "decisions_sealed"
    ]
    state_path.write_bytes(runner.canonical_json_bytes(state_document))
    with pytest.raises(
        runner.ParallelStrategyRunnerError, match="seal chain"
    ):
        runner.check_cycle(program, cycle.index)
    state_path.write_bytes(original_state)

    outcomes_path = program.root / "cycles/cycle-001/derived/outcomes.json"
    outcome_seal_path = program.root / "cycles/cycle-001/seals/outcome_sealed.json"
    original_outcomes = outcomes_path.read_bytes()
    original_outcome_seal = outcome_seal_path.read_bytes()
    tampered_outcomes = json.loads(original_outcomes)
    tampered_outcomes["decision_t0"] = _utc_text(
        cycle.scheduled_at + timedelta(minutes=15)
    )
    outcomes_path.write_bytes(runner.canonical_json_bytes(tampered_outcomes))
    outcome_seal = json.loads(original_outcome_seal)
    outcome_relative = outcomes_path.relative_to(program.root).as_posix()
    for artifact in outcome_seal["artifacts"]:
        if artifact["path"] == outcome_relative:
            artifact["sha256"] = runner._sha256(outcomes_path)
            break
    else:  # pragma: no cover - the successful outcome seal must bind this file
        raise AssertionError("outcome seal omitted outcomes.json")
    outcome_seal_path.write_bytes(runner.canonical_json_bytes(outcome_seal))
    state_document = json.loads(original_state)
    for reference in state_document["seals"]:
        if reference["stage"] == "outcome_sealed":
            reference["sha256"] = runner._sha256(outcome_seal_path)
    state_path.write_bytes(runner.canonical_json_bytes(state_document))
    with pytest.raises(
        runner.ParallelStrategyRunnerError, match="terminal cycle denominator"
    ):
        runner.check_cycle(program, cycle.index)
    outcomes_path.write_bytes(original_outcomes)
    outcome_seal_path.write_bytes(original_outcome_seal)
    state_path.write_bytes(original_state)

    panel = json.loads(
        (program.root / "cycles/cycle-001/derived/panel.json").read_text(
            encoding="utf-8"
        )
    )
    tampered = program.root / panel["screener_evidence"]["response"]["path"]
    tampered.write_text("{}\n", encoding="utf-8")
    with pytest.raises(runner.ParallelStrategyRunnerError, match="artifact hash differs"):
        runner.check_cycle(program, cycle.index)


def test_discovery_unscorable_finalizes_without_validation(activated_program):
    program, manifest_path = activated_program
    discovery = tuple(cycle for cycle in SCHEDULE if cycle.phase == "discovery")
    validation = tuple(cycle for cycle in SCHEDULE if cycle.phase == "validation")
    _terminalize_cycles(program, discovery)

    result_path = runner.finalize_program(manifest_path)
    first_bytes = result_path.read_bytes()
    result = json.loads(first_bytes)

    assert result["stage"] == "unscorable"
    assert result["terminal_reason"] == "insufficient_discovery_program_support"
    assert result["formal_family_ids"] == []
    assert not (program.root / f"cycles/cycle-{validation[0].index:03d}").exists()
    assert runner.finalize_program(manifest_path).read_bytes() == first_bytes

    replay = runner.check_program(manifest_path)
    assert replay["stages"]["unscorable"] == len(discovery)
    assert replay["stages"]["planned"] == len(validation)
    assert replay["terminal_cycles"] == len(discovery)
    assert replay["finalized"] is True


def test_finalize_and_check_replay_exact_phase_denominators(
    activated_program, monkeypatch: pytest.MonkeyPatch
):
    program, manifest_path = activated_program
    recorded: dict[str, list[int]] = {
        "freeze": [],
        "validation_discovery": [],
        "validation": [],
        "analysis": [],
    }

    _terminalize_cycles(program, SCHEDULE)

    def fake_freeze(*, cycle_statuses, records):
        assert len(cycle_statuses) == 42
        recorded["freeze"].append(len(records))
        return {
            "schema_version": 1,
            "stage": "validation_family_frozen",
            "family": [],
            "record_count": len(records),
        }

    def fake_validation_result(
        *,
        discovery_cycle_statuses,
        discovery_records,
        validation_cycle_statuses,
        validation_records,
        family_seal,
    ):
        assert len(discovery_cycle_statuses) == 42
        assert len(validation_cycle_statuses) == 43
        assert family_seal["record_count"] == 42 * TOKENS_PER_CYCLE
        recorded["validation_discovery"].append(len(discovery_records))
        recorded["validation"].append(len(validation_records))
        return {
            "schema_version": 1,
            "stage": "completed",
            "discovery_records": len(discovery_records),
            "validation_records": len(validation_records),
        }

    def fake_analysis(*, phase, cycle_statuses, records):
        assert phase == "validation"
        assert len(cycle_statuses) == 43
        recorded["analysis"].append(len(records))
        return {"schema_version": 1, "phase": phase, "record_count": len(records)}

    monkeypatch.setattr(runner, "freeze_discovery_family", fake_freeze)
    monkeypatch.setattr(runner, "validation_result", fake_validation_result)
    monkeypatch.setattr(runner, "phase_analysis", fake_analysis)

    result_path = runner.finalize_program(manifest_path)
    first_bytes = result_path.read_bytes()
    assert json.loads(first_bytes) == {
        "schema_version": 1,
        "stage": "completed",
        "discovery_records": 42 * TOKENS_PER_CYCLE,
        "validation_records": 43 * TOKENS_PER_CYCLE,
    }
    assert runner.finalize_program(manifest_path).read_bytes() == first_bytes

    replay = runner.check_program(manifest_path)
    assert replay["terminal_cycles"] == len(SCHEDULE)
    assert replay["stages"]["unscorable"] == len(SCHEDULE)
    assert replay["finalized"] is True
    assert set(recorded["freeze"]) == {42 * TOKENS_PER_CYCLE}
    assert set(recorded["validation_discovery"]) == {42 * TOKENS_PER_CYCLE}
    assert set(recorded["validation"]) == {43 * TOKENS_PER_CYCLE}
    assert set(recorded["analysis"]) == {43 * TOKENS_PER_CYCLE}
