from __future__ import annotations

import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .artifacts import atomic_replace_bytes, canonical_json_bytes, write_json_once
from .evaluation import load_evaluation_manifest


class ProspectiveError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProspectiveBundle:
    root: Path
    manifest_path: Path
    manifest: dict[str, Any]

    @property
    def experiment_id(self) -> str:
        return str(self.manifest["experiment_id"])


NEXT_STAGE = {
    "preregistered": {"snapshot_collected", "unscorable"},
    "snapshot_collected": {"decision_sealed", "unscorable"},
    "decision_sealed": {"entry_observed", "unscorable"},
    "entry_observed": {"settled", "unscorable"},
    "settled": set(),
    "unscorable": set(),
}

_SEAL_PATHS = {
    "snapshot_collected": "seals/snapshot.json",
    "decision_sealed": "seals/decision.json",
    "entry_observed": "seals/entry.json",
    "settled": "seals/outcome.json",
    "unscorable": "seals/unscorable.json",
}
_TOP_LEVEL_KEYS = {
    "schema_version",
    "experiment_id",
    "title",
    "created_at",
    "hypothesis",
    "stage",
    "source_strategy_manifest",
    "source_strategy_manifest_sha256",
    "preregistration_path",
    "preregistration_sha256",
    "design_path",
    "design_sha256",
    "nansen_contract_path",
    "nansen_contract_sha256",
    "max_nansen_calls",
    "max_nansen_credits",
    "budget_root",
    "seals",
    "artifacts",
}
_SEAL_REF_KEYS = {"stage", "path", "sha256"}
_ARTIFACT_REF_KEYS = {"stage", "path", "sha256"}
_SEAL_KEYS = {
    "schema_version",
    "experiment_id",
    "stage",
    "recorded_at",
    "previous_seal_sha256",
    "budget_snapshot_path",
    "budget_snapshot_sha256",
    "budget_journal_head_sha256",
    "artifacts",
}
_SEALED_ARTIFACT_KEYS = {"path", "sha256"}
_SNAPSHOT_KEYS = {
    "schema_version",
    "stage",
    "recorded_at",
    "totals",
    "provider_remaining",
    "journal_head_sha256",
    "transition_sha256s",
    "halted_reason",
}
_MARKER_KEYS = {
    "schema_version",
    "experiment_id",
    "prior_manifest_sha256",
    "prior_manifest",
    "proposed_stage",
    "recorded_at",
    "seal_path",
    "seal_sha256",
    "seal",
    "artifacts",
    "budget_snapshot_path",
    "budget_snapshot_sha256",
    "proposed_manifest_sha256",
    "proposed_manifest",
}
_SOURCE_PATH = "../2026-08-17-paper-strategy-feasibility/manifest.json"
_DESIGN_PATH = "../../../docs/superpowers/specs/2026-08-17-gpt-prospective-pilot-design.md"
_DESIGN_V2_PATH = "../../../docs/superpowers/specs/2026-08-17-gpt-prospective-pilot-account-baseline-v2.md"
_DESIGN_V3_PATH = "../../../docs/superpowers/specs/2026-08-18-gpt-prospective-pilot-completed-flow-v3.md"
_DESIGN_V4_PATH = "../../../docs/superpowers/specs/2026-08-18-gpt-prospective-pilot-contract-context-v4.md"
_DESIGN_V5_PATH = "../../../docs/superpowers/specs/2026-08-18-gpt-prospective-pilot-schema-subset-v5.md"
_DESIGN_PATHS = {
    _DESIGN_PATH,
    _DESIGN_V2_PATH,
    _DESIGN_V3_PATH,
    _DESIGN_V4_PATH,
    _DESIGN_V5_PATH,
}
_CONTRACT_PATH = "../../../docs/superpowers/specs/2026-08-17-nansen-api-contract-snapshot.json"
_HEX = frozenset("0123456789abcdef")
_TIMESTAMP_FIELDS = {
    "request_started_at",
    "response_retrieved_at",
    "provider_created_at",
    "artifact_written_at",
}


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise ProspectiveError(f"cannot read referenced file {path}: {exc}") from exc


def _keys(value: Any, expected: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProspectiveError(f"{label} must be an object")
    missing = sorted(expected - set(value))
    if missing:
        raise ProspectiveError(f"{label} missing keys: {', '.join(missing)}")
    unknown = sorted(set(value) - expected)
    if unknown:
        raise ProspectiveError(f"{label} has unknown keys: {', '.join(unknown)}")
    return value


def _string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProspectiveError(f"{field} must be a non-empty string")
    return value


def _hash(value: Any, *, field: str, allow_none: bool = False) -> str | None:
    if allow_none and value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _HEX for character in value)
    ):
        raise ProspectiveError(f"{field} must be a lowercase SHA-256")
    return value


def _time(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ProspectiveError(f"{field} must be a timezone-aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProspectiveError(f"{field} must be a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProspectiveError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _assert_no_symlink(path: Path, anchor: Path, *, label: str) -> None:
    try:
        relative = path.relative_to(anchor)
    except ValueError as exc:
        raise ProspectiveError(f"{label} escapes its trusted root") from exc
    cursor = anchor
    if cursor.is_symlink():
        raise ProspectiveError(f"{label} cannot traverse a symlink: {cursor}")
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ProspectiveError(f"{label} cannot traverse a symlink: {cursor}")


def _strict_relative(value: Any, *, label: str) -> PurePosixPath:
    text = _string(value, field=label)
    if "\\" in text:
        raise ProspectiveError(f"{label} must use a normalized relative path")
    relative = PurePosixPath(text)
    if (
        relative.is_absolute()
        or text != relative.as_posix()
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ProspectiveError(f"{label} must use a normalized relative path")
    return relative


def _bundle_ref(root: Path, value: Any, *, label: str) -> tuple[str, Path]:
    relative = _strict_relative(value, label=label)
    lexical = root.joinpath(*relative.parts)
    _assert_no_symlink(lexical, root, label=label)
    resolved = lexical.resolve()
    if resolved != root and root not in resolved.parents:
        raise ProspectiveError(f"{label} escapes the prospective bundle")
    return relative.as_posix(), resolved


def _external_ref(
    root: Path, value: Any, *, label: str, exact: str, expected: Path
) -> Path:
    text = _string(value, field=label)
    if text != exact:
        raise ProspectiveError(f"{label} must name the pinned repository file {exact}")
    relative = PurePosixPath(text)
    lexical = root.joinpath(*relative.parts)
    repo_root = root.parents[2]
    _assert_no_symlink(lexical, repo_root, label=label)
    resolved = lexical.resolve()
    if resolved != expected.resolve() or not resolved.is_file():
        raise ProspectiveError(f"{label} must resolve to {expected}")
    return resolved


def _provided_artifact_path(root: Path, path: Path, *, label: str) -> tuple[str, Path]:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise ProspectiveError(f"{label} escapes the prospective bundle") from exc
    else:
        relative = candidate
    normalized = _strict_relative(relative.as_posix(), label=label)
    return _bundle_ref(root, normalized.as_posix(), label=label)


def _read_json(path: Path, *, label: str, canonical: bool = False) -> Any:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProspectiveError(f"cannot read {label} {path}: {exc}") from exc
    if canonical and canonical_json_bytes(value) != raw:
        raise ProspectiveError(f"{label} is not canonical JSON: {path}")
    return value


def _validate_snapshot(
    path: Path, *, stage: str, seal_time: datetime
) -> tuple[str, str | None]:
    document = _keys(
        _read_json(path, label="budget snapshot", canonical=True),
        _SNAPSHOT_KEYS,
        label="budget snapshot",
    )
    if document["schema_version"] != 1 or document["stage"] != stage:
        raise ProspectiveError("budget snapshot identity does not match proposed stage")
    snapshot_time = _time(document["recorded_at"], field="budget snapshot recorded_at")
    if snapshot_time > seal_time:
        raise ProspectiveError("seal recorded_at precedes a referenced budget timestamp")
    totals = document["totals"]
    if (
        not isinstance(totals, dict)
        or set(totals) != {"calls", "credits"}
        or any(
            not isinstance(totals[field], int)
            or isinstance(totals[field], bool)
            or totals[field] < 0
            for field in ("calls", "credits")
        )
    ):
        raise ProspectiveError("budget snapshot totals are invalid")
    hashes = document["transition_sha256s"]
    if not isinstance(hashes, list):
        raise ProspectiveError("budget snapshot transition hashes must be a list")
    for index, digest in enumerate(hashes):
        _hash(digest, field=f"budget snapshot transition hash {index}")
    if len(set(hashes)) != len(hashes):
        raise ProspectiveError("budget snapshot transition hashes must be unique")
    journal_head = _hash(
        document["journal_head_sha256"],
        field="budget snapshot journal head",
        allow_none=True,
    )
    if (not hashes and journal_head is not None) or (
        hashes and journal_head != hashes[-1]
    ):
        raise ProspectiveError("budget snapshot journal head does not match its transitions")
    return _sha256_file(path), journal_head


def _timestamp_values(value: Any, *, location: str) -> Iterator[tuple[str, datetime]]:
    if isinstance(value, dict):
        parsed: dict[str, datetime] = {}
        for key in _TIMESTAMP_FIELDS & set(value):
            if key == "provider_created_at" and value[key] is None:
                continue
            parsed[key] = _time(value[key], field=f"{location} {key}")
            yield key, parsed[key]
        request = parsed.get("request_started_at")
        retrieval = parsed.get("response_retrieved_at")
        provider = parsed.get("provider_created_at")
        written = parsed.get("artifact_written_at")
        if request is not None and retrieval is not None and request > retrieval:
            raise ProspectiveError(f"{location} contains an internal timestamp reversal")
        if retrieval is not None and written is not None and retrieval > written:
            raise ProspectiveError(f"{location} contains an internal timestamp reversal")
        if request is not None and written is not None and request > written:
            raise ProspectiveError(f"{location} contains an internal timestamp reversal")
        if provider is not None and written is not None and provider > written:
            raise ProspectiveError(
                f"{location} provider timestamp is later than local durable-write time"
            )
        if provider is not None and retrieval is not None and provider > retrieval:
            raise ProspectiveError(f"{location} contains an internal timestamp reversal")
        for child_key, child in value.items():
            yield from _timestamp_values(child, location=f"{location}.{child_key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _timestamp_values(child, location=f"{location}[{index}]")


def _validate_artifact(
    path: Path, *, seal_time: datetime, require_written_at: bool = False
) -> str:
    if not path.is_file():
        raise ProspectiveError(f"sealed artifact is missing: {path}")
    is_raw_model_response = (
        path.parent.parent.name == "model"
        and path.name.startswith("attempt-")
        and path.name.endswith("-response.json")
    )
    is_raw_nansen_response = (
        path.parent.parent.name == "nansen"
        and path.parent.parent.parent.name == "raw"
        and path.name.startswith("attempt-")
        and path.name.endswith("-response.json")
    )
    metadata_path = path.with_name(
        path.name.removesuffix("-response.json") + "-response-metadata.json"
    )
    if (
        (is_raw_model_response or is_raw_nansen_response)
        and metadata_path.is_file()
        and not metadata_path.is_symlink()
    ):
        raw = path.read_bytes()
        metadata = _read_json(
            metadata_path,
            label="provider raw response metadata",
            canonical=True,
        )
        if (
            not isinstance(metadata, dict)
            or metadata.get("schema_version") != 1
            or metadata.get("response_file") != path.name
            or metadata.get("response_sha256") != _sha256_bytes(raw)
            or not isinstance(metadata.get("response_retrieved_at"), str)
            or not isinstance(metadata.get("artifact_written_at"), str)
        ):
            raise ProspectiveError("provider raw response metadata is invalid")
        for field, timestamp in _timestamp_values(
            metadata,
            location=metadata_path.as_posix(),
        ):
            if timestamp > seal_time:
                raise ProspectiveError(
                    f"seal recorded_at precedes referenced {field} timestamp in {metadata_path}"
                )
        return _sha256_bytes(raw)
    is_raw_openapi = (
        path.name == "nansen-openapi.json"
        and path.parent.name == "contracts"
        and path.parent.parent.name == "raw"
    )
    openapi_metadata = path.with_name("nansen-openapi-metadata.json")
    if is_raw_openapi and openapi_metadata.is_file() and not openapi_metadata.is_symlink():
        raw = path.read_bytes()
        metadata = _read_json(
            openapi_metadata,
            label="Nansen OpenAPI raw metadata",
            canonical=True,
        )
        if (
            not isinstance(metadata, dict)
            or metadata.get("schema_version") != 1
            or metadata.get("source_sha256") != _sha256_bytes(raw)
            or not isinstance(metadata.get("artifact_written_at"), str)
        ):
            raise ProspectiveError("Nansen OpenAPI raw metadata is invalid")
        for field, timestamp in _timestamp_values(
            metadata,
            location=openapi_metadata.as_posix(),
        ):
            if timestamp > seal_time:
                raise ProspectiveError(
                    f"seal recorded_at precedes referenced {field} timestamp in {openapi_metadata}"
                )
        return _sha256_bytes(raw)
    if path.suffix == ".json":
        document = _read_json(path, label="sealed JSON artifact")
        if require_written_at and (
            not isinstance(document, dict) or "artifact_written_at" not in document
        ):
            raise ProspectiveError(
                f"derived JSON artifact must carry artifact_written_at: {path}"
            )
        stem = path.name.removesuffix(".json")
        is_request = stem == "request" or stem.endswith(
            (".request", "-request", "_request")
        )
        is_response = stem == "response" or stem.endswith(
            (".response", "-response", "_response")
        )
        if is_request and (
            not isinstance(document, dict)
            or "request_started_at" not in document
            or "artifact_written_at" not in document
        ):
            raise ProspectiveError(
                "request sidecar must carry request_started_at and "
                f"artifact_written_at timestamps: {path}"
            )
        if is_response and (
            not isinstance(document, dict)
            or "response_retrieved_at" not in document
            or "artifact_written_at" not in document
        ):
            raise ProspectiveError(
                "response sidecar must carry response_retrieved_at and "
                f"artifact_written_at timestamps: {path}"
            )
        for field, timestamp in _timestamp_values(document, location=path.as_posix()):
            if timestamp > seal_time:
                raise ProspectiveError(
                    f"seal recorded_at precedes referenced {field} timestamp in {path}"
                )
    return _sha256_file(path)


def _validate_manifest_fields(root: Path, manifest: Any) -> dict[str, Any]:
    manifest = _keys(manifest, _TOP_LEVEL_KEYS, label="prospective manifest")
    if manifest["schema_version"] != 4:
        raise ProspectiveError(
            f"unsupported prospective schema: {manifest['schema_version']}"
        )
    for field in ("experiment_id", "title", "hypothesis"):
        _string(manifest[field], field=field)
    if root.name != manifest["experiment_id"]:
        raise ProspectiveError("prospective manifest experiment_id must match its directory")
    _time(manifest["created_at"], field="created_at")
    if not isinstance(manifest["stage"], str) or manifest["stage"] not in NEXT_STAGE:
        raise ProspectiveError(f"invalid prospective stage: {manifest['stage']}")
    for field in ("max_nansen_calls", "max_nansen_credits"):
        if manifest[field] != 10 or isinstance(manifest[field], bool):
            raise ProspectiveError(f"{field} must equal 10")
    if manifest["budget_root"] != "budget":
        raise ProspectiveError("budget_root must be the normalized bundle path budget")
    _, budget_root = _bundle_ref(root, manifest["budget_root"], label="budget_root")
    if budget_root.exists() and not budget_root.is_dir():
        raise ProspectiveError("budget_root must be a directory")

    preregistration_text, preregistration = _bundle_ref(
        root, manifest["preregistration_path"], label="preregistration_path"
    )
    if preregistration_text != "preregistration.json" or not preregistration.is_file():
        raise ProspectiveError("preregistration_path must name preregistration.json")
    expected_preregistration = _hash(
        manifest["preregistration_sha256"], field="preregistration_sha256"
    )
    if _sha256_file(preregistration) != expected_preregistration:
        raise ProspectiveError("preregistration checksum mismatch")
    preregistration_document = _read_json(
        preregistration, label="prospective preregistration", canonical=True
    )
    markdown_reference = (
        preregistration_document.get("preregistration_markdown")
        if isinstance(preregistration_document, dict)
        else None
    )
    if not isinstance(markdown_reference, dict) or set(markdown_reference) != {
        "path",
        "sha256",
    }:
        raise ProspectiveError("preregistration must bind PREREGISTRATION.md")
    markdown_text, markdown_path = _bundle_ref(
        root,
        markdown_reference["path"],
        label="preregistration markdown path",
    )
    if markdown_text != "PREREGISTRATION.md" or not markdown_path.is_file():
        raise ProspectiveError("preregistration markdown must name PREREGISTRATION.md")
    markdown_sha256 = _hash(
        markdown_reference["sha256"], field="preregistration markdown sha256"
    )
    if _sha256_file(markdown_path) != markdown_sha256:
        raise ProspectiveError("preregistration markdown checksum mismatch")

    repo_root = root.parents[2]
    source_text = _string(
        manifest["source_strategy_manifest"], field="source_strategy_manifest"
    )
    if source_text != _SOURCE_PATH:
        raise ProspectiveError(
            "source strategy manifest must be the committed direct sibling "
            "2026-08-17-paper-strategy-feasibility/manifest.json"
        )
    source_lexical = root / _SOURCE_PATH
    _assert_no_symlink(
        source_lexical, root.parent, label="source strategy manifest"
    )
    source = source_lexical.resolve()
    expected_source = root.parent / "2026-08-17-paper-strategy-feasibility/manifest.json"
    if source != expected_source.resolve() or not source.is_file():
        raise ProspectiveError(
            "source strategy manifest must be the committed direct sibling"
        )
    source_hash = _hash(
        manifest["source_strategy_manifest_sha256"],
        field="source_strategy_manifest_sha256",
    )
    if _sha256_file(source) != source_hash:
        raise ProspectiveError("source strategy manifest checksum mismatch")
    try:
        load_evaluation_manifest(source)
    except Exception as exc:
        raise ProspectiveError(f"invalid source strategy manifest: {exc}") from exc

    design_text = _string(manifest["design_path"], field="design_path")
    if design_text not in _DESIGN_PATHS:
        raise ProspectiveError("design_path must name a supported pinned pilot design")
    design = _external_ref(
        root,
        design_text,
        label="design_path",
        exact=design_text,
        expected=repo_root / design_text.removeprefix("../../../"),
    )
    design_hash = _hash(manifest["design_sha256"], field="design_sha256")
    if _sha256_file(design) != design_hash:
        raise ProspectiveError("prospective design checksum mismatch")
    contract = _external_ref(
        root,
        manifest["nansen_contract_path"],
        label="nansen_contract_path",
        exact=_CONTRACT_PATH,
        expected=repo_root / _CONTRACT_PATH.removeprefix("../../../"),
    )
    contract_hash = _hash(
        manifest["nansen_contract_sha256"], field="nansen_contract_sha256"
    )
    if _sha256_file(contract) != contract_hash:
        raise ProspectiveError("pinned Nansen contract checksum mismatch")

    seals = manifest["seals"]
    artifacts = manifest["artifacts"]
    if not isinstance(seals, list):
        raise ProspectiveError("seals must be an ordered list")
    if not isinstance(artifacts, list):
        raise ProspectiveError("artifacts must be a registry list")
    for index, reference in enumerate(seals):
        _keys(reference, _SEAL_REF_KEYS, label=f"seal reference {index}")
        if (
            not isinstance(reference["stage"], str)
            or reference["stage"] not in _SEAL_PATHS
        ):
            raise ProspectiveError(f"seal reference {index} has an invalid stage")
        _bundle_ref(root, reference["path"], label=f"seal reference {index} path")
        _hash(reference["sha256"], field=f"seal reference {index} sha256")
    for index, reference in enumerate(artifacts):
        _keys(reference, _ARTIFACT_REF_KEYS, label=f"artifact reference {index}")
        if (
            not isinstance(reference["stage"], str)
            or reference["stage"] not in _SEAL_PATHS
        ):
            raise ProspectiveError(f"artifact reference {index} has an invalid stage")
        _bundle_ref(root, reference["path"], label=f"artifact reference {index} path")
        _hash(reference["sha256"], field=f"artifact reference {index} sha256")
    return manifest


def _next_stage_is_valid(current: str, proposed: str) -> bool:
    return proposed in NEXT_STAGE[current]


def _require_stage_artifact_path(relative: str) -> None:
    if relative.startswith(("seals/", ".transactions/", "budget/")) or relative in {
        "manifest.json",
        "preregistration.json",
    }:
        raise ProspectiveError(f"artifact path is reserved: {relative}")


def _require_budget_snapshot_path(stage: str, relative: Any) -> str:
    expected = f"budget/snapshots/{stage}.json"
    if relative != expected:
        raise ProspectiveError(f"budget snapshot path must be {expected}")
    return expected


def _validate_seal(
    root: Path,
    reference: dict[str, Any],
    *,
    expected_previous: str | None,
    previous_time: datetime | None,
) -> tuple[dict[str, Any], datetime]:
    stage = reference["stage"]
    expected_path = _SEAL_PATHS[stage]
    if reference["path"] != expected_path:
        raise ProspectiveError(f"seal path for {stage} must be {expected_path}")
    _, path = _bundle_ref(root, reference["path"], label=f"{stage} seal path")
    if not path.is_file():
        raise ProspectiveError(f"manifest stage seal is absent: {path}")
    raw = path.read_bytes()
    if _sha256_bytes(raw) != reference["sha256"]:
        raise ProspectiveError(f"seal checksum mismatch for {stage}")
    seal = _keys(
        _read_json(path, label=f"{stage} seal", canonical=True),
        _SEAL_KEYS,
        label=f"{stage} seal",
    )
    if (
        seal["schema_version"] != 4
        or seal["experiment_id"] == ""
        or seal["stage"] != stage
    ):
        raise ProspectiveError(f"seal identity mismatch for {stage}")
    previous = _hash(
        seal["previous_seal_sha256"],
        field=f"{stage} previous seal sha256",
        allow_none=True,
    )
    if previous != expected_previous:
        raise ProspectiveError(f"seal hash chain is broken at {stage}")
    seal_time = _time(seal["recorded_at"], field=f"{stage} seal recorded_at")
    if previous_time is not None and seal_time < previous_time:
        raise ProspectiveError(f"seal time precedes prior seal at {stage}")
    _, snapshot = _bundle_ref(
        root, seal["budget_snapshot_path"], label=f"{stage} budget snapshot path"
    )
    _require_budget_snapshot_path(stage, seal["budget_snapshot_path"])
    snapshot_hash, journal_head = _validate_snapshot(
        snapshot, stage=stage, seal_time=seal_time
    )
    if snapshot_hash != seal["budget_snapshot_sha256"]:
        raise ProspectiveError(f"budget snapshot checksum mismatch for {stage}")
    _hash(
        seal["budget_snapshot_sha256"],
        field=f"{stage} budget snapshot sha256",
    )
    declared_head = _hash(
        seal["budget_journal_head_sha256"],
        field=f"{stage} budget journal head sha256",
        allow_none=True,
    )
    if declared_head != journal_head:
        raise ProspectiveError(f"budget journal head mismatch for {stage}")
    sealed_artifacts = seal["artifacts"]
    if not isinstance(sealed_artifacts, list):
        raise ProspectiveError(f"sealed artifacts must be a list for {stage}")
    for index, artifact in enumerate(sealed_artifacts):
        _keys(
            artifact,
            _SEALED_ARTIFACT_KEYS,
            label=f"{stage} sealed artifact {index}",
        )
    if sealed_artifacts != sorted(sealed_artifacts, key=lambda item: item["path"]):
        raise ProspectiveError(f"sealed artifacts must be sorted for {stage}")
    seen: set[str] = set()
    for index, artifact in enumerate(sealed_artifacts):
        relative, artifact_path = _bundle_ref(
            root,
            artifact["path"],
            label=f"{stage} sealed artifact {index} path",
        )
        _require_stage_artifact_path(relative)
        if relative in seen:
            raise ProspectiveError(f"duplicate sealed artifact path: {relative}")
        seen.add(relative)
        digest = _hash(
            artifact["sha256"],
            field=f"{stage} sealed artifact {index} sha256",
        )
        require_written_at = (
            PurePosixPath(relative).parts[0] != "raw"
            and artifact_path.suffix == ".json"
        )
        if _validate_artifact(
            artifact_path,
            seal_time=seal_time,
            require_written_at=require_written_at,
        ) != digest:
            raise ProspectiveError(f"sealed artifact checksum mismatch: {relative}")
    return seal, seal_time


def verify_hash_chain(bundle: ProspectiveBundle) -> None:
    root = Path(bundle.root).resolve()
    manifest = _validate_manifest_fields(root, bundle.manifest)
    seals = manifest["seals"]
    previous_hash: str | None = None
    previous_time: datetime | None = None
    current_stage = "preregistered"
    expected_registry: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for reference in seals:
        stage = reference["stage"]
        if not _next_stage_is_valid(current_stage, stage):
            raise ProspectiveError(
                f"seal transition is invalid: {current_stage} -> {stage}"
            )
        seal, previous_time = _validate_seal(
            root,
            reference,
            expected_previous=previous_hash,
            previous_time=previous_time,
        )
        if seal["experiment_id"] != manifest["experiment_id"]:
            raise ProspectiveError(f"seal experiment_id mismatch for {stage}")
        for artifact in seal["artifacts"]:
            if artifact["path"] in seen_paths:
                raise ProspectiveError(
                    f"artifact path is repurposed across seals: {artifact['path']}"
                )
            seen_paths.add(artifact["path"])
            expected_registry.append({"stage": stage, **artifact})
        previous_hash = reference["sha256"]
        current_stage = stage
    if manifest["stage"] != current_stage:
        raise ProspectiveError(
            f"manifest stage {manifest['stage']} has no matching terminal seal"
        )
    if manifest["artifacts"] != expected_registry:
        raise ProspectiveError("manifest artifact registry does not match its stage seals")


def load_prospective_manifest(path: str | Path) -> ProspectiveBundle:
    requested = Path(os.path.abspath(os.fspath(path)))
    trusted_experiments_root = requested.parent.parent.resolve()
    resolved = requested.resolve()
    if (
        requested.name != "manifest.json"
        or resolved.name != "manifest.json"
        or resolved.parent.parent != trusted_experiments_root
    ):
        raise ProspectiveError(
            "prospective manifest must remain in a direct bundle under the requested "
            f"trusted experiments root {trusted_experiments_root}"
        )
    if requested.is_symlink() or requested.parent.is_symlink():
        raise ProspectiveError("prospective manifest cannot traverse a symlink")
    manifest = _read_json(resolved, label="prospective manifest")
    root = resolved.parent.resolve()
    validated = _validate_manifest_fields(root, manifest)
    bundle = ProspectiveBundle(root=root, manifest_path=resolved, manifest=validated)
    verify_hash_chain(bundle)
    return bundle


@contextmanager
def _experiment_lock(root: Path) -> Iterator[None]:
    descriptor = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_transaction_marker(path: Path) -> None:
    path.unlink()
    _fsync_directory(path.parent)


def _build_transition(
    prior: dict[str, Any],
    *,
    stage: str,
    recorded_at: str,
    artifact_references: list[dict[str, str]],
    budget_snapshot_path: str,
    budget_snapshot_sha256: str,
    budget_journal_head_sha256: str | None,
) -> tuple[str, dict[str, Any], str, dict[str, Any]]:
    seal_path = _SEAL_PATHS[stage]
    previous_hash = None if not prior["seals"] else prior["seals"][-1]["sha256"]
    seal = {
        "schema_version": 4,
        "experiment_id": prior["experiment_id"],
        "stage": stage,
        "recorded_at": recorded_at,
        "previous_seal_sha256": previous_hash,
        "budget_snapshot_path": budget_snapshot_path,
        "budget_snapshot_sha256": budget_snapshot_sha256,
        "budget_journal_head_sha256": budget_journal_head_sha256,
        "artifacts": artifact_references,
    }
    seal_hash = _sha256_bytes(canonical_json_bytes(seal))
    proposed = {**prior}
    proposed["stage"] = stage
    proposed["seals"] = [
        *prior["seals"],
        {"stage": stage, "path": seal_path, "sha256": seal_hash},
    ]
    proposed["artifacts"] = [
        *prior["artifacts"],
        *({"stage": stage, **item} for item in artifact_references),
    ]
    return seal_path, seal, seal_hash, proposed


def _prepare_transition(
    bundle: ProspectiveBundle,
    *,
    stage: str,
    recorded_at: str,
    artifacts: tuple[Path, ...],
    budget_snapshot: Path,
) -> tuple[dict[str, Any], bytes, str, dict[str, Any], bytes, dict[str, Any]]:
    current = bundle.manifest["stage"]
    if (
        not isinstance(stage, str)
        or stage not in NEXT_STAGE
        or not _next_stage_is_valid(current, stage)
    ):
        raise ProspectiveError(f"invalid lifecycle transition: {current} -> {stage}")
    seal_time = _time(recorded_at, field="seal recorded_at")
    if bundle.manifest["seals"]:
        prior_ref = bundle.manifest["seals"][-1]
        _, prior_path = _bundle_ref(
            bundle.root, prior_ref["path"], label="prior seal path"
        )
        prior_seal = _read_json(prior_path, label="prior seal", canonical=True)
        if seal_time < _time(prior_seal["recorded_at"], field="prior seal recorded_at"):
            raise ProspectiveError("seal recorded_at is earlier than the prior seal")

    existing_paths = {item["path"] for item in bundle.manifest["artifacts"]}
    references: list[dict[str, str]] = []
    for index, artifact in enumerate(artifacts):
        relative, artifact_path = _provided_artifact_path(
            bundle.root, artifact, label=f"artifact {index} path"
        )
        if relative in existing_paths:
            raise ProspectiveError(f"artifact path is already sealed: {relative}")
        _require_stage_artifact_path(relative)
        require_written_at = (
            PurePosixPath(relative).parts[0] != "raw"
            and artifact_path.suffix == ".json"
        )
        references.append(
            {
                "path": relative,
                "sha256": _validate_artifact(
                    artifact_path,
                    seal_time=seal_time,
                    require_written_at=require_written_at,
                ),
            }
        )
    references.sort(key=lambda item: item["path"])
    if len({item["path"] for item in references}) != len(references):
        raise ProspectiveError("stage artifact paths must be unique")

    snapshot_relative, snapshot_path = _provided_artifact_path(
        bundle.root, budget_snapshot, label="budget snapshot path"
    )
    _require_budget_snapshot_path(stage, snapshot_relative)
    snapshot_hash, journal_head = _validate_snapshot(
        snapshot_path, stage=stage, seal_time=seal_time
    )
    seal_path, seal, seal_hash, proposed = _build_transition(
        bundle.manifest,
        stage=stage,
        recorded_at=recorded_at,
        artifact_references=references,
        budget_snapshot_path=snapshot_relative,
        budget_snapshot_sha256=snapshot_hash,
        budget_journal_head_sha256=journal_head,
    )
    seal_bytes = canonical_json_bytes(seal)
    proposed_bytes = canonical_json_bytes(proposed)
    marker = {
        "schema_version": 1,
        "experiment_id": bundle.experiment_id,
        "prior_manifest_sha256": _sha256_file(bundle.manifest_path),
        "prior_manifest": bundle.manifest,
        "proposed_stage": stage,
        "recorded_at": recorded_at,
        "seal_path": seal_path,
        "seal_sha256": seal_hash,
        "seal": seal,
        "artifacts": references,
        "budget_snapshot_path": snapshot_relative,
        "budget_snapshot_sha256": snapshot_hash,
        "proposed_manifest_sha256": _sha256_bytes(proposed_bytes),
        "proposed_manifest": proposed,
    }
    return marker, seal_bytes, seal_path, proposed, proposed_bytes, seal


def commit_stage(
    bundle: ProspectiveBundle,
    stage: str,
    recorded_at: str,
    artifacts: tuple[Path, ...],
    budget_snapshot: Path,
) -> ProspectiveBundle:
    root = Path(bundle.root).resolve()
    marker_path = root / ".transactions/stage.json"
    with _experiment_lock(root):
        if marker_path.exists() or marker_path.is_symlink():
            raise ProspectiveError("an unfinished stage transaction requires recovery")
        current = load_prospective_manifest(bundle.manifest_path)
        if current.manifest != bundle.manifest:
            raise ProspectiveError("prospective bundle is stale; manifest changed")
        marker, seal_bytes, seal_relative, _, proposed_bytes, _ = _prepare_transition(
            current,
            stage=stage,
            recorded_at=recorded_at,
            artifacts=artifacts,
            budget_snapshot=budget_snapshot,
        )
        seal_path = root / seal_relative
        if seal_path.exists() or seal_path.is_symlink():
            raise ProspectiveError(f"unrecorded seal already exists: {seal_relative}")
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_replace_bytes(marker_path, canonical_json_bytes(marker))
        write_json_once(seal_path, marker["seal"])
        atomic_replace_bytes(current.manifest_path, proposed_bytes)
        _remove_transaction_marker(marker_path)
        return load_prospective_manifest(current.manifest_path)


def _validate_marker(root: Path, marker_path: Path) -> dict[str, Any]:
    marker = _keys(
        _read_json(marker_path, label="stage transaction", canonical=True),
        _MARKER_KEYS,
        label="stage transaction",
    )
    if marker["schema_version"] != 1:
        raise ProspectiveError("stage transaction schema is corrupt")
    _string(marker["experiment_id"], field="stage transaction experiment_id")
    _hash(marker["prior_manifest_sha256"], field="transaction prior manifest hash")
    _hash(marker["seal_sha256"], field="transaction seal hash")
    _hash(
        marker["budget_snapshot_sha256"], field="transaction budget snapshot hash"
    )
    _hash(
        marker["proposed_manifest_sha256"], field="transaction proposed manifest hash"
    )
    if (
        not isinstance(marker["proposed_stage"], str)
        or marker["proposed_stage"] not in _SEAL_PATHS
    ):
        raise ProspectiveError("stage transaction proposed stage is corrupt")
    _time(marker["recorded_at"], field="stage transaction recorded_at")
    if marker["seal_path"] != _SEAL_PATHS[marker["proposed_stage"]]:
        raise ProspectiveError("stage transaction seal path is corrupt")
    _require_budget_snapshot_path(
        marker["proposed_stage"], marker["budget_snapshot_path"]
    )
    _validate_manifest_fields(root, marker["prior_manifest"])
    verify_hash_chain(
        ProspectiveBundle(
            root=root,
            manifest_path=root / "manifest.json",
            manifest=marker["prior_manifest"],
        )
    )
    if not _next_stage_is_valid(
        marker["prior_manifest"]["stage"], marker["proposed_stage"]
    ):
        raise ProspectiveError("stage transaction lifecycle transition is corrupt")
    if not isinstance(marker["artifacts"], list):
        raise ProspectiveError("stage transaction artifacts are corrupt")
    for index, artifact in enumerate(marker["artifacts"]):
        _keys(artifact, _SEALED_ARTIFACT_KEYS, label=f"transaction artifact {index}")
        _hash(artifact["sha256"], field=f"transaction artifact {index} sha256")
        relative, _ = _bundle_ref(
            root, artifact["path"], label=f"transaction artifact {index} path"
        )
        _require_stage_artifact_path(relative)
    seal_path, expected_seal, seal_hash, expected_manifest = _build_transition(
        marker["prior_manifest"],
        stage=marker["proposed_stage"],
        recorded_at=marker["recorded_at"],
        artifact_references=marker["artifacts"],
        budget_snapshot_path=marker["budget_snapshot_path"],
        budget_snapshot_sha256=marker["budget_snapshot_sha256"],
        budget_journal_head_sha256=marker["seal"].get(
            "budget_journal_head_sha256"
        )
        if isinstance(marker["seal"], dict)
        else None,
    )
    if (
        marker["seal"] != expected_seal
        or marker["seal_path"] != seal_path
        or marker["seal_sha256"] != seal_hash
        or marker["proposed_manifest"] != expected_manifest
        or marker["proposed_manifest_sha256"]
        != _sha256_bytes(canonical_json_bytes(expected_manifest))
    ):
        raise ProspectiveError("stage transaction proposed seal or manifest is corrupt")
    if marker["seal"].get("artifacts") != marker["artifacts"]:
        raise ProspectiveError("stage transaction artifact list is corrupt")

    seal_time = _time(marker["recorded_at"], field="stage transaction recorded_at")
    for artifact in marker["artifacts"]:
        _, artifact_path = _bundle_ref(
            root, artifact["path"], label="transaction artifact path"
        )
        relative = artifact["path"]
        require_written_at = (
            PurePosixPath(relative).parts[0] != "raw"
            and artifact_path.suffix == ".json"
        )
        if _validate_artifact(
            artifact_path,
            seal_time=seal_time,
            require_written_at=require_written_at,
        ) != artifact["sha256"]:
            raise ProspectiveError("stage transaction artifact checksum changed")
    _, snapshot = _bundle_ref(
        root, marker["budget_snapshot_path"], label="transaction budget snapshot path"
    )
    snapshot_hash, journal_head = _validate_snapshot(
        snapshot, stage=marker["proposed_stage"], seal_time=seal_time
    )
    if snapshot_hash != marker["budget_snapshot_sha256"]:
        raise ProspectiveError("stage transaction budget snapshot checksum changed")
    if journal_head != marker["seal"]["budget_journal_head_sha256"]:
        raise ProspectiveError("stage transaction budget journal head changed")
    return marker


def recover_stage_transaction(bundle: ProspectiveBundle) -> ProspectiveBundle:
    root = Path(bundle.root).resolve()
    marker_path = root / ".transactions/stage.json"
    with _experiment_lock(root):
        if marker_path.is_symlink():
            raise ProspectiveError("stage transaction marker cannot be a symlink")
        if not marker_path.exists():
            return load_prospective_manifest(bundle.manifest_path)
        marker = _validate_marker(root, marker_path)
        if marker["experiment_id"] != bundle.experiment_id:
            raise ProspectiveError("stage transaction experiment identity is corrupt")
        manifest_raw = bundle.manifest_path.read_bytes()
        manifest_hash = _sha256_bytes(manifest_raw)
        _, seal_path = _bundle_ref(
            root,
            marker["seal_path"],
            label="stage transaction seal path",
        )
        seal_exists = seal_path.exists() or seal_path.is_symlink()
        seal_exact = seal_path.is_file() and _sha256_file(seal_path) == marker["seal_sha256"]
        is_prior = manifest_hash == marker["prior_manifest_sha256"]
        is_proposed = manifest_hash == marker["proposed_manifest_sha256"]

        if is_prior and not seal_exists:
            if json.loads(manifest_raw) != marker["prior_manifest"]:
                raise ProspectiveError("stage transaction prior manifest changed")
            write_json_once(seal_path, marker["seal"])
            atomic_replace_bytes(
                bundle.manifest_path,
                canonical_json_bytes(marker["proposed_manifest"]),
            )
        elif is_prior and seal_exact:
            if json.loads(manifest_raw) != marker["prior_manifest"]:
                raise ProspectiveError("stage transaction prior manifest changed")
            atomic_replace_bytes(
                bundle.manifest_path,
                canonical_json_bytes(marker["proposed_manifest"]),
            )
        elif is_proposed and seal_exact:
            if manifest_raw != canonical_json_bytes(marker["proposed_manifest"]):
                raise ProspectiveError("stage transaction proposed manifest changed")
        else:
            if seal_exists and not seal_exact:
                raise ProspectiveError("stage transaction seal checksum is corrupt")
            raise ProspectiveError("stage transaction state is corrupt or changed")
        recovered = load_prospective_manifest(bundle.manifest_path)
        _remove_transaction_marker(marker_path)
        return recovered
