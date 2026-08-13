"""EDINET API V2 disclosures and large-shareholding metadata provider."""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .http import get_json
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus


class EDINETProvider:
    """Fetch document-list metadata only; full XBRL/PDF parsing is intentionally separate."""

    name = "EDINET"
    category = "edinet"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, max_days: int = 7):
        config = get_config().get("markets", {}).get("jp", {})
        self.api_key = api_key if api_key is not None else os.getenv("EDINET_API_KEY", "")
        self.base_url = (base_url or config.get("edinet_api_base_url") or "https://api.edinet-fsa.go.jp/api/v2").rstrip("/")
        self.timeout = float(config.get("request_timeout_seconds", 10))
        self.max_days = max_days
        self.enabled = bool(config.get("datasources", {}).get("edinet", True))

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
        return ProviderResponse(SourceStatus(self.name, DataStatus.OK, item_count=len(items)), tuple(items))


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
