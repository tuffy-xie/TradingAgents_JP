"""J-Quants API V2 provider (official JPX data, API-key authenticated)."""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .http import get_json
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus


class JQuantsProvider:
    """Retrieve official OHLCV and financial records available to the account plan.

    J-Quants V2 uses the ``x-api-key`` header.  This provider does not attempt
    deprecated V1 username/password or refresh-token authentication.
    """

    name = "J-Quants"
    category = "market_data"

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        config = get_config().get("markets", {}).get("jp", {})
        self.api_key = api_key if api_key is not None else os.getenv("JQUANTS_API_KEY", "")
        self.base_url = (base_url or config.get("jquants_api_base_url") or "https://api.jquants.com/v2").rstrip("/")
        self.timeout = float(config.get("request_timeout_seconds", 10))
        self.enabled = bool(config.get("datasources", {}).get("jquants", True))

    async def fetch(self, context: MarketContext, *, start_date: str, end_date: str) -> ProviderResponse:
        if not self.enabled:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DISABLED, detail="disabled by markets.jp.datasources"))
        if not self.api_key:
            return ProviderResponse(SourceStatus(self.name, DataStatus.AUTH_REQUIRED, detail="JQUANTS_API_KEY is not configured"))
        # V2 examples use a five-character local code (e.g. 86970 for 8697).
        response = await get_json(
            f"{self.base_url}/equities/bars/daily",
            params={"code": f"{context.native_symbol}0", "from": start_date.replace("-", ""), "to": end_date.replace("-", "")},
            headers={"x-api-key": self.api_key},
            timeout=self.timeout,
        )
        if response.status != DataStatus.OK:
            return ProviderResponse(SourceStatus(self.name, response.status, detail=response.detail))
        items = tuple(_normalise_records(response.payload, context))
        return ProviderResponse(SourceStatus(self.name, DataStatus.OK, item_count=len(items)), items)


def _records(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        return (item for item in payload if isinstance(item, dict))
    if isinstance(payload, dict):
        for key in ("data", "daily_quotes", "daily_bars", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return (item for item in value if isinstance(item, dict))
    return ()


def _normalise_records(payload: Any, context: MarketContext) -> Iterable[MarketInformation]:
    for record in _records(payload):
        date_value = str(record.get("Date") or record.get("date") or "")
        try:
            timestamp = datetime.fromisoformat(date_value[:10]).replace(tzinfo=UTC)
        except ValueError:
            timestamp = datetime.now(UTC)
        values = {
            key: record[key]
            for key in ("Open", "High", "Low", "Close", "Volume", "TurnoverValue", "open", "high", "low", "close", "volume")
            if key in record
        }
        yield MarketInformation(
            source="J-Quants",
            source_type="official_ohlcv",
            ticker=context.symbol,
            timestamp=timestamp,
            title="J-Quants official daily OHLCV",
            content="Official JPX-derived daily market data.",
            confidence=0.98,
            verified=True,
            layer=InformationLayer.VERIFIED_FACT,
            content_level="structured_data",
            metadata={"ohlcv": values, "raw_code": record.get("Code") or record.get("code")},
        )
