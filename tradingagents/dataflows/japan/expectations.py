"""Public Japanese analyst-consensus data, kept separate from issuer guidance.

Only explicitly dated, public consensus data is normalized.  This module never
uses an analyst site as a current-price source and never treats company guidance
as an analyst estimate.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .http import get_text
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus

_URL = "https://minkabu.jp/stock/{code}/analyst_consensus"
_TAG = re.compile(r"<[^>]+>")
_TABLE = re.compile(r"<table\b[^>]*>(?P<body>.*?)</table>", re.I | re.S)
_ROW = re.compile(r"<tr\b[^>]*>(?P<body>.*?)</tr>", re.I | re.S)
_CELL = re.compile(r"<t[hd]\b[^>]*>(?P<body>.*?)</t[hd]>", re.I | re.S)
_DATE = re.compile(r"(20\d{2})/(\d{1,2})/(\d{1,2})")
_NUMBER = re.compile(r"^-?[\d,]+(?:\.\d+)?$")


class JapanAnalystExpectationsProvider:
    """Normalize dated Minkabu analyst consensus without paywall workarounds."""

    name, category, cache_version = (
        "Japan Analyst Expectations",
        "analyst_expectations",
        "minkabu-consensus-v1",
    )

    def __init__(self):
        config = get_config().get("markets", {}).get("jp", {})
        self.enabled = bool(config.get("datasources", {}).get("analyst_expectations", True))
        self.timeout = float(config.get("request_timeout_seconds", 10))

    async def fetch(
        self, context: MarketContext, *, start_date: str, end_date: str
    ) -> ProviderResponse:
        if not self.enabled:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DISABLED, detail="disabled"))
        status, html, detail = await get_text(
            _URL.format(code=context.native_symbol), timeout=self.timeout
        )
        if status != DataStatus.OK:
            return ProviderResponse(SourceStatus(self.name, status, detail=f"Minkabu={detail}"))
        expectations = _parse_consensus(html)
        if expectations is None:
            return ProviderResponse(
                SourceStatus(
                    self.name,
                    DataStatus.DATA_UNAVAILABLE,
                    detail="Minkabu=no dated public consensus",
                ),
            )
        timestamp = _date_to_timestamp(expectations["as_of"])
        expectation_status = (
            DataStatus.OK if expectations["status"] == "OK" else DataStatus.DATA_UNAVAILABLE
        )
        item = MarketInformation(
            source="Minkabu Analyst Consensus",
            source_type="japan_analyst_expectations",
            ticker=context.symbol,
            timestamp=timestamp,
            title="日本证券分析师共识预期",
            content="Public analyst consensus; not company guidance and not an official fact.",
            url=_URL.format(code=context.native_symbol),
            confidence=0.75,
            verified=False,
            status=expectation_status,
            layer=InformationLayer.NEWS_ANALYST_VIEW,
            content_level="structured_data",
            metadata={"analyst_expectations": expectations},
        )
        return ProviderResponse(
            SourceStatus(
                self.name,
                expectation_status,
                detail="Minkabu=OK; current_price=not fetched",
                item_count=1 if expectation_status == DataStatus.OK else 0,
            ),
            (item,),
        )


def _parse_consensus(html):
    overview = _overview(html)
    earnings = _earnings_estimates(html)
    if overview is None:
        return None
    as_of, consensus_rating, target, previous_target, counts = overview
    analyst_count = sum(counts.values())
    # A target without a source-provided analyst count is not a usable
    # consensus.  Do not manufacture an average or rating from the page copy.
    if analyst_count == 0:
        target = None
        consensus_rating = None
    target_change = (
        target - previous_target if target is not None and previous_target is not None else None
    )
    result = {
        "source": "Minkabu Analyst Consensus",
        "source_type": "ANALYST_EXPECTATION",
        "as_of": as_of,
        "published_at": as_of,
        "currency": "JPY",
        "consensus_target_price": target,
        "analyst_count": analyst_count,
        "high_target": None,
        "low_target": None,
        "consensus_rating": consensus_rating,
        "rating_breakdown": counts,
        "ratings": [],
        "ratings_status": "DATA_UNAVAILABLE:no public dated institution-level rating feed",
        "rating_change": None,
        "target_price": target,
        "previous_target_price": previous_target,
        "target_change": target_change,
        "target_price_revision_direction": _direction(target_change),
        "target_price_revision_trend": {
            "30D": "DATA_UNAVAILABLE:no free dated 30-day target-price history",
            "90D": "DATA_UNAVAILABLE:no free dated 90-day target-price history",
        },
        "current_price": None,
        "current_price_source": "Market Analyst market tools or optional Verified Market Snapshot; not fetched by analyst-expectations provider",
        "upside_downside": None,
        "earnings_expectations": earnings,
        "revision_trend": _revision_trend(earnings),
        "company_guidance": None,
        "company_guidance_status": "separate VERIFIED_FACT providers only",
        "status": "OK" if analyst_count else "DATA_UNAVAILABLE",
    }
    return result


def _overview(html):
    plain = _plain(html)
    pattern = re.compile(
        r"(20\d{2}/\d{1,2}/\d{1,2})時点.*?アナリスト判断（コンセンサス）は、([^。]+)。内訳は、(.*?)。\s*アナリストの平均目標株価は([\d,]+)円",
        re.S,
    )
    match = pattern.search(plain)
    if match is None:
        return None
    counts = {
        label: int(value.replace(",", ""))
        for label, value in re.findall(r"([^、，\d]+?)(\d+)人", match.group(3))
    }
    previous = re.search(
        r"この１週間で([\d,]+)円から([\d,]+)円", plain[match.end() : match.end() + 500]
    )
    previous_target = _number(previous.group(1)) if previous else None
    return match.group(1), match.group(2).strip(), _number(match.group(4)), previous_target, counts


def _earnings_estimates(html):
    """Extract analyst-only FY fields from the dated revision table."""
    for table in _TABLE.findall(html):
        rows = [[_plain(cell) for cell in _CELL.findall(row)] for row in _ROW.findall(table)]
        text = " ".join(" ".join(row) for row in rows)
        if "証券アナリスト予想" not in text or "3ヶ月前" not in text or "1株当り利益" not in text:
            continue
        dates = _DATE.findall(text)
        if len(dates) < 4:
            continue
        analyst_dates = ["/".join(parts) for parts in dates[:4]]
        values = {}
        for row in rows:
            if len(row) < 5:
                continue
            label = row[0]
            if label not in {"売上高", "当期利益", "1株当り利益"}:
                continue
            snapshots = [_number(value) for value in row[1:5]]
            values[label] = dict(zip(analyst_dates, snapshots, strict=True))
        if not values:
            continue
        fiscal_year = _fiscal_year_after(html, table)
        latest = analyst_dates[-1]
        return {
            "fiscal_year": fiscal_year,
            "period_type": "FY",
            "estimate_date": latest,
            "unit": {"revenue": "million JPY", "net_income": "million JPY", "eps": "JPY"},
            "revenue_estimate": values.get("売上高", {}).get(latest),
            "net_income_estimate": values.get("当期利益", {}).get(latest),
            "eps_estimate": values.get("1株当り利益", {}).get(latest),
            "operating_profit_estimate": None,
            "ordinary_profit_estimate": None,
            "snapshots": values,
        }
    return {
        "fiscal_year": None,
        "period_type": None,
        "estimate_date": None,
        "status": "DATA_UNAVAILABLE:no public dated analyst earnings table",
    }


def _fiscal_year_after(html, table):
    position = html.find(table)
    nearby = html[position : position + 10000] if position >= 0 else html
    match = re.search(r"(20\d{2})年3月期\s*<br[^>]*>\s*アナリスト予想", nearby, re.I)
    return int(match.group(1)) if match else None


def _revision_trend(earnings):
    snapshots = earnings.get("snapshots") if isinstance(earnings, dict) else None
    if not snapshots or not earnings.get("estimate_date"):
        return {"status": "DATA_UNAVAILABLE:no dated analyst earnings revisions"}
    latest = earnings["estimate_date"]
    dates = sorted(next(iter(snapshots.values())).keys())
    trends = {}
    # The public table supplies 3-month, 1-month, 1-week and latest snapshots.
    # Keep 30D tied to the 1-month observation; a weekly observation is not a
    # valid substitute for a 30-day revision trend.
    for horizon, prior_index in (("30D", 1), ("90D", 0)):
        changes = []
        for _metric, series in snapshots.items():
            prior = series.get(dates[prior_index])
            current = series.get(latest)
            if prior is None or current is None:
                continue
            changes.append(_direction(current - prior))
        trends[horizon] = {
            "metric_upward_revisions": changes.count("UP"),
            "metric_downward_revisions": changes.count("DOWN"),
            "metric_unchanged": changes.count("UNCHANGED"),
            "note": "metric revisions, not individual analyst upgrade/downgrade counts",
        }
    return trends


def _direction(value):
    if value is None:
        return "UNDETERMINED"
    if value > 0:
        return "UP"
    if value < 0:
        return "DOWN"
    return "UNCHANGED"


def _plain(value):
    return " ".join(unescape(_TAG.sub(" ", value)).split())


def _number(value):
    raw = str(value).replace(",", "").strip()
    return float(raw) if _NUMBER.fullmatch(str(value).strip()) else None


def _date_to_timestamp(value):
    return datetime.strptime(value, "%Y/%m/%d").replace(tzinfo=UTC)
