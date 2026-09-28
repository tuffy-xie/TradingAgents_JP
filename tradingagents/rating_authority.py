"""Identify report-owned recommendations, distinct from attributed rating facts.

This is a publication policy helper, not a rating selector. Portfolio owns the
final rating; other Agents' recommendations remain in the technical log. Source
attribution is evaluated per proposition (or table row), not per whole report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VALUES = re.compile(r"\b(overweight|underweight|buy|hold|sell)\b|买入|增持|持有|减持|卖出", re.I)
_CANONICAL = {"买入": "Buy", "增持": "Overweight", "持有": "Hold", "减持": "Underweight", "卖出": "Sell"}
_LABEL = re.compile(
    r"(?:评级|(?:投资|交易|最终|综合)?建议|推荐|rating|recommendation|"
    r"final\s+transaction\s+proposal)\s*[:：|\-—]", re.I
)
_LABEL_HEADING = re.compile(
    r"^(?:(?:综合|最终|投资|系统|本报告)\s*)?(?:评级|投资建议|建议|推荐)$|"
    r"^(?:(?:final|overall|investment)\s+)?(?:rating|recommendation)$|"
    r"^final\s+transaction\s+proposal$", re.I
)
_OUR_RECOMMENDATION = re.compile(
    r"(?:我们|本报告|本系统|本分析师|我)(?:的)?(?:最终)?(?:投资)?(?:评级|建议|推荐)|"
    r"\b(?:we\s+recommend|our\s+(?:rating|recommendation)|i\s+recommend)\b", re.I
)
_EXTERNAL = re.compile(
    r"券商|投行|分析师共识|分析师评级|分析师.{0,20}(?:给予|给出|维持|调升|调降|下调|上调)|"
    r"(?:位|名)分析师|评级分布|共识评级|\b(?:broker(?:age)?|consensus|"
    r"third[ -]party|according\s+to)\b|\banalysts?\b.{0,35}"
    r"\b(?:rate|rates|rated|maintain|maintains|recommend|recommendation|ratings?)\b", re.I
)
_EXTERNAL_HEADING = re.compile(
    r"券商|投行|分析师(?:共识|评级动态|评级分布)|共识|评级分布|"
    r"\b(?:broker(?:age)?|consensus|third[ -]party)\b", re.I
)
_OWN_LABEL = re.compile(r"(?:综合|最终|本系统|本报告)(?:投资)?(?:评级|建议)|\b(?:our|overall|final)\s+(?:rating|recommendation)\b", re.I)


@dataclass(frozen=True)
class RatingClaim:
    start: int
    end: int
    text: str
    rating: str


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
        elif not _VALUES.search(plain) and _EXTERNAL_HEADING.search(plain):
            table_external = True
        if is_table and not _VALUES.search(plain):
            table_rating = table_rating or any(
                _LABEL_HEADING.fullmatch(cell.strip()) for cell in plain.strip("|").split("|")
            )
        for unit in re.finditer(r"[^\n。！？；;]+[。！？；;]?", line):
            cleaned = re.sub(r"[*`#_]", "", unit[0]).strip()
            cleaned = re.sub(r"^(?:[-+]\s+|\d+[.)、]\s*)", "", cleaned)
            match = _VALUES.search(cleaned)
            if not match:
                continue
            # Require an assertion of rating ownership, not e.g. 'downgrade to
            # Hold would increase risk' or a passing mention of the word Buy.
            labelled = _LABEL.search(cleaned)
            our = bool(_OUR_RECOMMENDATION.search(cleaned))
            bare = bool(re.fullmatch(r"(?:buy|hold|sell|overweight|underweight|买入|持有|卖出|增持|减持)\s*(?:[（(][^）)]*[）)])?[。.!]?", cleaned, re.I))
            if not (labelled or our or pending_label or bare or table_rating):
                continue
            external = bool(_EXTERNAL.search(cleaned)) or (
                (external_context or table_external or pending_external)
                and not _OWN_LABEL.search(cleaned)
            )
            if external and not our:
                continue
            # Label values take precedence over earlier quoted words.
            if labelled:
                match = _VALUES.search(cleaned, labelled.end())
                if not match:
                    continue
            value = match[0]
            rating = _CANONICAL.get(value, value.capitalize())
            claims.append(RatingClaim(offset + unit.start(), offset + unit.end(), unit[0], rating))
        if plain:
            pending_label = bool(_LABEL_HEADING.fullmatch(plain.rstrip(":：")))
            pending_external = bool(_EXTERNAL_HEADING.search(plain)) if pending_label else False
        offset += len(line)
    return claims


def remove_internal_ratings(text: str) -> str:
    """Remove only complete recommendation propositions; retain outside facts."""
    for claim in reversed(internal_rating_claims(text)):
        text = text[:claim.start] + text[claim.end:]
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
