"""Regression coverage for JP bundle injection and JP-only report sections."""

from __future__ import annotations

from unittest.mock import MagicMock

from tradingagents.agents.utils.agent_utils import get_japan_data_context_from_state
from tradingagents.dataflows.japan.context import (
    collect_japan_data_bundle,
    render_japan_report_sections,
)
from tradingagents.dataflows.japan.models import JapanResearchBundle
from tradingagents.dataflows.market import resolve_market_context
from tradingagents.graph.propagation import Propagator
from tradingagents.reporting import write_report_tree


def _bundle() -> dict:
    return {
        "ticker": "6981.T",
        "items": [
            {
                "source": "TDnet", "source_type": "share_buyback", "timestamp": "2026-08-12T15:00:00+00:00",
                "title": "自己株式の取得", "url": "https://example.test/tdnet.pdf", "metadata": {},
            },
            {
                "source": "JSF", "source_type": "securities_finance_balance", "timestamp": "2026-08-12T00:00:00+00:00",
                "title": "JSF financing and stock-loan balance", "metadata": {"finance_balance": 721900, "stock_loan_balance": 100},
            },
        ],
        "source_statuses": [
            {"source": "TDnet", "status": "OK"}, {"source": "JPX", "status": "OK"},
            {"source": "JSF", "status": "OK"},
        ],
    }


def test_japan_bundle_is_state_carried_and_us_gets_no_japan_prompt():
    state = Propagator().create_initial_state("6981.T", "2026-08-13", japan_data_bundle=_bundle())
    context = get_japan_data_context_from_state(state)
    assert "自己株式の取得" in context
    assert "finance_balance" in context

    us_state = Propagator().create_initial_state("NVDA", "2026-08-13")
    assert get_japan_data_context_from_state(us_state) == ""


def test_japan_report_sections_are_explicit_and_do_not_fabricate_sentiment(tmp_path):
    sections = render_japan_report_sections(_bundle())
    assert "## 日本官方披露" in sections
    assert "## 信用与需给" in sections
    assert "## 空卖与机构行为" in sections
    assert "## 日本市场情绪" in sections
    assert "数据不可用" in sections

    out = write_report_tree(
        {"market_context": {"market": "JP"}, "japan_data_bundle": _bundle()}, "6981.T", tmp_path
    )
    content = out.read_text(encoding="utf-8")
    assert "日本官方披露" in content
    assert (tmp_path / "0_japan_market_data" / "official_and_supply_demand.md").exists()


def test_japan_sentiment_prompt_appends_to_original_community_sources(monkeypatch):
    from tradingagents.agents.analysts import sentiment_analyst as module
    from tradingagents.agents.schemas import SentimentBand, SentimentReport

    social_calls = {}

    def stocktwits(*args, **kwargs):
        social_calls["stocktwits"] = (args, kwargs)
        return "StockTwits sample"

    def reddit(*args, **kwargs):
        social_calls["reddit"] = (args, kwargs)
        return "Reddit sample"

    monkeypatch.setattr(module, "fetch_stocktwits_messages", stocktwits)
    monkeypatch.setattr(module, "fetch_reddit_posts", reddit)
    monkeypatch.setattr(module.get_news, "func", lambda *_args, **_kwargs: "Yahoo news sample")
    captured = {}
    structured = MagicMock()

    def respond(prompt):
        captured["prompt"] = prompt
        return SentimentReport(
            overall_band=SentimentBand.NEUTRAL, overall_score=5, confidence="low", narrative="DATA UNAVAILABLE"
        )

    structured.invoke.side_effect = respond
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    state = Propagator().create_initial_state("6981.T", "2026-08-13", japan_data_bundle=_bundle())
    result = module.create_sentiment_analyst(llm)(state)
    assert "DATA UNAVAILABLE" in result["sentiment_report"]
    prompt_text = str(captured["prompt"])
    assert "自己株式の取得" not in prompt_text
    assert "Official filings, analyst consensus" in prompt_text
    assert "StockTwits messages" in prompt_text
    assert "Reddit posts" in prompt_text
    assert "Yahoo news sample" in prompt_text
    assert social_calls["stocktwits"] == (
        ("6981.T",),
        {"limit": 30, "start_date": "2026-08-06", "end_date": "2026-08-13"},
    )
    assert social_calls["reddit"] == (
        ("6981.T",),
        {"start_date": "2026-08-06", "end_date": "2026-08-13"},
    )


def test_us_sentiment_preload_call_contract_is_unchanged(monkeypatch):
    from tradingagents.agents.analysts import sentiment_analyst as module
    from tradingagents.agents.schemas import SentimentBand, SentimentReport

    social_calls = {}

    def stocktwits(*args, **kwargs):
        social_calls["stocktwits"] = (args, kwargs)
        return "StockTwits sample"

    def reddit(*args, **kwargs):
        social_calls["reddit"] = (args, kwargs)
        return "Reddit sample"

    monkeypatch.setattr(module, "fetch_stocktwits_messages", stocktwits)
    monkeypatch.setattr(module, "fetch_reddit_posts", reddit)
    monkeypatch.setattr(module.get_news, "func", lambda *_args, **_kwargs: "Yahoo news sample")
    structured = MagicMock()
    structured.invoke.return_value = SentimentReport(
        overall_band=SentimentBand.NEUTRAL,
        overall_score=5,
        confidence="low",
        narrative="No verified evidence",
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    state = Propagator().create_initial_state("NVDA", "2026-08-13")
    module.create_sentiment_analyst(llm)(state)

    assert social_calls["stocktwits"] == (("NVDA",), {"limit": 30})
    assert social_calls["reddit"] == (("NVDA",), {})


def test_financial_official_scan_is_not_shortened_by_trading_horizon(monkeypatch):
    captured = {}

    async def collect(_self, _context, *, start_date, end_date, provider_start_dates):
        captured.update(
            start_date=start_date,
            end_date=end_date,
            provider_start_dates=provider_start_dates,
        )
        return JapanResearchBundle("5016.T", (), ())

    monkeypatch.setattr(
        "tradingagents.dataflows.japan.context.JapanDataService.collect", collect
    )
    monkeypatch.setattr(
        "tradingagents.dataflows.japan.context.build_official_japan_providers", tuple
    )

    bundle = collect_japan_data_bundle(
        resolve_market_context("5016.T"), "2026-08-30", "multi_day"
    )

    assert captured["start_date"] == "2026-08-23"
    assert captured["provider_start_dates"] == {
        "TDnet": "2026-07-30",
        "Company IR": "2026-07-30",
    }
    assert bundle["window_policy"]["official_catalyst_days"] == 14
    assert bundle["window_policy"]["financial_official_scan_days"] == 32
