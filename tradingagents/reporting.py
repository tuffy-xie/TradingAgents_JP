"""Reusable report-tree writer shared by the CLI and the programmatic API.

Writes a run's per-section markdown (analysts, research, trading, risk,
portfolio) plus a consolidated ``complete_report.md`` under ``save_path``. The
CLI and ``TradingAgentsGraph.save_reports`` both call this, so a headless / API
run produces the same on-disk report tree a CLI run does.
"""

import json
import re
from datetime import datetime
from pathlib import Path

from tradingagents.dataflows.japan.context import render_japan_report_sections
from tradingagents.final_output import normalize_markdown_structure, require_canonical_final_state
from tradingagents.report_consistency import canonical_report_metadata


def _agent_section(name: str, text: str, market: str) -> str:
    """Nest accepted JP headings under the display wrapper without editing facts."""
    if market == "JP":
        def nested_heading(match):
            level = len(match.group(1)) + 3
            return ("#" * level + " " if level <= 6 else "") + match.group(2)

        text = re.sub(r"(?m)^(#{1,6})\s+(.+)$", nested_heading, text)
    return f"### {name}\n{text}"


def write_report_tree(final_state: dict, ticker: str, save_path) -> Path:
    """Save a completed run's reports to ``save_path``; return the complete-report path."""
    require_canonical_final_state(final_state)
    save_path = Path(save_path)
    save_path.mkdir(parents=True, exist_ok=True)
    sections = []
    agent_log_parts = ["# 完整多智能体推理与风控日志"]
    manifest = final_state.get("run_manifest") or {}
    agent_log_parts.append(
        "## Run Manifest\n\n```json\n"
        + json.dumps(manifest, ensure_ascii=False, indent=2)
        + "\n```"
    )
    agent_log_parts.append(
        "## Final Output Contract\n\n```json\n"
        + json.dumps(final_state.get("final_output_contract") or {}, ensure_ascii=False, indent=2)
        + "\n```"
    )
    raw_outputs = final_state.get("raw_agent_outputs") or {}
    audit = final_state.get("evidence_audit") or []
    agent_log_parts.append(
        "## 证据校验审计\n\n```json\n" + json.dumps(audit, ensure_ascii=False, indent=2) + "\n```"
    )
    agent_log_parts.append(
        "## Evidence Registry\n\n```json\n"
        + json.dumps(final_state.get("evidence_registry") or [], ensure_ascii=False, indent=2)
        + "\n```"
    )

    metadata = canonical_report_metadata(final_state)
    market = metadata["market"]
    if market == "JP":
        japan_section = render_japan_report_sections(final_state.get("japan_data_bundle"))
        japan_dir = save_path / "0_japan_market_data"
        japan_dir.mkdir(exist_ok=True)
        (japan_dir / "official_and_supply_demand.md").write_text(japan_section, encoding="utf-8")
        sections.append(japan_section)

    # 1. Analysts
    analysts_dir = save_path / "1_analysts"
    analyst_parts = []
    if final_state.get("market_report"):
        analysts_dir.mkdir(exist_ok=True)
        report = final_state["market_report"]
        (analysts_dir / "market.md").write_text(report, encoding="utf-8")
        analyst_parts.append(("Market Analyst", report))
        agent_log_parts.append(
            f"## 市场分析师原始报告\n\n{raw_outputs.get('market_report', final_state['market_report'])}"
        )
    if final_state.get("sentiment_report"):
        analysts_dir.mkdir(exist_ok=True)
        report = final_state["sentiment_report"]
        (analysts_dir / "sentiment.md").write_text(report, encoding="utf-8")
        analyst_parts.append(("Sentiment Analyst", report))
        agent_log_parts.append(
            f"## 情绪分析师原始报告\n\n{raw_outputs.get('sentiment_report', final_state['sentiment_report'])}"
        )
    if final_state.get("news_report"):
        analysts_dir.mkdir(exist_ok=True)
        report = final_state["news_report"]
        (analysts_dir / "news.md").write_text(report, encoding="utf-8")
        analyst_parts.append(("News Analyst", report))
        agent_log_parts.append(f"## 新闻分析师原始报告\n\n{raw_outputs.get('news_report', final_state['news_report'])}")
    if final_state.get("fundamentals_report"):
        analysts_dir.mkdir(exist_ok=True)
        report = final_state["fundamentals_report"]
        (analysts_dir / "fundamentals.md").write_text(report, encoding="utf-8")
        analyst_parts.append(("Fundamentals Analyst", report))
        agent_log_parts.append(
            f"## 基本面分析师原始报告\n\n{raw_outputs.get('fundamentals_report', final_state['fundamentals_report'])}"
        )
    if analyst_parts:
        content = "\n\n".join(_agent_section(name, text, market) for name, text in analyst_parts)
        sections.append(f"## I. Analyst Team Reports\n\n{content}")

    # 2. Research
    if final_state.get("investment_debate_state"):
        research_dir = save_path / "2_research"
        debate = final_state["investment_debate_state"]
        research_parts = []
        if debate.get("bull_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bull.md").write_text(debate["bull_history"], encoding="utf-8")
            raw_debate = raw_outputs.get("investment_debate_state") or {}
            agent_log_parts.append(f"## 多头研究员\n\n{raw_debate.get('bull_history', debate['bull_history'])}")
        if debate.get("bear_history"):
            research_dir.mkdir(exist_ok=True)
            (research_dir / "bear.md").write_text(debate["bear_history"], encoding="utf-8")
            raw_debate = raw_outputs.get("investment_debate_state") or {}
            agent_log_parts.append(f"## 空头研究员\n\n{raw_debate.get('bear_history', debate['bear_history'])}")
        if debate.get("judge_decision"):
            research_dir.mkdir(exist_ok=True)
            report = debate["judge_decision"]
            (research_dir / "manager.md").write_text(report, encoding="utf-8")
            research_parts.append(("Research Manager", report))
            raw_debate = raw_outputs.get("investment_debate_state") or {}
            agent_log_parts.append(f"## 研究经理原始评判\n\n{raw_debate.get('judge_decision', debate['judge_decision'])}")
        if research_parts:
            content = "\n\n".join(_agent_section(name, text, market) for name, text in research_parts)
            sections.append(f"## II. Research Team Decision\n\n{content}")

    # 3. Trading
    if final_state.get("trader_investment_plan"):
        trading_dir = save_path / "3_trading"
        trading_dir.mkdir(exist_ok=True)
        report = final_state["trader_investment_plan"]
        (trading_dir / "trader.md").write_text(report, encoding="utf-8")
        agent_log_parts.append(f"## 交易员原始决策\n\n{raw_outputs.get('trader_investment_plan', final_state['trader_investment_plan'])}")
        sections.append("## III. Trading Team Plan\n\n" + _agent_section("Trader", report, market))

    # 4. Risk Management
    if final_state.get("risk_debate_state"):
        risk_dir = save_path / "4_risk"
        risk = final_state["risk_debate_state"]
        if risk.get("aggressive_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "aggressive.md").write_text(risk["aggressive_history"], encoding="utf-8")
            raw_risk = raw_outputs.get("risk_debate_state") or {}
            agent_log_parts.append(f"## 激进风控分析师\n\n{raw_risk.get('aggressive_history', risk['aggressive_history'])}")
        if risk.get("conservative_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "conservative.md").write_text(risk["conservative_history"], encoding="utf-8")
            raw_risk = raw_outputs.get("risk_debate_state") or {}
            agent_log_parts.append(f"## 保守风控分析师\n\n{raw_risk.get('conservative_history', risk['conservative_history'])}")
        if risk.get("neutral_history"):
            risk_dir.mkdir(exist_ok=True)
            (risk_dir / "neutral.md").write_text(risk["neutral_history"], encoding="utf-8")
            raw_risk = raw_outputs.get("risk_debate_state") or {}
            agent_log_parts.append(f"## 中性风控分析师\n\n{raw_risk.get('neutral_history', risk['neutral_history'])}")

        # 5. Portfolio Manager
        if risk.get("judge_decision"):
            portfolio_dir = save_path / "5_portfolio"
            portfolio_dir.mkdir(exist_ok=True)
            report = risk["judge_decision"]
            (portfolio_dir / "decision.md").write_text(report, encoding="utf-8")
            sections.append(
                "## V. Portfolio Manager Decision\n\n" + _agent_section("Portfolio Manager", report, market)
            )
            raw_risk = raw_outputs.get("risk_debate_state") or {}
            agent_log_parts.append(f"## 投资组合经理原始决策\n\n{raw_risk.get('judge_decision', risk['judge_decision'])}")

    # Write consolidated report
    display_symbol = metadata["symbol"] or ticker
    header = (
        f"# Trading Analysis Report: {display_symbol}\n\n"
        f"Market: {metadata['market']} | Currency: {metadata['currency']} | Instrument type: {metadata['instrument_type']}\n\n"
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    complete_report = normalize_markdown_structure(header + "\n\n".join(sections))
    (save_path / "complete_report.md").write_text(complete_report + "\n", encoding="utf-8")
    (save_path / "full_agent_log.md").write_text("\n\n".join(agent_log_parts) + "\n", encoding="utf-8")
    return save_path / "complete_report.md"
