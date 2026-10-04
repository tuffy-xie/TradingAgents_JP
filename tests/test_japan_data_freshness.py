"""Stage-8 deterministic freshness regressions for enabled Japan sources."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tradingagents.dataflows import router as interface
from tradingagents.dataflows.errors import NoMarketDataError
from tradingagents.dataflows.japan.cache import JapanDataCache
from tradingagents.dataflows.japan.decision import build_japan_decision_context
from tradingagents.dataflows.japan.freshness import (
    FUTURE_DATA,
    LATEST_AVAILABLE,
    STALE_SOURCE,
    assess_expected_publication_date,
    assess_japan_session_data,
)
from tradingagents.dataflows.japan.http import BytesResponse, JsonResponse
from tradingagents.dataflows.japan.jpx import (
    _XLS_LINK,
    JPXProvider,
    _latest_workbook,
    _page_update_date,
    margin_workbook_data_date,
    normalise_margin_balances,
)
from tradingagents.dataflows.japan.jquants import JQuantsProvider
from tradingagents.dataflows.japan.jsf import _balance_freshness
from tradingagents.dataflows.japan.models import (
    DataStatus,
    InformationLayer,
    MarketInformation,
    ProviderResponse,
    SourceStatus,
)
from tradingagents.dataflows.japan.service import JapanDataService
from tradingagents.dataflows.japan.trading_calendar import latest_japan_trading_day
from tradingagents.dataflows.market import resolve_market_context
from tradingagents.dataflows.vendors.reddit import _reddit_post_in_window
from tradingagents.dataflows.vendors.stocktwits import _inside_explicit_window
from tradingagents.dataflows.vendors.yahoo.market import get_stock_stats_indicators_window
from tradingagents.dataflows.vendors.yahoo.news import _in_news_window
from tradingagents.dataflows.vendors.yahoo.ohlcv import _assert_ohlcv_not_stale


def _item(
    source: str,
    source_type: str,
    observed: date,
    *,
    title: str = "observation",
) -> MarketInformation:
    return MarketInformation(
        source=source,
        source_type=source_type,
        ticker="5016.T",
        timestamp=datetime.combine(observed, datetime.min.time(), tzinfo=UTC),
        title=title,
        verified=True,
        layer=InformationLayer.VERIFIED_FACT,
    )


def test_japan_calendar_accepts_weekend_and_national_holiday_previous_session():
    assert latest_japan_trading_day(date(2026, 8, 30)) == date(2026, 8, 28)
    assert latest_japan_trading_day(date(2026, 8, 11)) == date(2026, 8, 10)


def test_session_and_source_native_delay_are_distinct_from_stale():
    weekend = assess_japan_session_data(date(2026, 8, 28), date(2026, 8, 30))
    t_plus_one = assess_japan_session_data(
        date(2026, 8, 27),
        date(2026, 8, 30),
        publication_lag_sessions=1,
        native_cadence="T_PLUS_ONE",
    )
    stale = assess_japan_session_data(date(2026, 8, 26), date(2026, 8, 30))
    assert weekend.status == LATEST_AVAILABLE
    assert t_plus_one.status == LATEST_AVAILABLE
    assert stale.status == STALE_SOURCE


def test_documented_weekly_release_is_latest_available_not_same_day_stale():
    result = assess_expected_publication_date(
        date(2026, 8, 25),
        date(2026, 8, 30),
        expected_latest=date(2026, 8, 25),
        native_cadence="WEEKLY_TUESDAY",
    )
    assert result.status == LATEST_AVAILABLE


def test_japan_ohlcv_requires_exact_latest_tse_session_and_rejects_future():
    _assert_ohlcv_not_stale(
        pd.DataFrame({"Date": ["2026-08-28"], "Close": [100]}),
        "2026-08-30",
        "5016.T",
    )
    with pytest.raises(NoMarketDataError, match="expected the latest completed TSE session"):
        _assert_ohlcv_not_stale(
            pd.DataFrame({"Date": ["2026-08-27"], "Close": [100]}),
            "2026-08-30",
            "5016.T",
        )


def test_volume_only_placeholder_does_not_advance_latest_complete_ohlc():
    frame = pd.DataFrame(
        {
            "Date": ["2026-09-14", "2026-09-15"],
            "Open": [100.0, None],
            "High": [103.0, None],
            "Low": [99.0, None],
            "Close": [102.0, None],
            "Volume": [1000, 1200],
        }
    )

    with pytest.raises(NoMarketDataError, match="latest row is 2026-09-14"):
        _assert_ohlcv_not_stale(frame, "2026-09-16", "6981.T")


def test_jsf_pre_session_uses_previous_completed_session_as_expected_latest():
    jst = ZoneInfo("Asia/Tokyo")
    pre_open = _balance_freshness(
        [_item("JSF", "securities_finance_balance", date(2026, 9, 15))],
        date(2026, 9, 16),
        now=datetime(2026, 9, 16, 0, 59, tzinfo=jst),
    )
    after_close = _balance_freshness(
        [_item("JSF", "securities_finance_balance", date(2026, 9, 15))],
        date(2026, 9, 16),
        now=datetime(2026, 9, 16, 16, 0, tzinfo=jst),
    )

    assert pre_open.status == LATEST_AVAILABLE
    assert pre_open.expected_latest == date(2026, 9, 15)
    assert after_close.status == STALE_SOURCE
    assert after_close.expected_latest == date(2026, 9, 16)


def test_configured_market_fallback_runs_after_stale_primary(monkeypatch):
    def stale(*_args, **_kwargs):
        raise NoMarketDataError("5016.T", "5016.T", "STALE_SOURCE")

    monkeypatch.setattr(interface, "get_vendor", lambda *_args: "yfinance,alpha_vantage")
    monkeypatch.setitem(interface.VENDOR_METHODS["get_stock_data"], "yfinance", stale)
    monkeypatch.setitem(
        interface.VENDOR_METHODS["get_stock_data"],
        "alpha_vantage",
        lambda *_args, **_kwargs: "fresh fallback",
    )
    assert (
        interface.route_to_vendor(
            "get_stock_data", "5016.T", "2026-08-20", "2026-08-30"
        )
        == "fresh fallback"
    )
    with pytest.raises(NoMarketDataError, match="future"):
        _assert_ohlcv_not_stale(
            pd.DataFrame({"Date": ["2026-09-01"], "Close": [100]}),
            "2026-08-31",
            "5016.T",
        )


def test_indicator_output_labels_underlying_latest_bar(monkeypatch):
    monkeypatch.setattr(
        "tradingagents.dataflows.vendors.yahoo.market._get_stock_stats_bulk",
        lambda *_args: {"2026-08-27": "99", "2026-08-28": "100"},
    )
    result = get_stock_stats_indicators_window("5016.T", "rsi", "2026-08-30", 3)
    assert "Underlying OHLCV latest completed bar: 2026-08-28" in result
    assert "2026-08-30: N/A: Not a trading day" in result


def test_jquants_http_ok_stale_daily_bar_is_withheld(monkeypatch):
    responses = iter(
        [
            JsonResponse(
                DataStatus.OK,
                {"data": [{"Date": "2026-08-27", "Code": "50160", "C": 100}]},
            ),
            JsonResponse(
                DataStatus.OK,
                {"data": [{"Date": "2026-08-28", "Code": "50160"}]},
            ),
            JsonResponse(DataStatus.OK, {"data": []}),
        ]
    )

    async def fake_get_json(*_args, **_kwargs):
        return next(responses)

    monkeypatch.setattr("tradingagents.dataflows.japan.jquants.get_json", fake_get_json)
    response = asyncio.run(
        JQuantsProvider(api_key="test").fetch(
            resolve_market_context("5016.T"),
            start_date="2026-08-20",
            end_date="2026-08-30",
        )
    )
    assert not any(item.source_type == "official_ohlcv" for item in response.items)
    assert "daily_bars=STALE_SOURCE" in response.status.detail
    assert response.metadata["daily_bars_freshness"]["status"] == STALE_SOURCE


def test_jquants_plan_window_400_is_retried_and_classified_stale(monkeypatch):
    calls = []

    async def fake_get_json(url, *, params=None, **_kwargs):
        calls.append((url, params))
        if "/equities/bars/daily" in url:
            if "from" in (params or {}):
                return JsonResponse(DataStatus.DATA_UNAVAILABLE, detail="HTTP 400")
            return JsonResponse(
                DataStatus.OK,
                {"data": [{"Date": "2026-06-08", "Code": "50160", "C": 100}]},
            )
        return JsonResponse(DataStatus.OK, {"data": []})

    monkeypatch.setattr("tradingagents.dataflows.japan.jquants.get_json", fake_get_json)
    response = asyncio.run(
        JQuantsProvider(api_key="test").fetch(
            resolve_market_context("5016.T"),
            start_date="2026-08-20",
            end_date="2026-08-30",
        )
    )
    assert len([call for call in calls if "/equities/bars/daily" in call[0]]) == 2
    assert response.metadata["daily_bars_freshness"]["status"] == STALE_SOURCE
    assert "daily_bars=STALE_SOURCE" in response.status.detail
    assert not any(item.source_type == "official_ohlcv" for item in response.items)


def test_jquants_only_latest_bar_is_labelled_latest_available(monkeypatch):
    async def fake_get_json(url, **_kwargs):
        if "/equities/bars/daily" in url:
            return JsonResponse(
                DataStatus.OK,
                {
                    "data": [
                        {"Date": "2026-08-27", "Code": "50160", "C": 99},
                        {"Date": "2026-08-28", "Code": "50160", "C": 100},
                    ]
                },
            )
        return JsonResponse(DataStatus.OK, {"data": []})

    monkeypatch.setattr("tradingagents.dataflows.japan.jquants.get_json", fake_get_json)
    response = asyncio.run(
        JQuantsProvider(api_key="test").fetch(
            resolve_market_context("5016.T"),
            start_date="2026-08-20",
            end_date="2026-08-30",
        )
    )
    assert [item.metadata["freshness_status"] for item in response.items] == [
        "HISTORICAL_OBSERVATION",
        LATEST_AVAILABLE,
    ]


def test_jpx_publication_date_and_previous_session_margin_are_separate(monkeypatch):
    short_html = '<td>2026/08/28</td><a href="/Short_Positions.xls">short</a>'
    margin_html = """
        <div class="JPX-site-update">2026/08/28 更新</div>
        <th>2026年8月27日申込現在</th>
        <a href="/mtdailyk2026082700.xls">margin</a>
    """

    async def fake_get_text(url, **_kwargs):
        return DataStatus.OK, short_html if "short-selling" in url else margin_html, ""

    async def fake_get_bytes(*_args, **_kwargs):
        return BytesResponse(DataStatus.OK, b"fixture")

    monkeypatch.setattr("tradingagents.dataflows.japan.jpx.get_text", fake_get_text)
    monkeypatch.setattr("tradingagents.dataflows.japan.jpx.get_bytes", fake_get_bytes)
    monkeypatch.setattr(
        "tradingagents.dataflows.japan.jpx.normalise_short_positions",
        lambda *_args: [],
    )
    monkeypatch.setattr(
        "tradingagents.dataflows.japan.jpx.normalise_margin_balances",
        lambda *_args: [_item("JPX", "margin_trading_balance", date(2026, 8, 27))],
    )
    response = asyncio.run(
        JPXProvider().fetch(
            resolve_market_context("5016.T"),
            start_date="2026-08-20",
            end_date="2026-08-30",
        )
    )
    assert response.status.status == DataStatus.OK
    assert response.items[0].metadata["freshness_status"] == LATEST_AVAILABLE
    assert response.items[0].metadata["data_date"] == "2026-08-27"
    assert response.items[0].metadata["published_at"] == "2026-08-28"
    assert response.metadata["short_positions_freshness"]["status"] == LATEST_AVAILABLE


def test_jpx_margin_page_separates_publication_from_japanese_row_date():
    html = """
        <div class="JPX-site-update">2026/08/28 更新</div>
        <th>2026年8月27日申込現在</th>
        <a href="/mtdailyk2026082700.xls">margin</a>
    """
    assert _page_update_date(html) == date(2026, 8, 28)


def test_jpx_complete_workbooks_with_no_issuer_row_are_true_empty(monkeypatch):
    short_html = '<td>2026/08/28</td><a href="/Short_Positions.xls">short</a>'
    margin_html = """
        <div class="JPX-site-update">2026/08/28 更新</div>
        <a href="/mtdailyk2026082700.xls">margin</a>
    """

    async def fake_get_text(url, **_kwargs):
        return DataStatus.OK, short_html if "short-selling" in url else margin_html, ""

    async def fake_get_bytes(*_args, **_kwargs):
        return BytesResponse(DataStatus.OK, b"fixture")

    monkeypatch.setattr("tradingagents.dataflows.japan.jpx.get_text", fake_get_text)
    monkeypatch.setattr("tradingagents.dataflows.japan.jpx.get_bytes", fake_get_bytes)
    monkeypatch.setattr(
        "tradingagents.dataflows.japan.jpx.normalise_short_positions", lambda *_args: []
    )
    monkeypatch.setattr(
        "tradingagents.dataflows.japan.jpx.normalise_margin_balances", lambda *_args: []
    )
    monkeypatch.setattr(
        "tradingagents.dataflows.japan.jpx.margin_workbook_data_date",
        lambda *_args: date(2026, 8, 27),
    )

    response = asyncio.run(
        JPXProvider().fetch(
            resolve_market_context("5016.T"),
            start_date="2026-08-20",
            end_date="2026-08-30",
        )
    )
    assert response.status.status == DataStatus.OK
    assert response.items == ()
    assert response.metadata["margin_balances_freshness"]["status"] == LATEST_AVAILABLE


def test_jpx_margin_workbook_extended_security_code_matches_canonical(monkeypatch):
    frame = pd.DataFrame([[None] * 23 for _ in range(8)])
    frame.iloc[2, 1] = "2026-08-27"
    frame.iloc[7, 3] = "JX Advanced Metals"
    frame.iloc[7, 4] = "Prime"
    frame.iloc[7, 5] = "Loan"
    frame.iloc[7, 6] = "50160"
    frame.iloc[7, 8] = "100"
    frame.iloc[7, 9] = "10"
    frame.iloc[7, 11] = "200"
    frame.iloc[7, 12] = "20"
    frame.iloc[7, 14] = "0.5"
    monkeypatch.setattr(pd, "read_excel", lambda *_args, **_kwargs: frame)

    items = normalise_margin_balances(
        b"fixture", resolve_market_context("5016.T"), "https://example.test/margin.xls"
    )
    assert len(items) == 1
    assert items[0].timestamp.date() == date(2026, 8, 27)
    assert margin_workbook_data_date(b"fixture") == date(2026, 8, 27)


def test_jsf_same_session_confirmed_balance_is_latest_available():
    result = _balance_freshness(
        [_item("JSF", "securities_finance_balance", date(2026, 8, 28))],
        date(2026, 8, 30),
    )
    assert result.status == LATEST_AVAILABLE


def test_jpx_future_publication_cannot_enter_historical_analysis(monkeypatch):
    html = '<td>2026/08/28</td><a href="/Short_Positions.xls">short</a>'

    async def fake_get_text(*_args, **_kwargs):
        return DataStatus.OK, html, ""

    monkeypatch.setattr("tradingagents.dataflows.japan.jpx.get_text", fake_get_text)
    response = asyncio.run(
        JPXProvider().fetch(
            resolve_market_context("5016.T"),
            start_date="2026-08-01",
            end_date="2026-08-27",
        )
    )
    assert response.items == ()
    assert "FUTURE_DATA" in response.status.detail


def test_latest_workbook_requires_source_publication_date():
    url, published = _latest_workbook(
        '<a href="/Short_Positions.xls">short</a>',
        _XLS_LINK,
        "https://www.jpx.co.jp/index.html",
    )
    assert url is not None
    assert published is None


class _FreshnessSensitiveProvider:
    name = "Fresh Daily"
    category = "market_data"
    cache_version = "1"
    cache_requires_completed_window = True

    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def fetch(self, *_args, **_kwargs):
        self.calls += 1
        return self.response


def test_same_day_cache_cannot_hide_later_source_publication(tmp_path):
    old = ProviderResponse(
        SourceStatus(
            "Fresh Daily",
            DataStatus.OK,
            fetched_at=datetime(2026, 8, 28, 6, tzinfo=UTC),
            item_count=1,
        ),
        (_item("Fresh Daily", "official_ohlcv", date(2026, 8, 27)),),
    )
    new = ProviderResponse(
        SourceStatus("Fresh Daily", DataStatus.OK, item_count=1),
        (_item("Fresh Daily", "official_ohlcv", date(2026, 8, 28)),),
    )
    provider = _FreshnessSensitiveProvider(new)
    cache = JapanDataCache(tmp_path)
    key = "Fresh Daily:1:5016.T:2026-08-20:2026-08-28"
    cache.set("market_data", key, old.to_dict(), 3600)
    bundle = asyncio.run(
        JapanDataService((provider,), cache=cache).collect(
            resolve_market_context("5016.T"),
            start_date="2026-08-20",
            end_date="2026-08-28",
        )
    )
    assert provider.calls == 1
    assert any(item.timestamp.date() == date(2026, 8, 28) for item in bundle.items)


def test_service_excludes_future_provider_items(tmp_path):
    response = ProviderResponse(
        SourceStatus("Future", DataStatus.OK, item_count=2),
        (
            _item("Future", "event", date(2026, 8, 28)),
            _item("Future", "event", date(2026, 8, 31)),
        ),
    )
    provider = _FreshnessSensitiveProvider(response)
    provider.name = "Future"
    bundle = asyncio.run(
        JapanDataService((provider,), cache=JapanDataCache(tmp_path)).collect(
            resolve_market_context("5016.T"),
            start_date="2026-08-20",
            end_date="2026-08-30",
        )
    )
    assert not any(item.timestamp.date() > date(2026, 8, 30) for item in bundle.items)
    assert "future_items_excluded=1" in bundle.source_statuses[0].detail


def test_future_assessment_is_never_latest_available():
    result = assess_japan_session_data(date(2026, 8, 31), date(2026, 8, 30))
    assert result.status == FUTURE_DATA


def test_japan_news_requires_timestamp_and_excludes_old_or_future_articles():
    start = datetime(2026, 8, 24)
    end = datetime(2026, 8, 30)
    assert not _in_news_window(None, start, end, allow_undated_live=False)
    assert not _in_news_window(datetime(2026, 8, 10, tzinfo=UTC), start, end)
    assert not _in_news_window(datetime(2026, 8, 31, tzinfo=UTC), start, end)
    assert _in_news_window(datetime(2026, 8, 28, tzinfo=UTC), start, end)


def test_japan_social_sources_use_explicit_analysis_window():
    assert _inside_explicit_window(
        "2026-08-28T09:00:00Z", "2026-08-24", "2026-08-30"
    )
    assert not _inside_explicit_window(
        "2026-08-31T00:00:00Z", "2026-08-24", "2026-08-30"
    )
    assert not _inside_explicit_window("", "2026-08-24", "2026-08-30")
    assert _reddit_post_in_window(
        {"created_utc": datetime(2026, 8, 28, tzinfo=UTC).timestamp()},
        "2026-08-24",
        "2026-08-30",
    )
    assert not _reddit_post_in_window(
        {"created_utc": datetime(2026, 8, 31, tzinfo=UTC).timestamp()},
        "2026-08-24",
        "2026-08-30",
    )
    assert not _reddit_post_in_window({}, "2026-08-24", "2026-08-30")


def test_invalid_japan_decision_date_cannot_use_machine_time_for_freshness():
    context = build_japan_decision_context(
        {
            "analysis_date": "not-a-date",
            "items": [
                {
                    "source": "TDnet",
                    "source_type": "buyback",
                    "timestamp": "2026-08-28T00:00:00+00:00",
                    "metadata": {"event_type": "buyback"},
                }
            ],
        }
    )
    assert context["evidence_count"] == 0
    assert context["overall_direction"] == "unavailable"
