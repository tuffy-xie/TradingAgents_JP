"""Small report-boundary guards for immutable run context.

The graph state is the synthesis contract.  These helpers deliberately do not
fetch data or re-classify a ticker; they only make the already-resolved context
visible and remove US-community wording from Japanese reports.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_US_COMMUNITY = re.compile(r"(?i)\b(?:stocktwits|reddit)\b")


def canonical_report_metadata(state: Mapping[str, Any]) -> dict[str, str]:
    """Return display metadata exclusively from the immutable MarketContext."""
    context = state.get("market_context") or {}
    return {
        "symbol": str(context.get("symbol") or state.get("company_of_interest") or ""),
        "market": str(context.get("market") or "UNKNOWN"),
        "currency": str(context.get("currency") or "—"),
        "instrument_type": str(context.get("instrument_type") or "EQUITY"),
    }


def sanitize_report_section(text: str, market: str, section: str) -> str:
    """Apply deterministic disclosure wording at the synthesis boundary."""
    if not isinstance(text, str):
        return ""
    if market == "JP" and section == "sentiment_report":
        text = _US_COMMUNITY.sub("海外社区情绪", text)
    if market == "JP":
        text = _localize_japan_internal_statuses(text)
    return text


def _localize_japan_internal_statuses(text: str) -> str:
    """Keep internal enums in full_agent_log, not in the user report."""
    replacements = {
        "VERIFIED_FINANCIAL_AUTHORITY": "已验证财务权威来源",
        "VERIFIED_TOOL_OUTPUT": "已验证工具数据",
        "CURRENT_STRUCTURED_CONFIRMED": "已确认是截至分析日最新的结构化数据",
        "CURRENT_OFFICIAL": "截至分析日最新官方数据",
        "FRESHNESS_UNVERIFIED": "新鲜度未确认",
        "INSUFFICIENT_DATA": "证据不足",
        "DATA_UNAVAILABLE": "数据不可用",
        "NOT_APPLICABLE": "不适用",
        "NOT_PROVIDED": "公司未提供",
        "LATEST_AVAILABLE": "来源当前最新可得",
        "DATA UNAVAILABLE": "数据不可用",
    }
    for source, display in replacements.items():
        text = text.replace(source, display)
    return text


def report_consistency_notes(state: Mapping[str, Any]) -> list[str]:
    """Return machine-checkable invariant notes for logging/regression tests."""
    meta = canonical_report_metadata(state)
    snapshot = state.get("verified_market_snapshot") or ""
    notes = [
        f"canonical_symbol={meta['symbol']}",
        f"market={meta['market']}",
        f"currency={meta['currency']}",
        f"instrument_type={meta['instrument_type']}",
        "verified_snapshot=" + ("available" if "VERIFIED_MARKET_SNAPSHOT_UNAVAILABLE" not in snapshot else "unavailable"),
    ]
    return notes
