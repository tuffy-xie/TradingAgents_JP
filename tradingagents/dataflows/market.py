"""Market-aware instrument resolution.

This module classifies an instrument *before* a data provider is selected.
It deliberately has no network dependency: an exchange-qualified Tokyo ticker
is sufficient evidence for the Japan route, while a bare security code is only
accepted when it is present in an explicit, application-controlled allowlist.
That prevents a four-character US symbol or an arbitrary number from silently
becoming a Japanese security.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from enum import StrEnum


class Market(StrEnum):
    """Market route selected before invoking any data provider."""

    US = "US"
    JP = "JP"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MarketContext:
    """Canonical, provider-neutral description of a requested instrument."""

    market: Market
    exchange: str | None
    symbol: str
    native_symbol: str
    currency: str | None
    timezone: str | None
    resolved_by: str

    def to_dict(self) -> dict[str, str | None]:
        """Return a JSON/LangGraph-safe representation."""
        return asdict(self)


# These are explicit examples supported by the product and tests.  Production
# callers may pass a broader verified code set from their own reference-data
# source.  We do not infer that every four-character string is a Tokyo listing.
DEFAULT_KNOWN_JAPAN_SECURITY_CODES = frozenset({"6981", "5801", "285A", "7013"})

_TSE_PREFIX = re.compile(r"^(?:TSE|TYO):(?P<code>\d{4}|\d{3}[A-Z])$", re.IGNORECASE)
_TSE_SUFFIX = re.compile(r"^(?P<code>\d{4}|\d{3}[A-Z])\.T$", re.IGNORECASE)
_BARE_TSE_CODE = re.compile(r"^(?:\d{4}|\d{3}[A-Z])$", re.IGNORECASE)


def _normalise_known_codes(codes: Iterable[str] | None) -> frozenset[str]:
    source = DEFAULT_KNOWN_JAPAN_SECURITY_CODES if codes is None else codes
    return frozenset(str(code).strip().upper() for code in source)


class MarketResolver:
    """Resolve user input into a stable :class:`MarketContext`.

    ``6981.T``, ``TSE:6981`` and ``TYO:6981`` are exchange-qualified and route
    to Japan immediately.  A bare value such as ``6981`` is routed only when
    it is in ``known_japan_codes``; otherwise it remains ``UNKNOWN``.  Normal
    alphabetic equities retain the historical US default.
    """

    def __init__(self, known_japan_codes: Iterable[str] | None = None):
        self.known_japan_codes = _normalise_known_codes(known_japan_codes)

    def resolve(self, raw_symbol: str) -> MarketContext:
        if not isinstance(raw_symbol, str) or not raw_symbol.strip():
            raise ValueError("symbol must be a non-empty string")

        symbol = raw_symbol.strip().upper()
        match = _TSE_PREFIX.fullmatch(symbol) or _TSE_SUFFIX.fullmatch(symbol)
        if match:
            return self._japan_context(match.group("code"), "exchange_qualified")

        if _BARE_TSE_CODE.fullmatch(symbol):
            if symbol in self.known_japan_codes:
                return self._japan_context(symbol, "verified_bare_code")
            return MarketContext(
                market=Market.UNKNOWN,
                exchange=None,
                symbol=symbol,
                native_symbol=symbol,
                currency=None,
                timezone=None,
                resolved_by="unverified_bare_security_code",
            )

        # Preserve the project's existing convention: an ordinary equity ticker
        # has always used the US/global yfinance pipeline unless it carries a
        # recognised exchange suffix.  This compatibility default is deliberate.
        return MarketContext(
            market=Market.US,
            exchange="US",
            symbol=symbol,
            native_symbol=symbol,
            currency="USD",
            timezone="America/New_York",
            resolved_by="legacy_default",
        )

    @staticmethod
    def _japan_context(code: str, resolved_by: str) -> MarketContext:
        code = code.upper()
        return MarketContext(
            market=Market.JP,
            exchange="TSE",
            symbol=f"{code}.T",
            native_symbol=code,
            currency="JPY",
            timezone="Asia/Tokyo",
            resolved_by=resolved_by,
        )


class ProviderRouter:
    """Expose market-specific provider order without coupling agents to vendors."""

    _JP_CHAINS = {
        "market_data": ("jquants", "jpx", "yfinance"),
        "fundamentals": ("jquants", "edinet", "company_ir", "yfinance"),
        "disclosures": ("tdnet", "edinet", "company_ir"),
        "news": ("tdnet", "company_ir", "kabutan", "minkabu", "yahoo_japan"),
        "supply_demand": ("jsf", "jpx_short_selling"),
        "sentiment": ("x", "yahoo_japan_board", "fivech", "moomoo"),
    }

    def providers_for(self, context: MarketContext, capability: str) -> tuple[str, ...]:
        """Return the provider preference list for a capability.

        The US return value is empty on purpose: existing ``interface.py``
        remains the authority for the legacy vendor chain and is not altered.
        """
        if context.market == Market.JP:
            return self._JP_CHAINS.get(capability, ())
        return ()


def resolve_market_context(
    raw_symbol: str,
    known_japan_codes: Iterable[str] | None = None,
) -> MarketContext:
    """Convenience API used by CLI, web, graph and dataflow callers."""
    return MarketResolver(known_japan_codes).resolve(raw_symbol)
