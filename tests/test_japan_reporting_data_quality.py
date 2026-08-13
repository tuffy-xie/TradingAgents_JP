"""Regressions for factual JP reporting and financial-period safety."""
from __future__ import annotations

from tradingagents.agents.schemas import SentimentBand, SentimentReport, render_sentiment_report
from tradingagents.dataflows.japan.company_ir import _find_document_links
from tradingagents.dataflows.japan.models import FiscalPeriod, periods_are_comparable
from tradingagents.presentation import (
    build_investment_overview,
    build_key_data_section,
    render_japan_item_data,
)


def test_key_data_always_has_five_ranked_entries_and_no_dict_rendering():
    state = {
        "japan_data_bundle": {
            "items": [{"source": "JSF", "timestamp": "2026-08-14T00:00:00+00:00", "title": "日证金融资 / 贷株余额", "metadata": {"finance_balance_shares": 1000, "unit": "股"}}],
            "source_statuses": [{"source": source, "status": "AUTH_REQUIRED"} for source in ("TDnet", "Company IR", "JPX", "EDINET", "J-Quants")],
        }
    }
    rendered = build_key_data_section(state)
    assert all(f"## {index}." in rendered for index in range(1, 6))
    assert rendered.index("适时开示（TDnet）") < rendered.index("日证金（JSF）")
    assert "{" not in rendered and "Company IR" not in rendered


def test_company_ir_scans_financial_document_links_not_only_landing_page():
    html = '''<a href="/ir/2026/0812/results.pdf">2026年8月12日 決算説明資料</a>
    <a href="/about">会社概要</a><a href="/ir/annual.pdf">Annual Report</a>'''
    links = _find_document_links(html, "https://example.test/ir/", 6)
    assert [url for url, _label in links] == ["https://example.test/ir/2026/0812/results.pdf", "https://example.test/ir/annual.pdf"]


def test_jsf_presentation_has_dates_units_and_no_python_dict():
    data = render_japan_item_data({
        "source": "JSF", "timestamp": "2026-08-14T00:00:00+00:00", "title": "日证金融资 / 贷株余额",
        "metadata": {"application_date": "2026-08-12", "settlement_date": "2026-08-14", "finance_new_shares": 100, "finance_balance_shares": 1000},
    })
    assert "申込日：2026-08-12" in data and "决済日：2026-08-14" in data
    assert "融资新规：100 股" in data and "{" not in data


def test_fiscal_period_forbids_ttm_quarter_comparison():
    assert not periods_are_comparable(
        FiscalPeriod("2026-03-31", "trailing_twelve_months", "2026"),
        FiscalPeriod("2026-06-30", "quarter", "2027", quarter=1),
    )


def test_missing_sentiment_is_na_without_artificial_score():
    report = SentimentReport(overall_band=SentimentBand.UNAVAILABLE, overall_score=None, confidence="low", narrative="N/A")
    rendered = render_sentiment_report(report)
    assert "**Overall Sentiment:** **N/A**" in rendered
    assert "Score: 5" not in rendered


def test_overview_uses_structured_instrument_identity():
    rendered = build_investment_overview({"company_of_interest": "8002.T", "trade_date": "2026-08-13", "instrument_identity": {"company_name": "丸红株式会社"}})
    assert "公司名称：丸红株式会社" in rendered
