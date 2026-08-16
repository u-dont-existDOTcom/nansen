from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
from src.nansen_signal_lab.cli import build_parser


def flow_row(hour, price, holdings, *, complete=True, holders=2):
    return {
        "date": f"2026-08-01T{hour:02d}:00:00Z",
        "bucket_end": f"2026-08-01T{hour + 1:02d}:00:00Z",
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
    event_at_hour_1 = next(row for row in events if row["timestamp"] == "2026-08-01T01:00:00Z")
    assert event_at_hour_1["forward_price_return_1h_pct"] == pytest.approx(100 * (12 / 11 - 1))
    assert event_at_hour_1["forward_price_return_2h_pct"] is None
    assert event_at_hour_1["forward_2h_available"] is False


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
    data["evidence"][0]["sha256"] = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data))

    tables = build_analysis(load_and_validate_manifest(manifest))

    assert [row["timestamp"] for row in tables.hourly_features] == [
        "2026-08-01T00:00:00Z", "2026-08-01T01:00:00Z",
    ]
    assert tables.token_summary[0]["price_return_all_pct"] == pytest.approx(10.0)


def write_bundle(tmp_path: Path) -> Path:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    body = {"data": []}
    raw_path = raw_dir / "flows.json"
    raw_path.write_text(json.dumps(body))
    digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
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
            "selection": {},
        }],
        "evidence": [{
            "id": "flows-fix",
            "kind": "tgm_flows",
            "path": "raw/flows.json",
            "sha256": digest,
            "endpoint": "tgm/flows",
            "request": {"chain": "base", "token_address": "0xfixture"},
            "retrieved_at": "2026-08-16T00:00:00Z",
            "observed_from": None,
            "observed_to": None,
            "row_count": 0,
            "complete_count": 0,
        }],
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


def test_manifest_rejects_duplicate_token_identity(tmp_path):
    manifest = write_bundle(tmp_path)
    data = json.loads(manifest.read_text())
    data["cohort"].append(dict(data["cohort"][0]))
    manifest.write_text(json.dumps(data))
    with pytest.raises(ExperimentError, match="duplicate cohort token.*experiment_id=fixture"):
        load_and_validate_manifest(manifest)


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
