"""Japan macro and US-to-Japan cross-market context (JP route only)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import yfinance as yf

from tradingagents.dataflows import fred
from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus

_MARKETS = {
    "usd_jpy": ("JPY=X", "USD/JPY"),
    "nikkei_225": ("^N225", "Nikkei 225"),
    "topix": ("^TOPX", "TOPIX"),
    "sp500": ("^GSPC", "S&P 500"),
    "nasdaq_100": ("^NDX", "Nasdaq 100"),
    "nasdaq_composite": ("^IXIC", "Nasdaq Composite"),
    "sox": ("^SOX", "SOX Semiconductor Index"),
    "vix": ("^VIX", "VIX"),
    "dxy": ("DX-Y.NYB", "US Dollar Index"),
}
_FRED = {
    "japan_10y_yield": ("IRLTLT01JPM156N", "Japan 10Y government yield", "monthly"),
    "japan_cpi": ("JPNCPIALLMINMEI", "Japan CPI", "monthly"),
    "us_10y_yield": ("DGS10", "US 10Y Treasury yield", "daily"),
    "fed_policy_rate": ("FEDFUNDS", "Fed funds rate", "monthly"),
    "us_cpi": ("CPIAUCSL", "US CPI", "monthly"),
    "us_pce": ("PCEPI", "US PCE", "monthly"),
    "us_employment": ("PAYEMS", "US nonfarm payrolls", "monthly"),
}


class JapanMacroProvider:
    name, category, cache_version = "Japan Macro", "macro", "jp-us-cross-market-v1"

    def __init__(self):
        self.enabled = bool(
            get_config()
            .get("markets", {})
            .get("jp", {})
            .get("datasources", {})
            .get("japan_macro", True)
        )

    async def fetch(
        self, context: MarketContext, *, start_date: str, end_date: str
    ) -> ProviderResponse:
        if not self.enabled:
            return ProviderResponse(
                SourceStatus(
                    self.name, DataStatus.DISABLED, detail="disabled by markets.jp.datasources"
                )
            )
        markets, series = await asyncio.gather(_market_snapshot(), _fred_snapshot(end_date))
        macro = {
            **markets,
            **series,
            "boj_policy": _na("BOJ policy / recent meeting", "event-driven"),
            "fed_recent_meeting": _na("Fed recent meeting", "event-driven"),
            "sector_index": _na("issuer sector index", "daily"),
        }
        macro["cross_market"] = _cross(macro)
        item = MarketInformation(
            source=self.name,
            source_type="jp_cross_market_macro_context",
            ticker=context.symbol,
            timestamp=datetime.now(UTC),
            title="Japan macro and cross-market context",
            content="Structured source-attributed macro context.",
            confidence=0.85,
            verified=True,
            layer=InformationLayer.VERIFIED_FACT,
            metadata={"as_of": end_date, "macro_context": macro},
        )
        ok = any(x.get("status") == "OK" for key, x in macro.items() if key != "cross_market")
        return ProviderResponse(
            SourceStatus(
                self.name, DataStatus.OK if ok else DataStatus.DATA_UNAVAILABLE, item_count=1
            ),
            (item,),
        )


async def _market_snapshot():
    async def one(key, spec):
        ticker, label = spec
        try:
            frame = await asyncio.to_thread(
                lambda: yf.Ticker(ticker).history(period="2mo", auto_adjust=False)
            )
            closes = frame["Close"].dropna() if not frame.empty else []
            if len(closes) < 2:
                return key, _na(label, "daily")
            values = [float(x) for x in closes]
            return key, _value(
                values[-1], str(closes.index[-1].date()), "Yahoo Finance", "daily", values
            )
        except Exception as exc:
            return key, _na(label, "daily", type(exc).__name__)

    return dict(await asyncio.gather(*(one(k, v) for k, v in _MARKETS.items())))


async def _fred_snapshot(end_date):
    async def one(key, spec):
        series, label, frequency = spec

        def fetch():
            meta = fred._request("series", {"series_id": series}).get("seriess") or []
            obs = (
                fred._request(
                    "series/observations",
                    {
                        "series_id": series,
                        "observation_start": "2020-01-01",
                        "observation_end": end_date,
                        "sort_order": "asc",
                    },
                ).get("observations")
                or []
            )
            return meta, [
                (x.get("date"), x.get("value"))
                for x in obs
                if x.get("value") not in (".", None, "")
            ]

        try:
            meta, obs = await asyncio.to_thread(fetch)
            if not obs:
                return key, _na(label, frequency)
            if date.fromisoformat(obs[-1][0]) < date.fromisoformat(end_date) - timedelta(days=180):
                return key, _na(label, frequency, f"STALE_SERIES_AS_OF_{obs[-1][0]}")
            return key, _value(
                float(obs[-1][1]),
                obs[-1][0],
                "FRED",
                meta[0].get("frequency", frequency) if meta else frequency,
            )
        except Exception as exc:
            return key, _na(label, frequency, type(exc).__name__)

    return dict(await asyncio.gather(*(one(k, v) for k, v in _FRED.items())))


def _value(value, timestamp, source, frequency, values=None):
    out = {
        "value": value,
        "timestamp": timestamp,
        "as_of": timestamp,
        "source": source,
        "status": "OK",
        "frequency": frequency,
    }
    if values:
        for n in (1, 5, 20):
            out[f"change_{n}d"] = (
                round(values[-1] / values[-(n + 1)] - 1, 6)
                if len(values) > n and values[-(n + 1)]
                else None
            )
    return out


def _na(source, frequency, detail="DATA_UNAVAILABLE"):
    return {
        "value": None,
        "timestamp": None,
        "as_of": None,
        "source": source,
        "status": "DATA_UNAVAILABLE",
        "frequency": frequency,
        "detail": detail,
    }


def _cross(data):
    rows = []
    for a, b, label in (
        ("sp500", "nikkei_225", "US overnight risk appetite"),
        ("nasdaq_100", "sox", "US technology / semiconductor read-through (not issuer-specific)"),
        ("usd_jpy", "nikkei_225", "USDJPY exporter backdrop"),
        ("vix", "sp500", "US volatility / risk appetite"),
    ):
        left, right = data[a], data[b]
        if (
            left["status"] == right["status"] == "OK"
            and left.get("change_1d") is not None
            and right.get("change_1d") is not None
        ):
            rows.append(
                {
                    "factor": label,
                    "inputs": [a, b],
                    "observation": f"1D: {a}={left['change_1d']:+.2%}, {b}={right['change_1d']:+.2%}",
                    "status": "OBSERVED",
                }
            )
        else:
            rows.append(
                {
                    "factor": label,
                    "inputs": [a, b],
                    "observation": None,
                    "status": "DATA_UNAVAILABLE",
                }
            )
    yield_data = data.get("us_10y_yield", {})
    rows.append(
        {
            "factor": "US 10Y valuation backdrop for growth equities",
            "inputs": ["us_10y_yield"],
            "observation": f"Latest: {yield_data['value']} ({yield_data['timestamp']})"
            if yield_data.get("status") == "OK"
            else None,
            "status": "OBSERVED" if yield_data.get("status") == "OK" else "DATA_UNAVAILABLE",
        }
    )
    return {
        "status": "OBSERVED",
        "conclusions": rows,
        "rule": "Observed co-movements are not causal claims; issuer sensitivity requires separate evidence.",
    }
