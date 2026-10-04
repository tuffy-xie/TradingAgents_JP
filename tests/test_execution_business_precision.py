"""Business operations are not investor execution; gates still own trades."""
from unittest.mock import patch

import pytest

from tests.test_predicate_authority_closure import regime_state
from tests.test_rating_authority import state_with
from tradingagents import final_output as f
from tradingagents.agents.evidence_enforcement import (
    enforce_agent_output,
    probability_event_gate_violation,
)


@pytest.mark.parametrize('text', [
    '公司通过多地生产基地对冲供应链集中风险。',
    '村田在东南亚（泰国、菲律宾、越南、中国无锡）均有生产基地，可对冲部分地缘集中风险',
    '海外收入与成本形成自然汇率对冲。',
    'The production network can hedge supply-chain concentration risk.',
    '### 股票回购计划执行中', '### 回购执行进度达到40%',
    '### 资本开支计划执行中', '### 订单执行情况正常',
    '### 并购整合计划执行', '经营资源配置优化。',
    '### Company order execution', '### Share buyback execution progress',
    '基本面强到不支持卖出，估值和宏观又强到不支持加仓。',
    '基本面强→不支持卖出（否决Sell）',
])
def test_business_and_withholding_are_not_execution(text):
    assert not f._execution_violation(text)
    assert not f._is_execution_heading(text.lstrip('# '))
    report = text + ('\n\n公司经营状况说明。' if text.startswith('#') else '')
    assert text in f._prune_unapproved_execution(report)


@pytest.mark.parametrize('text', [
    '可考虑用SOX指数或日经225期货做部分对冲。', '支持逢低配置。',
    '保持现有仓位，等待更好的入场或加仓时机。', '分层加仓。',
    '**Stop Loss**: 7200', '**Maximum Position**: 20%',
    '公司自然对冲汇率风险，但投资者可用期货对冲持仓。',
    '公司配置生产设备，但建议配置该股票。',
    '执行止损。', '执行买入。', '建议减仓。', '建议止盈。',
    '## 用户交易执行', '## 分层建仓计划', '## Order execution', '## 股票回购与交易计划',
    '可考虑对冲汇率风险。', '公司有海外业务，建议用ETF对冲。',
])
def test_portfolio_actions_still_require_validator(text):
    assert f._execution_violation(text) or f._is_execution_heading(text.lstrip('# '))
    assert not f._artifact_execution_claims(f._prune_unapproved_execution(text))


def test_business_fact_survives_canonical_acceptance_and_exact_check():
    text = '公司通过多地生产基地对冲供应链集中风险。'
    accepted = f.build_canonical_final_state(state_with(text, 'news_report'))
    assert text in accepted['accepted_report_markdown']
    assert accepted['final_output_contract']['status'] == 'FINALIZED'
    assert not [x for x in accepted['evidence_audit'] if x.get('category') == 'UNAPPROVED_EXECUTION_CLAIM']


def test_corporate_heading_does_not_exempt_subordinate_portfolio_plan():
    text = '## 股票回购计划执行中\n\n公司经营正常。\n\n可考虑用期货对冲持仓。'
    pruned = f._prune_unapproved_execution(text)
    assert '股票回购计划执行中' in pruned and '公司经营正常' in pruned
    assert '期货' not in pruned


def test_macro_pricing_exact_artifact_bypass_blocked():
    compose = f.compose_user_report_markdown
    text = '市场已将美联储按兵不动充分定价。'
    with patch.object(f, 'compose_user_report_markdown', side_effect=lambda state: compose(state) + '\n\n## 研究附录\n\n' + text):
        accepted = f.build_canonical_final_state(regime_state())
    assert accepted['final_output_contract']['status'] == 'BLOCKED'
    assert not accepted['final_output_contract']['validation_dimensions']['domain_authority_consistent']


def test_replacement_dedup_does_not_cross_heading_or_change_regular_repetitions():
    from tradingagents.agents.evidence_enforcement import _collapse_adjacent_replacements
    checked = enforce_agent_output(regime_state(), '美国经济未陷入衰退。', 'News Analyst')
    notice = checked.text
    text = notice + '\n\n## 第二节\n\n' + notice + '\n公司增长。\n公司增长。'
    assert _collapse_adjacent_replacements(text, list(checked.findings)) == text


def test_replacement_retains_list_boundary_and_dedupes_only_generated_notices():
    text = '- 美股软着陆 → 中性偏好\n- 美国经济未陷入衰退。\n- 日本经济情况需要观察\n'
    checked = enforce_agent_output(regime_state(), text, 'News Analyst')
    assert len(checked.findings) == 2
    assert checked.text.count('该宏观情景或共识尚无法确认。') == 1
    assert checked.text.startswith('- 该宏观情景或共识尚无法确认。')
    assert '\n- 日本经济情况需要观察\n' in checked.text


def test_exact_artifact_still_blocks_real_hedge_after_upstream_bypass():
    compose = f.compose_user_report_markdown
    with patch.object(f, 'compose_user_report_markdown', side_effect=lambda state: compose(state) + '\n\n## 研究附录\n\n可用指数期货对冲持仓。'):
        accepted = f.build_canonical_final_state(state_with('公司具有成长潜力。'))
    assert accepted['final_output_contract']['status'] == 'BLOCKED'
    assert not accepted['final_output_contract']['validation_dimensions']['execution_consistent']


def test_distinct_regime_claims_keep_lineage_but_share_one_adjacent_replacement():
    checked = enforce_agent_output(regime_state(), '**美股软着陆 → 中性偏好**\n美国经济未陷入衰退。', 'News Analyst')
    assert len(checked.findings) == 2
    assert len({x.claim_sha256 for x in checked.findings}) == 2
    assert checked.text.count('该宏观情景或共识尚无法确认。') == 1
    source = regime_state()
    source['news_report'] = '**美股软着陆 → 中性偏好**\n美国经济未陷入衰退。'
    accepted = f.build_canonical_final_state(source)
    findings = [x for x in accepted['evidence_audit'] if x.get('agent') == 'News Analyst'
                and x.get('warning') == 'probability_event_mismatch']
    assert len(findings) == 2 and len({x['claim_sha256'] for x in findings}) == 2
    assert all(x['resolution'] == 'CLAIM_REMOVED_OR_REPLACED' for x in findings)
    assert all(x['accepted_artifact_sha256'] == accepted['final_output_contract']['accepted_report_sha256'] for x in findings)


@pytest.mark.parametrize('text', [
    '市场已将美联储按兵不动充分定价，但未对长期高利率充分重新定价。',
    'Markets have fully priced in the Fed holding rates.',
])
def test_prediction_odds_do_not_prove_asset_market_pricing(text):
    state = regime_state()
    assert probability_event_gate_violation(state, text)
    assert text not in enforce_agent_output(state, text, 'News Analyst').text
    state['evidence_registry'].append({'source': 'get_news', 'source_type': 'TOOL_OUTPUT',
                                     'verification_status': 'VERIFIED_TOOL_OUTPUT', 'value': text})
    assert not probability_event_gate_violation(state, text)


@pytest.mark.parametrize('text', ['市场可能已经定价，仍需观察。', '若市场充分定价，则重新评估。',
                                '公司定价能力改善。', 'Market pricing may change.'])
def test_hypothetical_pricing_and_company_pricing_preserved(text):
    assert not probability_event_gate_violation(regime_state(), text)
