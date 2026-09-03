"""Run-local evidence identity and provenance for the existing agent pipeline.

The registry is metadata carried in ``AgentState``.  It is not an agent, does
not fetch data, and does not change graph topology.  Its purpose is to keep a
verified tool/provider fact identifiable after the natural-language analyst
handoff and to expose fail-closed consumption eligibility to downstream nodes.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_JST = ZoneInfo("Asia/Tokyo")
_STALE = {
    "STALE",
    "STALE_SOURCE",
    "FUTURE",
    "FRESHNESS_UNVERIFIED",
    "HISTORICAL_OBSERVATION",
}
_TOOL_DOMAINS = {
    "get_stock_data": "MARKET",
    "get_indicators": "MARKET",
    "get_verified_market_snapshot": "MARKET",
    "get_news": "NEWS",
    "get_global_news": "NEWS",
    "get_macro_indicators": "NEWS",
    "get_prediction_markets": "NEWS",
    "get_fundamentals": "FUNDAMENTALS",
    "get_balance_sheet": "FUNDAMENTALS",
    "get_cashflow": "FUNDAMENTALS",
    "get_income_statement": "FUNDAMENTALS",
}
_AGENT_DOMAINS = {
    "Market Analyst": "MARKET",
    "Sentiment Analyst": "SENTIMENT",
    "News Analyst": "NEWS",
    "Fundamentals Analyst": "FUNDAMENTALS",
}


def build_run_manifest(
    *, analysis_as_of: str, market_context: Mapping[str, Any], config: Mapping[str, Any]
) -> dict[str, Any]:
    """Create non-secret metadata that identifies the exact production run."""
    repo = Path(__file__).resolve().parents[3]
    head = _git_value(repo, "rev-parse", "HEAD")
    branch = _git_value(repo, "branch", "--show-current")
    safe_config = {
        key: config.get(key)
        for key in (
            "max_debate_rounds",
            "max_risk_discuss_rounds",
            "selected_analysts",
            "trading_horizon",
        )
        if key in config
    }
    digest_payload = {
        "market": market_context.get("market"),
        "symbol": market_context.get("symbol"),
        "config": safe_config,
    }
    return {
        "run_id": str(uuid.uuid4()),
        "git_head": head,
        "git_branch": branch,
        "git_dirty": _git_dirty(repo),
        "analysis_as_of": str(analysis_as_of),
        "runtime_timestamp_jst": datetime.now(_JST).isoformat(),
        "cache_contract_versions": {
            "japan_bundle": "stage8-v1",
            "evidence_registry": "stage10-v1",
        },
        "config_digest": hashlib.sha256(_canonical(digest_payload).encode()).hexdigest()[:16],
    }


def initialize_evidence_registry(
    *,
    market_context: Mapping[str, Any],
    japan_data_bundle: Mapping[str, Any],
    verified_market_snapshot: str,
    analysis_as_of: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Seed provider facts and one real audit summary for a JP run."""
    if market_context.get("market") != "JP":
        return [], []
    entries: list[dict[str, Any]] = []
    for item in japan_data_bundle.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        entries.append(_entry_from_bundle_item(item, analysis_as_of))
    entries.extend(_financial_entries(japan_data_bundle, analysis_as_of))
    if verified_market_snapshot and "UNAVAILABLE" not in verified_market_snapshot:
        entries.append(
            _make_entry(
                domain="MARKET",
                claim_type="FACT",
                metric="verified_market_snapshot",
                value=verified_market_snapshot,
                source="Verified Market Snapshot",
                source_type="TOOL_OUTPUT",
                analysis_as_of=analysis_as_of,
                freshness="CURRENT",
                verification_status="VERIFIED_TOOL_OUTPUT",
                allowed=True,
            )
        )
    entries = _dedupe(entries)
    supported = [entry["evidence_id"] for entry in entries if entry["allowed_for_current_decision"]]
    audit = [
        {
            "category": "SUPPORTED_FACT",
            "agent": "Run Initialization",
            "detail": "Run-local evidence registry initialized from normalized provider facts.",
            "evidence_ids": supported,
            "supported_count": len(supported),
        }
    ]
    return entries, audit


def capture_agent_evidence(
    state: Mapping[str, Any],
    result: Mapping[str, Any],
    agent_name: str,
    *,
    capture_tools: bool = True,
    capture_reports: bool = True,
) -> dict[str, Any]:
    """Attach tool/report provenance to a node result without changing prose."""
    if (state.get("market_context") or {}).get("market") != "JP":
        return dict(result)
    registry = list(state.get("evidence_registry") or [])
    audit = list(state.get("evidence_audit") or [])
    analysis_as_of = str(state.get("trade_date") or "")
    new_entries: list[dict[str, Any]] = []
    for message in (state.get("messages") or []) if capture_tools else []:
        if getattr(message, "type", "") != "tool" and type(message).__name__ != "ToolMessage":
            continue
        tool_name = str(getattr(message, "name", "") or "unknown_tool")
        content = str(getattr(message, "content", "") or "")
        if not content:
            continue
        numeric_tokens = sorted(_numeric_tokens(content))
        new_entries.append(
            _make_entry(
                domain=_TOOL_DOMAINS.get(tool_name, _AGENT_DOMAINS.get(agent_name, "OTHER")),
                claim_type="FACT",
                metric="tool_output",
                value=content[:12000],
                semantic_basis="TOOL_FACT",
                source=tool_name,
                source_type="TOOL_OUTPUT",
                source_record_id=str(getattr(message, "tool_call_id", "") or "") or None,
                analysis_as_of=analysis_as_of,
                freshness="AS_OF_FILTERED",
                verification_status="VERIFIED_TOOL_OUTPUT",
                allowed=True,
                derivation={"numeric_tokens": numeric_tokens},
            )
        )
        new_entries.extend(
            _semantic_tool_entries(
                tool_name=tool_name,
                content=content,
                analysis_as_of=analysis_as_of,
                source_record_id=str(
                    getattr(message, "tool_call_id", "") or ""
                )
                or None,
            )
        )
    report_fields = {
        "market_report": "MARKET",
        "sentiment_report": "SENTIMENT",
        "news_report": "NEWS",
        "fundamentals_report": "FUNDAMENTALS",
        "investment_plan": "RESEARCH",
        "trader_investment_plan": "TRADING",
        "final_trade_decision": "PORTFOLIO",
    }
    upstream_ids = [entry["evidence_id"] for entry in registry + new_entries if entry.get("allowed_for_current_decision")]
    for field, domain in report_fields.items() if capture_reports else ():
        value = result.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        new_entries.append(
            _make_entry(
                domain=domain,
                claim_type="INFERENCE",
                metric=field,
                value=value[:12000],
                source=agent_name,
                source_type="ANALYST_REPORT",
                analysis_as_of=analysis_as_of,
                freshness="DERIVED_AS_OF",
                verification_status="SUPPORTED_INFERENCE",
                allowed=True,
                derivation={"upstream_evidence_ids": upstream_ids[-40:]},
            )
        )
    registry = _dedupe([*registry, *new_entries])
    if new_entries:
        audit.append(
            {
                "category": "SUPPORTED_FACT",
                "agent": agent_name,
                "detail": (
                    "Sanitized report provenance captured after evidence enforcement."
                    if capture_reports and not capture_tools
                    else "Tool/report provenance captured for natural-language handoff."
                ),
                "evidence_ids": [entry["evidence_id"] for entry in new_entries],
            }
        )
    updated = dict(result)
    updated["evidence_registry"] = registry
    updated["evidence_audit"] = audit
    return updated


def _semantic_tool_entries(
    *,
    tool_name: str,
    content: str,
    analysis_as_of: str,
    source_record_id: str | None,
) -> list[dict[str, Any]]:
    """Create narrow semantic entries without relabelling an entire tool dump.

    A fundamentals response may contain historical statements, TTM values and
    vendor forward estimates together.  Only explicit Forward fields receive
    the VENDOR_FORWARD_ESTIMATE semantic basis.
    """
    if tool_name != "get_fundamentals":
        return []
    entries: list[dict[str, Any]] = []
    for line in content.splitlines():
        match = re.match(
            r"\s*(Forward\s+(?P<metric>[A-Za-z][A-Za-z ]*)):\s*(?P<value>[-+\d,.]+)\s*$",
            line,
            re.I,
        )
        if not match:
            continue
        entries.append(
            _make_entry(
                domain="FUNDAMENTALS",
                claim_type="FACT",
                metric=re.sub(r"\s+", "_", match.group("metric").strip().lower()),
                value=match.group("value"),
                semantic_basis="VENDOR_FORWARD_ESTIMATE",
                source=tool_name,
                source_type="TOOL_OUTPUT",
                source_record_id=source_record_id,
                analysis_as_of=analysis_as_of,
                freshness="AS_OF_FILTERED",
                verification_status="VERIFIED_TOOL_OUTPUT",
                allowed=True,
            )
        )
    return entries


def _numeric_tokens(text: str) -> set[str]:
    return {
        match.group(0).replace(",", "")
        for match in re.finditer(r"(?<![A-Za-z0-9_])[-+]?\d[\d,]*(?:\.\d+)?%?", text)
    }


def render_downstream_evidence_context(state: Mapping[str, Any]) -> str:
    """Render compact provenance continuity, never raw FinancialDocument JSON."""
    if (state.get("market_context") or {}).get("market") != "JP":
        return ""
    entries = state.get("evidence_registry") or []
    tool_entries = [
        item
        for item in entries
        if isinstance(item, Mapping)
        and (
            item.get("source_type") in {"TOOL_OUTPUT", "ANALYST_REPORT"}
            or item.get("verification_status") == "VERIFIED_FINANCIAL_AUTHORITY"
        )
    ][-30:]
    lines = [
        "## Internal evidence continuity (JP; analyst handoff metadata)",
        "Verified tool facts remain verified downstream. Do not call them unverified merely because they are quoted through an Analyst report.",
        "Market Analyst numbers sourced from get_stock_data/get_indicators remain VERIFIED_TOOL_OUTPUT; cite that ownership instead of calling them debate-only or unverified.",
        "News Analyst facts sourced from named tools retain those tool sources; unsupported prose remains inference and is not hard evidence.",
        "Financial metrics retain VERIFIED_FINANCIAL_AUTHORITY with their TDnet/Company IR/EDINET DB source; they are not generic tool output.",
        "COMPANY_GUIDANCE, ANALYST_CONSENSUS, and VENDOR_FORWARD_ESTIMATE are distinct semantic types.",
        "STALE/HISTORICAL_ONLY evidence may be dated background but is prohibited as current directional evidence.",
    ]
    for item in tool_entries:
        derivation = item.get("derivation") or {}
        refs = derivation.get("upstream_evidence_ids") or []
        lines.append(
            f"- [{item.get('evidence_id')}] {item.get('domain')} {item.get('source')} "
            f"verification={item.get('verification_status')} freshness={item.get('freshness')} "
            f"current_eligible={item.get('allowed_for_current_decision')}"
            + (f" upstream={','.join(refs[-8:])}" if refs else "")
        )
    validation = state.get("validated_execution")
    if isinstance(validation, Mapping) and validation:
        from tradingagents.agents.utils.execution_validation import (
            authoritative_execution_context,
        )

        lines.extend(["", authoritative_execution_context(validation)])
    return "\n".join(lines)


def _entry_from_bundle_item(item: Mapping[str, Any], analysis_as_of: str) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    freshness = str(
        metadata.get("freshness_status")
        or item.get("freshness_status")
        or ("CURRENT" if item.get("verified") is True else "FRESHNESS_UNVERIFIED")
    )
    source_type = str(item.get("source_type") or "UNKNOWN")
    semantic = {
        "japan_analyst_expectations": "ANALYST_CONSENSUS",
        "guidance_revision": "COMPANY_GUIDANCE",
        "official_financial_summary": "COMPANY_GUIDANCE",
        "reported_short_position": "REPORTABLE_SHORT_POSITION",
        "securities_finance_balance": "OBSERVABLE_LENDING_BALANCE",
    }.get(source_type, source_type.upper())
    status = str(item.get("status") or "OK")
    allowed = item.get("verified") is True and freshness not in _STALE and status == "OK"
    return _make_entry(
        domain=_bundle_domain(item),
        claim_type="FACT" if status == "OK" else "UNAVAILABLE",
        metric=source_type,
        value={"title": item.get("title"), "metadata": metadata},
        unit=metadata.get("unit"),
        currency=metadata.get("currency"),
        period=metadata.get("period"),
        target_period=metadata.get("target_period_end"),
        semantic_basis=semantic,
        source=str(item.get("source") or "UNKNOWN"),
        source_type=source_type,
        source_record_id=metadata.get("source_record_id"),
        source_url=item.get("url"),
        data_date=str(item.get("timestamp") or "") or None,
        published_at=metadata.get("published_at"),
        fetched_at=metadata.get("fetched_at"),
        analysis_as_of=analysis_as_of,
        freshness=freshness,
        verification_status="VERIFIED_SOURCE" if item.get("verified") is True else "UNVERIFIED",
        allowed=allowed,
        derivation={"numeric_tokens": sorted(_numeric_tokens(_canonical(metadata)))},
    )


def _financial_entries(bundle: Mapping[str, Any], analysis_as_of: str) -> list[dict[str, Any]]:
    metadata = bundle.get("provider_metadata") or {}
    assessment = metadata.get("Japan Financial Authority") or {}
    entries: list[dict[str, Any]] = []
    for section, semantic in (("actual", "ACTUAL"), ("guidance", "COMPANY_GUIDANCE")):
        value = assessment.get(section)
        if not isinstance(value, Mapping):
            continue
        document = value.get("document")
        if not isinstance(document, Mapping):
            continue
        expected = section.upper()
        record = next(
            (
                row
                for row in document.get("records") or []
                if isinstance(row, Mapping) and row.get("record_type") == expected
            ),
            None,
        )
        if not isinstance(record, Mapping):
            continue
        for metric_name, metric in (record.get("metrics") or {}).items():
            if not isinstance(metric, Mapping):
                continue
            observed = metric.get("current_value") if section == "guidance" else metric
            if not isinstance(observed, Mapping):
                observed = metric
            metric_status = str(observed.get("status") or metric.get("status") or "DATA_UNAVAILABLE")
            entries.append(
                _make_entry(
                    domain="FUNDAMENTALS",
                    claim_type="FACT" if metric_status == "OK" else "UNAVAILABLE",
                    metric=str(metric_name),
                    value=observed.get("value"),
                    unit=observed.get("unit") or metric.get("unit"),
                    currency=record.get("currency"),
                    period=record.get("period_type"),
                    target_period=record.get("target_period_end"),
                    semantic_basis=(
                        semantic
                        if section == "guidance"
                        else metric.get("semantic_basis") or str(metric_name).upper()
                    ),
                    source=str(document.get("source") or "UNKNOWN"),
                    source_type=str(document.get("source_type") or "UNKNOWN"),
                    source_record_id=document.get("source_record_id"),
                    source_url=document.get("source_url"),
                    data_date=value.get("selected_disclosure_date"),
                    published_at=document.get("disclosure_timestamp"),
                    fetched_at=document.get("fetched_at"),
                    analysis_as_of=analysis_as_of,
                    freshness=str(value.get("freshness") or "FRESHNESS_UNVERIFIED"),
                    verification_status="VERIFIED_FINANCIAL_AUTHORITY",
                    allowed=value.get("status") == "OK" and metric_status == "OK",
                    derivation=metric.get("derivation"),
                )
            )
    return entries


def _make_entry(
    *,
    domain: str,
    claim_type: str,
    metric: str,
    value: Any,
    source: str,
    source_type: str,
    analysis_as_of: str,
    freshness: str,
    verification_status: str,
    allowed: bool,
    unit: Any = None,
    currency: Any = None,
    period: Any = None,
    target_period: Any = None,
    semantic_basis: Any = None,
    source_record_id: Any = None,
    source_url: Any = None,
    data_date: Any = None,
    published_at: Any = None,
    fetched_at: Any = None,
    derivation: Any = None,
) -> dict[str, Any]:
    payload = {
        "domain": domain,
        "claim_type": claim_type,
        "metric": metric,
        "value": value,
        "unit": unit,
        "currency": currency,
        "period": period,
        "target_period": target_period,
        "semantic_basis": semantic_basis,
        "source": source,
        "source_type": source_type,
        "source_record_id": source_record_id,
        "source_url": source_url,
        "data_date": data_date,
        "published_at": published_at,
        "fetched_at": fetched_at,
        "analysis_as_of": analysis_as_of,
        "freshness": freshness,
        "verification_status": verification_status,
        "allowed_for_current_decision": bool(allowed),
        "derivation": derivation,
    }
    identity = {key: payload.get(key) for key in payload if key not in {"fetched_at"}}
    payload["evidence_id"] = "E-" + hashlib.sha256(_canonical(identity).encode()).hexdigest()[:12]
    return payload


def _bundle_domain(item: Mapping[str, Any]) -> str:
    source = str(item.get("source") or "")
    source_type = str(item.get("source_type") or "")
    if source_type == "japan_analyst_expectations":
        return "FUNDAMENTALS"
    if item.get("layer") == "MARKET_SENTIMENT":
        return "SENTIMENT"
    if source in {"JPX", "JSF", "J-Quants"}:
        return "MARKET"
    if source in {"TDnet", "Company IR", "EDINET", "Japan Macro"}:
        return "NEWS"
    return "OTHER"


def _dedupe(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return list({entry["evidence_id"]: entry for entry in entries}.values())


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))


def _git_value(repo: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _git_dirty(repo: Path) -> bool | None:
    """Record uncommitted state so a manifest never overstates its HEAD."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(result.stdout.strip())
