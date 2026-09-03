"""Report parity: the shared writer produces the report tree for the CLI and the
programmatic API alike (#1037)."""

from types import SimpleNamespace

import pytest

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.reporting import write_report_tree
from web.server import _render_report_html


def _state():
    return {
        "market_report": "MKT",
        "news_report": "NEWS",
        "investment_debate_state": {"judge_decision": "RM PLAN"},
        "trader_investment_plan": "TRADE",
        "risk_debate_state": {"judge_decision": "PM DECISION"},
    }


@pytest.mark.unit
def test_write_report_tree_creates_files(tmp_path):
    out = write_report_tree(_state(), "AAPL", tmp_path)
    assert out.name == "complete_report.md"
    assert (tmp_path / "1_analysts" / "market.md").read_text() == "MKT"
    assert (tmp_path / "1_analysts" / "news.md").read_text() == "NEWS"
    assert (tmp_path / "2_research" / "manager.md").read_text() == "RM PLAN"
    assert (tmp_path / "3_trading" / "trader.md").read_text() == "TRADE"
    assert (tmp_path / "5_portfolio" / "decision.md").read_text() == "PM DECISION"
    complete = out.read_text()
    assert "Trading Analysis Report: AAPL" in complete
    assert "MKT" in complete and "PM DECISION" in complete


@pytest.mark.unit
def test_full_debate_is_kept_out_of_default_report_and_in_debug_log(tmp_path):
    state = _state() | {
        "investment_debate_state": {
            "bull_history": "BULL RAW ARGUMENT",
            "bear_history": "BEAR RAW ARGUMENT",
            "judge_decision": "RM PLAN",
        },
        "risk_debate_state": {
            "aggressive_history": "AGGRESSIVE RAW ARGUMENT",
            "conservative_history": "CONSERVATIVE RAW ARGUMENT",
            "neutral_history": "NEUTRAL RAW ARGUMENT",
            "judge_decision": "PM DECISION",
        },
    }
    report = write_report_tree(state, "AAPL", tmp_path)
    default_report = report.read_text(encoding="utf-8")
    agent_log = (tmp_path / "full_agent_log.md").read_text(encoding="utf-8")

    assert "BULL RAW ARGUMENT" not in default_report
    assert "BEAR RAW ARGUMENT" not in default_report
    assert "AGGRESSIVE RAW ARGUMENT" not in default_report
    assert "RM PLAN" in default_report and "PM DECISION" in default_report
    assert "BULL RAW ARGUMENT" in agent_log
    assert "BEAR RAW ARGUMENT" in agent_log
    assert "AGGRESSIVE RAW ARGUMENT" in agent_log
    assert "NEUTRAL RAW ARGUMENT" in agent_log


@pytest.mark.unit
def test_japan_user_report_localizes_internal_statuses_but_debug_log_keeps_them(tmp_path):
    state = _state() | {
        "market_context": {
            "market": "JP",
            "symbol": "6981.T",
            "currency": "JPY",
            "instrument_type": "EQUITY",
        },
        "fundamentals_report": (
            "Status: INSUFFICIENT_DATA; freshness=FRESHNESS_UNVERIFIED; "
            "value=DATA_UNAVAILABLE; provenance=VERIFIED_FINANCIAL_AUTHORITY"
        ),
    }

    report = write_report_tree(state, "6981.T", tmp_path)
    user_text = report.read_text(encoding="utf-8")
    debug_text = (tmp_path / "full_agent_log.md").read_text(encoding="utf-8")

    for internal in (
        "INSUFFICIENT_DATA",
        "FRESHNESS_UNVERIFIED",
        "DATA_UNAVAILABLE",
        "VERIFIED_FINANCIAL_AUTHORITY",
    ):
        assert internal not in user_text
    assert "证据不足" in user_text
    assert "新鲜度未确认" in user_text
    assert "INSUFFICIENT_DATA" in debug_text


@pytest.mark.unit
def test_japan_user_report_hides_internal_financial_replacement_artifact(tmp_path):
    artifact = "Current Company Guidance net_income: 338,000 百万円。"
    state = _state() | {
        "market_context": {
            "market": "JP",
            "symbol": "6981.T",
            "currency": "JPY",
            "instrument_type": "EQUITY",
        },
        "fundamentals_report": artifact + "\n正常基本面叙述。",
    }

    report = write_report_tree(state, "6981.T", tmp_path)
    user_text = report.read_text(encoding="utf-8")
    debug_text = (tmp_path / "full_agent_log.md").read_text(encoding="utf-8")

    assert artifact not in user_text
    assert "正常基本面叙述" in user_text
    assert artifact in debug_text


@pytest.mark.unit
def test_web_report_excludes_raw_debate_and_risk_transcripts():
    html = _render_report_html(
        {
            "company_of_interest": "AAPL",
            "trade_date": "2026-08-14",
            "market_report": "市场摘要",
            "investment_debate_state": {"bull_history": "BULL RAW ARGUMENT"},
            "risk_debate_state": {"aggressive_history": "AGGRESSIVE RAW ARGUMENT"},
        },
        auto_print=False,
    )
    assert "市场摘要" in html
    assert "BULL RAW ARGUMENT" not in html
    assert "AGGRESSIVE RAW ARGUMENT" not in html


@pytest.mark.unit
def test_save_reports_explicit_path(tmp_path):
    # Unbound: with an explicit save_path, the method doesn't touch self/config.
    out = TradingAgentsGraph.save_reports(None, _state(), "AAPL", save_path=tmp_path)
    assert (tmp_path / "complete_report.md").exists()
    assert out == tmp_path / "complete_report.md"


@pytest.mark.unit
def test_save_reports_defaults_under_results_dir(tmp_path):
    mock_self = SimpleNamespace(config={"results_dir": str(tmp_path)})
    out = TradingAgentsGraph.save_reports(mock_self, _state(), "AAPL")
    assert out.exists()
    assert out.parent.parent.name == "reports"  # results_dir/reports/AAPL_<stamp>/...
    assert out.parent.name.startswith("AAPL_")
