"""Offline tests for official Japan-provider contracts and no-key fallbacks."""

from __future__ import annotations

import pytest

from tradingagents.dataflows.japan.context import render_japan_audience_context
from tradingagents.dataflows.japan.edinet import (
    EDINETProvider,
    _normalise_documents,
    classify_edinet_document,
    extract_large_shareholding_fields,
)
from tradingagents.dataflows.japan.http import BytesResponse, JsonResponse
from tradingagents.dataflows.japan.jquants import (
    JQuantsProvider,
    _normalise_financial_records,
    _normalise_master_records,
    _normalise_records,
    compare_daily_ohlcv,
)
from tradingagents.dataflows.japan.jsf import (
    build_supply_demand_trend,
    normalise_balances,
    normalise_premium_charges,
)
from tradingagents.dataflows.japan.models import DataStatus
from tradingagents.dataflows.japan.tdnet import (
    TDnetProvider,
    _date_range,
    _parse_public_list,
    classify_tdnet_title,
    normalise_tdnet_records,
    structure_official_disclosure,
)
from tradingagents.dataflows.market import resolve_market_context


@pytest.mark.unit
def test_tdnet_financial_window_retains_prior_month_end_disclosure_date():
    days = _date_range("2026-07-31", "2026-08-31", maximum_days=32)

    assert len(days) == 32
    assert days[0].isoformat() == "2026-07-31"
    assert days[-1].isoformat() == "2026-08-31"


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


@pytest.mark.unit
def test_edinet_invalid_submit_timestamp_is_not_replaced_by_current_time():
    payload = {
        "results": [
            {
                "secCode": "69810",
                "docID": "S100X",
                "docDescription": "大量保有報告書",
                "docTypeCode": "350",
                "formCode": "010000",
                "submitDateTime": "not-a-timestamp",
            }
        ]
    }
    assert list(_normalise_documents(payload, resolve_market_context("6981.T"))) == []


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
    payload = {"results": [{"secCode": "69810", "docID": "S100X", "docDescription": "大量保有報告書", "docTypeCode": "350", "formCode": "010000", "submitDateTime": "2026-08-12T10:00:00+09:00"}]}
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
    assert classify_tdnet_title("自己株式の取得") == "buyback"
    assert items[0].metadata["extraction_status"] == "NOT_REQUESTED"


@pytest.mark.unit
def test_tdnet_authorised_feed_invalid_timestamp_fails_closed(monkeypatch):
    async def fake_get_json(*_args, **_kwargs):
        return JsonResponse(
            DataStatus.OK,
            {
                "items": [
                    {
                        "ticker": "5016",
                        "date": "not-a-date",
                        "title": "決算短信",
                    }
                ]
            },
        )

    monkeypatch.setattr("tradingagents.dataflows.japan.tdnet.get_json", fake_get_json)
    result = __import__("asyncio").run(
        TDnetProvider(feed_url="https://example.test/feed").fetch(
            resolve_market_context("5016.T"),
            start_date="2026-08-01",
            end_date="2026-08-15",
        )
    )

    assert result.status.status == DataStatus.DATA_UNAVAILABLE
    assert result.items == ()
    assert result.metadata["coverage"]["complete"] is False
    assert "timestamp" in result.status.detail


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
    assert items[0].source_type == "buyback"
    assert items[0].url.endswith("notice.pdf")


@pytest.mark.unit
def test_tdnet_fetch_preserves_transport_failure_details(monkeypatch):
    async def failed_get_text(*_args, **_kwargs):
        return DataStatus.DATA_UNAVAILABLE, "", "ConnectionError: DNS failure"

    monkeypatch.setattr("tradingagents.dataflows.japan.tdnet.get_text", failed_get_text)
    result = __import__("asyncio").run(
        TDnetProvider().fetch(resolve_market_context("5016.T"), start_date="2026-08-06", end_date="2026-08-06")
    )
    assert result.items == ()
    assert result.status.status == DataStatus.DATA_UNAVAILABLE
    assert "retrieval_failed=true" in result.status.detail
    assert "2026-08-06" in result.status.detail
    assert "I_list_001_20260806.html" in result.status.detail


@pytest.mark.unit
def test_tdnet_timeout_is_not_reported_as_empty(monkeypatch):
    async def timed_out_get_text(*_args, **_kwargs):
        return DataStatus.DATA_UNAVAILABLE, "", "TimeoutError: request timed out"

    monkeypatch.setattr("tradingagents.dataflows.japan.tdnet.get_text", timed_out_get_text)
    result = __import__("asyncio").run(
        TDnetProvider().fetch(resolve_market_context("5016.T"), start_date="2026-08-06", end_date="2026-08-06")
    )
    assert result.status.status == DataStatus.DATA_UNAVAILABLE
    assert result.items == ()
    assert "TimeoutError" in result.status.detail


@pytest.mark.unit
def test_tdnet_successful_empty_is_not_a_fetch_failure(monkeypatch):
    async def empty_get_text(*_args, **_kwargs):
        return DataStatus.OK, "<html><body>に開示された情報はありません。</body></html>", ""

    monkeypatch.setattr("tradingagents.dataflows.japan.tdnet.get_text", empty_get_text)
    result = __import__("asyncio").run(
        TDnetProvider().fetch(resolve_market_context("5016.T"), start_date="2026-08-06", end_date="2026-08-06")
    )
    assert result.status.status == DataStatus.OK
    assert result.items == ()
    assert "empty_result=true" in result.status.detail
    assert result.metadata["coverage"]["complete"] is True


def _tdnet_index_page(*, page: int, total: int, target: bool = False) -> str:
    page_size = 100
    start = (page - 1) * page_size + 1
    end = min(page * page_size, total)
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


@pytest.mark.unit
def test_tdnet_fetches_target_beyond_legacy_ten_page_cap(monkeypatch):
    requested_pages = []

    async def fake_get_text(url, **_kwargs):
        page = int(url.rsplit("I_list_", 1)[1][:3])
        requested_pages.append(page)
        return DataStatus.OK, _tdnet_index_page(
            page=page, total=1201, target=page == 12
        ), ""

    monkeypatch.setattr("tradingagents.dataflows.japan.tdnet.get_text", fake_get_text)
    provider = TDnetProvider()
    provider.max_pages_per_day = 20
    provider.extract_pdf_text = False
    result = __import__("asyncio").run(
        provider.fetch(
            resolve_market_context("5016.T"),
            start_date="2026-08-06",
            end_date="2026-08-06",
        )
    )

    assert result.status.status == DataStatus.OK
    assert len(result.items) == 1
    assert result.items[0].metadata["index_page"] == 12
    assert set(requested_pages) == set(range(1, 14))
    assert result.metadata["coverage"]["complete"] is True
    assert result.metadata["coverage"]["days"][0]["advertised_page_count"] == 13


@pytest.mark.unit
def test_tdnet_safety_ceiling_remains_fail_closed(monkeypatch):
    requested_pages = []

    async def fake_get_text(url, **_kwargs):
        page = int(url.rsplit("I_list_", 1)[1][:3])
        requested_pages.append(page)
        return DataStatus.OK, _tdnet_index_page(page=page, total=1201), ""

    monkeypatch.setattr("tradingagents.dataflows.japan.tdnet.get_text", fake_get_text)
    provider = TDnetProvider()
    provider.max_pages_per_day = 10
    provider.extract_pdf_text = False
    result = __import__("asyncio").run(
        provider.fetch(
            resolve_market_context("5016.T"),
            start_date="2026-08-06",
            end_date="2026-08-06",
        )
    )

    assert result.status.status == DataStatus.DATA_UNAVAILABLE
    assert set(requested_pages) == set(range(1, 11))
    assert result.metadata["coverage"]["complete"] is False
    assert result.metadata["coverage"]["days"][0]["advertised_page_count"] == 13
    assert "reached tdnet_max_pages_per_day=10" in result.status.detail


@pytest.mark.unit
def test_tdnet_inconsistent_page_summary_is_not_complete(monkeypatch):
    async def fake_get_text(url, **_kwargs):
        page = int(url.rsplit("I_list_", 1)[1][:3])
        total = 201 if page != 2 else 202
        return DataStatus.OK, _tdnet_index_page(page=page, total=total), ""

    monkeypatch.setattr("tradingagents.dataflows.japan.tdnet.get_text", fake_get_text)
    provider = TDnetProvider()
    provider.extract_pdf_text = False
    result = __import__("asyncio").run(
        provider.fetch(
            resolve_market_context("5016.T"),
            start_date="2026-08-06",
            end_date="2026-08-06",
        )
    )

    assert result.status.status == DataStatus.DATA_UNAVAILABLE
    assert result.items == ()
    assert result.metadata["coverage"]["complete"] is False
    assert "inconsistent TDnet pagination summary" in result.status.detail


@pytest.mark.unit
def test_tdnet_pdf_body_wins_when_it_explicitly_conflicts_with_index_title(monkeypatch):
    html = """
    <tr><td class="kjTime">15:00</td><td class="kjCode">69810</td><td class="kjName">Murata</td>
    <td class="kjTitle"><a href="notice.pdf">自己株式の取得</a></td></tr>
    """
    item = list(_parse_public_list(html, resolve_market_context("6981.T"), __import__("datetime").date(2026, 8, 12), 1))[0]

    async def fake_get_bytes(*_args, **_kwargs):
        return BytesResponse(DataStatus.OK, payload=b"%PDF-test")

    monkeypatch.setattr("tradingagents.dataflows.japan.tdnet.get_bytes", fake_get_bytes)
    monkeypatch.setattr(
        "tradingagents.dataflows.japan.tdnet.extract_pdf_text",
        lambda *_args: "業績予想の上方修正に関するお知らせ。売上高 100 百万円。",
    )
    enriched = __import__("asyncio").run(TDnetProvider()._extract_pdf(item))
    assert enriched.source_type == "guidance_revision"
    assert enriched.metadata["title_body_conflict"] is True
    assert enriched.metadata["guidance_change"] == "UPWARD"


@pytest.mark.unit
def test_tdnet_pdf_failure_preserves_index_metadata(monkeypatch):
    html = """
    <tr><td class="kjTime">15:00</td><td class="kjCode">69810</td><td class="kjName">Murata</td>
    <td class="kjTitle"><a href="notice.pdf">業績予想の修正</a></td></tr>
    """
    item = list(_parse_public_list(html, resolve_market_context("6981.T"), __import__("datetime").date(2026, 8, 12), 1))[0]

    async def fake_get_bytes(*_args, **_kwargs):
        return BytesResponse(DataStatus.OK, payload=b"not-a-pdf")

    monkeypatch.setattr("tradingagents.dataflows.japan.tdnet.get_bytes", fake_get_bytes)
    enriched = __import__("asyncio").run(TDnetProvider()._extract_pdf(item))
    assert enriched.status == DataStatus.PARSE_FAILED
    assert enriched.title == "業績予想の修正"
    assert enriched.url.endswith("notice.pdf")
    assert enriched.metadata["extraction_status"] == "PARSE_FAILED"


@pytest.mark.unit
def test_official_disclosure_structuring_does_not_invent_missing_values():
    fields = structure_official_disclosure(
        title="配当予想の修正に関するお知らせ", text="配当予想を修正します。",
        source="TDnet", event_date="2026-08-12", extraction_status="OK",
    )
    assert fields["event_type"] == "dividend"
    assert fields["dividend_change"] == "REVISED"
    assert fields["buyback_amount"] is None


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


@pytest.mark.unit
def test_jsf_preliminary_is_latest_available_and_confirmed_wins_same_date():
    balances = (
        "申込日,銘柄コード,銘柄名,融資残高株数,貸株残高株数,速報／確報\n"
        "2026/09/01,6981,村田製作所,1000,200,速報\n"
        "2026/08/31,6981,村田製作所,900,180,確報\n"
    ).encode("cp932")
    items = normalise_balances(balances, resolve_market_context("6981.T"))
    assert items[-1].metadata["publication_status"] == "PRELIMINARY"
    assert items[-1].metadata["application_date"] == "2026-09-01"

    same_date = (
        "申込日,銘柄コード,銘柄名,融資残高株数,貸株残高株数,速報／確報\n"
        "2026/09/01,6981,村田製作所,1000,200,速報\n"
        "2026/09/01,6981,村田製作所,1100,220,確報\n"
    ).encode("cp932")
    confirmed = normalise_balances(same_date, resolve_market_context("6981.T"))
    assert len(confirmed) == 1
    assert confirmed[0].metadata["publication_status"] == "CONFIRMED"
    assert confirmed[0].metadata["finance_balance"] == 1100.0


@pytest.mark.unit
def test_jsf_unknown_publication_status_is_not_promoted():
    balances = (
        "申込日,銘柄コード,銘柄名,融資残高株数,貸株残高株数,速報／確報\n"
        "2026/09/01,6981,村田製作所,1000,200,不明\n"
    ).encode("cp932")
    item = normalise_balances(balances, resolve_market_context("6981.T"))[0]
    assert item.metadata["publication_status"] == "UNKNOWN"


@pytest.mark.unit
def test_jsf_renderer_preserves_preliminary_label():
    item = normalise_balances(
        (
            "申込日,銘柄コード,銘柄名,融資残高株数,速報／確報\n"
            "2026/09/01,6981,村田製作所,1000,速報\n"
        ).encode("cp932"),
        resolve_market_context("6981.T"),
    )[0]
    rendered = render_japan_audience_context(
        {
            "market_context": {"market": "JP"},
            "japan_data_bundle": {"items": [item.to_dict()]},
        },
        "MARKET",
    )
    assert "publication_status': 'PRELIMINARY'" in rendered
    assert "CONFIRMED" not in rendered


@pytest.mark.unit
def test_jsf_history_schema_keeps_official_share_unit_and_dates():
    payload = (
        "銘柄コード,銘柄名,申込日,市場区分,融資新規（株）,融資返済（株）,融資残高（株）,貸株新規（株）,貸株返済（株）,貸株残高（株）,差引残高（株）\n"
        "6981,村田製作所,20260812,東証,100,50,1000,20,10,200,800\n"
    ).encode("cp932")
    item = normalise_balances(payload, resolve_market_context("6981.T"), historical=True)[0]
    assert item.metadata["unit"] == "株"
    assert item.metadata["application_date"] == "2026-08-12"
    assert item.metadata["settlement_date"] is None
    assert item.metadata["report_type"] == "UNSPECIFIED"


@pytest.mark.unit
def test_jsf_history_trend_requires_history_and_detects_confirmed_buildup():
    header = "銘柄コード,銘柄名,申込日,市場区分,融資新規（株）,融資返済（株）,融資残高（株）,貸株新規（株）,貸株返済（株）,貸株残高（株）,差引残高（株）\n"
    rows = []
    for day in range(1, 22):
        rows.append(f"6981,村田製作所,202607{day:02d},東証,200,100,{1000 + day * 100},50,10,{100 + day * 20},{900 + day * 80}")
    items = normalise_balances((header + "\n".join(rows)).encode("cp932"), resolve_market_context("6981.T"), historical=True)
    trend = build_supply_demand_trend(items, resolve_market_context("6981.T"), "2026-07-01", "2026-07-21")
    assert trend.metadata["observation_count"] == 21
    assert trend.metadata["finance_balance_change_5d"] == pytest.approx(500 / 2600, abs=1e-6)
    assert trend.metadata["finance_balance_change_20d"] == pytest.approx(2000 / 1100, abs=1e-6)
    assert trend.metadata["finance_balance_trend"] == "INCREASING"
    assert trend.metadata["rapid_finance_buildup"] is False


@pytest.mark.unit
def test_jsf_history_outlier_is_not_turned_into_trend():
    header = "銘柄コード,銘柄名,申込日,市場区分,融資新規（株）,融資返済（株）,融資残高（株）,貸株新規（株）,貸株返済（株）,貸株残高（株）,差引残高（株）\n"
    rows = [f"6981,村田製作所,202608{day:02d},東証,10,5,{100 if day < 6 else 1000},1,1,100,0" for day in range(1, 7)]
    items = normalise_balances((header + "\n".join(rows)).encode("cp932"), resolve_market_context("6981.T"), historical=True)
    trend = build_supply_demand_trend(items, resolve_market_context("6981.T"), "2026-08-01", "2026-08-06")
    assert trend.metadata["finance_balance_change_5d"] is None
    assert "OUTLIER_5D_CHANGE_GT_300_PCT" in trend.metadata["anomaly_flags"]
    assert trend.metadata["finance_balance_trend"] == "UNCONFIRMED"
