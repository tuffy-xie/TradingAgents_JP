"""Tokyo Stock Exchange session dates used by Japan freshness guards.

The project previously treated a generous number of calendar days as a market
freshness window.  That accepts a feed which is one or more *trading sessions*
behind, and also makes weekends look stale.  This module intentionally owns no
network access.  It implements the Japanese national-holiday rules relevant to
the TSE plus the exchange's year-end closure, so nightly and historical runs
share one deterministic calendar.

The rules cover 2020 onward, the supported production/backtest horizon.  The
Olympic holiday moves in 2020/2021 are explicit legal exceptions rather than a
company or ticker special case.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta
from functools import cache
from zoneinfo import ZoneInfo

_JST = ZoneInfo("Asia/Tokyo")
_TSE_CLOSE = time(15, 30)


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    weeks = calendar.monthcalendar(year, month)
    days = [week[weekday] for week in weeks if week[weekday]]
    return date(year, month, days[occurrence - 1])


def _vernal_equinox(year: int) -> date:
    # Cabinet Office holiday tables use the astronomical date.  This standard
    # approximation is exact for 1980-2099.
    day = int(20.8431 + 0.242194 * (year - 1980) - (year - 1980) // 4)
    return date(year, 3, day)


def _autumn_equinox(year: int) -> date:
    day = int(23.2488 + 0.242194 * (year - 1980) - (year - 1980) // 4)
    return date(year, 9, day)


@cache
def japan_public_holidays(year: int) -> frozenset[date]:
    """Return statutory Japanese holidays, including substitutes/citizen days."""
    holidays = {
        date(year, 1, 1),
        _nth_weekday(year, 1, calendar.MONDAY, 2),
        date(year, 2, 11),
        date(year, 2, 23),
        _vernal_equinox(year),
        date(year, 4, 29),
        date(year, 5, 3),
        date(year, 5, 4),
        date(year, 5, 5),
        date(year, 8, 11),
        _nth_weekday(year, 9, calendar.MONDAY, 3),
        _autumn_equinox(year),
        date(year, 11, 3),
        date(year, 11, 23),
    }
    if year == 2020:
        holidays.update({date(2020, 7, 23), date(2020, 7, 24), date(2020, 8, 10)})
    elif year == 2021:
        holidays.update({date(2021, 7, 22), date(2021, 7, 23), date(2021, 8, 8)})
    else:
        holidays.update(
            {
                _nth_weekday(year, 7, calendar.MONDAY, 3),
                _nth_weekday(year, 10, calendar.MONDAY, 2),
            }
        )

    # A non-holiday weekday between two national holidays is a citizen's
    # holiday.  Iterate because adding one can affect substitute placement.
    changed = True
    while changed:
        changed = False
        cursor = date(year, 1, 2)
        end = date(year, 12, 30)
        while cursor <= end:
            if (
                cursor.weekday() < 5
                and cursor not in holidays
                and cursor - timedelta(days=1) in holidays
                and cursor + timedelta(days=1) in holidays
            ):
                holidays.add(cursor)
                changed = True
            cursor += timedelta(days=1)

    # Since 2007, a Sunday holiday is observed on the next day which is not
    # already a national holiday.
    for holiday in sorted(holidays):
        if holiday.weekday() != calendar.SUNDAY:
            continue
        substitute = holiday + timedelta(days=1)
        while substitute in holidays:
            substitute += timedelta(days=1)
        holidays.add(substitute)
    return frozenset(holidays)


def is_japan_trading_day(value: date) -> bool:
    """Whether TSE has a regular cash-equity session on ``value``."""
    if value.weekday() >= 5:
        return False
    if (value.month, value.day) in {(1, 1), (1, 2), (1, 3), (12, 31)}:
        return False
    return value not in japan_public_holidays(value.year)


def latest_japan_trading_day(as_of: date) -> date:
    """Latest completed TSE session under the nightly date-level contract."""
    cursor = as_of
    while not is_japan_trading_day(cursor):
        cursor -= timedelta(days=1)
    return cursor


def latest_completed_japan_session(
    as_of: date, *, now: datetime | None = None
) -> date:
    """Latest completed TSE session for date-level analysis.

    Historical dates retain the Stage 8 nightly contract.  For the current JST
    date only, a run before the 15:30 cash-session close uses the prior session
    so an intraday daily candle cannot be called a completed close.
    """
    observed = now or datetime.now(_JST)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    local = observed.astimezone(_JST)
    candidate = latest_japan_trading_day(as_of)
    if as_of == local.date() and candidate == as_of and local.time() < _TSE_CLOSE:
        return previous_japan_trading_day(as_of)
    return candidate


def previous_japan_trading_day(value: date) -> date:
    """Trading session immediately before ``value``."""
    cursor = value - timedelta(days=1)
    while not is_japan_trading_day(cursor):
        cursor -= timedelta(days=1)
    return cursor


def shift_japan_trading_days(value: date, sessions: int) -> date:
    """Move backward by ``sessions`` TSE sessions (zero keeps ``value``)."""
    cursor = value
    for _ in range(max(0, sessions)):
        cursor = previous_japan_trading_day(cursor)
    return cursor
