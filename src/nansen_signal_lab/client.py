from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://api.nansen.ai/api/v1"


class NansenError(RuntimeError):
    pass


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

    def post(self, endpoint: str, payload: dict[str, Any], *, refresh: bool = False) -> dict[str, Any]:
        path = self._cache_path(endpoint, payload)
        if path.exists() and not refresh:
            return json.loads(path.read_text())

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

        path.write_text(json.dumps(body, indent=2, ensure_ascii=False))
        return body
