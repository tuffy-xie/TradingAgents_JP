from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
from langchain_core.messages import ToolMessage

from tradingagents.agents.analysts import sentiment_analyst as sentiment_module
from tradingagents.agents.analysts.news_analyst import _apply_japan_news_authority
from tradingagents.agents.schemas import SentimentBand, SentimentReport
from tradingagents.agents.utils.evidence_enforcement import (
    enforce_agent_output,
    enforce_agent_result,
)
from tradingagents.agents.utils.evidence_registry import (
    build_run_manifest,
    capture_agent_evidence,
    initialize_evidence_registry,
    render_downstream_evidence_context,
)
from tradingagents.agents.utils.execution_validation import (
    enforce_execution_math,
    million_jpy_to_oku,
    validate_execution_plan,
)
from tradingagents.dataflows.japan.context import (
    render_japan_audience_context,
    render_japan_financial_context,
    render_japan_report_sections,
)
from tradingagents.dataflows.japan.edinet_db_guidance_selector import (
    select_latest_guidance_record,
)
from tradingagents.dataflows.japan.edinet_db_selector import (
    select_latest_earnings_record,
)
from tradingagents.dataflows.japan.trading_calendar import (
    latest_completed_japan_session,
)
from tradingagents.dataflows.stockstats_utils import _filter_japan_completed_sessions
from tradingagents.graph.setup import _observe_agent_node


def _jp_state(**extra):
    state = {
        "market_context": {"market": "JP", "symbol": "5801.T"},
        "company_of_interest": "5801.T",
        "trade_date": "2026-08-31",
        "japan_data_bundle": {"items": [], "provider_metadata": {}},
        "evidence_registry": [],
        "evidence_audit": [],
        "messages": [],
        "validated_execution": {},
    }
    state.update(extra)
    return state


def _actual_record(**extra):
    return {
        "fiscal_year_end": "2027-03-31",
        "quarter": 1,
        "disclosure_date": "2026-08-06",
        "revenue": 365_221,
        "operating_income": 25_457,
        "ordinary_income": 30_953,
        "net_income": 22_229,
        "eps": 31.60,
        "forecast_revenue": 1_530_000,
        "forecast_operating_income": 123_000,
        "forecast_ordinary_income": 143_000,
        "forecast_net_income": 105_000,
        "forecast_eps": 149.25,
        **extra,
    }


def test_market_tool_fact_keeps_verified_identity_downstream():
    tool = ToolMessage(
        content="2026-08-31 Close 3926; SMA50 3900; ATR 180",
        name="get_stock_data",
        tool_call_id="market-1",
    )
    state = _jp_state(messages=[tool])
    captured = capture_agent_evidence(
        state, {"market_report": "Close 3926 from get_stock_data."}, "Market Analyst"
    )
    combined = {**state, **captured}

    context = render_downstream_evidence_context(combined)
    guarded = enforce_agent_output(
        combined,
        "这些行情和技术指标没有独立工具验证。",
        "Research Manager",
    )

    assert "VERIFIED_TOOL_OUTPUT" in context
    assert "不得降级" in guarded.text
    assert "verified_market_fact_downgraded" in guarded.warnings


def test_market_tool_fact_cannot_be_relabelled_as_missing_upstream_support():
    state = _jp_state(
        evidence_registry=[
            {
                "domain": "MARKET",
                "verification_status": "VERIFIED_TOOL_OUTPUT",
                "allowed_for_current_decision": True,
                "claim_type": "FACT",
                "value": "Close 4020 ATR 319.3",
            }
        ]
    )

    guarded = enforce_agent_output(
        state,
        "这些技术价位缺少上游数据验证，因此只能来自辩论。",
        "Portfolio Manager",
    )

    assert "verified_market_fact_downgraded" in guarded.warnings
    assert "Market 工具验证" in guarded.text


def test_news_tool_fact_keeps_provenance_and_unsupported_claim_is_audited():
    tool = ToolMessage(
        content="FRED series observation dated 2026-08-28",
        name="get_macro_indicators",
        tool_call_id="news-1",
    )
    state = _jp_state(messages=[tool])
    captured = capture_agent_evidence(
        state, {"news_report": "FRED observation is dated 2026-08-28."}, "News Analyst"
    )
    combined = {**state, **captured}
    guarded = enforce_agent_output(
        combined, "FRED宏观数据没有独立验证。", "Research Manager"
    )
    unsupported = enforce_agent_output(combined, "目标价9999。", "Trader")

    assert "verified_news_fact_downgraded" in guarded.warnings
    assert "unsupported_precise_number" in unsupported.warnings


def test_prefetched_japan_news_is_canonical_over_a_tool_specific_empty_result():
    state = _jp_state(
        japan_data_bundle={
            "items": [
                {
                    "source": "Japan News",
                    "source_type": "japan_stock_news",
                    "status": "OK",
                    "timestamp": "2026-09-02T09:30:00+09:00",
                    "title": "半導体製造装置の新製品を発表",
                    "url": "https://example.test/news/6981",
                    "metadata": {
                        "published_at": "2026-09-02T09:30:00+09:00",
                        "freshness_status": "LATEST_AVAILABLE",
                    },
                }
            ],
            "provider_metadata": {},
        }
    )

    report = _apply_japan_news_authority(
        "本日の個別ニュースは0条で、会社関連ニュースはありません。\nMacro background.",
        state,
    )
    news_context = render_japan_audience_context(state, "NEWS")

    assert "半導体製造装置の新製品を発表" in report
    assert "ニュースは0条" not in report
    assert "半導体製造装置の新製品を発表" in news_context


def test_report_sentiment_uses_one_source_native_authority_without_unavailable_claim():
    item = _japan_sentiment_item(score=-0.1821, sample_count=11)
    item["metadata"].update(
        positive_count=0,
        neutral_count=9,
        negative_count=2,
        confidence=0.7,
    )

    report = render_japan_report_sections({"items": [item]})

    assert "本次没有可用" not in report
    assert "样本 11 条" in report
    assert "温和偏空" in report
    assert report.count("样本 11 条，置信度 高") == 1


def test_evidence_audit_is_nonempty_from_run_initialization():
    registry, audit = initialize_evidence_registry(
        market_context={"market": "JP", "symbol": "5801.T"},
        japan_data_bundle={
            "items": [
                {
                    "source": "JSF",
                    "source_type": "securities_finance_balance",
                    "timestamp": "2026-08-31",
                    "title": "JSF balance",
                    "verified": True,
                    "status": "OK",
                    "metadata": {"freshness_status": "LATEST_AVAILABLE", "unit": "株"},
                }
            ]
        },
        verified_market_snapshot="",
        analysis_as_of="2026-08-31",
    )

    assert registry
    assert audit[0]["category"] == "SUPPORTED_FACT"
    assert audit[0]["supported_count"] == 1


def test_run_manifest_discloses_worktree_state_without_secrets():
    manifest = build_run_manifest(
        analysis_as_of="2026-08-31",
        market_context={"market": "JP", "symbol": "5801.T"},
        config={
            "max_debate_rounds": 1,
            "llm_provider": "minimax",
            "deep_think_llm": "MiniMax-M3",
            "quick_think_llm": "MiniMax-M2.7-highspeed",
        },
    )

    assert manifest["git_head"]
    assert manifest["git_dirty"] in {True, False}
    assert manifest["provider"] == "minimax"
    assert manifest["deep_model"] == "MiniMax-M3"
    assert manifest["quick_model"] == "MiniMax-M2.7-highspeed"
    assert "api_key" not in str(manifest).lower()


def test_stale_jsf_is_historical_only_not_current_directional_evidence():
    state = _jp_state(
        evidence_registry=[
            {
                "domain": "MARKET",
                "value": "JSF finance balance 1234 on 2026-08-17",
                "semantic_basis": "OBSERVABLE_LENDING_BALANCE",
                "allowed_for_current_decision": False,
            }
        ]
    )
    current = enforce_agent_output(
        state, "当前JSF融资余额1234说明空头已经出清。", "Risk Analyst"
    )

    assert current.warnings
    assert "1234" not in current.text
    context = render_downstream_evidence_context(state)
    assert "current_eligible=False" not in context  # raw provider rows are not duplicated downstream


def test_5801_forecast_only_h1_does_not_hide_q1_actual_or_fy_guidance():
    q1 = _actual_record(record_id="q1")
    h1_forecast = _actual_record(
        record_id="h1-guidance",
        quarter=2,
        revenue=None,
        operating_income=None,
        ordinary_income=None,
        net_income=None,
        eps=None,
        forecast_revenue=720_000,
        forecast_operating_income=49_500,
        forecast_ordinary_income=60_500,
        forecast_net_income=42_500,
        forecast_eps=60.41,
    )
    actual = select_latest_earnings_record(
        [h1_forecast, q1], issuer_id="E01332", analysis_as_of=date(2026, 8, 31)
    )
    guidance = select_latest_guidance_record(
        [h1_forecast, q1], issuer_id="E01332", analysis_as_of=date(2026, 8, 31)
    )

    assert actual.status == "OK"
    assert actual.period_type == "Q1"
    assert actual.record["revenue"] == 365_221
    assert guidance.status == "OK"
    assert guidance.record["forecast_revenue"] == 1_530_000
    assert guidance.period_type == "FY"


def test_critical_gate_failure_is_visible_and_blocks_current_quarter_conviction():
    assessment = {
        "actual": {
            "status": "INSUFFICIENT_DATA",
            "freshness": "FRESHNESS_UNVERIFIED",
            "critical_gate": {"status": "INSUFFICIENT_DATA", "available_count": 1},
            "detail": "fewer than two critical metrics available",
        },
        "guidance": {"status": "NOT_PROVIDED", "freshness": "CURRENT_OFFICIAL"},
    }
    state = _jp_state(
        japan_data_bundle={
            "items": [],
            "provider_metadata": {"Japan Financial Authority": assessment},
        }
    )
    context = render_japan_financial_context(state)
    guarded = enforce_agent_output(
        state, "最新季度已经全面明确确认强劲盈利爆发。", "Fundamentals Analyst"
    )

    assert "Critical field completeness: INSUFFICIENT_DATA" in context
    assert "Current-use eligibility: INSUFFICIENT_DATA" in context
    assert "critical_gate_bypassed" in guarded.warnings


def test_guidance_consensus_vendor_forward_are_semantically_distinct():
    state = _jp_state(
        evidence_registry=[
            {
                "value": 150.58,
                "semantic_basis": "ANALYST_CONSENSUS",
                "allowed_for_current_decision": True,
            },
            {
                "value": 296.56,
                "semantic_basis": "VENDOR_FORWARD_ESTIMATE",
                "allowed_for_current_decision": True,
            },
            {
                "value": 149.25,
                "semantic_basis": "COMPANY_GUIDANCE",
                "allowed_for_current_decision": True,
            },
        ]
    )
    mixed = enforce_agent_output(
        state, "公司Guidance采用vendor forward EPS 296.56。", "Risk Analyst"
    )

    assert "guidance_vendor_forward_mixed" in mixed.warnings
    assert "不同语义" in mixed.text


def test_financial_authority_cannot_be_collapsed_into_generic_tool_provenance():
    result = enforce_agent_output(
        _jp_state(),
        "以下数据均来自 VERIFIED_TOOL_OUTPUT，可直接作为决策依据。",
        "Portfolio Manager",
    )

    assert "collapsed_provenance_types" in result.warnings
    assert "VERIFIED_FINANCIAL_AUTHORITY" in result.text


def test_million_yen_unit_conversion_is_deterministic():
    assert million_jpy_to_oku(52_126) == Decimal("521.26")
    assert million_jpy_to_oku(31_066) == Decimal("310.66")


def test_wrong_capex_oku_conversion_is_removed():
    state = _jp_state(
        evidence_registry=[
            {
                "value": "Capital Expenditure 52,126 百万円",
                "allowed_for_current_decision": True,
            }
        ]
    )
    result = enforce_agent_output(state, "Capex约52亿。", "Risk Analyst")
    assert "unit_mismatch" in result.warnings


def test_execution_math_and_portfolio_risk_are_authoritative():
    plan = (
        "**Entry Price**: 4120\n\n**Stop Loss**: 3585\n\n"
        "**Position Sizing**: 5% of portfolio"
    )
    validation = validate_execution_plan(plan)
    assert validation["distance"] == 535
    assert validation["risk_pct"] == 12.99
    assert validation["portfolio_stop_risk_pct"] == 0.65

    wrong = "止损距离风险约8.8%；组合止损损失约0.44%。"
    corrected, warnings = enforce_execution_math(wrong, validation)
    assert "8.8%" not in corrected
    assert "0.44%" not in corrected
    assert "12.99%" in corrected
    assert "0.65%" in corrected
    assert warnings == ("ARITHMETIC_MISMATCH",)


def test_no_reportable_short_row_does_not_mean_no_shorts():
    result = enforce_agent_output(
        _jp_state(), "JPX没有可申报空头，因此没有机构做空。", "Risk Analyst"
    )
    assert "short_absence_overclaim" in result.warnings
    assert "无申报记录不代表不存在空头" in result.text


def test_low_lending_balance_cannot_become_no_short_pressure_claim():
    result = enforce_agent_output(
        _jp_state(), "贷株余额很低，因此未见融券/做空压力累积。", "Market Analyst"
    )

    assert "short_absence_overclaim" in result.warnings
    assert "无申报记录不代表不存在空头" in result.text


def test_unsupported_comparison_removes_the_compound_table_claim():
    state = _jp_state(
        evidence_registry=[
            {
                "claim_type": "FACT",
                "value": 365221,
                "allowed_for_current_decision": True,
            }
        ]
    )

    result = enforce_agent_output(
        state,
        "| Revenue | 365,221 | 293,700 | +24% |",
        "Fundamentals Analyst",
    )

    assert result.text == ""
    assert "365,221" not in result.text
    assert "293,700" not in result.text
    assert "+24%" not in result.text
    assert "数据不足" not in result.text


def _financial_evidence(
    metric,
    value,
    *,
    semantic_basis="REVENUE",
    period="Q1",
    target_period="2027-03-31",
    unit="百万円",
):
    return {
        "domain": "FUNDAMENTALS",
        "claim_type": "FACT",
        "metric": metric,
        "value": value,
        "unit": unit,
        "period": period,
        "target_period": target_period,
        "semantic_basis": semantic_basis,
        "verification_status": "VERIFIED_FINANCIAL_AUTHORITY",
        "allowed_for_current_decision": True,
    }


def test_verified_financial_authority_value_is_preserved_and_wrong_value_replaced():
    state = _jp_state(
        evidence_registry=[_financial_evidence("revenue", 365_221)]
    )

    exact = enforce_agent_output(
        state,
        "Latest Actual Q1 revenue: 365,221 百万円，target 2027-03-31。",
        "Fundamentals Analyst",
    )
    wrong = enforce_agent_output(
        state,
        "Latest Actual Q1 revenue: 365,220 百万円，target 2027-03-31。",
        "Fundamentals Analyst",
    )

    assert exact.text.startswith("Latest Actual Q1 revenue: 365,221 百万円")
    assert exact.warnings == ()
    assert wrong.text == "Latest Actual revenue: 365,221 百万円。"
    assert wrong.warnings == ("financial_authority_replaced",)


def test_financial_authority_replacement_requires_semantic_period_and_target_match():
    state = _jp_state(
        evidence_registry=[
            _financial_evidence(
                "revenue",
                1_530_000,
                semantic_basis="COMPANY_GUIDANCE",
                period="FY",
            )
        ]
    )

    wrong_period = enforce_agent_output(
        state,
        "Current Company Guidance Q1 revenue: 1,500,000 百万円。",
        "Fundamentals Analyst",
    )

    assert "1,530,000" not in wrong_period.text
    assert "1,500,000" not in wrong_period.text

    table_row = enforce_agent_output(
        state,
        "| Current Company Guidance net income | 1,500,000 百万円 | FY |",
        "Fundamentals Analyst",
    )
    assert table_row.text == ""
    assert "Current Company Guidance net_income:" not in table_row.text


def test_table_redaction_removes_complete_claim_without_token_corruption():
    result = enforce_agent_output(
        _jp_state(),
        "| Metric | P/E 27x | move (+2.3pt) | FY2027 | 52億円 |",
        "Fundamentals Analyst",
    )

    assert result.text == ""
    assert "FY2027" not in result.text
    for corrupt in (
        "DATA_UNAVAILABLEx",
        "DATA_UNAVAILABLEpt",
        "FY2DATA_UNAVAILABLE",
        "DATA_UNAVAILABLE億",
    ):
        assert corrupt not in result.text


def test_fully_supported_table_row_remains_intact():
    row = "| Revenue | 365,221 |"
    result = enforce_agent_output(
        _jp_state(
            evidence_registry=[
                {
                    "claim_type": "FACT",
                    "value": 365_221,
                    "allowed_for_current_decision": True,
                }
            ]
        ),
        row,
        "Fundamentals Analyst",
    )

    assert result.text == row
    assert not result.warnings


def test_copied_legacy_enforcement_corruption_is_sanitized():
    result = enforce_agent_output(
        _jp_state(),
        "P/E DATA_UNAVAILABLEx；spread DATA_UNAVAILABLEpt；"
        "FY2DATA_UNAVAILABLE增长预期。"
        "该精确数值缺少上游证据支持，已不纳入本项判断。",
        "Research Manager",
    )

    assert "DATA_UNAVAILABLEx" not in result.text
    assert "DATA_UNAVAILABLEpt" not in result.text
    assert "FY2DATA_UNAVAILABLE" not in result.text
    assert "缺少上游证据支持" not in result.text

    legacy_table = enforce_agent_output(
        _jp_state(),
        "| P/E | 数据不足 | 证据不足，暂不判断 |\n"
        "JSF 可观察贷株余额较低。| Conclusion | HOLD |",
        "Research Manager",
    )
    assert "数据不足" not in legacy_table.text
    assert "证据不足，暂不判断" not in legacy_table.text
    assert "可观察贷株余额较低。\n\n| Conclusion" in legacy_table.text


def test_unsupported_parenthesized_percent_clause_is_removed_cleanly():
    result = enforce_agent_output(
        _jp_state(),
        "增长非常强（+27.5%）。后续只保留定性观察。",
        "Research Manager",
    )

    assert "+27.5%" not in result.text
    assert "DATA_UNAVAILABLE" not in result.text
    assert "后续只保留定性观察" in result.text


def _japan_sentiment_item(*, score=0.505, sample_count=4):
    return {
        "source": "Japan Investor Sentiment",
        "source_type": "japan_investor_sentiment_aggregate",
        "layer": "MARKET_SENTIMENT",
        "status": "OK",
        "metadata": {
            "sample_count": sample_count,
            "positive_count": 3,
            "neutral_count": 0,
            "negative_count": 1,
            "sentiment_score": score,
            "confidence": 0.4,
        },
    }


def test_japan_sentiment_score_and_band_ignore_news_macro_direction(monkeypatch):
    monkeypatch.setattr(
        sentiment_module,
        "fetch_stocktwits_messages",
        lambda *a, **k: "<stocktwits unavailable>",
    )
    monkeypatch.setattr(
        sentiment_module,
        "fetch_reddit_posts",
        lambda *a, **k: "<no Reddit posts>",
    )
    monkeypatch.setattr(
        sentiment_module.get_news,
        "func",
        lambda *a, **k: "Very bullish macro news",
    )
    structured = MagicMock()
    structured.invoke.return_value = SentimentReport(
        overall_band=SentimentBand.BULLISH,
        overall_score=9,
        confidence="high",
        narrative=(
            "宏观新闻印证并强化看多情绪。Yahoo Board样本略偏正面。\n"
            "整体置信度为 Low。"
        ),
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    state = _jp_state(
        japan_data_bundle={
            "items": [_japan_sentiment_item()],
            "provider_metadata": {},
        }
    )

    report = sentiment_module.create_sentiment_analyst(llm)(state)["sentiment_report"]

    assert "Weak Positive Observation (LOW_SAMPLE)" in report
    assert "**Score:** 0.505 (source-native signed scale)" in report
    assert "**Confidence:** Low" in report
    assert "News and macro observations are background only" in report
    assert "印证并强化看多情绪" not in report
    assert "整体置信度为 Low" not in report
    assert report.count("**Confidence:**") == 1
    assert "**Score:** 9" not in report


def test_japan_sentiment_removes_alternate_llm_band_and_score_authority(monkeypatch):
    monkeypatch.setattr(
        sentiment_module,
        "fetch_stocktwits_messages",
        lambda *a, **k: "<stocktwits unavailable>",
    )
    monkeypatch.setattr(
        sentiment_module,
        "fetch_reddit_posts",
        lambda *a, **k: "<no Reddit posts>",
    )
    monkeypatch.setattr(sentiment_module.get_news, "func", lambda *a, **k: "Macro")
    llm = MagicMock()
    llm.with_structured_output.return_value.invoke.return_value = SentimentReport(
        overall_band=SentimentBand.NEUTRAL,
        overall_score=5,
        confidence="high",
        narrative=(
            "## overall_band：Neutral\n## overall_score：5.0\n"
            "情绪读数明确为中性。社交样本方向接近中性。"
        ),
    )
    state = _jp_state(
        japan_data_bundle={
            "items": [_japan_sentiment_item(score=-0.0009, sample_count=9)],
            "provider_metadata": {},
        }
    )

    report = sentiment_module.create_sentiment_analyst(llm)(state)["sentiment_report"]

    assert "**Overall Sentiment:** **Mixed**" in report
    assert "**Score:** -0.0009" in report
    assert "overall_band" not in report
    assert "overall_score" not in report
    assert "5.0" not in report
    assert "情绪读数明确为中性" not in report

    reapplied = sentiment_module._apply_japan_sentiment_domain_integrity(
        report, sentiment_module._japan_social_authority(state)
    )
    assert reapplied.count("## Investor/social sentiment authority") == 1
    assert reapplied.count("**Sample:**") == 1
    assert reapplied.count("**Domain boundary:**") == 1


def test_missing_japan_social_sample_cannot_become_neutral_from_news(monkeypatch):
    monkeypatch.setattr(
        sentiment_module,
        "fetch_stocktwits_messages",
        lambda *a, **k: "<stocktwits unavailable>",
    )
    monkeypatch.setattr(
        sentiment_module,
        "fetch_reddit_posts",
        lambda *a, **k: "<reddit status=RATE_LIMITED; sample_count=UNKNOWN>",
    )
    monkeypatch.setattr(
        sentiment_module.get_news,
        "func",
        lambda *a, **k: "Bullish macro background",
    )
    llm = MagicMock()
    state = _jp_state(
        japan_data_bundle={
            "items": [
                {
                    **_japan_sentiment_item(score=None, sample_count=0),
                    "status": "DATA_UNAVAILABLE",
                }
            ],
            "provider_metadata": {},
        }
    )

    report = sentiment_module.create_sentiment_analyst(llm)(state)["sentiment_report"]

    assert "**Overall Sentiment:** **DATA_UNAVAILABLE**" in report
    assert "**Score:** DATA_UNAVAILABLE" in report
    assert "**Overall Sentiment:** **Neutral**" not in report
    assert "**Score:** 5.0" not in report
    llm.invoke.assert_not_called()

    replayed = enforce_agent_result(
        state,
        {
            "sentiment_report": (
                "**Overall Sentiment:** **Neutral**\n**Score:** 5.0\n"
                "宏观新闻支持中性情绪。"
            )
        },
        "Sentiment Analyst",
    )
    assert "DATA_UNAVAILABLE" in replayed["sentiment_report"]
    assert "Neutral" not in replayed["sentiment_report"]


def test_jsf_low_lending_is_not_total_short_pressure():
    result = enforce_agent_output(
        _jp_state(),
        "JSF贷株余额为3000股，因此做空压力极低。",
        "Market Analyst",
    )

    assert result.warnings == ("short_pressure_overclaim",)
    assert "可观察贷株余额较低" in result.text
    assert "不代表全市场空头总量" in result.text
    assert "做空压力极低" not in result.text

    separated = enforce_agent_output(
        _jp_state(),
        "JSF贷株余额为3000股。做空压力极低。",
        "Market Analyst",
    )
    assert "做空压力极低" not in separated.text
    assert "不代表全市场空头总量" in separated.text

    traditional = enforce_agent_output(
        _jp_state(),
        "借券餘額20日下降98.14%，空頭幾乎全數回補，賣壓大幅減輕。",
        "Portfolio Manager",
    )
    assert "空頭幾乎全數回補" not in traditional.text
    assert "賣壓大幅減輕" not in traditional.text

    live_wording = enforce_agent_output(
        _jp_state(),
        "贷株余额20日下降52%，空头已大规模回补。",
        "Market Analyst",
    )
    assert "空头已大规模回补" not in live_wording.text
    assert "不代表全市场空头总量" in live_wording.text


def test_financial_fact_cannot_be_relabelled_generic_tool_output():
    result = enforce_agent_output(
        _jp_state(),
        "Q1业绩和全年指引均为已验证事实（VERIFIED_TOOL_OUTPUT）。",
        "Research Manager",
    )

    assert "VERIFIED_TOOL_OUTPUT" not in result.text
    assert "VERIFIED_FINANCIAL_AUTHORITY" in result.text
    assert result.warnings == ("financial_provenance_collapsed",)


def test_sentiment_unavailable_does_not_become_neutral_or_score(monkeypatch):
    monkeypatch.setattr(
        sentiment_module, "fetch_stocktwits_messages", lambda *a, **k: "<stocktwits unavailable>"
    )
    monkeypatch.setattr(
        sentiment_module, "fetch_reddit_posts", lambda *a, **k: "<reddit status=TIMEOUT; sample_count=UNKNOWN>"
    )
    monkeypatch.setattr(sentiment_module.get_news, "func", lambda *a, **k: "Yahoo news")
    llm = MagicMock()
    state = _jp_state(messages=[])

    result = sentiment_module.create_sentiment_analyst(llm)(state)

    assert "DATA_UNAVAILABLE" in result["sentiment_report"]
    assert "Score:** DATA_UNAVAILABLE" in result["sentiment_report"]
    assert "**Overall Sentiment:** **Neutral**" not in result["sentiment_report"]
    llm.invoke.assert_not_called()


def test_low_sample_caps_sentiment_confidence(monkeypatch):
    monkeypatch.setattr(
        sentiment_module,
        "fetch_stocktwits_messages",
        lambda *a, **k: "Bullish: 2 (100%) · Total: 2 most-recent messages",
    )
    monkeypatch.setattr(sentiment_module, "fetch_reddit_posts", lambda *a, **k: "<no Reddit posts>")
    monkeypatch.setattr(sentiment_module.get_news, "func", lambda *a, **k: "Yahoo news")
    structured = MagicMock()
    structured.invoke.return_value = SentimentReport(
        overall_band=SentimentBand.BULLISH,
        overall_score=8,
        confidence="high",
        narrative="two bullish posts",
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured

    report = sentiment_module.create_sentiment_analyst(llm)(_jp_state())["sentiment_report"]

    assert "LOW_SAMPLE" in report
    assert "**Confidence:** Low" in report


def test_intraday_jp_daily_bar_uses_previous_completed_session():
    jst = ZoneInfo("Asia/Tokyo")
    assert latest_completed_japan_session(
        date(2026, 8, 31), now=datetime(2026, 8, 31, 14, 12, tzinfo=jst)
    ) == date(2026, 8, 28)
    frame = pd.DataFrame(
        {"Date": ["2026-08-28", "2026-08-31"], "Close": [3900, 3926]}
    )
    filtered = _filter_japan_completed_sessions(
        frame,
        "2026-08-31",
        "5801.T",
        now=datetime(2026, 8, 31, 14, 12, tzinfo=jst),
    )
    assert filtered["Date"].tolist() == ["2026-08-28"]


def test_nightly_jp_daily_bar_uses_same_day_completed_session():
    jst = ZoneInfo("Asia/Tokyo")
    assert latest_completed_japan_session(
        date(2026, 8, 31), now=datetime(2026, 8, 31, 20, 0, tzinfo=jst)
    ) == date(2026, 8, 31)


def test_historical_outcome_cannot_be_current_directional_evidence():
    result = enforce_agent_output(
        _jp_state(), "上次交易5801亏损，所以本轮应当HOLD观望。", "Portfolio Manager"
    )
    assert "historical_outcome_as_current_evidence" in result.warnings


def test_financial_document_is_only_in_fundamentals_audience():
    state = _jp_state(
        japan_data_bundle={
            "items": [
                {
                    "source": "TDnet",
                    "source_type": "earnings_or_quarterly",
                    "timestamp": "2026-08-06",
                    "title": "Q1 results",
                    "metadata": {},
                }
            ],
            "provider_metadata": {
                "Japan Financial Authority": {
                    "actual": {"status": "INSUFFICIENT_DATA", "critical_gate": {}},
                    "guidance": {"status": "NOT_PROVIDED"},
                }
            },
        }
    )
    fundamentals = render_japan_financial_context(state)
    sentiment = render_japan_audience_context(state, "SENTIMENT")
    downstream = render_downstream_evidence_context(state)

    assert "Latest Actual" in fundamentals
    assert "Latest Actual" not in sentiment
    assert "Latest Actual" not in downstream
    assert "Q1 results" not in sentiment


def test_fundamentals_handoff_appends_exact_authority_values_after_llm_text():
    state = _jp_state(
        japan_data_bundle={
            "items": [],
            "provider_metadata": {
                "Japan Financial Authority": {
                    "analysis_as_of": "2026-08-31",
                    "actual": {
                        "status": "OK",
                        "freshness": "CURRENT_OFFICIAL",
                        "selected_disclosure_date": "2026-08-06",
                        "critical_gate": {"status": "OK"},
                        "document": {
                            "source": "TDnet",
                            "source_type": "OFFICIAL",
                            "records": [
                                {
                                    "record_type": "ACTUAL",
                                    "period_type": "Q1",
                                    "target_period_end": "2027-03-31",
                                    "accounting_standard": "J_GAAP",
                                    "scope": "CONSOLIDATED",
                                    "metrics": {
                                        "revenue": {
                                            "status": "OK",
                                            "value": 365221,
                                            "unit": "百万円",
                                        }
                                    },
                                }
                            ],
                        },
                    },
                    "guidance": {"status": "NOT_PROVIDED"},
                }
            },
        }
    )
    observed = _observe_agent_node(
        "Fundamentals Analyst",
        lambda _state: {"fundamentals_report": "LLM summary rounded revenue."},
        provider="test",
        timeout=1,
        retries=0,
    )

    report = observed(state)["fundamentals_report"]
    assert "LLM summary rounded revenue." in report
    assert "Japan financial authority assessment" in report
    assert "Revenue: 365221 百万円" in report


def test_non_fundamentals_handoff_never_appends_financial_authority_section():
    state = _jp_state(
        japan_data_bundle={
            "items": [],
            "provider_metadata": {
                "Japan Financial Authority": {
                    "actual": {"status": "INSUFFICIENT_DATA"},
                    "guidance": {"status": "NOT_PROVIDED"},
                }
            },
        }
    )
    observed = _observe_agent_node(
        "News Analyst",
        lambda _state: {"news_report": "News report."},
        provider="test",
        timeout=1,
        retries=0,
    )

    assert "Japan financial authority assessment" not in observed(state)["news_report"]


def test_us_observer_behavior_remains_byte_for_byte():
    state = {"market_context": {"market": "US"}, "company_of_interest": "NVDA"}
    expected = {"market_report": "US report 1234"}
    observed = _observe_agent_node(
        "Market Analyst", lambda _state: expected, provider="test", timeout=1, retries=0
    )
    assert observed(state) == expected


def test_long_tool_output_keeps_complete_numeric_index_without_dumping_full_value():
    content = "A" * 12_050 + "\nHistorical Metric: 987654321"
    state = _jp_state(
        messages=[
            ToolMessage(
                content=content,
                name="get_balance_sheet",
                tool_call_id="historical-long",
            )
        ]
    )

    captured = capture_agent_evidence(
        state,
        {},
        "Fundamentals Analyst",
        capture_tools=True,
        capture_reports=False,
    )
    tool_entry = next(
        item
        for item in captured["evidence_registry"]
        if item.get("source_record_id") == "historical-long"
    )
    guarded = enforce_agent_output(
        {**state, **captured},
        "Historical Metric: 987654321。",
        "Fundamentals Analyst",
    )

    assert len(tool_entry["value"]) == 12_000
    assert "987654321" in tool_entry["derivation"]["numeric_tokens"]
    assert guarded.text == "Historical Metric: 987654321。"
    assert not guarded.warnings


def test_bundle_metadata_numbers_are_indexed_for_evidence_enforcement():
    state = _jp_state(
        japan_data_bundle={
            "items": [_japan_sentiment_item(score=-0.0009, sample_count=9)],
            "provider_metadata": {},
        }
    )
    registry, _ = initialize_evidence_registry(
        market_context=state["market_context"],
        japan_data_bundle=state["japan_data_bundle"],
        verified_market_snapshot="",
        analysis_as_of="2026-09-02",
    )
    entry = next(
        item
        for item in registry
        if item.get("metric") == "japan_investor_sentiment_aggregate"
    )
    guarded = enforce_agent_output(
        {**state, "evidence_registry": registry},
        "**Score:** -0.0009 (source-native signed scale)。",
        "Sentiment Analyst",
    )

    assert "-0.0009" in entry["derivation"]["numeric_tokens"]
    assert guarded.text.startswith("**Score:** -0.0009")
    assert not guarded.warnings


def test_vendor_forward_semantic_applies_only_to_explicit_forward_fields():
    tool = ToolMessage(
        content="Revenue (TTM): 1916966010880\nForward EPS: 237.27\nNet Income: 265492004864",
        name="get_fundamentals",
        tool_call_id="fundamentals-semantic",
    )
    captured = capture_agent_evidence(
        _jp_state(messages=[tool]),
        {},
        "Fundamentals Analyst",
        capture_tools=True,
        capture_reports=False,
    )
    entries = [
        item
        for item in captured["evidence_registry"]
        if item.get("source_record_id") == "fundamentals-semantic"
    ]

    assert any(item.get("semantic_basis") == "TOOL_FACT" for item in entries)
    forward = [
        item
        for item in entries
        if item.get("semantic_basis") == "VENDOR_FORWARD_ESTIMATE"
    ]
    assert [(item["metric"], item["value"]) for item in forward] == [
        ("eps", "237.27")
    ]


def test_observer_audits_without_mutating_agent_reasoning():
    observed = _observe_agent_node(
        "Fundamentals Analyst",
        lambda _state: {"fundamentals_report": "Unsupported target 999999。"},
        provider="test",
        timeout=1,
        retries=0,
    )
    result = observed(_jp_state())

    assert result["fundamentals_report"] == "Unsupported target 999999。"
    inference = next(
        item
        for item in result.get("evidence_registry") or []
        if item.get("metric") == "fundamentals_report"
    )
    assert inference["claim_type"] == "INFERENCE"
    assert inference["verification_status"] == "ANALYST_INFERENCE"
    assert inference["allowed_for_current_decision"] is False
    warning = next(
        item
        for item in result["evidence_audit"]
        if item.get("warning") == "unsupported_precise_number"
    )
    assert warning["resolution"] == "PENDING_FINAL_ARTIFACT_VALIDATION"
    assert warning["execution_blocking"] is True
