"""Offline tests for official Japan-provider contracts and no-key fallbacks."""

from __future__ import annotations

import pytest

from tradingagents.dataflows.japan.edinet import (
    EDINETProvider,
    _normalise_documents,
    classify_edinet_document,
    extract_large_shareholding_fields,
)
from tradingagents.dataflows.japan.http import BytesResponse, JsonResponse
from tradingagents.dataflows.japan.jquants import (
    JQuantsProvider,
    compare_daily_ohlcv,
    _normalise_financial_records,
    _normalise_master_records,
    _normalise_records,
)
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
    payload = {"data": [{"Date": "2026-08-12", "Code": "69810", "O": 100, "C": 110, "Vo": 20, "Va": 2200}]}
    items = list(_normalise_records(payload, resolve_market_context("6981.T")))
    assert items[0].verified is True
    assert items[0].metadata["ohlcv"]["close"] == 110
    assert items[0].metadata["raw_ohlcv"]["C"] == 110


@pytest.mark.unit
def test_jquants_normalizes_master_and_quarterly_financial_summary():
    context = resolve_market_context("6981.T")
    master = list(_normalise_master_records({"data": [{"Date": "2026-05-21", "Code": "69810", "CoName": "Murata"}]}, context))
    financial = list(_normalise_financial_records({"data": [{"DiscDate": "2026-05-15", "Code": "69810", "CurPerType": "1Q", "Sales": 100}]}, context))
    assert master[0].source_type == "official_security_master"
    assert master[0].metadata["security_master"]["CoName"] == "Murata"
    assert financial[0].source_type == "official_financial_summary"
    assert financial[0].metadata["period_type"] == "1Q"


@pytest.mark.unit
def test_jquants_yfinance_comparison_labels_adjusted_basis_and_conflict():
    item = list(_normalise_records({"data": [{"Date": "2026-05-01", "Code": "58010", "C": 41190, "AdjC": 4119}]}, resolve_market_context("5801.T")))[0]
    matched = compare_daily_ohlcv([item], {"2026-05-01": {"Close": 4119}})[0]
    conflict = compare_daily_ohlcv([item], {"2026-05-01": {"Close": 4000}})[0]
    assert matched["status"] == "MATCH"
    assert matched["basis"] == "J-Quants 调整后价格（复权口径）"
    assert conflict["status"] == "CONFLICT"


@pytest.mark.unit
def test_jquants_keeps_endpoint_availability_in_source_status(monkeypatch):
    responses = iter([
        JsonResponse(DataStatus.DATA_UNAVAILABLE, detail="date range unavailable"),
        JsonResponse(DataStatus.OK, {"data": [{"Date": "2026-05-21", "Code": "69810"}]}),
        JsonResponse(DataStatus.OK, {"data": [{"DiscDate": "2026-05-15", "Code": "69810", "CurPerType": "1Q"}]}),
    ])

    async def fake_get_json(*_args, **_kwargs):
        return next(responses)

    monkeypatch.setattr("tradingagents.dataflows.japan.jquants.get_json", fake_get_json)
    result = __import__("asyncio").run(
        JQuantsProvider(api_key="test").fetch(
            resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13"
        )
    )
    assert result.status.status == DataStatus.OK
    assert "daily_bars=DATA_UNAVAILABLE:date range unavailable" in result.status.detail
    assert {item.source_type for item in result.items} == {"official_security_master", "official_financial_summary"}


@pytest.mark.unit
def test_edinet_only_keeps_matching_security_and_labels_large_holding():
    payload = {"results": [
        {"secCode": "69810", "docID": "S100X", "docDescription": "大量保有報告書", "docTypeCode": "350", "formCode": "010000", "submitDateTime": "2026-08-12T10:00:00+09:00"},
        {"secCode": "12340", "docID": "S100Y", "docDescription": "有価証券報告書"},
    ]}
    items = list(_normalise_documents(payload, resolve_market_context("6981.T")))
    assert len(items) == 1
    assert items[0].source_type == "large_shareholding_report"
    assert classify_edinet_document("四半期報告書", "") == "quarterly_report"
    fields = extract_large_shareholding_fields("発行者の名称 株式会社例 証券コード 6981 株券等保有割合 6.03%")
    assert fields["security_code"] == "6981"
    assert fields["current_holding_ratio"] == 6.03
    assert fields["position_change"] == "UNDETERMINED"


def test_edinet_large_holding_pdf_fields_only_compare_explicit_ratios():
    text = """
    【提出者（大量保有者）】 氏名又は名称 ブラックロック・ジャパン株式会社 住所又は本店所在地 東京都
    【報告義務発生日】 2026年8月4日 【提出日】 2026年8月12日
    発行者の名称 株式会社テスト 証券コード 6981
    【保有目的】 純投資 （３）【重要提案行為等】 該当なし
    上記提出者の株券等保有割合（％） 6.03
    直前の報告書に記載された 株券等保有割合（％） 5.12
    """
    fields = extract_large_shareholding_fields(text)
    assert fields == {
        "submit_date": "2026-08-12", "event_date": "2026-08-04",
        "holder_name": "ブラックロック・ジャパン株式会社", "issuer_name": "株式会社テスト",
        "security_code": "6981", "current_holding_ratio": 6.03, "previous_holding_ratio": 5.12,
        "position_change": "INCREASED", "purpose_of_holding": "純投資",
    }


def test_edinet_position_change_is_undetermined_without_explicit_previous_ratio():
    fields = extract_large_shareholding_fields("発行者の名称 株式会社例 証券コード 6981 株券等保有割合 5.01")
    assert fields["current_holding_ratio"] == 5.01
    assert fields["previous_holding_ratio"] is None
    assert fields["position_change"] == "UNDETERMINED"


@pytest.mark.unit
def test_edinet_non_pdf_document_response_is_retained_as_parse_failed(monkeypatch):
    payload = {"results": [{"secCode": "69810", "docID": "S100X", "docDescription": "大量保有報告書", "docTypeCode": "350", "formCode": "010000"}]}
    item = list(_normalise_documents(payload, resolve_market_context("6981.T")))[0]

    async def fake_get_bytes(*_args, **_kwargs):
        return BytesResponse(DataStatus.OK, payload=b'{"Status":"error"}')

    monkeypatch.setattr("tradingagents.dataflows.japan.edinet.get_bytes", fake_get_bytes)
    enriched = __import__("asyncio").run(EDINETProvider(api_key="test")._enrich_large_holding(item))
    assert enriched.status == DataStatus.PARSE_FAILED
    assert enriched.metadata["extraction_status"] == "PARSE_FAILED"
    assert "Subscription-Key" not in str(enriched.url)


@pytest.mark.unit
def test_edinet_fetch_puts_confirmed_structured_holding_into_provider_response(monkeypatch):
    payload = {"results": [{
        "secCode": "69810", "docID": "S100X", "docDescription": "変更報告書",
        "docTypeCode": "350", "formCode": "010002", "submitDateTime": "2026-08-12 09:00",
    }]}

    async def fake_get_json(*_args, **_kwargs):
        return JsonResponse(DataStatus.OK, payload)

    async def fake_get_bytes(*_args, **_kwargs):
        return BytesResponse(DataStatus.OK, payload=b"%PDF-test")

    monkeypatch.setattr("tradingagents.dataflows.japan.edinet.get_json", fake_get_json)
    monkeypatch.setattr("tradingagents.dataflows.japan.edinet.get_bytes", fake_get_bytes)
    monkeypatch.setattr("tradingagents.dataflows.japan.tdnet.extract_pdf_text", lambda *_args: """
        【提出者（大量保有者）】 氏名又は名称 テスト投資 住所又は本店所在地 東京
        報告義務発生日 2026年8月4日 提出日 2026年8月12日
        発行者の名称 テスト発行者 証券コード 6981 保有目的 純投資 （３）重要提案行為等
        上記提出者の株券等保有割合（％） 6.03 直前の報告書に記載された 株券等保有割合（％） 5.12
    """)
    result = __import__("asyncio").run(
        EDINETProvider(api_key="test", max_days=1).fetch(
            resolve_market_context("6981.T"), start_date="2026-08-12", end_date="2026-08-12"
        )
    )
    assert result.status.status == DataStatus.OK
    assert result.status.item_count == 1
    fields = result.items[0].metadata
    assert fields["position_change"] == "INCREASED"
    assert fields["issuer_name"] == "テスト発行者"
    assert fields["source_url"].endswith("?type=2")


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
        "申込日,銘柄コード,銘柄名,融資残高株数,貸株残高株数,差引残高株数,速報／確報\n"
        "2026/08/12,6981,村田製作所,1000,200,800,確報\n"
    ).encode("cp932")
    charges = (
        "貸借申込日,コード,銘柄名,貸株超過株数,当日品貸料率（円）,当日品貸日数\n"
        "20260812,6981,村田製作所,100,0.05,1\n"
    ).encode("cp932")
    context = resolve_market_context("6981.T")
    assert normalise_balances(balances, context)[0].metadata["finance_balance"] == 1000
    assert normalise_premium_charges(charges, context)[0].metadata["premium_charge_yen"] == 0.05
