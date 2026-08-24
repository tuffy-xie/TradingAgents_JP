"""Transport-only client for EDINET DB earnings disclosures.

This module deliberately keeps API transport separate from financial
normalization.  It returns the provider's raw earnings records plus immutable
source metadata; ``edinet_db_normalizer`` remains responsible for mapping
those records to ``FinancialDocument`` and ``FinancialRecord``.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import quote

import requests


class EDINETDBStatus(StrEnum):
    """Transport states specific to the EDINET DB API contract."""

    OK = "OK"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    API_ERROR = "API_ERROR"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


@dataclass(frozen=True)
class EDINETDBResponse:
    """Raw EDINET DB earnings response and source metadata.

    ``records`` preserves provider fields unchanged.  ``raw_response`` keeps
    the decoded top-level response for diagnostics and future normalization;
    no provider, cache, or agent integration happens in this module.
    """

    status: EDINETDBStatus
    records: tuple[dict[str, Any], ...] = ()
    raw_response: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    detail: str = ""


class EDINETDBProvider:
    """Fetch raw earnings disclosures from EDINET DB.

    ``issuer_identifier`` is passed as the API's company ``code`` path value.
    The caller is responsible for resolving a listed security code to an
    EDINET code when that mapping is required.
    """

    name = "EDINET DB"
    source_type = "STRUCTURED_SOURCE"
    default_base_url = "https://edinetdb.jp/v1"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 10.0,
        http_get: Callable[..., Any] | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("EDINETDB_API_KEY", "")
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.timeout = timeout
        self._http_get = http_get or requests.get

    async def fetch_earnings(
        self,
        issuer_identifier: str,
        *,
        limit: int = 8,
    ) -> EDINETDBResponse:
        """Fetch raw earnings records without applying financial semantics."""
        issuer_code = str(issuer_identifier or "").strip()
        if not self.api_key.strip():
            return self._response(
                EDINETDBStatus.AUTH_REQUIRED,
                issuer_code,
                detail="EDINETDB_API_KEY is not configured",
            )
        if not issuer_code:
            return self._response(
                EDINETDBStatus.DATA_UNAVAILABLE,
                issuer_code,
                detail="issuer identifier is required",
            )
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 30:
            return self._response(
                EDINETDBStatus.DATA_UNAVAILABLE,
                issuer_code,
                detail="limit must be an integer from 1 to 30",
            )

        url = f"{self.base_url}/companies/{quote(issuer_code, safe='')}/earnings"
        try:
            response = await asyncio.to_thread(
                self._http_get,
                url,
                params={"limit": limit},
                headers={"Accept": "application/json", "X-API-Key": self.api_key},
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            return self._response(
                EDINETDBStatus.TIMEOUT,
                issuer_code,
                requested_url=url,
                detail=type(exc).__name__,
            )
        except requests.RequestException as exc:
            return self._response(
                EDINETDBStatus.API_ERROR,
                issuer_code,
                requested_url=url,
                detail=type(exc).__name__,
            )

        status = self._http_status(response.status_code)
        if status != EDINETDBStatus.OK:
            return self._response(
                status,
                issuer_code,
                requested_url=url,
                detail=f"HTTP {response.status_code}",
            )
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return self._response(
                EDINETDBStatus.DATA_UNAVAILABLE,
                issuer_code,
                requested_url=url,
                detail="invalid JSON response",
            )

        records = _earnings_records(payload)
        if records is None:
            return self._response(
                EDINETDBStatus.DATA_UNAVAILABLE,
                issuer_code,
                requested_url=url,
                raw_response=payload,
                detail="earnings response has no list data field",
            )
        return self._response(
            EDINETDBStatus.OK,
            issuer_code,
            requested_url=url,
            records=records,
            raw_response=payload,
        )

    def _response(
        self,
        status: EDINETDBStatus,
        issuer_identifier: str,
        *,
        requested_url: str | None = None,
        records: tuple[dict[str, Any], ...] = (),
        raw_response: Any = None,
        detail: str = "",
    ) -> EDINETDBResponse:
        record_metadata = tuple(_record_metadata(record) for record in records)
        return EDINETDBResponse(
            status=status,
            records=records,
            raw_response=raw_response,
            metadata={
                "source": self.name,
                "source_type": self.source_type,
                "fetched_at": datetime.now(UTC).isoformat(),
                "issuer_identifier": issuer_identifier or None,
                "requested_url": requested_url,
                "record_metadata": record_metadata,
            },
            detail=detail,
        )

    @staticmethod
    def _http_status(status_code: int) -> EDINETDBStatus:
        if 200 <= status_code < 300:
            return EDINETDBStatus.OK
        if status_code in {401, 403}:
            return EDINETDBStatus.AUTH_REQUIRED
        if status_code == 429:
            return EDINETDBStatus.RATE_LIMITED
        if status_code == 404:
            return EDINETDBStatus.DATA_UNAVAILABLE
        return EDINETDBStatus.API_ERROR


def _earnings_records(payload: Any) -> tuple[dict[str, Any], ...] | None:
    if not isinstance(payload, Mapping):
        return None
    records = payload.get("data")
    if not isinstance(records, list) or not all(isinstance(record, Mapping) for record in records):
        return None
    return tuple(dict(record) for record in records)


def _record_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    """Expose provider identifiers/URLs without changing any raw record."""
    return {
        "source_record_id": _first_text(record, "source_record_id", "record_id", "id", "document_id"),
        "source_url": _first_text(record, "source_url", "pdf_url", "url"),
    }


def _first_text(record: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None
