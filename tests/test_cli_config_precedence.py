"""CLI config precedence (#976, #977).

An explicit environment override for the debate/risk round counts, or the
checkpoint flag, must win over the interactive research-depth selection — the CLI
must not clobber an env-configured value back to a prompt/flag default.
"""

import hashlib
from io import StringIO
from unittest import mock

import pytest
from rich.console import Console

import cli.main as m

# Minimal selections dict shaped like get_user_selections()'s return value.
SELECTIONS = {
    "research_depth": 5,
    "shallow_thinker": "gpt-5.4-mini",
    "deep_thinker": "gpt-5.5",
    "backend_url": None,
    "llm_provider": "openai",
    "google_thinking_level": None,
    "openai_reasoning_effort": None,
    "anthropic_effort": None,
    "output_language": "English",
}


def test_research_depth_sets_both_rounds_without_env(monkeypatch):
    for var in ("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "TRADINGAGENTS_MAX_RISK_ROUNDS"):
        monkeypatch.delenv(var, raising=False)
    cfg = m._build_run_config(SELECTIONS, checkpoint=None)
    assert cfg["max_debate_rounds"] == 5
    assert cfg["max_risk_discuss_rounds"] == 5


def test_env_round_counts_win_over_selection(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "2")
    monkeypatch.setenv("TRADINGAGENTS_MAX_RISK_ROUNDS", "4")
    # DEFAULT_CONFIG already reflects the env (applied at import); emulate that.
    patched = dict(m.DEFAULT_CONFIG, max_debate_rounds=2, max_risk_discuss_rounds=4)
    with mock.patch.object(m, "DEFAULT_CONFIG", patched):
        cfg = m._build_run_config(SELECTIONS, checkpoint=None)
    assert cfg["max_debate_rounds"] == 2  # env value, not research_depth=5
    assert cfg["max_risk_discuss_rounds"] == 4


def test_partial_env_only_overrides_that_count(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "2")
    monkeypatch.delenv("TRADINGAGENTS_MAX_RISK_ROUNDS", raising=False)
    patched = dict(m.DEFAULT_CONFIG, max_debate_rounds=2)
    with mock.patch.object(m, "DEFAULT_CONFIG", patched):
        cfg = m._build_run_config(SELECTIONS, checkpoint=None)
    assert cfg["max_debate_rounds"] == 2  # env wins
    assert cfg["max_risk_discuss_rounds"] == 5  # falls through to research_depth


def test_checkpoint_none_preserves_env_default():
    patched = dict(m.DEFAULT_CONFIG, checkpoint_enabled=True)  # e.g. env-enabled
    with mock.patch.object(m, "DEFAULT_CONFIG", patched):
        cfg = m._build_run_config(SELECTIONS, checkpoint=None)
    assert cfg["checkpoint_enabled"] is True  # not clobbered back to False


@pytest.mark.parametrize("flag", [True, False])
def test_checkpoint_flag_overrides_env(flag):
    patched = dict(m.DEFAULT_CONFIG, checkpoint_enabled=not flag)
    with mock.patch.object(m, "DEFAULT_CONFIG", patched):
        cfg = m._build_run_config(SELECTIONS, checkpoint=flag)
    assert cfg["checkpoint_enabled"] is flag


def test_japan_cli_displays_only_canonical_accepted_report(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(m, "console", Console(file=output, force_terminal=False))
    report = "# 日本株レポート\n\n正規化済み本文。"
    state = {
        "market_context": {"market": "JP"},
        "market_report": "RAW AGENT PROSE MUST NOT APPEAR",
        "accepted_report_markdown": report,
        "final_output_contract": {
            "version": "v5",
            "status": "FINALIZED",
            "accepted_report_sha256": hashlib.sha256(report.encode()).hexdigest(),
        },
    }

    m.display_complete_report(state)

    rendered = output.getvalue()
    assert "正規化済み本文" in rendered
    assert "RAW AGENT PROSE" not in rendered


def test_japan_cli_rejects_noncanonical_report(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(m, "console", Console(file=output, force_terminal=False))
    state = {
        "market_context": {"market": "JP"},
        "market_report": "raw report",
        "final_output_contract": {"version": "v3", "status": "BLOCKED"},
    }

    with pytest.raises(ValueError, match="not an accepted canonical"):
        m.display_complete_report(state)
