"""Deterministic market-data verification snapshot.

The market analyst is an LLM that can confabulate exact numbers — citing a
Bollinger band or a "historically validated bounce" that the underlying data
doesn't support (#830). This module computes a ground-truth snapshot (latest
OHLCV row on or before the analysis date, common indicators, recent closes)
the analyst is told to treat as the source of truth for any exact numeric
claim. Deterministic, no LLM involved.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

# A fixed, common indicator set so the snapshot is the same shape every run.
DEFAULT_SNAPSHOT_INDICATORS: tuple[str, ...] = (
    "close_10_ema", "close_50_sma", "close_200_sma",
    "rsi", "boll", "boll_ub", "boll_lb",
    "macd", "macds", "macdh", "atr",
    "vwma",
)


def _verified_rows(symbol: str, curr_date: str) -> pd.DataFrame:
    """OHLCV on or before curr_date, date-sorted. Raises if nothing usable.

    ``load_ohlcv`` already normalizes the Date column and filters out
    look-ahead rows, but we re-apply the cutoff defensively — this is a
    verification path, so it must not trust its input to be pre-filtered.
    """
    # Prefer the run cache.  This is the same OHLCV source used by the graph,
    # but avoids an incidental provider refresh while rendering/synthesizing a
    # report.  If no cache exists, the normal loader is imported lazily.
    cache_dir = Path.home() / ".tradingagents" / "cache"
    candidates = sorted(cache_dir.glob(f"{symbol.upper()}-YFin-data-*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        data = pd.read_csv(candidates[0], on_bad_lines="skip")
    else:
        from tradingagents.dataflows.stockstats_utils import load_ohlcv
        data = load_ohlcv(symbol, curr_date)
    if data is None or data.empty:
        raise ValueError(f"No OHLCV data available for {symbol}.")

    df = data.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    df = df[df["Date"] <= pd.to_datetime(curr_date)].sort_values("Date")
    if df.empty:
        raise ValueError(f"No OHLCV rows on or before {curr_date} for {symbol}.")
    stale_days = (pd.to_datetime(curr_date).normalize() - df["Date"].iloc[-1].normalize()).days
    if stale_days > 10:
        raise ValueError(
            f"Latest OHLCV row for {symbol} is {df['Date'].iloc[-1].date()}, "
            f"{stale_days} days before {curr_date}; refusing stale snapshot."
        )
    return df


def _fmt(value) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _latest_indicators(df: pd.DataFrame, names: Iterable[str]) -> dict[str, str]:
    """Calculate the fixed snapshot indicators directly from verified OHLCV.

    Keeping this independent from the optional stockstats package is important:
    a missing presentation dependency must not make the source-of-truth
    snapshot disappear and invite another provider's current quote into a
    report.  Formulas use standard 10/50/200 EMA/SMA, Wilder RSI/ATR, MACD
    (12, 26, 9), Bollinger (20, 2), and a 20-period VWMA.
    """
    close = pd.to_numeric(df["Close"], errors="coerce")
    high = pd.to_numeric(df["High"], errors="coerce")
    low = pd.to_numeric(df["Low"], errors="coerce")
    volume = pd.to_numeric(df["Volume"], errors="coerce")
    ema10 = close.ewm(span=10, adjust=False, min_periods=10).mean()
    sma50 = close.rolling(50, min_periods=50).mean()
    sma200 = close.rolling(200, min_periods=200).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, float("nan")))
    middle = close.rolling(20, min_periods=20).mean()
    std = close.rolling(20, min_periods=20).std(ddof=0)
    macd = close.ewm(span=12, adjust=False, min_periods=26).mean() - close.ewm(span=26, adjust=False, min_periods=26).mean()
    macds = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    prev_close = close.shift(1)
    true_range = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    vwma = (close * volume).rolling(20, min_periods=20).sum() / volume.rolling(20, min_periods=20).sum()
    computed = {
        "close_10_ema": ema10, "close_50_sma": sma50, "close_200_sma": sma200,
        "rsi": rsi, "boll": middle, "boll_ub": middle + 2 * std, "boll_lb": middle - 2 * std,
        "macd": macd, "macds": macds, "macdh": macd - macds, "atr": atr, "vwma": vwma,
    }
    return {name: _fmt(computed[name].iloc[-1]) if name in computed else "N/A (unsupported)" for name in names}


def build_verified_market_snapshot(
    symbol: str,
    curr_date: str,
    look_back_days: int = 30,
    indicators: Iterable[str] | None = None,
) -> str:
    """Render a ground-truth snapshot: latest OHLCV row, indicators, recent closes."""
    df = _verified_rows(symbol, curr_date)
    selected = tuple(indicators or DEFAULT_SNAPSHOT_INDICATORS)
    indicator_values = _latest_indicators(df, selected)

    latest = df.iloc[-1]
    latest_date = _fmt(latest["Date"])
    window = max(1, min(int(look_back_days), 30))
    recent = df.tail(window)

    lines = [
        f"## Verified market data snapshot for {symbol.upper()}",
        "",
        f"- Requested analysis date: {curr_date}",
        f"- Latest trading row used: {latest_date}",
        "- Rows after the requested analysis date are excluded before verification.",
        "",
        "### Latest verified OHLCV row",
        "",
        "| Field | Value |",
        "|---|---:|",
    ]
    for field in ("Open", "High", "Low", "Close", "Volume"):
        lines.append(f"| {field} | {_fmt(latest.get(field))} |")

    lines += ["", "### Verified technical indicators (latest row)", "",
              "| Indicator | Value |", "|---|---:|"]
    for name, value in indicator_values.items():
        lines.append(f"| {name} | {value} |")

    lines += ["", f"### Recent verified closes (last {len(recent)} rows)", "",
              "| Date | Close |", "|---|---:|"]
    for _, row in recent.iterrows():
        lines.append(f"| {_fmt(row['Date'])} | {_fmt(row.get('Close'))} |")

    lines += [
        "",
        "Use this snapshot as the source of truth for exact OHLCV, price-level, "
        "and indicator-value claims. If another tool output conflicts with it, "
        "flag the discrepancy rather than inventing a reconciled number. Do not "
        "claim historical validation, support/resistance bounces, or exact "
        "percentage moves unless directly supported by tool output with concrete "
        "dates and prices.",
    ]
    return "\n".join(lines)
