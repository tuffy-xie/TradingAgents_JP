"""Approved execution never grants other Agents a parallel publication right."""
import copy
import hashlib
from unittest.mock import patch

import pytest

from tests.test_rating_authority import state_with
from tradingagents import final_output as f
from tradingagents.rating_authority import internal_rating_claims


def sell_state(text="风险较高。", field="news_report"):
    state = state_with(text, field)
    state["trader_investment_plan"] = "Action: Sell\nEntry: 8513\nStop: 9500\nPosition: 3%"
    state["validated_execution"] = {
        "status": "OK", "action": "Sell", "entry": 8513.0, "stop": 9500.0,
        "position_pct": 3.0,
    }
    return state


@pytest.mark.parametrize("text", [
    "最终研究结论: BUY", "**最终投资结论：Strong Buy**",
    "明确给出Underweight评级，建议现有持仓者减持。",
    "我们的建议是Sell。", "建议投资者减持。", "We recommend Sell.",
    "整体投资评级：Bullish", "整体投资评级：积极看涨",
    "| 项目 | 判断 |\n| --- | --- |\n| 最终研究结论 | BUY |",
])
def test_formal_prose_rating_ownership(text):
    assert internal_rating_claims(text)
    accepted = f.build_canonical_final_state(sell_state(text))
    assert not f._rating_artifact_claims(accepted, accepted["accepted_report_markdown"])
    assert any(x.get("category") == "SECONDARY_INTERNAL_RATING" and x.get("agent") == "News Analyst"
               for x in accepted["evidence_audit"])


@pytest.mark.parametrize("text", [
    "技术面偏多，短期动能偏强。", "Bullish factor: 盈利改善。",
    "技术指标给出看涨因素。",
    "Nomura recommends Buy.", "分析师一致预期为 Strong Buy。",
    "公司管理层表示：我们预计收入增长。",
])
def test_analysis_and_attribution_survive(text):
    assert not internal_rating_claims(text)


@pytest.mark.parametrize("text", ["Nomura recommends Buy.", "某券商建议买入。", "分析师一致预期为 Strong Buy。"])
def test_attributed_rating_survives_approved_execution_pruning(text):
    accepted = f.build_canonical_final_state(sell_state(text))
    assert text in accepted["accepted_report_markdown"]


@pytest.mark.parametrize("text", [
    "建议逢低关注，回调至关键技术支撑位可考虑布局。",
    "仓位建议：轻仓至中仓，波动率较低时可适当加仓。",
    "建议现有持仓者减持。", "Consider entering long at 8000.",
    "Increase position to 5%.", "Consider adding exposure.",
    "| Trigger | Recommended action |\n| --- | --- |\n| Guidance cut | Sell |",
])
def test_approved_sell_does_not_authorize_analyst_execution(text):
    assert f._execution_violation(text)
    state = sell_state(text)
    accepted = f.build_canonical_final_state(state)
    contract = accepted["final_output_contract"]
    assert contract["execution_allowed"]
    assert contract["status"] == "FINALIZED", contract["artifact_issues"]
    remaining = f._artifact_without_validated_execution(accepted, accepted["accepted_report_markdown"])
    assert f._artifact_execution_claims(remaining) == []
    findings = [x for x in accepted["evidence_audit"] if x.get("category") == "UNAPPROVED_EXECUTION_CLAIM"]
    assert findings and all(x["resolution"] == "CLAIM_REMOVED_OR_REPLACED" for x in findings)
    for finding in findings:
        assert finding["claim_sha256"] == hashlib.sha256(finding["original_claim"].encode()).hexdigest()
        assert finding["accepted_artifact_sha256"] == contract["accepted_report_sha256"]


@pytest.mark.parametrize("text", [
    "当前不建议建仓。", "空仓者禁止建仓。", "无法支持实时入场参数。",
    "若回调，不建议布局。",
    "若未来 MACD 跌破零轴，则重新评估。", "该合作有望强化5G/6G业务布局。",
    "历史上公司出售过资产。", "若资金不足，公司可能出售非核心资产。",
])
def test_research_monitoring_and_prohibition_allowed(text):
    assert not f._execution_violation(text)


@pytest.mark.parametrize("text", [
    "Now I have all the data needed for a comprehensive analysis. Let me compile the detailed report.",
    "I have gathered the required data. I'll produce the final analysis.",
    "I will now analyze the data. Here is the report.",
    "Let us prepare our research report.",
])
def test_assistant_production_prose_removed_and_audited(text):
    accepted = f.build_canonical_final_state(sell_state(text))
    assert not f._generation_process_claims(accepted["accepted_report_markdown"])
    findings = [x for x in accepted["evidence_audit"] if x.get("semantic_type") == "PROCESS_NARRATION"]
    assert findings and all(x["resolution"] == "CLAIM_REMOVED_OR_REPLACED" for x in findings)


@pytest.mark.parametrize("text", [
    '> "I will prepare the report," management said.',
    '公司管理层表示：“We have gathered the required data.”',
    '“I will compile the analysis,” said the CFO.',
    'We expect improving profitability and strong revenue growth.',
])
def test_attributed_first_person_and_explanation_not_process(text):
    assert f._remove_generation_process_prose(text) == text


def test_process_removal_never_changes_an_identical_quoted_sentence():
    quoted = '> "Let me compile the report."'
    text = "Let me compile the report.\n" + quoted
    assert f._remove_generation_process_prose(text).strip() == quoted


@pytest.mark.parametrize("text,dimension", [
    ("最终研究结论: BUY", "domain_authority_consistent"),
    ("明确给出Underweight评级，建议现有持仓者减持。", "domain_authority_consistent"),
    ("仓位建议：可适当加仓", "execution_consistent"),
    ("建议逢低布局。", "execution_consistent"),
    ("Now I have all the data needed. Let me compile the detailed report.", "presentation_valid"),
])
def test_exact_candidate_blocks_after_all_upstream_pruning(text, dimension):
    compose = f.compose_user_report_markdown
    with patch.object(f, "compose_user_report_markdown", side_effect=lambda state: compose(state) + "\n\n## 市场附录\n\n" + text):
        accepted = f.build_canonical_final_state(sell_state())
    contract = accepted["final_output_contract"]
    assert contract["status"] == "BLOCKED"
    assert not contract["validation_dimensions"][dimension]


def test_canonical_block_values_ownership_and_republication():
    accepted = f.build_canonical_final_state(sell_state())
    assert accepted["validated_execution"]["action"] == "Sell"
    text = accepted["accepted_report_markdown"]
    assert "8513.0" in text and "9500.0" in text and "3.0%" in text and "交易方向 | 卖出" in text
    assert not f._validate_final_artifact(accepted, True, accepted_report=text)
    # Same numbers in an Analyst are still a second execution publisher.
    injected = text + "\n\n## Analyst appendage\n\n" + f._validated_execution_report(accepted["validated_execution"])
    assert f._validate_final_artifact(accepted, True, accepted_report=injected)
    tampered = copy.deepcopy(accepted)
    tampered["accepted_report_markdown"] = text.replace("8513.0", "8514.0")
    tampered["final_output_contract"]["accepted_report_sha256"] = hashlib.sha256(tampered["accepted_report_markdown"].encode()).hexdigest()
    with pytest.raises(ValueError):
        f.require_canonical_final_state(tampered)


def test_current_market_outlook_relabelled_not_deleted():
    state = sell_state("**整体评级：积极看涨（Bullish）**\nMACD 黄金交叉，趋势偏多。\n最终研究结论: BUY", "market_report")
    with patch.object(f, "canonical_market_authority", return_value={"status": "CURRENT"}):
        accepted = f.build_canonical_final_state(state)
        assert "技术面展望：积极看涨（Bullish）" in accepted["accepted_report_markdown"]
        assert "MACD 黄金交叉，趋势偏多。" in accepted["accepted_report_markdown"]
        assert "最终研究结论: BUY" not in accepted["accepted_report_markdown"]


def test_execution_findings_cannot_close_after_presentation_transform():
    accepted = f.build_canonical_final_state(sell_state("| 条件 | 来源 | 操作 |\n| --- | --- | --- |\n| 指引下调 | get_news | →减仓 |"))
    findings = [x for x in accepted["evidence_audit"] if x.get("category") == "UNAPPROVED_EXECUTION_CLAIM"]
    text = accepted["accepted_report_markdown"] + "\n\n## 附录\n| 条件 | 来源 | 操作 |\n| --- | --- | --- |\n| 指引下调 | 上游数据源 | →减仓 |"
    audit, issues = f._finalize_audit(findings, accepted, accepted_report=text, execution_allowed=True)
    assert issues and any(x["resolution"] == "UNRESOLVED" for x in audit)
