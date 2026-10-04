"""Identify report-owned recommendations, distinct from attributed rating facts.

This is a publication policy helper, not a rating selector. Portfolio owns the
final rating; other Agents' recommendations remain in the technical log. Source
attribution is evaluated per proposition (or table row), not per whole report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VALUES = re.compile(r"(?<![A-Za-z])(overweight|underweight|buy|hold|sell|bullish|bearish)(?![A-Za-z])|买入|增持|持有|减持|卖出|買い|売り|看涨|看跌|強気|弱気", re.I)
_CANONICAL = {"买入": "Buy", "增持": "Overweight", "持有": "Hold", "减持": "Underweight", "卖出": "Sell", "買い": "Buy", "売り": "Sell", "bullish": "Overweight", "bearish": "Underweight", "看涨": "Overweight", "看跌": "Underweight", "強気": "Overweight", "弱気": "Underweight"}
_OUTLOOK_VALUES = {"bullish", "bearish", "看涨", "看跌", "強気", "弱気"}
_LABEL = re.compile(
    r"(?:评级|综合评分|(?:投资|交易|最终|综合)?建议|推荐|rating|recommendation|"
    r"(?:最终|综合|投资|研究|交易)+(?:研究)?结论|投資判断|投資推奨|レーティング|推奨|final\s+transaction\s+proposal|"
    r"(?:final|investment|research)\s+conclusion)\s*[:：|\-—]", re.I
)
_RATING_ASSERTION_BEFORE = re.compile(
    r"(?:给出|给予|评为|定为|推荐为|建议(?:是|为))\s*$|"
    r"\b(?:recommend(?:s|ed)?|assign(?:s|ed)?|rate(?:s|d)?(?:\s+as)?)\s*$", re.I
)
_RATING_ASSERTION_AFTER = re.compile(r"^\s*(?:评级|評級|评价|rating\b)", re.I)
_LABEL_HEADING = re.compile(
    r"^(?:(?:综合|最终|投资|系统|本报告)\s*)?(?:评级|投资建议|建议|推荐|研究结论|投资结论)$|"
    r"^(?:投資判断|投資推奨|レーティング|推奨)$|"
    r"^(?:(?:final|overall|investment)\s+)?(?:rating|recommendation)$|"
    r"^final\s+transaction\s+proposal$|"
    r"^(?:(?:investment|trading)\s+)?signal$|^(?:投资|交易)?信号$|^(?:投資|取引)?シグナル$", re.I
)
_ADVICE_SURFACE = re.compile(
    r"^(?:[一二三四五六七八九十\d]+[、.)]\s*)?"
    r"(?:(?:核心|最终|最終|综合|綜合)?(?:结论|結論)(?:与|與|和|及)?)?"
    r"(?:投资|投資|交易)(?:建议|建議|推荐|推薦)$|"
    r"(?:(?:投资|投資|交易)(?:建议|建議|推荐|推薦)|"
    r"(?:仓位|倉位|持仓|持倉|组合敞口)(?:管理|策略)|"
    r"\b(?:investment|trading|portfolio)\s+(?:advice|recommendations?|management))$|"
    r"^(?:(?:final|investment|trading)\s+)?recommendations?$", re.I
)
# A report title's role survives suffixes (summary/risk notes/report/issuer).
# Corporate investment decisions and externally owned advice are not this role.
_INVESTMENT_SURFACE_ROLE = re.compile(
    r"(?:投资|投資|交易)(?:建议|建議|推荐|推薦|决策|決策)|"
    r"\b(?:investment|trading)\s+(?:advice|recommendations?|decisions?)\b", re.I
)
_CORPORATE_DECISION = re.compile(
    r"(?:公司|企业|企業|董事会|管理层|资本开支|产能|研发|并购).{0,12}(?:投资|投資)(?:决策|決策)|"
    r"\b(?:company|corporate|board|management|capex)\b.{0,24}\binvestment\s+decisions?\b", re.I
)
_DECISION_FRAME = re.compile(
    r"(?:本次|此次|本报告|我们(?:的)?|最终|最終)(?:投资|投資|交易)?(?:决策|決策)|"
    r"\b(?:our|this|final)\s+(?:investment\s+|trading\s+)?decision\b", re.I
)
_DIRECTIONAL_FRAME = re.compile(r"(?:看空|看跌|看多|看涨)(?:的)?立场|\b(?:bullish|bearish)\s+stance\b", re.I)
_OTHER_DECISION_OWNER = re.compile(
    r"(?:投资者|投資家|公司|企业|企業|董事会|管理层|客户|看涨方|看空方|多方|空方)|"
    r"\b(?:investors?|company|board|management|clients?|bull\s+case|bear\s+case)\b", re.I
)


def _advice_surface(title: str) -> bool:
    role = _INVESTMENT_SURFACE_ROLE.search(title)
    if role and (
        re.search(r"(?:不(?:提供|构成|发布)|无|没有|\b(?:no|without|not\s+providing))\s*$",
                  title[:role.start()], re.I)
        or re.match(r"\s*(?:的(?:限制|边界|定义|含义|风险)|\b(?:limitations?|definition|boundaries)\b)",
                    title[role.end():], re.I)
    ):
        # Discussing the limits/meaning of advice is not promising advice.
        # This exemption owns the title only, never a recommendation below it.
        return False
    return bool(_ADVICE_SURFACE.search(title) or (
        role and not _CORPORATE_DECISION.search(title)
    ))


def _research_framing(text: str) -> str | None:
    """Relabel internal decision agency, preserving the underlying analysis.

    An investor's independent decision, corporate decisions and attributed
    Bull/Bear/broker positions do not grant the report its own decision role.
    """
    replacements = []
    for match in sorted([*_DECISION_FRAME.finditer(text), *_DIRECTIONAL_FRAME.finditer(text)],
                        key=lambda item: item.start()):
        prefix = re.split(r"[，,：:；;。|]", text[:match.start()])[-1]
        if _OTHER_DECISION_OWNER.search(prefix) or _ADVICE_HISTORY.search(prefix):
            continue
        if match.re is _DIRECTIONAL_FRAME:
            negative = bool(re.search(r"看空|看跌|bearish", match[0], re.I))
            replacement = "下行风险判断" if negative else "上行潜力判断"
        else:
            replacement = "研究判断"
        replacements.append((match.start(), match.end(), replacement))
    result = text
    for start, end, replacement in reversed(replacements):
        result = result[:start] + replacement + result[end:]
    return result if replacements else None
# Recommendation ownership is not action authorization. A causal/evaluative
# reason for rejecting a trade is an investment stance; a bare ban or statement
# that execution is unavailable remains pure withholding.
_STANCE_ACTION = r"(?:追涨|追漲|追高|买入|买進|卖出|加仓|减仓|建仓|持有|\b(?:buy|sell|add|hold)\b)"
_REASONED_TRADE_STANCE = re.compile(
    rf"(?:因此|所以|故而|意味着|意味著|强到|弱到|高到|低到|→|⇒|=|\b(?:therefore|hence|thus)\b)"
    rf"\s*(?:不支持|不赞成|不贊成|反对|反對|否决|否決|\b(?:oppose|reject|does\s+not\s+support)\b)\s*{_STANCE_ACTION}|"
    rf"(?:反对|反對|不赞成|不贊成|\b(?:oppose|reject)\b)\s*{_STANCE_ACTION}", re.I
)
_STATUS_QUO_STANCE = re.compile(r"以不变应万变|以不變應萬變|维持现状|維持現狀|\b(?:maintain|keep)\s+(?:the\s+)?status\s+quo\b", re.I)
_STANCE_CONTEXT = re.compile(r"结论|結論|综合判断|綜合判斷|投资判断|投資判断|\b(?:conclusion|investment\s+stance)\b", re.I)
_OWN_ADVICE_DISCOURSE = re.compile(r"(?:本|此|这项|這項)(?:投资)?建议(?:基于|基於|依据|依據)|\bour\s+recommendation\s+is\s+based\b", re.I)
# A recommendation can be expressed as a short signal/status cell without a
# rating label. Match the direction's relation to recommendation semantics,
# not a direction word alone (e.g. 買い材料 is a research factor).
_DIRECTION = r"(?:\b(?:buy|sell|overweight|underweight)\b|买入|卖出|增持|减持|買い|売り)"
_RECOMMENDATION = re.compile(
    rf"{_DIRECTION}\s*(?:[のを]\s*)?(?:示唆|推奨|シグナル|信号|建议|推荐|\b(?:signal|recommendation)\b)|"
    rf"(?:建议|推荐|推奨)\s*(?:(?:投资者|持仓者|现有持仓者)\s*)?{_DIRECTION}|"
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
    r"券商|投行|証券会社|證券会社|証券|證券|アナリスト|分析师共识|分析师评级|分析师.{0,20}(?:给予|给出|维持|调升|调降|下调|上调|建议|推荐|认为)|"
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
    r"(?:值得|建议|推荐)[\s\"“”]*(?:(?:现有持仓)?投资者\s*)?(?:继续|保持|维持)?\s*持有|"
    r"持有(?:现有)?(?:仓位|头寸).{0,12}(?:最优|最佳)|"
    r"\b(?:worth\s+holding|recommend\s+(?:investors?\s+)?(?:continu(?:e|ing)\s+)?holding|"
    r"maintaining\s+(?:the\s+)?position\s+is\s+optimal)\b", re.I
)
# A named rating can be the subject of an evaluative predicate, rather than
# the value of a label. Ownership still applies, including future conditions.
# Direction-only technical outlooks are deliberately excluded by the caller.
_RATING_STANCE_PREDICATE = re.compile(
    r"^\s*(?:是|为|為)[^。！？；;|]{0,32}(?:合理|最优|最優|最佳|适当|適當)"
    r"[^。！？；;|]{0,24}(?:立场|立場|策略|选择|選擇|建议|建議)|"
    r"^\s*\bis\s+(?:the\s+)?(?:only\s+)?(?:appropriate|optimal|best|reasonable)"
    r"\b[^.!?;|]{0,32}\b(?:stance|strategy|choice|recommendation)\b|"
    r"^\s*(?:が|は)[^。！？；;|]{0,24}(?:最適|妥当|適切)"
    r"[^。！？；;|]{0,24}(?:判断|選択|方針|スタンス)", re.I
)
_NAMED_SOURCE_REPORT = re.compile(
    r"(?:^|[：:]\s*)(?P<source>[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,4})\s+"
    r"(?:upgraded|downgraded|maintains?|recommends?|raised|lowered|says?|reports?|argues?|notes?)\b"
)
_SYSTEM_SOURCE = re.compile(
    r"\b(?:we|our|i|research|manager|analyst|trader|portfolio|system|report)\b", re.I
)
# Selection advice has an investment object and a normative predicate, but
# need not name a rating. Attention to a business event is not stock selection.
_INSTRUMENT = re.compile(r"股票|该股|標的|标的|证券|銘柄|この株|\b(?:stock|shares?|security|investment\s+target)\b", re.I)
_INVESTOR_AUDIENCE = re.compile(r"投资者|投資家|\binvestors?\b", re.I)
_ISSUER_TARGET = re.compile(r"公司|企业|企業|\bcompany\b", re.I)
_SELECTION_OWNER = re.compile(r"本(?:系统|报告|分析师)|我们(?:认为|建议|推荐)|我(?:认为|建议|推荐)|"
                              r"\b(?:we\s+(?:think|believe|recommend)|our\s+(?:view|recommendation))\b", re.I)
_INVESTMENT_ADVICE = re.compile(
    r"(?:值得|适合|適合|优先|優先|建议|建議|推荐|推薦)(?:投资者)?\s*(?:配置|投资(?!者)|投資(?!家)|买入|買入)|"
    r"(?:投資|保有)(?:に適した|すべき)|"
    r"\b(?:worth\s+investing\s+in|suitable\s+for\s+investment|"
    r"(?:should|recommend(?:ed)?(?:\s+to)?)\s+(?:invest(?:ing)?\s+in|allocat(?:e|ing)\s+to))\b", re.I
)
_SELECTION_ADVICE = re.compile(
    r"(?:值得|应当|應當|应该|應該|建议|建議|推荐|推薦)[^。！？；;|]{0,16}(?:关注|關注|选择|選擇)|"
    r"(?:注目|選択)(?:すべき|に値する)|"
    r"\b(?:worth\s+(?:watching|considering)|(?:should|recommend)\s+(?:watch|consider|select))\b", re.I
)
_MONITORING_OBJECT = re.compile(
    r"(?:关注|關注|watch|consider)\s*(?:(?:该|这只|这家)?(?:股票|公司|标的|该股)(?:的|之)|"
    r"(?:the\s+)?(?:stock|company)'s\s+)|"
    r"(?:关注|關注|watch|consider)\s*(?:的|其|the\s+)?"
    r"(?:风险|風險|订单|訂單|公告|指标|指標|业绩|業績|财报|事件|估值|盈利|risk|orders?|announcements?|indicators?|earnings|events?|valuation)", re.I
)
_ISSUER_ATTRIBUTE_SUBJECT = re.compile(
    r"(?:股票|公司|标的|该股)(?:的|之)[^，,。！？；;|]{1,24}$|"
    r"\b(?:stock|company)'s\s+[^,.;!?|]{1,40}$", re.I
)
_BUSINESS_INVESTMENT = re.compile(r"(?:配置|投资|投資|invest(?:ing)?\s+in|allocat(?:e|ing)\s+to)\s*"
                                  r"(?:研发|研發|设备|設備|产能|產能|工厂|工廠|research|equipment|capacity|factories)", re.I)
_ADVICE_HISTORY = re.compile(r"历史上|歷史上|曾经|曾經|当时|當時|\b(?:historically|previously|used\s+to)\b", re.I)
_ADVICE_NEGATION = re.compile(r"(?:不|并非|並非|未|暂不|暫不|\b(?:not|never)\s*)$", re.I)


def _selection_recommendation(text: str) -> bool:
    for pattern in (_INVESTMENT_ADVICE, _SELECTION_ADVICE):
        for match in pattern.finditer(text):
            # Historical framing belongs to its proposition, not a sibling
            # current recommendation after a discourse/condition separator.
            prefix = re.split(r"[,，|]", text[:match.start()])[-1]
            if _ADVICE_HISTORY.search(prefix):
                continue
            if _ADVICE_NEGATION.search(text[:match.start()].rstrip()):
                continue
            if pattern is _INVESTMENT_ADVICE and not _BUSINESS_INVESTMENT.search(match[0] + text[match.end():]):
                return True
            selection_object = (_INSTRUMENT.search(text) or
                                (_INVESTOR_AUDIENCE.search(text) and _ISSUER_TARGET.search(text[match.end():])))
            if (pattern is _SELECTION_ADVICE and selection_object
                    and not _MONITORING_OBJECT.search(match[0] + text[match.end():])
                    and not _ISSUER_ATTRIBUTE_SUBJECT.search(prefix)):
                return True
    return False


@dataclass(frozen=True)
class RatingClaim:
    start: int
    end: int
    text: str
    rating: str | None
    semantic_type: str = "INVESTMENT_RATING"
    from_rating: str | None = None
    replacement: str | None = None


def _canonical_rating(value: str) -> str:
    return _CANONICAL.get(value.lower(), value.capitalize())


def normalize_technical_outlook_labels(text: str) -> str:
    """A qualitative technical outlook is not a formal investment rating.

    Relabel only a direction-only outlook. Explicit investment labels and
    formal Buy/Hold/Sell values still go through Portfolio ownership checks.
    """
    def outlook(match):
        values = list(_VALUES.finditer(match[2]))
        if values and all(value[0].lower() in _OUTLOOK_VALUES for value in values):
            return "技术面展望：" + match[2]
        return match[0]

    return re.sub(r"((?:整体|综合)评级)\s*[:：]([^\n。]+)", outlook, text, flags=re.I)


def attributed_rating_fact(text: str) -> bool:
    """Clause-local reporting agency; not a blanket exception for an Agent."""
    return bool((_VALUES.search(text) or _selection_recommendation(text)) and (_EXTERNAL.search(text) or _named_external_report(text))
                and not internal_rating_claims(text))


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
    """Find internal rating/selection recommendations with original offsets.

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
    stance_context: list[tuple[int, bool]] = []
    for line in text.splitlines(keepends=True):
        plain = re.sub(r"[*`_]", "", line).strip()
        heading = re.match(r"^(#{1,6})\s+(.+)", plain)
        if heading:
            level, title = len(heading[1]), heading[2].strip()
            while heading_context and heading_context[-1][0] >= level:
                heading_context.pop()
            external = bool(_EXTERNAL_HEADING.search(title))
            heading_context.append((level, external))
            while stance_context and stance_context[-1][0] >= level:
                stance_context.pop()
            stance_context.append((level, bool(_STANCE_CONTEXT.search(title) or _advice_surface(title))))
            plain = title
            if _advice_surface(title) and not any(ext for _, ext in heading_context):
                claims.append(RatingClaim(offset, offset + len(line.rstrip("\n")),
                                          line.rstrip("\n"), None, "RECOMMENDATION_SURFACE"))
        elif re.fullmatch(r"\s*\*\*[^\n]+\*\*\s*", line):
            # Bold numbered lead-ins are report surfaces too, not only # headings.
            title = re.sub(r"^[一二三四五六七八九十\d]+[、.)]\s*", "", plain)
            if _advice_surface(title) and not any(ext for _, ext in heading_context):
                claims.append(RatingClaim(offset, offset + len(line.rstrip("\n")),
                                          line.rstrip("\n"), None, "RECOMMENDATION_SURFACE"))
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
            # A title already has one stable surface identity; do not create
            # overlapping sentence claims for its decision-role vocabulary.
            if any(claim.start <= offset + unit.start() and claim.end >= offset + unit.end()
                   for claim in claims):
                continue
            cleaned = re.sub(r"[*`#_]", "", unit[0]).strip()
            cleaned = re.sub(r"^(?:[-+]\s+|\d+[.)、]\s*)", "", cleaned)
            match = _VALUES.search(cleaned)
            hold_stance = any(
                not _RECOMMENDATION_WITHHELD_BEFORE.search(cleaned[:stance.start()])
                and not _ADVICE_HISTORY.search(re.split(r"[,，|]", cleaned[:stance.start()])[-1])
                for stance in _HOLD_STANCE.finditer(cleaned)
            )
            selection = _selection_recommendation(cleaned)
            # Qualitative research conclusions may imply an investment stance
            # without naming Buy/Hold/Sell. Do not turn ordinary business facts
            # or isolated withholding statements into recommendations.
            status_quo = bool(_STATUS_QUO_STANCE.search(cleaned) and (
                any(active for _, active in stance_context)
                or re.search(r"最优|最優|最佳|策略|\b(?:optimal|best|strategy)\b", cleaned, re.I)
            ) and not _ISSUER_TARGET.search(cleaned))
            reasoned_stance = any(
                not _ADVICE_HISTORY.search(cleaned[:predicate.start()])
                and not (_ISSUER_TARGET.search(cleaned[:predicate.start()])
                         and re.match(r"\s*(?:其|非核心|核心|部分)?(?:资产|資產|业务|業務)", cleaned[predicate.end():]))
                for predicate in _REASONED_TRADE_STANCE.finditer(cleaned)
            )
            stance = bool(reasoned_stance or status_quo or _OWN_ADVICE_DISCOURSE.search(cleaned))
            framing = _research_framing(unit[0])
            if not match and not hold_stance and not selection and not stance and framing is None:
                continue
            # Ownership includes transitions and evaluative holding advice,
            # not just labelled values and signal/status cells.
            labelled = _LABEL.search(cleaned)
            asserted = next((value for value in _VALUES.finditer(cleaned)
                             if (value[0].lower() not in _OUTLOOK_VALUES and _RATING_ASSERTION_BEFORE.search(cleaned[:value.start()]))
                             or _RATING_ASSERTION_AFTER.search(cleaned[value.end():])
                             or (value[0].lower() not in _OUTLOOK_VALUES
                                 and _RATING_STANCE_PREDICATE.search(cleaned[value.end():]))), None)
            recommendation = _recommendation_match(cleaned)
            transition = _transition_match(cleaned)
            if _RECOMMENDATION.search(cleaned) and recommendation is None:
                continue
            our = bool(_OUR_RECOMMENDATION.search(cleaned) or (selection and _SELECTION_OWNER.search(cleaned)))
            bare = bool(re.fullmatch(r"(?:buy|hold|sell|overweight|underweight|买入|持有|卖出|增持|减持)\s*(?:[（(][^）)]*[）)])?[。.!]?", cleaned, re.I))
            if not (labelled or asserted or our or pending_label or bare or table_rating or recommendation or transition or hold_stance or selection or stance or framing is not None):
                continue
            external = bool(_EXTERNAL.search(cleaned)) or _named_external_report(cleaned) or (
                (external_context or table_external or pending_external)
                and not _OWN_LABEL.search(cleaned)
            )
            if external and not our:
                continue
            if framing is not None and not (asserted or recommendation or transition or hold_stance
                                           or selection or stance or (match and (labelled or bare or pending_label or table_rating))):
                claims.append(RatingClaim(offset + unit.start(), offset + unit.end(), unit[0],
                                          None, "RECOMMENDATION_FRAMING", replacement=framing))
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
                if not match and not selection:
                    continue
            elif recommendation:
                match = recommendation
            elif asserted:
                match = asserted
            rating = (None if stance and not (transition or recommendation or asserted or hold_stance) else
                      "Hold" if hold_stance and not transition and not recommendation else
                      None if selection and not (transition or recommendation or asserted) else _canonical_rating(match[0]))
            claims.append(RatingClaim(
                offset + unit.start(), offset + unit.end(), unit[0], rating,
                "RATING_TRANSITION" if transition else (
                    "INVESTMENT_RECOMMENDATION" if recommendation or hold_stance or selection or stance else "INVESTMENT_RATING"
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
        if claim.semantic_type == "RECOMMENDATION_FRAMING":
            text = text[:claim.start] + claim.replacement + text[claim.end:]
            continue
        if claim.semantic_type == "RECOMMENDATION_SURFACE":
            # The surface promises unowned trading advice even if pruning has
            # left only business analysis. Preserve that analysis and heading
            # structure, but make its research role explicit.
            marker = re.match(r"^\s*#{1,6}\s+(?:[一二三四五六七八九十\d]+[、.)]\s*)?", claim.text)
            bold = re.fullmatch(r"\s*\*\*([一二三四五六七八九十\d]+[、.)]\s*)?.+\*\*\s*", claim.text)
            replacement = (marker[0] if marker else "") + "研究分析"
            if bold:
                replacement = "**" + (bold[1] or "") + "研究分析**"
            text = text[:claim.start] + replacement + text[claim.end:]
            continue
        end = claim.end
        # Leaving an empty line where a table row stood terminates the table
        # and can make structural cleanup discard otherwise valid siblings.
        if claim.text.lstrip().startswith("|") and text[end:end + 1] == "\n":
            end += 1
        # Preserve the enclosing emphasis when pruning its opening sentence,
        # rather than globally stripping legitimate asterisks/US footnotes.
        opener = re.match(r"^([*_]{1,2})(?!\s)", claim.text)
        tail = text[end:].split("\n", 1)[0]
        keep_opener = (opener[1] if opener and tail.rstrip().endswith(opener[1])
                       and tail.strip() != opener[1] and not claim.text.endswith(opener[1]) else "")
        text = text[:claim.start] + keep_opener + text[end:]
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
        if not portfolio or (claim.rating is not None and claim.rating != portfolio_rating):
            violations.append(claim)
    return violations
