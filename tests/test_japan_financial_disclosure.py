"""Synthetic fixture tests for the standalone official-disclosure parser."""

from tradingagents.dataflows.japan.financial_disclosure import (
    DATA_UNAVAILABLE,
    NOT_APPLICABLE,
    NOT_PROVIDED,
    parse_financial_disclosure,
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
