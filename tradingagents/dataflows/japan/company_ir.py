"""Company IR discovery from a public company website, with honest extraction state."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from urllib.parse import urljoin

import yfinance as yf

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .http import get_text
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus

_IR_LINK = re.compile(r"href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<label>.*?)</a>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_IR_TERMS = ("investor relations", "ir情報", "irニュース", "投資家情報", "株主・投資家")


class CompanyIRProvider:
    """Find a public IR landing page without fabricating PDF or filing content."""

    name = "Company IR"
    category = "fundamentals"

    def __init__(self, urls: dict[str, str] | None = None):
        config = get_config().get("markets", {}).get("jp", {})
        self.urls = dict(urls if urls is not None else config.get("company_ir_urls", {}))
        self.timeout = float(config.get("request_timeout_seconds", 10))
        self.enabled = bool(config.get("datasources", {}).get("company_ir", True))

    async def fetch(self, context: MarketContext, *, start_date: str, end_date: str) -> ProviderResponse:
        del start_date, end_date
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
        text = _visible_text(ir_html)
        item = MarketInformation(
            source=self.name,
            source_type="company_ir_landing_page",
            ticker=context.symbol,
            timestamp=datetime.now(UTC),
            title="Company IR public landing page",
            content=text[:8000],
            url=ir_url,
            confidence=0.95,
            verified=True,
            layer=InformationLayer.VERIFIED_FACT,
            content_level="full_text",
            metadata={"extraction_status": "landing_page_text_only"},
        )
        return ProviderResponse(SourceStatus(self.name, DataStatus.OK, item_count=1), (item,))

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
    for match in _IR_LINK.finditer(html):
        label = _visible_text(match.group("label")).lower()
        href = unescape(match.group("href"))
        if any(term in label for term in _IR_TERMS):
            return urljoin(base_url, href)
    return None


def _visible_text(html: str) -> str:
    return " ".join(unescape(_TAG.sub(" ", html)).split())
