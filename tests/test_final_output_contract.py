from __future__ import annotations

import re

import pytest

from tradingagents.agents.utils.execution_validation import validate_execution_plan
from tradingagents.dataflows.japan.context import (
    render_japan_audience_context,
    render_japan_report_sections,
)
from tradingagents.final_output import (
    _execution_cross_state_issues,
    _execution_violation,
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
    assert violation["resolution"] == "CLAIM_REMOVED_OR_REPLACED"
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
        "action": "Buy",
        "entry": 4120.0,
        "stop": 3585.0,
        "distance": 535.0,
        "risk_pct": 12.99,
        "position_pct": 5.0,
        "portfolio_stop_risk_pct": 0.65,
    }
    state["trader_investment_plan"] = (
        "Action: Buy\nEntry: 4120\nStop: 3585\nPosition: 5%\n"
        "错误风险 8.8%，错误组合风险 0.44%。"
    )
    state["final_trade_decision"] = (
        "Rating: Buy\nEntry: 4200\nStop: 3900\n错误风险 8.8%。"
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


def test_portfolio_hold_revokes_legacy_actionless_numeric_authorization():
    state = _jp_state()
    state["trader_investment_plan"] = (
        "| 项目 | 具体设定 |\n"
        "|---|---|\n"
        "| **建议** | **Hold（观望）** |\n"
        "| **入场价** | 7300 |\n"
        "| **止损价** | 6800 |"
    )
    state["final_trade_decision"] = "**评级**: Hold\n维持观望。"
    state["risk_debate_state"]["judge_decision"] = state["final_trade_decision"]
    # This is the precise invalid state persisted by the old parser: complete
    # numbers were accepted even though the action was unknown.
    state["validated_execution"] = {
        "version": "v2",
        "status": "OK",
        "action": None,
        "entry": 7300.0,
        "stop": 6800.0,
        "distance": 500.0,
        "risk_pct": 6.85,
    }

    accepted = build_canonical_final_state(state)
    validation = accepted["validated_execution"]
    report = accepted["accepted_report_markdown"]

    assert validation["status"] == "DATA_UNAVAILABLE"
    assert validation["detail"] == "HOLD_DOES_NOT_AUTHORIZE_NEW_EXECUTION"
    assert validation["action"] == "Hold"
    assert accepted["final_output_contract"]["execution_allowed"] is False
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert "已验证交易执行参数" not in report
    assert "| 入场价 | 7300" not in report
    assert "| 止损价 | 6800" not in report
    consistency = next(
        item
        for item in accepted["evidence_audit"]
        if item.get("category") == "EXECUTION_ACTION_CONSISTENCY"
    )
    assert consistency["resolution"] == "EXECUTABLE_PLAN_WITHHELD"
    assert consistency["execution_blocking"] is False


def test_final_contract_independently_rejects_hold_execution_conflict():
    state = _jp_state()
    state["final_trade_decision"] = "**Rating**: Hold"
    state["validated_execution"] = {
        "status": "OK",
        "action": "Buy",
        "portfolio_rating": "Hold",
    }

    assert _execution_cross_state_issues(state, True) == [
        "EXECUTION_PORTFOLIO_RATING_CONFLICT"
    ]


def test_current_finalized_contract_is_reopened_when_hold_execution_conflicts():
    state = _jp_state()
    state["trader_investment_plan"] = (
        "Action: Buy\nEntry: 7300\nStop: 6800\nPosition: 3%"
    )
    state["validated_execution"] = validate_execution_plan(
        state["trader_investment_plan"]
    )
    state["final_trade_decision"] = "Rating: Buy\n方向已确认。"
    state["risk_debate_state"]["judge_decision"] = state["final_trade_decision"]
    finalized = build_canonical_final_state(state)

    finalized["raw_agent_outputs"]["trader_investment_plan"] = (
        "| 项目 | 具体设定 |\n|---|---|\n"
        "| 建议 | Hold（观望） |\n| 入场价 | 7300 |\n| 止损价 | 6800 |"
    )
    finalized["raw_agent_outputs"]["final_trade_decision"] = "评级：Hold"
    finalized["final_trade_decision"] = "评级：Hold\n\n## 已验证交易执行参数"
    finalized["validated_execution"].update(
        {"status": "OK", "action": None, "portfolio_rating": "Hold"}
    )
    finalized["final_output_contract"].update(
        {"status": "FINALIZED", "execution_allowed": True}
    )

    reaccepted = build_canonical_final_state(finalized)

    assert reaccepted["validated_execution"]["detail"] == (
        "HOLD_DOES_NOT_AUTHORIZE_NEW_EXECUTION"
    )
    assert reaccepted["final_output_contract"]["execution_allowed"] is False
    assert "已验证交易执行参数" not in reaccepted["accepted_report_markdown"]


@pytest.mark.parametrize(
    "action,rating",
    [("Buy", "Buy"), ("Buy", "Overweight"), ("Sell", "Sell"), ("Sell", "Underweight")],
)
def test_compatible_directional_decisions_keep_valid_execution(action, rating):
    state = _jp_state()
    state["trader_investment_plan"] = (
        f"Action: {action}\nEntry: 7300\nStop: 6800\nPosition: 3%"
    )
    state["validated_execution"] = validate_execution_plan(
        state["trader_investment_plan"]
    )
    state["final_trade_decision"] = f"Rating: {rating}\n方向已确认。"
    state["risk_debate_state"]["judge_decision"] = state["final_trade_decision"]

    accepted = build_canonical_final_state(state)

    assert accepted["validated_execution"]["status"] == "OK"
    assert accepted["final_output_contract"]["execution_allowed"] is True
    assert accepted["final_output_contract"]["status"] == "FINALIZED"


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
    assert accepted["final_output_contract"]["version"] == "v5"
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


@pytest.mark.parametrize(
    "claim",
    [
        "在7000买入",
        "跌破6800止损",
        "加仓至5%",
        "减仓至一半",
        "持仓周期控制在10日",
        "持仓10个交易日",
        "7500以下逐步建仓",
        "收盘跌破止损位即执行，无例外。",
        "**Time Horizon**: 5-15个交易日",
        "持仓者继续持有并享受股息收益。",
        "Hold并设置明确触发条件是纪律性选择。",
        "若基本面证伪则核心仓提前了结。",
        "Buy at 7000.",
        "Reduce the position to 5%.",
    ],
)
def test_execution_semantics_block_action_authorization(claim):
    assert _execution_violation(claim) is not None


@pytest.mark.parametrize(
    "claim",
    [
        "当前不建议建仓",
        "不追高",
        "暂不加仓",
        "空仓者禁止建仓",
        "没有获准交易计划",
        "7500是技术阻力位",
        "未来两周事件风险较高",
        "等待确认后再评估",
        "Do not open a position.",
        "No approved trading plan is available.",
    ],
)
def test_execution_semantics_allow_withholding_and_research_facts(claim):
    assert _execution_violation(claim) is None
    assert validate_final_report_text(
        claim, execution_allowed=False, check_structure=False
    ) == []


def test_execution_gate_records_claim_identity_before_pruning():
    state = _jp_state()
    state["investment_debate_state"]["judge_decision"] = (
        "方向性风险偏高。激进者可小仓做空。空仓者禁止建仓。"
    )

    accepted = build_canonical_final_state(state)
    report = accepted["accepted_report_markdown"]
    finding = next(
        item
        for item in accepted["evidence_audit"]
        if item.get("category") == "UNAPPROVED_EXECUTION_CLAIM"
        and item.get("original_claim") == "激进者可小仓做空。"
    )

    assert "激进者可小仓做空" not in report
    assert "空仓者禁止建仓" in report
    assert finding["original_claim"] == "激进者可小仓做空。"
    assert len(finding["claim_sha256"]) == 64
    assert finding["resolution"] == "CLAIM_REMOVED_OR_REPLACED"
    assert finding["execution_blocking"] is False
    assert accepted["final_output_contract"]["status"] == "FINALIZED"


def test_live_style_mixed_execution_claim_is_pruned_clause_by_clause():
    state = _jp_state()
    state["investment_debate_state"]["judge_decision"] = (
        "风险偏高。不在7,500上方加仓；"
        "持仓周期严格控制在5-15个交易日，下一次事件前重新评估。"
        "空仓者禁止建仓。"
    )

    accepted = build_canonical_final_state(state)
    report = accepted["accepted_report_markdown"]

    assert "7,500上方加仓" not in report
    assert "持仓周期严格控制" not in report
    assert "空仓者禁止建仓" in report
    assert accepted["final_output_contract"]["artifact_issues"] == []


def test_japan_html_cover_uses_canonical_localized_fields_without_execution_plan():
    state = _jp_state()
    state["final_trade_decision"] = (
        "**Rating**: Hold\n\n"
        "**Executive Summary**: 维持观望，空仓者禁止建仓。\n\n"
        "**Time Horizon**: 5-15个交易日\n\n"
        "收盘跌破止损位即执行，无例外。"
    )

    accepted = build_canonical_final_state(state)
    html = _render_report_html(accepted, auto_print=False)

    assert "暂无评级" not in html
    assert '<strong class="rating">持有</strong>' in html
    assert "空仓者禁止建仓" in html
    assert "5-15个交易日" not in html
    assert "止损位即执行" not in html


@pytest.mark.parametrize(
    "heading",
    [
        "交易策略建议",
        "交易建议",
        "操作策略",
        "取引レコメンデーション",
    ],
)
def test_execution_strategy_subsections_are_removed_as_semantic_units(heading):
    state = _jp_state()
    state["news_report"] = (
        "## 事实背景\n宏观风险仍高。\n\n"
        f"### {heading}\n"
        "#### 策略一\n7500以下逐步建仓。\n\n"
        "### 风险背景\n未来两周事件风险较高。"
    )

    accepted = build_canonical_final_state(state)
    report = accepted["accepted_report_markdown"]

    assert heading not in report
    assert "7500以下逐步建仓" not in report
    assert "宏观风险仍高" in report
    assert "未来两周事件风险较高" in report
    assert accepted["final_output_contract"]["status"] == "FINALIZED"


@pytest.mark.parametrize(
    ("label", "translated"),
    [
        ("Executive Summary", "研究摘要"),
        ("Recommendation", "研究建议"),
        ("Strategic Actions", "策略说明"),
        ("Rating", "评级"),
        ("Investment Thesis", "投资逻辑"),
        ("Time Horizon", "研究周期"),
        ("Final Transaction Proposal", "最终研究结论"),
    ],
)
def test_standard_report_labels_are_localized_in_japanese_market_output(
    label, translated
):
    state = _jp_state()
    state["news_report"] = f"**{label}**: 正文。"

    accepted = build_canonical_final_state(state)

    assert label not in accepted["accepted_report_markdown"]
    assert translated in accepted["accepted_report_markdown"]


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


def test_structural_normalization_handles_circled_and_roman_sequences():
    text = """## I. 分析师
内容。
## III. 交易结论
内容。
## V. 组合决策
内容。

③ 第一条保留事实
⑤ 第二条保留事实
"""
    normalized = normalize_markdown_structure(text)
    assert "## I. 分析师" in normalized
    assert "## II. 交易结论" in normalized
    assert "## III. 组合决策" in normalized
    assert "① 第一条保留事实" in normalized
    assert "② 第二条保留事实" in normalized
    assert "③ 第一条保留事实" not in normalized
    assert "⑤ 第二条保留事实" not in normalized
    assert validate_final_report_text(normalized, execution_allowed=False) == []


def test_heading_count_claim_is_removed_after_pruning():
    normalized = normalize_markdown_structure(
        "## 一、指标选择说明（8 个互补指标）\n指标 A。\n"
    )
    assert "8 个互补指标" not in normalized
    assert "## 一、指标选择说明" in normalized
    assert validate_final_report_text(normalized, execution_allowed=False) == []


def test_complete_report_structure_has_no_pruning_residue(tmp_path):
    state = _jp_state()
    raw_market = (
        "## 三、技术事实\n价格为 6981.T。\n\n"
        "## 五、残留小节\n概览保留。\n**\n---\n---\n"
        "### 4. MACD\nMACD 有效。\n### 7. RSI\nRSI 有效。\n"
        "| 项目 | 数值 |\n|---|---|\n| 好行 | 1 |\n| 坏行 | 2 | 多余 |"
    )
    state["market_report"] = raw_market
    state["evidence_registry"].append(
        {
            "claim_type": "FACT",
            "value": "6981 1 2 4 7",
            "allowed_for_current_decision": True,
        }
    )
    accepted = build_canonical_final_state(state)
    report = write_report_tree(accepted, "6981.T", tmp_path).read_text(encoding="utf-8")

    assert "##### 一、技术事实" in report
    assert "##### 二、残留小节" in report
    assert "###### 1. MACD" in report
    assert "###### 2. RSI" in report
    assert "坏行" not in report
    assert validate_final_report_text(report, execution_allowed=False) == []


def test_exact_accepted_artifact_is_validated_and_persisted(tmp_path):
    state = _jp_state()
    state["market_report"] = (
        "## 技术事实\n指标保持承压。\n\n"
        "## 仓位建议\n仓位必须按此缩放。"
    )

    accepted = build_canonical_final_state(state)
    artifact = accepted["accepted_report_markdown"]
    written = write_report_tree(accepted, "6981.T", tmp_path).read_text().rstrip()

    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert accepted["final_output_contract"]["artifact_issues"] == []
    assert written == artifact
    assert "仓位必须按此缩放" not in artifact
    assert validate_final_report_text(artifact, execution_allowed=False) == []

    accepted["market_report"] = "SHOULD NOT RECOMPOSE INTO USER ARTIFACT"
    html = _render_report_html(accepted, auto_print=False)
    assert "SHOULD NOT RECOMPOSE" not in html
    assert "指标保持承压" in html


def test_web_cover_renders_markdown_instead_of_leaking_raw_markers():
    state = _jp_state()
    state["final_trade_decision"] = "Rating: Hold\n\n**结论：继续观望。**"
    accepted = build_canonical_final_state(state)

    html = _render_report_html(accepted, auto_print=False)

    assert "**结论：继续观望。**" not in html
    assert "<strong>结论：继续观望。</strong>" in html


def test_fenced_report_diagram_does_not_print_inert_emphasis_markers():
    state = _jp_state()
    state["market_report"] = (
        "## 技术图示\n```text\n"
        "趋势改善，暗示**波动率窗口临近**。\n"
        "```"
    )

    accepted = build_canonical_final_state(state)
    html = _render_report_html(accepted, auto_print=False)

    assert "**" not in accepted["accepted_report_markdown"]
    assert "**" not in html
    assert "波动率窗口临近" in html


def test_final_artifact_validation_blocks_unknown_machine_enum():
    state = _jp_state()
    state["news_report"] = "状态：FUTURE_INTERNAL_STATUS。"

    accepted = build_canonical_final_state(state)

    assert accepted["final_output_contract"]["status"] == "BLOCKED"
    assert "INTERNAL_MACHINE_ENUM_VISIBLE:FUTURE_INTERNAL_STATUS" in accepted[
        "final_output_contract"
    ]["artifact_issues"]


def test_known_transport_status_is_localized_in_user_artifact():
    state = _jp_state()
    state["sentiment_report"] = "## 情绪数据\n状态：RATE_LIMITED，当前无可用样本。"

    accepted = build_canonical_final_state(state)
    artifact = accepted["accepted_report_markdown"]

    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert "RATE_LIMITED" not in artifact
    assert "本次没有可用的投资者或社交情绪样本" in artifact


def test_technical_debate_history_does_not_block_clean_user_artifact(tmp_path):
    state = _jp_state()
    state["risk_debate_state"].update(
        {
            "history": "风险讨论汇总。",
            "neutral_history": "内部讨论状态：FUTURE_RISK_DRAFT。",
            "current_neutral_response": "内部技术讨论。",
        }
    )

    accepted = build_canonical_final_state(state)
    user_text = accepted["accepted_report_markdown"]
    write_report_tree(accepted, "6981.T", tmp_path)
    debug_text = (tmp_path / "full_agent_log.md").read_text(encoding="utf-8")

    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert "FUTURE_RISK_DRAFT" not in user_text
    assert "FUTURE_RISK_DRAFT" in debug_text


def test_reacceptance_recomputes_obsolete_final_artifact_violations():
    state = _jp_state()
    state["evidence_audit"].append(
        {
            "category": "FINAL_ARTIFACT_VIOLATION",
            "detail": "INTERNAL_MACHINE_ENUM_VISIBLE:RATE_LIMITED",
            "resolution": "UNRESOLVED",
            "execution_blocking": True,
        }
    )

    accepted = build_canonical_final_state(state)

    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert not any(
        item.get("category") == "FINAL_ARTIFACT_VIOLATION"
        for item in accepted["evidence_audit"]
    )


def test_jsf_financing_language_cannot_bypass_observable_fact_boundary():
    state = _jp_state()
    state["market_report"] = (
        "## 技术面\n价格走势承压。\n\n"
        "## 融券供需\n融券大幅减少，空头力量衰竭。"
    )

    accepted = build_canonical_final_state(state)
    artifact = accepted["accepted_report_markdown"]

    assert "价格走势承压" in artifact
    assert "空头力量衰竭" not in artifact


def test_jsf_agent_subsection_is_removed_as_one_semantic_unit():
    state = _jp_state()
    state["market_report"] = (
        "## 技术面\n价格趋势偏弱。\n\n"
        "## JSF 信用供需\n"
        "| 项目 | 数值 |\n|---|---:|\n| 贷株余额 | 3000 |\n\n"
        "由此可见强制平仓压力正在下降。"
    )

    accepted = build_canonical_final_state(state)
    artifact = accepted["accepted_report_markdown"]

    assert "价格趋势偏弱" in artifact
    assert "强制平仓压力正在下降" not in artifact
    assert "贷株余额 | 3000" not in artifact
    assert "仅是 JSF 可观察余额" not in artifact  # fixture has no JSF bundle item


def test_separator_below_agent_wrapper_does_not_delete_wrapper():
    normalized = normalize_markdown_structure(
        "## I. Analyst Team Reports\n\n### Market Analyst\n\n---\n\n正文。\n"
    )
    assert "## I. Analyst Team Reports" in normalized
    assert "### Market Analyst" in normalized
    assert "正文。" in normalized
