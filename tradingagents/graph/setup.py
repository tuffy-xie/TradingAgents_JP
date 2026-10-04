# TradingAgents/graph/setup.py

import logging
import time
from collections import Counter
from functools import wraps
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from tradingagents.agents import (
    create_aggressive_debator,
    create_bear_researcher,
    create_bull_researcher,
    create_conservative_debator,
    create_fundamentals_analyst,
    create_market_analyst,
    create_neutral_debator,
    create_news_analyst,
    create_portfolio_manager,
    create_research_manager,
    create_sentiment_analyst,
    create_trader,
)
from tradingagents.agents.analysts.turn import WRAP_UP
from tradingagents.agents.evidence_enforcement import audit_agent_result
from tradingagents.agents.evidence_registry import capture_agent_evidence
from tradingagents.agents.execution_validation import (
    enforce_execution_math,
    validate_execution_plan,
)
from tradingagents.agents.state import AgentState

from .analyst_execution import build_analyst_execution_plan
from .conditional_logic import ConditionalLogic

logger = logging.getLogger(__name__)


def _classify_agent_error(error: Exception) -> str:
    """Return a stable, non-secret error class for operational logs."""
    name = type(error).__name__.lower()
    message = str(error).lower()
    if "timeout" in name or "timeout" in message:
        return "TIMEOUT"
    if "rate" in name or "429" in message:
        return "RATE_LIMITED"
    if "auth" in name or "401" in message or "403" in message:
        return "AUTH_ERROR"
    if "connect" in name or "connection" in message:
        return "CONNECTION_ERROR"
    return "AGENT_ERROR"


def _observe_agent_node(name: str, node: Any, *, provider: str, timeout: Any, retries: Any):
    """Log wall-clock timing and finite-failure classification per graph node.

    The node is deliberately not swallowed: a failed critical agent produces a
    clear, bounded run failure instead of a plausible-looking fabricated report.
    Successful graph semantics are unchanged.
    """
    @wraps(node)
    def observed(state, *args, **kwargs):
        ticker = state.get("company_of_interest", "unknown") if isinstance(state, dict) else "unknown"
        start = time.perf_counter()
        logger.info(
            "[Agent] start name=%s ticker=%s provider=%s timeout_seconds=%s retry_budget=%s",
            name, ticker, provider, timeout if timeout not in (None, "") else "provider-default",
            retries if retries not in (None, "") else "provider-default",
        )
        try:
            result = node(state, *args, **kwargs)
        except Exception as exc:
            logger.error(
                "[Agent] failed name=%s ticker=%s elapsed_seconds=%.2f class=%s error=%s",
                name, ticker, time.perf_counter() - start, _classify_agent_error(exc), type(exc).__name__,
            )
            raise
        if isinstance(result, dict):
            # Tool facts exist before audit. Agent reasoning remains intact for
            # downstream debate and the technical log; only the canonical
            # final-state boundary may alter user-visible content.
            result = capture_agent_evidence(
                state, result, name, capture_tools=True, capture_reports=False
            )
            combined_state = {**state, **result} if isinstance(state, dict) else result
            result = audit_agent_result(combined_state, result, name)
            result = _append_authoritative_financial_context(state, result, name)
            result = _apply_execution_authority(state, result, name)
            result = capture_agent_evidence(
                {**state, **result} if isinstance(state, dict) else result,
                result,
                name,
                capture_tools=False,
                capture_reports=True,
            )
        logger.info(
            "[Agent] complete name=%s ticker=%s elapsed_seconds=%.2f",
            name, ticker, time.perf_counter() - start,
        )
        return result
    return observed


def _append_authoritative_financial_context(
    state: dict[str, Any], result: dict[str, Any], agent_name: str
) -> dict[str, Any]:
    """Keep normalized JP financial facts exact in the Fundamentals handoff.

    The LLM report may summarize or round values.  Appending the existing
    deterministic renderer after evidence enforcement guarantees downstream
    agents also receive the exact gated values, statuses, units, and
    provenance.  No other analyst or US path receives this section.
    """
    if agent_name != "Fundamentals Analyst":
        return result
    if (state.get("market_context") or {}).get("market") != "JP":
        return result
    report = result.get("fundamentals_report")
    if not isinstance(report, str) or not report.strip():
        return result
    from tradingagents.dataflows.japan.context import render_japan_financial_context

    authority = render_japan_financial_context(state)
    if not authority or authority in report:
        return result
    updated = dict(result)
    updated["fundamentals_report"] = report.rstrip() + "\n\n---\n\n" + authority
    return updated


def _apply_execution_authority(
    state: dict[str, Any], result: dict[str, Any], agent_name: str
) -> dict[str, Any]:
    """Calculate and audit execution math without rewriting Agent prose.

    The canonical final-state builder is the only layer allowed to expose or
    withhold an executable plan. This node-time step only produces validation
    metadata needed by later agents and that final decision.
    """
    if (state.get("market_context") or {}).get("market") != "JP":
        return result
    updated = dict(result)
    audit = list(updated.get("evidence_audit") or state.get("evidence_audit") or [])
    if agent_name == "Trader" and isinstance(updated.get("trader_investment_plan"), str):
        validation = validate_execution_plan(updated["trader_investment_plan"])
        updated["validated_execution"] = validation
        _, warnings = enforce_execution_math(updated["trader_investment_plan"], validation)
        if warnings:
            audit.append({"category": "ARITHMETIC_MISMATCH", "agent": agent_name, "warnings": list(warnings)})
    else:
        validation = state.get("validated_execution") or {}
        for key in ("final_trade_decision",):
            if isinstance(updated.get(key), str):
                _, warnings = enforce_execution_math(updated[key], validation)
                if warnings:
                    audit.append({"category": "ARITHMETIC_MISMATCH", "agent": agent_name, "field": key, "warnings": list(warnings)})
        debate = updated.get("risk_debate_state")
        if isinstance(debate, dict):
            cleaned_debate = dict(debate)
            for key, value in debate.items():
                if not isinstance(value, str) or not (
                    key.startswith("current_") or key == "judge_decision"
                ):
                    continue
                _, warnings = enforce_execution_math(value, validation)
                if warnings:
                    audit.append({"category": "ARITHMETIC_MISMATCH", "agent": agent_name, "field": f"risk_debate_state.{key}", "warnings": list(warnings)})
            updated["risk_debate_state"] = cleaned_debate
    updated["evidence_audit"] = audit
    return updated

# Every target a shared conditional router can return. Each edge driven by the
# router maps all of them, so a fall-through return (e.g. under prompt/i18n/
# refactor drift in the speaker labels) can never hit a missing path_map entry
# and crash LangGraph mid-run (#1088).
DEBATE_PATH_MAP = {
    "Bull Researcher": "Bull Researcher",
    "Bear Researcher": "Bear Researcher",
    "Research Manager": "Research Manager",
}
RISK_ANALYSIS_PATH_MAP = {
    "Aggressive Analyst": "Aggressive Analyst",
    "Conservative Analyst": "Conservative Analyst",
    "Neutral Analyst": "Neutral Analyst",
    "Portfolio Manager": "Portfolio Manager",
}


def _tools_or_done(state) -> str:
    """Route an analyst's turn: run its tool calls, or finish with its report."""
    return "tools" if state["messages"][-1].tool_calls else END


def _analyst_graph(spec, agent, max_tool_rounds: int):
    """One analyst as a graph of its own: the model and its tools, on a private message history.

    It returns only its report, so analysts running side by side never write the
    same key, and its tool calls never reach the other analysts' messages. After
    ``max_tool_rounds`` rounds of tool calls it is told to write its report, and
    that turn ends it whatever it answers, so a model that keeps calling tools
    cannot run the graph into its recursion limit (#1420).
    """
    output = TypedDict(f"{spec.key.capitalize()}Report", {
        spec.report_key: str, "evidence_registry": list[dict], "evidence_audit": list[dict],
    })
    graph = StateGraph(AgentState, output_schema=output)
    graph.add_node("agent", agent)
    graph.add_edge(START, "agent")
    if not spec.tools:
        graph.add_edge("agent", END)
        return graph.compile()

    def calls(messages):
        return [call["name"] for m in messages for call in (getattr(m, "tool_calls", None) or [])]

    def rounds(messages) -> int:
        return sum(1 for m in messages if getattr(m, "tool_calls", None))

    def more_or_wrap_up(state) -> str:
        return "wrap_up" if rounds(state["messages"]) >= max_tool_rounds else "agent"

    def wrap_up(state):
        repeated = ", ".join(f"{name} x{n}" for name, n in Counter(calls(state["messages"])).most_common())
        logger.warning("%s used its %d tool rounds (%s); asking for its report",
                       spec.agent_node, max_tool_rounds, repeated)
        return agent({**state, "messages": [*state["messages"], HumanMessage(WRAP_UP)]})

    graph.add_node("tools", ToolNode(list(spec.tools)))
    graph.add_node("wrap_up", wrap_up)
    graph.add_conditional_edges("agent", _tools_or_done, ["tools", END])
    graph.add_conditional_edges("tools", more_or_wrap_up, ["agent", "wrap_up"])
    graph.add_edge("wrap_up", END)
    return graph.compile()


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: Any,
        deep_thinking_llm: Any,
        conditional_logic: ConditionalLogic,
        max_tool_rounds: int = 6,
        *,
        llm_provider: str = "unknown",
        llm_timeout_seconds: Any = None,
        llm_max_retries: Any = None,
    ):
        """Initialize with required components."""
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.conditional_logic = conditional_logic
        self.llm_provider = llm_provider
        self.llm_timeout_seconds = llm_timeout_seconds
        self.llm_max_retries = llm_max_retries
        self.max_tool_rounds = max_tool_rounds

    def _observed(self, name: str, node: Any):
        return _observe_agent_node(
            name,
            node,
            provider=self.llm_provider,
            timeout=self.llm_timeout_seconds,
            retries=self.llm_max_retries,
        )

    def setup_graph(
        self, selected_analysts=("market", "social", "news", "fundamentals")
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include. Options are:
                - "market": Market analyst
                - "social": Sentiment analyst
                - "news": News analyst
                - "fundamentals": Fundamentals analyst
        """
        plan = build_analyst_execution_plan(selected_analysts)

        analyst_factories = {
            "market": lambda: create_market_analyst(self.quick_thinking_llm),
            "social": lambda: create_sentiment_analyst(self.quick_thinking_llm),
            "news": lambda: create_news_analyst(self.quick_thinking_llm),
            "fundamentals": lambda: create_fundamentals_analyst(self.quick_thinking_llm),
        }

        bull_researcher_node = create_bull_researcher(self.quick_thinking_llm)
        bear_researcher_node = create_bear_researcher(self.quick_thinking_llm)
        research_manager_node = create_research_manager(self.deep_thinking_llm)
        trader_node = create_trader(self.quick_thinking_llm)

        aggressive_analyst = create_aggressive_debator(self.quick_thinking_llm)
        neutral_analyst = create_neutral_debator(self.quick_thinking_llm)
        conservative_analyst = create_conservative_debator(self.quick_thinking_llm)
        portfolio_manager_node = create_portfolio_manager(self.deep_thinking_llm)

        workflow = StateGraph(AgentState)

        for spec in plan.specs:
            workflow.add_node(spec.agent_node,
                              _analyst_graph(spec, self._observed(spec.agent_node, analyst_factories[spec.key]()), self.max_tool_rounds))

        for name, node in (
            ("Bull Researcher", bull_researcher_node), ("Bear Researcher", bear_researcher_node),
            ("Research Manager", research_manager_node), ("Trader", trader_node),
            ("Aggressive Analyst", aggressive_analyst), ("Neutral Analyst", neutral_analyst),
            ("Conservative Analyst", conservative_analyst), ("Portfolio Manager", portfolio_manager_node),
        ):
            workflow.add_node(name, self._observed(name, node))

        # The analysts work at the same time; the research debate starts once
        # every one of them has filed its report.
        analysts = [spec.agent_node for spec in plan.specs]
        for node in analysts:
            workflow.add_edge(START, node)
        workflow.add_edge(analysts, "Bull Researcher")

        # Both research-debate edges share the complete DEBATE_PATH_MAP (#1088).
        for debate_node in ("Bull Researcher", "Bear Researcher"):
            workflow.add_conditional_edges(
                debate_node,
                self.conditional_logic.should_continue_debate,
                DEBATE_PATH_MAP,
            )
        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Aggressive Analyst")
        # All three risk edges share the complete RISK_ANALYSIS_PATH_MAP (#1088).
        for risk_node in ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"):
            workflow.add_conditional_edges(
                risk_node,
                self.conditional_logic.should_continue_risk_analysis,
                RISK_ANALYSIS_PATH_MAP,
            )

        workflow.add_edge("Portfolio Manager", END)

        return workflow
