# Fresh 8d911: script, recommendation role and event-outcome closure

## Observed source facts

Source run `8d911d52-d281-4c07-ab43-0a2c34203847`, source HEAD
`9d7d74128b1a4f6f248b785c02d41aa926133d1d`, analysis date `2026-10-05`.
One compiled graph started and completed (22 stream events, 10 value states).
MiniMax Global M3/M2.7-highspeed only; one transient 529 recovered within the
existing SDK retries. This was not a provider interruption.

Original accepted Markdown SHA:
`0164ff89d4121cdcce3c331895d047218d0126159b85026f95712c181d51bc71`.
The source contract/scanners returned FINALIZED/CLOSED/zero violations, but
independent actual Markdown/HTML review rejected the result:

- Market table: `耐心持有核心部位`.
- Research: `為何不選擇Sell而選擇Underweight` and `透過減持降低下行風險`.
- News: event-odds-derived `地缘风险暂时缓和` and unsupported dot-plot alignment.
- JSF-derived section framing asserted participant cash flows/structural selling
  even after its numeric participant assertions had been removed.

These are product defects. No second fresh graph and no PDF are permitted.
The original captured/accepted state, reasoning, Markdown and HTML are retained;
only acceptance metadata records the independent rejection.

## Proved roots and minimal repair

1. Rating extraction recognized labelled values and signals, but not the
   predicate/value relation “choose [rating]”. It also inspected script-specific
   spellings. A labelled Recommendation was removed while its equivalent
   Traditional Chinese choice prose survived.
2. Existing position-preservation semantics required a narrower position noun
   and preservation verb. “持有 + 核心部位/头寸” escaped. Means/action/outcome
   prose (“through reducing exposure, lower risk”) also lacked directive cues.
3. A numbered compound advice heading and an inline bold action lead-in could
   survive as a recommendation surface after their explicit actions were pruned.
4. Economic-outcome checking covered demand, carry risk, monetary conditions
   and pricing, but not asserted geopolitical state or official-policy alignment.
   Event probabilities E-2d661b4e3b38 and E-7602e7f0e69e contain odds, not a
   realized geopolitical-risk statement or a Fed dot-plot witness.
5. JSF measurement identity previously focused on quantified short balances.
   Removing a numeric loan-to-participant inference did not remove its unnumbered
   assertion that lending/credit data reveal cash flow or participant selling.

Repair reuses existing Portfolio rating ownership, validated-execution gate,
SEMANTIC_MISMATCH and exact artifact defenses. No parallel authority/pipeline.
Script aliases are length-preserving classifier projections, not report
translation; original claim text, offsets and SHA remain exact. Japanese terms
remain distinguishable. Attribution, business actions, history and pure bans
remain exempt. A negative alternative in Portfolio prose is not its chosen
rating; the positive canonical choice remains permitted.

Independent verified statements can witness geopolitical/policy/flow outcomes;
prediction questions and JSF balances cannot. Conditional analysis, source
probabilities, current technical analysis and native lending observations remain.

Semantic revision: `script-role-event-outcome-2026-10`. Old accepted revisions
must revalidate or fail closed using the existing publication guards.

## Deterministic verification

`tests/test_fresh_8d911_authority_closure.py` reproduces failed source variants,
checks source offsets/claim SHA and preservation, and injects violations *after*
composition to prove independent Final Contract BLOCK. Initial unmodified
production helpers failed 32 of the first 47 reproductions.

Replays use only saved states and existing `scripts/replay_saved_states.py`,
which denies sockets, HTTP clients and model invocation. Original source bytes,
manifest, bundle and Market authority cannot be changed to obtain acceptance.
Fresh Market remains CURRENT, 245 complete rows through 2026-10-05; canonical
Underweight/Sell/8745/7500/null is preserved. Historical 8dd4 remains
Underweight/Sell/8513/9500/3%; Hold/unavailable plans retain null parameters.

Fresh failure plus 969ce71b, 69760305, ca4e6ddb, 8dd4eec0, 0f29d2b8, 82da370c
and 75a68627 are reaccepted through the sole publication path. Actual Markdown,
shared HTML, state and summary are reviewed; contract/state/Markdown bytes SHA
must exactly match. Manual verdicts and final replay code HEAD are stored in the
separate stable replay export, never substituted for source fresh artifacts.

Natural stopping point: OFFLINE_CLOSURE_PASS / NEEDS_FRESH_REVALIDATION.
Offline closure cannot retroactively turn the rejected fresh run into PASS.

Final offline test results: 62 fresh-failure targeted tests passed; the broader
authority/regression selection passed 494 tests before the four final natural
replacement cases were added. The final repository suite passed 2822 tests and
91 subtests (one live integration test deselected), with network/model calls
disabled. Ruff and diff whitespace checks passed. Final clean-HEAD replay and
export metadata carry their own exact code identity and report byte hashes.
