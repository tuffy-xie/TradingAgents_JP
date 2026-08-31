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
        financial_official_scan_days = max(31, official_days)
        official_start_date = end_date - timedelta(days=financial_official_scan_days - 1)
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
                "financial_official_scan_days": financial_official_scan_days,
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
    """Return research facts for JP agents, excluding provider diagnostics."""
    market = (state.get("market_context") or {}).get("market")
    bundle = state.get("japan_data_bundle") or {}
    if market != Market.JP or not bundle:
        return ""
    items = bundle.get("items") or []
    official = [
        item
        for item in items
        if item.get("source") in {"TDnet", "EDINET"}
        or (
            item.get("source") == "Company IR"
            and (item.get("metadata") or {}).get("freshness_status")
            != "FRESHNESS_UNVERIFIED"
        )
        or (
            item.get("source") == "J-Quants"
            and item.get("source_type")
            in {"official_ohlcv", "official_security_master"}
        )
    ]
    supply = [item for item in items if item.get("source") in {"JPX", "JSF"}]
    short = [item for item in items if item.get("source_type") == "reported_short_position"]
    macro = [item for item in items if item.get("source") == "Japan Macro"]
    expectations = [
        item for item in items if item.get("source_type") == "japan_analyst_expectations"
    ]
    sentiment = [item for item in items if item.get("layer") == "MARKET_SENTIMENT"]
    return "\n".join(
        [
            "## Japan market data bundle (pre-fetched; source-attributed)",
            "Use the dated research facts below as supplemental evidence. Treat VERIFIED FACT as fact; do not infer unavailable data.",
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
            "### Research controls",
            "Use valid exact price/OHLCV/technical numbers returned by the Market Analyst get_stock_data/get_indicators tools. Company guidance and analyst consensus are different bases, not conflicts. Do not calculate across FY/H1/Q1/Q2/Q3/Q4/TTM/Forecast/Analyst Estimate. JSF units and dates must be quoted unchanged. If a requested upstream research field is absent, mark that field unavailable; label conclusions as AI inference. Absence of a JPX >=0.5% reported short position does not mean no short interest.",
        ]
    )


def render_japan_financial_context(state: Mapping[str, Any]) -> str:
    """Render authority-assessed financial facts for the Fundamentals Analyst only.

    The document is stored outside ``items``, so the shared Japan renderer used
    by downstream agents cannot expose it. Operational provider diagnostics and
    raw EDINET DB payloads are intentionally excluded.
    """
    market = (state.get("market_context") or {}).get("market")
    bundle = state.get("japan_data_bundle") or {}
    if market != Market.JP or not bundle:
        return ""
    provider_metadata = bundle.get("provider_metadata") or {}
    assessment = provider_metadata.get("Japan Financial Authority") or {}
    if not isinstance(assessment, Mapping) or not assessment:
        return ""
    lines = [
        "## Japan financial authority assessment (Fundamentals Analyst only)",
        f"- Analysis as of: {assessment.get('analysis_as_of') or 'DATA_UNAVAILABLE'}",
    ]
    coverage = assessment.get("official_coverage")
    if isinstance(coverage, Mapping):
        lines.extend(
            [
                f"- Official freshness coverage: {coverage.get('status') or 'INCOMPLETE'}",
                f"- Official coverage source: {', '.join(coverage.get('sources') or []) or 'DATA_UNAVAILABLE'}",
                f"- Official coverage basis: {coverage.get('reason') or 'DATA_UNAVAILABLE'}",
            ]
        )
    lines.append("### Latest Actual")
    lines.extend(_financial_assessment_lines(assessment.get("actual"), guidance=False))

    lines.append("### Current Company Guidance")
    lines.extend(_financial_assessment_lines(assessment.get("guidance"), guidance=True))
    return "\n".join(lines)


def _financial_assessment_lines(value: Any, *, guidance: bool) -> list[str]:
    if not isinstance(value, Mapping):
        return ["- Status: DATA_UNAVAILABLE", "- Freshness: FRESHNESS_UNVERIFIED"]
    status = str(value.get("status") or "DATA_UNAVAILABLE")
    lines = [
        f"- Status: {status}",
        f"- Freshness: {value.get('freshness') or 'FRESHNESS_UNVERIFIED'}",
    ]
    document = value.get("document")
    if not isinstance(document, Mapping) or status != "OK":
        update = value.get("official_latest_update")
        if isinstance(update, Mapping):
            lines.append(
                f"- Latest official financial update: {update.get('disclosure_date') or 'DATA_UNAVAILABLE'}"
            )
            titles = update.get("titles") or []
            if titles:
                lines.append(f"- Official update title: {titles[0]}")
        if status == "NOT_PROVIDED":
            lines.append("- Company Guidance: NOT_PROVIDED")
        else:
            lines.append("- Normalized critical values: DATA_UNAVAILABLE")
        lines.append(f"- Assessment: {value.get('detail') or 'DATA_UNAVAILABLE'}")
        return lines

    records = document.get("records") or []
    expected = "GUIDANCE" if guidance else "ACTUAL"
    record = next(
        (
            item
            for item in records
            if isinstance(item, Mapping) and item.get("record_type") == expected
        ),
        None,
    )
    if not isinstance(record, Mapping):
        lines.append("- Normalized critical values: DATA_UNAVAILABLE")
        return lines
    lines.extend(_financial_record_lines(record, guidance=guidance))
    lines.extend(
        [
            f"- Source: {document.get('source') or 'DATA_UNAVAILABLE'}",
            f"- Source type: {document.get('source_type') or 'UNKNOWN'}",
            f"- Selected disclosure date: {value.get('selected_disclosure_date') or 'DATA_UNAVAILABLE'}",
            f"- Source record ID: {document.get('source_record_id') or 'DATA_UNAVAILABLE'}",
            f"- Source as-of: {document.get('source_as_of') or 'DATA_UNAVAILABLE'}",
        ]
    )
    if document.get("source_url"):
        lines.append(f"- Source URL: {document['source_url']}")
    if value.get("conflict"):
        lines.append(f"- Source conflict: {value['conflict']}")
    return lines


def _financial_record_lines(record: Mapping[str, Any], *, guidance: bool) -> list[str]:
    lines = [
        f"- Fiscal period: {record.get('period_type') or 'DATA_UNAVAILABLE'}",
        f"- Target period end: {record.get('target_period_end') or 'DATA_UNAVAILABLE'}",
        f"- Accounting standard: {record.get('accounting_standard') or 'UNKNOWN'}",
        f"- Scope: {record.get('scope') or 'UNKNOWN'}",
    ]
    labels = (
        ("revenue", "Revenue"),
        ("operating_profit", "Operating profit"),
        ("ordinary_profit", "Ordinary profit"),
        ("net_income", "Net income (parent attributable)"),
        ("profit_total", "Total period profit"),
        ("eps", "EPS"),
    )
    metrics = record.get("metrics") or {}
    for key, label in labels:
        metric = metrics.get(key)
        lines.append(f"- {label}: {_render_financial_metric(metric, guidance=guidance)}")
    return lines


def _render_financial_metric(value: Any, *, guidance: bool) -> str:
    if not isinstance(value, Mapping):
        return "NOT_PROVIDED"
    metric = value.get("current_value") if guidance else value
    if not isinstance(metric, Mapping):
        status = value.get("status")
        return str(status or "NOT_PROVIDED")
    status = str(metric.get("status") or value.get("status") or "DATA_UNAVAILABLE")
    if status != "OK":
        return status
    amount = metric.get("value")
    if amount is None:
        return "DATA_UNAVAILABLE"
    unit = metric.get("unit") or value.get("unit")
    return f"{amount} {unit}" if unit else f"{amount} (unit: DATA_UNAVAILABLE)"


def render_japan_provider_diagnostics(state: Mapping[str, Any]) -> str:
    """Render provider and snapshot diagnostics for internal logs, not prompts."""
    market = (state.get("market_context") or {}).get("market")
    bundle = state.get("japan_data_bundle") or {}
    if market != Market.JP or not bundle:
        return ""
    lines = ["## Japan provider diagnostics"]
    statuses = bundle.get("source_statuses") or []
    lines.extend(
        f"- {status.get('source', 'Unknown')}: {status.get('status', 'DATA_UNAVAILABLE')}"
        + (f" ({status['detail']})" if status.get("detail") else "")
        for status in statuses
    )
    snapshot = state.get("verified_market_snapshot")
    if snapshot:
        lines.extend(["### Verified market snapshot diagnostic", str(snapshot)])
    return "\n".join(lines)


def render_japan_report_sections(bundle: Mapping[str, Any] | None) -> str:
    """Render deterministic JP-only report sections without LLM invention."""
    bundle = bundle or {}
    items = bundle.get("items") or []
    official = [
        item
        for item in items
        if item.get("source") in {"TDnet", "EDINET"}
        or (
            item.get("source") == "Company IR"
            and (item.get("metadata") or {}).get("freshness_status")
            != "FRESHNESS_UNVERIFIED"
        )
        or (
            item.get("source") == "J-Quants"
            and item.get("source_type")
            in {"official_ohlcv", "official_security_master"}
        )
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
            "## 日股波段交易计划\n以上官方披露与需给数据作为补充上下文，最终交易计划由各 Agent 结合 Analyst 报告综合形成；若关键数据不可用，应保持观望而非补造结论。",
            "### Japan data source status\n" + source_status,
        ]
    )


def _item_lines(items: list[Mapping[str, Any]], *, include_metadata: bool) -> list[str]:
    if not items:
        return ["DATA UNAVAILABLE: 本次窗口无可用匹配数据。"]
    lines = []
    for item in items[:20]:
        prefix = f"- [{item.get('source', 'Unknown')}] {item.get('timestamp', '')}: {item.get('title', '')}"
        if item.get("status") and item.get("status") != "OK":
            prefix += f" [status={item['status']}]"
        if item.get("url"):
            prefix += f" ({item['url']})"
        if include_metadata and item.get("metadata"):
            metadata = item["metadata"]
            selected = {
                key: value
                for key, value in metadata.items()
                if value is not None and key not in {"coverage", "company"}
            }
            selected = _research_metadata(selected)
            if selected:
                prefix += f" — {selected}"
        lines.append(prefix)
    return lines


_DIAGNOSTIC_KEYS = {
    "cache",
    "cache_hit",
    "from_cache",
    "fallback",
    "fallback_level",
    "retry",
    "retries",
    "provider_status",
    "source_statuses",
    "debug",
    "diagnostics",
}


def _research_metadata(value: Any) -> Any:
    """Strip operational metadata while preserving normalized research fields."""
    if isinstance(value, Mapping):
        result = {}
        for key, nested in value.items():
            key_text = str(key).lower()
            if key_text in _DIAGNOSTIC_KEYS or "snapshot" in key_text:
                continue
            cleaned = _research_metadata(nested)
            if cleaned is not None:
                result[key] = cleaned
        return result
    if isinstance(value, list):
        return [_research_metadata(item) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if any(term in lowered for term in ("verified market snapshot", "http 4", "cache hit", "retry")):
            return None
    return value
