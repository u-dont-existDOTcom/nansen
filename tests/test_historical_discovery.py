from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.nansen_signal_lab.historical_discovery as discovery
import src.nansen_signal_lab.prospective_runner as prospective_runner
from src.nansen_signal_lab.client import NansenEvidenceResponse, NansenRequestFailure


NOW = "2026-08-18T12:00:00Z"
_UNSET = object()


def screener_body(*, complete: bool = True):
    rows = []
    for index in range(24):
        rows.append({
            "chain": "ethereum" if index % 2 == 0 else "base",
            "token_address": f"0x{index:040x}",
            "token_symbol": f"T{index}",
            "netflow": 10_000 - index,
            "liquidity": 500_000 + index,
            "market_cap_usd": 5_000_000 + index,
            "token_age_days": 30 + index,
            "sectors": ["DeFi"],
        })
    return {
        "data": rows,
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": complete},
    }


def boundary_screener_body():
    body = screener_body()
    chains = ["ethereum"] * 17 + ["solana", "base", "bnb"]
    for index, chain in enumerate(chains):
        body["data"][index]["chain"] = chain
        if chain == "solana":
            body["data"][index]["token_address"] = f"SolanaToken{index}"
    return body


def holdings_body(selection):
    rows = []
    start = discovery.HOLDINGS_FROM
    days = (discovery.HOLDINGS_TO - start).days + 1
    for member_index, member in enumerate(selection["members"][:2]):
        for offset in range(days):
            day = start + timedelta(days=offset)
            balance = 100.0 + offset
            rows.append({
                "date": day.isoformat(),
                "chain": member["chain"],
                "token_address": member["token_address"],
                "token_symbol": member["token_symbol"],
                "token_sectors": ["DeFi"],
                "smart_money_labels": ["Smart Trader"],
                "balance": balance,
                "value_usd": balance * (10.0 + 0.1 * offset + member_index),
                "balance_24h_percent_change": 0.01,
                "holders_count": 10 + offset if member_index == 0 else 10,
                "share_of_holdings_percent": 0.001,
                "token_age_days": 100,
                "market_cap_usd": 10_000_000.0,
            })
    return {
        "data": rows,
        "pagination": {"page": 1, "per_page": 1000, "is_last_page": True},
    }


def ohlcv_bodies(selection):
    member_index = {
        (member["chain"], member["token_address"]): index
        for index, member in enumerate(selection["members"])
    }
    result = {}
    first_day = discovery.SIGNAL_FROM + timedelta(days=1)
    days = (discovery.HOLDINGS_TO - first_day).days + 1
    for logical_id, payload in discovery._ohlcv_requests(selection):
        tokens = []
        for address in payload["token_addresses"]:
            index = member_index[(payload["chain"], address)]
            tokens.append({
                "token_address": address,
                "data": [
                    {
                        "interval_start": (
                            first_day + timedelta(days=offset)
                        ).isoformat() + "T00:00:00Z",
                        "open": 10.0 + index + 0.1 * offset,
                        "high": 10.1 + index + 0.1 * offset,
                        "low": 9.9 + index + 0.1 * offset,
                        "close": 10.0 + index + 0.1 * offset,
                        "volume": 1_000.0,
                        "volume_usd": 10_000.0,
                        "market_cap": {},
                    }
                    for offset in range(days)
                ],
            })
        result[logical_id] = {
            "chain": payload["chain"],
            "timeframe": "1d",
            "tokens": tokens,
            "truncated": False,
        }
    return result


def response(
    body,
    *,
    cost,
    remaining,
    status: int = 200,
    used=_UNSET,
    retry_after: str | None = None,
    request_started_at: str = NOW,
    response_retrieved_at: str = NOW,
):
    raw = discovery.canonical_json_bytes(body)
    actual_used = cost if used is _UNSET else used
    credit_remaining = remaining
    headers = {"X-Nansen-Credits-Cost": str(cost)}
    if cost == 0 and used is _UNSET:
        actual_used = None
        credit_remaining = None
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
        raw_body=raw,
        status_code=status,
        request_started_at=request_started_at,
        response_retrieved_at=response_retrieved_at,
        response_headers=headers,
        request_id=None,
        credit_cost=cost,
        credit_used=actual_used,
        credit_remaining=credit_remaining,
        credit_header_errors=(),
    )


def initialize_fixture(tmp_path, contract, monkeypatch):
    monkeypatch.setattr(
        discovery, "EXPECTED_OPENAPI_SHA256", hashlib.sha256(contract).hexdigest()
    )
    source_repo = Path(discovery.__file__).resolve().parents[2]
    design = tmp_path / discovery.DESIGN_PATH
    design.parent.mkdir(parents=True)
    design.write_bytes((source_repo / discovery.DESIGN_PATH).read_bytes())
    root = tmp_path / "research" / "experiments" / "fixture-historical"
    manifest_path = discovery.initialize_historical_discovery(
        root,
        created_at=datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        design_path=design,
    )
    return root, manifest_path


class FakeNansen:
    def __init__(self, contract, *, fail_screener_429: bool = False):
        self.contract = contract
        self.fail_screener_429 = fail_screener_429
        self.calls = []
        self.remaining = 75
        self.selection = discovery.select_cohort(screener_body())
        self.ohlcv = ohlcv_bodies(self.selection)

    def fetch_openapi(self):
        return self.contract

    def request_evidence(self, method, endpoint, payload, *, caller_request_id):
        self.calls.append((method, endpoint, payload, caller_request_id))
        if endpoint == "account":
            return response(
                {"plan": "free", "credits_remaining": self.remaining},
                cost=0,
                remaining=self.remaining,
            )
        if endpoint == discovery.SCREENER_ENDPOINT and self.fail_screener_429:
            failed = response(
                {"error": "rate limit"},
                cost=5,
                used=0,
                remaining=self.remaining,
                status=429,
                retry_after="1",
            )
            raise NansenRequestFailure(
                "Nansen HTTP 429", transmitted=True, response=failed
            )
        if endpoint == discovery.SCREENER_ENDPOINT:
            body, cost = screener_body(), 5
        elif endpoint == discovery.HOLDINGS_ENDPOINT:
            body, cost = holdings_body(self.selection), 1
        elif endpoint == discovery.OHLCV_ENDPOINT:
            body, cost = self.ohlcv[caller_request_id], 1
        else:  # pragma: no cover - protects fixture misuse
            raise AssertionError(endpoint)
        self.remaining -= cost
        return response(body, cost=cost, remaining=self.remaining)


def test_select_cohort_is_deterministic_and_page_local():
    selected = discovery.select_cohort(screener_body(complete=False))
    assert len(selected["members"]) == 20
    assert [member["token_symbol"] for member in selected["members"][:3]] == [
        "T0", "T1", "T2"
    ]


def test_select_cohort_rejects_duplicate_universe_identity_and_unrequested_chain():
    duplicate = screener_body()
    duplicate["data"][1] = dict(duplicate["data"][0])
    with pytest.raises(discovery.HistoricalDiscoveryError, match="duplicate"):
        discovery.select_cohort(duplicate)

    wrong_chain = screener_body()
    wrong_chain["data"][0]["chain"] = "arbitrum"
    with pytest.raises(discovery.HistoricalDiscoveryError, match="unrequested chain"):
        discovery.select_cohort(wrong_chain)


def test_daily_discovery_separates_positive_and_nonpositive_breadth():
    screen = screener_body()
    selection = discovery.select_cohort(screen)
    holdings = holdings_body(selection)

    _, features, events, summary = discovery.build_discovery(
        screen, [holdings], ohlcv_bodies(selection)
    )

    assert len(features) == 20 * 56
    assert {event["arm"] for event in events} == {
        "holder-breadth-positive-daily-v1",
        "holder-breadth-nonpositive-daily-v1",
    }
    assert all(event["gross_return_pct"] > 0 for event in events)
    assert summary["selection_status"] == "does_not_advance"
    assert "fewer than five tokens in an arm" in summary["gate_reasons"]


def test_missing_ohlcv_outcome_stays_in_eligible_denominator():
    screen = screener_body()
    selection = discovery.select_cohort(screen)
    bodies = ohlcv_bodies(selection)
    first = selection["members"][0]
    logical_id = next(
        logical
        for logical, payload in discovery._ohlcv_requests(selection)
        if first["token_address"] in payload["token_addresses"]
    )
    token = next(
        row for row in bodies[logical_id]["tokens"]
        if row["token_address"] == first["token_address"]
    )
    token["data"] = token["data"][1:]

    _, _, events, summary = discovery.build_discovery(
        screen, [holdings_body(selection)], bodies
    )

    positive = next(
        arm for arm in summary["arms"]
        if arm["arm"] == "holder-breadth-positive-daily-v1"
    )
    assert any(event["outcome_status"] == "missing_contiguous_ohlcv" for event in events)
    assert positive["eligible_episodes"] > positive["scored_episodes"]
    assert positive["outcome_coverage_rate"] < 1
    assert "outcome coverage is below 100% in an arm" in summary["gate_reasons"]


def test_start_archives_and_offline_check_replays_exact_outputs(tmp_path, monkeypatch):
    contract = b'{"openapi":"3.1.0","paths":{}}'
    root, manifest_path = initialize_fixture(tmp_path, contract, monkeypatch)
    fake = FakeNansen(contract)

    result = discovery.start_historical_discovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "completed", result["terminal_reason"]
    assert len(fake.calls) == 5
    seal = json.loads((root / "seals/completed.json").read_text())
    assert seal["authenticated_request_attempts"] == 5
    verified = discovery.check_historical_discovery(manifest_path)
    assert root / "derived/summary.json" in verified
    assert (root / "REPORT.md").read_text().startswith("# Historical holder-breadth")


def test_exact_request_credit_and_account_boundaries(tmp_path, monkeypatch):
    contract = b'{"openapi":"3.1.0","paths":{}}'
    root, manifest_path = initialize_fixture(tmp_path, contract, monkeypatch)
    screen = boundary_screener_body()
    selection = discovery.select_cohort(screen)
    all_holdings = holdings_body(selection)["data"]
    split = len(all_holdings) // 2
    holdings_pages = {
        1: {
            "data": all_holdings[:split],
            "pagination": {"page": 1, "per_page": 1000, "is_last_page": False},
        },
        2: {
            "data": all_holdings[split:],
            "pagination": {"page": 2, "per_page": 1000, "is_last_page": True},
        },
    }
    ohlcv = ohlcv_bodies(selection)
    assert len(discovery._ohlcv_requests(selection)) == 5

    class BoundaryNansen:
        def __init__(self):
            self.calls = []
            self.remaining = 12

        def fetch_openapi(self):
            return contract

        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            self.calls.append((method, endpoint, payload, caller_request_id))
            if endpoint == "account":
                return response(
                    {"plan": "free", "credits_remaining": self.remaining},
                    cost=0,
                    remaining=self.remaining,
                )
            if endpoint == discovery.SCREENER_ENDPOINT:
                body, cost = screen, 5
            elif endpoint == discovery.HOLDINGS_ENDPOINT:
                body, cost = holdings_pages[payload["pagination"]["page"]], 1
            elif endpoint == discovery.OHLCV_ENDPOINT:
                body, cost = ohlcv[caller_request_id], 1
            else:  # pragma: no cover - protects fixture misuse
                raise AssertionError(endpoint)
            self.remaining -= cost
            return response(body, cost=cost, remaining=self.remaining)

    fake = BoundaryNansen()
    result = discovery.start_historical_discovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "completed", result["terminal_reason"]
    assert len(fake.calls) == discovery.MAX_CALLS == 9
    totals = discovery.BudgetGuard(
        root, discovery.MAX_CALLS, discovery.MAX_CREDITS
    ).replay()
    assert (totals.calls, totals.credits, totals.provider_remaining) == (8, 12, 0)
    assert json.loads((root / "seals/completed.json").read_text())[
        "authenticated_request_attempts"
    ] == 9
    discovery.check_historical_discovery(manifest_path)


def test_zero_use_429_terminalizes_without_retry(tmp_path, monkeypatch):
    contract = b'{"openapi":"3.1.0","paths":{}}'
    root, manifest_path = initialize_fixture(tmp_path, contract, monkeypatch)
    fake = FakeNansen(contract, fail_screener_429=True)

    result = discovery.start_historical_discovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "unscorable"
    assert len(fake.calls) == 2
    assert sum(call[1] == discovery.SCREENER_ENDPOINT for call in fake.calls) == 1
    seal = json.loads((root / "seals/unscorable.json").read_text())
    assert seal["authenticated_request_attempts"] == 2
    discovery.check_historical_discovery(manifest_path)


@pytest.mark.parametrize("fault", ["after_report", "after_snapshot", "after_seal"])
def test_unscorable_finalization_adopts_original_reason_after_crash(
    tmp_path, monkeypatch, fault
):
    contract = b'{"openapi":"3.1.0","paths":{}}'
    root, manifest_path = initialize_fixture(tmp_path, contract, monkeypatch)
    fake = FakeNansen(contract, fail_screener_429=True)
    raised = False
    if fault == "after_report":
        original = discovery.BudgetGuard.snapshot

        def fail_once(self, stage, *, recorded_at):
            nonlocal raised
            if not raised and stage == "unscorable":
                raised = True
                raise OSError("injected unscorable snapshot failure")
            return original(self, stage, recorded_at=recorded_at)

        monkeypatch.setattr(discovery.BudgetGuard, "snapshot", fail_once)
        restore = lambda: monkeypatch.setattr(discovery.BudgetGuard, "snapshot", original)
    elif fault == "after_snapshot":
        original = discovery.write_bytes_once_or_adopt_exact

        def fail_once(path, content, *, metadata):
            nonlocal raised
            if not raised and Path(path) == root / "seals/unscorable.json":
                raised = True
                raise OSError("injected unscorable seal failure")
            return original(path, content, metadata=metadata)

        monkeypatch.setattr(discovery, "write_bytes_once_or_adopt_exact", fail_once)
        restore = lambda: monkeypatch.setattr(
            discovery, "write_bytes_once_or_adopt_exact", original
        )
    else:
        original = discovery.atomic_replace_bytes

        def fail_once(path, content):
            nonlocal raised
            if not raised and Path(path) == manifest_path:
                raised = True
                raise OSError("injected unscorable manifest failure")
            return original(path, content)

        monkeypatch.setattr(discovery, "atomic_replace_bytes", fail_once)
        restore = lambda: monkeypatch.setattr(discovery, "atomic_replace_bytes", original)

    with pytest.raises(OSError, match="injected"):
        discovery.start_historical_discovery(
            manifest_path,
            nansen=fake,
            clock=lambda: datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
            sleep=lambda _: None,
        )
    original_reason = (root / "REPORT.md").read_text()
    assert len(fake.calls) == 2

    restore()
    result = discovery.start_historical_discovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 13, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "unscorable"
    assert len(fake.calls) == 2
    assert (root / "REPORT.md").read_text() == original_reason
    discovery.check_historical_discovery(manifest_path)


def test_metadata_only_response_crash_terminalizes_without_retransmission(
    tmp_path, monkeypatch
):
    contract = b'{"openapi":"3.1.0","paths":{}}'
    root, manifest_path = initialize_fixture(tmp_path, contract, monkeypatch)

    class DistinctTimestampNansen(FakeNansen):
        def request_evidence(self, method, endpoint, payload, *, caller_request_id):
            self.calls.append((method, endpoint, payload, caller_request_id))
            assert endpoint == "account"
            return response(
                {"plan": "free", "credits_remaining": self.remaining},
                cost=0,
                remaining=self.remaining,
                request_started_at="2026-08-18T12:00:02Z",
                response_retrieved_at="2026-08-18T12:00:03Z",
            )

    fake = DistinctTimestampNansen(contract)
    original = prospective_runner._install_bytes
    raised = False

    def fail_after_metadata(path, content, *, kind):
        nonlocal raised
        if not raised and Path(path).name == "attempt-1-response.json":
            raised = True
            raise OSError("injected raw-response write failure")
        return original(path, content, kind=kind)

    monkeypatch.setattr(prospective_runner, "_install_bytes", fail_after_metadata)
    clock_values = iter((
        datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 8, 18, 12, 0, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 18, 12, 0, 4, tzinfo=timezone.utc),
    ))
    with pytest.raises(OSError, match="injected"):
        discovery.start_historical_discovery(
            manifest_path,
            nansen=fake,
            clock=lambda: next(clock_values),
            sleep=lambda _: None,
        )
    assert len(fake.calls) == 1
    metadata = tuple(root.glob("raw/nansen/*/attempt-1-response-metadata.json"))
    bodies = tuple(root.glob("raw/nansen/*/attempt-1-response.json"))
    assert len(metadata) == 1
    assert bodies == ()

    monkeypatch.setattr(prospective_runner, "_install_bytes", original)
    result = discovery.start_historical_discovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 13, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "unscorable"
    assert len(fake.calls) == 1
    assert json.loads((root / "seals/unscorable.json").read_text())[
        "authenticated_request_attempts"
    ] == 1
    discovery.check_historical_discovery(manifest_path)


@pytest.mark.parametrize("fault", ["after_snapshot", "after_seal"])
def test_terminal_finalization_is_crash_resumable(tmp_path, monkeypatch, fault):
    contract = b'{"openapi":"3.1.0","paths":{}}'
    root, manifest_path = initialize_fixture(tmp_path, contract, monkeypatch)
    fake = FakeNansen(contract)

    if fault == "after_snapshot":
        original = discovery.write_bytes_once_or_adopt_exact
        raised = False

        def fail_once(path, content, *, metadata):
            nonlocal raised
            if not raised and Path(path) == root / "seals/completed.json":
                raised = True
                raise OSError("injected seal failure")
            return original(path, content, metadata=metadata)

        monkeypatch.setattr(discovery, "write_bytes_once_or_adopt_exact", fail_once)
        restore = lambda: monkeypatch.setattr(
            discovery, "write_bytes_once_or_adopt_exact", original
        )
    else:
        original = discovery.atomic_replace_bytes
        raised = False

        def fail_once(path, content):
            nonlocal raised
            if not raised and Path(path) == manifest_path:
                raised = True
                raise OSError("injected manifest failure")
            return original(path, content)

        monkeypatch.setattr(discovery, "atomic_replace_bytes", fail_once)
        restore = lambda: monkeypatch.setattr(discovery, "atomic_replace_bytes", original)

    with pytest.raises(OSError, match="injected"):
        discovery.start_historical_discovery(
            manifest_path,
            nansen=fake,
            clock=lambda: datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
            sleep=lambda _: None,
        )
    initial_recorded_at = json.loads(
        (root / "budget/snapshots/completed.json").read_text()
    )["recorded_at"]
    assert len(fake.calls) == 5

    restore()
    result = discovery.start_historical_discovery(
        manifest_path,
        nansen=fake,
        clock=lambda: datetime(2026, 8, 18, 13, tzinfo=timezone.utc),
        sleep=lambda _: None,
    )

    assert result["stage"] == "completed"
    assert len(fake.calls) == 5
    assert json.loads((root / "seals/completed.json").read_text())["recorded_at"] == initial_recorded_at
    discovery.check_historical_discovery(manifest_path)


def test_initialize_refuses_nonempty_directory(tmp_path):
    root = tmp_path / "research" / "experiments" / "occupied"
    root.mkdir(parents=True)
    (root / "owner.txt").write_text("keep\n")

    with pytest.raises(discovery.HistoricalDiscoveryError, match="not empty"):
        discovery.initialize_historical_discovery(
            root,
            created_at=datetime.now(timezone.utc),
            design_path=tmp_path / discovery.DESIGN_PATH,
        )
    assert (root / "owner.txt").read_text() == "keep\n"


def test_initialize_rejects_shallow_or_nonfixed_location(tmp_path):
    with pytest.raises(discovery.HistoricalDiscoveryError, match="research/experiments"):
        discovery.initialize_historical_discovery(
            tmp_path / "shallow",
            created_at=datetime.now(timezone.utc),
            design_path=tmp_path / "design.md",
        )


def test_start_rejects_bundle_symlink_before_any_provider_access(tmp_path, monkeypatch):
    contract = b'{"openapi":"3.1.0","paths":{}}'
    root, manifest_path = initialize_fixture(tmp_path, contract, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "raw").symlink_to(outside, target_is_directory=True)

    class NoAccess:
        accessed = False

        def fetch_openapi(self):
            self.accessed = True
            raise AssertionError("provider access must not occur")

    nansen = NoAccess()
    with pytest.raises(discovery.HistoricalDiscoveryError, match="contains a symlink"):
        discovery.start_historical_discovery(
            manifest_path,
            nansen=nansen,
            clock=lambda: datetime(2026, 8, 18, 12, tzinfo=timezone.utc),
            sleep=lambda _: None,
        )
    assert nansen.accessed is False
