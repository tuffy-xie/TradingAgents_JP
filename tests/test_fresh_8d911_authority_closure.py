"""A completed fresh graph is not acceptance if its composed prose leaks roles."""
import hashlib
from unittest.mock import patch

import pytest

from tests.test_jsf_position_semantic_closure import source_state
from tests.test_macro_outcome_authority_closure import macro_state
from tests.test_published_authority_ownership import sell_state
from tradingagents import final_output as f
from tradingagents.agents.evidence_enforcement import (
    enforce_agent_output,
    jsf_measure_scope_violation,
    probability_event_gate_violation,
)
from tradingagents.rating_authority import internal_rating_claims, remove_internal_ratings

RATINGS = [
    "**四、為何不選擇Sell而選擇Underweight**",
    "因此選擇Underweight而非Sell。",
    "我们选择 Buy，而不是 Hold。",
    "Our research chooses Underweight rather than Sell.",
    "透過減持降低下行風險，將資金保留至更合理的進場區間，是紀律性選擇。",
    "| 長線 | 基本面復甦邏輯未變，耐心持有核心部位 |",
    "###### 評級與策略",
    "###### 三、交易启示与策略建议",
]
EXECUTION = [
    "耐心持有核心部位。", "继续持有现有头寸。",
    "透過減持降低下行風險。", "若反彈則逐步減持現有部位。",
    "**1. 倉位調整（核心行動）**",
    "**最終行動綱領**：在當前價位，風險回報結構不利於新增曝險。",
    "| 长线 | 耐心持有核心部位 |",
]
MACRO = [
    "台海紧张局势预测概率小幅下降，地缘风险暂时缓和。",
    "台海地緣風險已經緩和。", "Geopolitical risk has eased.",
    "市场以96%置信度预期2026年全年不降息，与美联储点阵图一致。",
]


@pytest.mark.parametrize("text", RATINGS)
def test_choice_and_script_equivalence_preserve_original_claim_identity(text):
    claims = internal_rating_claims(text)
    assert claims
    assert all(text[c.start:c.end] == c.text for c in claims)
    assert not internal_rating_claims(remove_internal_ratings(text))


@pytest.mark.parametrize("text", EXECUTION)
def test_position_object_and_trade_predicate_not_script_specific(text):
    assert f._artifact_execution_claims(text)
    accepted = f.build_canonical_final_state(sell_state(text, "market_report"))
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert not f._artifact_execution_claims(f._artifact_without_validated_execution(
        accepted, accepted["accepted_report_markdown"]))
    findings = [x for x in accepted["evidence_audit"] if x.get("category") == "UNAPPROVED_EXECUTION_CLAIM"]
    assert findings and all(x["resolution"] == "CLAIM_REMOVED_OR_REPLACED" for x in findings)
    for finding in findings:
        assert finding["claim_sha256"] == hashlib.sha256(finding["original_claim"].encode()).hexdigest()
        assert finding["accepted_artifact_sha256"] == accepted["final_output_contract"]["accepted_report_sha256"]


@pytest.mark.parametrize("text", MACRO)
def test_event_odds_do_not_witness_observed_risk_or_dotplot_alignment(text):
    assert probability_event_gate_violation(macro_state(), text)
    state = macro_state()
    state["evidence_registry"].append({"source": "get_global_news", "verification_status": "VERIFIED_TOOL_OUTPUT",
                                     "allowed_for_current_decision": True, "value": text})
    assert not probability_event_gate_violation(state, text)


@pytest.mark.parametrize("text", [
    "若台海風險緩和，則重新評估。", "地缘风险可能缓和。",
    "Nomura chooses Buy rather than Hold.", "券商選擇維持Underweight評級。",
    "技術面偏多，MACD多頭交叉。", "公司持有供應商股份。",
    "公司保留核心業務。", "公司通過生產基地分散對沖供應鏈風險。",
    "公司通过卖出非核心资产降低风险。", "不通过减持降低下行风险。",
    "當前不支持減持。", "不建議加倉。", "不提供持有核心部位的執行計畫。",
    "歷史上投資者持有核心部位。", "若未來MACD跌破零軸，則重新評估。",
])
def test_script_fold_does_not_grant_a_blanket_direction_keyword_ban(text):
    assert not internal_rating_claims(text)
    assert not f._artifact_execution_claims(text)
    assert not probability_event_gate_violation(macro_state(), text)


@pytest.mark.parametrize("text,dimension", [
    *[(t, "domain_authority_consistent") for t in RATINGS],
    *[(t, "execution_consistent") for t in EXECUTION],
    *[(t, "domain_authority_consistent") for t in MACRO],
])
def test_exact_composed_candidate_blocks_without_upstream_help(text, dimension):
    compose = f.compose_user_report_markdown
    state = sell_state() if text not in MACRO else macro_state()
    with patch.object(f, "compose_user_report_markdown", side_effect=lambda s:
                      compose(s) + "\n\n## 研究附录\n\n" + text):
        accepted = f.build_canonical_final_state(state)
    contract = accepted["final_output_contract"]
    assert contract["status"] == "BLOCKED"
    assert not contract["validation_dimensions"][dimension]


@pytest.mark.parametrize("text", [
    "籌碼面數據揭示真實資金流向。", "籌碼面數據揭示資金撤退的結構性信號。",
    "这些指标无法抵消籌碼面揭示的結構性賣壓。", "JSF融资数据证明机构撤退。",
])
def test_lending_balance_cannot_prove_participant_cash_flows_after_numeric_pruning(text):
    state = source_state()
    assert jsf_measure_scope_violation(state, text)
    assert text not in enforce_agent_output(state, text, "Research Manager").text
    compose = f.compose_user_report_markdown
    with patch.object(f, "compose_user_report_markdown", side_effect=lambda s:
                      compose(s) + "\n\n## 研究附录\n\n" + text):
        accepted = f.build_canonical_final_state(state)
    assert accepted["final_output_contract"]["status"] == "BLOCKED"
    assert not accepted["final_output_contract"]["validation_dimensions"]["domain_authority_consistent"]


def test_lending_native_fact_and_independent_flow_evidence_remain():
    state = source_state()
    assert not jsf_measure_scope_violation(state, "JSF融资余额变化需持续观察。")
    assert not jsf_measure_scope_violation(state, "融资余额不证明机构撤退。")
    text = "交易所报告称筹码数据确认机构撤退。"
    state["evidence_registry"].append({"source": "Exchange flow report", "verification_status": "VERIFIED_SOURCE",
                                     "allowed_for_current_decision": True, "value": text})
    assert not jsf_measure_scope_violation(state, text)


@pytest.mark.parametrize("text", MACRO)
def test_replacement_describes_the_unconfirmed_subject_not_an_unrelated_company_outcome(text):
    checked = enforce_agent_output(macro_state(), text, "News Analyst")
    assert checked.findings and "公司需求" not in checked.text
    assert not probability_event_gate_violation(macro_state(), checked.text)
