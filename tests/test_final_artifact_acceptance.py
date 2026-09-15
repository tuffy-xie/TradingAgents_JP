from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_final_output_contract import _jp_state
from tradingagents.agents.utils.evidence_enforcement import enforce_agent_output
from tradingagents.agents.utils.execution_validation import validate_execution_plan
from tradingagents.final_output import (
    _finalize_audit,
    build_canonical_final_state,
    normalize_markdown_structure,
)
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.report_artifacts import render_markdown_fragment, validate_rendered_html
from tradingagents.reporting import write_report_tree
from web.server import _render_report_html


def _sentiment_item() -> dict:
    return {
        "source": "Japan Investor Sentiment",
        "source_type": "japan_investor_sentiment_aggregate",
        "timestamp": "2026-09-13T14:59:59+00:00",
        "status": "OK",
        "metadata": {
            "sample_count": 14,
            "positive_count": 5,
            "neutral_count": 7,
            "negative_count": 2,
            "sentiment_score": 0.171,
            "confidence": 0.7,
            "latest_post_date": "2026-09-13",
            "time_window": {
                "1D": {
                    "status": "OK",
                    "sample_count": 4,
                    "positive_count": 0,
                    "neutral_count": 3,
                    "negative_count": 1,
                    "sentiment_score": -0.2431,
                },
                "7D": {
                    "status": "OK",
                    "sample_count": 14,
                    "positive_count": 5,
                    "neutral_count": 7,
                    "negative_count": 2,
                    "sentiment_score": 0.171,
                },
            },
        },
    }


def test_final_sentiment_has_one_source_native_authority():
    state = _jp_state()
    state["japan_data_bundle"]["items"] = [_sentiment_item()]
    state["sentiment_report"] = (
        "## LLM aggregate\n"
        "| field | value |\n|---|---|\n| overall_band | Neutral |\n"
        "9/13 positive=2 and neutral=2."
    )

    accepted = build_canonical_final_state(state)
    report = accepted["accepted_report_markdown"]

    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert report.count("综合方向：温和偏多") == 1
    assert "overall_band" not in report
    assert "Neutral" not in report
    assert "9/13 positive=2" not in report
    assert "| 1D | 4 | 0 | 3 | 1 | -0.2431 |" in report


def test_audit_resolution_requires_the_same_published_field_to_be_clean():
    state = _jp_state()
    state["news_report"] = "Unsupported exact value 999999."
    audit = [{
        "category": "UNSUPPORTED_CLAIM",
        "agent": "News Analyst",
        "field": "news_report",
        "warning": "unsupported_precise_number",
    }]

    finalized, issues = _finalize_audit(audit, state)

    assert finalized[0]["resolution"] == "UNRESOLVED"
    assert finalized[0]["execution_blocking"] is True
    assert len(finalized[0]["published_field_sha256"]) == 64
    assert issues == ["UNRESOLVED_EVIDENCE:news_report:unsupported_precise_number"]


def test_audit_finding_for_nonpublished_debate_is_not_falsely_called_validated():
    state = _jp_state()
    audit = [{
        "category": "UNSUPPORTED_CLAIM",
        "agent": "Bull Researcher",
        "field": "investment_debate_state.bull_history",
        "warning": "unsupported_precise_number",
    }]

    finalized, issues = _finalize_audit(audit, state)

    assert finalized[0]["resolution"] == "NOT_PUBLISHED_IN_FINAL_ARTIFACT"
    assert finalized[0]["resolution_basis"] == "FIELD_EXCLUDED_FROM_ACCEPTED_REPORT"
    assert issues == []


def test_markdown_table_is_terminated_before_following_report_content():
    markdown = normalize_markdown_structure(
        "## Summary\n| field | value |\n|---|---|\n| sample | 14 |\n"
        "###### Conclusion\nBody.\n---\n## Next\nText."
    )
    rendered = render_markdown_fragment(markdown)

    assert "| sample | 14 |\n\n###### Conclusion" in markdown
    assert validate_rendered_html(rendered) == []
    assert "<td>###### Conclusion</td>" not in rendered
    assert "<h6>Conclusion</h6>" in rendered


def test_decimal_child_headings_follow_their_surviving_parent_number():
    markdown = normalize_markdown_structure(
        "##### 一、概览\n正文。\n##### 二、财务\n"
        "###### 4.1 资产负债表\n正文。\n###### 4.3 现金流\n正文。"
    )

    assert "###### 2.1 资产负债表" in markdown
    assert "###### 2.2 现金流" in markdown


def test_structural_heading_ordinals_are_not_treated_as_evidence_values():
    state = _jp_state()
    checked = enforce_agent_output(
        state,
        "### 3.1 资产负债表关键数据\n",
        "Fundamentals Analyst",
    )

    assert checked.text == "### 3.1 资产负债表关键数据\n"
    assert checked.warnings == ()


def test_web_renderer_uses_accepted_artifact_and_disables_browser_file_footer():
    accepted = build_canonical_final_state(_jp_state())
    accepted["final_trade_decision"] = "Rating: Buy\nRAW BYPASS 999999"

    html = _render_report_html(accepted, auto_print=False)

    assert "RAW BYPASS" not in html
    assert "@page { size:auto; margin:0; }" in html
    assert validate_rendered_html(html) == []


def test_rendered_html_rejects_local_file_url_and_swallowed_markdown():
    html = "<table><tr><th>x</th></tr><tr><td>###### leaked</td></tr></table><a href='file:///tmp/x'>x</a>"
    assert validate_rendered_html(html) == [
        "LOCAL_FILE_URL_VISIBLE",
        "RAW_MARKDOWN_VISIBLE_AFTER_RENDER",
        "MARKDOWN_SWALLOWED_BY_TABLE",
    ]


@pytest.mark.parametrize("action", ["Hold", "持有"])
def test_hold_is_nonexecuting_even_when_model_supplies_prices(action):
    validation = validate_execution_plan(
        f"**Action**: {action}\n**Entry Price**: 7200\n**Stop Loss**: 6800\n"
        "**Maximum Position**: 3%"
    )

    assert validation["status"] == "DATA_UNAVAILABLE"
    assert validation["detail"] == "HOLD_DOES_NOT_AUTHORIZE_NEW_EXECUTION"
    assert validation["action"] == "Hold"


def test_hold_gate_removes_conditional_entry_and_exposure_prose():
    state = _jp_state()
    state["final_trade_decision"] = (
        "**Rating**: Hold\n\n"
        "**Investment Thesis**: 维持观察并降低事件前敞口。\n\n"
        "**Entry Condition**: 当前不入场，事件落地后满足条件再建仓。"
    )
    state["risk_debate_state"]["judge_decision"] = state["final_trade_decision"]
    state["trader_investment_plan"] = (
        "**Action**: Hold\n**Entry Price**: 7200\n**Stop Loss**: 6800\n"
        "**Maximum Position**: 3%"
    )
    state["validated_execution"] = validate_execution_plan(
        state["trader_investment_plan"]
    )

    accepted = build_canonical_final_state(state)
    report = accepted["accepted_report_markdown"]

    assert accepted["final_output_contract"]["execution_allowed"] is False
    assert "Entry Condition" not in report
    assert "降低事件前敞口" not in report
    assert "再建仓" not in report
    assert "没有获准的入场、止损、目标价或仓位计划" in report


def test_exact_accepted_artifact_and_digest_are_persisted(tmp_path: Path):
    state = _jp_state()
    state["run_manifest"] = {"run_id": "test-run"}
    state["investment_debate_state"].update(
        {"bull_history": "", "bear_history": "", "history": "", "current_response": ""}
    )
    state["risk_debate_state"].update(
        {
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "history": "",
        }
    )
    accepted = build_canonical_final_state(state)
    graph = SimpleNamespace(
        log_states_dict={},
        ticker="6981.T",
        config={"results_dir": str(tmp_path)},
    )

    TradingAgentsGraph._log_state(graph, "2026-09-02", accepted)
    path = tmp_path / "6981.T" / "TradingAgentsStrategy_logs" / "full_states_log_2026-09-02.json"
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert persisted["accepted_report_markdown"] == accepted["accepted_report_markdown"]
    assert persisted["final_output_contract"]["accepted_report_sha256"] == accepted[
        "final_output_contract"
    ]["accepted_report_sha256"]
    assert persisted["trader_investment_plan"] == accepted["trader_investment_plan"]
    report = write_report_tree(persisted, "6981.T", tmp_path / "report")
    assert report.read_text(encoding="utf-8").rstrip() == accepted["accepted_report_markdown"]


def test_tampered_accepted_artifact_fails_digest_gate():
    accepted = build_canonical_final_state(_jp_state())
    accepted["accepted_report_markdown"] += "\nchanged"

    with pytest.raises(ValueError, match="canonical digest"):
        write_report_tree(accepted, "6981.T", Path("/tmp/unused-report"))
