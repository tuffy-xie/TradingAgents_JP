"""Own investment stance differs from withholding; cleanup is not report prose."""
import hashlib
from unittest.mock import patch

import pytest

from tests.test_rating_authority import state_with
from tradingagents import final_output as f
from tradingagents.agents.evidence_enforcement import enforce_agent_output
from tradingagents.rating_authority import internal_rating_claims, remove_internal_ratings
from tradingagents.reporting import write_report_tree
from web.server import _render_report_html

STANCES = [
    "基本面强到不支持卖出，估值和宏观又强到不支持加仓。",
    "基本面强→不支持卖出（否决Sell）",
    "估值偏高，因此不支持加仓。",
    "反对追涨/加仓",
    "We therefore reject buy.",
    "综合评分：中性偏多（HOLD）",
    "## 研究结论\n\n在此之前，以不变应万变。",
    "维持现状是最佳策略。",
    "*本建议基于辩论中提供的数据及双方分析推断。*",
]
SURFACES = [
    "#### 示例公司多空辩论评估与投资建议",
    "## Evidence assessment and investment recommendations",
    "**1. 仓位管理**",
    "## 持仓策略",
]
WITHHELD_AND_FACTS = [
    "当前不支持卖出。", "不支持加仓。", "不建议执行。", "当前不授权新交易。",
    "空仓者禁止建仓。", "不追高。", "无法支持实时入场参数。",
    "公司具有良好的成长潜力。", "估值偏高。", "盈利预期改善。",
    "公司基本面较强。", "公司经营策略维持现状。", "公司强到不支持卖出非核心资产。",
    "历史上基本面强到不支持卖出。",
    "## 股票回购计划执行中\n\n回购计划持续推进。",
    "公司生产网络可对冲地缘集中风险。", "经营资源配置优化。",
    "券商认为估值偏高，因此不支持加仓。", "Nomura maintains Buy rating.",
    "分析师共识评级：买入。", "若未来订单落地，则重新评估。",
]


@pytest.mark.parametrize("text", STANCES + SURFACES)
def test_non_portfolio_stance_and_advisory_surfaces_have_claim_identity(text):
    assert internal_rating_claims(text)
    assert not internal_rating_claims(remove_internal_ratings(text))
    state = state_with(text + "\n\n公司经营稳定。", "investment_debate_state.judge_decision")
    state["evidence_registry"].append({"claim_type": "FACT", "value": "1",
                                       "allowed_for_current_decision": True})
    result = f.build_canonical_final_state(state)
    assert result["final_output_contract"]["status"] == "FINALIZED"
    findings = [e for e in result["evidence_audit"] if e["category"] == "SECONDARY_INTERNAL_RATING"
                and e["agent"] == "Research Manager"]
    assert findings
    for finding in findings:
        assert finding["agent"] == "Research Manager"
        assert finding["authority_owner"] == "Portfolio Manager"
        assert hashlib.sha256(finding["original_claim"].encode()).hexdigest() == finding["claim_sha256"]
        assert finding["resolution"] == "CLAIM_REMOVED_OR_REPLACED"
        assert finding["accepted_artifact_sha256"] == result["final_output_contract"]["accepted_report_sha256"]
    assert "公司经营稳定" in result["accepted_report_markdown"]


@pytest.mark.parametrize("text", WITHHELD_AND_FACTS)
def test_stance_boundary_preserves_bare_withholding_external_and_business(text):
    assert not internal_rating_claims(text)
    assert remove_internal_ratings(text) == text


@pytest.mark.parametrize("text", [
    "情形B目标8,500。", "情景C目标9,100日元。", "Scenario C target 9100.",
    "| 情景 | 价格预期 |\n| --- | --- |\n| 情景C | 目标9100 |",
])
def test_scenario_target_without_price_suffix_is_not_authorized_by_hold(text):
    assert f._execution_violation(text)
    state = state_with("公司经营稳定。")
    # The numeric catalog knows these values; it does not authorize a plan.
    state["evidence_registry"].append({"claim_type": "FACT", "value": "8500 9100",
                                       "allowed_for_current_decision": True})
    state["final_trade_decision"] = "**评级**: Hold\n\n" + text
    result = f.build_canonical_final_state(state)
    assert result["validated_execution"]["status"] != "OK"
    assert all(result["validated_execution"].get(k) is None for k in ("entry", "stop", "target", "position_pct"))
    assert not f._artifact_execution_claims(result["accepted_report_markdown"])
    assert result["final_output_contract"]["status"] == "FINALIZED"
    findings = [e for e in result["evidence_audit"] if e["category"] == "UNAPPROVED_EXECUTION_CLAIM"
                and ("目标" in e.get("original_claim", "") or "target" in e.get("original_claim", ""))]
    assert findings and all(e["resolution"] != "UNRESOLVED" for e in findings)


@pytest.mark.parametrize("text", [
    "券商给出情景B目标8,500。", "Analyst scenario C target 9100.",
    "Nomura maintains Buy rating, target 9100.",
    "不提供情景B目标8500。", "历史上情形B目标8500。",
    "公司收入目标8500万元。", "公司订单目标1000台。",
    "情景B收入目标8500万元。", "情景B订单目标1000台。",
    "| 外部来源 | 估值情景 |\n| --- | --- |\n| Nomura | 情景B目标8500 |",
])
def test_external_targets_business_goals_and_withheld_target_remain(text):
    assert not f._execution_violation(text)


PROCESS = [
    "【证据约束：社区情绪与分析师预期不可表述为已验证官方事实】",
    "【来源约束：不同来源不可混用】",
    "本项概率推导未能与来源事件逐项对应；已移除该推导，不影响其他独立绑定的来源概率。",
    "该项断言已删除。",
    "> 经营分析。本项概率推导未能与来源事件逐项对应；已移除该推导。",
]


@pytest.mark.parametrize("text", PROCESS)
def test_internal_cleanup_has_presentation_audit_and_is_not_user_prose(text):
    assert f._generation_process_claims(text)
    assert "PROCESS_PROSE_VISIBLE" in f.validate_final_report_text(text, execution_allowed=False, check_structure=False)
    result = f.build_canonical_final_state(state_with(text + "\n\n公司经营稳定。"))
    assert not f._generation_process_claims(result["accepted_report_markdown"])
    findings = [e for e in result["evidence_audit"] if e.get("semantic_type") == "PROCESS_NARRATION"]
    assert findings and all(e["resolution"] != "UNRESOLVED" for e in findings)
    assert result["final_output_contract"]["status"] == "FINALIZED"


def test_legacy_label_preserves_uncertainty_and_independent_neighbor():
    text = "【证据约束：社区情绪属于参考信息】本项概率推导未能与来源事件逐项对应；已移除该推导。\n- 独立事件概率为4%。"
    cleaned = f._publicize_inline_text(text)
    assert "社区情绪属于参考信息" in cleaned
    assert "- 独立事件概率为4%。" in cleaned
    assert not f._generation_process_claims(cleaned)


@pytest.mark.parametrize("text", [
    "该宏观情景尚无法确认。", "当前没有获准的交易计划。",
    "公司公告称回购计划已完成。", "公司披露已删除一个过时产品。",
    '> 管理层表示：“I will now prepare the annual report.”',
])
def test_natural_uncertainty_and_management_speech_are_not_cleanup(text):
    assert not f._generation_process_claims(text)


@pytest.mark.parametrize("text,dimension", [
    *[(t, "domain_authority_consistent") for t in STANCES + SURFACES],
    ("情形B目标8,500。", "execution_consistent"),
    ("## 仓位管理\n\n公司经营稳定。", "execution_consistent"),
    *[(t, "presentation_valid") for t in PROCESS],
])
def test_exact_candidate_independently_blocks_bypassed_cleanup(text, dimension):
    compose = f.compose_user_report_markdown
    with patch.object(f, "compose_user_report_markdown", side_effect=lambda s: compose(s) + "\n\n## 研究结论\n\n" + text):
        result = f.build_canonical_final_state(state_with("公司经营稳定。"))
    contract = result["final_output_contract"]
    assert contract["status"] == "BLOCKED"
    assert not contract["validation_dimensions"][dimension]


def test_current_canonical_sell_report_and_us_tables_are_unchanged(tmp_path):
    state = state_with("公司经营稳定。")
    state["final_trade_decision"] = "**评级**: Underweight"
    state["trader_investment_plan"] = "Recommendation: Sell\nEntry: 8513\nStop: 9500\nPosition: 3%"
    state["validated_execution"] = {"status": "OK"}
    result = f.build_canonical_final_state(state)
    assert result["final_output_contract"]["status"] == "FINALIZED"
    assert (result["validated_execution"]["entry"], result["validated_execution"]["stop"],
            result["validated_execution"]["position_pct"]) == (8513, 9500, 3)
    assert not f._artifact_execution_claims(f._artifact_without_validated_execution(result, result["accepted_report_markdown"]))
    write_report_tree(result, "TEST.T", tmp_path / "jp")
    html = _render_report_html(result, auto_print=False)
    assert not f.validate_rendered_html(html)
    us = {"market_report": "Buy\nEntry: 10\nStop: 9\nTarget: 12\n\n| Observation |\n| --- |\n| MACD weakening |"}
    write_report_tree(us, "TEST", tmp_path / "us")
    assert "MACD weakening" in (tmp_path / "us" / "complete_report.md").read_text()


def test_new_probability_notice_does_not_describe_internal_cleanup():
    from tests.test_independent_review_closure import macro_state
    original = "日本2026年软着陆概率极高。"
    checked = enforce_agent_output(macro_state(), original, "News Analyst")
    assert checked.findings and not f._generation_process_claims(checked.text)
    assert "已移除" not in checked.text and "来源事件逐项对应" not in checked.text


def test_relabel_does_not_resurrect_a_previously_withheld_execution_subtree():
    text = "## 综合交易建议\n\n综合评分：HOLD\n\n## 风险分析\n\n公司经营稳定。"
    result = f.build_canonical_final_state(state_with(text))
    assert "综合评分" not in result["accepted_report_markdown"]
    assert "公司经营稳定" in result["accepted_report_markdown"]
    assert result["final_output_contract"]["status"] == "FINALIZED"


def test_sentence_pruning_repairs_single_emphasis_and_keeps_ordinary_italic():
    text = "*本建议基于辩论。公司经营稳定。*"
    result = f.build_canonical_final_state(state_with(text, "investment_debate_state.judge_decision"))
    report = result["accepted_report_markdown"]
    assert "*公司经营稳定。*" in report and "本建议" not in report
    assert f._clean_empty_markdown("*公司经营稳定。*") == "*公司经营稳定。*"
    assert f._clean_empty_markdown("- 生产效率 * 成本") == "- 生产效率 * 成本"
    assert f._clean_empty_markdown("收益率稳健*") == "收益率稳健*"  # a US-style footnote marker


def test_external_target_column_does_not_exempt_an_added_portfolio_order():
    text = "| 外部来源 | 目标 | 操作 |\n| --- | --- | --- |\n| Nomura | 情景B目标8500 | 若回调则加仓 |"
    assert f._execution_violation(text)
    assert f._execution_violation("| 外部来源 | 参数 | 值 | 参数 | 值 |\n| --- | --- | --- | --- | --- |\n| Nomura | Entry | 8500 | Target | 9100 |")
