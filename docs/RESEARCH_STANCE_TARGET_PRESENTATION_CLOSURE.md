# Research stance, scenario target and presentation closure

## Observed evidence

Independent review of the immutable 4f21e1f user artifacts identified:
- 69760305 Research: a fundamental/valuation rationale for rejecting Sell/add, and a bold position-management surface.
- 0f29d2b8 Research: opposed chasing/add, a status-quo conclusion, a self-owned recommendation disclaimer, and a company-prefixed investment-advice heading.
- 82da370c Portfolio: scenario-B target 8500 while canonical execution is unavailable.
- Several reports: internal enforcement wrappers and descriptions of deleting/rebinding probability claims.

The source state and the 4f21e1f / 336e792 report exports are not modified.

## Proved causes

1. Recommendation ownership was narrower than execution polarity. The execution classifier correctly treats a bare prohibition as non-authorizing, but the rating classifier did not separately recognize evidence-to-trade-stance relations or status-quo advice in an investment conclusion. It also matched advice headings as whole short titles, not prefixed titles or bold numbered surfaces.
2. Price-plan detection relied on a target-price label or English target. Chinese scenario target omitted the optional price suffix. Portfolio final prose was not included in execution claim collection, although it was pruned at publication.
3. Typed deterministic enforcement templates emitted technical wrappers and a cleanup narrative themselves. Assistant-process detection covered report-generation intent, not claim-removal/rebinding intent. Later display conversion did not remove these emissions.
4. Surface renaming must preserve pruning order. A renamed trading-advice parent must not restore a previously withheld execution subtree. Original claim identities are collected first; when the original field contains a plan, the existing execution pruner runs on its original structure before remaining advisory surfaces are relabelled. Fact-only advisory headings remain research text, as the existing preservation contract requires.

## Minimal implementation within existing authorities

- SECONDARY_INTERNAL_RATING also represents implicit evaluative investment stance. Evidence/causal rationale + rejected trade action is distinct from pure withholding; contextual status-quo advice and self-owned recommendation discourse use the same owner, Portfolio Manager.
- Advice suffixes and bold management lead-ins are relabelled through the existing helper. Ordinary business facts and bullish/bearish factors are not recommendations.
- Scenario + numeric price target is a plan parameter, with business-goal units/objects, local prohibition/history and explicit external valuation attribution kept distinct.
- final_trade_decision joins existing claim collection so unapproved Portfolio proposal parameters have identity too. Only the exact validator-owned execution block is exempt in final scanning.
- Existing process/presentation detection now covers typed enforcement wrappers and claim-object + cleanup-operation narratives. User-facing replacements express natural uncertainty or source distinctions; technical actions stay in Audit/full_agent_log.
- Sentence pruning retains the opening delimiter of an enclosing emphasis span when independent sibling prose remains. Shared normalization does not strip US footnote markers, ordinary italic text, bullets or arithmetic.

No second authority/report pipeline, no Agent topology change, no changes to freshness/Financial/JSF/Sentiment rules, no restored custom horizon.

Semantic revision: reasoned-stance-scenario-target-presentation-2026-10.

## Tests and independent fault injection

Tests cover reasoned trade stance vs pure withholding; company-prefixed/bold advisory surfaces; external broker and consensus attribution; history and corporate action; scenario targets vs corporate metric goals; external target columns vs added entry/order plans; internal cleanup vs natural uncertainty/management quotations; old execution-subtree preservation; emphasis/numbering; canonical Sell 8513/9500/3%; US report writer.

Exact-candidate injection occurs after every upstream cleanup. Non-Portfolio stance, an unapproved scenario target/position surface, and internal cleanup prose independently BLOCK the Final Contract. Historical cross-cell bypass tests now inject after composition too, rather than bypassing only a single named pruning function.

Old tests asserting engineering replacement wording now assert the natural source distinction and the unchanged enforcement warning, not internal template syntax.

## Validation and handoff

Six immutable production states: 969ce71b, 69760305, ca4e6ddb, 8dd4eec0, 0f29d2b8, 82da370c.
Use scripts/replay_saved_states.py with its network/LLM deny guard. Final clean-HEAD artifacts and manual review record are exported together with source identities and exact SHA manifest.

Final validation: new targeted module 84 passed; authority aggregate 706 passed; full offline repository suite 2588 passed, 1 integration test deselected, 91 subtests passed. Ruff, diff-check and source-literal secret scan passed. The existing catalog warnings are non-failing and do not make external calls.

No fresh graph / LLM / external data API / PDF. main is unchanged.
Offline closure does not imply fresh acceptance: NEEDS_FRESH_REVALIDATION.
