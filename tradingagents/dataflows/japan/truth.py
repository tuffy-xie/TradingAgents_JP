"""Source-of-truth, comparability and freshness guards for Japan evidence.

The guard does not reconcile conflicting numbers.  It preserves upstream
evidence, labels its basis and exposes conflicts for agents and reports.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from .models import DataStatus, InformationLayer, MarketInformation


@dataclass(frozen=True)
class EvidenceValue:
    """A numeric observation together with the minimum comparison contract."""

    metric: str
    value: float | str | None
    source: str
    basis: str
    observed_at: datetime
    fiscal_year: int | None = None
    period_type: str | None = None
    unit: str | None = None


_OFFICIAL_GUIDANCE = {"TDnet", "Company IR"}
_FRESHNESS = {
    "official_ohlcv": (3, 7),
    "japan_stock_news": (1, 7),
    "securities_finance_balance": (3, 7),
    "securities_finance_trend": (3, 7),
    "reported_short_position": (7, 21),
    "margin_trading_balance": (7, 21),
    "official_financial_summary": (120, 200),
    "guidance_revision": (120, 200),
    "earnings_or_quarterly": (120, 200),
    "japan_analyst_expectations": (14, 30),
    "japan_investor_post": (1, 3),
    "japan_investor_sentiment_aggregate": (1, 3),
    "jp_cross_market_macro_context": (31, 62),
    "large_shareholding_report": (90, 180),
    "change_report": (90, 180),
}


def source_of_truth_rules() -> dict[str, str]:
    """Machine-readable policy rendered into every JP agent evidence block."""
    return {
        "current_price_ohlcv_technical": "Verified Market Snapshot only",
        "company_results_guidance": "TDnet / Company IR > J-Quants; compare only same basis and period",
        "analyst_consensus": "Independent analyst expectation; never overwrites company guidance",
        "large_shareholding": "EDINET official source",
        "credit_supply_demand": "JSF official unit/date preserved without conversion",
        "macro": "BOJ / Statistics Bureau / official series first; market vendors only for market prices",
        "exact_number_rule": "Agents may state exact numbers only when present in upstream evidence; otherwise mark unavailable",
    }


def freshness_for_item(item: MarketInformation, as_of: date) -> str:
    """Return frequency-specific freshness without applying one global TTL."""
    if item.status != DataStatus.OK:
        return "DATA_UNAVAILABLE"
    current_days, recent_days = _FRESHNESS.get(item.source_type, (30, 90))
    age = (as_of - item.timestamp.astimezone(UTC).date()).days
    if age <= current_days:
        return "CURRENT"
    if age <= recent_days:
        return "RECENT"
    return "STALE"


def compare_evidence(left: EvidenceValue, right: EvidenceValue) -> str:
    """Classify values without equating guidance, estimates or unlike periods."""
    if left.basis != right.basis:
        return "DIFFERENT_BASIS"
    if left.period_type != right.period_type or left.fiscal_year != right.fiscal_year:
        return "UNCOMPARABLE_PERIOD"
    if left.unit != right.unit:
        return "DIFFERENT_BASIS"
    if left.value == right.value:
        return "MATCH"
    return "CONFLICT"


def extract_evidence_values(items: Iterable[MarketInformation]) -> list[EvidenceValue]:
    """Extract only explicitly normalized metrics; no financial derivations."""
    values: list[EvidenceValue] = []
    for item in items:
        metadata = item.metadata or {}
        if item.source_type == "japan_analyst_expectations":
            estimate = metadata.get("analyst_expectations", {}).get("earnings_expectations", {})
            for field, metric in (
                ("revenue_estimate", "revenue"),
                ("net_income_estimate", "net_income"),
                ("eps_estimate", "eps"),
            ):
                if estimate.get(field) is not None:
                    values.append(
                        EvidenceValue(
                            metric,
                            estimate[field],
                            item.source,
                            "ANALYST_ESTIMATE",
                            item.timestamp,
                            estimate.get("fiscal_year"),
                            estimate.get("period_type"),
                            (estimate.get("unit") or {}).get(field.removesuffix("_estimate")),
                        )
                    )
            continue
        if item.source in _OFFICIAL_GUIDANCE and item.source_type == "guidance_revision":
            for label, value in (metadata.get("key_numbers") or {}).items():
                metric = {"売上高": "revenue", "親会社株主に帰属する当期純利益": "net_income"}.get(
                    label
                )
                if metric:
                    values.append(
                        EvidenceValue(
                            metric, value, item.source, "COMPANY_GUIDANCE", item.timestamp
                        )
                    )
            continue
        if item.source_type == "official_ohlcv":
            raw = metadata.get("raw_ohlcv") or {}
            adjusted = metadata.get("ohlcv") or {}
            if raw.get("Close", raw.get("C")) is not None:
                values.append(
                    EvidenceValue(
                        "close",
                        raw.get("Close", raw.get("C")),
                        item.source,
                        "RAW_OHLCV",
                        item.timestamp,
                        unit="JPY",
                    )
                )
            if adjusted.get("adjusted_close") is not None:
                values.append(
                    EvidenceValue(
                        "close",
                        adjusted["adjusted_close"],
                        item.source,
                        "ADJUSTED_OHLCV",
                        item.timestamp,
                        unit="JPY",
                    )
                )
    return values


def detect_conflicts(items: Iterable[MarketInformation]) -> list[dict[str, Any]]:
    """Return pairwise labels instead of choosing a hidden winning value."""
    item_list = tuple(items)
    groups: dict[str, list[EvidenceValue]] = {}
    for value in extract_evidence_values(item_list):
        groups.setdefault(value.metric, []).append(value)
    conflicts = []
    for metric, values in groups.items():
        for index, left in enumerate(values):
            for right in values[index + 1 :]:
                conflicts.append(
                    {
                        "metric": metric,
                        "left": _evidence_dict(left),
                        "right": _evidence_dict(right),
                        "status": compare_evidence(left, right),
                    }
                )
    if any(
        item.source in _OFFICIAL_GUIDANCE and item.source_type == "guidance_revision"
        for item in item_list
    ) and any(item.source_type == "japan_analyst_expectations" for item in item_list):
        conflicts.append(
            {
                "metric": "company_guidance_vs_analyst_consensus",
                "left": {"source_group": "TDnet / Company IR", "basis": "COMPANY_GUIDANCE"},
                "right": {"source_group": "Analyst Consensus", "basis": "ANALYST_ESTIMATE"},
                "status": "DIFFERENT_BASIS",
                "note": "not a numeric conflict; guidance and consensus must remain separate",
            }
        )
    return conflicts


def build_governance_item(
    items: Iterable[MarketInformation], ticker: str, as_of: date
) -> MarketInformation:
    """Add an immutable policy/diagnostic record to the Japan Bundle."""
    source_items = tuple(items)
    freshness = [
        {
            "source": item.source,
            "source_type": item.source_type,
            "timestamp": item.timestamp.isoformat(),
            "status": freshness_for_item(item, as_of),
        }
        for item in source_items
    ]
    return MarketInformation(
        source="Japan Evidence Governance",
        source_type="source_of_truth_assessment",
        ticker=ticker,
        timestamp=datetime.combine(as_of, datetime.min.time(), tzinfo=UTC),
        title="Japan source-of-truth and conflict assessment",
        content="System policy record; preserves upstream evidence without reconciliation.",
        confidence=1.0,
        verified=True,
        layer=InformationLayer.VERIFIED_FACT,
        content_level="structured_data",
        metadata={
            "source_of_truth": source_of_truth_rules(),
            "freshness": freshness,
            "conflicts": detect_conflicts(source_items),
            "derived_values": "PROHIBITED unless explicitly represented as upstream evidence",
        },
    )


def _evidence_dict(value: EvidenceValue) -> dict[str, Any]:
    return {
        "value": value.value,
        "source": value.source,
        "basis": value.basis,
        "fiscal_year": value.fiscal_year,
        "period_type": value.period_type,
        "unit": value.unit,
        "observed_at": value.observed_at.isoformat(),
    }
