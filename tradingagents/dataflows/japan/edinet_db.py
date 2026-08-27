"""Transport-only client for EDINET DB earnings disclosures.

This module deliberately keeps API transport separate from financial
normalization.  It returns the provider's raw earnings records plus immutable
source metadata; ``edinet_db_normalizer`` remains responsible for mapping
those records to ``FinancialDocument`` and ``FinancialRecord``.
"""

from __future__ import annotations

import asyncio
import os
import re
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


class IssuerIdentityStatus(StrEnum):
    """Fail-closed result states for issuer identity resolution."""

    OK = "OK"
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


@dataclass(frozen=True)
class IssuerIdentity:
    """Exact listed-issuer identity resolved from the EDINET DB company master."""

    input_ticker: str
    security_code: str | None
    source_security_code: str | None
    edinet_code: str | None
    listing_status: str | None
    source: str
    source_as_of: str | None
    fetched_at: str
    status: IssuerIdentityStatus
    detail: str = ""


class EDINETDBProvider:
    """Fetch raw earnings disclosures from EDINET DB.

    ``issuer_identifier`` is passed as the API's company ``code`` path value.
    The caller is responsible for resolving a listed security code to an
    EDINET code when that mapping is required.
    """

    name = "EDINET DB"
    source_type = "STRUCTURED_SOURCE"
    identity_source = "EDINET_DB_COMPANY_MASTER"
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

    async def resolve_issuer_identity(self, input_ticker: object) -> IssuerIdentity:
        """Resolve a JP security code through the exact company-master filter.

        The resolver never uses the fuzzy ``/search`` endpoint.  A result is
        accepted only when exactly one response security code normalizes back
        to the requested code, carries a valid EDINET code, and is explicitly
        marked as a currently listed issuer.
        """
        raw_input = str(input_ticker or "")
        security_code = normalize_security_code(input_ticker)
        if security_code is None:
            return self._identity(
                raw_input,
                None,
                detail="INVALID_SECURITY_CODE",
            )
        if not self.api_key.strip():
            return self._identity(
                raw_input,
                security_code,
                detail=EDINETDBStatus.AUTH_REQUIRED.value,
            )

        url = f"{self.base_url}/companies"
        status, payload, detail = await self._get_json(
            url,
            params={"sec_code": security_code, "per_page": 2},
        )
        if status != EDINETDBStatus.OK:
            return self._identity(
                raw_input,
                security_code,
                detail=_identity_transport_detail(status, detail),
            )

        records = _company_records(payload)
        if records is None:
            return self._identity(
                raw_input,
                security_code,
                detail="INVALID_RESPONSE",
            )
        candidates = tuple(
            record
            for record in records
            if normalize_security_code(record.get("sec_code")) == security_code
        )
        if not candidates:
            return self._identity(
                raw_input,
                security_code,
                source_as_of=_source_as_of(payload),
                detail="NO_EXACT_MATCH",
            )
        if len(candidates) != 1:
            return self._identity(
                raw_input,
                security_code,
                source_as_of=_source_as_of(payload),
                detail="AMBIGUOUS_IDENTITY",
            )

        candidate = candidates[0]
        source_security_code = str(candidate.get("sec_code") or "").strip() or None
        edinet_code = str(candidate.get("edinet_code") or "").strip().upper()
        listing_status = str(candidate.get("listing_status") or "").strip().lower()
        is_delisted = _explicit_bool(candidate.get("is_delisted"))
        common = {
            "source_security_code": source_security_code,
            "listing_status": listing_status or None,
            "source_as_of": _source_as_of(payload),
        }
        if _EDINET_CODE.fullmatch(edinet_code) is None:
            return self._identity(
                raw_input,
                security_code,
                detail="INVALID_EDINET_CODE",
                **common,
            )
        if listing_status != "listed" or is_delisted is not False:
            return self._identity(
                raw_input,
                security_code,
                detail="ISSUER_NOT_CURRENTLY_LISTED",
                **common,
            )
        return self._identity(
            raw_input,
            security_code,
            edinet_code=edinet_code,
            status=IssuerIdentityStatus.OK,
            **common,
        )

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
        status, payload, detail = await self._get_json(url, params={"limit": limit})
        if status != EDINETDBStatus.OK:
            return self._response(
                status,
                issuer_code,
                requested_url=url,
                requested_limit=limit,
                detail=detail,
            )

        records, reported_count, envelope_detail = _earnings_records(payload)
        if records is None:
            return self._response(
                EDINETDBStatus.DATA_UNAVAILABLE,
                issuer_code,
                requested_url=url,
                raw_response=payload,
                requested_limit=limit,
                data_as_of=_source_as_of(payload),
                detail=envelope_detail,
            )
        return self._response(
            EDINETDBStatus.OK,
            issuer_code,
            requested_url=url,
            records=records,
            raw_response=payload,
            requested_limit=limit,
            reported_count=reported_count,
            data_as_of=_source_as_of(payload),
            window_may_be_truncated=len(records) >= limit,
        )

    async def _get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any],
    ) -> tuple[EDINETDBStatus, Any, str]:
        """Perform one authenticated JSON GET for EDINET DB transports."""
        try:
            response = await asyncio.to_thread(
                self._http_get,
                url,
                params=dict(params),
                headers={"Accept": "application/json", "X-API-Key": self.api_key},
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            return EDINETDBStatus.TIMEOUT, None, type(exc).__name__
        except requests.RequestException as exc:
            return EDINETDBStatus.API_ERROR, None, type(exc).__name__

        status = self._http_status(response.status_code)
        if status != EDINETDBStatus.OK:
            return status, None, f"HTTP {response.status_code}"
        try:
            return EDINETDBStatus.OK, response.json(), ""
        except (TypeError, ValueError):
            return EDINETDBStatus.DATA_UNAVAILABLE, None, "invalid JSON response"

    def _identity(
        self,
        input_ticker: str,
        security_code: str | None,
        *,
        source_security_code: str | None = None,
        edinet_code: str | None = None,
        listing_status: str | None = None,
        source_as_of: str | None = None,
        status: IssuerIdentityStatus = IssuerIdentityStatus.DATA_UNAVAILABLE,
        detail: str = "",
    ) -> IssuerIdentity:
        return IssuerIdentity(
            input_ticker=input_ticker,
            security_code=security_code,
            source_security_code=source_security_code,
            edinet_code=edinet_code,
            listing_status=listing_status,
            source=self.identity_source,
            source_as_of=source_as_of,
            fetched_at=datetime.now(UTC).isoformat(),
            status=status,
            detail=detail,
        )

    def _response(
        self,
        status: EDINETDBStatus,
        issuer_identifier: str,
        *,
        requested_url: str | None = None,
        records: tuple[dict[str, Any], ...] = (),
        raw_response: Any = None,
        requested_limit: int | None = None,
        reported_count: int | None = None,
        data_as_of: str | None = None,
        window_may_be_truncated: bool | None = None,
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
                "requested_limit": requested_limit,
                "returned_count": len(records),
                "reported_count": reported_count,
                "data_as_of": data_as_of,
                "window_may_be_truncated": window_may_be_truncated,
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


_CANONICAL_SECURITY_CODE = re.compile(r"^(?:\d{4}|\d{3}[A-Z])$")
_EDINET_SECURITY_CODE = re.compile(r"^(?P<canonical>(?:\d{4}|\d{3}[A-Z]))0$")
_EDINET_CODE = re.compile(r"^E\d{5}$")


def normalize_security_code(value: object) -> str | None:
    """Return a canonical four-character JP security code, without guessing."""
    raw = str(value or "").strip().upper()
    if raw.endswith(".T"):
        raw = raw[:-2]
    extended = _EDINET_SECURITY_CODE.fullmatch(raw)
    if extended is not None:
        raw = extended.group("canonical")
    return raw if _CANONICAL_SECURITY_CODE.fullmatch(raw) is not None else None


def _company_records(payload: Any) -> tuple[dict[str, Any], ...] | None:
    if not isinstance(payload, Mapping):
        return None
    records = payload.get("data")
    if not isinstance(records, list) or not all(isinstance(record, Mapping) for record in records):
        return None
    return tuple(dict(record) for record in records)


def _source_as_of(payload: Any) -> str | None:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("meta"), Mapping):
        return None
    value = payload["meta"].get("data_as_of")
    return str(value) if value is not None and str(value).strip() else None


def _explicit_bool(value: Any) -> bool | None:
    if value is True or value == 1:
        return True
    if value is False or value == 0:
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    return None


def _identity_transport_detail(status: EDINETDBStatus, detail: str) -> str:
    if status == EDINETDBStatus.DATA_UNAVAILABLE and detail == "invalid JSON response":
        return "INVALID_RESPONSE"
    if detail == "HTTP 400":
        return EDINETDBStatus.DATA_UNAVAILABLE.value
    return status.value


def _earnings_records(
    payload: Any,
) -> tuple[tuple[dict[str, Any], ...] | None, int | None, str]:
    if not isinstance(payload, Mapping):
        return None, None, "INVALID_EARNINGS_PAYLOAD"
    if "data" not in payload:
        return None, None, "MISSING_EARNINGS_DATA"

    data = payload["data"]
    if not isinstance(data, Mapping):
        return None, None, "INVALID_EARNINGS_DATA"
    if "earnings" not in data:
        return None, _reported_count(data), "MISSING_EARNINGS_RECORDS"

    records = data["earnings"]
    if not isinstance(records, list):
        return None, _reported_count(data), "INVALID_EARNINGS_RECORDS"
    if not all(isinstance(record, Mapping) for record in records):
        return None, _reported_count(data), "INVALID_EARNINGS_RECORD"
    return tuple(dict(record) for record in records), _reported_count(data), ""


def _reported_count(data: Mapping[str, Any]) -> int | None:
    value = data.get("count")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


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
