"""Portfolio owns instrument selection advice, even without a named rating."""
import hashlib
from unittest.mock import patch

import pytest

from tests.test_predicate_authority_closure import regime_state
from tests.test_rating_authority import state_with
from tradingagents import final_output as f
from tradingagents.agents.evidence_enforcement import (
    enforce_agent_output,
    probability_event_gate_violation,
)
from tradingagents.rating_authority import artifact_rating_violations, internal_rating_claims


@pytest.mark.parametrize("text", [
    "对于寻求行业配置的投资者而言，该公司是一只值得重点关注的核心标的。",
    "这是一只值得投资的股票。", "该股值得配置。", "建议投资者优先配置该公司。",
    "该股票适合投资。", "建议投资者关注该股。", "建议投资者关注这家公司。",
    "This stock is worth considering.", "Investors should invest in this company.",
    "This security is suitable for investment.", "この銘柄は注目すべき。",
    "| 标的 | 判断 |\n| --- | --- |\n| 公司甲 | 值得配置 |",
    "历史上该股票估值偏高，但现在值得配置。",
    "本分析师认为该股值得配置。", "我们认为该股是值得关注的标的。",
])
def test_selection_advice_without_rating_has_existing_recommendation_owner(text):
    claims = internal_rating_claims(text)
    assert len(claims) == 1
    assert claims[0].rating is None  # selection advice is not an invented Buy
    assert claims[0].semantic_type == "INVESTMENT_RECOMMENDATION"
    assert text[claims[0].start:claims[0].end] == claims[0].text
    state = f.build_canonical_final_state(state_with(text, "fundamentals_report"))
    contract = state["final_output_contract"]
    assert contract["status"] == "FINALIZED"
    assert not f._rating_artifact_claims(state, state["accepted_report_markdown"])
    findings = [x for x in state["evidence_audit"] if x.get("category") == "SECONDARY_INTERNAL_RATING"
                and x.get("field") == "fundamentals_report"]
    assert findings
    for finding in findings:
        assert finding["authority_owner"] == "Portfolio Manager"
        assert finding["claim_sha256"] == hashlib.sha256(finding["original_claim"].encode()).hexdigest()
        assert finding["recommendation_semantics"] == "INVESTMENT_RECOMMENDATION"
        assert finding["resolution"] == "CLAIM_REMOVED_OR_REPLACED"
        assert finding["accepted_artifact_sha256"] == contract["accepted_report_sha256"]


@pytest.mark.parametrize("text", [
    "公司具有良好的成长潜力。", "公司是行业龙头。", "估值偏高，盈利预期改善。",
    "AI 需求是正面因素，EPS 预期上调。", "建议关注订单公告。",
    "建议投资者关注该股票的风险。", "该股值得关注的风险是客户集中度。", "建议投资者关注订单公告。",
    "建议投资者关注未来业务变化。", "建议投资者关注公司的行业地位。",
    "Investors should consider the company's growth prospects.",
    "该股票的财务风险值得关注。", "该公司的行业地位值得重点关注。",
    "该公司应该投资研发。", "公司值得投资设备以改善产能。",
    "不建议投资者关注该股。", "该股不值得配置。", "该股票并非值得投资。",
    "This stock is not worth considering.", "Historically this stock was worth investing in.",
    "分析师认为该股值得配置。", "券商建议投资者关注该股票。",
    "Nomura recommends this stock as suitable for investment.",
    "| 外部来源 | 判断 |\n| --- | --- |\n| Nomura | 值得配置 |",
    "若订单落地，则重新评估。",
])
def test_analysis_monitoring_prohibition_and_external_advice_are_not_internal_selection(text):
    assert not internal_rating_claims(text)


def test_portfolio_can_own_selection_advice_without_fabricated_rating():
    assert not artifact_rating_violations("## IV. 投资组合经理结论\n\n该股票值得配置。", "Underweight")


def test_surviving_selection_after_format_transform_cannot_close_audit():
    original = "该股票是值得关注的核心标的。"
    state = f.build_canonical_final_state(state_with(original, "fundamentals_report"))
    findings = [x for x in state["evidence_audit"] if x.get("category") == "SECONDARY_INTERNAL_RATING"
                and x.get("field") == "fundamentals_report"]
    exact = state["accepted_report_markdown"] + "\n\n## 基本面附录\n\n**该股票**是值得关注的核心标的。"
    audit, issues = f._finalize_audit(findings, state, accepted_report=exact, execution_allowed=False)
    assert issues and all(x["resolution"] == "UNRESOLVED" for x in audit if x.get("field") == "fundamentals_report")


@pytest.mark.parametrize("text", [
    "该股票是值得重点关注的核心标的。", "Investors should invest in this stock.",
    "### 衰退概率——软着陆共识稳固", "美国软着陆共识已经确立。",
])
def test_exact_candidate_blocks_when_upstream_enforcement_misses(text):
    compose = f.compose_user_report_markdown
    with patch.object(f, "compose_user_report_markdown", side_effect=lambda state: compose(state) + "\n\n## 基本面附录\n\n" + text):
        state = f.build_canonical_final_state(regime_state())
    contract = state["final_output_contract"]
    assert contract["status"] == "BLOCKED"
    assert not contract["validation_dimensions"]["domain_authority_consistent"]


@pytest.mark.parametrize("text", [
    "衰退概率——软着陆共识稳固", "美国软着陆共识已经确立。",
    "US soft landing consensus is firm.", "Soft landing has been confirmed.",
])
def test_different_regime_confirmation_not_proven_by_low_recession_odds(text):
    assert probability_event_gate_violation(regime_state(), text)


@pytest.mark.parametrize("text", [
    "美国经济有望软着陆。", "软着陆共识尚未确立。", "若软着陆得到确认，则重新评估。",
    "Soft landing may be achieved.", "该协议可能成为未来增长催化剂。", "美国衰退概率为8%。",
])
def test_macro_possibilities_and_correct_source_event_preserved(text):
    assert not probability_event_gate_violation(regime_state(), text)


def test_attributed_regime_confirmation_requires_verified_assertion_not_question():
    state = regime_state()
    text = "报告指出，美国软着陆共识稳固。"
    assert probability_event_gate_violation(state, text)
    state["evidence_registry"].append({"source": "get_news", "verification_status": "VERIFIED_TOOL_OUTPUT",
                                       "domain": "NEWS", "value": text})
    assert not probability_event_gate_violation(state, text)


def test_unsupported_heading_qualifier_removed_and_replacement_preserves_line_boundary():
    text = "### 2.3 衰退概率——软着陆共识稳固\n\n- 美国软着陆概率高达92%，经济韧性超预期\n- 日本经济情况需要观察\n"
    state = regime_state()
    state["evidence_registry"][-1]["value"] += "\n- **Japan recession in 2026?** — Yes 4%"
    checked = enforce_agent_output(state, text, "News Analyst")
    assert "### 2.3 衰退概率\n" in checked.text
    assert "共识稳固" not in checked.text
    lines = checked.text.splitlines()
    index = lines.index("- 日本经济情况需要观察")
    assert lines[index - 1].endswith("。")
    assert "不影响其他独立绑定的来源概率" in lines[index - 1]
    assert enforce_agent_output(state, checked.text, "News Analyst").text == checked.text


def test_removed_table_row_does_not_terminate_legal_siblings():
    text = "| 事件 | 概率 |\n| --- | --- |\n| 美国软着陆概率 | 92% |\n| 监控事件 | 需要观察 |\n"
    checked = enforce_agent_output(regime_state(), text, "News Analyst")
    assert "92%" not in checked.text
    assert "| --- | --- |\n| 监控事件 | 需要观察 |\n" in checked.text


@pytest.mark.parametrize("text", ["公司具有良好的成长潜力。", "公司是行业龙头。", "估值偏高，盈利预期改善。"])
def test_normal_analysis_remains_in_exact_candidate(text):
    state = f.build_canonical_final_state(state_with(text, "fundamentals_report"))
    assert state["final_output_contract"]["status"] == "FINALIZED"
    assert text in state["accepted_report_markdown"]
