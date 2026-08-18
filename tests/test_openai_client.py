from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest


def _clock():
    return datetime(2026, 8, 17, 10, tzinfo=timezone.utc)


def test_openai_transport_rejects_invalid_key_shape_before_transmission():
    from src.nansen_signal_lab.openai_client import OpenAIClient, OpenAIError

    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"id": "gpt-5.6-sol"})

    with pytest.raises(OpenAIError, match="invalid format") as caught:
        OpenAIClient(
            api_key="not-an-openai-api-key",
            transport=httpx.MockTransport(handler),
            now=_clock,
        )
    assert caught.value.transmitted is False
    assert calls == 0


def test_openai_transport_retains_only_safe_evidence_headers():
    from src.nansen_signal_lab.openai_client import OpenAIClient

    client = OpenAIClient(
        api_key="sk-test-secret-1234567890",
        transport=httpx.MockTransport(lambda _request: httpx.Response(
            200,
            json={"id": "gpt-5.6-sol"},
            headers={
                "Content-Type": "application/json",
                "Date": "Mon, 17 Aug 2026 10:00:00 GMT",
                "OpenAI-Processing-Ms": "7",
                "OpenAI-Version": "2020-10-01",
                "X-Request-ID": "request-1",
                "Set-Cookie": "do-not-archive",
                "Authorization": "do-not-archive",
                "X-API-Key": "do-not-archive",
            },
        )),
        now=_clock,
    )

    response = client.preflight_model("gpt-5.6-sol")

    assert response.response_headers == {
        "content-type": "application/json",
        "date": "Mon, 17 Aug 2026 10:00:00 GMT",
        "openai-processing-ms": "7",
        "openai-version": "2020-10-01",
        "x-request-id": "request-1",
    }


def test_openai_transport_uses_exact_model_and_responses_contract():
    from src.nansen_signal_lab.openai_client import OpenAIClient
    from src.nansen_signal_lab.gpt_protocol import PASS1_SCHEMA

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, content=b'{"id":"gpt-5.6-sol","created":0}')
        return httpx.Response(200, content=json.dumps({
            "id": "resp_1",
            "model": "gpt-5.6-sol",
            "created_at": 0,
            "status": "completed",
            "output": [{
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "{}"}],
            }],
            "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
        }).encode())

    client = OpenAIClient(
        api_key="sk-test-secret-1234567890",
        transport=httpx.MockTransport(handler),
        now=_clock,
    )
    preflight = client.preflight_model("gpt-5.6-sol")
    result = client.create_structured(
        model_id="gpt-5.6-sol",
        instructions="Return one object.",
        input_json={"candidate": {"identity": "candidate-1"}},
        schema_name="prospective_pass_1",
        schema=PASS1_SCHEMA,
    )

    assert requests[0].method == "GET"
    assert requests[0].url.path == "/v1/models/gpt-5.6-sol"
    assert requests[0].headers["authorization"] == (
        "Bearer sk-test-secret-1234567890"
    )
    assert requests[1].method == "POST"
    assert requests[1].url.path == "/v1/responses"
    body = json.loads(requests[1].content)
    assert body == {
        "model": "gpt-5.6-sol",
        "reasoning": {"effort": "high"},
        "instructions": "Return one object.",
        "input": '{"candidate":{"identity":"candidate-1"}}',
        "text": {"format": {
            "type": "json_schema",
            "name": "prospective_pass_1",
            "strict": True,
            "schema": PASS1_SCHEMA,
        }},
        "max_output_tokens": 4000,
        "store": False,
    }
    assert "tools" not in body and "previous_response_id" not in body
    assert requests[1].content == json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert preflight.returned_model_id == "gpt-5.6-sol"
    assert preflight.provider_created_at == "1970-01-01T00:00:00Z"
    assert result.response_id == "resp_1"
    assert result.provider_created_at == "1970-01-01T00:00:00Z"
    assert result.output_text == "{}"
    assert result.usage == {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}
    assert result.raw_body.startswith(b'{"id": "resp_1"')


def test_openai_transport_accepts_a_bounded_protocol_output_allowance():
    from src.nansen_signal_lab.openai_client import OpenAIClient

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "id": "resp_budget",
            "model": "gpt-5.6-sol",
            "status": "completed",
            "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": "{}"}],
            }],
        })

    client = OpenAIClient(
        api_key="sk-test-secret-1234567890",
        transport=httpx.MockTransport(handler),
        now=_clock,
    )
    client.create_structured(
        model_id="gpt-5.6-sol",
        instructions="Return one object.",
        input_json={},
        schema_name="pass_2_budget",
        schema={"type": "object"},
        max_output_tokens=25_000,
    )

    assert json.loads(requests[0].content)["max_output_tokens"] == 25_000

    with pytest.raises(ValueError, match="max_output_tokens"):
        client.create_structured(
            model_id="gpt-5.6-sol",
            instructions="Return one object.",
            input_json={},
            schema_name="invalid_budget",
            schema={"type": "object"},
            max_output_tokens=128_001,
        )


def test_openai_transport_rejects_model_mismatch_and_redacts_failures():
    from src.nansen_signal_lab.openai_client import OpenAIClient, OpenAIError

    mismatch = OpenAIClient(
        api_key="sk-do-not-print-me-1234567890",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"id": "gpt-5.6-terra"})
        ),
        now=_clock,
    )
    with pytest.raises(OpenAIError, match="model mismatch") as caught:
        mismatch.preflight_model("gpt-5.6-sol")
    assert "sk-do-not-print-me-1234567890" not in str(caught.value)
    assert caught.value.transmitted is True
    assert caught.value.response is not None

    failed = OpenAIClient(
        api_key="sk-do-not-print-me-1234567890",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(503, content=b"provider unavailable")
        ),
        now=_clock,
    )
    with pytest.raises(OpenAIError, match="HTTP 503") as caught:
        failed.create_structured(
            model_id="gpt-5.6-sol", instructions="x", input_json={},
            schema_name="x", schema={"type": "object"},
        )
    assert "sk-do-not-print-me-1234567890" not in str(caught.value)
    assert caught.value.transmitted is True
    assert caught.value.response is not None
    assert caught.value.response.raw_body == b"provider unavailable"


def test_openai_transport_classifies_timeout_after_send_without_retry():
    from src.nansen_signal_lab.openai_client import OpenAIClient, OpenAIError

    calls = 0

    def timeout(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("late", request=request)

    client = OpenAIClient(
        api_key="sk-test-secret-1234567890",
        transport=httpx.MockTransport(timeout),
        now=_clock,
    )
    with pytest.raises(OpenAIError, match="after transmission") as caught:
        client.create_structured(
            model_id="gpt-5.6-sol", instructions="x", input_json={},
            schema_name="x", schema={"type": "object"},
        )
    assert calls == 1
    assert caught.value.transmitted is True
    assert caught.value.response is None


def test_openai_transport_surfaces_refusal_without_treating_it_as_output():
    from src.nansen_signal_lab.openai_client import OpenAIClient

    raw = b'{"id":"resp_r","model":"gpt-5.6-sol","status":"completed","output":[{"type":"message","content":[{"type":"refusal","refusal":"no"}]}],"usage":{}}'
    client = OpenAIClient(
        api_key="sk-test-secret-1234567890",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=raw)),
        now=_clock,
    )
    response = client.create_structured(
        model_id="gpt-5.6-sol", instructions="x", input_json={},
        schema_name="x", schema={"type": "object"},
    )
    assert response.raw_body == raw
    assert response.refusal == "no"
    assert response.output_text is None
