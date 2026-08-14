"""Small, JP-only guardrail for agent text derived from run-level evidence.

This is deliberately a post-generation *downgrade* rather than a second
agent, provider, or LangGraph branch.  It prevents a clearly unsupported
precise claim from silently flowing into the next debate stage, while leaving
US behaviour untouched.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

_NUMBER = re.compile(r"(?<![A-Za-z_])[-+]?\d[\d,]*(?:\.\d+)?%?")
_CURRENT_PRICE = re.compile(r"(?:current\s+price|spot\s+price|当前(?:股)?价|现价|株価)", re.I)
_GUIDANCE = re.compile(r"(?:company\s+guidance|guidance|公司指引|业绩指引)", re.I)
_CONSENSUS = re.compile(r"(?:analyst\s+(?:consensus|estimate)|consensus|分析师(?:一致预期|预测)|市场预期)", re.I)
_CURRENT_WORD = re.compile(r"(?:current|latest|today|目前|当前|最新)", re.I)
_FACT_LABEL = re.compile(r"(?:verified\s+fact|official\s+fact|官方事实|已验证事实)", re.I)
_NON_FACT_SOURCE = re.compile(
    r"(?:yahoo[^\n]*?(?:board|掲示板)|community|sentiment|社区|情绪|analyst\s+(?:consensus|estimate)|分析师(?:一致预期|预测))",
    re.I,
)
_PERIOD_MIX = re.compile(
    r"(?:\bFY\b(?:(?![。!?\n]).)*(?:\bQ[1-4]\b|\bTTM\b)|(?:\bQ[1-4]\b|\bTTM\b)(?:(?![。!?\n]).)*\bFY\b)",
    re.I,
)


@dataclass(frozen=True)
class EvidenceEnforcementResult:
    text: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Catalog:
    all_numbers: frozenset[str]
    snapshot_numbers: frozenset[str]
    guidance_numbers: frozenset[str]
    analyst_numbers: frozenset[str]
    stale_numbers: frozenset[str]


def enforce_agent_output(state: Mapping[str, Any], text: str, agent_name: str) -> EvidenceEnforcementResult:
    """Downgrade unsupported JP numeric claims and retain an audit note.

    The check is intentionally conservative: a number is kept only when it is
    present in the immutable verified snapshot, the normalized Japan bundle,
    or an explicit user execution constraint.  It does not calculate or
    reconcile any values.
    """
    if not text or (state.get("market_context") or {}).get("market") != "JP":
        return EvidenceEnforcementResult(text)

    catalog = _catalog_from_state(state)
    warnings: list[str] = []

    def clean_clause(clause: str) -> str:
        if _PERIOD_MIX.search(clause):
            warnings.append("period_mismatch")
            return "【证据约束：FY、季度与 TTM 不可混算；相关数值已降级为数据不可用】"

        if _FACT_LABEL.search(clause) and _NON_FACT_SOURCE.search(clause):
            warnings.append("source_type_confusion")
            return "【证据约束：社区情绪与分析师预期不可表述为已验证官方事实】"

        if _GUIDANCE.search(clause) and _CONSENSUS.search(clause):
            warnings.append("guidance_consensus_mixed")
            return "【证据约束：公司指引与分析师一致预期属于不同口径，不能合并或互相替代】"

        tokens = _number_tokens(clause)
        if not tokens:
            return clause
        if _CURRENT_PRICE.search(clause) and any(token not in catalog.snapshot_numbers for token in tokens):
            warnings.append("non_snapshot_current_price")
            return _replace_unsupported(clause, catalog.snapshot_numbers)
        if _GUIDANCE.search(clause) and any(
            token in catalog.analyst_numbers and token not in catalog.guidance_numbers for token in tokens
        ):
            warnings.append("analyst_estimate_as_guidance")
            return _replace_unsupported(clause, catalog.guidance_numbers)
        if _CONSENSUS.search(clause) and any(
            token in catalog.guidance_numbers and token not in catalog.analyst_numbers for token in tokens
        ):
            warnings.append("guidance_as_analyst_consensus")
            return _replace_unsupported(clause, catalog.analyst_numbers)
        if _CURRENT_WORD.search(clause) and any(token in catalog.stale_numbers for token in tokens):
            warnings.append("stale_data_as_current")
            return _replace_unsupported(clause, catalog.all_numbers - catalog.stale_numbers)
        if any(token not in catalog.all_numbers for token in tokens):
            warnings.append("unsupported_precise_number")
            return _replace_unsupported(clause, catalog.all_numbers)
        return clause

    # Preserve markdown structure.  One line is a deliberately small enough
    # unit for reports and avoids splitting decimal values at their dot.
    cleaned = "".join(clean_clause(line) for line in text.splitlines(keepends=True))
    if not warnings:
        return EvidenceEnforcementResult(cleaned)
    unique_warnings = tuple(dict.fromkeys(warnings))
    audit = f"\n\n[Evidence enforcement | {agent_name}: " + ", ".join(unique_warnings) + "]"
    return EvidenceEnforcementResult(cleaned + audit, unique_warnings)


def enforce_agent_result(state: Mapping[str, Any], result: dict[str, Any], agent_name: str) -> dict[str, Any]:
    """Apply the text guard at the graph's common node boundary for JP only."""
    if (state.get("market_context") or {}).get("market") != "JP":
        return result
    result = dict(result)
    for key in ("market_report", "fundamentals_report", "news_report", "sentiment_report", "investment_plan", "trader_investment_plan", "final_trade_decision"):
        if isinstance(result.get(key), str):
            result[key] = enforce_agent_output(state, result[key], agent_name).text
    for debate_key in ("investment_debate_state", "risk_debate_state"):
        debate = result.get(debate_key)
        if not isinstance(debate, dict):
            continue
        updated = dict(debate)
        for key, value in debate.items():
            if isinstance(value, str):
                updated[key] = enforce_agent_output(state, value, agent_name).text
        result[debate_key] = updated
    return result


def _catalog_from_state(state: Mapping[str, Any]) -> _Catalog:
    snapshot_numbers = _numbers_in(state.get("verified_market_snapshot", ""))
    all_numbers = set(snapshot_numbers)
    guidance_numbers: set[str] = set()
    analyst_numbers: set[str] = set()
    stale_numbers: set[str] = set()
    bundle = state.get("japan_data_bundle") or {}
    stale_keys = {
        (entry.get("source"), entry.get("source_type"), entry.get("timestamp"))
        for item in _iter_mapping_values(bundle.get("items") or [])
        if item.get("source_type") == "source_of_truth_assessment"
        for entry in item.get("metadata", {}).get("freshness", [])
        if entry.get("status") == "STALE"
    }
    for item in _iter_mapping_values(bundle.get("items") or []):
        payload_numbers = _numbers_in(item)
        all_numbers.update(payload_numbers)
        source_type = str(item.get("source_type", ""))
        source = str(item.get("source", ""))
        if source_type == "japan_analyst_expectations":
            analyst_numbers.update(payload_numbers)
        if source in {"TDnet", "Company IR", "J-Quants"} and source_type in {"guidance_revision", "official_financial_summary"}:
            guidance_numbers.update(payload_numbers)
        if (source, source_type, item.get("timestamp")) in stale_keys:
            stale_numbers.update(payload_numbers)
    all_numbers.update(_numbers_in(state.get("trade_constraints") or {}))
    all_numbers.update(_numbers_in(state.get("decision_context") or {}))
    return _Catalog(
        frozenset(all_numbers),
        frozenset(snapshot_numbers),
        frozenset(guidance_numbers),
        frozenset(analyst_numbers),
        frozenset(stale_numbers),
    )


def _iter_mapping_values(items: Iterable[Any]) -> Iterable[Mapping[str, Any]]:
    return (item for item in items if isinstance(item, Mapping))


def _numbers_in(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return set().union(*(_numbers_in(item) for item in value.values())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(_numbers_in(item) for item in value)) if value else set()
    return set(_number_tokens(str(value)))


def _number_tokens(text: str) -> tuple[str, ...]:
    return tuple(_normalise_number(match.group(0)) for match in _NUMBER.finditer(text))


def _normalise_number(token: str) -> str:
    return token.replace(",", "")


def _replace_unsupported(clause: str, permitted: frozenset[str] | set[str]) -> str:
    def replace(match: re.Match[str]) -> str:
        return match.group(0) if _normalise_number(match.group(0)) in permitted else "数据不可用"

    return _NUMBER.sub(replace, clause)
