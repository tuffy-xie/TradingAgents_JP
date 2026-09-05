from __future__ import annotations

from tradingagents.dataflows.japan.context import (
    render_japan_audience_context,
    render_japan_report_sections,
)
from tradingagents.final_output import build_canonical_final_state
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
