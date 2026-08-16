from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.nansen_signal_lab.experiment import ExperimentError, load_and_validate_manifest


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
