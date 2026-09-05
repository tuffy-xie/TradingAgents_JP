from __future__ import annotations

import re

import pytest

from tradingagents.dataflows.japan.context import (
    render_japan_audience_context,
    render_japan_report_sections,
)
from tradingagents.final_output import (
    build_canonical_final_state,
    normalize_markdown_structure,
    validate_final_report_text,
)
from tradingagents.reporting import write_report_tree
from web.server import _render_report_html


def _metric(value, unit, *, status="OK"):
    return {"status": status, "value": value, "unit": unit}


def _financial_document(record_type: str):
    guidance = record_type == "GUIDANCE"
    values = {
        "revenue": (2_110_000 if guidance else 502_264, "百万円"),
        "operating_profit": (430_000 if guidance else 98_454, "百万円"),
        "net_income": (338_000 if guidance else 81_377, "百万円"),
        "eps": (185.68 if guidance else 44.71, "円"),
    }
    metrics = {}
    for key, (value, unit) in values.items():
        metric = _metric(value, unit)
        metrics[key] = {"status": "OK", "current_value": metric} if guidance else metric
    metrics["ordinary_profit"] = {
        "status": "NOT_APPLICABLE",
        "current_value": None,
    } if guidance else _metric(None, None, status="NOT_APPLICABLE")
    return {
        "source": "EDINET DB",
        "source_type": "STRUCTURED_SOURCE",
        "records": [
            {
                "record_type": record_type,
                "period_type": "FY" if guidance else "Q1",
                "target_period_end": "2027-03-31",
                "accounting_standard": "IFRS",
                "scope": "CONSOLIDATED",
                "metrics": metrics,
            }
        ],
    }


def _jp_state():
    actual = {
        "status": "OK",
        "freshness": "CURRENT_STRUCTURED_CONFIRMED",
        "selected_disclosure_date": "2026-07-31",
        "critical_gate": {"status": "OK"},
        "document": _financial_document("ACTUAL"),
    }
    guidance = {
        "status": "OK",
        "freshness": "CURRENT_STRUCTURED_CONFIRMED",
        "selected_disclosure_date": "2026-07-31",
        "critical_gate": {"status": "OK"},
        "document": _financial_document("GUIDANCE"),
    }
    return {
        "company_of_interest": "6981.T",
        "trade_date": "2026-09-02",
        "market_context": {
            "market": "JP",
            "symbol": "6981.T",
            "currency": "JPY",
            "instrument_type": "EQUITY",
        },
        "japan_data_bundle": {
            "items": [],
            "provider_metadata": {
                "Japan Financial Authority": {
                    "analysis_as_of": "2026-09-02",
                    "actual": actual,
                    "guidance": guidance,
                    "official_coverage": {"status": "COMPLETE", "sources": ["TDnet"]},
                }
            },
        },
        "evidence_registry": [
            {
                "claim_type": "FACT",
                "value": "Historical EPS 127.66",
                "allowed_for_current_decision": True,
            }
        ],
        "evidence_audit": [
            {
                "category": "UNSUPPORTED_CLAIM",
                "agent": "Fundamentals Analyst",
                "field": "fundamentals_report",
                "warning": "unsupported_precise_number",
                "resolution": "CLAIM_REMOVED_OR_REPLACED",
                "execution_blocking": False,
            }
        ],
        "fundamentals_report": (
            "# Fundamentals\n\n"
            "## 最新季度业绩\n| 指标 | 数值 |\n|---|---|\n| EPS | 44.71 |\n\n"
            "## 历史财务\n| 指标 | 数值 |\n|---|---|\n| EPS | 127.66 |\n\n"
            "## 空表结论\n|---|---|\n财务堡垒极强。"
        ),
        "market_report": "JSF贷株余额为3000股，因此无轧空基础。",
        "sentiment_report": "暂无情绪数据。",
        "news_report": "",
        "investment_debate_state": {"judge_decision": "## 研究结论\n继续观望。"},
        "investment_plan": "",
        "trader_investment_plan": "Entry: 7000\nStop: 7240\nPosition: 1%",
        "risk_debate_state": {
            "judge_decision": "Rating: Underweight\n入场价：7000；止损价：7240；目标价：6600。"
        },
        "final_trade_decision": "Rating: Underweight\n入场价：7000；止损价：7240。",
        "validated_execution": {
            "status": "DATA_UNAVAILABLE",
            "detail": "ENTRY_OR_STOP_UNAVAILABLE",
        },
    }


def test_canonical_final_state_owns_financial_and_execution_output():
    accepted = build_canonical_final_state(_jp_state())
    fundamentals = accepted["fundamentals_report"]
    visible = "\n".join(
        str(accepted.get(key) or "")
        for key in (
            "market_report",
            "fundamentals_report",
            "trader_investment_plan",
            "final_trade_decision",
        )
    )

    assert "502264 百万円" in fundamentals
    assert "98454 百万円" in fundamentals
    assert "81377 百万円" in fundamentals
    assert "2110000 百万円" in fundamentals
    assert "430000 百万円" in fundamentals
    assert "338000 百万円" in fundamentals
    assert "127.66" in fundamentals
    assert "## 空表结论" not in fundamentals
    assert "财务堡垒极强" not in fundamentals
    assert "无轧空基础" not in visible
    assert "STRUCTURED_SOURCE" not in visible
    assert "DATA_UNAVAILABLE" not in visible
    assert "7000" not in accepted["trader_investment_plan"]
    assert "7240" not in accepted["final_trade_decision"]
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert accepted["final_output_contract"]["execution_allowed"] is False
    violation = next(
        item
        for item in accepted["evidence_audit"]
        if item.get("category") == "UNSUPPORTED_CLAIM"
    )
    assert violation["resolution"] == "RESOLVED_AFTER_FINAL_ARTIFACT_VALIDATION"
    assert violation["execution_blocking"] is False


def test_full_agent_log_keeps_raw_prose_while_user_report_uses_canonical_state(tmp_path):
    report = write_report_tree(
        build_canonical_final_state(_jp_state()), "6981.T", tmp_path
    )
    user_text = report.read_text(encoding="utf-8")
    debug_text = (tmp_path / "full_agent_log.md").read_text(encoding="utf-8")

    assert "入场价：7000" not in user_text
    assert "入场价：7000" in debug_text
    assert "502264 百万円" in user_text
    assert "Final Output Contract" in debug_text


def test_report_supply_section_never_dumps_raw_metadata_or_claims_no_shorts():
    bundle = {
        "items": [
            {
                "source": "JSF",
                "source_type": "securities_finance_balance",
                "timestamp": "2026-09-02T00:00:00+00:00",
                "title": "JSF balance",
                "metadata": {
                    "data_date": "2026-09-02",
                    "finance_balance": 1000.0,
                    "stock_loan_balance": 3.0,
                    "net_balance": 997.0,
                    "unit": "株",
                    "publication_status": "UNKNOWN",
                    "native_cadence": "JSF_CONFIRMED_BALANCE_EACH_BUSINESS_DAY_11:30",
                },
            }
        ]
    }
    report = render_japan_report_sections(bundle)
    context = render_japan_audience_context(
        {"market_context": {"market": "JP"}, "japan_data_bundle": bundle},
        "MARKET",
    )

    assert "{'" not in report
    assert "无轧空基础" not in report
    assert "CONFIRMED" not in report
    assert "JSF_CONFIRMED" not in context
    assert "不代表全市场空头总量" in report


def test_non_japan_state_is_unchanged():
    state = {
        "market_context": {"market": "US"},
        "final_trade_decision": "Entry: 100; Stop: 90",
    }
    assert build_canonical_final_state(state) == state


def test_web_legacy_trader_key_cannot_bypass_execution_gate():
    state = _jp_state()
    state.pop("trader_investment_plan")
    state["trader_investment_decision"] = "Entry: 7000; Stop: 7240; Position: 1%"

    html = _render_report_html(build_canonical_final_state(state), auto_print=False)

    assert "7000" not in html
    assert "7240" not in html
    assert "确定性执行校验未通过" in html


def test_japan_renderers_reject_noncanonical_business_state(tmp_path):
    import pytest

    state = _jp_state()
    with pytest.raises(ValueError, match="not an accepted canonical final state"):
        write_report_tree(state, "6981.T", tmp_path)
    with pytest.raises(ValueError, match="not an accepted canonical final state"):
        _render_report_html(state, auto_print=False)


def test_validated_execution_is_the_only_user_visible_numeric_plan():
    state = _jp_state()
    state["validated_execution"] = {
        "status": "OK",
        "entry": 4120.0,
        "stop": 3585.0,
        "distance": 535.0,
        "risk_pct": 12.99,
        "position_pct": 5.0,
        "portfolio_stop_risk_pct": 0.65,
    }
    state["trader_investment_plan"] = (
        "Entry: 4120\nStop: 3585\nPosition: 5%\n错误风险 8.8%，错误组合风险 0.44%。"
    )
    state["final_trade_decision"] = (
        "Rating: Hold\nEntry: 4200\nStop: 3900\n错误风险 8.8%。"
    )

    accepted = build_canonical_final_state(state)
    plan = accepted["final_trade_decision"]

    assert "4120.0" in plan
    assert "3585.0" in plan
    assert "535.0" in plan
    assert "12.99%" in plan
    assert "5.0%" in plan
    assert "0.65%" in plan
    assert "4200" not in plan
    assert "3900" not in plan
    assert "8.8" not in plan
    assert "0.44" not in plan


def test_execution_gate_folds_empty_nested_table_and_orphan_conclusion():
    state = _jp_state()
    state["investment_debate_state"] = {
        "judge_decision": (
            "## 研究结论\n\n继续观察基本面。\n\n"
            "### 条件交易\n\n"
            "| 条件 | 动作 | 依据 |\n"
            "|---|---|---|\n"
            "| 跌破止损 7000 | 卖出 | 控制仓位 |\n\n"
            "没有上述执行参数就无法操作。\n\n"
            "### 已核验事实\n\n"
            "| 项目 | 状态 |\n"
            "|---|---|\n"
            "| 财务 | 已核验 |"
        )
    }

    accepted = build_canonical_final_state(state)
    decision = accepted["investment_debate_state"]["judge_decision"]

    assert "### 条件交易" not in decision
    assert "没有上述执行参数就无法操作" not in decision
    assert "### 已核验事实" in decision
    assert "| 财务 | 已核验 |" in decision


@pytest.mark.parametrize("field", [
    "market_report", "fundamentals_report", "news_report", "sentiment_report",
    "investment_plan", "trader_investment_plan", "final_trade_decision",
    "investment_debate_state.judge_decision", "risk_debate_state.judge_decision",
])
def test_unapproved_execution_is_removed_from_every_report_field(field, tmp_path):
    state = _jp_state()
    text = (
        "## 方向判断\nUnderweight（减配）；不新增多头；继续观望。\n\n"
        "激进者可在确认条件出现后小仓做空，但必须严守止损。\n"
        "总净敞口 ≤ 组合总值的 1%。\n"
        "严格止损，触发条件后强制离场。\n"
        "对拟建仓者，宜等待回调确认后再介入。\n"
        "我采纳可执行方案，吸收价格触发条件并给出执行纪律。\n"
        "Entry: 7000\nStop: 7240\nTarget: 6600\nPosition: 1%\n"
        "### 可操作投资计划\n等待信号后建立空头。\n"
    )
    path = field.split(".")
    if len(path) == 2:
        state[path[0]][path[1]] = text
    else:
        state[field] = text
    # Keep numeric clauses eligible for evidence checks so this test exercises
    # the execution contract, not incidental unsupported-number redaction.
    state["evidence_registry"].append({
        "claim_type": "FACT", "value": "7000 7240 6600 1%",
        "allowed_for_current_decision": True,
    })
    accepted = build_canonical_final_state(state)
    report = write_report_tree(accepted, "6981.T", tmp_path).read_text()
    html = _render_report_html(accepted, auto_print=False)
    for artifact in (report, html):
        for forbidden in ("小仓做空", "严守止损", "总净敞口", "严格止损", "强制离场",
                          "Entry:", "Stop:", "Target:", "Position:", "可操作投资计划",
                          "等待信号后建立空头", "回调确认后再介入", "我采纳可执行方案"):
            assert forbidden not in artifact
    assert validate_final_report_text(report, execution_allowed=False) == []
    raw = accepted["raw_agent_outputs"]
    assert (raw[path[0]][path[1]] if len(path) == 2 else raw[field]) == text


def test_directional_judgments_and_technical_facts_survive_execution_pruning(tmp_path):
    state = _jp_state()
    state["market_report"] = (
        "**核心立场：Underweight（减配）；不新增多头；保持观望；"
        "激进者仅在确认条件后小仓做空。**\n\n"
        "支撑位 6800，压力位 7200，是技术分析事实。\n"
    )
    state["news_report"] = "券商分析师目标价 8000 円，属于估值预期。"
    state["evidence_registry"].append({
        "claim_type": "FACT", "value": "6800 7200 8000",
        "allowed_for_current_decision": True,
    })
    accepted = build_canonical_final_state(state)
    report = write_report_tree(accepted, "6981.T", tmp_path).read_text()
    for allowed in ("Underweight（减配）", "不新增多头", "保持观望", "支撑位 6800", "压力位 7200",
                    "券商分析师目标价 8000"):
        assert allowed in report
    assert "小仓做空" not in report
    assert validate_final_report_text(report, execution_allowed=False) == []


def test_final_structural_cleanup_and_news_localization_in_complete_report(tmp_path):
    state = _jp_state()
    state["market_report"] = (
        "## 仍可使用的技术判断\n下行风险仍需关注。\n\n"
        "## 操作含义\n**\n给交易者的具体行动建议：\n- 小仓做空，严格止损。\n\n"
        "### 空子章节\n-\n1.\n\n"
        "## 其他事实\n以下为历史证据：\n- Historical EPS 127.66\n\n"
        "计算如下：\n\n该推导尚待核实。\n"
        "### 空表\n| 条件 | 动作 |\n|---|---|\n| 触发时 | 小仓试多 |\n\n"
        "### 尾部空标题\n剩余说明：\n**"
    )
    state["news_report"] = (
        "## Japan company-news authority\n"
        "The following timestamped Japan bundle headlines are available; "
        "a separate tool's empty result does not mean there is no company news.\n"
        "- 已核验公司新闻。"
    )
    accepted = build_canonical_final_state(state)
    report = write_report_tree(accepted, "6981.T", tmp_path).read_text()
    for forbidden in ("操作含义", "具体行动建议", "空子章节", "尾部空标题", "剩余说明：",
                      "### 空表", "Japan company-news authority", "The following timestamped",
                      "a separate tool's empty result", "DATA_UNAVAILABLE", "STRUCTURED_SOURCE", "计算如下："):
        assert forbidden not in report
    assert not re.search(r"(?m)^\s*(?:\*\*|[-+]|\d+\.)\s*$", report)
    assert "以下为历史证据：" in report
    assert "Historical EPS 127.66" in report
    assert "已核验的日本公司新闻" in report
    assert "其他新闻工具返回空结果，不代表本次没有公司新闻" in report
    assert validate_final_report_text(report, execution_allowed=False) == []
    assert "Japan company-news authority" in (tmp_path / "full_agent_log.md").read_text()
    for value in ("502264 百万円", "98454 百万円", "81377 百万円", "44.71 円",
                  "2110000 百万円", "430000 百万円", "338000 百万円", "185.68 円"):
        assert value in report


def test_old_finalized_contract_is_reaccepted_instead_of_bypassing_execution_gate():
    state = build_canonical_final_state(_jp_state())
    state["final_output_contract"]["version"] = "v1"
    state["raw_agent_outputs"]["market_report"] = "方向偏空。激进者可小仓做空。"
    state["market_report"] = "方向偏空。激进者可小仓做空。"
    accepted = build_canonical_final_state(state)
    assert accepted["market_report"] == "方向偏空。"
    assert accepted["final_output_contract"]["version"] == "v2"
    assert build_canonical_final_state(accepted) == accepted


@pytest.mark.parametrize("claim,category", [
    ("激进者可小仓做空。", "UNAPPROVED_EXECUTION_INSTRUCTION"),
    ("总净敞口 ≤ 组合总值的 1%。", "POSITION_SIZE_RECOMMENDATION"),
    ("Entry: 4120", "UNVALIDATED_EXECUTABLE_PLAN"),
    ("## 内容\n**\n", "EMPTY_MARKDOWN_STRUCTURE"),
    ("## Japan company-news authority\n有新闻。", "UNLOCALIZED_NEWS_AUTHORITY"),
])
def test_final_artifact_validation_detects_leakage_without_editing(claim, category):
    assert category in validate_final_report_text(claim, execution_allowed=False)


def test_post_pruning_structural_normalization_is_generic_and_value_preserving():
    text = """## 三、保留的事实
6981.T 的收入为 502264 百万円。

## 五、后续事实
### 3. 指标甲
指标甲有效。
### 5. 指标乙
指标乙有效。

1. 第一项
3. 第三项

| 指标 | 数值 | 来源 |
|---|---:|---|
| 收入 | 502264 | EDINET |
| 损坏行 | 1 | TDnet | 多余列 |

---

---

## 空小节
---
"""

    normalized = normalize_markdown_structure(text)

    assert "## 一、保留的事实" in normalized
    assert "## 二、后续事实" in normalized
    assert "### 1. 指标甲" in normalized
    assert "### 2. 指标乙" in normalized
    assert "1. 第一项" in normalized
    assert "2. 第三项" in normalized
    assert "损坏行" not in normalized
    assert "502264 百万円" in normalized
    assert sum(1 for line in normalized.splitlines() if line.strip() == "---") == 1
    assert "## 空小节" not in normalized
    assert validate_final_report_text(normalized, execution_allowed=False) == []


def test_structural_normalization_does_not_rewrite_years_or_financial_numbers():
    text = "## 2026. 年度背景\n2026. 年度数据为 502264 百万円。\n"
    normalized = normalize_markdown_structure(text)
    assert "## 2026. 年度背景" in normalized
    assert "2026. 年度数据为 502264 百万円。" in normalized


def test_complete_report_structure_has_no_pruning_residue(tmp_path):
    state = _jp_state()
    raw_market = (
        "## 三、技术事实\n价格为 6981.T。\n\n"
        "## 五、残留小节\n概览保留。\n**\n---\n---\n"
        "### 4. MACD\nMACD 有效。\n### 7. RSI\nRSI 有效。\n"
        "| 项目 | 数值 |\n|---|---|\n| 好行 | 1 |\n| 坏行 | 2 | 多余 |"
    )
    accepted = build_canonical_final_state(state)
    accepted["market_report"] = normalize_markdown_structure(raw_market)
    report = write_report_tree(accepted, "6981.T", tmp_path).read_text(encoding="utf-8")

    assert "##### 一、技术事实" in report
    assert "##### 二、残留小节" in report
    assert "###### 1. MACD" in report
    assert "###### 2. RSI" in report
    assert "坏行" not in report
    assert validate_final_report_text(report, execution_allowed=False) == []
