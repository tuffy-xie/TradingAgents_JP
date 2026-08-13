"""Offline contracts for public company-IR document discovery."""

from __future__ import annotations

import asyncio

from tradingagents.dataflows.japan.company_ir import CompanyIRProvider
from tradingagents.dataflows.japan.http import BytesResponse
from tradingagents.dataflows.japan.models import DataStatus
from tradingagents.dataflows.market import resolve_market_context


def test_company_ir_discovers_listing_then_latest_pdf(monkeypatch):
    landing = '<a href="/ir/library">IR Library</a>'
    listing = '<a href="/ir/files/2026-08-01-results.pdf">2026年8月1日 決算短信</a>'

    async def fake_get_text(url, **_kwargs):
        if url.endswith("/ir/"):
            return DataStatus.OK, landing, ""
        if url.endswith("/ir/library"):
            return DataStatus.OK, listing, ""
        return DataStatus.DATA_UNAVAILABLE, "", "unexpected URL"

    async def fake_get_bytes(*_args, **_kwargs):
        return BytesResponse(DataStatus.OK, payload=b"%PDF-test")

    monkeypatch.setattr("tradingagents.dataflows.japan.company_ir.get_text", fake_get_text)
    monkeypatch.setattr("tradingagents.dataflows.japan.company_ir.get_bytes", fake_get_bytes)
    monkeypatch.setattr(
        "tradingagents.dataflows.japan.company_ir.extract_pdf_text",
        lambda *_args: "2026年8月1日 決算短信 売上高 100 百万円 営業利益 10 百万円",
    )
    provider = CompanyIRProvider(urls={"6981": "https://issuer.example/ir/"})
    result = asyncio.run(provider.fetch(resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13"))
    assert result.status.status == DataStatus.OK
    assert result.status.item_count == 1
    item = result.items[0]
    assert item.source_type == "earnings_or_quarterly"
    assert item.metadata["extraction_status"] == "OK"
    assert item.metadata["key_numbers"]["売上高"] == "100 百万円"


def test_company_ir_pdf_parse_failure_keeps_document_metadata(monkeypatch):
    async def fake_get_text(url, **_kwargs):
        if url.endswith("/ir/"):
            return DataStatus.OK, '<h1>IR情報</h1><a href="latest.pdf">配当予想の修正</a>', ""
        return DataStatus.DATA_UNAVAILABLE, "", "unexpected URL"

    async def fake_get_bytes(*_args, **_kwargs):
        return BytesResponse(DataStatus.OK, payload=b"not-a-pdf")

    monkeypatch.setattr("tradingagents.dataflows.japan.company_ir.get_text", fake_get_text)
    monkeypatch.setattr("tradingagents.dataflows.japan.company_ir.get_bytes", fake_get_bytes)
    provider = CompanyIRProvider(urls={"6981": "https://issuer.example/ir/"})
    result = asyncio.run(provider.fetch(resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13"))
    item = result.items[0]
    assert item.status == DataStatus.PARSE_FAILED
    assert item.title == "配当予想の修正"
    assert item.url.endswith("latest.pdf")
    assert item.metadata["extraction_status"] == "PARSE_FAILED"


def test_company_ir_rejects_unrelated_external_navigation_pdf(monkeypatch):
    async def fake_get_text(url, **_kwargs):
        if url.endswith("/ir/"):
            return DataStatus.OK, '<h1>IR情報</h1><a href="https://unrelated.example/2026-results.pdf">Financial Results</a>', ""
        return DataStatus.DATA_UNAVAILABLE, "", "unexpected URL"

    monkeypatch.setattr("tradingagents.dataflows.japan.company_ir.get_text", fake_get_text)
    provider = CompanyIRProvider(urls={"6981": "https://issuer.example/ir/"})
    result = asyncio.run(provider.fetch(resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13"))
    assert result.status.status == DataStatus.DATA_UNAVAILABLE
    assert result.status.item_count == 0
