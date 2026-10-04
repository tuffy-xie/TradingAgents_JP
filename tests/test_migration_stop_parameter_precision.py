"""A stop parameter keeps its execution semantics across script/label layout."""
from unittest.mock import patch

import pytest

from tests.test_rating_authority import state_with
from tradingagents import final_output as f


@pytest.mark.parametrize("text", [
    "硬止損：¥6,500（技術性破壞區），觸發立即離場。",
    "止损价: 7100", "止損價：¥7,100", "止盈：9200",
    "| 分類 | 參數 | 數值 |\n|---|---|---|\n| 風險參數 | 止損 | 7100 |",
])
def test_unapproved_stop_parameter_is_detected_pruned_and_audited(text):
    assert f._execution_violation(text)
    assert not f._artifact_execution_claims(f._prune_unapproved_execution(text))
    original = state_with(text, "news_report")
    original["evidence_registry"] = [{
        "evidence_id": "E-local-price-fixture", "domain": "FUNDAMENTALS", "claim_type": "FACT",
        "source": "get_fundamentals", "verification_status": "VERIFIED_TOOL_OUTPUT",
        "allowed_for_current_decision": True,
        "value": "6500 7100 9200", "derivation": {"numeric_tokens": ["6500", "7100", "9200"]},
    }]
    raw_findings = f._collect_unapproved_execution_claims(original)
    claim = text.splitlines()[-1]
    assert any(e["category"] == "UNAPPROVED_EXECUTION_CLAIM" and e["original_claim"] == claim for e in raw_findings)
    accepted = f.build_canonical_final_state(original)
    assert text not in accepted["accepted_report_markdown"]
    # An unsupported price can be removed by numeric Evidence enforcement
    # before the execution gate. Either route must retain this exact identity.
    findings = [e for e in accepted["evidence_audit"] if e.get("original_claim") == claim]
    assert findings
    assert all(e["resolution"] != "UNRESOLVED" for e in findings)


def test_exact_artifact_blocks_stop_parameter_after_bypassed_pruning():
    compose = f.compose_user_report_markdown
    with patch.object(f, "compose_user_report_markdown", side_effect=lambda state: compose(state) + "\n\n硬止損：¥6,500，觸發立即離場。"):
        state = f.build_canonical_final_state(state_with("公司经营状况正常。", "news_report"))
    assert state["final_output_contract"]["status"] == "BLOCKED"
    assert not state["final_output_contract"]["validation_dimensions"]["execution_consistent"]


@pytest.mark.parametrize("text", [
    "不提供可執行止損參數。", "当前不授权新交易，也不提供止损价。",
    "公司通过分散生产基地对冲经营风险。", "股票回购计划执行中。",
    "6500是技术阻力位。", "若MACD跌破零轴则重新评估。",
])
def test_precision_fix_preserves_non_executable_research(text):
    assert not f._execution_violation(text)
