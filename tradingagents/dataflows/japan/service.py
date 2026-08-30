"""Concurrent, cached collection for normalized Japan-market providers."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Protocol

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import Market, MarketContext

from .cache import JapanDataCache
from .models import (
    DataStatus,
    JapanResearchBundle,
    MarketInformation,
    ProviderResponse,
    SourceStatus,
)
from .truth import build_governance_item

logger = logging.getLogger(__name__)


class JapanDataProvider(Protocol):
    """Minimal provider contract; individual sources own only retrieval/parsing."""

    name: str
    category: str

    async def fetch(
        self, context: MarketContext, *, start_date: str, end_date: str
    ) -> ProviderResponse: ...


class JapanDataService:
    """Collect independent providers concurrently and preserve every source status."""

    def __init__(
        self,
        providers: Sequence[JapanDataProvider] = (),
        *,
        cache: JapanDataCache | None = None,
        source_ttls: dict[str, int] | None = None,
        timeout_seconds: float | None = None,
        max_concurrency: int | None = None,
    ):
        config = get_config().get("markets", {}).get("jp", {})
        self.providers = tuple(providers)
        self.cache = cache or JapanDataCache(get_config()["data_cache_dir"])
        self.source_ttls = dict(config.get("cache_ttl_seconds", {})) | dict(source_ttls or {})
        self.timeout_seconds = float(timeout_seconds or config.get("request_timeout_seconds", 10))
        self.max_concurrency = int(max_concurrency or config.get("max_concurrency", 4))

    async def collect(
        self,
        context: MarketContext,
        *,
        start_date: str,
        end_date: str,
        categories: Iterable[str] | None = None,
        provider_start_dates: Mapping[str, str] | None = None,
    ) -> JapanResearchBundle:
        if context.market != Market.JP:
            raise ValueError("JapanDataService only accepts a JP MarketContext")

        wanted = set(categories or ())
        provider_start_dates = provider_start_dates or {}
        providers = tuple(
            provider for provider in self.providers if not wanted or provider.category in wanted
        )
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def collect_one(provider: JapanDataProvider) -> ProviderResponse:
            async with semaphore:
                return await self._collect_one(
                    provider,
                    context,
                    provider_start_dates.get(provider.name, start_date),
                    end_date,
                )

        responses = await asyncio.gather(*(collect_one(provider) for provider in providers))
        statuses = tuple(response.status for response in responses)
        raw_items = tuple(item for response in responses for item in response.items)
        items = tuple(self._deduplicate(raw_items))
        provider_metadata = {
            response.status.source: dict(response.metadata)
            for response in responses
            if response.metadata
        }
        governance = build_governance_item(items, context.symbol, date.fromisoformat(end_date))
        return JapanResearchBundle(
            ticker=context.symbol,
            items=(*items, governance),
            source_statuses=statuses,
            provider_metadata=provider_metadata,
        )

    async def _collect_one(
        self,
        provider: JapanDataProvider,
        context: MarketContext,
        start_date: str,
        end_date: str,
    ) -> ProviderResponse:
        cache_version = getattr(provider, "cache_version", "1")
        cache_key = f"{provider.name}:{cache_version}:{context.symbol}:{start_date}:{end_date}"
        cached = self.cache.get(provider.category, cache_key)
        if cached:
            response = ProviderResponse.from_dict(cached)
            cached_status = SourceStatus(
                source=response.status.source,
                status=response.status.status,
                fetched_at=response.status.fetched_at,
                detail=response.status.detail,
                item_count=response.status.item_count,
                from_cache=True,
            )
            logger.info("[JapanData] %s %s (cache)", provider.name, cached_status.status)
            return ProviderResponse(
                status=cached_status,
                items=response.items,
                metadata=response.metadata,
            )

        try:
            response = await asyncio.wait_for(
                provider.fetch(context, start_date=start_date, end_date=end_date),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            response = ProviderResponse(
                status=SourceStatus(
                    provider.name, DataStatus.DATA_UNAVAILABLE, detail="request timed out"
                ),
            )
        except Exception as exc:  # provider errors must not fail the research bundle
            logger.warning("[JapanData] %s failed: %s", provider.name, exc)
            response = ProviderResponse(
                status=SourceStatus(
                    provider.name, DataStatus.DATA_UNAVAILABLE, detail=type(exc).__name__
                ),
            )

        if (
            provider.name == "TDnet"
            and response.status.status == DataStatus.DATA_UNAVAILABLE
            and not response.items
        ):
            fallback = self._last_good_tdnet(cache_key, provider.category)
            if fallback is not None:
                cached_response, cache_age = fallback
                detail = response.status.detail or "live refresh failed"
                detail = (
                    f"{detail}; live_refresh_failed=true; "
                    f"fallback=previous_successful_cache; cache_age_seconds={cache_age:.0f}"
                )
                status = SourceStatus(
                    source="TDnet",
                    status=DataStatus.DATA_UNAVAILABLE,
                    fetched_at=response.status.fetched_at,
                    detail=detail,
                    item_count=len(cached_response.items),
                    from_cache=True,
                )
                return ProviderResponse(
                    status=status,
                    items=cached_response.items,
                    metadata=cached_response.metadata,
                )

        logger.info("[JapanData] %s %s", provider.name, response.status.status)
        ttl = int(self.source_ttls.get(provider.category, self.source_ttls.get(provider.name, 900)))
        self.cache.set(provider.category, cache_key, response.to_dict(), ttl)
        return response

    def _last_good_tdnet(
        self, cache_key: str, category: str
    ) -> tuple[ProviderResponse, float] | None:
        record = self.cache.get_stale(category, cache_key)
        if not record:
            return None
        try:
            response = ProviderResponse.from_dict(record["data"])
        except (KeyError, TypeError, ValueError):
            return None
        if response.status.status != DataStatus.OK or not response.items:
            return None
        if any(
            item.source != "TDnet"
            or not item.verified
            or not item.metadata.get("official_index")
            for item in response.items
        ):
            return None
        fetched_at = response.status.fetched_at.timestamp()
        return response, max(0.0, time.time() - fetched_at)

    @staticmethod
    def _deduplicate(items: Sequence[MarketInformation]) -> list[MarketInformation]:
        """Merge syndications into one event while retaining all contributing sources."""
        merged: dict[str, MarketInformation] = {}
        for item in items:
            key = JapanDataService._event_key(item)
            previous = merged.get(key)
            if previous is None:
                merged[key] = item
                continue
            if not JapanDataService._same_observation(previous, item):
                # Similar titles from different sources can have different
                # values, bases or periods.  Preserve both for the governance
                # layer to label rather than silently retaining the first.
                merged[f"{key}:{item.source}:{len(merged)}"] = item
                continue
            source_names = list(previous.metadata.get("cross_sources", [previous.source]))
            if item.source not in source_names:
                source_names.append(item.source)
            metadata = dict(previous.metadata)
            metadata["cross_sources"] = source_names
            metadata["cross_verified"] = previous.verified or item.verified
            merged[key] = MarketInformation(
                source=previous.source,
                source_type=previous.source_type,
                ticker=previous.ticker,
                timestamp=min(previous.timestamp, item.timestamp),
                title=previous.title,
                content=previous.content or item.content,
                url=previous.url,
                confidence=max(previous.confidence, item.confidence),
                verified=previous.verified or item.verified,
                status=previous.status,
                layer=previous.layer,
                content_level=previous.content_level,
                metadata=metadata,
            )
        return list(merged.values())

    @staticmethod
    def _same_observation(left: MarketInformation, right: MarketInformation) -> bool:
        """Only syndications with identical payloads can be safely merged."""
        return (
            left.source_type == right.source_type
            and left.layer == right.layer
            and left.content == right.content
            and left.status == right.status
            and json.dumps(left.metadata, sort_keys=True, default=str)
            == json.dumps(right.metadata, sort_keys=True, default=str)
        )

    @staticmethod
    def _event_key(item: MarketInformation) -> str:
        normalized_title = re.sub(r"[^0-9A-Z一-龯ぁ-んァ-ン]", "", item.title.upper())
        return f"{item.ticker}:{item.timestamp.date().isoformat()}:{normalized_title or item.url or item.source}"
