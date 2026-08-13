"""EDINET API V2 disclosures and large-shareholding metadata provider."""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .http import get_bytes, get_json
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus


class EDINETProvider:
    """Fetch document-list metadata only; full XBRL/PDF parsing is intentionally separate."""

    name = "EDINET"
    category = "edinet"
    cache_version = "holdings-pdf-v2"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, max_days: int = 7):
        config = get_config().get("markets", {}).get("jp", {})
        self.api_key = api_key if api_key is not None else os.getenv("EDINET_API_KEY", "")
        self.base_url = (base_url or config.get("edinet_api_base_url") or "https://api.edinet-fsa.go.jp/api/v2").rstrip("/")
        self.timeout = float(config.get("request_timeout_seconds", 10))
        self.max_days = max_days
        self.enabled = bool(config.get("datasources", {}).get("edinet", True))
        self.max_pdf_bytes = int(config.get("edinet_max_pdf_bytes", 8_000_000))
        self.max_pdf_chars = int(config.get("edinet_max_pdf_chars", 12_000))

    async def fetch(self, context: MarketContext, *, start_date: str, end_date: str) -> ProviderResponse:
        if not self.enabled:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DISABLED, detail="disabled by markets.jp.datasources"))
        if not self.api_key:
            return ProviderResponse(SourceStatus(self.name, DataStatus.AUTH_REQUIRED, detail="EDINET_API_KEY is not configured"))
        try:
            dates = _date_range(start_date, end_date, self.max_days)
        except ValueError:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DATA_UNAVAILABLE, detail="invalid date range"))
        items: list[MarketInformation] = []
        latest_status = DataStatus.OK
        details: list[str] = []
        for requested_date in dates:
            response = await get_json(
                f"{self.base_url}/documents.json",
                params={"date": requested_date.isoformat(), "type": "2", "Subscription-Key": self.api_key},
                timeout=self.timeout,
            )
            if response.status != DataStatus.OK:
                latest_status = response.status
                details.append(response.detail)
                continue
            items.extend(_normalise_documents(response.payload, context))
        if not items and latest_status != DataStatus.OK:
            return ProviderResponse(SourceStatus(self.name, latest_status, detail="; ".join(sorted(set(details)))))
        enriched = await __import__("asyncio").gather(*(self._enrich_large_holding(item) for item in items))
        return ProviderResponse(SourceStatus(self.name, DataStatus.OK, item_count=len(enriched)), tuple(enriched))

    async def _enrich_large_holding(self, item: MarketInformation) -> MarketInformation:
        if item.source_type != "large_shareholding_report" or not item.url:
            return item
        response = await get_bytes(item.url, timeout=self.timeout, max_bytes=self.max_pdf_bytes, headers={"Subscription-Key": self.api_key})
        if response.status != DataStatus.OK:
            return _with_document_status(item, "DATA_UNAVAILABLE", response.detail)
        try:
            from .tdnet import extract_pdf_text
            text = extract_pdf_text(response.payload, self.max_pdf_chars)
        except Exception as exc:
            return _with_document_status(item, "PARSE_FAILED", type(exc).__name__)
        fields = extract_large_shareholding_fields(text)
        return MarketInformation(**{**item.__dict__, "content": text or item.content, "content_level": "full_text" if text else "metadata_only", "metadata": {**item.metadata, **fields, "extraction_status": "OK" if text else "EMPTY_TEXT"}})


def _date_range(start_date: str, end_date: str, max_days: int) -> tuple[date, ...]:
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if end < start:
        raise ValueError("end date precedes start date")
    days = (end - start).days + 1
    if days > max_days:
        start = end - timedelta(days=max_days - 1)
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _documents(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        documents = payload.get("results") or payload.get("documents") or []
        if isinstance(documents, list):
            return (item for item in documents if isinstance(item, dict))
    return ()


def _normalise_documents(payload: Any, context: MarketContext) -> Iterable[MarketInformation]:
    for document in _documents(payload):
        code = str(document.get("secCode") or document.get("secCode") or "").rstrip("0")
        if code != context.native_symbol:
            continue
        title = str(document.get("docDescription") or document.get("docTypeCode") or "EDINET filing")
        doc_id = str(document.get("docID") or "")
        submitted = str(document.get("submitDateTime") or "")
        try:
            timestamp = datetime.fromisoformat(submitted.replace("Z", "+00:00"))
        except ValueError:
            timestamp = datetime.now(UTC)
        category = classify_edinet_document(title, str(document.get("docTypeCode") or ""))
        yield MarketInformation(
            source="EDINET",
            source_type=category,
            ticker=context.symbol,
            timestamp=timestamp,
            title=title,
            content="EDINET filing metadata; document text has not been extracted.",
            url=f"https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}?type=1" if doc_id else None,
            confidence=0.98,
            verified=True,
            layer=InformationLayer.VERIFIED_FACT,
            content_level="metadata_only",
            metadata={
                "doc_id": doc_id,
                "doc_type_code": document.get("docTypeCode"),
                "edinet_code": document.get("edinetCode"),
                "filer_name": document.get("filerName"),
            },
        )


def classify_edinet_document(title: str, doc_type_code: str) -> str:
    normalized = title.replace(" ", "")
    if "大量保有" in normalized or doc_type_code in {"120", "130"}:
        return "large_shareholding_report"
    if "有価証券報告書" in normalized:
        return "securities_report"
    if "四半期報告書" in normalized:
        return "quarterly_report"
    return "regulatory_filing"


def extract_large_shareholding_fields(text: str) -> dict[str, Any]:
    """Extract only explicit holding percentages; never infer a position change."""
    import re

    percentages = [float(value) for value in re.findall(r"(?:保有割合|株券等保有割合)[^0-9]{0,20}([0-9]+(?:\.[0-9]+)?)\s*%", text)]
    return {"reported_holding_percentages": percentages[:8], "position_change": "UNDETERMINED"}


def _with_document_status(item: MarketInformation, status: str, detail: str) -> MarketInformation:
    return MarketInformation(**{**item.__dict__, "metadata": {**item.metadata, "extraction_status": status, "extraction_detail": detail}})
