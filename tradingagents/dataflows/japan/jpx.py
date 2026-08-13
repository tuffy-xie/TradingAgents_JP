"""Official JPX public short-selling-position provider."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from io import BytesIO
from typing import Any
from urllib.parse import urljoin

import pandas as pd

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .http import get_bytes, get_text
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus

_SHORT_POSITIONS_URL = "https://www.jpx.co.jp/markets/public/short-selling/index.html"
_MARGIN_BALANCES_URL = "https://www.jpx.co.jp/markets/statistics-equities/margin/index.html"
_XLS_LINK = re.compile(r"href=[\"'](?P<href>[^\"']*Short_Positions\.xls)[\"']", re.IGNORECASE)
_MARGIN_XLS_LINK = re.compile(r"href=[\"'](?P<href>[^\"']*mtdailyk\d+\.xls)[\"']", re.IGNORECASE)


class JPXProvider:
    """Read JPX's publicly posted outstanding short-position workbook.

    The workbook contains reportable positions (at least 0.5%); it is not total
    intraday short interest.  That distinction is retained in metadata so
    downstream agents cannot mistake it for a complete short-interest measure.
    """

    name = "JPX"
    category = "supply_demand"
    cache_version = "public-workbooks-v1"

    def __init__(self, feed_url: str | None = None):
        config = get_config().get("markets", {}).get("jp", {})
        self.feed_url = feed_url or config.get("jpx_feed_url")
        self.timeout = float(config.get("request_timeout_seconds", 10))
        self.enabled = bool(config.get("datasources", {}).get("jpx", True))

    async def fetch(self, context: MarketContext, *, start_date: str, end_date: str) -> ProviderResponse:
        del start_date, end_date
        if not self.enabled:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DISABLED, detail="disabled by markets.jp.datasources"))
        if self.feed_url:
            return ProviderResponse(SourceStatus(
                self.name, DataStatus.DATA_UNAVAILABLE,
                detail="custom JPX feed parsing is not implemented; public short-position file is used by default",
            ))
        short_status, short_html, short_detail = await get_text(_SHORT_POSITIONS_URL, timeout=self.timeout)
        margin_status, margin_html, margin_detail = await get_text(_MARGIN_BALANCES_URL, timeout=self.timeout)
        items: list[MarketInformation] = []
        details: list[str] = []
        if short_status == DataStatus.OK:
            workbook_url = _latest_short_position_url(short_html)
            if workbook_url:
                download = await get_bytes(workbook_url, timeout=self.timeout)
                if download.status == DataStatus.OK:
                    try:
                        items.extend(normalise_short_positions(download.payload, context, workbook_url))
                    except (ImportError, OSError, ValueError, pd.errors.ParserError) as exc:
                        details.append(f"short_positions={type(exc).__name__}")
                else:
                    details.append(f"short_positions={download.status}")
            else:
                details.append("short_positions=workbook_link_not_found")
        else:
            details.append(f"short_positions={short_status}:{short_detail}")
        if margin_status == DataStatus.OK:
            workbook_url = _latest_margin_balance_url(margin_html)
            if workbook_url:
                download = await get_bytes(workbook_url, timeout=self.timeout)
                if download.status == DataStatus.OK:
                    try:
                        items.extend(normalise_margin_balances(download.payload, context, workbook_url))
                    except (ImportError, OSError, ValueError, pd.errors.ParserError) as exc:
                        details.append(f"margin_balances={type(exc).__name__}")
                else:
                    details.append(f"margin_balances={download.status}")
            else:
                details.append("margin_balances=workbook_link_not_found")
        else:
            details.append(f"margin_balances={margin_status}:{margin_detail}")
        status = DataStatus.OK if not details else (DataStatus.OK if items else DataStatus.DATA_UNAVAILABLE)
        return ProviderResponse(SourceStatus(self.name, status, detail="; ".join(details), item_count=len(items)), tuple(items))


def _latest_short_position_url(index_html: str) -> str | None:
    match = _XLS_LINK.search(index_html)
    return urljoin(_SHORT_POSITIONS_URL, unescape(match.group("href"))) if match else None


def _latest_margin_balance_url(index_html: str) -> str | None:
    match = _MARGIN_XLS_LINK.search(index_html)
    return urljoin(_MARGIN_BALANCES_URL, unescape(match.group("href"))) if match else None


def normalise_short_positions(
    workbook: bytes, context: MarketContext, source_url: str
) -> list[MarketInformation]:
    """Normalize matching rows from JPX's latest public .xls workbook."""
    # The first six rows are title/publication metadata; row 7 is the Japanese
    # schema and the following row repeats it in English.
    frame = pd.read_excel(BytesIO(workbook), header=6, dtype=str, engine="xlrd")
    columns = {_normalise_column(column): column for column in frame.columns}
    code_column = _find_column(columns, "銘柄コード", "code")
    if not code_column:
        raise ValueError("JPX workbook has no security-code column")
    items: list[MarketInformation] = []
    for _, row in frame.iterrows():
        if _normalise_code(row.get(code_column)) != context.native_symbol:
            continue
        data = {_normalise_column(column): _clean_value(row.get(column)) for column in frame.columns}
        institution = str(data.get("報告者名") or data.get("short seller") or data.get("商号又は名称") or "Unknown reporter")
        position = _as_number(data.get("空売り残高割合") or data.get("short position ratio"))
        report_date = _as_datetime(data.get("計算日") or data.get("calculation date"))
        title = f"Reported short position: {institution}"
        items.append(MarketInformation(
            source="JPX",
            source_type="reported_short_position",
            ticker=context.symbol,
            timestamp=report_date,
            title=title,
            content="JPX public outstanding short-selling position report (threshold: 0.5% or more).",
            url=source_url,
            confidence=0.98,
            verified=True,
            layer=InformationLayer.VERIFIED_FACT,
            content_level="full_text",
            metadata={
                "institution": institution,
                "position_ratio": position,
                "security_name": data.get("銘柄名") or data.get("issue name"),
                "report_date": report_date.date().isoformat(),
                "coverage": "reportable positions only (>=0.5%)",
            },
        ))
    return items


def normalise_margin_balances(
    workbook: bytes, context: MarketContext, source_url: str
) -> list[MarketInformation]:
    """Normalize the public JPX per-issue outstanding-margin workbook.

    JPX publishes a multi-row header; the data layout is intentionally accessed
    by documented column position and validated by the code column instead of
    pretending the workbook is a normal one-row CSV-style schema.
    """
    frame = pd.read_excel(BytesIO(workbook), header=None, dtype=str, engine="xlrd")
    items: list[MarketInformation] = []
    for _, row in frame.iloc[7:].iterrows():
        if _normalise_code(row.iloc[6] if len(row) > 6 else None) != context.native_symbol:
            continue
        report_date = _as_datetime(frame.iloc[2, 1] if frame.shape[0] > 2 else None)
        items.append(MarketInformation(
            source="JPX",
            source_type="margin_trading_balance",
            ticker=context.symbol,
            timestamp=report_date,
            title="JPX outstanding margin-trading balance by issue",
            content="Official JPX daily outstanding margin-trading balance by issue.",
            url=source_url,
            confidence=0.98,
            verified=True,
            layer=InformationLayer.VERIFIED_FACT,
            metadata={
                "company": _clean_value(row.iloc[3]),
                "market_section": _clean_value(row.iloc[4]),
                "margin_type": _clean_value(row.iloc[5]),
                "short_balance": _as_number(row.iloc[8]),
                "short_balance_change": _as_number(row.iloc[9]),
                "long_balance": _as_number(row.iloc[11]),
                "long_balance_change": _as_number(row.iloc[12]),
                "margin_ratio": _as_number(row.iloc[14]),
                "coverage": "JPX reported outstanding margin balance by issue",
            },
        ))
    return items


def _normalise_column(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).replace("\n", " ")).strip().lower()


def _find_column(columns: dict[str, Any], *names: str) -> Any | None:
    return next((columns[name.lower()] for name in names if name.lower() in columns), None)


def _normalise_code(value: Any) -> str:
    code = re.sub(r"\D", "", str(value))
    return code.zfill(4) if code else ""


def _clean_value(value: Any) -> Any:
    return None if pd.isna(value) else value


def _as_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace("%", "").replace(",", ""))
    except ValueError:
        return None


def _as_datetime(value: Any) -> datetime:
    if value is None:
        return datetime.now(UTC)
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return datetime.now(UTC)
    return parsed.to_pydatetime().replace(tzinfo=UTC)
