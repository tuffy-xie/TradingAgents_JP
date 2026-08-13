"""Regression coverage for JP bundle injection and JP-only report sections."""

from __future__ import annotations

from unittest.mock import MagicMock

from tradingagents.agents.utils.agent_utils import get_japan_data_context_from_state
from tradingagents.dataflows.japan.context import render_japan_report_sections
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
    assert "DATA UNAVAILABLE" in sections

    out = write_report_tree(
        {"market_context": {"market": "JP"}, "japan_data_bundle": _bundle()}, "6981.T", tmp_path
    )
    content = out.read_text(encoding="utf-8")
    assert "日本官方披露" in content
    assert (tmp_path / "0_japan_market_data" / "official_and_supply_demand.md").exists()


def test_japan_sentiment_prompt_uses_bundle_not_us_community_sources(monkeypatch):
    from tradingagents.agents.analysts import sentiment_analyst as module
    from tradingagents.agents.schemas import SentimentBand, SentimentReport

    monkeypatch.setattr(module, "fetch_stocktwits_messages", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(module, "fetch_reddit_posts", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))
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
    assert "自己株式の取得" in prompt_text
    assert "StockTwits messages" not in prompt_text
