"""Offline tests for official Japan-provider contracts and no-key fallbacks."""

from __future__ import annotations

import pytest

from tradingagents.dataflows.japan.edinet import (
    EDINETProvider,
    _normalise_documents,
    classify_edinet_document,
    extract_large_shareholding_fields,
)
from tradingagents.dataflows.japan.jquants import JQuantsProvider, _normalise_records
from tradingagents.dataflows.japan.jsf import normalise_balances, normalise_premium_charges
from tradingagents.dataflows.japan.models import DataStatus
from tradingagents.dataflows.japan.tdnet import (
    _parse_public_list,
    classify_tdnet_title,
    normalise_tdnet_records,
)
from tradingagents.dataflows.market import resolve_market_context


@pytest.mark.unit
def test_jquants_without_key_returns_auth_required(monkeypatch):
    monkeypatch.delenv("JQUANTS_API_KEY", raising=False)
    result = __import__("asyncio").run(
        JQuantsProvider().fetch(
            resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13"
        )
    )
    assert result.status.status == DataStatus.AUTH_REQUIRED


@pytest.mark.unit
def test_edinet_without_key_returns_auth_required(monkeypatch):
    monkeypatch.delenv("EDINET_API_KEY", raising=False)
    result = __import__("asyncio").run(
        EDINETProvider().fetch(
            resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13"
        )
    )
    assert result.status.status == DataStatus.AUTH_REQUIRED


@pytest.mark.unit
def test_jquants_normalizes_official_v2_daily_record():
    payload = {"data": [{"Date": "2026-08-12", "Code": "69810", "Open": 100, "Close": 110, "Volume": 20}]}
    items = list(_normalise_records(payload, resolve_market_context("6981.T")))
    assert items[0].verified is True
    assert items[0].metadata["ohlcv"]["Close"] == 110


@pytest.mark.unit
def test_edinet_only_keeps_matching_security_and_labels_large_holding():
    payload = {"results": [
        {"secCode": "69810", "docID": "S100X", "docDescription": "大量保有報告書", "submitDateTime": "2026-08-12T10:00:00+09:00"},
        {"secCode": "12340", "docID": "S100Y", "docDescription": "有価証券報告書"},
    ]}
    items = list(_normalise_documents(payload, resolve_market_context("6981.T")))
    assert len(items) == 1
    assert items[0].source_type == "large_shareholding_report"
    assert classify_edinet_document("四半期報告書", "") == "quarterly_report"
    fields = extract_large_shareholding_fields("株券等保有割合 6.03%")
    assert fields["reported_holding_percentages"] == [6.03]
    assert fields["position_change"] == "UNDETERMINED"


@pytest.mark.unit
def test_tdnet_parser_classifies_verified_disclosure():
    payload = {"items": [{"ticker": "6981", "date": "2026-08-12", "title": "業績予想の上方修正に関するお知らせ", "url": "https://example.test"}]}
    items = list(normalise_tdnet_records(payload, resolve_market_context("6981.T")))
    assert items[0].verified is True
    assert items[0].source_type == "guidance_revision"
    assert classify_tdnet_title("自己株式の取得") == "share_buyback"


@pytest.mark.unit
def test_tdnet_public_index_parser_keeps_only_matching_code_and_pdf_link():
    html = """
    <tr><td class="kjTime">15:00</td><td class="kjCode">69810</td><td class="kjName">Murata</td>
    <td class="kjTitle"><a href="notice.pdf">自己株式の取得</a></td></tr>
    <tr><td class="kjTime">15:00</td><td class="kjCode">12340</td><td class="kjName">Other</td>
    <td class="kjTitle"><a href="other.pdf">Other notice</a></td></tr>
    """
    items = list(_parse_public_list(html, resolve_market_context("6981.T"), __import__("datetime").date(2026, 8, 12), 1))
    assert len(items) == 1
    assert items[0].source_type == "share_buyback"
    assert items[0].url.endswith("notice.pdf")


@pytest.mark.unit
def test_jsf_csv_normalizers_keep_facts_separate():
    balances = (
        "申込日,決済日,銘柄コード,銘柄名,融資新規株数,融資返済株数,融資残高株数,貸株新規株数,貸株返済株数,貸株残高株数,差引残高株数,速報／確報\n"
        "2026/08/12,2026/08/14,6981,村田製作所,100,50,1000,20,10,200,800,確報\n"
    ).encode("cp932")
    charges = (
        "貸借申込日,コード,銘柄名,貸株超過株数,当日品貸料率（円）,当日品貸日数\n"
        "20260812,6981,村田製作所,100,0.05,1\n"
    ).encode("cp932")
    context = resolve_market_context("6981.T")
    balance = normalise_balances(balances, context)[0]
    assert balance.timestamp.date().isoformat() == "2026-08-14"
    assert balance.metadata["application_date"] == "2026-08-12"
    assert balance.metadata["finance_balance_shares"] == 1000
    assert balance.metadata["unit"] == "股"
    assert normalise_premium_charges(charges, context)[0].metadata["premium_charge_yen"] == 0.05
