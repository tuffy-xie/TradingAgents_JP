# 336e792 independent artifact review closure

The earlier offline PASS on 336e792 was invalidated by actual artifact review.
This repair does not execute any fresh graph, LLM or external data request.

## Observed facts and proved causes

- 0f29 Fundamentals published investor-directed advice to continue holding.
  `_HOLD_STANCE` required advice immediately followed by holding, omitting the
  aspect/audience relation in `建议继续持有`. The recommendation remains subject
  to Portfolio ownership, independent of whether it names Buy/Sell/Hold.
- 0f29 tables mapped US recession Yes 8% to soft landing and Japan recession
  Yes 4% to a robust economy. Registry E-095476851c97 contains recession events,
  not a soft-landing event or verified realized economic state. The old gate
  selected the first regime in a whole row and required a numeric probability
  label or an explicit certainty predicate. A sibling outcome cell satisfied
  neither. 8dd4's Registry E-a2f057b9df34 likewise contains recession events;
  its unnumbered "soft-landing probability extremely high" escaped numeric
  extraction. No corresponding source event independently binds that claim.
- 6976's `严格执行7,200日元止损` uses execution verb + value/unit + stop. Stop
  parsing previously covered label/value and trigger/action, not that relation.
- Hold/unavailable validators retained parsed or archived entry/stop/position
  and derived risk math. Reconciliation changed permission but never cleared
  parameters; the final cross-state guard returned early for denied execution.
- ca4e's visible `关键数据门控` came from the deterministic financial fallback
  itself, not the renderer. The public sanitizer and validator did not classify
  that internal label.

## Repair within existing owners

- Extend holding-advice grammar with aspect/audience while retaining external
  attribution, action-local negation, history and ordinary quality analysis.
- Reuse `probability_event_gate_violation` and SEMANTIC_MISMATCH. Recover the
  event/odds/outcome relation across cells, bind numeric and qualitative odds to
  their own country/year/outcome event, and require independent evidence for a
  realized regime, demand or monetary result. Conditional outcome cells remain
  valid. Bound probabilities remain; only unbound inference cells are replaced.
- Country context can be inherited from an enclosing heading, never from a
  previous sibling section. Script-aware US/UK labels retain Japanese mixed
  text's legitimate event facts. An invalid row is removed without splitting
  its valid sibling table with fallback prose.
- Review also found the same outcome gap in 82da's recession-to-demand row and
  ca4e's recession-to-realized-growth wording. They use the same existing gate,
  not a new macro authority or full-sentence blacklist.
- Extend the stop relation with action-local prohibition/history preservation.
- Clear all canonical execution proposal/risk fields when status is not OK.
  Preserved raw proposals remain in the technical log. Final validation rejects
  denied execution with any residual parameters, even after upstream cleanup.
  Sell 8513/9500/3% remains intact.
- Use natural financial withholding text. The existing presentation layer
  removes old enforcement labels and independently blocks an injected label.

Current semantic revision: `relational-macro-withheld-plan-2026-10`.
Old accepted revisions require normal canonical reacceptance, not a bypass.

## Verification

`tests/test_independent_review_closure.py` covers removal, preservation,
claim SHA, exact artifact bypass, denied-state parameter injection, table
structure, context isolation and legitimate Sell math. Saved-state replay adds
an explicit denied-state parameter count instead of reporting only text scans.

Final test/replay results, exact hashes and manual artifact review are saved
under output/independent-review-closure-validation and final clean-HEAD replay
directories. Original saved sources and the 336e792 review package are retained
unchanged. No PDF or fresh-live acceptance is claimed by this offline repair.
