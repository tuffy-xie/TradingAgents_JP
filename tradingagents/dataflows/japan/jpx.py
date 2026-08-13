"""JPX public-data provider shell with explicit data-access status."""

from __future__ import annotations

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .models import DataStatus, ProviderResponse, SourceStatus


class JPXProvider:
    """Expose whether a sanctioned machine-readable JPX feed is configured.

    JPX offers a mixture of public webpages/Excel publications and paid data
    services.  We deliberately do not scrape presentation pages or bypass paid
    services.  A sanctioned CSV/JSON feed can be supplied through
    ``markets.jp.jpx_feed_url`` in a deployment configuration; structured
    short-selling and supply/demand adapters are introduced in a later phase.
    """

    name = "JPX"
    category = "market_data"

    def __init__(self, feed_url: str | None = None):
        config = get_config().get("markets", {}).get("jp", {})
        self.feed_url = feed_url or config.get("jpx_feed_url")
        self.enabled = bool(config.get("datasources", {}).get("jpx", True))

    async def fetch(self, context: MarketContext, *, start_date: str, end_date: str) -> ProviderResponse:
        del context, start_date, end_date
        if not self.enabled:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DISABLED, detail="disabled by markets.jp.datasources"))
        if not self.feed_url:
            return ProviderResponse(SourceStatus(
                self.name, DataStatus.DATA_UNAVAILABLE,
                detail="no machine-readable public/authorized JPX feed configured",
            ))
        return ProviderResponse(SourceStatus(
            self.name, DataStatus.DATA_UNAVAILABLE,
            detail="configured JPX feed parser is not enabled for this dataset",
        ))
