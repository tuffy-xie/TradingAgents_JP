"""Exact artifact and canonical state obey ownership, not detector counts."""
import hashlib
from unittest.mock import patch

import pytest

from tests.test_macro_outcome_authority_closure import macro_state
from tests.test_rating_authority import state_with
from tradingagents import final_output as f
from tradingagents.agents.evidence_enforcement import (
    enforce_agent_output,
    probability_event_gate_violation,
)
from tradingagents.agents.execution_validation import (
    EXECUTION_PLAN_FIELDS,
    reconcile_execution_authority,
    validate_execution_plan,
)
from tradingagents.rating_authority import internal_rating_claims


@pytest.mark.parametrize("text", [
    "对于现有持仓投资者，建议继续持有以捕捉盈利增长带来的股价上涨。",
    "建议投资者继续持有该股票。", "推荐维持持有。",
    "We recommend investors continue holding the stock.",
])
def test_holding_advice_has_same_owner_with_aspect_or_audience(text):
    claims = internal_rating_claims(text)
    assert claims and claims[0].rating == "Hold"
    accepted = f.build_canonical_final_state(state_with(text, "fundamentals_report"))
    assert text not in accepted["accepted_report_markdown"]
    findings = [x for x in accepted["evidence_audit"] if x.get("category") == "SECONDARY_INTERNAL_RATING"]
    assert findings and all(x["resolution"] != "UNRESOLVED" for x in findings)
    assert all(hashlib.sha256(x["original_claim"].encode()).hexdigest() == x["claim_sha256"] for x in findings)


@pytest.mark.parametrize("text", [
    "券商建议投资者继续持有该股。", "Nomura recommends investors continue holding the stock.",
    "不建议继续持有。", "历史上建议继续持有该股。",
    "公司具有良好的成长潜力。", "股票回购计划执行中。",
    "公司生产网络可对冲地缘集中风险。",
])
def test_holding_precision_preserves_external_withholding_and_business(text):
    assert not internal_rating_claims(text)


MACRO_CLAIMS = [
    "| 美国2026年末衰退 | 8% | 低 | 经济软着陆预期强化 |",
    "| 日本2026年衰退 | 4% | 极低 | 日本经济稳健 |",
    "| 美国宏观 | 2026衰退概率 | 8% | ✅ 软着陆 |",
    "| US recession in 2026 | 8% | Low | Soft landing |",
    "| Japan recession in 2026 | 4% | Low | The economy is robust |",
    "预测市场显示日本经济软着陆概率极高。",
    "US soft landing probability is very high.",
    "日本衰退概率仅4%，经济温和增长。",
    "| 2026年USリセッション | 8% | 低リスク = 電子部品需要底堅 |",
    "| 日本2026年衰退 | 4% | 日本流动性无收紧压力 |",
]


@pytest.mark.parametrize("text", MACRO_CLAIMS)
def test_outcome_cells_and_qualitative_odds_need_their_own_source_event(text):
    state = macro_state(text)
    assert probability_event_gate_violation(state, text)
    checked = enforce_agent_output(state, text, "News Analyst")
    assert checked.findings and checked.findings[0].original_claim == text
    assert not probability_event_gate_violation(state, checked.text)
    if text.startswith("|"):
        assert "8%" in checked.text or "4%" in checked.text
        assert "不据此推断宏观状态" in checked.text


@pytest.mark.parametrize("text", [
    "| 日本2026年衰退 | 4% | 低 | 若经济稳健，则重新评估需求 |",
    "| US recession in 2026 | 8% | Low | Soft landing could support future demand |",
    "日本2026年衰退概率为4%。", "美国经济有望软着陆。",
    "若未来 MACD 跌破零轴，则重新评估。",
])
def test_bound_facts_and_local_hypotheses_are_not_realized_outcomes(text):
    assert not probability_event_gate_violation(macro_state(), text)


def test_native_soft_landing_event_and_independent_outcome_have_distinct_witnesses():
    state = macro_state()
    state["evidence_registry"][-1]["value"] += "\n- **Japan soft landing in 2026?** — Yes 96%"
    assert not probability_event_gate_violation(state, "日本2026年软着陆概率极高。")
    row = "| 日本2026年衰退 | 4% | 日本经济稳健 |"
    state["evidence_registry"].append({"source": "get_global_news", "verification_status": "VERIFIED_TOOL_OUTPUT",
                                      "allowed_for_current_decision": True, "value": row})
    assert not probability_event_gate_violation(state, row)
    state["evidence_registry"][-1]["allowed_for_current_decision"] = False
    assert probability_event_gate_violation(state, row)


@pytest.mark.parametrize("text", [
    "严格执行7,200日元止损", "设置6,800円止損", "执行9000元止盈",
    "| 纪律 | 严守 | 7200日元止损 |",
])
def test_value_before_stop_action_keeps_execution_relation(text):
    assert f._execution_violation(text)
    assert not f._artifact_execution_claims(f._prune_unapproved_execution(text))


@pytest.mark.parametrize("text", [
    "不建议执行7200日元止损。", "不提供止损价7200。",
    "历史上执行7200日元止损。", "公司回购计划执行中。",
    "当前不支持卖出。", "空仓者禁止建仓。",
])
def test_stop_relation_does_not_waive_action_local_polarity_or_history(text):
    assert not f._execution_violation(text)


def test_row_probability_cannot_borrow_another_event_value():
    assert probability_event_gate_violation(macro_state(), "| 日本2026年衰退 | 8% |")
    assert not probability_event_gate_violation(macro_state(), "| 日本2026年衰退 | 4% |")
    assert probability_event_gate_violation(macro_state(), "日本衰退概率为4%，因此日本经济稳健。")


def test_probability_binding_keeps_multilingual_labels_and_enclosing_country():
    state = macro_state()
    # Match production Registry's numeric metadata as well as event text.
    state["evidence_registry"][-2].update(claim_type="FACT", derivation={"numeric_tokens": ["8"]})
    assert not probability_event_gate_violation(state, "| 2026年USリセッション | 8% | 低リスク |")
    text = "## 美国宏观经济状况\n\n| 指标 | 数值 |\n| --- | --- |\n| 衰退概率（年末） | **8%** |"
    assert enforce_agent_output(state, text, "News Analyst").text == text
    # A subsequent sibling country replaces scope; another section cannot
    # borrow country context from a previous top-level section.
    changed = text + "\n\n## 日本宏观\n| 衰退概率（年末） | 8% |\n\n## 公司新闻\n| 衰退概率（年末） | 8% |"
    checked = enforce_agent_output(state, changed, "News Analyst")
    assert checked.text.count("8%") == 1 and len(checked.findings) == 2


@pytest.mark.parametrize("action,rating,status", [
    ("Hold", "Hold", "OK"), (None, "Hold", "OK"),
    ("Buy", "Underweight", "OK"), ("Sell", None, "OK"),
    ("Sell", "Underweight", "DATA_UNAVAILABLE"),
])
def test_withheld_canonical_parameters_are_cleared_not_just_hidden(action, rating, status):
    proposed = {"status": status, **dict.fromkeys(EXECUTION_PLAN_FIELDS, 123)}
    rejected = reconcile_execution_authority(proposed, trader_action=action, portfolio_rating=rating)
    assert rejected["status"] != "OK"
    assert all(rejected.get(key) is None for key in EXECUTION_PLAN_FIELDS)
    assert proposed["entry"] == 123  # raw proposal is never mutated


@pytest.mark.parametrize("text", [
    "Recommendation: Hold\nEntry: 8500\nStop: 7200\nPosition: 3%",
    "Entry: 8500\nStop: 7200\nPosition: 3%",
    "Recommendation: Buy\nEntry: 8500\nPosition: 3%",
])
def test_calculator_unavailable_state_also_has_no_execution_numbers(text):
    result = validate_execution_plan(text)
    assert result["status"] != "OK" and all(result.get(key) is None for key in EXECUTION_PLAN_FIELDS)


def test_legal_sell_numbers_remain_authorized():
    value = validate_execution_plan("Recommendation: Sell\nEntry: 8513\nStop: 9500\nPosition: 3%")
    result = reconcile_execution_authority(value, trader_action="Sell", portfolio_rating="Underweight")
    assert result["status"] == "OK" and (result["entry"], result["stop"], result["position_pct"]) == (8513, 9500, 3)


@pytest.mark.parametrize("text,dimension", [
    ("建议继续持有该股。", "domain_authority_consistent"),
    *[(text, "domain_authority_consistent") for text in MACRO_CLAIMS],
    ("严格执行7,200日元止损", "execution_consistent"),
    ("关键数据门控：当前季度证据不足。", "presentation_valid"),
])
def test_exact_contract_blocks_after_upstream_cleanup_bypass(text, dimension):
    compose = f.compose_user_report_markdown
    with patch.object(f, "compose_user_report_markdown", side_effect=lambda state: compose(state) + "\n\n## 独立附录\n\n" + text):
        accepted = f.build_canonical_final_state(macro_state())
    contract = accepted["final_output_contract"]
    assert contract["status"] == "BLOCKED" and not contract["validation_dimensions"][dimension]


def test_exact_state_guard_rejects_reintroduced_withheld_parameters():
    accepted = f.build_canonical_final_state(state_with("公司经营正常。"))
    accepted["validated_execution"]["stop"] = 7200
    assert "EXECUTION_WITHHELD_PARAMETERS_PRESENT" in f._validate_final_artifact(
        accepted, False, accepted_report=accepted["accepted_report_markdown"])
    with pytest.raises(ValueError, match="execution authority"):
        f.require_canonical_final_state(accepted)
    rebuilt = f.build_canonical_final_state(accepted)
    assert rebuilt["validated_execution"]["stop"] is None


def test_final_builder_blocks_parameter_reintroduced_after_reconciliation():
    compose = f.compose_user_report_markdown

    def inject(state):
        state["validated_execution"]["position_pct"] = 3
        return compose(state)

    with patch.object(f, "compose_user_report_markdown", side_effect=inject):
        accepted = f.build_canonical_final_state(state_with("公司经营正常。"))
    contract = accepted["final_output_contract"]
    assert contract["status"] == "BLOCKED"
    assert not contract["validation_dimensions"]["execution_consistent"]
    assert "EXECUTION_WITHHELD_PARAMETERS_PRESENT" in contract["artifact_issues"]


def test_financial_warning_is_natural_text_not_internal_gate_label():
    original = "【关键数据门控：当前季度证据不足，暂不发布当前实绩判断。】"
    transformed = f._publicize_inline_text(original)
    assert "门控" not in transformed and "当前季度证据不足" in transformed
    assert "INTERNAL_GATE_LABEL_VISIBLE" in f.validate_final_report_text(original, execution_allowed=False)
