from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.nansen_signal_lab.historical_recovery as recovery
import src.nansen_signal_lab.prospective_runner as prospective_runner
from src.nansen_signal_lab.client import NansenEvidenceResponse, NansenRequestFailure


NOW = "2026-08-18T14:00:00Z"


def _response(
    body,
    *,
    cost: int,
    remaining: int,
    used: int | None = None,
    status: int = 200,
    retry_after: str | None = None,
    request_started_at: str = NOW,
    response_retrieved_at: str = NOW,
) -> NansenEvidenceResponse:
    headers = {"X-Nansen-Credits-Cost": str(cost)}
    actual_used = cost if used is None else used
    provider_remaining = remaining
    if cost == 0:
        actual_used = None
        provider_remaining = None
    else:
        headers.update({
            "X-Nansen-Credits-Used": str(actual_used),
            "X-Nansen-Credits-Remaining": str(remaining),
        })
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return NansenEvidenceResponse(
        body=body,
        body_parse_status="json_object",
        raw_body=recovery.canonical_json_bytes(body),
        status_code=status,
        request_started_at=request_started_at,
        response_retrieved_at=response_retrieved_at,
        response_headers=headers,
        request_id=None,
        credit_cost=cost,
        credit_used=actual_used,
        credit_remaining=provider_remaining,
        credit_header_errors=(),
    )


def _holdings_body(selection, *, page: int = 1, last: bool = True):
    rows = []
    start = recovery.HOLDINGS_FROM
    days = (recovery.HOLDINGS_TO - start).days + 1
    for member_index, member in enumerate(selection["members"][:2]):
        for offset in range(days):
            day = start + timedelta(days=offset)
            balance = 100.0 + offset
            rows.append({
                "date": day.isoformat(),
                "chain": member["chain"],
                "token_address": member["token_address"],
                "token_symbol": member["token_symbol"],
                "balance": balance,
                "value_usd": balance * (10 + member_index),
                "holders_count": 10 + offset if member_index == 0 else 10,
            })
    if page == 2:
        rows = []
    return {
        "data": rows,
        "pagination": {"page": page, "per_page": 1000, "is_last_page": last},
    }


def _ohlcv_bodies(selection, plan):
    first_day = recovery.SIGNAL_FROM + timedelta(days=1)
    days = (recovery.HOLDINGS_TO - first_day).days + 1
    result = {}
    for logical_id, payload in plan:
        result[logical_id] = {
            "chain": payload["chain"],
            "timeframe": "1d",
            "truncated": False,
            "tokens": [
                {
                    "token_address": address,
                    "data": [
                        {
                            "interval_start": (
                                first_day + timedelta(days=offset)
                            ).isoformat() + "T00:00:00Z",
                            "close": 10.0 + 0.1 * offset,
                        }
                        for offset in range(days)
                    ],
                }
                for address in payload["token_addresses"]
            ],
        }
    return result


@pytest.fixture
def recovery_fixture(tmp_path, monkeypatch):
    project_root = Path(recovery.__file__).resolve().parents[2]
    source_state = recovery._source_state(project_root)
    monkeypatch.setattr(recovery, "_source_state", lambda _: source_state)
    monkeypatch.setattr(recovery, "EXPERIMENT_ID", "fixture-recovery")
    design = tmp_path / recovery.DESIGN_PATH
    design.parent.mkdir(parents=True)
    design.write_bytes((project_root / recovery.DESIGN_PATH).read_bytes())
    root = tmp_path / "research" / "experiments" / "fixture-recovery"
    manifest = recovery.initialize_historical_recovery(
        root,
        created_at=datetime(2026, 8, 18, 14, tzinfo=timezone.utc),
        design_path=design,
    )
    return root, manifest, source_state


def test_init_rejects_a_second_experiment_identity(tmp_path):
    with pytest.raises(recovery.HistoricalRecoveryError, match="identity is fixed"):
        recovery.initialize_historical_recovery(
            tmp_path / "research" / "experiments" / "duplicate-recovery",
            created_at=datetime(2026, 8, 18, 14, tzinfo=timezone.utc),
            design_path=tmp_path / recovery.DESIGN_PATH,
        )


class FakeNansen:
    def __init__(
        self,
        contract: bytes,
        selection,
        plan,
        *,
        two_pages: bool = False,
        remaining: int = 6,
    ):
        self.contract = contract
        self.selection = selection
        self.plan = plan
        self.two_pages = two_pages
        self.remaining = remaining
        self.calls = []
        self.ohlcv = _ohlcv_bodies(selection, plan)

    def fetch_openapi(self):
        return self.contract

    def request_evidence(self, method, endpoint, payload, *, caller_request_id):
        self.calls.append((method, endpoint, payload, caller_request_id))
        assert endpoint != "v1beta1/token-screener/historical"
        if endpoint == "account":
            return _response(
                {"plan": "free", "credits_remaining": self.remaining},
                cost=0,
                remaining=self.remaining,
            )
        if endpoint == recovery.HOLDINGS_ENDPOINT:
            page = payload["pagination"]["page"]
            last = not self.two_pages or page == 2
            body = _holdings_body(self.selection, page=page, last=last)
        elif endpoint == recovery.OHLCV_ENDPOINT:
            body = self.ohlcv[caller_request_id]
        else:  # pragma: no cover - protects fixture misuse
            raise AssertionError(endpoint)
        self.remaining -= 1
        return _response(body, cost=1, remaining=self.remaining)


def test_committed_terminal_source_verifies_and_normalizes():
    project_root = Path(recovery.__file__).resolve().parents[2]
    state = recovery._source_state(project_root)

    assert len(state["raw_body"]["data"]) == 153
    assert len(state["selection"]["members"]) == 20
    assert len(state["plan"]["requests"]) == 4
    assert {member["chain"] for member in state["selection"]["members"]} == {
        "base", "bnb", "ethereum", "solana"
    }
    assert all(
        member["source_raw_row_sha256"] != member["normalized_row_sha256"]
        for member in state["selection"]["members"]
        if member["chain"] == "bnb"
    )


def test_init_freezes_source_preprocessing_before_provider_access(recovery_fixture):
    root, manifest_path, _ = recovery_fixture
    manifest = recovery.load_historical_recovery_manifest(manifest_path)
    selection = json.loads((root / "derived/selection.json").read_text())
    plan = json.loads((root / "derived/ohlcv-request-plan.json").read_text())

    assert manifest["stage"] == "preregistered"
    assert selection["source_raw_file_sha256"] == recovery.SOURCE_BINDINGS[
        "screener_response"
    ]
    assert selection["normalized_body_sha256"] == recovery.SOURCE_BINDINGS[
        "normalized_screener"
    ]
    assert len(plan["requests"]) == recovery.EXPECTED_OHLCV_REQUESTS
    assert manifest["budget"]["cumulative_max_credits"] == 11


def test_recovery_completes_without_screener_rerun_and_reports_both_budgets(
    recovery_fixture,
):
    root, manifest_path, source_state = recovery_fixture
    contract = source_state["copies"]["adopted/nansen-openapi.json"][0].read_bytes()
    selection = json.loads((root / "derived/selection.json").read_text())
    plan = recovery._request_plan(root)
    fake = FakeNansen(contract, selection, plan)

    result = recovery.start_historical_recovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 14, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "completed", result["terminal_reason"]
    assert len(fake.calls) == 6
    assert all(call[1] != "v1beta1/token-screener/historical" for call in fake.calls)
    report = (root / "REPORT.md").read_text()
    assert "successor used 6 authenticated attempts and\n5 additional credits" in report
    assert "study used 8 authenticated attempts and\n10 credits" in report
    seal = json.loads((root / "seals/completed.json").read_text())
    assert seal["incremental_authenticated_request_attempts"] == 6
    assert seal["incremental_credits"] == 5
    assert seal["cumulative_authenticated_request_attempts"] == 8
    assert seal["cumulative_credits"] == 10
    recovery.check_historical_recovery(manifest_path)


def test_recovery_exact_incremental_ceiling_with_two_holdings_pages(recovery_fixture):
    root, manifest_path, source_state = recovery_fixture
    contract = source_state["copies"]["adopted/nansen-openapi.json"][0].read_bytes()
    selection = json.loads((root / "derived/selection.json").read_text())
    fake = FakeNansen(contract, selection, recovery._request_plan(root), two_pages=True)

    result = recovery.start_historical_recovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 14, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "completed", result["terminal_reason"]
    assert len(fake.calls) == recovery.MAX_CALLS == 7
    seal = json.loads((root / "seals/completed.json").read_text())
    assert seal["incremental_credits"] == recovery.MAX_CREDITS == 6
    assert seal["cumulative_credits"] == recovery.CUMULATIVE_MAX_CREDITS == 11
    recovery.check_historical_recovery(manifest_path)


def test_new_bsc_holdings_alias_is_rejected_without_outcome_calls(recovery_fixture):
    root, manifest_path, source_state = recovery_fixture
    contract = source_state["copies"]["adopted/nansen-openapi.json"][0].read_bytes()
    selection = json.loads((root / "derived/selection.json").read_text())

    class AliasNansen(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            response = super().request_evidence(
                method, endpoint, payload, caller_request_id=caller_request_id
            )
            if endpoint == recovery.HOLDINGS_ENDPOINT:
                response.body["data"][0]["chain"] = "bsc"
                return _response(response.body, cost=1, remaining=self.remaining)
            return response

    fake = AliasNansen(contract, selection, recovery._request_plan(root))
    result = recovery.start_historical_recovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 14, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "unscorable"
    assert "forbidden bsc alias" in result["terminal_reason"]
    assert len(fake.calls) == 2
    recovery.check_historical_recovery(manifest_path)


def test_malformed_holdings_is_fully_rejected_before_any_ohlcv_call(recovery_fixture):
    root, manifest_path, source_state = recovery_fixture
    contract = source_state["copies"]["adopted/nansen-openapi.json"][0].read_bytes()
    selection = json.loads((root / "derived/selection.json").read_text())

    class WrongSymbolNansen(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            response = super().request_evidence(
                method, endpoint, payload, caller_request_id=caller_request_id
            )
            if endpoint == recovery.HOLDINGS_ENDPOINT:
                response.body["data"][0]["token_symbol"] = "WRONG"
                return _response(response.body, cost=1, remaining=self.remaining)
            return response

    fake = WrongSymbolNansen(contract, selection, recovery._request_plan(root))
    result = recovery.start_historical_recovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 14, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "unscorable"
    assert "token symbol changed" in result["terminal_reason"]
    assert [call[1] for call in fake.calls] == ["account", recovery.HOLDINGS_ENDPOINT]
    recovery.check_historical_recovery(manifest_path)


def test_malformed_first_ohlcv_stops_before_later_batches(recovery_fixture):
    root, manifest_path, source_state = recovery_fixture
    contract = source_state["copies"]["adopted/nansen-openapi.json"][0].read_bytes()
    selection = json.loads((root / "derived/selection.json").read_text())

    class WrongTimeframeNansen(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            response = super().request_evidence(
                method, endpoint, payload, caller_request_id=caller_request_id
            )
            if endpoint == recovery.OHLCV_ENDPOINT:
                response.body["timeframe"] = "1h"
                return _response(response.body, cost=1, remaining=self.remaining)
            return response

    fake = WrongTimeframeNansen(contract, selection, recovery._request_plan(root))
    result = recovery.start_historical_recovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 14, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "unscorable"
    assert "identity does not match" in result["terminal_reason"]
    assert len(fake.calls) == 3
    assert sum(call[1] == recovery.OHLCV_ENDPOINT for call in fake.calls) == 1
    recovery.check_historical_recovery(manifest_path)


def test_strict_pricing_drift_terminalizes_before_outcomes(recovery_fixture):
    root, manifest_path, source_state = recovery_fixture
    contract = source_state["copies"]["adopted/nansen-openapi.json"][0].read_bytes()
    selection = json.loads((root / "derived/selection.json").read_text())

    class PricingDriftNansen(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            if endpoint != recovery.HOLDINGS_ENDPOINT:
                return super().request_evidence(
                    method, endpoint, payload, caller_request_id=caller_request_id
                )
            self.calls.append((method, endpoint, payload, caller_request_id))
            self.remaining -= 2
            return _response(
                _holdings_body(self.selection), cost=2, remaining=self.remaining
            )

    fake = PricingDriftNansen(contract, selection, recovery._request_plan(root))
    result = recovery.start_historical_recovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 14, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "unscorable"
    assert "pricing cost/use drift" in result["terminal_reason"]
    assert len(fake.calls) == 2
    recovery.check_historical_recovery(manifest_path)


def test_boundary_provider_overcharge_seals_actual_budget_breach(recovery_fixture):
    root, manifest_path, source_state = recovery_fixture
    contract = source_state["copies"]["adopted/nansen-openapi.json"][0].read_bytes()
    selection = json.loads((root / "derived/selection.json").read_text())
    plan = recovery._request_plan(root)
    last_logical_id = plan[-1][0]

    class OverchargeNansen(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            if caller_request_id != last_logical_id:
                return super().request_evidence(
                    method, endpoint, payload, caller_request_id=caller_request_id
                )
            self.calls.append((method, endpoint, payload, caller_request_id))
            self.remaining -= 2
            return _response(
                self.ohlcv[caller_request_id], cost=2, remaining=self.remaining
            )

    fake = OverchargeNansen(
        contract, selection, plan, two_pages=True, remaining=7
    )
    result = recovery.start_historical_recovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 14, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "unscorable"
    assert result["terminal_reason"] == "actual credit ceiling exceeded"
    seal = json.loads((root / "seals/unscorable.json").read_text())
    assert seal["incremental_authenticated_request_attempts"] == 7
    assert seal["incremental_credits"] == 7
    assert seal["cumulative_credits"] == 12
    report = (root / "REPORT.md").read_text()
    assert "provider-reported overcharge" in report
    recovery.check_historical_recovery(manifest_path)


def test_zero_use_429_terminalizes_without_retry(recovery_fixture):
    root, manifest_path, source_state = recovery_fixture
    contract = source_state["copies"]["adopted/nansen-openapi.json"][0].read_bytes()
    selection = json.loads((root / "derived/selection.json").read_text())

    class RateLimitedNansen(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            if endpoint != recovery.HOLDINGS_ENDPOINT:
                return super().request_evidence(
                    method, endpoint, payload, caller_request_id=caller_request_id
                )
            self.calls.append((method, endpoint, payload, caller_request_id))
            response = _response(
                {"error": "rate limit"}, cost=1, used=0, remaining=self.remaining,
                status=429, retry_after="1",
            )
            raise NansenRequestFailure(
                "Nansen HTTP 429", transmitted=True, response=response
            )

    fake = RateLimitedNansen(contract, selection, recovery._request_plan(root))
    result = recovery.start_historical_recovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 14, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "unscorable"
    assert len(fake.calls) == 2
    assert sum(call[1] == recovery.HOLDINGS_ENDPOINT for call in fake.calls) == 1
    recovery.check_historical_recovery(manifest_path)


@pytest.mark.parametrize("fault", ["after_snapshot", "after_seal"])
def test_completed_finalization_is_crash_resumable(
    recovery_fixture, monkeypatch, fault
):
    root, manifest_path, source_state = recovery_fixture
    contract = source_state["copies"]["adopted/nansen-openapi.json"][0].read_bytes()
    selection = json.loads((root / "derived/selection.json").read_text())
    fake = FakeNansen(contract, selection, recovery._request_plan(root))
    raised = False
    if fault == "after_snapshot":
        original = recovery.write_bytes_once_or_adopt_exact

        def fail_once(path, content, *, metadata):
            nonlocal raised
            if not raised and Path(path) == root / "seals/completed.json":
                raised = True
                raise OSError("injected completed seal failure")
            return original(path, content, metadata=metadata)

        monkeypatch.setattr(recovery, "write_bytes_once_or_adopt_exact", fail_once)
        restore = lambda: monkeypatch.setattr(
            recovery, "write_bytes_once_or_adopt_exact", original
        )
    else:
        original = recovery.atomic_replace_bytes

        def fail_once(path, content):
            nonlocal raised
            if not raised and Path(path) == manifest_path:
                raised = True
                raise OSError("injected completed manifest failure")
            return original(path, content)

        monkeypatch.setattr(recovery, "atomic_replace_bytes", fail_once)
        restore = lambda: monkeypatch.setattr(recovery, "atomic_replace_bytes", original)

    with pytest.raises(OSError, match="injected completed"):
        recovery.start_historical_recovery(
            manifest_path,
            nansen=fake,
            clock=lambda: datetime(2026, 8, 18, 14, tzinfo=timezone.utc),
            sleep=lambda _: None,
        )
    calls = len(fake.calls)
    restore()
    result = recovery.start_historical_recovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 15, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "completed"
    assert len(fake.calls) == calls
    recovery.check_historical_recovery(manifest_path)


@pytest.mark.parametrize("fault", ["after_report", "after_snapshot", "after_seal"])
def test_unscorable_finalization_is_crash_resumable(
    recovery_fixture, monkeypatch, fault
):
    root, manifest_path, source_state = recovery_fixture
    contract = source_state["copies"]["adopted/nansen-openapi.json"][0].read_bytes()
    selection = json.loads((root / "derived/selection.json").read_text())

    class WrongSymbolNansen(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            response = super().request_evidence(
                method, endpoint, payload, caller_request_id=caller_request_id
            )
            if endpoint == recovery.HOLDINGS_ENDPOINT:
                response.body["data"][0]["token_symbol"] = "WRONG"
                return _response(response.body, cost=1, remaining=self.remaining)
            return response

    fake = WrongSymbolNansen(contract, selection, recovery._request_plan(root))
    raised = False
    if fault == "after_report":
        original = recovery.BudgetGuard.snapshot

        def fail_once(self, stage, *, recorded_at):
            nonlocal raised
            if not raised and stage == "unscorable":
                raised = True
                raise OSError("injected unscorable snapshot failure")
            return original(self, stage, recorded_at=recorded_at)

        monkeypatch.setattr(recovery.BudgetGuard, "snapshot", fail_once)
        restore = lambda: monkeypatch.setattr(recovery.BudgetGuard, "snapshot", original)
    elif fault == "after_snapshot":
        original = recovery.write_bytes_once_or_adopt_exact

        def fail_once(path, content, *, metadata):
            nonlocal raised
            if not raised and Path(path) == root / "seals/unscorable.json":
                raised = True
                raise OSError("injected unscorable seal failure")
            return original(path, content, metadata=metadata)

        monkeypatch.setattr(recovery, "write_bytes_once_or_adopt_exact", fail_once)
        restore = lambda: monkeypatch.setattr(
            recovery, "write_bytes_once_or_adopt_exact", original
        )
    else:
        original = recovery.atomic_replace_bytes

        def fail_once(path, content):
            nonlocal raised
            if not raised and Path(path) == manifest_path:
                raised = True
                raise OSError("injected unscorable manifest failure")
            return original(path, content)

        monkeypatch.setattr(recovery, "atomic_replace_bytes", fail_once)
        restore = lambda: monkeypatch.setattr(recovery, "atomic_replace_bytes", original)

    with pytest.raises(OSError, match="injected unscorable"):
        recovery.start_historical_recovery(
            manifest_path,
            nansen=fake,
            clock=lambda: datetime(2026, 8, 18, 14, tzinfo=timezone.utc),
            sleep=lambda _: None,
        )
    calls = len(fake.calls)
    report = (root / "REPORT.md").read_bytes()
    restore()
    result = recovery.start_historical_recovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 15, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "unscorable"
    assert len(fake.calls) == calls
    assert (root / "REPORT.md").read_bytes() == report
    recovery.check_historical_recovery(manifest_path)


def test_metadata_only_interruption_terminalizes_without_retransmission(
    recovery_fixture, monkeypatch
):
    root, manifest_path, source_state = recovery_fixture
    contract = source_state["copies"]["adopted/nansen-openapi.json"][0].read_bytes()
    selection = json.loads((root / "derived/selection.json").read_text())

    class DistinctTimestampNansen(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            self.calls.append((method, endpoint, payload, caller_request_id))
            assert endpoint == "account"
            return _response(
                {"plan": "free", "credits_remaining": self.remaining},
                cost=0,
                remaining=self.remaining,
                request_started_at="2026-08-18T14:00:02Z",
                response_retrieved_at="2026-08-18T14:00:03Z",
            )

    fake = DistinctTimestampNansen(contract, selection, recovery._request_plan(root))
    original = prospective_runner._install_bytes
    raised = False

    def fail_after_metadata(path, content, *, kind):
        nonlocal raised
        if not raised and Path(path).name == "attempt-1-response.json":
            raised = True
            raise OSError("injected recovery response write failure")
        return original(path, content, kind=kind)

    monkeypatch.setattr(prospective_runner, "_install_bytes", fail_after_metadata)
    clock_values = iter((
        datetime(2026, 8, 18, 14, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 18, 14, 0, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 18, 14, 0, 4, tzinfo=timezone.utc),
    ))
    with pytest.raises(OSError, match="injected recovery"):
        recovery.start_historical_recovery(
            manifest_path,
            nansen=fake,
            clock=lambda: next(clock_values),
            sleep=lambda _: None,
        )
    monkeypatch.setattr(prospective_runner, "_install_bytes", original)
    result = recovery.start_historical_recovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 15, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "unscorable"
    assert len(fake.calls) == 1
    recovery.check_historical_recovery(manifest_path)


@pytest.mark.parametrize(
    "relative",
    ["derived/selection.json", "adopted/screener-response.json"],
)
def test_frozen_or_adopted_source_tamper_fails_before_provider_access(
    recovery_fixture, relative
):
    root, manifest_path, _ = recovery_fixture

    class NoAccess:
        accessed = False

        def fetch_openapi(self):
            self.accessed = True
            raise AssertionError("provider access is forbidden")

    target = root / relative
    target.write_bytes(target.read_bytes() + b"\n")
    nansen = NoAccess()
    with pytest.raises(recovery.HistoricalRecoveryError, match="frozen input bytes"):
        recovery.start_historical_recovery(
            manifest_path,
            nansen=nansen,
            clock=lambda: datetime(2026, 8, 18, 14, tzinfo=timezone.utc),
            sleep=lambda _: None,
        )
    assert nansen.accessed is False


def test_bundle_symlink_fails_before_provider_access(recovery_fixture, tmp_path):
    root, manifest_path, _ = recovery_fixture
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "raw").symlink_to(outside, target_is_directory=True)

    class NoAccess:
        accessed = False

        def fetch_openapi(self):
            self.accessed = True
            raise AssertionError("provider access is forbidden")

    nansen = NoAccess()
    with pytest.raises(recovery.HistoricalDiscoveryError, match="contains a symlink"):
        recovery.start_historical_recovery(
            manifest_path,
            nansen=nansen,
            clock=lambda: datetime(2026, 8, 18, 14, tzinfo=timezone.utc),
            sleep=lambda _: None,
        )
    assert nansen.accessed is False
