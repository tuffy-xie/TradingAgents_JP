"""Small, JP-only guardrail for agent text derived from run-level evidence.

This is deliberately a post-generation *downgrade* rather than a second
agent, provider, or LangGraph branch.  It prevents a clearly unsupported
precise claim from silently flowing into the next debate stage, while leaving
US behaviour untouched.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from tradingagents.agents.market_authority import canonical_market_authority

# A numeric token may be followed immediately by a display suffix such as
# ``x`` or ``pt``.  It may not, however, begin in the middle of an identifier
# such as ``FY2027``.  The old look-behind allowed the engine to restart at the
# second digit (``FY2`` + ``027``), which produced corrupt output such as
# ``FY2DATA_UNAVAILABLE``.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d[\d,]*(?:\.\d+)?%?")
_HEADING_ORDINAL = re.compile(
    r"^(?P<heading>#{1,6}\s+)"
    r"(?P<ordinal>(?:\d+(?:\.\d+)?[.)]?|[一二三四五六七八九十百千万]+[、.]|"
    r"[IVXLCDM]+[.)])\s+)(?P<body>.*)$",
    re.I,
)
_CURRENT_PRICE = re.compile(r"(?:current\s+price|spot\s+price|当前(?:股)?价|现价|株価)", re.I)
_GUIDANCE = re.compile(r"(?:company\s+guidance|guidance|公司指引|业绩指引)", re.I)
_CONSENSUS = re.compile(r"(?:analyst\s+(?:consensus|estimate)|consensus|分析师(?:一致预期|预测)|市场预期)", re.I)
_VENDOR_FORWARD = re.compile(r"(?:vendor\s+forward|forward\s+(?:eps|estimate)|供应商远期|vendor预期)", re.I)
_CURRENT_WORD = re.compile(r"(?:current|latest|today|目前|当前|最新)", re.I)
_FACT_LABEL = re.compile(r"(?:verified\s+fact|official\s+fact|官方事实|已验证事实)", re.I)
_NON_FACT_SOURCE = re.compile(
    r"(?:yahoo[^\n]*?(?:board|掲示板)|community|sentiment|社区|情绪|analyst\s+(?:consensus|estimate)|分析师(?:一致预期|预测))",
    re.I,
)
_PERIOD_MIX = re.compile(
    r"(?:\bFY\b(?:(?![。!?\n]).)*(?:\bQ[1-4]\b|\bTTM\b)|(?:\bQ[1-4]\b|\bTTM\b)(?:(?![。!?\n]).)*\bFY\b)",
    re.I,
)
_UNAVAILABLE_REDDIT = re.compile(
    r"(?:reddit[^\n。！？;]*(?:0\s*(?:posts?|mentions?)|zero\s+(?:posts?|mentions?)|"
    r"no\s+(?:posts?|mentions?|attention)|low\s+attention|lack\s+of\s+fomo|"
    r"无人关注|零提及|低热度|散户未进场)|"
    r"(?:0\s*(?:posts?|mentions?)|zero\s+(?:posts?|mentions?))[^\n。！？;]*reddit)",
    re.I,
)
_UNVERIFIED_MARKET = re.compile(
    r"(?:price|行情|价格|价位|technical|indicator|技术指标|ATR)[^\n。！？;]*"
    r"(?:unverified|not independently verified|无法验证|没有独立工具验证|缺少上游(?:数据|证据)?(?:支持|验证))",
    re.I,
)
_UNVERIFIED_NEWS = re.compile(
    r"(?:news|FRED|Yahoo|Polymarket|新闻|宏观)[^\n。！？;]*(?:unverified|无法验证|没有独立验证)",
    re.I,
)
_SHORT_ABSENCE_OVERCLAIM = re.compile(
    r"(?:no\s+(?:reportable\s+)?short(?:s| interest)?|没有(?:机构)?做空|无空头|"
    r"空头(?:全部)?(?:死光|出清)|(?:没有|未见)(?:融券[/／])?做空压力(?:累积)?|"
    r"(?:无|没有|缺乏)(?:明显|任何)?(?:轧空|軋空|short[ -]?squeeze)(?:行情|基础|條件|条件|风险|風險)?)",
    re.I,
)
_SHORT_PRESSURE_OVERCLAIM = re.compile(
    r"(?:short\s+pressure\s+(?:is\s+)?(?:extremely|very)?\s*low|"
    r"做空压力(?:极低|很低|有限)|空[头頭]压力(?:极低|很低|有限)|"
    r"空[头頭](?:部位)?(?:已)?(?:大幅|大规模|大規模|幾乎|几乎|全部|全數|全数)?(?:回补|回補|出清)|"
    r"(?:卖压|賣壓)(?:明显|明顯|大幅)?(?:减轻|減輕))",
    re.I,
)
_HISTORY_AS_SIGNAL = re.compile(
    r"(?:last\s+(?:trade|time)|previous\s+(?:trade|outcome)|上次|此前交易)[^\n。！？;]*(?:loss|亏损|bearish|看空|hold|观望|sell|卖出)",
    re.I,
)
_CURRENT_QUARTER_CONVICTION = re.compile(
    r"(?:latest|current|最新|当前)[^\n。！？;]*(?:quarter|季度)[^\n。！？;]*(?:fully|comprehensively|strongly|明确|全面|强劲|爆发|确认)",
    re.I,
)
_CURRENT_FINANCIAL_PERIOD = re.compile(
    r"(?:latest|current|最新|当前|當前)[^\n。！？;|]{0,20}?(?:quarter|季度|四半期)", re.I
)
_FINANCIAL_RESULT_METRIC = re.compile(
    r"盈利(?:能力)?|利润|利潤|营收|營收|收入|毛利|净利|淨利|每股收益|"
    r"売上|利益|収益|\b(?:earnings|profit(?:ability)?|revenue|margin|EPS)\b", re.I
)
_FINANCIAL_RESULT_PREDICATE = re.compile(
    r"回升|回落|增长|增長|下降|上升|改善|恶化|惡化|增加|减少|減少|稳健|穩健|强劲|強勁|"
    r"(?:増加|減少|改善|回復|上昇|低下)|"
    r"\b(?:improv(?:ed|ing|ement)|recover(?:ed|y)|increas(?:ed|ing)|decreas(?:ed|ing)|"
    r"grew|growth|declin(?:ed|ing)|strong|weak|rose|fell)\b", re.I
)
_FINANCIAL_PROJECTION = re.compile(
    r"预期|預期|预测|預測|预计|預計|有望|可能|未来|未來|潜力|潛力|如果|若|見込|予想|将来|"
    r"\b(?:expected|forecast|estimate|potential|could|may|might|future|if)\b", re.I
)
_FINANCIAL_ASSERTION_WITHHELD = re.compile(
    r"(?:不能|无法|無法|尚未|未能)[^。！？；;]{0,12}(?:确认|確認|证明|證明|验证|驗證|声称|断言)|"
    r"(?:仍|尚)?(?:待|未获|未獲)(?:确认|確認|验证|驗證)|"
    r"\b(?:cannot|can't|unable\s+to)\s+(?:verify|confirm|conclude|assert)\b", re.I
)
# Undated claims of *realized change* default to the current issuer result,
# unlike static business quality or an expressly dated historical observation.
# A qualitative heading can assert the result after its numeric support was
# removed. Its truth/realization predicate needs the same existing Actual gate.
_REALIZED_FINANCIAL_CHANGE = re.compile(
    r"(?:业绩|業績|利润率|利潤率|利益率|盈利|利润|earnings|profit|margin)"
    r"[^，,。！？；;|\n]{0,24}?(?:好转|好轉|加速|扩张|擴張|回升|增長|增长|改善|"
    r"増加|回復|拡大|悪化|転換|转变|轉變|证据[^。！？；;|]{0,8}压倒|growth|improved|accelerated|expanded|recovered|grew)", re.I
)
_REALIZED_FINANCIAL_ASSERTION = re.compile(
    r"真实|真實|实际|實際|已经|已經|已|实现|實現|属实|兑现|兌現|压倒|壓倒|"
    r"が確認|実現|事実|明白|明确|明確|前提[^。！？；;]{0,12}崩れ|\b(?:has|have|realized|actual|confirmed)\b", re.I
)
_FINANCIAL_CHANGE_REFERENCE = re.compile(
    r"(?:这(?:一|种)|這(?:一|種)|该|該|此)(?:业绩|業績|盈利)?(?:改善|增长|增長|加速|回升)|"
    r"\b(?:this|that)\s+(?:improvement|growth|recovery|acceleration)\b|こうした(?:改善|成長)|(?:これ|それ)は", re.I
)
# Economic regimes are distinct source events. In particular, the complement
# of a recession market is *not* a soft-landing market, and another country's
# probability cannot support the same numeric assertion. These concept aliases
# bind event identity; they do not blacklist prose or remove macro facts.
_ECONOMIC_REGIMES = {
    "soft_landing": re.compile(r"软着陆|軟着陸|ソフトランディング|\bsoft[ -]landing\b", re.I),
    "hard_landing": re.compile(r"硬着陆|硬着陸|ハードランディング|\bhard[ -]landing\b", re.I),
    "recession": re.compile(r"衰退|リセッション|\brecession\b", re.I),
    "stagflation": re.compile(r"滞胀|滯脹|スタグフレーション|\bstagflation\b", re.I),
    "deflation": re.compile(r"通缩|通縮|デフレ|\bdeflation\b", re.I),
}
_EVENT_JURISDICTIONS = {
    "US": re.compile(r"美国|美國|米国|(?<![A-Za-z])(?:US|U\.S\.|United States)(?![A-Za-z])", re.I),
    "JP": re.compile(r"日本|\bJapan\b", re.I),
    "UK": re.compile(r"英国|英國|(?<![A-Za-z])(?:UK|U\.K\.|United Kingdom)(?![A-Za-z])", re.I),
    "CN": re.compile(r"中国|中國|\bChina\b", re.I),
    "EU": re.compile(r"欧元区|歐元區|ユーロ圏|\b(?:Eurozone|Euro area)\b", re.I),
}
_EVENT_PROBABILITY = re.compile(
    r"(?:概率|機率|確率|\b(?:probability|chance|odds)\b)[^。！？；;|%]{0,20}?"
    r"(\d+(?:\.\d+)?)\s*%", re.I
)
_QUALITATIVE_EVENT_ODDS = re.compile(
    r"(?:概率|機率|確率)[^。！？；;|]{0,12}(?:高|低|大|小)|"
    r"\b(?:probability|chance|odds)\b[^.;!?|]{0,20}\b(?:high|low|likely|unlikely)\b", re.I
)
_ECONOMIC_STATE = re.compile(
    r"(?:经济|經濟|経済|景气|景氣)[^。！？；;|]{0,12}(?:稳健|穩健|强劲|強勁|复苏|復甦|增长|增長|成長)|"
    r"\beconom(?:y|ic\s+conditions)\b[^.;!?|]{0,20}\b(?:robust|strong|healthy|recovering)\b", re.I
)
_SOURCE_EVENT_PROBABILITY = re.compile(
    r"\*\*(?P<question>[^\n]+?)\*\*\s*[—-]\s*(?P<outcome>Yes|No)\s+"
    r"(?P<probability>\d+(?:\.\d+)?)%", re.I
)
_REGIME_CERTAINTY = re.compile(
    r"(?:共识|共識|コンセンサス|\bconsensus\b)[^。！？；;|]{0,16}(?:稳固|穩固|确定|確定|确立|確立|強固|solid|firm|confirmed|established)|"
    r"(?:已经|已經|已|すでに)[^。！？；;|]{0,12}(?:确认|確認|实现|實現)|"
    r"(?:经济|經濟)[^。！？；;|]{0,12}(?:未陷入|没有陷入|已避免)|"
    r"(?:反映|表明|证明|證明)[^。！？；;|]{0,40}(?:转变|轉變|改变|改變|转折|轉折)|"
    r"(?:告别|告別|走出|结束|結束)[^。！？；;|]{0,12}(?:通缩|通縮|デフレ|\bdeflation\b)|"
    r"\b(?:is|has\s+been)\s+(?:now\s+)?(?:confirmed|achieved|established)\b|"
    r"\b(?:has|have)\s+(?:avoided|escaped)\b", re.I
)
_REGIME_MODAL = re.compile(r"可能|有望|或许|或許|预计|預計|尚未|未能|不能|无法|無法|若|如果|\b(?:may|might|could|expected|if|not|cannot)\b", re.I)
_REGIME_ATTRIBUTION = re.compile(r"(?:报道|報道|报告|報告|称|指出|表示|による|と報じ|と述べ)|"
                                  r"\b(?:reports?|says?|according\s+to)\b", re.I)

# Event odds and realized outcomes are distinct propositions. These roles
# describe the asserted object/predicate, not positive/negative sentiment.
_MACRO_PREMISE = re.compile(
    r"衰退|リセッション|\brecession\b|央行|日央行|聯準會|美联储|美聯儲|"
    r"\b(?:BOJ|Fed|central\s+bank)\b", re.I
)
_DEMAND_OUTCOME = re.compile(
    r"(?:需求|需要|\bdemand\b)[^。！？；;|]{0,24}(?:保障|保证|保證|稳健|穩健|正面|有利|底堅)|"
    r"(?:保障|保证|保證)[^。！？；;|]{0,24}(?:需求|需要)|"
    r"\b(?:guarantees?|ensures?|supports?)\b[^.;!?|]{0,35}\bdemand\b|"
    r"\bdemand\b[^.;!?|]{0,24}\b(?:secured|guaranteed|strong|positive)\b", re.I
)
_MONETARY_OUTCOME = re.compile(
    r"(?:日本|市场|市場|金融)(?:的)?(?:流动性|流動性)[^。！？；;|]{0,18}(?:无|無|没有|沒有)(?:收紧|收緊)压力|"
    r"(?:金融|融资|融資|货币|貨幣)条件[^。！？；;|]{0,18}(?:宽松|寬鬆|收紧|收緊|改善)|"
    r"(?:金融|融資|貨幣)條件[^。！？；;|]{0,18}(?:寬鬆|收緊|改善)|"
    r"(?:套利交易|套息交易)[^。！？；;|]{0,24}(?:风险|風險)[^。！？；;|]{0,16}(?:降低|減少|减少|减轻|減輕)|"
    r"\b(?:financial|financing|monetary)\s+conditions\b[^.;!?|]{0,28}\b(?:loose|easy|tight|eased|improved)\b|"
    r"\bcarry\s+trade\b[^.;!?|]{0,28}\brisk\b[^.;!?|]{0,24}\b(?:decreased|declined|reduced)\b", re.I
)
_POLICY_ODDS_CHANGE = re.compile(
    r"(?:加息|降息|利率)[^。！？；;|]{0,16}(?:概率|機率|確率)[^。！？；;|]{0,16}(?:下降|上升|降低|增加)|"
    r"(?:收紧|收緊|宽松|寬鬆)[^。！？；;|]{0,16}(?:预期|預期)[^。！？；;|]{0,12}(?:降温|降溫|升温|升溫)|"
    r"\brate\s+(?:hike|cut)\s+(?:probability|odds)\b[^.;!?|]{0,20}\b(?:declined|increased|decreased)\b", re.I
)
_PROBABILITY_DERIVED_ASSET_STATE = re.compile(
    r"股价(?:正向|负向|利好|利空)|股價(?:正向|負向)|"
    r"(?:低|降低|减少|減少)[^。！？；;|]{0,6}系统性风险|"
    r"系统性风险[^。！？；;|]{0,8}(?:低|降低|减少)|"
    r"(?:市场|市場|预期|預期)[^。！？；;|]{0,14}(?:转向|轉向|偏向)[^。！？；;|]{0,8}(?:鹰派|鷹派|鸽派|鴿派)|"
    r"\b(?:share.price|stock.price)\s+(?:positive|negative)|"
    r"\b(?:low|lower)\s+systemic\s+risk\b", re.I
)


def _evidence_statement_key(text: str) -> str:
    return re.sub(r"[\s*`\"“”]", "", text).casefold().strip("。.")


def _independent_macro_statement(state: Mapping[str, Any], assertion: str) -> bool:
    """Require the asserted outcome itself, not odds or Agent inference.

    Exact proposition correspondence is intentionally fail-closed: unrelated
    countries, issuers, periods and a matching number cannot act as witnesses.
    """
    key = _evidence_statement_key(assertion)
    return any(
        isinstance(item, Mapping)
        and item.get("source") != "get_prediction_markets"
        and item.get("verification_status") in {"VERIFIED_TOOL_OUTPUT", "VERIFIED_SOURCE"}
        and item.get("allowed_for_current_decision") is not False
        and item.get("claim_type") != "INFERENCE"
        and any(key == _evidence_statement_key(statement.strip(" >-#"))
                for statement in re.split(r"[\n。！？;]", str(item.get("value") or "")) if statement.strip())
        for item in state.get("evidence_registry") or []
    )


def _outcome_is_hypothetical(clause: str, start: int, end: int) -> bool:
    # Modal scope belongs to the outcome's cell/clause. A sibling possibility
    # must not waive an asserted state after "but" or another table cell.
    prefix = clause[:start]
    left = max(prefix.rfind("，"), prefix.rfind(","), prefix.rfind("|")) + 1
    local = clause[left:end]
    local = re.split(r"但(?:是)?|然而|\bbut\b", local, flags=re.I)[-1]
    if _REGIME_MODAL.search(local):
        return True
    antecedent = re.sub(r"^[\s>*`|]+", "", clause)
    return bool(re.match(r"(?:若|如果|假如|\bif\b)", antecedent, re.I)
                and not re.search(r"但(?:是)?|然而|\bbut\b", prefix, re.I))


def _bound_policy_odds_change(state: Mapping[str, Any], clause: str) -> bool:
    """A direct weekly odds change needs the same bank, meeting and rate step.

    This does not authorize any carry-risk, demand or financial-state outcome.
    An annual path cannot borrow a change in one specific meeting's odds.
    """
    month = re.search(r"(?<!\d)(1[0-2]|[1-9])月", clause)
    months = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
    if month:
        month_name = months[int(month[1]) - 1]
    else:
        month_name = next((m for m in months if re.search(rf"\b{m}\b", clause, re.I)), None)
    years = set(re.findall(r"(?<!\d)20\d{2}(?!\d)", clause))
    step = re.search(r"(?<!\d)(\d+)\s*(?:bps|bp|基点|基點)", clause, re.I)
    change = re.search(r"([-+]?\d+(?:\.\d+)?)\s*(?:pp|个百分点|個百分點)", clause, re.I)
    bank = next((key for key, pattern in {
        "JP": re.compile(r"日本央行|日央行|\b(?:BOJ|Bank of Japan)\b", re.I),
        "US": re.compile(r"美联储|美聯儲|聯準會|\b(?:Fed|Federal Reserve)\b", re.I),
    }.items() if pattern.search(clause)), None)
    down = bool(re.search(r"下降|降低|\b(?:declined|decreased)\b", clause, re.I))
    rate_up = bool(re.search(r"加息|\b(?:hike|increase)\b", clause, re.I))
    if not (month_name and years and step and change and bank
            and re.search(r"本周|本週|一周|一週|\b(?:this\s+week|1-week|weekly)\b", clause, re.I)):
        return False
    for item in state.get("evidence_registry") or []:
        if (not isinstance(item, Mapping) or item.get("source") != "get_prediction_markets"
                or item.get("verification_status") != "VERIFIED_TOOL_OUTPUT"
                or item.get("allowed_for_current_decision") is False):
            continue
        for line in str(item.get("value") or "").splitlines():
            event = _SOURCE_EVENT_PROBABILITY.search(line)
            delta = re.search(r"1-week\s+([-+]?\d+(?:\.\d+)?)pp", line)
            if not event or event["outcome"].casefold() != "yes" or not delta:
                continue
            question = event["question"]
            if (_EVENT_JURISDICTIONS[bank].search(question)
                    and re.search(rf"\b{month_name}\b", question, re.I)
                    and years.issubset(set(re.findall(r"20\d{2}", question)))
                    and re.search(rf"(?<!\d){step[1]}\s+bps\b", question, re.I)
                    and bool(re.search(r"increases?|hikes?", question, re.I)) == rate_up
                    and (Decimal(delta[1]) < 0) == down
                    and abs(Decimal(delta[1])) == abs(Decimal(change[1]))):
                return True
    return False


def _unsupported_macro_outcome(state: Mapping[str, Any], clause: str) -> bool:
    """Probabilities cannot establish demand, carry risk or financial regimes."""
    patterns = [_MONETARY_OUTCOME]
    if _MACRO_PREMISE.search(clause):
        patterns.extend((_DEMAND_OUTCOME, _POLICY_ODDS_CHANGE, _ECONOMIC_STATE,
                         _PROBABILITY_DERIVED_ASSET_STATE))
    for pattern in patterns:
        for outcome in pattern.finditer(clause):
            if _outcome_is_hypothetical(clause, outcome.start(), outcome.end()):
                continue
            if pattern is _POLICY_ODDS_CHANGE and _bound_policy_odds_change(state, clause):
                continue
            if not _independent_macro_statement(state, clause):
                return True
    return False


def _probability_row_outcomes(state: Mapping[str, Any], clause: str) -> list[int]:
    """An event/odds cell does not witness a sibling regime/result cell.

    Keep the whole row as Audit identity. Only the unbound outcome cell is
    replaced, so independent source probabilities remain visible. Modality
    belongs to the outcome cell, not another sibling's confidence wording.
    """
    if not clause.strip().startswith("|") or not re.search(r"\d+(?:\.\d+)?%", clause):
        return []
    cells = [cell.strip() for cell in clause.strip().strip("|").split("|")]
    event_columns = [i for i, cell in enumerate(cells)
                     if any(pattern.search(cell) for pattern in _ECONOMIC_REGIMES.values())]
    if not event_columns:
        return []
    event_column = event_columns[0]
    return [i for i, cell in enumerate(cells)
            if i != event_column and not _REGIME_MODAL.search(cell)
            and (any(pattern.search(cell) for pattern in _ECONOMIC_REGIMES.values())
                 or _ECONOMIC_STATE.search(cell) or _DEMAND_OUTCOME.search(cell)
                 or _MONETARY_OUTCOME.search(cell) or _PROBABILITY_DERIVED_ASSET_STATE.search(cell))
            and not _independent_macro_statement(state, clause)]


def _source_regime_odds_bound(state: Mapping[str, Any], clause: str, regime: str,
                             probability: float | None = None) -> bool:
    """Bind both numeric and qualitative odds to the same outcome event.

    A qualitative likelihood still needs its own source event; a recession
    question cannot stand in for a soft-landing question. No qualitative odds
    threshold is invented here; numeric claims additionally require exact odds.
    """
    jurisdiction = next((key for key, pattern in _EVENT_JURISDICTIONS.items()
                         if pattern.search(clause)), None)
    if not jurisdiction:
        return False
    years = set(re.findall(r"(?<!\d)20\d{2}(?!\d)", clause))
    if not years:
        as_of = str(state.get("trade_date") or "")
        years = {as_of[:4]} if re.match(r"20\d{2}-", as_of) else set()
    regime_match = _ECONOMIC_REGIMES[regime].search(clause)
    negative = bool(re.search(r"(?:不(?:会|會)?|非|\b(?:no|not|without)\s+(?:a\s+)?)$",
                              clause[:regime_match.start()].rstrip(), re.I))
    for evidence in state.get("evidence_registry") or []:
        if (not isinstance(evidence, Mapping) or evidence.get("source") != "get_prediction_markets"
                or evidence.get("verification_status") != "VERIFIED_TOOL_OUTPUT"
                or evidence.get("allowed_for_current_decision") is False):
            continue
        for event in _SOURCE_EVENT_PROBABILITY.finditer(str(evidence.get("value") or "")):
            source_probability = float(event["probability"])
            if (event["outcome"].casefold() == "no") != negative:
                source_probability = 100 - source_probability
            if (_ECONOMIC_REGIMES[regime].search(event["question"])
                    and _EVENT_JURISDICTIONS[jurisdiction].search(event["question"])
                    and (not years or years.issubset(set(re.findall(r"(?<!\d)20\d{2}(?!\d)", event["question"]))))
                    and (probability is None or source_probability == probability)):
                return True
    return False


def _row_event_probability(clause: str) -> tuple[str, float] | None:
    """Recover the event/value relation, not a number from an unrelated cell."""
    if not clause.strip().startswith("|"):
        return None
    cells = [cell.strip(" *") for cell in clause.strip().strip("|").split("|")]
    for index, cell in enumerate(cells):
        probability = re.match(r"^(\d+(?:\.\d+)?)%", cell)
        subject = " ".join(cells[:index])
        if probability and any(pattern.search(subject) for pattern in _ECONOMIC_REGIMES.values()):
            return subject, float(probability[1])
    return None


def _unsupported_regime_certainty(state: Mapping[str, Any], clause: str) -> bool:
    """A low probability of one event cannot prove a different regime/consensus.

    Research possibilities are not confirmations. Attributed confirmations
    require a verified source assertion, not a prediction-market question.
    """
    certainty = _REGIME_CERTAINTY.search(clause)
    regimes = [(match.start(), pattern) for pattern in _ECONOMIC_REGIMES.values()
               for match in pattern.finditer(clause)]
    relation = re.search(r"→|⇒|=>|->", clause)
    # A regime used as the antecedent of an unqualified implication is an
    # asserted premise too. A prediction question/odds cannot establish it.
    premise = bool(relation and any(position < relation.start() for position, _ in regimes))
    if not regimes or not (certainty or premise) or _REGIME_MODAL.search(clause):
        return False
    # An editorial heading may name recession odds then assert a different
    # regime. Bind certainty to its nearest subject, not any regime in a line.
    assertion_start = certainty.start() if certainty else relation.start()
    preceding = [item for item in regimes if item[0] < assertion_start]
    _, asserted = max(preceding, key=lambda item: item[0]) if preceding else min(regimes, key=lambda item: item[0])
    if _REGIME_ATTRIBUTION.search(clause):
        for evidence in state.get("evidence_registry") or []:
            if not isinstance(evidence, Mapping) or evidence.get("verification_status") != "VERIFIED_TOOL_OUTPUT":
                continue
            # Prediction questions and numerical odds are not confirmed states.
            if evidence.get("source") == "get_prediction_markets":
                continue
            for statement in re.split(r"[\n。！？；;]", str(evidence.get("value") or "")):
                if (_REGIME_CERTAINTY.search(statement) and not _REGIME_MODAL.search(statement)
                        and asserted.search(statement)
                        and all(not pattern.search(clause) or pattern.search(statement)
                                for pattern in _EVENT_JURISDICTIONS.values())):
                    return False
    return True


def _unsupported_market_pricing(state: Mapping[str, Any], clause: str) -> bool:
    """Event odds are not proof that asset markets fully priced a policy path."""
    assertion = re.search(
        r"(?:市场|市場)[^。；;\n]{0,100}(?:充分|完全)[^。；;\n]{0,8}(?:定价|定價)|"
        r"\bmarkets?\b[^.;\n]{0,100}\b(?:fully|completely)\s+priced\s+in\b", clause, re.I,
    )
    if not assertion or _REGIME_MODAL.search(clause):
        return False
    # Only an actual verified assertion can witness this proposition. Numbers
    # or a prediction-market question (even matching odds) cannot do so.
    normalized = re.sub(r"[\s*`\"“”]", "", clause).casefold().strip("。.")
    return not any(
        isinstance(evidence, Mapping)
        and evidence.get("source") != "get_prediction_markets"
        and evidence.get("verification_status") == "VERIFIED_TOOL_OUTPUT"
        and normalized in re.sub(r"[\s*`\"“”]", "", str(evidence.get("value") or "")).casefold()
        for evidence in state.get("evidence_registry") or []
    )


def probability_event_gate_violation(state: Mapping[str, Any], clause: str,
                                     *, jurisdiction_context: str | None = None) -> bool:
    """Ground regime probabilities and confirmations in their own source event.

    This is deliberately narrower than arbitrary macro-language translation:
    only named economic-regime assertions are classified. Unconfirmed research
    possibilities and other properly supported macro numbers remain unchanged.
    """
    if _probability_row_outcomes(state, clause):
        return True
    row_event = _row_event_probability(clause)
    if row_event:
        subject, value = row_event
        if jurisdiction_context and not any(p.search(subject) for p in _EVENT_JURISDICTIONS.values()):
            subject = jurisdiction_context + " " + subject
        regime = next(key for key, pattern in _ECONOMIC_REGIMES.items() if pattern.search(subject))
        if not _source_regime_odds_bound(state, subject, regime, value):
            return True
    probability = _EVENT_PROBABILITY.search(clause)
    regime = next((key for key, pattern in _ECONOMIC_REGIMES.items() if pattern.search(clause)), None)
    if (_unsupported_regime_certainty(state, clause) or _unsupported_market_pricing(state, clause)
            or _unsupported_macro_outcome(state, clause)):
        return True
    if not regime:
        return False
    binding_clause = clause
    if jurisdiction_context and not any(p.search(clause) for p in _EVENT_JURISDICTIONS.values()):
        binding_clause = jurisdiction_context + " " + clause
    if not probability:
        return bool(_QUALITATIVE_EVENT_ODDS.search(clause)
                    and not _source_regime_odds_bound(state, binding_clause, regime)
                    and not _independent_macro_statement(state, clause))
    return not _source_regime_odds_bound(state, binding_clause, regime, float(probability[1]))


def current_financial_gate_violation(state: Mapping[str, Any], clause: str) -> bool:
    """Undated current-quarter actuals need the existing official Actual gate.

    Qualitative metric assertions need the same authority as numeric ones.
    Forecasts, historical periods and structural business analysis are not
    assertions of the current quarter's observed results.
    """
    if _actual_gate_ok(state):
        return False
    for change in _REALIZED_FINANCIAL_CHANGE.finditer(clause):
        left = max(clause.rfind(",", 0, change.start()), clause.rfind("，", 0, change.start())) + 1
        boundary = re.search(r"[，,]", clause[change.end():])
        right = change.end() + boundary.start() if boundary else len(clause)
        proposition = clause[left:right]
        historical = re.search(r"(?:20\d{2}年|\b20\d{2}\s+(?:Q[1-4]|quarter)|历史|歷史|過去|此前|前年|historical)", proposition, re.I)
        if (not historical and not _FINANCIAL_PROJECTION.search(proposition)
                and not _FINANCIAL_ASSERTION_WITHHELD.search(proposition)
                and _REALIZED_FINANCIAL_ASSERTION.search(proposition)):
            return True
    periods = list(_CURRENT_FINANCIAL_PERIOD.finditer(clause))
    for index, period in enumerate(periods):
        # A sibling withholding statement does not waive another assertion.
        # Keep its local prefix ("cannot confirm ...") and stop at the next
        # explicitly named current period rather than exempting a whole line.
        boundary = max(clause.rfind(",", 0, period.start()), clause.rfind("，", 0, period.start())) + 1
        end = periods[index + 1].start() if index + 1 < len(periods) else len(clause)
        proposition = clause[boundary:end]
        if _FINANCIAL_ASSERTION_WITHHELD.search(proposition):
            continue
        if _CURRENT_QUARTER_CONVICTION.search(proposition):
            return True
        if _FINANCIAL_PROJECTION.search(proposition):
            continue
        result = clause[period.end():end]
        metric = _FINANCIAL_RESULT_METRIC.search(result)
        if metric and _FINANCIAL_RESULT_PREDICATE.search(result[metric.end():]):
            return True
    return False


_OKU_VALUE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:億円|亿元|亿)")
_EXECUTION_INPUT = re.compile(
    r"(?:\*\*Entry Price\*\*|\*\*Stop Loss\*\*|\*\*Position Sizing\*\*|entry|stop(?:[ -]?loss)?|position sizing|入场|止损|仓位)",
    re.I,
)
_COLLAPSED_PROVENANCE = re.compile(
    r"(?:以下(?:数据|事实|证据).*均来自.*VERIFIED_TOOL_OUTPUT|"
    r"all\s+(?:data|facts|evidence).*VERIFIED_TOOL_OUTPUT)",
    re.I,
)
_FINANCIAL_GENERIC_PROVENANCE = re.compile(
    r"(?:(?:Q[1-4]|FY|latest|current|最新|当前)[^\n。！？;]*)?"
    r"(?:financial|fundamental|actual|guidance|revenue|profit|EPS|财务|基本面|业绩|指引|营收|利润)"
    r"[^\n。！？;]*VERIFIED_TOOL_OUTPUT",
    re.I,
)
_ACTUAL_CONTEXT = re.compile(
    r"(?:latest\s+actual|最新(?:季度)?(?:实绩|actual)|(?:Q1|H1|Q3|FY)[^\n。！？;]*(?:actual|实际|実績))",
    re.I,
)
_FINANCIAL_METRIC_PATTERNS = {
    "revenue": re.compile(r"(?:revenue|sales|营收|营业收入|売上高)", re.I),
    "operating_profit": re.compile(r"(?:operating\s+profit|OP\b|营业利润|営業利益)", re.I),
    "ordinary_profit": re.compile(r"(?:ordinary\s+profit|经常利润|経常利益)", re.I),
    "net_income": re.compile(r"(?:net\s+income|归母净利润|净利润|純利益)", re.I),
    "eps": re.compile(r"\bEPS\b|每股收益", re.I),
    "profit_total": re.compile(r"(?:total\s+period\s+profit|profit\s+total|当期总利润)", re.I),
}


@dataclass(frozen=True)
class EvidenceEnforcementResult:
    text: str
    warnings: tuple[str, ...] = ()
    findings: tuple[EvidenceFinding, ...] = ()


@dataclass(frozen=True)
class EvidenceFinding:
    """One concrete claim changed by evidence enforcement.

    Audit closure must follow this identity rather than treating a warning code
    as if it identified every claim in an entire report field.
    """

    warning: str
    claim_sha256: str
    original_claim: str
    replacement_claim: str
    action: str


@dataclass(frozen=True)
class _Catalog:
    all_numbers: frozenset[str]
    snapshot_numbers: frozenset[str]
    market_report_numbers: frozenset[str]
    guidance_numbers: frozenset[str]
    analyst_numbers: frozenset[str]
    vendor_forward_numbers: frozenset[str]
    stale_numbers: frozenset[str]


def enforce_agent_output(state: Mapping[str, Any], text: str, agent_name: str) -> EvidenceEnforcementResult:
    """Downgrade unsupported JP numeric claims and retain an audit note.

    The check is intentionally conservative: a number is kept only when it is
    present in the immutable verified snapshot, the normalized Japan bundle,
    or an explicit user execution constraint.  It does not calculate or
    reconcile any values.
    """
    if not text or (state.get("market_context") or {}).get("market") != "JP":
        return EvidenceEnforcementResult(text)

    catalog = _catalog_from_state(state)
    warnings: list[str] = []
    findings: list[EvidenceFinding] = []
    macro_headings: list[tuple[int, str | None]] = []
    macro_jurisdiction: str | None = None
    financial_headings: list[tuple[int, bool]] = []
    base_heading = 0

    def resolve_claim(claim: str, warning: str, replacement: str) -> str:
        warnings.append(warning)
        original = claim.strip()
        accepted = replacement.strip()
        findings.append(
            EvidenceFinding(
                warning=warning,
                claim_sha256=hashlib.sha256(original.encode("utf-8")).hexdigest(),
                original_claim=original,
                replacement_claim=accepted,
                action="REPLACED" if accepted else "REMOVED",
            )
        )
        return replacement

    def clean_clause(clause: str, *, heading: bool = False) -> str:
        if _FINANCIAL_GENERIC_PROVENANCE.search(clause):
            return resolve_claim(
                clause,
                "financial_provenance_collapsed",
                clause.replace(
                    "VERIFIED_TOOL_OUTPUT", "VERIFIED_FINANCIAL_AUTHORITY"
                ),
            )
        if _COLLAPSED_PROVENANCE.search(clause):
            return resolve_claim(
                clause,
                "collapsed_provenance_types",
                "行情、新闻与财务信息具有不同来源，应分别理解。",
            )
        if _UNVERIFIED_MARKET.search(clause):
            if _has_verified_domain(state, "MARKET"):
                return resolve_claim(
                    clause,
                    "verified_market_fact_downgraded",
                    "本次行情与技术指标有正式行情数据支持。",
                )
            if _has_market_tool_provenance(state):
                return resolve_claim(
                    clause,
                    "verified_market_fact_downgraded",
                    "历史行情有可追溯来源，但无法确认当前时效，不能据此判断当前技术状态。",
                )
        if _UNVERIFIED_NEWS.search(clause) and _has_verified_domain(state, "NEWS"):
            return resolve_claim(
                clause,
                "verified_news_fact_downgraded",
                "新闻事实以注明的来源为准，未经确认的内容仅供参考。",
            )
        if _SHORT_ABSENCE_OVERCLAIM.search(clause):
            return resolve_claim(
                clause,
                "short_absence_overclaim",
                "可观察借券余额或官方可申报仓位并不覆盖全部空头；无申报记录不代表不存在空头。",
            )
        if _SHORT_PRESSURE_OVERCLAIM.search(clause):
            return resolve_claim(
                clause,
                "short_pressure_overclaim",
                "JSF 可观察贷株余额较低；该指标不代表全市场空头总量、"
                "机构空头立场或不存在其他做空压力。",
            )
        if _HISTORY_AS_SIGNAL.search(clause):
            return resolve_claim(
                clause,
                "historical_outcome_as_current_evidence",
                "既往交易结果不能证明当前行情方向。",
            )
        dependent_actual = (any(blocked for _, blocked in financial_headings)
                            and _FINANCIAL_CHANGE_REFERENCE.search(clause)
                            and not _FINANCIAL_PROJECTION.search(clause)
                            and not _FINANCIAL_ASSERTION_WITHHELD.search(clause))
        if current_financial_gate_violation(state, clause) or dependent_actual:
            return resolve_claim(
                clause,
                "critical_gate_bypassed",
                "当前季度证据不足，暂不发布当前实绩判断。",
            )
        if probability_event_gate_violation(state, clause, jurisdiction_context=macro_jurisdiction):
            outcome_cells = _probability_row_outcomes(state, clause)
            event = _row_event_probability(clause)
            if event and macro_jurisdiction and not any(p.search(event[0]) for p in _EVENT_JURISDICTIONS.values()):
                event = macro_jurisdiction + " " + event[0], event[1]
            event_bound = event and _source_regime_odds_bound(
                state, event[0], next(key for key, pattern in _ECONOMIC_REGIMES.items()
                                     if pattern.search(event[0])), event[1])
            if outcome_cells and event_bound:
                cells = [cell.strip() for cell in clause.strip().strip("|").split("|")]
                for index in outcome_cells:
                    cells[index] = "不据此推断宏观状态"
                return resolve_claim(clause, "probability_event_mismatch",
                                     "| " + " | ".join(cells) + " |")
            if clause.lstrip().startswith("|"):
                # An unbound probability row is removed, not replaced with a
                # prose paragraph in the middle of its valid sibling rows.
                return resolve_claim(clause, "probability_event_mismatch", "")
            replacement = "该宏观情景尚无法确认。"
            if _unsupported_market_pricing(state, clause):
                replacement = "当前市场定价情况尚无法确认。"
            if _unsupported_macro_outcome(state, clause):
                replacement = "事件概率不直接证明公司需求或当前金融状况。"
            if _unsupported_regime_certainty(state, clause):
                replacement = "该宏观情景或共识尚无法确认。"
                if heading:
                    # Retain a factual heading subject, not its unsupported
                    # editorial conclusion. No guessed replacement regime.
                    subject = re.split(r"——|—|--|[:：]", clause, maxsplit=1)[0].strip()
                    replacement = subject if subject != clause.strip() and not probability_event_gate_violation(state, subject) else "宏观情景证据"
            return resolve_claim(
                clause,
                "probability_event_mismatch",
                replacement,
            )
        if _unit_mismatch(clause, state):
            return resolve_claim(
                clause,
                "unit_mismatch",
                "该数值的单位尚无法确认。",
            )
        if _PERIOD_MIX.search(clause):
            return resolve_claim(
                clause,
                "period_mismatch",
                "年度、季度与滚动十二个月数据口径不同，不能直接混算。",
            )

        if _FACT_LABEL.search(clause) and _NON_FACT_SOURCE.search(clause):
            return resolve_claim(
                clause,
                "source_type_confusion",
                "社区情绪与分析师预期属于参考信息，不代表官方确认。",
            )

        if _GUIDANCE.search(clause) and _CONSENSUS.search(clause):
            return resolve_claim(
                clause,
                "guidance_consensus_mixed",
                "公司指引与分析师一致预期属于不同口径，不能互相替代。",
            )
        if _GUIDANCE.search(clause) and _VENDOR_FORWARD.search(clause):
            return resolve_claim(
                clause,
                "guidance_vendor_forward_mixed",
                "公司指引与供应商远期估计属于不同口径，不能互相替代。",
            )

        tokens = _number_tokens(clause)
        if not tokens:
            return clause
        # A Markdown row is a compound claim. Replacing one metric with prose
        # would splice the sentence into the remaining cells. Let the table
        # path either preserve the fully supported row or omit it as a unit.
        financial_replacement = (
            None if "|" in clause else _financial_authority_replacement(clause, state)
        )
        if financial_replacement is not None:
            return resolve_claim(
                clause, "financial_authority_replaced", financial_replacement
            )
        if (
            agent_name == "Trader"
            and _EXECUTION_INPUT.search(clause)
            and not _CURRENT_PRICE.search(clause)
        ):
            # Entry, stop, and size are proposal inputs the Trader is allowed
            # to choose. Their derived percentages are validated separately.
            return clause
        current_price_numbers = catalog.snapshot_numbers | catalog.market_report_numbers
        if _CURRENT_PRICE.search(clause) and any(token not in current_price_numbers for token in tokens):
            return resolve_claim(
                clause,
                "non_snapshot_current_price",
                _replace_unsupported(clause, current_price_numbers),
            )
        if _GUIDANCE.search(clause) and any(
            (token in catalog.analyst_numbers or token in catalog.vendor_forward_numbers)
            and token not in catalog.guidance_numbers
            for token in tokens
        ):
            return resolve_claim(
                clause,
                "analyst_estimate_as_guidance",
                _replace_unsupported(clause, catalog.guidance_numbers),
            )
        if _CONSENSUS.search(clause) and any(
            token in catalog.guidance_numbers and token not in catalog.analyst_numbers for token in tokens
        ):
            return resolve_claim(
                clause,
                "guidance_as_analyst_consensus",
                _replace_unsupported(clause, catalog.analyst_numbers),
            )
        if _CURRENT_WORD.search(clause) and any(token in catalog.stale_numbers for token in tokens):
            return resolve_claim(
                clause,
                "stale_data_as_current",
                _replace_unsupported(clause, catalog.all_numbers - catalog.stale_numbers),
            )
        if any(token not in catalog.all_numbers for token in tokens):
            return resolve_claim(
                clause,
                "unsupported_precise_number",
                _replace_unsupported(clause, catalog.all_numbers),
            )
        return clause

    # Preserve markdown structure.  One line is a deliberately small enough
    # unit for reports and avoids splitting decimal values at their dot.
    lines = []
    for line in text.splitlines(keepends=True):
        heading = re.match(r"^\s*(#{1,6})\s+(.+)", line)
        if heading:
            level = len(heading[1])
            base_heading = level
            while macro_headings and macro_headings[-1][0] >= level:
                macro_headings.pop()
            jurisdiction = next((key for key, pattern in _EVENT_JURISDICTIONS.items()
                                 if pattern.search(heading[2])), None)
            macro_headings.append((level, jurisdiction))
            macro_jurisdiction = next((country for _, country in reversed(macro_headings) if country), None)
        # Emphasized financial section titles carry the same evidence role as
        # # headings. A linked "this improvement" cannot outlive its blocked
        # result premise; independent business facts and forecasts still can.
        bold_surface = re.match(r'\s*(?:\d+[.)、]\s*)?\*\*([^\n*]+)\*\*(?:\s*[:：]|\s*$)', line)
        if heading or bold_surface:
            rank = len(heading[1]) if heading else base_heading + 1
            while financial_headings and financial_headings[-1][0] >= rank:
                financial_headings.pop()
            financial_title = heading[2] if heading else bold_surface[1]
            financial_headings.append((rank, current_financial_gate_violation(state, financial_title)))
        lines.append(_clean_line(line, clean_clause))
    cleaned = "".join(lines)
    cleaned = _sanitize_legacy_enforcement_artifacts(cleaned)
    cleaned = _collapse_adjacent_replacements(cleaned, findings)
    if not warnings:
        return EvidenceEnforcementResult(cleaned)
    unique_warnings = tuple(dict.fromkeys(warnings))
    return EvidenceEnforcementResult(cleaned, unique_warnings, tuple(findings))


def _collapse_adjacent_replacements(text: str, findings: list[EvidenceFinding]) -> str:
    """Deduplicate adjacent generated notices without merging claim identities.

    Only whole replacement paragraphs from this enforcement pass qualify.
    Ordinary repeated facts, tables and notices across section boundaries do
    not. Both original findings remain available for exact-artifact closure.
    """
    replacements = {
        item.replacement_claim.strip() for item in findings
        if item.replacement_claim.strip().endswith("。")
        and not re.search(r"[|\n]|^\s*#", item.replacement_claim)
    }
    # Two sentence claims in one prose line may receive the same deterministic
    # notice. This is provenance-scoped replacement dedup, not fact dedup.
    for replacement in replacements:
        escaped = re.escape(replacement)
        text = re.sub(rf"{escaped}(?:\s*{escaped})+", replacement, text)
    output: list[str] = []
    previous = None
    for line in text.splitlines(keepends=True):
        content = re.sub(r"^[-*+]\s+", "", line.strip()).strip("*")
        if content and content == previous and content in replacements:
            continue
        output.append(line)
        if content:
            previous = content
    return "".join(output)


def enforce_agent_result(state: Mapping[str, Any], result: dict[str, Any], agent_name: str) -> dict[str, Any]:
    """Apply social status protection for all markets and JP evidence checks."""
    result = dict(result)
    result = _enforce_unavailable_social_source(state, result)
    if (state.get("market_context") or {}).get("market") != "JP":
        return result
    if isinstance(result.get("sentiment_report"), str) and not _has_japan_sentiment_evidence(state):
        result["sentiment_report"] = (
            "**Overall Sentiment:** **DATA_UNAVAILABLE**\n"
            "**Score:** DATA_UNAVAILABLE\n"
            "**Confidence:** Low\n\n"
            "暂无可用日本情绪数据。投资者/社交样本不可用或为空；"
            "News 和 Macro 仅为背景，未参与 sentiment score 或 band。"
        )
    audit: list[dict[str, Any]] = list(state.get("evidence_audit") or [])

    def clean_value(key: str, value: str) -> str:
        checked = enforce_agent_output(state, value, agent_name)
        if checked.findings:
            for finding in checked.findings:
                audit.append(
                    {
                        "category": _audit_category(finding.warning),
                        "agent": agent_name,
                        "field": key,
                        "warning": finding.warning,
                        "claim_sha256": finding.claim_sha256,
                        "original_claim": finding.original_claim,
                        "replacement_claim": finding.replacement_claim,
                        "enforcement_action": finding.action,
                        "resolution": "PENDING_FINAL_ARTIFACT_VALIDATION",
                        "execution_blocking": True,
                    }
                )
        return checked.text

    for key in ("market_report", "fundamentals_report", "news_report", "sentiment_report", "investment_plan", "trader_investment_plan", "final_trade_decision"):
        if isinstance(result.get(key), str):
            result[key] = clean_value(key, result[key])
    for debate_key in ("investment_debate_state", "risk_debate_state"):
        debate = result.get(debate_key)
        if not isinstance(debate, dict):
            continue
        updated = dict(debate)
        for key, value in debate.items():
            if isinstance(value, str):
                updated[key] = clean_value(f"{debate_key}.{key}", value)
        result[debate_key] = updated
    if audit:
        result["evidence_audit"] = audit
    return result


def audit_agent_result(
    state: Mapping[str, Any], result: dict[str, Any], agent_name: str
) -> dict[str, Any]:
    """Detect JP evidence violations without rewriting an agent's reasoning.

    Resolution belongs to the canonical final-state builder after the final
    artifact exists.  Node-time audit entries are therefore deliberately
    pending and execution-blocking until that final validation occurs.
    """
    result = dict(result)
    if (state.get("market_context") or {}).get("market") != "JP":
        return result
    audit: list[dict[str, Any]] = list(state.get("evidence_audit") or [])

    def inspect(key: str, value: str) -> None:
        checked = enforce_agent_output(state, value, agent_name)
        for finding in checked.findings:
            audit.append(
                {
                    "category": _audit_category(finding.warning),
                    "agent": agent_name,
                    "field": key,
                    "warning": finding.warning,
                    "claim_sha256": finding.claim_sha256,
                    "original_claim": finding.original_claim,
                    "replacement_claim": finding.replacement_claim,
                    "enforcement_action": finding.action,
                    "resolution": "PENDING_FINAL_ARTIFACT_VALIDATION",
                    "execution_blocking": True,
                }
            )

    for key in (
        "market_report",
        "fundamentals_report",
        "news_report",
        "sentiment_report",
        "investment_plan",
        "trader_investment_plan",
        "final_trade_decision",
    ):
        if isinstance(result.get(key), str):
            inspect(key, result[key])
    for debate_key in ("investment_debate_state", "risk_debate_state"):
        debate = result.get(debate_key)
        if not isinstance(debate, Mapping):
            continue
        for key, value in debate.items():
            if isinstance(value, str):
                inspect(f"{debate_key}.{key}", value)
    if audit:
        result["evidence_audit"] = audit
    return result


def _enforce_unavailable_social_source(
    state: Mapping[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    """Remove downstream popularity claims when Reddit was not queryable."""
    status_text = " ".join(
        str(value)
        for value in (
            state.get("sentiment_report"),
            result.get("sentiment_report"),
        )
        if value
    )
    if not re.search(r"Reddit：本次(?:因限流不可用|请求超时|获取失败)", status_text):
        return result

    def clean(text: str) -> str:
        return "".join(
            (
                "【数据源约束：Reddit 本次不可用，相关热度或方向性推断已移除】"
                if _UNAVAILABLE_REDDIT.search(part)
                else part
            )
            for part in re.split(r"(?<=[。！？；;])", text)
            if part
        )

    for key in (
        "market_report",
        "fundamentals_report",
        "news_report",
        "sentiment_report",
        "investment_plan",
        "trader_investment_plan",
        "final_trade_decision",
    ):
        if isinstance(result.get(key), str):
            result[key] = clean(result[key])
    for debate_key in ("investment_debate_state", "risk_debate_state"):
        debate = result.get(debate_key)
        if isinstance(debate, dict):
            result[debate_key] = {
                key: clean(value) if isinstance(value, str) else value
                for key, value in debate.items()
            }
    return result


def _clean_line(line: str, clean_clause) -> str:
    """Apply a downgrade to complete sentences, never isolated number tokens."""
    ending = "\n" if line.endswith("\n") else ""
    content = line[:-1] if ending else line
    heading = _HEADING_ORDINAL.match(content)
    if heading:
        # Structural ordinals such as ``3.1`` are not evidence values.  Keep
        # validating dates and metrics in the heading body, but do not make a
        # post-pruning renumber operation look like a new unsupported claim.
        return (
            heading.group("heading")
            + heading.group("ordinal")
            + clean_clause(heading.group("body"), heading=True)
            + ending
        )
    plain_heading = re.match(r"^(#{1,6}\s+)(.*)$", content)
    if plain_heading:
        return plain_heading[1] + clean_clause(plain_heading[2], heading=True) + ending
    parts = re.split(r"(?<=[。！？；;])", content)
    cleaned = "".join(clean_clause(part) for part in parts if part)
    # A whole-clause replacement still belongs to the original list item.
    # Losing its marker turns it into lazy continuation of a legal sibling,
    # misleadingly attaching a notice to independently bound evidence.
    bullet = re.match(r"^(\s*[-*+]\s+)", content)
    if bullet and cleaned and not re.match(r"^\s*[-*+]\s+", cleaned):
        cleaned = bullet[1] + cleaned
    # A removed table row must not leave a blank line terminating its table.
    return cleaned + ending if cleaned or not content.lstrip().startswith("|") else ""


def _sanitize_legacy_enforcement_artifacts(text: str) -> str:
    """Keep copied upstream text from carrying old substring corruption.

    A later agent can quote an earlier report.  This final normalization makes
    the no-substring contract transitive across handoffs without attempting to
    recover the unsupported value that was previously destroyed.
    """
    text = text.replace("该精确数值缺少上游证据支持，已不纳入本项判断。", "")
    text = re.sub(
        r"FY\d*DATA_UNAVAILABLE(?:增长预期|growth\s+estimate)?",
        "DATA_UNAVAILABLE",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"DATA_UNAVAILABLE(?:(?:x|pt|億円?|百万円|万亿|万億|日元|円|%))+",
        "DATA_UNAVAILABLE",
        text,
        flags=re.I,
    )
    text = re.sub(r"DATA_UNAVAILABLE\s*/\s*10", "DATA_UNAVAILABLE", text)
    # Reports generated before row-level enforcement can be quoted by later
    # agents. Remove those complete legacy table claims instead of exposing a
    # wall of placeholders in the user report.
    text = re.sub(
        r"(?m)^\|[^\n]*(?:数据不足|证据不足，暂不判断)[^\n]*\|\s*$",
        "",
        text,
    )
    text = text.replace("。|", "。\n\n|")
    return re.sub(r"\n{3,}", "\n\n", text)


def _catalog_from_state(state: Mapping[str, Any]) -> _Catalog:
    is_japan = (state.get("market_context") or {}).get("market") == "JP"
    market_current = not is_japan or canonical_market_authority(state)["status"] == "CURRENT"
    # The JP snapshot is diagnostic, not the formal Market-tool authority.
    snapshot_numbers = (
        set() if is_japan else _numbers_in(state.get("verified_market_snapshot", ""))
    )
    market_report_numbers: set[str] = set()
    all_numbers = set(snapshot_numbers) | set(market_report_numbers)
    guidance_numbers: set[str] = set()
    analyst_numbers: set[str] = set()
    vendor_forward_numbers: set[str] = set()
    stale_numbers: set[str] = set()
    bundle = state.get("japan_data_bundle") or {}
    stale_keys = {
        (entry.get("source"), entry.get("source_type"), entry.get("timestamp"))
        for item in _iter_mapping_values(bundle.get("items") or [])
        if item.get("source_type") == "source_of_truth_assessment"
        for entry in item.get("metadata", {}).get("freshness", [])
        if entry.get("status") == "STALE"
    }
    for item in _iter_mapping_values(bundle.get("items") or []):
        payload_numbers = _numbers_in(item)
        all_numbers.update(payload_numbers)
        source_type = str(item.get("source_type", ""))
        source = str(item.get("source", ""))
        if source_type == "japan_analyst_expectations":
            analyst_numbers.update(payload_numbers)
        if source in {"TDnet", "Company IR", "J-Quants"} and source_type in {"guidance_revision", "official_financial_summary"}:
            guidance_numbers.update(payload_numbers)
        if (source, source_type, item.get("timestamp")) in stale_keys:
            stale_numbers.update(payload_numbers)
    for item in _iter_mapping_values(state.get("evidence_registry") or []):
        if item.get("claim_type") not in {"FACT", "DERIVED"}:
            continue
        payload_numbers = _numbers_in(item.get("value"))
        derivation = item.get("derivation")
        if isinstance(derivation, Mapping):
            payload_numbers.update(
                str(token)
                for token in derivation.get("numeric_tokens") or []
            )
        all_numbers.update(payload_numbers)
        semantic = str(item.get("semantic_basis") or "")
        if (
            item.get("domain") == "MARKET"
            and item.get("verification_status") == "VERIFIED_TOOL_OUTPUT"
            and item.get("allowed_for_current_decision") is True
            and market_current
        ):
            market_report_numbers.update(payload_numbers)
        if semantic == "COMPANY_GUIDANCE":
            guidance_numbers.update(payload_numbers)
        elif semantic == "ANALYST_CONSENSUS":
            analyst_numbers.update(payload_numbers)
        elif semantic == "VENDOR_FORWARD_ESTIMATE":
            vendor_forward_numbers.update(payload_numbers)
        if item.get("allowed_for_current_decision") is False:
            stale_numbers.update(payload_numbers)
    all_numbers.update(_numbers_in(state.get("trade_constraints") or {}))
    all_numbers.update(_numbers_in(state.get("decision_context") or {}))
    all_numbers.update(_numbers_in(state.get("validated_execution") or {}))
    validation = state.get("validated_execution") or {}
    if isinstance(validation, Mapping):
        for key in ("risk_pct", "position_pct", "portfolio_stop_risk_pct"):
            value = validation.get(key)
            if isinstance(value, (int, float)):
                all_numbers.add(f"{_normalise_number(str(value))}%")
    # A current Market Analyst tool report is a separate upstream observation;
    # stale Japan-bundle values with the same numeric token must not invalidate
    # the tool result passed to downstream agents.
    stale_numbers.difference_update(market_report_numbers)
    return _Catalog(
        frozenset(all_numbers),
        frozenset(snapshot_numbers),
        frozenset(market_report_numbers),
        frozenset(guidance_numbers),
        frozenset(analyst_numbers),
        frozenset(vendor_forward_numbers),
        frozenset(stale_numbers),
    )


def _has_verified_domain(state: Mapping[str, Any], domain: str) -> bool:
    return any(
        isinstance(item, Mapping)
        and item.get("domain") == domain
        and str(item.get("verification_status", "")).startswith("VERIFIED")
        and item.get("allowed_for_current_decision") is True
        for item in state.get("evidence_registry") or []
    )


def _has_market_tool_provenance(state: Mapping[str, Any]) -> bool:
    return any(
        isinstance(item, Mapping)
        and item.get("domain") == "MARKET"
        and item.get("source_type") == "TOOL_OUTPUT"
        and item.get("source") in {"get_stock_data", "get_indicators"}
        and item.get("verification_status") == "VERIFIED_TOOL_OUTPUT"
        for item in state.get("evidence_registry") or []
    )


def _actual_gate_ok(state: Mapping[str, Any]) -> bool:
    bundle = state.get("japan_data_bundle") or {}
    assessment = (bundle.get("provider_metadata") or {}).get("Japan Financial Authority") or {}
    actual = assessment.get("actual") or {}
    gate = actual.get("critical_gate") or {}
    return actual.get("status") == "OK" and gate.get("status") == "OK"


def _unit_mismatch(clause: str, state: Mapping[str, Any]) -> bool:
    displayed = _OKU_VALUE.search(clause)
    if not displayed or not re.search(r"(?:capex|capital expenditure|acquisition|资本开支|收购)", clause, re.I):
        return False
    try:
        shown = float(displayed.group(1).replace(",", ""))
    except ValueError:
        return False
    keywords = (
        ("capex", "capital expenditure", "资本开支")
        if re.search(r"(?:capex|capital expenditure|资本开支)", clause, re.I)
        else ("acquisition", "收购")
    )
    for item in _iter_mapping_values(state.get("evidence_registry") or []):
        text = str(item.get("value") or "")
        if not any(keyword.lower() in text.lower() for keyword in keywords):
            continue
        for match in re.finditer(r"([\d,]+(?:\.\d+)?)\s*百万円", text):
            expected = float(match.group(1).replace(",", "")) / 100
            if abs(shown - expected) > 0.011:
                return True
    return False


def _audit_category(warning: str) -> str:
    if warning in {"stale_data_as_current"}:
        return "STALE_EVIDENCE_USE"
    if warning in {"unit_mismatch"}:
        return "UNIT_MISMATCH"
    if warning in {
        "source_type_confusion",
        "guidance_consensus_mixed",
        "guidance_vendor_forward_mixed",
        "analyst_estimate_as_guidance",
        "guidance_as_analyst_consensus",
        "short_absence_overclaim",
        "short_pressure_overclaim",
        "historical_outcome_as_current_evidence",
        "critical_gate_bypassed",
        "probability_event_mismatch",
        "collapsed_provenance_types",
        "financial_provenance_collapsed",
    }:
        return "SEMANTIC_MISMATCH"
    if warning in {
        "verified_market_fact_downgraded",
        "verified_news_fact_downgraded",
        "financial_authority_replaced",
    }:
        return "SUPPORTED_FACT"
    return "UNSUPPORTED_CLAIM"


def _iter_mapping_values(items: Iterable[Any]) -> Iterable[Mapping[str, Any]]:
    return (item for item in items if isinstance(item, Mapping))


def _has_japan_sentiment_evidence(state: Mapping[str, Any]) -> bool:
    bundle = state.get("japan_data_bundle") or {}
    for item in _iter_mapping_values(bundle.get("items") or []):
        if item.get("source_type") != "japan_investor_sentiment_aggregate":
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        sample_count = metadata.get("sample_count")
        score = metadata.get("sentiment_score")
        if (
            isinstance(sample_count, int)
            and sample_count > 0
            and isinstance(score, (int, float))
        ):
            return True
    return False


def _numbers_in(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        return set().union(*(_numbers_in(item) for item in value.values())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(_numbers_in(item) for item in value)) if value else set()
    return set(_number_tokens(str(value)))


def _number_tokens(text: str) -> tuple[str, ...]:
    # Dates, times and fiscal-year identifiers are temporal identity, not
    # unsupported numeric evidence.  Freshness validation owns them.
    searchable = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", "", text)
    searchable = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", "", searchable)
    searchable = re.sub(r"\bFY\d{4}\b", "", searchable, flags=re.I)
    return tuple(_normalise_number(match.group(0)) for match in _NUMBER.finditer(searchable))


def _normalise_number(token: str) -> str:
    # -8pp and -8.0pp are the same source value, not different evidence.
    # Retain units and sign; Decimal avoids float rounding or calculations.
    token = token.replace(",", "")
    suffix = "%" if token.endswith("%") else ""
    numeric = token[:-1] if suffix else token
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", numeric):
        return token
    rendered = format(Decimal(numeric), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if numeric.startswith("+"):
        rendered = "+" + rendered
    return rendered + suffix


def _financial_authority_replacement(
    clause: str, state: Mapping[str, Any]
) -> str | None:
    """Replace one incompatible current-financial claim with exact authority.

    Matching is by section, canonical metric and (when stated) fiscal period
    and target-period end.  Numeric proximity is never considered.  The helper
    intentionally handles only a single semantic metric per clause; compound
    prose falls through to normal clause-level removal rather than guessing.
    """
    is_guidance = bool(_GUIDANCE.search(clause))
    is_actual = bool(_ACTUAL_CONTEXT.search(clause))
    if is_guidance == is_actual:
        return None
    metrics = [
        metric
        for metric, pattern in _FINANCIAL_METRIC_PATTERNS.items()
        if pattern.search(clause)
    ]
    if len(metrics) != 1:
        return None
    metric = metrics[0]
    explicit_period = next(
        (period for period in ("Q1", "H1", "Q3", "FY") if re.search(rf"\b{period}\b", clause, re.I)),
        None,
    )
    target_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", clause)
    explicit_target = target_match.group(0) if target_match else None
    candidates = []
    for item in _iter_mapping_values(state.get("evidence_registry") or []):
        if item.get("verification_status") != "VERIFIED_FINANCIAL_AUTHORITY":
            continue
        if item.get("claim_type") != "FACT" or item.get("allowed_for_current_decision") is not True:
            continue
        if item.get("metric") != metric:
            continue
        semantic = str(item.get("semantic_basis") or "")
        if is_guidance != (semantic == "COMPANY_GUIDANCE"):
            continue
        if explicit_period and str(item.get("period") or "").upper() != explicit_period:
            continue
        if explicit_target and str(item.get("target_period") or "") != explicit_target:
            continue
        candidates.append(item)
    if len(candidates) != 1:
        return None
    authority = candidates[0]
    authority_token = _normalise_number(str(authority.get("value")))
    if authority_token in _number_tokens(clause):
        return None
    value = authority.get("value")
    if isinstance(value, float):
        displayed = f"{value:g}"
    elif isinstance(value, int):
        displayed = f"{value:,}"
    else:
        displayed = str(value)
    unit = str(authority.get("unit") or "").strip()
    section = "Current Company Guidance" if is_guidance else "Latest Actual"
    return f"{section} {metric}: {displayed}{(' ' + unit) if unit else ''}。"


def _replace_unsupported(clause: str, permitted: frozenset[str] | set[str]) -> str:
    """Remove a complete unsupported prose claim or fail-close a table cell.

    Substituting only the numeric substring left suffixes such as ``x`` and
    ``pt`` attached to ``DATA_UNAVAILABLE`` and could splice replacement text
    into identifiers.  Prose is now removed at clause granularity.  Markdown
    tables retain their shape and replace the complete unsupported cell.
    """
    if "|" in clause:
        return _redact_unsupported_table_values(clause, permitted)
    return ""


def _redact_unsupported_table_values(
    clause: str, permitted: frozenset[str] | set[str]
) -> str:
    """Drop one unsupported Markdown data row as a complete claim.

    Cell-by-cell placeholders preserved table syntax but produced dozens of
    user-visible ``数据不足`` fragments and left qualitative conclusions beside
    removed inputs.  A row is one compound claim: if any numeric cell lacks an
    exact evidence identity, omit that row.  Fully supported rows and separator
    rows remain byte-for-byte intact.
    """

    if any(token not in permitted for token in _number_tokens(clause)):
        return ""
    return clause
