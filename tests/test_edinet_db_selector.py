from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from itertools import permutations

import pytest

from tradingagents.dataflows.japan.edinet_db_selector import (
    STATUS_DATA_UNAVAILABLE,
    STATUS_OK,
    select_latest_earnings_record,
)


def _record(
    *,
    fiscal_year_end: str = "2027-03-31",
    quarter=1,
    disclosure_date="2026-08-06",
    is_correction=False,
    revenue=100,
    **extra,
) -> dict:
    return {
        "fiscal_year_end": fiscal_year_end,
        "quarter": quarter,
        "disclosure_date": disclosure_date,
        "is_correction": is_correction,
        "revenue": revenue,
        **extra,
    }


def _select(records, *, analysis_as_of=date(2026, 8, 31)):
    return select_latest_earnings_record(
        records,
        issuer_id="E01081",
        analysis_as_of=analysis_as_of,
    )


def _assert_selected(result, period_type: str, fiscal_year_end: str = "2027-03-31"):
    assert result.status == STATUS_OK
    assert result.fiscal_year_end == fiscal_year_end
    assert result.period_type == period_type
    assert result.selection_basis == "LATEST_FISCAL_PERIOD_AS_OF"
    assert result.detail == ""


def test_empty_input_has_no_eligible_record():
    result = _select([])
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "NO_ELIGIBLE_RECORD"


def test_selects_h1_over_q1():
    result = _select(
        [
            _record(quarter=1, disclosure_date="2026-08-06"),
            _record(quarter=2, disclosure_date="2026-11-06"),
        ],
        analysis_as_of=date(2026, 12, 1),
    )
    _assert_selected(result, "H1")


def test_selects_q3_over_h1():
    result = _select(
        [
            _record(quarter="H1", disclosure_date="2026-11-06"),
            _record(quarter="Q3", disclosure_date="2027-02-06"),
        ],
        analysis_as_of=date(2027, 2, 20),
    )
    _assert_selected(result, "Q3")


def test_new_fiscal_year_q1_beats_prior_fy():
    result = _select(
        [
            _record(
                fiscal_year_end="2026-03-31",
                quarter="FY",
                disclosure_date="2026-05-10",
            ),
            _record(quarter="Q1", disclosure_date="2026-08-06"),
        ]
    )
    _assert_selected(result, "Q1")


def test_later_q1_correction_does_not_override_h1_fiscal_period():
    h1 = _record(quarter="H1", disclosure_date="2026-08-07", revenue=200)
    q1_correction = _record(
        quarter="Q1",
        disclosure_date="2026-08-10",
        is_correction=True,
        revenue=101,
    )
    result = _select([q1_correction, h1])
    _assert_selected(result, "H1")
    assert result.record == h1


def test_forecast_only_correction_keeps_original_actual_baseline():
    original = _record(record_id="original", revenue=100)
    correction = _record(
        disclosure_date="2026-08-07",
        is_correction=True,
        revenue=100,
        forecast_revenue=1_025_000,
    )
    result = _select([correction, original])
    _assert_selected(result, "Q1")
    assert result.record == original
    assert result.source_record_id == "original"


def test_forecast_only_h1_record_does_not_displace_q1_actual():
    q1_actual = _record(quarter=1, revenue=365_221, record_id="q1-actual")
    h1_forecast_only = _record(
        quarter=2,
        revenue=None,
        operating_income=None,
        ordinary_income=None,
        net_income=None,
        eps=None,
        forecast_revenue=720_000,
        record_id="h1-guidance",
    )

    result = _select([h1_forecast_only, q1_actual])

    _assert_selected(result, "Q1")
    assert result.source_record_id == "q1-actual"


def test_actual_changing_correction_requires_resolution():
    result = _select(
        [
            _record(revenue=100),
            _record(disclosure_date="2026-08-07", is_correction=True, revenue=101),
        ]
    )
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "CORRECTION_REQUIRES_RESOLUTION"


def test_highest_period_correction_without_baseline_is_rejected():
    result = _select([_record(quarter="H1", is_correction=True)])
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "CORRECTION_REQUIRES_BASELINE"


def test_explicitly_unknown_correction_flag_fails_closed():
    result = _select([_record(is_correction=None)])
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "CORRECTION_REQUIRES_RESOLUTION"


def test_exact_duplicate_is_safely_deduplicated():
    record = _record(record_id="same")
    result = _select([record, dict(record)])
    _assert_selected(result, "Q1")
    assert result.source_record_id == "same"


def test_different_duplicate_is_ambiguous():
    result = _select([_record(revenue=100), _record(revenue=101)])
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "AMBIGUOUS_LATEST"


def test_future_record_is_ignored():
    eligible = _record(quarter="Q1", disclosure_date="2026-08-06")
    future = _record(quarter="H1", disclosure_date="2026-11-06")
    result = _select([future, eligible], analysis_as_of=date(2026, 8, 31))
    _assert_selected(result, "Q1")
    assert result.record == eligible


def test_only_future_records_are_unavailable():
    result = _select(
        [_record(disclosure_date="2026-09-01")],
        analysis_as_of=date(2026, 8, 31),
    )
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "FUTURE_ONLY"


def test_date_as_of_allows_same_day_date_precision():
    result = _select(
        [_record(disclosure_date="2026-08-06")],
        analysis_as_of=date(2026, 8, 6),
    )
    _assert_selected(result, "Q1")
    assert result.disclosure_precision == "DATE"


def test_aware_datetime_rejects_competing_same_day_date_precision():
    result = _select(
        [_record(disclosure_date="2026-08-06")],
        analysis_as_of=datetime(2026, 8, 6, 10, tzinfo=timezone(timedelta(hours=9))),
    )
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "DATE_ONLY_SAME_DAY_UNRESOLVED"


def test_naive_analysis_datetime_is_rejected():
    result = _select(
        [_record()],
        analysis_as_of=datetime(2026, 8, 31, 12),
    )
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "INVALID_ANALYSIS_AS_OF"


def test_rfc1123_midnight_is_date_precision_not_timestamp():
    result = _select(
        [_record(disclosure_date="Thu, 06 Aug 2026 00:00:00 GMT")],
        analysis_as_of=date(2026, 8, 6),
    )
    _assert_selected(result, "Q1")
    assert result.disclosure_date == "2026-08-06"
    assert result.disclosure_precision == "DATE"


def test_true_aware_timestamp_uses_timestamp_precision():
    record = _record(disclosure_date="2026-08-01")
    record["disclosure_timestamp"] = "2026-08-06T15:30:00+09:00"
    result = _select(
        [record],
        analysis_as_of=datetime(2026, 8, 6, 16, tzinfo=timezone(timedelta(hours=9))),
    )
    _assert_selected(result, "Q1")
    assert result.disclosure_precision == "TIMESTAMP"
    assert result.disclosure_date == "2026-08-06T15:30:00+09:00"


def test_malformed_temporal_data_for_clearly_older_fiscal_period_is_ignored():
    older_bad = _record(
        fiscal_year_end="2026-03-31",
        quarter="Q1",
        disclosure_date="not-a-date",
    )
    current = _record(quarter="H1", disclosure_date="2026-08-06")
    result = _select([older_bad, current])
    _assert_selected(result, "H1")


@pytest.mark.parametrize(
    ("fiscal_year_end", "quarter"),
    (("2027-03-31", "H1"), ("2027-03-31", "Q3"), ("2028-03-31", "Q1")),
)
def test_malformed_temporal_data_that_can_affect_latest_fails_closed(
    fiscal_year_end,
    quarter,
):
    result = _select(
        [
            _record(quarter="H1", disclosure_date="2026-08-06"),
            _record(
                fiscal_year_end=fiscal_year_end,
                quarter=quarter,
                disclosure_date="not-a-date",
            ),
        ]
    )
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "INVALID_TEMPORAL_DATA"


def test_unknown_quarter_that_can_affect_latest_fails_closed():
    result = _select(
        [
            _record(quarter="H1"),
            _record(quarter="Q5"),
        ]
    )
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "INVALID_FISCAL_PERIOD"


def test_unknown_quarter_for_clearly_older_fiscal_year_is_ignored():
    result = _select(
        [
            _record(quarter="H1"),
            _record(fiscal_year_end="2026-03-31", quarter="Q5"),
        ]
    )
    _assert_selected(result, "H1")


def test_malformed_fiscal_year_end_fails_closed():
    result = _select([_record(fiscal_year_end="FY2027")])
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "INVALID_FISCAL_PERIOD"


def test_latest_incomplete_record_is_not_replaced_by_complete_older_record():
    q1 = _record(quarter="Q1", revenue=100, operating_income=20)
    h1 = _record(quarter="H1", disclosure_date="2026-08-07", revenue=None)
    h1.pop("revenue")
    result = _select([q1, h1])
    _assert_selected(result, "H1")
    assert "revenue" not in result.record


def test_reversed_input_order_does_not_change_result():
    records = [
        _record(quarter="Q1", disclosure_date="2026-05-01"),
        _record(quarter="H1", disclosure_date="2026-08-01"),
        _record(quarter="Q3", disclosure_date="2026-11-01"),
    ]
    forward = _select(records, analysis_as_of=date(2026, 12, 1))
    reversed_result = _select(list(reversed(records)), analysis_as_of=date(2026, 12, 1))
    assert forward == reversed_result
    _assert_selected(forward, "Q3")


def test_every_input_permutation_selects_the_same_record():
    records = [
        _record(quarter="Q1", disclosure_date="2026-05-01", record_id="q1"),
        _record(quarter="H1", disclosure_date="2026-08-01", record_id="h1"),
        _record(quarter="Q3", disclosure_date="2026-11-01", record_id="q3"),
    ]
    results = {
        _select(order, analysis_as_of=date(2026, 12, 1)).source_record_id
        for order in permutations(records)
    }
    assert results == {"q3"}


def test_q4_is_canonical_full_year_actual():
    result = _select([_record(quarter="Q4", forecast_revenue=1_100)])
    _assert_selected(result, "FY")


@pytest.mark.parametrize("quarter", ("2", "Q2", "H1"))
def test_q2_aliases_are_canonical_h1(quarter):
    result = _select([_record(quarter=quarter)])
    _assert_selected(result, "H1")


@pytest.mark.parametrize(
    ("quarter", "expected"),
    ((1, "Q1"), (2, "H1"), (3, "Q3"), (4, "FY")),
)
def test_numeric_quarters_are_strictly_canonicalized(quarter, expected):
    result = _select([_record(quarter=quarter)])
    _assert_selected(result, expected)


def test_selector_does_not_mutate_input_records():
    record = _record(record_id="q1")
    original = dict(record)
    result = _select([record])
    _assert_selected(result, "Q1")
    assert record == original
    assert result.record is not record


def test_missing_source_record_id_remains_none():
    result = _select([_record()])
    _assert_selected(result, "Q1")
    assert result.source_record_id is None
