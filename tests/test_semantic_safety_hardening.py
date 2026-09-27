"""Publication safety must survive layout, polarity and presentation changes."""
import hashlib
from unittest.mock import patch

import pytest

from tests.test_final_output_contract import _jp_state
from tradingagents import final_output as output
from tradingagents.reporting import write_report_tree
from web.server import _render_report_html


@pytest.mark.parametrize("table", [
    "| 条件 | 操作 |\n|---|---|\n| CapEx下调 | 减仓 |",
    "| Condition | Response |\n|---|---|\n| If guidance is cut | Sell |",
    "| Event | Action |\n|---|---|\n| Earnings miss | Buy |",
    "| 事件 | 操作 |\n|---|---|\n| 需求改善 | →加仓 |",
])
def test_cross_cell_plan_has_audit_and_final_defense(table):
    assert output._artifact_execution_claims(table)
    state = _jp_state() | {"news_report": table}
    accepted = output.build_canonical_final_state(state)
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert not output._artifact_execution_claims(accepted["accepted_report_markdown"])
    findings = [x for x in accepted["evidence_audit"]
                if x.get("category") == "UNAPPROVED_EXECUTION_CLAIM" and x.get("field") == "news_report"]
    assert findings
    assert all(x["claim_sha256"] == hashlib.sha256(x["original_claim"].encode()).hexdigest() for x in findings)
    # The final boundary must not trust upstream pruning or finding collection.
    with patch.object(output, "_withhold_unvalidated_execution", side_effect=lambda s: s), \
         patch.object(output, "_collect_unapproved_execution_claims", return_value=[]):
        blocked = output.build_canonical_final_state(state)
    assert blocked["final_output_contract"]["status"] == "BLOCKED"
    assert any("FINAL_ARTIFACT_UNAUTHORIZED_EXECUTION" in x for x in blocked["final_output_contract"]["artifact_issues"])


@pytest.mark.parametrize("text", [
    "若回调，不建议建仓。", "If prices fall, do not buy.",
    "历史上公司若资金不足就卖出资产。", "若资金不足，公司可能出售非核心资产。",
    "Historically, the company would sell assets if cash was scarce.",
    "If cash is scarce, the company may sell non-core assets.",
    "暂不加仓。", "不追高。", "当前没有获准交易计划。", "CapEx下调属于负面风险。",
    "| 风险条件 | 影响 |\n|---|---|\n| CapEx下调 | 盈利压力增加 |",
    "| Condition | Response |\n|---|---|\n| If prices fall | Do not buy |",
    "| Subject | Action | Object |\n|---|---|---|\n| Company | Sell | assets |",
    "| Condition | Impact |\n|---|---|\n| If guidance is cut | Sell-side estimates fall |",
])
def test_prohibition_and_descriptive_subject_do_not_authorize(text):
    assert output._execution_violation(text) is None
    assert not output._artifact_execution_claims(text)


@pytest.mark.parametrize("text", [
    "若跌破关键位，则减仓。", "若回调可逢低买入。", "If guidance is cut, sell.",
    "条件满足后加仓至5%。", "公司出售资产后，建议买入股票。",
    "If the company sells assets, buy shares.",
    "若回调，不建议建仓，但确认后可以买入。",
    "Do not buy, hold for 10 days.",
    "不建议追高且可以买入回调。",
    "Do not chase but buy the dip.",
])
def test_authorization_is_not_hidden_by_condition_or_other_subject(text):
    assert output._execution_violation(text)


@pytest.mark.parametrize("original,published", [
    ("| CapEx下调 | get_news | →减仓 |", "| CapEx下调 | 上游数据源 | →减仓 |"),
    ("(3) 若回调可买入（get_news）。", "(1) 若回调可买入。"),
    ("**If guidance is cut, sell.**", "If guidance is cut, sell."),
    ("| If guidance is cut | get_news | Sell |", "<table><tr><th>Condition</th><th>Action</th></tr><tr><td>If guidance is cut</td><td>Sell</td></tr></table>"),
])
def test_audit_cannot_close_presentation_transformed_action(original, published):
    finding = {"category": "UNAPPROVED_EXECUTION_CLAIM", "field": "news_report",
               "original_claim": original, "claim_sha256": hashlib.sha256(original.encode()).hexdigest()}
    audit, issues = output._finalize_audit([finding], _jp_state(), accepted_report=published, execution_allowed=False)
    assert issues
    assert audit[0]["resolution"] == "UNRESOLVED"
    closed, issues = output._finalize_audit([finding], _jp_state(), accepted_report="维持观望。", execution_allowed=False)
    assert not issues
    assert closed[0]["resolution"] == "CLAIM_REMOVED_OR_REPLACED"


@pytest.mark.parametrize("row", ["| MACD weakening |", "| Guidance | Not provided |", "| EPS | N/A |",
                                     "| Coverage | unavailable |", "| Outlook | Improving |",
                                     "| MACD weakening | — |", "| Demand is recovering | |"])
def test_information_bearing_rows_survive(row):
    width = len(row.strip("|").split("|"))
    table = "| " + " | ".join(["Observation"] * width) + " |\n|" + "---|" * width + "\n" + row
    assert row in output.normalize_markdown_structure(table)


def test_placeholder_period_row_removed():
    assert output._is_semantically_empty_table_row(["FY2022", "—", "—", "—"])
    assert output._is_semantically_empty_table_row(["**FY2022**", "—", "—", "—"])


@pytest.mark.parametrize("publisher", ["guard", "writer", "html", "cli"])
@pytest.mark.parametrize("revision", [None, "obsolete"])
def test_stale_revision_fails_at_direct_publication(publisher, revision, tmp_path):
    state = output.build_canonical_final_state(_jp_state())
    state["final_output_contract"]["semantic_revision"] = revision
    with pytest.raises(ValueError, match="revision"):
        if publisher == "guard":
            output.require_canonical_final_state(state)
        elif publisher == "writer":
            write_report_tree(state, "TEST.T", tmp_path)
        elif publisher == "html":
            _render_report_html(state, auto_print=False)
        else:
            from cli.main import display_complete_report
            display_complete_report(state)
    rebuilt = output.build_canonical_final_state(state)
    output.require_canonical_final_state(rebuilt)
    assert output.build_canonical_final_state(rebuilt) == rebuilt


def test_us_report_keeps_observations_status_and_execution(tmp_path):
    state = {"market_context": {"market": "US"},
             "market_report": "## Observations\n| Observation |\n|---|\n| MACD weakening |",
             "fundamentals_report": "| Metric | Status |\n|---|---|\n| Guidance | Not provided |",
             "risk_debate_state": {"judge_decision": "Rating: Buy\nEntry: 100\nStop: 90\nTarget: 120\nTime Horizon: 3 months"}}
    report = write_report_tree(state, "AAPL", tmp_path).read_text()
    for text in ("MACD weakening", "Not provided", "Entry: 100", "Stop: 90", "Target: 120", "3 months"):
        assert text in report


def test_final_defense_uses_rendered_table_when_markdown_segmentation_misses():
    table = "| Condition | Response |\n|---|---|\n| If guidance is cut | Sell |"
    with patch.object(output, "_execution_lines", return_value=[]):
        assert output._artifact_execution_claims(table)


def test_archive_controller_reaccepts_before_publishing(monkeypatch):
    from web import server
    state = output.build_canonical_final_state(_jp_state())
    state["final_output_contract"]["semantic_revision"] = "obsolete"
    state["raw_agent_outputs"]["news_report"] = (
        "| Condition | Response |\n|---|---|\n| If guidance is cut | Sell |"
    )
    monkeypatch.setattr(server, "_load_run", lambda *_: state)
    response = server.get_report("TEST.T", "2026-09-02", print=False)
    assert response.status_code == 200
    assert "<td>Sell</td>" not in response.body.decode()


def test_partial_execution_field_never_closes_after_only_one_action_removed():
    original = "若需求降低则减仓；若回调可买入。"
    entry = {"category": "UNAPPROVED_EXECUTION_CLAIM", "original_claim": original,
             "field": "news_report", "claim_sha256": hashlib.sha256(original.encode()).hexdigest()}
    audit, issues = output._finalize_audit([entry], _jp_state(),
                                         accepted_report="若回调可买入。", execution_allowed=False)
    assert issues
    assert audit[0]["resolution"] == "UNRESOLVED"
