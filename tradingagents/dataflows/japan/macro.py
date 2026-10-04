"""Japan macro and US-to-Japan cross-market context (JP route only)."""

from __future__ import annotations

import asyncio
import csv
import re
from datetime import UTC, date, datetime, timedelta
from io import StringIO

import requests
import yfinance as yf

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext
from tradingagents.dataflows.vendors import fred

from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus
from .tdnet import extract_pdf_text
from .trading_calendar import latest_japan_trading_day

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
    name, category, cache_version = "Japan Macro", "macro", "jp-us-cross-market-v2"
    cache_requires_completed_window = True

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
        markets, series, official = await asyncio.gather(
            _market_snapshot(end_date),
            _fred_snapshot(end_date),
            _official_japan_snapshot(end_date),
        )
        macro = {
            **markets,
            **series,
            **official,
            "fed_recent_meeting": _na("Fed recent meeting", "event-driven"),
            "sector_index": _na("issuer sector index", "daily"),
        }
        macro["cross_market"] = _cross(macro)
        item = MarketInformation(
            source=self.name,
            source_type="jp_cross_market_macro_context",
            ticker=context.symbol,
            timestamp=datetime.combine(date.fromisoformat(end_date), datetime.min.time(), tzinfo=UTC),
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


async def _market_snapshot(end_date: str):
    analysis_as_of = date.fromisoformat(end_date)
    start_date = (analysis_as_of - timedelta(days=70)).isoformat()
    end_exclusive = (analysis_as_of + timedelta(days=1)).isoformat()

    async def one(key, spec):
        ticker, label = spec
        try:
            frame = await asyncio.to_thread(
                lambda: yf.Ticker(ticker).history(
                    start=start_date, end=end_exclusive, auto_adjust=False
                )
            )
            closes = frame["Close"].dropna() if not frame.empty else []
            if len(closes) < 2:
                return key, _na(label, "daily")
            latest_date = closes.index[-1].date()
            expected = (
                latest_japan_trading_day(analysis_as_of)
                if ticker in {"^N225", "^TOPX"}
                else _latest_completed_non_japan_market_date(analysis_as_of)
            )
            if latest_date != expected:
                return key, _na(
                    label,
                    "daily",
                    f"FRESHNESS_UNVERIFIED:latest={latest_date};expected={expected}",
                )
            values = [float(x) for x in closes]
            return key, _value(
                values[-1],
                latest_date.isoformat(),
                "Yahoo Finance",
                "daily",
                values,
                freshness_status="LATEST_AVAILABLE",
                freshness_basis="latest completed source-native market date through analysis_as_of",
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
                        "realtime_end": end_date,
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
                freshness_status="LATEST_AVAILABLE",
                freshness_basis="FRED realtime vintage and observation date bounded by analysis_as_of",
            )
        except Exception as exc:
            return key, _na(label, frequency, type(exc).__name__)

    return dict(await asyncio.gather(*(one(k, v) for k, v in _FRED.items())))


async def _official_japan_snapshot(end_date: str):
    return await asyncio.to_thread(_official_japan_snapshot_sync, end_date)


def _official_japan_snapshot_sync(end_date: str):
    analysis_as_of = date.fromisoformat(end_date)
    result = {
        "japan_cpi": _na("Statistics Bureau of Japan", "monthly"),
        "boj_policy": _na("Bank of Japan", "event-driven"),
    }
    try:
        year = analysis_as_of.year
        url = f"https://www.stat.go.jp/data/cpi/{year}/youshiki/csv/zmi{year}s.csv"
        rows = list(csv.reader(StringIO(requests.get(url, timeout=15).content.decode("cp932"))))
        values = [
            (row[0], row)
            for row in rows
            if row
            and re.fullmatch(r"20\d{4}", row[0])
            and len(row) > 5
            and row[0] <= analysis_as_of.strftime("%Y%m")
        ]
        latest = next(((month, row) for month, row in reversed(values) if row[1]), None)
        if latest:
            month, row = latest
            previous = next(
                (
                    candidate
                    for candidate in reversed(values)
                    if candidate[0] == f"{int(month[:4]) - 1}{month[4:]}"
                ),
                None,
            )

            def point(index):
                value = float(row[index]) if row[index] else None
                prior = float(previous[1][index]) if previous and previous[1][index] else None
                return {
                    "value": value,
                    "reference_month": f"{month[:4]}-{month[4:]}",
                    "release_date": None,
                    "timestamp": f"{month[:4]}-{month[4:]}-01",
                    "as_of": f"{month[:4]}-{month[4:]}-01",
                    "source": "Statistics Bureau of Japan CPI CSV",
                    "status": "OK",
                    "frequency": "monthly",
                    "yoy": round(value / prior - 1, 6) if value and prior else None,
                    "mom": None,
                    "release_date_status": "DATA_UNAVAILABLE_IN_SOURCE_CSV",
                    "freshness_status": "LATEST_AVAILABLE",
                    "freshness_basis": "latest reference month present in official CPI CSV through analysis_as_of",
                    "native_cadence": "MONTHLY",
                }

            result["japan_cpi"] = {
                "all_items": point(1),
                "core": point(2),
                "core_core": point(5),
                "source": url,
                "status": "OK",
                "frequency": "monthly",
            }
    except Exception as exc:
        result["japan_cpi"] = _na("Statistics Bureau of Japan", "monthly", type(exc).__name__)
    try:
        news = requests.get("https://www.boj.or.jp/en/whatsnew/", timeout=15).text
        link = re.search(
            r'href="(?P<href>[^"]+mpr_\d{4}/k(?P<date>\d{6})a\.(?:htm|pdf))"[^>]*>[^<]*Statement on Monetary Policy',
            news,
            re.I,
        )
        if link:
            href = (
                link["href"]
                if link["href"].startswith("http")
                else "https://www.boj.or.jp" + link["href"]
            )
            document = requests.get(href, timeout=15)
            text = (
                extract_pdf_text(document.content, 20_000)
                if href.endswith(".pdf")
                else document.text
            )
            rate = re.search(
                r"(?:uncollateralized overnight call rate|policy interest rate).*?(\d+(?:\.\d+)?)\s*percent",
                text,
                re.I | re.S,
            )
            raw = link["date"]
            release = f"20{raw[:2]}-{raw[2:4]}-{raw[4:]}"
            if date.fromisoformat(release) <= analysis_as_of:
                result["boj_policy"] = {
                    "meeting_date": release,
                    "release_date": release,
                    "policy_rate": float(rate.group(1)) if rate else None,
                    "policy_guideline": "Statement on Monetary Policy (official text link)",
                    "policy_change": "UNDETERMINED",
                    "statement_title": "Statement on Monetary Policy",
                    "source": href,
                    "status": "OK",
                    "frequency": "event-driven",
                    "as_of": release,
                    "freshness_status": "LATEST_AVAILABLE",
                    "freshness_basis": "latest dated official BOJ policy statement not after analysis_as_of",
                    "native_cadence": "EVENT_DRIVEN",
                }
    except Exception as exc:
        result["boj_policy"] = _na("Bank of Japan", "event-driven", type(exc).__name__)
    return result


def _latest_completed_non_japan_market_date(analysis_as_of: date) -> date:
    """Date-level JP-night contract for overseas/FX completed daily bars.

    A Japan-night run occurs before the same-date US cash close.  Non-Japan
    daily macro markets therefore use the preceding weekday.  A source holiday
    mismatch fails closed in ``_market_snapshot`` rather than being guessed.
    """
    cursor = analysis_as_of - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def _value(
    value,
    timestamp,
    source,
    frequency,
    values=None,
    *,
    freshness_status="LATEST_AVAILABLE",
    freshness_basis="latest source response bounded by analysis_as_of",
):
    out = {
        "value": value,
        "timestamp": timestamp,
        "as_of": timestamp,
        "source": source,
        "status": "OK",
        "frequency": frequency,
        "data_date": timestamp,
        "published_at": None,
        "freshness_status": freshness_status,
        "freshness_basis": freshness_basis,
        "native_cadence": frequency,
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
        "data_date": None,
        "published_at": None,
        "freshness_status": "DATA_UNAVAILABLE",
        "freshness_basis": detail,
        "native_cadence": frequency,
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
