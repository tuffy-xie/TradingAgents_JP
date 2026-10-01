"""Portfolio ownership must not erase attributed broker research facts."""
import hashlib
from unittest.mock import patch

import pytest

from tests.test_final_output_contract import _jp_state
from tradingagents import final_output as output
from tradingagents.rating_authority import internal_rating_claims, remove_internal_ratings
from tradingagents.reporting import write_report_tree
from web.server import _render_report_html


def state_with(text, field="news_report"):
    state = _jp_state()
    state["final_trade_decision"] = "**评级**: Underweight\n\n风险偏高。"
    state["risk_debate_state"]["judge_decision"] = state["final_trade_decision"]
    state["trader_investment_plan"] = "Recommendation: Hold"
    state["investment_debate_state"]["judge_decision"] = "风险偏高。"
    if "." in field:
        outer, inner = field.split(".")
        state[outer][inner] = text
    else:
        state[field] = text
    return state


INTERNAL = [
    "综合评级：HOLD（持有）", "**最终建议：BUY**", "Recommendation: Sell",
    "## Investment recommendation\n\nOverweight", "## 综合评级\nHold",
    "| 项目 | 结论 |\n|---|---|\n| 评级 | HOLD |",
    "我们建议持有。", "Our recommendation: Buy", "FINAL TRANSACTION PROPOSAL: HOLD",
    "| 综合评级 |\n|---|\n| HOLD |",
    "## News Analyst\nRecommendation: Hold",
    "投資判断: 買い転換の可能性",
    "## 投資判断\n買い",
]
EXTERNAL = [
    "某券商维持买入评级。", "日系中坚券商维持**强气（买入）评级**",
    "分析师共识偏多", "17/18 位分析师给予买入。",
    "Broker recommendation: Buy", "According to Goldman Sachs, Rating: Buy",
    "## 分析师共识\n\n| 评级 | 人数 |\n|---|---|\n| 买入 | 多数 |",
    "## Analyst consensus\n\nRecommendation: Buy",
    "评级下调至Hold会增加风险。",
    "| 公司 | 分析师评级 | 强气（买入） | 机构看好 |",
    "Nomura maintains Buy rating",
    "野村證券 投資判断: 買い",
    "| 外部来源 | 评级 |\n| --- | --- |\n| Nomura | Buy |",
    "| 分析师共识 | 强劲买入 |",
    "Yahoo 报道某券商维持 Buy",
    "米系大手証券：強気（継続）",
    "| External source | Signal |\n| --- | --- |\n| Broker research | Buy signal |",
    "AI服务器需求是正面因素；EPS预期上调。",
    "## 強気材料\nEPS 上方修正",
    "## 買い材料 (Bulls)\nAIサーバー需要拡大",
    "当前不建议买入。",
    "Not a Buy signal.",
    "| Signal | Status |\n| --- | --- |\n| Buy signal | 未确认 |",
    "買い示唆ではない。",
]

RECOMMENDATIONS = [
    ("| 企业 (TEST.T) | EPS trend | 上方修正 | 🟢 買い示唆 |", "Buy"),
    ("| 指标 | 状态 |\n| --- | --- |\n| EPS trend | 🟢 買い示唆 |", "Buy"),
    ("| 状態 | 売り示唆 |", "Sell"),
    ("| 判断 | 买入信号 |", "Buy"),
    ("| 判断 | 卖出信号 |", "Sell"),
    ("| Signal | Buy signal |", "Buy"),
    ("| Signal | Sell signal |", "Sell"),
    ("| Indicator | Signal |\n| --- | --- |\n| EPS trend | Buy |", "Buy"),
    ("買い推奨", "Buy"), ("売り推奨", "Sell"),
    ("買いの示唆", "Buy"), ("売りを推奨", "Sell"),
    ("建议买入", "Buy"), ("建议卖出", "Sell"),
]

TRANSITIONS = [
    ("Hold → Overweight", "Hold", "Overweight"),
    ("Hold -> Buy", "Hold", "Buy"),
    ("Buy ⇒ Sell", "Buy", "Sell"),
    ("Upgrade to Buy", None, "Buy"),
    ("Downgrade to Sell", None, "Sell"),
    ("上调至增持", None, "Overweight"),
    ("评级调整为买入", None, "Buy"),
    ("買いへ変更", None, "Buy"),
    ("Sellへ引き下げ", None, "Sell"),
    ("Overweightへ引き上げ", None, "Overweight"),
]


@pytest.mark.parametrize(("transition", "origin", "target"), TRANSITIONS)
@pytest.mark.parametrize("formatting", ["{}", "若订单落地：{}", "| 事件 | 评级调整方向 |\n| --- | --- |\n| 订单落地 | **{}** |"])
def test_rating_transition_has_target_ownership_even_when_hypothetical(transition, origin, target, formatting):
    text = formatting.format(transition)
    claims = internal_rating_claims(text)
    assert len(claims) == 1
    claim = claims[0]
    assert claim.semantic_type == "RATING_TRANSITION"
    assert (claim.from_rating, claim.rating) == (origin, target)
    assert text[claim.start:claim.end] == claim.text
    assert not internal_rating_claims(remove_internal_ratings(text))


@pytest.mark.parametrize("text", [
    "若订单落地，则重新评估。", "若 MACD 发生变化，则重新评估。",
    "Nomura upgraded the stock from Hold to Buy.",
    "Example Capital downgraded the stock to Sell.",
    "分析师一致预期为 Buy。", "野村証券はOverweightへ引き上げ。",
    "| 外部来源 | 评级变化 |\n| --- | --- |\n| Sample Research | Hold → Buy |",
    "评级下调至Hold会增加风险。", "Downgrade to Hold would increase risk.",
    "资产质量稳健，但事件风险偏高。", "BOJ大概率维持现状。",
])
def test_reassessment_external_transition_and_risk_description_remain(text):
    assert not internal_rating_claims(text)
    assert remove_internal_ratings(text) == text


@pytest.mark.parametrize("text", [
    '是"值得持有等待"的资产：维持现状是最优策略。',
    "该股票值得持有。", "The stock is worth holding.",
    "Our Research Manager upgraded the stock to Buy.",
    "Research Manager upgraded the stock from Hold to Buy.",
])
def test_internal_investment_stance_cannot_borrow_named_source_attribution(text):
    assert internal_rating_claims(text)


def test_transition_audit_preserves_origin_target_and_exact_artifact_lineage():
    text = "| 事件 | 评级调整方向 |\n| --- | --- |\n| 订单落地 | Hold → Overweight |"
    accepted = output.build_canonical_final_state(state_with(text, "investment_debate_state.judge_decision"))
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    findings = [x for x in accepted["evidence_audit"] if x.get("recommendation_semantics") == "RATING_TRANSITION"]
    assert len(findings) == 1
    finding = findings[0]
    assert finding["agent"] == "Research Manager"
    assert finding["detected_from_rating"] == "Hold"
    assert finding["detected_target_rating"] == "Overweight"
    assert finding["claim_sha256"] == hashlib.sha256(finding["original_claim"].encode()).hexdigest()
    assert finding["resolution"] == "CLAIM_REMOVED_OR_REPLACED"
    assert finding["authority_owner"] == "Portfolio Manager"
    assert finding["accepted_artifact_sha256"] == accepted["final_output_contract"]["accepted_report_sha256"]
    assert "Hold → Overweight" not in accepted["accepted_report_markdown"]
    tampered = accepted["accepted_report_markdown"] + "\n## 研究附录\n| 未来条件 | 评级变化 |\n| --- | --- |\n| 订单落地 | **HOLD -> Overweight** |\n"
    closed, issues = output._finalize_audit(findings, accepted, accepted_report=tampered, execution_allowed=False)
    assert issues
    assert closed[0]["resolution"] == "UNRESOLVED"


@pytest.mark.parametrize("transition", [x[0] for x in TRANSITIONS])
def test_exact_final_artifact_blocks_future_transition_without_upstream_pruning(transition):
    text = f"| 未来条件 | 评级调整方向 |\n| --- | --- |\n| 订单落地 | {transition} |"
    compose = output.compose_user_report_markdown
    # Inject after *all* field pruning, so the exact-artifact scanner alone must
    # defend against an upstream miss (including execution filtering).
    with patch.object(output, "compose_user_report_markdown", side_effect=lambda state: compose(state) + "\n## 研究附录\n" + text):
        accepted = output.build_canonical_final_state(state_with("风险偏高。"))
    contract = accepted["final_output_contract"]
    assert transition in accepted["accepted_report_markdown"]
    assert contract["status"] == "BLOCKED"
    assert not contract["validation_dimensions"]["domain_authority_consistent"]
    assert any(x.startswith("CROSS_DOMAIN_AUTHORITY:INVESTMENT_RATING") for x in contract["artifact_issues"])


def test_transition_pruning_retains_monitoring_and_external_sibling_rows():
    text = "| 事件 | 评级调整方向 |\n| --- | --- |\n| 订单落地 | Hold → Overweight |\n| 盈利改善 | 需要重新评估 |"
    result = remove_internal_ratings(text)
    assert "需要重新评估" in result
    assert "Overweight" not in result


@pytest.mark.parametrize(("text", "rating"), RECOMMENDATIONS)
def test_directional_recommendation_cell_has_row_claim_identity(text, rating):
    claims = internal_rating_claims(text)
    assert len(claims) == 1
    assert claims[0].rating == rating
    assert text[claims[0].start:claims[0].end] == claims[0].text
    assert not internal_rating_claims(remove_internal_ratings(text))


@pytest.mark.parametrize("field", ["news_report", "fundamentals_report", "investment_debate_state.judge_decision"])
def test_signal_authority_is_enforced_across_nonportfolio_sections(field):
    text = "| 指标 | 状态 |\n| --- | --- |\n| EPS trend | 🟢 買い示唆 |"
    state = state_with(text, field)
    accepted = output.build_canonical_final_state(state)
    assert "買い示唆" not in accepted["accepted_report_markdown"]
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    finding = next(x for x in accepted["evidence_audit"] if x.get("category") == "SECONDARY_INTERNAL_RATING" and x.get("field") == field)
    assert finding["original_claim"].strip() == "| EPS trend | 🟢 買い示唆 |"
    assert finding["claim_sha256"] == hashlib.sha256(finding["original_claim"].encode()).hexdigest()
    assert finding["detected_recommendation"] == "Buy"
    assert finding["authority_owner"] == "Portfolio Manager"
    assert finding["recommendation_semantics"] == "INVESTMENT_RECOMMENDATION"
    assert finding["resolution"] == "CLAIM_REMOVED_OR_REPLACED"
    assert finding["accepted_artifact_sha256"] == accepted["final_output_contract"]["accepted_report_sha256"]


@pytest.mark.parametrize("signal", ["🟢 買い示唆", "売り示唆", "Buy signal", "Sell signal", "买入信号", "卖出信号"])
def test_final_contract_blocks_signal_table_when_upstream_is_bypassed(signal):
    state = state_with(f"| 指标 | 状态 |\n| --- | --- |\n| EPS trend | {signal} |")
    state["final_trade_decision"] = "Rating: Hold"
    state["risk_debate_state"]["judge_decision"] = "Rating: Hold"
    with patch.object(output, "_enforce_portfolio_rating_ownership", side_effect=lambda candidate: (candidate, [])):
        accepted = output.build_canonical_final_state(state)
    assert signal in accepted["accepted_report_markdown"]
    contract = accepted["final_output_contract"]
    assert contract["status"] == "BLOCKED"
    assert contract["validation_dimensions"]["domain_authority_consistent"] is False
    assert any(issue.startswith("CROSS_DOMAIN_AUTHORITY:INVESTMENT_RATING") for issue in contract["artifact_issues"])


def test_signal_surviving_presentation_transform_cannot_close_audit():
    accepted = output.build_canonical_final_state(state_with("| 指标 | 状态 |\n| --- | --- |\n| EPS trend | 🟢 買い示唆 |"))
    audit = [x for x in accepted["evidence_audit"] if x.get("category") == "SECONDARY_INTERNAL_RATING" and x.get("agent") == "News Analyst"]
    assert audit
    tampered = accepted["accepted_report_markdown"] + "\n## 新闻附录\n| 指标 | 状态 |\n| --- | --- |\n| EPS trend | **買い示唆** |\n"
    findings, issues = output._finalize_audit(audit, accepted, accepted_report=tampered, execution_allowed=False)
    assert issues
    assert all(x["resolution"] == "UNRESOLVED" for x in findings)


def test_empty_source_column_does_not_authorize_external_recommendation():
    text = "| 外部来源 | 评级 |\n| --- | --- |\n| — | Buy |"
    assert internal_rating_claims(text)


def test_attributed_table_does_not_exempt_later_internal_signal_table():
    text = "| 外部来源 | 评级 |\n| --- | --- |\n| Nomura | Buy |\n\n| 指标 | 状态 |\n| --- | --- |\n| EPS trend | Buy signal |"
    claims = internal_rating_claims(text)
    assert len(claims) == 1
    assert "EPS trend" in claims[0].text
    assert "Nomura" in remove_internal_ratings(text)


def test_external_rating_cannot_exempt_explicit_system_signal():
    text = "某券商评级: Buy，但我们的卖出信号已经出现。"
    claims = internal_rating_claims(text)
    assert len(claims) == 1
    assert claims[0].rating == "Sell"


def test_signal_under_external_heading_still_respects_explicit_ownership():
    text = "## Broker research\nOur Buy signal is confirmed."
    assert internal_rating_claims(text)


def test_recommendation_row_pruning_preserves_adjacent_valid_table_rows():
    header = "| 指标 | 状态 |\n| --- | --- |\n"
    valid = "| EPS trend | 上方修正 |\n"
    signal = "| News。EPS | 🟢 買い示唆 |\n"
    text = header + signal + valid
    claims = internal_rating_claims(text)
    assert len(claims) == 1
    assert claims[0].text == signal.rstrip("\n")
    assert remove_internal_ratings(text) == header + valid
    accepted = output.build_canonical_final_state(state_with(text))
    assert "| EPS trend | 上方修正 |" in accepted["accepted_report_markdown"]
    assert "買い示唆" not in accepted["accepted_report_markdown"]
    assert accepted["final_output_contract"]["status"] == "FINALIZED"


@pytest.mark.parametrize("text", INTERNAL)
def test_internal_recommendations_have_claim_identity(text):
    claims = internal_rating_claims(text)
    assert claims
    assert all(text[c.start:c.end] == c.text for c in claims)
    assert not internal_rating_claims(remove_internal_ratings(text))


@pytest.mark.parametrize("text", EXTERNAL)
def test_external_facts_and_research_are_not_internal_recommendations(text):
    assert not internal_rating_claims(text)
    assert remove_internal_ratings(text) == text


@pytest.mark.parametrize("field", ["market_report", "news_report", "fundamentals_report", "investment_debate_state.judge_decision"])
def test_ownership_applies_across_analyst_and_research_fields(field, tmp_path):
    foreign = "某券商维持强气（买入）评级。"
    accepted = output.build_canonical_final_state(state_with(foreign + "\n综合评级：HOLD（持有）", field))
    contract = accepted["final_output_contract"]
    assert contract["status"] == "FINALIZED"
    assert contract["portfolio_rating"] == "Underweight"
    assert contract["validation_dimensions"]["domain_authority_consistent"]
    text = accepted["accepted_report_markdown"]
    assert foreign in text
    assert "综合评级：HOLD" not in text
    findings = [x for x in accepted["evidence_audit"] if x.get("category") == "SECONDARY_INTERNAL_RATING"]
    assert findings
    assert all(x["resolution"] == "CLAIM_REMOVED_OR_REPLACED" for x in findings)
    assert all(x["claim_sha256"] == hashlib.sha256(x["original_claim"].encode()).hexdigest() for x in findings)
    assert write_report_tree(accepted, "TEST.T", tmp_path).read_text() == text
    assert "综合评级：HOLD" not in _render_report_html(accepted, auto_print=False)


def test_attribution_is_clause_local_and_does_not_exempt_internal_sibling():
    text = "某券商维持买入评级；综合评级：HOLD（持有）。"
    cleaned = remove_internal_ratings(text)
    assert "某券商维持买入评级" in cleaned
    assert "HOLD" not in cleaned


def test_exact_artifact_defense_without_upstream_rating_pruning():
    with patch.object(output, "_enforce_portfolio_rating_ownership", side_effect=lambda state: (state, [])):
        accepted = output.build_canonical_final_state(state_with("综合评级：HOLD（持有）"))
    assert "综合评级：HOLD" in accepted["accepted_report_markdown"]
    contract = accepted["final_output_contract"]
    assert contract["status"] == "BLOCKED"
    assert contract["audit_closure_status"] == "UNRESOLVED"
    assert not contract["validation_dimensions"]["domain_authority_consistent"]
    assert any(x.startswith("CROSS_DOMAIN_AUTHORITY:INVESTMENT_RATING") for x in contract["artifact_issues"])
    with pytest.raises(ValueError):
        _render_report_html(accepted, auto_print=False)


def test_japanese_news_investment_rating_is_removed_but_broker_rating_remains():
    state = state_with("野村證券 投資判断: 買い\n投資判断: 買い転換の可能性")
    accepted = output.build_canonical_final_state(state)
    text = accepted["accepted_report_markdown"]
    assert "野村證券 投資判断: 買い" in text
    assert "投資判断: 買い転換の可能性" not in text
    assert accepted["final_output_contract"]["status"] == "FINALIZED"
    # Trader's raw recommendation is also now audited before replacement.
    findings = [x for x in accepted["evidence_audit"] if x.get("category") == "SECONDARY_INTERNAL_RATING" and x.get("agent") == "News Analyst"]
    assert len(findings) == 1
    assert findings[0]["agent"] == "News Analyst"
    assert findings[0]["claim_sha256"] == hashlib.sha256(findings[0]["original_claim"].encode()).hexdigest()
    assert findings[0]["resolution"] == "CLAIM_REMOVED_OR_REPLACED"


def test_exact_artifact_defense_blocks_japanese_rating_without_upstream_pruning():
    with patch.object(output, "_enforce_portfolio_rating_ownership", side_effect=lambda state: (state, [])):
        accepted = output.build_canonical_final_state(state_with("投資判断: 買い転換の可能性"))
    contract = accepted["final_output_contract"]
    assert "投資判断: 買い転換の可能性" in accepted["accepted_report_markdown"]
    assert contract["status"] == "BLOCKED"
    assert contract["validation_dimensions"]["domain_authority_consistent"] is False
    assert any(issue.startswith("CROSS_DOMAIN_AUTHORITY:INVESTMENT_RATING:") for issue in contract["artifact_issues"])


def test_audit_does_not_close_format_changed_secondary_rating():
    accepted = output.build_canonical_final_state(state_with("综合评级：HOLD（持有）"))
    audit = [x for x in accepted["evidence_audit"] if x.get("category") == "SECONDARY_INTERNAL_RATING"]
    tampered = accepted["accepted_report_markdown"] + "\n## V. 新闻附录\nRecommendation: **Hold**\n"
    findings, issues = output._finalize_audit(audit, accepted, accepted_report=tampered, execution_allowed=False)
    assert issues
    assert all(x["resolution"] == "UNRESOLVED" for x in findings)
    assert all(x["execution_blocking"] for x in findings)


def test_same_rating_in_another_section_is_still_second_authority():
    accepted = output.build_canonical_final_state(state_with("Recommendation: Underweight"))
    assert "Recommendation: Underweight" not in accepted["accepted_report_markdown"]
    assert accepted["final_output_contract"]["status"] == "FINALIZED"


def test_portfolio_not_execution_side_field_owns_rating():
    accepted = output.build_canonical_final_state(state_with("风险较高。"))
    accepted["validated_execution"]["portfolio_rating"] = "Hold"
    assert not output._rating_artifact_claims(accepted, accepted["accepted_report_markdown"])


def test_publisher_rejects_injected_secondary_rating_even_with_recomputed_hash(tmp_path):
    accepted = output.build_canonical_final_state(state_with("风险较高。"))
    accepted["accepted_report_markdown"] += "\n## V. 补充分析\nRecommendation: Buy\n"
    accepted["final_output_contract"]["accepted_report_sha256"] = hashlib.sha256(accepted["accepted_report_markdown"].encode()).hexdigest()
    with pytest.raises(ValueError):
        write_report_tree(accepted, "TEST.T", tmp_path)


def test_us_rating_behavior_unchanged(tmp_path):
    state = {"market_context": {"market": "US"}, "news_report": "Recommendation: Hold",
             "final_trade_decision": "Rating: Buy"}
    assert output.build_canonical_final_state(state) == state
    assert "Recommendation: Hold" in write_report_tree(state, "TEST", tmp_path).read_text()
