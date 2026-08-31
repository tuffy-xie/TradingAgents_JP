"""Public Japan-focused news aggregation; never treated as official facts."""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from html import unescape
from urllib.parse import urljoin, urlparse

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .http import get_text
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus

_SOURCES = {
    "Yahoo Finance Japan": "https://finance.yahoo.co.jp/quote/{code}.T/news",
    "Kabutan": "https://kabutan.jp/stock/news?code={code}",
    "Minkabu": "https://minkabu.jp/stock/{code}/news",
}
_LINK = re.compile(r'href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<label>.*?)</a>', re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(?P<title>.*?)</title>", re.I | re.S)
_META_TAG = re.compile(r"<meta\b[^>]*>", re.I)
_META_TIME = re.compile(
    r'(?:datePublished|article:published_time|publish(?:ed)?(?:_at)?)\s*["\']?\s*[:=]\s*["\'](?P<value>[^"\']+)',
    re.I,
)
_ISO_TIME = re.compile(r"20\d{2}-\d{1,2}-\d{1,2}T\d{1,2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})")
_DATE = re.compile(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::\d{2})?)?")
_JP_DATE = re.compile(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日(?:\s*(\d{1,2}):(\d{2}))?")
_NAVIGATION_TERMS = ("ログイン", "登録", "ヘルプ", "ランキング", "広告", "おすすめ", "関連銘柄")


@dataclass(frozen=True)
class _Candidate:
    source: str
    title: str
    url: str
    issuer_tokens: tuple[str, ...]


def _empty_stats() -> dict[str, int]:
    return {
        "raw_candidates": 0,
        "filtered_navigation_login_ad": 0,
        "filtered_irrelevant_stock": 0,
        "filtered_missing_published_at": 0,
        "filtered_time_window": 0,
        "filtered_detail_unavailable": 0,
    }


class JapanNewsProvider:
    name, category, cache_version = "Japan News", "news", "public-jp-news-v2"
    cache_requires_completed_window = True

    def __init__(self):
        self.enabled = bool(
            get_config()
            .get("markets", {})
            .get("jp", {})
            .get("datasources", {})
            .get("japan_news", True)
        )
        self.timeout = float(
            get_config().get("markets", {}).get("jp", {}).get("request_timeout_seconds", 10)
        )

    async def fetch(
        self, context: MarketContext, *, start_date: str, end_date: str
    ) -> ProviderResponse:
        if not self.enabled:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DISABLED, detail="disabled"))

        async def one(source, url):
            status, html, detail = await get_text(
                url.format(code=context.native_symbol), timeout=self.timeout
            )
            return source, status, html, detail

        fetched = await asyncio.gather(*(one(k, v) for k, v in _SOURCES.items()))
        stats = _empty_stats()
        candidates: list[_Candidate] = []
        details = []
        for source, status, html, _error in fetched:
            if status != DataStatus.OK:
                details.append(f"{source}={status}")
                continue
            candidates.extend(
                _discover_candidates(
                    source,
                    html,
                    _SOURCES[source].format(code=context.native_symbol),
                    context,
                    stats,
                )
            )

        async def detail(candidate: _Candidate):
            status, html, error = await get_text(candidate.url, timeout=self.timeout)
            return candidate, status, html, error

        fetched_details = await asyncio.gather(*(detail(candidate) for candidate in candidates))
        items: list[MarketInformation] = []
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        for candidate, status, html, _error in fetched_details:
            if status != DataStatus.OK:
                stats["filtered_detail_unavailable"] += 1
                continue
            item, reason = _validate_detail(candidate, html, context)
            if item is None:
                stats[reason] += 1
                continue
            if item.timestamp.date() < start or item.timestamp.date() > end:
                stats["filtered_time_window"] += 1
                continue
            items.append(
                replace(
                    item,
                    metadata={
                        **item.metadata,
                        "data_date": item.timestamp.date().isoformat(),
                        "freshness_status": "LATEST_AVAILABLE",
                        "freshness_basis": "explicit published_at inside requested news window",
                        "native_cadence": "CONTINUOUS_EVENT_STREAM",
                    },
                )
            )
        out = _dedup(items)
        details.append(
            "Reuters Japan=DATA_UNAVAILABLE:no licensed/public structured feed configured"
        )
        details.append(
            ", ".join(f"{key}={value}" for key, value in stats.items())
            + f", final_valid_news={len(items)}, clustered_events={len(out)}"
        )
        return ProviderResponse(
            SourceStatus(
                self.name,
                DataStatus.OK if out else DataStatus.DATA_UNAVAILABLE,
                detail="; ".join(details),
                item_count=len(out),
            ),
            tuple(out),
        )


def _discover_candidates(source, html, base, context, stats):
    """Discover only source-specific detail pages; listing links are not news."""
    output = []
    issuer_tokens = _issuer_tokens_from_listing(html, context.native_symbol)
    seen = set()
    for m in _LINK.finditer(html):
        title = " ".join(unescape(_TAG.sub(" ", m["label"])).split())
        href = urljoin(base, unescape(m["href"]))
        if not title or href in seen:
            continue
        seen.add(href)
        stats["raw_candidates"] += 1
        if len(title) < 12 or any(term in title for term in _NAVIGATION_TERMS):
            stats["filtered_navigation_login_ad"] += 1
            continue
        if not _is_detail_url(source, href, context.native_symbol):
            stats["filtered_navigation_login_ad"] += 1
            continue
        output.append(_Candidate(source, title, href, issuer_tokens))
    return output


def _is_detail_url(source, href, code):
    """Reject landing, help and related-security routes before fetching a detail page."""
    parsed = urlparse(href)
    if source == "Yahoo Finance Japan":
        return parsed.path.startswith("/news/detail/")
    if source == "Kabutan":
        return (
            parsed.path == "/stock/news"
            and f"code={code}" in parsed.query
            and "b=n" in parsed.query
        )
    # minkabu is commonly access-restricted.  Keep it disabled unless its public
    # detail route includes the requested security code.
    return source == "Minkabu" and "/news/" in parsed.path and code in href


def _validate_detail(candidate, html, context):
    """Require a detail-page timestamp plus direct code/issuer evidence."""
    timestamp = _published_at(candidate.source, html)
    if timestamp is None:
        return None, "filtered_missing_published_at"
    page_title = _page_title(html)
    # A description can be a basket, related-stock or recommendation list.  A
    # headline naming the issuer (or its clear quote-page alias) is the minimum
    # direct-relevance evidence accepted into the bundle.
    direct = any(token in page_title for token in candidate.issuer_tokens)
    if not direct:
        return None, "filtered_irrelevant_stock"
    return MarketInformation(
        source=candidate.source,
        source_type="japan_stock_news",
        ticker=context.symbol,
        timestamp=timestamp,
        title=candidate.title,
        url=candidate.url,
        confidence={"Yahoo Finance Japan": 0.7, "Kabutan": 0.85, "Minkabu": 0.75}[candidate.source],
        verified=False,
        layer=InformationLayer.NEWS_ANALYST_VIEW,
        content_level="summary_only",
        metadata={
            "ticker": context.native_symbol,
            "category": "stock_news",
            "company": "DATA_UNAVAILABLE",
            "summary": candidate.title,
            "summary_type": "headline_only",
            "sentiment": "UNDETERMINED",
            "relevance": "DIRECT",
            "published_at": timestamp.isoformat(),
            "source_status": "OK",
        },
    ), None


def _published_at(source, html):
    """Read only source-provided full timestamps; fetching time is never a fallback."""
    for tag in _META_TAG.findall(html):
        if "datepublished" not in tag.lower() and "publicationdate" not in tag.lower():
            continue
        content = re.search(r'content=["\']([^"\']+)', tag, flags=re.I)
        if content:
            parsed = _parse_timestamp(content.group(1))
            if parsed is not None:
                return parsed
    values = [*_META_TIME.findall(html), *_ISO_TIME.findall(html)]
    for value in values:
        parsed = _parse_timestamp(value)
        if parsed is not None:
            return parsed
    if source == "Kabutan":
        # Kabutan's generic page-update timestamps are not article publication
        # times.  Without its datePublished metadata, omit the item.
        return None
    match = _DATE.search(html) or _JP_DATE.search(html)
    if not match:
        return None
    return datetime(
        int(match[1]),
        int(match[2]),
        int(match[3]),
        int(match[4] or 0),
        int(match[5] or 0),
        tzinfo=UTC,
    )


def _parse_timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("/", "-").replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _page_title(html):
    match = _TITLE.search(html)
    return " ".join(unescape(_TAG.sub(" ", match["title"])).split()) if match else ""


def _issuer_tokens_from_listing(html, code):
    """Read the issuer identity from the requested quote heading, once."""
    for match in _LINK.finditer(html):
        href = unescape(match["href"])
        label = " ".join(unescape(_TAG.sub(" ", match["label"])).split())
        is_yahoo_quote = f"/quote/{code}.T" in href or f'/quote/{code}"' in href
        is_kabutan_quote = "/stock/" in href and f"code={code}" in href and "news" not in href
        if not is_yahoo_quote and not is_kabutan_quote:
            continue
        cleaned = re.sub(r"[（(【].*?[）)】]", "", label).strip()
        if len(cleaned) < 2:
            return ()
        aliases = [cleaned]
        for suffix, short_suffix in (("電気工業", "電"), ("製作所", "製"), ("商事", "")):
            if cleaned.endswith(suffix):
                aliases.append(cleaned[: -len(suffix)] + short_suffix)
        return tuple(dict.fromkeys(alias for alias in aliases if len(alias) >= 2))
    return ()


def _dedup(items):
    grouped = {}
    for item in items:
        key = re.sub(r"[^0-9A-Z一-龯ぁ-んァ-ン]", "", item.title.upper())
        cluster_id = f"jp-news-{hashlib.sha1(key.encode()).hexdigest()[:12]}"
        if key not in grouped:
            grouped[key] = MarketInformation(
                **{
                    **item.__dict__,
                    "metadata": {
                        **item.metadata,
                        "cluster_id": cluster_id,
                        "canonical_event": item.title,
                        "source_count": 1,
                        "sources": [item.source],
                    },
                }
            )
            continue
        prior = grouped[key]
        sources = list(prior.metadata["sources"])
        if item.source not in sources:
            sources.append(item.source)
        grouped[key] = MarketInformation(
            **{
                **prior.__dict__,
                "metadata": {
                    **prior.metadata,
                    "source_count": len(sources),
                    "sources": sources,
                },
            }
        )
    return list(grouped.values())
