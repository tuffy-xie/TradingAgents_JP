"""Claim units that require current JP Market-tool authority.

This is a publication boundary, not an attempt to infer a trading signal.  A
dated, attributed news headline is not promoted to a current technical fact;
an Agent's technical inference from that headline is a different claim.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_TECHNICAL_BASIS = re.compile(
    r"(?:(?<![A-Za-z])(?:MACD|RSI|ATR|VWMA|SMA|EMA)(?![A-Za-z])|"
    r"均线|均線|布林|技术(?:面|指标|动量|走势|转折|反弹)|技術(?:面|指標|動量)|"
    r"量价|量價|支撑位?|支撐位?|阻力位?|压力位?|壓力位?|"
    r"买盘支撑|買盤支撐|卖盘压力|賣盤壓力|technical\s+(?:trend|signal|momentum|support|resistance))",
    re.I,
)
_TECHNICAL_ASSERTION = re.compile(
    r"(?:金叉|死叉|黄金交叉|黃金交叉|超买|超賣|超卖|站上|跌破|突破|转多|轉多|"
    r"转空|轉空|转折|轉折|走平|上行|下行|强劲|強勁|很强|很強|成立|明确|明確|"
    r"确认|確認|属实|屬實|偏多|偏空|看涨|看漲|看跌|动能|動能|修复|修復|"
    r"附近|[是为為]\s*[¥￥]?\d|\b(?:cross(?:over)?|breakout|overbought|oversold|bullish|bearish)\b)",
    re.I,
)
_PRICE_ACTION = re.compile(
    r"(?:连续\s*\d+\s*日(?:上涨|下跌)|\d+日(?:続伸|続落|连涨|连跌)|"
    r"(?:近期|最近|当前|當前)[^。；;\n]{0,12}(?:上涨|上漲|下跌|走强|走強|转强|轉強|走势|走勢)|"
    r"现价|現價|当前价|當前價|当前股价|當前股價|收盘价|收盤價|"
    r"股价|股價|价格|價格|股价动能|股價動能|买盘|買盤|卖盘|賣盤|"
    r"当前趋势|當前趨勢|当前走势|當前走勢|"
    r"\b(?:share[ -]?price|stock[ -]?price|rally|buying\s+(?:support|pressure)|"
    r"selling\s+pressure|current\s+(?:price|trend|momentum|move))\b)",
    re.I,
)
_PRICE_INFERENCE = re.compile(
    r"(?:显示|顯示|表明|证明|證明|确认|確認|意味着|意味著|因此|所以|"
    r"反映|形成|支撑|支撐|动能|動能|趋势|趨勢|"
    r"推动|推動|带动|帶動|驱动|驅動|促成|提振|催化剂|催化劑|"
    r"增强|增強|转强|轉強|走强|走強|发布后|發佈後|"
    r"\b(?:driv(?:e|es|en|ing)|drove|fuel(?:ed|s)?|spark(?:ed|s)?|"
    r"cataly(?:st|zed)|confirm(?:s|ed)?|strengthen(?:s|ed)?|"
    r"following\s+the\s+(?:news|announcement))\b)",
    re.I,
)
_ATTRIBUTED_NEWS = re.compile(
    r"(?:Yahoo\s+Finance(?:\s+Japan)?|新闻(?:标题|报道称|报道)|新聞(?:標題|報道)|"
    r"媒体(?:报道|報道)|报导(?:称)?|報道(?:稱)?|headline|reported\s+by|"
    r"according\s+to\s+[^,，。]{2,40})",
    re.I,
)
_WITHHELD = re.compile(
    r"(?:证据不足|證據不足|不发布|不發佈|尚未确认|尚未確認|未获核验|未獲核驗|"
    r"无法证明|無法證明|数据不可用|資料不可用|未取得|没有当前|沒有當前|"
    r"not\s+(?:verified|available|confirmed)|insufficient\s+(?:data|evidence))",
    re.I,
)
_CURRENT_ASSERTION = re.compile(
    r"(?:当前|當前|现价|現價|目前|最新|已|正处于|正處於|成立|明确|明確|属实|屬實|"
    r"形成|站上|走平|确认|確認|显示|顯示|表明|\bcurrent(?:ly)?\b|\bnow\b)",
    re.I,
)
_PRESENT_TIME = re.compile(r"(?:当前|當前|现价|現價|目前|最新|现在|現在|\bcurrent(?:ly)?\b|\bnow\b)", re.I)
_HYPOTHETICAL = re.compile(r"^(?:若|如果|一旦|假如|待|等待|\bif\b|\bwhen\b)", re.I)
_FUTURE_TRIGGER = re.compile(
    r"(?:若|如果|一旦|假如|将来|未來|未来|触发|觸發|回落至|跌破后|突破后|"
    r"\b(?:if|when|would|were\s+to)\b)",
    re.I,
)
_HISTORY = re.compile(r"(?:历史上|歷史上|过去|過去|此前|当时|當時|曾经|曾經|historical(?:ly)?|previously)", re.I)
_DATE = re.compile(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)")
_TECHNICAL_LEVEL = re.compile(
    r"(?:支撑|支撐|阻力|压力|壓力|均线|均線)[^。；;\n]{0,20}\d[\d,]*(?:\.\d+)?"
    r"|\d[\d,]*(?:\.\d+)?[^。；;\n]{0,12}(?:支撑|支撐|阻力|压力|壓力|均线|均線)"
)


@dataclass(frozen=True)
class MarketClaim:
    text: str
    semantic_type: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def current_market_claims(text: str, *, analysis_as_of: str) -> list[MarketClaim]:
    """Find current technical assertions, including a complete Markdown row."""
    found: list[MarketClaim] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or _table_separator(stripped):
            continue
        units = [stripped] if stripped.startswith("|") else _split_clauses(stripped)
        for unit in units:
            semantic_type = _claim_type(unit, analysis_as_of=analysis_as_of)
            if semantic_type:
                found.append(MarketClaim(unit.strip(), semantic_type))
    return list(dict.fromkeys(found))


def remove_current_market_claims(text: str, *, analysis_as_of: str) -> str:
    """Remove complete unsupported claim units, not substrings of facts."""
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if stripped.startswith("|"):
            if not current_market_claims(stripped, analysis_as_of=analysis_as_of):
                kept.append(line)
            continue
        units = _split_clauses(line)
        retained = [
            unit for unit in units
            if not _claim_type(unit, analysis_as_of=analysis_as_of)
        ]
        if retained:
            kept.append("".join(retained).strip())
    return "\n".join(kept)


def _claim_type(text: str, *, analysis_as_of: str) -> str | None:
    plain = re.sub(r"^[#>*\s\-①-⑳]+", "", text).strip()
    plain = re.sub(r"^\d+[.、]\s*", "", plain)
    plain = re.sub(r"[*`_]", "", plain)
    if not plain or _WITHHELD.search(plain) and not _TECHNICAL_ASSERTION.search(plain):
        return None
    if _HYPOTHETICAL.search(plain) and not _CURRENT_ASSERTION.search(plain):
        return None
    if re.search(r"(?:→|⇒|=>|->)", plain) and _FUTURE_TRIGGER.search(plain) and not _PRESENT_TIME.search(plain):
        return None
    dated = [match.group(1) for match in _DATE.finditer(plain)]
    if (_HISTORY.search(plain) or dated and max(dated) < analysis_as_of) and not _PRESENT_TIME.search(plain):
        return None
    if _TECHNICAL_BASIS.search(plain) and (
        _TECHNICAL_ASSERTION.search(plain) or _TECHNICAL_LEVEL.search(plain)
    ):
        return "CURRENT_TECHNICAL_ASSERTION"
    if _PRICE_ACTION.search(plain) and (
        _PRICE_INFERENCE.search(plain)
        or _TECHNICAL_ASSERTION.search(plain)
        or re.search(r"\d", plain)
    ):
        # A source-attributed price headline is a news fact. The inferred
        # momentum/support/trend, even in the same paragraph, is not.
        if _ATTRIBUTED_NEWS.search(plain) and not _PRICE_INFERENCE.search(plain):
            return None
        if _PRICE_INFERENCE.search(plain) or not _ATTRIBUTED_NEWS.search(plain):
            return "CURRENT_PRICE_OR_MOMENTUM_ASSERTION"
    return None


def _split_clauses(line: str) -> list[str]:
    # Keep subject and predicate together across ordinary commas (for example
    # "MACD is low, its crossover..."), but separate a source-attributed
    # headline from a newly inferred conclusion or a new present-time claim.
    return [
        part for part in re.split(
            r"(?<=[。！？；;])|"
            r"(?<=，)(?=(?:因此|所以|显示|顯示|表明|意味着|意味著|反映|当前|當前|目前|现价|現價))|"
            r"(?<=,)(?=(?:therefore|showing|indicating|currently|now))",
            line,
            flags=re.I,
        )
        if part.strip()
    ]


def _table_separator(line: str) -> bool:
    return line.startswith("|") and re.fullmatch(r"[\s|:\-]+", line) is not None
