"""Public TDnet disclosure-index provider.

TDnet's public disclosure viewer publishes a date-indexed HTML list and linked
PDF documents.  This provider reads that public index politely; it does not use
the paid TDnet API, authenticate, or attempt to defeat access controls.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from html import unescape
from typing import Any
from urllib.parse import urljoin

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .http import get_json, get_text
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus

_PUBLIC_INDEX_URL = "https://www.release.tdnet.info/inbs/I_list_{page:03d}_{day:%Y%m%d}.html"
_ROW = re.compile(r"<tr>\s*(?P<body>.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL = re.compile(r"<td[^>]*class=[\"'][^\"']*kj(?P<field>Time|Code|Name|Title)[^\"']*[\"'][^>]*>(?P<value>.*?)</td>", re.IGNORECASE | re.DOTALL)
_PDF = re.compile(r"href=[\"'](?P<href>[^\"']+\.pdf)[\"']", re.IGNORECASE)
_PAGE = re.compile(r"I_list_(?P<page>\d{3})_\d{8}\.html")
_TAG = re.compile(r"<[^>]+>")


class TDnetProvider:
    """Collect recent, public TDnet disclosures for one Japanese security.

    ``feed_url`` remains supported for installations with an authorised
    structured feed.  By default, the official public index is used for the
    requested dates.  The page cap is explicit so earnings-season volume cannot
    overload the public service; a capped day is recorded in source status.
    """

    name = "TDnet"
    category = "tdnet"
    cache_version = "public-index-v1"

    def __init__(self, feed_url: str | None = None):
        config = get_config().get("markets", {}).get("jp", {})
        self.feed_url = feed_url or config.get("tdnet_feed_url")
        self.timeout = float(config.get("request_timeout_seconds", 10))
        self.max_pages_per_day = int(config.get("tdnet_max_pages_per_day", 10))
        self.enabled = bool(config.get("datasources", {}).get("tdnet", True))

    async def fetch(self, context: MarketContext, *, start_date: str, end_date: str) -> ProviderResponse:
        if not self.enabled:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DISABLED, detail="disabled by markets.jp.datasources"))
        if self.feed_url:
            return await self._fetch_authorised_feed(context, start_date, end_date)

        days = _date_range(start_date, end_date, maximum_days=31)
        if not days:
            return ProviderResponse(SourceStatus(self.name, DataStatus.PARSE_FAILED, detail="invalid date range"))
        results = await asyncio.gather(*(self._fetch_day(context, day) for day in days))
        items = tuple(item for day_items, _ in results for item in day_items)
        capped_days = sum(capped for _, capped in results)
        detail = ""
        if capped_days:
            detail = f"{capped_days} day(s) reached tdnet_max_pages_per_day={self.max_pages_per_day}"
        return ProviderResponse(SourceStatus(self.name, DataStatus.OK, detail=detail, item_count=len(items)), items)

    async def _fetch_authorised_feed(
        self, context: MarketContext, start_date: str, end_date: str
    ) -> ProviderResponse:
        response = await get_json(
            self.feed_url,
            params={"ticker": context.native_symbol, "start_date": start_date, "end_date": end_date},
            timeout=self.timeout,
        )
        if response.status != DataStatus.OK:
            return ProviderResponse(SourceStatus(self.name, response.status, detail=response.detail))
        items = tuple(normalise_tdnet_records(response.payload, context))
        return ProviderResponse(SourceStatus(self.name, DataStatus.OK, item_count=len(items)), items)

    async def _fetch_day(self, context: MarketContext, day: date) -> tuple[tuple[MarketInformation, ...], bool]:
        first_url = _PUBLIC_INDEX_URL.format(page=1, day=day)
        status, html, _ = await get_text(first_url, timeout=self.timeout)
        if status != DataStatus.OK:
            return (), False  # Weekends/holidays are simply absent from the public index.
        page_count = min(_page_count(html), self.max_pages_per_day)
        pages = [html]
        if page_count > 1:
            remaining = await asyncio.gather(*(
                get_text(_PUBLIC_INDEX_URL.format(page=number, day=day), timeout=self.timeout)
                for number in range(2, page_count + 1)
            ))
            pages.extend(body for status, body, _ in remaining if status == DataStatus.OK)
        items = tuple(
            item
            for page_number, page_html in enumerate(pages, start=1)
            for item in _parse_public_list(page_html, context, day, page_number)
        )
        return items, _page_count(html) > self.max_pages_per_day


def _date_range(start_date: str, end_date: str, *, maximum_days: int) -> tuple[date, ...]:
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return ()
    if end < start:
        return ()
    start = max(start, end - timedelta(days=maximum_days - 1))
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _page_count(html: str) -> int:
    pages = [int(match.group("page")) for match in _PAGE.finditer(html)]
    return max(pages, default=1)


def _parse_public_list(
    html: str, context: MarketContext, day: date, page: int
) -> Iterable[MarketInformation]:
    for row in _ROW.finditer(html):
        fields = {match.group("field").lower(): _text(match.group("value")) for match in _CELL.finditer(row.group("body"))}
        if _normalise_code(fields.get("code", "")) != context.native_symbol:
            continue
        title_html = next((match.group("value") for match in _CELL.finditer(row.group("body")) if match.group("field").lower() == "title"), "")
        pdf = _PDF.search(title_html)
        title = fields.get("title", "")
        if not title or not pdf:
            continue
        timestamp = datetime.combine(day, _parse_time(fields.get("time", "")), tzinfo=UTC)
        yield MarketInformation(
            source="TDnet",
            source_type=classify_tdnet_title(title),
            ticker=context.symbol,
            timestamp=timestamp,
            title=title,
            url=urljoin(_PUBLIC_INDEX_URL.format(page=page, day=day), unescape(pdf.group("href"))),
            confidence=0.98,
            verified=True,
            layer=InformationLayer.VERIFIED_FACT,
            content_level="summary_only",
            metadata={
                "company": fields.get("name", ""),
                "official_index": True,
                "index_page": page,
                "document_downloaded": False,
            },
        )


def normalise_tdnet_records(payload: Any, context: MarketContext) -> Iterable[MarketInformation]:
    """Normalize an optional authorised JSON feed using the same fact schema."""
    records = payload if isinstance(payload, list) else (payload.get("items", []) if isinstance(payload, dict) else [])
    for record in records:
        if not isinstance(record, dict) or _normalise_code(str(record.get("ticker") or record.get("code") or "")) != context.native_symbol:
            continue
        title = str(record.get("title") or "")
        yield MarketInformation(
            source="TDnet",
            source_type=classify_tdnet_title(title),
            ticker=context.symbol,
            timestamp=_parse_timestamp(record.get("date") or record.get("timestamp")),
            title=title,
            content=str(record.get("summary") or ""),
            url=record.get("url"),
            confidence=0.98,
            verified=True,
            layer=InformationLayer.VERIFIED_FACT,
            content_level=str(record.get("content_level") or "summary_only"),
            metadata={"company": record.get("company"), "importance": int(record.get("importance") or 0)},
        )


def classify_tdnet_title(title: str) -> str:
    categories = (
        ("業績予想", "guidance_revision"), ("上方修正", "upward_revision"), ("下方修正", "downward_revision"),
        ("配当", "dividend_revision"), ("自己株式", "share_buyback"), ("自社株", "share_buyback"),
        ("増資", "equity_financing"), ("転換社債", "convertible_bond"), ("M&A", "merger_acquisition"),
        ("公開買付", "tob"), ("受注", "large_order"), ("新製品", "new_product"),
        ("業務提携", "business_partnership"), ("中期経営", "midterm_plan"), ("特別損失", "extraordinary_loss"),
        ("特別利益", "extraordinary_gain"), ("訴訟", "litigation"), ("人事", "personnel"),
    )
    return next((category for keyword, category in categories if keyword in title), "timely_disclosure")


def _normalise_code(value: str) -> str:
    code = re.sub(r"\s+", "", value).upper().replace(".T", "")
    return code[:-1] if len(code) == 5 and code.endswith("0") else code


def _text(html: str) -> str:
    return " ".join(unescape(_TAG.sub(" ", html)).split())


def _parse_time(value: str):
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return datetime.min.time()


def _parse_timestamp(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)
