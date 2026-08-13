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
    "Company IR": "公司投资者关系（IR）",
    "JSF": "日证金（JSF）",
    "JPX": "东京证券交易所（JPX）",
    "TDnet": "适时开示（TDnet）",
    "EDINET": "电子披露系统（EDINET）",
    "J-Quants": "日本交易所数据（J-Quants）",
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

_SOURCE_IMPACT = {"TDnet": 100, "Company IR": 92, "JSF": 88, "JPX": 82, "EDINET": 76, "J-Quants": 72}

_TITLE_TRANSLATIONS = {
    "Company IR public landing page": "公司 IR 信息页",
    "JSF financing and stock-loan balance": "日证金融资 / 贷株余额",
    "JSF premium charge (逆日歩)": "日证金品贷料（逆日步）",
}


def chinese_status(value: str | None) -> str:
    return _STATUS.get(str(value or ""), "数据不可用")


def source_display_name(value: str | None) -> str:
    """Return the Chinese presentation name for a data source."""
    return _SOURCE_NAMES.get(str(value or ""), str(value or "数据源"))


def render_japan_item_data(item: Mapping[str, Any], *, include_metadata: bool = True) -> str:
    """Render a normalized Japan item without Python dict syntax or English labels."""
    source = _SOURCE_NAMES.get(str(item.get("source") or ""), str(item.get("source") or "数据源"))
    date = str(item.get("timestamp") or "")[:10] or "日期未提供"
    title = _TITLE_TRANSLATIONS.get(str(item.get("title") or ""), str(item.get("title") or "本窗口数据"))
    if str(item.get("source")) == "Company IR":
        doc_type = str((item.get("metadata") or {}).get("document_type") or "")
        title = {"earnings_release": "公司最新业绩公告", "earnings_presentation": "公司业绩说明资料"}.get(doc_type, "公司最新投资者关系文件")
    lines = [f"- 来源：{source}｜日期：{date}｜事项：{title}"]
    metadata = item.get("metadata") or {}
    if include_metadata and isinstance(metadata, Mapping):
        if str(item.get("source")) == "JSF":
            rows = [
                ("申込日", metadata.get("application_date")), ("决済日", metadata.get("settlement_date")),
                ("融资新规", metadata.get("finance_new_shares")), ("融资偿还", metadata.get("finance_repaid_shares")),
                ("融资余额", metadata.get("finance_balance_shares")), ("贷株新规", metadata.get("stock_loan_new_shares")),
                ("贷株偿还", metadata.get("stock_loan_repaid_shares")), ("贷株余额", metadata.get("stock_loan_balance_shares")),
                ("差引余额", metadata.get("net_balance_shares")),
            ]
            values = []
            for label, value in rows:
                if value is None:
                    continue
                suffix = " 股" if label not in {"申込日", "决済日"} else ""
                values.append(f"{label}：{value:,.0f}{suffix}" if isinstance(value, (int, float)) else f"{label}：{value}")
            if values:
                lines.append("  " + " / ".join(values))
        elif metadata.get("extraction_status"):
            extraction = {"pdf_text": "PDF 正文已解析", "html_text": "网页正文已解析", "pdf_parse_failed": "PDF 解析失败", "landing_page_only": "仅信息页"}.get(str(metadata["extraction_status"]), "已解析")
            lines.append(f"  文档状态：{extraction}")
    return "\n".join(lines)


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
    identity = state.get("instrument_identity") or {}
    if isinstance(identity, Mapping):
        name = identity.get("company_name") or identity.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    context = str(state.get("instrument_context") or "")
    match = re.search(r"(?:Company|公司)[：:]\s*([^;\.\n]+)", context, re.I)
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
    """Show exactly five high-impact, source-attributed facts or data gaps."""
    bundle = state.get("japan_data_bundle") or {}
    items = bundle.get("items") or []
    lines = ["# 本次分析最重要的 5 条数据", ""]
    candidates_by_source: dict[str, tuple[int, str, Mapping[str, Any] | None, Mapping[str, Any] | None]] = {}
    for item in items:
        source = str(item.get("source") or "")
        importance = int((item.get("metadata") or {}).get("importance") or 0)
        candidate = (_SOURCE_IMPACT.get(source, 50) + importance + 1, source, item, None)
        if candidate[0] > candidates_by_source.get(source, (-1, "", None, None))[0]:
            candidates_by_source[source] = candidate
    for status in bundle.get("source_statuses") or []:
        source = str(status.get("source") or "")
        candidate = (_SOURCE_IMPACT.get(source, 40), source, None, status)
        if candidate[0] > candidates_by_source.get(source, (-1, "", None, None))[0]:
            candidates_by_source[source] = candidate
    candidates = list(candidates_by_source.values())
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    selected = candidates[:5]
    while len(selected) < 5:
        selected.append((0, "数据覆盖", None, {"status": "DATA_UNAVAILABLE"}))
    for index, (_impact, source, item, status) in enumerate(selected, start=1):
        source_label = _SOURCE_NAMES.get(source, source or "数据覆盖")
        if item:
            data = render_japan_item_data(item)
            view = _key_data_view(source, item)
        else:
            current = str((status or {}).get("status") or "DATA_UNAVAILABLE")
            data = f"- 来源：{source_label}｜状态：{chinese_status(current)}"
            view = _status_data_view(source, current)
        lines.extend([
            f"## {index}. {source_label}",
            "数据：",
            data,
            "AI看法：",
            view,
            "",
        ])
    return "\n".join(lines).rstrip()


def _key_data_view(source: str, item: Mapping[str, Any]) -> str:
    if source == "JSF":
        return "日证金数据反映证券金融层面的需给；需结合多日变化、价格和成交量判断，单日读数不能单独构成交易信号。"
    if source == "JPX":
        return "公开申报空卖只覆盖达到门槛的仓位；即使无匹配记录，也不能推导为不存在空头。"
    if source in {"TDnet", "Company IR", "EDINET", "J-Quants"}:
        return "该项属于官方披露或官方数据，应优先于媒体与社区观点；仍需与其他周期一致的数据交叉验证。"
    return "该项已进入研究上下文；需与技术面、基本面和风险条件交叉验证，不能单独构成交易信号。"


def _status_data_view(source: str, status: str) -> str:
    if status == "OK" and source == "TDnet":
        return "本窗口没有匹配的新增适时开示，不构成利好或利空。"
    if status == "OK" and source == "JPX":
        return "未发现达到公开申报门槛的净空头；这不代表不存在其他空头。"
    return "该项为数据缺口，会降低相关判断的置信度；不能解释为利空、利多或市场缺乏关注。"
