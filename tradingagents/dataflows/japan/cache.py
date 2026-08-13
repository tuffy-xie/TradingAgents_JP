"""Small JSON TTL cache for public Japan data sources."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


class JapanDataCache:
    """Filesystem cache keyed by source/category/ticker without storing secrets."""

    def __init__(self, root: str | Path):
        self.root = Path(root) / "japan"

    def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        path = self._path(namespace, key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if float(payload["expires_at"]) <= time.time():
                return None
            data = payload.get("data")
            return data if isinstance(data, dict) else None
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def set(self, namespace: str, key: str, data: dict[str, Any], ttl_seconds: int) -> None:
        path = self._path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"expires_at": time.time() + max(0, ttl_seconds), "data": data}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)

    def _path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        safe_namespace = "".join(char if char.isalnum() or char in "_-" else "_" for char in namespace)
        return self.root / safe_namespace / f"{digest}.json"
