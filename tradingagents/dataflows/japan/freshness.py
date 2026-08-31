"""Small Japan-only freshness contract for source-dated observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .trading_calendar import latest_japan_trading_day, shift_japan_trading_days

LATEST_AVAILABLE = "LATEST_AVAILABLE"
STALE_SOURCE = "STALE_SOURCE"
FUTURE_DATA = "FUTURE_DATA"
FRESHNESS_UNVERIFIED = "FRESHNESS_UNVERIFIED"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


@dataclass(frozen=True)
class FreshnessAssessment:
    status: str
    data_date: date | None
    expected_latest: date | None
    native_cadence: str
    basis: str

    @property
    def usable(self) -> bool:
        return self.status == LATEST_AVAILABLE

    def to_dict(self) -> dict[str, str | None]:
        return {
            "status": self.status,
            "data_date": self.data_date.isoformat() if self.data_date else None,
            "expected_latest": self.expected_latest.isoformat()
            if self.expected_latest
            else None,
            "native_cadence": self.native_cadence,
            "basis": self.basis,
        }


def assess_japan_session_data(
    data_date: date | None,
    analysis_as_of: date,
    *,
    publication_lag_sessions: int = 0,
    native_cadence: str = "JPX_TRADING_SESSION",
) -> FreshnessAssessment:
    """Assess a source whose expected observation is tied to TSE sessions.

    ``publication_lag_sessions=1`` represents sources such as confirmed JSF
    balances which are published on the next TSE business day.  A native delay
    is therefore accepted only when it matches the documented cadence; it is
    never inferred from the age of a returned record.
    """
    expected = shift_japan_trading_days(
        latest_japan_trading_day(analysis_as_of), publication_lag_sessions
    )
    if data_date is None:
        return FreshnessAssessment(
            DATA_UNAVAILABLE,
            None,
            expected,
            native_cadence,
            "source data date missing",
        )
    if data_date > analysis_as_of:
        return FreshnessAssessment(
            FUTURE_DATA,
            data_date,
            expected,
            native_cadence,
            "data date is after analysis_as_of",
        )
    if data_date == expected:
        return FreshnessAssessment(
            LATEST_AVAILABLE,
            data_date,
            expected,
            native_cadence,
            "matches source-native expected TSE session",
        )
    return FreshnessAssessment(
        STALE_SOURCE,
        data_date,
        expected,
        native_cadence,
        "source already has a later expected TSE-session observation",
    )


def assess_expected_publication_date(
    data_date: date | None,
    analysis_as_of: date,
    *,
    expected_latest: date,
    native_cadence: str,
) -> FreshnessAssessment:
    """Assess a documented weekly/monthly/T+N schedule supplied by its adapter.

    The helper deliberately does not calculate a weekly release day.  That is
    source-contract knowledge and must be passed explicitly by a provider.
    """
    if data_date is None:
        status, basis = DATA_UNAVAILABLE, "source data date missing"
    elif data_date > analysis_as_of:
        status, basis = FUTURE_DATA, "data date is after analysis_as_of"
    elif data_date == expected_latest:
        status, basis = LATEST_AVAILABLE, "matches documented source publication schedule"
    else:
        status, basis = STALE_SOURCE, "source has a later documented publication available"
    return FreshnessAssessment(
        status,
        data_date,
        expected_latest,
        native_cadence,
        basis,
    )
