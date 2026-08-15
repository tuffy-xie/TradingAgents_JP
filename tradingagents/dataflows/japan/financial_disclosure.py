"""Small, conservative parser for Japanese official financial disclosures.

This module deliberately parses only explicit values present in extracted
TDnet/company-IR text.  It does not calculate ratios, combine periods, or
infer missing values.  TDnet and Company IR can call the same function later.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

NOT_PROVIDED = "NOT_PROVIDED"
DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
NOT_APPLICABLE = "NOT_APPLICABLE"

_AMOUNT = r"(?P<value>[+-]?[0-9０-９][0-9０-９,，]*(?:\.[0-9０-９]+)?)\s*(?P<unit>百万円|億円|千万円|千円|万円|円)"
_AMOUNT_PLAIN = r"([+-]?[0-9０-９][0-9０-９,，]*(?:\.[0-9０-９]+)?)\s*(百万円|億円|千万円|千円|万円|円)"
_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "revenue": ("売上高", "営業収益"),
    "operating_profit": ("営業利益",),
    "ordinary_profit": ("経常利益",),
    "net_income": (
        "親会社株主に帰属する四半期純利益",
        "親会社株主に帰属する当期純利益",
        "親会社株主に帰属する利益",
        "四半期純利益",
        "当期純利益",
    ),
    "eps": (
        "1株当たり四半期純利益",
        "1株当たり当期純利益",
        "1株当たり利益",
        "EPS",
    ),
}
_GUIDANCE_HEAD = re.compile(r"(?:通期|年間)?業績予想|業績見通し|予想の修正|修正予想", re.I)
_PERIOD = re.compile(
    r"(?P<year>20\d{2})年(?:[0-9０-９]{1,2}月期)?(?:\s*第(?P<quarter>[１２３四半期Qq1-4]+)四半期?)?"
    r"|(?P<fy>FY\s*20\d{2})\s*(?P<fy_period>Q[1-4]|H[12]|FY)?",
    re.I,
)


def parse_financial_disclosure(
    text: str,
    *,
    title: str = "",
    source: str = "",
    disclosure_timestamp: str | datetime | None = None,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Parse explicit Actual and company-Guidance values from disclosure text."""
    normalized = _normalize(text)
    metadata = _metadata(normalized, disclosure_timestamp, source_url)
    fields = list(_FIELD_LABELS)
    if not normalized:
        return {
            "status": DATA_UNAVAILABLE,
            "actual": {field: _missing(DATA_UNAVAILABLE) for field in fields},
            "guidance": {field: _guidance_missing(DATA_UNAVAILABLE) for field in fields},
            "revision_reason": DATA_UNAVAILABLE,
            **metadata,
        }

    actual_text = _actual_region(normalized)
    guidance_text = _guidance_region(normalized)
    actual = {field: _single_value(actual_text, labels) for field, labels in _FIELD_LABELS.items()}
    actual.update(_tdnet_actual_values(actual_text))
    guidance = {
        field: _revision_value(guidance_text, labels) for field, labels in _FIELD_LABELS.items()
    }
    guidance.update(_tdnet_guidance_values(guidance_text))
    standard = metadata["accounting_standard"]
    if standard == "IFRS" and actual["ordinary_profit"]["status"] == NOT_PROVIDED:
        actual["ordinary_profit"] = _missing(NOT_APPLICABLE)
    if standard == "IFRS" and guidance["ordinary_profit"]["status"] == NOT_PROVIDED:
        guidance["ordinary_profit"] = _guidance_missing(NOT_APPLICABLE)

    is_guidance = bool(_GUIDANCE_HEAD.search(f"{title} {normalized}"))
    has_number = bool(re.search(_AMOUNT, normalized))
    if is_guidance and not has_number:
        guidance = {field: _guidance_missing(DATA_UNAVAILABLE) for field in fields}
    return {
        "status": "OK" if has_number else DATA_UNAVAILABLE,
        "actual": actual,
        "guidance": guidance,
        "revision_reason": _revision_reason(normalized, is_guidance),
        **metadata,
    }


def _normalize(text: str) -> str:
    return re.sub(r"[ \t\r\n]+", " ", str(text or "")).strip()


def _actual_region(text: str) -> str:
    match = _GUIDANCE_HEAD.search(text)
    return text[: match.start()] if match else text


def _guidance_region(text: str) -> str:
    match = _GUIDANCE_HEAD.search(text)
    return text[match.start() :] if match else ""


def _single_value(text: str, labels: tuple[str, ...]) -> dict[str, Any]:
    match = _find_amount(text, labels)
    return _value(match) if match else _missing(NOT_PROVIDED)


_NUMBER = r"(?:△|-)?[0-9０-９][0-9０-９,，]*(?:\.[0-9０-９]+)?"


def _number_tokens(text: str) -> list[str]:
    return re.findall(_NUMBER, text)


def _tdnet_actual_values(text: str) -> dict[str, dict[str, Any]]:
    """Read the fixed column order used by TDnet earnings-summary tables.

    Extracted PDFs flatten the header and rows, so label-to-value matching is
    unsafe.  This narrow path only applies when the standard consolidated
    performance table and its current-period row are both present.
    """
    marker = re.search(r"連結経営成績.*?売上高.*?親会社の所有者に", text)
    if not marker:
        return {}
    tail = text[marker.start():]
    row = re.search(
        r"(?P<period>20\d{2}年[^ ]*第[１２３1-3]四半期)\s+(?P<body>.*?)(?=20\d{2}年[^ ]*第[１２３1-3]四半期)",
        tail,
    )
    if not row:
        return {}
    tokens = _number_tokens(row.group("body"))
    # Each amount is followed by its YoY percentage; the first five amounts
    # are revenue, operating profit, tax/pre-tax profit, quarter profit, and
    # parent-attributable quarter profit.
    if len(tokens) < 9:
        return {}
    unit_match = re.search(r"(百万円|億円|千万円|千円|万円|円)", tail[: row.end()])
    unit = unit_match.group(1) if unit_match else None
    if not unit:
        return {}
    fields = ("revenue", "operating_profit", "_tax_profit", "net_income", "_parent_income")
    values = {}
    for field, token in zip(fields, tokens[0:9:2], strict=True):
        values[field] = _value_from_parts((token, unit))
    eps_match = re.search(
        r"基本的[１1]株当たり\s*四半期利益.*?20\d{2}年[^ ]*第[１２３1-3]四半期\s+(" + _NUMBER + r")",
        tail,
    )
    if eps_match:
        values["eps"] = _value_from_parts((eps_match.group(1), "円"))
    result = {
        "revenue": values["revenue"],
        "operating_profit": values["operating_profit"],
        "net_income": values["net_income"],
        "eps": values.get("eps", _missing(NOT_PROVIDED)),
    }
    return result


def _tdnet_guidance_values(text: str) -> dict[str, dict[str, Any]]:
    """Read TDnet's 前回発表予想/今回修正予想 rows by column position."""
    marker = re.search(r"前回発表予想", text)
    current_marker = re.search(r"今回修正予想", text)
    if not marker or not current_marker or current_marker.start() <= marker.start():
        return {}
    previous_segment = text[marker.end(): current_marker.start()]
    # The row repeats the unit before each amount ("百万円 930,000").
    # Discard the announcement date and take the values following the first
    # unit marker, rather than treating the date as a financial number.
    first_unit = re.search(r"(?:百万円|億円|千万円|千円|万円|円)", previous_segment)
    previous_tokens = _number_tokens(previous_segment[first_unit.end():] if first_unit else previous_segment)
    current_tail = text[current_marker.end():]
    current_tokens = _number_tokens(current_tail)
    if len(previous_tokens) < 5 or len(current_tokens) < 5:
        return {}
    unit_match = re.search(r"(百万円|億円|千万円|千円|万円|円)", text[marker.start(): current_marker.start()])
    unit = unit_match.group(1) if unit_match else None
    if not unit:
        return {}
    names = ("revenue", "operating_profit", "_tax_profit", "net_income", "eps")
    result = {}
    for name, previous, current in zip(names, previous_tokens[:5], current_tokens[:5], strict=True):
        value_unit = "円" if name == "eps" else unit
        prev = _value_from_parts((previous, value_unit))
        curr = _value_from_parts((current, value_unit))
        result[name] = {
            "status": "OK",
            "previous_value": prev,
            "current_value": curr,
            "revision_direction": _direction(prev["value"], curr["value"]),
        }
    return {
        "revenue": result["revenue"],
        "operating_profit": result["operating_profit"],
        "net_income": result["net_income"],
        "eps": result["eps"],
    }


def _revision_value(text: str, labels: tuple[str, ...]) -> dict[str, Any]:
    if not text:
        return _guidance_missing(NOT_PROVIDED)
    label = _label_pattern(labels)
    match = re.search(rf"{label}.{{0,100}}?({_AMOUNT_PLAIN})(?:\s*(?:→|⇒|->|から|から今回))\s*({_AMOUNT_PLAIN})", text, re.I)
    if not match:
        # Also accept the common "従来予想 X、今回予想 Y" presentation.
        match = re.search(rf"{label}.{{0,100}}?(従来予想\s*{_AMOUNT_PLAIN}).{{0,80}}?(?:今回|修正後)予想\s*({_AMOUNT_PLAIN})", text, re.I)
    if not match:
        return _guidance_missing(NOT_PROVIDED)
    amounts = re.findall(_AMOUNT_PLAIN, match.group(0))
    if len(amounts) < 2:
        return _guidance_missing(DATA_UNAVAILABLE)
    previous = _value_from_parts(amounts[0])
    current = _value_from_parts(amounts[-1])
    direction = _direction(previous.get("value"), current.get("value"))
    return {
        "status": "OK",
        "previous_value": previous,
        "current_value": current,
        "revision_direction": direction,
    }


def _find_amount(text: str, labels: tuple[str, ...]) -> tuple[str, str] | None:
    pattern = rf"(?:{'|'.join(map(re.escape, labels))})[^0-9０-９]{{0,60}}({_AMOUNT})"
    match = re.search(pattern, text, re.I)
    return (match.group("value"), match.group("unit")) if match else None


def _label_pattern(labels: tuple[str, ...]) -> str:
    return rf"(?:{'|'.join(map(re.escape, labels))})"


def _value(match: tuple[str, str] | None) -> dict[str, Any]:
    return _value_from_parts(match) if match else _missing(NOT_PROVIDED)


def _value_from_parts(parts: tuple[str, str]) -> dict[str, Any]:
    raw, unit = parts
    numeric = float(raw.translate(str.maketrans("０１２３４５６７８９，", "0123456789," )).replace(",", ""))
    return {"status": "OK", "value": numeric, "raw_value": raw, "unit": unit}


def _missing(status: str) -> dict[str, Any]:
    return {"status": status, "value": None, "raw_value": None, "unit": None}


def _guidance_missing(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "previous_value": None,
        "current_value": None,
        "revision_direction": None,
    }


def _direction(previous: float | None, current: float | None) -> str | None:
    if previous is None or current is None:
        return None
    if current > previous:
        return "INCREASED"
    if current < previous:
        return "DECREASED"
    return "UNCHANGED"


def _metadata(text: str, timestamp: str | datetime | None, source_url: str | None) -> dict[str, Any]:
    period = _period(text)
    standard = "IFRS" if re.search(r"\bIFRS\b|ＩＦＲＳ|国際会計基準", text, re.I) else (
        "J-GAAP" if re.search(r"日本基準|日本会計基準", text) else DATA_UNAVAILABLE
    )
    scope = "CONSOLIDATED" if "連結" in text else (
        "NON_CONSOLIDATED" if "単体" in text else DATA_UNAVAILABLE
    )
    return {
        "fiscal_period": period[0],
        "period_type": period[1],
        "currency": "JPY" if any(unit in text for unit in ("百万円", "億円", "円")) else DATA_UNAVAILABLE,
        "unit": _first_unit(text) or DATA_UNAVAILABLE,
        "accounting_standard": standard,
        "consolidation_scope": scope,
        "disclosure_timestamp": _timestamp(timestamp),
        "source_url": source_url,
    }


def _period(text: str) -> tuple[str, str]:
    # Prefer the fiscal-year expression (e.g. 2027年３月期) over the
    # disclosure date that often appears first in a TDnet notice.
    fiscal = re.search(
        r"(?P<year>20\d{2})\s*年\s*[0-9０-９]{1,2}月期\s*(?:第\s*(?P<quarter>[１２３1-3])\s*四半期)?",
        text,
    )
    if fiscal:
        quarter = fiscal.group("quarter")
        if quarter:
            quarter = {"１": "Q1", "２": "Q2", "３": "Q3", "1": "Q1", "2": "Q2", "3": "Q3"}[quarter]
        else:
            quarter = "FY"
        return fiscal.group("year"), quarter
    match = _PERIOD.search(text)
    if not match:
        return DATA_UNAVAILABLE, DATA_UNAVAILABLE
    year = match.group("year") or (match.group("fy") or "").replace(" ", "")
    quarter = match.group("quarter") or match.group("fy_period") or "FY"
    quarter = {"１": "Q1", "２": "Q2", "３": "Q3", "四": "Q4", "1": "Q1", "2": "Q2", "3": "Q3", "4": "Q4"}.get(quarter, quarter.upper())
    return year, quarter


def _first_unit(text: str) -> str | None:
    # Flattened TDnet tables place the unit in a header before the numbers.
    for unit in ("百万円", "億円", "千万円", "千円", "万円", "円"):
        if unit in text:
            return unit
    return None


def _timestamp(value: str | datetime | None) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _revision_reason(text: str, is_guidance: bool) -> str:
    if not is_guidance:
        return NOT_APPLICABLE
    for marker in ("修正理由", "修正の理由", "理由"):
        match = re.search(rf"{marker}[:：]?\s*([^。]+)", text)
        if match:
            return match.group(1).strip()
    return DATA_UNAVAILABLE
