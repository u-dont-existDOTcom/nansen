from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://api.nansen.ai/api/v1"


class NansenError(RuntimeError):
    pass


@dataclass(frozen=True)
class NansenEvidenceResponse:
    body: Any | None
    body_parse_status: str
    raw_body: bytes
    status_code: int
    request_started_at: str
    response_retrieved_at: str
    response_headers: dict[str, str]
    request_id: str | None
    credit_cost: int | None
    credit_used: int | None
    credit_remaining: int | None
    credit_header_errors: tuple[str, ...]


class NansenRequestFailure(NansenError):
    def __init__(
        self,
        message: str,
        *,
        transmitted: bool,
        response: NansenEvidenceResponse | None = None,
    ) -> None:
        super().__init__(message)
        self.transmitted = transmitted
        self.response = response


@dataclass(frozen=True)
class NansenResponse:
    body: dict[str, Any]
    cache_hit: bool
    response_retrieved_at: str


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_EVIDENCE_HEADERS = (
    "X-Request-Id",
    "X-Nansen-Credits-Cost",
    "X-Nansen-Credits-Used",
    "X-Nansen-Credits-Remaining",
    "Retry-After",
)
_CREDIT_HEADERS = {
    "X-Nansen-Credits-Cost": "credit_cost",
    "X-Nansen-Credits-Used": "credit_used",
    "X-Nansen-Credits-Remaining": "credit_remaining",
}


def _reject_non_finite(constant: str) -> None:
    raise ValueError(f"non-finite JSON value: {constant}")


def _parse_evidence_body(raw_body: bytes) -> tuple[Any | None, str]:
    if not raw_body:
        return None, "empty"
    try:
        body = json.loads(
            raw_body.decode("utf-8"),
            parse_constant=_reject_non_finite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None, "non_json"
    return body, "json_object" if isinstance(body, dict) else "json_other"


def _parse_credit_header(headers: dict[str, str], name: str) -> tuple[int | None, bool]:
    raw = headers.get(name)
    if raw is None:
        return None, False
    if not raw.isascii() or not raw.isdigit():
        return None, True
    return int(raw), False


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class NansenClient:
    def __init__(self, api_key: str | None = None, cache_dir: str | Path = "data/cache"):
        self.api_key = api_key or os.environ.get("NANSEN_API_KEY")
        if not self.api_key:
            raise NansenError("NANSEN_API_KEY is not set")
        self.cache_dir = Path(cache_dir)

    def fetch_openapi(self) -> bytes:
        """Fetch exact public contract bytes without transmitting credentials."""
        with httpx.Client(timeout=60.0) as client:
            response = client.get("https://api.nansen.ai/openapi.json")
        response.raise_for_status()
        return bytes(response.content)

    def _cache_path(self, endpoint: str, payload: dict[str, Any]) -> Path:
        canonical = json.dumps({"endpoint": endpoint, "payload": payload}, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode()).hexdigest()[:24]
        slug = endpoint.strip("/").replace("/", "_")
        return self.cache_dir / f"{slug}-{digest}.json"

    @staticmethod
    def _cache_metadata_path(cache_path: Path) -> Path:
        return cache_path.with_suffix(".meta.json")

    def post_with_provenance(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        refresh: bool = False,
    ) -> NansenResponse:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(endpoint, payload)
        metadata_path = self._cache_metadata_path(path)
        if path.exists() and not refresh:
            response_bytes = path.read_bytes()
            body = json.loads(response_bytes)
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text())
                except (OSError, json.JSONDecodeError) as exc:
                    raise NansenError(f"cannot read cache provenance {metadata_path}: {exc}") from exc
                retrieved_at = metadata.get("response_retrieved_at")
                expected_sha256 = metadata.get("response_sha256")
                actual_sha256 = hashlib.sha256(response_bytes).hexdigest()
                if (
                    metadata.get("endpoint") != endpoint
                    or metadata.get("payload") != payload
                    or not isinstance(retrieved_at, str)
                    or expected_sha256 != actual_sha256
                ):
                    raise NansenError(f"cache provenance mismatch for {path}")
            else:
                retrieved_at = (
                    datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            return NansenResponse(
                body=body,
                cache_hit=True,
                response_retrieved_at=retrieved_at,
            )

        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{BASE_URL}/{endpoint.lstrip('/')}",
                headers={"apikey": self.api_key, "Content-Type": "application/json"},
                json=payload,
            )
        try:
            body = response.json()
        except Exception:
            body = {"raw": response.text}

        if response.status_code >= 400:
            raise NansenError(f"Nansen HTTP {response.status_code}: {json.dumps(body, ensure_ascii=False)[:1500]}")

        retrieved_at = _utc_now_text()
        response_bytes = json.dumps(body, indent=2, ensure_ascii=False).encode("utf-8")
        metadata = {
            "schema_version": 1,
            "endpoint": endpoint,
            "payload": payload,
            "response_retrieved_at": retrieved_at,
            "response_file": path.name,
            "response_sha256": hashlib.sha256(response_bytes).hexdigest(),
        }
        _atomic_write(path, response_bytes)
        _atomic_write(
            metadata_path,
            (json.dumps(metadata, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"),
        )
        return NansenResponse(
            body=body,
            cache_hit=False,
            response_retrieved_at=retrieved_at,
        )

    def request_evidence(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None,
        *,
        caller_request_id: str,
    ) -> NansenEvidenceResponse:
        normalized_endpoint = endpoint.strip("/")
        if (
            not normalized_endpoint
            or "://" in normalized_endpoint
            or "?" in normalized_endpoint
            or "#" in normalized_endpoint
        ):
            raise NansenRequestFailure(
                "Nansen evidence endpoint must be a relative endpoint ID",
                transmitted=False,
            )
        request_started_at = _utc_now_text()
        request_kwargs: dict[str, Any] = {
            "headers": {
                "apikey": self.api_key,
                "Content-Type": "application/json",
                "X-Request-Id": caller_request_id,
            }
        }
        if payload is not None:
            request_kwargs["json"] = payload
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.request(
                    method.upper(),
                    f"{BASE_URL}/{normalized_endpoint}",
                    **request_kwargs,
                )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise NansenRequestFailure(
                f"Nansen transport failed before transmission: {type(exc).__name__}",
                transmitted=False,
            ) from exc
        except httpx.HTTPError as exc:
            raise NansenRequestFailure(
                f"Nansen transport failed after transmission: {type(exc).__name__}",
                transmitted=True,
            ) from exc

        response_retrieved_at = _utc_now_text()
        raw_body = response.content
        body, body_parse_status = _parse_evidence_body(raw_body)
        retained_headers = {
            name: response.headers[name]
            for name in _EVIDENCE_HEADERS
            if name in response.headers
        }
        parsed_credits: dict[str, int | None] = {}
        credit_errors = []
        for header_name, field_name in _CREDIT_HEADERS.items():
            parsed, malformed = _parse_credit_header(retained_headers, header_name)
            parsed_credits[field_name] = parsed
            if malformed:
                credit_errors.append(header_name)
        evidence = NansenEvidenceResponse(
            body=body,
            body_parse_status=body_parse_status,
            raw_body=raw_body,
            status_code=response.status_code,
            request_started_at=request_started_at,
            response_retrieved_at=response_retrieved_at,
            response_headers=retained_headers,
            request_id=retained_headers.get("X-Request-Id"),
            credit_cost=parsed_credits["credit_cost"],
            credit_used=parsed_credits["credit_used"],
            credit_remaining=parsed_credits["credit_remaining"],
            credit_header_errors=tuple(credit_errors),
        )
        if not 200 <= response.status_code < 300:
            raise NansenRequestFailure(
                f"Nansen HTTP {response.status_code}",
                transmitted=True,
                response=evidence,
            )
        if body_parse_status != "json_object":
            raise NansenRequestFailure(
                "Nansen successful response was not a JSON object",
                transmitted=True,
                response=evidence,
            )
        return evidence

    def post(self, endpoint: str, payload: dict[str, Any], *, refresh: bool = False) -> dict[str, Any]:
        return self.post_with_provenance(endpoint, payload, refresh=refresh).body
