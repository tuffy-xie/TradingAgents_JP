"""Tests for source transparency, caching and resilient Japan aggregation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from tradingagents.dataflows.japan.cache import JapanDataCache
from tradingagents.dataflows.japan.models import (
    DataStatus,
    InformationLayer,
    MarketInformation,
    ProviderResponse,
    SourceStatus,
)
from tradingagents.dataflows.japan.service import JapanDataService
from tradingagents.dataflows.market import resolve_market_context


class _Provider:
    def __init__(self, name, response=None, error=None):
        self.name, self.category, self.response, self.error = name, "news", response, error
        self.calls = 0

    async def fetch(self, context, *, start_date, end_date):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response


def _item(source="TDnet", verified=True):
    return MarketInformation(
        source=source,
        source_type="disclosure",
        ticker="6981.T",
        timestamp=datetime(2026, 8, 13, tzinfo=UTC),
        title="業績予想の修正に関するお知らせ",
        content="Guidance revised.",
        confidence=0.98,
        verified=verified,
        layer=InformationLayer.VERIFIED_FACT if verified else InformationLayer.NEWS_ANALYST_VIEW,
    )


@pytest.mark.unit
def test_json_cache_respects_namespace_and_ttl(tmp_path):
    cache = JapanDataCache(tmp_path)
    cache.set("tdnet", "6981.T", {"value": 1}, 30)
    assert cache.get("tdnet", "6981.T") == {"value": 1}
    assert cache.get("news", "6981.T") is None


@pytest.mark.unit
def test_service_preserves_failure_status_and_deduplicates_sources(tmp_path):
    official = _Provider(
        "TDnet",
        ProviderResponse(SourceStatus("TDnet", DataStatus.OK, item_count=1), (_item(),)),
    )
    news = _Provider(
        "Kabutan",
        ProviderResponse(SourceStatus("Kabutan", DataStatus.OK, item_count=1), (_item("Kabutan", False),)),
    )
    failed = _Provider("EDINET", error=RuntimeError("offline"))
    service = JapanDataService((official, news, failed), cache=JapanDataCache(tmp_path), timeout_seconds=1)

    bundle = asyncio.run(
        service.collect(resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13")
    )

    assert len(bundle.items) == 1
    assert bundle.items[0].verified is True
    assert bundle.items[0].metadata["cross_sources"] == ["TDnet", "Kabutan"]
    assert {status.source: status.status for status in bundle.source_statuses}["EDINET"] == DataStatus.DATA_UNAVAILABLE


@pytest.mark.unit
def test_service_reuses_cached_provider_response(tmp_path):
    provider = _Provider(
        "TDnet",
        ProviderResponse(SourceStatus("TDnet", DataStatus.OK, item_count=1), (_item(),)),
    )
    service = JapanDataService((provider,), cache=JapanDataCache(tmp_path), timeout_seconds=1)
    context = resolve_market_context("6981.T")
    asyncio.run(service.collect(context, start_date="2026-08-01", end_date="2026-08-13"))
    second = asyncio.run(service.collect(context, start_date="2026-08-01", end_date="2026-08-13"))
    assert provider.calls == 1
    assert second.source_statuses[0].from_cache is True
