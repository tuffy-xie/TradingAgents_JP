"""Public Company-IR document discovery and conservative official extraction."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from html import unescape
from typing import Iterable
from urllib.parse import urljoin, urlparse

import yfinance as yf

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .http import get_bytes, get_text
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus
from .tdnet import extract_pdf_text, structure_official_disclosure

_IR_LINK = re.compile(r"href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<label>.*?)</a>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_IR_TERMS = ("investor relations", "ir情報", "irニュース", "投資家情報", "株主・投資家", "ir library")
_DOCUMENT_TERMS = (
    "決算", "financial results", "earnings", "quarterly", "業績", "配当", "自己株", "自社株",
    "share repurchase", "buyback", "acquisition", "増資", "new shares", "merger", "合併", "買収",
    "m&a", "受注", "contract", "契約", "restructur", "再編", "適時開示", "news release",
)
_LISTING_TERMS = ("決算", "financial", "earnings", "ir news", "ニュース", "library", "資料", "archive", "一覧")
_DATE = re.compile(r"(?P<year>20\d{2})[./年\-](?P<month>\d{1,2})[./月\-](?P<day>\d{1,2})")
_COMPACT_DATE = re.compile(r"(?P<year>20\d{2})(?P<month>\d{2})(?P<day>\d{2})")


class CompanyIRProvider:
    """Discover public issuer IR documents; a reachable landing page is not a fact.

    No login, cookies or search-result scraping is used.  The provider follows
    issuer-published IR navigation and keeps document metadata when PDF text
    extraction fails.
    """

    name = "Company IR"
    category = "fundamentals"
    cache_version = "public-document-discovery-v3"

    def __init__(self, urls: dict[str, str] | None = None):
        config = get_config().get("markets", {}).get("jp", {})
        self.urls = dict(urls if urls is not None else config.get("company_ir_urls", {}))
        self.timeout = float(config.get("request_timeout_seconds", 10))
        self.max_documents = int(config.get("company_ir_max_documents", 6))
        self.max_pdf_bytes = int(config.get("company_ir_max_pdf_bytes", 8_000_000))
        self.max_pdf_chars = int(config.get("company_ir_max_pdf_chars", 12_000))
        self.enabled = bool(config.get("datasources", {}).get("company_ir", True))

    async def fetch(self, context: MarketContext, *, start_date: str, end_date: str) -> ProviderResponse:
        del start_date
        if not self.enabled:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DISABLED, detail="disabled by markets.jp.datasources"))
        root = self.urls.get(context.native_symbol) or self.urls.get(context.symbol)
        if not root:
            root = await self._website_from_yfinance(context.symbol)
        if not root:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DATA_UNAVAILABLE, detail="company website could not be resolved"))

        landing = await self._resolve_ir_landing(root)
        if not landing:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DATA_UNAVAILABLE, detail="no public IR landing page could be resolved"))
        document_links = await self._discover_documents(landing)
        if not document_links:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DATA_UNAVAILABLE, detail="IR landing page reachable but no qualifying official document was found"))

        documents = await asyncio.gather(*(
            self._fetch_document(context, href, title, end_date) for href, title in document_links[: self.max_documents]
        ))
        items = tuple(item for item in documents if item is not None)
        return ProviderResponse(
            SourceStatus(self.name, DataStatus.OK, detail=f"scanned {len(document_links)} official IR document link(s)", item_count=len(items)),
            items,
        )

    async def _resolve_ir_landing(self, root: str) -> str | None:
        candidates = [root]
        parsed = urlparse(root)
        base = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else root.rstrip("/")
        candidates.extend((f"{base}/ir/", f"{base}/en/ir/", f"{base}/investors/"))
        for candidate in dict.fromkeys(candidates):
            status, html, _ = await get_text(candidate, timeout=self.timeout)
            if status != DataStatus.OK:
                continue
            linked = _find_ir_url(html, candidate)
            if linked and _looks_like_ir_landing(linked):
                return linked
            # Some issuers' finance-site landing page only links to a separate
            # corporate site. Follow that single public navigation hop, then
            # resolve the actual /ir/ page there.
            if linked:
                hop_status, hop_html, _ = await get_text(linked, timeout=self.timeout)
                if hop_status == DataStatus.OK:
                    nested = _find_ir_url(hop_html, linked)
                    if nested and _looks_like_ir_landing(nested):
                        return nested
            if _looks_like_ir_landing(candidate) and any(term in _visible_text(html).lower() for term in _IR_TERMS):
                return candidate
        return None

    async def _discover_documents(self, landing_url: str) -> list[tuple[str, str]]:
        status, html, _ = await get_text(landing_url, timeout=self.timeout)
        if status != DataStatus.OK:
            return []
        links = _links(html, landing_url)
        # One navigation layer catches common "IR library" / result-list pages
        # without broad crawling of the corporate site.
        listing_pages = [href for href, label in links if _is_listing_link(href, label) and not href.lower().endswith(".pdf")][:4]
        if listing_pages:
            listing_results = await asyncio.gather(*(get_text(href, timeout=self.timeout) for href in listing_pages))
            for (status, listing_html, _), page_url in zip(listing_results, listing_pages, strict=True):
                if status == DataStatus.OK:
                    links.extend(_links(listing_html, page_url))
        documents: list[tuple[str, str]] = []
        seen: set[str] = set()
        for href, label in links:
            if href in seen or not _is_document_link(href, label) or not _is_trusted_document_host(href, landing_url):
                continue
            seen.add(href)
            documents.append((href, label or href.rsplit("/", 1)[-1]))
        return sorted(documents, key=lambda item: _document_priority(*item), reverse=True)

    async def _fetch_document(
        self, context: MarketContext, url: str, title: str, end_date: str
    ) -> MarketInformation | None:
        timestamp = _document_date(title, url, fallback=end_date)
        if url.lower().split("?", 1)[0].endswith(".pdf"):
            return await self._fetch_pdf_document(context, url, title, timestamp)
        status, html, detail = await get_text(url, timeout=self.timeout)
        if status != DataStatus.OK:
            return _document_item(context, title, url, timestamp, "", "PARSE_FAILED", status=DataStatus.PARSE_FAILED, detail=detail)
        text = _visible_text(html)
        if not text:
            return _document_item(context, title, url, timestamp, "", "PARSE_FAILED", status=DataStatus.PARSE_FAILED, detail="empty HTML document")
        return _document_item(context, title, url, timestamp, text, "OK")

    async def _fetch_pdf_document(
        self, context: MarketContext, url: str, title: str, timestamp: datetime
    ) -> MarketInformation:
        response = await get_bytes(url, timeout=self.timeout, max_bytes=self.max_pdf_bytes)
        if response.status != DataStatus.OK:
            return _document_item(context, title, url, timestamp, "", "PARSE_FAILED", status=DataStatus.PARSE_FAILED, detail=response.detail)
        try:
            text = extract_pdf_text(response.payload, self.max_pdf_chars)
        except Exception as exc:
            return _document_item(context, title, url, timestamp, "", "PARSE_FAILED", status=DataStatus.PARSE_FAILED, detail=type(exc).__name__)
        return _document_item(context, title, url, timestamp, text, "OK" if text else "EMPTY_TEXT")

    @staticmethod
    async def _website_from_yfinance(symbol: str) -> str | None:
        def resolve() -> str | None:
            try:
                website = (yf.Ticker(symbol).info or {}).get("website")
                return website if isinstance(website, str) and website.startswith("http") else None
            except Exception:
                return None

        return await asyncio.to_thread(resolve)


def _document_item(
    context: MarketContext, title: str, url: str, timestamp: datetime, text: str, extraction_status: str,
    *, status: DataStatus = DataStatus.OK, detail: str = "",
) -> MarketInformation:
    metadata = {
        "official_document": True,
        "document_downloaded": extraction_status not in {"PARSE_FAILED", "NOT_REQUESTED"},
        "extraction_detail": detail,
        **structure_official_disclosure(
            title=title,
            text=text,
            source="Company IR",
            event_date=timestamp.date().isoformat(),
            extraction_status=extraction_status,
        ),
    }
    return MarketInformation(
        source="Company IR",
        source_type=metadata["event_type"],
        ticker=context.symbol,
        timestamp=timestamp,
        title=title,
        content=text,
        url=url,
        confidence=float(metadata["confidence"]),
        verified=True,
        status=status,
        layer=InformationLayer.VERIFIED_FACT,
        content_level="full_text" if text else "summary_only",
        metadata=metadata,
    )


def _links(html: str, base_url: str) -> list[tuple[str, str]]:
    return [(urljoin(base_url, unescape(match.group("href"))), _visible_text(match.group("label"))) for match in _IR_LINK.finditer(html)]


def _find_ir_url(html: str, base_url: str) -> str | None:
    for href, label in _links(html, base_url):
        if href.lower().split("?", 1)[0].endswith(".pdf"):
            continue
        if any(term in label.lower() for term in _IR_TERMS) or "/ir/" in href.lower():
            return href
    return None


def _looks_like_ir_landing(url: str) -> bool:
    path = urlparse(url).path.lower()
    return "/ir" in path or "investor" in path


def _is_listing_link(href: str, label: str) -> bool:
    value = f"{href} {label}".lower()
    return any(term in value for term in _LISTING_TERMS)


def _is_document_link(href: str, label: str) -> bool:
    value = f"{href} {label}".lower()
    if href.lower().split("?", 1)[0].endswith(".pdf"):
        return True
    # HTML detail pages are accepted only if their own label/URL has a date;
    # generic navigation such as "Financial Results" belongs to discovery, not
    # to the issuer's latest disclosure set.
    return bool(_DATE.search(value)) and any(term in value for term in _DOCUMENT_TERMS)


def _is_trusted_document_host(document_url: str, landing_url: str) -> bool:
    """Reject unrelated external PDFs linked from an issuer's navigation.

    EIR Parts is a commonly issuer-designated Japanese IR document host, so it
    remains eligible.  Other cross-domain documents require an explicit
    configured company-IR URL and are not silently treated as issuer filings.
    """
    document_host = (urlparse(document_url).hostname or "").lower()
    landing_host = (urlparse(landing_url).hostname or "").lower()
    if document_host == landing_host or document_host.endswith("." + landing_host) or landing_host.endswith("." + document_host):
        return True
    return document_host.endswith("eir-parts.net")


def _document_priority(href: str, label: str) -> tuple[int, int, str]:
    value = f"{href} {label}".lower()
    date_score = _date_score(value)
    return (
        int(href.lower().split("?", 1)[0].endswith(".pdf")),
        date_score,
        value,
    )


def _document_date(title: str, url: str, *, fallback: str) -> datetime:
    match = _DATE.search(f"{title} {url}")
    if not match:
        match = _COMPACT_DATE.search(f"{title} {url}")
    if match:
        return datetime(int(match["year"]), int(match["month"]), int(match["day"]), tzinfo=UTC)
    try:
        return datetime.fromisoformat(fallback).replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)


def _date_score(value: str) -> int:
    match = _DATE.search(value) or _COMPACT_DATE.search(value)
    if not match:
        return 0
    return int(f"{match['year']}{int(match['month']):02d}{int(match['day']):02d}")


def _visible_text(html: str) -> str:
    return " ".join(unescape(_TAG.sub(" ", html)).split())
