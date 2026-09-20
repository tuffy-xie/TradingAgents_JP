"""Build the single accepted user-facing state after all agents have run.

Agents keep ownership of reasoning, while source authority, evidence auditing,
and execution validation remain machine contracts.  This module is the sole
place where those contracts are applied to the final user-visible prose.  The
original agent output is retained separately for the technical agent log.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from tradingagents.agents.utils.evidence_enforcement import enforce_agent_output
from tradingagents.agents.utils.execution_validation import validate_execution_plan
from tradingagents.dataflows.japan.context import (
    render_japan_financial_report,
    render_japan_report_sections,
    render_japan_sentiment_report,
)
from tradingagents.dataflows.japan.trading_calendar import (
    latest_completed_japan_session,
)
from tradingagents.report_artifacts import render_markdown_fragment, validate_rendered_html
from tradingagents.report_consistency import canonical_report_metadata
from tradingagents.secret_redaction import sanitize_text

_REPORT_FIELDS = (
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "investment_plan",
    "trader_investment_plan",
    "final_trade_decision",
)
_DEBATE_FIELDS = ("investment_debate_state", "risk_debate_state")
_CONTRACT_VERSION = "v5"
_VIOLATION_CATEGORIES = {
    "UNSUPPORTED_CLAIM",
    "STALE_EVIDENCE_USE",
    "FUTURE_DATA_USE",
    "SEMANTIC_MISMATCH",
    "UNIT_MISMATCH",
    "ARITHMETIC_MISMATCH",
}
_CURRENT_FINANCIAL_HEADINGS = (
    "current financial authority summary",
    "latest actual",
    "current company guidance",
    "japan financial authority assessment",
    "最新季度业绩",
    "最新实际",
    "最新实绩",
    "管理层指引",
    "公司指引",
    "当前财务权威摘要",
)
_EXECUTION_HEADING = re.compile(
    r"(?:execution|trading?\s+plan|trade\s+parameters?|actionable|"
    r"可操作|执行|行动建议|交易者|交易计划|交易参数|入场|止损|止盈|仓位)",
    re.I,
)
_WITHHELD_EXECUTION_HEADINGS = {
    "trading team plan",
    "交易执行状态",
    "执行许可",
}
_EXECUTION_LINE = re.compile(
    r"(?:entry(?: price| condition)?|stop(?:[ -]?loss)?|price target|position(?: sizing)?|"
    r"入场(?:价|条件)?|建仓价|止损(?:价)?|止盈|目标价|仓位(?:上限)?)"
    r"(?:\*\*)?\s*[:：|]",
    re.I,
)
_JSF_CLAIM = re.compile(
    r"(?:\bJSF\b|日证金|日證金|貸株|贷株|借券|融券|融資|融资|securities\s+finance)",
    re.I,
)
_EXECUTION_INSTRUCTIONS = re.compile(
    r"(?:严守|严格|设置|设定|执行|触发|强制)?(?:止损|止盈)|强制离场|"
    r"(?:小|轻|重|试探|少量)[仓倉](?:位)?|"
    r"(?:建议|可以|可|应|宜|考虑|评估|确认后|破位后|逢高|择机|伺机|开始|建立)[^。；;\n]{0,80}(?:做空|试空|试多|开空|开多|建仓|入场|介入|进场|加仓|减仓|买入|卖出)|"
    r"(?:做空|建仓|入场|开空|开多)[^。；;\n]{0,80}(?:条件|触发|执行|建议)|"
    r"可执行方案|执行纪律|(?:仓位|敞口)[^。；;\n]{0,8}(?:纪律|配置)|价格触发条件|"
    r"\b(?:enter|initiate|open|take)\b[^.;\n]{0,30}\b(?:short|long|position|trade)\b|"
    r"\b(?:stop[ -]?loss|small position|forced exit)\b",
    re.I,
)
_EXECUTION_ACTION = re.compile(
    r"(?:买入|賣出|卖出|增持|減持|减持|加仓|加倉|减仓|減倉|建仓|建倉|"
    r"开仓|開倉|平仓|平倉|清仓|清倉|做多|做空|介入|入场|進場|进场|"
    r"逢低布局|逢高减码|逢高減碼|调整仓位|調整倉位|调整敞口|調整敞口|"
    r"open\s+(?:a\s+)?position|increase\s+(?:the\s+)?position|"
    r"reduce\s+(?:the\s+)?position|close\s+(?:the\s+)?position)",
    re.I,
)
_EXECUTION_DIRECTIVE_CONTEXT = re.compile(
    r"(?:建议|建議|应|應|应该|應該|宜|可(?:以)?|考虑|考慮|等待[^。；;\n]{0,20}后|"
    r"确认[^。；;\n]{0,20}后|突破[^。；;\n]{0,20}后|跌破[^。；;\n]{0,20}后|"
    r"逢低|逢高|分批|积极|積極|现有持仓|現有持倉|未投资者|未投資者|"
    r"目标水平|目標水平|策略|操作|计划|計畫|plan|recommend|should|consider|if\b)",
    re.I,
)
_SENTIMENT_AUTHORITY = re.compile(
    r"(?:投资者|投資者|社交|市场|市場)?情绪[^。；;\n|]{0,40}"
    r"(?:\d+(?:\.\d+)?\s*/\s*10|分数|分數|评分|評分|偏多|偏空|中性|"
    r"温和偏多|溫和偏多|温和偏空|溫和偏空|bullish|bearish|neutral)|"
    r"(?:sentiment\s+(?:score|band|sample|confidence)|overall_band)",
    re.I,
)
_PRESENTATION_HEADING_TRANSLATIONS = {
    "final transaction proposal": "最终研究结论",
    "recommendation": "研究建议",
    "strategic actions": "策略说明",
    "rating": "评级",
    "investment thesis": "投资逻辑",
    "time horizon": "研究周期",
}
_POSITION_RECOMMENDATION = re.compile(
    r"(?:仓位|倉位|净敞口|净暴露|组合总值|position(?: size| sizing)?|net exposure|allocation)"
    r"[^。；;\n]*(?:\d|%|≤|≥|上限|不超过)|"
    r"\d[\d.]*\s*%[^。；;\n]*(?:组合|portfolio|position|仓位(?:配置|分配|上限))",
    re.I,
)
_POSITION_DIRECTIVE = re.compile(
    r"(?:(?:仓位|倉位|敞口|净暴露|position|allocation)"
    r"[^。；;\n]{0,32}(?:建议|必须|应当|应该|应|宜|控制|缩放|配置|调整|维持|限制|降低|增加)|"
    r"(?:建议|必须|应当|应该|应|宜|控制|缩放|配置|调整|维持|限制|降低|增加)"
    r"[^。；;\n]{0,32}(?:仓位|倉位|敞口|净暴露|position|allocation))",
    re.I,
)
_EXECUTION_PARAMETER = re.compile(
    r"(?:入场|建仓|目标价|第一目标|第二目标|\bentry\b|\bstop\b|\btarget\b)"
    r"[^。；;\n]*\d",
    re.I,
)
_TRADER_WITHHELD = "确定性执行校验未通过，因此没有获准的入场、止损、目标价或仓位计划。"
_EXECUTION_WITHHELD = (
    "确定性执行校验未通过。本报告不提供可执行的入场、止损、目标价或仓位参数；"
    "方向性研究结论不等于已批准交易指令。"
)
_PUBLIC_NEWS_TEXT = {
    "Japan company-news authority": "已核验的日本公司新闻",
    "The following timestamped Japan bundle headlines are available; a separate tool's empty result does not mean there is no company news.":
        "以下公司新闻均有明确发布时间；其他新闻工具返回空结果，不代表本次没有公司新闻。",
}
_INTERNAL_STATUS = {
    "OK": "通过",
    "UNKNOWN": "未确认",
    "VERIFIED_FINANCIAL_AUTHORITY": "已核验的财务权威数据",
    "VERIFIED_TOOL_OUTPUT": "已核验的工具数据",
    "CURRENT_STRUCTURED_CONFIRMED": "截至分析日已确认最新",
    "CURRENT_OFFICIAL": "截至分析日最新官方数据",
    "FRESHNESS_UNVERIFIED": "新鲜度未确认",
    "INSUFFICIENT_DATA": "证据不足",
    "DATA_UNAVAILABLE": "数据不可用",
    "NOT_APPLICABLE": "不适用",
    "NOT_PROVIDED": "公司未提供",
    "LATEST_AVAILABLE": "来源当前最新可得",
    "STRUCTURED_SOURCE": "结构化补充来源",
    "NO_COMPLETE_OFFICIAL_AUTHORITY_PATH": "官方披露覆盖链路不完整",
    "OUTLIER_20D_CHANGE_GT_300_PCT": "异常变化，需复核",
    "NO_CONFIRMED_TREND": "趋势未确认",
    "UNCONFIRMED": "未确认",
    "INCOMPLETE": "不完整",
    "AUTH_REQUIRED": "需要认证",
    "RATE_LIMITED": "请求频率受限",
    "TIMEOUT": "请求超时",
    "API_ERROR": "数据源请求失败",
    "FETCH_FAILED": "数据获取失败",
    "PARSE_FAILED": "数据解析失败",
    "INVALID_RESPONSE": "数据源响应无效",
    "LOW_SAMPLE": "样本量不足",
    "DISABLED": "未启用",
    "VENDOR_FORWARD_ESTIMATE": "供应商远期估计",
    "COMPANY_GUIDANCE": "公司业绩指引",
    "ANALYST_CONSENSUS": "分析师一致预期",
    "J_GAAP": "日本会计准则",
    "US_GAAP": "美国会计准则",
}
_MACHINE_ENUM = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z][A-Z0-9]*)(?:_[A-Z0-9]+)+(?![A-Za-z0-9])")


def build_canonical_final_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return the sole accepted final state; leave non-Japan runs unchanged."""
    result = copy.deepcopy(dict(state))
    if (result.get("market_context") or {}).get("market") != "JP":
        return result
    contract = result.get("final_output_contract") or {}
    if contract.get("status") == "FINALIZED" and contract.get("version") == _CONTRACT_VERSION:
        return result
    if contract and isinstance(result.get("raw_agent_outputs"), Mapping):
        # Re-accept archived v1 state under the current output contract.
        result.update(copy.deepcopy(result["raw_agent_outputs"]))
    if not result.get("trader_investment_plan") and result.get(
        "trader_investment_decision"
    ):
        result["trader_investment_plan"] = result["trader_investment_decision"]

    raw_outputs = _snapshot_agent_outputs(result)
    result["raw_agent_outputs"] = raw_outputs
    # Artifact violations describe one concrete rendered candidate.  Archived
    # state can be re-accepted under a newer contract, so carry forward the
    # evidence audit but recompute final-artifact findings from the new exact
    # accepted_report_markdown below.
    audit = [
        item
        for item in (result.get("evidence_audit") or [])
        if not (
            isinstance(item, Mapping)
            and (
                item.get("category") in {"FINAL_ARTIFACT_VIOLATION", "EXECUTION_GATE"}
                or item.get("warning")
            )
        )
    ]

    for field in _REPORT_FIELDS:
        value = result.get(field)
        if not isinstance(value, str):
            continue
        result[field], audit = _accept_text(result, value, field, audit)

    for debate_field in _DEBATE_FIELDS:
        debate = result.get(debate_field)
        if not isinstance(debate, Mapping):
            continue
        accepted = dict(debate)
        for key, value in debate.items():
            if isinstance(value, str):
                accepted[key], audit = _accept_text(
                    result, value, f"{debate_field}.{key}", audit
                )
        result[debate_field] = accepted

    # Replaying a pre-v4 persisted state must also upgrade the execution
    # semantic contract. Earlier validators accepted numeric plans even when
    # the Trader action was Hold (defined by the product prompts as no action).
    if (
        contract
        and contract.get("version") != _CONTRACT_VERSION
        and (result.get("validated_execution") or {}).get("status") == "OK"
    ):
        trader_text = result.get("trader_investment_plan")
        if isinstance(trader_text, str):
            upgraded = validate_execution_plan(trader_text)
            # Contract upgrades may revoke an execution authorization (for
            # example, Hold is explicitly non-executing), but never manufacture
            # a new authorization that the persisted run did not already have.
            if upgraded.get("status") != "OK":
                result["validated_execution"] = upgraded

    financial = render_japan_financial_report(result)
    if financial:
        base = _remove_current_financial_sections(result.get("fundamentals_report", ""))
        result["fundamentals_report"] = _fold_empty_sections(
            (base.rstrip() + "\n\n" + financial).strip()
        )

    # The source-native aggregate is the only published JP sentiment
    # authority. Agent prose remains available in raw_agent_outputs/full log.
    result["sentiment_report"] = render_japan_sentiment_report(
        result.get("japan_data_bundle")
    )
    result["market_report"] = _canonicalize_market_report(result)
    result = _enforce_domain_authority_ownership(result)

    execution_allowed = (result.get("validated_execution") or {}).get("status") == "OK"
    if execution_allowed:
        result = _apply_validated_execution(result)
    else:
        result = _withhold_unvalidated_execution(result)
        audit.append(
            {
                "category": "EXECUTION_GATE",
                "agent": "Canonical Final State",
                "field": "executable_plan",
                "detail": "Execution inputs were not approved by the deterministic validator.",
                "resolution": "PENDING_FINAL_ARTIFACT_VALIDATION",
                "execution_blocking": True,
            }
        )

    result = _publicize_state_text(result)
    accepted_report = sanitize_text(compose_user_report_markdown(result))
    artifact_issues = _validate_final_artifact(
        result, execution_allowed, accepted_report=accepted_report
    )
    audit, audit_issues = _finalize_audit(
        audit,
        result,
        accepted_report=accepted_report,
        execution_allowed=execution_allowed,
    )
    artifact_issues = list(dict.fromkeys([*artifact_issues, *audit_issues]))
    for issue in artifact_issues:
        audit.append(
            {
                "category": "FINAL_ARTIFACT_VIOLATION",
                "agent": "Canonical Final State",
                "detail": issue,
                "resolution": "UNRESOLVED",
                "execution_blocking": True,
            }
        )
    result["evidence_audit"] = audit
    result["accepted_report_markdown"] = accepted_report
    if result.get("trader_investment_plan"):
        # Archived web states use this historical key. Keep it as a display
        # alias of the accepted plan so raw legacy prose cannot bypass the gate.
        result["trader_investment_decision"] = result["trader_investment_plan"]
    artifact_sha256 = hashlib.sha256(accepted_report.encode("utf-8")).hexdigest()
    result["final_output_contract"] = {
        "version": _CONTRACT_VERSION,
        "status": "FINALIZED" if not artifact_issues else "BLOCKED",
        "authority": "CANONICAL_FINAL_STATE",
        "execution_allowed": execution_allowed,
        "audit_finalized_after_artifact_validation": True,
        "artifact_issues": artifact_issues,
        "accepted_report_sha256": artifact_sha256,
        "audit_closure_status": "CLOSED" if not audit_issues else "UNRESOLVED",
        "validation_dimensions": {
            "structural_artifact_valid": not any(
                issue.startswith(("HEADING_", "NUMBERED_", "CIRCLED_", "EMPTY_", "MALFORMED_", "RAW_MARKDOWN", "MARKDOWN_SWALLOWED"))
                for issue in artifact_issues
            ),
            "evidence_closure": not audit_issues,
            "domain_authority_consistent": not any(
                issue.startswith("CROSS_DOMAIN_AUTHORITY") for issue in artifact_issues
            ),
            "execution_consistent": not any(
                issue.startswith(("UNAPPROVED_", "UNVALIDATED_", "POSITION_SIZE_"))
                for issue in artifact_issues
            ),
            "presentation_valid": not any(
                issue.startswith(("PROCESS_PROSE", "UNLOCALIZED_", "EMPTY_"))
                for issue in artifact_issues
            ),
            "persistence_byte_contract": "UTF8_EXACT_NO_APPENDED_BYTES",
        },
    }
    return result


def compose_user_report_markdown(
    state: Mapping[str, Any], *, ticker: str | None = None
) -> str:
    """Compose the exact JP user artifact from accepted canonical state.

    This is deterministic and side-effect free. Renderers may translate the
    Markdown to another presentation format, but must not reselect facts,
    execution parameters, or Agent prose.
    """
    metadata = canonical_report_metadata(state)
    display_symbol = metadata["symbol"] or ticker or ""
    instrument_type = {
        "EQUITY": "股票",
        "ETF": "ETF",
    }.get(metadata["instrument_type"], metadata["instrument_type"])
    manifest = state.get("run_manifest") or {}
    generated = manifest.get("runtime_timestamp_jst")
    header = [
        f"# TradingAgents 日本股票分析报告：{display_symbol}",
        "",
        f"市场：{metadata['market']} | 货币：{metadata['currency']} | "
        f"标的类型：{instrument_type}",
    ]
    if generated:
        header.extend(["", f"生成时间：{generated}"])

    sections: list[str] = []
    japan_section = render_japan_report_sections(state.get("japan_data_bundle"))
    if japan_section:
        sections.append(japan_section)

    analysts = [
        ("市场分析", state.get("market_report")),
        ("情绪分析", state.get("sentiment_report")),
        ("新闻分析", state.get("news_report")),
        ("基本面分析", state.get("fundamentals_report")),
    ]
    analyst_parts = [
        _agent_report_section(name, text)
        for name, text in analysts
        if isinstance(text, str) and text.strip()
    ]
    if analyst_parts:
        sections.append("## I. 分析师报告\n\n" + "\n\n".join(analyst_parts))

    research = state.get("investment_debate_state") or {}
    if isinstance(research, Mapping) and isinstance(research.get("judge_decision"), str):
        text = research["judge_decision"].strip()
        if text:
            sections.append(
                "## II. 研究团队结论\n\n"
                + _agent_report_section("研究经理", text)
            )

    trader = state.get("trader_investment_plan")
    if isinstance(trader, str) and trader.strip():
        sections.append(
            "## III. 交易团队计划\n\n"
            + _agent_report_section("交易员", trader)
        )

    risk = state.get("risk_debate_state") or {}
    if isinstance(risk, Mapping) and isinstance(risk.get("judge_decision"), str):
        text = risk["judge_decision"].strip()
        if text:
            sections.append(
                "## IV. 投资组合经理结论\n\n"
                + _agent_report_section("投资组合经理", text)
            )

    return normalize_markdown_structure(
        "\n\n".join(["\n".join(header), *sections])
    )


def _agent_report_section(name: str, text: str) -> str:
    """Nest Agent headings below the deterministic report wrapper."""

    def nested_heading(match: re.Match[str]) -> str:
        level = len(match.group(1)) + 3
        return (("#" * level + " ") if level <= 6 else "") + match.group(2)

    nested = re.sub(r"(?m)^(#{1,6})\s+(.+)$", nested_heading, text.strip())
    return f"### {name}\n{nested}"


def require_canonical_final_state(state: Mapping[str, Any]) -> None:
    """Fail closed when a JP renderer receives non-canonical business state."""
    if (state.get("market_context") or {}).get("market") != "JP":
        return
    contract = state.get("final_output_contract") or {}
    if contract.get("status") != "FINALIZED" or contract.get("version") != _CONTRACT_VERSION:
        raise ValueError("Japan report input is not an accepted canonical final state")
    accepted = state.get("accepted_report_markdown")
    expected_hash = contract.get("accepted_report_sha256")
    if not isinstance(accepted, str) or hashlib.sha256(accepted.encode("utf-8")).hexdigest() != expected_hash:
        raise ValueError("Japan accepted report does not match its canonical digest")


def _snapshot_agent_outputs(state: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = {key: copy.deepcopy(state.get(key)) for key in _REPORT_FIELDS}
    snapshot.update({key: copy.deepcopy(state.get(key)) for key in _DEBATE_FIELDS})
    return snapshot


def _accept_text(
    state: Mapping[str, Any], text: str, field: str, audit: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    agent = _agent_for_field(field)
    checked = enforce_agent_output(state, text, agent)
    for finding in checked.findings:
        audit.append(
            {
                "category": _category_for_warning(finding.warning),
                "agent": agent,
                "field": field,
                "warning": finding.warning,
                "claim_sha256": finding.claim_sha256,
                "original_claim": finding.original_claim,
                "replacement_claim": finding.replacement_claim,
                "enforcement_action": finding.action,
                "resolution": "PENDING_FINAL_ARTIFACT_VALIDATION",
                "execution_blocking": True,
            }
        )
    return _fold_empty_sections(
        _remove_generation_process_prose(_remove_jsf_agent_claims(checked.text))
    ), audit


_GENERATION_PROCESS_PROSE = re.compile(
    r"(?im)^\s*(?:based\s+on\s+the\s+(?:available|collected|comprehensive)[^\n]*,\s*)?"
    r"(?:i(?:'ll|\s+will|\s+am\s+going\s+to)|we(?:'ll|\s+will))\s+"
    r"(?:now\s+)?(?:compile|prepare|provide|write|generate)\b[^\n]*(?:report|analysis)[^\n]*\.?\s*$"
)
_ZH_GENERATION_PROCESS_PROSE = re.compile(
    r"(?im)^\s*(?:现在|接下来|下面)?(?:让(?:我|我们)|我(?:将|来))"
    r"[^\n]{0,40}(?:基于|根据)[^\n]{0,80}(?:提供|生成|撰写|整理)"
    r"[^\n]{0,30}(?:报告|分析)[。.!]?\s*$"
)


def _remove_generation_process_prose(text: str) -> str:
    """Remove model process narration, not research content."""
    text = _GENERATION_PROCESS_PROSE.sub("", text)
    text = _ZH_GENERATION_PROCESS_PROSE.sub("", text)
    process_clause = re.compile(
        r"(?:现在|接下来|下面)?(?:让(?:我|我们)|我(?:将|来))"
        r"[^。！？\n]{0,60}(?:基于|根据)[^。！？\n]{0,100}"
        r"(?:提供|生成|撰写|整理)[^。！？\n]{0,40}(?:报告|分析)[。！？]?"
    )
    return process_clause.sub("", text)


def _canonicalize_market_report(state: Mapping[str, Any]) -> str:
    """Publish current Market prose only when its OHLC authority is current.

    An analyst can reason over historical bars, but a stale or incomplete tool
    window cannot be promoted to a current technical assessment.  The raw prose
    remains in ``raw_agent_outputs`` for audit.
    """
    report = str(state.get("market_report") or "")
    tool_values = [
        str(item.get("value") or "")
        for item in state.get("evidence_registry") or []
        if isinstance(item, Mapping)
        and item.get("domain") == "MARKET"
        and item.get("source") == "get_stock_data"
        and item.get("source_type") == "TOOL_OUTPUT"
    ]
    if not tool_values:
        return report
    manifest = state.get("run_manifest") or {}
    analysis_text = str(manifest.get("analysis_as_of") or state.get("trade_date") or "")
    try:
        analysis_as_of = date.fromisoformat(analysis_text)
    except ValueError:
        return report
    observed_at = _manifest_timestamp(manifest)
    expected = latest_completed_japan_session(analysis_as_of, now=observed_at)
    latest = max(
        (
            value
            for content in tool_values
            if not content.lstrip().startswith("NO_DATA_AVAILABLE")
            for value in [_latest_complete_ohlcv_date(content, analysis_as_of)]
            if value is not None
        ),
        default=None,
    )
    if latest == expected:
        return report
    latest_text = latest.isoformat() if latest else "未取得"
    return (
        "## 当前市场数据状态\n\n"
        f"截至 {analysis_as_of.isoformat()}，最近完整 OHLCV 日期为 {latest_text}；"
        f"预期最近已完成交易日为 {expected.isoformat()}。"
        "当前行情与技术指标证据不足，因此不发布当前技术方向、指标分数或交易含义。"
        "历史行情仍保留在技术审计日志中。"
    )


def _manifest_timestamp(manifest: Mapping[str, Any]) -> datetime | None:
    value = manifest.get("runtime_timestamp_jst")
    try:
        observed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return observed if observed.tzinfo is not None and observed.utcoffset() is not None else None


def _latest_complete_ohlcv_date(content: str, analysis_as_of: date) -> date | None:
    lines = content.splitlines()
    header_index = next(
        (index for index, line in enumerate(lines) if line.startswith("Date,")),
        None,
    )
    if header_index is None:
        return None
    latest: date | None = None
    for row in csv.DictReader(io.StringIO("\n".join(lines[header_index:]))):
        try:
            observed = date.fromisoformat(str(row.get("Date") or "")[:10])
        except ValueError:
            continue
        if observed > analysis_as_of or not all(
            _present_number(row.get(name)) for name in ("Open", "High", "Low", "Close")
        ):
            continue
        latest = max(latest, observed) if latest else observed
    return latest


def _present_number(value: Any) -> bool:
    normalized = str(value or "").strip().casefold()
    if normalized in {"", "nan", "none", "null"}:
        return False
    try:
        float(normalized.replace(",", ""))
    except ValueError:
        return False
    return True


def _enforce_domain_authority_ownership(state: Mapping[str, Any]) -> dict[str, Any]:
    """Keep source-native sentiment aggregates in their one canonical field."""
    result = dict(state)
    for field in _REPORT_FIELDS:
        if field == "sentiment_report" or not isinstance(result.get(field), str):
            continue
        result[field] = _remove_cross_domain_sentiment_authority(result[field])
    for field in _DEBATE_FIELDS:
        debate = result.get(field)
        if not isinstance(debate, Mapping):
            continue
        result[field] = {
            key: _remove_cross_domain_sentiment_authority(value)
            if isinstance(value, str)
            else value
            for key, value in debate.items()
        }
    return result


def _remove_cross_domain_sentiment_authority(text: str) -> str:
    text = _filter_markdown_sections(
        text,
        lambda heading: not _SENTIMENT_AUTHORITY.search(heading),
    )
    kept: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            if not _SENTIMENT_AUTHORITY.search(line):
                kept.append(line)
            continue
        clauses = re.split(r"(?<=[。！？；;])", line)
        kept.append("".join(part for part in clauses if not _SENTIMENT_AUTHORITY.search(part)))
    return _fold_empty_sections("\n".join(kept))


def _agent_for_field(field: str) -> str:
    if "fundamentals" in field:
        return "Fundamentals Analyst"
    if "market" in field:
        return "Market Analyst"
    if "news" in field:
        return "News Analyst"
    if "sentiment" in field:
        return "Sentiment Analyst"
    if "trader" in field:
        return "Trader"
    if "risk_debate_state" in field or "final_trade" in field:
        return "Portfolio Manager"
    return "Research Manager"


def _category_for_warning(warning: str) -> str:
    if warning == "stale_data_as_current":
        return "STALE_EVIDENCE_USE"
    if warning == "unit_mismatch":
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


def _remove_current_financial_sections(text: str) -> str:
    return _filter_markdown_sections(
        text,
        lambda heading: not any(
            token in heading.casefold() for token in _CURRENT_FINANCIAL_HEADINGS
        ),
    )


def _filter_markdown_sections(text: str, keep_heading) -> str:
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    excluded_level: int | None = None
    for line in lines:
        level = _heading_level(line)
        if level is not None:
            if excluded_level is not None and level <= excluded_level:
                excluded_level = None
            if excluded_level is None and not keep_heading(line.lstrip("#").strip()):
                excluded_level = level
        if excluded_level is None:
            output.append(line)
    return "".join(output).strip()


def _fold_empty_sections(text: str) -> str:
    """Drop empty table subsections together with conclusions that depend on them."""
    lines = [
        line
        for line in text.splitlines(keepends=True)
        if "|" not in line or line.lstrip().startswith("|")
    ]
    lines = _drop_empty_table_subsections(lines)
    lines = _normalize_markdown_tables(lines)
    lines = _drop_empty_table_subsections(lines)
    preamble: list[str] = []
    sections: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if re.match(r"^##\s+", line):
            current = [line]
            sections.append(current)
        elif current is None:
            preamble.append(line)
        else:
            current.append(line)
    kept = preamble
    for section in sections:
        table_lines = [line for line in section if line.lstrip().startswith("|")]
        if table_lines and (
            _is_table_separator(table_lines[0])
            or not _table_has_data_row(table_lines)
        ):
            continue
        body = "".join(section[1:]).strip()
        if body:
            kept.extend(section)
    return normalize_markdown_structure(_clean_empty_markdown("".join(kept)))


def _clean_empty_markdown(text: str) -> str:
    """Fold empty headings/lead-ins after pruning, without deleting their siblings."""
    lines = text.splitlines()
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if (
            stripped
            and not re.sub(r"[\s*_#>+\-\d.)]", "", stripped)
            and stripped not in {"---", "***", "___"}
        ):
            continue
        # Clause pruning can leave one end of an emphasis span behind.
        if line.count("**") % 2:
            line = line.replace("**", "")
        kept.append(line)
    # Removing an empty child can empty its parent: work to a fixed point.
    while True:
        result: list[str] = []
        for index, line in enumerate(kept):
            heading = _heading_level(line)
            label = line.strip().strip("*_ ")
            lead_in = label.endswith((":", "：")) and not line.lstrip().startswith("|")
            bold_heading = (
                line.strip().startswith("**") and line.strip().endswith("**")
                and not re.search(r"[。；;.!?：:]", label)
            )
            if heading is not None or lead_in or bold_heading:
                next_index = next(
                    (
                        i
                        for i in range(index + 1, len(kept))
                        if kept[i].strip()
                        and _SEPARATOR.match(kept[i].strip()) is None
                    ),
                    len(kept),
                )
                following = kept[next_index] if next_index < len(kept) else ""
                next_level = _heading_level(following)
                boundary = not following
                if heading is not None:
                    boundary = boundary or (next_level is not None and next_level <= heading)
                else:
                    boundary = boundary or next_level is not None or (
                        following.strip().startswith("**") and following.strip().endswith("**")
                    )
                    if lead_in and next_index > index + 1 and not re.match(
                        r"\s*(?:[-*+>]\s|\d+[.)]\s|\|)", following
                    ):
                        boundary = True
                if boundary:
                    continue
            result.append(line)
        if result == kept:
            break
        kept = result
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


_NUMBERED_HEADING = re.compile(
    r"^(?P<prefix>\s*)(?:(?P<arabic>\d+)(?P<arabic_punct>[.)])"
    r"(?P<arabic_space>\s+)|(?P<chinese>[一二三四五六七八九十百千万]+)"
    r"(?P<chinese_punct>[、.])(?P<chinese_space>\s*)|(?P<roman>[IVXLCDM]+)"
    r"(?P<roman_punct>[.)])(?P<roman_space>\s+))(?P<body>.+?)\s*$",
    re.IGNORECASE,
)
_NUMBERED_LIST = re.compile(
    r"^(?P<indent>\s*)(?P<number>\d+|[一二三四五六七八九十百千万]+)"
    r"(?P<punct>[.、)])(?P<space>\s+)(?P<body>.+?)\s*$"
)
_DECIMAL_HEADING = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\s+(?P<body>.+?)\s*$"
)
_SEPARATOR = re.compile(r"^\s*(?P<char>[-*_])(?:\s*(?P=char)){2,}\s*$")
_CIRCLED_LIST = re.compile(r"^(?P<indent>\s*)(?P<number>[①②③④⑤⑥⑦⑧⑨⑩])(?P<space>\s+)(?P<body>.+?)\s*$")
_HEADING_COUNT_CLAIM = re.compile(
    r"[（(]\s*\d+\s*个[^）)]{0,24}(?:指标|要点|项目|因素|维度|信号)[^）)]*[）)]"
)


def _chinese_ordinal(value: int) -> str:
    """Render the small ordinal range used by report headings in Chinese."""
    digits = "零一二三四五六七八九"
    if value < 10:
        return digits[value]
    if value < 20:
        return "十" if value == 10 else "十" + digits[value - 10]
    if value < 100:
        tens, ones = divmod(value, 10)
        return digits[tens] + "十" + (digits[ones] if ones else "")
    return str(value)


def _ordinal_value(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    chinese = {char: index for index, char in enumerate("零一二三四五六七八九")}
    if value in chinese:
        return chinese[value]
    if value == "十":
        return 10
    if len(value) == 2 and value[0] == "十" and value[1] in chinese:
        return 10 + chinese[value[1]]
    if len(value) == 2 and value[1] == "十" and value[0] in chinese:
        return chinese[value[0]] * 10
    if len(value) == 3 and value[1] == "十" and value[0] in chinese and value[2] in chinese:
        return chinese[value[0]] * 10 + chinese[value[2]]
    return None


def _roman_value(value: str) -> int | None:
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    previous = 0
    for char in reversed(value.upper()):
        current = values.get(char)
        if current is None:
            return None
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total


def _heading_number_kind(match: re.Match[str]) -> str:
    if match.group("arabic") is not None:
        return "arabic"
    if match.group("chinese") is not None:
        return "chinese"
    return "roman"


def _reset_heading_counters(
    counters: dict[tuple[int, tuple[str, ...], str], int],
    level: int,
    parent: tuple[str, ...],
) -> None:
    for key in tuple(counters):
        if key[0] == level and key[1] == parent:
            counters.pop(key, None)


def _is_ordinary_year(value: str) -> bool:
    return value.isdigit() and len(value) == 4 and 1900 <= int(value) <= 2100


def _normalize_heading_numbers(lines: list[str]) -> list[str]:
    """Renumber numbered sibling headings without touching ordinary numbers."""
    output = list(lines)
    stack: list[tuple[int, str, int | None]] = []
    counters: dict[tuple[int, tuple[str, ...], str], int] = {}
    for index, line in enumerate(output):
        level = _heading_level(line)
        if level is None:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = tuple(title for _, title, _ in stack)
        decimal = _DECIMAL_HEADING.match(match.group(2))
        if decimal:
            key = (level, parent, "decimal")
            counters[key] = counters.get(key, 0) + 1
            minor = counters[key]
            parent_number = next(
                (number for _, _, number in reversed(stack) if number is not None),
                None,
            )
            major = parent_number or int(decimal.group("major"))
            line_ending = "\n" if line.endswith("\n") else ""
            output[index] = (
                f"{match.group(1)} {major}.{minor} {decimal.group('body')}"
                f"{line_ending}"
            )
            stack.append((level, f"{major}.{minor} {decimal.group('body')}", minor))
            continue
        numbered = _NUMBERED_HEADING.match(match.group(2))
        kind = _heading_number_kind(numbered) if numbered else "plain"
        key = (level, parent, kind)
        if numbered and _is_ordinary_year(numbered.group("arabic") or ""):
            numbered = None
            kind = "plain"
            key = (level, parent, kind)
        # A non-numbered sibling starts a new heading sequence.
        heading_number: int | None = None
        if not numbered:
            _reset_heading_counters(counters, level, parent)
        else:
            counters[key] = counters.get(key, 0) + 1
            number = counters[key]
            heading_number = number
            kind = _heading_number_kind(numbered)
            replacement = (
                str(number)
                if kind == "arabic"
                else _chinese_ordinal(number)
                if kind == "chinese"
                else _int_to_roman(number)
            )
            body = numbered.group("body")
            punctuation = (
                numbered.group("arabic_punct")
                or numbered.group("chinese_punct")
                or numbered.group("roman_punct")
                or ""
            )
            space = (
                numbered.group("arabic_space")
                or numbered.group("chinese_space")
                or numbered.group("roman_space")
                or ""
            )
            line_ending = "\n" if line.endswith("\n") else ""
            output[index] = (
                f"{match.group(1)} {replacement}{punctuation}"
                f"{space}{body}{line_ending}"
            )
        stack.append((level, output[index].strip().lstrip("# "), heading_number))
    return output


def _normalize_heading_count_claims(lines: list[str]) -> list[str]:
    output = list(lines)
    for index, line in enumerate(output):
        level = _heading_level(line)
        if level is None:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)(\n?)$", line)
        if not match:
            continue
        body = _HEADING_COUNT_CLAIM.sub("", match.group(2)).strip()
        if body != match.group(2):
            output[index] = f"{match.group(1)} {body}{match.group(3)}"
    return output


def _int_to_roman(value: int) -> str:
    parts = ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
    result = []
    for unit, symbol in parts:
        count, value = divmod(value, unit)
        result.append(symbol * count)
    return "".join(result)


def _normalize_circled_lists(lines: list[str]) -> list[str]:
    output = list(lines)
    index = 0
    while index < len(output):
        match = _CIRCLED_LIST.match(output[index])
        if not match:
            index += 1
            continue
        indent = match.group("indent")
        current = index
        number = 1
        while current < len(output):
            item = _CIRCLED_LIST.match(output[current])
            if item and item.group("indent") == indent:
                symbol = "①②③④⑤⑥⑦⑧⑨⑩"[number - 1] if number <= 10 else str(number)
                ending = "\n" if output[current].endswith("\n") else ""
                output[current] = f"{indent}{symbol}{item.group('space')}{item.group('body')}{ending}"
                number += 1
                current += 1
                continue
            if not output[current].strip() and current + 1 < len(output):
                next_item = _CIRCLED_LIST.match(output[current + 1])
                if next_item and next_item.group("indent") == indent:
                    current += 1
                    continue
            break
        index = max(current, index + 1)
    return output


def _normalize_numbered_lists(lines: list[str]) -> list[str]:
    """Renumber list items across their indented continuation paragraphs."""
    output = list(lines)
    index = 0
    while index < len(output):
        match = _NUMBERED_LIST.match(output[index])
        if not match or _is_ordinary_year(match.group("number")):
            index += 1
            continue
        indent = match.group("indent")
        style = match.group("number").isdigit()
        current = index
        number = 1
        while current < len(output):
            item = _NUMBERED_LIST.match(output[current])
            if (
                item
                and not _is_ordinary_year(item.group("number"))
                and item.group("indent") == indent
                and item.group("number").isdigit() == style
            ):
                replacement = str(number) if style else _chinese_ordinal(number)
                output[current] = (
                    f"{indent}{replacement}{item.group('punct')}"
                    f"{item.group('space')}{item.group('body')}"
                    f"{chr(10) if output[current].endswith(chr(10)) else ''}"
                )
                number += 1
                current += 1
                continue
            stripped = output[current].strip()
            if not stripped or len(output[current]) - len(output[current].lstrip()) > len(indent):
                current += 1
                continue
            break
        index = max(current, index + 1)
    return output


def _normalize_markdown_tables(lines: list[str]) -> list[str]:
    """Keep only well-formed Markdown table rows; never guess how to split a row."""
    result: list[str] = []
    index = 0
    while index < len(lines):
        if not lines[index].lstrip().startswith("|"):
            result.append(lines[index])
            index += 1
            continue
        start = index
        while index < len(lines) and lines[index].lstrip().startswith("|"):
            index += 1
        block = lines[start:index]
        if len(block) < 2 or not _is_table_separator(block[1]):
            continue
        header = [cell.strip() for cell in block[0].strip().strip("|").split("|")]
        separator = [cell.strip() for cell in block[1].strip().strip("|").split("|")]
        if not header or len(header) != len(separator):
            continue
        valid = block[:2]
        for row in block[2:]:
            cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
            minimum_content = 1 if len(header) == 1 else 2
            if len(cells) == len(header) and sum(bool(cell) for cell in cells) >= minimum_content:
                valid.append(row)
        if len(valid) > 2 and _table_has_sequence_column(header, valid[2:]):
            for sequence, row_index in enumerate(range(2, len(valid)), start=1):
                cells = [cell.strip() for cell in valid[row_index].strip().strip("|").split("|")]
                cells[0] = str(sequence)
                ending = "\n" if valid[row_index].endswith("\n") else ""
                valid[row_index] = "| " + " | ".join(cells) + " |" + ending
        if len(valid) > 2:
            if result and result[-1].strip():
                result.append("\n")
            result.extend(valid)
            # Python-Markdown's table extension continues a table across a
            # nonblank line.  A hard block boundary is therefore part of the
            # accepted Markdown contract, not cosmetic whitespace.
            if index < len(lines) and lines[index].strip():
                result.append("\n")
    return result


def _remove_orphan_structures(lines: list[str]) -> list[str]:
    """Remove markers and headings/lead-ins emptied by an earlier prune."""
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append(line)
            continue
        if _SEPARATOR.match(stripped):
            if not cleaned or cleaned[-1].strip() != "---":
                cleaned.append("---\n")
            continue
        if re.fullmatch(r"(?:[*_#>-]|\d+[.)]|[①②③④⑤⑥⑦⑧⑨⑩])+(?:\s*)", stripped):
            continue
        cleaned.append(line)

    # A heading or colon-ended lead-in with no following content is an orphan.
    changed = True
    while changed:
        changed = False
        output: list[str] = []
        for index, line in enumerate(cleaned):
            heading = _heading_level(line)
            label = line.strip().strip("*_ ")
            lead_in = label.endswith((":", "：")) and not line.lstrip().startswith("|")
            if heading is None and not lead_in:
                output.append(line)
                continue
            next_index = next(
                (
                    i
                    for i in range(index + 1, len(cleaned))
                    if cleaned[i].strip()
                    and _SEPARATOR.match(cleaned[i].strip()) is None
                ),
                len(cleaned),
            )
            following = cleaned[next_index] if next_index < len(cleaned) else ""
            next_level = _heading_level(following)
            boundary = not following
            if heading is not None:
                boundary = boundary or (next_level is not None and next_level <= heading)
            else:
                boundary = boundary or next_level is not None or re.fullmatch(r"(?:[-*+]|\d+[.)]|[①②③④⑤⑥⑦⑧⑨⑩])\s*", following.strip() or "") is not None
            if boundary:
                changed = True
                continue
            output.append(line)
        cleaned = output
    return cleaned


def normalize_markdown_structure(text: str) -> str:
    """Normalize user-facing Markdown after semantic pruning.

    This function only changes Markdown structure: numbering, table shape,
    separators, and empty fragments. It never edits facts or their values.
    """
    if not text:
        return ""
    lines = [line + "\n" for line in text.splitlines()]
    lines = _normalize_markdown_tables(lines)
    lines = _normalize_heading_numbers(lines)
    lines = _normalize_heading_count_claims(lines)
    lines = _normalize_numbered_lists(lines)
    lines = _normalize_circled_lists(lines)
    lines = _remove_orphan_structures(lines)
    # Collapse separators once more after orphan removal.
    output: list[str] = []
    last_nonblank_separator = False
    for line in lines:
        if _SEPARATOR.match(line.strip()):
            if last_nonblank_separator:
                while output and not output[-1].strip():
                    output.pop()
                continue
            output.append("---\n")
            last_nonblank_separator = True
        else:
            output.append(line)
            if line.strip():
                last_nonblank_separator = False
    return re.sub(r"\n{3,}", "\n\n", "".join(output)).strip()


def _markdown_structure_issues(text: str) -> list[str]:
    """Return structural defects that must never reach a user artifact."""
    lines = text.splitlines()
    issues: list[str] = []

    normalized_heading_lines = _normalize_heading_numbers(
        [line + "\n" for line in lines]
    )
    if any(
        original.strip() != normalized.strip()
        for original, normalized in zip(lines, normalized_heading_lines, strict=True)
        if _heading_level(original) is not None
    ):
        issues.append("HEADING_NUMBERING_DISCONTINUITY")

    # Heading sequence validation mirrors the renumbering state machine.
    stack: list[tuple[int, str]] = []
    counters: dict[tuple[int, tuple[str, ...], str], int] = {}
    for line in lines:
        level = _heading_level(line)
        if level is None:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = tuple(title for _, title in stack)
        numbered = _NUMBERED_HEADING.match(match.group(2))
        kind = _heading_number_kind(numbered) if numbered else "plain"
        key = (level, parent, kind)
        if numbered and _is_ordinary_year(numbered.group("arabic") or ""):
            numbered = None
            kind = "plain"
            key = (level, parent, kind)
        if not numbered:
            _reset_heading_counters(counters, level, parent)
        else:
            counters[key] = counters.get(key, 0) + 1
            raw_number = numbered.group("arabic") or numbered.group("chinese") or numbered.group("roman") or ""
            parsed = (
                _ordinal_value(raw_number)
                if kind != "roman"
                else _roman_value(raw_number)
            )
            if parsed != counters[key]:
                issues.append("HEADING_NUMBERING_DISCONTINUITY")
        if _HEADING_COUNT_CLAIM.search(match.group(2)):
            issues.append("STALE_HEADING_COUNT_CLAIM")
        stack.append((level, match.group(2)))

    # Numbered list blocks must start at one and increase without gaps.
    index = 0
    while index < len(lines):
        match = _NUMBERED_LIST.match(lines[index])
        if not match or _is_ordinary_year(match.group("number")):
            index += 1
            continue
        indent = match.group("indent")
        style = match.group("number").isdigit()
        expected = 1
        current = index
        while current < len(lines):
            item = _NUMBERED_LIST.match(lines[current])
            if (
                item
                and not _is_ordinary_year(item.group("number"))
                and item.group("indent") == indent
                and item.group("number").isdigit() == style
            ):
                parsed = _ordinal_value(item.group("number"))
                if parsed != expected:
                    issues.append("NUMBERED_LIST_DISCONTINUITY")
                expected += 1
                current += 1
                continue
            stripped = lines[current].strip()
            if not stripped or len(lines[current]) - len(lines[current].lstrip()) > len(indent):
                current += 1
                continue
            break
        index = max(current, index + 1)

    # Circled-number lead-ins are list items too, even when they are not
    # rendered as Markdown list markers.
    index = 0
    circled = "①②③④⑤⑥⑦⑧⑨⑩"
    while index < len(lines):
        match = _CIRCLED_LIST.match(lines[index])
        if not match:
            index += 1
            continue
        indent = match.group("indent")
        expected = 1
        current = index
        while current < len(lines):
            item = _CIRCLED_LIST.match(lines[current])
            if item and item.group("indent") == indent:
                if circled.index(item.group("number")) + 1 != expected:
                    issues.append("CIRCLED_NUMBER_DISCONTINUITY")
                expected += 1
                current += 1
                continue
            if not lines[current].strip() and current + 1 < len(lines):
                next_item = _CIRCLED_LIST.match(lines[current + 1])
                if next_item and next_item.group("indent") == indent:
                    current += 1
                    continue
            break
        index = max(current, index + 1)

    for start, end in _table_blocks(lines, 0, len(lines)):
        block = lines[start:end]
        if len(block) < 2 or not _is_table_separator(block[1]):
            issues.append("MALFORMED_MARKDOWN_TABLE")
            continue
        header_count = len(block[0].strip().strip("|").split("|"))
        separator_count = len(block[1].strip().strip("|").split("|"))
        if header_count != separator_count:
            issues.append("MALFORMED_MARKDOWN_TABLE")
            continue
        for row in block[2:]:
            cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
            minimum_content = 1 if header_count == 1 else 2
            if len(cells) != header_count:
                issues.append("MALFORMED_MARKDOWN_TABLE")
                break
            if sum(bool(cell) for cell in cells) < minimum_content:
                issues.append("EMPTY_MARKDOWN_TABLE_ROW")
                break
        header = [cell.strip() for cell in block[0].strip().strip("|").split("|")]
        rows = block[2:]
        if _table_has_sequence_column(header, rows):
            values = [int(row.strip().strip("|").split("|")[0].strip()) for row in rows]
            if values != list(range(1, len(values) + 1)):
                issues.append("NUMBERED_TABLE_DISCONTINUITY")

    meaningful = [line.strip() for line in lines if line.strip()]
    for previous, current in zip(meaningful, meaningful[1:], strict=False):
        if _SEPARATOR.match(previous) and _SEPARATOR.match(current):
            issues.append("DUPLICATE_SEPARATOR")
            break

    for index, line in enumerate(lines):
        if re.fullmatch(r"\s*(?:[*_#>-]|\d+[.)]|[①②③④⑤⑥⑦⑧⑨⑩])\s*", line):
            issues.append("ORPHAN_MARKDOWN_MARKER")
            break
        heading = _heading_level(line)
        if heading is None:
            continue
        next_index = next(
            (
                i
                for i in range(index + 1, len(lines))
                if lines[i].strip() and _SEPARATOR.match(lines[i].strip()) is None
            ),
            len(lines),
        )
        if next_index == len(lines):
            issues.append("ORPHAN_HEADING")
            break
        following = lines[next_index]
        next_level = _heading_level(following)
        if next_level is not None and next_level <= heading:
            issues.append("ORPHAN_HEADING")
            break
    return list(dict.fromkeys(issues))


def _drop_empty_table_subsections(lines: list[str]) -> list[str]:
    """Remove the nearest Markdown subsection when all of its tables are empty.

    Work from the deepest heading upward so an empty H3 is removed without
    discarding a valid sibling table in its H2 parent.  This is structural
    cleanup: it does not inspect company names, metrics, or prose wording.
    """
    result = list(lines)
    for level in range(6, 0, -1):
        headings = [
            index
            for index, line in enumerate(result)
            if _heading_level(line) == level
        ]
        ranges: list[tuple[int, int]] = []
        for start in headings:
            end = len(result)
            for index in range(start + 1, len(result)):
                next_level = _heading_level(result[index])
                if next_level is not None and next_level <= level:
                    end = index
                    break
            table_blocks = _table_blocks(result, start + 1, end)
            if table_blocks and not any(
                _table_has_data_row(result[table_start:table_end])
                for table_start, table_end in table_blocks
            ):
                ranges.append((start, end))
        for start, end in reversed(ranges):
            del result[start:end]

    for start, end in reversed(_table_blocks(result, 0, len(result))):
        if not _table_has_data_row(result[start:end]):
            del result[start:end]
    return result


def _heading_level(line: str) -> int | None:
    match = re.match(r"^(#{1,6})\s+", line)
    return len(match.group(1)) if match else None


def _table_blocks(
    lines: list[str], start: int, end: int
) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    index = start
    while index < end:
        if not lines[index].lstrip().startswith("|"):
            index += 1
            continue
        table_start = index
        while index < end and lines[index].lstrip().startswith("|"):
            index += 1
        blocks.append((table_start, index))
    return blocks


def _table_has_data_row(lines: list[str]) -> bool:
    separator_seen = False
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if _is_table_separator(line):
            separator_seen = True
            continue
        minimum_content = 1 if len(cells) == 1 else 2
        if separator_seen and sum(bool(cell) for cell in cells) >= minimum_content:
            return True
    return False


def _table_has_sequence_column(header: list[str], rows: list[str]) -> bool:
    if not header or not re.fullmatch(r"(?:序号|編號|编号|no\.?|#)", header[0], re.I):
        return False
    values = [row.strip().strip("|").split("|")[0].strip() for row in rows]
    return bool(values) and all(value.isdigit() for value in values)


def _is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _withhold_unvalidated_execution(state: dict[str, Any]) -> dict[str, Any]:
    # Every potentially displayed Agent field obeys the same policy, including
    # Research Manager and legacy investment_plan aliases.
    result = _map_report_text(state, _prune_unapproved_execution)
    decision = str(result.get("final_trade_decision") or "")
    gate = "## 执行许可\n\n" + _EXECUTION_WITHHELD
    accepted = (decision.rstrip() + "\n\n" + gate).strip() if decision else gate
    result["trader_investment_plan"] = "## 交易执行状态\n\n" + _TRADER_WITHHELD
    result["final_trade_decision"] = accepted
    risk = result.get("risk_debate_state")
    if isinstance(risk, Mapping):
        risk = dict(risk)
        risk["judge_decision"] = accepted
        result["risk_debate_state"] = risk
    return result


def _execution_violation(text: str) -> str | None:
    plain = text.replace("**", "").replace("`", "")
    if _POSITION_DIRECTIVE.search(plain):
        return "POSITION_SIZE_RECOMMENDATION"
    if _POSITION_RECOMMENDATION.search(plain):
        return "POSITION_SIZE_RECOMMENDATION"
    if _EXECUTION_INSTRUCTIONS.search(plain):
        return "UNAPPROVED_EXECUTION_INSTRUCTION"
    if _EXECUTION_ACTION.search(plain) and _EXECUTION_DIRECTIVE_CONTEXT.search(plain):
        return "UNAPPROVED_EXECUTION_INSTRUCTION"
    if _EXECUTION_PARAMETER.search(plain):
        # A sourced valuation target is an analytical fact, not a trade exit.
        if re.search(r"(?:分析师|券商|consensus|analyst|broker).*(?:目标价|target)", plain, re.I):
            return None
        return "UNVALIDATED_EXECUTABLE_PLAN"
    return None


def _prune_unapproved_execution(text: str) -> str:
    text = _filter_markdown_sections(
        text,
        lambda heading: not _is_execution_heading(heading),
    )
    lines = []
    for line in text.splitlines():
        if line.strip() in {_TRADER_WITHHELD, _EXECUTION_WITHHELD}:
            lines.append(line)
            continue
        # A structured execution field is one logical unit. Splitting it at a
        # sentence boundary can orphan the dependent trigger clause and make
        # an unapproved plan appear without its label.
        if _EXECUTION_LINE.search(line):
            continue
        if line.lstrip().startswith("|"):
            if not _execution_violation(line):
                lines.append(line)
            continue
        # Remove complete clauses, retaining independent directional judgments.
        clauses = re.split(r"(?<=[。！？；;])", line)
        lines.append("".join(clause for clause in clauses if not _execution_violation(clause)))
    return _fold_empty_sections("\n".join(lines))


def _apply_validated_execution(state: dict[str, Any]) -> dict[str, Any]:
    """Expose only calculator-approved execution numbers in the user artifact."""
    result = dict(state)
    validation = result.get("validated_execution") or {}
    decision = _strip_execution_plan(
        str(result.get("final_trade_decision") or ""), include_target=True
    )
    block = _validated_execution_report(validation)
    result["trader_investment_plan"] = block
    result["final_trade_decision"] = (decision.rstrip() + "\n\n" + block).strip()
    for field in ("market_report", "sentiment_report", "news_report", "fundamentals_report"):
        if isinstance(result.get(field), str):
            result[field] = _remove_unapproved_execution_lines(result[field])
    research = result.get("investment_debate_state")
    if isinstance(research, Mapping):
        research = dict(research)
        if isinstance(research.get("judge_decision"), str):
            research["judge_decision"] = _remove_execution_sections(
                research["judge_decision"]
            )
        result["investment_debate_state"] = research
    risk = result.get("risk_debate_state")
    if isinstance(risk, Mapping):
        risk = dict(risk)
        risk["judge_decision"] = result["final_trade_decision"]
        result["risk_debate_state"] = risk
    return result


def _validated_execution_report(validation: Mapping[str, Any]) -> str:
    lines = [
        "## 已验证交易执行参数",
        "",
        "| 项目 | 确定性校验值 |",
        "|---|---:|",
        f"| 入场价 | {validation.get('entry')} |",
        f"| 止损价 | {validation.get('stop')} |",
        f"| 止损距离 | {validation.get('distance')} |",
        f"| 入场至止损风险 | {validation.get('risk_pct')}% |",
    ]
    if validation.get("position_pct") is not None:
        lines.append(f"| 仓位 | {validation['position_pct']}% |")
    if validation.get("portfolio_stop_risk_pct") is not None:
        lines.append(
            f"| 组合止损风险 | {validation['portfolio_stop_risk_pct']}% |"
        )
    return "\n".join(lines)


def _remove_jsf_agent_claims(text: str) -> str:
    """Keep JSF observable facts in its deterministic source section only."""
    text = _filter_markdown_sections(
        text, lambda heading: _JSF_CLAIM.search(heading) is None
    )
    lines = []
    for line in text.splitlines():
        if _JSF_CLAIM.search(line) and not line.lstrip().startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _remove_execution_sections(text: str) -> str:
    text = _filter_markdown_sections(
        text,
        lambda heading: not _is_execution_heading(heading),
    )
    lines = []
    for line in text.splitlines():
        if _EXECUTION_LINE.search(line):
            continue
        lines.append(line)
    return _fold_empty_sections("\n".join(lines))


def _is_execution_heading(heading: str) -> bool:
    """Classify execution sections without treating valuation facts as plans."""
    folded = heading.strip().casefold()
    if folded in _WITHHELD_EXECUTION_HEADINGS:
        return False
    if re.search(r"(?:分析师|券商|consensus|analyst|broker).*(?:目标价|target)", folded):
        return False
    return _EXECUTION_HEADING.search(folded) is not None


def _strip_execution_plan(text: str, *, include_target: bool) -> str:
    text = _remove_execution_sections(text)
    return _remove_unapproved_execution_lines(text, include_target=include_target)


def _remove_unapproved_execution_lines(
    text: str, *, include_target: bool = False
) -> str:
    """Remove numeric execution instructions, while retaining factual target news."""
    terms = r"entry|stop(?:[ -]?loss)?|position(?: sizing)?|入场|建仓|止损|止盈|仓位"
    if include_target:
        terms += r"|price target|目标价|第一目标|第二目标"
    execution_term = re.compile(rf"(?:{terms})", re.I)
    numeric = re.compile(r"\d")
    lines = [
        line
        for line in text.splitlines()
        if not (execution_term.search(line) and numeric.search(line))
    ]
    return _fold_empty_sections("\n".join(lines))


def _publicize_state_text(state: dict[str, Any]) -> dict[str, Any]:
    def publicize(text: str) -> str:
        for internal, display in _PUBLIC_NEWS_TEXT.items():
            text = text.replace(internal, display)
        for internal in sorted(_INTERNAL_STATUS, key=len, reverse=True):
            text = re.sub(
                rf"(?<![A-Za-z0-9_]){re.escape(internal)}(?![A-Za-z0-9_])",
                _INTERNAL_STATUS[internal],
                text,
            )
        text = text.replace("DATA UNAVAILABLE", "数据不可用")
        text = _localize_presentation_labels(text)
        return _fold_empty_sections(text)

    return _map_report_text(state, publicize)


def _localize_presentation_labels(text: str) -> str:
    """Translate standard report labels without rewriting business prose."""
    output: list[str] = []
    labels = "|".join(re.escape(label) for label in _PRESENTATION_HEADING_TRANSLATIONS)
    pattern = re.compile(
        rf"(?i)(?P<prefix>^(?:#{{1,6}}\s+)?|^\*\*)"
        rf"(?P<label>{labels})(?P<suffix>\*\*)?(?P<colon>\s*[:：])?"
    )
    for line in text.splitlines():
        match = pattern.search(line)
        if match:
            source = match.group("label").casefold()
            translated = _PRESENTATION_HEADING_TRANSLATIONS[source]
            line = line[: match.start("label")] + translated + line[match.end("label") :]
        # A model may put a Markdown heading in the value of a labelled field,
        # e.g. ``**Strategic Actions**: ## Plan``.  Once it is inline the
        # heading marker has no structural meaning and would be printed raw.
        line = re.sub(r"([:：])\s*#{1,6}\s+", r"\1 ", line)
        output.append(line)
    return "\n".join(output)


def _map_report_text(state: Mapping[str, Any], transform) -> dict[str, Any]:
    result = dict(state)
    for field in _REPORT_FIELDS:
        if isinstance(result.get(field), str):
            result[field] = transform(result[field])
    for field in _DEBATE_FIELDS:
        debate = result.get(field)
        if isinstance(debate, Mapping):
            result[field] = {
                key: transform(value) if isinstance(value, str) else value
                for key, value in debate.items()
            }
    return result


def _validate_final_artifact(
    state: Mapping[str, Any],
    execution_allowed: bool,
    *,
    accepted_report: str,
) -> list[str]:
    # The accepted report is the sole user artifact.  Raw reports and debate
    # histories remain available in full_agent_log for technical audit, but
    # validating those omitted fragments here would let non-published prose
    # falsely block a clean canonical artifact.
    issues = validate_final_report_text(
        accepted_report, execution_allowed=execution_allowed, check_structure=True
    )
    issues.extend(_domain_authority_issues(state))
    rendered = render_markdown_fragment(accepted_report)
    issues.extend(validate_rendered_html(rendered))
    return list(dict.fromkeys(issues))


def validate_final_report_text(
    text: str, *, execution_allowed: bool, check_structure: bool = True
) -> list[str]:
    """Detect violations in accepted text or a composed artifact; never edit it."""
    issues: list[str] = []
    for token in _INTERNAL_STATUS:
        if re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", text
        ):
            issues.append(f"INTERNAL_STATUS_VISIBLE:{token}")
    for token in _MACHINE_ENUM.findall(text):
        issues.append(f"INTERNAL_MACHINE_ENUM_VISIBLE:{token}")
    if re.search(r"(?:无|没有|缺乏)(?:明显|任何)?(?:轧空|軋空|short[ -]?squeeze)", text, re.I):
        issues.append("SHORT_MARKET_OVERCLAIM")
    if _remove_generation_process_prose(text) != text:
        issues.append("PROCESS_PROSE_VISIBLE")
    if _unlocalized_presentation_label(text):
        issues.append("UNLOCALIZED_PRESENTATION_LABEL")
    if re.search(r"[:：]\s*#{1,6}\s+", text):
        issues.append("INLINE_MARKDOWN_HEADING_VISIBLE")
    for internal in _PUBLIC_NEWS_TEXT:
        if internal in text:
            issues.append("UNLOCALIZED_NEWS_AUTHORITY")
    for line in text.splitlines():
        if line.strip() in {_TRADER_WITHHELD, _EXECUTION_WITHHELD}:
            continue
        if not execution_allowed:
            if _heading_level(line) and _is_execution_heading(line.lstrip("# ")):
                issues.append("UNAPPROVED_EXECUTION_SECTION")
            if _EXECUTION_LINE.search(line):
                issues.append("UNVALIDATED_EXECUTABLE_PLAN")
            violation = _execution_violation(line)
            if violation:
                issues.append(violation)
    if _clean_empty_markdown(text) != re.sub(r"\n{3,}", "\n\n", text).strip():
        issues.append("EMPTY_MARKDOWN_STRUCTURE")
    lines = text.splitlines()
    if any(not _table_has_data_row(lines[start:end]) for start, end in _table_blocks(lines, 0, len(lines))):
        issues.append("EMPTY_MARKDOWN_TABLE")
    if check_structure:
        issues.extend(_markdown_structure_issues(text))
    return list(dict.fromkeys(issues))


def _domain_authority_issues(state: Mapping[str, Any]) -> list[str]:
    """Ensure an authoritative aggregate is published in exactly one domain."""
    issues: list[str] = []
    for field in _REPORT_FIELDS:
        if field == "sentiment_report":
            continue
        value = state.get(field)
        if isinstance(value, str) and _SENTIMENT_AUTHORITY.search(value):
            issues.append(f"CROSS_DOMAIN_AUTHORITY:SENTIMENT:{field}")
    for outer in _DEBATE_FIELDS:
        value = state.get(outer)
        if not isinstance(value, Mapping):
            continue
        for inner, text in value.items():
            if isinstance(text, str) and _SENTIMENT_AUTHORITY.search(text):
                issues.append(f"CROSS_DOMAIN_AUTHORITY:SENTIMENT:{outer}.{inner}")
    return issues


def _unlocalized_presentation_label(text: str) -> bool:
    labels = "|".join(re.escape(label) for label in _PRESENTATION_HEADING_TRANSLATIONS)
    return re.search(
        rf"(?im)^(?:#{{1,6}}\s+|\*\*)?(?:{labels})(?:\*\*)?\s*(?::|$)",
        text,
    ) is not None


def _published_field_text(state: Mapping[str, Any], field: str) -> str | None:
    if field in {"market_report", "sentiment_report", "news_report", "fundamentals_report"}:
        value = state.get(field)
        return value if isinstance(value, str) else None
    if field == "trader_investment_plan":
        value = state.get(field)
        return value if isinstance(value, str) else None
    if field in {"investment_debate_state.judge_decision", "risk_debate_state.judge_decision"}:
        outer, inner = field.split(".", 1)
        container = state.get(outer)
        value = container.get(inner) if isinstance(container, Mapping) else None
        return value if isinstance(value, str) else None
    return None


def _finalize_audit(
    audit: list[dict[str, Any]],
    state: Mapping[str, Any],
    *,
    accepted_report: str,
    execution_allowed: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Close findings only when their concrete claim is absent or supported.

    A warning code identifies a rule, not a claim.  Re-running that rule over a
    whole report field can therefore falsely close one finding after a sibling
    clause was removed.  Closure follows the recorded original/replacement
    claim identity and the exact accepted artifact instead.
    """
    finalized: list[dict[str, Any]] = []
    issues: list[str] = []
    seen: set[tuple[Any, ...]] = set()
    for raw in audit:
        entry = dict(raw)
        if entry.get("category") == "EXECUTION_GATE":
            execution_issues = (
                []
                if execution_allowed
                else [
                    issue
                    for issue in validate_final_report_text(
                        accepted_report,
                        execution_allowed=False,
                        check_structure=False,
                    )
                    if issue.startswith(
                        ("UNAPPROVED_", "UNVALIDATED_", "POSITION_SIZE_")
                    )
                ]
            )
            if execution_issues:
                entry["resolution"] = "UNRESOLVED"
                entry["execution_blocking"] = True
                issues.extend(f"UNRESOLVED_EXECUTION:{issue}" for issue in execution_issues)
            else:
                entry["resolution"] = "EXECUTABLE_PLAN_WITHHELD"
                entry["resolution_basis"] = "EXACT_ACCEPTED_ARTIFACT_HAS_NO_EXECUTABLE_PLAN"
                entry["execution_blocking"] = False
        elif entry.get("category") in _VIOLATION_CATEGORIES or entry.get("warning"):
            field = str(entry.get("field") or "")
            published = _published_field_text(state, field)
            if published is None:
                entry["resolution"] = "NOT_PUBLISHED_IN_FINAL_ARTIFACT"
                entry["resolution_basis"] = "FIELD_EXCLUDED_FROM_ACCEPTED_REPORT"
                entry["execution_blocking"] = False
            else:
                warning = entry.get("warning")
                entry["published_field_sha256"] = hashlib.sha256(
                    published.encode("utf-8")
                ).hexdigest()
                entry["accepted_artifact_sha256"] = hashlib.sha256(
                    accepted_report.encode("utf-8")
                ).hexdigest()
                original = str(entry.get("original_claim") or "").strip()
                replacement = str(entry.get("replacement_claim") or "").strip()
                original_published = bool(
                    original and _claim_is_published(original, accepted_report)
                )
                replacement_published = bool(
                    replacement and _claim_is_published(replacement, accepted_report)
                )
                replacement_supported = bool(
                    replacement_published
                    and warning
                    and warning
                    not in enforce_agent_output(
                        state, replacement, _agent_for_field(field)
                    ).warnings
                )
                if warning and original and not original_published and (
                    not replacement_published or replacement_supported
                ):
                    entry["resolution"] = "CLAIM_REMOVED_OR_REPLACED"
                    entry["resolution_basis"] = (
                        "ORIGINAL_CLAIM_ABSENT_FROM_EXACT_ACCEPTED_ARTIFACT"
                        if not replacement_published
                        else "SUPPORTED_REPLACEMENT_PRESENT_IN_EXACT_ACCEPTED_ARTIFACT"
                    )
                    entry["execution_blocking"] = False
                elif not warning and entry.get("category") == "ARITHMETIC_MISMATCH":
                    entry["resolution"] = "REPLACED_BY_VALIDATED_EXECUTION"
                    entry["resolution_basis"] = "DETERMINISTIC_EXECUTION_BLOCK"
                    entry["execution_blocking"] = False
                else:
                    entry["resolution"] = "UNRESOLVED"
                    entry["execution_blocking"] = True
                    issue = f"UNRESOLVED_EVIDENCE:{field}:{warning or entry.get('category')}"
                    issues.append(issue)
        key = (
            entry.get("category"),
            entry.get("agent"),
            entry.get("field"),
            entry.get("warning"),
            entry.get("claim_sha256"),
            entry.get("detail"),
        )
        if key not in seen:
            finalized.append(entry)
            seen.add(key)
    return finalized, list(dict.fromkeys(issues))


def _claim_is_published(claim: str, accepted_report: str) -> bool:
    """Match one whole published claim, never a substring of another claim."""
    normalized_claim = re.sub(r"\s+", " ", claim).strip()
    if not normalized_claim:
        return False
    candidates: list[str] = []
    for line in accepted_report.splitlines():
        normalized_line = re.sub(r"\s+", " ", line).strip()
        if normalized_line:
            candidates.append(normalized_line)
        candidates.extend(
            re.sub(r"\s+", " ", clause).strip()
            for clause in re.split(r"(?<=[。！？；;.!?])", line)
            if clause.strip()
        )
    return normalized_claim in candidates
