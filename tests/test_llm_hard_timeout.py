"""Deterministic coverage for the synchronous LLM whole-call deadline."""

from __future__ import annotations

import time
from threading import Event

import pytest
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from tradingagents.llm_clients.openai_client import NormalizedChatOpenAI


@pytest.mark.unit
def test_hard_deadline_returns_without_waiting_for_slow_provider(monkeypatch):
    completed = Event()

    def slow_invoke(self, input, config=None, **kwargs):
        try:
            time.sleep(0.12)
            return AIMessage(content="late")
        finally:
            completed.set()

    monkeypatch.setattr(ChatOpenAI, "invoke", slow_invoke)
    llm = NormalizedChatOpenAI(model="test", api_key="test", timeout=0.01, max_retries=0)
    started = time.perf_counter()
    with pytest.raises(TimeoutError, match="hard deadline"):
        llm.invoke("test")
    assert time.perf_counter() - started < 0.08
    assert not completed.is_set()
    assert completed.wait(0.2)  # sync SDK call is detached, not cancellable in-thread


@pytest.mark.unit
def test_hard_deadline_allows_a_fast_provider(monkeypatch):
    monkeypatch.setattr(
        ChatOpenAI, "invoke", lambda self, input, config=None, **kwargs: AIMessage(content="ok")
    )
    llm = NormalizedChatOpenAI(model="test", api_key="test", timeout=0.1, max_retries=0)
    assert llm.invoke("test").content == "ok"
