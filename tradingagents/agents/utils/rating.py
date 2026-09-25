"""Shared 5-tier rating vocabulary and a deterministic heuristic parser.

The same five-tier scale (Buy, Overweight, Hold, Underweight, Sell) is used by:
- The Research Manager (investment plan recommendation)
- The Portfolio Manager (final position decision)
- The signal processor (rating extracted for downstream consumers)
- The memory log (rating tag stored alongside each decision entry)

Centralising it here avoids drift between those call sites.
"""

from __future__ import annotations

import re

# Canonical, ordered 5-tier scale (most bullish to most bearish).
RATINGS_5_TIER: tuple[str, ...] = (
    "Buy", "Overweight", "Hold", "Underweight", "Sell",
)

_RATING_SET = {r.lower() for r in RATINGS_5_TIER}

# Matches "Rating: X" / "rating - X" / "Rating: **X**" — tolerates markdown
# bold wrappers and either a colon or hyphen separator.
_RATING_LABEL_RE = re.compile(
    r"(?:rating|评级).*?[:：\-—][\s*]*(buy|overweight|hold|underweight|sell)",
    re.IGNORECASE,
)


def parse_rating(text: str, default: str = "Hold") -> str:
    """Heuristically extract a 5-tier rating from prose text.

    Two-pass strategy:
    1. Look for an explicit "Rating: X" label (tolerant of markdown bold).
    2. Fall back to the first 5-tier rating word found anywhere in the text.

    Returns a Title-cased rating string, or ``default`` if no rating word appears.
    """
    explicit = parse_explicit_rating(text)
    if explicit:
        return explicit

    for line in text.splitlines():
        for word in line.lower().split():
            clean = word.strip("*:.,")
            if clean in _RATING_SET:
                return clean.capitalize()

    return default


def parse_explicit_rating(text: str) -> str | None:
    """Return only an explicitly labelled portfolio rating.

    Unlike :func:`parse_rating`, this helper never invents a default and never
    treats a rating word embedded in narrative prose as final authority.  It is
    intended for execution authorization and other fail-closed boundaries.
    """
    for line in text.splitlines():
        match = _RATING_LABEL_RE.search(line)
        if match and match.group(1).lower() in _RATING_SET:
            return match.group(1).capitalize()
    return None
