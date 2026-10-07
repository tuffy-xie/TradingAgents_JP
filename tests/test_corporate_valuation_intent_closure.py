"""Corporate action facts do not witness management valuation intent."""
import copy
import hashlib
from unittest.mock import patch

import pytest

from tests.test_published_authority_ownership import sell_state
from tradingagents import final_output as f
from tradingagents.agents.evidence_enforcement import enforce_agent_output


def corporate_state(text="风险较高。"):
    state = sell_state(text)
    symbol = state["market_context"]["symbol"]
    state["evidence_registry"].append({
        "evidence_id": "E-buyback", "domain": "NEWS", "claim_type": "FACT", "source": "TDnet",
        "source_type": "buyback", "source_url": "https://example.test/buyback.pdf",
        "verification_status": "VERIFIED_SOURCE", "allowed_for_current_decision": True,
        "value": "回购决议已通过。回购期间截至2027年1月29日。",
    })
    state["japan_data_bundle"]["items"].append({
        "source": "TDnet", "source_type": "buyback", "ticker": symbol, "verified": True,
        "status": "OK", "url": "https://example.test/buyback.pdf",
        "metadata": {"key_facts": ["回购决议已通过。", "回购期间截至2027年1月29日。"]},
    })
    return state


INTENT = [
    "持续的大规模回购表明管理层认为当前股价具有投资价值。",
    "结合股票回购计划，侧面反映公司认为自身估值具备吸引力。",
    "回购彰显管理层对自身估值的信心。",
    "董事会认定本公司股票被低估。",
    "公司相信当前股价便宜。",
    "管理層認為當前股價具有投資價值。",
    "The buyback shows management believes the stock price is undervalued.",
    "The company considers its valuation attractive.",
    "Buybacks signal management confidence in the company's valuation.",
    "| 公司行为 | 解读 |\n|---|---|\n| 股票回购 | 公司认为自身估值具有吸引力 |",
    "管理层认为当前股价便宜，但回购可能放缓。",
]


@pytest.mark.parametrize("text", INTENT)
def test_action_evidence_does_not_witness_owner_mental_valuation(text):
    checked = enforce_agent_output(corporate_state(), text, "News Analyst")
    assert any(x.warning == "corporate_valuation_intent_mismatch" for x in checked.findings)
    assert not any(x.warning == "corporate_valuation_intent_mismatch"
                   for x in enforce_agent_output(corporate_state(), checked.text, "News Analyst").findings)


@pytest.mark.parametrize("text", [
    "回购决议已通过。", "股票回购计划执行中。", "回购期间截至2027年1月29日。",
    "分析判断：回购可能增加股票需求。",
    "回购可能反映管理层对自身估值的信心，但还需确认。",
    "无法确认公司认为当前股价便宜。",
    "本报告认为公司的估值具备吸引力。",
    "公司具有良好的成长潜力。", "管理层认为业务布局有利于未来增长。",
    "Nomura believes the stock is undervalued.",
    "公司经营资源配置优化。", "空仓者禁止建仓。",
])
def test_action_facts_hypotheses_own_analysis_and_business_language_survive(text):
    assert not enforce_agent_output(corporate_state(), text, "News Analyst").findings


@pytest.mark.parametrize("text", INTENT)
def test_post_composition_injection_blocks_without_upstream_finding(text):
    compose = f.compose_user_report_markdown
    with patch.object(f, "compose_user_report_markdown", side_effect=lambda s:
                      compose(s) + "\n\n## 新闻补充\n\n" + text):
        state = f.build_canonical_final_state(corporate_state())
    c = state["final_output_contract"]
    assert c["status"] == "BLOCKED" and c["audit_closure_status"] != "CLOSED"
    assert not c["validation_dimensions"]["domain_authority_consistent"]
    assert any(x.startswith("CROSS_DOMAIN_AUTHORITY:NEWS_ATTRIBUTION:") for x in c["artifact_issues"])


def test_own_official_document_statement_can_witness_intent_but_other_issuer_cannot():
    text = "董事会认定本公司股票被低估。"
    state = corporate_state()
    item = state["japan_data_bundle"]["items"][-1]
    item["metadata"]["key_facts"].append(text)
    assert not enforce_agent_output(state, text, "News Analyst").findings
    item["metadata"]["title_body_conflict"] = True
    assert enforce_agent_output(state, text, "News Analyst").findings
    item["metadata"]["title_body_conflict"] = False
    item["ticker"] = "OTHER.T"
    assert enforce_agent_output(state, text, "News Analyst").findings
    item["ticker"] = state["market_context"]["symbol"]
    state["evidence_registry"][-1]["allowed_for_current_decision"] = False
    assert enforce_agent_output(state, text, "News Analyst").findings


def test_literal_verified_official_statement_and_registry_scope():
    text = "管理层认为当前股价具有投资价值。"
    state = corporate_state()
    state["evidence_registry"][-1].update(value=text, ticker=state["market_context"]["symbol"])
    assert not enforce_agent_output(state, text, "News Analyst").findings
    state["evidence_registry"][-1]["source"] = "Issuer interview"
    assert not enforce_agent_output(state, text, "News Analyst").findings
    state["evidence_registry"][-1]["ticker"] = "OTHER.T"
    assert enforce_agent_output(state, text, "News Analyst").findings


def test_same_class_rewording_cannot_close_a_removed_intent_claim():
    state = corporate_state()
    original = "公司认为自身估值具备吸引力。"
    audit = [{"category": "SEMANTIC_MISMATCH", "agent": "News Analyst", "field": "news_report",
              "warning": "corporate_valuation_intent_mismatch", "original_claim": original,
              "claim_sha256": hashlib.sha256(original.encode()).hexdigest(), "replacement_claim": ""}]
    candidate = "回购彰显管理层对自身估值的信心。"
    state["news_report"] = candidate
    closed, issues = f._finalize_audit(copy.deepcopy(audit), state,
                                     accepted_report=candidate, execution_allowed=True)
    assert closed[0]["resolution"] == "UNRESOLVED" and closed[0]["execution_blocking"] and issues
    accepted = f.build_canonical_final_state(corporate_state(candidate))
    findings = [x for x in accepted["evidence_audit"] if x.get("warning") == "corporate_valuation_intent_mismatch"]
    assert findings and all(x["resolution"] == "CLAIM_REMOVED_OR_REPLACED" for x in findings)
    for x in findings:
        assert x["claim_sha256"] == hashlib.sha256(x["original_claim"].encode()).hexdigest()
        assert x["accepted_artifact_sha256"] == accepted["final_output_contract"]["accepted_report_sha256"]
