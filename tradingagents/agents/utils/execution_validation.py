"""Deterministic execution math shared by Trader, Risk, and Portfolio nodes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

_ENTRY = re.compile(r"(?:\*\*Entry Price\*\*|entry(?: price)?|入场(?:价)?|建仓价)\s*[:：=]?\s*[¥￥]?\s*([\d,]+(?:\.\d+)?)", re.I)
_STOP = re.compile(r"(?:\*\*Stop Loss\*\*|stop(?:[ -]?loss)?|止损(?:价)?)\s*[:：=]?\s*[¥￥]?\s*([\d,]+(?:\.\d+)?)", re.I)
_POSITION = re.compile(r"(?:\*\*Maximum Position\*\*|\*\*Position Sizing\*\*|position(?: sizing)?|maximum position|仓位上限|仓位)\s*[:：=]?\s*([\d.]+)\s*%", re.I)
_ACTION = re.compile(
    r"(?:\*\*Action\*\*|action|最终交易建议|交易动作)\s*[:：=]?\s*"
    r"(?:\*\*)?(Buy|Hold|Sell|买入|持有|卖出|賣出)(?:\*\*)?",
    re.I,
)
_ACTION_CANONICAL = {
    "buy": "Buy",
    "hold": "Hold",
    "sell": "Sell",
    "买入": "Buy",
    "持有": "Hold",
    "卖出": "Sell",
    "賣出": "Sell",
}
_RISK_CLAUSE = re.compile(
    r"[^\n。！？;；]*(?:止损距离|stop(?:[ -]?loss)?\s+(?:distance|risk)|risk\s*(?:pct|percentage|%)|组合(?:止损)?损失|portfolio\s+(?:stop\s+)?risk)[^\n。！？;；]*[。！？;；]?",
    re.I,
)


def validate_execution_plan(text: str) -> dict[str, Any]:
    """Calculate plan risk from model-chosen entry, stop, and position inputs."""
    action_match = _ACTION.search(text or "")
    action = (
        _ACTION_CANONICAL[action_match.group(1).casefold()]
        if action_match
        else None
    )
    entry = _first_decimal(_ENTRY, text)
    stop = _first_decimal(_STOP, text)
    position = _first_decimal(_POSITION, text)
    if action == "Hold":
        return {
            "version": "v2",
            "status": "DATA_UNAVAILABLE",
            "detail": "HOLD_DOES_NOT_AUTHORIZE_NEW_EXECUTION",
            "action": action,
            "entry": _float(entry),
            "stop": _float(stop),
            "position_pct": _float(position),
        }
    if entry is None or stop is None or entry <= 0:
        return {
            "version": "v2",
            "status": "DATA_UNAVAILABLE",
            "detail": "ENTRY_OR_STOP_UNAVAILABLE",
            "action": action,
            "entry": _float(entry),
            "stop": _float(stop),
            "position_pct": _float(position),
        }
    distance = abs(entry - stop)
    risk_pct = distance / entry * Decimal("100")
    portfolio_risk = (
        position * risk_pct / Decimal("100") if position is not None else None
    )
    return {
        "version": "v2",
        "status": "OK",
        "action": action,
        "entry": _float(entry),
        "stop": _float(stop),
        "distance": _float(distance),
        "risk_pct": _rounded(risk_pct),
        "position_pct": _float(position),
        "portfolio_stop_risk_pct": _rounded(portfolio_risk),
        "derivation": {
            "method": "DETERMINISTIC_EXECUTION_CALCULATOR",
            "risk_formula": "abs(entry-stop)/entry*100",
            "portfolio_risk_formula": "position_pct*risk_pct/100",
        },
    }


def authoritative_execution_context(value: Mapping[str, Any] | None) -> str:
    if not value or value.get("status") != "OK":
        return "## Deterministic execution validation\n- Status: DATA_UNAVAILABLE"
    portfolio = value.get("portfolio_stop_risk_pct")
    lines = [
        "## Deterministic execution validation (numeric authority)",
        f"- Entry: {value.get('entry')}",
        f"- Stop: {value.get('stop')}",
        f"- Distance: {value.get('distance')}",
        f"- Entry-to-stop risk: {value.get('risk_pct')}%",
    ]
    if value.get("position_pct") is not None:
        lines.append(f"- Position: {value.get('position_pct')}%")
    if portfolio is not None:
        lines.append(f"- Portfolio stop risk: {portfolio}%")
    lines.append("Do not recalculate or contradict these validated values.")
    return "\n".join(lines)


def enforce_execution_math(
    text: str, validation: Mapping[str, Any] | None
) -> tuple[str, tuple[str, ...]]:
    """Remove conflicting risk arithmetic and append the authoritative block."""
    if not text or not validation or validation.get("status") != "OK":
        return text, ()
    expected = {
        _normal_percent(validation.get("risk_pct")),
        _normal_percent(validation.get("portfolio_stop_risk_pct")),
    } - {None}
    warnings: list[str] = []

    def replace(match: re.Match[str]) -> str:
        clause = match.group(0)
        seen = {
            _normal_percent(token)
            for token in re.findall(r"([\d.]+)\s*%", clause)
        } - {None}
        if seen and not seen.issubset(expected):
            warnings.append("ARITHMETIC_MISMATCH")
            return "【执行数学已由确定性校验器更正；以文末验证结果为准。】"
        return clause

    cleaned = _RISK_CLAUSE.sub(replace, text).rstrip()
    block = authoritative_execution_context(validation)
    if "## Deterministic execution validation" not in cleaned:
        cleaned += "\n\n" + block
    return cleaned, tuple(dict.fromkeys(warnings))


def million_jpy_to_oku(value: int | float | Decimal | str) -> Decimal:
    """Convert source 百万円 to 億円 without LLM arithmetic."""
    try:
        number = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError("invalid million-yen value") from exc
    return (number / Decimal("100")).quantize(Decimal("0.01"))


def _first_decimal(pattern: re.Pattern[str], text: str) -> Decimal | None:
    match = pattern.search(text or "")
    if not match:
        return None
    try:
        return Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None


def _float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _rounded(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _normal_percent(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    except InvalidOperation:
        return None
