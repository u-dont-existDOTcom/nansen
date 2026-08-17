from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

import src.nansen_signal_lab.cli as cli


def test_write_api_artifacts_preserves_generic_provenance_without_api_key(tmp_path):
    """Fails if generic evidence loses its literal endpoint or response provenance."""
    output = tmp_path / "evidence.json"
    payload = {"chain": "base", "token_address": "0xtoken"}

    response_path, request_path = cli.write_api_artifacts(
        body={"data": []},
        payload=payload,
        endpoint="tgm/who-bought-sold",
        output_path=output,
        cache_hit=True,
        response_retrieved_at="2026-08-16T23:00:05Z",
        artifact_written_at="2026-08-16T23:01:05Z",
    )

    assert json.loads(response_path.read_text()) == {"data": []}
    metadata = json.loads(request_path.read_text())
    assert metadata == {
        "schema_version": 2,
        "endpoint": "tgm/who-bought-sold",
        "payload": payload,
        "cache_hit": True,
        "response_retrieved_at": "2026-08-16T23:00:05Z",
        "artifact_written_at": "2026-08-16T23:01:05Z",
        "response_file": "evidence.json",
        "response_sha256": hashlib.sha256(response_path.read_bytes()).hexdigest(),
    }


def test_exchange_flows_preserve_label_and_use_distinct_default_path(tmp_path, monkeypatch):
    """Fails if exchange evidence can collide with legacy Smart-Money evidence."""
    body = {"data": []}

    class FakeNansenClient:
        def post_with_provenance(self, endpoint, payload, *, refresh=False):
            assert endpoint == "tgm/flows"
            assert payload["label"] == "exchange"
            return SimpleNamespace(
                body=body,
                cache_hit=False,
                response_retrieved_at="2026-08-16T23:00:05Z",
            )

    monkeypatch.setattr(cli, "NansenClient", FakeNansenClient)
    monkeypatch.chdir(tmp_path)
    args = cli.build_parser().parse_args([
        "flows", "--chain", "base", "--token", "0xtoken", "--label", "exchange",
        "--from", "2026-08-16T22:00:00Z", "--to", "2026-08-16T23:00:00Z",
    ])

    args.func(args)

    response_path = tmp_path / "results" / "flows-exchange-base-0xtoken.json"
    assert json.loads(response_path.read_text()) == body
    assert json.loads(response_path.with_name("flows-exchange-base-0xtoken.request.json").read_text())["payload"]["label"] == "exchange"


def test_exchange_flows_normalize_timezone_aware_bounds_to_utc(tmp_path, monkeypatch):
    """Fails if valid offset bounds are archived without normalization to canonical UTC."""
    class FakeNansenClient:
        def post_with_provenance(self, endpoint, payload, *, refresh=False):
            assert payload["date"] == {
                "from": "2026-08-01T00:00:00Z",
                "to": "2026-08-02T00:00:00Z",
            }
            return SimpleNamespace(
                body={"data": []},
                cache_hit=False,
                response_retrieved_at="2026-08-02T00:01:00Z",
            )

    monkeypatch.setattr(cli, "NansenClient", FakeNansenClient)
    output = tmp_path / "exchange.json"
    args = cli.build_parser().parse_args([
        "flows", "--chain", "base", "--token", "0xtoken", "--label", "exchange",
        "--from", "2026-08-01T01:00:00+01:00",
        "--to", "2026-08-02T01:00:00+01:00",
        "--output", str(output),
    ])

    args.func(args)


@pytest.mark.parametrize(
    ("bounds", "message"),
    [
        (
            ["--from", "2026-08-01T00:00:00", "--to", "2026-08-02T00:00:00Z"],
            "timezone-aware",
        ),
        (
            ["--from", "2026-08-02T00:00:00Z", "--to", "2026-08-01T00:00:00Z"],
            "before --to",
        ),
        (["--days", "0", "--to", "2026-08-02T00:00:00Z"], "positive"),
        (["--days", "-1", "--to", "2026-08-02T00:00:00Z"], "positive"),
        (
            ["--from", "2026-08-01T00:00:00Z", "--to", "2026-08-02T00:00:00Z", "--limit", "0"],
            "between 1 and 100",
        ),
        (
            ["--from", "2026-08-01T00:00:00Z", "--to", "2026-08-02T00:00:00Z", "--limit", "101"],
            "between 1 and 100",
        ),
    ],
    ids=["naive", "reversed", "zero-days", "negative-days", "zero-limit", "large-limit"],
)
def test_exchange_flows_reject_invalid_requests_before_client_construction(
    tmp_path, monkeypatch, bounds, message
):
    """Fails if an invalid exchange request can construct a client or create evidence."""
    class ApiMustNotBeCalled:
        def __init__(self):
            raise AssertionError("API client constructed for invalid exchange request")

    monkeypatch.setattr(cli, "NansenClient", ApiMustNotBeCalled)
    output = tmp_path / "exchange.json"
    args = cli.build_parser().parse_args([
        "flows", "--chain", "base", "--token", "0xtoken", "--label", "exchange",
        "--output", str(output),
        *bounds,
    ])

    with pytest.raises(ValueError, match=message):
        args.func(args)
    assert not output.exists()
    assert not output.with_name("exchange.request.json").exists()


@pytest.mark.parametrize("body", [[], {"data": {}}], ids=["non-object", "non-list-data"])
def test_exchange_flows_reject_malformed_response_before_artifact_writing(
    tmp_path, monkeypatch, body
):
    """Fails if malformed exchange response data can be archived as evidence."""
    output = tmp_path / "exchange.json"

    class FakeNansenClient:
        def post_with_provenance(self, endpoint, payload, *, refresh=False):
            return SimpleNamespace(
                body=body,
                cache_hit=False,
                response_retrieved_at="2026-08-02T00:01:00Z",
            )

    monkeypatch.setattr(cli, "NansenClient", FakeNansenClient)
    args = cli.build_parser().parse_args([
        "flows", "--chain", "base", "--token", "0xtoken", "--label", "exchange",
        "--from", "2026-08-01T00:00:00Z", "--to", "2026-08-02T00:00:00Z",
        "--output", str(output),
    ])

    with pytest.raises(ValueError, match="API response data must be a list"):
        args.func(args)
    assert not output.exists()
    assert not output.with_name("exchange.request.json").exists()


def test_who_bought_sold_archives_exact_buy_payload_and_incomplete_page_warning(
    tmp_path, monkeypatch, capsys
):
    """Fails if buyer evidence loses request fidelity or calls a partial page complete breadth."""
    body = {
        "data": [{"address": "0xbuyer", "bought_volume_usd": 1200.0}],
        "pagination": {"is_last_page": False},
    }
    expected_payload = {
        "chain": "base",
        "token_address": "0xtoken",
        "buy_or_sell": "BUY",
        "date": {"from": "2026-08-01T00:00:00Z", "to": "2026-08-02T00:00:00Z"},
        "pagination": {"page": 1, "per_page": 20},
        "filters": {
            "include_smart_money_labels": ["Fund", "Smart Trader", "30D Smart Trader"],
            "trade_volume_usd": {"min": 1000.0},
        },
        "order_by": [{"field": "bought_volume_usd", "direction": "DESC"}],
    }

    class FakeNansenClient:
        def post_with_provenance(self, endpoint, payload, *, refresh=False):
            assert endpoint == "tgm/who-bought-sold"
            assert payload == expected_payload
            return SimpleNamespace(
                body=body,
                cache_hit=True,
                response_retrieved_at="2026-08-02T00:01:00Z",
            )

    monkeypatch.setattr(cli, "NansenClient", FakeNansenClient)
    monkeypatch.chdir(tmp_path)
    args = cli.build_parser().parse_args([
        "who-bought-sold", "--chain", "base", "--token", "0xtoken", "--side", "BUY",
        "--from", "2026-08-01T00:00:00Z", "--to", "2026-08-02T00:00:00Z",
        "--min-volume-usd", "1000", "--limit", "20",
    ])

    args.func(args)

    response_path = tmp_path / "results" / "who-bought-sold-BUY-base-0xtoken.json"
    metadata = json.loads(response_path.with_name("who-bought-sold-BUY-base-0xtoken.request.json").read_text())
    assert json.loads(response_path.read_text()) == body
    assert metadata["endpoint"] == "tgm/who-bought-sold"
    assert metadata["payload"] == expected_payload
    assert metadata["response_metadata"] == {"row_count": 1, "pagination_complete": False}
    assert "pagination_complete: false" in capsys.readouterr().out


def test_who_bought_sold_sell_changes_only_side_and_ordering_field(tmp_path, monkeypatch):
    """Fails if SELL evidence is sorted by buyer volume or changes another request dimension."""
    expected_payload = {
        "chain": "base",
        "token_address": "0xtoken",
        "buy_or_sell": "SELL",
        "date": {"from": "2026-08-01T00:00:00Z", "to": "2026-08-02T00:00:00Z"},
        "pagination": {"page": 1, "per_page": 20},
        "filters": {
            "include_smart_money_labels": ["Fund", "Smart Trader", "30D Smart Trader"],
            "trade_volume_usd": {"min": 1000.0},
        },
        "order_by": [{"field": "sold_volume_usd", "direction": "DESC"}],
    }

    class FakeNansenClient:
        def post_with_provenance(self, endpoint, payload, *, refresh=False):
            assert endpoint == "tgm/who-bought-sold"
            assert payload == expected_payload
            return SimpleNamespace(
                body={"data": [], "pagination": {"is_last_page": True}},
                cache_hit=False,
                response_retrieved_at="2026-08-02T00:01:00Z",
            )

    monkeypatch.setattr(cli, "NansenClient", FakeNansenClient)
    monkeypatch.chdir(tmp_path)
    args = cli.build_parser().parse_args([
        "who-bought-sold", "--chain", "base", "--token", "0xtoken", "--side", "SELL",
        "--from", "2026-08-01T00:00:00Z", "--to", "2026-08-02T00:00:00Z",
        "--min-volume-usd", "1000", "--limit", "20",
    ])

    args.func(args)


def test_who_bought_sold_normalizes_timezone_aware_timestamps_to_utc(tmp_path, monkeypatch):
    """Fails if a valid offset timestamp leaks into the archived request without UTC normalization."""
    class FakeNansenClient:
        def post_with_provenance(self, endpoint, payload, *, refresh=False):
            assert payload["date"] == {
                "from": "2026-08-01T00:00:00Z",
                "to": "2026-08-02T00:00:00Z",
            }
            return SimpleNamespace(
                body={"data": [], "pagination": {"is_last_page": True}},
                cache_hit=False,
                response_retrieved_at="2026-08-02T00:01:00Z",
            )

    monkeypatch.setattr(cli, "NansenClient", FakeNansenClient)
    monkeypatch.chdir(tmp_path)
    args = cli.build_parser().parse_args([
        "who-bought-sold", "--chain", "base", "--token", "0xtoken", "--side", "BUY",
        "--from", "2026-08-01T01:00:00+01:00", "--to", "2026-08-02T01:00:00+01:00",
    ])

    args.func(args)


def test_who_bought_sold_normalizes_labels_before_archiving_payload(tmp_path, monkeypatch):
    """Fails if cosmetic label whitespace produces a different archived API request."""
    class FakeNansenClient:
        def post_with_provenance(self, endpoint, payload, *, refresh=False):
            assert payload["filters"]["include_smart_money_labels"] == [
                "Fund", "Smart Trader",
            ]
            return SimpleNamespace(
                body={"data": [], "pagination": {"is_last_page": True}},
                cache_hit=False,
                response_retrieved_at="2026-08-02T00:01:00Z",
            )

    monkeypatch.setattr(cli, "NansenClient", FakeNansenClient)
    monkeypatch.chdir(tmp_path)
    args = cli.build_parser().parse_args([
        "who-bought-sold", "--chain", "base", "--token", "0xtoken", "--side", "BUY",
        "--from", "2026-08-01T00:00:00Z", "--to", "2026-08-02T00:00:00Z",
        "--labels", " Fund ", "Smart Trader ",
    ])

    args.func(args)


def test_who_bought_sold_rejects_fractional_start_after_end_before_client_construction(
    monkeypatch
):
    """Fails if timestamp string ordering accepts a fractional start later than its end."""
    class ApiMustNotBeCalled:
        def __init__(self):
            raise AssertionError("API client constructed for an inverted time window")

    monkeypatch.setattr(cli, "NansenClient", ApiMustNotBeCalled)
    args = cli.build_parser().parse_args([
        "who-bought-sold", "--chain", "base", "--token", "0xtoken", "--side", "BUY",
        "--from", "2026-08-02T00:00:00.500Z", "--to", "2026-08-02T00:00:00Z",
    ])

    with pytest.raises(ValueError, match="before --to"):
        args.func(args)


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--from", "2026-08-01T00:00:00", "--to", "2026-08-02T00:00:00Z"], "timezone-aware"),
        (["--from", "2026-08-02T00:00:00Z", "--to", "2026-08-02T00:00:00Z"], "before --to"),
        (["--limit", "0"], "between 1 and 100"),
        (["--limit", "101"], "between 1 and 100"),
        (["--min-volume-usd", "-0.1"], "non-negative"),
        (["--min-volume-usd", "nan"], "finite"),
        (["--min-volume-usd", "inf"], "finite"),
        (["--labels", ""], "empty"),
        (["--labels", "Fund", "Fund"], "duplicates"),
        (["--labels", "Fund", " Fund "], "duplicates"),
    ],
)
def test_who_bought_sold_rejects_invalid_arguments_before_client_construction(
    tmp_path, monkeypatch, arguments, message
):
    """Fails if invalid collection requests can spend a credit before being rejected."""
    class ApiMustNotBeCalled:
        def __init__(self):
            raise AssertionError("API client constructed for invalid arguments")

    monkeypatch.setattr(cli, "NansenClient", ApiMustNotBeCalled)
    base = [
        "who-bought-sold", "--chain", "base", "--token", "0xtoken", "--side", "BUY",
        "--from", "2026-08-01T00:00:00Z", "--to", "2026-08-02T00:00:00Z",
    ]
    args = cli.build_parser().parse_args(base + arguments)

    with pytest.raises(ValueError, match=message):
        args.func(args)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("body", [
    {"data": {}, "pagination": {"is_last_page": True}},
    {"data": [], "pagination": []},
    {"data": [], "pagination": {"is_last_page": "true"}},
])
def test_who_bought_sold_rejects_malformed_response_before_artifact_writing(
    tmp_path, monkeypatch, body
):
    """Fails if malformed response shapes are archived as trustworthy evidence."""
    output = tmp_path / "buyers.json"

    class FakeNansenClient:
        def post_with_provenance(self, endpoint, payload, *, refresh=False):
            return SimpleNamespace(
                body=body,
                cache_hit=False,
                response_retrieved_at="2026-08-02T00:01:00Z",
            )

    monkeypatch.setattr(cli, "NansenClient", FakeNansenClient)
    args = cli.build_parser().parse_args([
        "who-bought-sold", "--chain", "base", "--token", "0xtoken", "--side", "BUY",
        "--from", "2026-08-01T00:00:00Z", "--to", "2026-08-02T00:00:00Z",
        "--output", str(output),
    ])

    with pytest.raises(ValueError, match="API response"):
        args.func(args)
    assert not output.exists()
    assert not output.with_name("buyers.request.json").exists()


@pytest.mark.parametrize("existing_name", ["buyers.json", "buyers.request.json"])
def test_who_bought_sold_refuses_explicit_existing_artifacts_before_client_construction(
    tmp_path, monkeypatch, existing_name
):
    """Fails if explicit buyer/seller evidence can be overwritten or costs a request first."""
    output = tmp_path / "buyers.json"
    (tmp_path / existing_name).write_text("existing\n")

    class ApiMustNotBeCalled:
        def __init__(self):
            raise AssertionError("API client constructed before overwrite refusal")

    monkeypatch.setattr(cli, "NansenClient", ApiMustNotBeCalled)
    args = cli.build_parser().parse_args([
        "who-bought-sold", "--chain", "base", "--token", "0xtoken", "--side", "BUY",
        "--from", "2026-08-01T00:00:00Z", "--to", "2026-08-02T00:00:00Z",
        "--output", str(output),
    ])

    with pytest.raises(FileExistsError, match="refusing to overwrite explicit buyer/seller output"):
        args.func(args)


def test_evaluate_command_is_offline_and_reports_verified_outputs(tmp_path, monkeypatch, capsys):
    """Fails if the offline evaluator constructs an API client or hides checked files."""
    from test_evaluation import _bundle
    from src.nansen_signal_lab.evaluation import evaluate_manifest

    manifest, _ = _bundle(tmp_path)
    evaluate_manifest(manifest)

    class ClientMustNotBeConstructed:
        def __init__(self):
            raise AssertionError("offline evaluator constructed NansenClient")

    monkeypatch.setattr(cli, "NansenClient", ClientMustNotBeConstructed)
    args = cli.build_parser().parse_args([
        "evaluate", "--manifest", str(manifest), "--check",
    ])
    args.func(args)

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3
    assert all(line.startswith("verified: ") for line in lines)
