"""Small JP-only consistency checks for final decision hand-offs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_ACTION = re.compile(r"\*\*Action\*\*:\s*(Buy|Hold|Sell)", re.I)
_RECOMMENDATION = re.compile(r"\*\*Recommendation\*\*:\s*(Buy|Overweight|Hold|Underweight|Sell)", re.I)
_RATING = re.compile(r"\*\*Rating\*\*:\s*(Buy|Overweight|Hold|Underweight|Sell)", re.I)
_INVALIDATION = re.compile(r"(?:stop[- ]?loss|invalidation|失效|止损)", re.I)


def enforce_decision_consistency(state: Mapping[str, Any], result: dict[str, Any], agent_name: str) -> dict[str, Any]:
    """Downgrade only clear JP hand-off contradictions; US remains untouched."""
    if (state.get("market_context") or {}).get("market") != "JP":
        return result
    context = state.get("decision_context") or {}
    result = dict(result)
    if agent_name == "Research Manager" and isinstance(result.get("investment_plan"), str):
        result["investment_plan"] = _enforce_plan(
            result["investment_plan"], context, _RECOMMENDATION, "Recommendation"
        )
    elif agent_name == "Trader" and isinstance(result.get("trader_investment_plan"), str):
        result["trader_investment_plan"] = _enforce_plan(
            result["trader_investment_plan"], context, _ACTION, "Action"
        )
    elif agent_name == "Portfolio Manager" and isinstance(result.get("final_trade_decision"), str):
        decision = _enforce_plan(result["final_trade_decision"], context, _RATING, "Rating")
        warnings: list[str] = []
        trader_action = _extract(_ACTION, str(state.get("trader_investment_plan", "")))
        rating = _extract(_RATING, decision)
        if _opposes(trader_action, rating):
            warnings.append("trader_portfolio_direction_conflict")
        risk_direction = ((context.get("dimensions") or {}).get("risk") or {}).get("direction")
        if rating in {"buy", "overweight"} and risk_direction == "bearish" and not _INVALIDATION.search(decision):
            warnings.append("material_risk_without_invalidation")
        if warnings:
            decision = _replace_choice(decision, _RATING, "Hold")
            decision += "\n\n[Decision consistency: " + ", ".join(warnings) + "; downgraded to Hold]"
        result["final_trade_decision"] = decision
        debate = result.get("risk_debate_state")
        if isinstance(debate, dict):
            updated = dict(debate)
            updated["judge_decision"] = decision
            result["risk_debate_state"] = updated
    return result


def _enforce_plan(text: str, context: Mapping[str, Any], pattern: re.Pattern[str], label: str) -> str:
    choice = _extract(pattern, text)
    if choice not in {"buy", "overweight", "sell"}:
        return text
    if context.get("overall_confidence") != "low":
        return text
    downgraded = _replace_choice(text, pattern, "Hold")
    return downgraded + f"\n\n[Decision consistency: low confidence; {label} downgraded to Hold]"


def _extract(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1).lower() if match else None


def _replace_choice(text: str, pattern: re.Pattern[str], replacement: str) -> str:
    return pattern.sub(lambda match: match.group(0).replace(match.group(1), replacement), text, count=1)


def _opposes(trader: str | None, rating: str | None) -> bool:
    return (trader == "buy" and rating in {"sell", "underweight"}) or (
        trader == "sell" and rating in {"buy", "overweight"}
    )
