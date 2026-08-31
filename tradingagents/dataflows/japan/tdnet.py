"""Public TDnet disclosure-index provider.

TDnet's public disclosure viewer publishes a date-indexed HTML list and linked
PDF documents.  This provider reads that public index politely; it does not use
the paid TDnet API, authenticate, or attempt to defeat access controls.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from html import unescape
from typing import Any
from urllib.parse import urljoin

from tradingagents.dataflows.config import get_config
from tradingagents.dataflows.market import MarketContext

from .http import get_bytes, get_json, get_text
from .models import DataStatus, InformationLayer, MarketInformation, ProviderResponse, SourceStatus

_PUBLIC_INDEX_URL = "https://www.release.tdnet.info/inbs/I_list_{page:03d}_{day:%Y%m%d}.html"
_ROW = re.compile(r"<tr>\s*(?P<body>.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL = re.compile(r"<td[^>]*class=[\"'][^\"']*kj(?P<field>Time|Code|Name|Title)[^\"']*[\"'][^>]*>(?P<value>.*?)</td>", re.IGNORECASE | re.DOTALL)
_PDF = re.compile(r"href=[\"'](?P<href>[^\"']+\.pdf)[\"']", re.IGNORECASE)
_PAGE = re.compile(r"I_list_(?P<page>\d{3})_\d{8}\.html")
_SUMMARY = re.compile(
    r'<div[^>]*class=["\'][^"\']*kaijiSum[^"\']*["\'][^>]*>'
    r"\s*(?P<start>\d+)\s*[～-]\s*(?P<end>\d+)\s*件\s*(?:&nbsp;|\s)*"
    r"/\s*(?:&nbsp;|\s)*全\s*(?P<total>\d+)\s*件\s*</div>",
    re.IGNORECASE,
)
_NO_DISCLOSURES = re.compile(r"開示された情報はありません")
_TAG = re.compile(r"<[^>]+>")


class TDnetProvider:
    """Collect recent, public TDnet disclosures for one Japanese security.

    ``feed_url`` remains supported for installations with an authorised
    structured feed.  By default, the official public index is used for the
    requested dates.  The page cap is explicit so earnings-season volume cannot
    overload the public service; a capped day is recorded in source status.
    """

    name = "TDnet"
    category = "tdnet"
    cache_version = "public-index-pdf-v3"

    def __init__(self, feed_url: str | None = None):
        config = get_config().get("markets", {}).get("jp", {})
        self.feed_url = feed_url or config.get("tdnet_feed_url")
        self.timeout = float(config.get("request_timeout_seconds", 10))
        self.max_pages_per_day = int(config.get("tdnet_max_pages_per_day", 50))
        self.page_concurrency = int(config.get("tdnet_page_concurrency", 8))
        self.extract_pdf_text = bool(config.get("tdnet_extract_pdf_text", True))
        self.max_pdf_bytes = int(config.get("tdnet_max_pdf_bytes", 8_000_000))
        self.max_pdf_chars = int(config.get("tdnet_max_pdf_chars", 12_000))
        self.enabled = bool(config.get("datasources", {}).get("tdnet", True))

    async def fetch(self, context: MarketContext, *, start_date: str, end_date: str) -> ProviderResponse:
        if not self.enabled:
            return ProviderResponse(SourceStatus(self.name, DataStatus.DISABLED, detail="disabled by markets.jp.datasources"))
        if self.feed_url:
            return await self._fetch_authorised_feed(context, start_date, end_date)

        # The financial-authority scan uses a 32-day inclusive window so a
        # prior month-end structured disclosure remains inside official
        # coverage on a month-end analysis date.  This is still tightly
        # bounded and each day retains the independent page safety cap.
        days = _date_range(start_date, end_date, maximum_days=32)
        if not days:
            return ProviderResponse(SourceStatus(self.name, DataStatus.PARSE_FAILED, detail="invalid date range"))
        page_semaphore = asyncio.Semaphore(max(1, self.page_concurrency))
        results = await asyncio.gather(
            *(
                self._fetch_day_detailed(context, day, page_semaphore=page_semaphore)
                for day in days
            )
        )
        items = tuple(item for day_items, _, _, _ in results for item in day_items)
        if self.extract_pdf_text:
            items = await self._enrich_pdf_text(items)
        capped_days = sum(capped for _, capped, _, _ in results)
        failures = [failure for _, _, failure, _ in results if failure]
        day_coverage = [coverage for _, _, _, coverage in results]
        detail_parts = []
        status = DataStatus.OK
        if failures:
            status = DataStatus.DATA_UNAVAILABLE
            detail_parts.append("retrieval_failed=true")
            detail_parts.append(f"failures={json.dumps(failures, ensure_ascii=False)}")
        elif not items:
            detail_parts.append("empty_result=true")
        if capped_days:
            status = DataStatus.DATA_UNAVAILABLE
            detail_parts.append(f"{capped_days} day(s) reached tdnet_max_pages_per_day={self.max_pages_per_day}")
        coverage_complete = not failures and not capped_days and all(
            entry["complete"] for entry in day_coverage
        )
        coverage = {
            "status": "COMPLETE" if coverage_complete else "INCOMPLETE",
            "complete": coverage_complete,
            "requested_start_date": days[0].isoformat(),
            "requested_end_date": days[-1].isoformat(),
            "requested_day_count": len(days),
            "complete_day_count": sum(bool(entry["complete"]) for entry in day_coverage),
            "max_pages_per_day": self.max_pages_per_day,
            "days": day_coverage,
        }
        return ProviderResponse(
            SourceStatus(self.name, status, detail="; ".join(detail_parts), item_count=len(items)),
            items,
            metadata={"coverage": coverage},
        )

    async def _enrich_pdf_text(
        self, items: tuple[MarketInformation, ...]
    ) -> tuple[MarketInformation, ...]:
        """Best-effort extract public PDF text; metadata remains usable on failure."""
        enriched = await asyncio.gather(*(self._extract_pdf(item) for item in items))
        return tuple(enriched)

    async def _extract_pdf(self, item: MarketInformation) -> MarketInformation:
        if not item.url or not item.url.lower().endswith(".pdf"):
            return item
        response = await get_bytes(item.url, timeout=self.timeout, max_bytes=self.max_pdf_bytes)
        if response.status != DataStatus.OK:
            return _with_extraction_status(item, "DATA_UNAVAILABLE", response.detail)
        try:
            text = extract_pdf_text(response.payload, self.max_pdf_chars)
        except Exception as exc:  # an issuer PDF parse error must not lose the index fact
            return _with_extraction_status(item, "PARSE_FAILED", type(exc).__name__, status=DataStatus.PARSE_FAILED)
        metadata = {
            **item.metadata,
            "document_downloaded": True,
            **structure_official_disclosure(
                title=item.title,
                text=text,
                source=item.source,
                event_date=item.timestamp.date().isoformat(),
                extraction_status="OK" if text else "EMPTY_TEXT",
            ),
        }
        return MarketInformation(
            **{
                **item.__dict__,
                "source_type": metadata["event_type"],
                "content": text or item.content,
                "content_level": "full_text" if text else "summary_only",
                "metadata": metadata,
            }
        )

    async def _fetch_authorised_feed(
        self, context: MarketContext, start_date: str, end_date: str
    ) -> ProviderResponse:
        response = await get_json(
            self.feed_url,
            params={"ticker": context.native_symbol, "start_date": start_date, "end_date": end_date},
            timeout=self.timeout,
        )
        if response.status != DataStatus.OK:
            return ProviderResponse(SourceStatus(self.name, response.status, detail=response.detail))
        matching_records = tuple(_matching_authorised_records(response.payload, context))
        if any(
            _parse_timestamp(record.get("date") or record.get("timestamp")) is None
            for record in matching_records
        ):
            return ProviderResponse(
                SourceStatus(
                    self.name,
                    DataStatus.DATA_UNAVAILABLE,
                    detail="invalid authorised-feed disclosure timestamp",
                ),
                metadata={
                    "coverage": {
                        "status": "INCOMPLETE",
                        "complete": False,
                        "requested_start_date": start_date,
                        "requested_end_date": end_date,
                    }
                },
            )
        items = tuple(normalise_tdnet_records(response.payload, context))
        return ProviderResponse(
            SourceStatus(self.name, DataStatus.OK, item_count=len(items)),
            items,
            metadata={
                "coverage": {
                    "status": "COMPLETE",
                    "complete": True,
                    "requested_start_date": start_date,
                    "requested_end_date": end_date,
                    "requested_day_count": None,
                    "complete_day_count": None,
                    "max_pages_per_day": None,
                    "days": [],
                }
            },
        )

    async def _fetch_day(self, context: MarketContext, day: date) -> tuple[tuple[MarketInformation, ...], bool]:
        items, capped, _, _ = await self._fetch_day_detailed(context, day)
        return items, capped

    async def _fetch_day_detailed(
        self,
        context: MarketContext,
        day: date,
        *,
        page_semaphore: asyncio.Semaphore | None = None,
    ) -> tuple[
        tuple[MarketInformation, ...],
        bool,
        dict[str, str] | None,
        dict[str, Any],
    ]:
        first_url = _PUBLIC_INDEX_URL.format(page=1, day=day)
        status, html, error_detail = await self._get_index_page(
            first_url, page_semaphore
        )
        if status != DataStatus.OK:
            failure = {
                "requested_date": day.isoformat(),
                "requested_url": first_url,
                "failure_type": status.value,
                "error": error_detail or status.value,
            }
            return (), False, failure, _coverage_entry(day, complete=False, failure=failure)
        pagination = _pagination_summary(html)
        if pagination is None:
            failure = {
                "requested_date": day.isoformat(),
                "requested_url": first_url,
                "failure_type": DataStatus.PARSE_FAILED.value,
                "error": "invalid TDnet pagination summary",
            }
            return (), False, failure, _coverage_entry(day, complete=False, failure=failure)
        total_items, advertised_pages, page_size = pagination
        capped = advertised_pages > self.max_pages_per_day
        page_count = min(advertised_pages, self.max_pages_per_day)
        pages = [html]
        if page_count > 1:
            remaining = await asyncio.gather(*(
                self._get_index_page(
                    _PUBLIC_INDEX_URL.format(page=number, day=day), page_semaphore
                )
                for number in range(2, page_count + 1)
            ))
            page_failures = []
            for number, (page_status, body, page_error) in enumerate(remaining, start=2):
                if page_status == DataStatus.OK:
                    pages.append(body)
                else:
                    page_failures.append({
                        "requested_date": day.isoformat(),
                        "requested_url": _PUBLIC_INDEX_URL.format(page=number, day=day),
                        "failure_type": page_status.value,
                        "error": page_error or page_status.value,
                    })
            if page_failures:
                failure = page_failures[0]
                return (), capped, failure, _coverage_entry(
                    day,
                    complete=False,
                    total_items=total_items,
                    advertised_pages=advertised_pages,
                    fetched_pages=len(pages),
                    failure=failure,
                )
        pagination_failure = _validate_page_summaries(
            pages, total_items=total_items, page_size=page_size
        )
        if pagination_failure is not None:
            failure = {
                "requested_date": day.isoformat(),
                "requested_url": _PUBLIC_INDEX_URL.format(
                    page=pagination_failure, day=day
                ),
                "failure_type": DataStatus.PARSE_FAILED.value,
                "error": "inconsistent TDnet pagination summary",
            }
            return (), capped, failure, _coverage_entry(
                day,
                complete=False,
                total_items=total_items,
                advertised_pages=advertised_pages,
                fetched_pages=len(pages),
                failure=failure,
            )
        items = tuple(
            item
            for page_number, page_html in enumerate(pages, start=1)
            for item in _parse_public_list(page_html, context, day, page_number)
        )
        return items, capped, None, _coverage_entry(
            day,
            complete=not capped,
            total_items=total_items,
            advertised_pages=advertised_pages,
            fetched_pages=len(pages),
        )

    async def _get_index_page(
        self, url: str, semaphore: asyncio.Semaphore | None
    ) -> tuple[DataStatus, str, str]:
        if semaphore is None:
            return await get_text(url, timeout=self.timeout)
        async with semaphore:
            return await get_text(url, timeout=self.timeout)


def _date_range(start_date: str, end_date: str, *, maximum_days: int) -> tuple[date, ...]:
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return ()
    if end < start:
        return ()
    start = max(start, end - timedelta(days=maximum_days - 1))
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _page_count(html: str) -> int:
    summary = _pagination_summary(html)
    if summary is not None:
        return summary[1]
    pages = [int(match.group("page")) for match in _PAGE.finditer(html)]
    return max(pages, default=1)


def _pagination_summary(html: str) -> tuple[int, int, int] | None:
    """Return (total items, total pages, page size) from TDnet's own summary."""
    match = _SUMMARY.search(html)
    if match:
        start = int(match.group("start"))
        end = int(match.group("end"))
        total = int(match.group("total"))
        if start != 1 or end < start or total < end:
            return None
        page_size = end - start + 1
        return total, math.ceil(total / page_size), page_size
    if _NO_DISCLOSURES.search(html):
        return 0, 1, 0
    return None


def _validate_page_summaries(
    pages: list[str], *, total_items: int, page_size: int
) -> int | None:
    if total_items == 0:
        return None if len(pages) == 1 and _NO_DISCLOSURES.search(pages[0]) else 1
    for page_number, html in enumerate(pages, start=1):
        match = _SUMMARY.search(html)
        if match is None:
            return page_number
        expected_start = (page_number - 1) * page_size + 1
        expected_end = min(page_number * page_size, total_items)
        if (
            int(match.group("start")) != expected_start
            or int(match.group("end")) != expected_end
            or int(match.group("total")) != total_items
        ):
            return page_number
    return None


def _coverage_entry(
    day: date,
    *,
    complete: bool,
    total_items: int | None = None,
    advertised_pages: int | None = None,
    fetched_pages: int = 0,
    failure: dict[str, str] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "date": day.isoformat(),
        "complete": complete,
        "advertised_item_count": total_items,
        "advertised_page_count": advertised_pages,
        "fetched_page_count": fetched_pages,
    }
    if failure:
        entry["failure_type"] = failure.get("failure_type")
        entry["failure_detail"] = failure.get("error")
    return entry


def _parse_public_list(
    html: str, context: MarketContext, day: date, page: int
) -> Iterable[MarketInformation]:
    for row in _ROW.finditer(html):
        fields = {match.group("field").lower(): _text(match.group("value")) for match in _CELL.finditer(row.group("body"))}
        if _normalise_code(fields.get("code", "")) != context.native_symbol:
            continue
        title_html = next((match.group("value") for match in _CELL.finditer(row.group("body")) if match.group("field").lower() == "title"), "")
        pdf = _PDF.search(title_html)
        title = fields.get("title", "")
        if not title or not pdf:
            continue
        timestamp = datetime.combine(day, _parse_time(fields.get("time", "")), tzinfo=UTC)
        yield MarketInformation(
            source="TDnet",
            source_type=classify_tdnet_title(title),
            ticker=context.symbol,
            timestamp=timestamp,
            title=title,
            url=urljoin(_PUBLIC_INDEX_URL.format(page=page, day=day), unescape(pdf.group("href"))),
            confidence=0.98,
            verified=True,
            layer=InformationLayer.VERIFIED_FACT,
            content_level="summary_only",
            metadata={
                "company": fields.get("name", ""),
                "official_index": True,
                "index_page": page,
                "document_downloaded": False,
                **structure_official_disclosure(
                    title=title,
                    text="",
                    source="TDnet",
                    event_date=day.isoformat(),
                    extraction_status="NOT_REQUESTED",
                ),
            },
        )


def normalise_tdnet_records(payload: Any, context: MarketContext) -> Iterable[MarketInformation]:
    """Normalize an optional authorised JSON feed using the same fact schema."""
    for record in _matching_authorised_records(payload, context):
        title = str(record.get("title") or "")
        timestamp = _parse_timestamp(record.get("date") or record.get("timestamp"))
        if timestamp is None:
            continue
        yield MarketInformation(
            source="TDnet",
            source_type=classify_tdnet_title(title),
            ticker=context.symbol,
            timestamp=timestamp,
            title=title,
            content=str(record.get("summary") or ""),
            url=record.get("url"),
            confidence=0.98,
            verified=True,
            layer=InformationLayer.VERIFIED_FACT,
            content_level=str(record.get("content_level") or "summary_only"),
            metadata={
                "company": record.get("company"),
                "importance": int(record.get("importance") or 0),
                **structure_official_disclosure(
                    title=title,
                    text=str(record.get("content") or record.get("summary") or ""),
                    source="TDnet",
                    event_date=_parse_timestamp(record.get("date") or record.get("timestamp")).date().isoformat(),
                    extraction_status="FEED_CONTENT" if record.get("summary") else "NOT_REQUESTED",
                ),
            },
        )


def _matching_authorised_records(
    payload: Any, context: MarketContext
) -> Iterable[dict[str, Any]]:
    records = (
        payload
        if isinstance(payload, list)
        else (payload.get("items", []) if isinstance(payload, dict) else [])
    )
    for record in records:
        if not isinstance(record, dict):
            continue
        code = _normalise_code(str(record.get("ticker") or record.get("code") or ""))
        if code == context.native_symbol:
            yield record


def classify_tdnet_title(title: str) -> str:
    categories = (
        ("financial results", "earnings_or_quarterly"), ("earnings release", "earnings_or_quarterly"),
        ("有価証券報告書", "earnings_or_quarterly"), ("半期報告書", "earnings_or_quarterly"),
        ("earnings forecast", "guidance_revision"), ("dividend", "dividend"), ("share repurchase", "buyback"),
        ("業績予想", "guidance_revision"), ("上方修正", "guidance_revision"), ("下方修正", "guidance_revision"),
        ("配当", "dividend"), ("自己株式", "buyback"), ("自社株", "buyback"),
        ("増資", "capital_dilution"), ("新株予約権", "capital_dilution"), ("転換社債", "capital_dilution"),
        ("M&A", "m_and_a"), ("合併", "m_and_a"), ("公開買付", "m_and_a"),
        ("受注", "material_contract_or_order"), ("契約", "material_contract_or_order"),
        ("業務提携", "m_and_a"), ("事業再編", "restructuring"), ("組織再編", "restructuring"),
        ("決算", "earnings_or_quarterly"), ("四半期", "earnings_or_quarterly"),
    )
    return next((category for keyword, category in categories if keyword in title), "other_material_disclosure")


def structure_official_disclosure(
    *, title: str, text: str, source: str, event_date: str, extraction_status: str,
) -> dict[str, Any]:
    """Extract only explicit, traceable facts from an official disclosure.

    This deliberately avoids deriving forecasts or financial ratios.  The
    metadata is shared by TDnet and Company IR, so agents receive the same
    schema regardless of where an issuer published the official document.
    """
    title_type = classify_tdnet_title(title)
    # The first page/heading is a safer conflict check than scanning every
    # reference contained in a financial release.
    body_head = text[:2500]
    body_type = classify_tdnet_title(body_head) if text else title_type
    # Financial releases often discuss dividends and buybacks in their body.
    # Treat it as a genuine conflict only when the title's own signature is
    # absent from the heading region and a different official event is explicit.
    title_signature = _event_signature(title_type)
    conflict = bool(
        text and body_type != title_type and body_type != "other_material_disclosure"
        and title_signature and not any(term in body_head for term in title_signature)
    )
    event_type = body_type if conflict else title_type
    compact = " ".join(text.split())
    facts = _key_facts(compact)
    return {
        "event_type": event_type,
        "date": event_date,
        "title": title,
        "source": source,
        "key_facts": facts,
        "key_numbers": _key_numbers(compact),
        "guidance_change": _guidance_change(compact or title),
        "dividend_change": _dividend_change(compact or title),
        "buyback_amount": _buyback_amount(compact),
        "capital_policy_change": _capital_policy_change(compact or title),
        "confidence": 0.98 if extraction_status in {"OK", "FEED_CONTENT"} else 0.95,
        "extraction_status": extraction_status,
        "title_body_conflict": conflict,
        "title_body_conflict_reason": "official body classification differs from index title" if conflict else "",
    }


def _key_facts(text: str) -> list[str]:
    terms = ("業績予想", "配当予想", "自己株式", "公開買付", "受注", "契約", "合併", "事業再編", "新株予約権")
    sentences = re.split(r"(?<=[。．])", text)
    return [sentence.strip()[:500] for sentence in sentences if any(term in sentence for term in terms)][:6]


def _key_numbers(text: str) -> dict[str, str]:
    labels = {
        "売上高": "(?:億円|百万円|千円)", "営業利益": "(?:億円|百万円|千円)", "経常利益": "(?:億円|百万円|千円)",
        "親会社株主に帰属する当期純利益": "(?:億円|百万円|千円)", "1株当たり配当金": "円",
        "取得価額": "(?:億円|百万円|千円|円)", "取得し得る株式": "株",
    }
    values: dict[str, str] = {}
    for label, unit in labels.items():
        match = re.search(rf"{re.escape(label)}[^0-9０-９]{{0,40}}([0-9０-９,，.．]+\s*{unit})", text)
        if match:
            values[label] = match.group(1).replace("，", ",").replace("．", ".")
    return values


def _guidance_change(text: str) -> str:
    if "上方修正" in text:
        return "UPWARD"
    if "下方修正" in text:
        return "DOWNWARD"
    if "業績予想" in text and ("修正" in text or "変更" in text):
        return "REVISED"
    return "UNDETERMINED"


def _dividend_change(text: str) -> str:
    if "増配" in text:
        return "INCREASED"
    if "減配" in text:
        return "DECREASED"
    if "無配" in text:
        return "SUSPENDED"
    if "配当" in text and ("修正" in text or "変更" in text):
        return "REVISED"
    return "UNDETERMINED"


def _buyback_amount(text: str) -> str | None:
    match = re.search(r"(?:取得価額|取得総額|取得金額)[^0-9０-９]{0,40}([0-9０-９,，.．]+\s*(?:億円|百万円|千円|円))", text)
    return match.group(1).replace("，", ",").replace("．", ".") if match else None


def _capital_policy_change(text: str) -> str:
    if any(term in text for term in ("増資", "新株予約権", "第三者割当", "転換社債")):
        return "DILUTION_OR_CAPITAL_CHANGE"
    if "自己株式" in text:
        return "BUYBACK"
    return "UNDETERMINED"


def _event_signature(event_type: str) -> tuple[str, ...]:
    signatures = {
        "earnings_or_quarterly": ("決算", "四半期"), "guidance_revision": ("業績予想", "上方修正", "下方修正"),
        "dividend": ("配当",), "buyback": ("自己株式", "自社株"),
        "capital_dilution": ("増資", "新株予約権", "転換社債"), "m_and_a": ("M&A", "合併", "公開買付", "業務提携"),
        "material_contract_or_order": ("受注", "契約"), "restructuring": ("事業再編", "組織再編"),
    }
    return signatures.get(event_type, ())


def _normalise_code(value: str) -> str:
    code = re.sub(r"\s+", "", value).upper().replace(".T", "")
    return code[:-1] if len(code) == 5 and code.endswith("0") else code


def _text(html: str) -> str:
    return " ".join(unescape(_TAG.sub(" ", html)).split())


def _parse_time(value: str):
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return datetime.min.time()


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def extract_pdf_text(payload: bytes, max_chars: int) -> str:
    """Extract text from a public issuer PDF without OCR or document invention."""
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(payload))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return " ".join(text.split())[:max_chars]


def _with_extraction_status(
    item: MarketInformation, extraction_status: str, detail: str, *, status: DataStatus | None = None,
) -> MarketInformation:
    return MarketInformation(
        **{
            **item.__dict__,
            "status": status or item.status,
            "metadata": {
                **item.metadata,
                **structure_official_disclosure(
                    title=item.title,
                    text="",
                    source=item.source,
                    event_date=item.timestamp.date().isoformat(),
                    extraction_status=extraction_status,
                ),
                "extraction_detail": detail,
            },
        }
    )
