"""Event odds do not establish issuer demand or observed monetary conditions."""
import hashlib
from unittest.mock import patch

import pytest

from tests.test_predicate_authority_closure import regime_state
from tests.test_rating_authority import state_with
from tradingagents import final_output as f
from tradingagents.agents.evidence_enforcement import (
    enforce_agent_output,
    probability_event_gate_violation,
)
from tradingagents.rating_authority import internal_rating_claims

CLAIMS = [
    '日本经济衰退概率极低（4%），对村田的本土需求提供基本保障。',
    '美国衰退概率短期下降对电子产品出口需求属正面。',
    '日元套利交易尾部风险阶段性降低。',
    '| 日央行年內加息概率下降 | 正面（短期） | 金融條件維持寬鬆 |',
    '| 日本2026年衰退概率極低 | 正面 | 本土需求基本面相對穩健 |',
    'Low recession odds guarantee the company domestic demand.',
    'Financial conditions remain loose.',
    'Yen carry trade tail risk has decreased.',
    'BOJ annual rate hike probability declined.',
    '市場認為10月按兵不動概率大增，日央行年內快速收緊貨幣政策的預期降溫。',
]


def macro_state(text='风险较高。'):
    state = regime_state(text)
    state['evidence_registry'].append({
        'evidence_id': 'E-events', 'claim_type': 'FACT', 'domain': 'NEWS',
        'source': 'get_prediction_markets', 'verification_status': 'VERIFIED_TOOL_OUTPUT',
        'allowed_for_current_decision': True,
        'derivation': {'numeric_tokens': ['10']},
        'value': '- **Japan recession in 2026?** — Yes 4%\n'
                 '- **Bank of Japan increases interest rates by 25 bps after the October 2026 meeting?** — Yes 14% (1-week -8.0pp)\n'
                 '- **No change in Bank of Japan interest rates after the October 2026 meeting?** — Yes 84% (1-week +9.0pp)\n'
                 '- **Bank of Japan increases interest rates by 50+ bps after the October 2026 meeting?** — Yes 1%',
    })
    return state


@pytest.mark.parametrize('text', CLAIMS)
def test_unqualified_macro_outcome_needs_its_own_evidence(text):
    state = macro_state(text)
    assert probability_event_gate_violation(state, text)
    result = enforce_agent_output(state, text, 'News Analyst')
    assert text not in result.text
    assert any(x.warning == 'probability_event_mismatch' for x in result.findings)


@pytest.mark.parametrize('text', [
    '日本2026年衰退概率为4%。',
    '若美国衰退风险下降，可能有利于公司出口需求。',
    '低衰退概率可能对本土需求有利，但不能保证公司订单。',
    '如果BOJ维持政策不变，金融条件可能保持宽松。',
    'Financial conditions could remain loose if the policy path is unchanged.',
    '若日元套利交易尾部风险降低，则重新评估。',
    '公司生产网络可对冲供应链集中风险。',
    '回购计划执行中。', '公司经营资源配置优化。',
    '分析师一致预期上调。', '海外收入与成本形成自然汇率对冲。',
    '若未来 MACD 跌破零轴，则重新评估。',
    'Cannot confirm that financial conditions remain loose.',
])
def test_probabilities_hypotheses_and_business_facts_remain(text):
    assert not probability_event_gate_violation(macro_state(), text)


@pytest.mark.parametrize('text', CLAIMS)
def test_exact_contract_blocks_post_pruning_macro_injection(text):
    compose = f.compose_user_report_markdown
    with patch.object(f, 'compose_user_report_markdown', side_effect=lambda s: compose(s) + '\n\n## 新闻附录\n\n' + text):
        state = f.build_canonical_final_state(macro_state())
    c = state['final_output_contract']
    assert c['status'] == 'BLOCKED'
    assert not c['validation_dimensions']['domain_authority_consistent']
    assert any(x.startswith('CROSS_DOMAIN_AUTHORITY:NEWS_PROBABILITY:') for x in c['artifact_issues'])


def test_independent_verified_assertion_can_support_the_actual_outcome():
    text = '官方报告指出日本金融条件维持宽松。'
    state = macro_state()
    state['evidence_registry'].append({'evidence_id': 'E-independent', 'source': 'get_global_news',
        'verification_status': 'VERIFIED_TOOL_OUTPUT', 'allowed_for_current_decision': True, 'value': text})
    assert not probability_event_gate_violation(state, text)
    state['evidence_registry'][-1]['allowed_for_current_decision'] = False
    assert probability_event_gate_violation(state, text)


def test_prediction_question_and_analyst_inference_are_not_independent_witnesses():
    text = '日元套利交易尾部风险阶段性降低。'
    state = macro_state()
    for source, verification in [('get_prediction_markets', 'VERIFIED_TOOL_OUTPUT'), ('News Analyst', 'ANALYST_INFERENCE')]:
        state['evidence_registry'].append({'source': source, 'verification_status': verification, 'value': text})
    assert probability_event_gate_violation(state, text)


def test_unscoped_state_cannot_borrow_another_country_or_historical_statement():
    state = macro_state()
    state['evidence_registry'].append({'source': 'get_global_news',
        'verification_status': 'VERIFIED_TOOL_OUTPUT', 'value': '美国去年金融条件维持宽松。'})
    assert probability_event_gate_violation(state, '金融条件维持宽松。')
    assert probability_event_gate_violation(state, '日本金融条件维持宽松。')


def test_modal_sibling_does_not_waive_asserted_monetary_state():
    text = '公司可能改善盈利，但金融条件维持宽松。'
    assert probability_event_gate_violation(macro_state(), text)


@pytest.mark.parametrize('text', [
    '## 核心结论与交易建议\n公司有成长潜力。',
    '### 六、核心結論與交易建議\n盈利预期改善。',
    '## Investment recommendations\nCompany quality is strong.',
])
def test_nonportfolio_recommendation_surface_is_relabelled_not_body_deleted(text):
    claims = internal_rating_claims(text)
    assert any(x.semantic_type == 'RECOMMENDATION_SURFACE' for x in claims)
    state = f.build_canonical_final_state(state_with(text))
    report = state['accepted_report_markdown']
    assert '交易建议' not in report and '交易建議' not in report
    assert text.splitlines()[-1] in report
    findings = [x for x in state['evidence_audit'] if x.get('recommendation_semantics') == 'RECOMMENDATION_SURFACE']
    assert findings and all(x['resolution'] == 'CLAIM_REMOVED_OR_REPLACED' for x in findings)
    assert all(hashlib.sha256(x['original_claim'].encode()).hexdigest() == x['claim_sha256'] for x in findings)


@pytest.mark.parametrize('text', [
    '## 券商投资建议\nNomura maintains Buy.',
    '## 交易建议的限制\n当前不提供交易建议。',
    '## 经营建议\n公司应优化生产资源。',
])
def test_external_educational_and_business_headings_are_not_owned_advice(text):
    assert not internal_rating_claims(text)


def test_exact_contract_blocks_unowned_advice_heading_without_rating_word():
    compose = f.compose_user_report_markdown
    with patch.object(f, 'compose_user_report_markdown', side_effect=lambda s: compose(s) + '\n\n## 新闻附录\n\n### 核心结论与交易建议\n公司有成长潜力。'):
        state = f.build_canonical_final_state(state_with('风险较高。'))
    assert state['final_output_contract']['status'] == 'BLOCKED'


def test_macro_audit_cannot_close_surviving_presentation_transform():
    text = CLAIMS[0]
    state = f.build_canonical_final_state(macro_state(text))
    findings = [x for x in state['evidence_audit'] if x.get('warning') == 'probability_event_mismatch']
    assert findings and all(x['resolution'] == 'CLAIM_REMOVED_OR_REPLACED' for x in findings)
    artifact = state['accepted_report_markdown'] + '\n\n## 新闻附录\n' + '**' + text + '**'
    audit, issues = f._finalize_audit(findings, state, accepted_report=artifact, execution_allowed=False)
    assert issues and any(x['resolution'] == 'UNRESOLVED' for x in audit)


def test_bound_event_rows_keep_equivalent_decimal_change_notation():
    text = '| 日本央行2026年10月会议加息25bps | 14%（本周 -8pp） |'
    assert enforce_agent_output(macro_state(), text, 'News Analyst').text == text


def test_inline_replacements_dedup_without_deleting_independent_fact():
    text = CLAIMS[0] + CLAIMS[1] + '\n\n日本2026年衰退概率为4%。'
    result = enforce_agent_output(macro_state(), text, 'News Analyst')
    assert result.text.count('事件概率不直接证明') == 1
    assert '日本2026年衰退概率为4%' in result.text
    assert len(result.findings) == 2


def test_direct_policy_odds_change_keeps_source_event_scope():
    valid = '日本央行2026年10月加息25bps概率本周下降8pp。'
    assert not probability_event_gate_violation(macro_state(), valid)
    for invalid in [valid.replace('10月', '年内'), valid.replace('25bps', '50bps'),
                    valid.replace('2026', '2027'), valid.replace('8pp', '9pp'),
                    valid.replace('下降', '上升')]:
        assert probability_event_gate_violation(macro_state(), invalid)


def test_odds_binding_does_not_support_sibling_realized_condition():
    text = '日本央行2026年10月加息25bps概率本周下降8pp，金融条件维持宽松。'
    assert probability_event_gate_violation(macro_state(), text)
