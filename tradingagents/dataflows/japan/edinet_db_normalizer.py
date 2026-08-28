"""Pure normalization for EDINET DB earnings records.

This module deliberately has no HTTP, environment-variable, cache, or
provider-status concerns.  It translates a single structured EDINET DB record
into the existing financial-disclosure schema without replacing TDnet or
Company IR official disclosures.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from .financial_disclosure import (
    DATA_UNAVAILABLE,
    NOT_APPLICABLE,
    NOT_PROVIDED,
    VALUE_ORIGIN_SOURCE,
    FinancialDocument,
    FinancialRecord,
)

_ACTUAL_FIELDS: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue",),
    "operating_profit": ("operating_income",),
    "net_income": (
        "net_income",
        "net_income_attributable_to_parent",
        "profit_attributable_to_owners_of_parent",
    ),
    "profit_total": ("profit_ifrs", "total_period_profit", "profit_total"),
    "eps": ("eps",),
}
_GUIDANCE_FIELDS: dict[str, tuple[str, ...]] = {
    "revenue": ("forecast_revenue",),
    "operating_profit": ("forecast_operating_income",),
    "net_income": (
        "forecast_net_income",
        "forecast_net_income_attributable_to_parent",
    ),
    "eps": ("forecast_eps",),
}
_CANONICAL_STANDARDS = {
    "IFRS": "IFRS",
    "JP": "J_GAAP",
    "JP_GAAP": "J_GAAP",
    "J_GAAP": "J_GAAP",
    "JAPANESE_GAAP": "J_GAAP",
    "US_GAAP": "US_GAAP",
}
_PERIODS = {
    "1": "Q1",
    "Q1": "Q1",
    "2": "H1",
    "Q2": "H1",
    "H1": "H1",
    "3": "Q3",
    "Q3": "Q3",
    "4": "FY",
    "Q4": "FY",
    "FY": "FY",
}
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_accounting_standard(value: Any) -> str:
    """Return an allowlisted accounting-standard value without inference."""
    normalized = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return _CANONICAL_STANDARDS.get(normalized, "UNKNOWN")


def normalize_scope(value: Any) -> tuple[str, str]:
    """Normalize explicit consolidated flags; ambiguous values remain unknown."""
    if value is True or value == 1:
        return "CONSOLIDATED", "SOURCE"
    if value is False or value == 0:
        return "NON_CONSOLIDATED", "SOURCE"
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1"}:
            return "CONSOLIDATED", "SOURCE"
        if normalized in {"false", "0"}:
            return "NON_CONSOLIDATED", "SOURCE"
    return "UNKNOWN", "UNKNOWN"


def normalize_actual_metrics(raw: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalize direct Actual values without deriving ratios or revisions."""
    standard = normalize_accounting_standard(raw.get("accounting_standard"))
    amount_unit = _text_or_none(raw.get("unit"))
    eps_unit = _text_or_none(raw.get("eps_unit")) or "円"

    metrics = {
        "revenue": _direct_metric(raw, _ACTUAL_FIELDS["revenue"], amount_unit, "REVENUE"),
        "operating_profit": _direct_metric(
            raw, _ACTUAL_FIELDS["operating_profit"], amount_unit, "OPERATING_PROFIT"
        ),
        "net_income": _direct_metric(
            raw,
            _ACTUAL_FIELDS["net_income"],
            amount_unit,
            "PARENT_ATTRIBUTABLE_PROFIT",
        ),
        "profit_total": _direct_metric(
            raw, _ACTUAL_FIELDS["profit_total"], amount_unit, "TOTAL_PERIOD_PROFIT"
        ),
        "eps": _direct_metric(raw, _ACTUAL_FIELDS["eps"], eps_unit, "BASIC_EPS"),
    }

    if standard == "J_GAAP":
        metrics["ordinary_profit"] = _direct_metric(
            raw, ("ordinary_income",), amount_unit, "ORDINARY_PROFIT"
        )
    elif standard == "IFRS":
        metrics["ordinary_profit"] = _not_applicable("ORDINARY_PROFIT")
    else:
        metrics["ordinary_profit"] = _unavailable("ORDINARY_PROFIT")

    return metrics


def normalize_actual_record(raw: Mapping[str, Any]) -> FinancialRecord:
    """Build one Actual record from one EDINET DB earnings record."""
    standard = normalize_accounting_standard(raw.get("accounting_standard"))
    scope, scope_origin = normalize_scope(raw.get("is_consolidated"))
    period_type = _resolve_period(raw, "period", "period_type", "quarter")
    return FinancialRecord(
        record_type="ACTUAL",
        fiscal_year=_normalize_fiscal_year(raw.get("fiscal_year")),
        period_type=period_type,
        period_basis=_period_basis(period_type),
        scope=scope,
        accounting_standard=standard,
        currency=_normalize_currency(raw.get("currency")),
        unit=_text_or_none(raw.get("unit")) or DATA_UNAVAILABLE,
        metrics=normalize_actual_metrics(raw),
        revision_reason=NOT_APPLICABLE,
        section_text="",
        target_period_end=_resolve_date_fields(raw, "target_period_end", "fiscal_year_end"),
        scope_origin=scope_origin,
        revision_reason_status=NOT_APPLICABLE,
    )


def normalize_financial_document(
    raw: Mapping[str, Any],
    *,
    fetched_at: str | None = None,
) -> FinancialDocument:
    """Build an EDINET DB structured-source document without transport work."""
    records: list[FinancialRecord] = []
    if _has_any_field(raw, _ACTUAL_FIELDS.values()) or "ordinary_income" in raw:
        records.append(normalize_actual_record(raw))
    if _has_any_field(raw, _GUIDANCE_FIELDS.values()) or "forecast_ordinary_income" in raw:
        records.append(normalize_guidance_record(raw))

    return FinancialDocument(
        status="OK" if records else DATA_UNAVAILABLE,
        source="EDINET DB",
        title=_text_or_none(raw.get("title")) or "EDINET DB earnings record",
        disclosure_timestamp=_text_or_none(
            raw.get("disclosure_timestamp") or raw.get("disclosure_date")
        ),
        source_url=_text_or_none(
            raw.get("source_url") or raw.get("source_pdf_url") or raw.get("pdf_url")
        ),
        accounting_standard=normalize_accounting_standard(raw.get("accounting_standard")),
        records=tuple(records),
        source_type="STRUCTURED_SOURCE",
        fetched_at=fetched_at,
        source_as_of=_text_or_none(raw.get("source_as_of")),
        source_record_id=_text_or_none(raw.get("source_record_id") or raw.get("record_id")),
    )


def normalize_guidance_record(raw: Mapping[str, Any]) -> FinancialRecord:
    """Build Current Guidance only; historical matching belongs to a later task."""
    standard = normalize_accounting_standard(raw.get("accounting_standard"))
    scope, scope_origin = normalize_scope(raw.get("is_consolidated"))
    period_type = _guidance_period(raw)
    return FinancialRecord(
        record_type="GUIDANCE",
        fiscal_year=_normalize_fiscal_year(
            raw.get("forecast_fiscal_year") or raw.get("fiscal_year")
        ),
        period_type=period_type,
        period_basis=_period_basis(period_type),
        scope=scope,
        accounting_standard=standard,
        currency=_normalize_currency(raw.get("currency")),
        unit=_text_or_none(raw.get("unit")) or DATA_UNAVAILABLE,
        metrics=_normalize_guidance_metrics(raw, standard),
        revision_reason=NOT_PROVIDED,
        section_text="",
        target_period_end=_guidance_target_period_end(raw),
        scope_origin=scope_origin,
        revision_reason_status=NOT_PROVIDED,
    )


def _normalize_guidance_metrics(
    raw: Mapping[str, Any], accounting_standard: str
) -> dict[str, dict[str, Any]]:
    amount_unit = _text_or_none(raw.get("unit"))
    eps_unit = _text_or_none(raw.get("eps_unit")) or "円"
    metrics = {
        "revenue": _current_guidance_metric(
            raw, _GUIDANCE_FIELDS["revenue"], amount_unit, "REVENUE"
        ),
        "operating_profit": _current_guidance_metric(
            raw,
            _GUIDANCE_FIELDS["operating_profit"],
            amount_unit,
            "OPERATING_PROFIT",
        ),
        "net_income": _current_guidance_metric(
            raw,
            _GUIDANCE_FIELDS["net_income"],
            amount_unit,
            "PARENT_ATTRIBUTABLE_PROFIT",
        ),
        "eps": _current_guidance_metric(raw, _GUIDANCE_FIELDS["eps"], eps_unit, "BASIC_EPS"),
    }
    if accounting_standard == "J_GAAP":
        metrics["ordinary_profit"] = _current_guidance_metric(
            raw, ("forecast_ordinary_income",), amount_unit, "ORDINARY_PROFIT"
        )
    elif accounting_standard == "IFRS":
        metrics["ordinary_profit"] = _guidance_not_applicable("ORDINARY_PROFIT")
    else:
        metrics["ordinary_profit"] = _guidance_unavailable("ORDINARY_PROFIT")
    return metrics


def _direct_metric(
    raw: Mapping[str, Any], field_names: tuple[str, ...], unit: str | None, semantic_basis: str
) -> dict[str, Any]:
    source_field, raw_value = _first_present(raw, field_names)
    if source_field is None:
        return _not_provided(semantic_basis)
    value = _number(raw_value)
    if value is None:
        return _unavailable(semantic_basis, source_field=source_field, raw_value=raw_value)
    return {
        "status": "OK",
        "value": value,
        "raw_value": raw_value,
        "unit": unit,
        "source_field": source_field,
        "semantic_basis": semantic_basis,
        "value_origin": VALUE_ORIGIN_SOURCE,
        "derivation": None,
    }


def _current_guidance_metric(
    raw: Mapping[str, Any], field_names: tuple[str, ...], unit: str | None, semantic_basis: str
) -> dict[str, Any]:
    current = _direct_metric(raw, field_names, unit, semantic_basis)
    return {
        "status": current["status"],
        "previous_value": None,
        "current_value": current if current["status"] == "OK" else None,
        "revision_direction": None,
        "source_field": current["source_field"],
        "semantic_basis": semantic_basis,
        "value_origin": VALUE_ORIGIN_SOURCE,
        "derivation": None,
    }


def _not_provided(semantic_basis: str) -> dict[str, Any]:
    return _metric_with_status(NOT_PROVIDED, semantic_basis)


def _unavailable(
    semantic_basis: str, *, source_field: str | None = None, raw_value: Any = None
) -> dict[str, Any]:
    return _metric_with_status(
        DATA_UNAVAILABLE,
        semantic_basis,
        source_field=source_field,
        raw_value=raw_value,
    )


def _not_applicable(semantic_basis: str) -> dict[str, Any]:
    return _metric_with_status(NOT_APPLICABLE, semantic_basis)


def _metric_with_status(
    status: str,
    semantic_basis: str,
    *,
    source_field: str | None = None,
    raw_value: Any = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "value": None,
        "raw_value": raw_value,
        "unit": None,
        "source_field": source_field,
        "semantic_basis": semantic_basis,
        "value_origin": VALUE_ORIGIN_SOURCE,
        "derivation": None,
    }


def _guidance_not_applicable(semantic_basis: str) -> dict[str, Any]:
    return _guidance_with_status(NOT_APPLICABLE, semantic_basis)


def _guidance_unavailable(semantic_basis: str) -> dict[str, Any]:
    return _guidance_with_status(DATA_UNAVAILABLE, semantic_basis)


def _guidance_with_status(status: str, semantic_basis: str) -> dict[str, Any]:
    return {
        "status": status,
        "previous_value": None,
        "current_value": None,
        "revision_direction": None,
        "source_field": None,
        "semantic_basis": semantic_basis,
        "value_origin": VALUE_ORIGIN_SOURCE,
        "derivation": None,
    }


def _first_present(raw: Mapping[str, Any], field_names: tuple[str, ...]) -> tuple[str | None, Any]:
    for field_name in field_names:
        if field_name in raw and raw[field_name] is not None:
            return field_name, raw[field_name]
    return None, None


def _has_any_field(raw: Mapping[str, Any], groups: Any) -> bool:
    return any(field_name in raw and raw[field_name] is not None for group in groups for field_name in group)


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        normalized = value.strip().replace(",", "")
        if not normalized:
            return None
        try:
            return float(normalized)
        except ValueError:
            return None
    return None


def _normalize_period(value: Any) -> str:
    normalized = str(value or "").strip().upper().replace(" ", "")
    return _PERIODS.get(normalized, DATA_UNAVAILABLE)


def _resolve_period(raw: Mapping[str, Any], *field_names: str) -> str:
    values = [
        _normalize_period(raw[field_name])
        for field_name in field_names
        if field_name in raw and raw[field_name] is not None
    ]
    if not values or DATA_UNAVAILABLE in values or len(set(values)) != 1:
        return DATA_UNAVAILABLE
    return values[0]


def _guidance_period(raw: Mapping[str, Any]) -> str:
    explicit_fields = ("forecast_period", "forecast_period_type", "guidance_period")
    explicit_period = _resolve_period(raw, *explicit_fields)
    has_explicit = any(field in raw and raw[field] is not None for field in explicit_fields)
    live_actual_period = _resolve_period(raw, "quarter")

    if has_explicit:
        if explicit_period == DATA_UNAVAILABLE:
            return DATA_UNAVAILABLE
        if live_actual_period != DATA_UNAVAILABLE and explicit_period != "FY":
            return DATA_UNAVAILABLE
        return explicit_period
    return "FY" if live_actual_period != DATA_UNAVAILABLE else DATA_UNAVAILABLE


def _period_basis(period_type: str) -> str:
    if period_type == "FY":
        return "FULL_YEAR"
    if period_type in {"Q1", "H1", "Q3"}:
        return "CUMULATIVE"
    return DATA_UNAVAILABLE


def _guidance_target_period_end(raw: Mapping[str, Any]) -> str | None:
    explicit_fields = ("forecast_target_period_end", "guidance_target_period_end")
    explicit_target = _resolve_date_fields(raw, *explicit_fields)
    has_explicit = any(field in raw and raw[field] is not None for field in explicit_fields)
    actual_period = _resolve_period(raw, "quarter")

    if has_explicit:
        if explicit_target is None:
            return None
        if actual_period in {"Q1", "H1", "Q3"} and "fiscal_year_end" in raw:
            live_target = _resolve_date_fields(raw, "fiscal_year_end")
            if live_target is None or live_target != explicit_target:
                return None
        return explicit_target

    if actual_period in {"Q1", "H1", "Q3"}:
        return _resolve_date_fields(raw, "fiscal_year_end")
    return None


def _resolve_date_fields(raw: Mapping[str, Any], *field_names: str) -> str | None:
    values = [
        _strict_date(raw[field_name])
        for field_name in field_names
        if field_name in raw and raw[field_name] is not None
    ]
    if not values or None in values or len(set(values)) != 1:
        return None
    return values[0]


def _strict_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or _DATE_PATTERN.fullmatch(value.strip()) is None:
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def _normalize_fiscal_year(value: Any) -> str:
    normalized = str(value or "").strip().upper().removeprefix("FY")
    return normalized if normalized.isdigit() and len(normalized) == 4 else DATA_UNAVAILABLE


def _normalize_currency(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return "JPY" if normalized in {"JPY", "JAPANESE_YEN"} else DATA_UNAVAILABLE


def _text_or_none(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
