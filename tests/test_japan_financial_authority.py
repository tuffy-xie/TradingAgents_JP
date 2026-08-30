from __future__ import annotations

from dataclasses import replace
from datetime import date

from tradingagents.dataflows.japan.context import render_japan_financial_context
from tradingagents.dataflows.japan.edinet_db_normalizer import normalize_financial_document
from tradingagents.dataflows.japan.financial_authority import (
    FRESHNESS_CURRENT_OFFICIAL,
    FRESHNESS_CURRENT_STRUCTURED,
    FRESHNESS_OFFICIAL_UPDATE_UNPARSED,
    FRESHNESS_UNVERIFIED,
    STATUS_INSUFFICIENT,
    STATUS_OK,
    assess_japan_financial_data,
)


def _structured(*, disclosure="2026-08-06", revenue=260604, quarter=1):
    document = normalize_financial_document(
        {
            "record_id": "structured-q1",
            "disclosure_date": disclosure,
            "accounting_standard": "IFRS",
            "is_consolidated": True,
            "quarter": quarter,
            "fiscal_year_end": "2027-03-31",
            "revenue": revenue,
            "operating_income": 81446,
            "net_income": 53070,
            "profit_ifrs": 60418,
            "eps": 56.8,
            "forecast_revenue": 1025000,
            "forecast_operating_income": 232000,
            "forecast_net_income": 141000,
            "forecast_eps": 155.8,
        },
        fetched_at="2026-08-15T00:00:00+00:00",
    )
    actual = next(record for record in document.records if record.record_type == "ACTUAL")
    guidance = next(record for record in document.records if record.record_type == "GUIDANCE")
    return replace(document, records=(actual,)), replace(document, records=(guidance,))


def _metadata(*, disclosure="2026-08-06", revenue=260604, quarter=1):
    actual, guidance = _structured(
        disclosure=disclosure, revenue=revenue, quarter=quarter
    )
    return {
        "EDINET DB Financials": {
            "actual_document": actual.to_dict(),
            "guidance_document": guidance.to_dict(),
            "actual_status": "OK",
            "guidance_status": "OK",
            "actual_selection": {"disclosure_date": disclosure},
            "guidance_selection": {"disclosure_date": disclosure},
        }
    }


def _official_item(
    *,
    disclosure="2026-08-06",
    event_type="earnings_or_quarterly",
    content="",
    title="Official financial update",
    source="TDnet",
):
    return {
        "source": source,
        "source_type": event_type,
        "verified": True,
        "timestamp": f"{disclosure}T15:00:00+09:00",
        "title": title,
        "content": content,
        "url": "https://example.test/official.pdf",
        "metadata": {
            "date": disclosure,
            "event_type": event_type,
            "extraction_status": "OK" if content else "DATA_UNAVAILABLE",
        },
    }


def _assess(
    *,
    metadata=None,
    items=(),
    tdnet_status="OK",
    tdnet_detail="",
    scan_start="2026-08-01",
    tdnet_coverage=None,
    company_ir_coverage=None,
):
    provider_metadata = metadata or _metadata()
    if tdnet_coverage is not None:
        provider_metadata = {**provider_metadata, "TDnet": {"coverage": tdnet_coverage}}
    if company_ir_coverage is not None:
        provider_metadata = {
            **provider_metadata,
            "Company IR": {"coverage": company_ir_coverage},
        }
    return assess_japan_financial_data(
        items=items,
        source_statuses=[
            {"source": "TDnet", "status": tdnet_status, "detail": tdnet_detail},
            {"source": "Company IR", "status": "OK", "detail": ""},
        ],
        provider_metadata=provider_metadata,
        analysis_as_of=date(2026, 8, 15),
        official_scan_start=date.fromisoformat(scan_start),
    )


def test_official_and_structured_same_date_pass_freshness_and_gates():
    result = _assess(items=[_official_item()])

    assert result["actual"]["status"] == STATUS_OK
    assert result["actual"]["freshness"] == FRESHNESS_CURRENT_STRUCTURED
    assert result["actual"]["critical_gate"]["available_count"] == 3
    assert result["guidance"]["status"] == STATUS_OK
    assert result["guidance"]["critical_gate"]["available_count"] == 4


def test_newer_unparsed_official_update_withholds_complete_structured_values():
    result = _assess(items=[_official_item(disclosure="2026-08-15")])

    for kind in ("actual", "guidance"):
        assert result[kind]["status"] == STATUS_INSUFFICIENT
        assert result[kind]["freshness"] == FRESHNESS_OFFICIAL_UPDATE_UNPARSED
        assert result[kind]["document"] is None


def test_guidance_revision_updates_guidance_without_replacing_actual():
    result = _assess(
        items=[
            _official_item(),
            _official_item(
                disclosure="2026-08-15",
                event_type="guidance_revision",
                title="通期業績予想の修正に関するお知らせ",
            ),
        ]
    )

    assert result["actual"]["status"] == STATUS_OK
    assert result["actual"]["selected_disclosure_date"] == "2026-08-06"
    assert result["guidance"]["status"] == STATUS_INSUFFICIENT
    assert result["guidance"]["official_latest_update"]["disclosure_date"] == "2026-08-15"


def test_reliably_parsed_official_guidance_revision_replaces_only_guidance():
    content = """
通期業績予想の修正に関するお知らせ
2027年3月期 通期連結業績予想数値の修正
売上高 営業利益 税引前利益 親会社の所有者に帰属する当期利益 基本的１株当たり当期利益
前回発表予想（Ａ）（2026年5月11日発表） 百万円 930,000 百万円 190,000 百万円 178,000 百万円 114,000 円 銭 125.97
今回修正予想（Ｂ） 1,025,000 232,000 221,000 141,000 155.80
（２）修正の理由 販売単価の改善を踏まえ修正いたします。
"""
    result = _assess(
        items=[
            _official_item(),
            _official_item(
                disclosure="2026-08-15",
                event_type="guidance_revision",
                content=content,
                title="2027年3月期 通期業績予想の修正に関するお知らせ",
            ),
        ]
    )

    assert result["actual"]["selected_disclosure_date"] == "2026-08-06"
    assert result["guidance"]["status"] == STATUS_OK
    assert result["guidance"]["freshness"] == FRESHNESS_CURRENT_OFFICIAL
    assert result["guidance"]["selected_disclosure_date"] == "2026-08-15"
    revenue = result["guidance"]["document"]["records"][0]["metrics"]["revenue"]
    assert revenue["previous_value"]["value"] == 930000
    assert revenue["current_value"]["value"] == 1025000


def test_later_old_period_correction_does_not_replace_latest_actual():
    correction_text = """
2027年３月期 第１四半期決算短信〔ＩＦＲＳ〕(連結)
（１）連結経営成績(累計) 売上高 営業利益 税引前利益 四半期利益 親会社の所有者に帰属する 四半期利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％
2027年３月期第１四半期 260,604 36.2 81,446 175.5 79,644 179.9 60,418 160.0 53,070 181.3
基本的１株当たり 四半期利益 円 銭 2027年３月期第１四半期 56.80
"""
    correction = _official_item(
        disclosure="2026-08-15",
        event_type="earnings_or_quarterly",
        title="2027年3月期 第1四半期決算短信の訂正〔IFRS〕(連結)",
        content=correction_text,
    )
    result = _assess(
        metadata=_metadata(disclosure="2026-08-07", quarter=2),
        items=[_official_item(disclosure="2026-08-07"), correction],
    )

    assert result["actual"]["status"] == STATUS_OK
    assert result["actual"]["selected_disclosure_date"] == "2026-08-07"
    assert result["actual"]["official_latest_update"]["disclosure_date"] == "2026-08-07"


def test_actual_gate_requires_two_critical_metrics_and_never_uses_zero():
    metadata = _metadata()
    record = metadata["EDINET DB Financials"]["actual_document"]["records"][0]
    for key in ("operating_profit", "net_income"):
        record["metrics"][key].update(status="DATA_UNAVAILABLE", value=None)

    result = _assess(metadata=metadata)

    assert result["actual"]["status"] == STATUS_INSUFFICIENT
    assert result["actual"]["document"] is None
    assert 0 not in [metric.get("value") for metric in record["metrics"].values()]


def test_ifrs_ordinary_profit_not_applicable_does_not_fail_actual_gate():
    result = _assess()
    record = result["actual"]["document"]["records"][0]

    assert record["metrics"]["ordinary_profit"]["status"] == "NOT_APPLICABLE"
    assert result["actual"]["critical_gate"]["status"] == STATUS_OK


def test_guidance_not_provided_is_distinct_when_freshness_is_proven():
    metadata = _metadata()
    entry = metadata["EDINET DB Financials"]
    entry["guidance_document"] = None
    entry["guidance_status"] = "NOT_PROVIDED"

    result = _assess(metadata=metadata)

    assert result["guidance"]["status"] == "NOT_PROVIDED"
    assert result["guidance"]["freshness"] == FRESHNESS_CURRENT_STRUCTURED


def test_guidance_not_provided_fails_closed_without_official_coverage():
    metadata = _metadata()
    entry = metadata["EDINET DB Financials"]
    entry["guidance_document"] = None
    entry["guidance_status"] = "NOT_PROVIDED"

    result = _assess(metadata=metadata, tdnet_status="DATA_UNAVAILABLE")

    assert result["guidance"]["status"] == STATUS_INSUFFICIENT
    assert result["guidance"]["freshness"] == FRESHNESS_UNVERIFIED


def test_unknown_unit_fails_gate_instead_of_being_guessed():
    metadata = _metadata()
    record = metadata["EDINET DB Financials"]["actual_document"]["records"][0]
    for metric in record["metrics"].values():
        if metric.get("status") == "OK":
            metric["unit"] = "DATA_UNAVAILABLE"

    result = _assess(metadata=metadata)

    assert result["actual"]["status"] == STATUS_INSUFFICIENT


def test_future_official_disclosure_does_not_enter_assessment():
    result = _assess(items=[_official_item(disclosure="2026-08-16")])

    assert result["actual"]["status"] == STATUS_OK
    assert result["actual"]["official_latest_update"] is None


def test_future_structured_document_is_never_renderable():
    result = _assess(metadata=_metadata(disclosure="2026-08-16"))

    assert result["actual"]["status"] == STATUS_INSUFFICIENT
    assert result["actual"]["document"] is None


def test_rfc1123_live_disclosure_date_is_preserved_as_calendar_date():
    metadata = _metadata()
    document = metadata["EDINET DB Financials"]["actual_document"]
    document["disclosure_timestamp"] = "Fri, 07 Aug 2026 00:00:00 GMT"
    metadata["EDINET DB Financials"]["actual_selection"]["disclosure_date"] = "2026-08-07"

    result = _assess(metadata=metadata, scan_start="2026-08-01")

    assert result["actual"]["status"] == STATUS_OK
    assert result["actual"]["selected_disclosure_date"] == "2026-08-07"


def test_official_parsed_actual_overrides_same_basis_structured_conflict():
    content = """
2027年３月期 第１四半期決算短信〔ＩＦＲＳ〕(連結)
１．2027年３月期第１四半期の連結業績（2026年４月１日～2026年６月30日）
（１）連結経営成績(累計) 売上高 営業利益 税引前利益 四半期利益 親会社の所有者に帰属する 四半期利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％
2027年３月期第１四半期 260,604 36.2 81,446 175.5 79,644 179.9 60,418 160.0 53,070 181.3
基本的１株当たり 四半期利益 円 銭 2027年３月期第１四半期 56.80
"""
    result = _assess(
        metadata=_metadata(revenue=999999),
        items=[
            _official_item(
                content=content,
                title="2027年3月期 第1四半期決算短信〔IFRS〕(連結)",
            )
        ],
    )

    assert result["actual"]["status"] == STATUS_OK
    assert result["actual"]["freshness"] == FRESHNESS_CURRENT_OFFICIAL
    assert result["actual"]["conflict"] == "OFFICIAL_OVERRIDES_STRUCTURED"
    metric = result["actual"]["document"]["records"][0]["metrics"]["revenue"]
    assert metric["value"] == 260604


def test_scan_window_start_after_structured_disclosure_fails_closed():
    result = _assess(scan_start="2026-08-07")

    assert result["actual"]["status"] == STATUS_INSUFFICIENT
    assert result["actual"]["freshness"] == FRESHNESS_UNVERIFIED


def test_tdnet_page_cap_means_official_window_is_not_complete():
    result = _assess(tdnet_detail="2 day(s) reached tdnet_max_pages_per_day=10")

    assert result["actual"]["status"] == STATUS_INSUFFICIENT
    assert result["actual"]["freshness"] == FRESHNESS_UNVERIFIED


def test_structured_tdnet_coverage_metadata_is_the_completeness_authority():
    result = _assess(
        tdnet_coverage={
            "status": "COMPLETE",
            "complete": True,
            "requested_start_date": "2026-08-01",
            "requested_end_date": "2026-08-15",
        }
    )

    assert result["actual"]["status"] == STATUS_OK
    assert result["official_coverage"] == {
        "status": "COMPLETE",
        "complete": True,
        "sources": ["TDnet"],
        "coverage_start": "2026-08-01",
        "coverage_end": "2026-08-15",
        "reason": "ALL_ADVERTISED_TDNET_PAGES_VALIDATED",
    }


def test_effective_scan_start_uses_actual_tdnet_public_window():
    result = _assess(
        scan_start="2026-07-01",
        tdnet_coverage={
            "status": "COMPLETE",
            "complete": True,
            "requested_start_date": "2026-07-16",
            "requested_end_date": "2026-08-15",
        },
    )

    assert result["official_scan_start"] == "2026-07-16"
    assert result["actual"]["status"] == STATUS_OK


def test_incomplete_tdnet_metadata_fails_even_without_legacy_detail_marker():
    result = _assess(
        tdnet_coverage={
            "status": "INCOMPLETE",
            "complete": False,
            "requested_start_date": "2026-08-01",
            "requested_end_date": "2026-08-15",
        }
    )

    assert result["actual"]["status"] == STATUS_INSUFFICIENT
    assert result["actual"]["freshness"] == FRESHNESS_UNVERIFIED
    assert result["official_coverage"]["complete"] is False


def test_complete_marker_ending_before_analysis_date_fails_closed():
    result = _assess(
        tdnet_coverage={
            "status": "COMPLETE",
            "complete": True,
            "requested_start_date": "2026-08-01",
            "requested_end_date": "2026-08-14",
        }
    )

    assert result["actual"]["status"] == STATUS_INSUFFICIENT
    assert result["official_coverage"]["complete"] is False


def test_unknown_date_company_ir_cannot_complete_partial_tdnet_coverage():
    item = _official_item(source="Company IR", title="Latest financial results")
    result = _assess(
        items=[item],
        tdnet_status="DATA_UNAVAILABLE",
        tdnet_coverage={"status": "INCOMPLETE", "complete": False},
    )

    assert result["actual"]["status"] == STATUS_INSUFFICIENT
    assert result["official_coverage"]["reason"] == "NO_COMPLETE_OFFICIAL_AUTHORITY_PATH"


def test_explicitly_dated_company_ir_update_supplements_freshness_detection():
    result = _assess(
        items=[
            _official_item(
                disclosure="2026-08-15",
                source="Company IR",
                title="2026-08-15 通期業績予想の修正に関するお知らせ",
                event_type="guidance_revision",
            )
        ]
    )

    assert result["actual"]["status"] == STATUS_OK
    assert result["guidance"]["status"] == STATUS_INSUFFICIENT
    assert result["guidance"]["freshness"] == FRESHNESS_OFFICIAL_UPDATE_UNPARSED
    assert result["guidance"]["official_latest_update"]["sources"] == ["Company IR"]


def test_explicit_complete_company_ir_window_can_be_an_authority_path():
    result = _assess(
        tdnet_status="DATA_UNAVAILABLE",
        tdnet_coverage={"status": "INCOMPLETE", "complete": False},
        company_ir_coverage={
            "status": "COMPLETE",
            "complete": True,
            "requested_start_date": "2026-08-01",
            "requested_end_date": "2026-08-15",
        },
    )

    assert result["actual"]["status"] == STATUS_OK
    assert result["official_coverage"]["sources"] == ["Company IR"]


def test_conflicting_official_values_fail_closed():
    template = """
2027年３月期 第１四半期決算短信〔ＩＦＲＳ〕(連結)
１．2027年３月期第１四半期の連結業績（2026年４月１日～2026年６月30日）
（１）連結経営成績(累計) 売上高 営業利益 税引前利益 四半期利益 親会社の所有者に帰属する 四半期利益
四半期包括利益合計額 百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％
2027年３月期第１四半期 {revenue} 36.2 81,446 175.5 79,644 179.9 60,418 160.0 53,070 181.3 76,356 506.7
2026年３月期第１四半期 191,276 12.1 29,558 21.8 28,459 21.3 23,242 32.7 18,865 27.8 12,585 △69.8
基本的１株当たり 四半期利益 円 銭 2027年３月期第１四半期 56.80
"""
    items = [
        _official_item(
            content=template.format(revenue="260,604"),
            title="2027年3月期 第1四半期決算短信〔IFRS〕(連結)",
        ),
        _official_item(
            content=template.format(revenue="260,999"),
            title="2026年8月6日 2027年3月期 第1四半期決算短信〔IFRS〕(連結)",
            source="Company IR",
        ),
    ]

    result = _assess(items=items)

    assert result["actual"]["status"] == "CONFLICT_UNRESOLVED"
    assert result["actual"]["document"] is None


def test_fundamentals_renderer_exposes_freshness_but_not_withheld_old_values():
    assessment = _assess(items=[_official_item(disclosure="2026-08-15")])
    state = {
        "market_context": {"market": "JP"},
        "japan_data_bundle": {
            "provider_metadata": {"Japan Financial Authority": assessment}
        },
    }

    rendered = render_japan_financial_context(state)

    assert "OFFICIAL_UPDATE_VALUES_UNAVAILABLE" in rendered
    assert "Latest official financial update: 2026-08-15" in rendered
    assert "Normalized critical values: DATA_UNAVAILABLE" in rendered
    assert "260604" not in rendered
    assert "HTTP" not in rendered
