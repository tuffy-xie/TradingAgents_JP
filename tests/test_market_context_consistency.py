"""Regression tests for the immutable report market context contract."""

from tradingagents.dataflows.market import enrich_market_context, resolve_market_context
from tradingagents.graph.propagation import Propagator
from tradingagents.report_consistency import canonical_report_metadata, sanitize_report_section


def test_japan_etf_context_survives_into_initial_state():
    context = enrich_market_context(resolve_market_context("1629.T"), {"quote_type": "ETF"})
    state = Propagator().create_initial_state(
        "1629.T", "2026-08-13", market_context=context,
        verified_market_snapshot="verified snapshot",
    )
    assert state["company_of_interest"] == "1629.T"
    assert state["market_context"]["market"] == "JP"
    assert state["market_context"]["currency"] == "JPY"
    assert state["market_context"]["instrument_type"] == "ETF"
    assert state["verified_market_snapshot"] == "verified snapshot"


def test_report_metadata_is_only_read_from_market_context():
    state = {
        "company_of_interest": "wrong-name",
        "market_context": {"symbol": "6981.T", "market": "JP", "currency": "JPY", "instrument_type": "EQUITY"},
    }
    assert canonical_report_metadata(state) == {
        "symbol": "6981.T", "market": "JP", "currency": "JPY", "instrument_type": "EQUITY",
    }


def test_japan_sentiment_removes_us_community_platform_names():
    text = sanitize_report_section("Reddit and StockTwits were quiet.", "JP", "sentiment_report")
    assert "暂无可用日本情绪数据" not in text
    assert "Reddit" not in text
    assert "StockTwits" not in text
    assert "海外社区情绪" in text
    assert "Reddit" not in text
    assert "StockTwits" not in text
