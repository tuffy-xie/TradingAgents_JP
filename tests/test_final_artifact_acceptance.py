from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_final_output_contract import _jp_state
from tradingagents.agents.utils.evidence_enforcement import enforce_agent_output
from tradingagents.agents.utils.execution_validation import (
    parse_execution_action,
    validate_execution_plan,
)
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

    finalized, issues = _finalize_audit(
        audit,
        state,
        accepted_report=state["news_report"],
        execution_allowed=False,
    )

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

    finalized, issues = _finalize_audit(
        audit,
        state,
        accepted_report="",
        execution_allowed=False,
    )

    assert finalized[0]["resolution"] == "NOT_PUBLISHED_IN_FINAL_ARTIFACT"
    assert finalized[0]["resolution_basis"] == "FIELD_EXCLUDED_FROM_ACCEPTED_REPORT"
    assert issues == []


def test_claim_level_audit_does_not_close_when_original_claim_survives():
    state = _jp_state()
    original = "Unsupported exact value 999999."
    state["news_report"] = original + "\nA sibling claim was removed."
    audit = [{
        "category": "UNSUPPORTED_CLAIM",
        "agent": "News Analyst",
        "field": "news_report",
        "warning": "unsupported_precise_number",
        "claim_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "original_claim": original,
        "replacement_claim": "",
        "enforcement_action": "REMOVED",
    }]

    finalized, issues = _finalize_audit(
        audit,
        state,
        accepted_report=state["news_report"],
        execution_allowed=False,
    )

    assert finalized[0]["resolution"] == "UNRESOLVED"
    assert issues == ["UNRESOLVED_EVIDENCE:news_report:unsupported_precise_number"]


def test_stale_market_tool_window_cannot_publish_current_technical_prose():
    state = _jp_state()
    state["run_manifest"] = {
        "analysis_as_of": "2026-09-16",
        "runtime_timestamp_jst": "2026-09-16T00:59:00+09:00",
    }
    state["market_report"] = "已取得完整OHLCV，当前技术面偏多。"
    state["evidence_registry"] = [{
        "domain": "MARKET",
        "source": "get_stock_data",
        "source_type": "TOOL_OUTPUT",
        "value": (
            "Date,Open,High,Low,Close,Volume\n"
            "2026-09-14,100,103,99,102,1000\n"
            "2026-09-15,,,,,1200\n"
        ),
        "verification_status": "VERIFIED_TOOL_OUTPUT",
        "allowed_for_current_decision": True,
    }]

    accepted = build_canonical_final_state(state)

    # A legacy, partial display value cannot establish a lossless latest date.
    assert "最近完整 OHLCV 日期为 未获完整证据核验" in accepted["market_report"]
    assert "预期最近已完成交易日为 2026-09-15" in accepted["market_report"]
    assert "当前技术面偏多" not in accepted["accepted_report_markdown"]


def test_cross_domain_sentiment_authority_is_removed_from_market_report():
    state = _jp_state()
    state["japan_data_bundle"]["items"] = [_sentiment_item()]
    state["market_report"] = "价格仍承压。市场情绪 3/10，偏空。"

    accepted = build_canonical_final_state(state)
    report = accepted["accepted_report_markdown"]

    assert "价格仍承压" in report
    assert "市场情绪 3/10" not in report
    assert report.count("综合方向：温和偏多") == 1
    assert accepted["final_output_contract"]["status"] == "FINALIZED"


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


def test_structural_normalizer_drops_semantic_empty_rows_and_renumbers_lists():
    markdown = normalize_markdown_structure(
        "## Data\n| kind | metric | value |\n|---|---|---|\n"
        "| Valuation | | |\n| Profit | EPS | 10 |\n\n"
        "1. First\n   - detail\n\n1. Second\n"
    )

    assert "| Valuation | | |" not in markdown
    assert "1. First" in markdown
    assert "2. Second" in markdown


def test_process_narration_and_standard_english_labels_are_not_user_visible():
    state = _jp_state()
    state["news_report"] = (
        "已完成资料核验。现在让我基于这些数据为您提供详细的分析报告。\n"
        "**Recommendation**: Hold\n**Investment Thesis**: 证据仍不足。\n"
        "**Strategic Actions**: ## 后续观察计划"
    )

    report = build_canonical_final_state(state)["accepted_report_markdown"]

    assert "现在让我" not in report
    assert "Recommendation" not in report
    assert "Investment Thesis" not in report
    # Localization cannot publish News' own second investment rating.
    assert "**研究建议**: Hold" not in report
    assert "**投资逻辑**: 证据仍不足" in report
    assert "**策略说明**: 后续观察计划" in report
    assert ": ##" not in report


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


@pytest.mark.parametrize(
    "text",
    [
        "Hold",
        "HOLD",
        "Hold（观望）",
        "建议：Hold",
        "建议：HOLD（持有）",
        "**建议**: **Hold（观望）**",
        "| **建议** | **Hold（观望）** | 等待更清晰信号 |",
    ],
)
def test_free_text_trader_hold_variants_are_nonexecuting(text):
    plan = (
        text
        if "：" in text or ":" in text or text.startswith("|")
        else f"Action: {text}"
    )
    plan += "\nEntry: 7300\nStop: 6800\nPosition: 3%"

    validation = validate_execution_plan(plan)

    assert parse_execution_action(plan) == "Hold"
    assert validation["status"] == "DATA_UNAVAILABLE"
    assert validation["detail"] == "HOLD_DOES_NOT_AUTHORIZE_NEW_EXECUTION"


def test_numeric_plan_without_explicit_action_fails_closed():
    validation = validate_execution_plan(
        "Entry: 7300\nStop: 6800\nPosition: 3%"
    )

    assert validation["status"] == "DATA_UNAVAILABLE"
    assert validation["detail"] == "ACTION_UNAVAILABLE"
    assert validation["action"] is None


@pytest.mark.parametrize("action", ["Buy", "Sell"])
def test_explicit_buy_and_sell_still_authorize_complete_numeric_plan(action):
    validation = validate_execution_plan(
        f"Action: {action}\nEntry: 7300\nStop: 6800\nPosition: 3%"
    )

    assert validation["status"] == "OK"
    assert validation["action"] == action


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


@pytest.mark.parametrize(
    "field,prose",
    [
        ("market_report", "确认后应积极减持至目标水平。"),
        ("news_report", "事件落地后可以逢低买入。"),
        ("fundamentals_report", "估值回落后建议长期逢低布局。"),
    ],
)
def test_hold_gate_applies_to_every_user_facing_analyst_section(field, prose):
    state = _jp_state()
    state[field] = "方向性研究仍保留。" + prose
    state["validated_execution"] = validate_execution_plan(
        "**Action**: Hold\n**Entry Price**: 7200\n**Stop Loss**: 6800\n"
        "**Maximum Position**: 3%"
    )

    accepted = build_canonical_final_state(state)
    report = accepted["accepted_report_markdown"]

    assert accepted["final_output_contract"]["execution_allowed"] is False
    assert prose not in report
    assert "方向性研究仍保留" in report
    assert accepted["final_output_contract"]["status"] == "FINALIZED"


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
    published = report.read_bytes()
    assert published == accepted["accepted_report_markdown"].encode("utf-8")
    assert hashlib.sha256(published).hexdigest() == accepted[
        "final_output_contract"
    ]["accepted_report_sha256"]


def test_tampered_accepted_artifact_fails_digest_gate():
    accepted = build_canonical_final_state(_jp_state())
    accepted["accepted_report_markdown"] += "\nchanged"

    with pytest.raises(ValueError, match="canonical digest"):
        write_report_tree(accepted, "6981.T", Path("/tmp/unused-report"))
