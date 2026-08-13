"""User-facing Chinese presentation helpers.

The agent state deliberately keeps its stable English schema labels and enum
values.  This module is the final presentation boundary used by Markdown and
HTML/PDF reports, so changing a visible label never changes an agent contract
or a parser's input.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_LABELS = {
    "Recommendation": "研究评级",
    "Rationale": "核心理由",
    "Strategic Actions": "操作建议",
    "Action": "操作方向",
    "Reasoning": "操作逻辑",
    "Entry Price": "参考执行价",
    "Stop Loss": "止损 / 失效位",
    "Position Sizing": "仓位建议",
    "Entry Condition": "执行条件",
    "Stop-Loss Condition": "止损 / 失效条件",
    "Take-Profit / Trim Condition": "止盈 / 减仓条件",
    "Maximum Position": "最大仓位",
    "Rating": "综合评级",
    "Executive Summary": "执行摘要",
    "Investment Thesis": "投资逻辑",
    "Price Target": "目标价",
    "Time Horizon": "预计持有周期",
    "Overall Sentiment": "整体情绪",
    "Confidence": "置信度",
    "Score": "评分",
}

_VALUES = {
    "Buy": "买入",
    "BUY": "买入",
    "Add": "加仓",
    "ADD": "加仓",
    "Hold": "持有 / 观望",
    "HOLD": "持有 / 观望",
    "Wait": "观望 / 等待",
    "WAIT": "观望 / 等待",
    "Reduce": "减仓",
    "REDUCE": "减仓",
    "Sell": "卖出 / 减仓",
    "SELL": "卖出 / 减仓",
    "Exit": "清仓",
    "EXIT": "清仓",
    "Short": "做空",
    "SHORT": "做空",
    "Overweight": "超配 / 偏多",
    "OVERWEIGHT": "超配 / 偏多",
    "Underweight": "低配 / 偏谨慎",
    "UNDERWEIGHT": "低配 / 偏谨慎",
    "Neutral": "中性",
    "NEUTRAL": "中性",
    "Bullish": "偏多",
    "Bearish": "偏空",
    "Mixed": "分歧",
    "Low": "低",
    "Medium": "中",
    "High": "高",
}

_STATUS = {
    "OK": "正常",
    "AUTH_REQUIRED": "缺少 API Key 或权限",
    "DATA_UNAVAILABLE": "数据不可用",
    "PARSE_FAILED": "解析失败",
    "RATE_LIMITED": "请求受限",
    "LOW_SAMPLE": "样本不足",
    "DISABLED": "未启用",
}

_SOURCE_NAMES = {
    "Company IR": "公司 IR",
    "JSF": "日证金",
    "JPX": "JPX",
    "TDnet": "TDnet",
    "EDINET": "EDINET",
    "J-Quants": "J-Quants",
}

_VISIBLE_TERMS = {
    "Bull Researcher": "多头研究员",
    "Bear Researcher": "空头研究员",
    "Aggressive Analyst": "激进风险分析师",
    "Conservative Analyst": "保守风险分析师",
    "Neutral Analyst": "中性风险分析师",
    "Portfolio Manager": "投资组合经理",
    "Research Manager": "研究经理",
    "Trader": "交易员",
}


def chinese_status(value: str | None) -> str:
    return _STATUS.get(str(value or ""), "数据不可用")


def localize_markdown(text: str | None) -> str:
    """Translate known report labels/enums without touching free-form evidence."""
    result = text or ""
    for english, chinese in _LABELS.items():
        result = re.sub(rf"(?<=\*\*){re.escape(english)}(?=\*\*)", chinese, result)
    result = re.sub(
        r"FINAL TRANSACTION PROPOSAL:\s*\*\*([A-Z]+)\*\*",
        lambda m: f"最终交易方向（内部动作）：**{_VALUES.get(m.group(1), m.group(1))}**",
        result,
    )
    for english, chinese in _VISIBLE_TERMS.items():
        result = result.replace(english, chinese)
    for english, chinese in _VALUES.items():
        result = re.sub(rf"\b{re.escape(english)}\b", chinese, result)
        result = re.sub(rf"(?<=[:：]\s)\*\*{re.escape(english)}\*\*", f"**{chinese}**", result)
        result = re.sub(rf"(?<=[:：]\s){re.escape(english)}(?=\s*$)", chinese, result, flags=re.MULTILINE)
    for english, chinese in _STATUS.items():
        result = result.replace(english, chinese)
    return result


def _field(text: str, field: str) -> str:
    match = re.search(rf"\*\*{re.escape(field)}\*\*:\s*([^\n]+)", text or "", re.I)
    return match.group(1).strip() if match else "数据未提供"


def _company_name(state: Mapping[str, Any]) -> str:
    context = str(state.get("instrument_context") or "")
    match = re.search(r"(?:Company|公司)[：:]\s*([^\n]+)", context, re.I)
    return match.group(1).strip() if match else "数据未提供"


def _action_advice(action: str) -> tuple[str, str]:
    """Separate new-position and existing-position advice to avoid SELL ambiguity."""
    normalized = action.lower()
    if normalized == "buy":
        return "符合执行条件后分批建仓", "持有；仅在确认条件满足后加仓"
    if normalized in {"sell", "reduce", "exit"}:
        return "观望，不建议新建仓或追空", "已有持仓：按反弹区与风险条件分批减仓"
    if normalized in {"hold", "wait"}:
        return "观望，等待执行条件确认", "已有持仓：持有但不加仓，严格执行止损 / 减仓条件"
    return "观望 / 等待确认", "以风险控制为先"


def build_investment_overview(state: Mapping[str, Any]) -> str:
    """Build the compact, source-safe first-page investment summary."""
    final = str(state.get("final_trade_decision") or "")
    trader = str(state.get("trader_investment_plan") or state.get("trader_investment_decision") or "")
    ticker = str(state.get("company_of_interest") or "数据未提供")
    date = str(state.get("trade_date") or "数据未提供")
    rating = _field(final, "Rating")
    action = _field(trader, "Action")
    summary = _field(final, "Executive Summary")
    entry = _field(trader, "Entry Price")
    entry_condition = _field(trader, "Entry Condition")
    stop = _field(trader, "Stop Loss")
    if stop == "数据未提供":
        stop = _field(final, "Stop-Loss Condition")
    target = _field(final, "Price Target")
    horizon = _field(final, "Time Horizon")
    position = _field(trader, "Maximum Position")
    if position == "数据未提供":
        position = _field(final, "Maximum Position")
    chinese_rating = _VALUES.get(rating, rating)
    new_position, existing_position = _action_advice(action)
    one_line = summary if summary != "数据未提供" else "关键数据不足，暂不宜据此做出激进交易决策。"
    return "\n".join([
        "# 投资结论总览",
        "",
        "## 一句话投资结论",
        one_line,
        "",
        "## 核心投资建议",
        f"- 股票代码：{ticker}",
        f"- 公司名称：{_company_name(state)}",
        f"- 分析日期：{date}",
        f"- 综合评级：{chinese_rating}",
        f"- 新仓建议：{new_position}",
        f"- 已有持仓建议：{existing_position}",
        f"- 预计持有周期：{horizon}",
        f"- 参考执行价：{entry}",
        f"- 执行条件：{entry_condition}",
        f"- 核心目标价：{target}",
        f"- 核心止损 / 失效位：{stop}",
        f"- 最大仓位：{position}",
        "- 综合置信度：以数据覆盖度与 Agent 交叉验证为准",
    ])


def build_key_data_section(state: Mapping[str, Any]) -> str:
    """Show up to five source-attributed facts before long-form debate."""
    bundle = state.get("japan_data_bundle") or {}
    items = bundle.get("items") or []
    lines = ["# 本次分析最重要的 5 条数据", ""]
    for index, item in enumerate(items[:5], start=1):
        source = _SOURCE_NAMES.get(item.get("source"), item.get("source", "数据源"))
        title = item.get("title") or "本窗口数据"
        timestamp = str(item.get("timestamp") or "")[:10]
        lines.extend([
            f"## {index}. {source}",
            f"数据：{timestamp} {title}",
            "AI看法：该数据已进入本次研究上下文；需与技术面、基本面和风险条件交叉验证，不能单独构成交易信号。",
            "",
        ])
    if len(lines) == 2:
        lines.extend([
            "数据：本次无可直接展示的单项核心事实数据。",
            "AI看法：数据缺口降低结论置信度，不应被解释为利空。",
        ])
    return "\n".join(lines).rstrip()
