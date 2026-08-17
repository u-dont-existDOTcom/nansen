from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest


def _clock():
    return datetime(2026, 8, 17, 10, tzinfo=timezone.utc)


def test_openai_transport_uses_exact_model_and_responses_contract():
    from src.nansen_signal_lab.openai_client import OpenAIClient
    from src.nansen_signal_lab.gpt_protocol import PASS1_SCHEMA

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, content=b'{"id":"gpt-5.6-sol"}')
        return httpx.Response(200, content=json.dumps({
            "id": "resp_1",
            "model": "gpt-5.6-sol",
            "status": "completed",
            "output": [{
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "{}"}],
            }],
            "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
        }).encode())

    client = OpenAIClient(
        api_key="test-secret",
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
    assert requests[0].headers["authorization"] == "Bearer test-secret"
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
    assert result.response_id == "resp_1"
    assert result.output_text == "{}"
    assert result.usage == {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}
    assert result.raw_body.startswith(b'{"id": "resp_1"')


def test_openai_transport_rejects_model_mismatch_and_redacts_failures():
    from src.nansen_signal_lab.openai_client import OpenAIClient, OpenAIError

    mismatch = OpenAIClient(
        api_key="do-not-print-me",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"id": "gpt-5.6-terra"})
        ),
        now=_clock,
    )
    with pytest.raises(OpenAIError, match="model mismatch") as caught:
        mismatch.preflight_model("gpt-5.6-sol")
    assert "do-not-print-me" not in str(caught.value)
    assert caught.value.transmitted is True
    assert caught.value.response is not None

    failed = OpenAIClient(
        api_key="do-not-print-me",
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
    assert "do-not-print-me" not in str(caught.value)
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
        api_key="test", transport=httpx.MockTransport(timeout), now=_clock,
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
        api_key="test",
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
