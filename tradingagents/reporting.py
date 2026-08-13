"""Report-tree writer and user-facing presentation order.

Agent state stays in its original schema-compatible form.  This module is the
presentation boundary: it builds the concise conclusion first, localizes known
labels, and puts Japan official data before long-form analyst debate.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

from tradingagents.dataflows.japan.context import render_japan_report_sections
from tradingagents.presentation import (
    build_investment_overview,
    build_key_data_section,
    localize_markdown,
)


def build_report_sections(final_state: dict[str, Any], *, include_debate_detail: bool = True) -> list[tuple[str, str]]:
    """Return ordered, localized user-visible report sections for Markdown/HTML."""
    market = (final_state.get("market_context") or {}).get("market")
    sections: list[tuple[str, str]] = [("投资结论总览", build_investment_overview(final_state))]
    sections.append(("本次最重要的 5 条数据", build_key_data_section(final_state)))
    if market == "JP":
        sections.append(("日本市场核心数据", render_japan_report_sections(final_state.get("japan_data_bundle"))))

    # Put execution before analytical transcripts. Individual reports remain
    # source material, while the compact plan is what a user acts on first.
    trader_plan = final_state.get("trader_investment_plan") or final_state.get("trader_investment_decision")
    if trader_plan:
        sections.append(("交易计划", "# 交易计划\n\n" + localize_markdown(trader_plan)))

    analyst_order = [
        ("market_report", "技术分析"),
        ("fundamentals_report", "基本面分析"),
        ("news_report", "新闻与宏观"),
        ("sentiment_report", "情绪分析"),
    ]
    for key, title in analyst_order:
        if final_state.get(key):
            sections.append((title, f"# {title}\n\n" + localize_markdown(final_state[key])))

    debate = final_state.get("investment_debate_state") or {}
    if include_debate_detail:
        if debate.get("bull_history"):
            sections.append(("多头观点", "# 多头观点\n\n" + localize_markdown(debate["bull_history"])))
        if debate.get("bear_history"):
            sections.append(("空头观点", "# 空头观点\n\n" + localize_markdown(debate["bear_history"])))
    if debate.get("judge_decision"):
        sections.append(("研究结论", "# 研究结论\n\n" + localize_markdown(debate["judge_decision"])))

    risk = final_state.get("risk_debate_state") or {}
    if include_debate_detail:
        risk_parts = []
        for key, label in [
            ("aggressive_history", "激进风险分析师"),
            ("conservative_history", "保守风险分析师"),
            ("neutral_history", "中性风险分析师"),
        ]:
            if risk.get(key):
                risk_parts.append(f"## {label}\n\n{localize_markdown(risk[key])}")
        if risk_parts:
            sections.append(("风险管理辩论", "# 风险管理辩论\n\n" + "\n\n".join(risk_parts)))
    if risk.get("judge_decision"):
        sections.append(("投资组合经理最终详细判断", "# 投资组合经理最终详细判断\n\n" + localize_markdown(risk["judge_decision"])))

    if market == "JP":
        sections.append(("数据来源与数据缺口", "# 数据来源 / 数据缺口\n\n日本官方数据状态已在“日本市场核心数据”中逐项列出。缺失数据降低置信度，不应被解释为负面事实。"))
    return sections


def write_report_tree(final_state: dict, ticker: str, save_path) -> Path:
    """Save a localized, ordered report tree and return ``complete_report.md``."""
    save_path = Path(save_path)
    save_path.mkdir(parents=True, exist_ok=True)
    market = (final_state.get("market_context") or {}).get("market")

    if market == "JP":
        japan_dir = save_path / "0_japan_market_data"
        japan_dir.mkdir(exist_ok=True)
        (japan_dir / "official_and_supply_demand.md").write_text(
            render_japan_report_sections(final_state.get("japan_data_bundle")), encoding="utf-8"
        )

    # Persist analyst and decision files in Chinese presentation form, while the
    # in-memory state remains untouched for parsers, memory and API consumers.
    file_specs = [
        ("1_analysts", "market.md", final_state.get("market_report")),
        ("1_analysts", "sentiment.md", final_state.get("sentiment_report")),
        ("1_analysts", "news.md", final_state.get("news_report")),
        ("1_analysts", "fundamentals.md", final_state.get("fundamentals_report")),
        ("2_research", "bull.md", (final_state.get("investment_debate_state") or {}).get("bull_history")),
        ("2_research", "bear.md", (final_state.get("investment_debate_state") or {}).get("bear_history")),
        ("2_research", "manager.md", (final_state.get("investment_debate_state") or {}).get("judge_decision")),
        ("3_trading", "trader.md", final_state.get("trader_investment_plan") or final_state.get("trader_investment_decision")),
        ("4_risk", "aggressive.md", (final_state.get("risk_debate_state") or {}).get("aggressive_history")),
        ("4_risk", "conservative.md", (final_state.get("risk_debate_state") or {}).get("conservative_history")),
        ("4_risk", "neutral.md", (final_state.get("risk_debate_state") or {}).get("neutral_history")),
        ("5_portfolio", "decision.md", (final_state.get("risk_debate_state") or {}).get("judge_decision")),
    ]
    for dirname, filename, content in file_specs:
        if content:
            directory = save_path / dirname
            directory.mkdir(exist_ok=True)
            (directory / filename).write_text(localize_markdown(content), encoding="utf-8")

    ordered = build_report_sections(final_state)
    content = "\n\n".join(section for _title, section in ordered)
    header = f"# {ticker} 投资研究报告\n\n生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    output = save_path / "complete_report.md"
    output.write_text(header + content, encoding="utf-8")
    return output
