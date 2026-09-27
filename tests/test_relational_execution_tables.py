"""Trading responses inherit row context, not a fixed spelling of the header."""
import hashlib
from unittest.mock import patch

import pytest

from tests.test_final_output_contract import _jp_state
from tradingagents import final_output as output
from tradingagents.reporting import write_report_tree
from web.server import _render_report_html

PLANS = [
    ("条件", "操作", "CapEx下调", "减仓"),
    ("风险条件", "操作建议", "CapEx下调", "减仓"),
    ("Trigger", "Response", "Guidance cut", "Sell"),
    ("Trigger", "Recommended action", "Guidance cut", "Sell"),
    ("触发因素", "应对策略", "跌破关键位", "降低仓位"),
    ("Scenario", "Portfolio response", "Earnings miss", "Reduce exposure"),
]
OBSERVATIONS = [
    ("风险条件", "影响", "CapEx下调", "盈利压力增加"),
    ("Trigger", "Impact", "Guidance cut", "Lower visibility"),
    ("指标", "评价", "MACD", "动能转弱"),
    ("Event", "Company action", "Liquidity shortage", "Sell non-core assets"),
    ("Trigger", "Recommended action", "Guidance cut", "Do not sell"),
    ("触发因素", "应对策略", "股价回调", "暂不建仓"),
]


def table(cells, *, modifier="", reverse=False, separator="---"):
    left, right, condition, response = cells
    headers, values = [left, modifier + right], [condition, response]
    if reverse:
        headers.reverse()
        values.reverse()
    return ("| " + " | ".join(headers) + " |\n"
            + f"|{separator}|{separator}|\n| " + " | ".join(values) + " |")


def state_with_news(text):
    # No unrelated entry/stop fixtures: only the injected row can block the test.
    state = _jp_state() | {"news_report": text, "trader_investment_plan": "Recommendation: Hold",
                           "final_trade_decision": "评级：Hold。维持观望。"}
    state["risk_debate_state"]["judge_decision"] = state["final_trade_decision"]
    state["investment_debate_state"]["judge_decision"] = "风险较高。"
    return state


@pytest.mark.parametrize("cells", PLANS)
@pytest.mark.parametrize("reverse", [False, True])
def test_plan_detected_pruned_and_audited(cells, reverse):
    text = table(cells, reverse=reverse)
    assert output._execution_violation(text)
    accepted = output.build_canonical_final_state(state_with_news(text))
    contract = accepted["final_output_contract"]
    assert contract["execution_allowed"] is False
    assert contract["status"] == "FINALIZED"
    assert not output._artifact_execution_claims(accepted["accepted_report_markdown"])
    findings = [x for x in accepted["evidence_audit"] if x.get("category") == "UNAPPROVED_EXECUTION_CLAIM"
                and x.get("field") == "news_report"]
    assert findings
    for finding in findings:
        assert finding["claim_sha256"] == hashlib.sha256(finding["original_claim"].encode()).hexdigest()
        assert finding["resolution"] == "CLAIM_REMOVED_OR_REPLACED"
        assert finding["original_claim"] not in accepted["accepted_report_markdown"]
        surviving, issues = output._finalize_audit([finding], accepted, accepted_report=text, execution_allowed=False)
        assert surviving[0]["resolution"] == "UNRESOLVED"
        assert surviving[0]["execution_blocking"]
        assert issues


@pytest.mark.parametrize("cells", PLANS)
def test_exact_artifact_blocks_without_upstream_findings_or_pruning(cells):
    text = table(cells)
    with patch.object(output, "_collect_unapproved_execution_claims", return_value=[]), \
         patch.object(output, "_prune_unapproved_execution", side_effect=lambda text: text):
        accepted = output.build_canonical_final_state(state_with_news(text))
    contract = accepted["final_output_contract"]
    assert text in accepted["accepted_report_markdown"]
    assert contract["execution_allowed"] is False
    assert contract["status"] == "BLOCKED"
    assert contract["validation_dimensions"]["execution_consistent"] is False
    assert any(i.startswith("FINAL_ARTIFACT_UNAUTHORIZED_EXECUTION:") for i in contract["artifact_issues"])
    with pytest.raises(ValueError):
        _render_report_html(accepted, auto_print=False)
    # The rendered row defense must also work without Markdown segmentation.
    with patch.object(output, "_execution_lines", return_value=[]):
        assert output._artifact_execution_claims(text)


@pytest.mark.parametrize("cells", OBSERVATIONS)
def test_risk_company_action_and_prohibition_are_preserved(cells, tmp_path):
    text = table(cells)
    assert output._execution_violation(text) is None
    assert not output._artifact_execution_claims(text)
    accepted = output.build_canonical_final_state(state_with_news(text))
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    report = write_report_tree(accepted, "TEST.T", tmp_path).read_text()
    assert cells[3] in report


@pytest.mark.parametrize("modifier", ["Suggested portfolio ", "Proposed immediate ", "**Recommended** "])
def test_header_roles_compose_with_unseen_modifiers(modifier):
    assert output._execution_violation(table(("Scenario", "response", "Weak demand", "Sell"), modifier=modifier))


@pytest.mark.parametrize("header", ["Recommended action / impact", "风险影响及应对策略"])
def test_mixed_role_column_does_not_hide_action_or_invent_one(header):
    assert output._execution_violation(table(("Scenario", header, "Weak demand", "Sell")))
    assert not output._execution_violation(table(("Scenario", header, "Weak demand", "盈利压力增加")))


def test_short_markdown_separator_is_detected_before_structural_normalization():
    text = table(PLANS[0], separator="--")
    assert output._execution_violation(text)
    findings = output._collect_unapproved_execution_claims(state_with_news(text))
    assert any(x.get("field") == "news_report" for x in findings)
