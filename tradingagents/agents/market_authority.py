"""Lossless Market-tool freshness metadata and one JP consumption authority.

Tool prose may be shortened for the registry/LLM.  The metadata below is
extracted from the *complete* tool response before that shortening happens.
It is deliberately separate from the optional diagnostic market snapshot.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from tradingagents.dataflows.japan.trading_calendar import latest_completed_japan_session

_STOCK_HEADER = re.compile(
    r"^# Stock data for (?P<symbol>.+?) from (?P<start>\d{4}-\d{2}-\d{2}) "
    r"to (?P<end>\d{4}-\d{2}-\d{2})$",
    re.M,
)
_COUNT_HEADER = re.compile(r"^# Total records: (?P<count>\d+)$", re.M)
_RETRIEVED_HEADER = re.compile(r"^# Data retrieved on: (?P<time>[^\n]+)$", re.M)
_INDICATOR_HEADER = re.compile(
    r"^## (?P<indicator>[\w_]+) values from (?P<start>\d{4}-\d{2}-\d{2}) "
    r"to (?P<end>\d{4}-\d{2}-\d{2}):$",
    re.M,
)
_UNDERLYING_BAR = re.compile(
    r"^Underlying OHLCV latest completed bar: (?P<date>\d{4}-\d{2}-\d{2}|DATA_UNAVAILABLE)$",
    re.M,
)
_OHLC = ("Open", "High", "Low", "Close")


def summarize_market_tool_response(tool_name: str, content: str) -> dict[str, Any] | None:
    """Summarize a complete response, never a registry presentation prefix."""
    if tool_name not in {"get_stock_data", "get_indicators"}:
        return None
    summary: dict[str, Any] = {
        "tool": tool_name,
        "payload_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "payload_chars": len(content),
        "status": "UNVERIFIED_RESPONSE",
    }
    if tool_name == "get_indicators":
        header = _INDICATOR_HEADER.search(content)
        underlying = _UNDERLYING_BAR.search(content)
        if header:
            summary.update(
                indicator=header.group("indicator"),
                requested_start=header.group("start"),
                requested_end=header.group("end"),
            )
        if underlying:
            summary["underlying_latest_complete_date"] = (
                underlying.group("date")
                if underlying.group("date") != "DATA_UNAVAILABLE"
                else None
            )
        if header and underlying and summary["underlying_latest_complete_date"]:
            summary["status"] = "PARSED"
        return summary

    header = _STOCK_HEADER.search(content)
    count = _COUNT_HEADER.search(content)
    retrieved = _RETRIEVED_HEADER.search(content)
    if header:
        summary.update(
            symbol=header.group("symbol"),
            requested_start=header.group("start"),
            requested_end=header.group("end"),
        )
    if count:
        summary["reported_row_count"] = int(count.group("count"))
    if retrieved:
        # This is the tool's retrieval label, not a source publication time.
        summary["retrieved_at_label"] = retrieved.group("time").strip()
    lines = content.splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.startswith("Date,")), None)
    if header_index is None:
        return summary
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[header_index:]))))
    latest: date | None = None
    complete = 0
    valid_dates = 0
    for row in rows:
        try:
            observed = date.fromisoformat(str(row.get("Date") or "")[:10])
        except ValueError:
            continue
        valid_dates += 1
        if all(_numeric(row.get(key)) for key in _OHLC):
            complete += 1
            latest = max(latest, observed) if latest else observed
    summary.update(
        parsed_row_count=len(rows),
        dated_row_count=valid_dates,
        complete_ohlcv_row_count=complete,
        latest_complete_ohlcv_date=latest.isoformat() if latest else None,
    )
    if (
        header
        and count
        and retrieved
        and len(rows) == valid_dates == complete == int(count.group("count"))
        and latest is not None
    ):
        summary["status"] = "PARSED_COMPLETE_PAYLOAD"
    return summary


def canonical_market_authority(state: Mapping[str, Any]) -> dict[str, Any]:
    """Reconcile stock-data and indicator provenance without parsing display text.

    A legacy entry with no lossless metadata is unverified, even when its
    shortened ``value`` happens to contain a seemingly recent row.
    """
    manifest = state.get("run_manifest") or {}
    as_of_text = str(manifest.get("analysis_as_of") or state.get("trade_date") or "")
    try:
        as_of = date.fromisoformat(as_of_text)
    except ValueError:
        return {"status": "UNAVAILABLE", "reason": "INVALID_ANALYSIS_AS_OF"}
    observed_at = _manifest_time(manifest)
    expected = latest_completed_japan_session(as_of, now=observed_at)
    base = {
        "status": "UNAVAILABLE",
        "analysis_as_of": as_of.isoformat(),
        "expected_latest_complete_date": expected.isoformat(),
        "latest_complete_ohlcv_date": None,
        "stock_evidence_ids": [],
        "indicator_evidence_ids": [],
    }
    entries = [
        entry for entry in (state.get("evidence_registry") or [])
        if isinstance(entry, Mapping)
        and entry.get("source_type") == "TOOL_OUTPUT"
        and entry.get("source") in {"get_stock_data", "get_indicators"}
    ]
    stocks = [entry for entry in entries if entry.get("source") == "get_stock_data"]
    indicators = [entry for entry in entries if entry.get("source") == "get_indicators"]
    base["stock_evidence_ids"] = [entry.get("evidence_id") for entry in stocks]
    base["indicator_evidence_ids"] = [entry.get("evidence_id") for entry in indicators]
    if not stocks:
        return base | {"reason": "NO_STOCK_TOOL_EVIDENCE"}
    summaries = [
        (entry, _market_summary(entry)) for entry in stocks
        if _market_summary(entry).get("requested_end") == as_of_text
    ]
    if not summaries:
        reason = (
            "STOCK_PAYLOAD_METADATA_UNVERIFIED"
            if any(not _market_summary(entry) for entry in stocks)
            else "NO_AS_OF_STOCK_TOOL_EVIDENCE"
        )
        return base | {"reason": reason}
    if any(summary.get("status") != "PARSED_COMPLETE_PAYLOAD" for _, summary in summaries):
        return base | {"reason": "STOCK_PAYLOAD_METADATA_UNVERIFIED"}
    stock_dates = {summary.get("latest_complete_ohlcv_date") for _, summary in summaries}
    if len(stock_dates) != 1:
        return base | {"reason": "STOCK_TOOL_DATE_CONFLICT"}
    latest = stock_dates.pop()
    base["latest_complete_ohlcv_date"] = latest
    base["row_count"] = max(summary["complete_ohlcv_row_count"] for _, summary in summaries)
    if latest != expected.isoformat():
        return base | {"reason": "STOCK_DATA_NOT_LATEST_COMPLETED_SESSION"}
    current_indicators = [
        (entry, _market_summary(entry)) for entry in indicators
        if _market_summary(entry).get("requested_end") == as_of_text
    ]
    if not current_indicators:
        return base | {"reason": "NO_AS_OF_INDICATOR_EVIDENCE"}
    if any(
        summary.get("status") != "PARSED"
        or summary.get("underlying_latest_complete_date") != latest
        for _, summary in current_indicators
    ):
        return base | {"reason": "STOCK_INDICATOR_DATE_CONFLICT"}
    return base | {"status": "CURRENT", "reason": "TOOL_DATES_AND_COMPLETE_ROWS_RECONCILED"}


def market_tool_response_eligible(
    summary: Mapping[str, Any] | None, state: Mapping[str, Any]
) -> bool:
    """A single tool entry may be cited as current only with intact metadata."""
    return market_tool_response_freshness(summary, state) in {
        "LATEST_AVAILABLE", "AS_OF_FILTERED"
    }


def market_tool_response_freshness(
    summary: Mapping[str, Any] | None, state: Mapping[str, Any]
) -> str:
    """Classify a tool response independently of its shortened display value."""
    if summary is None:
        return "AS_OF_FILTERED"  # Non-Market tools retain their established contract.
    manifest = state.get("run_manifest") or {}
    try:
        as_of = date.fromisoformat(
            str(manifest.get("analysis_as_of") or state.get("trade_date") or "")
        )
    except ValueError:
        return "FRESHNESS_UNVERIFIED"
    expected = latest_completed_japan_session(as_of, now=_manifest_time(manifest))
    latest = (
        summary.get("latest_complete_ohlcv_date")
        if summary.get("tool") == "get_stock_data"
        else summary.get("underlying_latest_complete_date")
    )
    required_status = (
        "PARSED_COMPLETE_PAYLOAD"
        if summary.get("tool") == "get_stock_data"
        else "PARSED"
    )
    if summary.get("status") != required_status or summary.get("requested_end") != as_of.isoformat():
        return "FRESHNESS_UNVERIFIED"
    try:
        observed = date.fromisoformat(str(latest))
    except ValueError:
        return "FRESHNESS_UNVERIFIED"
    if observed > expected:
        return "FUTURE"
    return "LATEST_AVAILABLE" if observed == expected else "STALE_SOURCE"


def _market_summary(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    derivation = entry.get("derivation")
    value = derivation.get("market_data") if isinstance(derivation, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def _numeric(value: Any) -> bool:
    try:
        return value is not None and str(value).strip() != "" and math.isfinite(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return False


def _manifest_time(manifest: Mapping[str, Any]) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(manifest.get("runtime_timestamp_jst") or ""))
    except ValueError:
        return None
    return value if value.tzinfo is not None and value.utcoffset() is not None else None
