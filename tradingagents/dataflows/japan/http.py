"""Conservative HTTP helpers shared by Japan data providers."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import requests

from .models import DataStatus

USER_AGENT = "TradingAgents/0.3 JapanData (+https://github.com/xiejiatao369/TradingAgents)"


@dataclass(frozen=True)
class JsonResponse:
    status: DataStatus
    payload: Any = None
    detail: str = ""


@dataclass(frozen=True)
class BytesResponse:
    """A public-file response, used for CSV/XLSX/XLS publications."""

    status: DataStatus
    payload: bytes = b""
    detail: str = ""


async def get_json(
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> JsonResponse:
    """Fetch JSON with explicit authentication/rate-limit/parser states."""
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    request_headers.update(headers or {})
    try:
        response = await asyncio.to_thread(
            requests.get, url, params=params, headers=request_headers, timeout=timeout
        )
    except requests.RequestException as exc:
        return JsonResponse(DataStatus.DATA_UNAVAILABLE, detail=type(exc).__name__)

    if response.status_code in {401, 403}:
        return JsonResponse(DataStatus.AUTH_REQUIRED, detail=f"HTTP {response.status_code}")
    if response.status_code == 429:
        return JsonResponse(DataStatus.RATE_LIMITED, detail="HTTP 429")
    if not response.ok:
        return JsonResponse(DataStatus.DATA_UNAVAILABLE, detail=f"HTTP {response.status_code}")
    try:
        return JsonResponse(DataStatus.OK, payload=response.json())
    except (TypeError, ValueError, json.JSONDecodeError):
        return JsonResponse(DataStatus.PARSE_FAILED, detail="invalid JSON response")


async def get_text(url: str, *, timeout: float = 10.0) -> tuple[DataStatus, str, str]:
    """Fetch a public HTML page without accepting credentials or paywall bypasses."""
    try:
        response = await asyncio.to_thread(
            requests.get,
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return DataStatus.DATA_UNAVAILABLE, "", type(exc).__name__
    if response.status_code == 429:
        return DataStatus.RATE_LIMITED, "", "HTTP 429"
    if response.status_code in {401, 403}:
        return DataStatus.AUTH_REQUIRED, "", f"HTTP {response.status_code}"
    if not response.ok:
        return DataStatus.DATA_UNAVAILABLE, "", f"HTTP {response.status_code}"
    return DataStatus.OK, response.text, ""


async def get_bytes(
    url: str, *, params: dict[str, str] | None = None, timeout: float = 10.0,
    max_bytes: int | None = None, headers: dict[str, str] | None = None,
) -> BytesResponse:
    """Fetch a public download without credentials, cookies, or paywall bypasses."""
    try:
        response = await asyncio.to_thread(
            requests.get,
            url,
            params=params,
            headers={"User-Agent": USER_AGENT, "Accept": "text/csv,application/vnd.ms-excel,*/*", **(headers or {})},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return BytesResponse(DataStatus.DATA_UNAVAILABLE, detail=type(exc).__name__)
    if response.status_code == 429:
        return BytesResponse(DataStatus.RATE_LIMITED, detail="HTTP 429")
    if response.status_code in {401, 403}:
        return BytesResponse(DataStatus.AUTH_REQUIRED, detail=f"HTTP {response.status_code}")
    if not response.ok:
        return BytesResponse(DataStatus.DATA_UNAVAILABLE, detail=f"HTTP {response.status_code}")
    if max_bytes is not None and len(response.content) > max_bytes:
        return BytesResponse(DataStatus.DATA_UNAVAILABLE, detail=f"download exceeds {max_bytes} bytes")
    return BytesResponse(DataStatus.OK, payload=response.content)
