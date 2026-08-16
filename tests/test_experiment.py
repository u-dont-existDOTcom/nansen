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
    with pytest.raises(ExperimentError, match="duplicate cohort token"):
        load_and_validate_manifest(manifest)
