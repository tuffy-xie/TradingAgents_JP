"""User-facing Chinese report order and localization regressions."""
from __future__ import annotations

from tradingagents.presentation import localize_markdown
from tradingagents.reporting import build_report_sections, write_report_tree
from web.server import _render_report_html


def _jp_state():
    return {
        "company_of_interest": "8002.T",
        "trade_date": "2026-08-13",
        "market_context": {"market": "JP"},
        "instrument_context": "Company: 丸红株式会社",
        "market_report": "技术面内容",
        "fundamentals_report": "基本面内容",
        "news_report": "新闻内容",
        "sentiment_report": "情绪内容",
        "trader_investment_plan": "**Action**: Hold\n\n**Entry Price**: 5000\n\n**Stop Loss**: 4788",
        "investment_debate_state": {"bull_history": "多头", "bear_history": "空头", "judge_decision": "**Recommendation**: Hold"},
        "risk_debate_state": {"judge_decision": "**Rating**: Underweight\n\n**Executive Summary**: 暂不追高\n\n**Price Target**: 5450"},
        "japan_data_bundle": {
            "items": [{"source": "TDnet", "timestamp": "2026-08-12T00:00:00+00:00", "title": "決算短信"}],
            "source_statuses": [{"source": source, "status": "OK"} for source in ("TDnet", "Company IR", "JPX", "JSF")]
                + [{"source": source, "status": "AUTH_REQUIRED"} for source in ("EDINET", "J-Quants")],
        },
    }


def test_localize_known_schema_labels_without_changing_free_text():
    text = "**Recommendation**: Overweight\n**Action**: Sell\nFINAL TRANSACTION PROPOSAL: **HOLD**"
    rendered = localize_markdown(text)
    assert "研究评级" in rendered and "超配 / 偏多" in rendered
    assert "操作方向" in rendered and "卖出 / 减仓" in rendered
    assert "最终交易方向（内部动作）" in rendered


def test_jp_report_order_places_summary_and_data_before_debate(tmp_path):
    state = _jp_state()
    sections = build_report_sections(state)
    titles = [title for title, _ in sections]
    assert titles[:4] == ["投资结论总览", "本次最重要的 5 条数据", "日本市场核心数据", "交易计划"]
    assert titles.index("日本市场核心数据") < titles.index("多头观点")
    output = write_report_tree(state, "8002.T", tmp_path).read_text(encoding="utf-8")
    assert "# 投资结论总览" in output
    assert "Recommendation" not in output and "**Action**" not in output
    assert "状态：缺少 API Key 或权限" in output


def test_pdf_html_starts_with_overview_and_us_has_no_japan_section():
    jp_html = _render_report_html(_jp_state(), auto_print=False)
    assert "投资结论总览" in jp_html
    assert jp_html.index("投资结论总览") < jp_html.index("日本市场核心数据")
    us = _jp_state()
    us["market_context"] = {"market": "US"}
    us_html = _render_report_html(us, auto_print=False)
    assert "日本市场核心数据" not in us_html
    assert "page-break-before:auto" in jp_html
    assert "<h2>投资结论总览</h2>" not in jp_html
