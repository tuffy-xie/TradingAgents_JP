# TradingAgents/graph/setup.py

import logging
import time
from functools import wraps
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from tradingagents.agents import (
    create_aggressive_debator,
    create_bear_researcher,
    create_bull_researcher,
    create_conservative_debator,
    create_fundamentals_analyst,
    create_market_analyst,
    create_msg_delete,
    create_neutral_debator,
    create_news_analyst,
    create_portfolio_manager,
    create_research_manager,
    create_sentiment_analyst,
    create_trader,
)
from tradingagents.agents.utils.agent_states import AgentState
from tradingagents.agents.utils.evidence_enforcement import enforce_agent_result
from tradingagents.agents.utils.evidence_registry import capture_agent_evidence
from tradingagents.agents.utils.execution_validation import (
    enforce_execution_math,
    validate_execution_plan,
)

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
            result = capture_agent_evidence(state, result, name)
            combined_state = {**state, **result} if isinstance(state, dict) else result
            result = enforce_agent_result(combined_state, result, name)
            result = _apply_execution_authority(state, result, name)
        logger.info(
            "[Agent] complete name=%s ticker=%s elapsed_seconds=%.2f",
            name, ticker, time.perf_counter() - start,
        )
        return result
    return observed


def _apply_execution_authority(
    state: dict[str, Any], result: dict[str, Any], agent_name: str
) -> dict[str, Any]:
    if (state.get("market_context") or {}).get("market") != "JP":
        return result
    updated = dict(result)
    audit = list(updated.get("evidence_audit") or state.get("evidence_audit") or [])
    if agent_name == "Trader" and isinstance(updated.get("trader_investment_plan"), str):
        validation = validate_execution_plan(updated["trader_investment_plan"])
        updated["validated_execution"] = validation
        cleaned, warnings = enforce_execution_math(updated["trader_investment_plan"], validation)
        updated["trader_investment_plan"] = cleaned
        if warnings:
            audit.append({"category": "ARITHMETIC_MISMATCH", "agent": agent_name, "warnings": list(warnings)})
    else:
        validation = state.get("validated_execution") or {}
        for key in ("final_trade_decision",):
            if isinstance(updated.get(key), str):
                cleaned, warnings = enforce_execution_math(updated[key], validation)
                updated[key] = cleaned
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
                cleaned, warnings = enforce_execution_math(value, validation)
                cleaned_debate[key] = cleaned
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


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: Any,
        deep_thinking_llm: Any,
        tool_nodes: dict[str, ToolNode],
        conditional_logic: ConditionalLogic,
        *,
        llm_provider: str = "unknown",
        llm_timeout_seconds: Any = None,
        llm_max_retries: Any = None,
    ):
        """Initialize with required components."""
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.tool_nodes = tool_nodes
        self.conditional_logic = conditional_logic
        self.llm_provider = llm_provider
        self.llm_timeout_seconds = llm_timeout_seconds
        self.llm_max_retries = llm_max_retries

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
                - "social": Social media analyst
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

        # Create researcher and manager nodes
        bull_researcher_node = create_bull_researcher(self.quick_thinking_llm)
        bear_researcher_node = create_bear_researcher(self.quick_thinking_llm)
        research_manager_node = create_research_manager(self.deep_thinking_llm)
        trader_node = create_trader(self.quick_thinking_llm)

        # Create risk analysis nodes
        aggressive_analyst = create_aggressive_debator(self.quick_thinking_llm)
        neutral_analyst = create_neutral_debator(self.quick_thinking_llm)
        conservative_analyst = create_conservative_debator(self.quick_thinking_llm)
        portfolio_manager_node = create_portfolio_manager(self.deep_thinking_llm)

        # Create workflow
        workflow = StateGraph(AgentState)

        # Add analyst nodes to the graph
        for spec in plan.specs:
            workflow.add_node(spec.agent_node, self._observed(spec.agent_node, analyst_factories[spec.key]()))
            workflow.add_node(spec.clear_node, create_msg_delete())
            workflow.add_node(spec.tool_node, self.tool_nodes[spec.key])

        # Add other nodes
        workflow.add_node("Bull Researcher", self._observed("Bull Researcher", bull_researcher_node))
        workflow.add_node("Bear Researcher", self._observed("Bear Researcher", bear_researcher_node))
        workflow.add_node("Research Manager", self._observed("Research Manager", research_manager_node))
        workflow.add_node("Trader", self._observed("Trader", trader_node))
        workflow.add_node("Aggressive Analyst", self._observed("Aggressive Analyst", aggressive_analyst))
        workflow.add_node("Neutral Analyst", self._observed("Neutral Analyst", neutral_analyst))
        workflow.add_node("Conservative Analyst", self._observed("Conservative Analyst", conservative_analyst))
        workflow.add_node("Portfolio Manager", self._observed("Portfolio Manager", portfolio_manager_node))

        # Define edges
        # Start with the first analyst
        workflow.add_edge(START, plan.specs[0].agent_node)

        # Connect analysts in sequence
        for i, spec in enumerate(plan.specs):
            current_analyst = spec.agent_node
            current_tools = spec.tool_node
            current_clear = spec.clear_node

            # Add conditional edges for current analyst
            workflow.add_conditional_edges(
                current_analyst,
                getattr(self.conditional_logic, f"should_continue_{spec.key}"),
                [current_tools, current_clear],
            )
            workflow.add_edge(current_tools, current_analyst)

            # Connect to next analyst or to Bull Researcher if this is the last analyst
            if i < len(plan.specs) - 1:
                workflow.add_edge(current_clear, plan.specs[i + 1].agent_node)
            else:
                workflow.add_edge(current_clear, "Bull Researcher")

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
