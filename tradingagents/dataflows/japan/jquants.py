"""J-Quants API V2 provider (official JPX data, API-key authenticated).

Only documented V2 endpoints and the official ``x-api-key`` authentication
header are used.  Each endpoint is normalized separately, so a plan restriction
or a date-range restriction is visible to Agents instead of being silently
replaced by another vendor.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .freshness import assess_japan_session_data
from .http import JsonResponse, get_json
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus


class JQuantsProvider:
    """Fetch V2 security master, daily bars and financial-summary records.

    ``/fins/details`` is deliberately not called on every research run.  It is
    a separately entitled endpoint for many accounts; the summary endpoint
    already provides the available disclosure/quarterly financial records.
    ``probe_capabilities`` can be used by diagnostics to test it explicitly.
    """

    name = "J-Quants"
    category = "market_data"
    cache_version = "v5-freshness-gated-security-bars-financial-summary"
    cache_requires_completed_window = True

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        config = get_config().get("markets", {}).get("jp", {})
        self.api_key = api_key if api_key is not None else os.getenv("JQUANTS_API_KEY", "")
        self.base_url = (base_url or config.get("jquants_api_base_url") or "https://api.jquants.com/v2").rstrip("/")
        self.timeout = float(config.get("request_timeout_seconds", 10))
        self.enabled = bool(config.get("datasources", {}).get("jquants", True))

    async def fetch(self, context: MarketContext, *, start_date: str, end_date: str) -> ProviderResponse:
        if not self.enabled:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DISABLED, detail="disabled by markets.jp.datasources"))
        if not self.api_key:
            return ProviderResponse(SourceStatus(self.name, DataStatus.AUTH_REQUIRED, detail="JQUANTS_API_KEY is not configured"))

        code = f"{context.native_symbol}0"
        headers = {"x-api-key": self.api_key}
        daily_url = f"{self.base_url}/equities/bars/daily"
        daily, master, financials = await asyncio.gather(
            get_json(
                daily_url,
                params={"code": code, "from": _compact_date(start_date), "to": _compact_date(end_date)},
                headers=headers,
                timeout=self.timeout,
            ),
            get_json(f"{self.base_url}/equities/master", params={"code": code}, headers=headers, timeout=self.timeout),
            get_json(f"{self.base_url}/fins/summary", params={"code": code}, headers=headers, timeout=self.timeout),
        )
        # A delayed subscription rejects a date range newer than its entitled
        # window with HTTP 400.  Query the same security without dates so the
        # provider can expose the latest source-visible bar and classify it as
        # STALE_SOURCE instead of confusing plan delay with an empty dataset.
        if daily.status == DataStatus.DATA_UNAVAILABLE and daily.detail == "HTTP 400":
            daily = await get_json(
                daily_url,
                params={"code": code},
                headers=headers,
                timeout=self.timeout,
            )
        cutoff = date.fromisoformat(end_date)
        daily_items = (
            tuple(_normalise_records(daily.payload, context))
            if daily.status == DataStatus.OK
            else ()
        )
        daily_items = tuple(item for item in daily_items if item.timestamp.date() <= cutoff)
        daily_freshness = assess_japan_session_data(
            max((item.timestamp.date() for item in daily_items), default=None), cutoff
        )
        if daily_items and daily_freshness.usable:
            latest_daily_date = max(item.timestamp.date() for item in daily_items)
            daily_items = tuple(
                replace(
                    item,
                    metadata={
                        **item.metadata,
                        "data_date": item.timestamp.date().isoformat(),
                        "published_at": None,
                        "freshness_status": (
                            daily_freshness.status
                            if item.timestamp.date() == latest_daily_date
                            else "HISTORICAL_OBSERVATION"
                        ),
                        "freshness_basis": (
                            daily_freshness.basis
                            if item.timestamp.date() == latest_daily_date
                            else f"historical bar preceding latest source date {latest_daily_date}"
                        ),
                        "native_cadence": daily_freshness.native_cadence,
                    },
                )
                for item in daily_items
            )
        else:
            daily_items = ()
        master_items = (
            tuple(_normalise_master_records(master.payload, context))
            if master.status == DataStatus.OK
            else ()
        )
        financial_items = (
            tuple(_normalise_financial_records(financials.payload, context))
            if financials.status == DataStatus.OK
            else ()
        )
        # Master and disclosure endpoints can return records newer than a
        # historical analysis date.  They are source-dated, so future rows are
        # excluded rather than relabelled with the requested date.
        master_items = tuple(item for item in master_items if item.timestamp.date() <= cutoff)
        if master_items:
            latest_master_date = max(item.timestamp for item in master_items)
            master_items = tuple(
                item for item in master_items if item.timestamp == latest_master_date
            )
        financial_items = tuple(
            item for item in financial_items if item.timestamp.date() <= cutoff
        )
        items = (*daily_items, *master_items, *financial_items)
        endpoint_statuses = {
            "daily_bars": _endpoint_detail(daily)
            if daily.status != DataStatus.OK
            else daily_freshness.status,
            "security_master": _endpoint_detail(master),
            "financial_summary": _endpoint_detail(financials),
        }
        successful = sum(response.status == DataStatus.OK for response in (daily, master, financials))
        if not successful:
            status = _worst_status(daily.status, master.status, financials.status)
        else:
            status = DataStatus.OK
        return ProviderResponse(
            SourceStatus(
                self.name,
                status,
                detail="; ".join(f"{name}={value}" for name, value in endpoint_statuses.items()),
                item_count=len(items),
            ),
            tuple(items),
            metadata={"daily_bars_freshness": daily_freshness.to_dict()},
        )

    async def probe_capabilities(self, context: MarketContext) -> dict[str, str]:
        """Explicitly test the optional detailed-financial endpoint for diagnostics.

        This is never part of the normal market-data bundle, avoiding a known
        entitlement failure and unnecessary rate-limit consumption on each run.
        """
        if not self.api_key:
            return {"financial_details": DataStatus.AUTH_REQUIRED.value}
        response = await get_json(
            f"{self.base_url}/fins/details",
            params={"code": f"{context.native_symbol}0"},
            headers={"x-api-key": self.api_key},
            timeout=self.timeout,
        )
        return {"financial_details": _endpoint_detail(response)}


def _compact_date(value: str) -> str:
    return str(value).replace("-", "")


def _endpoint_detail(response: JsonResponse) -> str:
    if response.status == DataStatus.OK:
        return "OK"
    return f"{response.status.value}:{response.detail or 'no detail'}"


def _worst_status(*statuses: DataStatus) -> DataStatus:
    for candidate in (DataStatus.AUTH_REQUIRED, DataStatus.RATE_LIMITED, DataStatus.PARSE_FAILED, DataStatus.DATA_UNAVAILABLE):
        if candidate in statuses:
            return candidate
    return DataStatus.DATA_UNAVAILABLE


def _records(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        return (item for item in payload if isinstance(item, dict))
    if isinstance(payload, Mapping):
        for key in ("data", "daily_quotes", "daily_bars", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return (item for item in value if isinstance(item, dict))
    return ()


def _timestamp(value: Any) -> datetime | None:
    raw = str(value or "")
    for candidate in (raw[:10], f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) >= 8 else ""):
        try:
            return datetime.fromisoformat(candidate).replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


def _normalise_records(payload: Any, context: MarketContext) -> Iterable[MarketInformation]:
    """Normalize official V2 daily bars without renaming its raw fields."""
    for record in _records(payload):
        timestamp = _timestamp(record.get("Date") or record.get("date"))
        if timestamp is None:
            continue
        # V2 returns compact field names (O/H/L/C/Vo/Va and Adj*) while a few
        # documented examples still show their expanded spellings.  Preserve
        # the official raw names and also expose stable normalized aliases.
        raw_values = {key: record[key] for key in ("Open", "High", "Low", "Close", "Volume", "TurnoverValue", "O", "H", "L", "C", "Vo", "Va", "AdjustmentFactor", "AdjustmentOpen", "AdjustmentHigh", "AdjustmentLow", "AdjustmentClose", "AdjustmentVolume", "AdjFactor", "AdjO", "AdjH", "AdjL", "AdjC", "AdjVo") if key in record}
        aliases = {
            "open": record.get("Open", record.get("O")),
            "high": record.get("High", record.get("H")),
            "low": record.get("Low", record.get("L")),
            "close": record.get("Close", record.get("C")),
            "volume": record.get("Volume", record.get("Vo")),
            "turnover_value": record.get("TurnoverValue", record.get("Va")),
            "adjustment_factor": record.get("AdjustmentFactor", record.get("AdjFactor")),
            "adjusted_open": record.get("AdjustmentOpen", record.get("AdjO")),
            "adjusted_high": record.get("AdjustmentHigh", record.get("AdjH")),
            "adjusted_low": record.get("AdjustmentLow", record.get("AdjL")),
            "adjusted_close": record.get("AdjustmentClose", record.get("AdjC")),
            "adjusted_volume": record.get("AdjustmentVolume", record.get("AdjVo")),
        }
        values = {key: value for key, value in aliases.items() if value is not None}
        yield MarketInformation(
            source="J-Quants", source_type="official_ohlcv", ticker=context.symbol,
            timestamp=timestamp,
            title="J-Quants V2 official daily OHLCV", content="Official JPX-derived daily market data.",
            confidence=0.98, verified=True, layer=InformationLayer.VERIFIED_FACT,
            content_level="structured_data",
            metadata={"ohlcv": values, "raw_ohlcv": raw_values, "raw_code": record.get("Code") or record.get("code"), "api_version": "v2"},
        )


def _normalise_master_records(payload: Any, context: MarketContext) -> Iterable[MarketInformation]:
    for record in _records(payload):
        timestamp = _timestamp(record.get("Date"))
        if timestamp is None:
            continue
        yield MarketInformation(
            source="J-Quants", source_type="official_security_master", ticker=context.symbol,
            timestamp=timestamp, title="J-Quants V2 listed security master",
            content="Official listed-security reference data.", confidence=0.98, verified=True,
            layer=InformationLayer.VERIFIED_FACT, content_level="structured_data",
            metadata={
                "security_master": dict(record),
                "raw_code": record.get("Code"),
                "api_version": "v2",
                "data_date": timestamp.date().isoformat(),
                "published_at": None,
                "freshness_status": "LATEST_AVAILABLE",
                "freshness_basis": "latest source master record not after analysis_as_of",
                "native_cadence": "SOURCE_UPDATE_DRIVEN",
            },
        )


def _normalise_financial_records(payload: Any, context: MarketContext) -> Iterable[MarketInformation]:
    for record in _records(payload):
        timestamp = _timestamp(record.get("DiscDate"))
        if timestamp is None:
            continue
        period_type = str(record.get("CurPerType") or "")
        yield MarketInformation(
            source="J-Quants", source_type="official_financial_summary", ticker=context.symbol,
            timestamp=timestamp,
            title=f"J-Quants V2 financial summary ({period_type or 'period unavailable'})",
            content="Official disclosed financial-summary record; periods must be compared like-for-like.",
            confidence=0.98, verified=True, layer=InformationLayer.VERIFIED_FACT,
            content_level="structured_data",
            metadata={
                "financial_summary": dict(record),
                "period_type": period_type,
                "raw_code": record.get("Code"),
                "api_version": "v2",
                "data_date": timestamp.date().isoformat(),
                "published_at": timestamp.date().isoformat(),
                "freshness_status": "FRESHNESS_UNVERIFIED",
                "freshness_basis": "raw summaries require fiscal-period selector before latest use",
                "native_cadence": "EVENT_DRIVEN",
            },
        )


def compare_daily_ohlcv(
    jquants_items: Iterable[MarketInformation],
    yfinance_rows: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Compare official daily bars against supplied yfinance rows transparently.

    yfinance may expose adjusted prices after a split while J-Quants exposes
    both raw and adjusted series.  The result explicitly labels that basis; a
    mismatch is returned as ``CONFLICT`` rather than overwritten.
    """
    results: list[dict[str, Any]] = []
    for item in jquants_items:
        if item.source_type != "official_ohlcv":
            continue
        date = item.timestamp.date().isoformat()
        vendor = yfinance_rows.get(date)
        if not vendor:
            results.append({"date": date, "status": "YFINANCE_MISSING", "jquants_source": "J-Quants V2"})
            continue
        raw = item.metadata.get("raw_ohlcv") or {}
        adjusted = item.metadata.get("ohlcv") or {}
        y_close = _number(vendor.get("Close", vendor.get("close")))
        raw_close = _number(raw.get("Close", raw.get("C")))
        adjusted_close = _number(adjusted.get("adjusted_close", adjusted.get("close")))
        if _same_number(y_close, raw_close):
            status, basis = "MATCH", "J-Quants 原始价（未复权）"
        elif _same_number(y_close, adjusted_close):
            status, basis = "MATCH", "J-Quants 调整后价格（复权口径）"
        else:
            status, basis = "CONFLICT", "无法与 J-Quants 原始价或调整后价格对应"
        results.append({
            "date": date,
            "status": status,
            "jquants_source": "J-Quants V2",
            "yfinance_source": "yfinance",
            "basis": basis,
            "jquants_raw_close": raw_close,
            "jquants_adjusted_close": adjusted_close,
            "yfinance_close": y_close,
        })
    return results


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _same_number(left: float | None, right: float | None) -> bool:
    return left is not None and right is not None and abs(left - right) <= max(1e-8, abs(right) * 1e-6)
