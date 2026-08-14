"""Web runs must persist the same report tree as non-web graph runs."""

from __future__ import annotations

from tradingagents.reporting import write_report_tree


def test_completed_web_state_can_be_archived_as_default_and_debug_reports(tmp_path):
    state = {
        "company_of_interest": "CRCL",
        "market_context": {"symbol": "CRCL", "market": "US", "currency": "USD", "instrument_type": "EQUITY"},
        "market_report": "市场分析",
        "investment_debate_state": {"bull_history": "多头原文", "bear_history": "空头原文", "judge_decision": "研究结论"},
        "trader_investment_plan": "交易计划",
        "risk_debate_state": {
            "aggressive_history": "激进风控原文",
            "conservative_history": "保守风控原文",
            "neutral_history": "中性风控原文",
            "judge_decision": "最终结论",
        },
        "evidence_audit": [{"agent": "Trader", "warnings": ["unsupported_precise_number"]}],
    }
    report = write_report_tree(state, "CRCL", tmp_path)

    assert report.exists()
    assert (tmp_path / "full_agent_log.md").exists()
    assert "多头原文" not in report.read_text(encoding="utf-8")
    debug_log = (tmp_path / "full_agent_log.md").read_text(encoding="utf-8")
    assert "多头原文" in debug_log
    assert "证据校验审计" in debug_log
