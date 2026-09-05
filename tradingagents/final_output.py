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
    stack: list[tuple[int, str]] = []
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
        parent = tuple(title for _, title in stack)
        numbered = _NUMBERED_HEADING.match(match.group(2))
        kind = _heading_number_kind(numbered) if numbered else "plain"
        key = (level, parent, kind)
        if numbered and _is_ordinary_year(numbered.group("arabic") or ""):
            numbered = None
            kind = "plain"
            key = (level, parent, kind)
        # A non-numbered sibling starts a new heading sequence.
        if not numbered:
            _reset_heading_counters(counters, level, parent)
        else:
            counters[key] = counters.get(key, 0) + 1
            number = counters[key]
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
        stack.append((level, match.group(2)))
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
    """Renumber contiguous numbered lists, leaving prose numbers untouched."""
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
            if not output[current].strip() and current + 1 < len(output):
                next_item = _NUMBERED_LIST.match(output[current + 1])
                if (
                    next_item
                    and not _is_ordinary_year(next_item.group("number"))
                    and next_item.group("indent") == indent
                    and next_item.group("number").isdigit() == style
                ):
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
            if len(cells) == len(header) and any(cells):
                valid.append(row)
        if len(valid) > 2:
            result.extend(valid)
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
            next_index = next((i for i in range(index + 1, len(cleaned)) if cleaned[i].strip()), len(cleaned))
            following = cleaned[next_index] if next_index < len(cleaned) else ""
            next_level = _heading_level(following)
            boundary = not following or _SEPARATOR.match(following.strip()) is not None
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
            if not lines[current].strip() and current + 1 < len(lines):
                next_item = _NUMBERED_LIST.match(lines[current + 1])
                if (
                    next_item
                    and not _is_ordinary_year(next_item.group("number"))
                    and next_item.group("indent") == indent
                    and next_item.group("number").isdigit() == style
                ):
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
            if len(row.strip().strip("|").split("|")) != header_count:
                issues.append("MALFORMED_MARKDOWN_TABLE")
                break

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
        next_index = next((i for i in range(index + 1, len(lines)) if lines[i].strip()), len(lines))
        if next_index == len(lines):
            issues.append("ORPHAN_HEADING")
            break
        following = lines[next_index]
        next_level = _heading_level(following)
        if _SEPARATOR.match(following.strip()) or (next_level is not None and next_level <= heading):
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
    for field in _REPORT_FIELDS:
        value = state.get(field)
        if isinstance(value, str):
            parts.append(value)
    for field in _DEBATE_FIELDS:
        debate = state.get(field)
        if isinstance(debate, Mapping):
            parts.extend(value for value in debate.values() if isinstance(value, str))
    issues = validate_final_report_text(
        "\n".join(parts), execution_allowed=execution_allowed, check_structure=False
    )
    for part in parts:
        issues.extend(_markdown_structure_issues(part))
    return list(dict.fromkeys(issues))


def validate_final_report_text(
    text: str, *, execution_allowed: bool, check_structure: bool = True
) -> list[str]:
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
    if check_structure:
        issues.extend(_markdown_structure_issues(text))
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
