import re
from collections.abc import Mapping

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.analysts.turn import take_turn
from tradingagents.agents.context import (
    get_instrument_context_from_state,
    get_japan_news_context_from_state,
    get_language_instruction,
)
from tradingagents.agents.tools import (
    get_global_news,
    get_macro_indicators,
    get_news,
    get_prediction_markets,
)

# The tools this analyst is offered; its tool node is built from the same tuple.
TOOLS = (
    get_news,
    get_global_news,
    get_macro_indicators,
    get_prediction_markets,
)


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        asset_type = state.get("asset_type", "stock")
        asset_label = "company" if asset_type == "stock" else "asset"
        instrument_context = get_instrument_context_from_state(state)
        japan_data_context = get_japan_news_context_from_state(state)

        system_message = (
            f"You are a news researcher tasked with analyzing recent news and trends over the past week. Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Use the available tools: get_news(start_date, end_date) for news about the {asset_label} under analysis, get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news, get_macro_indicators(indicator, curr_date, look_back_days) to ground macro commentary in actual data from FRED (e.g. 'cpi', 'core_pce', 'unemployment', 'fed_funds_rate', '10y_treasury', 'yield_curve'), and get_prediction_markets(topic, limit) for live market-implied probabilities of forward-looking events (e.g. 'Fed rate cut', 'recession 2026', geopolitical or sector events). Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + ("\n\n" + japan_data_context if japan_data_context else "")
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " Report what your tools support; another agent decides the trade."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in TOOLS]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        result, report = take_turn(prompt, llm, TOOLS, state["messages"])
        if report and (state.get("market_context") or {}).get("market") == "JP":
            report = _apply_japan_news_authority(report, state)

        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node


_NO_COMPANY_NEWS = re.compile(
    r"(?:no\s+(?:company[- ]specific\s+)?news|no news found|"
    r"(?:没有|无|未抓取到|未发现)[^。；\n|]{0,30}(?:公司|个股|相关)?新闻|"
    r"新闻[^。；\n|]{0,20}(?:0\s*条|无结果)|"
    r"ニュース[^。；\n|]{0,30}(?:0\s*条|ありません|なし))",
    re.I,
)


def _apply_japan_news_authority(report: str, state: Mapping[str, object]) -> str:
    """Make the pre-fetched, as-of-filtered Japan news list canonical.

    The generic Yahoo tool may legitimately return no results while the Japan
    bundle contains timestamped local-market headlines.  A tool-specific empty
    result must not overwrite that independent authority.
    """
    bundle = state.get("japan_data_bundle")
    if not isinstance(bundle, Mapping):
        return report
    items = [
        item
        for item in bundle.get("items") or []
        if isinstance(item, Mapping)
        and item.get("source_type") == "japan_stock_news"
        and item.get("status") == "OK"
        and (item.get("metadata") or {}).get("freshness_status")
        in {"CURRENT", "LATEST_AVAILABLE"}
    ]
    if not items:
        return report
    cleaned_lines = [line for line in report.splitlines() if not _NO_COMPANY_NEWS.search(line)]
    ordered = sorted(items, key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    authority = [
        "## Japan company-news authority",
        "The following timestamped Japan bundle headlines are available; a separate tool's empty result does not mean there is no company news.",
    ]
    for item in ordered[:8]:
        published = (item.get("metadata") or {}).get("published_at") or item.get("timestamp")
        authority.append(
            f"- {published} [{item.get('source')}] {item.get('title')}"
            + (f" ({item.get('url')})" if item.get("url") else "")
        )
    narrative = "\n".join(cleaned_lines).strip()
    return "\n".join(authority) + ("\n\n" + narrative if narrative else "")
