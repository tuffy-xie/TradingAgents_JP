from __future__ import annotations

from dataclasses import replace

import pytest

from tradingagents.dataflows.japan.financial_disclosure import FinancialRecord
from tradingagents.dataflows.japan.guidance_matcher import (
    GuidanceRecordContext,
    match_guidance_revision,
)


def _metric(
    value: int | float,
    *,
    semantic_basis: str = "REVENUE",
    unit: str = "百万円",
) -> dict:
    return {
        "status": "OK",
        "previous_value": None,
        "current_value": {
            "status": "OK",
            "value": value,
            "raw_value": value,
            "unit": unit,
            "source_field": "forecast_revenue",
            "semantic_basis": semantic_basis,
            "value_origin": "SOURCE",
            "derivation": None,
        },
        "revision_direction": None,
        "source_field": "forecast_revenue",
        "semantic_basis": semantic_basis,
        "value_origin": "SOURCE",
        "derivation": None,
    }


def _record(
    value: int | float,
    *,
    fiscal_year: str = "2027",
    period_type: str = "FY",
    scope: str = "CONSOLIDATED",
    accounting_standard: str = "IFRS",
    currency: str = "JPY",
    unit: str = "百万円",
    semantic_basis: str = "REVENUE",
    metric_unit: str = "百万円",
) -> FinancialRecord:
    return FinancialRecord(
        record_type="GUIDANCE",
        fiscal_year=fiscal_year,
        period_type=period_type,
        period_basis="FULL_YEAR" if period_type == "FY" else "CUMULATIVE",
        scope=scope,
        accounting_standard=accounting_standard,
        currency=currency,
        unit=unit,
        metrics={
            "revenue": _metric(
                value, semantic_basis=semantic_basis, unit=metric_unit
            )
        },
        revision_reason="NOT_PROVIDED",
        section_text="",
        target_period_end="2027-03-31",
        scope_origin="SOURCE",
        revision_reason_status="NOT_PROVIDED",
    )


def _context(
    value: int | float,
    *,
    timestamp: str,
    source_record_id: str,
    issuer_id: str = "E01081",
    is_correction: bool = False,
    **record_overrides,
) -> GuidanceRecordContext:
    return GuidanceRecordContext(
        record=_record(value, **record_overrides),
        issuer_id=issuer_id,
        source_record_id=source_record_id,
        disclosure_timestamp=timestamp,
        metadata={"is_correction": is_correction},
    )


@pytest.mark.parametrize(
    ("previous_value", "current_value", "expected"),
    [
        (930_000, 1_025_000, "INCREASED"),
        (1_025_000, 930_000, "DECREASED"),
        (930_000, 930_000, "UNCHANGED"),
    ],
)
def test_matches_revision_direction(previous_value, current_value, expected):
    previous = _context(
        previous_value,
        timestamp="2026-05-11T15:00:00+09:00",
        source_record_id="previous",
    )
    current = _context(
        current_value,
        timestamp="2026-08-06T15:00:00+09:00",
        source_record_id="current",
    )

    result = match_guidance_revision(previous, current)

    revision = result["metrics"]["revenue"]
    assert result["status"] == "OK"
    assert revision["revision_direction"] == expected
    assert revision["value_origin"] == "DERIVED"
    assert revision["previous_value"]["value"] == previous_value
    assert revision["previous_value"]["value_origin"] == "SOURCE"
    assert revision["current_value"]["value"] == current_value
    assert revision["current_value"]["value_origin"] == "SOURCE"
    assert revision["derivation"] == {
        "method": "COMPARE_MATCHED_GUIDANCE_RECORDS",
        "previous_source_record_id": "previous",
        "current_source_record_id": "current",
    }


def _valid_pair() -> tuple[GuidanceRecordContext, GuidanceRecordContext]:
    return (
        _context(
            930_000,
            timestamp="2026-05-11T15:00:00+09:00",
            source_record_id="previous",
        ),
        _context(
            1_025_000,
            timestamp="2026-08-06T15:00:00+09:00",
            source_record_id="current",
        ),
    )


def _assert_unavailable(previous, current, reason_fragment: str) -> None:
    result = match_guidance_revision(previous, current)
    assert result["status"] == "DATA_UNAVAILABLE"
    assert reason_fragment in result["reason"]
    assert result["metrics"] == {}


def test_rejects_cross_fiscal_target():
    previous, current = _valid_pair()
    current = replace(
        current,
        record=replace(current.record, target_period_end="2028-03-31", fiscal_year="2028"),
    )
    _assert_unavailable(previous, current, "target fiscal period")


def test_rejects_cross_fiscal_year_when_target_period_end_is_absent():
    previous, current = _valid_pair()
    previous = replace(
        previous,
        record=replace(previous.record, target_period_end=None),
    )
    current = replace(
        current,
        record=replace(current.record, target_period_end=None, fiscal_year="2028"),
    )
    _assert_unavailable(previous, current, "fiscal year")


def test_rejects_cross_period_type():
    previous, current = _valid_pair()
    current = replace(current, record=replace(current.record, period_type="H1"))
    _assert_unavailable(previous, current, "period")


def test_rejects_cross_scope():
    previous, current = _valid_pair()
    current = replace(current, record=replace(current.record, scope="NON_CONSOLIDATED"))
    _assert_unavailable(previous, current, "scope")


def test_rejects_mixed_accounting_standards():
    previous, current = _valid_pair()
    current = replace(current, record=replace(current.record, accounting_standard="J_GAAP"))
    _assert_unavailable(previous, current, "accounting standard")


def test_rejects_semantic_basis_mismatch():
    previous, current = _valid_pair()
    current_metric = _metric(1_025_000, semantic_basis="TOTAL_PERIOD_PROFIT")
    current = replace(
        current,
        record=replace(current.record, metrics={"revenue": current_metric}),
    )
    _assert_unavailable(previous, current, "semantic basis")


def test_rejects_metric_unit_mismatch():
    previous, current = _valid_pair()
    current_metric = _metric(1_025_000, unit="億円")
    current = replace(
        current,
        record=replace(current.record, metrics={"revenue": current_metric}),
    )
    _assert_unavailable(previous, current, "metric unit")


def test_rejects_record_unit_mismatch():
    previous, current = _valid_pair()
    current = replace(current, record=replace(current.record, unit="億円"))
    _assert_unavailable(previous, current, "record unit")


def test_rejects_currency_mismatch():
    previous, current = _valid_pair()
    current = replace(current, record=replace(current.record, currency="USD"))
    _assert_unavailable(previous, current, "currency")


def test_rejects_non_increasing_timestamp():
    previous, current = _valid_pair()
    current = replace(current, disclosure_timestamp="2026-05-11T15:00:00+09:00")
    _assert_unavailable(previous, current, "timestamps")


def test_accepts_strictly_increasing_date_only_timestamps():
    previous, current = _valid_pair()
    previous = replace(previous, disclosure_timestamp="2026-05-11")
    current = replace(current, disclosure_timestamp="2026-08-06")
    assert match_guidance_revision(previous, current)["status"] == "OK"


def test_rejects_correction_record():
    previous, current = _valid_pair()
    current = replace(current, metadata={"is_correction": True})
    _assert_unavailable(previous, current, "correction")


def test_rejects_unknown_scope():
    previous, current = _valid_pair()
    current = replace(current, record=replace(current.record, scope="UNKNOWN"))
    _assert_unavailable(previous, current, "scope")


def test_rejects_company_name_only_without_stable_issuer_id():
    previous, current = _valid_pair()
    previous = replace(previous, issuer_id=None, metadata={"company_name": "Example Corp"})
    current = replace(current, issuer_id=None, metadata={"company_name": "Example Corp"})
    _assert_unavailable(previous, current, "issuer identity")


def test_rejects_different_stable_issuer_ids():
    previous, current = _valid_pair()
    current = replace(current, issuer_id="E01712")
    _assert_unavailable(previous, current, "issuer mismatch")
