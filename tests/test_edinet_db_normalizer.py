"""Fixture tests for the pure EDINET DB financial normalizer."""

import pytest

from tradingagents.dataflows.japan.edinet_db_normalizer import (
    normalize_accounting_standard,
    normalize_actual_metrics,
    normalize_actual_record,
    normalize_financial_document,
    normalize_guidance_record,
    normalize_scope,
)
from tradingagents.dataflows.japan.financial_disclosure import (
    DATA_UNAVAILABLE,
    NOT_APPLICABLE,
    NOT_PROVIDED,
)

EDINET_5016_IFRS = {
    "accounting_standard": "IFRS",
    "is_consolidated": True,
    "fiscal_year": 2027,
    "period": "Q1",
    "target_period_end": "2027-03-31",
    "currency": "JPY",
    "unit": "百万円",
    "revenue": 260604,
    "operating_income": 81446,
    "ordinary_income": 79644,
    "net_income": 53070,
    "profit_ifrs": 60418,
    "eps": 56.80,
}

EDINET_6324_JGAAP = {
    "accounting_standard": "JP_GAAP",
    "is_consolidated": "true",
    "fiscal_year": "FY2027",
    "period": "Q1",
    "currency": "JPY",
    "unit": "百万円",
    "revenue": 16681,
    "operating_income": 1840,
    "ordinary_income": 1719,
    "net_income": 1274,
    "eps": 13.46,
}

EDINET_6981_IFRS = {
    "accounting_standard": "IFRS",
    "is_consolidated": True,
    "fiscal_year": 2027,
    "period": "Q1",
    "currency": "JPY",
    "unit": "百万円",
    "revenue": 502264,
    "operating_income": 98454,
    "ordinary_income": 109233,
    "net_income": 81377,
    "eps": 44.71,
}

LIVE_5016_IFRS = {
    "accounting_standard": "IFRS",
    "quarter": 1,
    "fiscal_year_end": "2027-03-31",
    "currency": "JPY",
    "unit": "百万円",
    "revenue": 260604,
    "operating_income": 81446,
    "ordinary_income": 79644,
    "net_income": 53070,
    "profit_ifrs": 60418,
    "eps": 56.80,
    "forecast_revenue": 1025000,
    "forecast_operating_income": 232000,
    "forecast_net_income": 141000,
    "forecast_eps": 155.80,
}

LIVE_6324_JGAAP = {
    "accounting_standard": "JP",
    "quarter": 1,
    "fiscal_year_end": "2027-03-31",
    "currency": "JPY",
    "unit": "百万円",
    "revenue": 16681,
    "operating_income": 1840,
    "ordinary_income": 1719,
    "net_income": 1274,
    "eps": 13.46,
}

LIVE_6981_IFRS = {
    "accounting_standard": "IFRS",
    "quarter": 1,
    "fiscal_year_end": "2027-03-31",
    "currency": "JPY",
    "unit": "百万円",
    "revenue": 502264,
    "operating_income": 98454,
    "ordinary_income": 109233,
    "net_income": 81377,
    "eps": 44.71,
}


def test_normalizes_5016_ifrs_without_conflating_parent_and_total_profit():
    record = normalize_actual_record(EDINET_5016_IFRS)

    assert record.metrics["revenue"]["value"] == 260604
    assert record.metrics["operating_profit"]["value"] == 81446
    assert record.metrics["ordinary_profit"]["status"] == NOT_APPLICABLE
    assert record.metrics["net_income"]["value"] == 53070
    assert record.metrics["profit_total"]["value"] == 60418
    assert record.metrics["net_income"]["value"] != record.metrics["profit_total"]["value"]
    assert record.metrics["net_income"]["semantic_basis"] == "PARENT_ATTRIBUTABLE_PROFIT"
    assert record.metrics["profit_total"]["semantic_basis"] == "TOTAL_PERIOD_PROFIT"
    assert record.scope == "CONSOLIDATED"
    assert record.scope_origin == "SOURCE"
    assert record.target_period_end == "2027-03-31"


def test_normalizes_6324_jgaap_actual_values_and_ordinary_profit():
    record = normalize_actual_record(EDINET_6324_JGAAP)

    assert record.accounting_standard == "J_GAAP"
    assert record.metrics["revenue"]["value"] == 16681
    assert record.metrics["operating_profit"]["value"] == 1840
    assert record.metrics["ordinary_profit"]["value"] == 1719
    assert record.metrics["net_income"]["value"] == 1274
    assert record.metrics["profit_total"]["status"] == NOT_PROVIDED
    assert record.metrics["eps"]["value"] == 13.46


def test_ifrs_ordinary_income_never_becomes_ordinary_profit():
    metrics = normalize_actual_metrics(EDINET_6981_IFRS)

    assert metrics["ordinary_profit"]["status"] == NOT_APPLICABLE
    assert metrics["ordinary_profit"]["value"] is None
    assert metrics["net_income"]["value"] == 81377


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, ("CONSOLIDATED", "SOURCE")),
        (False, ("NON_CONSOLIDATED", "SOURCE")),
        ("true", ("CONSOLIDATED", "SOURCE")),
        ("false", ("NON_CONSOLIDATED", "SOURCE")),
        ("unknown", ("UNKNOWN", "UNKNOWN")),
    ],
)
def test_normalizes_scope_without_python_truthiness(value, expected):
    assert normalize_scope(value) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IFRS", "IFRS"),
        ("JP", "J_GAAP"),
        ("JP_GAAP", "J_GAAP"),
        ("J-GAAP", "J_GAAP"),
        ("unknown", "UNKNOWN"),
    ],
)
def test_normalizes_accounting_standard_with_an_allowlist(raw, expected):
    assert normalize_accounting_standard(raw) == expected


def test_current_guidance_is_source_data_without_a_derived_revision_direction():
    record = normalize_guidance_record(
        {
            "accounting_standard": "IFRS",
            "is_consolidated": False,
            "forecast_fiscal_year": 2027,
            "forecast_period": "FY",
            "forecast_target_period_end": "2027-03-31",
            "currency": "JPY",
            "unit": "百万円",
            "forecast_revenue": 1025000,
            "forecast_operating_income": 232000,
            "forecast_net_income": 141000,
            "forecast_eps": 155.80,
        }
    )

    revenue = record.metrics["revenue"]
    assert record.record_type == "GUIDANCE"
    assert record.scope == "NON_CONSOLIDATED"
    assert record.revision_reason_status == NOT_PROVIDED
    assert revenue["current_value"]["value"] == 1025000
    assert revenue["previous_value"] is None
    assert revenue["revision_direction"] is None
    assert revenue["value_origin"] == "SOURCE"
    assert record.metrics["ordinary_profit"]["status"] == NOT_APPLICABLE


def test_normalizes_raw_record_into_a_structured_source_document():
    document = normalize_financial_document(
        {
            **EDINET_5016_IFRS,
            "title": "2027年3月期 第1四半期決算",
            "disclosure_date": "2026-08-06T15:30:00+09:00",
            "source_pdf_url": "https://example.test/5016.pdf",
            "source_as_of": "2026-08-06",
            "record_id": "earnings-5016-q1",
            "forecast_period": "FY",
            "forecast_revenue": 1025000,
            "forecast_operating_income": 232000,
            "forecast_net_income": 141000,
            "forecast_eps": 155.80,
        },
        fetched_at="2026-08-23T00:00:00+00:00",
    )

    assert document.status == "OK"
    assert document.source == "EDINET DB"
    assert document.source_type == "STRUCTURED_SOURCE"
    assert document.fetched_at == "2026-08-23T00:00:00+00:00"
    assert document.source_as_of == "2026-08-06"
    assert document.source_record_id == "earnings-5016-q1"
    assert {record.record_type for record in document.records} == {"ACTUAL", "GUIDANCE"}


def test_unknown_standard_does_not_assume_financial_semantics():
    metrics = normalize_actual_metrics(
        {
            "accounting_standard": "unrecognized",
            "unit": "百万円",
            "ordinary_income": 100,
        }
    )

    assert metrics["ordinary_profit"]["status"] == DATA_UNAVAILABLE
    assert metrics["net_income"]["status"] == NOT_PROVIDED


def test_normalizes_5016_live_actual_and_current_guidance():
    document = normalize_financial_document(LIVE_5016_IFRS)
    actual = next(record for record in document.records if record.record_type == "ACTUAL")
    guidance = next(record for record in document.records if record.record_type == "GUIDANCE")

    assert actual.period_type == "Q1"
    assert actual.period_basis == "CUMULATIVE"
    assert actual.target_period_end == "2027-03-31"
    assert actual.fiscal_year == DATA_UNAVAILABLE
    assert actual.scope == "UNKNOWN"
    assert actual.metrics["ordinary_profit"]["status"] == NOT_APPLICABLE
    assert actual.metrics["net_income"]["value"] == 53070
    assert actual.metrics["profit_total"]["value"] == 60418
    assert actual.metrics["operating_profit"]["source_field"] == "operating_income"

    assert guidance.period_type == "FY"
    assert guidance.period_basis == "FULL_YEAR"
    assert guidance.target_period_end == "2027-03-31"
    assert guidance.metrics["revenue"]["current_value"]["value"] == 1025000
    assert guidance.metrics["operating_profit"]["current_value"]["value"] == 232000
    assert guidance.metrics["net_income"]["current_value"]["value"] == 141000
    assert guidance.metrics["eps"]["current_value"]["value"] == 155.80
    assert guidance.metrics["revenue"]["source_field"] == "forecast_revenue"


def test_normalizes_6324_live_jgaap_actual():
    record = normalize_actual_record(LIVE_6324_JGAAP)

    assert record.period_type == "Q1"
    assert record.target_period_end == "2027-03-31"
    assert record.accounting_standard == "J_GAAP"
    assert record.scope == "UNKNOWN"
    assert record.metrics["revenue"]["value"] == 16681
    assert record.metrics["operating_profit"]["value"] == 1840
    assert record.metrics["ordinary_profit"]["value"] == 1719
    assert record.metrics["net_income"]["value"] == 1274
    assert record.metrics["eps"]["value"] == 13.46


def test_normalizes_6981_live_ifrs_without_ordinary_profit():
    record = normalize_actual_record(LIVE_6981_IFRS)

    assert record.period_type == "Q1"
    assert record.target_period_end == "2027-03-31"
    assert record.metrics["ordinary_profit"]["status"] == NOT_APPLICABLE
    assert record.metrics["net_income"]["value"] == 81377


@pytest.mark.parametrize(
    ("quarter", "expected"),
    [
        (1, "Q1"),
        (2, "H1"),
        (3, "Q3"),
        (4, "FY"),
        ("1", "Q1"),
        ("2", "H1"),
        ("3", "Q3"),
        ("4", "FY"),
        ("Q1", "Q1"),
        ("Q2", "H1"),
        ("Q3", "Q3"),
        ("Q4", "FY"),
        ("H1", "H1"),
        ("FY", "FY"),
    ],
)
def test_normalizes_live_quarter_variants(quarter, expected):
    record = normalize_actual_record({"quarter": quarter, "revenue": 1})
    assert record.period_type == expected


def test_allows_matching_period_and_quarter():
    record = normalize_actual_record({"period": "Q1", "quarter": 1, "revenue": 1})
    assert record.period_type == "Q1"


def test_rejects_conflicting_period_and_quarter():
    record = normalize_actual_record({"period": "Q1", "quarter": 2, "revenue": 1})
    assert record.period_type == DATA_UNAVAILABLE
    assert record.period_basis == DATA_UNAVAILABLE


def test_allows_matching_target_and_fiscal_year_end():
    record = normalize_actual_record(
        {
            "period": "Q1",
            "target_period_end": "2027-03-31",
            "fiscal_year_end": "2027-03-31",
            "revenue": 1,
        }
    )
    assert record.target_period_end == "2027-03-31"


def test_rejects_conflicting_target_and_fiscal_year_end():
    record = normalize_actual_record(
        {
            "period": "Q1",
            "target_period_end": "2027-03-31",
            "fiscal_year_end": "2028-03-31",
            "revenue": 1,
        }
    )
    assert record.target_period_end is None


@pytest.mark.parametrize("fiscal_year_end", ("2027/03/31", "FY2027", "2027-02-30"))
def test_rejects_malformed_live_fiscal_year_end(fiscal_year_end):
    record = normalize_actual_record(
        {
            "quarter": 1,
            "fiscal_year_end": fiscal_year_end,
            "revenue": 1,
        }
    )
    assert record.period_type == "Q1"
    assert record.target_period_end is None


@pytest.mark.parametrize("quarter", (1, 2, 3))
def test_q1_to_q3_guidance_uses_live_fiscal_year_end(quarter):
    record = normalize_guidance_record(
        {
            "quarter": quarter,
            "fiscal_year_end": "2027-03-31",
            "forecast_revenue": 100,
        }
    )
    assert record.period_type == "FY"
    assert record.target_period_end == "2027-03-31"


def test_q1_guidance_rejects_conflicting_explicit_and_live_target_end():
    record = normalize_guidance_record(
        {
            "quarter": 1,
            "fiscal_year_end": "2027-03-31",
            "forecast_target_period_end": "2028-03-31",
            "forecast_revenue": 100,
        }
    )
    assert record.period_type == "FY"
    assert record.target_period_end is None


def test_q4_guidance_does_not_reuse_actual_fiscal_year_end():
    document = normalize_financial_document(
        {
            "quarter": 4,
            "fiscal_year_end": "2026-03-31",
            "revenue": 100,
            "forecast_revenue": 120,
        }
    )
    actual = next(record for record in document.records if record.record_type == "ACTUAL")
    guidance = next(record for record in document.records if record.record_type == "GUIDANCE")

    assert actual.period_type == "FY"
    assert actual.target_period_end == "2026-03-31"
    assert guidance.period_type == "FY"
    assert guidance.target_period_end is None
    assert guidance.metrics["revenue"]["current_value"]["value"] == 120


def test_q4_guidance_accepts_explicit_forecast_target_period_end():
    record = normalize_guidance_record(
        {
            "quarter": 4,
            "fiscal_year_end": "2026-03-31",
            "forecast_target_period_end": "2027-03-31",
            "forecast_revenue": 120,
        }
    )
    assert record.period_type == "FY"
    assert record.target_period_end == "2027-03-31"
