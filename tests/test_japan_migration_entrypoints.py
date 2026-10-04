"""Offline compatibility proof for upstream state, defaults and Chinese Web."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from tradingagents.default_config import build_default_config
from tradingagents.final_output import build_canonical_final_state
from tradingagents.graph.propagation import Propagator
from web import server


def test_japan_default_sources_and_bounded_timeout_survive_migration():
    config = build_default_config()
    assert config["llm_timeout_seconds"] == 180.0
    jp = config["markets"]["jp"]
    assert jp["tdnet_max_pages_per_day"] == 50
    assert jp["datasources"]["company_ir"] and jp["datasources"]["jsf"]
    assert jp["sentiment"]["yahoo_board"]
    assert config["data_vendors"]["fundamental_data"] == "sec_edgar,yfinance"
    assert "trading_horizon" not in config


def test_upstream_manager_output_enters_existing_japan_acceptance():
    original = {
        "company_of_interest": "6981.T", "trade_date": "2026-10-01",
        "market_context": {"market": "JP", "symbol": "6981.T", "currency": "JPY"},
        "investment_debate_state": {}, "risk_debate_state": {},
        "investment_plan": "订单落地后需要重新评估。最终研究结论: BUY",
        "final_trade_decision": "评级：Hold", "trader_investment_plan": "建议：Hold",
    }
    accepted = build_canonical_final_state(original)
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert "订单落地后需要重新评估" in accepted["accepted_report_markdown"]
    assert "最终研究结论: BUY" not in accepted["accepted_report_markdown"]
    assert not accepted["final_output_contract"]["execution_allowed"]
    assert any(x.get("category") == "SECONDARY_INTERNAL_RATING" for x in accepted["evidence_audit"])


@pytest.mark.parametrize("market,blocked", [("JP", False), ("JP", True), ("US", False)])
def test_web_stream_uses_production_interfaces_and_publication_gate(monkeypatch, market, blocked):
    calls = []
    ticker = "6981.T" if market == "JP" else "AAPL"
    context = SimpleNamespace(symbol=ticker, market=market)
    monkeypatch.setattr(server, "resolve_market_context", lambda _: context)
    monkeypatch.setattr(server, "resolve_instrument_identity", lambda _: {})
    monkeypatch.setattr(server, "enrich_market_context", lambda value, _: value)

    class FakeGraph:
        def __init__(self, selected_analysts, config):
            self.config = config
            self.propagator = Propagator()
            calls.append(("construct", selected_analysts, config))

        def create_run_state(self, symbol, date, **kwargs):
            calls.append(("initialize", symbol, date, kwargs))
            return self.propagator.create_initial_state(symbol, date, market_context={"market": market, "symbol": symbol})

        def begin_checkpoint(self, *args):
            calls.append(("begin", args))
            return "isolated-thread"

        def checkpoint_input(self, state):
            calls.append(("checkpoint_input", state))
            return state

        def stream_run(self, initial, **args):
            calls.append(("stream", initial, args))
            yield [], {"market_report": "PROVISIONAL RAW BUY", "investment_plan": "PROVISIONAL RAW RESEARCH"}
            yield [], initial | {"market_report": "PROVISIONAL RAW BUY", "investment_plan": "PROVISIONAL RAW RESEARCH",
                                 "final_trade_decision": "PROVISIONAL RAW DECISION"}

        def record_decision(self, *args):
            calls.append(("accept", args))
            state = args[-1] | {"market_report": "ACCEPTED MARKET", "investment_plan": "ACCEPTED RESEARCH",
                               "final_trade_decision": "ACCEPTED DECISION"}
            if market == "JP":
                # The real acceptance mapping is exercised above and in saved-state
                # replays. Here the production publication result is controlled.
                state["investment_debate_state"] = {"judge_decision": "ACCEPTED RESEARCH"}
                state["final_output_contract"] = {"status": "BLOCKED" if blocked else "FINALIZED"}
            return state

        def _log_state(self, *args):
            calls.append(("log", args))

        def save_reports(self, *args):
            calls.append(("publish", args))
            return None

        def clear_checkpoint_on_success(self, *args):
            calls.append(("clear", args))

        def end_checkpoint(self):
            calls.append(("end",))

    monkeypatch.setattr(server, "TradingAgentsGraph", FakeGraph)

    async def collect():
        response = await server.analyze(ticker, "2026-10-01", "minimax", "MiniMax-M3", "MiniMax-M2.7-highspeed")
        return [json.loads(event["data"]) async for event in response.body_iterator]

    events = asyncio.run(collect())
    assert sum(c[0] == "stream" for c in calls) == 1
    stream = next(c for c in calls if c[0] == "stream")
    assert stream[-1]["stream_mode"] == "values"
    assert stream[-1]["config"]["configurable"]["thread_id"] == "isolated-thread"
    assert "PROVISIONAL RAW" not in json.dumps(events)
    assert any(e.get("id") == "Research Manager" and e.get("status") == "completed" for e in events)
    if blocked:
        assert events[-1]["type"] == "error"
        assert not any(e["type"] in {"section", "final", "done"} for e in events)
        assert not any(c[0] in {"publish", "clear"} for c in calls)
    else:
        assert events[-1]["type"] == "done"
        assert any(e.get("content") == "ACCEPTED RESEARCH" for e in events)
        assert any(c[0] == "publish" for c in calls)
    assert calls[-1][0] == "end"


def test_cli_never_persists_provisional_japan_report(monkeypatch, tmp_path):
    from tests import test_cli_memory_log as helper

    class Buffer(helper._FakeBuffer):
        def update_report_section(self, section, content):
            self.report_sections[section] = content

    class Graph(helper._FakeGraph):
        def create_run_state(self, ticker, date, *args):
            return Propagator().create_initial_state(ticker, date, market_context={"market": "JP", "symbol": ticker})

        def stream_run(self, initial, **kwargs):
            yield [], initial | {"market_report": "PROVISIONAL RAW BUY", "investment_plan": "最终研究结论: BUY"}
            yield [], initial | {"market_report": "PROVISIONAL RAW BUY", "investment_plan": "最终研究结论: BUY",
                                 "final_trade_decision": "评级：Hold", "trader_investment_plan": "建议：Hold"}

        def record_decision(self, ticker, date, state):
            assert not list((tmp_path / "results").rglob("reports/*.md"))
            return build_canonical_final_state(state)

    monkeypatch.setattr(helper, "_FakeBuffer", Buffer)
    helper._run_cli(monkeypatch, tmp_path, Graph())
    files = list((tmp_path / "results").rglob("reports/*.md"))
    assert files
    assert all("PROVISIONAL RAW" not in file.read_text() for file in files)
    assert all("最终研究结论: BUY" not in file.read_text() for file in files)
