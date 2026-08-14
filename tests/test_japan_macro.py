from __future__ import annotations

import asyncio

from tradingagents.dataflows.japan.macro import JapanMacroProvider, _cross, _value
from tradingagents.dataflows.market import resolve_market_context


def test_market_values_include_frequency_as_of_and_changes():
    value = _value(120.0, "2026-08-13", "Yahoo Finance", "daily", [100, 110, 120])
    assert value["status"] == "OK"
    assert value["frequency"] == "daily"
    assert value["as_of"] == "2026-08-13"
    assert value["change_1d"] == 0.090909


def test_cross_market_never_claims_when_a_required_input_is_unavailable():
    ok = _value(100, "2026-08-13", "test", "daily", [90, 100])
    data = {key: dict(ok) for key in ("sp500", "nikkei_225", "nasdaq_100", "sox", "usd_jpy", "vix")}
    data["sox"] = {"status": "DATA_UNAVAILABLE"}
    rows = _cross(data)["conclusions"]
    assert (
        next(row for row in rows if row["factor"].startswith("US technology / semiconductor"))[
            "status"
        ]
        == "DATA_UNAVAILABLE"
    )


def test_japan_macro_provider_emits_structured_context(monkeypatch):
    async def markets():
        return {
            key: _value(100, "2026-08-13", "Yahoo Finance", "daily", [99, 100])
            for key in (
                "usd_jpy",
                "nikkei_225",
                "topix",
                "sp500",
                "nasdaq_100",
                "nasdaq_composite",
                "sox",
                "vix",
                "dxy",
            )
        }

    async def series(_):
        return {
            "japan_10y_yield": _value(1.5, "2026-07-01", "FRED", "Monthly"),
            "japan_cpi": _value(110, "2026-07-01", "FRED", "Monthly"),
        }

    monkeypatch.setattr("tradingagents.dataflows.japan.macro._market_snapshot", markets)
    monkeypatch.setattr("tradingagents.dataflows.japan.macro._fred_snapshot", series)
    result = asyncio.run(
        JapanMacroProvider().fetch(
            resolve_market_context("6981.T"), start_date="2026-08-01", end_date="2026-08-13"
        )
    )
    macro = result.items[0].metadata["macro_context"]
    assert result.status.status == "OK"
    assert macro["usd_jpy"]["value"] == 100
    assert macro["boj_policy"]["status"] in {"OK", "DATA_UNAVAILABLE"}
