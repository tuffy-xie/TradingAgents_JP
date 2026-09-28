"""JP-only regressions for the lightweight Agent output evidence guard."""

from __future__ import annotations

from tradingagents.agents.utils.evidence_enforcement import (
    enforce_agent_output,
    enforce_agent_result,
)
from tradingagents.graph.setup import _observe_agent_node


def _state(*, market="JP"):
    governance = {
        "source": "Japan Evidence Governance",
        "source_type": "source_of_truth_assessment",
        "timestamp": "2026-08-14T00:00:00+00:00",
        "metadata": {
            "freshness": [
                {
                    "source": "J-Quants",
                    "source_type": "official_financial_summary",
                    "timestamp": "2024-08-01T00:00:00+00:00",
                    "status": "STALE",
                }
            ]
        },
    }
    return {
        "market_context": {"market": market},
        "verified_market_snapshot": "| Close | 5000 |\n| RSI | 55.2 |",
        "trade_constraints": {"max_position_pct": 10},
        "japan_data_bundle": {
            "items": [
                {
                    "source": "TDnet",
                    "source_type": "guidance_revision",
                    "timestamp": "2026-08-01T00:00:00+00:00",
                    "metadata": {"key_numbers": {"売上高": "1000"}},
                },
                {
                    "source": "Minkabu Analyst Consensus",
                    "source_type": "japan_analyst_expectations",
                    "timestamp": "2026-08-12T00:00:00+00:00",
                    "metadata": {
                        "analyst_expectations": {
                            "earnings_expectations": {
                                "fiscal_year": 2027,
                                "period_type": "FY",
                                "revenue_estimate": 1200,
                                "eps_estimate": 12.5,
                            }
                        }
                    },
                },
                {
                    "source": "J-Quants",
                    "source_type": "official_financial_summary",
                    "timestamp": "2024-08-01T00:00:00+00:00",
                    "metadata": {"revenue": 888},
                },
                governance,
            ]
        },
    }


def test_unsupported_target_and_non_snapshot_current_price_are_downgraded():
    result = enforce_agent_output(
        _state(), "当前股价为1200，目标价为9999。", "Trader"
    )
    assert "1200" not in result.text
    assert "9999" not in result.text
    assert "non_snapshot_current_price" in result.warnings


def test_analyst_estimate_cannot_be_presented_as_company_guidance():
    result = enforce_agent_output(_state(), "公司指引营收为1200。", "Fundamentals Analyst")
    assert "1200" not in result.text
    assert "analyst_estimate_as_guidance" in result.warnings


def test_company_guidance_and_analyst_consensus_cannot_be_combined_even_when_equal():
    result = enforce_agent_output(_state(), "公司指引等同于分析师一致预期1200。", "Trader")
    assert "不同口径" in result.text
    assert "guidance_consensus_mixed" in result.warnings


def test_community_or_consensus_cannot_be_relabelled_as_verified_fact():
    result = enforce_agent_output(
        _state(), "VERIFIED FACT：Yahoo 掲示板情绪确认当前股价为5000。", "Sentiment Analyst"
    )
    assert "不可表述为已验证官方事实" in result.text
    assert "source_type_confusion" in result.warnings


def test_stale_data_and_cross_period_financial_math_are_downgraded():
    stale = enforce_agent_output(_state(), "最新营收为888。", "News Analyst")
    assert "888" not in stale.text
    assert "stale_data_as_current" in stale.warnings

    mixed = enforce_agent_output(_state(), "FY 2027 EPS 12.5 减去 Q1 EPS 3.0。", "Bear Researcher")
    assert "不可混算" in mixed.text
    assert "period_mismatch" in mixed.warnings


def test_common_graph_boundary_covers_reports_debates_and_structured_decisions():
    state = _state()
    result = enforce_agent_result(
        state,
        {
            "market_report": "目标价9999",
            "investment_plan": "当前股价1200",
            "trader_investment_plan": "公司指引1200",
            "final_trade_decision": "最新营收888",
            "investment_debate_state": {"current_response": "FY 2027 与 Q1 EPS 12.5"},
            "risk_debate_state": {"current_aggressive_response": "目标价9999"},
        },
        "Portfolio Manager",
    )
    assert "9999" not in result["market_report"]
    assert "1200" not in result["investment_plan"]
    assert "888" not in result["final_trade_decision"]
    assert "不可混算" in result["investment_debate_state"]["current_response"]
    assert "9999" not in result["risk_debate_state"]["current_aggressive_response"]


def test_us_output_is_unchanged():
    text = "Current price 9999 and target 12345."
    assert enforce_agent_output(_state(market="US"), text, "Trader").text == text


def test_unsupported_claims_are_rewritten_as_complete_sentences():
    result = enforce_agent_output(
        _state(),
        "目标价9999附近减仓；EPS 77 增长；当前股价为1200，止损位为1100。",
        "Trader",
    )
    assert "9999" not in result.text
    assert "77" not in result.text
    assert "1200" not in result.text
    assert "1100" not in result.text
    assert "DATA_UNAVAILABLE" not in result.text
    assert "缺少上游证据支持" not in result.text


def test_evidence_audit_is_internal_not_appended_to_agent_prose():
    result = enforce_agent_result(
        _state(), {"trader_investment_plan": "目标价9999。"}, "Trader"
    )
    assert "Evidence enforcement" not in result["trader_investment_plan"]
    [finding] = result["evidence_audit"]
    assert finding["category"] == "UNSUPPORTED_CLAIM"
    assert finding["agent"] == "Trader"
    assert finding["field"] == "trader_investment_plan"
    assert finding["warning"] == "unsupported_precise_number"
    assert finding["original_claim"] == "目标价9999。"
    assert finding["replacement_claim"] == ""
    assert finding["resolution"] == "PENDING_FINAL_ARTIFACT_VALIDATION"
    assert finding["execution_blocking"] is True


def test_graph_observer_preserves_prose_and_captures_evidence_metadata():
    expected = {"market_report": "50日均线为1234。"}
    observed = _observe_agent_node(
        "Market Analyst",
        lambda _state: expected,
        provider="test",
        timeout=1,
        retries=0,
    )
    result = observed(_state())
    assert result["market_report"] == expected["market_report"]
    assert result["evidence_registry"]
    assert result["evidence_audit"][0]["category"] == "UNSUPPORTED_CLAIM"
    assert (
        result["evidence_audit"][0]["resolution"]
        == "PENDING_FINAL_ARTIFACT_VALIDATION"
    )


def test_verified_market_tool_numbers_need_lossless_current_metadata():
    state = _state()
    state["verified_market_snapshot"] = ""
    state["market_report"] = "当前价3861，SMA50为3986.16，SMA200为3283.34，ATR为277.53，RSI为49.06。"
    state["evidence_registry"] = [
        {
            "domain": "MARKET",
            "claim_type": "FACT",
            "value": state["market_report"],
            "verification_status": "VERIFIED_TOOL_OUTPUT",
            "allowed_for_current_decision": True,
        }
    ]
    result = enforce_agent_output(
        state,
        "当前价3861，SMA50为3986.16，SMA200为3283.34，ATR为277.53，RSI为49.06。",
        "Trader",
    )
    assert "non_snapshot_current_price" in result.warnings
    assert "当前价3861" not in result.text


def test_market_agent_prose_is_not_its_own_numeric_authority():
    state = _state()
    state["verified_market_snapshot"] = ""
    state["market_report"] = "当前价9999。"

    result = enforce_agent_output(state, state["market_report"], "Market Analyst")

    assert "9999" not in result.text
    assert "non_snapshot_current_price" in result.warnings
