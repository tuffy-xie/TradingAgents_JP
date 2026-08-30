from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from itertools import permutations

import pytest

from tradingagents.dataflows.japan.edinet_db_guidance_selector import (
    STATUS_DATA_UNAVAILABLE,
    STATUS_NOT_PROVIDED,
    STATUS_OK,
    select_latest_guidance_record,
)


def _record(
    *,
    record_id: str = "q1",
    quarter=1,
    fiscal_year_end: str = "2027-03-31",
    disclosure_date="2026-08-06",
    forecast_revenue=1_000,
    **extra,
):
    return {
        "record_id": record_id,
        "quarter": quarter,
        "fiscal_year_end": fiscal_year_end,
        "disclosure_date": disclosure_date,
        "forecast_revenue": forecast_revenue,
        "accounting_standard": "IFRS",
        "is_consolidated": True,
        **extra,
    }


def _select(records, *, analysis_as_of=date(2026, 8, 31)):
    return select_latest_guidance_record(
        records,
        issuer_id="E01081",
        analysis_as_of=analysis_as_of,
    )


def test_later_guidance_revision_is_independent_from_latest_actual_period():
    h1 = _record(
        record_id="h1-earnings",
        quarter=2,
        disclosure_date="2026-08-07",
        forecast_revenue=1_000,
    )
    q1_revision = _record(
        record_id="q1-guidance-revision",
        quarter=1,
        disclosure_date="2026-08-15",
        forecast_revenue=1_200,
        is_correction=True,
    )

    result = _select([h1, q1_revision])

    assert result.status == STATUS_OK
    assert result.source_record_id == "q1-guidance-revision"
    assert result.record["forecast_revenue"] == 1_200
    assert result.target_period_end == "2027-03-31"
    assert result.period_type == "FY"
    assert result.selection_basis == "LATEST_GUIDANCE_DISCLOSURE_AS_OF"


def test_later_q1_actual_correction_does_not_change_guidance_when_forecast_is_same():
    h1 = _record(record_id="h1", quarter=2, disclosure_date="2026-08-07")
    correction = _record(
        record_id="q1-correction",
        quarter=1,
        disclosure_date="2026-08-15",
        forecast_revenue=1_000,
        is_correction=True,
        revenue=101,
    )
    result = _select([h1, correction])
    assert result.status == STATUS_OK
    assert result.source_record_id == "q1-correction"
    assert result.record["forecast_revenue"] == 1_000


def test_newer_fiscal_target_wins_before_disclosure_order():
    prior_target = _record(
        record_id="old-target-late",
        fiscal_year_end="2026-03-31",
        disclosure_date="2026-08-20",
    )
    current_target = _record(record_id="current-target", disclosure_date="2026-08-06")
    result = _select([prior_target, current_target])
    assert result.source_record_id == "current-target"


def test_q4_requires_explicit_next_fy_target():
    result = _select([_record(quarter=4, fiscal_year_end="2026-03-31")])
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "INVALID_GUIDANCE_TARGET"


def test_older_q4_unknown_target_does_not_block_later_q1_current_guidance():
    result = _select(
        [
            _record(
                record_id="q4",
                quarter=4,
                fiscal_year_end="2026-03-31",
                disclosure_date="2026-05-13",
                forecast_revenue=68_000,
            ),
            _record(
                record_id="q1",
                quarter=1,
                fiscal_year_end="2027-03-31",
                disclosure_date="2026-08-07",
                forecast_revenue=74_500,
            ),
        ]
    )

    assert result.status == STATUS_OK
    assert result.source_record_id == "q1"
    assert result.target_period_end == "2027-03-31"


def test_q4_accepts_explicit_guidance_target_without_plus_one_derivation():
    result = _select(
        [
            _record(
                quarter=4,
                fiscal_year_end="2026-03-31",
                forecast_target_period_end="2027-03-31",
            )
        ]
    )
    assert result.status == STATUS_OK
    assert result.target_period_end == "2027-03-31"


def test_all_null_forecast_contract_is_not_provided():
    result = _select([_record(forecast_revenue=None, forecast_eps=None)])
    assert result.status == STATUS_NOT_PROVIDED
    assert result.detail == "GUIDANCE_NOT_PROVIDED"


def test_absent_forecast_contract_is_not_provided():
    result = _select([{"quarter": 1, "fiscal_year_end": "2027-03-31"}])
    assert result.status == STATUS_NOT_PROVIDED
    assert result.detail == "GUIDANCE_NOT_PROVIDED"


def test_future_revision_is_ignored():
    current = _record(record_id="current")
    future = _record(
        record_id="future",
        disclosure_date="2026-09-01",
        forecast_revenue=1_200,
    )
    result = _select([future, current])
    assert result.source_record_id == "current"


def test_only_future_guidance_fails_closed():
    result = _select([_record(disclosure_date="2026-09-01")])
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "FUTURE_ONLY"


def test_same_day_different_guidance_is_ambiguous():
    result = _select(
        [
            _record(record_id="a", forecast_revenue=1_000),
            _record(record_id="b", forecast_revenue=1_200),
        ]
    )
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "AMBIGUOUS_LATEST_GUIDANCE"


def test_same_day_same_guidance_is_deduplicated():
    result = _select(
        [
            _record(record_id="b", forecast_revenue=1_000),
            _record(record_id="a", forecast_revenue=1_000),
        ]
    )
    assert result.status == STATUS_OK
    assert result.record["forecast_revenue"] == 1_000


def test_date_as_of_allows_same_day_date_precision():
    result = _select([_record()], analysis_as_of=date(2026, 8, 6))
    assert result.status == STATUS_OK
    assert result.disclosure_precision == "DATE"


def test_aware_datetime_rejects_date_only_same_day():
    result = _select(
        [_record()],
        analysis_as_of=datetime(2026, 8, 6, 10, tzinfo=timezone(timedelta(hours=9))),
    )
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "DATE_ONLY_SAME_DAY_UNRESOLVED"


def test_naive_analysis_datetime_is_rejected():
    result = _select([_record()], analysis_as_of=datetime(2026, 8, 31, 10))
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "INVALID_ANALYSIS_AS_OF"


def test_unknown_target_and_malformed_disclosure_fail_closed():
    result = _select([_record(fiscal_year_end="FY2027")])
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "INVALID_GUIDANCE_TARGET"
    result = _select([_record(disclosure_date="bad-date")])
    assert result.status == STATUS_DATA_UNAVAILABLE
    assert result.detail == "INVALID_TEMPORAL_DATA"


@pytest.mark.parametrize("order", permutations(("q1", "h1", "revision")))
def test_input_order_never_changes_selected_guidance(order):
    records = {
        "q1": _record(record_id="q1", disclosure_date="2026-05-01", forecast_revenue=900),
        "h1": _record(record_id="h1", quarter=2, disclosure_date="2026-08-01"),
        "revision": _record(
            record_id="revision",
            disclosure_date="2026-08-15",
            forecast_revenue=1_200,
        ),
    }
    result = _select([records[key] for key in order])
    assert result.source_record_id == "revision"


def test_selector_does_not_mutate_input():
    record = _record()
    original = dict(record)
    _select([record])
    assert record == original
