"""Japan Securities Finance (JSF/Nihon Securities Finance) public CSV provider."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from io import StringIO

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .http import get_bytes
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus

_BALANCES_URL = "https://www.taisyaku.jp/data/zandaka.csv"
_PREMIUM_CHARGES_URL = "https://www.taisyaku.jp/data/shina.csv"


class JSFProvider:
    """Read official, keyless daily JSF balance and premium-charge CSV files."""

    name = "JSF"
    category = "supply_demand"
    cache_version = "public-csv-v2"

    def __init__(self):
        config = get_config().get("markets", {}).get("jp", {})
        self.timeout = float(config.get("request_timeout_seconds", 10))
        self.enabled = bool(config.get("datasources", {}).get("jsf", True))

    async def fetch(self, context: MarketContext, *, start_date: str, end_date: str) -> ProviderResponse:
        del start_date, end_date
        if not self.enabled:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DISABLED, detail="disabled by markets.jp.datasources"))
        balances, charges = await __import__("asyncio").gather(
            get_bytes(_BALANCES_URL, timeout=self.timeout),
            get_bytes(_PREMIUM_CHARGES_URL, timeout=self.timeout),
        )
        items: list[MarketInformation] = []
        details: list[str] = []
        if balances.status == DataStatus.OK:
            items.extend(normalise_balances(balances.payload, context))
        else:
            details.append(f"balances={balances.status}")
        if charges.status == DataStatus.OK:
            items.extend(normalise_premium_charges(charges.payload, context))
        else:
            details.append(f"premium_charges={charges.status}")
        status = DataStatus.OK if items or not details else DataStatus.DATA_UNAVAILABLE
        return ProviderResponse(SourceStatus(self.name, status, detail=", ".join(details), item_count=len(items)), tuple(items))


def normalise_balances(payload: bytes, context: MarketContext) -> list[MarketInformation]:
    rows = _csv_rows(payload)
    items: list[MarketInformation] = []
    for row in rows:
        if row.get("銘柄コード") != context.native_symbol:
            continue
        application_date = _date(row.get("申込日"))
        settlement_date = _date(row.get("決済日"))
        items.append(MarketInformation(
            source="JSF",
            source_type="securities_finance_balance",
            ticker=context.symbol,
            # The data becomes effective on the settlement date. Keep the
            # application date separately so users never mistake the two.
            timestamp=settlement_date,
            title="日证金融资 / 贷株余额",
            content="日证金每日融资与贷株余额；仅代表证券金融数据，不等同于全部券商信用交易。",
            url=_BALANCES_URL,
            confidence=0.95,
            verified=True,
            layer=InformationLayer.VERIFIED_FACT,
            metadata={
                "company": row.get("銘柄名"),
                "application_date": application_date.date().isoformat(),
                "settlement_date": settlement_date.date().isoformat(),
                "unit": "股",
                "finance_new_shares": _number(row.get("融資新規株数")),
                "finance_repaid_shares": _number(row.get("融資返済株数")),
                "finance_balance_shares": _number(row.get("融資残高株数")),
                "stock_loan_new_shares": _number(row.get("貸株新規株数")),
                "stock_loan_repaid_shares": _number(row.get("貸株返済株数")),
                "stock_loan_balance_shares": _number(row.get("貸株残高株数")),
                "net_balance_shares": _number(row.get("差引残高株数")),
                "report_type": row.get("速報／確報"),
            },
        ))
    return items


def normalise_premium_charges(payload: bytes, context: MarketContext) -> list[MarketInformation]:
    rows = _csv_rows(payload)
    items: list[MarketInformation] = []
    for row in rows:
        if row.get("コード") != context.native_symbol:
            continue
        application_date = _date(row.get("貸借申込日"), compact=True)
        settlement_date = _date(row.get("決済日"), compact=True)
        items.append(MarketInformation(
            source="JSF",
            source_type="premium_charge",
            ticker=context.symbol,
            timestamp=settlement_date,
            title="日证金品贷料（逆日步）",
            content="日证金品贷料（逆日步）公告；单项费用本身不是交易信号。",
            url=_PREMIUM_CHARGES_URL,
            confidence=0.95,
            verified=True,
            layer=InformationLayer.VERIFIED_FACT,
            metadata={
                "company": row.get("銘柄名"),
                "application_date": application_date.date().isoformat(),
                "settlement_date": settlement_date.date().isoformat(),
                "shares_unit": "股",
                "rate_unit": "日元 / 股 / 日",
                "loan_excess_shares": _number(row.get("貸株超過株数")),
                "maximum_rate_yen": _number(row.get("最高料率（円）")),
                "premium_charge_yen": _number(row.get("当日品貸料率（円）")),
                "premium_charge_days": _number(row.get("当日品貸日数")),
                "remarks": row.get("備考"),
            },
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


def _number(value: str | None) -> float | None:
    if not value or value == "*****":
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _date(value: str | None, *, compact: bool = False) -> datetime:
    del compact
    normalized = (value or "").strip().replace("-", "/")
    for fmt in ("%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(normalized, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    # The date is an official field. Never silently label an invalid record as
    # today's observation, which would create a false "latest" signal.
    return datetime(1970, 1, 1, tzinfo=UTC)
