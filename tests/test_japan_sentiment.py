"""Regression coverage for Japan-local investor sentiment safeguards."""

from datetime import date

from tradingagents.dataflows.japan.models import DataStatus
from tradingagents.dataflows.japan.sentiment import _aggregate, _parse_posts, _summary_item
from tradingagents.dataflows.market import Market, resolve_market_context


def _article(number, user, published_at, body):
    return f"""<article><a href="https://finance.yahoo.co.jp/cm/personal/history/comment?user={user}">user</a>
    <a href="/quote/6981.T/forum/{number}">No.{number}</a>
    <time>{published_at}</time><div class="BbsItem__body">{body}</div></article>"""


def _page(*articles):
    return "<title>(株)村田製作所【6981】：掲示板</title>" + "".join(articles)


def _stats():
    return {
        "raw_samples": 0,
        "filtered_noise": 0,
        "filtered_irrelevant": 0,
        "filtered_missing_published_at": 0,
        "filtered_duplicate": 0,
        "filtered_user_cap": 0,
        "filtered_time_window": 0,
    }


def test_posts_require_direct_issuer_evidence_and_remove_duplicates():
    context = resolve_market_context("6981.T")
    html = _page(
        _article(1, "a" * 64, "2026/8/14 13:00", "村田は上昇を期待する"),
        _article(2, "a" * 64, "2026/8/14 13:01", "村田は上昇を期待する"),
        _article(3, "b" * 64, "2026/8/14 13:02", "今日は暑いですね"),
        _article(4, "c" * 64, "2026/8/14 13:03", "https://spam.example 村田"),
    )
    stats = _stats()
    posts = _parse_posts(html, context, ("村田製作所", "村田製", "村田"), stats)
    assert len(posts) == 1
    assert posts[0].metadata["sentiment"] == "POSITIVE"
    assert stats["filtered_duplicate"] == 1
    assert stats["filtered_irrelevant"] == 1
    assert stats["filtered_noise"] == 1


def test_empty_sample_is_unavailable_not_neutral():
    aggregate = _aggregate([], date(2026, 8, 14))
    assert aggregate["sample_count"] == 0
    assert aggregate["sentiment_score"] is None
    assert aggregate["status"] == "DATA_UNAVAILABLE"
    assert all(window["sentiment_score"] is None for window in aggregate["time_window"].values())
    summary = _summary_item(resolve_market_context("6981.T"), aggregate, date(2026, 8, 14))
    assert summary.status is DataStatus.DATA_UNAVAILABLE


def test_aggregate_keeps_1d_3d_7d_separate_and_weights_recent_posts():
    context = resolve_market_context("6981.T")
    stats = _stats()
    posts = _parse_posts(
        _page(
            _article(1, "a" * 64, "2026/8/14 12:00", "村田は上昇を期待する"),
            _article(2, "b" * 64, "2026/8/12 12:00", "村田は下落が心配"),
            _article(3, "c" * 64, "2026/8/08 12:00", "村田は売り"),
        ),
        context,
        ("村田製作所", "村田製", "村田"),
        stats,
    )
    aggregate = _aggregate(posts, date(2026, 8, 14))
    assert aggregate["time_window"]["1D"]["sample_count"] == 1
    assert aggregate["time_window"]["3D"]["sample_count"] == 2
    assert aggregate["time_window"]["7D"]["sample_count"] == 3
    assert aggregate["time_window"]["1D"]["sentiment_score"] > 0


def test_nvda_stays_in_us_route_without_japan_sentiment_provider():
    assert resolve_market_context("NVDA").market is Market.US


def test_japan_sentiment_provider_is_registered_only_for_japan_bundle():
    from tradingagents.dataflows.japan.official import build_official_japan_providers

    assert "Japan Investor Sentiment" in {
        provider.name for provider in build_official_japan_providers()
    }
