"""Removed numbers cannot license unbound outcomes or research rating agency."""
import copy
import hashlib
from unittest.mock import patch

import pytest

from tests.test_published_authority_ownership import sell_state
from tradingagents import final_output as f
from tradingagents.agents.evidence_enforcement import enforce_agent_output
from tradingagents.rating_authority import internal_rating_claims, remove_internal_ratings


def factor_state(text="风险较高。"):
    state = sell_state(text)
    state["evidence_registry"].append({
        "evidence_id": "E-index-observation", "source": "Japan Macro", "domain": "NEWS", "claim_type": "FACT",
        "verification_status": "VERIFIED_SOURCE", "allowed_for_current_decision": True,
        "value": "1D: sox=+2.40%; observed co-movements are not issuer-specific causal claims.",
        "derivation": {"numeric_tokens": ["+2.40%"]},
    })
    return state


INDEX_OUTCOMES = [
    "| 行业趋势 | SOX暴涨+AI需求 | ✅ 非常正面 |",
    "| 市场情绪 | 日经强势+SOX强势 | ✅ 正面 |",
    "费城半导体指数飙升。", "納斯達克指數強勢。",
    "The SOX index is surging.", "TOPIX has plunged.",
    "SOX上涨直接利好公司的估值。", "日经指数上涨证明公司需求增强。",
    "| 市场因素 | 公司影响 |\n|---|---|\n| SOX上涨 | 村田直接受益 |",
    "公司股价与SOX指数存在较强正相关性。",
    "The issuer stock price is positively correlated with SOX.",
]
RATING_AGENCY = [
    "持续监控以下指标，这将决定未来评级调整方向：",
    "持續監控以下三類指標的演變，這將決定未來評級調整方向：",
    "这些风险指标用于后续评级调整。",
    "研究信号指导评级转换方向。",
    "These indicators determine future rating adjustments.",
    "| 指标 | 用途 |\n|---|---|\n| 新订单 | 决定未来评级调整方向 |",
]


@pytest.mark.parametrize("text", INDEX_OUTCOMES)
def test_unbound_index_magnitude_and_issuer_outcome_are_claims_without_numbers(text):
    checked = enforce_agent_output(factor_state(), text, "News Analyst")
    assert any(x.warning == "cross_market_outcome_mismatch" for x in checked.findings)
    assert "暴涨" not in checked.text and "直接利好" not in checked.text


@pytest.mark.parametrize("text", RATING_AGENCY)
def test_non_portfolio_rating_adjustment_agency_becomes_research_framing(text):
    claims = internal_rating_claims(text)
    assert claims and all(x.semantic_type == "RECOMMENDATION_FRAMING" for x in claims)
    replaced = remove_internal_ratings(text)
    assert ("研究" in replaced or "research" in replaced) and not internal_rating_claims(replaced)
    state = factor_state()
    report = text + "\n- 观察订单与库存变化。" if text.endswith("：") else text
    state["investment_plan"] = report
    state["investment_debate_state"]["judge_decision"] = report
    accepted = f.build_canonical_final_state(state)
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert not f._rating_artifact_claims(accepted, accepted["accepted_report_markdown"])
    assert any(x.get("category") == "SECONDARY_INTERNAL_RATING"
               and x.get("resolution") == "CLAIM_REMOVED_OR_REPLACED"
               for x in accepted["evidence_audit"])


@pytest.mark.parametrize("text", [
    "| SOX观察值 | +2.40% |", "若SOX暴涨，可能有利于公司估值。",
    "SOX并未暴涨。", "无法确认SOX强势。",
    "AI服务器需求是潜在成长因素。", "公司回购计划执行中。",
    "公司通过多地生产基地对冲供应链风险。", "暂不加仓。",
    "若未来MACD跌破零轴，则重新评估。",
    "需持续监控SOX走势、订单与库存。", "评级调整可能影响估值。",
    "这些研究指标不决定未来评级调整方向。",
    "Nomura reports that these indicators determine future rating adjustments.",
    "外部券商决定未来评级调整方向。", "分析师共识：Buy。",
    "需监控SOX走势，公司订单扩张使核心业务直接受益。",
    "當SOX指數暴漲，則需要重新評估。",
    "当SOX指数回调、贷款余额飙升，则需要重新评估。",
    "When SOX is surging, then reassess the risks.",
    "需监控SOX、公司订单强势改善。",
    "公司股价与SOX可能存在相关性，需检验。",
])
def test_observations_monitoring_business_withholding_and_external_roles_survive(text):
    assert not enforce_agent_output(factor_state(), text, "News Analyst").findings
    assert not internal_rating_claims(text)


@pytest.mark.parametrize("text", INDEX_OUTCOMES)
def test_own_verified_outcome_can_witness_same_proposition_not_inference(text):
    state = factor_state()
    witness = {"evidence_id": "E-own-proposition", "source": "Source report",
               "verification_status": "VERIFIED_SOURCE", "allowed_for_current_decision": True,
               "claim_type": "FACT", "value": text}
    state["evidence_registry"].append(witness)
    assert not any(x.warning == "cross_market_outcome_mismatch"
                   for x in enforce_agent_output(state, text, "News Analyst").findings)
    witness["verification_status"] = "ANALYST_INFERENCE"
    assert any(x.warning == "cross_market_outcome_mismatch"
               for x in enforce_agent_output(state, text, "News Analyst").findings)


@pytest.mark.parametrize("text", [*INDEX_OUTCOMES, *RATING_AGENCY])
def test_final_contract_and_audit_block_post_cleanup_injection(text):
    compose = f.compose_user_report_markdown
    with patch.object(f, "compose_user_report_markdown", side_effect=lambda s:
                      compose(s) + "\n\n## 研究补充\n\n" + text):
        state = f.build_canonical_final_state(factor_state())
    contract = state["final_output_contract"]
    assert contract["status"] == "BLOCKED"
    assert not contract["validation_dimensions"]["domain_authority_consistent"]
    assert contract["audit_closure_status"] != "CLOSED"


def test_removed_precise_claim_cannot_close_after_same_subject_outcome_rewording():
    state = factor_state()
    original = "SOX20日涨幅15.72%，直接利好公司估值。"
    audit = [{"category": "UNSUPPORTED_CLAIM", "agent": "News Analyst", "field": "news_report",
              "warning": "unsupported_precise_number", "original_claim": original,
              "claim_sha256": hashlib.sha256(original.encode()).hexdigest(),
              "replacement_claim": "", "enforcement_action": "REMOVED"}]
    candidate = "| 行业趋势 | 费城半导体指数暴涨+AI需求 | ✅ 非常正面 |"
    state["news_report"] = candidate
    closed, issues = f._finalize_audit(copy.deepcopy(audit), state,
                                     accepted_report=candidate, execution_allowed=True)
    assert closed[0]["resolution"] == "UNRESOLVED" and closed[0]["execution_blocking"]
    assert issues
    legitimate = "需监控SOX走势。"
    state["news_report"] = legitimate
    closed, issues = f._finalize_audit(copy.deepcopy(audit), state,
                                     accepted_report=legitimate, execution_allowed=True)
    assert closed[0]["resolution"] == "CLAIM_REMOVED_OR_REPLACED" and not issues


def test_portfolio_monitoring_keeps_its_rating_adjustment_role():
    state = factor_state()
    state["final_trade_decision"] = "**评级**: Underweight\n\n这些指标决定未来评级调整方向。"
    state["risk_debate_state"]["judge_decision"] = state["final_trade_decision"]
    accepted = f.build_canonical_final_state(state)
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert "决定未来评级调整方向" in accepted["accepted_report_markdown"]
    assert not f._rating_artifact_claims(accepted, accepted["accepted_report_markdown"])


def test_index_observation_cannot_borrow_a_sibling_hypothesis_or_other_index_witness():
    state = factor_state()
    text = "| 行业因素 | SOX暴涨 | 公司需求可能改善 |"
    state["evidence_registry"].append({"source": "Source report", "verification_status": "VERIFIED_SOURCE",
                                       "allowed_for_current_decision": True, "value": "TOPIX暴涨。"})
    assert any(x.warning == "cross_market_outcome_mismatch"
               for x in enforce_agent_output(state, text, "News Analyst").findings)
    assert not enforce_agent_output(state, "| 指数监控 | SOX | 公司经营表现强势 |", "News Analyst").findings


def test_multiple_framing_replacements_preserve_original_offsets():
    text = "这些指标决定未来评级调整方向，本次决策的依据是订单情况。"
    replaced = remove_internal_ratings(text)
    assert replaced == "这些指标决定未来研究与风险判断，研究判断的依据是订单情况。"
