"""Small, JP-only guardrail for agent text derived from run-level evidence.

This is deliberately a post-generation *downgrade* rather than a second
agent, provider, or LangGraph branch.  It prevents a clearly unsupported
precise claim from silently flowing into the next debate stage, while leaving
US behaviour untouched.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

# A numeric token may be followed immediately by a display suffix such as
# ``x`` or ``pt``.  It may not, however, begin in the middle of an identifier
# such as ``FY2027``.  The old look-behind allowed the engine to restart at the
# second digit (``FY2`` + ``027``), which produced corrupt output such as
# ``FY2DATA_UNAVAILABLE``.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d[\d,]*(?:\.\d+)?%?")
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
    r"空头(?:全部)?(?:死光|出清)|(?:没有|未见)(?:融券[/／])?做空压力(?:累积)?)",
    re.I,
)
_SHORT_PRESSURE_OVERCLAIM = re.compile(
    r"(?:short\s+pressure\s+(?:is\s+)?(?:extremely|very)?\s*low|"
    r"做空压力(?:极低|很低|有限)|空头压力(?:极低|很低|有限))",
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

    def clean_clause(clause: str) -> str:
        if _COLLAPSED_PROVENANCE.search(clause):
            warnings.append("collapsed_provenance_types")
            return (
                "【来源约束：各事实保留 Evidence Registry 中各自的来源类型；行情/新闻工具事实为 "
                "VERIFIED_TOOL_OUTPUT，财务事实为 VERIFIED_FINANCIAL_AUTHORITY，不得合并改写。】"
            )
        if _UNVERIFIED_MARKET.search(clause) and _has_verified_domain(state, "MARKET"):
            warnings.append("verified_market_fact_downgraded")
            return "【证据连续性：行情与技术指标已由本轮 Market 工具验证，不得降级为未验证引用。】"
        if _UNVERIFIED_NEWS.search(clause) and _has_verified_domain(state, "NEWS"):
            warnings.append("verified_news_fact_downgraded")
            return "【证据连续性：本轮 News 工具事实保留其来源与验证状态；未验证部分不得作为硬证据。】"
        if _SHORT_ABSENCE_OVERCLAIM.search(clause):
            warnings.append("short_absence_overclaim")
            return "【语义约束：仅能陈述可观察的借券余额或官方可申报仓位；无申报记录不代表不存在空头。】"
        if _SHORT_PRESSURE_OVERCLAIM.search(clause):
            warnings.append("short_pressure_overclaim")
            return (
                "JSF 可观察贷株余额较低；该指标不代表全市场空头总量、"
                "机构空头立场或不存在其他做空压力。"
            )
        if _HISTORY_AS_SIGNAL.search(clause):
            warnings.append("historical_outcome_as_current_evidence")
            return "【历史隔离：既往交易结果仅用于风险与信心校准，不构成本轮方向性证据。】"
        if _CURRENT_QUARTER_CONVICTION.search(clause) and not _actual_gate_ok(state):
            warnings.append("critical_gate_bypassed")
            return "【关键数据门控：当前季度证据不足，不能声称最新季度已被全面确认。】"
        if _unit_mismatch(clause, state):
            warnings.append("unit_mismatch")
            return "【单位校验：该换算与来源原始单位不一致，已从判断中移除。】"
        if _PERIOD_MIX.search(clause):
            warnings.append("period_mismatch")
            return "【证据约束：FY、季度与 TTM 不可混算；相关数值已降级为数据不可用】"

        if _FACT_LABEL.search(clause) and _NON_FACT_SOURCE.search(clause):
            warnings.append("source_type_confusion")
            return "【证据约束：社区情绪与分析师预期不可表述为已验证官方事实】"

        if _GUIDANCE.search(clause) and _CONSENSUS.search(clause):
            warnings.append("guidance_consensus_mixed")
            return "【证据约束：公司指引与分析师一致预期属于不同口径，不能合并或互相替代】"
        if _GUIDANCE.search(clause) and _VENDOR_FORWARD.search(clause):
            warnings.append("guidance_vendor_forward_mixed")
            return "【证据约束：公司指引与供应商远期估计属于不同语义，不能互相替代】"

        tokens = _number_tokens(clause)
        if not tokens:
            return clause
        financial_replacement = _financial_authority_replacement(clause, state)
        if financial_replacement is not None:
            warnings.append("financial_authority_replaced")
            return financial_replacement
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
            warnings.append("non_snapshot_current_price")
            return _replace_unsupported(clause, current_price_numbers)
        if _GUIDANCE.search(clause) and any(
            (token in catalog.analyst_numbers or token in catalog.vendor_forward_numbers)
            and token not in catalog.guidance_numbers
            for token in tokens
        ):
            warnings.append("analyst_estimate_as_guidance")
            return _replace_unsupported(clause, catalog.guidance_numbers)
        if _CONSENSUS.search(clause) and any(
            token in catalog.guidance_numbers and token not in catalog.analyst_numbers for token in tokens
        ):
            warnings.append("guidance_as_analyst_consensus")
            return _replace_unsupported(clause, catalog.analyst_numbers)
        if _CURRENT_WORD.search(clause) and any(token in catalog.stale_numbers for token in tokens):
            warnings.append("stale_data_as_current")
            return _replace_unsupported(clause, catalog.all_numbers - catalog.stale_numbers)
        if any(token not in catalog.all_numbers for token in tokens):
            warnings.append("unsupported_precise_number")
            return _replace_unsupported(clause, catalog.all_numbers)
        return clause

    # Preserve markdown structure.  One line is a deliberately small enough
    # unit for reports and avoids splitting decimal values at their dot.
    cleaned = "".join(_clean_line(line, clean_clause) for line in text.splitlines(keepends=True))
    cleaned = _sanitize_legacy_enforcement_artifacts(cleaned)
    if not warnings:
        return EvidenceEnforcementResult(cleaned)
    unique_warnings = tuple(dict.fromkeys(warnings))
    return EvidenceEnforcementResult(cleaned, unique_warnings)


def enforce_agent_result(state: Mapping[str, Any], result: dict[str, Any], agent_name: str) -> dict[str, Any]:
    """Apply social status protection for all markets and JP evidence checks."""
    result = dict(result)
    result = _enforce_unavailable_social_source(state, result)
    if (state.get("market_context") or {}).get("market") != "JP":
        return result
    audit: list[dict[str, Any]] = list(state.get("evidence_audit") or [])

    def clean_value(key: str, value: str) -> str:
        checked = enforce_agent_output(state, value, agent_name)
        if checked.warnings:
            for warning in checked.warnings:
                audit.append(
                    {
                        "category": _audit_category(warning),
                        "agent": agent_name,
                        "field": key,
                        "warning": warning,
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
    parts = re.split(r"(?<=[。！？；;])", line)
    return "".join(clean_clause(part) for part in parts if part)


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
    return re.sub(r"DATA_UNAVAILABLE\s*/\s*10", "DATA_UNAVAILABLE", text)


def _catalog_from_state(state: Mapping[str, Any]) -> _Catalog:
    snapshot_numbers = _numbers_in(state.get("verified_market_snapshot", ""))
    market_report_numbers = _numbers_in(state.get("market_report", ""))
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
        all_numbers.update(payload_numbers)
        semantic = str(item.get("semantic_basis") or "")
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
        "collapsed_provenance_types",
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
    return token.replace(",", "")


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
    """Keep supported cells visible while fail-closing unsupported table cells.

    Replacing an entire Markdown row because one comparison value was absent
    previously hid verified current-quarter Actual and Guidance values.  This
    keeps only exact source-backed numbers and marks every other numeric token
    unavailable; it does not infer or recalculate a replacement value.
    """

    cells = clause.split("|")
    for index, cell in enumerate(cells):
        tokens = _number_tokens(cell)
        if tokens and any(token not in permitted for token in tokens):
            cells[index] = " DATA_UNAVAILABLE "
    return "|".join(cells)
