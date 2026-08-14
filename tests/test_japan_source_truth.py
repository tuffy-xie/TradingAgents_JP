"""Source-of-truth regressions: no silent overwrite or cross-period math."""

from datetime import UTC, date, datetime

from tradingagents.dataflows.japan.context import render_japan_agent_context
from tradingagents.dataflows.japan.models import InformationLayer, MarketInformation
from tradingagents.dataflows.japan.truth import (
    EvidenceValue,
    build_governance_item,
    compare_evidence,
    detect_conflicts,
    freshness_for_item,
)


def _item(source, source_type, metadata, *, timestamp=datetime(2026, 8, 14, tzinfo=UTC)):
    return MarketInformation(
        source=source,
        source_type=source_type,
        ticker="6981.T",
        timestamp=timestamp,
        layer=InformationLayer.VERIFIED_FACT,
        metadata=metadata,
    )


def test_guidance_and_analyst_estimate_are_different_basis_not_conflict():
    guidance = _item("TDnet", "guidance_revision", {"key_numbers": {"売上高": "2,110,000百万円"}})
    analyst = _item(
        "Minkabu Analyst Consensus",
        "japan_analyst_expectations",
        {
            "analyst_expectations": {
                "earnings_expectations": {
                    "fiscal_year": 2027,
                    "period_type": "FY",
                    "unit": {"revenue": "million JPY"},
                    "revenue_estimate": 2080600.0,
                    "net_income_estimate": None,
                    "eps_estimate": None,
                }
            }
        },
    )
    records = detect_conflicts([guidance, analyst])
    assert records[0]["status"] == "DIFFERENT_BASIS"


def test_guidance_basis_relation_is_retained_when_official_pdf_has_no_numbers():
    guidance = _item("Company IR", "guidance_revision", {"key_numbers": {}})
    analyst = _item("Minkabu Analyst Consensus", "japan_analyst_expectations", {})
    records = detect_conflicts([guidance, analyst])
    assert records == [
        {
            "metric": "company_guidance_vs_analyst_consensus",
            "left": {"source_group": "TDnet / Company IR", "basis": "COMPANY_GUIDANCE"},
            "right": {"source_group": "Analyst Consensus", "basis": "ANALYST_ESTIMATE"},
            "status": "DIFFERENT_BASIS",
            "note": "not a numeric conflict; guidance and consensus must remain separate",
        }
    ]


def test_period_and_adjustment_basis_are_never_silently_reconciled():
    comparable = EvidenceValue(
        "eps",
        100,
        "A",
        "ANALYST_ESTIMATE",
        datetime(2026, 8, 14, tzinfo=UTC),
        2027,
        "FY",
        "JPY",
    )
    assert compare_evidence(comparable, comparable) == "MATCH"
    assert (
        compare_evidence(
            comparable,
            EvidenceValue(
                "eps",
                90,
                "B",
                "ANALYST_ESTIMATE",
                datetime(2026, 8, 14, tzinfo=UTC),
                2027,
                "FY",
                "JPY",
            ),
        )
        == "CONFLICT"
    )
    assert (
        compare_evidence(
            EvidenceValue(
                "eps",
                100,
                "A",
                "ANALYST_ESTIMATE",
                datetime(2026, 8, 14, tzinfo=UTC),
                2027,
                "FY",
                "JPY",
            ),
            EvidenceValue(
                "eps",
                20,
                "B",
                "ANALYST_ESTIMATE",
                datetime(2026, 8, 14, tzinfo=UTC),
                2027,
                "Q1",
                "JPY",
            ),
        )
        == "UNCOMPARABLE_PERIOD"
    )
    ohlcv = _item(
        "J-Quants", "official_ohlcv", {"raw_ohlcv": {"Close": 100}, "ohlcv": {"adjusted_close": 50}}
    )
    assert detect_conflicts([ohlcv])[0]["status"] == "DIFFERENT_BASIS"


def test_frequency_specific_freshness_and_jsf_unit_preservation():
    as_of = date(2026, 8, 14)
    assert (
        freshness_for_item(
            _item("J-Quants", "official_ohlcv", {}, timestamp=datetime(2026, 8, 13, tzinfo=UTC)),
            as_of,
        )
        == "CURRENT"
    )
    assert (
        freshness_for_item(
            _item("Japan News", "japan_stock_news", {}, timestamp=datetime(2026, 8, 5, tzinfo=UTC)),
            as_of,
        )
        == "STALE"
    )
    jsf = _item("JSF", "securities_finance_balance", {"unit": "株", "finance_balance": 1234})
    governance = build_governance_item([jsf], "6981.T", as_of)
    assert governance.metadata["source_of_truth"]["credit_supply_demand"].startswith("JSF")
    assert jsf.metadata["unit"] == "株"


def test_governance_allows_market_tools_with_optional_snapshot_diagnostic():
    governance = build_governance_item([], "6981.T", date(2026, 8, 14))
    assert (
        governance.metadata["source_of_truth"]["current_price_ohlcv_technical"]
        == "Market Analyst get_stock_data/get_indicators; Verified Market Snapshot is optional diagnostic"
    )
    assert governance.metadata["derived_values"].startswith("PROHIBITED")


def test_all_japan_agents_receive_exact_number_and_period_rules():
    governance = build_governance_item([], "6981.T", date(2026, 8, 14)).to_dict()
    rendered = render_japan_agent_context(
        {"market_context": {"market": "JP"}, "japan_data_bundle": {"items": [governance]}}
    )
    assert "get_stock_data/get_indicators" in rendered
    assert "optional diagnostic" in rendered
    assert "FY/H1/Q1/Q2/Q3/Q4/TTM/Forecast/Analyst Estimate" in rendered
    assert "AI inference" in rendered
    assert render_japan_agent_context({"market_context": {"market": "US"}}) == ""


def test_japan_context_does_not_make_snapshot_an_technical_data_gate():
    governance = build_governance_item([], "5016.T", date(2026, 8, 14)).to_dict()
    rendered = render_japan_agent_context(
        {"market_context": {"market": "JP"}, "japan_data_bundle": {"items": [governance]}}
    )
    assert "get_stock_data/get_indicators" in rendered
    assert "not an admission gate" in rendered
    assert "may only be copied from Verified Market Snapshot" not in rendered
