"""Fresh-artifact predicate assertions obey the existing publication owners."""
import hashlib
from unittest.mock import patch

import pytest

from tests.test_rating_authority import state_with
from tradingagents import final_output as f
from tradingagents.agents.evidence_enforcement import (
    current_financial_gate_violation,
    enforce_agent_output,
    probability_event_gate_violation,
)
from tradingagents.rating_authority import internal_rating_claims


def unavailable_actual(text="风险较高。", field="fundamentals_report"):
    state = state_with(text, field)
    actual = state["japan_data_bundle"]["provider_metadata"]["Japan Financial Authority"]["actual"]
    actual.update(status="INSUFFICIENT_DATA", freshness="FRESHNESS_UNVERIFIED")
    actual["critical_gate"] = {"status": "INSUFFICIENT_DATA"}
    return state


@pytest.mark.parametrize("text,rating", [
    ("在此之前，Hold 是唯一合理的風險調整後立場。", "Hold"),
    ("Buy is the only appropriate stance.", "Buy"),
    ("**Sell** is the optimal strategy.", "Sell"),
    ("增持是最优的研究立场。", "Overweight"),
    ("Hold が最適な投資判断。", "Hold"),
    ("若订单落地，Buy is the best choice.", "Buy"),
])
def test_named_rating_subject_and_evaluative_predicate(text, rating):
    claims = internal_rating_claims(text)
    assert len(claims) == 1 and claims[0].rating == rating
    assert text[claims[0].start:claims[0].end] == claims[0].text
    state = state_with(text, "investment_debate_state.judge_decision")
    accepted = f.build_canonical_final_state(state)
    assert not f._rating_artifact_claims(accepted, accepted["accepted_report_markdown"])
    findings = [x for x in accepted["evidence_audit"] if x.get("category") == "SECONDARY_INTERNAL_RATING"
                and x.get("agent") == "Research Manager"]
    assert findings
    for finding in findings:
        assert finding["claim_sha256"] == hashlib.sha256(finding["original_claim"].encode()).hexdigest()
        assert finding["resolution"] == "CLAIM_REMOVED_OR_REPLACED"
        assert finding["accepted_artifact_sha256"] == accepted["final_output_contract"]["accepted_report_sha256"]


@pytest.mark.parametrize("text", [
    "Nomura says Hold is the appropriate stance.",
    "券商认为 Hold 是合理立场。",
    "Hold is not an appropriate stance.",
    "Hold 不是最佳选择。",
    "Bullish is the appropriate technical stance.",
    "技术面偏多，盈利改善是看涨因素。",
    "若未来 MACD 跌破零轴，则重新评估。",
])
def test_attribution_negation_outlook_and_monitoring_preserved(text):
    assert not internal_rating_claims(text)


@pytest.mark.parametrize("text", [
    "公司最新季度盈利能力显著回升。",
    "当前季度营收增长。",
    "The latest quarter earnings improved.",
    "最新四半期の利益が増加。",
    "不能确认最新季度营收，但当前季度盈利已经改善。",
])
def test_current_qualitative_actuals_require_existing_gate(text):
    state = unavailable_actual(text)
    assert current_financial_gate_violation(state, text)
    checked = enforce_agent_output(state, text, "Fundamentals Analyst")
    assert "critical_gate_bypassed" in checked.warnings
    accepted = f.build_canonical_final_state(state)
    assert text not in accepted["accepted_report_markdown"]
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    findings = [x for x in accepted["evidence_audit"] if x.get("warning") == "critical_gate_bypassed"]
    assert findings and all(x["resolution"] == "CLAIM_REMOVED_OR_REPLACED" for x in findings)
    assert all(x["claim_sha256"] == hashlib.sha256(x["original_claim"].encode()).hexdigest() for x in findings)


@pytest.mark.parametrize("text", [
    "2025 年第三季度供应商数据表明盈利改善。",
    "分析师预计最新季度盈利可能改善。",
    "该协议可能成为未来业务增长催化剂。",
    "公司盈利能力具有结构性优势。",
    "If current quarter earnings improve, reassessment is needed.",
    "若最新季度营收增长，则需要重新评估。",
    "The latest quarter earnings are expected to improve.",
    "不能确认当前季度盈利已经改善。",
    "最新季度盈利是否改善仍待确认。",
    "Cannot confirm the latest quarter earnings improved.",
])
def test_dated_history_forecasts_and_conditions_are_not_current_actuals(text):
    assert not current_financial_gate_violation(unavailable_actual(), text)


def test_verified_current_actual_and_us_are_not_downgraded():
    text = "公司最新季度盈利能力显著回升。"
    state = state_with(text, "fundamentals_report")
    assert not current_financial_gate_violation(state, text)
    assert enforce_agent_output(state, text, "Fundamentals Analyst").text == text
    state = unavailable_actual(text)
    state["market_context"]["market"] = "US"
    assert enforce_agent_output(state, text, "Fundamentals Analyst").text == text


def test_authority_labels_and_single_word_status_are_localized():
    text = "技术面因 Market authority UNAVAILABLE 无法作为决策依据。MACD、RSI、ATR、EBITDA 保留。"
    accepted = f.build_canonical_final_state(state_with(text, "final_trade_decision"))
    assert "行情证据 不可用" in accepted["accepted_report_markdown"]
    assert "MACD、RSI、ATR、EBITDA" in accepted["accepted_report_markdown"]
    assert not f.validate_final_report_text(accepted["accepted_report_markdown"], execution_allowed=False)


@pytest.mark.parametrize("text,dimension", [
    ("Hold 是唯一合理的風險調整後立場。", "domain_authority_consistent"),
    ("公司最新季度盈利能力显著回升。", "domain_authority_consistent"),
    ("Market authority UNAVAILABLE", "presentation_valid"),
])
def test_exact_artifact_blocks_upstream_bypass(text, dimension):
    compose = f.compose_user_report_markdown
    with patch.object(f, "compose_user_report_markdown", side_effect=lambda state: compose(state) + "\n\n## 研究附录\n\n" + text):
        accepted = f.build_canonical_final_state(unavailable_actual())
    contract = accepted["final_output_contract"]
    assert contract["status"] == "BLOCKED"
    assert not contract["validation_dimensions"][dimension]
    if "最新季度" in text:
        assert not contract["validation_dimensions"]["evidence_closure"]
        assert contract["audit_closure_status"] == "UNRESOLVED"


def test_presentation_transform_cannot_close_surviving_rating_predicate():
    text = "Hold 是唯一合理的立場。"
    accepted = f.build_canonical_final_state(state_with(text, "investment_debate_state.judge_decision"))
    findings = [x for x in accepted["evidence_audit"] if x.get("category") == "SECONDARY_INTERNAL_RATING"]
    injected = accepted["accepted_report_markdown"] + "\n\n## 研究附录\n\n**Hold** 是唯一合理的立場。"
    audit, issues = f._finalize_audit(findings, accepted, accepted_report=injected, execution_allowed=False)
    assert issues and any(x["resolution"] == "UNRESOLVED" for x in audit)


def regime_state(text="风险较高。"):
    state = unavailable_actual(text, "news_report")
    state["evidence_registry"].append({
        "source": "get_prediction_markets", "verification_status": "VERIFIED_TOOL_OUTPUT",
        "domain": "NEWS", "source_type": "TOOL_OUTPUT",
        "value": "- **US recession by end of 2026?** — Yes 8%\n"
                 "- **UK Recession in 2026?** — Yes 10%\n"
                 "- **Will China GDP grow between 4% and 5%?** — Yes 92%",
    })
    return state


@pytest.mark.parametrize("text", [
    "美国软着陆概率高达92%，经济韧性超预期。",
    "US soft landing probability is 92%.",
    "米国ソフトランディング確率は92%。",
    "美国衰退概率为10%。",  # another country's value is not evidence
    "英国衰退概率为8%。",
    "2027 年美国衰退概率为8%。",  # the event window is not interchangeable
])
def test_regime_probability_is_bound_to_event_and_subject(text):
    state = regime_state(text)
    assert probability_event_gate_violation(state, text)
    checked = enforce_agent_output(state, text, "News Analyst")
    assert "probability_event_mismatch" in checked.warnings
    accepted = f.build_canonical_final_state(state)
    assert text not in accepted["accepted_report_markdown"]
    findings = [x for x in accepted["evidence_audit"] if x.get("warning") == "probability_event_mismatch"]
    assert findings and all(x["resolution"] == "CLAIM_REMOVED_OR_REPLACED" for x in findings)
    assert accepted["final_output_contract"]["status"] == "FINALIZED"


@pytest.mark.parametrize("text", [
    "美国衰退概率为8%。", "UK recession probability is 10%.",
    "美国不衰退概率为92%。",  # complement of the same event, not soft landing
    "中国 GDP 区间概率为92%。", "该协议可能成为未来增长催化剂。",
    "美国经济有望软着陆。",
])
def test_observable_probability_events_and_qualitative_research_allowed(text):
    assert not probability_event_gate_violation(regime_state(), text)


def test_native_soft_landing_event_can_support_its_own_probability():
    state = regime_state()
    state["evidence_registry"][-1]["value"] += "\n- **US soft landing in 2026?** — Yes 92%"
    assert not probability_event_gate_violation(state, "美国软着陆概率为92%。")


def test_unbound_probability_blocks_exact_artifact_after_upstream_bypass():
    compose = f.compose_user_report_markdown
    with patch.object(f, "compose_user_report_markdown", side_effect=lambda state: compose(state) + "\n\n## 研究附录\n\n美国软着陆概率高达92%。"):
        accepted = f.build_canonical_final_state(regime_state())
    contract = accepted["final_output_contract"]
    assert contract["status"] == "BLOCKED"
    assert not contract["validation_dimensions"]["domain_authority_consistent"]
    assert not contract["validation_dimensions"]["evidence_closure"]
