from __future__ import annotations

import asyncio
import inspect
from datetime import date, datetime, timedelta, timezone

from tradingagents.dataflows.japan import edinet_db_service as service_module
from tradingagents.dataflows.japan.edinet_db import EDINETDBResponse, EDINETDBStatus
from tradingagents.dataflows.japan.edinet_db_service import (
    EDINETDBFinancialService,
    GuidanceRecordIdentifier,
)
from tradingagents.dataflows.japan.financial_disclosure import DATA_UNAVAILABLE, FinancialDocument


class _ProviderStub:
    def __init__(self, responses: dict[str, EDINETDBResponse]):
        self.responses = responses
        self.calls: list[str] = []
        self.limits: list[int] = []

    async def fetch_earnings(self, issuer_id: str, *, limit: int = 8) -> EDINETDBResponse:
        self.calls.append(issuer_id)
        self.limits.append(limit)
        return self.responses[issuer_id]


def _response(
    *records: dict,
    returned_count: int | None = None,
    window_may_be_truncated: bool | None = None,
) -> EDINETDBResponse:
    count = len(records) if returned_count is None else returned_count
    truncated = count >= 30 if window_may_be_truncated is None else window_may_be_truncated
    return EDINETDBResponse(
        status=EDINETDBStatus.OK,
        records=tuple(records),
        metadata={
            "source": "EDINET DB",
            "fetched_at": "2026-08-24T00:00:00+00:00",
            "data_as_of": "2026-08-24",
            "requested_limit": 30,
            "returned_count": count,
            "window_may_be_truncated": truncated,
            "record_metadata": tuple(
                {
                    "source_record_id": record.get("record_id"),
                    "source_url": record.get("pdf_url"),
                }
                for record in records
            ),
        },
    )


def _actual_record(
    *,
    record_id: str = "actual-q1",
    fiscal_year_end: str = "2027-03-31",
    quarter=1,
    disclosure_date: str = "2026-08-06T15:30:00+09:00",
    is_correction: bool = False,
    revenue: int | None = 260604,
) -> dict:
    return {
        "record_id": record_id,
        "title": "2027年3月期 第1四半期決算",
        "disclosure_date": disclosure_date,
        "pdf_url": f"https://example.test/{record_id}.pdf",
        "accounting_standard": "IFRS",
        "is_consolidated": True,
        "fiscal_year_end": fiscal_year_end,
        "quarter": quarter,
        "is_correction": is_correction,
        "currency": "JPY",
        "unit": "百万円",
        "revenue": revenue,
        "operating_income": 81446,
        "net_income": 53070,
        "profit_ifrs": 60418,
        "eps": 56.80,
    }


def _guidance_record(record_id: str, disclosure_date: str, revenue: int) -> dict:
    return {
        "record_id": record_id,
        "disclosure_date": disclosure_date,
        "pdf_url": f"https://example.test/{record_id}.pdf",
        "accounting_standard": "IFRS",
        "is_consolidated": True,
        "forecast_fiscal_year": 2027,
        "forecast_period": "FY",
        "forecast_target_period_end": "2027-03-31",
        "currency": "JPY",
        "unit": "百万円",
        "forecast_revenue": revenue,
        "forecast_operating_income": 232000,
        "forecast_net_income": 141000,
        "forecast_eps": 155.80,
    }


def test_fetch_financial_document_normalizes_provider_record():
    provider = _ProviderStub({"E01081": _response(_actual_record())})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    document = asyncio.run(
        service.fetch_financial_document("E01081", analysis_as_of=date(2026, 8, 31))
    )

    assert isinstance(document, FinancialDocument)
    assert document.status == "OK"
    assert document.source == "EDINET DB"
    assert document.source_record_id == "actual-q1"
    assert document.fetched_at == "2026-08-24T00:00:00+00:00"
    assert document.source_as_of == "2026-08-24"
    assert any(record.record_type == "ACTUAL" for record in document.records)
    assert document.records[0].metrics["revenue"]["value"] == 260604
    assert provider.limits == [30]


def test_fetch_financial_document_returns_auth_required_transport_response():
    transport = EDINETDBResponse(status=EDINETDBStatus.AUTH_REQUIRED, detail="no key")
    provider = _ProviderStub({"E01081": transport})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    result = asyncio.run(
        service.fetch_financial_document("E01081", analysis_as_of=date(2026, 8, 31))
    )

    assert result is transport
    assert result.status == EDINETDBStatus.AUTH_REQUIRED


def test_fetch_financial_document_returns_api_error_transport_response():
    transport = EDINETDBResponse(status=EDINETDBStatus.API_ERROR, detail="HTTP 500")
    provider = _ProviderStub({"E01081": transport})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    result = asyncio.run(
        service.fetch_financial_document("E01081", analysis_as_of=date(2026, 8, 31))
    )

    assert result is transport
    assert result.status == EDINETDBStatus.API_ERROR


def test_fetch_guidance_revision_matches_explicit_source_records():
    previous = _guidance_record("previous", "2026-05-11T15:00:00+09:00", 930000)
    current = _guidance_record("current", "2026-08-06T15:00:00+09:00", 1025000)
    provider = _ProviderStub({"E01081": _response(previous, current)})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    result = asyncio.run(
        service.fetch_guidance_revision(
            GuidanceRecordIdentifier("E01081", "previous"),
            GuidanceRecordIdentifier("E01081", "current"),
        )
    )

    assert result["status"] == "OK"
    assert result["metrics"]["revenue"]["revision_direction"] == "INCREASED"
    assert result["metrics"]["revenue"]["previous_value"]["value"] == 930000
    assert result["metrics"]["revenue"]["current_value"]["value"] == 1025000
    assert provider.calls == ["E01081", "E01081"]
    assert provider.limits == [8, 8]


def test_normalizer_exception_returns_data_unavailable(monkeypatch):
    old = _actual_record(
        record_id="old-q1",
        fiscal_year_end="2026-03-31",
        disclosure_date="2025-08-06",
    )
    latest = _actual_record(record_id="latest-q1")
    provider = _ProviderStub({"E01081": _response(old, latest)})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]
    normalized_ids: list[str] = []

    def fail_normalizer(record, **_kwargs):
        normalized_ids.append(record["record_id"])
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(
        "tradingagents.dataflows.japan.edinet_db_service.normalize_financial_document",
        fail_normalizer,
    )

    document = asyncio.run(
        service.fetch_financial_document("E01081", analysis_as_of=date(2026, 8, 31))
    )

    assert isinstance(document, FinancialDocument)
    assert document.status == DATA_UNAVAILABLE
    assert document.source_record_id == "latest-q1"
    assert normalized_ids == ["latest-q1"]


def test_reversed_provider_order_still_selects_latest_fiscal_period():
    latest = _actual_record(record_id="q1-fy2027")
    prior = _actual_record(
        record_id="fy2026",
        fiscal_year_end="2026-03-31",
        quarter=4,
        disclosure_date="2026-05-10",
    )
    provider = _ProviderStub({"E01081": _response(latest, prior)})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    document = asyncio.run(
        service.fetch_financial_document("E01081", analysis_as_of=date(2026, 8, 31))
    )

    assert isinstance(document, FinancialDocument)
    assert document.source_record_id == "q1-fy2027"
    actual = next(record for record in document.records if record.record_type == "ACTUAL")
    assert actual.period_type == "Q1"
    assert actual.target_period_end == "2027-03-31"


def test_later_q1_correction_does_not_replace_h1_actual():
    h1 = _actual_record(
        record_id="h1",
        quarter=2,
        disclosure_date="2026-08-07",
        revenue=300000,
    )
    q1_correction = _actual_record(
        record_id="q1-correction",
        quarter=1,
        disclosure_date="2026-08-10",
        is_correction=True,
        revenue=260605,
    )
    provider = _ProviderStub({"E01081": _response(q1_correction, h1)})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    document = asyncio.run(
        service.fetch_financial_document("E01081", analysis_as_of=date(2026, 8, 31))
    )

    assert isinstance(document, FinancialDocument)
    assert document.source_record_id == "h1"
    assert document.records[0].period_type == "H1"


def test_future_record_never_enters_financial_document():
    eligible = _actual_record(record_id="q1", disclosure_date="2026-08-06")
    future = _actual_record(
        record_id="h1-future",
        quarter=2,
        disclosure_date="2026-11-06",
    )
    provider = _ProviderStub({"E01081": _response(future, eligible)})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    document = asyncio.run(
        service.fetch_financial_document("E01081", analysis_as_of=date(2026, 8, 31))
    )

    assert isinstance(document, FinancialDocument)
    assert document.source_record_id == "q1"


def test_only_future_records_fail_closed_with_selector_diagnostic():
    provider = _ProviderStub(
        {"E01081": _response(_actual_record(disclosure_date="2026-09-01"))}
    )
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    result = asyncio.run(
        service.fetch_financial_document("E01081", analysis_as_of=date(2026, 8, 31))
    )

    assert isinstance(result, EDINETDBResponse)
    assert result.status == EDINETDBStatus.DATA_UNAVAILABLE
    assert result.detail == "FUTURE_ONLY"
    assert result.metadata["selection_detail"] == "FUTURE_ONLY"
    assert result.metadata["selection_basis"] == "LATEST_FISCAL_PERIOD_AS_OF"


def test_aware_datetime_and_date_only_same_day_fail_closed():
    provider = _ProviderStub(
        {"E01081": _response(_actual_record(disclosure_date="2026-08-06"))}
    )
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    result = asyncio.run(
        service.fetch_financial_document(
            "E01081",
            analysis_as_of=datetime(
                2026,
                8,
                6,
                10,
                tzinfo=timezone(timedelta(hours=9)),
            ),
        )
    )

    assert isinstance(result, EDINETDBResponse)
    assert result.status == EDINETDBStatus.DATA_UNAVAILABLE
    assert result.detail == "DATE_ONLY_SAME_DAY_UNRESOLVED"


def test_same_period_actual_changing_correction_fails_closed():
    original = _actual_record(record_id="original", revenue=100)
    correction = _actual_record(
        record_id="correction",
        disclosure_date="2026-08-07",
        is_correction=True,
        revenue=101,
    )
    provider = _ProviderStub({"E01081": _response(original, correction)})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    result = asyncio.run(
        service.fetch_financial_document("E01081", analysis_as_of=date(2026, 8, 31))
    )

    assert isinstance(result, EDINETDBResponse)
    assert result.status == EDINETDBStatus.DATA_UNAVAILABLE
    assert result.detail == "CORRECTION_REQUIRES_RESOLUTION"


def test_different_latest_baselines_are_ambiguous():
    first = _actual_record(record_id="first", revenue=100)
    second = _actual_record(record_id="second", revenue=101)
    provider = _ProviderStub({"E01081": _response(first, second)})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    result = asyncio.run(
        service.fetch_financial_document("E01081", analysis_as_of=date(2026, 8, 31))
    )

    assert isinstance(result, EDINETDBResponse)
    assert result.status == EDINETDBStatus.DATA_UNAVAILABLE
    assert result.detail == "AMBIGUOUS_LATEST"


def test_incomplete_latest_is_normalized_without_falling_back():
    complete_q1 = _actual_record(record_id="complete-q1", quarter=1, revenue=260604)
    incomplete_h1 = _actual_record(
        record_id="incomplete-h1",
        quarter=2,
        disclosure_date="2026-11-06",
        revenue=None,
    )
    provider = _ProviderStub({"E01081": _response(complete_q1, incomplete_h1)})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    document = asyncio.run(
        service.fetch_financial_document("E01081", analysis_as_of=date(2026, 12, 1))
    )

    assert isinstance(document, FinancialDocument)
    assert document.source_record_id == "incomplete-h1"
    assert document.records[0].period_type == "H1"
    assert document.records[0].metrics["revenue"]["status"] == "NOT_PROVIDED"


def _full_candidate_window() -> tuple[dict, ...]:
    older = tuple(
        _actual_record(
            record_id=f"older-{year}",
            fiscal_year_end=f"{year}-03-31",
            disclosure_date=f"{year - 1}-08-06",
        )
        for year in range(1998, 2027)
    )
    return (*older, _actual_record(record_id="latest-2027"))


def test_full_candidate_window_can_select_current_latest():
    records = _full_candidate_window()
    assert len(records) == 30
    provider = _ProviderStub({"E01081": _response(*records)})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    document = asyncio.run(
        service.fetch_financial_document("E01081", analysis_as_of=date(2026, 8, 31))
    )

    assert isinstance(document, FinancialDocument)
    assert document.source_record_id == "latest-2027"


def test_full_candidate_window_rejects_as_of_before_entire_window():
    records = _full_candidate_window()
    provider = _ProviderStub({"E01081": _response(*records)})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    result = asyncio.run(
        service.fetch_financial_document("E01081", analysis_as_of=date(1990, 1, 1))
    )

    assert isinstance(result, EDINETDBResponse)
    assert result.status == EDINETDBStatus.DATA_UNAVAILABLE
    assert result.detail == "CANDIDATE_WINDOW_INCOMPLETE"
    assert result.metadata["selection_detail"] == "CANDIDATE_WINDOW_INCOMPLETE"


def test_analysis_as_of_is_required():
    provider = _ProviderStub({"E01081": _response(_actual_record())})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    try:
        asyncio.run(service.fetch_financial_document("E01081"))  # type: ignore[call-arg]
    except TypeError:
        pass
    else:
        raise AssertionError("analysis_as_of must be required")

    assert provider.calls == []


def test_service_contains_no_provider_order_latest_semantic():
    source = inspect.getsource(service_module.EDINETDBFinancialService.fetch_financial_document)
    assert "records[0]" not in source
    assert "records[-1]" not in source
    assert ".sort(" not in source
    assert "sorted(" not in source
