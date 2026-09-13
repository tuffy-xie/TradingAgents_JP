"""Japan Securities Finance (JSF/Nihon Securities Finance) history provider."""

from __future__ import annotations

import asyncio
import csv
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from typing import Any

import requests

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .freshness import assess_japan_session_data
from .http import USER_AGENT, get_bytes
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus

_BALANCES_URL = "https://www.taisyaku.jp/data/zandaka.csv"
_PREMIUM_CHARGES_URL = "https://www.taisyaku.jp/data/shina.csv"
_DETAIL_URL = "https://www.taisyaku.jp/app/stock/detail/{code}-01"
_SEARCH_URL = "https://www.taisyaku.jp/app/stock/detail/{code}/search"
_HISTORY_CSV_URL = "https://www.taisyaku.jp/app/stock/detail/{code}/csv"
_INPUT = re.compile(r'<input[^>]+name="(?P<name>[^"]+)"[^>]*value="(?P<value>[^"]*)"', re.IGNORECASE)


class JSFProvider:
    """Read public JSF daily history without treating one observation as a trend."""

    name = "JSF"
    category = "supply_demand"
    cache_version = "public-history-trend-freshness-v6-publication-status"
    cache_requires_completed_window = True

    def __init__(self):
        config = get_config().get("markets", {}).get("jp", {})
        self.timeout = float(config.get("request_timeout_seconds", 10))
        self.enabled = bool(config.get("datasources", {}).get("jsf", True))
        self.history_calendar_days = int(config.get("jsf_history_calendar_days", 45))

    async def fetch(self, context: MarketContext, *, start_date: str, end_date: str) -> ProviderResponse:
        if not self.enabled:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DISABLED, detail="disabled by markets.jp.datasources"))
        history_start = _history_start(start_date, end_date, self.history_calendar_days)
        history, charges = await asyncio.gather(
            _fetch_history_csv(context.native_symbol, history_start, end_date, self.timeout),
            get_bytes(_PREMIUM_CHARGES_URL, timeout=self.timeout),
        )
        details: list[str] = []
        items: list[MarketInformation] = []
        metadata: dict[str, Any] = {}
        analysis_as_of = date.fromisoformat(end_date)
        if history.status == DataStatus.OK:
            balance_items = normalise_balances(
                history.payload,
                context,
                historical=True,
                url=_HISTORY_CSV_URL.format(code=context.native_symbol),
            )
            balance_items = [
                item for item in balance_items if item.timestamp.date() <= analysis_as_of
            ]
            freshness = _balance_freshness(balance_items, analysis_as_of)
            metadata["balances_freshness"] = freshness.to_dict()
            if balance_items and freshness.usable:
                latest_balance_date = max(item.timestamp.date() for item in balance_items)
                balance_items = [
                    _with_freshness(
                        item,
                        freshness,
                        is_latest=item.timestamp.date() == latest_balance_date,
                    )
                    for item in balance_items
                ]
                items.extend(balance_items)
                items.append(
                    _with_freshness(
                        build_supply_demand_trend(
                            balance_items, context, history_start, end_date
                        ),
                        freshness,
                    )
                )
            else:
                details.append(
                    f"history={freshness.status if balance_items else 'NO_MATCHING_SECURITY'}"
                )
        else:
            # Current daily CSV remains a safe, official fallback; it is marked
            # as a single-day observation and never produces a trend.
            fallback = await get_bytes(_BALANCES_URL, timeout=self.timeout)
            if fallback.status == DataStatus.OK:
                balance_items = [
                    item
                    for item in normalise_balances(
                        fallback.payload, context, historical=False, url=_BALANCES_URL
                    )
                    if item.timestamp.date() <= analysis_as_of
                ]
                freshness = _balance_freshness(balance_items, analysis_as_of)
                metadata["balances_freshness"] = freshness.to_dict()
                if freshness.usable:
                    items.extend(_with_freshness(item, freshness) for item in balance_items)
                    details.append(f"history={history.status}; using current daily fallback")
                else:
                    details.append(
                        f"history={history.status}; current_balances={freshness.status}"
                    )
            else:
                details.append(f"history={history.status}; current_balances={fallback.status}")
        if charges.status == DataStatus.OK:
            items.extend(
                item
                for item in normalise_premium_charges(charges.payload, context)
                if item.timestamp.date() <= analysis_as_of
            )
        else:
            details.append(f"premium_charges={charges.status}")
        status = DataStatus.OK if items else DataStatus.DATA_UNAVAILABLE
        return ProviderResponse(
            SourceStatus(
                self.name,
                status,
                detail=", ".join(details),
                item_count=len(items),
            ),
            tuple(items),
            metadata=metadata,
        )


def _balance_freshness(items: list[MarketInformation], analysis_as_of: date):
    return assess_japan_session_data(
        max((item.timestamp.date() for item in items), default=None),
        analysis_as_of,
        # Publication lifecycle is carried separately by publication_status.
        # A fresh preliminary or unknown observation must not inherit a
        # misleading "CONFIRMED" cadence label.
        native_cadence="JSF_SECURITIES_FINANCE_BALANCE_BUSINESS_DAY",
    )


def _with_freshness(item: MarketInformation, freshness, *, is_latest: bool = True):
    return replace(
        item,
        metadata={
            **item.metadata,
            "data_date": item.timestamp.date().isoformat(),
            "published_at": None,
            "freshness_status": (
                freshness.status if is_latest else "HISTORICAL_OBSERVATION"
            ),
            "freshness_basis": (
                freshness.basis
                if is_latest
                else f"historical observation preceding latest source date {freshness.data_date}"
            ),
            "native_cadence": freshness.native_cadence,
        },
    )


async def _fetch_history_csv(code: str, start_date: str, end_date: str, timeout: float):
    """Use JSF's public form and its documented per-security CSV download."""
    def request_history():
        session = requests.Session()
        headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
        try:
            detail = session.get(_DETAIL_URL.format(code=code), headers=headers, timeout=timeout)
            if detail.status_code in {401, 403}:
                return _bytes_response(DataStatus.AUTH_REQUIRED, detail=f"HTTP {detail.status_code}")
            if detail.status_code == 429:
                return _bytes_response(DataStatus.RATE_LIMITED, detail="HTTP 429")
            if not detail.ok:
                return _bytes_response(DataStatus.DATA_UNAVAILABLE, detail=f"HTTP {detail.status_code}")
            form_start = detail.text.find(f'<form action="{_SEARCH_URL.format(code=code)}"')
            if form_start < 0:
                return _bytes_response(DataStatus.PARSE_FAILED, detail="official history form not found")
            fields = {match["name"]: match["value"] for match in _INPUT.finditer(detail.text[form_start:])}
            if not fields.get("csrf_test_name") or not fields.get("orgMgrCd"):
                return _bytes_response(DataStatus.PARSE_FAILED, detail="official history form fields missing")
            fields.update({
                "mkYmdFrom": _jsf_date(start_date), "mkYmdTo": _jsf_date(end_date),
                # This provider currently targets the TSE ``-01`` detail
                # endpoint. The HTML includes later, unrelated market inputs;
                # retain the endpoint's official Tokyo market code explicitly.
                "trjoKbn": "01", "kjnYmdDays": "",
            })
            searched = session.post(_SEARCH_URL.format(code=code), data=fields, headers=headers, timeout=timeout)
            if not searched.ok:
                return _bytes_response(DataStatus.DATA_UNAVAILABLE, detail=f"search HTTP {searched.status_code}")
            downloaded = session.get(_HISTORY_CSV_URL.format(code=code), headers={**headers, "Accept": "text/csv,*/*"}, timeout=timeout)
            if not downloaded.ok:
                return _bytes_response(DataStatus.DATA_UNAVAILABLE, detail=f"CSV HTTP {downloaded.status_code}")
            if not downloaded.content.startswith((b'"\x96', b'\x96', b'"\x8c')) and "融資新規" not in downloaded.content.decode("cp932", errors="ignore"):
                return _bytes_response(DataStatus.PARSE_FAILED, detail="history CSV schema not found")
            return _bytes_response(DataStatus.OK, downloaded.content)
        except requests.RequestException as exc:
            return _bytes_response(DataStatus.DATA_UNAVAILABLE, detail=type(exc).__name__)

    return await asyncio.to_thread(request_history)


def _bytes_response(status: DataStatus, payload: bytes = b"", detail: str = ""):
    from .http import BytesResponse
    return BytesResponse(status, payload, detail)


def normalise_balances(
    payload: bytes, context: MarketContext, *, historical: bool = False, url: str = _BALANCES_URL,
) -> list[MarketInformation]:
    rows = _csv_rows(payload)
    items: list[MarketInformation] = []
    for row in rows:
        code = row.get("銘柄コード") or row.get("コード")
        if code != context.native_symbol:
            continue
        application = _date(row.get("申込日"), compact=historical)
        unit = _unit_from_schema(row)
        report_type = row.get("速報／確報") or "UNSPECIFIED"
        fields = {
            "company": row.get("銘柄名"),
            "finance_new": _number(row.get("融資新規（株）") or row.get("融資新規株数")),
            "finance_repaid": _number(row.get("融資返済（株）") or row.get("融資返済株数")),
            "finance_balance": _number(row.get("融資残高（株）") or row.get("融資残高株数")),
            "stock_loan_new": _number(row.get("貸株新規（株）") or row.get("貸株新規株数")),
            "stock_loan_repaid": _number(row.get("貸株返済（株）") or row.get("貸株返済株数")),
            "stock_loan_balance": _number(row.get("貸株残高（株）") or row.get("貸株残高株数")),
            "net_balance": _number(row.get("差引残高（株）") or row.get("差引残高株数")),
            "application_date": application.date().isoformat() if application else None,
            # The official CSV does not publish a settlement-date column. Do
            # not infer T+2 because holidays and settlement rules can differ.
            "settlement_date": None,
            # Keep the source label for backwards compatibility, while also
            # exposing a canonical publication lifecycle to consumers.
            "report_type": report_type,
            "publication_status": _publication_status(report_type),
            "unit": unit,
            "schema_fields": [key for key in row if "融資" in key or "貸株" in key or "差引" in key],
            "history_observation": historical,
        }
        if application is None:
            continue
        # JSF pages can expose a dated but still-empty column before any
        # balance values are published.  A calendar label is not an
        # observation and must not displace the latest valid source record.
        if not _has_balance_observation(fields):
            continue
        items.append(MarketInformation(
            source="JSF", source_type="securities_finance_balance", ticker=context.symbol,
            timestamp=application, title="JSF financing and stock-loan balance",
            content="Official JSF securities-finance balance. This is not all brokerage margin positions.",
            url=url, confidence=0.95, verified=True, layer=InformationLayer.VERIFIED_FACT, metadata=fields,
        ))
    # A preliminary and confirmed publication may coexist for one data date.
    # Prefer confirmed for that date, but retain all observations when no
    # publication state gives us a safe tie-breaker.
    preferred: dict[date, list[MarketInformation]] = {}
    for item in items:
        key = item.timestamp.date()
        current = preferred.get(key, [])
        if not current:
            preferred[key] = [item]
            continue
        current_rank = max(_publication_rank(existing.metadata.get("publication_status")) for existing in current)
        item_rank = _publication_rank(item.metadata.get("publication_status"))
        if item_rank > current_rank:
            preferred[key] = [item]
        elif item_rank == current_rank:
            preferred[key].append(item)
    return sorted(
        (item for same_date in preferred.values() for item in same_date),
        key=lambda item: item.timestamp,
    )


def _publication_status(value: Any) -> str:
    """Normalize JSF's source publication label without guessing unknown values."""
    normalized = str(value or "").strip().upper()
    if "速報" in str(value) or "PRELIMINARY" in normalized:
        return "PRELIMINARY"
    if "確報" in str(value) or "確定" in str(value) or "CONFIRMED" in normalized:
        return "CONFIRMED"
    return "UNKNOWN"


def _publication_rank(value: Any) -> int:
    return {"UNKNOWN": 0, "PRELIMINARY": 1, "CONFIRMED": 2}.get(str(value), 0)


def _has_balance_observation(fields: Mapping[str, Any]) -> bool:
    return any(
        isinstance(fields.get(key), (int, float))
        for key in ("finance_balance", "stock_loan_balance", "net_balance")
    )


def build_supply_demand_trend(
    balance_items: list[MarketInformation], context: MarketContext, start_date: str, end_date: str,
) -> MarketInformation:
    observations = _valid_observations(balance_items)
    metrics = _trend_metrics(observations)
    latest = observations[-1] if observations else None
    return MarketInformation(
        source="JSF", source_type="securities_finance_trend", ticker=context.symbol,
        timestamp=(
            latest.timestamp
            if latest
            else datetime.fromisoformat(end_date).replace(tzinfo=UTC)
        ),
        title="JSF financing and stock-loan history trend",
        content="Trend metrics require comparable official daily observations; a single day is not classified as a trend.",
        url=_HISTORY_CSV_URL.format(code=context.native_symbol), confidence=0.92, verified=True,
        layer=InformationLayer.VERIFIED_FACT,
        metadata={
            "history_start_date": start_date, "history_end_date": end_date,
            "observation_count": len(observations), "unit": "株",
            "application_date": latest.metadata.get("application_date") if latest else None,
            "settlement_date": None, "report_type": latest.metadata.get("report_type") if latest else "UNSPECIFIED",
            **metrics,
        },
    )


def _valid_observations(items: list[MarketInformation]) -> list[MarketInformation]:
    seen: set[str] = set()
    result: list[MarketInformation] = []
    for item in sorted(items, key=lambda value: value.timestamp):
        key = item.metadata.get("application_date")
        if (
            not key
            or key in seen
            or item.metadata.get("unit") != "株"
            or not _has_balance_observation(item.metadata)
        ):
            continue
        seen.add(key)
        result.append(item)
    return result


def _trend_metrics(observations: list[MarketInformation]) -> dict[str, Any]:
    finance = [item.metadata.get("finance_balance") for item in observations]
    loan = [item.metadata.get("stock_loan_balance") for item in observations]
    finance_5, finance_5_flag = _window_change(finance, 5)
    finance_20, finance_20_flag = _window_change(finance, 20)
    loan_5, loan_5_flag = _window_change(loan, 5)
    loan_20, loan_20_flag = _window_change(loan, 20)
    finance_up, finance_down = _consecutive_days(finance)
    loan_up, loan_down = _consecutive_days(loan)
    latest = observations[-1].metadata if observations else {}
    ratio = _ratio(latest.get("finance_new"), latest.get("finance_repaid"))
    finance_flags = [flag for flag in (finance_5_flag, finance_20_flag) if flag]
    loan_flags = [flag for flag in (loan_5_flag, loan_20_flag) if flag]
    flags = [*finance_flags, *loan_flags]
    confirmed_finance = _direction(finance_5, finance_up, finance_down, finance_flags)
    confirmed_loan = _direction(loan_5, loan_up, loan_down, loan_flags)
    return {
        "finance_balance_change_5d": finance_5, "finance_balance_change_20d": finance_20,
        "stock_loan_balance_change_5d": loan_5, "stock_loan_balance_change_20d": loan_20,
        "consecutive_finance_balance_increase_days": finance_up,
        "consecutive_finance_balance_decrease_days": finance_down,
        "consecutive_stock_loan_balance_increase_days": loan_up,
        "consecutive_stock_loan_balance_decrease_days": loan_down,
        "finance_new_repaid_ratio": ratio,
        "continuous_deleveraging": bool(finance_down >= 3 and confirmed_finance == "DECREASING"),
        "rapid_finance_buildup": bool(finance_5 is not None and finance_5 >= 0.20 and finance_up >= 3 and not finance_flags),
        "rapid_stock_loan_increase": bool(loan_5 is not None and loan_5 >= 0.20 and loan_up >= 3 and not loan_flags),
        "finance_balance_trend": confirmed_finance, "stock_loan_balance_trend": confirmed_loan,
        "anomaly_flags": flags, "trend_status": "OK" if not flags else "ANOMALY_CHECK_REQUIRED",
    }


def _window_change(values: list[float | None], business_days: int) -> tuple[float | None, str | None]:
    if len(values) < business_days + 1:
        return None, "INSUFFICIENT_HISTORY"
    start, end = values[-(business_days + 1)], values[-1]
    if start is None or end is None or start <= 0:
        return None, "MISSING_OR_ZERO_BASELINE"
    change = (end - start) / start
    # A >300% balance jump can be real, but is not trusted automatically: it
    # may reflect a split, unit/schema change, or missing observations.
    if abs(change) > 3.0:
        return None, f"OUTLIER_{business_days}D_CHANGE_GT_300_PCT"
    return round(change, 6), None


def _consecutive_days(values: list[float | None]) -> tuple[int, int]:
    if len(values) < 2 or values[-1] is None:
        return 0, 0
    up = down = 0
    for current, previous in zip(reversed(values[1:]), reversed(values[:-1]), strict=True):
        if current is None or previous is None:
            break
        if current > previous and down == 0:
            up += 1
        elif current < previous and up == 0:
            down += 1
        else:
            break
    return up, down


def _direction(change_5d: float | None, up: int, down: int, flags: list[str]) -> str:
    if flags or change_5d is None:
        return "UNCONFIRMED"
    if up >= 3 and change_5d >= 0.05:
        return "INCREASING"
    if down >= 3 and change_5d <= -0.05:
        return "DECREASING"
    return "NO_CONFIRMED_TREND"


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def normalise_premium_charges(payload: bytes, context: MarketContext) -> list[MarketInformation]:
    rows = _csv_rows(payload)
    items: list[MarketInformation] = []
    for row in rows:
        if row.get("コード") != context.native_symbol:
            continue
        timestamp = _date(row.get("貸借申込日"), compact=True)
        if timestamp is None:
            continue
        items.append(MarketInformation(
            source="JSF", source_type="premium_charge", ticker=context.symbol, timestamp=timestamp,
            title="JSF premium charge (逆日歩)", content="Official JSF premium-charge publication; a charge is not by itself a trading signal.",
            url=_PREMIUM_CHARGES_URL, confidence=0.95, verified=True, layer=InformationLayer.VERIFIED_FACT,
            metadata={"company": row.get("銘柄名"), "loan_excess_shares": _number(row.get("貸株超過株数")),
                      "maximum_rate_yen": _number(row.get("最高料率（円）")), "premium_charge_yen": _number(row.get("当日品貸料率（円）")),
                      "premium_charge_days": _number(row.get("当日品貸日数")), "remarks": row.get("備考"), "unit": "円"},
        ))
    return items


def _csv_rows(payload: bytes) -> list[dict[str, str]]:
    text = payload.decode("cp932", errors="replace")
    lines = list(csv.reader(StringIO(text)))
    header_index = next((index for index, row in enumerate(lines) if "銘柄コード" in row or "コード" in row), None)
    if header_index is None:
        return []
    headers = lines[header_index]
    return [dict(zip(headers, row, strict=False)) for row in lines[header_index + 1:] if len(row) >= len(headers)]


def _unit_from_schema(row: dict[str, str]) -> str | None:
    headers = "|".join(row)
    return "株" if "（株）" in headers or "株数" in headers else None


def _number(value: str | None) -> float | None:
    if not value or value in {"*****", "-"}:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _date(value: str | None, *, compact: bool = False) -> datetime | None:
    try:
        fmt = "%Y%m%d" if compact else "%Y/%m/%d"
        return datetime.strptime(value or "", fmt).replace(tzinfo=UTC)
    except ValueError:
        return None


def _history_start(start_date: str, end_date: str, calendar_days: int) -> str:
    try:
        end = date.fromisoformat(end_date)
        requested = date.fromisoformat(start_date)
        return min(requested, end - timedelta(days=calendar_days)).isoformat()
    except ValueError:
        return start_date


def _jsf_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
        return f"{parsed.year:04d} / {parsed.month:02d} / {parsed.day:02d}"
    except ValueError:
        return value
