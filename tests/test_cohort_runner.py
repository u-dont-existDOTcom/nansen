from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.nansen_signal_lab.cohort_runner as cohort_runner
import src.nansen_signal_lab.prospective_runner as prospective_runner
from src.nansen_signal_lab.artifacts import canonical_json_bytes
from src.nansen_signal_lab.budget import BudgetGuard
from src.nansen_signal_lab.client import NansenEvidenceResponse
from src.nansen_signal_lab.cohort_runner import (
    check_cycle,
    initialize_cycle,
    replay_program,
    settle_cycle,
    start_cycle,
)
from src.nansen_signal_lab.cohort_schema import (
    CONTRACT_SOURCE_PATH,
    STRATEGY_SOURCE_PATH,
    initialize_cohort_program,
)


SCHEDULED = datetime(2026, 8, 24, 12, 5, tzinfo=timezone.utc)


class MutableClock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self):
        return self.value


def _repo(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1]
    repo = tmp_path / "repo"
    design_rel = "docs/superpowers/specs/2026-08-18-prospective-multi-cycle-cohort-v1.md"
    design = repo / design_rel
    design.parent.mkdir(parents=True)
    design.write_bytes((source / design_rel).read_bytes())
    for relative in (CONTRACT_SOURCE_PATH, STRATEGY_SOURCE_PATH):
        destination = repo / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source / relative).read_bytes())
    strategy = json.loads((source / STRATEGY_SOURCE_PATH).read_text())
    dependency = (Path(STRATEGY_SOURCE_PATH).parent / strategy["source_signal_manifest"]).as_posix()
    dependency_target = repo / dependency
    dependency_target.parent.mkdir(parents=True, exist_ok=True)
    dependency_target.write_bytes((source / dependency).read_bytes())
    return repo


def _program(tmp_path: Path):
    repo = _repo(tmp_path)
    return initialize_cohort_program(
        repo / "research/experiments/prospective-fixture",
        created_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        first_cycle_at=SCHEDULED,
        repo_root=repo,
    )


def _flow(change: float):
    rows = []
    boundary = SCHEDULED.replace(minute=0)
    for index in range(26):
        end = boundary - timedelta(hours=25 - index)
        amount = 100.0 * ((1.0 + change) ** index)
        rows.append({
            "date": (end - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "bucket_end": end.isoformat().replace("+00:00", "Z"),
            "is_complete": True,
            "price_usd": 10.0,
            "token_amount": amount,
            "value_usd": amount * 10,
            "holders_count": 10 + index,
            "total_inflows_count": 1,
            "total_outflows_count": -1,
            "total_inflows_dex": 1,
            "total_outflows_dex": -1,
            "total_inflows_cex": 1,
            "total_outflows_cex": -1,
        })
    return {"data": rows, "pagination": {"page": 1, "per_page": 1000, "is_last_page": True}}


def _screener():
    specifications = (
        ("EARLY", "0x01", 0.01, 500),
        ("MIDDLE", "0x02", 0.10, 400),
        ("MOMENTUM", "0x03", 0.20, 300),
        ("NEUTRAL", "0x04", -0.10, 0),
        ("DISTRIBUTION", "0x05", 0.10, -600),
    )
    return {
        "data": [
            {
                "chain": "base", "token_address": address, "token_symbol": symbol,
                "token_age_days": 10, "market_cap_usd": 1_000_000,
                "liquidity": 250_000, "price_usd": 10, "price_change": change,
                "volume": 10_000, "netflow": netflow,
            }
            for symbol, address, change, netflow in specifications
        ],
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
    }


def _wbs(side: str):
    count = 3 if side == "BUY" else 1
    field = "bought_volume_usd" if side == "BUY" else "sold_volume_usd"
    return {
        "data": [
            {"address": f"0x{index + 100:040x}", field: float(100 - index)}
            for index in range(count)
        ],
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
    }


def _trade(payload):
    side = payload["filters"]["action"]
    start = datetime.fromisoformat(payload["date"]["from"].replace("Z", "+00:00"))
    price = 10.0 if side == "BUY" else 11.0
    return {
        "data": [{
            "block_timestamp": start.isoformat().replace("+00:00", "Z"),
            "transaction_hash": "0xentry" if side == "BUY" else "0xexit",
            "trader_address": "0xtrader", "action": side,
            "token_address": payload["token_address"], "token_name": "TOKEN",
            "token_amount": 100.0, "traded_token_address": "0xusd",
            "traded_token_name": "USD", "traded_token_amount": 100 * price,
            "estimated_swap_price_usd": price, "estimated_value_usd": 100 * price,
        }],
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
    }


def _ohlcv(payload):
    start = datetime.fromisoformat(payload["date"]["from"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(payload["date"]["to"].replace("Z", "+00:00"))
    rows = []
    cursor = start
    while cursor <= end:
        rows.append({
            "interval_start": cursor.isoformat().replace("+00:00", "Z"),
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
            "volume": 100, "volume_usd": 1000,
            "market_cap": {"open": 1000, "high": 1100, "low": 900, "close": 1050},
        })
        cursor += timedelta(minutes=5)
    return {
        "chain": payload["chain"], "token_address": payload["token_address"],
        "timeframe": "5m", "truncated": False, "data": rows,
    }


class FakeNansen:
    def __init__(
        self,
        clock: MutableClock,
        *,
        remaining: int = 1792,
        two_pages: bool = False,
        bad_contract: bool = False,
        malformed_first_flow: bool = False,
        malformed_first_dex: bool = False,
        invalid_account: bool = False,
        partial_entry: bool = False,
        screener_body: dict | None = None,
    ):
        self.clock = clock
        self.remaining = remaining
        self.calls = []
        self.two_pages = two_pages
        self.bad_contract = bad_contract
        self.malformed_first_flow = malformed_first_flow
        self.malformed_first_dex = malformed_first_dex
        self.invalid_account = invalid_account
        self.partial_entry = partial_entry
        self.screener_body = screener_body
        self.flow_calls = 0
        self.dex_calls = 0

    def fetch_openapi(self):
        source = Path(__file__).resolve().parents[1]
        content = (source / CONTRACT_SOURCE_PATH).read_bytes()
        return content + b"drift" if self.bad_contract else content

    def request_evidence(self, method, endpoint, payload, *, caller_request_id):
        self.calls.append((method, endpoint, payload, caller_request_id))
        if endpoint == "account":
            body = {"plan": "free", "credits_remaining": self.remaining}
            if self.invalid_account:
                body = {"plan": "invalid", "credits_remaining": 0}
            cost = 0
            used = remaining = None
            headers = {"X-Nansen-Credits-Cost": "0"}
            if self.invalid_account:
                used = 0
                remaining = self.remaining
                headers.update({
                    "X-Nansen-Credits-Used": "0",
                    "X-Nansen-Credits-Remaining": str(remaining),
                })
        else:
            if endpoint == "token-screener":
                body = json.loads(json.dumps(self.screener_body or _screener()))
            elif endpoint == "tgm/flows":
                body = _flow(0.01 if payload["label"] == "smart_money" else -0.01)
                self.flow_calls += 1
                if self.malformed_first_flow and self.flow_calls == 1:
                    body["pagination"]["is_last_page"] = False
            elif endpoint == "tgm/who-bought-sold":
                page = payload["pagination"]["page"]
                body = _wbs(payload["buy_or_sell"])
                body["pagination"]["page"] = page
                if self.two_pages:
                    body["pagination"]["is_last_page"] = page == 2
                    if page == 2:
                        body["data"] = []
            elif endpoint == "tgm/dex-trades":
                body = _trade(payload)
                if self.partial_entry and payload["filters"]["action"] == "BUY":
                    body["data"][0].update(
                        token_amount=0.1,
                        traded_token_amount=1.0,
                        estimated_value_usd=1.0,
                    )
                page = payload["pagination"]["page"]
                body["pagination"]["page"] = page
                if self.two_pages:
                    body["pagination"]["is_last_page"] = page == 2
                    if page == 2:
                        body["data"] = []
                self.dex_calls += 1
                if self.malformed_first_dex and self.dex_calls == 1:
                    body["data"][0]["token_address"] = "0xwrong"
            elif endpoint == "tgm/token-ohlcv":
                body = _ohlcv(payload)
            else:  # pragma: no cover
                raise AssertionError(endpoint)
            cost = used = 1
            self.remaining -= 1
            remaining = self.remaining
            headers = {
                "X-Nansen-Credits-Cost": "1",
                "X-Nansen-Credits-Used": "1",
                "X-Nansen-Credits-Remaining": str(remaining),
            }
        timestamp = self.clock().isoformat().replace("+00:00", "Z")
        return NansenEvidenceResponse(
            body=body,
            body_parse_status="json_object",
            raw_body=canonical_json_bytes(body),
            status_code=200,
            request_started_at=timestamp,
            response_retrieved_at=timestamp,
            response_headers=headers,
            request_id=None,
            credit_cost=cost,
            credit_used=used,
            credit_remaining=remaining,
            credit_header_errors=(),
        )


def test_full_cycle_collects_every_counterfactual_and_replays(tmp_path):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock)
    started = start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert started["stage"] == "decisions_sealed"
    assert len(fake.calls) == 22  # account + screener + 4 predecision calls/token
    decisions = json.loads(
        (program.root / "cycles/cycle-01/derived/decisions.json").read_text()
    )
    assert all(
        sum(
            item["variant"] == "distribution_veto"
            for item in token["comparators"]
        )
        == 5
        for token in decisions["tokens"]
    )
    before = len(fake.calls)
    assert start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)["stage"] == "decisions_sealed"
    assert len(fake.calls) == before

    clock.value = datetime(2026, 8, 24, 16, 31, tzinfo=timezone.utc)
    settled = settle_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert settled["stage"] == "outcome_sealed"
    assert len(fake.calls) == 37  # all 5 tokens: BUY, SELL and OHLCV
    checked = check_cycle(program, 1)
    assert checked["attempts"] == 37
    assert checked["credits"] == 36
    assert replay_program(program)["terminal_cycles"] == 1


def test_direct_replay_rejects_archived_runtime_drift(tmp_path):
    program = _program(tmp_path)
    archived_module = (
        program.root
        / "contracts/implementation/src/nansen_signal_lab/cohort_selection.py"
    )
    archived_module.write_text(archived_module.read_text() + "\n# drift\n")

    with pytest.raises(
        Exception,
        match="archived protocol implementation bytes differ",
    ):
        replay_program(program)


def test_decisions_use_program_local_comparators_after_source_is_removed(tmp_path):
    program = _program(tmp_path)
    (program.root.parents[2] / STRATEGY_SOURCE_PATH).unlink()
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock)
    state = start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert state["stage"] == "decisions_sealed"


def test_late_cycle_terminalizes_without_provider_access(tmp_path):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED + timedelta(minutes=16))
    fake = FakeNansen(clock)
    state = start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert state["stage"] == "unscorable"
    assert fake.calls == []
    assert check_cycle(program, 1)["attempts"] == 0


def test_early_cycle_start_is_nonterminal_and_can_run_on_schedule(tmp_path):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED - timedelta(seconds=1))
    fake = FakeNansen(clock)
    with pytest.raises(Exception, match="cannot start before"):
        start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert fake.calls == []
    assert json.loads(
        (program.root / "cycles/cycle-01/state.json").read_text()
    )["stage"] == "planned"
    clock.value = SCHEDULED
    assert start_cycle(
        program, 1, nansen=fake, clock=clock, sleep=lambda _: None
    )["stage"] == "decisions_sealed"


def test_settlement_too_early_is_nonterminal_and_makes_no_calls(tmp_path):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock)
    assert start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)["stage"] == "decisions_sealed"
    before = len(fake.calls)
    with pytest.raises(Exception, match="too early"):
        settle_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert len(fake.calls) == before
    assert check_cycle(program, 1)["stage"] == "decisions_sealed"


def test_exact_two_page_boundary_uses_57_attempts_and_56_credits(tmp_path):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock, two_pages=True)
    assert start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)["stage"] == "decisions_sealed"
    clock.value = datetime(2026, 8, 24, 16, 31, tzinfo=timezone.utc)
    assert settle_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)["stage"] == "outcome_sealed"
    checked = check_cycle(program, 1)
    assert checked["attempts"] == 57
    assert checked["credits"] == 56


def test_insufficient_full_program_funding_stops_after_zero_credit_account(tmp_path):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock, remaining=1791)
    state = start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert state["stage"] == "unscorable"
    assert [call[1] for call in fake.calls] == ["account"]
    assert check_cycle(program, 1)["credits"] == 0


def test_contract_drift_stops_before_authenticated_access(tmp_path):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock, bad_contract=True)
    state = start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert state["stage"] == "unscorable"
    assert fake.calls == []


def test_invalid_complete_header_account_body_stops_before_paid_access(tmp_path):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock, invalid_account=True)
    state = start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert state["stage"] == "unscorable"
    assert [call[1] for call in fake.calls] == ["account"]
    assert check_cycle(program, 1)["credits"] == 0


@pytest.mark.parametrize(
    "mutation,reason",
    (
        (lambda body: body["pagination"].update(is_last_page=False), "insufficient_universe"),
        (
            lambda body: body.update(
                data=[row for row in body["data"] if row["token_symbol"] != "MIDDLE"]
            ),
            "insufficient_strata",
        ),
    ),
)
def test_selection_failures_preserve_frozen_terminal_reason(
    tmp_path, mutation, reason
):
    body = _screener()
    mutation(body)
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock, screener_body=body)
    state = start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert state["stage"] == "unscorable"
    assert state["terminal_reason"] == reason
    assert [call[1] for call in fake.calls] == ["account", "token-screener"]


def test_first_malformed_feature_and_outcome_fail_fast(tmp_path):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    bad_feature = FakeNansen(clock, malformed_first_flow=True)
    state = start_cycle(
        program, 1, nansen=bad_feature, clock=clock, sleep=lambda _: None
    )
    assert state["stage"] == "unscorable"
    assert [call[1] for call in bad_feature.calls] == [
        "account", "token-screener", "tgm/flows"
    ]

    program2 = initialize_cohort_program(
        program.root.parent / "prospective-fixture-2",
        created_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        first_cycle_at=SCHEDULED,
        repo_root=program.root.parents[2],
    )
    bad_outcome = FakeNansen(clock, malformed_first_dex=True)
    assert start_cycle(
        program2, 1, nansen=bad_outcome, clock=clock, sleep=lambda _: None
    )["stage"] == "decisions_sealed"
    clock.value = datetime(2026, 8, 24, 16, 31, tzinfo=timezone.utc)
    state = settle_cycle(
        program2, 1, nansen=bad_outcome, clock=clock, sleep=lambda _: None
    )
    assert state["stage"] == "unscorable"
    assert bad_outcome.dex_calls == 1


def test_partial_entry_preserves_the_observed_exit_fill(tmp_path):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock, partial_entry=True)
    assert start_cycle(
        program, 1, nansen=fake, clock=clock, sleep=lambda _: None
    )["stage"] == "decisions_sealed"
    clock.value = datetime(2026, 8, 24, 16, 31, tzinfo=timezone.utc)
    assert settle_cycle(
        program, 1, nansen=fake, clock=clock, sleep=lambda _: None
    )["stage"] == "outcome_sealed"
    outcome = json.loads(
        (program.root / "cycles/cycle-01/derived/outcomes/token-01.json").read_text()
    )
    assert outcome["outcome"]["status"] == "UNFILLED_ENTRY"
    assert outcome["outcome"]["entry_fill"]["filled_token_amount"] == 0.1
    assert outcome["outcome"]["exit_fill"]["filled_token_amount"] == 0.1
    assert outcome["utc_week"] == "2026-W35"


def test_outcome_failure_keeps_sealed_opportunities_signals_and_attempts(tmp_path):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock, malformed_first_dex=True)
    assert start_cycle(
        program, 1, nansen=fake, clock=clock, sleep=lambda _: None
    )["stage"] == "decisions_sealed"
    clock.value = datetime(2026, 8, 24, 16, 31, tzinfo=timezone.utc)
    assert settle_cycle(
        program, 1, nansen=fake, clock=clock, sleep=lambda _: None
    )["stage"] == "unscorable"
    records, _, selected, attempted = cohort_runner._aggregate_records(program)
    primary = [row for row in records if row["rule_id"] == cohort_runner._H5]
    assert selected == 5
    assert attempted == 1
    assert len(primary) == 5
    assert all(row["action"] == "LONG" for row in primary)
    assert all(row["outcome"]["status"] == "UNAVAILABLE" for row in primary)


def test_unsealed_extra_evidence_is_rejected(tmp_path):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED + timedelta(minutes=16))
    fake = FakeNansen(clock)
    assert start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)["stage"] == "unscorable"
    extra = program.root / "cycles/cycle-01/derived/extra.json"
    extra.write_text("{}")
    with pytest.raises(Exception, match="unsealed"):
        check_cycle(program, 1)


def test_mutable_state_cannot_claim_unscorable_without_a_seal(tmp_path):
    program = _program(tmp_path)
    state_path = initialize_cycle(program, 1)
    state = json.loads(state_path.read_text())
    state["stage"] = "unscorable"
    state["terminal_reason"] = "forged mutable terminal state"
    state_path.write_text(json.dumps(state))
    with pytest.raises(Exception, match="without its seal"):
        check_cycle(program, 1)


def test_nested_budget_symlink_and_extra_file_fail_exact_archive_check(tmp_path):
    program = _program(tmp_path)
    initialize_cycle(program, 1)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = program.root / "cycles/cycle-01/budget/journal/escape"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(Exception, match="symlink"):
        check_cycle(program, 1)

    program2 = initialize_cohort_program(
        program.root.parent / "prospective-fixture-budget-extra",
        created_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        first_cycle_at=SCHEDULED,
        repo_root=program.root.parents[2],
    )
    clock = MutableClock(SCHEDULED + timedelta(minutes=16))
    assert start_cycle(
        program2, 1, nansen=FakeNansen(clock), clock=clock, sleep=lambda _: None
    )["stage"] == "unscorable"
    (program2.root / "cycles/cycle-01/budget/extra.json").write_text("{}")
    with pytest.raises(Exception, match="budget archive"):
        check_cycle(program2, 1)


def test_program_symlink_anywhere_stops_before_provider_access(tmp_path):
    program = _program(tmp_path)
    outside = tmp_path / "outside-program"
    outside.mkdir()
    (program.root / "derived").symlink_to(outside, target_is_directory=True)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock)
    with pytest.raises(Exception, match="cannot contain symlinks"):
        start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert fake.calls == []


def test_stale_sealed_prefix_terminalizes_before_more_provider_calls(
    tmp_path, monkeypatch
):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock)
    original = cohort_runner._seal_stage

    def crash_after_universe(*args, **kwargs):
        state = original(*args, **kwargs)
        if kwargs.get("stage") == "universe_sealed":
            raise OSError("simulated crash after immutable universe seal")
        return state

    monkeypatch.setattr(cohort_runner, "_seal_stage", crash_after_universe)
    with pytest.raises(OSError, match="simulated crash"):
        start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    before = len(fake.calls)
    monkeypatch.setattr(cohort_runner, "_seal_stage", original)
    clock.value = SCHEDULED + timedelta(minutes=46)
    state = start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert state["stage"] == "unscorable"
    assert "decision deadline" in state["terminal_reason"]
    assert len(fake.calls) == before


def test_checker_rebuilds_the_exact_request_contract(tmp_path, monkeypatch):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock)
    assert start_cycle(
        program, 1, nansen=fake, clock=clock, sleep=lambda _: None
    )["stage"] == "decisions_sealed"
    monkeypatch.setattr(cohort_runner, "screener_payload", lambda: {"drift": True})
    with pytest.raises(Exception, match="archived request contract differs"):
        check_cycle(program, 1)


def test_complete_response_crash_is_adopted_without_retransmission(
    tmp_path, monkeypatch
):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock)
    original = prospective_runner._confirm_nansen_success
    crashed = False

    def crash_once(**kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise OSError("simulated crash after complete response archive")
        return original(**kwargs)

    monkeypatch.setattr(prospective_runner, "_confirm_nansen_success", crash_once)
    with pytest.raises(OSError, match="complete response archive"):
        start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert [call[1] for call in fake.calls] == ["account"]
    monkeypatch.setattr(prospective_runner, "_confirm_nansen_success", original)
    state = start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert state["stage"] == "decisions_sealed"
    assert [call[1] for call in fake.calls].count("account") == 1


def test_pretransmission_reservation_crash_resumes_safely(tmp_path, monkeypatch):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock)
    original = prospective_runner._install_json
    request_installs = 0

    def crash_before_third_request(path, value, *, kind):
        nonlocal request_installs
        if kind == "nansen_request":
            request_installs += 1
            if request_installs == 3:
                raise OSError("simulated crash before request artifact installation")
        return original(path, value, kind=kind)

    monkeypatch.setattr(prospective_runner, "_install_json", crash_before_third_request)
    with pytest.raises(OSError, match="before request artifact"):
        start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert [call[1] for call in fake.calls] == ["account", "token-screener"]
    monkeypatch.setattr(prospective_runner, "_install_json", original)
    state = start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert state["stage"] == "decisions_sealed"
    assert len(fake.calls) == 22


def test_late_resume_cancels_unbound_pretransmission_reservation(
    tmp_path, monkeypatch
):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock)
    original = prospective_runner._install_json

    def crash_before_request_artifact(path, value, *, kind):
        if kind == "nansen_request":
            raise OSError("simulated crash before request artifact installation")
        return original(path, value, kind=kind)

    monkeypatch.setattr(
        prospective_runner, "_install_json", crash_before_request_artifact
    )
    with pytest.raises(OSError, match="before request artifact"):
        start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert fake.calls == []
    monkeypatch.setattr(prospective_runner, "_install_json", original)
    clock.value = SCHEDULED + timedelta(minutes=46)
    state = start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert state["stage"] == "unscorable"
    checked = check_cycle(program, 1)
    assert checked["attempts"] == 0
    assert checked["credits"] == 0


def test_request_write_before_ledger_bind_becomes_ambiguous_without_transmission(
    tmp_path, monkeypatch
):
    program = _program(tmp_path)
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock)
    original = BudgetGuard.bind_request_artifact
    binds = 0

    def crash_before_third_bind(self, reservation, artifact_sha256):
        nonlocal binds
        binds += 1
        if binds == 3:
            raise OSError("simulated crash before request ledger bind")
        return original(self, reservation, artifact_sha256)

    monkeypatch.setattr(BudgetGuard, "bind_request_artifact", crash_before_third_bind)
    with pytest.raises(OSError, match="request ledger bind"):
        start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert [call[1] for call in fake.calls] == ["account", "token-screener"]
    monkeypatch.setattr(BudgetGuard, "bind_request_artifact", original)
    state = start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert state["stage"] == "unscorable"
    assert len(fake.calls) == 2
    assert check_cycle(program, 1)["stage"] == "unscorable"


def test_immutable_artifact_collision_terminalizes_and_seals_conflict(tmp_path):
    program = _program(tmp_path)
    initialize_cycle(program, 1)
    panel = program.root / "cycles/cycle-01/derived/panel.json"
    panel.parent.mkdir(parents=True)
    panel.write_text("{}")
    clock = MutableClock(SCHEDULED)
    fake = FakeNansen(clock)
    state = start_cycle(program, 1, nansen=fake, clock=clock, sleep=lambda _: None)
    assert state["stage"] == "unscorable"
    assert "collided" in state["terminal_reason"]
    assert check_cycle(program, 1)["stage"] == "unscorable"
