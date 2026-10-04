"""Decision-context and final-decision consistency regressions."""

from __future__ import annotations

from tradingagents.agents.context import get_japan_decision_context_from_state
from tradingagents.agents.decision_consistency import enforce_decision_consistency
from tradingagents.dataflows.japan.decision import build_japan_decision_context
from tradingagents.graph.propagation import Propagator


def _item(source, source_type, title, metadata=None, *, layer="VERIFIED_FACT"):
    return {
        "source": source,
        "source_type": source_type,
        "title": title,
        "timestamp": "2026-08-13T00:00:00+00:00",
        "layer": layer,
        "metadata": metadata or {},
    }


def _bundle(items):
    freshness = [
        {
            "source": item["source"],
            "source_type": item["source_type"],
            "timestamp": item["timestamp"],
            "status": "CURRENT",
        }
        for item in items
    ]
    return {
        "analysis_date": "2026-08-15",
        "items": [
            *items,
            _item(
                "Japan Evidence Governance",
                "source_of_truth_assessment",
                "governance",
                {"freshness": freshness},
            ),
        ]
    }


def _state(bundle, context):
    return {
        "market_context": {"market": "JP"},
        "japan_data_bundle": bundle,
        "decision_context": context,
        "trader_investment_plan": "**Action**: Buy\n**Stop-Loss Condition**: 日收盘跌破已验证支撑位",
    }


def test_unavailable_is_not_bearish_and_reduces_confidence_only():
    context = build_japan_decision_context({}, "")
    assert context["dimensions"]["supply_demand"]["direction"] == "unavailable"
    assert context["dimensions"]["sentiment"]["direction"] == "unavailable"
    assert context["overall_direction"] == "unavailable"
    assert context["overall_confidence"] == "low"


def test_official_catalyst_is_deduped_and_news_is_not_a_second_core_vote():
    official = _item("TDnet", "share_buyback", "自己株式の取得", {"event_type": "share_buyback"})
    mirrored = _item(
        "Company IR",
        "share_buyback",
        "自己株式取得に関する説明資料",
        {"event_type": "share_buyback"},
    )
    news = _item(
        "Japan News",
        "japan_stock_news",
        "buyback coverage",
        {"cluster_id": "buyback-1", "sentiment": "positive"},
        layer="NEWS_ANALYST_VIEW",
    )
    context = build_japan_decision_context(_bundle([official, mirrored, news]), "Close 100")
    assert context["dimensions"]["official_catalysts"]["evidence_count"] == 1
    assert context["dimensions"]["news"]["evidence_count"] == 1
    assert "duplicate news" in context["dimensions"]["official_catalysts"]["note"].lower()


def test_recent_official_catalyst_is_retained_with_event_age_but_old_one_is_not_current():
    recent = _item("TDnet", "share_buyback", "自己株式の取得", {"event_type": "share_buyback", "date": "2026-08-03"})
    old = _item("TDnet", "share_buyback", "old buyback", {"event_type": "share_buyback", "date": "2026-02-01"})
    bundle = _bundle([recent, old])
    bundle.update({"analysis_date": "2026-08-14", "window_policy": {"official_catalyst_days": 30}})
    context = build_japan_decision_context(bundle, "Close 100")
    catalyst = context["dimensions"]["official_catalysts"]
    assert catalyst["direction"] == "weak_bullish"
    assert catalyst["freshness"] == "RECENT"
    assert catalyst["evidence_count"] == 1
    assert catalyst["events"] == [
        {
            "event_date": "2026-08-03",
            "age_days": 11,
            "freshness": "RECENT",
            "source": "TDnet",
            "event_type": "share_buyback",
            "title": "自己株式の取得",
        }
    ]


def test_guidance_and_consensus_remain_separate_dimensions():
    guidance = _item("Company IR", "guidance_revision", "業績予想修正", {"guidance_change": "UPWARD"})
    consensus = _item(
        "Minkabu Analyst Consensus",
        "japan_analyst_expectations",
        "consensus",
        {"analyst_expectations": {"consensus_rating": "中立", "revision_trend": {"30D": {"metric_upward_revisions": 0, "metric_downward_revisions": 1}}}},
    )
    context = build_japan_decision_context(_bundle([guidance, consensus]), "Close 100")
    assert context["dimensions"]["fundamentals"]["direction"] == "weak_bullish"
    assert context["dimensions"]["analyst_expectations"]["direction"] == "bearish"
    assert "separate" in context["dimensions"]["fundamentals"]["note"].lower()


def test_sentiment_cannot_reverse_core_conclusion_by_itself():
    sentiment = _item(
        "Yahoo Board",
        "japan_investor_post",
        "post",
        {"sentiment": "positive"},
        layer="MARKET_SENTIMENT",
    )
    context = build_japan_decision_context(_bundle([sentiment]), "")
    assert context["dimensions"]["sentiment"]["direction"] == "weak_bullish"
    assert context["overall_direction"] == "unavailable"
    assert context["overall_confidence"] == "low"


def test_source_native_sentiment_aggregate_is_the_only_sentiment_vote():
    aggregate = _item(
        "Japan Investor Sentiment",
        "japan_investor_sentiment_aggregate",
        "source-native aggregate",
        {"sample_count": 11, "sentiment_score": -0.1821, "confidence": 0.7},
        layer="MARKET_SENTIMENT",
    )
    bullish_news = _item(
        "Japan News",
        "japan_stock_news",
        "bullish macro headline",
        {"sentiment": "positive"},
        layer="NEWS_ANALYST_VIEW",
    )

    context = build_japan_decision_context(_bundle([aggregate, bullish_news]), "")
    sentiment = context["dimensions"]["sentiment"]

    assert sentiment["direction"] == "bearish"
    assert sentiment["evidence_count"] == 11
    assert "news and macro are excluded" in sentiment["note"].lower()


def test_decision_context_is_carried_for_jp_final_nodes_only():
    bundle = _bundle([])
    jp_state = Propagator().create_initial_state("8002.T", "2026-08-14", japan_data_bundle=bundle)
    assert "Japan Decision Context" in get_japan_decision_context_from_state(jp_state)
    us_state = Propagator().create_initial_state("NVDA", "2026-08-14")
    assert us_state["decision_context"] == {}
    assert get_japan_decision_context_from_state(us_state) == ""


def test_low_confidence_and_risk_contradictions_are_downgraded():
    low_context = {"overall_confidence": "low", "dimensions": {"risk": {"direction": "unavailable"}}}
    state = _state({}, low_context)
    plan = enforce_decision_consistency(
        state, {"investment_plan": "**Recommendation**: Buy"}, "Research Manager"
    )
    assert "**Recommendation**: Hold" in plan["investment_plan"]

    risk_context = {"overall_confidence": "medium", "dimensions": {"risk": {"direction": "bearish"}}}
    state = _state({}, risk_context)
    final = enforce_decision_consistency(
        state,
        {"final_trade_decision": "**Rating**: Sell", "risk_debate_state": {}},
        "Portfolio Manager",
    )
    assert "**Rating**: Hold" in final["final_trade_decision"]
    assert "trader_portfolio_direction_conflict" in final["final_trade_decision"]


def test_us_decision_result_is_unchanged():
    state = {"market_context": {"market": "US"}}
    result = {"final_trade_decision": "**Rating**: Buy"}
    assert enforce_decision_consistency(state, result, "Portfolio Manager") == result
