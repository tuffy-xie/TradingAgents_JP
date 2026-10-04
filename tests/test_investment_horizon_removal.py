from __future__ import annotations

import inspect
from pathlib import Path

from tradingagents.agents.context import get_trade_constraints_from_state
from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.trader.trader import create_trader
from tradingagents.dataflows.market import resolve_market_context
from tradingagents.final_output import build_canonical_final_state
from tradingagents.graph.propagation import Propagator
from web.server import _render_report_html, analyze

ROOT = Path(__file__).resolve().parents[1]


def _legacy_japan_state() -> dict:
    state = Propagator().create_initial_state(
        "6981.T",
        "2026-09-26",
        market_context=resolve_market_context("6981.T"),
        trade_constraints={"horizon": "multi_day"},
        japan_data_bundle={
            "analysis_date": "2026-09-26",
            "trading_horizon": "multi_day",
            "items": [],
            "source_statuses": [],
        },
    )
    state.update(
        {
            "market_report": "行情证据不足。",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "investment_debate_state": {
                **state["investment_debate_state"],
                "judge_decision": (
                    "用户2-10日窗口属于短线区间，因此维持减配。"
                    "用户交易指令是2-10个交易日。独立证据仍支持谨慎判断。"
                ),
            },
            "trader_investment_plan": "Action: Hold",
            "risk_debate_state": {
                **state["risk_debate_state"],
                "judge_decision": "Rating: Hold\n维持观望。",
            },
            "final_trade_decision": "Rating: Hold\n维持观望。",
            "validated_execution": {
                "status": "DATA_UNAVAILABLE",
                "detail": "HOLD_DOES_NOT_AUTHORIZE_NEW_EXECUTION",
            },
        }
    )
    return state


def test_initial_state_has_no_default_user_investment_horizon():
    state = Propagator().create_initial_state("NVDA", "2026-09-26")

    assert state["trade_constraints"] == {}
    assert get_trade_constraints_from_state(state) == ""


def test_legacy_horizon_is_ignored_but_explicit_execution_constraint_remains():
    rendered = get_trade_constraints_from_state(
        {
            "trade_constraints": {
                "horizon": "multi_day",
                "stop_loss_condition": "跌破已验证支撑位",
            }
        }
    )

    assert "horizon" not in rendered.casefold()
    assert "2–10" not in rendered
    assert "跌破已验证支撑位" in rendered


def test_web_api_and_ui_no_longer_offer_custom_horizon():
    assert "trading_horizon" not in inspect.signature(analyze).parameters
    frontend = (ROOT / "web/frontend/src/App.vue").read_text(encoding="utf-8")

    for retired in ("tradingHorizon", "trading_horizon", "2–10 个交易日", "2–12 周"):
        assert retired not in frontend


def test_decision_prompts_do_not_request_a_user_horizon():
    prompt_sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "tradingagents/agents/managers/research_manager.py",
            "tradingagents/agents/managers/portfolio_manager.py",
            "tradingagents/agents/trader/trader.py",
            "tradingagents/agents/researchers/bull_researcher.py",
            "tradingagents/agents/researchers/bear_researcher.py",
            "tradingagents/agents/risk_mgmt/aggressive_debator.py",
            "tradingagents/agents/risk_mgmt/conservative_debator.py",
            "tradingagents/agents/risk_mgmt/neutral_debator.py",
        )
    )

    assert "requested horizon" not in prompt_sources.casefold()
    assert "requested holding horizon" not in prompt_sources.casefold()
    assert "2–10" not in prompt_sources
    # The upstream optional Portfolio recommendation remains supported; only
    # user-selected horizon injection is retired.
    assert "time_horizon" in (ROOT / "tradingagents/agents/schemas.py").read_text(encoding="utf-8")
    assert callable(create_trader)
    assert callable(create_portfolio_manager)


def test_legacy_state_replay_removes_user_horizon_metadata_and_prose():
    accepted = build_canonical_final_state(_legacy_japan_state())
    report = accepted["accepted_report_markdown"]

    assert "horizon" not in accepted["trade_constraints"]
    assert "trading_horizon" not in accepted["japan_data_bundle"]
    assert "用户2-10日窗口" not in report
    assert "用户交易指令是2-10个交易日" not in report
    assert "独立证据仍支持谨慎判断" in report
    assert accepted["final_output_contract"]["status"] == "FINALIZED"


def test_cover_hides_missing_target_and_horizon_instead_of_showing_dash_cards():
    accepted = build_canonical_final_state(_legacy_japan_state())
    html = _render_report_html(accepted, auto_print=False)

    assert "<small>目标价</small>" not in html
    assert "<small>投资期限</small>" not in html
    assert "<small>投资评级</small>" in html
    assert "<small>分析师覆盖</small>" in html


def test_cover_keeps_explicit_original_portfolio_recommendations():
    html = _render_report_html(
        {
            "company_of_interest": "NVDA",
            "trade_date": "2026-09-26",
            "market_context": {
                "market": "US",
                "symbol": "NVDA",
                "currency": "USD",
                "instrument_type": "EQUITY",
            },
            "final_trade_decision": (
                "**Rating**: Buy\n"
                "**Price Target**: 215\n"
                "**Time Horizon**: 3-6 months\n"
                "**Executive Summary**: Evidence-supported recommendation."
            ),
        },
        auto_print=False,
    )

    assert "<small>目标价</small><strong>215</strong>" in html
    assert "<small>投资期限</small><strong>3-6 months</strong>" in html
