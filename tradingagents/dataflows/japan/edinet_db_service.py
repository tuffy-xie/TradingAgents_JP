"""High-level orchestration for EDINET DB transport and financial normalization.

The service deliberately does not register a provider, mutate a Japan bundle,
or inject data into agents.  It connects the already-isolated transport,
normalizer, and strict guidance matcher only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .edinet_db import EDINETDBProvider, EDINETDBResponse, EDINETDBStatus
from .edinet_db_normalizer import normalize_financial_document
from .financial_disclosure import DATA_UNAVAILABLE, FinancialDocument, FinancialRecord
from .guidance_matcher import GuidanceRecordContext, match_guidance_revision


@dataclass(frozen=True)
class GuidanceRecordIdentifier:
    """Exact upstream identity for one EDINET DB earnings record.

    The service never treats a company name, date, or list position as an
    identifier.  ``source_record_id`` must match an explicit ID in the raw
    record returned by EDINET DB.
    """

    issuer_id: str
    source_record_id: str


class EDINETDBFinancialService:
    """Connect EDINET DB transport to the existing normalization contracts."""

    def __init__(self, provider: EDINETDBProvider) -> None:
        self.provider = provider

    async def fetch_financial_document(
        self, issuer_id: str
    ) -> FinancialDocument | EDINETDBResponse:
        """Return the first provider-returned earnings document after normalization.

        Transport failures are returned unchanged so callers retain their exact
        ``AUTH_REQUIRED``, ``TIMEOUT``, ``RATE_LIMITED``, or ``API_ERROR``
        semantics.  The API response is not sorted or re-ranked here.
        """
        response = await self.provider.fetch_earnings(issuer_id)
        if response.status != EDINETDBStatus.OK:
            return response
        if not response.records:
            return _unavailable_document(
                response,
                detail="earnings response contains no records",
            )
        return _normalize_record(response, response.records[0])

    async def fetch_guidance_revision(
        self,
        previous_identifier: GuidanceRecordIdentifier,
        current_identifier: GuidanceRecordIdentifier,
    ) -> dict[str, Any] | EDINETDBResponse:
        """Match two explicitly identified normalized GUIDANCE records.

        This method makes no attempt to discover a prior record by date,
        company name, or list order.  Each identifier is selected by its exact
        raw provider record ID before entering the normalizer.
        """
        previous = await self._fetch_guidance_context(previous_identifier)
        if isinstance(previous, EDINETDBResponse):
            return previous
        if isinstance(previous, dict):
            return previous

        current = await self._fetch_guidance_context(current_identifier)
        if isinstance(current, EDINETDBResponse):
            return current
        if isinstance(current, dict):
            return current

        return match_guidance_revision(previous, current)

    async def _fetch_guidance_context(
        self,
        identifier: GuidanceRecordIdentifier,
    ) -> GuidanceRecordContext | EDINETDBResponse | dict[str, Any]:
        response = await self.provider.fetch_earnings(identifier.issuer_id)
        if response.status != EDINETDBStatus.OK:
            return response

        raw_record = _find_source_record(response.records, identifier.source_record_id)
        if raw_record is None:
            return _failure("requested source record ID is not present in provider response")

        document = _normalize_record(response, raw_record)
        if document.status != "OK":
            return _failure("normalization failed for requested source record")
        guidance = _first_guidance_record(document.records)
        if guidance is None:
            return _failure("requested source record has no normalized GUIDANCE record")

        return GuidanceRecordContext(
            record=guidance,
            issuer_id=identifier.issuer_id,
            source_record_id=identifier.source_record_id,
            disclosure_timestamp=document.disclosure_timestamp,
            metadata={"is_correction": raw_record.get("is_correction")},
        )


def _normalize_record(
    response: EDINETDBResponse,
    raw_record: Mapping[str, Any],
) -> FinancialDocument:
    record = _with_source_metadata(raw_record, response)
    try:
        return normalize_financial_document(
            record,
            fetched_at=_text_or_none(response.metadata.get("fetched_at")),
        )
    except Exception as exc:
        return _unavailable_document(
            response,
            raw_record=record,
            detail=f"normalization failed: {type(exc).__name__}",
        )


def _with_source_metadata(
    raw_record: Mapping[str, Any],
    response: EDINETDBResponse,
) -> dict[str, Any]:
    """Propagate explicit provider metadata without manufacturing facts."""
    record = dict(raw_record)
    source_metadata = _source_metadata_for_record(raw_record, response.metadata)
    if "source_record_id" not in record and source_metadata.get("source_record_id"):
        record["source_record_id"] = source_metadata["source_record_id"]
    if not _first_text(record, "source_url", "source_pdf_url", "pdf_url") and source_metadata.get(
        "source_url"
    ):
        record["source_url"] = source_metadata["source_url"]
    return record


def _source_metadata_for_record(
    raw_record: Mapping[str, Any],
    response_metadata: Mapping[str, Any],
) -> Mapping[str, Any]:
    source_id = _source_record_id(raw_record)
    for metadata in response_metadata.get("record_metadata", ()):
        if isinstance(metadata, Mapping) and metadata.get("source_record_id") == source_id:
            return metadata
    return {}


def _find_source_record(
    records: tuple[dict[str, Any], ...],
    source_record_id: str,
) -> dict[str, Any] | None:
    expected = str(source_record_id or "").strip()
    if not expected:
        return None
    for record in records:
        if _source_record_id(record) == expected:
            return record
    return None


def _source_record_id(record: Mapping[str, Any]) -> str | None:
    return _first_text(record, "source_record_id", "record_id", "id", "document_id")


def _first_guidance_record(records: tuple[FinancialRecord, ...]) -> FinancialRecord | None:
    return next((record for record in records if record.record_type == "GUIDANCE"), None)


def _unavailable_document(
    response: EDINETDBResponse,
    *,
    raw_record: Mapping[str, Any] | None = None,
    detail: str,
) -> FinancialDocument:
    record = raw_record or {}
    return FinancialDocument(
        status=DATA_UNAVAILABLE,
        source="EDINET DB",
        title=_text_or_none(record.get("title")) or "EDINET DB earnings record",
        disclosure_timestamp=_text_or_none(
            record.get("disclosure_timestamp") or record.get("disclosure_date")
        ),
        source_url=_first_text(record, "source_url", "source_pdf_url", "pdf_url"),
        accounting_standard="UNKNOWN",
        source_type="STRUCTURED_SOURCE",
        fetched_at=_text_or_none(response.metadata.get("fetched_at")),
        source_as_of=_text_or_none(record.get("source_as_of")),
        source_record_id=_source_record_id(record),
    )


def _failure(reason: str) -> dict[str, Any]:
    return {"status": DATA_UNAVAILABLE, "reason": reason, "metrics": {}}


def _first_text(record: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
