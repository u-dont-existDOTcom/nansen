import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any


class ExperimentError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenceFile:
    id: str
    kind: str
    path: Path
    sha256: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class Bundle:
    root: Path
    manifest_path: Path
    manifest: dict[str, Any]
    evidence: tuple[EvidenceFile, ...]

    @property
    def experiment_id(self) -> str:
        return str(self.manifest["experiment_id"])

    @property
    def evidence_by_id(self) -> dict[str, EvidenceFile]:
        return {item.id: item for item in self.evidence}


def sha256_file(path: str | Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def load_and_validate_manifest(manifest_path: str | Path) -> Bundle:
    path = Path(manifest_path).resolve()
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read manifest {path}: {exc}") from exc

    required = {
        "schema_version", "experiment_id", "title", "status", "created_at",
        "hypothesis", "horizons_hours", "source", "cohort", "evidence",
        "exclusions",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ExperimentError(f"manifest missing keys: {', '.join(missing)}")
    if manifest["schema_version"] != 1:
        raise ExperimentError(f"unsupported schema version: {manifest['schema_version']}")
    if manifest["status"] not in {"discovery", "holdout"}:
        raise ExperimentError(f"invalid experiment status: {manifest['status']}")

    horizons = manifest["horizons_hours"]
    if (
        not isinstance(horizons, list)
        or not horizons
        or any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in horizons)
        or len(horizons) != len(set(horizons))
    ):
        raise ExperimentError("horizons_hours must contain unique positive integers")

    root = path.parent.resolve()
    evidence = []
    seen_evidence_ids = set()
    for record in manifest["evidence"]:
        evidence_id = str(record.get("id", ""))
        if not evidence_id or evidence_id in seen_evidence_ids:
            raise ExperimentError(f"duplicate or empty evidence id: {evidence_id}")
        seen_evidence_ids.add(evidence_id)
        evidence_path = (root / str(record.get("path", ""))).resolve()
        if evidence_path != root and root not in evidence_path.parents:
            raise ExperimentError(f"evidence {evidence_id} is outside bundle: {evidence_path}")
        if not evidence_path.is_file():
            raise ExperimentError(f"evidence {evidence_id} is missing: {evidence_path}")
        expected = str(record.get("sha256", ""))
        actual = sha256_file(evidence_path)
        if actual != expected:
            raise ExperimentError(
                f"checksum mismatch for evidence {evidence_id}: expected {expected}, got {actual}"
            )
        evidence.append(EvidenceFile(
            id=evidence_id,
            kind=str(record.get("kind", "")),
            path=evidence_path,
            sha256=expected,
            metadata=dict(record),
        ))

    evidence_ids = {item.id for item in evidence}
    seen_tokens = set()
    for member in manifest["cohort"]:
        identity = (str(member.get("chain", "")), str(member.get("address", "")).lower())
        if not all(identity) or identity in seen_tokens:
            raise ExperimentError(f"duplicate cohort token: {identity[0]}:{identity[1]}")
        seen_tokens.add(identity)
        flow_id = str(member.get("flow_evidence_id", ""))
        if flow_id not in evidence_ids:
            raise ExperimentError(f"cohort token {identity[0]}:{identity[1]} has unknown flow evidence {flow_id}")

    return Bundle(
        root=root,
        manifest_path=path,
        manifest=manifest,
        evidence=tuple(evidence),
    )
