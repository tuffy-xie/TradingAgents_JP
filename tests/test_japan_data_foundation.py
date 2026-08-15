"""Tests for source transparency, caching and resilient Japan aggregation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from tradingagents.dataflows.japan.cache import JapanDataCache
from tradingagents.dataflows.japan.context import collect_japan_data_bundle
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
        self.start_dates = []

    async def fetch(self, context, *, start_date, end_date):
        self.calls += 1
        self.start_dates.append(start_date)
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
def test_cache_write_failure_is_non_fatal():
    # /dev/null is a file, so it cannot contain a cache directory.
    assert JapanDataCache("/dev/null").set("tdnet", "6981.T", {"value": 1}, 30) is False


@pytest.mark.unit
def test_service_preserves_failure_status_and_conflicting_source_evidence(tmp_path):
    official = _Provider(
        "TDnet",
        ProviderResponse(SourceStatus("TDnet", DataStatus.OK, item_count=1), (_item(),)),
    )
    news = _Provider(
        "Kabutan",
        ProviderResponse(
            SourceStatus("Kabutan", DataStatus.OK, item_count=1), (_item("Kabutan", False),)
        ),
    )
    failed = _Provider("EDINET", error=RuntimeError("offline"))
    service = JapanDataService(
        (official, news, failed), cache=JapanDataCache(tmp_path), timeout_seconds=1
    )

    bundle = asyncio.run(
        service.collect(
            resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13"
        )
    )

    evidence = [item for item in bundle.items if item.source_type == "disclosure"]
    assert len(evidence) == 2
    assert {item.source for item in evidence} == {"TDnet", "Kabutan"}
    assert any(item.source_type == "source_of_truth_assessment" for item in bundle.items)
    assert {status.source: status.status for status in bundle.source_statuses}[
        "EDINET"
    ] == DataStatus.DATA_UNAVAILABLE


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


@pytest.mark.unit
def test_tdnet_expired_successful_cache_falls_back_on_live_failure(tmp_path):
    item = _item()
    item = MarketInformation(
        **{**item.__dict__, "url": "https://www.release.tdnet.info/inbs/official.pdf", "metadata": {"official_index": True}}
    )
    good = ProviderResponse(SourceStatus("TDnet", DataStatus.OK, item_count=1), (item,))
    provider = _Provider("TDnet", ProviderResponse(SourceStatus("TDnet", DataStatus.DATA_UNAVAILABLE, detail="ConnectionError")))
    cache = JapanDataCache(tmp_path)
    key = "TDnet:1:6981.T:2026-08-01:2026-08-13"
    cache.set("news", key, good.to_dict(), -1)
    service = JapanDataService((provider,), cache=cache, timeout_seconds=1)
    result = asyncio.run(service.collect(resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13"))
    status = result.source_statuses[0]
    assert status.from_cache is True
    assert status.status == DataStatus.DATA_UNAVAILABLE
    assert "live_refresh_failed=true" in status.detail
    assert result.items[0].url == item.url
    assert result.items[0].verified is True


@pytest.mark.unit
def test_tdnet_successful_empty_does_not_use_unrelated_expired_cache(tmp_path):
    old = _item()
    old = MarketInformation(**{**old.__dict__, "metadata": {"official_index": True}})
    cache = JapanDataCache(tmp_path)
    key = "TDnet:1:6981.T:2026-08-01:2026-08-13"
    cache.set("news", key, ProviderResponse(SourceStatus("TDnet", DataStatus.OK, item_count=1), (old,)).to_dict(), -1)
    provider = _Provider("TDnet", ProviderResponse(SourceStatus("TDnet", DataStatus.OK, item_count=0), ()))
    service = JapanDataService((provider,), cache=cache, timeout_seconds=1)
    result = asyncio.run(service.collect(resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13"))
    assert result.source_statuses[0].from_cache is False
    assert result.source_statuses[0].status == DataStatus.OK
    assert result.source_statuses[0].item_count == 0


@pytest.mark.unit
def test_tdnet_failure_without_last_good_cache_remains_unavailable(tmp_path):
    provider = _Provider(
        "TDnet",
        ProviderResponse(SourceStatus("TDnet", DataStatus.DATA_UNAVAILABLE, detail="TimeoutError")),
    )
    service = JapanDataService((provider,), cache=JapanDataCache(tmp_path), timeout_seconds=1)
    result = asyncio.run(service.collect(resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13"))
    assert result.source_statuses[0].status == DataStatus.DATA_UNAVAILABLE
    assert result.source_statuses[0].from_cache is False
    assert result.source_statuses[0].item_count == 0


@pytest.mark.unit
def test_tdnet_live_success_replaces_expired_cache(tmp_path):
    old = _item()
    old = MarketInformation(**{**old.__dict__, "metadata": {"official_index": True}})
    new = MarketInformation(
        **{**old.__dict__, "title": "新しい公式披露", "url": "https://www.release.tdnet.info/inbs/new.pdf"}
    )
    cache = JapanDataCache(tmp_path)
    key = "TDnet:1:6981.T:2026-08-01:2026-08-13"
    cache.set("news", key, ProviderResponse(SourceStatus("TDnet", DataStatus.OK, item_count=1), (old,)).to_dict(), -1)
    provider = _Provider("TDnet", ProviderResponse(SourceStatus("TDnet", DataStatus.OK, item_count=1), (new,)))
    service = JapanDataService((provider,), cache=cache, timeout_seconds=1)
    result = asyncio.run(service.collect(resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13"))
    assert result.source_statuses[0].from_cache is False
    assert result.source_statuses[0].status == DataStatus.OK
    assert result.items[0].url == new.url


@pytest.mark.unit
def test_service_allows_official_provider_window_to_exceed_news_window(tmp_path):
    tdnet = _Provider("TDnet", ProviderResponse(SourceStatus("TDnet", DataStatus.OK)))
    news = _Provider("Japan News", ProviderResponse(SourceStatus("Japan News", DataStatus.OK)))
    service = JapanDataService((tdnet, news), cache=JapanDataCache(tmp_path), timeout_seconds=1)
    asyncio.run(
        service.collect(
            resolve_market_context("6981.T"),
            start_date="2026-08-07",
            end_date="2026-08-14",
            provider_start_dates={"TDnet": "2026-07-15"},
        )
    )
    # The cache key incorporates the effective start date; provider calls are
    # deliberately separated rather than forcing news into the 30-day window.
    assert tdnet.start_dates == ["2026-07-15"]
    assert news.start_dates == ["2026-08-07"]


@pytest.mark.unit
def test_us_context_does_not_construct_or_call_edinet(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.dataflows.japan.context.build_official_japan_providers",
        lambda: (_ for _ in ()).throw(AssertionError("Japan providers must not run for US")),
    )
    assert collect_japan_data_bundle(resolve_market_context("NVDA"), "2026-08-13") == {}
