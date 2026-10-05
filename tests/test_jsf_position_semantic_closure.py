"""Position constraints and source-native lending identity survive rewording."""
import hashlib
from unittest.mock import patch

import pytest

from tests.test_published_authority_ownership import sell_state
from tradingagents import final_output as f
from tradingagents.agents.evidence_enforcement import enforce_agent_output


def source_state(text="风险较高。", field="news_report"):
    state = sell_state(text, field)
    state["evidence_registry"].append({
        "evidence_id": "E-jsf-native", "domain": "SHORT", "source": "JSF",
        "metric": "securities_finance_balance", "semantic_basis": "OBSERVABLE_LENDING_BALANCE",
        "verification_status": "VERIFIED_TOOL_OUTPUT", "current_eligible": True,
        "value": {"metadata": {"stock_loan_balance": 2100, "finance_balance": 570500,
                               "net_balance": 568400}},
    })
    return state


POSITION_CONSTRAINTS = [
    "ボリンジャー上限至近の現状 8,467 円では新規フルポジションは避ける。",
    "現在の価格ではフルポジションを避ける。",
    "新規ポジションは半分に抑える。",
    "ポジションを3%に制限する。",
    "支持線を割った場合はポジションを縮小する。",
    "| 条件 | 操作 |\n| --- | --- |\n| 現在の価格 | フルポジションを避ける |",
]
JSF_MISLABELS = [
    "空头余额从当前 2,100 股进一步下降至接近零。",
    "空头总量目前为2,100股。",
    "若空头余额从2,100股降至零，则重新评估。",
    "Short interest is now 2,100 shares.",
    "空売り残高は現在2,100株だ。",
    "| 指标 | 当前值 |\n| --- | --- |\n| 空头余额 | 2,100股 |",
    "| 当前值 | 指标 |\n| --- | --- |\n| 2,100股 | 空头余额 |",
    "JSF可观察的是贷株，不代表全部空头。但空头仓位目前为2,100股。",
]


@pytest.mark.parametrize("text", POSITION_CONSTRAINTS)
def test_qualified_position_constraints_are_not_whole_action_withholding(text):
    assert f._artifact_execution_claims(text)
    accepted = f.build_canonical_final_state(source_state(text, "investment_debate_state.judge_decision"))
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert not f._artifact_execution_claims(f._artifact_without_validated_execution(
        accepted, accepted["accepted_report_markdown"]))
    findings = [x for x in accepted["evidence_audit"] if x.get("category") == "UNAPPROVED_EXECUTION_CLAIM"]
    assert findings and all(x["resolution"] == "CLAIM_REMOVED_OR_REPLACED" for x in findings)


@pytest.mark.parametrize("text", [
    "エントリーは見送る。", "新規買いは急がない。", "現在は新規取引を認めない。",
    "新規ポジションは取らない。", "ポジションは提供しない。",
    "会社の海外生産拠点で自然為替ヘッジを行う。",
    "自社株買いの実施が継続している。", "ボリンジャー上限は8,467円。",
    "会社の業界ポジションを拡大する。", "ポジションを縮小しない。",
])
def test_pure_withholding_business_and_technical_observations_are_preserved(text):
    assert not f._artifact_execution_claims(text)


@pytest.mark.parametrize("text", JSF_MISLABELS)
def test_native_loan_quantity_cannot_be_renamed_short_interest(text):
    state = source_state(text)
    checked = enforce_agent_output(state, text, "Portfolio Manager")
    findings = [x for x in checked.findings if x.warning == "jsf_measure_scope_mismatch"]
    assert findings
    assert not f._artifact_evidence_gate_findings(state, checked.text)
    for finding in findings:
        assert finding.claim_sha256 == hashlib.sha256(finding.original_claim.encode()).hexdigest()
    accepted = f.build_canonical_final_state(state)
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert not f._artifact_evidence_gate_findings(accepted, accepted["accepted_report_markdown"])
    audit = [x for x in accepted["evidence_audit"] if x.get("warning") == "jsf_measure_scope_mismatch"]
    assert audit and all(x["category"] == "SEMANTIC_MISMATCH" and x["resolution"] == "CLAIM_REMOVED_OR_REPLACED" for x in audit)


@pytest.mark.parametrize("text", [
    "JSF贷株余额为2,100株，融资余额为570,500株，差引余额为568,400株。",
    "JSF贷株余额不代表全市场空头余额。",
    "JSF stock-loan balance of 2,100 shares is not total short interest.",
    "融资余额下降并不证明机构空头余额下降。",
    "官方披露机构A的空头持仓比例为0.6%。",
])
def test_native_lending_and_scope_disclaimers_are_not_mislabeled_measurements(text):
    assert "jsf_measure_scope_mismatch" not in enforce_agent_output(source_state(), text, "Portfolio Manager").warnings


def test_independent_typed_short_interest_fact_is_not_jsf_lending():
    state = source_state()
    state["evidence_registry"].append({
        "evidence_id": "E-independent-short", "domain": "SHORT", "source": "Exchange",
        "metric": "short_interest", "verification_status": "VERIFIED_TOOL_OUTPUT",
        "current_eligible": True, "value": {"short_interest": 2100},
    })
    assert "jsf_measure_scope_mismatch" not in enforce_agent_output(
        state, "Exchange reports short interest of 2,100 shares.", "Portfolio Manager").warnings
    # Attribution cannot turn JSF's lending measure into that external metric.
    assert "jsf_measure_scope_mismatch" in enforce_agent_output(
        state, "JSF reports short interest of 2,100 shares.", "Portfolio Manager").warnings


@pytest.mark.parametrize("text", POSITION_CONSTRAINTS + JSF_MISLABELS)
def test_exact_candidate_defense_needs_no_upstream_findings(text):
    compose = f.compose_user_report_markdown
    with patch.object(f, "compose_user_report_markdown", side_effect=lambda state:
                      compose(state) + "\n\n## 研究附录\n\n" + text):
        accepted = f.build_canonical_final_state(source_state())
    contract = accepted["final_output_contract"]
    assert contract["status"] == "BLOCKED" and contract["audit_closure_status"] != "CLOSED"
    assert any(x.startswith(("FINAL_ARTIFACT_UNAUTHORIZED_EXECUTION:", "CROSS_DOMAIN_AUTHORITY:JSF:"))
               for x in contract["artifact_issues"])


def test_removed_claim_cannot_close_when_renamed_jsf_semantic_class_survives():
    state = source_state()
    original = "空头余额当前为2,100股。"
    replacement_wording = "Short interest is now 2,100 shares."
    finding = {
        "category": "SEMANTIC_MISMATCH", "warning": "jsf_measure_scope_mismatch",
        "field": "news_report", "agent": "News Analyst", "original_claim": original,
        "claim_sha256": hashlib.sha256(original.encode()).hexdigest(), "replacement_claim": "",
    }
    entries, issues = f._finalize_audit([finding], state, accepted_report=replacement_wording, execution_allowed=True)
    assert entries[0]["resolution"] == "UNRESOLVED" and entries[0]["execution_blocking"]
    assert issues
    closed, issues = f._finalize_audit([finding], state, accepted_report="JSF贷株余额为2,100株。", execution_allowed=True)
    assert closed[0]["resolution"] == "CLAIM_REMOVED_OR_REPLACED" and not issues


def test_position_constraint_cannot_close_via_rewording_or_bold_localization():
    original = POSITION_CONSTRAINTS[0]
    artifact = "**現在の価格ではフルポジションを避ける。**"
    assert f._execution_claim_survives(original, artifact)
    assert not f._execution_claim_survives(original, "新規ポジションは取らない。")
