from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Callable, Mapping

import httpx


OPENAI_BASE_URL = "https://api.openai.com/v1"
PROSPECTIVE_MODEL_ID = "gpt-5.6-sol"
_EVIDENCE_RESPONSE_HEADERS = frozenset({
    "content-type",
    "date",
    "openai-processing-ms",
    "openai-version",
    "x-request-id",
    "x-ratelimit-limit-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-requests",
    "x-ratelimit-reset-tokens",
})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _reject_non_finite(constant: str) -> None:
    raise ValueError(f"non-finite JSON constant: {constant}")


def _parse_body(raw_body: bytes) -> tuple[dict[str, Any] | None, str]:
    if not raw_body:
        return None, "empty"
    try:
        value = json.loads(
            raw_body.decode("utf-8"),
            parse_constant=_reject_non_finite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None, "non_json"
    return (value, "json_object") if isinstance(value, dict) else (None, "json_other")


def _response_content(body: dict[str, Any] | None) -> tuple[str | None, str | None]:
    if body is None or not isinstance(body.get("output"), list):
        return None, None
    output_texts: list[str] = []
    refusals: list[str] = []
    for output in body["output"]:
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        content = output.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "output_text" and isinstance(item.get("text"), str):
                output_texts.append(item["text"])
            elif item.get("type") == "refusal" and isinstance(item.get("refusal"), str):
                refusals.append(item["refusal"])
    refusal = refusals[0] if len(refusals) == 1 else None
    output_text = output_texts[0] if len(output_texts) == 1 and not refusals else None
    return output_text, refusal


def _provider_created_at(body: dict[str, Any] | None) -> str | None:
    if not isinstance(body, dict):
        return None
    value = body.get("created_at", body.get("created"))
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(float(value)) and value >= 0:
        try:
            return _utc_text(datetime.fromtimestamp(float(value), tz=timezone.utc))
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return _utc_text(parsed)
    return None


@dataclass(frozen=True)
class OpenAIEvidenceResponse:
    body: dict[str, Any] | None
    body_parse_status: str
    raw_body: bytes
    status_code: int
    request_started_at: str
    response_retrieved_at: str
    response_headers: Mapping[str, str]
    provider_created_at: str | None
    response_id: str | None
    returned_model_id: str | None
    usage: Mapping[str, Any]
    output_text: str | None
    refusal: str | None

    @classmethod
    def from_raw(
        cls,
        *,
        raw_body: bytes,
        status_code: int,
        request_started_at: str,
        response_retrieved_at: str,
        response_headers: Mapping[str, str],
    ) -> "OpenAIEvidenceResponse":
        body, parse_status = _parse_body(raw_body)
        output_text, refusal = _response_content(body)
        response_id = body.get("id") if isinstance(body, dict) else None
        model = body.get("model") if isinstance(body, dict) else None
        # Model catalog responses identify the model through their `id` field.
        if model is None and isinstance(body, dict) and "output" not in body:
            model = body.get("id")
        usage = body.get("usage", {}) if isinstance(body, dict) else {}
        return cls(
            body=body,
            body_parse_status=parse_status,
            raw_body=bytes(raw_body),
            status_code=int(status_code),
            request_started_at=request_started_at,
            response_retrieved_at=response_retrieved_at,
            response_headers=MappingProxyType(dict(response_headers)),
            provider_created_at=_provider_created_at(body),
            response_id=response_id if isinstance(response_id, str) else None,
            returned_model_id=model if isinstance(model, str) else None,
            usage=MappingProxyType(dict(usage) if isinstance(usage, dict) else {}),
            output_text=output_text,
            refusal=refusal,
        )


class OpenAIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        transmitted: bool,
        response: OpenAIEvidenceResponse | None = None,
    ) -> None:
        super().__init__(message)
        self.transmitted = transmitted
        self.response = response


def structured_request_body(
    *,
    model_id: str,
    instructions: str,
    input_json: dict[str, Any],
    schema_name: str,
    schema: dict[str, Any],
    max_output_tokens: int = 4000,
) -> dict[str, Any]:
    if (
        isinstance(max_output_tokens, bool)
        or not isinstance(max_output_tokens, int)
        or not 1 <= max_output_tokens <= 128_000
    ):
        raise ValueError("max_output_tokens must be an integer from 1 through 128000")
    canonical_input = json.dumps(
        input_json,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return {
        "model": model_id,
        "reasoning": {"effort": "high"},
        "instructions": instructions,
        "input": canonical_input,
        "text": {"format": {
            "type": "json_schema",
            "name": schema_name,
            "strict": True,
            "schema": schema,
        }},
        "max_output_tokens": max_output_tokens,
        "store": False,
    }


class OpenAIClient:
    """Minimal no-retry transport for prospective OpenAI evidence calls."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = OPENAI_BASE_URL,
        timeout: float = 120.0,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self._api_key:
            raise OpenAIError("OPENAI_API_KEY is not set", transmitted=False)
        if (
            not self._api_key.startswith("sk-")
            or len(self._api_key) < 20
            or not self._api_key.isascii()
            or any(character.isspace() for character in self._api_key)
        ):
            raise OpenAIError(
                "OPENAI_API_KEY has invalid format",
                transmitted=False,
            )
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._now = now

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> OpenAIEvidenceResponse:
        request_started_at = _utc_text(self._now())
        kwargs: dict[str, Any] = {
            "headers": {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        }
        if body is not None:
            kwargs["content"] = json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.request(method, f"{self._base_url}/{path.lstrip('/')}", **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise OpenAIError(
                f"OpenAI transport failed before transmission: {type(exc).__name__}",
                transmitted=False,
            ) from exc
        except httpx.HTTPError as exc:
            raise OpenAIError(
                f"OpenAI transport failed after transmission: {type(exc).__name__}",
                transmitted=True,
            ) from exc
        evidence = OpenAIEvidenceResponse.from_raw(
            raw_body=response.content,
            status_code=response.status_code,
            request_started_at=request_started_at,
            response_retrieved_at=_utc_text(self._now()),
            response_headers={
                name: value
                for name, value in response.headers.items()
                if name.lower() in _EVIDENCE_RESPONSE_HEADERS
            },
        )
        if response.status_code >= 400:
            raise OpenAIError(
                f"OpenAI HTTP {response.status_code}",
                transmitted=True,
                response=evidence,
            )
        return evidence

    def preflight_model(self, model_id: str) -> OpenAIEvidenceResponse:
        if model_id != PROSPECTIVE_MODEL_ID:
            raise OpenAIError("prospective model ID is fixed", transmitted=False)
        response = self._request("GET", f"models/{model_id}")
        if response.returned_model_id != model_id:
            raise OpenAIError(
                "OpenAI model mismatch during preflight",
                transmitted=True,
                response=response,
            )
        return response

    def create_structured(
        self,
        *,
        model_id: str,
        instructions: str,
        input_json: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
        max_output_tokens: int = 4000,
    ) -> OpenAIEvidenceResponse:
        if model_id != PROSPECTIVE_MODEL_ID:
            raise OpenAIError("prospective model ID is fixed", transmitted=False)
        body = structured_request_body(
            model_id=model_id,
            instructions=instructions,
            input_json=input_json,
            schema_name=schema_name,
            schema=schema,
            max_output_tokens=max_output_tokens,
        )
        return self._request("POST", "responses", body=body)
