"""Fresh Research directives retain ownership through prose and script variants."""
from unittest.mock import patch

import pytest

from tests.test_predicate_authority_closure import regime_state
from tests.test_rating_authority import state_with
from tradingagents import final_output as f
from tradingagents.agents.utils.evidence_enforcement import (
    enforce_agent_output,
    probability_event_gate_violation,
)


@pytest.mark.parametrize('claim', [
    '現有倉位維持不變，不做主動加減。',
    '现有仓位维持不变，不主动买卖。',
    '維持現有倉位。', '保持已有持仓。',
    'Current exposure should be kept unchanged.',
    'Keep the existing position unchanged.',
    'Maintain current exposure and wait for better entry conditions.',
    'Existing holders should hold.',
])
def test_position_preservation_is_still_an_execution_plan(claim):
    assert f._execution_violation(claim)
    accepted = f.build_canonical_final_state(state_with(claim, 'investment_debate_state.judge_decision'))
    assert not f._artifact_execution_claims(accepted['accepted_report_markdown'])
    findings = [x for x in accepted['evidence_audit'] if x.get('category') == 'UNAPPROVED_EXECUTION_CLAIM']
    assert findings and all(x['resolution'] == 'CLAIM_REMOVED_OR_REPLACED' for x in findings)


@pytest.mark.parametrize('claim', [
    '暫不加倉。', '不建議維持倉位。', 'Do not maintain the position.',
    '去年维持现有仓位。', 'Last year we maintained the position.',
    '公司經營資源配置維持不變。', '维持观望。',
    '公司保持子公司股份持仓。', 'The company maintained its competitive position.',
    'The company should maintain its competitive position.',
])
def test_withholding_history_and_business_remain_non_authorizing(claim):
    assert not f._execution_violation(claim)


def test_exact_artifact_position_maintenance_bypass_blocks():
    compose = f.compose_user_report_markdown
    with patch.object(f, 'compose_user_report_markdown', side_effect=lambda s: compose(s) + '\n\n## 研究附录\n\n現有倉位維持不變，不做主動加減。'):
        accepted = f.build_canonical_final_state(state_with('公司具有成长潜力。'))
    assert accepted['final_output_contract']['status'] == 'BLOCKED'
    assert not accepted['final_output_contract']['validation_dimensions']['execution_consistent']


def test_inline_dot_list_left_by_pruning_is_normalized_and_validated():
    raw = '**策略说明**: 1. 持续观察经营指标。4. 重新评估新披露。'
    assert 'INLINE_NUMBERING_DISCONTINUITY' in f._markdown_structure_issues(raw)
    normalized = f.normalize_markdown_structure(raw)
    assert '\n1. 持续观察经营指标。\n2. 重新评估新披露。' in normalized
    assert not f._markdown_structure_issues(normalized)
    single = f.normalize_markdown_structure('**Strategic Actions**: 4. Monitor cash flow.')
    assert '\n1. Monitor cash flow.' in single


def test_ordinary_numbers_and_quotes_are_not_inline_lists():
    text = 'EPS为1.5元，PE为4.2倍。\n> 管理层称：4. Monitor cash flow.'
    assert f.normalize_markdown_structure(text) == text


def test_prediction_odds_do_not_confirm_an_observed_regime_change():
    claim = '市場對日本通膨粘性的判斷大幅強化，反映出日本告別多年通縮的結構性轉變。'
    state = regime_state()
    assert probability_event_gate_violation(state, claim)
    checked = enforce_agent_output(state, claim, 'News Analyst')
    assert claim not in checked.text
    assert any(x.warning == 'probability_event_mismatch' for x in checked.findings)
    compose = f.compose_user_report_markdown
    with patch.object(f, 'compose_user_report_markdown', side_effect=lambda s: compose(s) + '\n\n## 新闻附录\n\n' + claim):
        accepted = f.build_canonical_final_state(state)
    assert accepted['final_output_contract']['status'] == 'BLOCKED'
    assert not accepted['final_output_contract']['validation_dimensions']['domain_authority_consistent']


@pytest.mark.parametrize('text', [
    '日本过去长期处于通缩。', '若日本未来摆脱通缩，则重新评估。',
    '公司改革反映出经营结构的转变。',
])
def test_business_history_and_hypotheses_are_not_regime_confirmations(text):
    assert not probability_event_gate_violation(regime_state(), text)


def test_verified_attribution_can_support_a_regime_confirmation():
    text = '官方报道确认日本已经结束通缩。'
    state = regime_state()
    state['evidence_registry'].append({
        'source': 'get_global_news', 'verification_status': 'VERIFIED_TOOL_OUTPUT', 'value': text,
    })
    assert not probability_event_gate_violation(state, text)


def test_long_domain_label_localizes_without_touching_proper_names():
    text = f._publicize_inline_text('Canonical Market authority UNAVAILABLE。公司名：Canonical Ltd。')
    assert text == '正式行情证据 不可用。公司名：Canonical Ltd。'
