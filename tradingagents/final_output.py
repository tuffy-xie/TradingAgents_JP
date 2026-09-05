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
    if contract.get("status") == "FINALIZED":
        return result
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
        "version": "v1",
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
    if (state.get("final_output_contract") or {}).get("status") != "FINALIZED":
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
    section: list[str] = []
    keep = True
    for line in lines:
        if re.match(r"^##\s+", line):
            if keep:
                output.extend(section)
            section = [line]
            keep = keep_heading(re.sub(r"^##\s+", "", line).strip())
        else:
            section.append(line)
    if keep:
        output.extend(section)
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
    return re.sub(r"\n{3,}", "\n\n", "".join(kept)).strip()


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
    result = dict(state)
    decision = str(result.get("final_trade_decision") or "")
    accepted = _strip_execution_plan(decision, include_target=True)
    gate = (
        "## 执行许可\n\n"
        "确定性执行校验未通过。本报告不提供可执行的入场、止损、目标价或仓位参数；"
        "方向性研究结论不等于已批准交易指令。"
    )
    accepted = (accepted.rstrip() + "\n\n" + gate).strip() if accepted else gate
    result["trader_investment_plan"] = (
        "## 交易执行状态\n\n"
        "确定性执行校验未通过，因此没有获准的入场、止损、目标价或仓位计划。"
    )
    result["final_trade_decision"] = accepted
    for field in ("market_report", "sentiment_report", "news_report", "fundamentals_report"):
        if isinstance(result.get(field), str):
            result[field] = _remove_unapproved_execution_lines(result[field])
    risk = result.get("risk_debate_state")
    if isinstance(risk, Mapping):
        risk = dict(risk)
        risk["judge_decision"] = accepted
        result["risk_debate_state"] = risk
    research = result.get("investment_debate_state")
    if isinstance(research, Mapping):
        research = dict(research)
        if isinstance(research.get("judge_decision"), str):
            research["judge_decision"] = _strip_execution_plan(
                research["judge_decision"], include_target=True
            )
        result["investment_debate_state"] = research
    if isinstance(result.get("investment_plan"), str):
        result["investment_plan"] = _remove_execution_sections(result["investment_plan"])
    return result


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
    result = dict(state)

    def publicize(text: str) -> str:
        for internal, display in _INTERNAL_STATUS.items():
            text = text.replace(internal, display)
        text = text.replace("DATA UNAVAILABLE", "数据不可用")
        return _fold_empty_sections(text)

    for field in _REPORT_FIELDS:
        if isinstance(result.get(field), str):
            result[field] = publicize(result[field])
    for field in _DEBATE_FIELDS:
        debate = result.get(field)
        if isinstance(debate, Mapping):
            result[field] = {
                key: publicize(value) if isinstance(value, str) else value
                for key, value in debate.items()
            }
    return result


def _validate_final_artifact(state: Mapping[str, Any], execution_allowed: bool) -> list[str]:
    text = "\n".join(
        str(state.get(field) or "") for field in _REPORT_FIELDS
    )
    issues: list[str] = []
    for token in _INTERNAL_STATUS:
        if token in text:
            issues.append(f"INTERNAL_STATUS_VISIBLE:{token}")
    if re.search(r"(?:无|没有|缺乏)(?:明显|任何)?(?:轧空|軋空|short[ -]?squeeze)", text, re.I):
        issues.append("SHORT_MARKET_OVERCLAIM")
    if not execution_allowed and any(
        _EXECUTION_LINE.search(str(state.get(field) or ""))
        for field in ("trader_investment_plan", "final_trade_decision")
    ):
        issues.append("UNVALIDATED_EXECUTABLE_PLAN")
    return issues


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
