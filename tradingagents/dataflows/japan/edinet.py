"""EDINET API V2 provider for large-shareholding and change reports.

EDINET's document list often omits ``secCode`` for a large-shareholding filing.
Such filings are kept as *candidates* only until their official PDF confirms
the issuer security code.  This prevents a filing for another issuer from ever
entering a target stock's Japan bundle.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .http import get_bytes, get_json
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus

_HOLDING_FORM_CODES = frozenset({"010000", "010002", "020002", "090001"})
_HOLDING_DOC_TYPES = frozenset({"350", "360"})


class EDINETProvider:
    """Fetch and verify EDINET 5%-rule filings for a Japan ticker only."""

    name = "EDINET"
    category = "edinet"
    cache_version = "v2-large-holdings-structured-v3"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, max_days: int = 7):
        config = get_config().get("markets", {}).get("jp", {})
        self.api_key = api_key if api_key is not None else os.getenv("EDINET_API_KEY", "")
        self.base_url = (base_url or config.get("edinet_api_base_url") or "https://api.edinet-fsa.go.jp/api/v2").rstrip("/")
        self.timeout = float(config.get("request_timeout_seconds", 10))
        self.max_days = max_days
        self.enabled = bool(config.get("datasources", {}).get("edinet", True))
        self.max_pdf_bytes = int(config.get("edinet_max_pdf_bytes", 8_000_000))
        self.max_pdf_chars = int(config.get("edinet_max_pdf_chars", 12_000))
        # The public daily index contains many unrelated 5%-rule reports with
        # no secCode. Bound PDF work while still allowing a caller to increase
        # the limit explicitly for a historical audit.
        self.max_candidates = int(config.get("edinet_max_holding_candidates", 80))

    async def fetch(self, context: MarketContext, *, start_date: str, end_date: str) -> ProviderResponse:
        if not self.enabled:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DISABLED, detail="disabled by markets.jp.datasources"))
        if not self.api_key:
            return ProviderResponse(SourceStatus(self.name, DataStatus.AUTH_REQUIRED, detail="EDINET_API_KEY is not configured"))
        try:
            dates = _date_range(start_date, end_date, self.max_days)
        except ValueError:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DATA_UNAVAILABLE, detail="invalid date range"))

        listed: list[MarketInformation] = []
        failed_statuses: list[DataStatus] = []
        details: list[str] = []
        for requested_date in dates:
            response = await get_json(
                f"{self.base_url}/documents.json",
                params={"date": requested_date.isoformat(), "type": "2", "Subscription-Key": self.api_key},
                timeout=self.timeout,
            )
            if response.status != DataStatus.OK:
                failed_statuses.append(response.status)
                details.append(response.detail)
                continue
            listed.extend(_normalise_documents(response.payload, context))

        direct = [item for item in listed if not item.metadata.get("requires_pdf_security_match")]
        candidates = [item for item in listed if item.metadata.get("requires_pdf_security_match")][: self.max_candidates]
        enriched = await self._enrich_all([*direct, *candidates])
        confirmed = [item for item in enriched if _matches_context(item, context)]
        status = DataStatus.OK if listed or not failed_statuses else _worst_status(failed_statuses)
        detail_parts = [f"document_list_days={len(dates)}", f"holding_candidates={len(candidates)}", f"confirmed={len(confirmed)}"]
        if len(listed) - len(direct) > len(candidates):
            detail_parts.append(f"candidate_limit={self.max_candidates}")
        if details:
            detail_parts.extend(sorted(set(details)))
        return ProviderResponse(SourceStatus(self.name, status, detail="; ".join(detail_parts), item_count=len(confirmed)), tuple(confirmed))

    async def _enrich_all(self, items: list[MarketInformation]) -> list[MarketInformation]:
        semaphore = asyncio.Semaphore(4)

        async def enrich(item: MarketInformation) -> MarketInformation:
            async with semaphore:
                return await self._enrich_large_holding(item)

        return list(await asyncio.gather(*(enrich(item) for item in items)))

    async def _enrich_large_holding(self, item: MarketInformation) -> MarketInformation:
        if item.source_type != "large_shareholding_report" or not item.url:
            return item
        response = await get_bytes(
            item.url, params={"type": "2", "Subscription-Key": self.api_key},
            timeout=self.timeout, max_bytes=self.max_pdf_bytes,
        )
        if response.status != DataStatus.OK:
            return _with_document_status(item, DataStatus.DATA_UNAVAILABLE, response.detail)
        if not response.payload.startswith(b"%PDF"):
            return _with_document_status(item, DataStatus.PARSE_FAILED, "document response is not a PDF")
        try:
            from .tdnet import extract_pdf_text

            text = extract_pdf_text(response.payload, self.max_pdf_chars)
        except Exception as exc:  # parser failure preserves all document metadata
            return _with_document_status(item, DataStatus.PARSE_FAILED, type(exc).__name__)
        if not text.strip():
            return _with_document_status(item, DataStatus.PARSE_FAILED, "empty extracted PDF text")
        fields = extract_large_shareholding_fields(text)
        return MarketInformation(
            **{
                **item.__dict__,
                "content": text,
                "content_level": "full_text",
                "metadata": {**item.metadata, **fields, "extraction_status": "OK"},
            }
        )


def _date_range(start_date: str, end_date: str, max_days: int) -> tuple[date, ...]:
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if end < start:
        raise ValueError("end date precedes start date")
    if (end - start).days + 1 > max_days:
        start = end - timedelta(days=max_days - 1)
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _documents(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, Mapping):
        documents = payload.get("results") or payload.get("documents") or []
        if isinstance(documents, list):
            return (item for item in documents if isinstance(item, dict))
    return ()


def _normalise_documents(payload: Any, context: MarketContext) -> Iterable[MarketInformation]:
    """Keep direct matches plus relevant no-code reports for PDF verification."""
    for document in _documents(payload):
        title = str(document.get("docDescription") or document.get("docTypeCode") or "EDINET filing")
        category = classify_edinet_document(title, str(document.get("docTypeCode") or ""), str(document.get("formCode") or ""))
        if category != "large_shareholding_report":
            continue
        raw_code = str(document.get("secCode") or "")
        code = raw_code.rstrip("0") if raw_code else ""
        if code and code != context.native_symbol:
            continue
        doc_id = str(document.get("docID") or "")
        submitted = str(document.get("submitDateTime") or "")
        yield MarketInformation(
            source="EDINET", source_type=category, ticker=context.symbol,
            timestamp=_parse_timestamp(submitted), title=title,
            content="EDINET 5%-rule filing metadata; PDF verification pending.",
            url=f"https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}" if doc_id else None,
            confidence=0.98, verified=True, layer=InformationLayer.VERIFIED_FACT,
            content_level="metadata_only",
            metadata={
                "submit_date": submitted[:10] or None,
                "event_date": None,
                "holder_name": str(document.get("filerName") or "") or None,
                "issuer_name": None,
                "security_code": code or None,
                "current_holding_ratio": None,
                "previous_holding_ratio": None,
                "position_change": "UNDETERMINED",
                "purpose_of_holding": None,
                "document_id": doc_id or None,
                "source_url": f"https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}?type=2" if doc_id else None,
                "doc_type_code": document.get("docTypeCode"),
                "form_code": document.get("formCode"),
                "edinet_code": document.get("edinetCode"),
                "issuer_edinet_code": document.get("issuerEdinetCode"),
                "pdf_available": str(document.get("pdfFlag") or "") == "1",
                "xbrl_available": str(document.get("xbrlFlag") or "") == "1",
                "requires_pdf_security_match": not bool(code),
                "extraction_status": "PENDING",
            },
        )


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)


def classify_edinet_document(title: str, doc_type_code: str, form_code: str = "") -> str:
    normalized = title.replace(" ", "")
    if (doc_type_code in _HOLDING_DOC_TYPES and form_code in _HOLDING_FORM_CODES) or "大量保有" in normalized or "変更報告書" in normalized:
        return "large_shareholding_report"
    if "有価証券報告書" in normalized:
        return "securities_report"
    if "四半期報告書" in normalized:
        return "quarterly_report"
    return "regulatory_filing"


def extract_large_shareholding_fields(text: str) -> dict[str, Any]:
    """Extract only fields explicitly stated in an EDINET 5%-rule PDF."""
    compact = re.sub(r"[ \t\r\n]+", " ", text).replace("％", "%")
    submit = _date_value(_capture(compact, r"提出日[^0-9]{0,20}(20\d{2}年\d{1,2}月\d{1,2}日)"))
    event = _date_value(_capture(compact, r"報告義務発生日[^0-9]{0,20}(20\d{2}年\d{1,2}月\d{1,2}日)"))
    issuer = _capture(compact, r"発行者の名称\s*(.+?)\s*証券コード")
    security = _capture(compact, r"証券コード\s*([0-9]{4}|[0-9]{3}[A-Z])")
    # Prefer the named holder in the "提出者（大量保有者）" block over the
    # cover-page filing agent's name.
    holder = _capture(compact, r"提出者.*?氏名又は名称\s*(.+?)\s*住所又は本店所在地")
    purpose = _capture(compact, r"保有目的[^\S\r\n]*(?:】)?\s*(.+?)(?=\s*[（(][3３][）)]|\s*重要提案行為等)")
    current = _last_ratio_before(compact, "直前の報告書に記載された")
    previous = _capture_ratio_after(compact, r"直前の報告書に記載された\s*株券等保有割合[^%]{0,120}")
    change = _position_change(current, previous)
    return {
        "submit_date": submit,
        "event_date": event,
        "holder_name": _clean(holder),
        "issuer_name": _clean(issuer),
        "security_code": security or None,
        "current_holding_ratio": current,
        "previous_holding_ratio": previous,
        "position_change": change,
        "purpose_of_holding": _clean(purpose),
    }


def _capture(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, re.S)
    return match.group(1).strip() if match else None


def _capture_ratio_after(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text, re.S)
    if not match:
        return None
    tail = text[match.end():]
    # The following section starts at item (5); keep the previous-report
    # percentage local so dates or later transaction quantities cannot win.
    tail = re.split(r"[（(][5５][）)]", tail, maxsplit=1)[0][:300]
    values = re.findall(r"(?<![0-9])([0-9]+(?:\.[0-9]+)?)", tail)
    return float(values[-1]) if values else None


def _last_ratio_before(text: str, marker: str) -> float | None:
    prefix = text.split(marker, 1)[0]
    headings = list(re.finditer(r"上記提出者(?:及び共同保有者)?の株券等保有割合", prefix))
    if not headings:
        # Small fixtures and some compact PDF layouts omit the full heading.
        # This fallback intentionally requires the numeric value immediately
        # after the label, so it does not treat prose such as "100分の5" as a
        # reported ratio.
        match = re.search(r"株券等保有割合(?:[（(][^）)]*[）)])?\s*([0-9]+(?:\.[0-9]+)?)", prefix)
        return float(match.group(1)) if match else None
    values = re.findall(r"(?<![0-9])([0-9]+(?:\.[0-9]+)?)", prefix[headings[-1].end():])
    return float(values[-1]) if values else None


def _date_value(value: str | None) -> str | None:
    if not value:
        return None
    match = re.fullmatch(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", value)
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}" if match else None


def _position_change(current: float | None, previous: float | None) -> str:
    if current is None or previous is None:
        return "UNDETERMINED"
    if current > previous:
        return "INCREASED"
    if current < previous:
        return "DECREASED"
    return "UNCHANGED"


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip(" ：:")
    return cleaned[:200] if cleaned else None


def _matches_context(item: MarketInformation, context: MarketContext) -> bool:
    metadata = item.metadata
    return str(metadata.get("security_code") or "") == context.native_symbol


def _worst_status(statuses: Iterable[DataStatus]) -> DataStatus:
    values = set(statuses)
    for candidate in (DataStatus.AUTH_REQUIRED, DataStatus.RATE_LIMITED, DataStatus.PARSE_FAILED, DataStatus.DATA_UNAVAILABLE):
        if candidate in values:
            return candidate
    return DataStatus.DATA_UNAVAILABLE


def _with_document_status(item: MarketInformation, status: DataStatus, detail: str) -> MarketInformation:
    return MarketInformation(
        **{
            **item.__dict__,
            "status": status,
            "metadata": {**item.metadata, "extraction_status": status.value, "extraction_detail": detail},
        }
    )
