"""Deterministic coverage for provider-native LLM timeouts."""

from __future__ import annotations

import time

import pytest
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from tradingagents.llm_clients.openai_client import NormalizedChatOpenAI


@pytest.mark.unit
def test_slow_provider_call_is_not_killed_by_project_deadline(monkeypatch):
    def slow_invoke(self, input, config=None, **kwargs):
        time.sleep(0.02)
        return AIMessage(content="late")

    monkeypatch.setattr(ChatOpenAI, "invoke", slow_invoke)
    llm = NormalizedChatOpenAI(model="test", api_key="test", timeout=0.01, max_retries=0)
    assert llm.invoke("test").content == "late"


@pytest.mark.unit
def test_provider_native_timeout_propagates_without_detached_worker(monkeypatch):
    def timeout_invoke(self, input, config=None, **kwargs):
        raise TimeoutError("provider read timeout")

    monkeypatch.setattr(ChatOpenAI, "invoke", timeout_invoke)
    llm = NormalizedChatOpenAI(model="test", api_key="test", timeout=0.01, max_retries=0)
    with pytest.raises(TimeoutError, match="provider read timeout"):
        llm.invoke("test")


@pytest.mark.unit
def test_hard_deadline_allows_a_fast_provider(monkeypatch):
    monkeypatch.setattr(
        ChatOpenAI, "invoke", lambda self, input, config=None, **kwargs: AIMessage(content="ok")
    )
    llm = NormalizedChatOpenAI(model="test", api_key="test", timeout=0.1, max_retries=0)
    assert llm.invoke("test").content == "ok"
