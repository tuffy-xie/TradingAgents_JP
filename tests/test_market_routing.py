"""Regression tests for market-aware routing before Japan providers exist."""

import pytest

from tradingagents.dataflows.market import Market, ProviderRouter, resolve_market_context
from tradingagents.graph.propagation import Propagator


@pytest.mark.unit
@pytest.mark.parametrize("raw", ["NVDA", "AAPL", "TSLA", "MSFT"])
def test_us_tickers_keep_legacy_market_route(raw):
    context = resolve_market_context(raw)
    assert context.market is Market.US
    assert context.symbol == raw
    assert ProviderRouter().providers_for(context, "market_data") == ()


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    ("6981.T", "6981.T"),
    ("5801.T", "5801.T"),
    ("285A.T", "285A.T"),
    ("7013.T", "7013.T"),
    ("TSE:6981", "6981.T"),
    ("TYO:6981", "6981.T"),
    ("6981", "6981.T"),
    ("285A", "285A.T"),
])
def test_japan_tickers_resolve_to_canonical_tse_symbol(raw, expected):
    context = resolve_market_context(raw)
    assert context.market is Market.JP
    assert context.symbol == expected
    assert context.exchange == "TSE"
    assert context.currency == "JPY"
    assert context.timezone == "Asia/Tokyo"


@pytest.mark.unit
def test_unverified_bare_security_code_is_not_guessed_as_japan():
    context = resolve_market_context("9999")
    assert context.market is Market.UNKNOWN
    assert context.resolved_by == "unverified_bare_security_code"


@pytest.mark.unit
def test_japan_provider_order_is_isolated_from_us_route():
    router = ProviderRouter()
    assert router.providers_for(resolve_market_context("6981.T"), "market_data") == (
        "jquants", "jpx", "yfinance",
    )
    assert router.providers_for(resolve_market_context("NVDA"), "fundamentals") == ()


@pytest.mark.unit
def test_initial_state_carries_market_context_and_canonical_japan_symbol():
    state = Propagator().create_initial_state("TSE:6981", "2026-08-13")
    assert state["company_of_interest"] == "6981.T"
    assert state["market_context"]["market"] == "JP"
