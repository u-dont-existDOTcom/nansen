from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
OPENAPI_RAW = b'{"openapi":"3.1.0","info":{"title":"fixture"}}\n'


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    experiments = repo / "research/experiments"
    for name in (
        "2026-08-16-seven-token-pilot",
        "2026-08-16-community-signal-shadow",
        "2026-08-17-paper-strategy-feasibility",
    ):
        shutil.copytree(ROOT / "research/experiments" / name, experiments / name)
    for relative in (
        "docs/superpowers/specs/2026-08-17-gpt-prospective-pilot-design.md",
        "docs/superpowers/specs/2026-08-17-gpt-prospective-pilot-account-baseline-v2.md",
        "docs/superpowers/specs/2026-08-18-gpt-prospective-pilot-completed-flow-v3.md",
        "docs/superpowers/specs/2026-08-18-gpt-prospective-pilot-contract-context-v4.md",
        "docs/superpowers/specs/2026-08-18-gpt-prospective-pilot-schema-subset-v5.md",
        "docs/superpowers/specs/2026-08-17-nansen-api-contract-snapshot.json",
    ):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    contract = repo / "docs/superpowers/specs/2026-08-17-nansen-api-contract-snapshot.json"
    value = json.loads(contract.read_text())
    value["source_sha256"] = hashlib.sha256(OPENAPI_RAW).hexdigest()
    contract.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    return repo


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _flow_rows(*, count=26, decreasing=False):
    last = datetime(2026, 8, 17, 10, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        end = last - timedelta(hours=count - 1 - index)
        amount = 1000 - index if decreasing else 100 + index * index
        rows.append({
            "date": _iso(end - timedelta(hours=1)),
            "bucket_end": _iso(end),
            "price_usd": 10 + index,
            "token_amount": amount,
            "value_usd": (10 + index) * amount,
            "holders_count": 1000 + index,
            "total_inflows": 10 + index,
            "total_outflows": 1,
            "total_inflows_count": 2,
            "total_outflows_count": 1,
            "is_complete": True,
        })
    return rows


class FakeNansen:
    def __init__(self, *, flow_count=26, decreasing=False):
        self.calls = []
        self.remaining = 10
        self.flow_count = flow_count
        self.decreasing = decreasing

    def fetch_openapi(self):
        self.calls.append(("OPENAPI", "openapi", None))
        return OPENAPI_RAW

    def request_evidence(self, method, endpoint, payload, *, caller_request_id):
        from src.nansen_signal_lab.client import NansenEvidenceResponse

        self.calls.append((method, endpoint, payload))
        if endpoint == "account":
            body = {"plan": "pro", "credits_remaining": 10}
            cost = used = 0
            timestamp = "2026-08-17T10:37:00Z"
        else:
            self.remaining -= 1
            cost = used = 1
            timestamp = (
                "2026-08-17T15:00:00Z"
                if endpoint in {"tgm/dex-trades", "tgm/token-ohlcv"}
                else "2026-08-17T10:37:00Z"
            )
            if endpoint == "token-screener":
                body = {
                    "data": [{
                        "chain": "solana",
                        "token_address": "So111",
                        "token_symbol": "SOL",
                        "price_usd": 10.0,
                        "volume_usd": 2_000_000.0,
                        "liquidity": 1_000_000.0,
                        "market_cap_usd": 20_000_000.0,
                        "netflow": 50_000.0,
                    }],
                    "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
                }
            elif endpoint == "tgm/token-information":
                body = {"data": {"price_usd": 35.0, "market_cap_usd": 20_000_000.0}}
            elif endpoint == "tgm/flow-intelligence":
                body = {"data": {"smart_money_holders_count": 25, "netflow_usd": 50_000.0}}
            elif endpoint == "tgm/flows":
                body = {
                    "data": _flow_rows(count=self.flow_count, decreasing=self.decreasing),
                    "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
                }
            elif endpoint == "tgm/dex-trades":
                side = payload["filters"]["action"]
                at = datetime.fromisoformat(payload["date"]["from"].replace("Z", "+00:00"))
                price = 100.0 if side == "BUY" else 110.0
                body = {
                    "data": [{
                        "block_timestamp": _iso(at + timedelta(seconds=1)),
                        "transaction_hash": f"{side.lower()}-1",
                        "action": side,
                        "token_amount": 10.0,
                        "estimated_swap_price_usd": price,
                        "estimated_value_usd": 10 * price,
                    }],
                    "pagination": {"page": payload["pagination"]["page"], "per_page": 1000, "is_last_page": True},
                }
            elif endpoint == "tgm/token-ohlcv":
                start = datetime.fromisoformat(payload["date"]["from"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(payload["date"]["to"].replace("Z", "+00:00"))
                rows = []
                cursor = start
                while cursor < end:
                    rows.append({
                        "interval_start": _iso(cursor),
                        "open": 100.0,
                        "high": 112.0,
                        "low": 98.0,
                        "close": 110.0 if cursor + timedelta(minutes=5) == end else 101.0,
                        "volume": 1000.0,
                        "market_cap": 20_000_000.0,
                    })
                    cursor += timedelta(minutes=5)
                body = {"data": rows, "truncated": False}
            else:  # pragma: no cover - an unexpected endpoint must fail loudly.
                raise AssertionError(endpoint)
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        headers = {
            "X-Request-Id": caller_request_id,
            "X-Nansen-Credits-Cost": str(cost),
            "X-Nansen-Credits-Used": str(used),
            "X-Nansen-Credits-Remaining": str(self.remaining),
        }
        return NansenEvidenceResponse(
            body=body,
            body_parse_status="json_object",
            raw_body=raw,
            status_code=200,
            request_started_at=timestamp,
            response_retrieved_at=timestamp,
            response_headers=headers,
            request_id=caller_request_id,
            credit_cost=cost,
            credit_used=used,
            credit_remaining=self.remaining,
            credit_header_errors=(),
        )


def _openai_response(value, response_id):
    from src.nansen_signal_lab.openai_client import OpenAIEvidenceResponse

    raw = json.dumps({
        "id": response_id,
        "model": "gpt-5.6-sol",
        "status": "completed",
        "output": [{"type": "message", "content": [{
            "type": "output_text", "text": json.dumps(value),
        }]}],
        "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
    }).encode()
    return OpenAIEvidenceResponse.from_raw(
        raw_body=raw,
        status_code=200,
        request_started_at="2026-08-17T10:37:00Z",
        response_retrieved_at="2026-08-17T10:37:00Z",
        response_headers={"x-request-id": response_id},
    )


class FakeOpenAI:
    def __init__(self, *, pass1_action="LONG", pass2_action="LONG"):
        self.calls = []
        self.pass1_action = pass1_action
        self.pass2_action = pass2_action

    def preflight_model(self, model_id):
        from src.nansen_signal_lab.openai_client import OpenAIEvidenceResponse

        self.calls.append(("preflight", model_id))
        return OpenAIEvidenceResponse.from_raw(
            raw_body=json.dumps({"id": model_id}).encode(),
            status_code=200,
            request_started_at="2026-08-17T10:37:00Z",
            response_retrieved_at="2026-08-17T10:37:00Z",
            response_headers={},
        )

    def create_structured(self, **kwargs):
        self.calls.append(("structured", kwargs["schema_name"]))
        input_value = kwargs["input_json"]
        if kwargs["schema_name"] == "prospective_pass_1":
            value = {
                "action": self.pass1_action,
                "confidence": 0.7,
                "expected_direction_4h": "UP" if self.pass1_action == "LONG" else "FLAT",
                "evidence_for": ["smart_money.final_feature.holdings_change_1h_pct"],
                "evidence_against": [],
                "missing_evidence": [],
                "rationale": "The sealed point-in-time flow is positive.",
                "risk_flags": ["EXECUTION_RISK"],
            }
            return _openai_response(value, "pass1")
        ids = [item["id"] for item in input_value["theory_records"]]
        value = {
            "snapshot_sha256": input_value["snapshot_sha256"],
            "pass1": {"response_sha256": input_value["pass1"]["response_sha256"]},
            "pass1_assessment": "UPHOLD",
            "final_action": self.pass2_action,
            "theory_assessments": [{
                "theory_id": theory_id,
                "applicability": "APPLICABLE",
                "predicate_alignment": "SUPPORTS_LONG",
                "rationale": "Compared only with the frozen predicate record.",
            } for theory_id in ids],
            "conflicts": [],
            "evidence_for": ["smart_money.final_feature.holdings_change_1h_pct"],
            "evidence_against": [],
            "missing_evidence": [],
            "rationale": "The independent critique upholds the sealed decision.",
        }
        return _openai_response(value, "pass2")


def test_initialize_pilot_creates_offline_preregistered_bundle(tmp_path):
    from src.nansen_signal_lab.prospective_runner import check_pilot, initialize_pilot

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/fixture-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
    )
    assert bundle.manifest["stage"] == "preregistered"
    assert bundle.manifest["max_nansen_calls"] == 10
    assert bundle.manifest["max_nansen_credits"] == 10
    assert (bundle.root / "PREREGISTRATION.md").is_file()
    assert not (bundle.root / "REPORT.md").exists()
    assert bundle.root / "PREREGISTRATION.md" in check_pilot(bundle)


def test_v2_account_fallback_is_explicitly_preregistered_and_auditable(tmp_path):
    from dataclasses import replace
    from src.nansen_signal_lab.prospective_runner import initialize_pilot, start_pilot

    class AccountHeadersOmitted(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            response = super().request_evidence(
                method, endpoint, payload, caller_request_id=caller_request_id
            )
            if endpoint != "account":
                return response
            headers = {
                "X-Request-Id": caller_request_id,
                "X-Nansen-Credits-Cost": "0",
            }
            return replace(
                response,
                response_headers=headers,
                credit_used=None,
                credit_remaining=None,
            )

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/fallback-v2-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
        protocol_version="account-baseline-v2",
    )
    assert bundle.manifest["design_path"].endswith("account-baseline-v2.md")

    decision = start_pilot(
        bundle,
        nansen=AccountHeadersOmitted(),
        openai=FakeOpenAI(),
        clock=lambda: datetime(2026, 8, 17, 10, 37, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )

    assert decision.manifest["stage"] == "decision_sealed"
    derivation_path = decision.root / "derived/account-baseline.json"
    derivation = json.loads(derivation_path.read_text())
    assert derivation["rule_version"] == "account-baseline-v2"
    assert derivation["observed"] == {
        "credit_cost": 0,
        "credit_remaining": None,
        "credit_used": None,
    }
    assert derivation["effective"] == {
        "credit_cost": 0,
        "credit_remaining": 10,
        "credit_used": 0,
    }
    assert any(
        item["path"] == "derived/account-baseline.json"
        for item in decision.manifest["artifacts"]
    )
    budget = json.loads((decision.root / "budget/head.json").read_text())
    account = next(
        item for item in budget["entries"]
        if item["logical_request_id"] == "account-preflight"
    )
    assert account["state"] == "confirmed_zero"
    assert account["credit_used"] == 0
    assert account["credit_remaining"] == 10


def test_v2_fallback_does_not_relax_paid_response_headers(tmp_path):
    from dataclasses import replace
    from src.nansen_signal_lab.prospective_runner import initialize_pilot, start_pilot

    class MissingPaidHeader(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            response = super().request_evidence(
                method, endpoint, payload, caller_request_id=caller_request_id
            )
            if endpoint == "token-screener":
                headers = dict(response.response_headers)
                headers.pop("X-Nansen-Credits-Remaining")
                return replace(
                    response,
                    response_headers=headers,
                    credit_remaining=None,
                )
            return response

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/paid-strict-v2-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
        protocol_version="account-baseline-v2",
    )
    nansen = MissingPaidHeader()
    final = start_pilot(
        bundle,
        nansen=nansen,
        openai=FakeOpenAI(),
        clock=lambda: datetime(2026, 8, 17, 10, 37, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )

    assert final.manifest["stage"] == "unscorable"
    assert [
        endpoint for method, endpoint, _ in nansen.calls if method != "OPENAPI"
    ] == ["account", "token-screener"]
    assert "pricing evidence is incomplete" in (final.root / "REPORT.md").read_text()


def test_v2_fallback_recovers_archived_response_without_retransmission(
    tmp_path, monkeypatch
):
    from dataclasses import replace
    from src.nansen_signal_lab import prospective_runner
    from src.nansen_signal_lab.budget import BudgetGuard
    from src.nansen_signal_lab.prospective_runner import _nansen_call, initialize_pilot

    class AccountHeadersOmitted(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            response = super().request_evidence(
                method, endpoint, payload, caller_request_id=caller_request_id
            )
            assert endpoint == "account"
            return replace(
                response,
                response_headers={
                    "X-Request-Id": caller_request_id,
                    "X-Nansen-Credits-Cost": "0",
                },
                credit_used=None,
                credit_remaining=None,
            )

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/fallback-recovery-v2-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
        protocol_version="account-baseline-v2",
    )
    guard = BudgetGuard(bundle.root)
    nansen = AccountHeadersOmitted()
    original_confirm = BudgetGuard.confirm_account_baseline

    def interrupt_before_ledger_confirm(*_args, **_kwargs):
        raise KeyboardInterrupt("fixture interruption after fallback derivation")

    monkeypatch.setattr(
        BudgetGuard, "confirm_account_baseline", interrupt_before_ledger_confirm
    )
    with pytest.raises(KeyboardInterrupt, match="after fallback derivation"):
        _nansen_call(
            root=bundle.root,
            guard=guard,
            nansen=nansen,
            logical_request_id="account-preflight",
            method="GET",
            endpoint="account",
            payload=None,
            expected_credits=1,
            clock=lambda: datetime(2026, 8, 17, 10, 37, tzinfo=timezone.utc),
            sleep=lambda _seconds: None,
            account_baseline_version="account-baseline-v2",
            openapi_sha256=hashlib.sha256(OPENAPI_RAW).hexdigest(),
        )
    assert len([call for call in nansen.calls if call[0] != "OPENAPI"]) == 1
    assert (bundle.root / "derived/account-baseline.json").is_file()

    monkeypatch.setattr(BudgetGuard, "confirm_account_baseline", original_confirm)
    response, paths = _nansen_call(
        root=bundle.root,
        guard=BudgetGuard(bundle.root),
        nansen=nansen,
        logical_request_id="account-preflight",
        method="GET",
        endpoint="account",
        payload=None,
        expected_credits=1,
        clock=lambda: datetime(2026, 8, 17, 10, 38, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
        account_baseline_version="account-baseline-v2",
        openapi_sha256=hashlib.sha256(OPENAPI_RAW).hexdigest(),
    )

    assert response.credit_remaining is None
    assert bundle.root / "derived/account-baseline.json" in paths
    assert len([call for call in nansen.calls if call[0] != "OPENAPI"]) == 1
    totals = BudgetGuard(bundle.root).replay()
    assert (totals.calls, totals.credits, totals.provider_remaining) == (0, 0, 10)


def test_v3_runner_uses_completed_hour_range_for_both_flow_labels(tmp_path):
    from dataclasses import replace
    from src.nansen_signal_lab.prospective_runner import initialize_pilot, start_pilot

    class V3Nansen(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            response = super().request_evidence(
                method, endpoint, payload, caller_request_id=caller_request_id
            )
            if endpoint != "account":
                return response
            return replace(
                response,
                response_headers={
                    "X-Request-Id": caller_request_id,
                    "X-Nansen-Credits-Cost": "0",
                },
                credit_used=None,
                credit_remaining=None,
            )

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/completed-flow-v3-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
        protocol_version="completed-flow-v3",
    )
    nansen = V3Nansen()
    decision = start_pilot(
        bundle,
        nansen=nansen,
        openai=FakeOpenAI(),
        clock=lambda: datetime(
            2026, 8, 17, 10, 37, 42, 123456, tzinfo=timezone.utc
        ),
        sleep=lambda _seconds: None,
    )

    assert decision.manifest["stage"] == "decision_sealed"
    assert decision.manifest["design_path"].endswith("completed-flow-v3.md")
    assert (decision.root / "derived/account-baseline.json").is_file()
    flow_payloads = [
        payload for method, endpoint, payload in nansen.calls
        if endpoint == "tgm/flows"
    ]
    assert [payload["label"] for payload in flow_payloads] == [
        "smart_money", "exchange"
    ]
    assert [payload["date"] for payload in flow_payloads] == [
        {
            "from": "2026-08-16T09:00:00Z",
            "to": "2026-08-17T09:59:59.999999Z",
        },
        {
            "from": "2026-08-16T09:00:00Z",
            "to": "2026-08-17T09:59:59.999999Z",
        },
    ]


def test_v4_runner_accepts_live_contract_context_shapes(tmp_path):
    from dataclasses import replace
    from src.nansen_signal_lab.prospective_runner import initialize_pilot, start_pilot

    class V4Nansen(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            response = super().request_evidence(
                method, endpoint, payload, caller_request_id=caller_request_id
            )
            body = response.body
            if endpoint == "account":
                return replace(
                    response,
                    response_headers={
                        "X-Request-Id": caller_request_id,
                        "X-Nansen-Credits-Cost": "0",
                    },
                    credit_used=None,
                    credit_remaining=None,
                )
            if endpoint == "tgm/token-information":
                body = {
                    "data": {
                        "contract_address": "So111",
                        "name": "LEAK",
                        "spot_metrics": {
                            "volume_total_usd": 1000.0,
                            "liquidity_usd": 250000.0,
                            "total_holders": 99,
                        },
                        "token_details": {"market_cap_usd": 1000000.0},
                    },
                    "warnings": None,
                }
            elif endpoint == "tgm/flow-intelligence":
                body = {
                    "data": [{
                        "smart_trader_net_flow_usd": 25.0,
                        "smart_trader_wallet_count": 4,
                    }],
                    "warnings": ["provider warning"],
                }
            elif endpoint == "tgm/flows" and payload["label"] == "exchange":
                body = dict(body, warnings=None)
            else:
                return response
            raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            return replace(response, body=body, raw_body=raw)

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/contract-context-v4-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
        protocol_version="contract-context-v4",
    )
    decision = start_pilot(
        bundle,
        nansen=V4Nansen(),
        openai=FakeOpenAI(),
        clock=lambda: datetime(2026, 8, 17, 10, 37, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )

    assert decision.manifest["stage"] == "decision_sealed"
    normalized = json.loads((decision.root / "normalized/snapshot.json").read_text())
    assert normalized["token_information"]["data"] == {
        "holders_count": 99,
        "liquidity_usd": 250000.0,
        "market_cap_usd": 1000000.0,
        "volume_usd": 1000.0,
    }
    assert normalized["flow_intelligence"]["data"] == {
        "smart_trader_net_flow_usd": 25.0,
        "smart_trader_wallet_count": 4,
    }
    assert normalized["exchange"]["warnings"] == {"count": 0, "present": False}


def test_v5_reuses_exact_v4_snapshot_and_never_calls_nansen(tmp_path):
    from dataclasses import replace

    from src.nansen_signal_lab.budget import BudgetGuard
    from src.nansen_signal_lab.openai_client import OpenAIEvidenceResponse, OpenAIError
    from src.nansen_signal_lab.prospective_runner import (
        PilotError,
        check_pilot,
        initialize_model_successor,
        initialize_pilot,
        replay_pilot,
        settle_pilot,
        start_pilot,
    )

    class LiveV4Nansen(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            response = super().request_evidence(
                method, endpoint, payload, caller_request_id=caller_request_id
            )
            body = response.body
            if endpoint == "account":
                return replace(
                    response,
                    response_headers={
                        "X-Request-Id": caller_request_id,
                        "X-Nansen-Credits-Cost": "0",
                    },
                    credit_used=None,
                    credit_remaining=None,
                )
            if endpoint == "tgm/token-information":
                body = {
                    "data": {
                        "spot_metrics": {
                            "volume_total_usd": 1000.0,
                            "liquidity_usd": 250000.0,
                            "total_holders": 99,
                        },
                        "token_details": {"market_cap_usd": 1000000.0},
                    },
                    "warnings": None,
                }
            elif endpoint == "tgm/flow-intelligence":
                body = {"data": [{"smart_trader_net_flow_usd": 25.0}]}
            elif endpoint == "tgm/flows" and payload["label"] == "exchange":
                body = dict(body, warnings=None)
            else:
                return response
            raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            return replace(response, body=body, raw_body=raw)

    class SchemaRejectingOpenAI(FakeOpenAI):
        def create_structured(self, **kwargs):
            self.calls.append(("structured", kwargs["schema_name"]))
            response = OpenAIEvidenceResponse.from_raw(
                raw_body=b'{"error":{"code":"invalid_json_schema"}}',
                status_code=400,
                request_started_at="2026-08-17T10:37:00Z",
                response_retrieved_at="2026-08-17T10:37:01Z",
                response_headers={"x-request-id": "schema-reject"},
            )
            raise OpenAIError("OpenAI HTTP 400", transmitted=True, response=response)

    repo = _repo(tmp_path)
    source = initialize_pilot(
        repo / "research/experiments/source-v4-terminal",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
        protocol_version="contract-context-v4",
    )
    source = start_pilot(
        source,
        nansen=LiveV4Nansen(),
        openai=SchemaRejectingOpenAI(),
        clock=lambda: datetime(2026, 8, 17, 10, 38, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )
    assert source.manifest["stage"] == "unscorable"
    source_snapshot = source.root / "normalized/snapshot.json"
    source_selection = source.root / "derived/selection.json"

    successor = initialize_model_successor(
        repo / "research/experiments/schema-subset-v5",
        source_manifest=source.manifest_path,
        created_at=datetime(2026, 8, 17, 11, tzinfo=timezone.utc),
    )
    assert successor.manifest["stage"] == "snapshot_collected"
    assert (successor.root / "normalized/snapshot.json").read_bytes() == source_snapshot.read_bytes()
    assert (successor.root / "derived/selection.json").read_bytes() == source_selection.read_bytes()
    assert BudgetGuard(successor.root).replay().calls == 0

    class NeverNansen:
        def __getattr__(self, name):
            raise AssertionError(f"Nansen must not be touched: {name}")

    openai = FakeOpenAI()
    decision = start_pilot(
        successor,
        nansen=NeverNansen(),
        openai=openai,
        clock=lambda: datetime(2026, 8, 17, 11, 1, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )
    assert decision.manifest["stage"] == "decision_sealed"
    assert openai.calls == [
        ("preflight", "gpt-5.6-sol"),
        ("structured", "prospective_pass_1"),
        ("structured", "prospective_pass_2"),
    ]
    assert (decision.root / "MODEL-RESULT.md").is_file()
    assert BudgetGuard(decision.root).replay().calls == 0
    assert replay_pilot(decision)["nansen_credits"] == 0
    assert decision.root / "MODEL-RESULT.md" in check_pilot(decision)
    with pytest.raises(PilotError, match="model-only"):
        settle_pilot(
            decision,
            nansen=NeverNansen(),
            clock=lambda: datetime(2026, 8, 17, 16, tzinfo=timezone.utc),
        )


def test_full_fake_lifecycle_uses_exact_billable_calls_and_replays_offline(tmp_path):
    from src.nansen_signal_lab.prospective_runner import (
        check_pilot,
        initialize_pilot,
        replay_pilot,
        settle_pilot,
        start_pilot,
    )

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/fixture-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
    )
    nansen = FakeNansen()
    openai = FakeOpenAI()
    decision = start_pilot(
        bundle,
        nansen=nansen,
        openai=openai,
        clock=lambda: datetime(2026, 8, 17, 10, 37, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )
    assert decision.manifest["stage"] == "decision_sealed"
    final = settle_pilot(
        decision,
        nansen=nansen,
        clock=lambda: datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc),
    )
    assert final.manifest["stage"] == "settled"
    billable = [
        endpoint + (
            f":{payload['label']}" if endpoint == "tgm/flows"
            else f":{payload['filters']['action']}:{payload['pagination']['page']}"
            if endpoint == "tgm/dex-trades"
            else ""
        )
        for method, endpoint, payload in nansen.calls
        if method != "OPENAPI" and endpoint != "account"
    ]
    assert billable == [
        "token-screener",
        "tgm/token-information",
        "tgm/flow-intelligence",
        "tgm/flows:smart_money",
        "tgm/flows:exchange",
        "tgm/dex-trades:BUY:1",
        "tgm/dex-trades:SELL:1",
        "tgm/token-ohlcv",
    ]
    before = list(nansen.calls)
    replay = replay_pilot(final)
    checked = check_pilot(final)
    assert replay["stage"] == "settled"
    assert replay["nansen_calls"] == 8
    assert replay["nansen_credits"] == 8
    assert final.manifest_path in checked
    assert nansen.calls == before
    assert (final.root / "REPORT.md").is_file()


def test_no_long_mixed_abstain_unavailable_skips_dex_but_collects_ohlcv(tmp_path):
    from src.nansen_signal_lab.prospective_runner import initialize_pilot, settle_pilot, start_pilot

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/no-long-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
    )
    nansen = FakeNansen(flow_count=25, decreasing=True)
    decision = start_pilot(
        bundle,
        nansen=nansen,
        openai=FakeOpenAI(pass1_action="ABSTAIN", pass2_action="ABSTAIN"),
        clock=lambda: datetime(2026, 8, 17, 10, 37, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )
    final = settle_pilot(
        decision,
        nansen=nansen,
        clock=lambda: datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc),
    )
    endpoints = [endpoint for method, endpoint, _ in nansen.calls if method != "OPENAPI"]
    assert "tgm/dex-trades" not in endpoints
    assert endpoints.count("tgm/token-ohlcv") == 1
    entry = json.loads((final.root / "derived/entry-observation.json").read_text())
    assert entry["fill_required"] is False
    comparison = json.loads((final.root / "derived/comparison.json").read_text())
    assert comparison["score"]["gpt_beats_frozen_strategies"] == "unscorable"


def test_incomplete_second_dex_page_is_terminal_and_never_spends_ohlcv(tmp_path):
    from dataclasses import replace
    from src.nansen_signal_lab.prospective_runner import initialize_pilot, settle_pilot, start_pilot

    class IncompleteDex(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            response = super().request_evidence(
                method, endpoint, payload, caller_request_id=caller_request_id
            )
            if endpoint == "tgm/dex-trades" and payload["filters"]["action"] == "BUY":
                body = dict(response.body)
                body["pagination"] = dict(body["pagination"], is_last_page=False)
                raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
                return replace(response, body=body, raw_body=raw)
            return response

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/incomplete-dex-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
    )
    nansen = IncompleteDex()
    decision = start_pilot(
        bundle,
        nansen=nansen,
        openai=FakeOpenAI(),
        clock=lambda: datetime(2026, 8, 17, 10, 37, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )
    final = settle_pilot(
        decision,
        nansen=nansen,
        clock=lambda: datetime(2026, 8, 17, 15, 0, tzinfo=timezone.utc),
    )
    assert final.manifest["stage"] == "unscorable"
    assert not any(endpoint == "tgm/token-ohlcv" for _, endpoint, _ in nansen.calls)
    calls = list(nansen.calls)
    assert settle_pilot(
        final,
        nansen=nansen,
        clock=lambda: datetime(2026, 8, 17, 15, 1, tzinfo=timezone.utc),
    ).manifest["stage"] == "unscorable"
    assert nansen.calls == calls


def test_early_settlement_refuses_before_any_outcome_call(tmp_path):
    from src.nansen_signal_lab.prospective_runner import PilotError, initialize_pilot, settle_pilot, start_pilot

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/early-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
    )
    nansen = FakeNansen()
    decision = start_pilot(
        bundle,
        nansen=nansen,
        openai=FakeOpenAI(),
        clock=lambda: datetime(2026, 8, 17, 10, 37, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )
    calls = list(nansen.calls)
    with pytest.raises(PilotError, match="early"):
        settle_pilot(
            decision,
            nansen=nansen,
            clock=lambda: datetime(2026, 8, 17, 14, 50, tzinfo=timezone.utc),
        )
    assert nansen.calls == calls


def test_positive_account_preflight_seals_terminal_and_never_repeats_http(tmp_path):
    from dataclasses import replace
    from src.nansen_signal_lab.prospective_runner import initialize_pilot, start_pilot

    class PaidAccount(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            response = super().request_evidence(
                method, endpoint, payload, caller_request_id=caller_request_id
            )
            if endpoint == "account":
                self.remaining = 9
                body = {"plan": "pro", "credits_remaining": 9}
                raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
                headers = dict(response.response_headers)
                headers.update({
                    "X-Nansen-Credits-Cost": "1",
                    "X-Nansen-Credits-Used": "1",
                    "X-Nansen-Credits-Remaining": "9",
                })
                return replace(
                    response,
                    body=body,
                    raw_body=raw,
                    response_headers=headers,
                    credit_cost=1,
                    credit_used=1,
                    credit_remaining=9,
                )
            return response

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/paid-account-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
    )
    nansen = PaidAccount()
    openai = FakeOpenAI()
    final = start_pilot(
        bundle,
        nansen=nansen,
        openai=openai,
        clock=lambda: datetime(2026, 8, 17, 10, 37, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )
    assert final.manifest["stage"] == "unscorable"
    assert [endpoint for method, endpoint, _ in nansen.calls if method != "OPENAPI"] == ["account"]
    calls = list(nansen.calls)
    assert start_pilot(
        final,
        nansen=nansen,
        openai=openai,
        clock=lambda: datetime(2026, 8, 17, 10, 38, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    ).manifest["stage"] == "unscorable"
    assert nansen.calls == calls


def test_preexisting_transmissible_nansen_request_is_never_retransmitted(tmp_path):
    from src.nansen_signal_lab.artifacts import write_json_once
    from src.nansen_signal_lab.budget import BudgetGuard, canonical_request_sha256
    from src.nansen_signal_lab.prospective_runner import (
        PilotError,
        _nansen_call,
        _nansen_paths,
        initialize_pilot,
    )

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/ambiguous-request-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
    )
    guard = BudgetGuard(bundle.root)
    payload = {"chains": ["solana"]}
    identity = canonical_request_sha256("POST", "token-screener", payload)
    reservation = guard.reserve("screen", identity, "token-screener", 1)
    request_path, _, _ = _nansen_paths(bundle.root, reservation)
    write_json_once(request_path, {
        "method": "POST",
        "endpoint": "token-screener",
        "payload": payload,
        "request_sha256": identity,
        "caller_request_id": "screen",
        "request_started_at": "2026-08-17T10:00:00Z",
        "artifact_written_at": "2026-08-17T10:00:00Z",
        "transmission_may_begin": True,
    })

    class NoNetwork:
        def request_evidence(self, *_args, **_kwargs):
            raise AssertionError("ambiguous request was retransmitted")

    with pytest.raises(PilotError, match="retransmission is forbidden"):
        _nansen_call(
            root=bundle.root,
            guard=guard,
            nansen=NoNetwork(),
            logical_request_id="screen",
            method="POST",
            endpoint="token-screener",
            payload=payload,
            expected_credits=1,
            clock=lambda: datetime(2026, 8, 17, 10, tzinfo=timezone.utc),
            sleep=lambda _seconds: None,
        )
    assert guard.replay().entries[0].state == "ambiguous"


def test_deterministic_artifact_adoption_preserves_first_durable_timestamp(tmp_path):
    from src.nansen_signal_lab.prospective_runner import _install_timestamped_json

    path = tmp_path / "derived/value.json"
    first = _install_timestamped_json(
        path,
        {"schema_version": 1, "value": "same"},
        kind="fixture",
        clock=lambda: datetime(2026, 8, 17, 10, tzinfo=timezone.utc),
    )
    original = first.read_bytes()
    second = _install_timestamped_json(
        path,
        {"schema_version": 1, "value": "same"},
        kind="fixture",
        clock=lambda: datetime(2026, 8, 17, 11, tzinfo=timezone.utc),
    )
    assert second.read_bytes() == original
    assert json.loads(original)["artifact_written_at"] == "2026-08-17T10:00:00Z"


def test_start_resumes_sealed_snapshot_without_repeating_nansen_or_preflight(
    tmp_path, monkeypatch
):
    from src.nansen_signal_lab import prospective_runner
    from src.nansen_signal_lab.prospective_runner import initialize_pilot, start_pilot

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/resume-snapshot-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
    )
    nansen = FakeNansen()
    first_openai = FakeOpenAI()
    original_load = prospective_runner.load_frozen_records

    def interrupt_after_snapshot(*_args, **_kwargs):
        raise KeyboardInterrupt("fixture interruption after snapshot seal")

    monkeypatch.setattr(prospective_runner, "load_frozen_records", interrupt_after_snapshot)
    with pytest.raises(KeyboardInterrupt, match="after snapshot seal"):
        start_pilot(
            bundle,
            nansen=nansen,
            openai=first_openai,
            clock=lambda: datetime(2026, 8, 17, 10, 37, tzinfo=timezone.utc),
            sleep=lambda _seconds: None,
        )

    interrupted = prospective_runner.load_prospective_manifest(bundle.manifest_path)
    assert interrupted.manifest["stage"] == "snapshot_collected"
    nansen_calls = list(nansen.calls)
    preflight_calls = list(first_openai.calls)
    monkeypatch.setattr(prospective_runner, "load_frozen_records", original_load)

    resumed = start_pilot(
        interrupted,
        nansen=nansen,
        openai=first_openai,
        clock=lambda: datetime(2026, 8, 17, 10, 38, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )
    assert resumed.manifest["stage"] == "decision_sealed"
    assert nansen.calls == nansen_calls
    assert first_openai.calls[: len(preflight_calls)] == preflight_calls
    assert [call[0] for call in first_openai.calls].count("preflight") == 1


def test_settle_resumes_entry_seal_without_repeating_buy_calls(tmp_path, monkeypatch):
    from src.nansen_signal_lab import prospective_runner
    from src.nansen_signal_lab.prospective_runner import (
        initialize_pilot,
        settle_pilot,
        start_pilot,
    )

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/resume-entry-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
    )
    nansen = FakeNansen()
    decision = start_pilot(
        bundle,
        nansen=nansen,
        openai=FakeOpenAI(),
        clock=lambda: datetime(2026, 8, 17, 10, 37, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )
    original_collect = prospective_runner._collect_trade_pages

    def interrupt_before_exit(**kwargs):
        if kwargs["side"] == "SELL":
            raise KeyboardInterrupt("fixture interruption after entry seal")
        return original_collect(**kwargs)

    monkeypatch.setattr(prospective_runner, "_collect_trade_pages", interrupt_before_exit)
    with pytest.raises(KeyboardInterrupt, match="after entry seal"):
        settle_pilot(
            decision,
            nansen=nansen,
            clock=lambda: datetime(2026, 8, 17, 15, tzinfo=timezone.utc),
        )

    interrupted = prospective_runner.load_prospective_manifest(decision.manifest_path)
    assert interrupted.manifest["stage"] == "entry_observed"
    buy_calls = [
        call for call in nansen.calls
        if call[1] == "tgm/dex-trades" and call[2]["filters"]["action"] == "BUY"
    ]
    monkeypatch.setattr(prospective_runner, "_collect_trade_pages", original_collect)

    final = settle_pilot(
        interrupted,
        nansen=nansen,
        clock=lambda: datetime(2026, 8, 17, 15, 1, tzinfo=timezone.utc),
    )
    assert final.manifest["stage"] == "settled"
    assert [
        call for call in nansen.calls
        if call[1] == "tgm/dex-trades" and call[2]["filters"]["action"] == "BUY"
    ] == buy_calls


def test_archived_zero_credit_429_is_adopted_before_the_single_retry(tmp_path):
    from src.nansen_signal_lab.artifacts import write_json_once
    from src.nansen_signal_lab.budget import BudgetGuard, canonical_request_sha256
    from src.nansen_signal_lab.client import NansenEvidenceResponse
    from src.nansen_signal_lab.prospective_runner import (
        _archive_nansen_response,
        _nansen_call,
        _nansen_paths,
        initialize_pilot,
    )

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/retry-recovery-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
    )
    guard = BudgetGuard(bundle.root)
    payload = None
    identity = canonical_request_sha256("GET", "account", payload)
    reservation = guard.reserve("screen", identity, "account", 1)
    request_path, _, _ = _nansen_paths(bundle.root, reservation)
    write_json_once(request_path, {
        "method": "GET",
        "endpoint": "account",
        "payload": payload,
        "request_sha256": identity,
        "caller_request_id": "screen",
        "request_started_at": "2026-08-17T10:00:00Z",
        "artifact_written_at": "2026-08-17T10:00:00Z",
        "transmission_may_begin": True,
    })
    reservation = guard.bind_request_artifact(reservation, hashlib.sha256(request_path.read_bytes()).hexdigest())
    zero_429 = NansenEvidenceResponse(
        body={"error": "rate limited"},
        body_parse_status="json_object",
        raw_body=b'{"error":"rate limited"}',
        status_code=429,
        request_started_at="2026-08-17T10:00:00Z",
        response_retrieved_at="2026-08-17T10:00:00Z",
        response_headers={"Retry-After": "0"},
        request_id="screen",
        credit_cost=1,
        credit_used=0,
        credit_remaining=10,
        credit_header_errors=(),
    )
    _archive_nansen_response(
        bundle.root,
        reservation,
        zero_429,
        clock=lambda: datetime(2026, 8, 17, 10, tzinfo=timezone.utc),
    )

    class RetryOnly:
        def __init__(self):
            self.calls = 0

        def request_evidence(self, method, endpoint, sent_payload, *, caller_request_id):
            self.calls += 1
            body = {"plan": "pro", "credits_remaining": 10}
            raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            return NansenEvidenceResponse(
                body=body,
                body_parse_status="json_object",
                raw_body=raw,
                status_code=200,
                request_started_at="2026-08-17T10:00:01Z",
                response_retrieved_at="2026-08-17T10:00:01Z",
                response_headers={},
                request_id=caller_request_id,
                credit_cost=0,
                credit_used=0,
                credit_remaining=10,
                credit_header_errors=(),
            )

    client = RetryOnly()
    response, paths = _nansen_call(
        root=bundle.root,
        guard=guard,
        nansen=client,
        logical_request_id="screen",
        method="GET",
        endpoint="account",
        payload=payload,
        expected_credits=1,
        clock=lambda: datetime(2026, 8, 17, 10, 0, 1, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )
    assert response.status_code == 200
    assert client.calls == 1
    assert all("attempt-2" in path.name for path in paths)
    assert guard.replay().entries[0].attempt_count == 2
    assert guard.replay().entries[0].state == "confirmed_zero"


def test_decision_t0_cannot_precede_provider_or_durable_evidence_time(tmp_path):
    from src.nansen_signal_lab.prospective_runner import (
        PilotError,
        _assert_decision_t0,
    )
    from src.nansen_signal_lab.prospective_schema import ProspectiveBundle

    artifact = tmp_path / "model/pass-1/attempt-1-response-metadata.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(json.dumps({
        "provider_created_at": "2026-08-17T10:00:00Z",
        "response_retrieved_at": "2026-08-17T10:00:01Z",
        "artifact_written_at": "2026-08-17T10:00:02Z",
    }))
    bundle = ProspectiveBundle(
        root=tmp_path,
        manifest_path=tmp_path / "manifest.json",
        manifest={"seals": []},
    )
    with pytest.raises(PilotError, match="precedes sealed evidence"):
        _assert_decision_t0(
            datetime(2026, 8, 17, 10, 0, 1, tzinfo=timezone.utc),
            bundle,
            (artifact,),
        )


def test_start_reuses_frozen_snapshot_cutoff_after_predecision_interruption(
    tmp_path, monkeypatch
):
    from src.nansen_signal_lab import prospective_runner
    from src.nansen_signal_lab.prospective_runner import initialize_pilot, start_pilot

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/resume-predecision-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
    )
    nansen = FakeNansen()
    openai = FakeOpenAI()
    original_normalize = prospective_runner.normalize_snapshot

    def interrupt_after_predecision(*_args, **_kwargs):
        raise KeyboardInterrupt("fixture interruption after predecision responses")

    monkeypatch.setattr(
        prospective_runner, "normalize_snapshot", interrupt_after_predecision
    )
    with pytest.raises(KeyboardInterrupt, match="after predecision responses"):
        start_pilot(
            bundle,
            nansen=nansen,
            openai=openai,
            clock=lambda: datetime(2026, 8, 17, 10, 37, tzinfo=timezone.utc),
            sleep=lambda _seconds: None,
        )
    paid_calls = [call for call in nansen.calls if call[0] != "OPENAPI"]
    assert len(paid_calls) == 6

    monkeypatch.setattr(prospective_runner, "normalize_snapshot", original_normalize)
    resumed = start_pilot(
        prospective_runner.load_prospective_manifest(bundle.manifest_path),
        nansen=nansen,
        openai=openai,
        clock=lambda: datetime(2026, 8, 17, 10, 42, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )
    assert resumed.manifest["stage"] == "decision_sealed"
    assert [call for call in nansen.calls if call[0] != "OPENAPI"] == paid_calls


def test_terminal_report_interruption_adopts_first_reason_without_more_http(
    tmp_path, monkeypatch
):
    from dataclasses import replace
    from src.nansen_signal_lab import prospective_runner
    from src.nansen_signal_lab.prospective_runner import initialize_pilot, start_pilot

    class PaidAccount(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            response = super().request_evidence(
                method, endpoint, payload, caller_request_id=caller_request_id
            )
            if endpoint != "account":
                return response
            self.remaining = 9
            body = {"plan": "pro", "credits_remaining": 9}
            raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
            headers = dict(response.response_headers)
            headers.update({
                "X-Nansen-Credits-Cost": "1",
                "X-Nansen-Credits-Used": "1",
                "X-Nansen-Credits-Remaining": "9",
            })
            return replace(
                response,
                body=body,
                raw_body=raw,
                response_headers=headers,
                credit_cost=1,
                credit_used=1,
                credit_remaining=9,
            )

    bundle = initialize_pilot(
        _repo(tmp_path) / "research/experiments/resume-terminal-prospective",
        created_at=datetime(2026, 8, 17, 9, tzinfo=timezone.utc),
    )
    nansen = PaidAccount()
    openai = FakeOpenAI()
    original_commit = prospective_runner.commit_stage

    def interrupt_terminal(current, target, *args, **kwargs):
        if target == "unscorable":
            raise KeyboardInterrupt("fixture interruption after terminal report")
        return original_commit(current, target, *args, **kwargs)

    monkeypatch.setattr(prospective_runner, "commit_stage", interrupt_terminal)
    with pytest.raises(KeyboardInterrupt, match="after terminal report"):
        start_pilot(
            bundle,
            nansen=nansen,
            openai=openai,
            clock=lambda: datetime(2026, 8, 17, 10, 37, tzinfo=timezone.utc),
            sleep=lambda _seconds: None,
        )
    assert (bundle.root / "REPORT.md").is_file()
    calls = list(nansen.calls)

    monkeypatch.setattr(prospective_runner, "commit_stage", original_commit)
    final = start_pilot(
        prospective_runner.load_prospective_manifest(bundle.manifest_path),
        nansen=nansen,
        openai=openai,
        clock=lambda: datetime(2026, 8, 17, 10, 38, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )
    assert final.manifest["stage"] == "unscorable"
    assert nansen.calls == calls
