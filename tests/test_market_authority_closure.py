"""Lossless JP Market authority and cross-section publication regressions."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import pytest
from langchain_core.messages import ToolMessage

from tradingagents.agents.evidence_registry import capture_agent_evidence
from tradingagents.agents.market_authority import (
    canonical_market_authority,
    summarize_market_tool_response,
)
from tradingagents.agents.market_claims import (
    current_market_claims,
    remove_current_market_claims,
)
from tradingagents.final_output import (
    _artifact_market_claims,
    _canonicalize_market_report,
    _validate_final_artifact,
    build_canonical_final_state,
    require_canonical_final_state,
)

AS_OF = "2026-09-28"


def _stock_response(*, latest: str = AS_OF) -> str:
    end = date.fromisoformat(latest)
    dates = [end - timedelta(days=offset) for offset in range(560)]
    dates = sorted(day for day in dates if day.weekday() < 5)
    lines = [
        f"# Stock data for TEST.T from 2025-03-01 to {AS_OF}",
        f"# Total records: {len(dates)}",
        "# Data retrieved on: 2026-09-28 23:15:00",
        "",
        "Date,Open,High,Low,Close,Volume",
    ]
    lines.extend(
        f"{day.isoformat()},100.0,103.0,99.0,102.0,120000"
        for day in dates
    )
    return "\n".join(lines) + "\n"


def _indicator_response(*, latest: str = AS_OF) -> str:
    return (
        f"## macd values from 2026-08-29 to {AS_OF}:\n\n"
        f"Underlying OHLCV latest completed bar: {latest}\n\n"
        f"{latest}: 2.5\n\nMACD trend indicator."
    )


def _state_with_tools(*, stock_latest: str = AS_OF, indicator_latest: str = AS_OF):
    state = {
        "market_context": {"market": "JP", "symbol": "TEST.T"},
        "trade_date": AS_OF,
        "run_manifest": {
            "analysis_as_of": AS_OF,
            "runtime_timestamp_jst": "2026-09-28T23:15:00+09:00",
        },
        "evidence_registry": [],
        "evidence_audit": [],
        "messages": [
            ToolMessage(
                content=_stock_response(latest=stock_latest),
                name="get_stock_data",
                tool_call_id="stock-1",
            ),
            ToolMessage(
                content=_indicator_response(latest=indicator_latest),
                name="get_indicators",
                tool_call_id="indicator-1",
            ),
        ],
    }
    captured = capture_agent_evidence(
        state, {}, "Market Analyst", capture_reports=False
    )
    return {**state, **captured}


def _minimal_final_state():
    return {
        "company_of_interest": "TEST.T",
        "trade_date": AS_OF,
        "market_context": {
            "market": "JP", "symbol": "TEST.T", "currency": "JPY",
            "instrument_type": "EQUITY",
        },
        "run_manifest": {
            "analysis_as_of": AS_OF,
            "runtime_timestamp_jst": "2026-09-28T23:15:00+09:00",
        },
        "japan_data_bundle": {"items": [], "provider_metadata": {}},
        "evidence_registry": [
            {
                "domain": "NEWS", "claim_type": "FACT",
                "source": "Yahoo Finance Japan",
                "value": "Yahoo Finance Japan 报道标题称该股4日続伸。",
                "allowed_for_current_decision": True,
            }
        ],
        "evidence_audit": [],
        "market_report": "MACD黄金交叉，当前技术面转折成立。",
        "news_report": (
            "Yahoo Finance Japan 报道标题称该股4日続伸。\n"
            "因此买盘支撑很强，当前趋势转多。"
        ),
        "fundamentals_report": "",
        "sentiment_report": "",
        "investment_debate_state": {
            "judge_decision": "## 研究判断\n技术面转折成立：MACD黄金交叉。\n非市场领域仍需研究。"
        },
        "trader_investment_plan": "Hold。",
        "risk_debate_state": {
            "judge_decision": "Rating: Hold\n技术面转折明确（已核验的工具数据）。"
        },
        "final_trade_decision": "Rating: Hold\n技术面转折明确（已核验的工具数据）。",
        "validated_execution": {
            "status": "DATA_UNAVAILABLE",
            "detail": "HOLD_DOES_NOT_AUTHORIZE_NEW_EXECUTION",
        },
    }


def test_long_stock_response_keeps_lossless_authority_metadata():
    state = _state_with_tools()
    stock = next(
        item for item in state["evidence_registry"]
        if item["source"] == "get_stock_data"
    )
    summary = stock["derivation"]["market_data"]
    assert len(_stock_response()) > 12_000
    assert len(stock["value"]) == 12_000
    assert "2026-09-28" not in stock["value"].splitlines()[-1]
    assert summary["status"] == "PARSED_COMPLETE_PAYLOAD"
    assert summary["requested_end"] == AS_OF
    assert summary["latest_complete_ohlcv_date"] == AS_OF
    assert summary["reported_row_count"] == summary["complete_ohlcv_row_count"]
    assert stock["freshness"] == "LATEST_AVAILABLE"
    assert stock["source_record_id"] == "stock-1"
    assert stock["fetched_at"]
    assert canonical_market_authority(state)["status"] == "CURRENT"


def test_presentation_truncation_does_not_change_market_freshness():
    state = _state_with_tools()
    stock = next(
        item for item in state["evidence_registry"]
        if item["source"] == "get_stock_data"
    )
    stock["value"] = stock["value"][:40]
    assert canonical_market_authority(state)["status"] == "CURRENT"
    # A legacy state with no complete-payload summary never recovers authority
    # by parsing the shortened CSV or trusting a reported record count.
    stock["derivation"].pop("market_data")
    assert canonical_market_authority(state)["status"] == "UNAVAILABLE"


def test_stock_indicator_disagreement_fails_closed():
    state = _state_with_tools(indicator_latest="2026-09-25")
    stale_indicator = next(
        item for item in state["evidence_registry"]
        if item["source"] == "get_indicators"
    )
    assert stale_indicator["freshness"] == "STALE_SOURCE"
    assert stale_indicator["allowed_for_current_decision"] is False
    assert canonical_market_authority(state)["reason"] == "STOCK_INDICATOR_DATE_CONFLICT"
    state["market_report"] = "当前MACD黄金交叉。"
    assert "证据不足" in _canonicalize_market_report(state)


def test_partial_or_miscounted_stock_response_is_not_current():
    full = _stock_response()
    summary = summarize_market_tool_response("get_stock_data", full[:12_000])
    assert summary["status"] != "PARSED_COMPLETE_PAYLOAD"


def test_unavailable_market_prunes_all_published_sections_but_keeps_news_fact():
    accepted = build_canonical_final_state(_minimal_final_state())
    report = accepted["accepted_report_markdown"]
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert accepted["final_output_contract"]["audit_closure_status"] == "CLOSED"
    assert not _artifact_market_claims(accepted, report)
    assert "MACD黄金交叉" not in report
    assert "技术面转折" not in report
    assert "Yahoo Finance Japan 报道标题称该股4日続伸" in report
    assert "买盘支撑很强" not in report
    assert "非市场领域仍需研究" in report
    findings = [
        item for item in accepted["evidence_audit"]
        if item.get("category") == "MARKET_AUTHORITY_CLAIM"
    ]
    assert findings
    assert all(item["resolution"] == "CLAIM_REMOVED_OR_REPLACED" for item in findings)
    assert all(item["claim_sha256"] and item["accepted_artifact_sha256"] for item in findings)
    require_canonical_final_state(accepted)


def test_exact_artifact_defense_blocks_market_claim_when_pruning_is_bypassed():
    accepted = build_canonical_final_state(_minimal_final_state())
    injected = accepted["accepted_report_markdown"] + "\n\n当前技术面转折成立：MACD黄金交叉。"
    issues = _validate_final_artifact(
        accepted, False, accepted_report=injected
    )
    assert any(issue.startswith("CROSS_DOMAIN_AUTHORITY:MARKET:") for issue in issues)
    assert current_market_claims(injected, analysis_as_of=AS_OF)


def test_news_derived_market_inference_is_removed_with_claim_level_audit():
    state = _minimal_final_state()
    state["news_report"] = (
        "Yahoo Finance Japan 报道标题称该股4日続伸。\n"
        "该消息是推动近期股价连续走强的核心催化剂。\n"
        "该协议可能成为未来业绩增长催化剂。"
    )
    accepted = build_canonical_final_state(state)
    report = accepted["accepted_report_markdown"]
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert "4日続伸" in report
    assert "未来业绩增长催化剂" in report
    assert "推动近期股价连续走强" not in report
    finding = next(
        item for item in accepted["evidence_audit"]
        if item.get("category") == "MARKET_AUTHORITY_CLAIM"
        and "推动近期股价" in item.get("original_claim", "")
    )
    assert finding["agent"] == "News Analyst"
    assert finding["field"] == "news_report"
    assert finding["required_domain_authority"] == "CURRENT_MARKET"
    assert finding["authority_state"] == "UNAVAILABLE"
    assert finding["claim_sha256"]
    assert finding["enforcement_action"] == "REMOVED"
    assert finding["resolution"] == "CLAIM_REMOVED_OR_REPLACED"
    assert finding["accepted_artifact_sha256"] == accepted["final_output_contract"]["accepted_report_sha256"]


def test_exact_artifact_defense_blocks_news_derived_market_inference():
    accepted = build_canonical_final_state(_minimal_final_state())
    injected = (
        accepted["accepted_report_markdown"]
        + "\n\n该消息推动近期股价连续走强。"
    )
    issues = _validate_final_artifact(accepted, False, accepted_report=injected)
    assert any(issue.startswith("CROSS_DOMAIN_AUTHORITY:MARKET:") for issue in issues)
    assert current_market_claims(injected, analysis_as_of=AS_OF)


def test_final_contract_blocks_news_inference_without_upstream_market_findings(monkeypatch):
    import tradingagents.final_output as final_output

    claim = "该消息推动近期股价连续走强。"
    state = _minimal_final_state()
    state["news_report"] = "Yahoo Finance Japan 报道标题称该股4日続伸。\n" + claim
    monkeypatch.setattr(
        final_output,
        "_enforce_market_authority_ownership",
        lambda candidate: (dict(candidate), []),
    )

    accepted = build_canonical_final_state(state)
    contract = accepted["final_output_contract"]
    assert claim in accepted["accepted_report_markdown"]
    assert not any(
        item.get("category") == "MARKET_AUTHORITY_CLAIM"
        for item in accepted["evidence_audit"]
    )
    assert contract["status"] == "BLOCKED"
    assert contract["validation_dimensions"]["domain_authority_consistent"] is False
    assert (
        "CROSS_DOMAIN_AUTHORITY:MARKET:"
        + hashlib.sha256(claim.encode("utf-8")).hexdigest()
    ) in contract["artifact_issues"]


def test_japanese_market_inference_is_removed_without_erasing_attributed_news():
    state = _minimal_final_state()
    state["news_report"] = (
        "Kabutan は『株価の底堅さがみられる』と報じた。\n"
        "EPS訂正が上方修正されたことで株価テクニカル面での上昇トレンド示唆。\n"
        "分析师一致预期上调。"
    )
    accepted = build_canonical_final_state(state)
    report = accepted["accepted_report_markdown"]
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    assert "底堅さがみられる" in report
    assert "分析师一致预期上调" in report
    assert "株価テクニカル面での上昇トレンド示唆" not in report
    findings = [
        item for item in accepted["evidence_audit"]
        if item.get("category") == "MARKET_AUTHORITY_CLAIM"
        and item.get("field") == "news_report"
    ]
    assert len(findings) == 1
    assert findings[0]["agent"] == "News Analyst"
    assert findings[0]["required_domain_authority"] == "CURRENT_MARKET"
    assert findings[0]["authority_state"] == "UNAVAILABLE"
    assert findings[0]["resolution"] == "CLAIM_REMOVED_OR_REPLACED"
    assert findings[0]["accepted_artifact_sha256"] == accepted["final_output_contract"]["accepted_report_sha256"]


@pytest.mark.parametrize(
    "claim",
    [
        "株価テクニカル面での上昇トレンド示唆",
        "現在の株価は上昇トレンドに入った",
        "買い優勢が鮮明になった",
        "上昇基調に転じた",
        "テクニカル的に強気",
        "株価上昇余地が拡大",
        "トレンド転換を確認",
        "Technical uptrend is now confirmed.",
        "Current buying momentum is strengthening.",
        "Shares have turned bullish.",
        "The stock is entering an uptrend.",
        "当前趋势转多。",
        "技术面开始走强。",
        "股价进入上升趋势。",
        "当前买盘增强。",
        "現在の株価は上昇トレンドに入ったが、記事では強気と報じた。",
    ],
)
def test_multilingual_current_market_propositions_need_market_authority(claim):
    assert current_market_claims(claim, analysis_as_of=AS_OF)


@pytest.mark.parametrize(
    "fact",
    [
        "Yahoo Finance Japan の見出しは『4日続伸』と報じた。",
        "Kabutan は『7000円～7500円辺りでの底堅さがみられる』と報じた。",
        "分析师一致预期上调。",
        "该协议可能成为未来业务增长催化剂。",
        "若未来 MACD 跌破零轴，则重新评估。",
    ],
)
def test_multilingual_news_facts_and_future_conditions_remain_available(fact):
    assert not current_market_claims(fact, analysis_as_of=AS_OF)
    assert remove_current_market_claims(fact, analysis_as_of=AS_OF) == fact


@pytest.mark.parametrize(
    "claim",
    ["株価テクニカル面での上昇トレンド示唆", "Technical uptrend is now confirmed."],
)
def test_exact_artifact_defense_blocks_multilingual_market_claim_without_upstream(monkeypatch, claim):
    import tradingagents.final_output as final_output

    state = _minimal_final_state()
    state["news_report"] = claim
    monkeypatch.setattr(
        final_output,
        "_enforce_market_authority_ownership",
        lambda candidate: (dict(candidate), []),
    )
    accepted = build_canonical_final_state(state)
    contract = accepted["final_output_contract"]
    assert claim in accepted["accepted_report_markdown"]
    assert contract["status"] == "BLOCKED"
    assert contract["validation_dimensions"]["domain_authority_consistent"] is False
    assert any(issue.startswith("CROSS_DOMAIN_AUTHORITY:MARKET:") for issue in contract["artifact_issues"])


@pytest.mark.parametrize(
    "text",
    [
        "Yahoo Finance Japan 报道标题称该股“4日続伸”。",
        "报道称该股连续第四日上涨。",
        "该协议可能成为未来业绩增长催化剂。",
        "该合作有望强化5G/6G业务布局。",
        "若未来MACD柱状图跌破零轴，则视为需要重新评估的技术信号。",
        "MACD柱状图回落至零轴下方 → 技术转折失效信号。",
    ],
)
def test_market_unavailable_preserves_news_facts_business_and_hypotheses(text):
    assert not current_market_claims(text, analysis_as_of=AS_OF)
    assert remove_current_market_claims(text, analysis_as_of=AS_OF) == text


@pytest.mark.parametrize(
    "text",
    [
        "该消息推动近期股价连续走强。",
        "该事件是近期上涨的核心催化剂。",
        "消息发布后买盘明显增强。",
        "当前走势因该消息明显转强。",
        "The announcement drove the recent rally.",
        "Strong buying support confirms the current move.",
    ],
)
def test_market_unavailable_blocks_news_derived_current_price_inference(text):
    assert current_market_claims(text, analysis_as_of=AS_OF)
    assert not current_market_claims(
        remove_current_market_claims(text, analysis_as_of=AS_OF),
        analysis_as_of=AS_OF,
    )


@pytest.mark.parametrize(
    ("text", "current"),
    [
        ("Yahoo Finance Japan 报道标题称该股4日続伸。", False),
        ("Yahoo Finance Japan 报道标题称该股4日続伸，因此买盘支撑很强。", True),
        ("当前RSI处于超买。", True),
        ("现价突破当前阻力。", True),
        ("2025-09-30 的历史 MACD 金叉仅供回顾。", False),
        ("若 MACD 回落至零轴下方，趋势风险可能上升。", False),
    ],
)
def test_news_fact_and_current_technical_claim_boundary(text, current):
    claims = current_market_claims(text, analysis_as_of=AS_OF)
    assert bool(claims) is current
    if current:
        assert not current_market_claims(
            remove_current_market_claims(text, analysis_as_of=AS_OF),
            analysis_as_of=AS_OF,
        )


def test_current_verified_market_keeps_technical_analysis():
    state = _state_with_tools()
    state["market_report"] = "当前MACD黄金交叉，RSI处于中性区间。"
    assert canonical_market_authority(state)["status"] == "CURRENT"
    assert _canonicalize_market_report(state) == state["market_report"]
