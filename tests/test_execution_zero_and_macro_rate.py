"""Account-target vs trade-size and price-index vs annual-rate contracts."""
import copy
import hashlib
from unittest.mock import patch

import pytest

from tests.test_final_output_contract import _jp_state
from tradingagents import final_output as f
from tradingagents.agents.evidence_enforcement import (
    enforce_agent_output,
    macro_rate_identity_violation,
)
from tradingagents.agents.execution_validation import (
    EXECUTION_PLAN_FIELDS,
    reconcile_execution_authority,
    validate_execution_plan,
)


def plan(action="Sell", size="3%"):
    return f"Action: {action}\nEntry: 7500\nStop: 8200\nPosition: {size}"


@pytest.mark.parametrize("action", ["Buy", "Sell"])
def test_zero_trade_never_authorizes_entry_stop(action):
    validation = validate_execution_plan(plan(action, "0%"))
    assert validation["status"] == "DATA_UNAVAILABLE"
    assert validation["action"] == action
    assert all(validation.get(key) is None for key in EXECUTION_PLAN_FIELDS)


@pytest.mark.parametrize("text", [
    "减持至 0% 仓位", "目标仓位：0%", "reduce position to 0%",
    "Exit or reduce ABC position to 0%", "target allocation: 0%",
])
@pytest.mark.parametrize("context", ["", "Portfolio at the analysis date:\n- Current position in ABC: 100 units"])
def test_account_target_is_not_traded_exposure(text, context):
    result = reconcile_execution_authority(validate_execution_plan(plan()), trader_action="Sell",
                                           portfolio_rating="Sell", portfolio_text=text,
                                           portfolio_context=context)
    assert result["status"] == "DATA_UNAVAILABLE" and result["portfolio_rating"] == "Sell"
    assert result["detail"] == ("TARGET_POSITION_NOT_TRADE_SIZE" if context else "CLOSING_HOLDINGS_UNVERIFIED")
    assert result["execution_intent"] == "TARGET_FLAT"
    assert all(result.get(k) is None for k in EXECUTION_PLAN_FIELDS)


@pytest.mark.parametrize("action,size", [("Sell", "3%"), ("Buy", "3%"), ("Sell", "未提供"), ("Buy", "未提供")])
def test_existing_directional_risk_plans_remain(action, size):
    result = validate_execution_plan(plan(action, size))
    assert result["status"] == "OK"
    assert result["position_pct"] == (3 if size == "3%" else None)
    # Do not invent a book or classify an ambiguous Sell as a new short.
    assert "current_holdings" not in result and result.get("execution_intent") is None


@pytest.mark.parametrize("text", ["不建议减持至0%仓位。", "Do not reduce position to 0%."])
def test_withholding_does_not_turn_into_a_zero_target_order(text):
    result = reconcile_execution_authority(validate_execution_plan(plan()), trader_action="Sell",
                                           portfolio_rating="Underweight", portfolio_text=text)
    assert result["status"] == "OK"


def pce_state(units="Index 2017=100", value="130.455", date="2026-08-01", core=True):
    state = _jp_state()
    state["trade_date"] = "2026-10-08"
    state["evidence_registry"] = [{
        "evidence_id": "E-pce", "source": "get_macro_indicators", "metric": "tool_output",
        "claim_type": "FACT", "verification_status": "VERIFIED_TOOL_OUTPUT",
        "allowed_for_current_decision": True,
        "value": "## FRED: Personal Consumption Expenditures "
                 + ("Excluding Food and Energy (PCEPILFE)" if core else "(PCEPI)")
                 + f"\n- Units: {units}\n| Date | Value |\n| --- | --- |\n| {date} | {value} |\n",
    }]
    return state


@pytest.mark.parametrize("text", [
    "美国核心PCE同比高于美联储2%目标。", "核心PCE同比为130.455%。",
    "Core PCE YoY inflation is above the Fed's 2% target.",
    "コアPCE前年比は2%目標を超過。", "| 美国核心PCE | 高于2%目标 |",
])
def test_index_level_cannot_witness_annual_rate(text):
    state = pce_state()
    assert macro_rate_identity_violation(state, text)
    checked = enforce_agent_output(state, text, "News Analyst")
    assert any(x.warning == "macro_rate_identity_mismatch" for x in checked.findings)
    assert text not in checked.text


def test_section_identity_survives_sentence_segmentation():
    text = "### 核心PCE\n- 最新值：130.455（同比隐含约2.8%）。\n- 核心通胀仍高于2%目标。\n\n### 公司业务\n公司具有成长潜力。"
    checked = enforce_agent_output(pce_state(), text, "News Analyst")
    assert len([x for x in checked.findings if x.warning == "macro_rate_identity_mismatch"]) == 2
    assert "公司具有成长潜力" in checked.text


@pytest.mark.parametrize("text", [
    "核心PCE指数水平为130.455。", "若未来核心PCE同比高于2%，则重新评估。",
    "核心PCE指数水平不能证明同比率高于2%目标。", "公司具有成长潜力。",
])
def test_index_facts_withholding_and_hypotheses_remain(text):
    assert not macro_rate_identity_violation(pce_state(), text)


@pytest.mark.parametrize("units", ["Percent Change from Year Ago", "同比百分比"])
def test_correct_annual_series_supports_rate_and_target_comparison(units):
    state = pce_state(units, "2.8")
    assert not macro_rate_identity_violation(state, "核心PCE同比为2.8%。")
    assert not macro_rate_identity_violation(state, "核心PCE高于2%目标。")
    assert macro_rate_identity_violation(state, "核心PCE同比为3.2%。")
    assert macro_rate_identity_violation(state, "核心PCE低于2%目标。")


@pytest.mark.parametrize("units,date,core", [
    ("Percent Change", "2026-08-01", True),
    ("Percent Change from Year Ago", "2026-08-01", False),
    ("Percent Change from Year Ago", "2026-11-01", True),
])
def test_wrong_transform_series_or_future_date_does_not_bind(units, date, core):
    assert macro_rate_identity_violation(pce_state(units, "2.8", date, core), "核心PCE同比为2.8%。")


def test_requested_observation_date_and_conflicting_witnesses_fail_closed():
    state = pce_state("Percent Change from Year Ago", "2.8")
    assert macro_rate_identity_violation(state, "核心PCE 2026-07-01 同比为2.8%。")
    state["evidence_registry"].append(copy.deepcopy(state["evidence_registry"][0]))
    state["evidence_registry"][-1]["value"] = state["evidence_registry"][-1]["value"].replace("2.8", "3.0")
    assert macro_rate_identity_violation(state, "核心PCE同比为2.8%。")


@pytest.mark.parametrize("text", ["核心PCE同比高于2%目标。", "| 核心PCE | 高于2%目标 |"])
def test_final_contract_blocks_post_cleanup_rate_injection(text):
    compose = f.compose_user_report_markdown
    with patch.object(f, "compose_user_report_markdown", side_effect=lambda s: compose(s) + "\n\n## 新闻附录\n" + text):
        state = f.build_canonical_final_state(pce_state())
    assert state["final_output_contract"]["status"] == "BLOCKED"
    assert any("CROSS_DOMAIN_AUTHORITY" in issue for issue in state["final_output_contract"]["artifact_issues"])


def test_exact_execution_defense_does_not_trust_authorized_flag_or_prior_status():
    state = _jp_state()
    state["validated_execution"] = {"status": "OK", "action": "Sell", "portfolio_rating": "Sell",
                                    "entry": 7500, "stop": 8200, "position_pct": 0}
    assert "EXECUTION_ZERO_EXPOSURE_NOT_AUTHORIZED" in f._execution_cross_state_issues(state, True)
    state["validated_execution"]["status"] = "DATA_UNAVAILABLE"
    assert f._execution_cross_state_issues(state, True) == ["EXECUTION_PERMISSION_STATUS_CONFLICT"]


def test_final_contract_blocks_zero_exposure_injected_after_execution_cleanup():
    state = pce_state()
    state["trader_investment_plan"] = plan()
    state["validated_execution"] = validate_execution_plan(plan())
    state["final_trade_decision"] = "Rating: Sell\n估值偏高。"
    publish = f._apply_validated_execution

    def inject(candidate):
        candidate["validated_execution"]["position_pct"] = 0
        candidate["validated_execution"]["portfolio_stop_risk_pct"] = 0
        return publish(candidate)

    with patch.object(f, "_apply_validated_execution", side_effect=inject):
        accepted = f.build_canonical_final_state(state)
    contract = accepted["final_output_contract"]
    assert contract["status"] == "BLOCKED"
    assert not contract["validation_dimensions"]["execution_consistent"]
    assert "EXECUTION_ZERO_EXPOSURE_NOT_AUTHORIZED" in contract["artifact_issues"]


def test_target_flat_revocation_retains_sell_and_closes_claim_level_audit():
    state = pce_state()
    state["trader_investment_plan"] = plan("Sell", "0%")
    state["validated_execution"] = {"status": "OK", "action": "Sell", "entry": 7500, "stop": 8200, "position_pct": 0}
    state["final_trade_decision"] = "Rating: Sell\n清仓或减持至0%仓位。"
    accepted = f.build_canonical_final_state(state)
    contract = accepted["final_output_contract"]
    assert contract["status"] == "FINALIZED" and contract["portfolio_rating"] == "Sell"
    assert not contract["execution_allowed"]
    assert all(accepted["validated_execution"].get(k) is None for k in EXECUTION_PLAN_FIELDS)
    entries = [e for e in accepted["evidence_audit"] if e.get("category") == "EXECUTION_ACTION_CONSISTENCY"]
    assert entries and entries[0]["claim_sha256"] == hashlib.sha256(plan("Sell", "0%").encode()).hexdigest()
    assert entries[0]["resolution"] == "EXECUTABLE_PLAN_WITHHELD"
    assert entries[0]["accepted_artifact_sha256"] == contract["accepted_report_sha256"]


def test_changed_wording_cannot_close_annual_rate_mismatch():
    state = pce_state()
    state["news_report"] = "核心PCE同比高于2%目标。"
    accepted = f.build_canonical_final_state(state)
    findings = [e for e in accepted["evidence_audit"] if e.get("warning") == "macro_rate_identity_mismatch"]
    assert findings
    artifact = accepted["accepted_report_markdown"] + "\n核心PCE年率超过2%目标。"
    audit, issues = f._finalize_audit(findings, accepted, accepted_report=artifact, execution_allowed=False)
    assert issues and all(e["resolution"] == "UNRESOLVED" for e in audit)
