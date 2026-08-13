"""Offline tests for official Japan-provider contracts and no-key fallbacks."""

from __future__ import annotations

import pytest

from tradingagents.dataflows.japan.edinet import (
    EDINETProvider,
    _normalise_documents,
    classify_edinet_document,
)
from tradingagents.dataflows.japan.jquants import JQuantsProvider, _normalise_records
from tradingagents.dataflows.japan.models import DataStatus
from tradingagents.dataflows.japan.tdnet import (
    TDnetProvider,
    classify_tdnet_title,
    normalise_tdnet_records,
)
from tradingagents.dataflows.market import resolve_market_context


@pytest.mark.unit
def test_jquants_without_key_returns_auth_required(monkeypatch):
    monkeypatch.delenv("JQUANTS_API_KEY", raising=False)
    result = __import__("asyncio").run(
        JQuantsProvider().fetch(
            resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13"
        )
    )
    assert result.status.status == DataStatus.AUTH_REQUIRED


@pytest.mark.unit
def test_edinet_without_key_returns_auth_required(monkeypatch):
    monkeypatch.delenv("EDINET_API_KEY", raising=False)
    result = __import__("asyncio").run(
        EDINETProvider().fetch(
            resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13"
        )
    )
    assert result.status.status == DataStatus.AUTH_REQUIRED


@pytest.mark.unit
def test_jquants_normalizes_official_v2_daily_record():
    payload = {"data": [{"Date": "2026-08-12", "Code": "69810", "Open": 100, "Close": 110, "Volume": 20}]}
    items = list(_normalise_records(payload, resolve_market_context("6981.T")))
    assert items[0].verified is True
    assert items[0].metadata["ohlcv"]["Close"] == 110


@pytest.mark.unit
def test_edinet_only_keeps_matching_security_and_labels_large_holding():
    payload = {"results": [
        {"secCode": "69810", "docID": "S100X", "docDescription": "大量保有報告書", "submitDateTime": "2026-08-12T10:00:00+09:00"},
        {"secCode": "12340", "docID": "S100Y", "docDescription": "有価証券報告書"},
    ]}
    items = list(_normalise_documents(payload, resolve_market_context("6981.T")))
    assert len(items) == 1
    assert items[0].source_type == "large_shareholding_report"
    assert classify_edinet_document("四半期報告書", "") == "quarterly_report"


@pytest.mark.unit
def test_tdnet_parser_classifies_verified_disclosure():
    payload = {"items": [{"ticker": "6981", "date": "2026-08-12", "title": "業績予想の上方修正に関するお知らせ", "url": "https://example.test"}]}
    items = list(normalise_tdnet_records(payload, resolve_market_context("6981.T")))
    assert items[0].verified is True
    assert items[0].source_type == "guidance_revision"
    assert classify_tdnet_title("自己株式の取得") == "share_buyback"


@pytest.mark.unit
def test_tdnet_without_authorized_feed_does_not_scrape_public_ui():
    result = __import__("asyncio").run(
        TDnetProvider(feed_url=None).fetch(
            resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13"
        )
    )
    assert result.status.status == DataStatus.DATA_UNAVAILABLE
    assert "not scraped" in result.status.detail
