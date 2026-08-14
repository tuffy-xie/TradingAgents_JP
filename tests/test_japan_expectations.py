"""Regression tests for dated, non-official Japanese analyst expectations."""

from tradingagents.dataflows.japan.expectations import _parse_consensus
from tradingagents.dataflows.market import Market, resolve_market_context


def _page(*, counts="強気買い2人、買い1人、中立1人", prior="9,900", current="10,000"):
    return f"""
    2026/08/14時点における、テスト社に対する、アナリスト判断（コンセンサス）は、買い。内訳は、{counts}。
    アナリストの平均目標株価は{current}円で、この１週間で{prior}円から{current}円と判断が変更されました。
    <table><tr><th>証券アナリスト予想</th></tr>
    <tr><th>3ヶ月前 (2026/05/14)</th><th>1ヶ月前 (2026/07/14)</th><th>1週間前 (2026/08/07)</th><th>最新 (2026/08/14)</th></tr>
    <tr><th>売上高</th><td>100</td><td>140</td><td>120</td><td>130</td></tr>
    <tr><th>当期利益</th><td>10</td><td>14</td><td>12</td><td>13</td></tr>
    <tr><th>1株当り利益</th><td>1.0</td><td>1.4</td><td>1.2</td><td>1.3</td></tr></table>
    2027年3月期<br>アナリスト予想
    """


def test_consensus_is_dated_and_kept_separate_from_company_guidance():
    parsed = _parse_consensus(_page())
    assert parsed is not None
    assert parsed["published_at"] == "2026/08/14"
    assert parsed["analyst_count"] == 4
    assert parsed["consensus_target_price"] == 10000.0
    assert parsed["target_change"] == 100.0
    assert parsed["current_price"] is None
    assert parsed["upside_downside"] is None
    assert parsed["company_guidance"] is None
    earnings = parsed["earnings_expectations"]
    assert earnings["fiscal_year"] == 2027
    assert earnings["period_type"] == "FY"
    assert earnings["eps_estimate"] == 1.3
    assert earnings["operating_profit_estimate"] is None
    assert earnings["ordinary_profit_estimate"] is None


def test_zero_analyst_count_cannot_create_a_consensus():
    parsed = _parse_consensus(_page(counts="", current="10,000"))
    assert parsed is not None
    assert parsed["analyst_count"] == 0
    assert parsed["consensus_target_price"] is None
    assert parsed["consensus_rating"] is None
    assert parsed["status"] == "DATA_UNAVAILABLE"


def test_revision_trend_is_metric_based_not_individual_analyst_counts():
    parsed = _parse_consensus(_page())
    assert parsed is not None
    assert parsed["revision_trend"]["30D"]["metric_downward_revisions"] == 3
    assert parsed["revision_trend"]["90D"]["metric_upward_revisions"] == 3
    assert "not individual analyst" in parsed["revision_trend"]["90D"]["note"]


def test_nvda_remains_us_and_does_not_use_japan_expectations_route():
    assert resolve_market_context("NVDA").market is Market.US


def test_expectations_provider_is_registered_for_japan_bundle():
    from tradingagents.dataflows.japan.official import build_official_japan_providers

    assert "Japan Analyst Expectations" in {
        provider.name for provider in build_official_japan_providers()
    }
