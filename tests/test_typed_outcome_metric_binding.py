"""Fresh-state relational evidence and named-metric authority regressions."""
import copy
import hashlib
from unittest.mock import patch

import pytest

from tests.test_jsf_position_semantic_closure import source_state
from tests.test_market_authority_closure import _state_with_tools
from tests.test_predicate_authority_closure import regime_state, unavailable_actual
from tests.test_rating_authority import state_with
from tradingagents import final_output as f
from tradingagents.agents.evidence_enforcement import (
    corporate_action_outcome_violation,
    current_financial_gate_violation,
    enforce_agent_output,
    jsf_measure_scope_violation,
    market_metric_identity_violation,
    probability_event_gate_violation,
)
from tradingagents.rating_authority import internal_rating_claims


def metric_state(text="风险较高。"):
    state = unavailable_actual(text, "market_report")
    tools = _state_with_tools()
    state.update({key: tools[key] for key in ("trade_date", "run_manifest", "market_context")})
    state["evidence_registry"] += tools["evidence_registry"]
    # The old union-of-numbers catalogue could incorrectly license another
    # indicator value found in an unrelated Fundamentals response.
    state["evidence_registry"].append({
        "evidence_id": "E-unrelated", "source": "get_fundamentals", "domain": "FUNDAMENTALS",
        "verification_status": "VERIFIED_TOOL_OUTPUT", "allowed_for_current_decision": True,
        "value": "A different metric is 7.25.",
    })
    return state


def inflation_state():
    state = regime_state()
    state["evidence_registry"].append({
        "evidence_id": "E-cpi", "source": "get_prediction_markets", "domain": "NEWS",
        "verification_status": "VERIFIED_TOOL_OUTPUT", "allowed_for_current_decision": True,
        "value": "- **Will Japan's core-core CPI increase by between 2.0 and 2.4% in 2026?** — Yes 78%\n"
                 "- **Will Japan's core CPI increase by between 2.0 and 2.4% in 2026?** — Yes 42%",
    })
    return state


@pytest.mark.parametrize("text", [
    "| 预测市场 | 美国衰退低概率 | ✅ | 需求支撑 | 中 |",
    "| 宏观 | 美国衰退概率 | 8% | 多头（需求） |",
    "| Fed 2026全年不降息 | 96% | 空头（流动性） |",
    "| 日本央行不加息 | 低概率 | 金融条件宽松 |",
])
def test_relational_event_outcome_does_not_require_percentage(text):
    state = regime_state()
    assert probability_event_gate_violation(state, text)
    checked = enforce_agent_output(state, text, "News Analyst")
    assert any(x.warning == "probability_event_mismatch" for x in checked.findings)
    assert not probability_event_gate_violation(state, checked.text)


@pytest.mark.parametrize("text", [
    "| 美国2026年衰退概率 | 8% | 若需求改善，则重新评估 |",
    "| 宏观 | 衰退低概率 | 可能有利于需求 |",
    "美国2026年衰退概率为8%。",
    "该协议可能成为未来业务增长催化剂。",
])
def test_event_facts_and_local_conditional_outcomes_remain(text):
    assert not probability_event_gate_violation(regime_state(), text)


def test_bound_event_cell_survives_unsupported_sibling_outcome():
    state = regime_state()
    text = "| 美国2026年衰退 | 8% | 需求支撑 |"
    checked = enforce_agent_output(state, text, "News Analyst")
    assert "8%" in checked.text and "需求支撑" not in checked.text
    assert not probability_event_gate_violation(state, checked.text)


@pytest.mark.parametrize("text", [
    "| 日本核心CPI 2026年 +2.0%~2.4% | 78% |",
    "日本通胀预期升温（78%概率核心CPI达2-2.4%）。",
    "| 日本CPI预期升温 | 78%达2-2.4% | 潜在收紧 |",
    "| Japan core CPI 2027 +2.0%~2.4% | 42% |",
    "| US core CPI 2026 +2.0%~2.4% | 42% |",
    "| 日本核心CPI 2026 +2.5%~2.9% | 42% |",
])
def test_cpi_definition_country_year_and_range_must_match_event(text):
    assert probability_event_gate_violation(inflation_state(), text)


@pytest.mark.parametrize("text", [
    "| 日本核心核心CPI 2026年 +2.0%~2.4% | 78% |",
    "| 日本核心CPI 2026年 +2.0%~2.4% | 42% |",
    "| Japan core-core CPI 2026 +2.0%~2.4% | 78% |",
    "若未来核心CPI超过2.4%，则重新评估。",
])
def test_cpi_valid_probability_and_plain_hypotheses_remain(text):
    assert not probability_event_gate_violation(inflation_state(), text)


def test_nominal_spread_is_not_an_inflation_expectations_measure():
    text = "曲线陡峭化，反映市场长期通胀预期有所升温。"
    assert probability_event_gate_violation(regime_state(), text)
    assert not probability_event_gate_violation(regime_state(), text.replace("反映", "可能反映"))


@pytest.mark.parametrize("text", [
    "为何不是Sell而是Underweight", "不是Buy，而是Hold。",
    "Rather than Buy, we choose Sell.",
])
def test_rating_contrast_still_selects_an_internal_rating(text):
    assert internal_rating_claims(text)
    state = state_with(text, "investment_debate_state.judge_decision")
    accepted = f.build_canonical_final_state(state)
    assert not f._rating_artifact_claims(accepted, accepted["accepted_report_markdown"])


@pytest.mark.parametrize("text,selected", [
    ("不是Sell而是Underweight。", "Underweight"),
    ("支持Hold而非Buy或Sell。", "Hold"),
    ("Hold rather than Buy.", "Hold"),
])
def test_contrast_binds_the_selected_rating_not_the_rejected_alternative(text, selected):
    claims = internal_rating_claims(text)
    assert claims and claims[0].rating == selected
    artifact = "## III. 投资组合经理结论\n\n### 投资组合经理\n" + text
    assert not f.artifact_rating_violations(artifact, selected)


@pytest.mark.parametrize("text", [
    "**聪明钱的撤退信号（已验证）**", "专业投资者在用仓位表态。",
    "短期资金面的恶化是专业投资者真金白银的押注。",
    "Smart money withdrawal is confirmed.",
])
def test_jsf_balance_cannot_authorize_an_unattributed_participant_outcome(text):
    assert jsf_measure_scope_violation(source_state(), text)
    assert not enforce_agent_output(source_state(), text, "Portfolio Manager").text.strip()


@pytest.mark.parametrize("text", [
    "JSF融资余额和贷株余额并不证明机构资金撤退。",
    "专业投资者可能撤退，需要继续观察。", "股票回购计划执行中。",
    "海外生产基地可对冲地缘集中风险。",
])
def test_native_disclaimers_business_and_possible_inferences_remain(text):
    assert not jsf_measure_scope_violation(source_state(), text)
    assert not f._artifact_execution_claims(text)


def test_participant_outcome_can_have_an_independent_actual_witness():
    text = "专业投资者在用仓位表态。"
    state = source_state()
    state["evidence_registry"].append({"source": "Exchange", "claim_type": "FACT",
                                     "verification_status": "VERIFIED_SOURCE", "value": text})
    assert not jsf_measure_scope_violation(state, text)


@pytest.mark.parametrize("text", [
    "毛利率持续改善。", "利润率结构性恶化。", "自由现金流的硬数据崩塌。",
    "毛利率改善但净利率恶化。", "这是已核实的财报数据。",
])
def test_unqualified_metric_change_needs_current_actual(text):
    assert current_financial_gate_violation(unavailable_actual(), text)


@pytest.mark.parametrize("text", [
    "FY2025毛利率改善。", "2025 年净利率恶化。",
    "该合并对中长期利润率改善有利。", "有助于中长期利润率改善。", "未来自由现金流可能增长。",
    "毛利率改善是看涨因素。", "利润率恶化是风险。",
    "毛利率是否改善仍待确认。", "公司盈利能力具有结构性优势。",
])
def test_historical_prospective_and_conceptual_financial_analysis_preserved(text):
    assert not current_financial_gate_violation(unavailable_actual(), text)


@pytest.mark.parametrize("text", ["MACD: 7.25", "| MACD | **7.25** | 偏多 |", "VWMA: 7.25"])
def test_an_unrelated_number_is_not_an_indicator_witness(text):
    assert market_metric_identity_violation(metric_state(), text)
    assert "market_metric_identity_mismatch" in enforce_agent_output(metric_state(), text, "Market Analyst").warnings


@pytest.mark.parametrize("text", ["MACD: 2.50", "| MACD | 2.5 | 多头因素 |", "MACD趋势偏多。"])
def test_current_metric_date_and_display_precision_match(text):
    assert not market_metric_identity_violation(metric_state(), text)


def test_explicit_history_does_not_impersonate_current_metric():
    assert not market_metric_identity_violation(metric_state(), "2025-09-01 MACD: 7.25")


def test_derived_indicator_observation_requires_its_typed_components():
    state = metric_state()
    assert market_metric_identity_violation(state, "MACD金叉已确认。")
    assert market_metric_identity_violation(state, "Histogram保持正值扩张。")
    assert not market_metric_identity_violation(state, "若未来MACD形成金叉，则重新评估。")
    assert not market_metric_identity_violation(state, "MACD在零轴上方运行。")
    assert not market_metric_identity_violation(state, "MACD完成零轴金叉。")
    assert market_metric_identity_violation(state, "MACD在零轴上方与Signal线形成金叉。")


def test_indicator_date_and_duplicate_conflicting_witness_fail_closed():
    state = metric_state()
    indicator = next(e for e in state["evidence_registry"] if e.get("source") == "get_indicators" and e.get("value"))
    duplicate = copy.deepcopy(indicator)
    duplicate["value"] = duplicate["value"].replace("2.5", "7.25")
    state["evidence_registry"].append(duplicate)
    assert market_metric_identity_violation(state, "MACD: 2.50")


@pytest.mark.parametrize("text", ["耐心离场是唯一负责任的操作。", "投资者应离场。"])
def test_exit_alias_is_execution_not_a_research_opinion(text):
    assert f._artifact_execution_claims(text)


def test_list_replacement_preserves_the_original_numbered_item():
    checked = enforce_agent_output(unavailable_actual(), "1. 毛利率持续改善。\n2. 公司具有成长潜力。", "Fundamentals Analyst")
    assert checked.text.startswith("1. ")
    assert "\n2. 公司具有成长潜力。" in checked.text


@pytest.mark.parametrize("text", [
    "回购对股价有直接支撑作用。", "纳入TOPIX指数将触发被动资金流入。",
])
def test_issuer_action_does_not_witness_realized_price_or_flow(text):
    assert corporate_action_outcome_violation(state_with(""), text)
    assert not corporate_action_outcome_violation(state_with(""), "可能" + text)


@pytest.mark.parametrize("text,factory", [
    ("| 预测市场 | 美国衰退低概率 | 需求支撑 |", regime_state),
    ("| 日本核心CPI 2026 +2.0%~2.4% | 78% |", inflation_state),
    ("为何不是Sell而是Underweight", state_with),
    ("专业投资者在用仓位表态。", source_state),
    ("毛利率持续改善。", unavailable_actual),
    ("回购对股价有直接支撑作用。", state_with),
    ("MACD: 7.25", metric_state),
    ("耐心离场是唯一负责任的操作。", source_state),
    ("**交易启示：**\n公司质量良好。", state_with),
])
def test_final_contract_blocks_direct_post_cleanup_injection(text, factory):
    compose = f.compose_user_report_markdown
    with patch.object(f, "compose_user_report_markdown", side_effect=lambda s:
                      compose(s) + "\n\n## 研究附录\n\n" + text):
        accepted = f.build_canonical_final_state(factory("风险较高。") if factory is state_with else factory())
    contract = accepted["final_output_contract"]
    assert contract["status"] == "BLOCKED" and contract["artifact_issues"]


def test_semantic_rewording_cannot_close_a_prior_outcome_finding():
    text = "美国2026年衰退概率为8%，需求支撑已经形成。"
    state = f.build_canonical_final_state(regime_state(text))
    findings = [e for e in state["evidence_audit"] if e.get("warning") == "probability_event_mismatch"]
    assert findings
    candidate = state["accepted_report_markdown"] + "\n\n| 预测市场 | 美国衰退低概率 | 多头（需求） |"
    audit, issues = f._finalize_audit(findings, state, accepted_report=candidate, execution_allowed=False)
    assert issues and all(e["resolution"] == "UNRESOLVED" for e in audit)
    for entry in audit:
        assert hashlib.sha256(entry["original_claim"].encode()).hexdigest() == entry["claim_sha256"]
        assert entry["accepted_artifact_sha256"] == hashlib.sha256(candidate.encode()).hexdigest()


def test_publication_does_not_erase_legal_current_analysis_or_execution():
    state = source_state("风险较高。")
    state.update({k: v for k, v in metric_state("MACD: 2.50").items()
                  if k in {"run_manifest", "trade_date", "market_context", "evidence_registry"}})
    state["market_report"] = "MACD: 2.50\n技术面偏多，公司成长潜力较好。"
    accepted = f.build_canonical_final_state(state)
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert "MACD: 2.50" in accepted["accepted_report_markdown"]
    assert "技术面偏多" in accepted["accepted_report_markdown"]
    assert accepted["validated_execution"]["action"] == "Sell"


@pytest.mark.parametrize("text", [
    "技術指標需current_eligible=True後再作判斷。",
    "当current_eligible=False时不能发布当前指标。",
    "来源get_stock_data已记录。", "已知cache_valid=false但公司具有成长潜力。",
    "### Markdown 总结表\n\n公司具有成长潜力。",
])
def test_engineering_tokens_have_ascii_boundaries_in_multilingual_prose(text):
    assert f.validate_final_report_text(text, execution_allowed=True)
    public = f._publicize_inline_text(text)
    assert not f.validate_final_report_text(public, execution_allowed=True)
    if "公司具有成长潜力" in text:
        assert "公司具有成长潜力" in public
