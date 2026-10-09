"""Deterministic execution math shared by Trader, Risk, and Portfolio nodes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

_ENTRY = re.compile(r"(?:\*\*Entry Price\*\*|entry(?: price)?|入场(?:价)?|建仓价)\s*[:：=]?\s*[¥￥]?\s*([\d,]+(?:\.\d+)?)", re.I)
_STOP = re.compile(r"(?:\*\*Stop Loss\*\*|stop(?:[ -]?loss)?|止损(?:价)?)\s*[:：=]?\s*[¥￥]?\s*([\d,]+(?:\.\d+)?)", re.I)
_POSITION = re.compile(r"(?:\*\*Maximum Position\*\*|\*\*Position Sizing\*\*|position(?: sizing)?|maximum position|仓位上限|仓位)\s*[:：=]?\s*([\d.]+)\s*%", re.I)
_ZERO_TARGET = re.compile(
    r"(?:减持|減持|减仓|減倉|仓位|倉位|目标仓位|目标配置)[^。；;\n]{0,12}(?:至|到|归零|歸零|[:：=])\s*0\s*%|"
    r"(?:exit|close|reduce|target)[^.;\n]{0,45}(?:position|exposure|allocation)[^.;\n]{0,12}(?:to|:)\s*0\s*%", re.I,
)


def _targets_flat(text: str) -> bool:
    for unit in re.split(r"[。；;\n]|\bbut\b|但", text, flags=re.I):
        for match in _ZERO_TARGET.finditer(unit):
            prefix = unit[max(0, match.start() - 20):match.start()]
            if not re.search(r"不(?:应|應|要|建议|建議|得)?|禁止|\b(?:do\s+not|must\s+not|avoid)\s*$", prefix, re.I):
                return True
    return False
_ACTION_LABELS = {
    "action",
    "recommendation",
    "final recommendation",
    "final trading recommendation",
    "final transaction proposal",
    "建议",
    "建議",
    "交易建议",
    "交易建議",
    "最终建议",
    "最終建議",
    "最终交易建议",
    "最終交易建議",
    "交易动作",
    "交易動作",
    "操作建议",
    "操作建議",
    "推奨",
    "取引推奨",
    "売買判断",
    "アクション",
}
_ACTION_VALUE = re.compile(
    r"^\s*(Buy|Hold|Sell|买入|持有|卖出|賣出|观望|觀望)"
    r"(?=$|[\s*`（(：:|])",
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
    "观望": "Hold",
    "觀望": "Hold",
}
_RISK_CLAUSE = re.compile(
    r"[^\n。！？;；]*(?:止损距离|stop(?:[ -]?loss)?\s+(?:distance|risk)|risk\s*(?:pct|percentage|%)|组合(?:止损)?损失|portfolio\s+(?:stop\s+)?risk)[^\n。！？;；]*[。！？;；]?",
    re.I,
)

# Parsed proposals remain in raw_agent_outputs. A rejected canonical plan must
# not carry actionable numbers or their derived risk math into publication.
EXECUTION_PLAN_FIELDS = (
    "entry", "stop", "target", "target_price", "position_pct", "position_size",
    "distance", "risk_pct", "portfolio_stop_risk_pct", "derivation",
)


def _withheld_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    for field in EXECUTION_PLAN_FIELDS:
        if field in result or field in {"entry", "stop", "position_pct"}:
            result[field] = None
    return result


def validate_execution_plan(text: str) -> dict[str, Any]:
    """Calculate plan risk from model-chosen entry, stop, and position inputs."""
    action = parse_execution_action(text)
    entry = _first_decimal(_ENTRY, text)
    stop = _first_decimal(_STOP, text)
    position = _first_decimal(_POSITION, text)
    # Sizing is traded exposure, NOT a target account balance. A zero-size
    # proposal cannot acquire permission just because its entry/stop exist.
    if action in {"Buy", "Sell"} and (position == 0 or _targets_flat(text)):
        return _withheld_plan({
            "version": "v2", "status": "DATA_UNAVAILABLE",
            "detail": "ZERO_EXPOSURE_IS_NOT_AN_EXECUTABLE_TRADE",
            "action": action, "execution_intent": "TARGET_FLAT" if action == "Sell" else "ZERO_SIZE",
        })
    if action == "Hold":
        return _withheld_plan({
            "version": "v2",
            "status": "DATA_UNAVAILABLE",
            "detail": "HOLD_DOES_NOT_AUTHORIZE_NEW_EXECUTION",
            "action": action,
            "entry": _float(entry),
            "stop": _float(stop),
            "position_pct": _float(position),
        })
    if action is None:
        return _withheld_plan({
            "version": "v2",
            "status": "DATA_UNAVAILABLE",
            "detail": "ACTION_UNAVAILABLE",
            "action": None,
            "entry": _float(entry),
            "stop": _float(stop),
            "position_pct": _float(position),
        })
    if entry is None or stop is None or entry <= 0:
        return _withheld_plan({
            "version": "v2",
            "status": "DATA_UNAVAILABLE",
            "detail": "ENTRY_OR_STOP_UNAVAILABLE",
            "action": action,
            "entry": _float(entry),
            "stop": _float(stop),
            "position_pct": _float(position),
        })
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


def parse_execution_action(text: str) -> str | None:
    """Read an explicitly labelled Trader action from prose or Markdown.

    Free-text provider fallbacks are allowed to localize labels and to use a
    Markdown table.  Only a labelled field is authoritative: an incidental
    ``Hold`` in the reasoning never grants or revokes execution permission.
    """
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("|") and line.endswith("|"):
            cells = [_plain_field(cell) for cell in line.strip("|").split("|")]
            for index, cell in enumerate(cells[:-1]):
                if _normal_label(cell) in _ACTION_LABELS:
                    action = _canonical_action(cells[index + 1])
                    if action:
                        return action
        plain = _plain_field(line)
        match = re.match(r"^(.+?)\s*[:：=]\s*(.+)$", plain)
        if not match or _normal_label(match.group(1)) not in _ACTION_LABELS:
            continue
        action = _canonical_action(match.group(2))
        if action:
            return action
    return None


def reconcile_execution_authority(
    validation: Mapping[str, Any] | None,
    *,
    trader_action: str | None,
    portfolio_rating: str | None,
    portfolio_text: str = "",
    portfolio_context: str = "",
) -> dict[str, Any]:
    """Require compatible explicit Trader and Portfolio decisions.

    Numeric completeness is necessary but not sufficient for a new execution
    plan.  The Trader must explicitly authorize Buy/Sell and the Portfolio
    rating must agree with that direction.  Hold and unknown decisions always
    fail closed, independently of any parsed entry or stop values.
    """
    result = dict(validation or {})
    action = trader_action or result.get("action")
    result["action"] = action
    result["portfolio_rating"] = portfolio_rating

    detail: str | None = None
    if portfolio_rating == "Hold" or action == "Hold":
        detail = "HOLD_DOES_NOT_AUTHORIZE_NEW_EXECUTION"
    elif action not in {"Buy", "Sell"}:
        detail = "ACTION_UNAVAILABLE"
    elif portfolio_rating is None:
        detail = "FINAL_RATING_UNAVAILABLE"
    elif (
        action == "Buy" and portfolio_rating not in {"Buy", "Overweight"}
    ) or (
        action == "Sell" and portfolio_rating not in {"Sell", "Underweight"}
    ):
        detail = "TRADER_PORTFOLIO_ACTION_CONFLICT"

    if detail:
        result.update(
            {
                "version": result.get("version", "v2"),
                "status": "DATA_UNAVAILABLE",
                "detail": detail,
            }
        )
    if not detail and (result.get("position_pct") == 0 or _targets_flat(portfolio_text)):
        # The current price/stop calculator has no closing quantity or account
        # transition validator. Even a supplied book cannot make a zero target
        # a positive trade size. Keep the rating, withhold the unsupported plan.
        result.update(status="DATA_UNAVAILABLE", execution_intent="TARGET_FLAT",
                      detail="CLOSING_HOLDINGS_UNVERIFIED" if not portfolio_context.strip()
                      else "TARGET_POSITION_NOT_TRADE_SIZE")
    return _withheld_plan(result) if result.get("status") != "OK" else result


def execution_plan_authority_issues(validation: Mapping[str, Any], portfolio_text: str = "") -> list[str]:
    """Independent publisher check: zero target/size is never a new risk trade."""
    if validation.get("status") == "OK" and (
        validation.get("position_pct") == 0 or _targets_flat(portfolio_text)
        or validation.get("execution_intent") in {"TARGET_FLAT", "ZERO_SIZE"}
    ):
        return ["EXECUTION_ZERO_EXPOSURE_NOT_AUTHORIZED"]
    return []


def _plain_field(value: str) -> str:
    value = re.sub(r"^\s*(?:#{1,6}|[-*+] |\d+[.)]\s+)", "", value)
    return value.replace("**", "").replace("__", "").replace("`", "").strip()


def _normal_label(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _canonical_action(value: str) -> str | None:
    match = _ACTION_VALUE.match(value)
    return _ACTION_CANONICAL.get(match.group(1).casefold()) if match else None


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
