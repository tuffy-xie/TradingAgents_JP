"""Conservative Japanese retail-investor sentiment from public Yahoo boards.

Community text is always ``MARKET_SENTIMENT``.  It is never an official fact
and an empty sample remains unavailable rather than becoming neutral.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import UTC, date, datetime, time
from html import unescape
from zoneinfo import ZoneInfo

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .http import get_text
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus

_JST = ZoneInfo("Asia/Tokyo")
_BOARD_URL = "https://finance.yahoo.co.jp/quote/{code}.T/forum"
_ARTICLE = re.compile(r"<article\b[^>]*>(?P<body>.*?)</article>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_TIME = re.compile(r"<time[^>]*>(?P<value>20\d{2}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2})</time>", re.I)
_DETAIL = re.compile(r'href=["\'](?P<path>/quote/[^"\']+/forum/\d+)["\']', re.I)
_USER = re.compile(r"user=([a-f0-9]{16,})", re.I)
_BODY = re.compile(r"<div[^>]+BbsItem__body[^>]*>(?P<body>.*?)</div>", re.I | re.S)
_TITLE = re.compile(r"<title[^>]*>(?P<title>.*?)</title>", re.I | re.S)
_NOISE = ("http://", "https://", "無料登録", "キャンペーン", "広告", "line.me")
_POSITIVE = ("買い", "買う", "上昇", "上が", "強気", "反発", "期待", "増益", "上方", "回復", "割安")
_NEGATIVE = (
    "売り",
    "売る",
    "下落",
    "下が",
    "弱気",
    "暴落",
    "損切",
    "戻り売り",
    "減益",
    "下方",
    "危険",
)


def _stats() -> dict[str, int]:
    return {
        "raw_samples": 0,
        "filtered_noise": 0,
        "filtered_irrelevant": 0,
        "filtered_missing_published_at": 0,
        "filtered_duplicate": 0,
        "filtered_user_cap": 0,
        "filtered_time_window": 0,
    }


class JapanSentimentProvider:
    """Public Yahoo Finance Japan board provider, without US-platform fallback."""

    name, category, cache_version = "Japan Investor Sentiment", "sentiment", "yahoo-board-v1"
    cache_requires_completed_window = True

    def __init__(self):
        config = get_config().get("markets", {}).get("jp", {})
        self.enabled = bool(config.get("sentiment", {}).get("yahoo_board", True))
        self.timeout = float(config.get("request_timeout_seconds", 10))

    async def fetch(
        self, context: MarketContext, *, start_date: str, end_date: str
    ) -> ProviderResponse:
        if not self.enabled:
            return ProviderResponse(
                SourceStatus(self.name, DataStatus.DISABLED, detail="Yahoo board disabled")
            )
        status, html, detail = await get_text(
            _BOARD_URL.format(code=context.native_symbol), timeout=self.timeout
        )
        if status != DataStatus.OK:
            return ProviderResponse(SourceStatus(self.name, status, detail=f"Yahoo Board={detail}"))

        stats = _stats()
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
        issuer_tokens = _issuer_tokens(html, context.native_symbol)
        posts = _parse_posts(html, context, issuer_tokens, stats)
        in_window = []
        for post in posts:
            if start <= post.timestamp.astimezone(_JST).date() <= end:
                in_window.append(post)
            else:
                stats["filtered_time_window"] += 1
        aggregate = _aggregate(in_window, end)
        summary = _summary_item(context, aggregate, end)
        source_status = _source_status(aggregate, stats, detail)
        return ProviderResponse(source_status, (*in_window, summary))


def _parse_posts(html, context, issuer_tokens, stats):
    posts = []
    seen = set()
    per_user = Counter()
    for article in _ARTICLE.findall(html):
        stats["raw_samples"] += 1
        timestamp = _timestamp(article)
        if timestamp is None:
            stats["filtered_missing_published_at"] += 1
            continue
        body_match = _BODY.search(article)
        text = _clean(body_match["body"]) if body_match else ""
        if len(text) < 5 or any(term in text.lower() for term in _NOISE):
            stats["filtered_noise"] += 1
            continue
        if not _is_direct(text, context.native_symbol, issuer_tokens):
            stats["filtered_irrelevant"] += 1
            continue
        detail = _DETAIL.search(article)
        if detail is None:
            stats["filtered_noise"] += 1
            continue
        user = _USER.search(article)
        user_id = user.group(1) if user else "anonymous"
        normalized = re.sub(r"\W+", "", text).lower()
        duplicate_key = (user_id, normalized)
        if duplicate_key in seen:
            stats["filtered_duplicate"] += 1
            continue
        seen.add(duplicate_key)
        if per_user[user_id] >= 3:
            stats["filtered_user_cap"] += 1
            continue
        per_user[user_id] += 1
        sentiment = _classify(text)
        posts.append(
            MarketInformation(
                source="Yahoo Finance Japan Board",
                source_type="japan_investor_post",
                ticker=context.symbol,
                timestamp=timestamp,
                title="Yahoo Finance Japan 掲示板投稿",
                content=text,
                url=f"https://finance.yahoo.co.jp{detail['path']}",
                confidence=0.45 if user_id != "anonymous" else 0.35,
                verified=False,
                layer=InformationLayer.MARKET_SENTIMENT,
                content_level="full_text",
                metadata={
                    "ticker": context.native_symbol,
                    "published_at": timestamp.isoformat(),
                    "sentiment": sentiment,
                    "relevance": "DIRECT",
                    "user_id": user_id,
                    "source_status": "OK",
                },
            )
        )
    return posts


def _timestamp(article):
    match = _TIME.search(article)
    if match is None:
        return None
    try:
        return (
            datetime.strptime(match["value"], "%Y/%m/%d %H:%M").replace(tzinfo=_JST).astimezone(UTC)
        )
    except ValueError:
        return None


def _clean(value):
    return " ".join(unescape(_TAG.sub(" ", value)).split())


def _issuer_tokens(html, code):
    title = _TITLE.search(html)
    text = _clean(title["title"]) if title else ""
    match = re.search(r"(.+?)(?:【|\(|\s)" + re.escape(code), text)
    company = match.group(1).strip(" (株)") if match else ""
    aliases = [company] if len(company) >= 2 else []
    for suffix, short_suffix in (("電気工業", "電"), ("製作所", "製"), ("商事", "")):
        if company.endswith(suffix):
            aliases.append(company[: -len(suffix)] + short_suffix)
    # Japanese boards commonly use the short exchange-style company stem (for
    # example, 村田 for 村田製).  It remains issuer evidence, unlike a price-only
    # message, because it is derived from the page's own target identity.
    for alias in tuple(aliases):
        if alias.endswith(("製", "電")) and len(alias) > 2:
            aliases.append(alias[:-1])
    return tuple(dict.fromkeys(alias for alias in aliases if len(alias) >= 2))


def _is_direct(text, code, issuer_tokens):
    return code in text or any(token in text for token in issuer_tokens)


def _classify(text):
    positive = sum(text.count(term) for term in _POSITIVE)
    negative = sum(text.count(term) for term in _NEGATIVE)
    if positive > negative:
        return "POSITIVE"
    if negative > positive:
        return "NEGATIVE"
    return "NEUTRAL"


def _aggregate(posts, end_date):
    end = datetime.combine(end_date, time.max, tzinfo=_JST).astimezone(UTC)
    windows = {}
    for label, days in (("1D", 1), ("3D", 3), ("7D", 7)):
        cutoff = end.timestamp() - days * 86400
        subset = [post for post in posts if post.timestamp.timestamp() >= cutoff]
        counts = Counter(post.metadata["sentiment"] for post in subset)
        if not subset:
            windows[label] = {
                "sample_count": 0,
                "positive_count": 0,
                "neutral_count": 0,
                "negative_count": 0,
                "sentiment_score": None,
                "confidence": 0.0,
                "status": "DATA_UNAVAILABLE",
            }
            continue
        weighted = [
            (
                (_sentiment_value(post.metadata["sentiment"])),
                math.exp(-(end - post.timestamp).total_seconds() / 86400 / 3),
            )
            for post in subset
        ]
        score = sum(value * weight for value, weight in weighted) / sum(
            weight for _, weight in weighted
        )
        windows[label] = {
            "sample_count": len(subset),
            "positive_count": counts["POSITIVE"],
            "neutral_count": counts["NEUTRAL"],
            "negative_count": counts["NEGATIVE"],
            "sentiment_score": round(score, 4),
            "confidence": round(min(0.7, 0.2 + len(subset) * 0.05), 3),
            "status": "OK" if len(subset) >= 3 else "INSUFFICIENT_DATA",
        }
    current = windows["7D"]
    return {
        **current,
        "latest_post_date": max(
            (post.timestamp.astimezone(_JST).date().isoformat() for post in posts),
            default=None,
        ),
        "time_window": windows,
        "source_breakdown": {
            "Yahoo Finance Japan Board": current["sample_count"],
            "Kabutan": 0,
            "Minkabu": 0,
            "5ch": 0,
            "X": 0,
        },
    }


def _sentiment_value(value):
    return {"POSITIVE": 1, "NEUTRAL": 0, "NEGATIVE": -1}[value]


def _summary_item(context, aggregate, end_date):
    status = DataStatus.OK if aggregate["sample_count"] else DataStatus.DATA_UNAVAILABLE
    timestamp = datetime.combine(end_date, time.max, tzinfo=_JST).astimezone(UTC)
    return MarketInformation(
        source="Japan Investor Sentiment",
        source_type="japan_investor_sentiment_aggregate",
        ticker=context.symbol,
        timestamp=timestamp,
        title="日本投资者情绪聚合（7日）",
        content="",
        confidence=aggregate["confidence"],
        verified=False,
        status=status,
        layer=InformationLayer.MARKET_SENTIMENT,
        content_level="aggregate",
        metadata={
            **aggregate,
            "data_date": aggregate.get("latest_post_date"),
            "published_at": None,
            "freshness_status": "LATEST_AVAILABLE"
            if aggregate["sample_count"]
            else "DATA_UNAVAILABLE",
            "freshness_basis": "explicitly timestamped posts inside requested window",
            "native_cadence": "CONTINUOUS_EVENT_STREAM",
        },
    )


def _source_status(aggregate, stats, detail):
    if aggregate["sample_count"] == 0:
        status = DataStatus.DATA_UNAVAILABLE
    elif aggregate["status"] == "INSUFFICIENT_DATA":
        status = DataStatus.LOW_SAMPLE
    else:
        status = DataStatus.OK
    unavailable = (
        "Kabutan=DATA_UNAVAILABLE:no public post feed",
        "Minkabu=DATA_UNAVAILABLE:no verifiable public investor-post feed",
        "5ch=DATA_UNAVAILABLE:not enabled without stable legal filtering",
        "X=DATA_UNAVAILABLE:no configured official API",
    )
    stats_text = ", ".join(f"{key}={value}" for key, value in stats.items())
    return SourceStatus(
        "Japan Investor Sentiment",
        status,
        detail=f"Yahoo Board={detail}; {stats_text}; {', '.join(unavailable)}",
        item_count=aggregate["sample_count"],
    )
