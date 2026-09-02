"""Deterministic Stage-7 acceptance for official financial freshness coverage."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, timedelta

from tradingagents.dataflows.japan.cache import JapanDataCache
from tradingagents.dataflows.japan.context import (
    render_japan_agent_context,
    render_japan_financial_context,
)
from tradingagents.dataflows.japan.edinet_db_normalizer import (
    normalize_financial_document,
)
from tradingagents.dataflows.japan.models import (
    DataStatus,
    ProviderResponse,
    SourceStatus,
)
from tradingagents.dataflows.japan.service import JapanDataService
from tradingagents.dataflows.japan.tdnet import TDnetProvider
from tradingagents.dataflows.market import resolve_market_context


class _StructuredFinancialProvider:
    name = "EDINET DB Financials"
    category = "fundamentals"
    cache_version = "stage7-test"
    disclosure_date = "2026-08-06"

    async def fetch(self, _context, *, start_date, end_date):
        del start_date
        document = normalize_financial_document(
            {
                "record_id": "earnings-5016-q1",
                "disclosure_date": self.disclosure_date,
                "accounting_standard": "IFRS",
                "is_consolidated": True,
                "quarter": 1,
                "fiscal_year_end": "2027-03-31",
                "revenue": 260604,
                "operating_income": 81446,
                "net_income": 53070,
                "profit_ifrs": 60418,
                "eps": 56.8,
                "forecast_revenue": 1025000,
                "forecast_operating_income": 232000,
                "forecast_net_income": 141000,
                "forecast_eps": 155.8,
            },
            fetched_at=f"{end_date}T00:00:00+00:00",
        )
        actual = next(record for record in document.records if record.record_type == "ACTUAL")
        guidance = next(
            record for record in document.records if record.record_type == "GUIDANCE"
        )
        actual_document = replace(document, records=(actual,))
        guidance_document = replace(document, records=(guidance,))
        return ProviderResponse(
            SourceStatus(self.name, DataStatus.OK, item_count=2),
            metadata={
                "actual_document": actual_document.to_dict(),
                "guidance_document": guidance_document.to_dict(),
                "actual_status": "OK",
                "guidance_status": "OK",
                "actual_selection": {"disclosure_date": self.disclosure_date},
                "guidance_selection": {"disclosure_date": self.disclosure_date},
                "analysis_as_of": end_date,
            },
        )


class _EarlierStructuredFinancialProvider(_StructuredFinancialProvider):
    disclosure_date = "2026-07-31"


class _CoverageSpyProvider:
    name = "TDnet"
    category = "official_disclosures"
    cache_version = "dynamic-coverage-test"

    def __init__(self):
        self.requested_start = None

    async def fetch(self, _context, *, start_date, end_date):
        self.requested_start = start_date
        days = []
        current = date.fromisoformat(start_date)
        finish = date.fromisoformat(end_date)
        while current <= finish:
            days.append(
                {
                    "date": current.isoformat(),
                    "status": "COMPLETE",
                    "complete": True,
                }
            )
            current += timedelta(days=1)
        return ProviderResponse(
            SourceStatus(self.name, DataStatus.OK),
            metadata={
                "coverage": {
                    "status": "COMPLETE",
                    "complete": True,
                    "requested_start_date": start_date,
                    "requested_end_date": end_date,
                    "complete_day_count": len(days),
                    "days": days,
                }
            },
        )


def test_official_coverage_starts_at_selected_structured_disclosure(tmp_path):
    tdnet = _CoverageSpyProvider()
    bundle = asyncio.run(
        JapanDataService(
            (_EarlierStructuredFinancialProvider(), tdnet),
            cache=JapanDataCache(tmp_path),
            timeout_seconds=2,
        ).collect(
            resolve_market_context("6981.T"),
            start_date="2026-08-26",
            end_date="2026-09-02",
            provider_start_dates={"TDnet": "2026-08-02"},
        )
    ).to_dict()

    assessment = bundle["provider_metadata"]["Japan Financial Authority"]

    assert tdnet.requested_start == "2026-07-31"
    assert assessment["official_scan_start"] == "2026-07-31"
    assert assessment["official_coverage"]["status"] == "COMPLETE"
    assert assessment["actual"]["status"] == "OK"
    assert assessment["guidance"]["status"] == "OK"


def _index_page(*, page: int, total: int, target: bool = False) -> str:
    start = (page - 1) * 100 + 1
    end = min(page * 100, total)
    row = ""
    if target:
        row = """
        <tr><td class="kjTime">15:00</td><td class="kjCode">50160</td>
        <td class="kjName">JX Advanced Metals</td><td class="kjTitle">
        <a href="notice.pdf">2027年3月期 第1四半期決算短信</a></td></tr>
        """
    return (
        f'<div class="kaijiSum">{start}～{end}件&nbsp;/&nbsp;全{total}件</div>'
        f"<table>{row}</table>"
    )


def _state(bundle):
    return {
        "market_context": {"market": "JP"},
        "japan_data_bundle": bundle,
    }


def test_page_beyond_ten_proves_freshness_through_fundamentals_context(
    monkeypatch, tmp_path
):
    requested_pages = []

    async def fake_get_text(url, **_kwargs):
        page = int(url.rsplit("I_list_", 1)[1][:3])
        requested_pages.append(page)
        return DataStatus.OK, _index_page(
            page=page, total=1201, target=page == 12
        ), ""

    monkeypatch.setattr("tradingagents.dataflows.japan.tdnet.get_text", fake_get_text)
    tdnet = TDnetProvider()
    tdnet.max_pages_per_day = 20
    tdnet.extract_pdf_text = False
    bundle = asyncio.run(
        JapanDataService(
            (_StructuredFinancialProvider(), tdnet),
            cache=JapanDataCache(tmp_path),
            timeout_seconds=2,
        ).collect(
            resolve_market_context("5016.T"),
            start_date="2026-08-06",
            end_date="2026-08-06",
            provider_start_dates={"TDnet": "2026-08-06"},
        )
    ).to_dict()

    assessment = bundle["provider_metadata"]["Japan Financial Authority"]
    financial = render_japan_financial_context(_state(bundle))
    shared = render_japan_agent_context(_state(bundle))

    assert set(requested_pages) == set(range(1, 14))
    assert assessment["official_coverage"]["status"] == "COMPLETE"
    assert assessment["actual"]["status"] == "OK"
    assert assessment["guidance"]["status"] == "OK"
    assert "Official freshness coverage: COMPLETE" in financial
    assert "Revenue: 260604 百万円" in financial
    assert "Revenue: 1025000 百万円" in financial
    assert "260604" not in shared
    assert "1025000" not in shared


def test_project_safety_ceiling_withholds_structured_values(monkeypatch, tmp_path):
    async def fake_get_text(url, **_kwargs):
        page = int(url.rsplit("I_list_", 1)[1][:3])
        return DataStatus.OK, _index_page(page=page, total=1201), ""

    monkeypatch.setattr("tradingagents.dataflows.japan.tdnet.get_text", fake_get_text)
    tdnet = TDnetProvider()
    tdnet.max_pages_per_day = 10
    tdnet.extract_pdf_text = False
    bundle = asyncio.run(
        JapanDataService(
            (_StructuredFinancialProvider(), tdnet),
            cache=JapanDataCache(tmp_path),
            timeout_seconds=2,
        ).collect(
            resolve_market_context("5016.T"),
            start_date="2026-08-06",
            end_date="2026-08-06",
            provider_start_dates={"TDnet": "2026-08-06"},
        )
    ).to_dict()

    assessment = bundle["provider_metadata"]["Japan Financial Authority"]
    financial = render_japan_financial_context(_state(bundle))

    assert assessment["official_coverage"]["status"] == "INCOMPLETE"
    assert assessment["actual"]["status"] == "INSUFFICIENT_DATA"
    assert assessment["guidance"]["status"] == "INSUFFICIENT_DATA"
    assert "Normalized critical values: DATA_UNAVAILABLE" in financial
    assert "260604" not in financial
    assert "1025000" not in financial


def test_non_jp_context_does_not_render_stage7_financial_assessment():
    state = {
        "market_context": {"market": "US"},
        "japan_data_bundle": {
            "provider_metadata": {
                "Japan Financial Authority": {"status": "OK"}
            }
        },
    }

    assert render_japan_financial_context(state) == ""
    assert render_japan_agent_context(state) == ""
