"""State and prompt adapters for the Japan data bundle.

The data service remains the only place that fetches providers.  This module
serializes its normalized output for LangGraph state and renders a compact,
source-attributed context for agents and reports.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from tradingagents.dataflows.market import Market, MarketContext
from tradingagents.presentation import chinese_status

from .official import build_official_japan_providers
from .service import JapanDataService


def collect_japan_data_bundle(context: MarketContext, trade_date: str) -> dict[str, Any]:
    """Fetch a bounded recent Japan bundle synchronously at graph-run start.

    Provider failures are represented inside ``source_statuses``.  A defensive
    outer fallback preserves that guarantee if the event loop or cache layer is
    unavailable before the service can create its normal response.
    """
    if context.market != Market.JP:
        return {}
    try:
        end_date = date.fromisoformat(str(trade_date))
        start_date = end_date - timedelta(days=7)
    except ValueError:
        return {
            "ticker": context.symbol,
            "items": [],
            "source_statuses": [{"source": "JapanData", "status": "DATA_UNAVAILABLE", "detail": "invalid trade date"}],
        }
    try:
        bundle = asyncio.run(
            JapanDataService(build_official_japan_providers()).collect(
                context, start_date=start_date.isoformat(), end_date=end_date.isoformat()
            )
        )
    except Exception as exc:  # fail closed: no unverified replacement data
        return {
            "ticker": context.symbol,
            "items": [],
            "source_statuses": [{"source": "JapanData", "status": "DATA_UNAVAILABLE", "detail": type(exc).__name__}],
        }
    return bundle.to_dict()


def render_japan_agent_context(state: Mapping[str, Any]) -> str:
    """Return a compact fact-only Japan context, or an empty string for US."""
    market = (state.get("market_context") or {}).get("market")
    bundle = state.get("japan_data_bundle") or {}
    if market != Market.JP or not bundle:
        return ""
    items = bundle.get("items") or []
    status_lines = [
        f"- {status.get('source', 'Unknown')}: {status.get('status', 'DATA_UNAVAILABLE')}"
        + (f" ({status['detail']})" if status.get("detail") else "")
        for status in bundle.get("source_statuses", [])
    ]
    official = [item for item in items if item.get("source") in {"TDnet", "Company IR", "EDINET", "J-Quants"}]
    supply = [item for item in items if item.get("source") in {"JPX", "JSF"}]
    short = [item for item in items if item.get("source_type") == "reported_short_position"]
    return "\n".join([
        "## Japan market data bundle (pre-fetched; source-attributed)",
        "This block is the only Japan-specific factual input. Treat VERIFIED FACT as fact; do not infer unavailable data.",
        "### Source status", *(status_lines or ["- DATA_UNAVAILABLE"]),
        "### Verified official disclosures", *_item_lines(official, include_metadata=False),
        "### Credit supply/demand", *_item_lines(supply, include_metadata=True),
        "### Reportable institutional short positions", *_item_lines(short, include_metadata=True),
        "### Japan sentiment and macro", "- DATA_UNAVAILABLE: no Japan sentiment or macro provider is enabled in this phase.",
        "Rules: absence of a JPX >=0.5% reported short position does not mean no short interest. JSF balances are securities-finance data, not all broker margin positions. Do not turn a disclosure, forum absence, or a single balance into a buy/sell certainty.",
    ])


def render_japan_report_sections(bundle: Mapping[str, Any] | None) -> str:
    """Render factual Japan blocks with clearly separated AI interpretation."""
    bundle = bundle or {}
    items = bundle.get("items") or []
    statuses = {entry.get("source"): entry for entry in bundle.get("source_statuses") or []}

    def block(source: str, title: str, view: str, *, metadata: bool = False) -> str:
        matching = [item for item in items if item.get("source") == source]
        status = statuses.get(source, {})
        data = "\n".join(_item_lines(matching, include_metadata=metadata))
        if not matching:
            data = _missing_source_data(source, status)
        return "\n".join([
            f"### 【{title}】",
            f"状态：{chinese_status(status.get('status'))}",
            "数据：",
            data,
            "AI看法：",
            view,
        ])

    return "\n\n".join([
        "# 日本市场核心数据",
        "## 日本官方披露",
        block("TDnet", "TDnet", "披露属于官方事实；应重点比较利润、EPS、全年指引及资本政策变化，不能只根据标题判断利多或利空。"),
        block("Company IR", "公司 IR", "公司 IR 是官方资料。若只有摘要或解析失败，应降低结论置信度，而不是补造财务结论。"),
        "## 信用与需给",
        block("JSF", "日证金", "融资与贷株数据反映证券金融层面的需给。单日变化不足以确认趋势，应与价格、成交量和多期余额联合判断。", metadata=True),
        "## 空卖与机构行为",
        block("JPX", "JPX 机构空卖", "未发现达到公开申报门槛的净空头，只代表没有 ≥0.5% 的公开申报仓位，不代表不存在其他空头。", metadata=True),
        "## 财务与持仓披露",
        block("EDINET", "EDINET", "大量保有报告可识别机构持仓变化；未配置 API Key 仅代表数据缺失，不能解释为负面信号。"),
        block("J-Quants", "J-Quants", "专业历史财务及部分市场数据缺失时，本报告会更多依赖其他可用官方来源，结论置信度应相应降低。"),
        "## 日本市场情绪",
        "状态：数据不可用\n数据：本阶段未启用日本社区情绪 Provider，未使用 Reddit 或 StockTwits 替代。\nAI看法：情绪数据缺失降低覆盖度，不构成利空或市场关注度不足。",
        "## 日股波段交易计划",
        "数据：官方披露与需给数据已注入分析师、多空研究员、交易员、风险管理团队和投资组合经理。\nAI看法：最终计划应以报告前部的入场、止损、止盈与仓位建议为准；关键数据不可用时应观望，而非补造结论。",
    ])


def _missing_source_data(source: str, status: Mapping[str, Any]) -> str:
    value = status.get("status")
    if value == "AUTH_REQUIRED":
        return f"未配置 {source} API Key 或当前账户无权限。"
    if value == "OK":
        if source == "TDnet":
            return "本分析窗口无匹配披露。"
        if source == "JPX":
            return "本窗口未发现 ≥0.5% 需公开申报的净空头仓位。"
        return "本分析窗口无匹配数据。"
    return "本次未取得可用数据。"


def _item_lines(items: list[Mapping[str, Any]], *, include_metadata: bool) -> list[str]:
    if not items:
        return ["DATA UNAVAILABLE: 本次窗口无可用匹配数据。"]
    lines = []
    for item in items[:20]:
        prefix = f"- [{item.get('source', 'Unknown')}] {item.get('timestamp', '')}: {item.get('title', '')}"
        if item.get("url"):
            prefix += f" ({item['url']})"
        if include_metadata and item.get("metadata"):
            metadata = item["metadata"]
            selected = {key: value for key, value in metadata.items() if value is not None and key not in {"coverage", "company"}}
            if selected:
                prefix += f" — {selected}"
        lines.append(prefix)
    return lines
