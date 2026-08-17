from __future__ import annotations

import hashlib
import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

import src.nansen_signal_lab.client as client_module
from src.nansen_signal_lab.artifacts import canonical_json_bytes
from src.nansen_signal_lab.client import NansenClient


def _snapshot_module():
    try:
        return importlib.import_module("src.nansen_signal_lab.prospective_snapshot")
    except ModuleNotFoundError as exc:
        pytest.fail(f"prospective snapshot public module is not available: {exc}")


def _candidate_row(
    *,
    chain: str = "base",
    address: str = "0xAbC",
    symbol: str = "TOP",
    liquidity: float = 250_000,
    netflow: float = 50,
    price_usd: float = 2,
    volume_usd: float = 100,
    market_cap_usd: float = 1_000_000,
):
    return {
        "chain": chain,
        "token_address": address,
        "symbol": symbol,
        "price_usd": price_usd,
        "volume_usd": volume_usd,
        "liquidity": liquidity,
        "market_cap_usd": market_cap_usd,
        "netflow": netflow,
    }


def _screener_body(*rows):
    return {"data": list(rows), "pagination": {"page": 1, "per_page": 1000}}


def test_screener_payload_and_page_local_ranking_are_literal():
    snapshot = _snapshot_module()
    assert snapshot.screener_payload() == {
        "chains": ["solana", "ethereum", "base", "bnb", "arbitrum"],
        "timeframe": "24h",
        "pagination": {"page": 1, "per_page": 1000},
        "filters": {
            "trader_type": "sm",
            "include_stablecoins": False,
            "token_age_days": {"min": 3},
            "market_cap_usd": {"min": 1_000_000},
            "liquidity": {"min": 250_000},
        },
        "order_by": [{"field": "netflow", "direction": "DESC"}],
    }
    # Equal netflow proves local tie-breaking: chain first, then EVM address lowercased.
    candidate = snapshot.select_candidate(
        _screener_body(
            _candidate_row(chain="ethereum", address="0xB", symbol="SECOND", netflow=9),
            _candidate_row(chain="ethereum", address="0xa", symbol="FIRST", netflow=9),
            _candidate_row(chain="solana", address="aBc", symbol="SOL", netflow=9),
        ),
        frozenset(),
    )
    assert (candidate.chain, candidate.token_address, candidate.token_symbol) == (
        "ethereum", "0xa", "FIRST",
    )


def test_selection_excludes_prior_tokens_and_rejects_invalid_or_empty_eligibility(tmp_path):
    snapshot = _snapshot_module()
    experiments = tmp_path / "experiments"
    prior = experiments / "prior"
    prior.mkdir(parents=True)
    (prior / "manifest.json").write_text(json.dumps({"cohort": [{
        "chain": "base", "address": "0xABC",
    }]}))
    excluded = snapshot.prior_token_identities(experiments)
    assert excluded == frozenset({("base", "0xabc")})

    selected = snapshot.select_candidate(
        _screener_body(
            _candidate_row(address="0xAbC", symbol="PRIOR", netflow=99),
            _candidate_row(address="0xDef", symbol="NEW", netflow=1),
        ),
        excluded,
    )
    assert selected.token_symbol == "NEW"

    bad = _candidate_row(address="0xBad", netflow=float("inf"))
    with pytest.raises(snapshot.SnapshotError, match="no eligible"):
        snapshot.select_candidate(_screener_body(bad), frozenset())
    with pytest.raises(snapshot.SnapshotError, match="pagination"):
        snapshot.select_candidate({"data": [_candidate_row()]}, frozenset())
    with pytest.raises(snapshot.SnapshotError, match="no eligible"):
        snapshot.select_candidate(_screener_body(_candidate_row(price_usd=0)), frozenset())


def test_solana_addresses_remain_case_sensitive_when_excluding_and_sorting():
    snapshot = _snapshot_module()
    candidate = snapshot.select_candidate(
        _screener_body(
            _candidate_row(chain="solana", address="AbC", symbol="UPPER", netflow=5),
            _candidate_row(chain="solana", address="aBc", symbol="LOWER", netflow=5),
        ),
        frozenset({("solana", "AbC")}),
    )
    assert candidate.token_symbol == "LOWER"


def test_freeze_uses_only_screener_liquidity_and_binds_hashes():
    snapshot = _snapshot_module()
    small_row = _candidate_row(liquidity=250_000)
    small = snapshot.Candidate("base", "0xAbC", "TOP", 999_999_999, small_row)
    frozen = snapshot.freeze_selection(
        small,
        screener_response_sha256="a" * 64,
        screener_retrieved_at="2026-08-17T10:00:00Z",
    )
    assert frozen["identity"] == {
        "chain": "base", "token_address": "0xAbC", "token_symbol": "TOP",
    }
    assert frozen["screener"]["selected_row_sha256"] == hashlib.sha256(
        canonical_json_bytes(small_row)
    ).hexdigest()
    assert frozen["screener"]["response_sha256"] == "a" * 64
    assert frozen["screener"]["retrieved_at"] == "2026-08-17T10:00:00Z"
    assert frozen["liquidity"]["screener_liquidity_usd"] == 250_000.0
    assert frozen["notional"] == {
        "formula": "min(1000, 0.001 * liquidity_usd)",
        "virtual_notional_usd": 250.0,
    }

    capped = snapshot.freeze_selection(
        snapshot.Candidate("base", "0xDef", "CAP", 1, _candidate_row(liquidity=2_000_000)),
        screener_response_sha256="b" * 64,
        screener_retrieved_at="2026-08-17T10:00:00Z",
    )
    assert capped["notional"]["virtual_notional_usd"] == 1_000.0


def test_predecision_requests_are_exact_relative_contract_triples():
    snapshot = _snapshot_module()
    candidate = snapshot.Candidate("solana", "So111", "SOL", 250_000, _candidate_row())
    available_at = datetime(2026, 8, 17, 10, tzinfo=timezone.utc)
    assert snapshot.predecision_requests(candidate, available_at) == (
        ("POST", "tgm/token-information", {
            "chain": "solana", "token_address": "So111", "timeframe": "1d",
        }),
        ("POST", "tgm/flow-intelligence", {
            "chain": "solana", "token_address": "So111", "timeframe": "1d",
        }),
        ("POST", "tgm/flows", {
            "chain": "solana", "token_address": "So111",
            "date": {"from": "2026-08-16T09:00:00Z", "to": "2026-08-17T10:00:00Z"},
            "label": "smart_money",
            "pagination": {"page": 1, "per_page": 1000},
            "order_by": [{"field": "date", "direction": "ASC"}],
        }),
        ("POST", "tgm/flows", {
            "chain": "solana", "token_address": "So111",
            "date": {"from": "2026-08-16T09:00:00Z", "to": "2026-08-17T10:00:00Z"},
            "label": "exchange",
            "pagination": {"page": 1, "per_page": 1000},
            "order_by": [{"field": "date", "direction": "ASC"}],
        }),
    )


def test_contract_relative_paths_are_not_double_prefixed(tmp_path, monkeypatch):
    snapshot = _snapshot_module()
    requests = []
    transport = httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, json={"data": []})
    )
    real_client = httpx.Client
    monkeypatch.setattr(
        client_module.httpx,
        "Client",
        lambda *, timeout: real_client(timeout=timeout, transport=transport),
    )
    client = NansenClient(api_key="test", cache_dir=tmp_path)
    client.request_evidence("POST", "token-screener", snapshot.screener_payload(), caller_request_id="s")
    client.request_evidence("GET", "account", None, caller_request_id="a")
    candidate = snapshot.Candidate("solana", "So111", "SOL", 1, _candidate_row())
    for method, endpoint, body in snapshot.predecision_requests(
        candidate, datetime(2026, 8, 17, 10, tzinfo=timezone.utc)
    ):
        client.request_evidence(method, endpoint, body, caller_request_id=endpoint)
    assert [request.url.path for request in requests] == [
        "/api/v1/token-screener", "/api/v1/account", "/api/v1/tgm/token-information",
        "/api/v1/tgm/flow-intelligence", "/api/v1/tgm/flows", "/api/v1/tgm/flows",
    ]
    assert requests[0].method == "POST" and json.loads(requests[0].content) == snapshot.screener_payload()
    assert requests[1].method == "GET" and requests[1].content == b""


def _flow_row(hour: int, *, complete: object = True, future: bool = False):
    available_at = datetime(2026, 8, 17, 10, tzinfo=timezone.utc)
    end = available_at - timedelta(hours=13 - hour)
    if future:
        end = available_at + timedelta(hours=1)
    row = {
        "date": (end - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "bucket_end": end.isoformat().replace("+00:00", "Z"),
        "price_usd": 10 + hour,
        "token_amount": 100 + hour,
        "holders_count": 5 + hour,
    }
    if complete != "missing":
        row["is_complete"] = complete
    return row


def _final_flow_body(rows, *, is_last_page: object = True):
    return {"data": rows, "pagination": {"page": 1, "is_last_page": is_last_page}}


def _selection(snapshot):
    return snapshot.freeze_selection(
        snapshot.Candidate("solana", "So111", "LEAK", 250_000, _candidate_row(chain="solana", address="So111")),
        screener_response_sha256="c" * 64,
        screener_retrieved_at="2026-08-17T10:00:00Z",
    )


def test_normalization_rejects_incomplete_late_or_nonfinal_flow_evidence():
    snapshot = _snapshot_module()
    available_at = datetime(2026, 8, 17, 10, tzinfo=timezone.utc)
    valid = [_flow_row(hour) for hour in range(13)]
    for rows, final, message in (
        (valid + [_flow_row(13, complete=False)], True, "is_complete"),
        (valid + [_flow_row(13, complete="missing")], True, "is_complete"),
        (valid + [_flow_row(13, future=True)], True, "bucket_end"),
        (valid, False, "is_last_page"),
    ):
        with pytest.raises(snapshot.SnapshotError, match=message):
            snapshot.normalize_snapshot(
                _selection(snapshot), {"data": {}}, {"data": {}},
                _final_flow_body(rows, is_last_page=final), _final_flow_body(valid),
                available_at=available_at,
            )


def test_normalization_preserves_gap_as_unavailable_and_ignores_token_info_liquidity():
    snapshot = _snapshot_module()
    available_at = datetime(2026, 8, 17, 10, tzinfo=timezone.utc)
    # Missing hour 11 creates an interior gap; it must not bridge any trailing horizon.
    rows = [_flow_row(hour) for hour in range(13) if hour != 11]
    normalized = snapshot.normalize_snapshot(
        _selection(snapshot),
        {"data": {"liquidity_usd": 9_999_999, "name": "secret token", "symbol": "LEAK"}},
        {"data": {"smart_money_netflow_usd": 12, "url": "https://secret.invalid"}},
        _final_flow_body(rows), _final_flow_body(rows), available_at=available_at,
    )
    final = normalized["smart_money"]["final_feature"]
    assert final["holdings_change_1h_pct"] is None
    assert final["holdings_change_4h_pct"] is None
    assert final["holdings_change_12h_pct"] is None
    assert normalized["selection"]["virtual_notional_usd"] == 250.0
    assert normalized["token_information"]["data"]["liquidity_usd"] == 9_999_999


def _walk(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)
    elif isinstance(value, str):
        yield value


def test_blind_snapshot_is_a_fresh_identity_and_outcome_safe_whitelist():
    snapshot = _snapshot_module()
    available_at = datetime(2026, 8, 17, 10, tzinfo=timezone.utc)
    normalized = snapshot.normalize_snapshot(
        _selection(snapshot),
        {"data": {"name": "Secret Name", "symbol": "LEAK", "liquidity_usd": 1}},
        {"data": {"social_url": "https://secret.invalid", "netflow_usd": 2}},
        _final_flow_body([_flow_row(hour) for hour in range(13)]),
        _final_flow_body([_flow_row(hour) for hour in range(13)]),
        available_at=available_at,
    )
    blinded = snapshot.blind_snapshot(normalized)
    assert blinded["candidate"] == {"identity": "candidate-1", "chain": "solana"}
    assert blinded["selection"] == {
        "formula": "min(1000, 0.001 * liquidity_usd)", "virtual_notional_usd": 250.0,
    }
    rendered = json.dumps(blinded).lower()
    for prohibited in (
        "name", "symbol", "address", "so111", "secret", "url", "social", "forward_",
        "mfe", "mae", "selection_status", "prior_return",
    ):
        assert prohibited not in rendered
