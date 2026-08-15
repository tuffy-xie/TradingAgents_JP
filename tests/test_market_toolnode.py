"""Market analyst execution keeps the original market tools authoritative."""
import pytest

from tradingagents.graph.trading_graph import TradingAgentsGraph


@pytest.mark.unit
def test_market_toolnode_keeps_original_market_tools_only():
    # _create_tool_nodes does not use self -> call unbound (avoids building LLMs).
    nodes = TradingAgentsGraph._create_tool_nodes(None)
    market_tools = set(nodes["market"].tools_by_name)
    assert {"get_stock_data", "get_indicators"} <= market_tools
    assert "get_verified_market_snapshot" not in market_tools
