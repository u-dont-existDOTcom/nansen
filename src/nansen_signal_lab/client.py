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
class NansenResponse:
    body: dict[str, Any]
    cache_hit: bool
    response_retrieved_at: str


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
        self.cache_dir.mkdir(parents=True, exist_ok=True)

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

    def post(self, endpoint: str, payload: dict[str, Any], *, refresh: bool = False) -> dict[str, Any]:
        return self.post_with_provenance(endpoint, payload, refresh=refresh).body
