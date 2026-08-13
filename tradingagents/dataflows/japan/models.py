"""Normalized, source-attributed models for Japanese-market research."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class DataStatus(StrEnum):
    OK = "OK"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    PARSE_FAILED = "PARSE_FAILED"
    LOW_SAMPLE = "LOW_SAMPLE"
    DISABLED = "DISABLED"


class InformationLayer(StrEnum):
    VERIFIED_FACT = "VERIFIED_FACT"
    NEWS_ANALYST_VIEW = "NEWS_ANALYST_VIEW"
    MARKET_SENTIMENT = "MARKET_SENTIMENT"


@dataclass(frozen=True)
class FiscalPeriod:
    """Explicit accounting period attached to every comparable financial fact.

    A trailing-twelve-month metric and a single quarterly metric are different
    accounting bases.  Consumers must not subtract or extrapolate one from the
    other unless a provider supplies an explicit, aligned period series.
    """

    end_date: str
    kind: str  # annual | quarter | half_year | nine_months | trailing_twelve_months
    fiscal_year: str | None = None
    quarter: int | None = None
    audited: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "end_date": self.end_date,
            "kind": self.kind,
            "fiscal_year": self.fiscal_year,
            "quarter": self.quarter,
            "audited": self.audited,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> FiscalPeriod:
        return cls(
            end_date=str(raw["end_date"]),
            kind=str(raw["kind"]),
            fiscal_year=raw.get("fiscal_year"),
            quarter=raw.get("quarter"),
            audited=raw.get("audited"),
        )


def periods_are_comparable(left: FiscalPeriod, right: FiscalPeriod) -> bool:
    """Whether two accounting facts can be directly compared or combined."""
    return left.kind == right.kind and left.fiscal_year == right.fiscal_year


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_datetime(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return _utc_now()


@dataclass(frozen=True)
class MarketInformation:
    """One normalized fact, news item, view, or sentiment observation.

    ``layer`` keeps verified disclosures, editorial material, and community
    content separate before an LLM ever sees them. ``content_level`` is
    ``summary_only`` when a source did not provide legally accessible full text.
    """

    source: str
    source_type: str
    ticker: str
    timestamp: datetime
    title: str = ""
    content: str = ""
    url: str | None = None
    confidence: float = 0.0
    verified: bool = False
    status: DataStatus = DataStatus.OK
    layer: InformationLayer = InformationLayer.NEWS_ANALYST_VIEW
    content_level: str = "full_text"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_type": self.source_type,
            "ticker": self.ticker,
            "timestamp": self.timestamp.isoformat(),
            "title": self.title,
            "content": self.content,
            "url": self.url,
            "confidence": self.confidence,
            "verified": self.verified,
            "status": self.status.value,
            "layer": self.layer.value,
            "content_level": self.content_level,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MarketInformation:
        return cls(
            source=str(raw["source"]),
            source_type=str(raw["source_type"]),
            ticker=str(raw["ticker"]),
            timestamp=_parse_datetime(raw.get("timestamp")),
            title=str(raw.get("title") or ""),
            content=str(raw.get("content") or ""),
            url=raw.get("url"),
            confidence=float(raw.get("confidence") or 0.0),
            verified=bool(raw.get("verified")),
            status=DataStatus(raw.get("status", DataStatus.OK)),
            layer=InformationLayer(raw.get("layer", InformationLayer.NEWS_ANALYST_VIEW)),
            content_level=str(raw.get("content_level") or "full_text"),
            metadata=dict(raw.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SourceStatus:
    """Transparent result of one provider attempt; errors never become facts."""

    source: str
    status: DataStatus
    fetched_at: datetime = field(default_factory=_utc_now)
    detail: str = ""
    item_count: int = 0
    from_cache: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status.value,
            "fetched_at": self.fetched_at.isoformat(),
            "detail": self.detail,
            "item_count": self.item_count,
            "from_cache": self.from_cache,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SourceStatus:
        return cls(
            source=str(raw["source"]),
            status=DataStatus(raw["status"]),
            fetched_at=_parse_datetime(raw.get("fetched_at")),
            detail=str(raw.get("detail") or ""),
            item_count=int(raw.get("item_count") or 0),
            from_cache=bool(raw.get("from_cache")),
        )


@dataclass(frozen=True)
class ProviderResponse:
    """Provider output consumed by the shared Japan data service."""

    status: SourceStatus
    items: tuple[MarketInformation, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status.to_dict(), "items": [item.to_dict() for item in self.items]}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ProviderResponse:
        return cls(
            status=SourceStatus.from_dict(raw["status"]),
            items=tuple(MarketInformation.from_dict(item) for item in raw.get("items", [])),
        )


@dataclass(frozen=True)
class JapanResearchBundle:
    """A source-transparent, deduplicated snapshot supplied to Japan-aware agents."""

    ticker: str
    items: tuple[MarketInformation, ...]
    source_statuses: tuple[SourceStatus, ...]
    collected_at: datetime = field(default_factory=_utc_now)

    def by_layer(self, layer: InformationLayer) -> tuple[MarketInformation, ...]:
        return tuple(item for item in self.items if item.layer == layer)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "items": [item.to_dict() for item in self.items],
            "source_statuses": [status.to_dict() for status in self.source_statuses],
            "collected_at": self.collected_at.isoformat(),
        }
