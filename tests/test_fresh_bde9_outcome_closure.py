"""Idempotent event replacement, actual proof, ownership and rendered closure."""
from unittest.mock import patch

import pytest

from tests.test_execution_zero_and_macro_rate import pce_state
from tests.test_jsf_position_semantic_closure import source_state
from tests.test_market_authority_closure import _state_with_tools
from tests.test_predicate_authority_closure import regime_state, unavailable_actual
from tests.test_rating_authority import state_with
from tradingagents import final_output as f
from tradingagents.agents.evidence_enforcement import (
    current_financial_gate_violation,
    enforce_agent_output,
    macro_observation_identity_violation,
    market_metric_identity_violation,
    probability_event_gate_violation,
)
from tradingagents.rating_authority import (
    internal_rating_claims,
    normalize_technical_outlook_labels,
)
from tradingagents.report_artifacts import render_markdown_fragment, validate_rendered_html


@pytest.mark.parametrize('row', [
    '| 宏观——衰退 | 美国2026年衰退概率 | 8% | 极低 |',
    '| 事件分类 | 美国2026年衰退 | 8% | 需求已获保障 |',
    '| 衰退 | 美国2026年衰退概率 | 8% | 软着陆 |',
])
def test_replacement_keeps_event_identity_and_is_idempotent(row):
    state = regime_state()
    state['evidence_registry'][-1]['derivation'] = {'numeric_tokens': ['2026', '8%']}
    state['evidence_registry'][-1].update(claim_type='FACT', allowed_for_current_decision=True)
    checked = enforce_agent_output(state, row, 'News Analyst')
    assert '美国2026年衰退' in checked.text and '8%' in checked.text
    assert not probability_event_gate_violation(state, checked.text)
    assert enforce_agent_output(state, checked.text, 'News Analyst').text == checked.text
    assert '需求已获保障' not in checked.text and '| 软着陆 |' not in checked.text


@pytest.mark.parametrize('text', [
    '公司新产能将有助于应对地缘政治风险、降低生产成本。',
    '多地生产可缓释经营风险，降低业务成本。',
    '供应链布局将有助于降低地缘政治风险。',
])
def test_business_risk_subject_and_future_modality_do_not_become_realized_macro(text):
    assert not probability_event_gate_violation(regime_state(), text)


def test_realized_geopolitical_state_still_requires_its_own_witness():
    assert probability_event_gate_violation(regime_state(), '地缘政治风险已经降低。')


@pytest.mark.parametrize('text', [
    '**1. 减持触发条件（满足后重新评估至Underweight）**',
    '若订单落地，则再评估至Buy。', 'Reassess the rating to Sell.',
    '公司基本面依然支持持仓逻辑。',
])
def test_future_rating_target_and_holding_stance_are_owned(text):
    assert internal_rating_claims(text)


def test_transition_does_not_invent_an_origin_from_an_action_heading():
    claim = internal_rating_claims('减持触发条件：重新评估至Underweight。')[0]
    assert claim.rating == 'Underweight' and claim.from_rating is None


@pytest.mark.parametrize('text', [
    'Nomura upgraded the stock from Hold to Buy.',
    '若订单落地，则重新评估。', '暂不加仓。', '公司质量良好。',
])
def test_external_ratings_monitoring_and_business_analysis_remain(text):
    assert not internal_rating_claims(text)


@pytest.mark.parametrize('text', [
    'Q3业绩是已验证事实，而非分析师预期。',
    'Q2盈利是已核验的事实，而非市场预期。',
    '短期风险不能颠覆公司基本面已经证明的盈利转机。',
    '这是公司基本面的硬证据。',
    '2026年Q3净利润为82.7。',
])
def test_actual_proof_cannot_borrow_a_negated_forecast_or_counterargument(text):
    assert current_financial_gate_violation(unavailable_actual(), text)


@pytest.mark.parametrize('text', [
    '若未来Q3盈利改善，则重新评估。', '历史FY2025 Q3盈利增长。', '2025年Q3盈利增长。',
    '当前无法仅凭这些证据确认盈利回升。', '公司具有长期成长潜力。',
])
def test_forecast_historical_actual_and_withholding_remain(text):
    assert not current_financial_gate_violation(unavailable_actual(), text)


@pytest.mark.parametrize('text', [
    '短期做空燃料已基本耗尽。', '贷株下降证明空头踩踏。',
])
def test_jsf_does_not_prove_short_participant_outcome(text):
    checked = enforce_agent_output(source_state(), text, 'Portfolio Manager')
    assert any(e.warning in {'short_absence_overclaim', 'short_pressure_overclaim'} for e in checked.findings)


@pytest.mark.parametrize('text', ['无法确认短期做空燃料已耗尽。', 'JSF余额不能证明空头踩踏。'])
def test_short_outcome_withholding_is_not_an_asserted_outcome(text):
    checked = enforce_agent_output(source_state(), text, 'Portfolio Manager')
    assert checked.text == text and not checked.findings


def test_reworded_short_outcome_cannot_close_the_same_jsf_semantic_class():
    state = f.build_canonical_final_state(source_state('短期做空燃料已耗尽。'))
    findings = [e for e in state['evidence_audit'] if e.get('warning') == 'short_absence_overclaim']
    assert findings
    candidate = state['accepted_report_markdown'] + '\n贷株数据证明空头踩踏。'
    closed, issues = f._finalize_audit(findings, state, accepted_report=candidate,
                                     execution_allowed=state['final_output_contract']['execution_allowed'])
    assert issues and all(e['resolution'] == 'UNRESOLVED' for e in closed)


def test_wrong_national_metric_cannot_borrow_a_value_from_another_source():
    state = pce_state()
    state['evidence_registry'].append({
        'source': 'get_macro_indicators', 'claim_type': 'FACT', 'verification_status': 'VERIFIED_TOOL_OUTPUT',
        'allowed_for_current_decision': True,
        'value': '## FRED: Unemployment Rate (UNRATE)\n- Units: %\n| 2026-09-01 | 4.2 |',
    })
    assert macro_observation_identity_violation(state, '| 美国非农就业 | 159,044千人 |')
    state['evidence_registry'][-1]['value'] = '## FRED: All Employees, Total Nonfarm (PAYEMS)\n- Units: Thousands of Persons\n| 2026-09-01 | 159044 |'
    assert not macro_observation_identity_violation(state, '| 美国非农就业 | 159,044千人 |')
    assert macro_observation_identity_violation(state, '| 美国非农就业 | 159,044% |')
    state['evidence_registry'] = [{'metric': 'PAYEMS', 'value': 159044, 'unit': '千人', 'data_date': '2026-09-01',
                                  'claim_type': 'FACT', 'allowed_for_current_decision': True, 'verification_status': 'VERIFIED_SOURCE'}]
    assert not macro_observation_identity_violation(state, '| 美国非农就业 | 159,044千人 |')


@pytest.mark.parametrize('text', ['| Metric | State |\n| --- | --- |\n| RSI | ** neutral zone** |', '** 明确风险 **', '** нейтральная зона**'])
def test_emphasis_spacing_repaired_before_shared_render(text):
    normalized = f.normalize_markdown_structure(text)
    assert not validate_rendered_html(render_markdown_fragment(normalized))
    assert normalized.replace('**', '').split() == text.replace('**', '').split()


def test_code_and_legitimate_tables_are_not_rewritten_as_prose():
    text = '```python\nx = "** literal **"\n```\n\n`** literal **`\n\n| Observation |\n| --- |\n| MACD weakening |'
    emphasis_only = ''.join(f._normalize_emphasis_spacing(text.splitlines(keepends=True)))
    assert 'x = "** literal **"' in emphasis_only and '`** literal **`' in emphasis_only
    normalized = f.normalize_markdown_structure(text)
    assert '`** literal **`' in normalized
    assert 'MACD weakening' in normalized


def test_neutral_market_outlook_is_not_a_formal_investment_label():
    assert normalize_technical_outlook_labels('**评级:** NEUTRAL') == '技术面展望：中性'
    assert normalize_technical_outlook_labels('投资评级：Bullish') == '投资评级：Bullish'


def test_tool_provenance_does_not_collapse_all_domain_authorities():
    state = unavailable_actual()
    checked = enforce_agent_output(state, '多方论点均有verified工具数据支撑。', 'Research Manager')
    assert 'collapsed_provenance_types' in checked.warnings
    assert '行情、新闻与财务信息具有不同来源' in checked.text
    assert 'Markdown' not in f._publicize_inline_text('### 八 Markdown 汇总表')
    heading = enforce_agent_output(state, '### 核心证据：多方论点均有verified工具数据支撑', 'Portfolio Manager')
    assert heading.text == '### 研究证据概述'


def test_bare_financial_field_does_not_prove_current_actual_gate():
    from tests.test_stage10_evidence_integrity import _financial_evidence, _jp_state
    state = _jp_state(evidence_registry=[_financial_evidence('revenue', 365221)])
    checked = enforce_agent_output(state, 'Latest Actual Q1 revenue: 365,221 百万円。', 'Fundamentals Analyst')
    assert 'critical_gate_bypassed' in checked.warnings


@pytest.mark.parametrize('before,after,blocked', [(-1, 1, False), (1, 2, True), (-2, -1, True)])
def test_dated_signal_cross_owns_both_observations_not_just_series_presence(before, after, blocked):
    state = _state_with_tools()
    current = f.canonical_market_authority(state)['latest_complete_ohlcv_date']
    for metric, earlier, latest in [('macd', before, after), ('macds', 0, 0)]:
        state['evidence_registry'].append({'source': 'get_indicators', 'verification_status': 'VERIFIED_TOOL_OUTPUT',
            'allowed_for_current_decision': True, 'domain': 'MARKET', 'claim_type': 'FACT',
            'derivation': {'market_data': {'indicator': metric, 'underlying_latest_complete_date': current}},
            'value': f'2026-09-24: {latest}\n2026-09-23: N/A: Not a trading day\n2026-09-18: {earlier}\n{current}: {latest}'})
    assert market_metric_identity_violation(state, 'MACDは9月24日にシグナル線をゴールデンクロスした。') is blocked
    assert not market_metric_identity_violation(state, '若未来MACD出现信号线金叉，则重新评估。')


@pytest.mark.parametrize('text,factory', [
    ('| 宏观 | 美国2026年衰退概率 | 8% | 软着陆 |', regime_state),
    ('Q3业绩已验证，而非分析师预期。', unavailable_actual),
    ('公司基本面已经证明盈利转机。', unavailable_actual),
    ('若条件成立，重新评估至Underweight。', state_with),
    ('短期做空燃料已耗尽。', source_state),
    ('| 美国非农就业 | 159044千人 |', pce_state),
    ('全部证据均有verified工具数据支撑。', unavailable_actual),
    ('** unclosed emphasis', state_with),
])
def test_final_contract_blocks_post_cleanup_fault_injection(text, factory):
    compose = f.compose_user_report_markdown
    with patch.object(f, 'compose_user_report_markdown', side_effect=lambda s: compose(s) + '\n\n## 研究附录\n' + text):
        accepted = f.build_canonical_final_state(factory('风险较高。') if factory is state_with else factory())
    contract = accepted['final_output_contract']
    assert contract['status'] == 'BLOCKED' and contract['artifact_issues']
    if 'unclosed' in text:
        assert not contract['validation_dimensions']['presentation_valid']
