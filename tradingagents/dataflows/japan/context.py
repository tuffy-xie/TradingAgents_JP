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

from .official import build_official_japan_providers
from .service import JapanDataService


def collect_japan_data_bundle(
    context: MarketContext, trade_date: str, trading_horizon: str = "multi_day"
) -> dict[str, Any]:
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
            "source_statuses": [
                {
                    "source": "JapanData",
                    "status": "DATA_UNAVAILABLE",
                    "detail": "invalid trade date",
                }
            ],
        }
    try:
        official_days = _official_event_days(trading_horizon)
        official_start_date = end_date - timedelta(days=official_days)
        bundle = asyncio.run(
            JapanDataService(build_official_japan_providers()).collect(
                context,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                provider_start_dates={
                    "TDnet": official_start_date.isoformat(),
                    "Company IR": official_start_date.isoformat(),
                },
            )
        )
    except Exception as exc:  # fail closed: no unverified replacement data
        return {
            "ticker": context.symbol,
            "items": [],
            "source_statuses": [
                {"source": "JapanData", "status": "DATA_UNAVAILABLE", "detail": type(exc).__name__}
            ],
        }
    raw = bundle.to_dict()
    raw.update(
        {
            "analysis_date": end_date.isoformat(),
            "trading_horizon": trading_horizon,
            "window_policy": {
                "official_catalyst_days": official_days,
                "news_days": 7,
                "sentiment_days": 7,
                "supply_demand_days": 20,
                "analyst_revision_windows": [30, 90],
            },
        }
    )
    return raw


def _official_event_days(trading_horizon: str) -> int:
    """Reuse the existing UI horizon names; only official events get widened."""
    return {
        "intraday": 7,
        "multi_day": 14,
        "multi_week": 30,
        "long_term": 45,
    }.get(trading_horizon, 14)


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
    official = [
        item
        for item in items
        if item.get("source") in {"TDnet", "Company IR", "EDINET", "J-Quants"}
    ]
    supply = [item for item in items if item.get("source") in {"JPX", "JSF"}]
    short = [item for item in items if item.get("source_type") == "reported_short_position"]
    macro = [item for item in items if item.get("source") == "Japan Macro"]
    expectations = [
        item for item in items if item.get("source_type") == "japan_analyst_expectations"
    ]
    sentiment = [item for item in items if item.get("layer") == "MARKET_SENTIMENT"]
    governance = [item for item in items if item.get("source_type") == "source_of_truth_assessment"]
    return "\n".join(
        [
            "## Japan market data bundle (pre-fetched; source-attributed)",
            "This block is the only Japan-specific factual input. Treat VERIFIED FACT as fact; do not infer unavailable data.",
            "### Source status",
            *(status_lines or ["- DATA_UNAVAILABLE"]),
            "### Verified official disclosures",
            *_item_lines(official, include_metadata=False),
            "### Credit supply/demand",
            *_item_lines(supply, include_metadata=True),
            "### Reportable institutional short positions",
            *_item_lines(short, include_metadata=True),
            "### Japan macro and cross-market context",
            *_item_lines(macro, include_metadata=True),
            "### Analyst consensus (separate from company guidance)",
            *_item_lines(expectations, include_metadata=True),
            "### Japan sentiment",
            *_item_lines(sentiment, include_metadata=True),
            "### Source-of-truth and conflict controls",
            *_item_lines(governance, include_metadata=True),
            "Rules: exact price/OHLCV/technical numbers may only be copied from Verified Market Snapshot. Company guidance and analyst consensus are different bases, not conflicts. Do not calculate across FY/H1/Q1/Q2/Q3/Q4/TTM/Forecast/Analyst Estimate. JSF units and dates must be quoted unchanged. If upstream evidence lacks an exact number, mark it unavailable; label any conclusion as AI inference. Absence of a JPX >=0.5% reported short position does not mean no short interest.",
        ]
    )


def render_japan_report_sections(bundle: Mapping[str, Any] | None) -> str:
    """Render deterministic JP-only report sections without LLM invention."""
    bundle = bundle or {}
    items = bundle.get("items") or []
    official = [
        item
        for item in items
        if item.get("source") in {"TDnet", "Company IR", "EDINET", "J-Quants"}
    ]
    supply = [item for item in items if item.get("source") in {"JPX", "JSF"}]
    short = [item for item in items if item.get("source_type") == "reported_short_position"]
    statuses = bundle.get("source_statuses") or []
    source_status = (
        "\n".join(
            f"- {entry.get('source', 'Unknown')}: {entry.get('status', 'DATA_UNAVAILABLE')}"
            + (f" — {entry['detail']}" if entry.get("detail") else "")
            for entry in statuses
        )
        or "- DATA_UNAVAILABLE"
    )
    return "\n\n".join(
        [
            "## 日本官方披露\n" + "\n".join(_item_lines(official, include_metadata=False)),
            "## 信用与需给\n" + "\n".join(_item_lines(supply, include_metadata=True)),
            "## 空卖与机构行为\n"
            + (
                "\n".join(_item_lines(short, include_metadata=True))
                if short
                else "DATA UNAVAILABLE: 未发现当前公开文件中的 ≥0.5% 申报空卖仓位；这不代表不存在其他空头。"
            ),
            "## 日本市场情绪\n暂无可用日本情绪数据。",
            "## 日股波段交易计划\n以上官方披露与需给数据已注入 Analyst、Bull/Bear、Trader、Risk Manager 和 Portfolio Manager。交易计划以最终决策中的入场、止损、止盈及仓位上限为准；若关键数据不可用，应保持观望而非补造结论。",
            "### Japan data source status\n" + source_status,
        ]
    )


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
            selected = {
                key: value
                for key, value in metadata.items()
                if value is not None and key not in {"coverage", "company"}
            }
            if selected:
                prefix += f" — {selected}"
        lines.append(prefix)
    return lines
