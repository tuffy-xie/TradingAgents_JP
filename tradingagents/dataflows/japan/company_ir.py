"""Company IR discovery from a public company website, with honest extraction state."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from html import unescape
from urllib.parse import urljoin

import yfinance as yf

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .http import get_bytes, get_text
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus
from .tdnet import extract_pdf_text

_IR_LINK = re.compile(r"href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<label>.*?)</a>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_IR_TERMS = ("investor relations", "ir情報", "irニュース", "投資家情報", "株主・投資家")
_DOCUMENT_TERMS = (
    "決算", "financial results", "earnings", "決算短信", "決算説明", "説明会", "presentation",
    "業績", "financial report", "annual report", "統合報告", "有価証券", "ir news", "適時開示",
)
_DATE = re.compile(r"(20\d{2})[./年-](\d{1,2})[./月-](\d{1,2})")


class CompanyIRProvider:
    """Discover and extract recent public IR/earnings documents, bounded politely."""

    name = "Company IR"
    category = "fundamentals"
    cache_version = "public-documents-v2"

    def __init__(self, urls: dict[str, str] | None = None):
        config = get_config().get("markets", {}).get("jp", {})
        self.urls = dict(urls if urls is not None else config.get("company_ir_urls", {}))
        self.timeout = float(config.get("request_timeout_seconds", 10))
        self.enabled = bool(config.get("datasources", {}).get("company_ir", True))
        self.max_documents = int(config.get("company_ir_max_documents", 6))
        self.max_pdf_bytes = int(config.get("company_ir_max_pdf_bytes", 8_000_000))
        self.max_pdf_chars = int(config.get("company_ir_max_pdf_chars", 12_000))

    async def fetch(self, context: MarketContext, *, start_date: str, end_date: str) -> ProviderResponse:
        if not self.enabled:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DISABLED, detail="disabled by markets.jp.datasources"))
        url = self.urls.get(context.native_symbol) or self.urls.get(context.symbol)
        if not url:
            url = await self._website_from_yfinance(context.symbol)
        if not url:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DATA_UNAVAILABLE, detail="company IR URL could not be resolved"))
        status, html, detail = await get_text(url, timeout=self.timeout)
        if status != DataStatus.OK:
            return ProviderResponse(SourceStatus(self.name, status, detail=detail))
        ir_url = _find_ir_url(html, url)
        if not ir_url:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DATA_UNAVAILABLE, detail="no public IR landing-page link found"))
        status, ir_html, detail = await get_text(ir_url, timeout=self.timeout)
        if status != DataStatus.OK:
            return ProviderResponse(SourceStatus(self.name, status, detail=detail))
        documents = _find_document_links(ir_html, ir_url, self.max_documents)
        # IR landing pages often link to a current-results index rather than
        # directly to its PDF. Follow at most three such public index pages,
        # then extract their newest qualifying documents.
        index_pages = [link for link, _label in documents if not _is_pdf(link)][:3]
        expanded_pages = await asyncio.gather(*(get_text(link, timeout=self.timeout) for link in index_pages))
        for page_url, (page_status, page_html, _detail) in zip(index_pages, expanded_pages, strict=False):
            if page_status == DataStatus.OK:
                documents.extend(_find_document_links(page_html, page_url, self.max_documents))
        documents = _deduplicate_documents(documents, self.max_documents)
        document_items = await asyncio.gather(*(
            self._fetch_document(context, link, label, start_date, end_date) for link, label in documents
        ))
        items = tuple(item for item in document_items if item is not None)
        if not items:
            # Keep a landing-page fact only as a transparent fallback, never as
            # a substitute for a scanned financial release.
            items = (MarketInformation(
                source=self.name,
                source_type="company_ir_landing_page",
                ticker=context.symbol,
                timestamp=datetime.now(UTC),
                title="公司 IR 信息页（未发现可解析的最新决算/IR文档）",
                content=_visible_text(ir_html)[:8000],
                url=ir_url,
                confidence=0.95,
                verified=True,
                layer=InformationLayer.VERIFIED_FACT,
                content_level="full_text",
                metadata={"extraction_status": "landing_page_only"},
            ),)
        return ProviderResponse(SourceStatus(self.name, DataStatus.OK, item_count=len(items)), items)

    async def _fetch_document(
        self, context: MarketContext, url: str, label: str, start_date: str, end_date: str
    ) -> MarketInformation | None:
        del start_date, end_date  # The public IR index is ordered newest-first; retain latest documents.
        timestamp = _document_date(label) or datetime.now(UTC)
        if _is_pdf(url):
            response = await get_bytes(url, timeout=self.timeout, max_bytes=self.max_pdf_bytes)
            if response.status != DataStatus.OK:
                return None
            text = extract_pdf_text(response.payload, self.max_pdf_chars)
            if not text:
                return MarketInformation(
                    source=self.name, source_type="company_ir_document", ticker=context.symbol,
                    timestamp=timestamp, title=_document_title(label), content="",
                    url=url, confidence=0.95, verified=True, layer=InformationLayer.VERIFIED_FACT,
                    content_level="summary_only", metadata={"extraction_status": "pdf_parse_failed"},
                )
            return MarketInformation(
                source=self.name, source_type="company_ir_document", ticker=context.symbol,
                timestamp=timestamp, title=_document_title(label), content=text,
                url=url, confidence=0.95, verified=True, layer=InformationLayer.VERIFIED_FACT,
                content_level="full_text", metadata={"extraction_status": "pdf_text", "document_type": _document_type(label)},
            )
        status, html, _detail = await get_text(url, timeout=self.timeout)
        if status != DataStatus.OK:
            return None
        return MarketInformation(
            source=self.name, source_type="company_ir_document", ticker=context.symbol,
            timestamp=timestamp, title=_document_title(label), content=_visible_text(html)[:self.max_pdf_chars],
            url=url, confidence=0.95, verified=True, layer=InformationLayer.VERIFIED_FACT,
            content_level="full_text", metadata={"extraction_status": "html_text", "document_type": _document_type(label)},
        )

    @staticmethod
    async def _website_from_yfinance(symbol: str) -> str | None:
        def resolve() -> str | None:
            try:
                website = (yf.Ticker(symbol).info or {}).get("website")
                return website if isinstance(website, str) and website.startswith("http") else None
            except Exception:
                return None

        import asyncio

        return await asyncio.to_thread(resolve)


def _find_ir_url(html: str, base_url: str) -> str | None:
    for match in _IR_LINK.finditer(_without_script_markup(html)):
        label = _visible_text(match.group("label")).lower()
        href = unescape(match.group("href"))
        if any(term in label for term in _IR_TERMS):
            return urljoin(base_url, href)
    return None


def _find_document_links(html: str, base_url: str, limit: int) -> list[tuple[str, str]]:
    """Return latest IR/earnings links without blindly crawling a site."""
    selected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for match in _IR_LINK.finditer(_without_script_markup(html)):
        label = _visible_text(match.group("label"))
        href = urljoin(base_url, unescape(match.group("href")))
        if not href.startswith(("https://", "http://")):
            continue
        haystack = f"{label} {href}".lower()
        if not any(term in haystack for term in _DOCUMENT_TERMS):
            continue
        if href in seen:
            continue
        seen.add(href)
        selected.append((href, label or "公司 IR 文件"))
        if len(selected) >= limit:
            break
    return selected


def _deduplicate_documents(documents: list[tuple[str, str]], limit: int) -> list[tuple[str, str]]:
    seen: set[str] = set()
    result = []
    for url, label in sorted(documents, key=lambda entry: (not _is_pdf(entry[0]),)):
        if url in seen:
            continue
        seen.add(url)
        result.append((url, label))
        if len(result) >= limit:
            break
    return result


def _is_pdf(url: str) -> bool:
    return url.lower().split("?", 1)[0].endswith(".pdf")


def _without_script_markup(html: str) -> str:
    return re.sub(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>", "", html, flags=re.IGNORECASE | re.DOTALL)


def _document_date(label: str) -> datetime | None:
    match = _DATE.search(label)
    if not match:
        return None
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=UTC)
    except ValueError:
        return None


def _document_type(label: str) -> str:
    lowered = label.lower()
    if "決算" in label or "earnings" in lowered or "financial results" in lowered:
        return "earnings_release"
    if "説明" in label or "presentation" in lowered:
        return "earnings_presentation"
    return "ir_document"


def _document_title(label: str) -> str:
    return label or "公司 IR 文件"


def _visible_text(html: str) -> str:
    return " ".join(unescape(_TAG.sub(" ", html)).split())
