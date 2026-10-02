"""Prediction odds are not realized regimes; News inference is not Market data."""
from unittest.mock import patch

import pytest

from tests.test_predicate_authority_closure import regime_state
from tradingagents import final_output as f
from tradingagents.agents.utils.evidence_enforcement import probability_event_gate_violation
from tradingagents.agents.utils.market_claims import current_market_claims


@pytest.mark.parametrize('text', [
    '**美股软着陆 → 中性偏好**', 'Soft landing -> stronger risk appetite',
    '美国经济未陷入衰退（衰退概率仅8%），需求保持健康。',
    'US has avoided recession (recession probability 8%).',
])
def test_prediction_odds_do_not_prove_realized_regime_or_regime_premise(text):
    assert probability_event_gate_violation(regime_state(), text)


@pytest.mark.parametrize('text', [
    '美国衰退概率为8%。', '若软着陆，则消费需求可能改善。',
    'Soft landing may support future demand.', 'If recession is avoided, reassess.',
])
def test_bound_probability_and_hypothetical_regime_remain_legal(text):
    assert not probability_event_gate_violation(regime_state(), text)


@pytest.mark.parametrize('text', [
    '| 分析师 | EPS上修 | 机构资金流入驱动 |',
    'Institutional capital inflows are driving the move.',
])
def test_news_asserted_investor_flows_require_current_market_authority(text):
    assert current_market_claims(text, analysis_as_of='2026-10-03')


@pytest.mark.parametrize('text', [
    '盈利预期上修有望吸引机构资金流入。', '若未来机构资金流入增强，则重新评估。',
    '公司将资金配置到研发设备。', 'Capital inflows may support a future recovery.',
])
def test_possible_flows_business_allocation_and_monitoring_are_not_current_facts(text):
    assert not current_market_claims(text, analysis_as_of='2026-10-03')


@pytest.mark.parametrize('text', [
    '**美股软着陆 → 中性偏好**', '| 分析师 | EPS上修 | 机构资金流入驱动 |',
])
def test_exact_artifact_independently_blocks_unproven_regime_or_flow_premise(text):
    source = regime_state()
    source['evidence_registry'] = [entry for entry in source['evidence_registry'] if entry.get('domain') != 'MARKET']
    compose = f.compose_user_report_markdown
    with patch.object(f, 'compose_user_report_markdown', side_effect=lambda state: compose(state) + '\n\n## 附录\n\n' + text):
        accepted = f.build_canonical_final_state(source)
    assert accepted['final_output_contract']['status'] == 'BLOCKED'
    assert not accepted['final_output_contract']['validation_dimensions']['domain_authority_consistent']
