"""Strict historical matching for normalized company guidance records.

The matcher is intentionally transport- and provider-agnostic.  It compares
two already-normalized ``FinancialRecord`` instances only when the provenance
needed to identify the issuer and disclosure order is explicit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from .financial_disclosure import (
    DATA_UNAVAILABLE,
    VALUE_ORIGIN_DERIVED,
    VALUE_ORIGIN_SOURCE,
    FinancialRecord,
)

_KNOWN_SCOPES = {"CONSOLIDATED", "NON_CONSOLIDATED"}
_KNOWN_STANDARDS = {"IFRS", "J_GAAP", "US_GAAP"}
_KNOWN_PERIODS = {"Q1", "H1", "Q3", "FY"}


@dataclass(frozen=True)
class GuidanceRecordContext:
    """A guidance record plus provenance not carried by FinancialRecord.

    ``issuer_id`` must be a stable issuer identifier, not a company name.
    ``source_record_id`` identifies the individual source record and is used
    only for derivation lineage; it is not used as the issuer identity.
    """

    record: FinancialRecord
    issuer_id: str | None
    source_record_id: str | None
    disclosure_timestamp: str | datetime | None
    metadata: Mapping[str, Any] = field(default_factory=dict)


def match_guidance_revision(
    previous_record: GuidanceRecordContext,
    current_record: GuidanceRecordContext,
) -> dict[str, Any]:
    """Compare two compatible guidance records and derive metric revisions.

    All record-level identity, period, scope, accounting, unit, and chronology
    checks are strict.  Any mismatch returns ``DATA_UNAVAILABLE`` rather than
    guessing.  Direct previous/current values retain ``SOURCE`` origin, while
    each calculated direction is marked ``DERIVED``.
    """

    failure = _validate_records(previous_record, current_record)
    if failure is not None:
        return _failure(failure)

    previous = previous_record.record
    current = current_record.record
    derivation = {
        "method": "COMPARE_MATCHED_GUIDANCE_RECORDS",
        "previous_source_record_id": previous_record.source_record_id,
        "current_source_record_id": current_record.source_record_id,
    }
    revisions: dict[str, dict[str, Any]] = {}

    for metric_name in sorted(set(previous.metrics) & set(current.metrics)):
        previous_metric = previous.metrics[metric_name]
        current_metric = current.metrics[metric_name]
        if not _metric_is_available(previous_metric) or not _metric_is_available(
            current_metric
        ):
            continue

        previous_value = previous_metric["current_value"]
        current_value = current_metric["current_value"]
        semantic_failure = _validate_metric_pair(
            metric_name,
            previous_metric,
            current_metric,
            previous_value,
            current_value,
        )
        if semantic_failure is not None:
            return _failure(semantic_failure)

        previous_number = _decimal(previous_value.get("value"))
        current_number = _decimal(current_value.get("value"))
        if previous_number is None or current_number is None:
            continue

        revisions[metric_name] = {
            "status": "OK",
            "previous_value": _source_value(previous_value),
            "current_value": _source_value(current_value),
            "revision_direction": _direction(previous_number, current_number),
            "semantic_basis": previous_metric.get("semantic_basis"),
            "value_origin": VALUE_ORIGIN_DERIVED,
            "derivation": dict(derivation),
        }

    if not revisions:
        return _failure("no comparable guidance metrics")

    return {
        "status": "OK",
        "metrics": revisions,
        "value_origin": VALUE_ORIGIN_DERIVED,
        "derivation": derivation,
    }


def _validate_records(
    previous: GuidanceRecordContext,
    current: GuidanceRecordContext,
) -> str | None:
    if not isinstance(previous, GuidanceRecordContext) or not isinstance(
        current, GuidanceRecordContext
    ):
        return "guidance provenance context is required"
    if previous.record.record_type != "GUIDANCE" or current.record.record_type != "GUIDANCE":
        return "both records must be GUIDANCE"
    if _is_correction(previous.metadata) or _is_correction(current.metadata):
        return "correction records cannot establish a guidance revision"
    if not previous.issuer_id or not current.issuer_id:
        return "stable issuer identity is unavailable"
    if previous.issuer_id != current.issuer_id:
        return "issuer mismatch"
    if not previous.source_record_id or not current.source_record_id:
        return "source record identity is unavailable"

    target_failure = _validate_target_period(previous.record, current.record)
    if target_failure is not None:
        return target_failure

    if (
        previous.record.period_type not in _KNOWN_PERIODS
        or current.record.period_type not in _KNOWN_PERIODS
        or previous.record.period_type != current.record.period_type
    ):
        return "period mismatch or unavailable period"
    if (
        previous.record.scope not in _KNOWN_SCOPES
        or current.record.scope not in _KNOWN_SCOPES
        or previous.record.scope != current.record.scope
    ):
        return "scope mismatch or unavailable scope"
    if (
        previous.record.accounting_standard not in _KNOWN_STANDARDS
        or current.record.accounting_standard not in _KNOWN_STANDARDS
        or previous.record.accounting_standard != current.record.accounting_standard
    ):
        return "accounting standard mismatch or unavailable standard"
    if (
        not previous.record.currency
        or previous.record.currency in {"UNKNOWN", DATA_UNAVAILABLE}
        or previous.record.currency != current.record.currency
    ):
        return "currency mismatch or unavailable currency"
    if (
        not previous.record.unit
        or previous.record.unit in {"UNKNOWN", DATA_UNAVAILABLE}
        or previous.record.unit != current.record.unit
    ):
        return "record unit mismatch or unavailable unit"

    previous_time = _timestamp(previous.disclosure_timestamp)
    current_time = _timestamp(current.disclosure_timestamp)
    if (
        previous_time is None
        or current_time is None
        or (previous_time.tzinfo is None) != (current_time.tzinfo is None)
        or previous_time >= current_time
    ):
        return "disclosure timestamps are unavailable or not strictly increasing"
    return None


def _validate_target_period(previous: FinancialRecord, current: FinancialRecord) -> str | None:
    previous_end = previous.target_period_end
    current_end = current.target_period_end
    if previous_end is not None or current_end is not None:
        if not previous_end or not current_end or previous_end != current_end:
            return "target fiscal period mismatch or incomplete target period"
        return None
    if (
        not previous.fiscal_year
        or previous.fiscal_year in {"UNKNOWN", DATA_UNAVAILABLE}
        or previous.fiscal_year != current.fiscal_year
    ):
        return "fiscal year mismatch or unavailable fiscal year"
    return None


def _validate_metric_pair(
    metric_name: str,
    previous_metric: Mapping[str, Any],
    current_metric: Mapping[str, Any],
    previous_value: Mapping[str, Any],
    current_value: Mapping[str, Any],
) -> str | None:
    previous_basis = previous_metric.get("semantic_basis")
    current_basis = current_metric.get("semantic_basis")
    if not previous_basis or previous_basis != current_basis:
        return f"semantic basis mismatch for {metric_name}"
    if previous_value.get("value_origin") != VALUE_ORIGIN_SOURCE or current_value.get(
        "value_origin"
    ) != VALUE_ORIGIN_SOURCE:
        return f"non-source guidance value for {metric_name}"
    previous_unit = previous_value.get("unit")
    current_unit = current_value.get("unit")
    if not previous_unit or previous_unit != current_unit:
        return f"metric unit mismatch or unavailable unit for {metric_name}"
    return None


def _metric_is_available(metric: Mapping[str, Any]) -> bool:
    current_value = metric.get("current_value")
    return (
        metric.get("status") == "OK"
        and isinstance(current_value, Mapping)
        and current_value.get("status") == "OK"
    )


def _source_value(value: Mapping[str, Any]) -> dict[str, Any]:
    copied = dict(value)
    copied["value_origin"] = VALUE_ORIGIN_SOURCE
    return copied


def _direction(previous: Decimal, current: Decimal) -> str:
    if current > previous:
        return "INCREASED"
    if current < previous:
        return "DECREASED"
    return "UNCHANGED"


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _timestamp(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed


def _is_correction(metadata: Mapping[str, Any]) -> bool:
    value = metadata.get("is_correction")
    if value is True or value == 1:
        return True
    return isinstance(value, str) and value.strip().lower() in {"true", "1"}


def _failure(reason: str) -> dict[str, Any]:
    return {
        "status": DATA_UNAVAILABLE,
        "reason": reason,
        "metrics": {},
        "value_origin": "UNKNOWN",
        "derivation": None,
    }
