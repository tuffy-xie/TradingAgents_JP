import json
from typing import Annotated

from langgraph.graph import MessagesState
from typing_extensions import TypedDict


def merge_evidence_records(left: list[dict], right: list[dict]) -> list[dict]:
    """Union branch-local records without losing facts at the parallel join.

    Evidence has a canonical identity; findings retain their complete claim
    identity, including field and category. Unidentified operational records
    deduplicate only when identical, never merely because their field matches.
    """
    records = {}
    for record in [*(left or []), *(right or [])]:
        identity = record.get("evidence_id")
        key = ("evidence", identity) if identity else ("finding", json.dumps(record, sort_keys=True, default=str))
        records[key] = record
    return list(records.values())


# Researcher team state
class InvestDebateState(TypedDict):
    judge_decision: str
    bull_history: Annotated[
        str, "Bullish Conversation history"
    ]
    bear_history: Annotated[
        str, "Bearish Conversation history"
    ]
    history: Annotated[str, "Conversation history"]
    current_response: Annotated[str, "Latest response"]
    count: Annotated[int, "Length of the current conversation"]


# Risk management team state
class RiskDebateState(TypedDict):
    judge_decision: str
    aggressive_history: Annotated[
        str, "Aggressive Agent's Conversation history"
    ]
    conservative_history: Annotated[
        str, "Conservative Agent's Conversation history"
    ]
    neutral_history: Annotated[
        str, "Neutral Agent's Conversation history"
    ]
    history: Annotated[str, "Conversation history"]
    latest_speaker: Annotated[str, "Analyst that spoke last"]
    current_aggressive_response: Annotated[
        str, "Latest response by the aggressive analyst"
    ]
    current_conservative_response: Annotated[
        str, "Latest response by the conservative analyst"
    ]
    current_neutral_response: Annotated[
        str, "Latest response by the neutral analyst"
    ]
    count: Annotated[int, "Length of the current conversation"]


class AgentState(MessagesState):
    company_of_interest: Annotated[str, "Company that we are interested in trading"]
    asset_type: Annotated[str, "Asset type under analysis such as stock or crypto"]
    market_context: Annotated[dict, "Canonical market, exchange, symbol, currency, timezone and instrument type"]
    verified_market_snapshot: Annotated[str, "Optional OHLCV and technical-data validation snapshot for this run"]
    japan_data_bundle: Annotated[dict, "Source-attributed Japan facts; empty for non-Japan markets"]
    decision_context: Annotated[dict, "Deterministic Japan decision dimensions; empty for non-Japan markets"]
    evidence_audit: Annotated[list[dict], merge_evidence_records]
    evidence_registry: Annotated[list[dict], merge_evidence_records]
    run_manifest: Annotated[dict, "Non-secret runtime identity metadata for reproducibility"]
    validated_execution: Annotated[dict, "Deterministically calculated entry/stop/position risk facts"]
    final_output_contract: Annotated[dict, "Canonical user-output acceptance contract"]
    raw_agent_outputs: Annotated[dict, "Original Agent prose retained only for technical logs"]
    instrument_context: Annotated[str, "Deterministic ticker identity resolved at run start"]
    trade_date: Annotated[str, "What date we are trading at"]
    trade_constraints: Annotated[
        dict, "Optional user-supplied execution and risk constraints"
    ]

    sender: Annotated[str, "Agent that sent this message"]

    # research step
    market_report: Annotated[str, "Report from the Market Analyst"]
    sentiment_report: Annotated[str, "Report from the Sentiment Analyst"]
    news_report: Annotated[str, "Report from the News Analyst on company and world news"]
    fundamentals_report: Annotated[str, "Report from the Fundamentals Analyst"]

    # researcher team discussion step
    investment_debate_state: Annotated[
        InvestDebateState, "Current state of the debate on if to invest or not"
    ]
    investment_plan: Annotated[str, "Investment plan from the Research Manager"]

    trader_investment_plan: Annotated[str, "Transaction proposal from the Trader"]

    # risk management team discussion step
    risk_debate_state: Annotated[
        RiskDebateState, "Current state of the debate on evaluating risk"
    ]
    final_trade_decision: Annotated[str, "Final decision from the Portfolio Manager"]
    final_rating: Annotated[str, "The Portfolio Manager's 5-tier rating, or REVIEW when it has none"]
    past_context: Annotated[str, "Memory log context injected at run start (same-ticker decisions + cross-ticker lessons)"]
    portfolio_context: Annotated[str, "Caller-supplied holdings and cash, rendered at run start; empty when not provided"]
