from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import httpx
import pytest

import src.nansen_signal_lab.client as client_module
from src.nansen_signal_lab.client import NansenClient, NansenRequestFailure


def install_transport(monkeypatch, handler):
    """Keep NansenClient real while replacing only its external HTTP transport."""
    real_client = httpx.Client
    transport = httpx.MockTransport(handler)

    def client_factory(*, timeout):
        return real_client(timeout=timeout, transport=transport)

    monkeypatch.setattr(client_module.httpx, "Client", client_factory)


def test_network_response_records_original_cache_retrieval_time(tmp_path, monkeypatch):
    """Fails if a network response is cached without its original retrieval provenance."""
    body = {"data": [{"value": 1}]}
    install_transport(monkeypatch, lambda request: httpx.Response(200, json=body))
    client = NansenClient(api_key="test-key", cache_dir=tmp_path)

    result = client.post_with_provenance("tgm/flows", {"chain": "base"})

    assert result.body == body
    assert result.cache_hit is False
    parsed = datetime.fromisoformat(result.response_retrieved_at.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    metadata_paths = list(tmp_path.glob("*.meta.json"))
    assert len(metadata_paths) == 1
    metadata = json.loads(metadata_paths[0].read_text())
    assert metadata["response_retrieved_at"] == result.response_retrieved_at
    assert metadata["endpoint"] == "tgm/flows"
    assert metadata["payload"] == {"chain": "base"}


def test_cache_hit_reuses_original_response_retrieval_time(tmp_path, monkeypatch):
    """Fails if a cache hit claims the later artifact/request time as response retrieval."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"data": []})

    install_transport(monkeypatch, handler)
    client = NansenClient(api_key="test-key", cache_dir=tmp_path)

    first = client.post_with_provenance("tgm/flows", {"chain": "base"})
    second = client.post_with_provenance("tgm/flows", {"chain": "base"})

    assert len(calls) == 1
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.response_retrieved_at == first.response_retrieved_at
    assert second.body == first.body


def test_legacy_raw_only_cache_uses_file_mtime_as_retrieval_fallback(tmp_path):
    """Fails if legacy cache reuse invents a fresh network retrieval timestamp."""
    client = NansenClient(api_key="test-key", cache_dir=tmp_path)
    payload = {"chain": "base"}
    cache_path = client._cache_path("tgm/flows", payload)
    cache_path.write_text(json.dumps({"data": []}))
    fallback_epoch = 1_786_838_400
    os.utime(cache_path, (fallback_epoch, fallback_epoch))

    result = client.post_with_provenance("tgm/flows", payload)

    expected = datetime.fromtimestamp(fallback_epoch, timezone.utc).isoformat().replace("+00:00", "Z")
    assert result.cache_hit is True
    assert result.response_retrieved_at == expected


def test_post_keeps_returning_only_the_response_body(tmp_path, monkeypatch):
    """Fails if adding provenance breaks existing body-returning callers."""
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={"data": [1]}))
    client = NansenClient(api_key="test-key", cache_dir=tmp_path)

    assert client.post("tgm/flows", {"chain": "base"}) == {"data": [1]}


def test_evidence_request_returns_exact_bytes_and_credit_headers(tmp_path, monkeypatch):
    raw = b'{"data":[]}'
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            content=raw,
            headers={
                "X-Request-Id": "nansen-1",
                "X-Nansen-Credits-Cost": "1",
                "X-Nansen-Credits-Used": "1",
                "X-Nansen-Credits-Remaining": "9",
                "Server": "must-not-be-retained",
            },
        )

    install_transport(monkeypatch, handler)
    cache = tmp_path / "cache"
    response = NansenClient(api_key="test", cache_dir=cache).request_evidence(
        "POST", "tgm/flows", {"chain": "base"}, caller_request_id="pilot-1"
    )

    assert response.raw_body == raw
    assert response.body == {"data": []}
    assert response.body_parse_status == "json_object"
    assert response.request_id == "nansen-1"
    assert (response.credit_cost, response.credit_used, response.credit_remaining) == (1, 1, 9)
    assert response.credit_header_errors == ()
    assert response.response_headers == {
        "X-Request-Id": "nansen-1",
        "X-Nansen-Credits-Cost": "1",
        "X-Nansen-Credits-Used": "1",
        "X-Nansen-Credits-Remaining": "9",
    }
    assert requests[0].headers["X-Request-Id"] == "pilot-1"
    assert requests[0].url.path == "/api/v1/tgm/flows"
    assert len(requests) == 1
    assert not cache.exists()


def test_evidence_request_get_sends_no_body_and_uses_one_relative_prefix(tmp_path, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"plan": "free"},
            headers={
                "X-Nansen-Credits-Cost": "0",
                "X-Nansen-Credits-Used": "0",
                "X-Nansen-Credits-Remaining": "10",
            },
        )

    install_transport(monkeypatch, handler)
    NansenClient(api_key="test", cache_dir=tmp_path / "absent").request_evidence(
        "GET", "account", None, caller_request_id="account-1"
    )

    assert len(requests) == 1
    assert requests[0].url.path == "/api/v1/account"
    assert requests[0].content == b""


@pytest.mark.parametrize(
    ("status", "raw", "parse_status", "body"),
    [
        (429, b"<html>slow down</html>", "non_json", None),
        (503, b"", "empty", None),
        (400, b"[]", "json_other", []),
    ],
)
def test_evidence_non_2xx_preserves_archivable_response(
    tmp_path, monkeypatch, status, raw, parse_status, body
):
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            status,
            content=raw,
            headers={
                "X-Request-Id": "failure-1",
                "X-Nansen-Credits-Cost": "broken",
                "X-Nansen-Credits-Used": "0",
                "Retry-After": "12",
            },
        ),
    )

    with pytest.raises(NansenRequestFailure) as caught:
        NansenClient(api_key="super-secret", cache_dir=tmp_path).request_evidence(
            "POST", "tgm/flows", {}, caller_request_id="pilot-2"
        )

    failure = caught.value
    assert failure.transmitted is True
    assert failure.response is not None
    assert failure.response.raw_body == raw
    assert failure.response.body == body
    assert failure.response.body_parse_status == parse_status
    assert failure.response.response_headers == {
        "X-Request-Id": "failure-1",
        "X-Nansen-Credits-Cost": "broken",
        "X-Nansen-Credits-Used": "0",
        "Retry-After": "12",
    }
    assert failure.response.credit_cost is None
    assert failure.response.credit_used == 0
    assert failure.response.credit_header_errors == ("X-Nansen-Credits-Cost",)
    assert "super-secret" not in str(failure)
    assert "super-secret" not in repr(failure)


@pytest.mark.parametrize("raw", [b"[]", b"not json", b""])
def test_evidence_success_requires_json_object_and_keeps_response(tmp_path, monkeypatch, raw):
    install_transport(monkeypatch, lambda request: httpx.Response(200, content=raw))

    with pytest.raises(NansenRequestFailure) as caught:
        NansenClient(api_key="secret-value", cache_dir=tmp_path).request_evidence(
            "POST", "tgm/flows", {}, caller_request_id="pilot-3"
        )

    assert caught.value.transmitted is True
    assert caught.value.response is not None
    assert caught.value.response.raw_body == raw
    assert "secret-value" not in repr(caught.value)


def test_evidence_timeout_after_transmission_has_no_response_and_no_secret(tmp_path, monkeypatch):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("read timed out", request=request)

    install_transport(monkeypatch, handler)

    with pytest.raises(NansenRequestFailure) as caught:
        NansenClient(api_key="never-print-this", cache_dir=tmp_path).request_evidence(
            "POST", "tgm/flows", {}, caller_request_id="pilot-4"
        )

    assert calls == 1
    assert caught.value.transmitted is True
    assert caught.value.response is None
    assert "never-print-this" not in str(caught.value)
    assert "never-print-this" not in repr(caught.value)
