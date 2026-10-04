"""A non-Portfolio report may assess research, not own the investment decision."""
import hashlib
from unittest.mock import patch

import pytest

from tests.test_rating_authority import state_with
from tradingagents import final_output as f
from tradingagents.rating_authority import internal_rating_claims, remove_internal_ratings
from tradingagents.reporting import write_report_tree

FRAMES = [
    "## 投资建议总结", "## 投资建议与风险提示", "## 投资决策报告：示例公司",
    "## 示例公司的投资建议摘要", "**投资决策总结**",
    "## Investment decision report", "## Investment advice and risk notes",
    "估值溢价是本次决策的决定性因素。", "**可能改变看空立场的催化剂：**",
    "强化看空立场的催化剂。", "## 最终决策", "Our decision rests on valuation.",
    "The bearish stance may change if orders recover.",
]
FACTS = [
    "公司质量良好。", "估值溢价是本次研究判断的重要因素。", "## 看空因素",
    "## 公司投资决策流程", "## 董事会资本开支投资决策报告", "经营资源配置优化。",
    "公司本次投资决策涉及扩建工厂。", "董事会做出最终决策。",
    "投资者应结合自身风险承受能力和投资目标做出最终决策。",
    "看空方立场认为竞争加剧。", "看空方的看空立场基于估值。",
    "## 外部券商投资建议总结\nNomura maintains Buy rating.",
    "Nomura reports its investment decision and maintains Buy rating.",
    "分析师共识评级：Strong Buy。", "## 技术面展望\n积极看涨。",
    "当前不支持加仓。", "股票回购计划执行中。", "若未来订单落地则重新评估。",
    "## 交易建议的限制\n当前不提供交易建议。", "## 不提供投资建议",
    "## 投资建议的定义\n评级与风险分析不同。",
]


@pytest.mark.parametrize("text", FRAMES)
def test_nonportfolio_framing_relabels_without_losing_research(text):
    claims = internal_rating_claims(text)
    assert claims and all(text[c.start:c.end] == c.text for c in claims)
    cleaned = remove_internal_ratings(text)
    assert not internal_rating_claims(cleaned)
    state = state_with(text + "\n\n- 公司质量良好。", "fundamentals_report")
    result = f.build_canonical_final_state(state)
    contract = result["final_output_contract"]
    assert contract["status"] == "FINALIZED"
    assert "公司质量良好。" in result["accepted_report_markdown"]
    assert not f._rating_artifact_claims(result, result["accepted_report_markdown"])
    findings = [e for e in result["evidence_audit"] if e.get("agent") == "Fundamentals Analyst"
                and e.get("category") == "SECONDARY_INTERNAL_RATING"]
    assert findings
    for finding in findings:
        assert hashlib.sha256(finding["original_claim"].encode()).hexdigest() == finding["claim_sha256"]
        assert finding["authority_owner"] == "Portfolio Manager"
        assert finding["resolution"] == "CLAIM_REMOVED_OR_REPLACED"
        assert finding["accepted_artifact_sha256"] == contract["accepted_report_sha256"]


@pytest.mark.parametrize("text", FACTS)
def test_analysis_external_business_withholding_and_user_decisions_remain(text):
    assert not internal_rating_claims(text)
    assert remove_internal_ratings(text) == text


@pytest.mark.parametrize("text", FRAMES)
def test_exact_composer_fault_injection_blocks_framing_without_upstream_findings(text):
    compose = f.compose_user_report_markdown
    def injected(state):
        # Actual section body, not an orphan title or structurally invalid shell.
        return compose(state) + "\n\n## 研究结论\n\n" + text.replace("## ", "##### ") + "\n\n公司质量良好。"
    with patch.object(f, "compose_user_report_markdown", side_effect=injected):
        result = f.build_canonical_final_state(state_with("公司质量良好。"))
    contract = result["final_output_contract"]
    assert contract["status"] == "BLOCKED"
    assert not contract["validation_dimensions"]["domain_authority_consistent"]
    assert any("RATING" in issue for issue in contract["artifact_issues"])


def test_own_portfolio_decision_framing_and_canonical_sell_keep_authority():
    state = state_with("## 投资建议总结\n\n公司质量良好。", "fundamentals_report")
    state["trader_investment_plan"] = "Recommendation: Sell\nEntry: 8513\nStop: 9500\nPosition: 3%"
    state["validated_execution"] = {"status": "OK"}
    state["final_trade_decision"] = "**评级**: Underweight\n\n估值溢价是本次决策的决定性因素。"
    result = f.build_canonical_final_state(state)
    assert result["final_output_contract"]["status"] == "FINALIZED"
    assert result["final_output_contract"]["portfolio_rating"] == "Underweight"
    assert "估值溢价是本次决策的决定性因素" in result["accepted_report_markdown"]
    execution = result["validated_execution"]
    assert (execution["action"], execution["entry"], execution["stop"], execution["position_pct"]) == ("Sell", 8513, 9500, 3)


def test_relabel_preserves_evaluation_and_risk_prose_and_shared_us_path(tmp_path):
    text = "**估值溢价是本次决策的决定性因素；可能改变看空立场的催化剂是订单恢复。**"
    cleaned = remove_internal_ratings(text)
    assert "估值溢价" in cleaned and "订单恢复" in cleaned and "**" in cleaned
    assert not internal_rating_claims(cleaned)
    us = {"market_report": "## Investment decision report\nBuy\nEntry: 10\nStop: 9\nTarget: 12"}
    write_report_tree(us, "TEST", tmp_path)
    assert "Investment decision report" in (tmp_path / "complete_report.md").read_text()


def test_educational_or_corporate_title_never_exempts_internal_recommendation():
    for title in ("交易建议的限制", "公司投资决策流程", "不提供投资建议"):
        claims = internal_rating_claims("## " + title + "\n\n最终建议：Buy")
        assert claims and any(c.rating == "Buy" for c in claims)
