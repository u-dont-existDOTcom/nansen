from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
import textwrap
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.nansen_signal_lab.cli as cli


def _exit_while_holding_artifact_lock(output_path):
    with cli._artifact_pair_lock(output_path):
        os._exit(0)


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


def test_write_api_artifacts_shallow_copies_response_metadata(tmp_path):
    """Fails if later caller changes can alter the top-level archived response metadata."""
    response_metadata = {"row_count": 1, "pagination_complete": True}
    _, request_path = cli.write_api_artifacts(
        body={"data": []},
        payload={},
        endpoint="tgm/who-bought-sold",
        output_path=tmp_path / "evidence.json",
        cache_hit=False,
        response_retrieved_at="2026-08-02T00:01:00Z",
        artifact_written_at="2026-08-02T00:02:00Z",
        response_metadata=response_metadata,
    )
    response_metadata["row_count"] = 999

    assert json.loads(request_path.read_text())["response_metadata"] == {
        "row_count": 1,
        "pagination_complete": True,
    }


@pytest.mark.parametrize("existing_pair", [False, True])
def test_write_api_artifacts_rolls_back_pair_when_sidecar_install_fails(
    tmp_path, monkeypatch, existing_pair
):
    """Fails if a sidecar-install failure leaves an unpaired or stale response artifact."""
    output = tmp_path / "evidence.json"
    request = tmp_path / "evidence.request.json"
    if existing_pair:
        output.write_bytes(b"old response\n")
        request.write_bytes(b"old sidecar\n")
    original_files = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    def fail_sidecar_install(temporary, target):
        if target == request:
            raise OSError("injected sidecar install failure")
        temporary.replace(target)

    monkeypatch.setattr(cli, "_install_artifact", fail_sidecar_install, raising=False)

    with pytest.raises(OSError, match="injected sidecar install failure"):
        cli.write_api_artifacts(
            body={"data": ["new"]},
            payload={"page": 1},
            endpoint="tgm/who-bought-sold",
            output_path=output,
            cache_hit=False,
            response_retrieved_at="2026-08-02T00:01:00Z",
            artifact_written_at="2026-08-02T00:02:00Z",
        )

    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == original_files


def test_write_api_artifacts_pair_lock_blocks_second_writer_before_targets_change(tmp_path):
    """Fails if a concurrent writer can enter a response/sidecar pair commit without its lock."""
    output = tmp_path / "evidence.json"
    request = tmp_path / "evidence.request.json"
    output.write_bytes(b"old response\n")
    request.write_bytes(b"old sidecar\n")
    started = threading.Event()
    finished = threading.Event()
    failures = []

    def second_writer():
        try:
            started.set()
            cli.write_api_artifacts(
                body={"data": ["new"]},
                payload={"page": 1},
                endpoint="tgm/who-bought-sold",
                output_path=output,
                cache_hit=False,
                response_retrieved_at="2026-08-02T00:01:00Z",
                artifact_written_at="2026-08-02T00:02:00Z",
            )
        except BaseException as exc:  # Assert thread failures on the test thread.
            failures.append(exc)
        finally:
            finished.set()

    with cli._artifact_pair_lock(output):
        worker = threading.Thread(target=second_writer)
        worker.start()
        assert started.wait(timeout=1)
        assert not finished.wait(timeout=0.1)
        assert output.read_bytes() == b"old response\n"
        assert request.read_bytes() == b"old sidecar\n"

    assert finished.wait(timeout=10)
    worker.join()
    assert not failures
    assert json.loads(output.read_text()) == {"data": ["new"]}
    assert not list(tmp_path.glob(".*"))


def test_artifact_lock_uses_no_stale_path_sentinel(tmp_path):
    """Fails if an abrupt writer exit can strand a filesystem lock-path sentinel."""
    output = tmp_path / "evidence.json"

    with cli._artifact_pair_lock(output):
        assert not list(tmp_path.glob("*.pair.lock"))


def test_artifact_lock_is_released_after_abrupt_owner_exit(tmp_path):
    """Fails if a killed writer can permanently block later evidence collection."""
    output = tmp_path / "evidence.json"
    context = multiprocessing.get_context("fork")
    holder = context.Process(target=_exit_while_holding_artifact_lock, args=(output,))
    holder.start()
    holder.join(timeout=10)
    assert holder.exitcode == 0

    response_path, request_path = cli.write_api_artifacts(
        body={"data": []},
        payload={},
        endpoint="tgm/who-bought-sold",
        output_path=output,
        cache_hit=False,
        response_retrieved_at="2026-08-02T00:01:00Z",
        artifact_written_at="2026-08-02T00:02:00Z",
    )

    assert response_path.exists()
    assert request_path.exists()


def test_write_api_artifacts_recovers_pair_after_real_subprocess_crash(tmp_path):
    """Fails if abrupt mid-commit exit leaves mismatched evidence or transaction debris."""
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    output = artifact_dir / "evidence.json"
    request = artifact_dir / "evidence.request.json"
    cli.write_api_artifacts(
        body={"data": ["old"]},
        payload={"writer": "old"},
        endpoint="tgm/flows",
        output_path=output,
        cache_hit=False,
        response_retrieved_at="2026-08-02T00:01:00Z",
        artifact_written_at="2026-08-02T00:02:00Z",
    )
    crash_marker = tmp_path / "response-installed.marker"
    child_code = textwrap.dedent(
        """
        import os
        import sys
        from pathlib import Path

        import src.nansen_signal_lab.cli as cli

        output = Path(sys.argv[1])
        marker = Path(sys.argv[2])
        install_artifact = cli._install_artifact

        def crash_after_response_install(temporary, target):
            install_artifact(temporary, target)
            if target == output:
                with marker.open("wb") as signal:
                    signal.write(b"response-installed\\n")
                    signal.flush()
                    os.fsync(signal.fileno())
                os._exit(86)

        cli._install_artifact = crash_after_response_install
        cli.write_api_artifacts(
            body={"data": ["interrupted"]},
            payload={"writer": "interrupted"},
            endpoint="tgm/flows",
            output_path=output,
            cache_hit=False,
            response_retrieved_at="2026-08-02T00:03:00Z",
            artifact_written_at="2026-08-02T00:04:00Z",
        )
        """
    )

    crashed = subprocess.run(
        [sys.executable, "-c", child_code, str(output), str(crash_marker)],
        cwd=Path.cwd(),
        timeout=10,
        check=False,
    )

    assert crashed.returncode == 86
    assert crash_marker.read_text() == "response-installed\n"
    interrupted_metadata = json.loads(request.read_text())
    assert hashlib.sha256(output.read_bytes()).hexdigest() != interrupted_metadata[
        "response_sha256"
    ]

    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        cli.write_api_artifacts(
            body={"data": ["must-not-overwrite"]},
            payload={"writer": "must-not-overwrite"},
            endpoint="tgm/flows",
            output_path=output,
            cache_hit=False,
            response_retrieved_at="2026-08-02T00:05:00Z",
            artifact_written_at="2026-08-02T00:06:00Z",
            overwrite=False,
        )

    recovered_metadata = json.loads(request.read_text())
    assert json.loads(output.read_text()) == {"data": ["interrupted"]}
    assert recovered_metadata["payload"] == {"writer": "interrupted"}
    assert hashlib.sha256(output.read_bytes()).hexdigest() == recovered_metadata[
        "response_sha256"
    ]
    assert not list(artifact_dir.glob(".*"))


@pytest.mark.parametrize(
    "restored_target",
    ["evidence.json", "evidence.request.json"],
    ids=["after-response-restore", "after-sidecar-restore"],
)
def test_write_api_artifacts_recovers_repeatedly_after_rollback_crash(
    tmp_path, restored_target
):
    """Fails if rollback direction is not durable and idempotent across process exit."""
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    output = artifact_dir / "evidence.json"
    request = artifact_dir / "evidence.request.json"
    cli.write_api_artifacts(
        body={"data": ["old"]},
        payload={"writer": "old"},
        endpoint="tgm/flows",
        output_path=output,
        cache_hit=False,
        response_retrieved_at="2026-08-02T00:01:00Z",
        artifact_written_at="2026-08-02T00:02:00Z",
    )
    old_response = output.read_bytes()
    old_request = request.read_bytes()
    crash_marker = tmp_path / f"{restored_target}.restored.marker"
    child_code = textwrap.dedent(
        """
        import os
        import sys
        from pathlib import Path

        import src.nansen_signal_lab.cli as cli

        output = Path(sys.argv[1])
        request = Path(sys.argv[2])
        marker = Path(sys.argv[3])
        restored_target = sys.argv[4]
        install_artifact = cli._install_artifact
        path_replace = Path.replace

        def fail_sidecar_install(temporary, target):
            if target == request:
                raise OSError("injected sidecar install failure")
            install_artifact(temporary, target)

        def crash_after_target_restore(path, target):
            result = path_replace(path, target)
            if path.name.endswith(".bak") and Path(target).name == restored_target:
                with marker.open("wb") as signal:
                    signal.write(b"target-restored\\n")
                    signal.flush()
                    os.fsync(signal.fileno())
                os._exit(87)
            return result

        cli._install_artifact = fail_sidecar_install
        Path.replace = crash_after_target_restore
        cli.write_api_artifacts(
            body={"data": ["new"]},
            payload={"writer": "new"},
            endpoint="tgm/flows",
            output_path=output,
            cache_hit=False,
            response_retrieved_at="2026-08-02T00:03:00Z",
            artifact_written_at="2026-08-02T00:04:00Z",
        )
        """
    )

    crashed = subprocess.run(
        [
            sys.executable,
            "-c",
            child_code,
            str(output),
            str(request),
            str(crash_marker),
            restored_target,
        ],
        cwd=Path.cwd(),
        timeout=10,
        check=False,
    )

    assert crashed.returncode == 87
    assert crash_marker.read_text() == "target-restored\n"
    for attempt in range(2):
        with pytest.raises(FileExistsError, match="refusing to overwrite"):
            cli.write_api_artifacts(
                body={"data": [f"must-not-overwrite-{attempt}"]},
                payload={"writer": f"must-not-overwrite-{attempt}"},
                endpoint="tgm/flows",
                output_path=output,
                cache_hit=False,
                response_retrieved_at="2026-08-02T00:05:00Z",
                artifact_written_at="2026-08-02T00:06:00Z",
                overwrite=False,
            )

    assert output.read_bytes() == old_response
    assert request.read_bytes() == old_request
    recovered_metadata = json.loads(request.read_text())
    assert hashlib.sha256(output.read_bytes()).hexdigest() == recovered_metadata[
        "response_sha256"
    ]
    assert not list(artifact_dir.glob(".*"))


def test_write_api_artifacts_cleans_registered_backup_when_later_backup_fails(
    tmp_path, monkeypatch
):
    """Fails if a later backup error leaks an earlier staged backup file."""
    output = tmp_path / "evidence.json"
    request = tmp_path / "evidence.request.json"
    output.write_bytes(b"old response\n")
    request.write_bytes(b"old sidecar\n")
    original_files = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    write_backup = cli._write_sibling_backup
    calls = 0

    def fail_second_backup(path, content):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second backup failure")
        return write_backup(path, content)

    monkeypatch.setattr(cli, "_write_sibling_backup", fail_second_backup)

    with pytest.raises(OSError, match="injected second backup failure"):
        cli.write_api_artifacts(
            body={"data": ["new"]},
            payload={"page": 1},
            endpoint="tgm/who-bought-sold",
            output_path=output,
            cache_hit=False,
            response_retrieved_at="2026-08-02T00:01:00Z",
            artifact_written_at="2026-08-02T00:02:00Z",
        )

    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == original_files


def test_write_api_artifacts_serializes_overwrite_refusal_between_real_writers(
    tmp_path, monkeypatch
):
    """Fails if writer B can pass its overwrite check while writer A commits the pair."""
    output = tmp_path / "evidence.json"
    request = tmp_path / "evidence.request.json"
    response_install_started = threading.Event()
    release_first_writer = threading.Event()
    first_response_install = True
    writer_a_errors = []
    writer_b_errors = []
    writer_b_started = threading.Event()
    writer_b_finished = threading.Event()
    install_artifact = cli._install_artifact

    def pause_first_response_install(temporary, target):
        nonlocal first_response_install
        if target == output and first_response_install:
            first_response_install = False
            response_install_started.set()
            assert release_first_writer.wait(timeout=10)
        install_artifact(temporary, target)

    monkeypatch.setattr(cli, "_install_artifact", pause_first_response_install)

    def writer_a():
        try:
            cli.write_api_artifacts(
                body={"data": ["A"]},
                payload={"writer": "A"},
                endpoint="tgm/who-bought-sold",
                output_path=output,
                cache_hit=False,
                response_retrieved_at="2026-08-02T00:01:00Z",
                artifact_written_at="2026-08-02T00:02:00Z",
                overwrite=False,
            )
        except BaseException as exc:
            writer_a_errors.append(exc)

    def writer_b():
        try:
            writer_b_started.set()
            cli.write_api_artifacts(
                body={"data": ["B"]},
                payload={"writer": "B"},
                endpoint="tgm/who-bought-sold",
                output_path=output,
                cache_hit=False,
                response_retrieved_at="2026-08-02T00:01:00Z",
                artifact_written_at="2026-08-02T00:02:00Z",
                overwrite=False,
            )
        except BaseException as exc:
            writer_b_errors.append(exc)
        finally:
            writer_b_finished.set()

    first = threading.Thread(target=writer_a)
    first.start()
    assert response_install_started.wait(timeout=10)
    second = threading.Thread(target=writer_b)
    second.start()
    assert writer_b_started.wait(timeout=1)
    try:
        assert not writer_b_finished.wait(timeout=0.1)
    finally:
        release_first_writer.set()
    first.join(timeout=10)
    assert not first.is_alive()
    assert writer_b_finished.wait(timeout=10)
    second.join()

    assert not writer_a_errors
    assert len(writer_b_errors) == 1
    assert isinstance(writer_b_errors[0], FileExistsError)
    assert json.loads(output.read_text()) == {"data": ["A"]}
    assert json.loads(request.read_text())["payload"] == {"writer": "A"}
    assert not list(tmp_path.glob(".*"))
