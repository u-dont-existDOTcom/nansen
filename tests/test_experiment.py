from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.nansen_signal_lab.experiment import (
    ExperimentError,
    analyze_manifest,
    build_analysis,
    build_event_windows,
    build_hourly_features,
    build_token_summary,
    csv_text,
    load_and_validate_manifest,
    prepare_flow_rows,
)
import src.nansen_signal_lab.cli as cli
from src.nansen_signal_lab.cli import build_parser, write_flow_artifacts


def test_write_flow_artifacts_preserves_payload_and_response(tmp_path):
    output = tmp_path / "raw" / "cdxr-followup.json"
    payload = {
        "chain": "ethereum",
        "token_address": "0x40aaf75454036bed56f3266ccf18f6b7befd6aca",
        "date": {"from": "2026-08-15T22:00:00Z", "to": "2026-08-16T23:00:00Z"},
        "label": "smart_money",
        "pagination": {"page": 1, "per_page": 100},
        "order_by": [{"field": "date", "direction": "ASC"}],
    }
    response_path, request_path = write_flow_artifacts(
        body={"data": []},
        payload=payload,
        output_path=output,
        cache_hit=True,
        response_retrieved_at="2026-08-16T23:01:00Z",
        artifact_written_at="2026-08-17T00:01:00Z",
    )
    assert json.loads(response_path.read_text()) == {"data": []}
    metadata = json.loads(request_path.read_text())
    assert metadata["endpoint"] == "tgm/flows"
    assert metadata["payload"] == payload
    assert metadata["cache_hit"] is True
    assert metadata["response_retrieved_at"] == "2026-08-16T23:01:00Z"
    assert metadata["artifact_written_at"] == "2026-08-17T00:01:00Z"
    assert metadata["response_sha256"] == hashlib.sha256(response_path.read_bytes()).hexdigest()
    assert "apikey" not in request_path.read_text().lower()
    assert not list(output.parent.glob("*.tmp"))


def test_flows_parser_accepts_explicit_output_and_force_flag():
    args = build_parser().parse_args([
        "flows", "--chain", "ethereum", "--token", "0xtoken",
        "--days", "2", "--output", "research/raw/flows.json", "--force-output",
    ])
    assert args.output == "research/raw/flows.json"
    assert args.force_output is True


def test_cmd_flows_default_output_writes_response_and_request_sidecar(tmp_path, monkeypatch):
    body = {
        "data": [{
            "date": "2026-08-16T22:00:00Z",
            "bucket_end": "2026-08-16T23:00:00Z",
            "is_complete": True,
            "price_usd": 1.25,
            "token_amount": 80,
        }],
    }

    class FakeNansenClient:
        def post_with_provenance(self, endpoint, payload, *, refresh=False):
            assert endpoint == "tgm/flows"
            assert payload["chain"] == "ethereum"
            assert payload["token_address"] == "0xtoken"
            return SimpleNamespace(
                body=body,
                cache_hit=False,
                response_retrieved_at="2026-08-16T23:00:05Z",
            )

    monkeypatch.setattr(cli, "NansenClient", FakeNansenClient)
    monkeypatch.chdir(tmp_path)
    scratch = tmp_path / "results"
    scratch.mkdir()
    (scratch / "flows-ethereum-0xtoken.json").write_text("old response\n")
    (scratch / "flows-ethereum-0xtoken.request.json").write_text("old sidecar\n")
    args = cli.build_parser().parse_args([
        "flows", "--chain", "ethereum", "--token", "0xtoken",
        "--from", "2026-08-16T22:00:00Z", "--to", "2026-08-16T23:00:00Z",
    ])
    args.func(args)

    response_path = tmp_path / "results" / "flows-ethereum-0xtoken.json"
    request_path = tmp_path / "results" / "flows-ethereum-0xtoken.request.json"
    assert json.loads(response_path.read_text()) == body
    metadata = json.loads(request_path.read_text())
    assert metadata["endpoint"] == "tgm/flows"
    assert metadata["response_file"] == response_path.name
    assert metadata["payload"] == {
        "chain": "ethereum",
        "token_address": "0xtoken",
        "date": {"from": "2026-08-16T22:00:00Z", "to": "2026-08-16T23:00:00Z"},
        "label": "smart_money",
        "pagination": {"page": 1, "per_page": 100},
        "order_by": [{"field": "date", "direction": "ASC"}],
    }
    assert metadata["cache_hit"] is False
    assert metadata["response_retrieved_at"] == "2026-08-16T23:00:05Z"
    assert metadata["artifact_written_at"].endswith("Z")
    assert metadata["response_sha256"] == hashlib.sha256(response_path.read_bytes()).hexdigest()


@pytest.mark.parametrize("existing_name", ["flows.json", "flows.request.json"])
def test_cmd_flows_refuses_explicit_existing_response_or_sidecar_before_api_call(
    tmp_path, monkeypatch, existing_name
):
    """Fails if an explicit evidence path can be overwritten or incurs an API call first."""
    output = tmp_path / "flows.json"
    (tmp_path / existing_name).write_text("existing\n")

    class ApiMustNotBeCalled:
        def __init__(self):
            raise AssertionError("API client constructed before overwrite refusal")

    monkeypatch.setattr(cli, "NansenClient", ApiMustNotBeCalled)
    args = cli.build_parser().parse_args([
        "flows", "--chain", "base", "--token", "0xtoken", "--output", str(output),
    ])

    with pytest.raises(FileExistsError, match="refusing to overwrite explicit flow output"):
        args.func(args)


def test_cmd_flows_force_output_replaces_response_and_sidecar(tmp_path, monkeypatch):
    """Fails if the explicit force flag cannot deliberately replace both artifact files."""
    output = tmp_path / "flows.json"
    sidecar = tmp_path / "flows.request.json"
    output.write_text("old response\n")
    sidecar.write_text("old sidecar\n")
    body = {"data": []}

    class FakeNansenClient:
        def post_with_provenance(self, endpoint, payload, *, refresh=False):
            return SimpleNamespace(
                body=body,
                cache_hit=True,
                response_retrieved_at="2026-08-16T20:00:00Z",
            )

    monkeypatch.setattr(cli, "NansenClient", FakeNansenClient)
    args = cli.build_parser().parse_args([
        "flows", "--chain", "base", "--token", "0xtoken", "--output", str(output),
        "--force-output",
    ])

    args.func(args)

    assert json.loads(output.read_text()) == body
    assert json.loads(sidecar.read_text())["cache_hit"] is True
    assert not list(tmp_path.glob("*.tmp"))


def flow_row(hour, price, holdings, *, complete=True, holders=2):
    start = datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(hours=hour)
    end = start + timedelta(hours=1)
    return {
        "date": start.isoformat().replace("+00:00", "Z"),
        "bucket_end": end.isoformat().replace("+00:00", "Z"),
        "is_complete": complete,
        "price_usd": price,
        "token_amount": holdings,
        "value_usd": price * holdings,
        "holders_count": holders,
        "total_inflows_count": 0,
        "total_outflows_count": 0,
    }


def fixture_member():
    return {
        "chain": "base",
        "symbol": "FIX",
        "address": "0xfixture",
        "role": "early",
        "selection": {},
    }


def test_prepare_rows_excludes_incomplete_and_counts_it():
    """Fails if incomplete source rows enter a valid feature series."""
    body = {"data": [flow_row(0, 10, 100), flow_row(1, 11, 110, complete=False)]}
    prepared = prepare_flow_rows(body)
    assert len(prepared.rows) == 1
    assert prepared.incomplete_count == 1


def test_prepare_rows_rejects_duplicate_timestamp():
    """Fails if two source observations can share the same analysis timestamp."""
    body = {"data": [flow_row(0, 10, 100), flow_row(0, 11, 110)]}
    with pytest.raises(ExperimentError, match="duplicate timestamp"):
        prepare_flow_rows(body)


def test_features_use_bucket_end_availability_and_preserve_source_boundaries():
    """Fails if finalized bucket contents are labeled at their unavailable start time."""
    prepared = prepare_flow_rows({"data": [
        flow_row(0, 10, 100),
        flow_row(1, 11, 110),
        flow_row(2, 12, 120),
    ]})

    features = build_hourly_features(
        experiment_id="fixture",
        cohort_member=fixture_member(),
        prepared=prepared,
        horizons=(1,),
    )
    events = build_event_windows(features, horizons=(1,))

    assert [row["timestamp"] for row in features] == [
        "2026-08-01T01:00:00Z",
        "2026-08-01T02:00:00Z",
        "2026-08-01T03:00:00Z",
    ]
    assert features[1]["source_bucket_start"] == "2026-08-01T01:00:00Z"
    assert features[1]["source_bucket_end"] == "2026-08-01T02:00:00Z"
    assert events[0]["timestamp"] == "2026-08-01T02:00:00Z"
    assert events[0]["source_bucket_start"] == "2026-08-01T01:00:00Z"
    assert events[0]["source_bucket_end"] == "2026-08-01T02:00:00Z"
    assert events[0]["forward_price_return_1h_pct"] == pytest.approx(100 * (12 / 11 - 1))


@pytest.mark.parametrize(
    ("date", "bucket_end", "message"),
    [
        ("2026-08-01T00:00:00", "2026-08-01T01:00:00Z", "timezone-aware"),
        ("2026-08-01T00:00:00Z", "2026-08-01T01:00:00", "timezone-aware"),
        ("2026-08-01T00:00:00Z", "2026-08-01T00:00:00Z", "after bucket start"),
        ("2026-08-01T01:00:00Z", "2026-08-01T00:00:00Z", "after bucket start"),
    ],
)
def test_prepare_rows_rejects_invalid_bucket_boundaries(date, bucket_end, message):
    """Fails if malformed or non-increasing bucket boundaries acquire an analysis identity."""
    row = flow_row(0, 10, 100)
    row.update({"date": date, "bucket_end": bucket_end})

    with pytest.raises(ExperimentError, match=message):
        prepare_flow_rows({"data": [row]})


def test_prepare_rows_rejects_non_object_rows():
    """Fails if a malformed raw row causes an attribute error instead of ExperimentError."""
    with pytest.raises(ExperimentError, match="flow row must be an object"):
        prepare_flow_rows({"data": ["not-an-object"]})


def test_prepare_rows_excludes_missing_or_zero_metrics_and_counts_them():
    """Fails if rows lacking usable price or holdings contaminate calculations."""
    missing_price = flow_row(1, 10, 100)
    missing_price["price_usd"] = None
    zero_price = flow_row(2, 0, 100)
    missing_holdings = flow_row(3, 10, 100)
    del missing_holdings["token_amount"]
    prepared = prepare_flow_rows({"data": [
        flow_row(0, 10, 100), missing_price, zero_price, missing_holdings,
    ]})
    assert len(prepared.rows) == 1
    assert prepared.invalid_metric_count == 3


def test_prepare_rows_excludes_string_zero_and_nonfinite_or_malformed_prices():
    """Fails if price values that cannot safely support return math are accepted."""
    string_zero = flow_row(0, 10, 100)
    string_zero["price_usd"] = "0"
    malformed_price = flow_row(1, 10, 100)
    malformed_price["price_usd"] = "not-a-number"
    infinite_price = flow_row(2, 10, 100)
    infinite_price["price_usd"] = "inf"
    nan_price = flow_row(3, 10, 100)
    nan_price["price_usd"] = float("nan")

    prepared = prepare_flow_rows({"data": [
        string_zero, malformed_price, infinite_price, nan_price,
    ]})

    assert prepared.rows == ()
    assert prepared.invalid_metric_count == 4


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("price_usd", -1),
        ("token_amount", "not-a-number"),
        ("token_amount", -1),
        ("token_amount", float("nan")),
        ("token_amount", float("inf")),
        ("token_amount", float("-inf")),
    ],
)
def test_prepare_rows_excludes_unsafe_price_or_holdings(field, value):
    """Fails if invalid metrics crash analysis or emit negative/non-finite values."""
    row = flow_row(0, 10, 100)
    row[field] = value

    prepared = prepare_flow_rows({"data": [row]})

    assert prepared.rows == ()
    assert prepared.invalid_metric_count == 1


def test_feature_and_event_windows_do_not_cross_gap_or_future():
    """Fails if returns bridge a missing hour or label an immature horizon available."""
    body = {"data": [
        flow_row(0, 10, 100),
        flow_row(1, 11, 110),
        flow_row(2, 12, 120),
        flow_row(4, 15, 130),
    ]}
    prepared = prepare_flow_rows(body)
    features = build_hourly_features(
        experiment_id="fixture",
        cohort_member=fixture_member(),
        prepared=prepared,
        horizons=(1, 2),
    )
    assert features[1]["trailing_price_return_1h_pct"] == pytest.approx(10.0)
    assert features[-1]["trailing_price_return_2h_pct"] is None
    events = build_event_windows(features, horizons=(1, 2))
    event_available_at_hour_2 = next(
        row for row in events if row["timestamp"] == "2026-08-01T02:00:00Z"
    )
    assert event_available_at_hour_2["forward_price_return_1h_pct"] == pytest.approx(100 * (12 / 11 - 1))
    assert event_available_at_hour_2["forward_price_return_2h_pct"] is None
    assert event_available_at_hour_2["forward_2h_available"] is False


def test_mfe_and_mae_use_only_prices_inside_mature_horizon():
    """Fails if excursions include a price outside a mature forward window."""
    body = {"data": [
        flow_row(0, 10, 100),
        flow_row(1, 9, 110),
        flow_row(2, 12, 110),
        flow_row(3, 8, 110),
    ]}
    prepared = prepare_flow_rows(body)
    features = build_hourly_features(
        experiment_id="fixture",
        cohort_member=fixture_member(),
        prepared=prepared,
        horizons=(2,),
    )
    event = build_event_windows(features, horizons=(2,))[0]
    assert event["mfe_2h_pct"] == pytest.approx(100 * (12 / 9 - 1))
    assert event["mae_2h_pct"] == pytest.approx(100 * (8 / 9 - 1))


def test_token_summary_weights_only_mature_accumulation_events():
    """Fails if an unavailable forward label contributes to weighted accumulation return."""
    prepared = prepare_flow_rows({"data": [
        flow_row(0, 10, 100),
        flow_row(1, 11, 110),
        flow_row(2, 12, 120),
    ]})
    features = build_hourly_features(
        experiment_id="fixture", cohort_member=fixture_member(), prepared=prepared, horizons=(1,),
    )
    events = build_event_windows(features, horizons=(1,))
    summary = build_token_summary(features, events, prepared, horizons=(1,))
    assert summary["gross_accumulation_tokens"] == 20.0
    assert summary["accumulation_event_count"] == 2
    assert summary["accumulation_weighted_forward_1h_pct"] == pytest.approx(100 * (12 / 11 - 1))


def test_token_summary_returns_none_for_24h_change_across_a_gap():
    """Fails if a 24-hour summary return bridges a missing hourly observation."""
    next_day = flow_row(0, 34, 124)
    next_day["date"] = "2026-08-02T00:00:00Z"
    next_day["bucket_end"] = "2026-08-02T01:00:00Z"
    prepared = prepare_flow_rows({"data": [
        flow_row(0, 10, 100),
        *[flow_row(hour, 10 + hour, 100 + hour) for hour in range(2, 24)],
        next_day,
    ]})
    features = build_hourly_features(
        experiment_id="fixture", cohort_member=fixture_member(), prepared=prepared, horizons=(1,),
    )
    summary = build_token_summary(features, (), prepared, horizons=(1,))

    assert summary["price_return_24h_pct"] is None
    assert summary["holdings_change_24h_pct"] is None


def test_build_analysis_uses_referenced_flow_evidence(tmp_path):
    """Fails if bundle analysis ignores the cohort member's referenced flow evidence."""
    manifest = write_bundle(tmp_path)
    raw_path = tmp_path / "raw" / "flows.json"
    raw_path.write_text(json.dumps({"data": [flow_row(0, 10, 100), flow_row(1, 11, 110)]}))
    data = json.loads(manifest.read_text())
    data["horizons_hours"] = [1]
    data["evidence"][0].update({
        "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "observed_from": "2026-08-01T00:00:00Z",
        "observed_to": "2026-08-01T01:00:00Z",
        "row_count": 2,
        "complete_count": 2,
    })
    manifest.write_text(json.dumps(data))

    tables = build_analysis(load_and_validate_manifest(manifest))

    assert [row["timestamp"] for row in tables.hourly_features] == [
        "2026-08-01T01:00:00Z", "2026-08-01T02:00:00Z",
    ]
    assert tables.token_summary[0]["price_return_all_pct"] == pytest.approx(10.0)


def write_bundle(tmp_path: Path) -> Path:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    body = {"data": []}
    raw_path = raw_dir / "flows.json"
    raw_path.write_text(json.dumps(body))
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    candidates_path = raw_dir / "candidates.csv"
    candidates_path.write_text("chain,token_address\n")
    candidates_digest = hashlib.sha256(candidates_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "experiment_id": "fixture",
        "title": "Fixture",
        "status": "discovery",
        "created_at": "2026-08-16T00:00:00Z",
        "hypothesis": "Fixture hypothesis",
        "horizons_hours": [1, 4, 12, 24],
        "source": {"provider": "Nansen", "attribution": "Powered by Nansen API"},
        "cohort": [{
            "chain": "base",
            "symbol": "FIX",
            "address": "0xfixture",
            "role": "early",
            "flow_evidence_id": "flows-fix",
            "selection": {"candidate_evidence_id": "candidates-fixture"},
        }],
        "evidence": [
            {
                "id": "flows-fix",
                "kind": "tgm_flows",
                "path": "raw/flows.json",
                "sha256": digest,
                "endpoint": "tgm/flows",
                "request": {
                    "chain": "base",
                    "token_address": "0xfixture",
                    "exact_from": None,
                    "exact_to": None,
                    "boundary_provenance": "unavailable in fixture",
                },
                "retrieved_at": "2026-08-16T00:00:00Z",
                "observed_from": None,
                "observed_to": None,
                "row_count": 0,
                "complete_count": 0,
            },
            {
                "id": "candidates-fixture",
                "kind": "token_screener_candidates",
                "path": "raw/candidates.csv",
                "sha256": candidates_digest,
                "endpoint": "token-screener",
                "request": {"request_payload_persisted": False},
                "retrieved_at": "2026-08-16T00:00:00Z",
                "observed_from": None,
                "observed_to": None,
                "row_count": 0,
                "complete_count": 0,
            },
        ],
        "exclusions": [],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def write_bundle_with_four_hour_flow_fixture(tmp_path: Path) -> Path:
    """Create four complete consecutive observations with hand-known returns."""
    manifest = write_bundle(tmp_path)
    body = {"data": [
        flow_row(0, 10, 100),
        flow_row(1, 11, 110),
        flow_row(2, 12, 120),
        flow_row(3, 13, 130),
    ]}
    raw_path = tmp_path / "raw" / "flows.json"
    raw_path.write_text(json.dumps(body))
    data = json.loads(manifest.read_text())
    data["horizons_hours"] = [1, 2]
    data["evidence"][0].update({
        "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "observed_from": "2026-08-01T00:00:00Z",
        "observed_to": "2026-08-01T03:00:00Z",
        "row_count": 4,
        "complete_count": 4,
    })
    manifest.write_text(json.dumps(data))
    return manifest


def test_analyze_writes_deterministic_csvs(tmp_path):
    """Fails if regeneration changes committed CSV bytes or omits a table."""
    manifest = write_bundle_with_four_hour_flow_fixture(tmp_path)

    paths = analyze_manifest(manifest)
    first = {path.name: path.read_bytes() for path in paths}
    analyze_manifest(manifest)
    second = {path.name: path.read_bytes() for path in paths}

    assert first == second
    assert set(first) == {"hourly-features.csv", "event-windows.csv", "token-summary.csv"}


def test_csv_text_rejects_unknown_output_fields():
    """Fails if an analysis row silently loses an unexpected output field."""
    with pytest.raises(ValueError, match="dict contains fields not in fieldnames"):
        csv_text(({"known": "ok", "unexpected": "lost"},), ("known",))


def test_analyze_check_rejects_derived_drift(tmp_path):
    """Fails if --check accepts bytes that do not match the real analysis output."""
    manifest = write_bundle_with_four_hour_flow_fixture(tmp_path)
    paths = analyze_manifest(manifest)
    paths[0].write_text("mutated\n")

    with pytest.raises(ExperimentError, match="derived output differs"):
        analyze_manifest(manifest, check=True)


def test_analyze_parser_accepts_manifest_and_check():
    """Fails if the CLI no longer accepts its reproducibility-check arguments."""
    args = build_parser().parse_args(["analyze", "--manifest", "bundle/manifest.json", "--check"])

    assert args.manifest == "bundle/manifest.json"
    assert args.check is True


def test_manifest_accepts_matching_evidence(tmp_path):
    bundle = load_and_validate_manifest(write_bundle(tmp_path))
    assert bundle.experiment_id == "fixture"
    assert bundle.evidence_by_id["flows-fix"].path.name == "flows.json"


@pytest.mark.parametrize(
    ("kind", "endpoint"),
    [
        ("tgm_flows", "token-screener"),
        ("token_screener_candidates", "tgm/flows"),
        ("unknown", "tgm/flows"),
    ],
)
def test_manifest_rejects_invalid_evidence_kind_endpoint_combinations(tmp_path, kind, endpoint):
    """Fails if an evidence file can claim an incompatible or unknown API source."""
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data["evidence"][0]["kind"] = kind
    data["evidence"][0]["endpoint"] = endpoint
    manifest.write_text(json.dumps(data))

    with pytest.raises(ExperimentError, match="evidence kind/endpoint"):
        load_and_validate_manifest(manifest)


def test_manifest_rejects_non_list_flow_data(tmp_path):
    """Fails if a flow artifact without a row list is accepted as analyzable evidence."""
    manifest = write_bundle(tmp_path)
    raw_path = tmp_path / "raw" / "flows.json"
    raw_path.write_text(json.dumps({"data": {}}))
    data = json.loads(manifest.read_text())
    data["evidence"][0]["sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data))

    with pytest.raises(ExperimentError, match="flow response data must be a list"):
        load_and_validate_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("row_count", 3),
        ("complete_count", 3),
        ("observed_from", "2026-07-31T23:00:00Z"),
        ("observed_to", "2026-08-01T04:00:00Z"),
    ],
)
def test_manifest_rejects_flow_provenance_that_disagrees_with_raw_rows(tmp_path, field, value):
    """Fails if declared counts or source observation bounds can contradict raw rows."""
    manifest = write_bundle_with_four_hour_flow_fixture(tmp_path)
    data = json.loads(manifest.read_text())
    data["evidence"][0][field] = value
    manifest.write_text(json.dumps(data))

    with pytest.raises(ExperimentError, match=field):
        load_and_validate_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "value"),
    [("chain", "ethereum"), ("token_address", "0xother")],
)
def test_manifest_rejects_flow_request_identity_mismatch(tmp_path, field, value):
    """Fails if cohort identity and persisted flow request identity can disagree."""
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data["evidence"][0]["request"][field] = value
    manifest.write_text(json.dumps(data))

    with pytest.raises(ExperimentError, match="request identity"):
        load_and_validate_manifest(manifest)


def test_manifest_rejects_unknown_candidate_selection_evidence(tmp_path):
    """Fails if cohort selection points outside the evidence index."""
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data["cohort"][0]["selection"]["candidate_evidence_id"] = "missing-candidates"
    manifest.write_text(json.dumps(data))

    with pytest.raises(ExperimentError, match="unknown candidate evidence"):
        load_and_validate_manifest(manifest)


def test_manifest_rejects_candidate_selection_pointing_to_flow_evidence(tmp_path):
    """Fails if a flow response can masquerade as the token-selection record."""
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data["cohort"][0]["selection"]["candidate_evidence_id"] = "flows-fix"
    manifest.write_text(json.dumps(data))

    with pytest.raises(ExperimentError, match="is not token_screener_candidates"):
        load_and_validate_manifest(manifest)


def test_manifest_rejects_flow_reference_pointing_to_candidate_evidence(tmp_path):
    """Fails if cohort analysis references a selection export instead of raw flow rows."""
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data["cohort"][0]["flow_evidence_id"] = "candidates-fixture"
    manifest.write_text(json.dumps(data))

    with pytest.raises(ExperimentError, match="is not tgm_flows"):
        load_and_validate_manifest(manifest)


def test_manifest_rejects_checksum_drift(tmp_path):
    manifest = write_bundle(tmp_path)
    (tmp_path / "raw" / "flows.json").write_text('{"data":[1]}')
    with pytest.raises(ExperimentError, match="checksum mismatch"):
        load_and_validate_manifest(manifest)


def test_manifest_rejects_path_escape(tmp_path):
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data["evidence"][0]["path"] = "../outside.json"
    manifest.write_text(json.dumps(data))
    with pytest.raises(ExperimentError, match="outside bundle"):
        load_and_validate_manifest(manifest)


def test_manifest_rejects_evm_duplicate_token_identity_case_insensitively(tmp_path):
    """Fails if casing lets the same EVM address enter a cohort twice."""
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    duplicate = dict(data["cohort"][0])
    duplicate["address"] = "0xFIXTURE"
    data["cohort"].append(duplicate)
    manifest.write_text(json.dumps(data))
    with pytest.raises(ExperimentError, match="duplicate cohort token.*experiment_id=fixture"):
        load_and_validate_manifest(manifest)


def test_manifest_treats_solana_address_case_as_significant(tmp_path):
    """Fails if distinct case-sensitive Solana identities are collapsed by normalization."""
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    first = data["cohort"][0]
    first.update({"chain": "solana", "address": "AbC", "flow_evidence_id": "flows-fix"})
    data["evidence"][0]["request"].update({"chain": "solana", "token_address": "AbC"})
    second = dict(first)
    second["address"] = "abc"
    second["flow_evidence_id"] = "flows-fix-2"
    data["cohort"].append(second)
    second_flow = dict(data["evidence"][0])
    second_flow["id"] = "flows-fix-2"
    second_flow["request"] = {"chain": "solana", "token_address": "abc"}
    data["evidence"].append(second_flow)
    manifest.write_text(json.dumps(data))

    bundle = load_and_validate_manifest(manifest)

    assert [member["address"] for member in bundle.manifest["cohort"]] == ["AbC", "abc"]


def test_manifest_rejects_non_object_top_level(tmp_path):
    manifest = write_bundle(tmp_path)
    manifest.write_text("[]")
    with pytest.raises(ExperimentError, match="manifest must be an object.*experiment_id=unknown"):
        load_and_validate_manifest(manifest)


def test_manifest_rejects_non_list_evidence(tmp_path):
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data["evidence"] = {}
    manifest.write_text(json.dumps(data))
    with pytest.raises(ExperimentError, match="evidence must be a list.*experiment_id=fixture"):
        load_and_validate_manifest(manifest)


def test_manifest_rejects_non_object_evidence_record(tmp_path):
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data["evidence"] = [[]]
    manifest.write_text(json.dumps(data))
    with pytest.raises(ExperimentError, match="evidence record must be an object.*evidence_id=unknown"):
        load_and_validate_manifest(manifest)


def test_manifest_rejects_non_list_cohort(tmp_path):
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data["cohort"] = {}
    manifest.write_text(json.dumps(data))
    with pytest.raises(ExperimentError, match="cohort must be a list.*experiment_id=fixture"):
        load_and_validate_manifest(manifest)


def test_manifest_rejects_non_object_cohort_member(tmp_path):
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data["cohort"] = [[]]
    manifest.write_text(json.dumps(data))
    with pytest.raises(ExperimentError, match="cohort member must be an object.*experiment_id=fixture"):
        load_and_validate_manifest(manifest)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", 2, "unsupported schema version"),
        ("status", "invalid", "invalid experiment status"),
        ("horizons_hours", [1, 1], "horizons_hours must contain unique positive integers"),
    ],
)
def test_manifest_validation_errors_include_experiment_id(tmp_path, field, value, message):
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data[field] = value
    manifest.write_text(json.dumps(data))
    with pytest.raises(ExperimentError, match=f"{message}.*experiment_id=fixture"):
        load_and_validate_manifest(manifest)


def test_manifest_missing_key_error_includes_experiment_id(tmp_path):
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    del data["title"]
    manifest.write_text(json.dumps(data))
    with pytest.raises(ExperimentError, match="manifest missing keys: title.*experiment_id=fixture"):
        load_and_validate_manifest(manifest)


def test_committed_pilot_summary_matches_observed_results():
    manifest = Path("research/experiments/2026-08-16-seven-token-pilot/manifest.json")
    tables = build_analysis(load_and_validate_manifest(manifest))
    actual = {
        row["symbol"]: (
            round(row["price_return_24h_pct"], 2),
            round(row["holdings_change_24h_pct"], 2),
            round(row["price_return_all_pct"], 2),
            round(row["holdings_change_all_pct"], 2),
        )
        for row in tables.token_summary
    }
    assert actual == {
        "CDXR": (0.04, 45.55, 0.72, 52.50),
        "AI-HEDGE-FUND": (-27.01, 0.70, -20.14, 0.14),
        "CHEAT.SH": (39.80, 1.28, 171.94, 1.14),
        "MONGO": (-20.48, 0.20, -64.11, 1.31),
        "PRISMA": (168.92, 0.86, 264.03, -0.15),
        "TOAD": (-6.98, 5.35, -46.95, 32.58),
        "CATE": (27.23, 3.71, 9.48, 11.28),
    }
    assert {
        (row["observed_from"], row["observed_to"])
        for row in tables.token_summary
    } == {("2026-08-12T11:00:00Z", "2026-08-16T09:00:00Z")}
    cdxr_largest = max(
        (row for row in tables.event_windows if row["symbol"] == "CDXR"),
        key=lambda row: abs(row["holdings_delta_tokens"]),
    )
    assert cdxr_largest["timestamp"] == "2026-08-15T22:00:00Z"
    assert cdxr_largest["source_bucket_start"] == "2026-08-15T21:00:00Z"
    assert cdxr_largest["source_bucket_end"] == "2026-08-15T22:00:00Z"
