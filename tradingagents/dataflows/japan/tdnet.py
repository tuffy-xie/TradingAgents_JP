"""TDnet timely-disclosure normalization with no undocumented scraping fallback."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .http import get_json
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus


class TDnetProvider:
    """Read a configured, authorized TDnet JSON feed and normalize its disclosures.

    TDnet's public web search is not treated as a stable documented API.  A
    deployer can set ``markets.jp.tdnet_feed_url`` to an authorized feed; until
    then the provider reports DATA_UNAVAILABLE rather than scraping UI HTML.
    """

    name = "TDnet"
    category = "tdnet"

    def __init__(self, feed_url: str | None = None):
        config = get_config().get("markets", {}).get("jp", {})
        self.feed_url = feed_url or config.get("tdnet_feed_url")
        self.timeout = float(config.get("request_timeout_seconds", 10))
        self.enabled = bool(config.get("datasources", {}).get("tdnet", True))

    async def fetch(self, context: MarketContext, *, start_date: str, end_date: str) -> ProviderResponse:
        if not self.enabled:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DISABLED, detail="disabled by markets.jp.datasources"))
        if not self.feed_url:
            return ProviderResponse(SourceStatus(
                self.name, DataStatus.DATA_UNAVAILABLE,
                detail="no authorized TDnet feed configured; public search is not scraped",
            ))
        response = await get_json(
            self.feed_url,
            params={"ticker": context.native_symbol, "start_date": start_date, "end_date": end_date},
            timeout=self.timeout,
        )
        if response.status != DataStatus.OK:
            return ProviderResponse(SourceStatus(self.name, response.status, detail=response.detail))
        items = tuple(normalise_tdnet_records(response.payload, context))
        return ProviderResponse(SourceStatus(self.name, DataStatus.OK, item_count=len(items)), items)


def normalise_tdnet_records(payload: Any, context: MarketContext) -> Iterable[MarketInformation]:
    records = payload if isinstance(payload, list) else (payload.get("items", []) if isinstance(payload, dict) else [])
    for record in records:
        if not isinstance(record, dict):
            continue
        code = str(record.get("ticker") or record.get("code") or "").replace(".T", "")
        if code and code != context.native_symbol:
            continue
        title = str(record.get("title") or "")
        timestamp = _parse_timestamp(record.get("date") or record.get("timestamp"))
        yield MarketInformation(
            source="TDnet",
            source_type=classify_tdnet_title(title),
            ticker=context.symbol,
            timestamp=timestamp,
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


def _parse_timestamp(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)
