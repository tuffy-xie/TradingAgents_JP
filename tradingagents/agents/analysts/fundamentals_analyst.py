from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_instrument_context_from_state,
    get_japan_data_context_from_state,
    get_language_instruction,
    get_verified_market_snapshot_from_state,
)


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = get_instrument_context_from_state(state)
        japan_data_context = get_japan_data_context_from_state(state)
        verified_snapshot = get_verified_market_snapshot_from_state(state)
        is_etf = (state.get("market_context") or {}).get("instrument_type") == "ETF"

        tools = [get_fundamentals] if is_etf else [
            get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement,
        ]

        framework = (
            "This instrument is an ETF. Do NOT use a normal operating-company three-statement framework and do not call balance-sheet, cash-flow, or income-statement tools. Analyze the tracking index, disclosed major holdings, sector concentration, fee ratio, AUM, liquidity, distributions, tracking error, portfolio valuation, and relative strength. If a field is unavailable, state it is unavailable rather than substituting a company metric."
            if is_etf else
            "This instrument is an operating company. Analyze its business and financial statements."
        )

        system_message = (
            "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements."
            + " " + framework
            + "\n\nThe following is the single verified source for all current price, OHLC, moving-average, RSI, MACD, ATR, and VWMA claims. Do not obtain or infer these values from fundamentals tools; report unavailable if this snapshot is unavailable:\n"
            + verified_snapshot
            + ("\n\n" + japan_data_context if japan_data_context else "")
            + get_language_instruction(),
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)

        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
