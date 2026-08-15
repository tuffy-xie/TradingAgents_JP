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
        record = self._read_record(namespace, key)
        if record is None or record["expires_at"] <= time.time():
            return None
        return record["data"]

    def get_stale(self, namespace: str, key: str) -> dict[str, Any] | None:
        """Return an expired record for providers that support last-good fallback.

        The normal ``get`` path remains TTL strict.  This explicit method keeps
        stale fallback opt-in and exposes cache age metadata to the caller.
        """
        record = self._read_record(namespace, key)
        if record is None:
            return None
        return record

    def _read_record(self, namespace: str, key: str) -> dict[str, Any] | None:
        path = self._path(namespace, key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            data = payload.get("data")
            if not isinstance(data, dict):
                return None
            return {
                "data": data,
                "expires_at": float(payload["expires_at"]),
                "created_at": float(payload.get("created_at", payload["expires_at"])),
            }
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def set(self, namespace: str, key: str, data: dict[str, Any], ttl_seconds: int) -> bool:
        path = self._path(namespace, key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            now = time.time()
            payload = {
                "created_at": now,
                "expires_at": now + max(0, ttl_seconds),
                "data": data,
            }
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, path)
            return True
        except OSError:
            # A read-only home/cache directory must never turn a data-source
            # failure into a graph failure. The caller still receives the live
            # response and a visible provider status.
            return False

    def _path(self, namespace: str, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        safe_namespace = "".join(char if char.isalnum() or char in "_-" else "_" for char in namespace)
        return self.root / safe_namespace / f"{digest}.json"
