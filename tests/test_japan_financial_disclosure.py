"""Fixture tests for the standalone official-disclosure parser."""

from tradingagents.dataflows.japan.financial_disclosure import (
    DATA_UNAVAILABLE,
    NOT_APPLICABLE,
    NOT_PROVIDED,
    parse_financial_disclosure,
    parse_financial_document,
)

ACTUAL_FIXTURE = """
2026年3月期 第1四半期 決算短信 連結 日本基準
売上高 1,234 百万円 営業利益 123 百万円 経常利益 120 百万円
親会社株主に帰属する四半期純利益 80 百万円 1株当たり四半期純利益 12.50 円
"""

GUIDANCE_FIXTURE = """
2026年3月期 連結 日本基準 通期業績予想の修正に関するお知らせ
通期業績予想 売上高 10,000 百万円 → 11,000 百万円
営業利益 1,000 百万円 → 900 百万円
経常利益 950 百万円 → 850 百万円
親会社株主に帰属する当期純利益 600 百万円 → 550 百万円
修正理由：原材料価格の上昇
"""

# Cropped from the cached public TDnet disclosure for 5016.T (2026-08-06).
# This is intentionally a small text fixture, not a report-derived fixture.
TDNET_5016_Q1_FIXTURE = """
2027年３月期 第１四半期決算短信〔ＩＦＲＳ〕(連結)
１．2027年３月期第１四半期の連結業績（2026年４月１日～2026年６月30日）
（１）連結経営成績(累計) 売上高 営業利益 税引前利益 四半期利益 親会社の所有者に帰属する 四半期利益
四半期包括利益合計額 百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％
2027年３月期第１四半期 260,604 36.2 81,446 175.5 79,644 179.9 60,418 160.0 53,070 181.3 76,356 506.7
2026年３月期第１四半期 191,276 12.1 29,558 21.8 28,459 21.3 23,242 32.7 18,865 27.8 12,585 △69.8
基本的１株当たり 四半期利益 円 銭 2027年３月期第１四半期 56.80 56.06
"""

TDNET_5016_GUIDANCE_FIXTURE = """
通期業績予想の修正に関するお知らせ
2027 年３月期 通期連結業績予想数値の修正
売上高 営業利益 税引前利益 親会社の所有者に帰属する当期利益 基本的１株当たり当期利益
前回発表予想（Ａ）（2026年５月11日発表） 百万円 930,000 百万円 190,000 百万円 178,000 百万円 114,000 円 銭 125.97
今回修正予想（Ｂ） 1,025,000 232,000 221,000 141,000 155.80
（２）修正の理由 資源事業における天候不良による鉱山の減産影響を見込むものの、
フォーカス事業においてＡＩサーバ関連需要の拡大を背景とした増販及び販売単価改善が進展したことを踏まえ修正いたします。
"""

# Cropped from the same cached public TDnet Q1 disclosure. This fixture keeps
# the official section order but intentionally omits table values: Task 1 only
# validates record segmentation, not financial amount extraction.
TDNET_5016_SEGMENTATION_FIXTURE = TDNET_5016_Q1_FIXTURE + """
３．2027年３月期の連結業績予想（2026年４月１日～2027年３月31日）
売上高 営業利益 税引前利益 当期利益 親会社の所有者に帰属する当期利益 基本的１株当たり当期利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％ 円 銭
通期 1,025,000 15.9 232,000 32.6 221,000 30.7 174,000 35.2 141,000 34.7 155.80
"""

# Cropped from cached public TDnet text for 6324.T, disclosed 2026-08-07.
TDNET_6324_SEGMENTATION_FIXTURE = """
2027年３月期 第１四半期決算短信〔日本基準〕(連結)
１．2027年３月期第１四半期の連結業績（2026年４月１日～2026年６月30日）
（１）連結経営成績(累計) 売上高 営業利益 経常利益 親会社株主に帰属する四半期純利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％
2027年３月期第１四半期 16,681 23.6 1,840 － 1,719 － 1,274 －
2026年３月期第１四半期 13,496 3.8 122 － 131 － △38 －
１株当たり四半期純利益 潜在株式調整後１株当たり四半期純利益
円 銭 円 銭
2027年３月期第１四半期 13.46 －
2026年３月期第１四半期 △0.40 －
１. 連結業績予想の修正
① 2027 年３月期 第２四半期（中間期）連結業績予想数値の修正
売上高 営業利益 経常利益 親会社株主に帰属する中間純利益 1株当たり中間純利益
前回予想（Ａ） 百万円 33,500 百万円 3,000 百万円 3,000 百万円 2,200 円 銭 23 24
② 2027 年３月期 通期連結業績予想数値の修正
売上高 営業利益 経常利益 親会社株主に帰属する当期純利益 1株当たり当期純利益
前回予想（Ａ） 百万円 68,000 百万円 6,200 百万円 6,200 百万円 4,500 円 銭 47 54
２. 個別業績予想の修正
① 2027 年３月期 第２四半期（中間期）個別業績予想数値の修正
売上高 営業利益 経常利益 中間純利益 1株当たり中間純利益
前回予想（Ａ） 百万円 20,300 百万円 1,800 百万円 3,500 百万円 2,900 円 銭 30 63
② 2027 年３月期 通期個別業績予想数値の修正
売上高 営業利益 経常利益 当期純利益 1株当たり当期純利益
前回予想（Ａ） 百万円 42,000 百万円 4,000 百万円 4,800 百万円 3,800 円 銭 40 00
"""


def test_extracts_quarterly_actual_without_cross_period_derivation():
    result = parse_financial_disclosure(
        ACTUAL_FIXTURE,
        title="2026年3月期 第1四半期 決算短信",
        source="TDnet",
        disclosure_timestamp="2026-08-06T15:30:00+09:00",
        source_url="https://example.test/q1.pdf",
    )
    assert result["period_type"] == "Q1"
    assert result["actual"]["revenue"]["value"] == 1234
    assert result["actual"]["operating_profit"]["value"] == 123
    assert result["actual"]["net_income"]["value"] == 80
    assert result["actual"]["eps"]["value"] == 12.5
    assert result["guidance"]["revenue"]["status"] == NOT_PROVIDED
    assert result["source_url"].endswith("q1.pdf")


def test_extracts_guidance_previous_current_per_field():
    result = parse_financial_disclosure(
        GUIDANCE_FIXTURE,
        title="通期業績予想の修正に関するお知らせ",
        source="TDnet",
    )
    assert result["period_type"] == "FY"
    assert result["guidance"]["revenue"]["previous_value"]["value"] == 10000
    assert result["guidance"]["revenue"]["current_value"]["value"] == 11000
    assert result["guidance"]["revenue"]["revision_direction"] == "INCREASED"
    assert result["guidance"]["operating_profit"]["revision_direction"] == "DECREASED"
    assert result["revision_reason"] == "原材料価格の上昇"
    assert result["actual"]["revenue"]["status"] == NOT_PROVIDED


def test_ifrs_ordinary_profit_is_not_applicable_and_missing_is_not_zero():
    result = parse_financial_disclosure(
        "2026年3月期 第1四半期 決算短信 連結 IFRS 売上高 100 百万円 営業利益 10 百万円",
        title="決算短信",
    )
    assert result["actual"]["ordinary_profit"]["status"] == NOT_APPLICABLE
    assert result["actual"]["net_income"]["status"] == NOT_PROVIDED
    assert result["actual"]["net_income"]["value"] is None


def test_period_and_unit_are_retained_without_conversion():
    result = parse_financial_disclosure(ACTUAL_FIXTURE, title="決算短信")
    assert result["unit"] == "百万円"
    assert result["currency"] == "JPY"
    assert result["actual"]["revenue"]["raw_value"] == "1,234"


def test_malformed_document_is_data_unavailable():
    result = parse_financial_disclosure("決算短信本文を取得できませんでした", title="決算短信")
    assert result["status"] == DATA_UNAVAILABLE
    assert result["actual"]["revenue"]["status"] == NOT_PROVIDED
    assert result["guidance"]["revenue"]["status"] == NOT_PROVIDED


def test_empty_document_is_data_unavailable_not_zero():
    result = parse_financial_disclosure("", title="通期業績予想の修正")
    assert result["status"] == DATA_UNAVAILABLE
    assert result["actual"]["revenue"]["status"] == DATA_UNAVAILABLE
    assert result["guidance"]["revenue"]["status"] == DATA_UNAVAILABLE
    assert result["actual"]["revenue"]["value"] is None


def test_real_tdnet_5016_q1_table_values_are_mapped_by_columns():
    result = parse_financial_disclosure(
        TDNET_5016_Q1_FIXTURE,
        title="2027年３月期第１四半期決算短信〔IFRS〕(連結)",
        source="TDnet",
        disclosure_timestamp="2026-08-06T15:30:00+00:00",
        source_url="https://www.release.tdnet.info/inbs/140120260805510453.pdf",
    )
    assert result["actual"]["revenue"]["value"] == 260604
    assert result["actual"]["operating_profit"]["value"] == 81446
    assert result["actual"]["net_income"]["value"] == 53070
    assert result["actual"]["ordinary_profit"]["status"] == NOT_APPLICABLE
    assert result["actual"]["eps"]["value"] == 56.8
    assert result["unit"] == "百万円"
    assert result["accounting_standard"] == "IFRS"
    assert result["consolidation_scope"] == "CONSOLIDATED"


def test_real_tdnet_5016_guidance_revision_preserves_before_after_values():
    result = parse_financial_disclosure(
        TDNET_5016_GUIDANCE_FIXTURE,
        title="通期業績予想の修正に関するお知らせ",
        source="TDnet",
        disclosure_timestamp="2026-08-06T15:30:00+00:00",
        source_url="https://www.release.tdnet.info/inbs/140120260805510520.pdf",
    )
    guidance = result["guidance"]
    assert guidance["revenue"]["previous_value"]["value"] == 930000
    assert guidance["revenue"]["current_value"]["value"] == 1025000
    assert guidance["operating_profit"]["previous_value"]["value"] == 190000
    assert guidance["operating_profit"]["current_value"]["value"] == 232000
    assert guidance["net_income"]["previous_value"]["value"] == 114000
    assert guidance["net_income"]["current_value"]["value"] == 141000
    assert guidance["eps"]["previous_value"]["value"] == 125.97
    assert guidance["eps"]["current_value"]["value"] == 155.80
    assert guidance["ordinary_profit"]["status"] == NOT_PROVIDED
    assert all(
        guidance[name]["revision_direction"] == "INCREASED"
        for name in ("revenue", "operating_profit", "net_income", "eps")
    )


def _record_keys(document):
    return {
        (record.record_type, record.fiscal_year, record.period_type, record.scope)
        for record in document.records
    }


def test_segments_5016_ifrs_actual_and_fy_consolidated_guidance():
    document = parse_financial_document(
        TDNET_5016_SEGMENTATION_FIXTURE,
        title="2027年３月期第１四半期決算短信〔IFRS〕(連結)",
        source="TDnet",
    )
    keys = _record_keys(document)
    assert document.status == "OK"
    assert ("ACTUAL", "2027", "Q1", "CONSOLIDATED") in keys
    assert ("GUIDANCE", "2027", "FY", "CONSOLIDATED") in keys
    actual = next(record for record in document.records if record.record_type == "ACTUAL")
    assert actual.metrics["revenue"]["value"] == 260604
    assert actual.metrics["operating_profit"]["value"] == 81446
    assert actual.metrics["ordinary_profit"]["status"] == NOT_APPLICABLE
    assert actual.metrics["net_income"]["value"] == 53070
    assert actual.metrics["net_income"]["value"] != 60418
    assert actual.metrics["eps"]["value"] == 56.8


def test_legacy_entry_point_uses_ifrs_parent_attributable_profit():
    result = parse_financial_disclosure(TDNET_5016_Q1_FIXTURE)
    assert result["actual"]["net_income"]["value"] == 53070
    assert result["actual"]["net_income"]["value"] != 60418


def test_segments_6324_jgaap_periods_and_scopes_without_document_level_override():
    document = parse_financial_document(
        TDNET_6324_SEGMENTATION_FIXTURE,
        title="業績予想の修正に関するお知らせ",
        source="TDnet",
    )
    assert _record_keys(document) == {
        ("ACTUAL", "2027", "Q1", "CONSOLIDATED"),
        ("GUIDANCE", "2027", "H1", "CONSOLIDATED"),
        ("GUIDANCE", "2027", "FY", "CONSOLIDATED"),
        ("GUIDANCE", "2027", "H1", "NON_CONSOLIDATED"),
        ("GUIDANCE", "2027", "FY", "NON_CONSOLIDATED"),
    }
    actual = next(record for record in document.records if record.record_type == "ACTUAL")
    assert actual.metrics["revenue"]["value"] == 16681
    assert actual.metrics["operating_profit"]["value"] == 1840
    assert actual.metrics["ordinary_profit"]["value"] == 1719
    assert actual.metrics["ordinary_profit"]["status"] == "OK"
    assert actual.metrics["net_income"]["value"] == 1274
    assert actual.metrics["eps"]["value"] == 13.46
    assert all(
        record.metrics["revenue"]["status"] == DATA_UNAVAILABLE
        for record in document.records
        if record.record_type == "GUIDANCE"
    )


def test_actual_selects_current_period_instead_of_previous_period():
    document = parse_financial_document(
        TDNET_6324_SEGMENTATION_FIXTURE,
        title="2027年３月期第１四半期決算短信〔日本基準〕(連結)",
    )
    actual = next(record for record in document.records if record.record_type == "ACTUAL")
    assert actual.metrics["revenue"]["value"] == 16681
    assert actual.metrics["operating_profit"]["value"] == 1840
    assert actual.metrics["net_income"]["value"] == 1274


def test_pre_tax_profit_is_not_mapped_to_ifrs_ordinary_profit():
    document = parse_financial_document(
        TDNET_5016_Q1_FIXTURE,
        title="2027年３月期第１四半期決算短信〔IFRS〕(連結)",
    )
    actual = document.records[0]
    assert actual.metrics["ordinary_profit"]["status"] == NOT_APPLICABLE
    assert actual.metrics["ordinary_profit"]["value"] is None


def test_split_yen_sen_is_recombined_only_in_eps_context():
    fixture = """
2027年３月期 第１四半期決算短信〔日本基準〕(連結)
１．2027年３月期第１四半期の連結業績
（１）連結経営成績(累計) 売上高 営業利益 経常利益 親会社株主に帰属する四半期純利益
百万円 ％ 百万円 ％ 百万円 ％ 百万円 ％
2027年３月期第１四半期 13 46 8 － 7 － 5 －
2026年３月期第１四半期 12 1.0 6 － 5 － 4 －
１株当たり四半期純利益 円 銭
2027年３月期第１四半期 7 08
2026年３月期第１四半期 6 12
"""
    document = parse_financial_document(fixture, title="決算短信")
    actual = document.records[0]
    assert actual.metrics["revenue"]["value"] == 13
    assert actual.metrics["revenue"]["raw_value"] == "13"
    assert actual.metrics["eps"]["value"] == 7.08
    assert actual.metrics["eps"]["raw_value"] == "7 08"


def test_missing_actual_field_is_not_zero_or_parse_failure():
    fixture = """
2027年３月期 第１四半期決算短信〔日本基準〕(連結)
１．2027年３月期第１四半期の連結業績
（１）連結経営成績(累計) 売上高 営業利益
百万円 ％ 百万円 ％
2027年３月期第１四半期 1,200 2.0 80 3.0
2026年３月期第１四半期 1,100 1.0 70 2.0
"""
    document = parse_financial_document(fixture, title="決算短信")
    actual = document.records[0]
    assert actual.metrics["ordinary_profit"]["status"] == NOT_PROVIDED
    assert actual.metrics["ordinary_profit"]["value"] is None
    assert actual.metrics["net_income"]["status"] == NOT_PROVIDED
    assert actual.metrics["net_income"]["value"] is None
