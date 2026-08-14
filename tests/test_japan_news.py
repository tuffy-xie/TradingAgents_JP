"""Regression tests for strict Japan news inclusion rules."""

from datetime import UTC, datetime

from tradingagents.dataflows.japan.models import InformationLayer, MarketInformation
from tradingagents.dataflows.japan.news import (
    _Candidate,
    _dedup,
    _discover_candidates,
    _published_at,
    _validate_detail,
)
from tradingagents.dataflows.market import Market, resolve_market_context


def _listing() -> str:
    return """
    <a href="https://finance.yahoo.co.jp/quote/6981.T">村田製作所【6981.T】</a>
    <a href="/news/detail/direct">村田製作所の業績見通しを発表</a>
    <a href="https://login.yahoo.co.jp/config/login">ログインしてポートフォリオを表示</a>
    <a href="/quote/9999.T">他社の株価</a>
    """


def _detail(*, title: str, description: str, timestamp: str | None = "2026-08-13T09:00:00Z") -> str:
    time = f'"datePublished":"{timestamp}"' if timestamp else ""
    return f'<title>{title}</title><meta name="description" content="{description}"><script>{time}</script>'


def test_listing_discovery_rejects_navigation_and_non_detail_links():
    stats = dict.fromkeys(
        (
            "raw_candidates",
            "filtered_navigation_login_ad",
            "filtered_irrelevant_stock",
            "filtered_missing_published_at",
            "filtered_time_window",
            "filtered_detail_unavailable",
        ),
        0,
    )
    candidates = _discover_candidates(
        "Yahoo Finance Japan",
        _listing(),
        "https://finance.yahoo.co.jp/quote/6981.T/news",
        resolve_market_context("6981.T"),
        stats,
    )
    assert len(candidates) == 1
    assert candidates[0].issuer_tokens == ("村田製作所", "村田製")
    assert stats["filtered_navigation_login_ad"] >= 2


def test_detail_requires_published_time_and_direct_issuer_evidence():
    context = resolve_market_context("6981.T")
    candidate = _Candidate(
        "Yahoo Finance Japan",
        "村田製作所の業績見通しを発表",
        "https://example/news",
        ("村田製作所",),
    )
    accepted, reason = _validate_detail(
        candidate,
        _detail(title="村田製作所の業績見通し", description="村田製作所の発表"),
        context,
    )
    assert reason is None
    assert accepted is not None
    assert accepted.timestamp == datetime(2026, 8, 13, 9, tzinfo=UTC)
    assert accepted.metadata["relevance"] == "DIRECT"

    unrelated, reason = _validate_detail(
        candidate, _detail(title="他社決算", description="他社の発表"), context
    )
    assert unrelated is None
    assert reason == "filtered_irrelevant_stock"

    undated, reason = _validate_detail(
        candidate, _detail(title="村田製作所", description="村田製作所", timestamp=None), context
    )
    assert undated is None
    assert reason == "filtered_missing_published_at"


def test_kabutan_uses_publication_date_not_page_update_time():
    html = '<meta name="PublicationDate" content="2026/08/06 07:32:01"><time>2026-08-14T12:55+09:00</time>'
    assert _published_at("Kabutan", html) == datetime(2026, 8, 6, 7, 32, 1, tzinfo=UTC)


def test_dedup_emits_event_cluster_metadata():
    base = MarketInformation(
        source="Kabutan",
        source_type="japan_stock_news",
        ticker="6981.T",
        timestamp=datetime(2026, 8, 13, tzinfo=UTC),
        title="村田製作所の決算",
        layer=InformationLayer.NEWS_ANALYST_VIEW,
    )
    duplicate = MarketInformation(**{**base.__dict__, "source": "Yahoo Finance Japan"})
    clustered = _dedup([base, duplicate])
    assert len(clustered) == 1
    assert clustered[0].metadata["source_count"] == 2
    assert clustered[0].metadata["sources"] == ["Kabutan", "Yahoo Finance Japan"]
    assert clustered[0].metadata["canonical_event"] == "村田製作所の決算"


def test_us_symbol_remains_outside_japan_route():
    assert resolve_market_context("NVDA").market is Market.US


def test_us_bundle_does_not_build_or_call_japan_providers(monkeypatch):
    import tradingagents.dataflows.japan.context as japan_context

    monkeypatch.setattr(
        japan_context,
        "build_official_japan_providers",
        lambda: (_ for _ in ()).throw(AssertionError("Japan provider must not be built for NVDA")),
    )
    assert (
        japan_context.collect_japan_data_bundle(resolve_market_context("NVDA"), "2026-08-14") == {}
    )
