"""Pure, fail-closed selection of the latest EDINET DB Actual record.

The selector deliberately has no transport, normalization, provider-window,
or agent concerns.  It chooses by fiscal reporting period as of an explicit
cutoff, never by API array order or metric completeness.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any

STATUS_OK = "OK"
STATUS_DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
SELECTION_BASIS = "LATEST_FISCAL_PERIOD_AS_OF"

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PERIODS: dict[Any, tuple[str, int]] = {
    1: ("Q1", 1),
    2: ("H1", 2),
    3: ("Q3", 3),
    4: ("FY", 4),
    "1": ("Q1", 1),
    "Q1": ("Q1", 1),
    "2": ("H1", 2),
    "Q2": ("H1", 2),
    "H1": ("H1", 2),
    "3": ("Q3", 3),
    "Q3": ("Q3", 3),
    "4": ("FY", 4),
    "Q4": ("FY", 4),
    "FY": ("FY", 4),
}
_ACTUAL_FIELDS = (
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
_SOURCE_ID_FIELDS = ("source_record_id", "record_id", "id", "document_id")


@dataclass(frozen=True)
class LatestEarningsSelection:
    """Result of selecting one latest fiscal-period Actual record."""

    status: str
    record: dict[str, Any] | None
    issuer_id: str
    source_record_id: str | None
    fiscal_year_end: str | None
    period_type: str | None
    disclosure_date: str | None
    disclosure_precision: str | None
    selection_basis: str | None
    detail: str


@dataclass(frozen=True, order=True)
class _FiscalIdentity:
    fiscal_year_end: date
    period_rank: int


@dataclass(frozen=True)
class _FiscalParts:
    fiscal_year_end: date | None
    period_type: str | None
    period_rank: int | None

    @property
    def identity(self) -> _FiscalIdentity | None:
        if self.fiscal_year_end is None or self.period_rank is None:
            return None
        return _FiscalIdentity(self.fiscal_year_end, self.period_rank)


@dataclass(frozen=True)
class _DisclosureMoment:
    value: date | datetime
    precision: str


@dataclass(frozen=True)
class _Candidate:
    record: dict[str, Any]
    fiscal: _FiscalParts
    disclosure: _DisclosureMoment | None


def select_latest_earnings_record(
    records: Iterable[Mapping[str, Any]],
    *,
    issuer_id: str,
    analysis_as_of: date | datetime,
) -> LatestEarningsSelection:
    """Select the latest eligible Actual fiscal period without guessing.

    Fiscal identity is strictly ``fiscal_year_end + quarter``.  Metric
    completeness, API order, titles, and forecast fields never influence the
    selected period.
    """
    stable_issuer = str(issuer_id or "").strip()
    cutoff = _analysis_cutoff(analysis_as_of)
    if cutoff is None:
        return _failure(stable_issuer, "INVALID_ANALYSIS_AS_OF")

    try:
        raw_records = tuple(records)
    except TypeError:
        return _failure(stable_issuer, "NO_ELIGIBLE_RECORD")
    if not raw_records:
        return _failure(stable_issuer, "NO_ELIGIBLE_RECORD")

    candidates: list[_Candidate] = []
    for raw in raw_records:
        if not isinstance(raw, Mapping):
            return _failure(stable_issuer, "INVALID_FISCAL_PERIOD")
        copied = dict(raw)
        candidates.append(
            _Candidate(
                record=copied,
                fiscal=_parse_fiscal_parts(copied),
                disclosure=_parse_disclosure(copied),
            )
        )

    eligible: list[_Candidate] = []
    invalid_fiscal: list[_Candidate] = []
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
        if candidate.fiscal.identity is None:
            invalid_fiscal.append(candidate)
            continue
        eligible.append(candidate)

    if not eligible:
        if invalid_fiscal:
            return _failure(stable_issuer, "INVALID_FISCAL_PERIOD")
        if same_day_unresolved:
            if any(candidate.fiscal.identity is None for candidate in same_day_unresolved):
                return _failure(stable_issuer, "INVALID_FISCAL_PERIOD")
            return _failure(stable_issuer, "DATE_ONLY_SAME_DAY_UNRESOLVED")
        if invalid_temporal:
            return _failure(stable_issuer, "INVALID_TEMPORAL_DATA")
        if future_count:
            return _failure(stable_issuer, "FUTURE_ONLY")
        return _failure(stable_issuer, "NO_ELIGIBLE_RECORD")

    best_identity = max(candidate.fiscal.identity for candidate in eligible)
    assert best_identity is not None

    if any(_might_affect(candidate.fiscal, best_identity) for candidate in invalid_fiscal):
        return _failure(stable_issuer, "INVALID_FISCAL_PERIOD")

    for candidate in same_day_unresolved:
        if not _might_affect(candidate.fiscal, best_identity):
            continue
        if candidate.fiscal.identity is None:
            return _failure(stable_issuer, "INVALID_FISCAL_PERIOD")
        return _failure(stable_issuer, "DATE_ONLY_SAME_DAY_UNRESOLVED")

    if any(_might_affect(candidate.fiscal, best_identity) for candidate in invalid_temporal):
        return _failure(stable_issuer, "INVALID_TEMPORAL_DATA")

    latest = [candidate for candidate in eligible if candidate.fiscal.identity == best_identity]
    baseline, failure_detail = _resolve_latest_period(latest)
    if baseline is None:
        return _failure(stable_issuer, failure_detail)

    disclosure = baseline.disclosure
    assert disclosure is not None
    return LatestEarningsSelection(
        status=STATUS_OK,
        record=dict(baseline.record),
        issuer_id=stable_issuer,
        source_record_id=_source_record_id(baseline.record),
        fiscal_year_end=best_identity.fiscal_year_end.isoformat(),
        period_type=baseline.fiscal.period_type,
        disclosure_date=_format_disclosure(disclosure),
        disclosure_precision=disclosure.precision,
        selection_basis=SELECTION_BASIS,
        detail="",
    )


def _analysis_cutoff(value: date | datetime) -> date | datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value
    if isinstance(value, date):
        return value
    return None


def _parse_fiscal_parts(record: Mapping[str, Any]) -> _FiscalParts:
    fiscal_end = _strict_date(record.get("fiscal_year_end"))
    period = _parse_period(record.get("quarter"))
    if period is None:
        return _FiscalParts(fiscal_end, None, None)
    return _FiscalParts(fiscal_end, period[0], period[1])


def _parse_period(value: Any) -> tuple[str, int] | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().upper()
    return _PERIODS.get(value)


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

    parsed_timestamp = _aware_datetime(raw)
    if parsed_timestamp is not None:
        return _DisclosureMoment(parsed_timestamp, "TIMESTAMP")
    return None


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
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


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

    disclosure_date = (
        disclosure.value.date()
        if isinstance(disclosure.value, datetime)
        else disclosure.value
    )
    return "ELIGIBLE" if disclosure_date <= cutoff else "FUTURE"


def _might_affect(parts: _FiscalParts, best: _FiscalIdentity) -> bool:
    if parts.fiscal_year_end is None:
        return True
    if parts.fiscal_year_end < best.fiscal_year_end:
        return False
    if parts.fiscal_year_end > best.fiscal_year_end:
        return True
    return parts.period_rank is None or parts.period_rank >= best.period_rank


def _resolve_latest_period(candidates: list[_Candidate]) -> tuple[_Candidate | None, str]:
    baseline_candidates: list[_Candidate] = []
    correction_candidates: list[_Candidate] = []
    for candidate in candidates:
        correction = _explicit_bool(candidate.record.get("is_correction"))
        if correction is None and "is_correction" in candidate.record:
            return None, "CORRECTION_REQUIRES_RESOLUTION"
        if correction is True:
            correction_candidates.append(candidate)
        else:
            baseline_candidates.append(candidate)

    if not baseline_candidates:
        return None, "CORRECTION_REQUIRES_BASELINE"

    baseline = _deduplicate_baselines(baseline_candidates)
    if baseline is None:
        return None, "AMBIGUOUS_LATEST"

    if any(_correction_changes_actual(baseline.record, item.record) for item in correction_candidates):
        return None, "CORRECTION_REQUIRES_RESOLUTION"
    return baseline, ""


def _deduplicate_baselines(candidates: list[_Candidate]) -> _Candidate | None:
    if len(candidates) == 1:
        return candidates[0]
    first = candidates[0]
    if all(candidate.record == first.record for candidate in candidates[1:]):
        return first

    source_ids = tuple(_source_record_id(candidate.record) for candidate in candidates)
    if source_ids[0] is not None and len(set(source_ids)) == 1:
        return min(candidates, key=lambda candidate: _stable_payload(candidate.record))
    return None


def _correction_changes_actual(
    baseline: Mapping[str, Any], correction: Mapping[str, Any]
) -> bool:
    for field in _ACTUAL_FIELDS:
        if field in correction and (
            field not in baseline or correction.get(field) != baseline.get(field)
        ):
            return True
    return False


def _explicit_bool(value: Any) -> bool | None:
    if value is True or value == 1:
        return True
    if value is False or value == 0:
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    return None


def _source_record_id(record: Mapping[str, Any]) -> str | None:
    for field in _SOURCE_ID_FIELDS:
        value = record.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _stable_payload(record: Mapping[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, ensure_ascii=False, default=repr)


def _format_disclosure(disclosure: _DisclosureMoment) -> str:
    return disclosure.value.isoformat()


def _failure(issuer_id: str, detail: str) -> LatestEarningsSelection:
    return LatestEarningsSelection(
        status=STATUS_DATA_UNAVAILABLE,
        record=None,
        issuer_id=issuer_id,
        source_record_id=None,
        fiscal_year_end=None,
        period_type=None,
        disclosure_date=None,
        disclosure_precision=None,
        selection_basis=None,
        detail=detail,
    )
