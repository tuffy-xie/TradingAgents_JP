from typing import Any

from tradingagents.agents.evidence_registry import initialize_evidence_registry
from tradingagents.agents.state import (
    InvestDebateState,
    RiskDebateState,
)
from tradingagents.dataflows.japan.decision import build_japan_decision_context
from tradingagents.dataflows.market import MarketContext, resolve_market_context


class Propagator:
    """Handles state initialization and propagation through the graph."""

    def __init__(self, max_recur_limit=100):
        """Initialize with configuration parameters."""
        self.max_recur_limit = max_recur_limit

    def create_initial_state(
        self,
        company_name: str,
        trade_date: str,
        asset_type: str = "stock",
        past_context: str = "",
        instrument_context: str = "",
        trade_constraints: dict[str, Any] | None = None,
        market_context: MarketContext | dict[str, Any] | None = None,
        japan_data_bundle: dict[str, Any] | None = None,
        verified_market_snapshot: str = "",
        run_manifest: dict[str, Any] | None = None,
        portfolio_context: str = "",
    ) -> dict[str, Any]:
        """Create the initial state for the agent graph.

        ``instrument_context`` is the deterministic ticker-identity string
        resolved once at run start (see
        ``TradingAgentsGraph.resolve_instrument_context``). When empty, agents
        fall back to ticker-only context via
        ``get_instrument_context_from_state``.
        """
        context = market_context or resolve_market_context(company_name)
        context_dict = context.to_dict() if isinstance(context, MarketContext) else dict(context)
        canonical_symbol = context_dict["symbol"]
        evidence_registry, evidence_audit = initialize_evidence_registry(
            market_context=context_dict,
            japan_data_bundle=japan_data_bundle or {},
            verified_market_snapshot=verified_market_snapshot,
            analysis_as_of=str(trade_date),
        )
        return {
            "messages": [("human", canonical_symbol)],
            "company_of_interest": canonical_symbol,
            "asset_type": asset_type,
            "market_context": context_dict,
            "japan_data_bundle": japan_data_bundle or {},
            "decision_context": build_japan_decision_context(
                japan_data_bundle, verified_market_snapshot
            )
            if context_dict.get("market") == "JP"
            else {},
            "evidence_registry": evidence_registry,
            "evidence_audit": evidence_audit,
            "run_manifest": run_manifest or {},
            "validated_execution": {},
            "verified_market_snapshot": verified_market_snapshot,
            "instrument_context": instrument_context,
            "trade_date": str(trade_date),
            "trade_constraints": trade_constraints or {},
            "past_context": past_context,
            "portfolio_context": portfolio_context,
            "investment_debate_state": InvestDebateState(
                {
                    "bull_history": "",
                    "bear_history": "",
                    "history": "",
                    "current_response": "",
                    "count": 0,
                }
            ),
            "risk_debate_state": RiskDebateState(
                {
                    "aggressive_history": "",
                    "conservative_history": "",
                    "neutral_history": "",
                    "history": "",
                    "latest_speaker": "",
                    "current_aggressive_response": "",
                    "current_conservative_response": "",
                    "current_neutral_response": "",
                    "count": 0,
                }
            ),
            "market_report": "",
            "fundamentals_report": "",
            "sentiment_report": "",
            "news_report": "",
        }

    def get_graph_args(self, callbacks: list | None = None) -> dict[str, Any]:
        """Get arguments for the graph invocation.

        Args:
            callbacks: Optional list of callback handlers for tool execution tracking.
                       Note: LLM callbacks are handled separately via LLM constructor.
        """
        config = {"recursion_limit": self.max_recur_limit}
        if callbacks:
            config["callbacks"] = callbacks
        return {
            "stream_mode": "values",
            "config": config,
        }
