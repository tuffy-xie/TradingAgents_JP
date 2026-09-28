"""Portfolio ownership must not erase attributed broker research facts."""
import hashlib
from unittest.mock import patch

import pytest

from tests.test_final_output_contract import _jp_state
from tradingagents import final_output as output
from tradingagents.rating_authority import internal_rating_claims, remove_internal_ratings
from tradingagents.reporting import write_report_tree
from web.server import _render_report_html


def state_with(text, field="news_report"):
    state = _jp_state()
    state["final_trade_decision"] = "**评级**: Underweight\n\n风险偏高。"
    state["risk_debate_state"]["judge_decision"] = state["final_trade_decision"]
    state["trader_investment_plan"] = "Recommendation: Hold"
    state["investment_debate_state"]["judge_decision"] = "风险偏高。"
    if "." in field:
        outer, inner = field.split(".")
        state[outer][inner] = text
    else:
        state[field] = text
    return state


INTERNAL = [
    "综合评级：HOLD（持有）", "**最终建议：BUY**", "Recommendation: Sell",
    "## Investment recommendation\n\nOverweight", "## 综合评级\nHold",
    "| 项目 | 结论 |\n|---|---|\n| 评级 | HOLD |",
    "我们建议持有。", "Our recommendation: Buy", "FINAL TRANSACTION PROPOSAL: HOLD",
    "| 综合评级 |\n|---|\n| HOLD |",
    "## News Analyst\nRecommendation: Hold",
]
EXTERNAL = [
    "某券商维持买入评级。", "日系中坚券商维持**强气（买入）评级**",
    "分析师共识偏多", "17/18 位分析师给予买入。",
    "Broker recommendation: Buy", "According to Goldman Sachs, Rating: Buy",
    "## 分析师共识\n\n| 评级 | 人数 |\n|---|---|\n| 买入 | 多数 |",
    "## Analyst consensus\n\nRecommendation: Buy",
    "评级下调至Hold会增加风险。",
    "| 公司 | 分析师评级 | 强气（买入） | 机构看好 |",
]


@pytest.mark.parametrize("text", INTERNAL)
def test_internal_recommendations_have_claim_identity(text):
    claims = internal_rating_claims(text)
    assert claims
    assert all(text[c.start:c.end] == c.text for c in claims)
    assert not internal_rating_claims(remove_internal_ratings(text))


@pytest.mark.parametrize("text", EXTERNAL)
def test_external_facts_and_research_are_not_internal_recommendations(text):
    assert not internal_rating_claims(text)
    assert remove_internal_ratings(text) == text


@pytest.mark.parametrize("field", ["market_report", "news_report", "fundamentals_report", "investment_debate_state.judge_decision"])
def test_ownership_applies_across_analyst_and_research_fields(field, tmp_path):
    foreign = "某券商维持强气（买入）评级。"
    accepted = output.build_canonical_final_state(state_with(foreign + "\n综合评级：HOLD（持有）", field))
    contract = accepted["final_output_contract"]
    assert contract["status"] == "FINALIZED"
    assert contract["portfolio_rating"] == "Underweight"
    assert contract["validation_dimensions"]["domain_authority_consistent"]
    text = accepted["accepted_report_markdown"]
    assert foreign in text
    assert "综合评级：HOLD" not in text
    findings = [x for x in accepted["evidence_audit"] if x.get("category") == "SECONDARY_INTERNAL_RATING"]
    assert findings
    assert all(x["resolution"] == "CLAIM_REMOVED_OR_REPLACED" for x in findings)
    assert all(x["claim_sha256"] == hashlib.sha256(x["original_claim"].encode()).hexdigest() for x in findings)
    assert write_report_tree(accepted, "TEST.T", tmp_path).read_text() == text
    assert "综合评级：HOLD" not in _render_report_html(accepted, auto_print=False)


def test_attribution_is_clause_local_and_does_not_exempt_internal_sibling():
    text = "某券商维持买入评级；综合评级：HOLD（持有）。"
    cleaned = remove_internal_ratings(text)
    assert "某券商维持买入评级" in cleaned
    assert "HOLD" not in cleaned


def test_exact_artifact_defense_without_upstream_rating_pruning():
    with patch.object(output, "_enforce_portfolio_rating_ownership", side_effect=lambda state: (state, [])):
        accepted = output.build_canonical_final_state(state_with("综合评级：HOLD（持有）"))
    assert "综合评级：HOLD" in accepted["accepted_report_markdown"]
    contract = accepted["final_output_contract"]
    assert contract["status"] == "BLOCKED"
    assert contract["audit_closure_status"] == "UNRESOLVED"
    assert not contract["validation_dimensions"]["domain_authority_consistent"]
    assert any(x.startswith("CROSS_DOMAIN_AUTHORITY:INVESTMENT_RATING") for x in contract["artifact_issues"])
    with pytest.raises(ValueError):
        _render_report_html(accepted, auto_print=False)


def test_audit_does_not_close_format_changed_secondary_rating():
    accepted = output.build_canonical_final_state(state_with("综合评级：HOLD（持有）"))
    audit = [x for x in accepted["evidence_audit"] if x.get("category") == "SECONDARY_INTERNAL_RATING"]
    tampered = accepted["accepted_report_markdown"] + "\n## V. 新闻附录\nRecommendation: **Hold**\n"
    findings, issues = output._finalize_audit(audit, accepted, accepted_report=tampered, execution_allowed=False)
    assert issues
    assert all(x["resolution"] == "UNRESOLVED" for x in findings)
    assert all(x["execution_blocking"] for x in findings)


def test_same_rating_in_another_section_is_still_second_authority():
    accepted = output.build_canonical_final_state(state_with("Recommendation: Underweight"))
    assert "Recommendation: Underweight" not in accepted["accepted_report_markdown"]
    assert accepted["final_output_contract"]["status"] == "FINALIZED"


def test_portfolio_not_execution_side_field_owns_rating():
    accepted = output.build_canonical_final_state(state_with("风险较高。"))
    accepted["validated_execution"]["portfolio_rating"] = "Hold"
    assert not output._rating_artifact_claims(accepted, accepted["accepted_report_markdown"])


def test_publisher_rejects_injected_secondary_rating_even_with_recomputed_hash(tmp_path):
    accepted = output.build_canonical_final_state(state_with("风险较高。"))
    accepted["accepted_report_markdown"] += "\n## V. 补充分析\nRecommendation: Buy\n"
    accepted["final_output_contract"]["accepted_report_sha256"] = hashlib.sha256(accepted["accepted_report_markdown"].encode()).hexdigest()
    with pytest.raises(ValueError):
        write_report_tree(accepted, "TEST.T", tmp_path)


def test_us_rating_behavior_unchanged(tmp_path):
    state = {"market_context": {"market": "US"}, "news_report": "Recommendation: Hold",
             "final_trade_decision": "Rating: Buy"}
    assert output.build_canonical_final_state(state) == state
    assert "Recommendation: Hold" in write_report_tree(state, "TEST", tmp_path).read_text()
