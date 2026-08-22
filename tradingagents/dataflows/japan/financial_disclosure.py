"""Small, conservative parser for Japanese official financial disclosures.

This module deliberately parses only explicit values present in extracted
TDnet/company-IR text.  It does not calculate ratios, combine periods, or
infer missing values.  TDnet and Company IR can call the same function later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field
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
_ACTUAL_SECTION = re.compile(
    r"(?P<year>20\d{2})\s*年\s*[0-9０-９]{1,2}月期\s*第\s*(?P<quarter>[１２３1-3])\s*四半期"
    r"(?:の)?\s*(?P<scope>連結|個別|単体)業績(?!予想)",
)
_GUIDANCE_SECTION = re.compile(
    r"(?P<year>20\d{2})\s*年\s*[0-9０-９]{1,2}月期"
    r"(?:\s*第\s*(?P<quarter>[１２３1-3])\s*四半期(?:\s*[（(]中間期[）)])?)?"
    r"(?:の)?\s*(?:通期\s*)?(?P<scope>連結|個別|単体)業績予想(?:数値)?(?:の修正)?",
)
_ACTUAL_TABLE_HEAD = re.compile(r"(?:連結|個別|単体)経営成績(?:\s*[（(]累計[）)])?")
_HEADER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "parent_income",
        re.compile(
            r"親会社(?:株主|の所有者)に\s*帰属(?:する)?\s*(?:四半期|当期)(?:純)?利益"
        ),
    ),
    ("revenue", re.compile(r"売上高|営業収益|売上収益")),
    ("operating_profit", re.compile(r"営業利益")),
    ("ordinary_profit", re.compile(r"経常利益")),
    ("pre_tax_profit", re.compile(r"税引前(?:四半期|当期)?利益")),
    ("net_income", re.compile(r"(?:四半期|当期)(?:純)?利益")),
    ("comprehensive_income", re.compile(r"四半期\s*包括利益(?:\s*合計額)?")),
)
_TABLE_CELL = re.compile(r"△?[0-9０-９][0-9０-９,，]*(?:\.[0-9０-９]+)?|[－―—-]")
_EPS_HEAD = re.compile(r"(?:基本的\s*)?[１1]\s*株当たり\s*(?:四半期|当期)(?:純)?利益")


@dataclass(frozen=True)
class FinancialRecord:
    """One period/scope-specific official financial section."""

    record_type: str
    fiscal_year: str
    period_type: str
    period_basis: str
    scope: str
    accounting_standard: str
    currency: str
    unit: str
    metrics: dict[str, dict[str, Any]]
    revision_reason: str
    section_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_type": self.record_type,
            "fiscal_year": self.fiscal_year,
            "period_type": self.period_type,
            "period_basis": self.period_basis,
            "scope": self.scope,
            "accounting_standard": self.accounting_standard,
            "currency": self.currency,
            "unit": self.unit,
            "metrics": self.metrics,
            "revision_reason": self.revision_reason,
            "section_text": self.section_text,
        }


@dataclass(frozen=True)
class FinancialDocument:
    """Official disclosure segmented into independently scoped records."""

    status: str
    source: str
    title: str
    disclosure_timestamp: str | None
    source_url: str | None
    accounting_standard: str
    records: tuple[FinancialRecord, ...] = dataclass_field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "source": self.source,
            "title": self.title,
            "disclosure_timestamp": self.disclosure_timestamp,
            "source_url": self.source_url,
            "accounting_standard": self.accounting_standard,
            "records": [record.to_dict() for record in self.records],
        }


def parse_financial_document(
    text: str,
    *,
    title: str = "",
    source: str = "",
    disclosure_timestamp: str | datetime | None = None,
    source_url: str | None = None,
) -> FinancialDocument:
    """Segment official disclosure text before any table-value extraction.

    A Japanese disclosure can contain multiple periods and both consolidated
    and non-consolidated guidance. This function identifies those sections
    without deciding which values are present in each table.
    """
    normalized = _normalize(text)
    document_metadata = _metadata(normalized, disclosure_timestamp, source_url)
    if not normalized:
        return FinancialDocument(
            status=DATA_UNAVAILABLE,
            source=source,
            title=title,
            disclosure_timestamp=document_metadata["disclosure_timestamp"],
            source_url=source_url,
            accounting_standard=document_metadata["accounting_standard"],
        )

    matches: list[tuple[int, str, re.Match[str]]] = []
    matches.extend(
        (match.start(), "ACTUAL", match)
        for match in _ACTUAL_SECTION.finditer(normalized)
        if _has_financial_table_header(normalized, match.end())
    )
    matches.extend(
        (match.start(), "GUIDANCE", match)
        for match in _GUIDANCE_SECTION.finditer(normalized)
        if _has_financial_table_header(normalized, match.end())
    )
    matches.sort(key=lambda item: item[0])

    records: list[FinancialRecord] = []
    for index, (start, record_type, match) in enumerate(matches):
        end = matches[index + 1][0] if index + 1 < len(matches) else len(normalized)
        section_text = normalized[start:end].strip()
        record_metadata = _metadata(section_text, disclosure_timestamp, source_url)
        standard = record_metadata["accounting_standard"]
        if standard == DATA_UNAVAILABLE:
            standard = document_metadata["accounting_standard"]
        period_type = _section_period_type(match.groupdict().get("quarter"))
        records.append(
            FinancialRecord(
                record_type=record_type,
                fiscal_year=match.group("year"),
                period_type=period_type,
                period_basis="FULL_YEAR" if period_type == "FY" else "CUMULATIVE",
                scope=_section_scope(match.group("scope")),
                accounting_standard=standard,
                currency=record_metadata["currency"],
                unit=record_metadata["unit"],
                metrics=(
                    _parse_actual_metrics(
                        section_text,
                        fiscal_year=match.group("year"),
                        period_type=period_type,
                        accounting_standard=standard,
                        unit=record_metadata["unit"],
                    )
                    if record_type == "ACTUAL"
                    else _section_metrics(record_type, standard)
                ),
                revision_reason=(
                    _revision_reason(section_text, True)
                    if record_type == "GUIDANCE"
                    else NOT_APPLICABLE
                ),
                section_text=section_text,
            )
        )

    return FinancialDocument(
        status="OK" if records else DATA_UNAVAILABLE,
        source=source,
        title=title,
        disclosure_timestamp=document_metadata["disclosure_timestamp"],
        source_url=source_url,
        accounting_standard=document_metadata["accounting_standard"],
        records=tuple(records),
    )


def _section_period_type(quarter: str | None) -> str:
    if not quarter:
        return "FY"
    normalized = quarter.translate(str.maketrans("１２３", "123"))
    return {"1": "Q1", "2": "H1", "3": "Q3"}[normalized]


def _has_financial_table_header(text: str, start: int) -> bool:
    """Reject narrative references to a forecast that are not a table block."""
    window = text[start : start + 150]
    labels = sum(label in window for labels in _FIELD_LABELS.values() for label in labels)
    return labels >= 2 and any(unit in window for unit in ("百万円", "億円", "円"))


def _section_scope(value: str) -> str:
    return "CONSOLIDATED" if value == "連結" else "NON_CONSOLIDATED"


def _section_metrics(record_type: str, accounting_standard: str) -> dict[str, dict[str, Any]]:
    if record_type == "ACTUAL":
        metrics = {field: _missing(DATA_UNAVAILABLE) for field in _FIELD_LABELS}
    else:
        metrics = {field: _guidance_missing(DATA_UNAVAILABLE) for field in _FIELD_LABELS}
    if accounting_standard == "IFRS":
        metrics["ordinary_profit"] = (
            _missing(NOT_APPLICABLE)
            if record_type == "ACTUAL"
            else _guidance_missing(NOT_APPLICABLE)
        )
    return metrics


def _parse_actual_metrics(
    section_text: str,
    *,
    fiscal_year: str,
    period_type: str,
    accounting_standard: str,
    unit: str,
) -> dict[str, dict[str, Any]]:
    """Parse one already-segmented Actual table by its header and current row."""
    unavailable = _section_metrics("ACTUAL", accounting_standard)
    table_head = _ACTUAL_TABLE_HEAD.search(section_text)
    if not table_head:
        return unavailable
    row = _find_period_row(section_text, table_head.end(), fiscal_year, period_type)
    if not row:
        return unavailable
    header_text = section_text[table_head.end() : row.start()]
    columns = _actual_header_columns(header_text)
    if not columns:
        return unavailable
    row_text = _period_row_body(section_text, row.end())
    cells = _TABLE_CELL.findall(row_text)
    has_percent_columns = header_text.count("％") >= len(columns)
    stride = 2 if has_percent_columns else 1
    if len(cells) < (len(columns) - 1) * stride + 1:
        return unavailable

    metrics = {field: _missing(NOT_PROVIDED) for field in _FIELD_LABELS}
    if accounting_standard == "IFRS":
        metrics["ordinary_profit"] = _missing(NOT_APPLICABLE)
    for index, column in enumerate(columns):
        target = _actual_metric_target(column, metrics, accounting_standard)
        if not target:
            continue
        token = cells[index * stride]
        metrics[target] = _table_value(token, unit)

    eps = _parse_actual_eps(section_text, fiscal_year, period_type)
    if eps is not None:
        metrics["eps"] = eps
    return metrics


def _actual_header_columns(header_text: str) -> list[str]:
    matches: list[tuple[int, int, str]] = []
    for name, pattern in _HEADER_PATTERNS:
        matches.extend((match.start(), match.end(), name) for match in pattern.finditer(header_text))
    matches.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    columns: list[str] = []
    accepted: list[tuple[int, int]] = []
    for start, end, name in matches:
        if any(start < accepted_end and end > accepted_start for accepted_start, accepted_end in accepted):
            continue
        accepted.append((start, end))
        columns.append(name)
    return columns


def _actual_metric_target(
    column: str,
    metrics: dict[str, dict[str, Any]],
    accounting_standard: str,
) -> str | None:
    if column in {"revenue", "operating_profit", "ordinary_profit"}:
        return column
    if column == "parent_income":
        # Both J-GAAP and IFRS may expose an explicit parent-attributable
        # profit column. It is the only acceptable IFRS source for the
        # normalized net_income field when a separate total-period profit
        # column is also present.
        return "net_income"
    if column == "net_income" and accounting_standard != "IFRS":
        # Some J-GAAP tables use a generic 純利益 label instead of the more
        # specific parent-attributable label.
        return "net_income"
    return None


def _find_period_row(
    text: str,
    start: int,
    fiscal_year: str,
    period_type: str,
) -> re.Match[str] | None:
    pattern = _period_row_pattern(fiscal_year, period_type)
    return re.compile(pattern).search(text, start)


def _period_row_pattern(fiscal_year: str, period_type: str) -> str:
    base = rf"{re.escape(fiscal_year)}\s*年\s*[0-9０-９]{{1,2}}月期"
    suffixes = {
        "Q1": r"\s*第\s*[１1]\s*四半期",
        "H1": r"(?:\s*第\s*[２2]\s*四半期|\s*[（(]?中間期[）)]?)",
        "Q3": r"\s*第\s*[３3]\s*四半期",
        "FY": "",
    }
    return base + suffixes[period_type]


def _period_row_body(text: str, start: int) -> str:
    next_row = re.search(
        r"20\d{2}\s*年\s*[0-9０-９]{1,2}月期(?:\s*第\s*[１２３1-3]\s*四半期|\s*[（(]?中間期[）)]?)?",
        text[start:],
    )
    end = start + next_row.start() if next_row else min(len(text), start + 500)
    return text[start:end]


def _table_value(token: str, unit: str) -> dict[str, Any]:
    if re.fullmatch(r"[－―—-]", token):
        return _missing(NOT_PROVIDED)
    return _value_from_parts((token, unit))


def _parse_actual_eps(
    section_text: str,
    fiscal_year: str,
    period_type: str,
) -> dict[str, Any] | None:
    eps_head = _EPS_HEAD.search(section_text)
    if not eps_head:
        return None
    row = _find_period_row(section_text, eps_head.end(), fiscal_year, period_type)
    if not row:
        return _missing(DATA_UNAVAILABLE)
    cells = _TABLE_CELL.findall(_period_row_body(section_text, row.end()))
    if not cells or re.fullmatch(r"[－―—-]", cells[0]):
        return _missing(NOT_PROVIDED)
    first = cells[0]
    if "." in first or "．" in first:
        return _value_from_parts((first.replace("．", "."), "円"))
    # TDnet PDF extraction may split the yen/sen columns into "13 46".
    # Recombine only under an explicit EPS header with a two-digit sen token.
    if len(cells) >= 2 and re.fullmatch(r"[0-9０-９]{2}", cells[1]):
        raw = f"{first} {cells[1]}"
        integer = first.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        decimals = cells[1].translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        sign = "-" if integer.startswith("△") else ""
        integer = integer.removeprefix("△")
        return {"status": "OK", "value": float(f"{sign}{integer}.{decimals}"), "raw_value": raw, "unit": "円"}
    return _value_from_parts((first, "円"))


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
    """Reuse the header-aware Actual parser for the legacy entry point.

    The previous implementation assigned the fourth flattened amount
    (IFRS total period profit) to ``net_income`` before seeing the explicit
    parent-attributable column. Keeping one semantic parser for both public
    entry points prevents that column-position error.
    """
    metadata = _metadata(text, None, None)
    if metadata["fiscal_period"] == DATA_UNAVAILABLE or not _ACTUAL_TABLE_HEAD.search(text):
        return {}
    return _parse_actual_metrics(
        text,
        fiscal_year=metadata["fiscal_period"],
        period_type=metadata["period_type"],
        accounting_standard=metadata["accounting_standard"],
        unit=metadata["unit"],
    )


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
