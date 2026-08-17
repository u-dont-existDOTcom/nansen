from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .artifacts import (
    canonical_json_bytes,
    write_bytes_once_or_adopt_exact,
)
from .openai_client import (
    PROSPECTIVE_MODEL_ID,
    OpenAIEvidenceResponse,
    OpenAIError,
    structured_request_body,
)


def _string_schema(*, max_length: int = 2000) -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "maxLength": max_length}


def _string_list_schema(*, maximum: int = 12) -> dict[str, Any]:
    return {
        "type": "array",
        "items": _string_schema(max_length=240),
        "maxItems": maximum,
        "uniqueItems": True,
    }


RISK_FLAGS = (
    "LOW_LIQUIDITY",
    "INCOMPLETE_DATA",
    "STALE_DATA",
    "FLOW_CONCENTRATION",
    "EXCHANGE_PRESSURE",
    "HIGH_VOLATILITY",
    "EXECUTION_RISK",
    "MODEL_UNCERTAINTY",
)


PASS1_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action",
        "confidence",
        "expected_direction_4h",
        "evidence_for",
        "evidence_against",
        "missing_evidence",
        "rationale",
        "risk_flags",
    ],
    "properties": {
        "action": {"type": "string", "enum": ["LONG", "ABSTAIN"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "expected_direction_4h": {"type": "string", "enum": ["UP", "FLAT", "DOWN"]},
        "evidence_for": _string_list_schema(),
        "evidence_against": _string_list_schema(),
        "missing_evidence": _string_list_schema(),
        "rationale": _string_schema(),
        "risk_flags": {
            "type": "array",
            "items": {"type": "string", "enum": list(RISK_FLAGS)},
            "maxItems": len(RISK_FLAGS),
            "uniqueItems": True,
        },
    },
}


_THEORY_ASSESSMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["theory_id", "applicability", "predicate_alignment", "rationale"],
    "properties": {
        "theory_id": _string_schema(max_length=200),
        "applicability": {
            "type": "string",
            "enum": ["APPLICABLE", "NOT_APPLICABLE", "UNAVAILABLE"],
        },
        "predicate_alignment": {
            "type": "string",
            "enum": [
                "SUPPORTS_LONG",
                "SUPPORTS_ABSTAIN",
                "CONFLICTS",
                "INDETERMINATE",
            ],
        },
        "rationale": _string_schema(),
    },
}


PASS2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "snapshot_sha256",
        "pass1",
        "pass1_assessment",
        "final_action",
        "theory_assessments",
        "conflicts",
        "evidence_for",
        "evidence_against",
        "missing_evidence",
        "rationale",
    ],
    "properties": {
        "snapshot_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "pass1": {
            "type": "object",
            "additionalProperties": False,
            "required": ["response_sha256"],
            "properties": {
                "response_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
        },
        "pass1_assessment": {"type": "string", "enum": ["UPHOLD", "OVERRULE"]},
        "final_action": {"type": "string", "enum": ["LONG", "ABSTAIN"]},
        "theory_assessments": {
            "type": "array",
            "items": _THEORY_ASSESSMENT_SCHEMA,
            "minItems": 6,
            "maxItems": 6,
        },
        "conflicts": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["theory_id", "description"],
                "properties": {
                    "theory_id": _string_schema(max_length=200),
                    "description": _string_schema(),
                },
            },
        },
        "evidence_for": _string_list_schema(),
        "evidence_against": _string_list_schema(),
        "missing_evidence": _string_list_schema(),
        "rationale": _string_schema(),
    },
}


PASS1_INSTRUCTIONS = """You are an independent prospective market analyst. Analyze only the supplied identity-blinded, point-in-time snapshot. Choose LONG or ABSTAIN for a fixed four-hour paper objective. Cite only field paths that exist in the supplied snapshot. Do not infer token identity, use tools, request outside data, or discuss future outcomes. Return only the required structured object."""


PASS2_INSTRUCTIONS = """You are a prospective strategy critic. Review the immutable Pass 1 decision against exactly the supplied frozen strategy records and the exact same identity-blinded snapshot. Do not modify Pass 1, infer token identity, use tools, request outside data, or discuss future outcomes. Return only the required structured object."""


class GPTProtocolError(RuntimeError):
    """Raised when sealed model evidence cannot safely produce a decision."""


@dataclass(frozen=True)
class GPTPassResult:
    value: dict[str, Any]
    snapshot_sha256: str
    request_path: Path
    request_sha256: str
    response_path: Path
    response_sha256: str
    response_id: str
    returned_model_id: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise GPTProtocolError("protocol clock must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _reject_non_finite(constant: str) -> None:
    raise ValueError(f"non-finite JSON constant: {constant}")


def _json_object(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_non_finite)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise GPTProtocolError(f"{label} is not valid finite JSON") from exc
    if not isinstance(value, dict):
        raise GPTProtocolError(f"{label} must be a JSON object")
    return value


def _regular_file(path: Path, *, label: str) -> bool:
    if path.is_symlink():
        raise GPTProtocolError(f"{label} must not be a symlink")
    if not path.exists():
        return False
    if not path.is_file():
        raise GPTProtocolError(f"{label} must be a regular file")
    return True


class GPTArtifactWriter:
    """Write-once model evidence rooted in one prospective experiment bundle."""

    def __init__(
        self,
        root: str | Path,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.root = Path(root)
        self.now = now

    def request_path(self, scope: str, attempt: int) -> Path:
        return self.root / "model" / scope / f"attempt-{attempt}-request.json"

    def response_path(self, scope: str, attempt: int) -> Path:
        return self.root / "model" / scope / f"attempt-{attempt}-response.json"

    def response_metadata_path(self, scope: str, attempt: int) -> Path:
        return self.root / "model" / scope / f"attempt-{attempt}-response-metadata.json"

    def final_path(self, scope: str) -> Path:
        return self.root / "model" / scope / "final.json"

    @staticmethod
    def _install(path: Path, content: bytes, *, kind: str) -> Path:
        try:
            return write_bytes_once_or_adopt_exact(
                path,
                content,
                metadata={"kind": kind, "received_sha256": _sha256(content)},
            )
        except (FileExistsError, RuntimeError) as exc:
            raise GPTProtocolError(str(exc)) from exc

    def install_request(self, scope: str, attempt: int, value: dict[str, Any]) -> Path:
        return self._install(
            self.request_path(scope, attempt),
            canonical_json_bytes(value),
            kind="openai_request",
        )

    def install_response(
        self,
        scope: str,
        attempt: int,
        response: OpenAIEvidenceResponse,
    ) -> Path:
        path = self._install(
            self.response_path(scope, attempt),
            response.raw_body,
            kind="openai_response",
        )
        artifact_written_at = _utc_text(self.now())
        metadata = {
            "schema_version": 1,
            "attempt": attempt,
            "status_code": response.status_code,
            "request_started_at": response.request_started_at,
            "response_retrieved_at": response.response_retrieved_at,
            "provider_created_at": response.provider_created_at,
            "artifact_written_at": artifact_written_at,
            "response_headers": dict(response.response_headers),
            "response_id": response.response_id,
            "returned_model_id": response.returned_model_id,
            "usage": dict(response.usage),
            "response_file": path.name,
            "response_sha256": _sha256(response.raw_body),
        }
        self._install(
            self.response_metadata_path(scope, attempt),
            canonical_json_bytes(metadata),
            kind="openai_response_metadata",
        )
        return path

    def install_final(self, scope: str, value: dict[str, Any]) -> Path:
        return self._install(
            self.final_path(scope),
            canonical_json_bytes(value),
            kind="openai_final_pointer",
        )


def _load_snapshot(path: str | Path) -> tuple[Path, bytes, dict[str, Any], str]:
    snapshot_path = Path(path)
    if not _regular_file(snapshot_path, label="normalized snapshot"):
        raise GPTProtocolError("normalized snapshot is missing")
    raw = snapshot_path.read_bytes()
    value = _json_object(raw, label="normalized snapshot")
    _assert_no_forbidden_input(value)
    return snapshot_path, raw, value, _sha256(raw)


_FORBIDDEN_KEYS = frozenset({
    "token_address",
    "token_symbol",
    "address",
    "name",
    "url",
    "social_url",
    "selection_status",
    "forward_return",
    "forward_returns",
    "mfe",
    "mae",
    "prior_return",
    "prior_results",
})


def _assert_no_forbidden_input(value: Any, *, path: str = "input") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in _FORBIDDEN_KEYS or normalized.startswith("forward_"):
                raise GPTProtocolError(f"forbidden identity/outcome key at {path}.{key}")
            _assert_no_forbidden_input(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_forbidden_input(child, path=f"{path}[{index}]")
    elif isinstance(value, float) and not math.isfinite(value):
        raise GPTProtocolError(f"non-finite model input at {path}")


_PRIOR_RESULT_KEY_MARKERS = (
    "return",
    "performance",
    "feasibility",
    "verdict",
    "selected",
    "win_rate",
    "event_count",
    "sample_size",
    "profit",
    "pnl",
)


def _assert_no_prior_results(value: Any, *, path: str = "theory_records") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if any(marker in normalized for marker in _PRIOR_RESULT_KEY_MARKERS):
                raise GPTProtocolError(f"prior feasibility result key is forbidden at {path}.{key}")
            _assert_no_prior_results(child, path=f"{path}.{key}")
    elif isinstance(value, (tuple, list)):
        for index, child in enumerate(value):
            _assert_no_prior_results(child, path=f"{path}[{index}]")


def _field_exists(snapshot: Any, reference: str) -> bool:
    if not isinstance(reference, str) or not reference:
        return False
    if reference.startswith("/"):
        parts = [part.replace("~1", "/").replace("~0", "~") for part in reference[1:].split("/")]
    else:
        parts = reference.split(".")
    current = snapshot
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return False
    return True


def _exact_keys(value: Any, expected: Iterable[str], *, label: str, errors: list[str]) -> bool:
    expected_set = set(expected)
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return False
    actual = set(value)
    if actual != expected_set:
        errors.append(
            f"{label} keys must be exactly {sorted(expected_set)}; got {sorted(actual)}"
        )
        return False
    return True


def _bounded_text(value: Any, *, label: str, errors: list[str], maximum: int = 2000) -> None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        errors.append(f"{label} must be a non-empty string of at most {maximum} characters")


def _unique_strings(
    value: Any,
    *,
    label: str,
    errors: list[str],
    maximum: int = 12,
    allowed: set[str] | None = None,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        errors.append(f"{label} must be a list with at most {maximum} items")
        return []
    strings: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or len(item) > 240:
            errors.append(f"{label} entries must be non-empty bounded strings")
            continue
        if allowed is not None and item not in allowed:
            errors.append(f"{label} contains invalid value {item!r}")
        strings.append(item)
    if len(strings) != len(set(strings)):
        errors.append(f"{label} must not contain duplicates")
    return strings


def _validate_evidence_lists(value: dict[str, Any], snapshot: dict[str, Any], errors: list[str]) -> None:
    evidence_for = _unique_strings(value.get("evidence_for"), label="evidence_for", errors=errors)
    evidence_against = _unique_strings(
        value.get("evidence_against"), label="evidence_against", errors=errors
    )
    if len(evidence_for + evidence_against) != len(set(evidence_for + evidence_against)):
        errors.append("evidence references must be unique across for and against lists")
    for reference in evidence_for + evidence_against:
        if not _field_exists(snapshot, reference):
            errors.append(f"evidence reference does not exist in snapshot: {reference}")
    _unique_strings(value.get("missing_evidence"), label="missing_evidence", errors=errors)


def _validate_pass1(value: Any, snapshot: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    keys = PASS1_SCHEMA["required"]
    if not _exact_keys(value, keys, label="Pass 1", errors=errors):
        return errors
    assert isinstance(value, dict)
    if value["action"] not in {"LONG", "ABSTAIN"}:
        errors.append("action must be LONG or ABSTAIN")
    confidence = value["confidence"]
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not 0 <= float(confidence) <= 1
    ):
        errors.append("confidence must be a finite number in [0, 1]")
    if value["expected_direction_4h"] not in {"UP", "FLAT", "DOWN"}:
        errors.append("expected_direction_4h must be UP, FLAT, or DOWN")
    _validate_evidence_lists(value, snapshot, errors)
    _bounded_text(value["rationale"], label="rationale", errors=errors)
    _unique_strings(
        value["risk_flags"],
        label="risk_flags",
        errors=errors,
        maximum=len(RISK_FLAGS),
        allowed=set(RISK_FLAGS),
    )
    return errors


def _theory_ids(theory_records: Any) -> tuple[str, ...]:
    if not isinstance(theory_records, (tuple, list)) or len(theory_records) != 6:
        raise GPTProtocolError("Pass 2 requires exactly six frozen theory records")
    identifiers: list[str] = []
    for record in theory_records:
        if not isinstance(record, dict):
            raise GPTProtocolError("frozen theory records must be objects")
        identifier = record.get("id", record.get("theory_id"))
        if not isinstance(identifier, str) or not identifier:
            raise GPTProtocolError("every frozen theory record must have an ID")
        identifiers.append(identifier)
    if len(set(identifiers)) != 6:
        raise GPTProtocolError("frozen theory record IDs must be unique")
    return tuple(identifiers)


def _validate_pass2(
    value: Any,
    snapshot: dict[str, Any],
    *,
    snapshot_sha256: str,
    pass1_response_sha256: str,
    theory_ids: tuple[str, ...],
) -> list[str]:
    errors: list[str] = []
    if not _exact_keys(value, PASS2_SCHEMA["required"], label="Pass 2", errors=errors):
        return errors
    assert isinstance(value, dict)
    if value["snapshot_sha256"] != snapshot_sha256:
        errors.append("snapshot_sha256 does not match the exact sealed snapshot")
    pass1 = value["pass1"]
    if _exact_keys(pass1, ("response_sha256",), label="pass1", errors=errors):
        if pass1["response_sha256"] != pass1_response_sha256:
            errors.append("pass1.response_sha256 does not match the exact archived response")
    if value["pass1_assessment"] not in {"UPHOLD", "OVERRULE"}:
        errors.append("pass1_assessment must be UPHOLD or OVERRULE")
    if value["final_action"] not in {"LONG", "ABSTAIN"}:
        errors.append("final_action must be LONG or ABSTAIN")

    assessments = value["theory_assessments"]
    seen: list[str] = []
    if not isinstance(assessments, list) or len(assessments) != 6:
        errors.append("theory_assessments must contain exactly six records")
    else:
        for index, assessment in enumerate(assessments):
            label = f"theory_assessments[{index}]"
            if not _exact_keys(
                assessment,
                ("theory_id", "applicability", "predicate_alignment", "rationale"),
                label=label,
                errors=errors,
            ):
                continue
            identifier = assessment["theory_id"]
            if not isinstance(identifier, str):
                errors.append(f"{label}.theory_id must be a string")
            else:
                seen.append(identifier)
            if assessment["applicability"] not in {
                "APPLICABLE", "NOT_APPLICABLE", "UNAVAILABLE",
            }:
                errors.append(f"{label}.applicability is invalid")
            if assessment["predicate_alignment"] not in {
                "SUPPORTS_LONG", "SUPPORTS_ABSTAIN", "CONFLICTS", "INDETERMINATE",
            }:
                errors.append(f"{label}.predicate_alignment is invalid")
            _bounded_text(assessment["rationale"], label=f"{label}.rationale", errors=errors)
        if len(seen) != len(set(seen)) or set(seen) != set(theory_ids):
            errors.append("theory_assessments must cover each frozen record exactly once")

    conflicts = value["conflicts"]
    if not isinstance(conflicts, list) or len(conflicts) > 12:
        errors.append("conflicts must be a list with at most 12 items")
    else:
        conflict_ids: list[str] = []
        for index, conflict in enumerate(conflicts):
            label = f"conflicts[{index}]"
            if not _exact_keys(
                conflict, ("theory_id", "description"), label=label, errors=errors
            ):
                continue
            identifier = conflict["theory_id"]
            if identifier not in theory_ids:
                errors.append(f"{label}.theory_id is not a frozen record")
            elif identifier in conflict_ids:
                errors.append("conflicts must not repeat a theory ID")
            conflict_ids.append(identifier)
            _bounded_text(conflict["description"], label=f"{label}.description", errors=errors)
    _validate_evidence_lists(value, snapshot, errors)
    _bounded_text(value["rationale"], label="rationale", errors=errors)
    return errors


def _request_document(
    *,
    scope: str,
    attempt: int,
    request_body: dict[str, Any],
    snapshot_sha256: str,
    writer: GPTArtifactWriter,
) -> dict[str, Any]:
    # Reject non-finite request content before canonical installation.
    try:
        json.dumps(request_body, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise GPTProtocolError("OpenAI request contains non-finite or non-JSON input") from exc
    instructions = request_body["instructions"]
    schema = request_body["text"]["format"]["schema"]
    request_started_at = _utc_text(writer.now())
    artifact_written_at = _utc_text(writer.now())
    return {
        "schema_version": 1,
        "scope": scope,
        "attempt": attempt,
        "method": "POST",
        "path": "/v1/responses",
        "transmission_may_begin": True,
        "request_started_at": request_started_at,
        "artifact_written_at": artifact_written_at,
        "requested_model_id": PROSPECTIVE_MODEL_ID,
        "reasoning": {"effort": "high"},
        "snapshot_sha256": snapshot_sha256,
        "prompt_sha256": _sha256(instructions.encode("utf-8")),
        "schema_sha256": _sha256(canonical_json_bytes(schema)),
        "input_sha256": _sha256(canonical_json_bytes(request_body["input"])),
        "request_body": request_body,
    }


def _load_response(writer: GPTArtifactWriter, scope: str, attempt: int) -> OpenAIEvidenceResponse:
    response_path = writer.response_path(scope, attempt)
    metadata_path = writer.response_metadata_path(scope, attempt)
    if not _regular_file(response_path, label="OpenAI response artifact"):
        raise GPTProtocolError("OpenAI response artifact is missing")
    if not _regular_file(metadata_path, label="OpenAI response metadata"):
        raise GPTProtocolError("OpenAI response metadata is missing")
    metadata = _json_object(metadata_path.read_bytes(), label="OpenAI response metadata")
    raw = response_path.read_bytes()
    expected_keys = {
        "schema_version", "attempt", "status_code", "request_started_at",
        "response_retrieved_at", "provider_created_at", "artifact_written_at",
        "response_headers", "response_id", "returned_model_id", "usage",
        "response_file", "response_sha256",
    }
    if set(metadata) != expected_keys or metadata.get("schema_version") != 1:
        raise GPTProtocolError("OpenAI response metadata has an invalid shape")
    if metadata.get("attempt") != attempt or metadata.get("response_file") != response_path.name:
        raise GPTProtocolError("OpenAI response metadata identity mismatch")
    status_code = metadata.get("status_code")
    headers = metadata.get("response_headers")
    if (
        isinstance(status_code, bool)
        or not isinstance(status_code, int)
        or not isinstance(metadata.get("request_started_at"), str)
        or not isinstance(metadata.get("response_retrieved_at"), str)
        or (
            metadata.get("provider_created_at") is not None
            and not isinstance(metadata.get("provider_created_at"), str)
        )
        or not isinstance(metadata.get("artifact_written_at"), str)
        or not isinstance(headers, dict)
        or any(not isinstance(key, str) or not isinstance(value, str) for key, value in headers.items())
    ):
        raise GPTProtocolError("OpenAI response metadata types are invalid")
    if metadata.get("response_sha256") != _sha256(raw):
        raise GPTProtocolError("OpenAI response artifact hash mismatch")
    response = OpenAIEvidenceResponse.from_raw(
        raw_body=raw,
        status_code=status_code,
        request_started_at=metadata["request_started_at"],
        response_retrieved_at=metadata["response_retrieved_at"],
        response_headers=headers,
    )
    if (
        metadata.get("response_id") != response.response_id
        or metadata.get("returned_model_id") != response.returned_model_id
        or metadata.get("provider_created_at") != response.provider_created_at
        or metadata.get("usage") != dict(response.usage)
    ):
        raise GPTProtocolError("OpenAI response metadata does not match exact response bytes")
    return response


def _obtain_response(
    *,
    client: Any,
    writer: GPTArtifactWriter,
    scope: str,
    attempt: int,
    request_document: dict[str, Any],
    instructions: str,
    input_json: dict[str, Any],
    schema_name: str,
    schema: dict[str, Any],
) -> tuple[OpenAIEvidenceResponse, Path]:
    request_path = writer.request_path(scope, attempt)
    response_path = writer.response_path(scope, attempt)
    request_exists = _regular_file(request_path, label="OpenAI request artifact")
    response_exists = _regular_file(response_path, label="OpenAI response artifact")
    if response_exists and not request_exists:
        raise GPTProtocolError("OpenAI response exists without its request artifact")
    if request_exists:
        if not response_exists:
            raise GPTProtocolError("ambiguous OpenAI request has no response; refusing reroll")
        archived_raw = request_path.read_bytes()
        archived = _json_object(archived_raw, label="OpenAI request artifact")
        if archived_raw != canonical_json_bytes(archived):
            raise GPTProtocolError("archived OpenAI request is not canonical")
        archived_started_at = archived.pop("request_started_at", None)
        archived_written_at = archived.pop("artifact_written_at", None)
        expected = dict(request_document)
        expected.pop("request_started_at", None)
        expected.pop("artifact_written_at", None)
        if (
            not isinstance(archived_started_at, str)
            or not isinstance(archived_written_at, str)
            or archived != expected
        ):
            raise GPTProtocolError("archived OpenAI request does not match the requested attempt")
        return _load_response(writer, scope, attempt), request_path

    request_path = writer.install_request(scope, attempt, request_document)
    try:
        response = client.create_structured(
            model_id=PROSPECTIVE_MODEL_ID,
            instructions=instructions,
            input_json=input_json,
            schema_name=schema_name,
            schema=schema,
        )
    except OpenAIError as exc:
        if exc.response is not None:
            writer.install_response(scope, attempt, exc.response)
        classification = "after transmission" if exc.transmitted else "before transmission"
        raise GPTProtocolError(f"OpenAI request failed {classification}: {exc}") from exc
    response_path = writer.install_response(scope, attempt, response)
    return response, request_path


def _parsed_model_output(response: OpenAIEvidenceResponse) -> dict[str, Any]:
    if response.returned_model_id != PROSPECTIVE_MODEL_ID:
        raise GPTProtocolError("OpenAI response model mismatch")
    if response.body is None or response.body.get("status") != "completed":
        raise GPTProtocolError("OpenAI response is not completed")
    if response.refusal is not None:
        raise GPTProtocolError("OpenAI structured-output refusal")
    if response.output_text is None:
        raise GPTProtocolError("OpenAI structured output is absent")
    try:
        value = json.loads(response.output_text, parse_constant=_reject_non_finite)
    except (json.JSONDecodeError, ValueError) as exc:
        raise GPTProtocolError("OpenAI structured output is malformed JSON") from exc
    if not isinstance(value, dict):
        raise GPTProtocolError("OpenAI structured output must be an object")
    return value


def _result_from_response(
    *,
    value: dict[str, Any],
    snapshot_sha256: str,
    request_path: Path,
    response_path: Path,
    response: OpenAIEvidenceResponse,
) -> GPTPassResult:
    if not isinstance(response.response_id, str) or not response.response_id:
        raise GPTProtocolError("OpenAI response ID is absent")
    if response.returned_model_id != PROSPECTIVE_MODEL_ID:
        raise GPTProtocolError("OpenAI response model mismatch")
    return GPTPassResult(
        value=value,
        snapshot_sha256=snapshot_sha256,
        request_path=request_path,
        request_sha256=_sha256(request_path.read_bytes()),
        response_path=response_path,
        response_sha256=_sha256(response_path.read_bytes()),
        response_id=response.response_id,
        returned_model_id=response.returned_model_id,
    )


def _run_pass(
    *,
    client: Any,
    writer: GPTArtifactWriter,
    scope: str,
    schema_name: str,
    schema: dict[str, Any],
    instructions: str,
    original_input: dict[str, Any],
    snapshot_sha256: str,
    validate: Callable[[dict[str, Any]], list[str]],
) -> GPTPassResult:
    final_path = writer.final_path(scope)
    if _regular_file(final_path, label="OpenAI final pointer"):
        final = _json_object(final_path.read_bytes(), label="OpenAI final pointer")
        expected_final_keys = {
            "schema_version", "scope", "attempt", "snapshot_sha256",
            "request_file", "request_sha256", "response_file", "response_sha256",
            "response_id", "requested_model_id", "returned_model_id", "validated_at",
            "artifact_written_at",
        }
        if set(final) != expected_final_keys or final.get("schema_version") != 1:
            raise GPTProtocolError("OpenAI final pointer has an invalid shape")
        if final.get("scope") != scope:
            raise GPTProtocolError("OpenAI final pointer scope mismatch")
        if final.get("snapshot_sha256") != snapshot_sha256:
            raise GPTProtocolError("final pointer snapshot hash mismatch")
        attempt = final.get("attempt")
        if attempt not in (1, 2):
            raise GPTProtocolError("final pointer attempt is invalid")
        response = _load_response(writer, scope, attempt)
        request_path = writer.request_path(scope, attempt)
        response_path = writer.response_path(scope, attempt)
        if not _regular_file(request_path, label="OpenAI request artifact"):
            raise GPTProtocolError("final pointer request is missing")
        if final.get("request_file") != request_path.name:
            raise GPTProtocolError("final pointer request filename mismatch")
        if final.get("response_file") != response_path.name:
            raise GPTProtocolError("final pointer response filename mismatch")
        if final.get("request_sha256") != _sha256(request_path.read_bytes()):
            raise GPTProtocolError("final pointer request hash mismatch")
        if final.get("response_sha256") != _sha256(response_path.read_bytes()):
            raise GPTProtocolError("final pointer response hash mismatch")
        value = _parsed_model_output(response)
        if (
            final.get("response_id") != response.response_id
            or final.get("requested_model_id") != PROSPECTIVE_MODEL_ID
            or final.get("returned_model_id") != response.returned_model_id
        ):
            raise GPTProtocolError("OpenAI final pointer response identity mismatch")
        errors = validate(value)
        if errors:
            raise GPTProtocolError("final pointer references invalid structured output")
        return _result_from_response(
            value=value,
            snapshot_sha256=snapshot_sha256,
            request_path=request_path,
            response_path=response_path,
            response=response,
        )

    input_json = original_input
    for attempt in (1, 2):
        request_body = structured_request_body(
            model_id=PROSPECTIVE_MODEL_ID,
            instructions=instructions,
            input_json=input_json,
            schema_name=schema_name,
            schema=schema,
        )
        request_document = _request_document(
            scope=scope,
            attempt=attempt,
            request_body=request_body,
            snapshot_sha256=snapshot_sha256,
            writer=writer,
        )
        response, request_path = _obtain_response(
            client=client,
            writer=writer,
            scope=scope,
            attempt=attempt,
            request_document=request_document,
            instructions=instructions,
            input_json=input_json,
            schema_name=schema_name,
            schema=schema,
        )
        value = _parsed_model_output(response)
        errors = validate(value)
        if errors:
            if attempt == 2:
                raise GPTProtocolError(
                    "OpenAI structured output failed local validation after one repair: "
                    + "; ".join(errors)
                )
            input_json = {
                "repair": {
                    "invalid_response": value,
                    "validation_errors": errors,
                },
                "original_input": original_input,
                "original_schema": schema,
            }
            _assert_no_forbidden_input(input_json)
            continue
        response_path = writer.response_path(scope, attempt)
        result = _result_from_response(
            value=value,
            snapshot_sha256=snapshot_sha256,
            request_path=request_path,
            response_path=response_path,
            response=response,
        )
        validated_at = _utc_text(writer.now())
        final = {
            "schema_version": 1,
            "scope": scope,
            "attempt": attempt,
            "snapshot_sha256": snapshot_sha256,
            "request_file": request_path.name,
            "request_sha256": result.request_sha256,
            "response_file": response_path.name,
            "response_sha256": result.response_sha256,
            "response_id": result.response_id,
            "requested_model_id": PROSPECTIVE_MODEL_ID,
            "returned_model_id": result.returned_model_id,
            "validated_at": validated_at,
            "artifact_written_at": validated_at,
        }
        writer.install_final(scope, final)
        return result
    raise AssertionError("bounded pass loop exhausted")


def run_pass1(client: Any, snapshot: str | Path, writer: GPTArtifactWriter) -> GPTPassResult:
    _, _, snapshot_value, snapshot_sha256 = _load_snapshot(snapshot)
    input_json = {
        "objective": "Choose LONG or ABSTAIN for the fixed four-hour paper outcome.",
        "snapshot_sha256": snapshot_sha256,
        "snapshot": snapshot_value,
    }
    _assert_no_forbidden_input(input_json)
    return _run_pass(
        client=client,
        writer=writer,
        scope="pass-1",
        schema_name="prospective_pass_1",
        schema=PASS1_SCHEMA,
        instructions=PASS1_INSTRUCTIONS,
        original_input=input_json,
        snapshot_sha256=snapshot_sha256,
        validate=lambda value: _validate_pass1(value, snapshot_value),
    )


def run_pass2(
    client: Any,
    snapshot: str | Path,
    pass1: GPTPassResult,
    theory_records: Iterable[dict[str, Any]],
    writer: GPTArtifactWriter,
) -> GPTPassResult:
    _, _, snapshot_value, snapshot_sha256 = _load_snapshot(snapshot)
    if snapshot_sha256 != pass1.snapshot_sha256:
        raise GPTProtocolError("Pass 2 snapshot differs from the exact Pass 1 snapshot")
    if not _regular_file(pass1.response_path, label="Pass 1 response artifact"):
        raise GPTProtocolError("Pass 1 response artifact is missing")
    if _sha256(pass1.response_path.read_bytes()) != pass1.response_sha256:
        raise GPTProtocolError("Pass 1 response artifact hash mismatch")
    pass1_final_path = writer.final_path("pass-1")
    if not _regular_file(pass1_final_path, label="Pass 1 final pointer"):
        raise GPTProtocolError("Pass 1 final pointer is missing")
    pass1_final = _json_object(pass1_final_path.read_bytes(), label="Pass 1 final pointer")
    attempt = pass1_final.get("attempt")
    if attempt not in (1, 2):
        raise GPTProtocolError("Pass 1 final pointer attempt is invalid")
    archived_pass1 = _load_response(writer, "pass-1", attempt)
    archived_value = _parsed_model_output(archived_pass1)
    if _validate_pass1(archived_value, snapshot_value):
        raise GPTProtocolError("Pass 1 final response is no longer valid")
    if archived_value != pass1.value:
        raise GPTProtocolError("Pass 1 value does not match the exact archived response")
    if (
        writer.response_path("pass-1", attempt) != pass1.response_path
        or pass1_final.get("response_sha256") != pass1.response_sha256
        or archived_pass1.response_id != pass1.response_id
        or archived_pass1.returned_model_id != pass1.returned_model_id
    ):
        raise GPTProtocolError("Pass 1 result does not match its final pointer")
    records = tuple(theory_records)
    identifiers = _theory_ids(records)
    _assert_no_prior_results(records)
    _assert_no_forbidden_input(records)
    input_json = {
        "objective": "Critique Pass 1 against the six frozen records for the same four-hour outcome.",
        "snapshot_sha256": snapshot_sha256,
        "snapshot": snapshot_value,
        "pass1": {
            "response_sha256": pass1.response_sha256,
            "value": pass1.value,
        },
        "theory_records": list(records),
    }
    _assert_no_forbidden_input(input_json)
    return _run_pass(
        client=client,
        writer=writer,
        scope="pass-2",
        schema_name="prospective_pass_2",
        schema=PASS2_SCHEMA,
        instructions=PASS2_INSTRUCTIONS,
        original_input=input_json,
        snapshot_sha256=snapshot_sha256,
        validate=lambda value: _validate_pass2(
            value,
            snapshot_value,
            snapshot_sha256=snapshot_sha256,
            pass1_response_sha256=pass1.response_sha256,
            theory_ids=identifiers,
        ),
    )


def archive_model_preflight(
    client: Any,
    writer: GPTArtifactWriter,
    model_id: str = PROSPECTIVE_MODEL_ID,
) -> OpenAIEvidenceResponse:
    if model_id != PROSPECTIVE_MODEL_ID:
        raise GPTProtocolError("prospective model ID is fixed")
    scope = "preflight"
    request_path = writer.request_path(scope, 1)
    response_path = writer.response_path(scope, 1)
    request_exists = _regular_file(request_path, label="model preflight request")
    response_exists = _regular_file(response_path, label="model preflight response")
    if response_exists and not request_exists:
        raise GPTProtocolError("model preflight response exists without request")
    if request_exists:
        if not response_exists:
            raise GPTProtocolError("ambiguous model preflight request has no response")
        archived_raw = request_path.read_bytes()
        archived = _json_object(archived_raw, label="model preflight request")
        expected_keys = {
            "schema_version", "scope", "attempt", "method", "path",
            "requested_model_id", "transmission_may_begin", "request_started_at",
            "artifact_written_at",
        }
        if (
            archived_raw != canonical_json_bytes(archived)
            or set(archived) != expected_keys
            or archived.get("schema_version") != 1
            or archived.get("scope") != scope
            or archived.get("attempt") != 1
            or archived.get("method") != "GET"
            or archived.get("path") != f"/v1/models/{model_id}"
            or archived.get("requested_model_id") != model_id
            or archived.get("transmission_may_begin") is not True
            or not isinstance(archived.get("request_started_at"), str)
            or not isinstance(archived.get("artifact_written_at"), str)
        ):
            raise GPTProtocolError("archived model preflight request is invalid")
        response = _load_response(writer, scope, 1)
    else:
        request_started_at = _utc_text(writer.now())
        artifact_written_at = _utc_text(writer.now())
        request = {
            "schema_version": 1,
            "scope": scope,
            "attempt": 1,
            "method": "GET",
            "path": f"/v1/models/{model_id}",
            "requested_model_id": model_id,
            "transmission_may_begin": True,
            "request_started_at": request_started_at,
            "artifact_written_at": artifact_written_at,
        }
        writer.install_request(scope, 1, request)
        try:
            response = client.preflight_model(model_id)
        except OpenAIError as exc:
            if exc.response is not None:
                writer.install_response(scope, 1, exc.response)
            classification = "after transmission" if exc.transmitted else "before transmission"
            raise GPTProtocolError(f"model preflight failed {classification}: {exc}") from exc
        writer.install_response(scope, 1, response)
    if response.status_code != 200 or response.returned_model_id != model_id:
        raise GPTProtocolError("model preflight returned the wrong model ID")
    return response
