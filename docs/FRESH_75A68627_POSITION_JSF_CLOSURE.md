# Position constraints and JSF metric-scope closure

## Observed artifacts and proved causes

Source run `75a68627-5a5a-44ed-afc8-d967174bdd77` completed the only authorized
fresh graph on source HEAD `0d9601522fd03aca30e70ce576e52ac07a946684`.
Independent review of the `72f9dca` offline artifact found two remaining
false negatives, reproduced with the actual execution/evidence helpers and
exact final validator before repair:

- Research said to avoid a **new full position at the current price** in
  Japanese. The existing size/directive recognizer did not understand the
  Japanese position object. Negative action polarity alone is insufficient:
  rejecting full size under a price condition is a sizing/entry constraint,
  not withholding the entire trade.
- Portfolio described JSF's 2,100-share stock-loan balance as a short balance
  declining toward zero. The generic numeric catalog accepted that number,
  while the short-semantic guard covered short pressure/absence but not a
  renamed measurement. The exact evidence checker only reran financial and
  probability gates, not JSF scope. The old 47,700-to-2,100 wording had really
  disappeared; literal closure of that old numeric claim did not prove that
  the lending-to-total-short substitution had disappeared as a semantic class.

## Minimal changes within the existing authorities

1. Existing execution recognition now binds a Japanese trading-position
   object to a size constraint/adjustment predicate. Avoiding full size is
   distinct from avoiding all new entry. Action-local prohibition, company
   competitive position, corporate execution and ordinary technical outlook
   remain separate. Use the existing `UNAPPROVED_EXECUTION_CLAIM` category and
   `POSITION_SIZE_RECOMMENDATION` warning. Upstream pruning and the exact
   execution scanner both apply it, even when canonical execution is allowed.

2. Existing JP evidence enforcement binds short-measure assertions to JSF's
   structured `securities_finance_balance` / `OBSERVABLE_LENDING_BALANCE`
   observations. A known lending quantity cannot be renamed a total short
   measurement in prose, a table, or a future condition. Typed and attributed
   independent short-interest evidence remains a separate fact; disclaimers
   and original financing/stock-loan/net measurements remain legitimate.
   Reuse `SEMANTIC_MISMATCH` with `jsf_measure_scope_mismatch`, original claim
   SHA, Agent/field, source evidence IDs and JSF authority owner. Delete the
   unsupported derived clause, not the canonical JSF data or source state.

3. Final Contract independently reruns existing JSF scope/short-semantic gates
   on exact candidate bytes and records `CROSS_DOMAIN_AUTHORITY:JSF:<claim SHA>`.
   Audit closure checks surviving JSF measurement semantics, not just old
   literal equality. Changed wording cannot close that class while another
   invalid lending-to-short assertion remains. Qualified Japanese position
   constraints likewise retain lineage through rewording/bold presentation.

Semantic revision: `position-constraint-jsf-scope-2026-10`. Existing stale
publication guards force reacceptance of older FINALIZED artifacts. No new
authority, pipeline, Agent, source loader or execution permission was added.

## Verification and preserved outputs

Regression tests exercise production acceptance, table cells and alternate
wording, independent typed external facts, pure Japanese withholding, company
position/execution and post-composition fault injection with no upstream
findings. The initial repro had 26 failures; the repaired matrix passes.

Seven immutable saved states are reaccepted offline using the existing
`scripts/replay_saved_states.py` network/LLM guards: this source plus
`969ce71b`, `69760305`, `ca4e6ddb`, `8dd4eec0`, `0f29d2b8`, `82da370c`.
Exact Markdown/state/contract SHA, claim SHA, Audit artifact SHA, HTML DOM and
tables, source bundle/manifest/Market authority and immutable source bytes
are checked. Manual review is separately recorded with final clean code HEAD.

Source 75a68627 retains Market CURRENT (2026-10-02 complete bar), Portfolio
Overweight, canonical Buy / 8050 / 7600 / position null. Source 8dd4 retains
Underweight, Sell / 8513 / 9500 / 3%. Hold/unavailable plans retain null
execution fields. Corporate execution and natural hedging remain available;
JSF source-native balances and external consensus remain available.

This is deterministic offline closure only. No second fresh graph, LLM,
external data API, browser or PDF is used. Main and JPX Stage are untouched.
Captured mixed-language prose remains an existing output-quality limitation;
this repair does not translate or regenerate Agent reasoning.

Status: `OFFLINE_CLOSURE_PASS` / `NEEDS_FRESH_REVALIDATION`.

Final verification: 46 new focused cases pass; the combined authority,
execution, evidence and Final Contract targeted run passed 361 cases before
seven additional alternate-wording/preservation cases were added. Full final
suite: 2760 passed, 1 external integration test deselected, 91 subtests passed
(20 non-failing model-catalog warnings). Ruff, diff-check and credential
exposure/redaction scans pass. The six historical Markdown artifacts are
byte-identical to their previous accepted versions; the new source report
only loses the two offending claims and their now-empty dependent heading.
