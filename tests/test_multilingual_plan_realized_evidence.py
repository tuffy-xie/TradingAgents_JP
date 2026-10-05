"""Realized evidence and multilingual plans retain the existing owners."""
import hashlib
from unittest.mock import patch

import pytest

from tests.test_predicate_authority_closure import regime_state, unavailable_actual
from tests.test_published_authority_ownership import sell_state
from tests.test_rating_authority import state_with
from tradingagents import final_output as f
from tradingagents.agents.evidence_enforcement import (
    current_financial_gate_violation,
    probability_event_gate_violation,
)
from tradingagents.rating_authority import internal_rating_claims, remove_internal_ratings
from tradingagents.reporting import write_report_tree

PLANS = [
    "押し目を待ったエントリーがリスク対効果の上で優位だ。",
    "果断なエントリーも許容範囲内と判断する。",
    "70に近づく場合は短期的な利益確定の検討余地がある。",
    "8,000円でエントリーを検討する。",
    "## 具体的アクションプラン\n\n買い増しを推奨する。",
    "## 目標価格と段階的利確\n\n業績を監視する。",
    "## 損切りとリスク管理\n\n業績を監視する。",
    "具体的アクションプラン：\n\n**1. 目標価格と段階的利確**\n\n8,500円で利確を検討する。",
    "**1. 損切りとリスク管理**\n\n業績を監視する。",
]
FRAMES = ["## 投資判断レポート", "## 投資判断のまとめ"]
ACTUALS = [
    '業績の質的転換が明白だ。',
    '業績の加速は事実だ。', '前期比利益率悪化という前提自体が崩れつつある。',
    '业绩的三倍加速是真实的运营杠杆。', '结构性的业绩好转压倒了估值担忧。',
    'Earnings growth has been realized.',
    '业绩的加速是真实的运营杠杆，未来需求可能增长。',
    '看多（业绩证据压倒宏观谨慎）。',
]
MACRO = [
    '| 日本2026年衰退概率4% | 股价正向 |',
    '| 日本2026年衰退概率4% | 低系统性风险 |',
    '| 美联储2026年不降息概率96% | 市场大幅转向鹰派 |',
]


def macro_state():
    state = regime_state()
    state['evidence_registry'][-1]['value'] += '\n- **Japan recession in 2026?** — Yes 4%'
    return state


@pytest.mark.parametrize('text', PLANS)
def test_japanese_execution_owns_directive_suitability_and_plan_surface(text):
    assert f._artifact_execution_claims(text)
    accepted = f.build_canonical_final_state(sell_state(text, 'market_report'))
    assert accepted['final_output_contract']['status'] == 'FINALIZED'
    unauthorized = f._artifact_without_validated_execution(accepted, accepted['accepted_report_markdown'])
    assert not f._artifact_execution_claims(unauthorized)
    findings = [x for x in accepted['evidence_audit'] if x.get('category') == 'UNAPPROVED_EXECUTION_CLAIM']
    assert findings and all(x['resolution'] in {'CLAIM_REMOVED_OR_REPLACED', 'NOT_PUBLISHED_IN_FINAL_ARTIFACT'} for x in findings)
    for finding in findings:
        assert hashlib.sha256(finding['original_claim'].encode()).hexdigest() == finding['claim_sha256']


@pytest.mark.parametrize('text', [
    '現状8,467円では新規買いは急がない。', 'エントリーは見送る。',
    '8,000円でエントリーしない。', '利益確定は推奨しない。',
    '過去のエントリー記録を分析する。', 'RSIは62.14で上昇トレンドを示す。',
    '自社株買いの実施が継続している。', '会社の設備投資判断レポート',
    '## アナリストの目標価格\nNomura maintains Buy.',
    '**当前不提供入场参数**',
])
def test_research_business_external_attribution_and_japanese_withholding_remain(text):
    assert not f._artifact_execution_claims(text)
    assert not internal_rating_claims(text)


@pytest.mark.parametrize('text', FRAMES)
def test_japanese_investment_role_is_not_a_report_owned_decision(text):
    assert internal_rating_claims(text)
    cleaned = remove_internal_ratings(text)
    assert '研究分析' in cleaned and not internal_rating_claims(cleaned)


def test_rating_parenthesis_is_one_claim_and_does_not_leave_a_signal_fragment():
    text = '**Rating:** BUY (buy; strong buy)\n\nNomura maintains Buy rating.'
    claims = internal_rating_claims(text)
    assert len(claims) == 1
    assert claims[0].text == text.splitlines()[0]
    cleaned = remove_internal_ratings(text)
    assert 'strong buy)' not in cleaned and 'Nomura maintains Buy rating.' in cleaned


def test_instrument_suffix_is_not_an_ordinal_when_relabelling_a_surface():
    assert remove_internal_ratings('## 1234.T 投資判断レポート') == '## 研究分析'


def test_plan_leadin_does_not_orphan_children_or_delete_independent_disclosure():
    text = '## 研究分析\n\n具体的アクションプラン：\n\n**1. 目標価格**\n\n価格変化を待つ。\n\n**重要開示事項**\n\n会社の設備投資は業務判断。'
    cleaned = f._prune_unapproved_execution(text)
    assert 'アクションプラン' not in cleaned and '価格変化を待つ' not in cleaned
    assert '重要開示事項' in cleaned and '会社の設備投資' in cleaned


def test_soft_plan_scope_cannot_delete_sibling_research_or_corporate_progress():
    text = '## 公司回购\n\n**截至本月实际执行情况**：\n- 公司回购已经完成。\n\n## 研究分析\n\n**1. 仓位计划**\n\n加仓至5%。\n\n**2. 监控条件**\n\n若订单落地则重新评估。\n\n### 风险因素\n\n客户集中风险较高。'
    cleaned = f._prune_unapproved_execution(text)
    assert '公司回购已经完成' in cleaned
    assert '若订单落地则重新评估' in cleaned and '客户集中风险较高' in cleaned
    assert '加仓' not in cleaned


def test_advice_leadin_keeps_monitoring_sibling_without_waiving_trade_body():
    text = '**行动建议**：\n\n**1. 仓位计划**\n加仓至5%。\n\n**2. 监控条件**\n若订单落地则重新评估。\n建议用期货对冲持仓。'
    cleaned = f._prune_unapproved_execution(text)
    assert '若订单落地则重新评估' in cleaned
    assert '加仓' not in cleaned and '期货对冲持仓' not in cleaned


def test_blocked_financial_premise_cannot_leave_its_dependent_causal_claim():
    from tradingagents.agents.evidence_enforcement import enforce_agent_output
    text = '**1. 业绩的加速是真实的运营杠杆。**\n\n业务需求是这一改善的驱动因素。\n\n**2. 公司经营**\n\n公司优化经营资源配置。'
    checked = enforce_agent_output(unavailable_actual(), text, 'Portfolio Manager')
    assert '这一改善' not in checked.text and '公司优化经营资源配置' in checked.text
    assert len([x for x in checked.findings if x.warning == 'critical_gate_bypassed']) == 2


def test_inline_financial_surface_scopes_japanese_reference_but_not_sibling_business():
    from tradingagents.agents.evidence_enforcement import enforce_agent_output
    text = '**業績の質的転換が明白**：利益率が急拡大。これは需要による改善の結果。\n\n**会社の事業**：生産拠点を分散する。'
    checked = enforce_agent_output(unavailable_actual(), text, 'Research Manager')
    assert 'これは需要' not in checked.text and '生産拠点を分散' in checked.text
    compose = f.compose_user_report_markdown
    with patch.object(f, 'compose_user_report_markdown', side_effect=lambda state:
                      compose(state) + '\n\n## 研究附录\n\n' + text):
        accepted = f.build_canonical_final_state(unavailable_actual())
    assert accepted['final_output_contract']['status'] == 'BLOCKED'


@pytest.mark.parametrize('text', ACTUALS)
def test_undated_realized_financial_change_requires_existing_actual_authority(text):
    assert current_financial_gate_violation(unavailable_actual(), text)
    accepted = f.build_canonical_final_state(unavailable_actual(text))
    assert text not in accepted['accepted_report_markdown']
    findings = [x for x in accepted['evidence_audit'] if x.get('warning') == 'critical_gate_bypassed']
    assert findings and all(x['resolution'] == 'CLAIM_REMOVED_OR_REPLACED' for x in findings)


@pytest.mark.parametrize('text', [
    '公司盈利能力具有结构性优势。', '盈利改善是看涨因素。',
    '分析师预计未来业绩可能加速。', '若未来业绩增长得到确认，则重新评估。',
    '2025年第三季度供应商数据表明利润率已经扩张。',
    'Nomura expects earnings growth.',
    'The company has earnings growth potential.',
    '公司盈利增长潜力已获认可。',
])
def test_business_quality_dated_history_and_forecasts_are_not_realized_current_actuals(text):
    assert not current_financial_gate_violation(unavailable_actual(), text)


@pytest.mark.parametrize('text', MACRO)
def test_event_odds_are_not_asset_direction_or_systemic_risk_witnesses(text):
    assert probability_event_gate_violation(macro_state(), text)


@pytest.mark.parametrize('text', [
    '| 日本2026年 | 4% | 低概率 |',
    '若未来日本衰退风险降低，股价可能受到正面影响。',
    '| 日本2026年衰退概率4% | 股价可能受到正面影响 |',
])
def test_event_probability_and_explicit_conditional_outcome_are_preserved(text):
    assert not probability_event_gate_violation(macro_state(), text)


@pytest.mark.parametrize('text', [*PLANS, *FRAMES, *ACTUALS, *MACRO, 'close_10_ema 与 boll_ub'])
def test_exact_contract_fault_injection_is_independent_of_upstream_findings(text):
    source = unavailable_actual()
    source['evidence_registry'] = macro_state()['evidence_registry']
    compose = f.compose_user_report_markdown
    with patch.object(f, 'compose_user_report_markdown', side_effect=lambda state:
                      compose(state) + '\n\n## 研究附录\n\n' + text + '\n\n公司具有成长潜力。'):
        accepted = f.build_canonical_final_state(source)
    assert accepted['final_output_contract']['status'] == 'BLOCKED'
    assert accepted['final_output_contract']['artifact_issues']


def test_schema_indicator_labels_are_displayed_without_changing_values_or_us_reports(tmp_path):
    text = 'close_10_ema: 8050；boll_ub: 9000；MACD、RSI、ATR。'
    displayed = f._publicize_inline_text(text)
    assert '10日EMA: 8050' in displayed and '布林上轨: 9000' in displayed
    assert 'MACD、RSI、ATR' in displayed
    write_report_tree({'market_report': text}, 'TEST', tmp_path)
    assert text in (tmp_path / 'complete_report.md').read_text()


def test_approved_sell_values_are_unchanged_by_precision_and_presentation():
    accepted = f.build_canonical_final_state(sell_state())
    validation = accepted['validated_execution']
    assert (validation['action'], validation['entry'], validation['stop'], validation['position_pct']) == ('Sell', 8513, 9500, 3)
    assert accepted['final_output_contract']['status'] == 'FINALIZED'


def test_legitimate_current_actual_is_not_suppressed():
    assert not current_financial_gate_violation(state_with('风险较高。'), '业绩的加速是真实的运营杠杆。')
