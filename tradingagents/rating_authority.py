"""Identify report-owned recommendations, distinct from attributed rating facts.

This is a publication policy helper, not a rating selector. Portfolio owns the
final rating; other Agents' recommendations remain in the technical log. Source
attribution is evaluated per proposition (or table row), not per whole report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VALUES = re.compile(r"(?<![A-Za-z])(overweight|underweight|buy|hold|sell)(?![A-Za-z])|买入|增持|持有|减持|卖出|買い|売り", re.I)
_CANONICAL = {"买入": "Buy", "增持": "Overweight", "持有": "Hold", "减持": "Underweight", "卖出": "Sell", "買い": "Buy", "売り": "Sell"}
_LABEL = re.compile(
    r"(?:评级|(?:投资|交易|最终|综合)?建议|推荐|rating|recommendation|"
    r"投資判断|投資推奨|レーティング|推奨|final\s+transaction\s+proposal)\s*[:：|\-—]", re.I
)
_LABEL_HEADING = re.compile(
    r"^(?:(?:综合|最终|投资|系统|本报告)\s*)?(?:评级|投资建议|建议|推荐)$|"
    r"^(?:投資判断|投資推奨|レーティング|推奨)$|"
    r"^(?:(?:final|overall|investment)\s+)?(?:rating|recommendation)$|"
    r"^final\s+transaction\s+proposal$|"
    r"^(?:(?:investment|trading)\s+)?signal$|^(?:投资|交易)?信号$|^(?:投資|取引)?シグナル$", re.I
)
# A recommendation can be expressed as a short signal/status cell without a
# rating label. Match the direction's relation to recommendation semantics,
# not a direction word alone (e.g. 買い材料 is a research factor).
_DIRECTION = r"(?:\b(?:buy|sell)\b|买入|卖出|買い|売り)"
_RECOMMENDATION = re.compile(
    rf"{_DIRECTION}\s*(?:[のを]\s*)?(?:示唆|推奨|シグナル|信号|建议|推荐|\b(?:signal|recommendation)\b)|"
    rf"(?:建议|推荐|推奨)\s*{_DIRECTION}|"
    rf"\b(?:recommend(?:s|ed)?|signal(?:s)?\s+to)\s+{_DIRECTION}", re.I
)
_RECOMMENDATION_WITHHELD_BEFORE = re.compile(
    r"(?:不(?:是|构成|建议|推荐)?|没有|尚未(?:出现|形成|确认)|未(?:出现|形成|确认)|未获确认|"
    r"\b(?:no|not(?:\s+(?:a|an|confirmed))?|without))\s*$", re.I
)
_RECOMMENDATION_WITHHELD_AFTER = re.compile(
    r"^\s*(?:ではない|とはいえない|はない|なし|未確認|尚未确认|未确认|不存在|"
    r"\b(?:is|has)\s+not\b)", re.I
)
_OUR_RECOMMENDATION = re.compile(
    r"(?:我们|本报告|本系统|本分析师|我)(?:的)?(?:最终)?(?:投资)?(?:(?:买入|卖出)?信号|评级|建议|推荐)|"
    r"\b(?:we\s+recommend|our\s+(?:(?:buy|sell)\s+)?(?:rating|recommendation|signal)|i\s+recommend)\b", re.I
)
_EXTERNAL = re.compile(
    r"券商|投行|証券会社|證券会社|証券|證券|アナリスト|分析师共识|分析师评级|分析师.{0,20}(?:给予|给出|维持|调升|调降|下调|上调)|"
    r"(?:位|名)分析师|评级分布|共识评级|\b(?:broker(?:age)?|consensus|"
    r"third[ -]party|according\s+to)\b|\banalysts?\b.{0,35}"
    r"\b(?:rate|rates|rated|maintain|maintains|recommend|recommendation|ratings?)\b", re.I
)
_EXTERNAL_HEADING = re.compile(
    r"券商|投行|証券会社|證券会社|アナリスト評価|分析师(?:共识|评级动态|评级分布)|共识|评级分布|(?:外部|第三方)(?:来源|评级|建议|信号)|"
    r"\b(?:broker(?:age)?|consensus|third[ -]party|external\s+(?:source|ratings?|recommendations?|signals?))\b", re.I
)
_OWN_LABEL = re.compile(r"(?:综合|最终|本系统|本报告)(?:投资)?(?:评级|建议)|\b(?:our|overall|final)\s+(?:rating|recommendation)\b", re.I)

# Transitions are rating-ownership propositions, even in future monitoring
# context. Their relation (not an exact header or sentence) supplies ownership.
_TRANSITION_LINK = re.compile(r"^\s*(?:→|⇒|⟶|->|=>|to|至|到|から)\s*$", re.I)
_TRANSITION_BEFORE = re.compile(
    r"(?:\b(?:upgrad(?:e|ed|ing)|downgrad(?:e|ed|ing)|rais(?:e|ed|ing)|lower(?:ed|ing)?)\b"
    r"[^。！？；;|]*\bto\s+|(?:上调|下调|调升|调降|调整|变更|変更|引き上げ|引き下げ)"
    r"(?:评级|評級|評価|判断)?\s*(?:为|至|到|成|へ|に)\s*)$", re.I
)
_TRANSITION_AFTER = re.compile(r"^\s*(?:へ|に)\s*(?:変更|転換|引き上げ|引き下げ)")
_TRANSITION_CONSEQUENCE = re.compile(
    r"^\s*(?:会|将|可能)(?:增加|降低|加大|减少)风险|"
    r"^\s*\b(?:would|could)\s+(?:increase|reduce|raise|lower)\s+risk\b", re.I
)
_HOLD_STANCE = re.compile(
    r"(?:值得|建议|推荐)[\s\"“”]*持有|持有(?:现有)?(?:仓位|头寸).{0,12}(?:最优|最佳)|"
    r"\b(?:worth\s+holding|recommend\s+holding|maintaining\s+(?:the\s+)?position\s+is\s+optimal)\b", re.I
)
_NAMED_SOURCE_REPORT = re.compile(
    r"(?:^|[：:]\s*)(?P<source>[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,4})\s+"
    r"(?:upgraded|downgraded|maintains?|raised|lowered)\b"
)
_SYSTEM_SOURCE = re.compile(
    r"\b(?:we|our|i|research|manager|analyst|trader|portfolio|system|report)\b", re.I
)


@dataclass(frozen=True)
class RatingClaim:
    start: int
    end: int
    text: str
    rating: str
    semantic_type: str = "INVESTMENT_RATING"
    from_rating: str | None = None


def _canonical_rating(value: str) -> str:
    return _CANONICAL.get(value, value.capitalize())


def _transition_match(text: str):
    """Extract a rating target and optional origin from their relation.

    No current/future exemption: specifying a target rating is distinct from
    describing an event that might require reassessment without a rating target.
    """
    values = list(_VALUES.finditer(text))
    for origin, target in zip(values, values[1:], strict=False):
        if _TRANSITION_LINK.fullmatch(text[origin.end():target.start()]):
            return target, _canonical_rating(origin[0])
    for target in values:
        if _TRANSITION_BEFORE.search(text[:target.start()]) or _TRANSITION_AFTER.search(text[target.end():]):
            # Here a rating event is the subject of a risk assessment, not a
            # prescribed target. Future target instructions have no such waiver.
            if _TRANSITION_CONSEQUENCE.search(text[target.end():]):
                continue
            origin = next((v for v in reversed(values) if v.end() < target.start()), None)
            return target, _canonical_rating(origin[0]) if origin else None
    return None


def _named_external_report(text: str) -> bool:
    """A named reporting source owns its rating fact, not the internal Agent.

    This is a subject/reporting-verb relation, not a broker-name allowlist.
    Internal role names and explicit first-person ownership cannot qualify.
    """
    match = _NAMED_SOURCE_REPORT.search(text.strip(" |"))
    return bool(match and not _SYSTEM_SOURCE.search(match["source"]))


def _recommendation_match(text: str):
    """Return an asserted directional recommendation, preserving polarity."""
    for match in _RECOMMENDATION.finditer(text):
        if _RECOMMENDATION_WITHHELD_BEFORE.search(text[:match.start()]):
            continue
        if _RECOMMENDATION_WITHHELD_AFTER.search(text[match.end():].lstrip(" |")):
            continue
        return _VALUES.search(text, match.start(), match.end())
    return None


def internal_rating_claims(text: str) -> list[RatingClaim]:
    """Find explicit internal rating assertions with original text offsets.

    Formatting, labelled table cells and a label on the preceding line are
    supported. A rating word in ordinary risk prose is not a recommendation.
    External attribution belongs to its clause/row; it cannot exempt a sibling
    internal recommendation in the same paragraph.
    """
    claims: list[RatingClaim] = []
    heading_context: list[tuple[int, bool]] = []
    pending_label = False
    pending_external = False
    offset = 0
    table_external = False
    table_rating = False
    table_headers: list[str] = []
    for line in text.splitlines(keepends=True):
        plain = re.sub(r"[*`_]", "", line).strip()
        heading = re.match(r"^(#{1,6})\s+(.+)", plain)
        if heading:
            level, title = len(heading[1]), heading[2].strip()
            while heading_context and heading_context[-1][0] >= level:
                heading_context.pop()
            external = bool(_EXTERNAL_HEADING.search(title))
            heading_context.append((level, external))
            plain = title
        external_context = any(external for _, external in heading_context)
        is_table = line.lstrip().startswith("|")
        if not is_table:
            table_external = False
            table_rating = False
            table_headers = []
        else:
            cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", plain.strip("|"))]
            if not table_headers:
                table_headers = cells
            table_rating = any(_LABEL_HEADING.fullmatch(cell) for cell in table_headers)
            # Attribution is a relation between a source column and its value,
            # not a flag inherited from an arbitrary earlier table row.
            table_external = any(
                _EXTERNAL_HEADING.search(header)
                and index < len(cells)
                and cells[index].casefold() not in {"", "—", "-", "n/a", "not provided"}
                and re.fullmatch(r"[:\-]+", cells[index]) is None
                for index, header in enumerate(table_headers)
            )
        # A row is one relational proposition, including cells with sentence
        # punctuation. Keep its identity whole through detection and pruning.
        unit_pattern = r"[^\n]+" if is_table else r"[^\n。！？；;]+[。！？；;]?"
        for unit in re.finditer(unit_pattern, line):
            cleaned = re.sub(r"[*`#_]", "", unit[0]).strip()
            cleaned = re.sub(r"^(?:[-+]\s+|\d+[.)、]\s*)", "", cleaned)
            match = _VALUES.search(cleaned)
            hold_stance = bool(_HOLD_STANCE.search(cleaned))
            if not match and not hold_stance:
                continue
            # Ownership includes transitions and evaluative holding advice,
            # not just labelled values and signal/status cells.
            labelled = _LABEL.search(cleaned)
            recommendation = _recommendation_match(cleaned)
            transition = _transition_match(cleaned)
            if _RECOMMENDATION.search(cleaned) and recommendation is None:
                continue
            our = bool(_OUR_RECOMMENDATION.search(cleaned))
            bare = bool(re.fullmatch(r"(?:buy|hold|sell|overweight|underweight|买入|持有|卖出|增持|减持)\s*(?:[（(][^）)]*[）)])?[。.!]?", cleaned, re.I))
            if not (labelled or our or pending_label or bare or table_rating or recommendation or transition or hold_stance):
                continue
            external = bool(_EXTERNAL.search(cleaned)) or _named_external_report(cleaned) or (
                (external_context or table_external or pending_external)
                and not _OWN_LABEL.search(cleaned)
            )
            if external and not our:
                continue
            # Explicit system recommendations take precedence over quoted
            # broker values; otherwise use the value belonging to the label.
            from_rating = None
            if transition and not (recommendation and our):
                match, from_rating = transition
            elif recommendation and our:
                match = recommendation
            elif labelled:
                match = _VALUES.search(cleaned, labelled.end())
                if not match:
                    continue
            elif recommendation:
                match = recommendation
            rating = "Hold" if hold_stance and not transition and not recommendation else _canonical_rating(match[0])
            claims.append(RatingClaim(
                offset + unit.start(), offset + unit.end(), unit[0], rating,
                "RATING_TRANSITION" if transition else (
                    "INVESTMENT_RECOMMENDATION" if recommendation or hold_stance else "INVESTMENT_RATING"
                ), from_rating,
            ))
        if plain:
            pending_label = bool(_LABEL_HEADING.fullmatch(plain.rstrip(":：")))
            pending_external = bool(_EXTERNAL_HEADING.search(plain)) if pending_label else False
        offset += len(line)
    return claims


def remove_internal_ratings(text: str) -> str:
    """Remove only complete recommendation propositions; retain outside facts."""
    for claim in reversed(internal_rating_claims(text)):
        end = claim.end
        # Leaving an empty line where a table row stood terminates the table
        # and can make structural cleanup discard otherwise valid siblings.
        if claim.text.lstrip().startswith("|") and text[end:end + 1] == "\n":
            end += 1
        text = text[:claim.start] + text[end:]
    return text


def artifact_rating_violations(text: str, portfolio_rating: str | None) -> list[RatingClaim]:
    """Check exact composed Markdown independently of Agent-field pruning.

    Only the deterministic top-level Portfolio wrapper owns an internal rating.
    Agent headings are nested below that wrapper by the existing composer.
    Even an identical rating in another section is a second internal authority.
    """
    boundaries = list(re.finditer(r"(?m)^##\s+(.+)$", text))
    violations: list[RatingClaim] = []
    for claim in internal_rating_claims(text):
        wrapper = next((m[1] for m in reversed(boundaries) if m.start() < claim.start), "")
        portfolio = bool(re.fullmatch(r"(?:[IVX]+\.\s*)?投资组合经理结论", wrapper))
        if not portfolio or claim.rating != portfolio_rating:
            violations.append(claim)
    return violations
