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
    """Legacy display adapter; it never judges facts or execution eligibility."""
    if not isinstance(text, str):
        return ""
    if market == "JP" and section == "sentiment_report":
        text = _US_COMMUNITY.sub("海外社区情绪", text)
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
