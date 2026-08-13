"""Regression coverage for bounded LLM runs and node-level timing logs."""
from __future__ import annotations

import logging

import pytest

from tradingagents.graph.setup import _observe_agent_node


@pytest.mark.unit
def test_observed_node_logs_start_and_completion(caplog):
    node = _observe_agent_node(
        "Fundamentals Analyst", lambda state: {"fundamentals_report": "ok"},
        provider="deepseek", timeout=90, retries=2,
    )
    with caplog.at_level(logging.INFO):
        assert node({"company_of_interest": "6981.T"}) == {"fundamentals_report": "ok"}
    assert "[Agent] start name=Fundamentals Analyst ticker=6981.T" in caplog.text
    assert "[Agent] complete name=Fundamentals Analyst ticker=6981.T" in caplog.text


@pytest.mark.unit
def test_observed_node_classifies_timeout_and_re_raises(caplog):
    def fails(_state):
        raise TimeoutError("remote read timeout")

    node = _observe_agent_node("Fundamentals Analyst", fails, provider="deepseek", timeout=90, retries=2)
    with caplog.at_level(logging.ERROR), pytest.raises(TimeoutError):
        node({"company_of_interest": "6981.T"})
    assert "class=TIMEOUT" in caplog.text
