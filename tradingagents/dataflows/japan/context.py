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

_OFFICIAL_CATALYST_LOOKBACK_DAYS = 14
_FINANCIAL_OFFICIAL_SCAN_DAYS = 32


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
            "source_statuses": [
                {
                    "source": "JapanData",
                    "status": "DATA_UNAVAILABLE",
                    "detail": "invalid trade date",
                }
            ],
        }
    try:
        # Keep the latest month-end disclosure itself inside the verified
        # official window for every 31-day analysis interval.  A 31-day
        # inclusive window can begin one day after a July 31 disclosure on an
        # August 31 run, leaving same-day official updates unverified.
        financial_official_scan_days = _FINANCIAL_OFFICIAL_SCAN_DAYS
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
            "window_policy": {
                # Source/evidence lookbacks are production data contracts, not
                # a user-selected investment horizon.
                "official_catalyst_days": _OFFICIAL_CATALYST_LOOKBACK_DAYS,
                "financial_official_scan_days": financial_official_scan_days,
                "news_days": 7,
                "sentiment_days": 7,
                "supply_demand_days": 20,
                "analyst_revision_windows": [30, 90],
            },
        }
    )
    return raw


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
            and (item.get("metadata") or {}).get("freshness_status") != "FRESHNESS_UNVERIFIED"
        )
        or (
            item.get("source") == "J-Quants"
            and item.get("source_type") in {"official_ohlcv", "official_security_master"}
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


def render_japan_audience_context(state: Mapping[str, Any], audience: str) -> str:
    """Render provider facts only for the owning JP Analyst domain.

    Downstream debate/decision agents use analyst reports plus evidence
    continuity instead of receiving the same raw bundle a second time.
    """
    market = (state.get("market_context") or {}).get("market")
    bundle = state.get("japan_data_bundle") or {}
    if market != Market.JP or not bundle:
        return ""
    items = [item for item in bundle.get("items") or [] if isinstance(item, Mapping)]
    audience = audience.upper()
    if audience == "MARKET":
        selected = [
            item
            for item in items
            if item.get("source") in {"JPX", "JSF", "J-Quants", "Japan Macro"}
            and item.get("layer") != "MARKET_SENTIMENT"
        ]
        controls = (
            "Use dated supply/short observations only at their stated freshness. "
            "STALE_SOURCE is historical background, never current directional evidence. "
            "No reportable JPX row does not mean no shorts."
        )
    elif audience == "NEWS":
        selected = [
            item
            for item in items
            if (
                item.get("source") in {"TDnet", "Company IR", "Japan Macro"}
                or item.get("source_type") == "japan_stock_news"
            )
        ]
        selected = [
            item for item in selected if item.get("source_type") != "japan_analyst_expectations"
        ]
        controls = "Keep source and publication date with every factual event. Exclude future or unverified events."
    elif audience == "FUNDAMENTALS":
        selected = [
            item
            for item in items
            if item.get("source_type") == "japan_analyst_expectations"
            or item.get("source") in {"TDnet", "Company IR"}
        ]
        controls = (
            "COMPANY_GUIDANCE, ANALYST_CONSENSUS, and VENDOR_FORWARD_ESTIMATE are separate semantic types. "
            "The dedicated financial authority section controls current-quarter conclusions."
        )
    elif audience == "SENTIMENT":
        selected = [item for item in items if item.get("layer") == "MARKET_SENTIMENT"]
        controls = (
            "Use only real investor/social sentiment samples here. Official filings, analyst consensus, "
            "credit balances, macro data, and ordinary news are not sentiment substitutes."
        )
    else:
        return ""
    return "\n".join(
        [
            f"## Japan {audience.lower()} context (pre-fetched; source-attributed)",
            *_item_lines(selected, include_metadata=True),
            "### Domain controls",
            controls,
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


def render_japan_financial_report(state: Mapping[str, Any]) -> str:
    """Render current financial authority as stable user-facing Markdown.

    Current Actual and Company Guidance are rendered from the canonical
    authority assessment rather than copied back from an LLM report. Historical
    statements and vendor estimates remain the Fundamentals Analyst's domain.
    """
    bundle = state.get("japan_data_bundle") or {}
    assessment = (bundle.get("provider_metadata") or {}).get("Japan Financial Authority")
    if not isinstance(assessment, Mapping):
        return ""
    lines = ["## 当前财务权威摘要"]
    lines.extend(_financial_report_section("最新实际业绩", assessment.get("actual"), False))
    lines.extend(_financial_report_section("当前公司业绩指引", assessment.get("guidance"), True))
    coverage = assessment.get("official_coverage")
    if isinstance(coverage, Mapping):
        status = _public_financial_status(coverage.get("status"))
        sources = "、".join(str(value) for value in coverage.get("sources") or [])
        lines.extend(
            [
                "### 时效与来源",
                f"- 官方披露覆盖：{status}",
                f"- 覆盖来源：{sources or '未提供'}",
                f"- 分析截止日：{assessment.get('analysis_as_of') or '未提供'}",
            ]
        )
    return "\n".join(lines)


def _financial_report_section(title: str, value: Any, guidance: bool) -> list[str]:
    lines = [f"### {title}"]
    if not isinstance(value, Mapping):
        return [*lines, "本次没有可核验的结构化数据。"]
    status = str(value.get("status") or "DATA_UNAVAILABLE")
    document = value.get("document")
    expected = "GUIDANCE" if guidance else "ACTUAL"
    record = None
    if isinstance(document, Mapping):
        record = next(
            (
                row
                for row in document.get("records") or []
                if isinstance(row, Mapping) and row.get("record_type") == expected
            ),
            None,
        )
    if status != "OK" or not isinstance(record, Mapping):
        if status == "NOT_PROVIDED":
            return [*lines, "公司未提供该项指引。"]
        return [*lines, "当前数据证据不足，未作为已确认的最新值展示。"]

    gate = value.get("critical_gate") or {}
    lines.extend(
        [
            f"- 披露日：{value.get('selected_disclosure_date') or '未提供'}",
            f"- 期间：{record.get('period_type') or '未提供'}",
            f"- 目标期末：{record.get('target_period_end') or '未提供'}",
            f"- 会计准则：{record.get('accounting_standard') or '未提供'}",
            f"- 范围：{_public_scope(record.get('scope'))}",
            f"- 关键数据门控：{_public_financial_status(gate.get('status'))}",
            "",
            "| 指标 | 数值 |",
            "|---|---:|",
        ]
    )
    labels = (
        ("revenue", "营收"),
        ("operating_profit", "营业利润"),
        ("ordinary_profit", "经常利润"),
        ("net_income", "归属于母公司所有者的净利润"),
        ("profit_total", "当期总利润"),
        ("eps", "每股收益（EPS）"),
    )
    metrics = record.get("metrics") or {}
    for key, label in labels:
        lines.append(
            f"| {label} | {_public_financial_metric(metrics.get(key), guidance=guidance)} |"
        )
    source = document.get("source") or "未提供"
    source_role = (
        "结构化补充来源" if document.get("source_type") == "STRUCTURED_SOURCE" else "官方披露来源"
    )
    lines.extend(
        [
            "",
            f"- 数据来源：{source}（{source_role}）",
            f"- 时效：{_public_financial_status(value.get('freshness'))}",
        ]
    )
    return lines


def _public_financial_metric(value: Any, *, guidance: bool) -> str:
    if not isinstance(value, Mapping):
        return "公司未提供"
    metric = value.get("current_value") if guidance else value
    if not isinstance(metric, Mapping):
        return _public_financial_status(value.get("status"))
    status = str(metric.get("status") or value.get("status") or "DATA_UNAVAILABLE")
    if status != "OK":
        return _public_financial_status(status)
    amount = metric.get("value")
    if amount is None:
        return "数据不可用"
    unit = metric.get("unit") or value.get("unit")
    display_amount: Any = amount
    if isinstance(amount, float) and amount.is_integer():
        display_amount = int(amount)
    return f"{display_amount} {unit}" if unit else f"{display_amount}（单位未提供）"


def _public_financial_status(value: Any) -> str:
    return {
        "OK": "通过",
        "COMPLETE": "完整",
        "CURRENT_STRUCTURED_CONFIRMED": "截至分析日已确认最新",
        "CURRENT_OFFICIAL": "截至分析日最新官方数据",
        "LATEST_AVAILABLE": "来源当前最新可得",
        "INCOMPLETE": "不完整",
        "NOT_APPLICABLE": "不适用",
        "NOT_PROVIDED": "公司未提供",
        "INSUFFICIENT_DATA": "证据不足",
        "DATA_UNAVAILABLE": "数据不可用",
        "FRESHNESS_UNVERIFIED": "新鲜度未确认",
    }.get(str(value or ""), "未提供")


def _public_scope(value: Any) -> str:
    return {
        "CONSOLIDATED": "合并",
        "NON_CONSOLIDATED": "非合并",
        "UNKNOWN": "未提供",
    }.get(str(value or "UNKNOWN"), str(value))


def _financial_assessment_lines(value: Any, *, guidance: bool) -> list[str]:
    if not isinstance(value, Mapping):
        return ["- Status: DATA_UNAVAILABLE", "- Freshness: FRESHNESS_UNVERIFIED"]
    status = str(value.get("status") or "DATA_UNAVAILABLE")
    lines = [
        f"- Status: {status}",
        f"- Freshness: {value.get('freshness') or 'FRESHNESS_UNVERIFIED'}",
    ]
    gate = value.get("critical_gate")
    if isinstance(gate, Mapping):
        lines.extend(
            [
                f"- Critical field completeness: {gate.get('status') or 'INSUFFICIENT_DATA'}",
                f"- Critical fields: {gate.get('critical_fields') or {}}",
            ]
        )
    decision_eligible = bool(
        status == "OK" and isinstance(gate, Mapping) and gate.get("status") == "OK"
    )
    lines.append(
        "- Current-use eligibility: OK"
        if decision_eligible
        else "- Current-use eligibility: INSUFFICIENT_DATA"
    )
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
            and (item.get("metadata") or {}).get("freshness_status") != "FRESHNESS_UNVERIFIED"
        )
        or (
            item.get("source") == "J-Quants"
            and item.get("source_type") in {"official_ohlcv", "official_security_master"}
        )
    ]
    supply = [item for item in items if item.get("source") in {"JPX", "JSF"}]
    short = [item for item in items if item.get("source_type") == "reported_short_position"]
    return "\n\n".join(
        [
            "## 日本官方披露\n" + "\n".join(_official_report_lines(official)),
            "## 信用与供需\n" + "\n".join(_supply_report_lines(supply)),
            "## 空卖与机构行为\n"
            + (
                "\n".join(_short_report_lines(short))
                if short
                else "数据不可用：未发现当前公开文件中的 ≥0.5% 申报空卖仓位；这不代表不存在其他空头。"
            ),
            "## 日本市场情绪\n" + "\n".join(_sentiment_report_lines(items)),
            "## 日本市场补充资料\n以上官方披露与供需数据作为补充上下文，最终研究结论由各 Analyst 报告综合形成；若关键数据不可用，应降低结论置信度，不得补造事实。",
        ]
    )


def _official_report_lines(items: list[Mapping[str, Any]]) -> list[str]:
    if not items:
        return ["本次窗口没有可核验的官方披露。"]
    lines = []
    for item in sorted(items, key=_item_timestamp_sort_key, reverse=True)[:20]:
        date_text = str(item.get("timestamp") or "")[:10]
        line = f"- [{item.get('source', '未提供')}] {date_text}：{item.get('title') or '公开披露'}"
        if item.get("url"):
            line += f"（{item['url']}）"
        status = item.get("status")
        if status and status != "OK":
            line += f"；状态：{_public_financial_status(status)}"
        lines.append(line)
    return lines


def _supply_report_lines(items: list[Mapping[str, Any]]) -> list[str]:
    """Expose dated observable balances without raw metadata or short inference."""
    if not items:
        return ["本次窗口没有可用的信用与供需数据。"]
    lines: list[str] = []
    for source in ("JSF", "JPX"):
        candidates = [item for item in items if item.get("source") == source]
        if source == "JSF":
            candidates = [
                item
                for item in candidates
                if item.get("source_type") == "securities_finance_balance"
            ]
        if not candidates:
            continue
        item = max(candidates, key=_item_timestamp_sort_key)
        metadata = item.get("metadata") or {}
        date_text = metadata.get("data_date") or str(item.get("timestamp") or "")[:10]
        if source == "JSF":
            lines.append(f"- JSF 观察日：{date_text}")
            for key, label in (
                ("finance_balance", "融资余额"),
                ("stock_loan_balance", "贷株余额"),
                ("net_balance", "差引余额"),
            ):
                if metadata.get(key) is not None:
                    lines.append(
                        f"  - {label}：{metadata[key]:g} {metadata.get('unit') or ''}".rstrip()
                    )
            publication = metadata.get("publication_status")
            if publication == "PRELIMINARY":
                lines.append("  - 发布状态：速報")
            elif publication == "CONFIRMED":
                lines.append("  - 发布状态：確報")
            lines.append("  - 语义边界：以上仅是 JSF 可观察余额，不代表全市场空头总量或机构立场。")
        else:
            lines.append(f"- JPX：{date_text}，{item.get('title') or '公开数据'}")
    return lines or ["本次窗口没有可用的信用与供需数据。"]


def _short_report_lines(items: list[Mapping[str, Any]]) -> list[str]:
    return [
        f"- [{item.get('source', 'JPX')}] {str(item.get('timestamp') or '')[:10]}："
        f"{item.get('title') or '达到披露门槛的公开申报仓位'}"
        for item in sorted(items, key=_item_timestamp_sort_key, reverse=True)[:10]
    ]


def _sentiment_report_lines(items: list[Mapping[str, Any]]) -> list[str]:
    aggregates = [
        item
        for item in items
        if item.get("source_type") == "japan_investor_sentiment_aggregate"
        and item.get("status") == "OK"
        and isinstance(item.get("metadata"), Mapping)
    ]
    if not aggregates:
        return ["本次没有可用的日本投资者/社交情绪样本。"]
    latest = max(aggregates, key=_item_timestamp_sort_key)
    metadata = latest["metadata"]
    sample_count = metadata.get("sample_count")
    score = metadata.get("sentiment_score")
    if (
        not isinstance(sample_count, int)
        or sample_count <= 0
        or not isinstance(score, (int, float))
    ):
        return ["本次没有可用的日本投资者/社交情绪样本。"]
    band = _sentiment_band(float(score))
    confidence = _sentiment_confidence(metadata.get("confidence"), sample_count)
    return [
        f"- 观察：{band}（样本 {sample_count} 条，置信度 {confidence}）",
        f"- 样本构成：正面 {metadata.get('positive_count', 0)} / "
        f"中性 {metadata.get('neutral_count', 0)} / 负面 {metadata.get('negative_count', 0)}",
        "- 说明：仅投资者/社交样本决定本项方向；新闻和宏观信息不参与情绪评分。",
    ]


def render_japan_sentiment_report(bundle: Mapping[str, Any] | None) -> str:
    """Render the sole user-facing JP sentiment aggregate from source data.

    The Sentiment Analyst still reasons over the source context and its raw
    narrative remains in ``full_agent_log``.  The published run-level band,
    score, confidence and counts come only from the canonical investor/social
    aggregate so an LLM cannot introduce a second authority.
    """
    items = [
        item
        for item in (bundle or {}).get("items", [])
        if isinstance(item, Mapping)
        and item.get("source_type") == "japan_investor_sentiment_aggregate"
        and item.get("status") == "OK"
        and isinstance(item.get("metadata"), Mapping)
    ]
    if not items:
        return "## 日本投资者与社交情绪\n\n本次没有可用的投资者或社交情绪样本。"
    latest = max(items, key=_item_timestamp_sort_key)
    metadata = latest["metadata"]
    sample_count = metadata.get("sample_count")
    score = metadata.get("sentiment_score")
    if (
        not isinstance(sample_count, int)
        or sample_count <= 0
        or not isinstance(score, (int, float))
    ):
        return "## 日本投资者与社交情绪\n\n本次没有可用的投资者或社交情绪样本。"

    lines = [
        "## 日本投资者与社交情绪",
        f"- 综合方向：{_sentiment_band(float(score))}",
        f"- 来源原生分数：{float(score):.4g}",
        f"- 置信度：{_sentiment_confidence(metadata.get('confidence'), sample_count)}",
        f"- 样本：{sample_count} 条（正面 {metadata.get('positive_count', 0)} / "
        f"中性 {metadata.get('neutral_count', 0)} / 负面 {metadata.get('negative_count', 0)}）",
    ]
    if metadata.get("latest_post_date"):
        lines.append(f"- 最新样本日期：{metadata['latest_post_date']}")

    windows = metadata.get("time_window")
    if isinstance(windows, Mapping):
        rows = []
        for label in ("1D", "3D", "7D"):
            value = windows.get(label)
            if not isinstance(value, Mapping) or value.get("status") != "OK":
                continue
            count = value.get("sample_count")
            window_score = value.get("sentiment_score")
            if not isinstance(count, int) or not isinstance(window_score, (int, float)):
                continue
            rows.append(
                f"| {label} | {count} | {value.get('positive_count', 0)} | "
                f"{value.get('neutral_count', 0)} | {value.get('negative_count', 0)} | "
                f"{float(window_score):.4g} |"
            )
        if rows:
            lines.extend(
                [
                    "",
                    "### 分析窗口",
                    "",
                    "| 窗口 | 样本数 | 正面 | 中性 | 负面 | 来源原生分数 |",
                    "|---|---:|---:|---:|---:|---:|",
                    *rows,
                ]
            )
    lines.extend(
        [
            "",
            "- 口径说明：只有投资者和社交样本参与方向与分数计算；"
            "新闻及宏观数据仅作背景，不参与情绪聚合。",
        ]
    )
    return "\n".join(lines)


def _sentiment_band(score: float) -> str:
    if score >= 0.5:
        return "偏多"
    if score >= 0.1:
        return "温和偏多"
    if score <= -0.5:
        return "偏空"
    if score <= -0.1:
        return "温和偏空"
    return "中性混合"


def _sentiment_confidence(value: Any, sample_count: int) -> str:
    if sample_count < 5:
        return "低"
    if isinstance(value, (int, float)):
        if float(value) < 0.45:
            return "低"
        if float(value) < 0.7:
            return "中"
        return "高"
    return {"low": "低", "medium": "中", "high": "高"}.get(str(value or "").strip().lower(), "低")


def _item_lines(items: list[Mapping[str, Any]], *, include_metadata: bool) -> list[str]:
    if not items:
        return ["数据不可用：本次窗口无可用匹配数据。"]
    lines = []
    # Providers emit chronological series in different orders.  Agent context
    # must retain the newest dated observations when bounded; slicing the
    # provider order previously hid fresh JSF rows after 2026-08-17.
    ordered = sorted(items, key=_item_timestamp_sort_key, reverse=True)
    for item in ordered[:20]:
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


def _item_timestamp_sort_key(item: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("timestamp") or ""),
        str(item.get("source") or ""),
        str(item.get("title") or ""),
    )


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
        unknown_publication = value.get("publication_status") == "UNKNOWN"
        for key, nested in value.items():
            key_text = str(key).lower()
            if key_text in _DIAGNOSTIC_KEYS or "snapshot" in key_text:
                continue
            if (
                unknown_publication
                and key_text == "native_cadence"
                and "CONFIRMED" in str(nested).upper()
            ):
                continue
            cleaned = _research_metadata(nested)
            if cleaned is not None:
                result[key] = cleaned
        return result
    if isinstance(value, list):
        return [_research_metadata(item) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if any(
            term in lowered for term in ("verified market snapshot", "http 4", "cache hit", "retry")
        ):
            return None
    return value
