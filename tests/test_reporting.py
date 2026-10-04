"""Report parity: the shared writer produces the report tree for the CLI and the
programmatic API alike (#1037)."""

from types import SimpleNamespace

import pytest

from tradingagents.final_output import build_canonical_final_state
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.reporting import write_report_tree
from web.server import _render_report_html


def _state():
    return {
        "market_report": "MKT",
        "news_report": "NEWS",
        "investment_debate_state": {"bull_history": "BULL"},
        "investment_plan": "RM PLAN",
        "trader_investment_plan": "TRADE",
        "risk_debate_state": {"neutral_history": "NEUTRAL"},
        "final_trade_decision": "PM DECISION",
    }


SETTINGS = {"version": "0.5.2", "llm_provider": "openai", "deep_think_llm": "gpt-6-sol",
            "quick_think_llm": "gpt-6-luna", "analysts": ["market", "news"], "max_debate_rounds": 1,
            "max_risk_discuss_rounds": 2, "data_vendors": {"core_stock_apis": "yfinance"}}


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

    report = write_report_tree(
        build_canonical_final_state(state), "6981.T", tmp_path
    )
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

    report = write_report_tree(
        build_canonical_final_state(state), "6981.T", tmp_path
    )
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
    graph = SimpleNamespace(run_settings=lambda: SETTINGS)
    out = TradingAgentsGraph.save_reports(graph, _state(), "AAPL", save_path=tmp_path)
    assert (tmp_path / "complete_report.md").exists()
    assert out == tmp_path / "complete_report.md"


@pytest.mark.unit
def test_save_reports_defaults_under_results_dir(tmp_path):
    graph = object.__new__(TradingAgentsGraph)
    graph.config = {"results_dir": str(tmp_path)}
    graph.run_settings = lambda: SETTINGS
    out = graph.save_reports(_state(), "AAPL")
    assert out.exists()
    assert out.parent.parent.name == "reports"  # results_dir/reports/AAPL_<stamp>/...
    assert out.parent.name.startswith("AAPL_")



@pytest.mark.unit
def test_the_report_names_the_analysis_date_and_what_produced_it(tmp_path):
    state = dict(_state(), trade_date="2026-09-23")

    header = write_report_tree(state, "NVDA", tmp_path, settings=SETTINGS).read_text().split("## ")[0]

    assert "Analysis date: 2026-09-23" in header
    assert "TradingAgents 0.5.2" in header
    assert "openai, deep gpt-6-sol, quick gpt-6-luna" in header
    assert "Analysts: market, news" in header
    assert "research debate rounds 1, risk debate rounds 2" in header
    assert "core_stock_apis yfinance" in header


@pytest.mark.unit
def test_run_settings_record_the_run_without_endpoints_or_paths():
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = object.__new__(TradingAgentsGraph)
    graph.selected_analysts = ("market", "news")
    graph.config = {"llm_provider": "openai", "deep_think_llm": "gpt-6-sol", "quick_think_llm": "gpt-6-luna",
                    "max_debate_rounds": 1, "max_risk_discuss_rounds": 1, "output_language": "English",
                    "data_vendors": {"core_stock_apis": "yfinance"}, "tool_vendors": {},
                    "backend_url": "https://user:secret@relay.example/v1", "results_dir": "/home/me/results"}

    settings = graph.run_settings()

    assert settings["analysts"] == ["market", "news"]
    assert settings["deep_think_llm"] == "gpt-6-sol"
    assert "secret" not in str(settings) and "/home/me" not in str(settings)


@pytest.mark.unit
def test_a_partial_settings_dict_still_writes_the_report(tmp_path):
    out = write_report_tree(_state(), "AAPL", tmp_path, settings={"llm_provider": "openai"})
    assert "openai" in out.read_text()


@pytest.mark.unit
def test_run_settings_name_the_version_of_the_running_code():
    import tradingagents
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = object.__new__(TradingAgentsGraph)
    graph.selected_analysts, graph.config = ("market",), {}
    assert graph.run_settings()["version"] == tradingagents.__version__
