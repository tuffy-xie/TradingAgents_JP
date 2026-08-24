from __future__ import annotations

import asyncio

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

    async def fetch_earnings(self, issuer_id: str) -> EDINETDBResponse:
        self.calls.append(issuer_id)
        return self.responses[issuer_id]


def _response(*records: dict) -> EDINETDBResponse:
    return EDINETDBResponse(
        status=EDINETDBStatus.OK,
        records=tuple(records),
        metadata={
            "source": "EDINET DB",
            "fetched_at": "2026-08-24T00:00:00+00:00",
            "record_metadata": tuple(
                {
                    "source_record_id": record.get("record_id"),
                    "source_url": record.get("pdf_url"),
                }
                for record in records
            ),
        },
    )


def _actual_record() -> dict:
    return {
        "record_id": "actual-q1",
        "title": "2027年3月期 第1四半期決算",
        "disclosure_date": "2026-08-06T15:30:00+09:00",
        "pdf_url": "https://example.test/actual-q1.pdf",
        "accounting_standard": "IFRS",
        "is_consolidated": True,
        "fiscal_year": 2027,
        "period": "Q1",
        "target_period_end": "2027-03-31",
        "currency": "JPY",
        "unit": "百万円",
        "revenue": 260604,
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

    document = asyncio.run(service.fetch_financial_document("E01081"))

    assert isinstance(document, FinancialDocument)
    assert document.status == "OK"
    assert document.source == "EDINET DB"
    assert document.source_record_id == "actual-q1"
    assert document.fetched_at == "2026-08-24T00:00:00+00:00"
    assert any(record.record_type == "ACTUAL" for record in document.records)
    assert document.records[0].metrics["revenue"]["value"] == 260604


def test_fetch_financial_document_returns_auth_required_transport_response():
    transport = EDINETDBResponse(status=EDINETDBStatus.AUTH_REQUIRED, detail="no key")
    provider = _ProviderStub({"E01081": transport})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    result = asyncio.run(service.fetch_financial_document("E01081"))

    assert result is transport
    assert result.status == EDINETDBStatus.AUTH_REQUIRED


def test_fetch_financial_document_returns_api_error_transport_response():
    transport = EDINETDBResponse(status=EDINETDBStatus.API_ERROR, detail="HTTP 500")
    provider = _ProviderStub({"E01081": transport})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    result = asyncio.run(service.fetch_financial_document("E01081"))

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


def test_normalizer_exception_returns_data_unavailable(monkeypatch):
    provider = _ProviderStub({"E01081": _response(_actual_record())})
    service = EDINETDBFinancialService(provider)  # type: ignore[arg-type]

    def fail_normalizer(*_args, **_kwargs):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(
        "tradingagents.dataflows.japan.edinet_db_service.normalize_financial_document",
        fail_normalizer,
    )

    document = asyncio.run(service.fetch_financial_document("E01081"))

    assert isinstance(document, FinancialDocument)
    assert document.status == DATA_UNAVAILABLE
    assert document.source_record_id == "actual-q1"
