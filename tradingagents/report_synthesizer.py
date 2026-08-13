"""Concise, user-facing equity research report synthesis.

This presentation component consumes completed Agent state but never changes
the Agent graph, prompts, recommendations, or provider selection.  It avoids
a second LLM request: the report is traceable to the completed multi-Agent run.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tradingagents.dataflows.japan.context import render_japan_report_sections
from tradingagents.presentation import build_investment_overview, build_key_data_section, chinese_status, localize_markdown, source_display_name

_UNAVAILABLE = "数据未提供"


@dataclass(frozen=True)
class SynthesizedReport:
    sections: list[tuple[str, str]]
    full_agent_log: str


class ReportSynthesizer:
    """Create a concise Chinese report and preserve raw Agent output separately."""

    def synthesize(self, state: Mapping[str, Any]) -> SynthesizedReport:
        market = str((state.get("market_context") or {}).get("market") or "US")
        sections: list[tuple[str, str]] = [
            ("投资结论总览", self._overview_with_cards(state)),
            ("本次最重要的 5 条数据", build_key_data_section(state)),
            ("投资逻辑", self._ai_section("投资逻辑", state.get("final_trade_decision"), 900)),
            ("公司概览", self._company_overview(state)),
            ("财务分析", self._ai_section("财务分析", state.get("fundamentals_report"), 1100, financial=True)),
            ("估值分析", self._valuation_section(state)),
            ("最新披露与新闻", self._disclosure_and_news(state, market)),
            ("催化剂", self._catalyst_section(state)),
            ("技术分析", self._ai_section("技术分析", state.get("market_report"), 1100)),
        ]
        if market == "JP":
            sections.append(("日本资金与需给", render_japan_report_sections(state.get("japan_data_bundle"))))
        sections.extend([
            ("风险因素", self._risk_section(state)),
            ("多 Agent 共识", self._consensus_section(state)),
            ("核心结论", self._ai_section("核心结论", state.get("final_trade_decision"), 900)),
            ("交易计划", self._trading_plan(state)),
            ("数据来源与数据缺口", self._sources_section(state, market)),
        ])
        return SynthesizedReport(sections, self._full_agent_log(state))

    def _overview_with_cards(self, state: Mapping[str, Any]) -> str:
        trader = str(state.get("trader_investment_plan") or state.get("trader_investment_decision") or "")
        final = str(state.get("final_trade_decision") or "")
        source = "\n".join(str(state.get(key) or "") for key in ("market_report", "fundamentals_report", "news_report", "instrument_context"))
        cards = [
            ("综合评级", self._field(final, "Rating")), ("现价", self._metric(source, ("Current Price", "当前价格", "最新收盘", "收盘价"))),
            ("核心目标价", self._field(final, "Price Target")), ("风险位（止损 / 失效）", self._first_available(self._field(trader, "Stop Loss"), self._field(final, "Stop-Loss Condition"))),
            ("市值", self._metric(source, ("Market Cap", "市值"))), ("市盈率（PE）", self._metric(source, ("P/E", "PE", "市盈率"))),
            ("市净率（PB）", self._metric(source, ("P/B", "PB", "市净率"))), ("净资产收益率（ROE）", self._metric(source, ("ROE", "净资产收益率"))),
            ("股息率", self._metric(source, ("Dividend Yield", "股息率"))), ("52 周区间", self._metric(source, ("52 Week Range", "52周区间", "52 周区间"))),
        ]
        rendered = "\n".join(f'<div class="summary-card"><span>{label}</span><strong>{localize_markdown(value)}</strong></div>' for label, value in cards)
        return f"{build_investment_overview(state)}\n\n## 首页关键指标\n\n<div class=\"summary-cards\">\n{rendered}\n</div>"

    def _company_overview(self, state: Mapping[str, Any]) -> str:
        identity = state.get("instrument_identity") or {}
        identity = identity if isinstance(identity, Mapping) else {}
        rows = [("股票代码", state.get("company_of_interest")), ("公司名称", identity.get("company_name") or identity.get("name")), ("交易所", identity.get("exchange")), ("行业", identity.get("industry") or identity.get("sector")), ("市场", (state.get("market_context") or {}).get("market"))]
        return "\n".join(["# 公司概览", "", "数据：", *(f"- {label}：{value or _UNAVAILABLE}" for label, value in rows), "", "AI看法：", "公司识别信息来自 Instrument Identity；业务、财务与估值判断仅采用本报告中明确标注的数据和 AI 综合判断。"])

    def _valuation_section(self, state: Mapping[str, Any]) -> str:
        source = "\n".join(str(state.get(key) or "") for key in ("fundamentals_report", "market_report", "instrument_context"))
        rows = [("市值", self._metric(source, ("Market Cap", "市值"))), ("市盈率（PE）", self._metric(source, ("P/E", "PE", "市盈率"))), ("市净率（PB）", self._metric(source, ("P/B", "PB", "市净率"))), ("净资产收益率（ROE）", self._metric(source, ("ROE", "净资产收益率"))), ("股息率", self._metric(source, ("Dividend Yield", "股息率")))]
        return "\n".join(["# 估值分析", "", "数据：", *(f"- {label}：{value}" for label, value in rows), "", "AI看法：", "估值指标仅在原始数据明确提供时展示。缺失指标代表数据覆盖不足，不代表估值偏高或偏低；估值结论须与同业、盈利周期和风险位共同判断。"])

    def _disclosure_and_news(self, state: Mapping[str, Any], market: str) -> str:
        if market != "JP":
            return self._ai_section("最新披露与新闻", state.get("news_report"), 1000)
        bundle = state.get("japan_data_bundle") or {}
        official = [item for item in bundle.get("items") or [] if str(item.get("source")) in {"TDnet", "Company IR", "EDINET", "J-Quants"}]
        facts = "\n".join(f"- {str(item.get('timestamp') or '')[:10] or '日期未提供'}｜{source_display_name(item.get('source'))}｜{self._title(item.get('title'))}" for item in official[:5]) or "- 本分析窗口未取得可展示的官方披露。"
        return f"# 最新披露与新闻\n\n数据：\n{facts}\n\nAI看法：\n{self._brief(state.get('news_report'), 700)}"

    def _catalyst_section(self, state: Mapping[str, Any]) -> str:
        debate = state.get("investment_debate_state") or {}
        judge = debate.get("judge_decision") if isinstance(debate, Mapping) else ""
        evidence = f"{state.get('news_report') or ''}\n{judge or ''}"
        return f"# 催化剂\n\n数据：\n- 已将新闻、官方披露与研究经理结论纳入催化剂筛选。\n\nAI看法：\n{self._brief(evidence, 800)}"

    def _risk_section(self, state: Mapping[str, Any]) -> str:
        risk = state.get("risk_debate_state") or {}
        judge = risk.get("judge_decision") if isinstance(risk, Mapping) else ""
        return f"# 风险因素\n\n数据：\n- 风险管理结论来自激进、保守与中性风险视角的完整辩论。\n\nAI看法：\n{self._brief(judge, 950)}"

    def _consensus_section(self, state: Mapping[str, Any]) -> str:
        debate, risk = state.get("investment_debate_state") or {}, state.get("risk_debate_state") or {}
        bull = self._brief(debate.get("bull_history") if isinstance(debate, Mapping) else "", 360)
        bear = self._brief(debate.get("bear_history") if isinstance(debate, Mapping) else "", 360)
        risk_view = self._brief(risk.get("judge_decision") if isinstance(risk, Mapping) else "", 360)
        return "\n".join(["# 多 Agent 共识", "", f"- 多方关注：{bull}", f"- 空方关注：{bear}", f"- 风控结论：{risk_view}", "", "完整多 Agent 发言已保存至 `full_agent_log.md`，不作为默认研报正文。"])

    def _trading_plan(self, state: Mapping[str, Any]) -> str:
        return self._ai_section("交易计划", state.get("trader_investment_plan") or state.get("trader_investment_decision"), 1200)

    def _sources_section(self, state: Mapping[str, Any], market: str) -> str:
        lines = ["# 数据来源与数据缺口", "", "数据："]
        if market == "JP":
            bundle = state.get("japan_data_bundle") or {}
            for status in bundle.get("source_statuses") or []:
                lines.append(f"- {source_display_name(status.get('source'))}｜状态：{chinese_status(str(status.get('status') or 'DATA_UNAVAILABLE'))}｜时间：{str(status.get('timestamp') or '')[:10] or '时间未提供'}")
        else:
            lines.append("- 美股沿用既有市场、基本面、新闻与情绪数据路径。")
        lines.extend(["", "AI看法：", "数据缺口会降低相关结论的置信度，不能被解释为利多、利空或缺乏市场关注。所有事实、新闻观点、市场情绪和 AI 推断须分层阅读。"])
        return "\n".join(lines)

    def _ai_section(self, title: str, content: Any, limit: int, *, financial: bool = False) -> str:
        return f"# {title}\n\nAI综合判断：\n{self._brief(content, limit, financial=financial)}"

    @staticmethod
    def _field(text: str, field: str) -> str:
        match = re.search(rf"\*\*{re.escape(field)}\*\*:\s*([^\n]+)", text, re.I)
        return localize_markdown(match.group(1).strip()) if match else _UNAVAILABLE

    @staticmethod
    def _first_available(*values: str) -> str:
        return next((value for value in values if value != _UNAVAILABLE), _UNAVAILABLE)

    def _metric(self, text: str, labels: tuple[str, ...]) -> str:
        for label in labels:
            match = re.search(rf"(?:\*\*\s*)?{re.escape(label)}(?:\s*\*\*)?\s*[:：=]\s*([^\n|]+)", text, re.I)
            if match and (value := match.group(1).strip(" *`")) and len(value) <= 100:
                return localize_markdown(value)
        if any(label in {"Current Price", "当前价格", "最新收盘", "收盘价"} for label in labels):
            match = re.search(r"(?:收盘|收)\s*([0-9][0-9,]*(?:\.\d+)?)", text)
            if match:
                return match.group(1)
        return _UNAVAILABLE

    @staticmethod
    def _title(value: Any) -> str:
        titles = {"Company IR public landing page": "公司投资者关系信息页", "JSF financing and stock-loan balance": "日证金融资与贷株余额"}
        return titles.get(str(value or ""), str(value or "本窗口数据"))

    def _brief(self, content: Any, limit: int, *, financial: bool = False) -> str:
        raw = localize_markdown(str(content or ""))
        raw = re.sub(r"```.*?```", "", raw, flags=re.S)
        raw = re.sub(r"^#{1,6}\s*.*$", "", raw, flags=re.M)
        raw = re.sub(r"\s+", " ", raw).strip()
        raw = re.sub(r"^(?:Bull|Bear) Analyst:\s*", "", raw, flags=re.I)
        if not raw:
            return "相关数据未提供；该模块不对缺失数据作方向性推断。"
        if len(raw) < 80:
            return "该模块已纳入多 Agent 综合判断；原始细节详见独立调试日志。"
        letters, chinese = len(re.findall(r"[A-Za-z]", raw)), len(re.findall(r"[\u4e00-\u9fff]", raw))
        if letters > chinese * 2 and letters > 10:
            return "已纳入完整多 Agent 推理；当前记录缺少可安全展示的中文摘要，详见独立调试日志。"
        if financial and re.search(r"TTM.{0,80}Q[1-4]|Q[1-4].{0,80}TTM", raw, re.I):
            return "原始记录含不可直接比较的会计期间；为避免跨期间推导错误，本摘要不展示该项，详见数据来源与数据缺口。"
        if len(raw) > limit:
            raw = raw[:limit].rsplit("。", 1)[0] or raw[:limit]
            raw += "。"
        return raw

    def _full_agent_log(self, state: Mapping[str, Any]) -> str:
        debate, risk = state.get("investment_debate_state") or {}, state.get("risk_debate_state") or {}
        blocks = ["# 完整多 Agent 日志（调试用）", "", "此文件保留原始 Agent 输出，非默认用户研报。"]
        records = [("市场分析师", state.get("market_report")), ("基本面分析师", state.get("fundamentals_report")), ("新闻分析师", state.get("news_report")), ("情绪分析师", state.get("sentiment_report")), ("多头研究员", debate.get("bull_history") if isinstance(debate, Mapping) else None), ("空头研究员", debate.get("bear_history") if isinstance(debate, Mapping) else None), ("研究经理", debate.get("judge_decision") if isinstance(debate, Mapping) else None), ("交易员", state.get("trader_investment_plan") or state.get("trader_investment_decision")), ("激进风险分析师", risk.get("aggressive_history") if isinstance(risk, Mapping) else None), ("保守风险分析师", risk.get("conservative_history") if isinstance(risk, Mapping) else None), ("中性风险分析师", risk.get("neutral_history") if isinstance(risk, Mapping) else None), ("投资组合经理", risk.get("judge_decision") if isinstance(risk, Mapping) else None), ("最终交易结论", state.get("final_trade_decision"))]
        for title, value in records:
            if value:
                blocks.extend([f"\n## {title}\n", str(value).strip()])
        return "\n".join(blocks).rstrip() + "\n"
