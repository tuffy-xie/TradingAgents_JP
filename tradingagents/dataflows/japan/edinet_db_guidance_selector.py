"""Pure, fail-closed selection of current EDINET DB company Guidance.

Actual-period selection belongs to ``edinet_db_selector``.  This selector
instead follows the fiscal target of ``forecast_*`` fields and then chooses the
latest eligible disclosure for that target.  It never uses provider order,
metric completeness, or Actual-period rank as a proxy for current Guidance.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any

STATUS_OK = "OK"
STATUS_DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
STATUS_NOT_PROVIDED = "NOT_PROVIDED"
SELECTION_BASIS = "LATEST_GUIDANCE_DISCLOSURE_AS_OF"

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FORECAST_FIELDS = (
    "forecast_revenue",
    "forecast_operating_income",
    "forecast_ordinary_income",
    "forecast_net_income",
    "forecast_net_income_attributable_to_parent",
    "forecast_eps",
)
_SOURCE_ID_FIELDS = ("source_record_id", "record_id", "id", "document_id")
_Q1_Q3 = {1, 2, 3, "1", "2", "3", "Q1", "Q2", "Q3", "H1"}
_FY = {4, "4", "Q4", "FY"}


@dataclass(frozen=True)
class LatestGuidanceSelection:
    """Result of selecting one latest FY Guidance disclosure."""

    status: str
    record: dict[str, Any] | None
    issuer_id: str
    source_record_id: str | None
    target_period_end: str | None
    period_type: str | None
    disclosure_date: str | None
    disclosure_precision: str | None
    selection_basis: str | None
    detail: str


@dataclass(frozen=True)
class _DisclosureMoment:
    value: date | datetime
    precision: str


@dataclass(frozen=True)
class _Candidate:
    record: dict[str, Any]
    target_period_end: date | None
    disclosure: _DisclosureMoment | None


def select_latest_guidance_record(
    records: Iterable[Mapping[str, Any]],
    *,
    issuer_id: str,
    analysis_as_of: date | datetime,
) -> LatestGuidanceSelection:
    """Select current company Guidance independently from latest Actual.

    Q1-Q3 ``forecast_*`` values target the record's ``fiscal_year_end``.  Q4
    forecasts target the next fiscal year, so Q4 requires an explicit forecast
    target field and is never advanced by a guessed ``+1 year`` calculation.
    """
    stable_issuer = str(issuer_id or "").strip()
    cutoff = _analysis_cutoff(analysis_as_of)
    if cutoff is None:
        return _failure(stable_issuer, "INVALID_ANALYSIS_AS_OF")

    try:
        raw_records = tuple(records)
    except TypeError:
        return _failure(stable_issuer, "NO_GUIDANCE_RECORD")
    forecast_records = [raw for raw in raw_records if _has_fy_guidance_contract(raw)]
    if not forecast_records:
        return _failure(stable_issuer, "GUIDANCE_NOT_PROVIDED", STATUS_NOT_PROVIDED)

    candidates: list[_Candidate] = []
    for raw in forecast_records:
        if not isinstance(raw, Mapping):
            return _failure(stable_issuer, "INVALID_GUIDANCE_TARGET")
        copied = dict(raw)
        candidates.append(
            _Candidate(
                copied,
                _guidance_target_period_end(copied),
                _parse_disclosure(copied),
            )
        )

    eligible: list[_Candidate] = []
    invalid_target: list[_Candidate] = []
    invalid_temporal: list[_Candidate] = []
    same_day_unresolved: list[_Candidate] = []
    future_count = 0
    for candidate in candidates:
        if candidate.disclosure is None:
            invalid_temporal.append(candidate)
            continue
        relation = _temporal_relation(candidate.disclosure, cutoff)
        if relation == "FUTURE":
            future_count += 1
            continue
        if relation == "SAME_DAY_UNRESOLVED":
            same_day_unresolved.append(candidate)
            continue
        if candidate.target_period_end is None:
            invalid_target.append(candidate)
            continue
        eligible.append(candidate)

    if not eligible:
        if invalid_target:
            return _failure(stable_issuer, "INVALID_GUIDANCE_TARGET")
        if same_day_unresolved:
            return _failure(stable_issuer, "DATE_ONLY_SAME_DAY_UNRESOLVED")
        if invalid_temporal:
            return _failure(stable_issuer, "INVALID_TEMPORAL_DATA")
        if future_count:
            return _failure(stable_issuer, "FUTURE_ONLY")
        return _failure(stable_issuer, "NO_GUIDANCE_RECORD")

    best_target = max(candidate.target_period_end for candidate in eligible)
    assert best_target is not None
    latest_eligible_day = max(_disclosure_day(candidate.disclosure) for candidate in eligible)
    if any(
        _disclosure_day(candidate.disclosure) >= latest_eligible_day
        for candidate in invalid_target
    ):
        return _failure(stable_issuer, "INVALID_GUIDANCE_TARGET")
    if same_day_unresolved:
        return _failure(stable_issuer, "DATE_ONLY_SAME_DAY_UNRESOLVED")
    if invalid_temporal:
        return _failure(stable_issuer, "INVALID_TEMPORAL_DATA")

    target_candidates = [
        candidate for candidate in eligible if candidate.target_period_end == best_target
    ]
    latest_moment = max(_moment_key(candidate.disclosure) for candidate in target_candidates)
    latest = [
        candidate
        for candidate in target_candidates
        if _moment_key(candidate.disclosure) == latest_moment
    ]
    selected = _deduplicate_guidance(latest)
    if selected is None:
        return _failure(stable_issuer, "AMBIGUOUS_LATEST_GUIDANCE")

    disclosure = selected.disclosure
    assert disclosure is not None
    guidance_status = (
        STATUS_OK if any(selected.record.get(field) is not None for field in _FORECAST_FIELDS)
        else STATUS_NOT_PROVIDED
    )
    return LatestGuidanceSelection(
        status=guidance_status,
        record=dict(selected.record),
        issuer_id=stable_issuer,
        source_record_id=_source_record_id(selected.record),
        target_period_end=best_target.isoformat(),
        period_type="FY",
        disclosure_date=disclosure.value.isoformat(),
        disclosure_precision=disclosure.precision,
        selection_basis=SELECTION_BASIS,
        detail="" if guidance_status == STATUS_OK else "GUIDANCE_NOT_PROVIDED",
    )


def _has_fy_guidance_contract(raw: Any) -> bool:
    """Return whether a raw record can safely represent annual Guidance.

    A Q1-Q3 earnings record with Actual values carries annual forecast fields.
    A forecast-only Q1-Q3 record instead describes that quarter/H1 target and
    must not compete with annual Guidance.  An explicit guidance target remains
    authoritative for forecast-only records.
    """
    if not isinstance(raw, Mapping) or not any(
        field in raw and raw.get(field) is not None for field in _FORECAST_FIELDS
    ):
        return False
    if _resolve_explicit_target(raw) is not None:
        return True
    quarter = raw.get("quarter")
    if isinstance(quarter, str):
        quarter = quarter.strip().upper()
    if quarter in _FY:
        # Keep the record so the explicit-target validator can fail closed;
        # Q4 next-FY targets must never be guessed.
        return True
    return quarter in _Q1_Q3 and _has_actual_value(raw)


def _has_actual_value(record: Mapping[str, Any]) -> bool:
    fields = (
        "revenue",
        "operating_income",
        "ordinary_income",
        "net_income",
        "net_income_attributable_to_parent",
        "profit_attributable_to_owners_of_parent",
        "eps",
        "profit_ifrs",
        "total_period_profit",
        "profit_total",
    )
    return any(record.get(field) is not None for field in fields)


def _guidance_target_period_end(record: Mapping[str, Any]) -> date | None:
    explicit = _resolve_explicit_target(record)
    if explicit is not None:
        return explicit
    if any(
        field in record and record.get(field) is not None
        for field in ("forecast_target_period_end", "guidance_target_period_end")
    ):
        return None
    quarter = record.get("quarter")
    if isinstance(quarter, str):
        quarter = quarter.strip().upper()
    if quarter in _Q1_Q3:
        return _strict_date(record.get("fiscal_year_end"))
    if quarter in _FY:
        return None
    return None


def _resolve_explicit_target(record: Mapping[str, Any]) -> date | None:
    values = [
        _strict_date(record.get(field))
        for field in ("forecast_target_period_end", "guidance_target_period_end")
        if field in record and record.get(field) is not None
    ]
    if not values or any(value is None for value in values) or len(set(values)) != 1:
        return None
    return values[0]


def _analysis_cutoff(value: date | datetime) -> date | datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None and value.utcoffset() is not None else None
    return value if isinstance(value, date) else None


def _strict_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or _DATE_PATTERN.fullmatch(value.strip()) is None:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _parse_disclosure(record: Mapping[str, Any]) -> _DisclosureMoment | None:
    if "disclosure_timestamp" in record and record.get("disclosure_timestamp") is not None:
        parsed = _aware_datetime(record.get("disclosure_timestamp"))
        return _DisclosureMoment(parsed, "TIMESTAMP") if parsed is not None else None
    value = record.get("disclosure_date")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return _DisclosureMoment(value, "TIMESTAMP")
    if isinstance(value, date):
        return _DisclosureMoment(value, "DATE")
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    parsed_date = _strict_date(raw)
    if parsed_date is not None:
        return _DisclosureMoment(parsed_date, "DATE")
    try:
        rfc1123 = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        rfc1123 = None
    if rfc1123 is not None and rfc1123.tzinfo is not None:
        return _DisclosureMoment(rfc1123.date(), "DATE")
    timestamp = _aware_datetime(raw)
    return _DisclosureMoment(timestamp, "TIMESTAMP") if timestamp is not None else None


def _aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _temporal_relation(disclosure: _DisclosureMoment, cutoff: date | datetime) -> str:
    if isinstance(cutoff, datetime):
        if disclosure.precision == "TIMESTAMP":
            assert isinstance(disclosure.value, datetime)
            return "ELIGIBLE" if disclosure.value <= cutoff else "FUTURE"
        assert isinstance(disclosure.value, date) and not isinstance(disclosure.value, datetime)
        if disclosure.value < cutoff.date():
            return "ELIGIBLE"
        if disclosure.value > cutoff.date():
            return "FUTURE"
        return "SAME_DAY_UNRESOLVED"
    disclosure_date = disclosure.value.date() if isinstance(disclosure.value, datetime) else disclosure.value
    return "ELIGIBLE" if disclosure_date <= cutoff else "FUTURE"


def _moment_key(disclosure: _DisclosureMoment | None) -> tuple[date, str]:
    assert disclosure is not None
    day = disclosure.value.date() if isinstance(disclosure.value, datetime) else disclosure.value
    instant = (
        disclosure.value.astimezone(UTC).isoformat()
        if isinstance(disclosure.value, datetime)
        else ""
    )
    return day, instant


def _disclosure_day(disclosure: _DisclosureMoment | None) -> date:
    assert disclosure is not None
    return disclosure.value.date() if isinstance(disclosure.value, datetime) else disclosure.value


def _deduplicate_guidance(candidates: list[_Candidate]) -> _Candidate | None:
    if len(candidates) == 1:
        return candidates[0]
    first = candidates[0]
    first_payload = _guidance_payload(first.record)
    if all(_guidance_payload(candidate.record) == first_payload for candidate in candidates[1:]):
        return min(candidates, key=lambda candidate: _stable_payload(candidate.record))
    return None


def _guidance_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "forecast": {field: record.get(field) for field in _FORECAST_FIELDS},
        "accounting_standard": record.get("accounting_standard"),
        "is_consolidated": record.get("is_consolidated"),
        "currency": record.get("currency"),
        "unit": record.get("unit"),
    }


def _source_record_id(record: Mapping[str, Any]) -> str | None:
    for field in _SOURCE_ID_FIELDS:
        value = record.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _stable_payload(record: Mapping[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, ensure_ascii=False, default=repr)


def _failure(
    issuer_id: str,
    detail: str,
    status: str = STATUS_DATA_UNAVAILABLE,
) -> LatestGuidanceSelection:
    return LatestGuidanceSelection(
        status=status,
        record=None,
        issuer_id=issuer_id,
        source_record_id=None,
        target_period_end=None,
        period_type=None,
        disclosure_date=None,
        disclosure_precision=None,
        selection_basis=None,
        detail=detail,
    )
