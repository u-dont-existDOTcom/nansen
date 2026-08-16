from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import httpx

import src.nansen_signal_lab.client as client_module
from src.nansen_signal_lab.client import NansenClient


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
