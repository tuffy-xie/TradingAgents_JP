"""Official-authority, freshness, and critical-data assessment for JP financials.

This module is pure: it consumes an already collected Japan bundle, parses
available official text without fetching, and decides whether normalized
Actual and Guidance records are safe to present to the Fundamentals Analyst.
Operational provider diagnostics remain outside the rendered research facts.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Any

from .financial_disclosure import (
    DATA_UNAVAILABLE,
    NOT_PROVIDED,
    FinancialRecord,
    parse_financial_disclosure,
    parse_financial_document,
)

STATUS_OK = "OK"
STATUS_INSUFFICIENT = "INSUFFICIENT_DATA"
STATUS_CONFLICT = "CONFLICT_UNRESOLVED"
FRESHNESS_CURRENT_OFFICIAL = "CURRENT_OFFICIAL"
FRESHNESS_CURRENT_STRUCTURED = "CURRENT_STRUCTURED_CONFIRMED"
FRESHNESS_OFFICIAL_UPDATE_UNPARSED = "OFFICIAL_UPDATE_VALUES_UNAVAILABLE"
FRESHNESS_UNVERIFIED = "FRESHNESS_UNVERIFIED"

_OFFICIAL_SOURCES = {"TDnet", "Company IR"}
_ACTUAL_EVENT_TYPES = {"earnings_or_quarterly"}
_GUIDANCE_EVENT_TYPES = {"earnings_or_quarterly", "guidance_revision"}
_PERIOD_RANK = {"Q1": 1, "H1": 2, "Q3": 3, "FY": 4}
_TARGET = re.compile(r"(?P<year>20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月期")
_EXPLICIT_DATE = re.compile(
    r"20\d{2}(?:[./年-]\d{1,2}[./月-]\d{1,2}日?|\d{4})"
)


def assess_japan_financial_data(
    *,
    items: Sequence[Mapping[str, Any]],
    source_statuses: Sequence[Mapping[str, Any]],
    provider_metadata: Mapping[str, Mapping[str, Any]],
    analysis_as_of: date,
    official_scan_start: date,
) -> dict[str, Any]:
    """Return independently gated Actual and Guidance assessments."""
    edinet = provider_metadata.get("EDINET DB Financials") or {}
    official_items = [
        item
        for item in items
        if isinstance(item, Mapping)
        and item.get("source") in _OFFICIAL_SOURCES
        and item.get("verified") is True
        and _official_date(item) is not None
        and _official_date(item) <= analysis_as_of
    ]
    statuses = {
        str(entry.get("source")): entry
        for entry in source_statuses
        if isinstance(entry, Mapping)
    }
    tdnet_window_complete = _tdnet_coverage_complete(statuses.get("TDnet"))

    actual = _assess_kind(
        kind="ACTUAL",
        structured_document=_mapping(edinet.get("actual_document") or edinet.get("financial_document")),
        structured_status=str(edinet.get("actual_status") or DATA_UNAVAILABLE),
        structured_detail=str(edinet.get("actual_detail") or ""),
        structured_reference_date=_strict_date(
            (_mapping(edinet.get("actual_selection")) or {}).get("disclosure_date")
        ),
        official_items=[item for item in official_items if _event_type(item) in _ACTUAL_EVENT_TYPES],
        analysis_as_of=analysis_as_of,
        official_scan_start=official_scan_start,
        official_window_complete=tdnet_window_complete,
    )
    guidance = _assess_kind(
        kind="GUIDANCE",
        structured_document=_mapping(edinet.get("guidance_document")),
        structured_status=str(edinet.get("guidance_status") or NOT_PROVIDED),
        structured_detail=str(edinet.get("guidance_detail") or ""),
        structured_reference_date=_strict_date(
            (_mapping(edinet.get("guidance_selection")) or {}).get("disclosure_date")
        ),
        official_items=[item for item in official_items if _event_type(item) in _GUIDANCE_EVENT_TYPES],
        analysis_as_of=analysis_as_of,
        official_scan_start=official_scan_start,
        official_window_complete=tdnet_window_complete,
    )
    return {
        "status": STATUS_OK if actual["status"] == STATUS_OK else STATUS_INSUFFICIENT,
        "analysis_as_of": analysis_as_of.isoformat(),
        "official_scan_start": official_scan_start.isoformat(),
        "actual": actual,
        "guidance": guidance,
    }


def _assess_kind(
    *,
    kind: str,
    structured_document: Mapping[str, Any] | None,
    structured_status: str,
    structured_detail: str,
    structured_reference_date: date | None,
    official_items: Sequence[Mapping[str, Any]],
    analysis_as_of: date,
    official_scan_start: date,
    official_window_complete: bool,
) -> dict[str, Any]:
    structured_date = _document_date(structured_document) or structured_reference_date
    if structured_date is not None and structured_date > analysis_as_of:
        structured_document = None
        structured_status = DATA_UNAVAILABLE
        structured_detail = "FUTURE_STRUCTURED_DISCLOSURE"
    parsed_official = [(item, _parse_official_item(item, kind)) for item in official_items]
    relevant_official = [
        (item, document)
        for item, document in parsed_official
        if not _demonstrably_older(item, document, structured_document, kind)
    ]
    official_latest_date = max(
        (_official_date(item) for item, _document in relevant_official), default=None
    )
    latest_pairs = [
        (item, document)
        for item, document in relevant_official
        if _official_date(item) == official_latest_date
    ]
    latest_items = [item for item, _document in latest_pairs]
    official_document, official_conflict = _resolve_official_documents(
        [document for _item, document in latest_pairs if document is not None], kind
    )
    official_update = _official_update_summary(latest_items, official_latest_date)

    if official_conflict:
        return _assessment(
            STATUS_CONFLICT,
            FRESHNESS_UNVERIFIED,
            None,
            kind,
            "Conflicting official parsed values for the same latest disclosure date.",
            official_update,
            structured_date,
        )

    if official_latest_date is not None and (
        structured_date is None or official_latest_date > structured_date
    ):
        if official_document is None:
            return _assessment(
                STATUS_INSUFFICIENT,
                FRESHNESS_OFFICIAL_UPDATE_UNPARSED,
                None,
                kind,
                "A newer official financial update exists, but normalized critical values are unavailable.",
                official_update,
                structured_date,
            )
        gate = _gate_document(official_document, kind)
        if gate["status"] != STATUS_OK:
            return _assessment(
                STATUS_INSUFFICIENT,
                FRESHNESS_OFFICIAL_UPDATE_UNPARSED,
                None,
                kind,
                "A newer official update was parsed, but its critical financial data gate did not pass.",
                official_update,
                structured_date,
                gate,
            )
        return _assessment(
            STATUS_OK,
            FRESHNESS_CURRENT_OFFICIAL,
            official_document,
            kind,
            "Latest normalized values come from the newer official disclosure.",
            official_update,
            structured_date,
            gate,
        )

    if structured_status == NOT_PROVIDED and kind == "GUIDANCE":
        coverage = _coverage_confirmed(
            structured_date, official_scan_start, official_window_complete
        )
        return _assessment(
            NOT_PROVIDED if coverage else STATUS_INSUFFICIENT,
            FRESHNESS_CURRENT_STRUCTURED if coverage else FRESHNESS_UNVERIFIED,
            None,
            kind,
            (
                "The company Guidance fields are not provided by the current structured record."
                if coverage
                else "Official-source coverage cannot prove that Guidance is currently not provided."
            ),
            official_update,
            structured_date,
        )
    if structured_document is None or structured_status != STATUS_OK:
        return _assessment(
            STATUS_INSUFFICIENT,
            FRESHNESS_UNVERIFIED,
            None,
            kind,
            structured_detail or "Structured financial data is unavailable.",
            official_update,
            structured_date,
        )

    structured_gate = _gate_document(structured_document, kind)
    if structured_gate["status"] != STATUS_OK:
        return _assessment(
            STATUS_INSUFFICIENT,
            FRESHNESS_UNVERIFIED,
            None,
            kind,
            "Structured critical financial data gate did not pass.",
            official_update,
            structured_date,
            structured_gate,
        )

    if official_latest_date == structured_date and official_document is not None:
        official_gate = _gate_document(official_document, kind)
        if official_gate["status"] == STATUS_OK:
            comparison = _compare_documents(official_document, structured_document, kind)
            if comparison == "CONFLICT":
                return _assessment(
                    STATUS_OK,
                    FRESHNESS_CURRENT_OFFICIAL,
                    official_document,
                    kind,
                    "Official parsed values override conflicting structured-source values for the same basis.",
                    official_update,
                    structured_date,
                    official_gate,
                    conflict="OFFICIAL_OVERRIDES_STRUCTURED",
                )
            return _assessment(
                STATUS_OK,
                FRESHNESS_CURRENT_OFFICIAL,
                official_document,
                kind,
                "Official parsed values are available for the latest disclosure.",
                official_update,
                structured_date,
                official_gate,
            )

    if not _coverage_confirmed(structured_date, official_scan_start, official_window_complete):
        return _assessment(
            STATUS_INSUFFICIENT,
            FRESHNESS_UNVERIFIED,
            None,
            kind,
            "Official-source coverage cannot prove that the structured values are current.",
            official_update,
            structured_date,
            structured_gate,
        )
    return _assessment(
        STATUS_OK,
        FRESHNESS_CURRENT_STRUCTURED,
        structured_document,
        kind,
        "No newer official financial update was found in the verified TDnet coverage window.",
        official_update,
        structured_date,
        structured_gate,
    )


def _parse_official_item(
    item: Mapping[str, Any], kind: str
) -> dict[str, Any] | None:
    content = str(item.get("content") or "")
    if not content or str((item.get("metadata") or {}).get("extraction_status")) not in {
        "OK",
        "FEED_CONTENT",
    }:
        return None
    timestamp = str(item.get("timestamp") or "")
    document = parse_financial_document(
        content,
        title=str(item.get("title") or ""),
        source=str(item.get("source") or ""),
        disclosure_timestamp=timestamp,
        source_url=item.get("url"),
    )
    target_end = _target_period_end(f"{item.get('title', '')} {content[:1200]}")
    if kind == "ACTUAL":
        records = [record for record in document.records if record.record_type == "ACTUAL"]
        if not records:
            return None
        records = [replace(record, target_period_end=target_end) for record in records]
        record = max(records, key=_record_period_key)
    else:
        sections = [record for record in document.records if record.record_type == "GUIDANCE"]
        if len(sections) != 1:
            return None
        flat = parse_financial_disclosure(
            content,
            title=str(item.get("title") or ""),
            source=str(item.get("source") or ""),
            disclosure_timestamp=timestamp,
            source_url=item.get("url"),
        )
        record = replace(
            sections[0],
            target_period_end=target_end,
            metrics=flat["guidance"],
            revision_reason=str(flat.get("revision_reason") or DATA_UNAVAILABLE),
            revision_reason_status=(
                "OK"
                if flat.get("revision_reason") not in {None, DATA_UNAVAILABLE, NOT_PROVIDED}
                else DATA_UNAVAILABLE
            ),
        )
    normalized = replace(
        document,
        records=(record,),
        source_type="OFFICIAL_DISCLOSURE",
        source_as_of=_official_date(item).isoformat() if _official_date(item) else None,
    )
    return normalized.to_dict()


def _resolve_official_documents(
    documents: Sequence[Mapping[str, Any]], kind: str
) -> tuple[Mapping[str, Any] | None, bool]:
    if not documents:
        return None, False
    valid = [document for document in documents if _gate_document(document, kind)["status"] == STATUS_OK]
    if not valid:
        return None, False
    first = valid[0]
    for other in valid[1:]:
        comparison = _compare_documents(first, other, kind)
        if comparison == "CONFLICT":
            return None, True
    return next((doc for doc in valid if doc.get("source") == "TDnet"), valid[0]), False


def _gate_document(document: Mapping[str, Any], kind: str) -> dict[str, Any]:
    record = _record(document, kind)
    critical_names = (
        ("revenue", "operating_profit", "net_income")
        if kind == "ACTUAL"
        else ("revenue", "operating_profit", "net_income", "eps")
    )
    available = [name for name in critical_names if _metric_ok((record or {}).get("metrics", {}).get(name), kind)]
    reasons = []
    if record is None:
        reasons.append("record unavailable")
    else:
        if record.get("period_type") not in _PERIOD_RANK:
            reasons.append("period unavailable")
        if not _strict_date(record.get("target_period_end")):
            reasons.append("target period unavailable")
        if kind == "ACTUAL" and record.get("accounting_standard") not in {
            "IFRS",
            "J_GAAP",
            "US_GAAP",
        }:
            reasons.append("accounting standard unavailable")
        if not document.get("source") or not document.get("disclosure_timestamp"):
            reasons.append("provenance unavailable")
        if len(available) < 2:
            reasons.append("fewer than two critical metrics available")
    return {
        "status": STATUS_OK if not reasons else STATUS_INSUFFICIENT,
        "critical_fields": {name: ("OK" if name in available else _metric_status((record or {}).get("metrics", {}).get(name), kind)) for name in critical_names},
        "available_count": len(available),
        "required_count": 2,
        "detail": "; ".join(reasons),
    }


def _metric_ok(metric: Any, kind: str) -> bool:
    if not isinstance(metric, Mapping):
        return False
    value = metric.get("current_value") if kind == "GUIDANCE" else metric
    return (
        metric.get("status") == STATUS_OK
        and isinstance(value, Mapping)
        and value.get("status") == STATUS_OK
        and value.get("value") is not None
        and value.get("unit") not in {None, "", DATA_UNAVAILABLE, "UNKNOWN"}
    ) if kind == "GUIDANCE" else (
        metric.get("status") == STATUS_OK
        and metric.get("value") is not None
        and metric.get("unit") not in {None, "", DATA_UNAVAILABLE, "UNKNOWN"}
    )


def _metric_status(metric: Any, kind: str) -> str:
    if not isinstance(metric, Mapping):
        return NOT_PROVIDED
    if kind == "GUIDANCE" and isinstance(metric.get("current_value"), Mapping):
        return str(metric["current_value"].get("status") or metric.get("status") or DATA_UNAVAILABLE)
    return str(metric.get("status") or DATA_UNAVAILABLE)


def _compare_documents(left: Mapping[str, Any], right: Mapping[str, Any], kind: str) -> str:
    left_record = _record(left, kind)
    right_record = _record(right, kind)
    if left_record is None or right_record is None:
        return "NOT_COMPARABLE"
    keys = ("period_type", "target_period_end", "scope", "accounting_standard", "currency", "unit")
    if any(
        left_record.get(key) in {None, "", "UNKNOWN", DATA_UNAVAILABLE}
        or left_record.get(key) != right_record.get(key)
        for key in keys
    ):
        return "NOT_COMPARABLE"
    compared = 0
    for name in set(left_record.get("metrics", {})) & set(right_record.get("metrics", {})):
        left_metric = left_record["metrics"][name]
        right_metric = right_record["metrics"][name]
        if not _metric_ok(left_metric, kind) or not _metric_ok(right_metric, kind):
            continue
        left_value = left_metric.get("current_value") if kind == "GUIDANCE" else left_metric
        right_value = right_metric.get("current_value") if kind == "GUIDANCE" else right_metric
        if left_metric.get("semantic_basis") and left_metric.get("semantic_basis") != right_metric.get("semantic_basis"):
            return "NOT_COMPARABLE"
        compared += 1
        if left_value.get("unit") != right_value.get("unit") or left_value.get("value") != right_value.get("value"):
            return "CONFLICT"
    return "MATCH" if compared else "NOT_COMPARABLE"


def _assessment(
    status: str,
    freshness: str,
    document: Mapping[str, Any] | None,
    kind: str,
    detail: str,
    official_update: Mapping[str, Any] | None,
    structured_date: date | None,
    gate: Mapping[str, Any] | None = None,
    *,
    conflict: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "freshness": freshness,
        "document": dict(document) if document is not None else None,
        "record_type": kind,
        "selected_source": document.get("source") if document else None,
        "selected_source_type": document.get("source_type") if document else None,
        "selected_disclosure_date": _document_date(document).isoformat() if _document_date(document) else None,
        "structured_latest_disclosure": structured_date.isoformat() if structured_date else None,
        "official_latest_update": dict(official_update) if official_update else None,
        "critical_gate": dict(gate or {}),
        "conflict": conflict,
        "detail": detail,
    }


def _official_update_summary(
    items: Sequence[Mapping[str, Any]], disclosure_date: date | None
) -> dict[str, Any] | None:
    if not items or disclosure_date is None:
        return None
    return {
        "disclosure_date": disclosure_date.isoformat(),
        "sources": sorted({str(item.get("source")) for item in items}),
        "event_types": sorted({_event_type(item) for item in items}),
        "titles": [str(item.get("title") or "") for item in items[:4]],
        "source_urls": [str(item.get("url")) for item in items[:4] if item.get("url")],
    }


def _coverage_confirmed(
    structured_date: date | None,
    official_scan_start: date,
    official_window_complete: bool,
) -> bool:
    return bool(
        structured_date is not None
        and official_window_complete
        and structured_date >= official_scan_start
    )


def _tdnet_coverage_complete(status: Mapping[str, Any] | None) -> bool:
    if not status or str(status.get("status")) != STATUS_OK:
        return False
    detail = str(status.get("detail") or "").lower()
    incomplete_markers = (
        "reached tdnet_max_pages_per_day",
        "retrieval_failed=true",
        "live_refresh_failed=true",
    )
    return not any(marker in detail for marker in incomplete_markers)


def _record(document: Mapping[str, Any], kind: str) -> Mapping[str, Any] | None:
    return next(
        (
            record
            for record in document.get("records", ())
            if isinstance(record, Mapping) and record.get("record_type") == kind
        ),
        None,
    )


def _record_period_key(record: FinancialRecord) -> tuple[date, int]:
    target = _strict_date(record.target_period_end) or date.min
    return target, _PERIOD_RANK.get(record.period_type, 0)


def _demonstrably_older(
    item: Mapping[str, Any],
    official: Mapping[str, Any] | None,
    structured: Mapping[str, Any] | None,
    kind: str,
) -> bool:
    official_key = _document_period_key(official, kind) or _official_period_hint(item, kind)
    structured_key = _document_period_key(structured, kind)
    return bool(
        official_key is not None
        and structured_key is not None
        and official_key < structured_key
    )


def _official_period_hint(
    item: Mapping[str, Any], kind: str
) -> tuple[date, int] | None:
    """Read an explicit fiscal-period identity from an official title only."""
    title = str(item.get("title") or "").translate(
        str.maketrans("０１２３４５６７８９", "0123456789")
    )
    target = _target_period_end(title)
    if target is None:
        return None
    if re.search(r"第\s*1\s*四半期", title):
        period = "Q1"
    elif re.search(r"第\s*2\s*四半期", title) or "中間" in title:
        period = "H1"
    elif re.search(r"第\s*3\s*四半期", title):
        period = "Q3"
    elif (kind == "GUIDANCE" and "通期" in title) or (
        kind == "ACTUAL" and ("通期決算" in title or "本決算" in title)
    ):
        period = "FY"
    else:
        return None
    return date.fromisoformat(target), _PERIOD_RANK[period]


def _document_period_key(
    document: Mapping[str, Any] | None, kind: str
) -> tuple[date, int] | None:
    if not document:
        return None
    record = _record(document, kind)
    if record is None:
        return None
    target = _strict_date(record.get("target_period_end"))
    rank = _PERIOD_RANK.get(str(record.get("period_type") or ""))
    if target is None or rank is None:
        return None
    return target, rank


def _target_period_end(text: str) -> str | None:
    match = _TARGET.search(text.translate(str.maketrans("０１２３４５６７８９", "0123456789")))
    if not match:
        return None
    year = int(match.group("year"))
    month = int(match.group("month"))
    if month < 1 or month > 12:
        return None
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def _official_date(item: Mapping[str, Any]) -> date | None:
    if item.get("source") == "Company IR":
        visible = f"{item.get('title', '')} {item.get('url', '')}"
        if not _EXPLICIT_DATE.search(visible):
            return None
    value = str((item.get("metadata") or {}).get("date") or item.get("timestamp") or "")[:10]
    return _strict_date(value)


def _document_date(document: Mapping[str, Any] | None) -> date | None:
    if not document:
        return None
    raw = str(document.get("disclosure_timestamp") or "").strip()
    parsed = _strict_date(raw[:10])
    if parsed is not None:
        return parsed
    try:
        timestamp = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return timestamp.date() if timestamp is not None else None


def _event_type(item: Mapping[str, Any]) -> str:
    return str((item.get("metadata") or {}).get("event_type") or item.get("source_type") or "")


def _strict_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None
