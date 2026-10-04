"""Regression coverage for unavailable social-source semantics."""

from tradingagents.agents.analysts.sentiment_analyst import (
    _apply_source_status_integrity,
    _build_system_message,
)
from tradingagents.agents.evidence_enforcement import enforce_agent_result


def test_rate_limited_reddit_is_not_rendered_as_zero_mentions():
    block = (
        "<reddit status=RATE_LIMITED; sample_count=UNKNOWN> Reddit data is "
        "unavailable for this run."
    )
    report = _apply_source_status_integrity("其他来源可用。", block)
    assert "帖子数量为未知" in report
    assert "零提及" in report
    assert "其他来源可用。" in report


def test_failed_reddit_is_not_directional_evidence():
    block = "<reddit status=FETCH_FAILED; sample_count=UNKNOWN>"
    report = _apply_source_status_integrity("保持低置信度。", block)
    assert "任何方向性证据" in report
    assert "帖子数量为未知" in report


def test_successful_zero_sample_does_not_receive_unavailable_correction():
    report = _apply_source_status_integrity(
        "在成功获取范围内未发现帖子。",
        "<no Reddit posts found mentioning NVDA>",
    )
    assert not report.startswith("## 数据源状态")


def test_sentiment_prompt_prohibits_unavailable_reddit_attention_inference():
    prompt = _build_system_message(
        ticker="CRCL",
        start_date="2026-08-08",
        end_date="2026-08-15",
        news_block="news",
        stocktwits_block="stocktwits",
        reddit_block="<reddit status=RATE_LIMITED; sample_count=UNKNOWN>",
    )
    assert "UNKNOWN count, not zero" in prompt
    assert "attention, lack of FOMO" in prompt


def test_downstream_debate_cannot_turn_rate_limit_into_attention_signal():
    state = {
        "market_context": {"market": "US"},
        "sentiment_report": (
            "Reddit：本次因限流不可用；帖子数量为未知，情绪与社区热度均无法判断。"
        ),
    }
    result = enforce_agent_result(
        state,
        {
            "investment_debate_state": {
                "history": "Reddit 0 mentions show low attention and no FOMO。"
            }
        },
        "Bull Researcher",
    )
    history = result["investment_debate_state"]["history"]
    assert "0 mentions" not in history
    assert "数据源约束" in history
