"""Fresh production strategy prose obeys execution ownership after formatting."""
from unittest.mock import patch

import pytest

from tests.test_published_authority_ownership import sell_state
from tests.test_rating_authority import state_with
from tradingagents import final_output as f
from tradingagents.report_artifacts import render_markdown_fragment, validate_rendered_html


@pytest.mark.parametrize('text', [
    '长期需求改善，支持逢低配置。', '需求改善支持配置该股票。', '战略上支持配置该股。', '回调是买入良机。',
    '可考虑用指数期货做部分对冲。', '短期策略：高抛低吸。',
    '短期（1-3个月）：震荡格局，高抛低吸。',
    '最终决策：Hold，附条件性分层加仓框架。',
    '维持现状并等待更优入场点（Hold+分层建仓）。',
    '## 综合框架：Hold + 分层加仓\n\n公司具有成长潜力。',
    'This is an opportunity to buy the stock.', 'Consider hedging with index futures.',
    '| 情景 | 响应 |\n| --- | --- |\n| 风险上升 | 分批配置 |',
])
def test_strategy_propositions_are_execution_not_blanket_permission(text):
    assert f._execution_violation(text)
    for source in [state_with(text, 'news_report'), sell_state(text)]:
        source['evidence_registry'].append({
            'source': 'get_news', 'domain': 'NEWS', 'source_type': 'TOOL_OUTPUT',
            'verification_status': 'VERIFIED_TOOL_OUTPUT', 'value': 'Source research window: 1-3 months',
            'claim_type': 'FACT', 'allowed_for_current_decision': True,
        })
        accepted = f.build_canonical_final_state(source)
        contract = accepted['final_output_contract']
        assert contract['status'] == 'FINALIZED', contract['artifact_issues']
        artifact = accepted['accepted_report_markdown']
        unauthorized = f._artifact_without_validated_execution(accepted, artifact) if contract['execution_allowed'] else artifact
        assert not f._artifact_execution_claims(unauthorized)
        findings = [x for x in accepted['evidence_audit'] if x.get('category') == 'UNAPPROVED_EXECUTION_CLAIM']
        assert findings and all(x['resolution'] == 'CLAIM_REMOVED_OR_REPLACED' for x in findings)


@pytest.mark.parametrize('text', [
    '不建议逢低配置。', '目前不存在买入机会。', '买入机会尚未确认。',
    '历史上投资者高抛低吸。', '历史上投资者高卖低买。', '不采用高抛低吸。', '公司计划配置生产设备。', '研发资源配置改善效率。',
    '盈利改善支持公司配置生产设备。',
    '公司具有良好的成长潜力。', '配置比例变化是需要监控的风险。',
    '公司曾对冲外汇风险。', '若未来订单改善，则重新评估。',
    'The company plans hedging currency risk.',
    'Nomura recommends Buy.', '分析师一致预期为 Buy。',
])
def test_strategy_subject_polarity_history_and_research_preserved(text):
    assert not f._execution_violation(text)


@pytest.mark.parametrize('text', [
    '支持逢低配置。', '回调是买入良机。', '可考虑对冲。',
    '## 动态Hold + 分层加仓',
])
def test_exact_artifact_blocks_strategy_when_upstream_missed(text):
    compose = f.compose_user_report_markdown
    with patch.object(f, 'compose_user_report_markdown', side_effect=lambda state: compose(state) + '\n\n## 附录\n\n' + text):
        accepted = f.build_canonical_final_state(sell_state())
    contract = accepted['final_output_contract']
    assert contract['status'] == 'BLOCKED'
    assert not contract['validation_dimensions']['execution_consistent']


def test_generic_engineering_assignments_are_not_a_second_authority():
    text = '行情证据不可用（latest_complete=None）；MACD、RSI、ATR 保留。'
    accepted = f.build_canonical_final_state(state_with(text, 'final_trade_decision'))
    artifact = accepted['accepted_report_markdown']
    assert 'latest_complete' not in artifact and '（）' not in artifact
    assert '行情证据不可用' in artifact
    assert not f._validate_final_artifact(accepted, False, accepted_report=artifact)


def test_declared_markdown_fences_render_report_tables_not_literal_pipe_rows():
    text = '## 汇总\n```markdown\n| Observation | Status |\n| --- | --- |\n| MACD | Not provided |\n```'
    normalized = f.normalize_markdown_structure(text)
    html = render_markdown_fragment(normalized)
    assert '<table>' in html and '<pre>' not in html
    assert not validate_rendered_html(html)
    assert 'Not provided' in normalized
    assert f.normalize_markdown_structure(normalized) == normalized


def test_non_markdown_code_fence_remains_inert():
    text = '```python\nprint("literal")\n```'
    assert f.normalize_markdown_structure(text) == text


def test_nested_markdown_example_in_code_is_not_promoted_to_report_content():
    text = '````python\n```markdown\n| Example |\n| --- |\n| literal |\n```\n````'
    normalized = f.normalize_markdown_structure(text)
    assert '```markdown' in normalized
    assert '<table>' not in render_markdown_fragment(normalized)


def test_unclosed_markdown_fence_does_not_gain_structural_acceptance():
    text = '```markdown\n| Observation |\n| --- |\n| literal |'
    assert f.normalize_markdown_structure(text).startswith('```markdown')


def test_execution_pruning_folds_empty_emphasized_list_labels_and_renumbers_siblings():
    text = '**1. 仓位管理**\n\n可考虑对冲。\n\n**2. 监控条件**\n\n若未来订单改善则重新评估。\n\n**3. 风险因素**\n\n盈利风险较高。'
    normalized = f._prune_unapproved_execution(text)
    assert '仓位管理' not in normalized
    assert '**1. 监控条件**' in normalized and '**2. 风险因素**' in normalized
    assert f.normalize_markdown_structure(normalized) == normalized


def test_strategy_title_pruning_does_not_orphan_its_conditional_trigger_children():
    text = '## Hold + 分层加仓框架\n\n触发条件：\n- 股价深度回调并且利好兑现\n\n## 公司质量\n\n公司具有成长潜力。'
    normalized = f._prune_unapproved_execution(text)
    assert '触发条件' not in normalized and '股价深度回调' not in normalized
    assert '公司具有成长潜力' in normalized


def test_surviving_strategy_after_presentation_change_cannot_close_audit():
    original = '支持逢低配置。'
    accepted = f.build_canonical_final_state(sell_state(original))
    findings = [x for x in accepted['evidence_audit'] if x.get('category') == 'UNAPPROVED_EXECUTION_CLAIM']
    candidate = accepted['accepted_report_markdown'] + '\n\n## 附录\n\n**支持逢低配置**。'
    audit, issues = f._finalize_audit(findings, accepted, accepted_report=candidate, execution_allowed=True)
    assert issues and any(x['resolution'] == 'UNRESOLVED' for x in audit)
