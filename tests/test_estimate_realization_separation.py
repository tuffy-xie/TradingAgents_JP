"""Analyst revisions cannot witness realized issuer performance."""
import copy
import hashlib
from unittest.mock import patch

import pytest

from tests.test_predicate_authority_closure import unavailable_actual
from tradingagents import final_output as f
from tradingagents.agents.evidence_enforcement import enforce_agent_output

ASSERTIONS = [
    "3 項盈利指標 30 天內上調，基本面改善仍在進行",
    "分析师盈利预期上调，公司基本面正在改善。",
    "市场盈利预测上调，公司的盈利已经改善。",
    "EPS 上方修正，基本面持续好转。",
    "Analyst earnings estimates revised higher, fundamentals are improving now.",
    "Earnings upward revisions confirm that profitability has improved.",
    "| 支持证据 | 结论 |\n|---|---|\n| EPS预期上调 | 基本面改善仍在进行 |",
    "分析师上调盈利预测，但基本面已经改善。",
    "盈利预测可能上调，但基本面改善仍在进行。",
    "Analysts raised earnings estimates but fundamentals are improving now.",
    "分析师预计盈利上调但基本面改善仍在进行。",
    "分析师盈利预期上调说明基本面改善仍在进行。",
]


@pytest.mark.parametrize("text", ASSERTIONS)
def test_upstream_rewrites_only_unavailable_actual_outcome(text):
    state = unavailable_actual()
    checked = enforce_agent_output(state, text, "Portfolio Manager")
    assert "financial_estimate_realization_mismatch" in checked.warnings
    assert "盈利预期变化不代表已实现业绩改善" in checked.text
    assert "financial_estimate_realization_mismatch" not in enforce_agent_output(state, checked.text, "Portfolio Manager").warnings


@pytest.mark.parametrize("text", ASSERTIONS)
def test_final_artifact_fault_injection_independently_blocks(text):
    compose = f.compose_user_report_markdown
    with patch.object(f, "compose_user_report_markdown", side_effect=lambda s:
                      compose(s) + "\n\n## 财务补充\n\n" + text):
        state = f.build_canonical_final_state(unavailable_actual())
    contract = state["final_output_contract"]
    assert contract["status"] == "BLOCKED"
    assert not contract["validation_dimensions"]["domain_authority_consistent"]
    assert any(x.startswith("CROSS_DOMAIN_AUTHORITY:FINANCIAL:") for x in contract["artifact_issues"])


@pytest.mark.parametrize("text", [
    "分析师盈利预期上调，盈利预期正在改善。",
    "3项盈利预期指标在30天内上调。",
    "盈利预测上调，公司基本面可能改善。",
    "若盈利预测上调，未来基本面可能改善。",
    "盈利预期上调，但不能确认基本面已经改善。",
    "盈利预期上调，但基本面是否改善仍待确认。",
    "盈利预期上调，2025年盈利已经改善。",
    "公司盈利能力具有结构性优势。",
    "盈利改善是看涨因素。",
    "基本面改善是值得监控的条件。",
    "盈利预期上调，基本面改善需要进行观察。",
    "EPS预期上调可能意味着基本面正在改善。",
    "Nomura expects improving earnings.",
    "公司成长潜力良好，行业地位领先。",
    "经营资源配置优化，股票回购计划执行中。",
    "空仓者禁止建仓。",
])
def test_forecasts_history_analysis_business_and_withholding_survive(text):
    checked = enforce_agent_output(unavailable_actual(), text, "Portfolio Manager")
    assert "financial_estimate_realization_mismatch" not in checked.warnings


def test_count_window_and_table_structure_are_preserved():
    text = ASSERTIONS[0]
    checked = enforce_agent_output(unavailable_actual(), text, "Portfolio Manager")
    assert "3 項盈利预期指标 30 天內上調" in checked.text
    table = enforce_agent_output(unavailable_actual(), ASSERTIONS[6], "Portfolio Manager").text
    assert "| EPS预期上调 |" in table and table.count("|") == ASSERTIONS[6].count("|")


def test_replacement_preserves_emphasis_delimiters():
    text = "盈利预期上调，**基本面改善仍在进行**。"
    checked = enforce_agent_output(unavailable_actual(), text, "Portfolio Manager")
    assert "**盈利预期变化不代表已实现业绩改善**" in checked.text
    assert "financial_estimate_realization_mismatch" in checked.warnings


def test_canonical_actual_and_us_are_not_downgraded():
    state = unavailable_actual()
    actual = state["japan_data_bundle"]["provider_metadata"]["Japan Financial Authority"]["actual"]
    actual.update(status="OK", critical_gate={"status": "OK"})
    assert "financial_estimate_realization_mismatch" not in enforce_agent_output(state, ASSERTIONS[0], "Portfolio Manager").warnings
    state = unavailable_actual()
    state["market_context"]["market"] = "US"
    assert enforce_agent_output(state, ASSERTIONS[0], "Portfolio Manager").text == ASSERTIONS[0]


@pytest.mark.parametrize("candidate", [ASSERTIONS[1], "基本面改善仍在进行。"])
def test_claim_sha_and_reworded_outcome_cannot_falsely_close(candidate):
    original = ASSERTIONS[0]
    state = unavailable_actual(original, "final_trade_decision")
    accepted = f.build_canonical_final_state(state)
    entries = [x for x in accepted["evidence_audit"] if x.get("warning") == "financial_estimate_realization_mismatch"]
    assert entries
    for e in entries:
        assert e["category"] == "SEMANTIC_MISMATCH"
        assert e["claim_sha256"] == hashlib.sha256(e["original_claim"].encode()).hexdigest()
        assert e["accepted_artifact_sha256"] == accepted["final_output_contract"]["accepted_report_sha256"]
        assert e["resolution"] == "CLAIM_REMOVED_OR_REPLACED"
    closed, issues = f._finalize_audit(copy.deepcopy(entries), state,
                                     accepted_report=candidate, execution_allowed=False)
    assert issues and all(e["resolution"] == "UNRESOLVED" for e in closed)


@pytest.mark.parametrize("text", [
    "基本面改善仍在进行。", "公司盈利正在改善。",
    "Fundamentals improvement is ongoing.",
])
def test_removing_revision_premise_does_not_waive_current_actual_gate(text):
    state = unavailable_actual()
    assert "critical_gate_bypassed" in enforce_agent_output(state, text, "Portfolio Manager").warnings
    assert any(x.warning == "critical_gate_bypassed" for x in f._artifact_evidence_gate_findings(state, text))
