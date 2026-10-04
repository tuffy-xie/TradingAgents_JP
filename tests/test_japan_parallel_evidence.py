"""Offline proof of the upstream private-analyst join and JP authority capture."""
from typing import Annotated

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import InjectedState

from tradingagents.agents.state import AgentState, merge_evidence_records
from tradingagents.graph.analyst_execution import AnalystNodeSpec, build_analyst_execution_plan
from tradingagents.graph.propagation import Propagator
from tradingagents.graph.setup import _analyst_graph, _observe_agent_node


def test_market_registration_keeps_formal_tools_and_diagnostic_boundary():
    spec = build_analyst_execution_plan(["market"]).specs[0]
    assert {"get_stock_data", "get_indicators"} <= {t.name for t in spec.tools}
    diagnostic = next(t for t in spec.tools if t.name == "get_verified_market_snapshot")
    assert "diagnostic only" in diagnostic.description


def test_parallel_private_tools_reach_registry_without_cross_branch_messages():
    seen = {}

    @tool
    def get_stock_data(symbol: Annotated[str, InjectedState("company_of_interest")]) -> str:
        """Local stock fixture only."""
        return f"local complete payload for {symbol}"

    @tool
    def get_news(symbol: Annotated[str, InjectedState("company_of_interest")]) -> str:
        """Local news fixture only."""
        return f"local news payload for {symbol}"

    parent = StateGraph(AgentState)
    for key, tool_fn in (("market", get_stock_data), ("news", get_news)):
        field = f"{key}_report"
        name = f"{key.title()} Analyst"
        spec = AnalystNodeSpec(key, name, field, (tool_fn,))

        def node(state, *, key=key, field=field, tool_fn=tool_fn):
            seen.setdefault(key, []).append(list(state["messages"]))
            if not any(isinstance(m, ToolMessage) for m in state["messages"]):
                return {"messages": [AIMessage(content="", tool_calls=[{
                    "name": tool_fn.name, "args": {}, "id": f"{key}-tool",
                }])]}
            return {field: "合法研究事实。", "messages": [AIMessage(content="合法研究事实。")]}

        observed = _observe_agent_node(name, node, provider="offline", timeout=1, retries=0)
        parent.add_node(key, _analyst_graph(spec, observed, 2))
        parent.add_edge(START, key)
        parent.add_edge(key, END)

    initial = Propagator().create_initial_state("6981.T", "2026-09-28")
    state = parent.compile().invoke(initial)
    assert state["market_report"] == state["news_report"] == "合法研究事实。"
    sources = {e["source"] for e in state["evidence_registry"] if e["source_type"] == "TOOL_OUTPUT"}
    assert sources == {"get_stock_data", "get_news"}
    assert {f["agent"] for f in state["evidence_audit"]} >= {"Market Analyst", "News Analyst"}
    for key, histories in seen.items():
        tool_names = {m.name for history in histories for m in history if isinstance(m, ToolMessage)}
        assert tool_names == {"get_stock_data" if key == "market" else "get_news"}
    assert not any(isinstance(m, ToolMessage) for m in state["messages"])


def test_parallel_findings_dedupe_exact_identity_not_field():
    left = [{"category": "UNSUPPORTED_CLAIM", "field": "news_report", "claim_sha256": "a"}]
    right = [*left, {"category": "UNSUPPORTED_CLAIM", "field": "news_report", "claim_sha256": "b"}]
    assert len(merge_evidence_records(left, right)) == 2
