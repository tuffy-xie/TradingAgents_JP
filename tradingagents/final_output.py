"""Build the single accepted user-facing state after all agents have run.

Agents keep ownership of reasoning, while source authority, evidence auditing,
and execution validation remain machine contracts.  This module is the sole
place where those contracts are applied to the final user-visible prose.  The
original agent output is retained separately for the technical agent log.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from tradingagents.agents.utils.evidence_enforcement import enforce_agent_output
from tradingagents.dataflows.japan.context import render_japan_financial_report

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
_CONTRACT_VERSION = "v2"
_VIOLATION_CATEGORIES = {
    "UNSUPPORTED_CLAIM",
    "STALE_EVIDENCE_USE",
    "FUTURE_DATA_USE",
    "SEMANTIC_MISMATCH",
    "UNIT_MISMATCH",
    "ARITHMETIC_MISMATCH",
}
_CURRENT_FINANCIAL_HEADINGS = (
    "latest actual",
    "current company guidance",
    "japan financial authority assessment",
    "最新季度业绩",
    "最新实际",
    "最新实绩",
    "管理层指引",
    "公司指引",
)
_EXECUTION_HEADINGS = (
    "trading plan",
    "execution plan",
    "trade parameters",
    "可操作交易计划",
    "可操作投资计划",
    "具体行动建议",
    "investment execution plan",
    "交易参数",
    "入场",
    "止损",
    "止盈",
    "仓位",
)
_EXECUTION_LINE = re.compile(
    r"(?:entry(?: price)?|stop(?:[ -]?loss)?|price target|position(?: sizing)?|"
    r"入场(?:价)?|建仓价|止损(?:价)?|止盈|目标价|仓位(?:上限)?)\s*[:：|]",
    re.I,
)
_JSF_CLAIM = re.compile(r"(?:\bJSF\b|日证金|日證金|貸株|贷株)", re.I)
_EXECUTION_INSTRUCTIONS = re.compile(
    r"(?:严守|严格|设置|设定|执行|触发|强制)?(?:止损|止盈)|强制离场|"
    r"(?:小|轻|重|试探|少量)[仓倉](?:位)?|"
    r"(?:建议|可以|可|应|宜|考虑|确认后|破位后|逢高|择机|伺机|开始|建立)[^。；;\n]{0,80}(?:做空|试空|试多|开空|开多|建仓|入场|介入|进场)|"
    r"(?:做空|建仓|入场|开空|开多)[^。；;\n]{0,80}(?:条件|触发|执行|建议)|"
    r"可执行方案|执行纪律|(?:仓位|敞口)[^。；;\n]{0,8}(?:纪律|配置)|价格触发条件|"
    r"\b(?:enter|initiate|open|take)\b[^.;\n]{0,30}\b(?:short|long|position|trade)\b|"
    r"\b(?:stop[ -]?loss|small position|forced exit)\b",
    re.I,
)
_POSITION_RECOMMENDATION = re.compile(
    r"(?:仓位|倉位|净敞口|净暴露|组合总值|position(?: size| sizing)?|net exposure|allocation)"
    r"[^。；;\n]*(?:\d|%|≤|≥|上限|不超过)|"
    r"\d[\d.]*\s*%[^。；;\n]*(?:组合|portfolio|position|仓位(?:配置|分配|上限))",
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
}


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
    audit = list(result.get("evidence_audit") or [])

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

    financial = render_japan_financial_report(result)
    if financial:
        base = _remove_current_financial_sections(result.get("fundamentals_report", ""))
        result["fundamentals_report"] = _fold_empty_sections(
            (base.rstrip() + "\n\n" + financial).strip()
        )

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
                "resolution": "EXECUTABLE_PLAN_WITHHELD",
                "execution_blocking": False,
            }
        )

    result = _publicize_state_text(result)
    artifact_issues = _validate_final_artifact(result, execution_allowed)
    audit = _finalize_audit(audit, artifact_ok=not artifact_issues)
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
    if result.get("trader_investment_plan"):
        # Archived web states use this historical key. Keep it as a display
        # alias of the accepted plan so raw legacy prose cannot bypass the gate.
        result["trader_investment_decision"] = result["trader_investment_plan"]
    result["final_output_contract"] = {
        "version": _CONTRACT_VERSION,
        "status": "FINALIZED" if not artifact_issues else "BLOCKED",
        "authority": "CANONICAL_FINAL_STATE",
        "execution_allowed": execution_allowed,
        "audit_finalized_after_artifact_validation": True,
        "artifact_issues": artifact_issues,
    }
    return result


def require_canonical_final_state(state: Mapping[str, Any]) -> None:
    """Fail closed when a JP renderer receives non-canonical business state."""
    if (state.get("market_context") or {}).get("market") != "JP":
        return
    contract = state.get("final_output_contract") or {}
    if contract.get("status") != "FINALIZED" or contract.get("version") != _CONTRACT_VERSION:
        raise ValueError("Japan report input is not an accepted canonical final state")


def _snapshot_agent_outputs(state: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = {key: copy.deepcopy(state.get(key)) for key in _REPORT_FIELDS}
    snapshot.update({key: copy.deepcopy(state.get(key)) for key in _DEBATE_FIELDS})
    return snapshot


def _accept_text(
    state: Mapping[str, Any], text: str, field: str, audit: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    agent = _agent_for_field(field)
    checked = enforce_agent_output(state, text, agent)
    for warning in checked.warnings:
        audit.append(
            {
                "category": _category_for_warning(warning),
                "agent": agent,
                "field": field,
                "warning": warning,
                "resolution": "PENDING_FINAL_ARTIFACT_VALIDATION",
                "execution_blocking": True,
            }
        )
    return _fold_empty_sections(_remove_jsf_agent_claims(checked.text)), audit


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
    return _clean_empty_markdown("".join(kept))


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
                    (i for i in range(index + 1, len(kept)) if kept[i].strip()), len(kept)
                )
                following = kept[next_index] if next_index < len(kept) else ""
                next_level = _heading_level(following)
                boundary = not following or following.strip() in {"---", "***", "___"}
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
        if separator_seen and any(cell for cell in cells):
            return True
    return False


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
    if _POSITION_RECOMMENDATION.search(plain):
        return "POSITION_SIZE_RECOMMENDATION"
    if _EXECUTION_INSTRUCTIONS.search(plain):
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
        lambda heading: not any(word in heading.casefold() for word in _EXECUTION_HEADINGS),
    )
    lines = []
    for line in text.splitlines():
        if line.strip() in {_TRADER_WITHHELD, _EXECUTION_WITHHELD}:
            lines.append(line)
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
    lines = []
    for line in text.splitlines():
        if _JSF_CLAIM.search(line) and not line.lstrip().startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _remove_execution_sections(text: str) -> str:
    text = _filter_markdown_sections(
        text,
        lambda heading: not any(
            token in heading.casefold() for token in _EXECUTION_HEADINGS
        ),
    )
    lines = []
    for line in text.splitlines():
        if _EXECUTION_LINE.search(line):
            continue
        lines.append(line)
    return _fold_empty_sections("\n".join(lines))


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
        for internal, display in _INTERNAL_STATUS.items():
            text = text.replace(internal, display)
        text = text.replace("DATA UNAVAILABLE", "数据不可用")
        return _fold_empty_sections(text)

    return _map_report_text(state, publicize)


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


def _validate_final_artifact(state: Mapping[str, Any], execution_allowed: bool) -> list[str]:
    parts: list[str] = []
    _map_report_text(state, lambda text: parts.append(text) or text)
    return validate_final_report_text("\n".join(parts), execution_allowed=execution_allowed)


def validate_final_report_text(text: str, *, execution_allowed: bool) -> list[str]:
    """Detect violations in accepted text or a composed artifact; never edit it."""
    issues: list[str] = []
    for token in _INTERNAL_STATUS:
        if token in text:
            issues.append(f"INTERNAL_STATUS_VISIBLE:{token}")
    if re.search(r"(?:无|没有|缺乏)(?:明显|任何)?(?:轧空|軋空|short[ -]?squeeze)", text, re.I):
        issues.append("SHORT_MARKET_OVERCLAIM")
    for internal in _PUBLIC_NEWS_TEXT:
        if internal in text:
            issues.append("UNLOCALIZED_NEWS_AUTHORITY")
    for line in text.splitlines():
        if line.strip() in {_TRADER_WITHHELD, _EXECUTION_WITHHELD}:
            continue
        if not execution_allowed:
            if _heading_level(line) and any(word in line.casefold() for word in _EXECUTION_HEADINGS):
                issues.append("UNAPPROVED_EXECUTION_SECTION")
            violation = _execution_violation(line)
            if violation:
                issues.append(violation)
    if _clean_empty_markdown(text) != re.sub(r"\n{3,}", "\n\n", text).strip():
        issues.append("EMPTY_MARKDOWN_STRUCTURE")
    lines = text.splitlines()
    if any(not _table_has_data_row(lines[start:end]) for start, end in _table_blocks(lines, 0, len(lines))):
        issues.append("EMPTY_MARKDOWN_TABLE")
    return list(dict.fromkeys(issues))


def _finalize_audit(
    audit: list[dict[str, Any]], *, artifact_ok: bool
) -> list[dict[str, Any]]:
    finalized: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for raw in audit:
        entry = dict(raw)
        if entry.get("category") in _VIOLATION_CATEGORIES or entry.get("warning"):
            entry["resolution"] = (
                "RESOLVED_AFTER_FINAL_ARTIFACT_VALIDATION"
                if artifact_ok
                else "UNRESOLVED"
            )
            entry["execution_blocking"] = not artifact_ok
        key = (
            entry.get("category"),
            entry.get("agent"),
            entry.get("field"),
            entry.get("warning"),
            entry.get("detail"),
        )
        if key not in seen:
            finalized.append(entry)
            seen.add(key)
    return finalized
