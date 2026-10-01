# Final-output semantic safety

## Ownership

`validated_execution` remains the sole execution authority. Agents reason;
canonical acceptance collects and removes unapproved plans; Audit proves claim
closure; renderers display the accepted artifact. No provider, agent topology,
financial authority or US execution policy is changed by this revision.

The v5 semantic revision is `published-rating-execution-ownership-2026-10`.

Approval is scoped to the deterministic execution publisher, not to all Agent
text. Both approved and withheld runs collect execution claims before pruning.
Raw Analyst/Research/Portfolio action or position instructions are removed;
only the validator-generated plan is inserted in the Trader and Portfolio
publication slots. The exact artifact defense exempts that block only when its
owner and every label/value (including direction) match canonical validation.
A changed parameter, action or an identical block in an Analyst section blocks
publication. Ordinary technical analysis, monitoring, prohibitions, attributed
ratings and business descriptions retain their existing semantics.

Formal rating ownership includes conclusion labels and natural-language
assertions linking a recommendation/value to a rating predicate, not just
label/colon syntax. A direction-only Market outlook labelled "整体评级" is
relabelled "技术面展望"; an explicit investment rating or Buy/Hold/Sell conclusion
is not exempt. Assistant-agency report-production/data-preparation narration
is presentation leakage; its original claim is audited and removed. Quoted
company speech and ordinary first-person business explanations are preserved.

These checks run after exact composition and at publication/idempotent entry
points independently of the upstream findings. Audit closes an execution
claim against the artifact excluding only exact validator-owned blocks, so
an approved deterministic replacement cannot be mistaken for raw plan survival.

## User-facing investment rating ownership

Portfolio's explicitly labelled final rating is the only internal investment
rating in JP user reports. Acceptance freezes it as `portfolio_rating` in the
Final Output Contract. It does not derive that rating from Trader action,
execution parameters or an Analyst's recommendation. Publisher checks use the
frozen contract, not subsequently changed raw Agent fields.

Other published Analyst/Research/Trader fields lose only their own rating
propositions; their raw reasoning remains in the full Agent log. Attributed
broker ratings and consensus distributions remain third-party facts, not
internal recommendations. Attribution is clause/row-local; a broker citation
cannot exempt a sibling internal recommendation. Label/value lines, headings,
table cells and first-person recommendations share this policy.

Rating transitions also assign investment authority. A from-rating linked to a
target-rating (arrow or change/upgrade/downgrade relation) is an internal rating
proposition regardless of its table heading or future condition. Reassessment
without a specified target remains research input. Explicitly attributed broker
transitions and consensus remain external facts. Named-source reporting verbs
provide attribution without a broker-name allowlist; internal Agent role names
cannot claim that exemption.

Evaluative advice to hold an asset (e.g. worth holding or maintaining a position
as the optimal strategy) is a Hold recommendation, not merely a positive factor.
General risk/quality assessments and descriptive policy status are not investment
recommendations. The Research Manager still produces an upstream investment plan
for the Trader; only its published user-facing recommendation propositions are
removed. Its raw plan and debate remain in the technical log.

Transition findings use the existing `SECONDARY_INTERNAL_RATING` category and
retain the original row/claim hash, from-rating and target-rating, Portfolio
ownership, enforcement and exact accepted-artifact digest. Presentation changes
cannot close a surviving transition. The exact-artifact scanner independently
blocks a secondary target even if all upstream pruning was bypassed.

Each removal has its original claim, SHA, rating and field in Audit. Closure
rechecks secondary rating ownership against the exact composed artifact;
format/localization changes do not count as removal. A second internal rating
(even one agreeing with Portfolio), or a contradictory Portfolio rating,
blocks domain-authority validation. The final artifact check does not depend
on upstream rating finding/pruning. Current-revision publication also checks
the exact artifact, in addition to the digest and revision guards.

### Read-only Market snapshot investigation (run 01d7d9bb)

The captured run (2026-09-26 01:52 JST) contains a diagnostic snapshot dated
2026-09-25 with complete OHLCV and calculated indicators. Its Market tool
evidence contains only `NO_DATA_AVAILABLE`: latest complete bar 2026-09-24,
expected completed session 2026-09-25. `_canonicalize_market_report` uses
`get_stock_data` evidence, not snapshot text, so it cannot recover an actual
CSV date and displays "未取得". This means no accepted tool OHLCV was obtained;
it is not proof that all run-local diagnostic data is absent.

The source-of-truth contract and tests explicitly make snapshot an optional
diagnostic. Market Analyst has `get_stock_data` / `get_indicators` tools and is
not given snapshot as replacement authority. The paths are different:
snapshot selects the most recently modified matching CSV in the home cache
(normal loader only on cache miss); stock data calls configured vendor routing
and Yahoo history; indicators use the configured five-year cache/loader.
Snapshot is built before Agent execution. The archived evidence does not save
the selected cache filename/hash, actual tool request arguments, or per-call
fetch times, so the exact cause of the differing vendor/cache rows cannot be
proved retroactively. No transport/timing hypothesis is promoted to fact.

There is legacy wording drift: snapshot text still calls itself source of truth
and its registry seed is eligible, while the Japan governance contract treats
it as diagnostic. This is a follow-up dataflow/diagnostic contract issue, not a
reason to promote snapshot into Market authority. A minimal later fix would
record per-source/cache lineage, scope the unavailable message to the Market
tool path, and share a normalized, freshness-validated market result across
diagnostic and tool consumers. This rating change modifies none of those paths
or freshness gates.

## Review findings and fixes

* **R1:** Cell-by-cell classification discarded condition/response relationships.
  Collection and pruning now retain Markdown row/header context. Action columns
  and explicit conditional antecedents give imperative responses their context;
  subject/object columns preserve descriptive company actions. Final validation
  also extracts rows from the actual rendered HTML, independently of upstream
  Markdown segmentation and Audit collection.
  Header roles are composed from condition/response/description/agency concepts,
  so modifiers such as recommended, portfolio or suggested do not depend on an
  exact full-header spelling. Trigger/response columns can occur in either order.
  Company-action columns supply company agency to the existing descriptive-action
  policy; they do not become reader trading instructions. Short Markdown delimiter
  cells are recognized for execution collection before structural normalization.
* **R2:** Whole-line literal equality mistook presentation changes for removal.
  Audit retains the original SHA and a presentation-normalized SHA, compares
  action-bearing components, and follows the same deterministic localization
  transformations as the publisher. Numbering, emphasis and tool-label changes
  are not evidence that an action disappeared. If an unauthorized same-action
  claim remains after reformatting, closure stays unresolved conservatively.
* **R3:** The old empty-row rule treated the first column as a label in every
  table and treated availability states as placeholders. Single-column values,
  qualitative observations and explicit `N/A` / `Not provided` states now survive.
  Only empty glyphs are placeholders. A leading cell is ignored only when the
  header declares a label/period role or the cell is a period identifier. Both
  normalization and validation use this contract, including the US report writer.
* **R4:** A conditional token used to override prohibition, and descriptive
  corporate actions were treated as investor orders. Polarity and agency are now
  evaluated at the action. A later authorization cannot inherit a preceding ban;
  a company sale of assets does not authorize the reader to trade stock. Holding
  duration and stop directives still require execution approval.
* **R5:** The builder checked semantic revision but publishers did not. Direct
  publication now rejects missing/stale revisions. Reacceptance is explicit at
  the existing controller/builder boundary, never invented by the renderer.

## Publication entry points

| Entry | Boundary |
|---|---|
| `write_report_tree` (programmatic API, CLI archive, Web archive writer) | `require_canonical_final_state` before any files are written |
| CLI `display_complete_report` | Same guard before displaying JP accepted Markdown |
| `_render_report_html` | Same guard before HTML generation |
| Web `/api/report/{ticker}/{date}` | Reaccepts loaded current/historical state, then guarded HTML renderer |
| PDF / browser print | Consumes the same guarded HTML; no alternate business composer |
| Offline replay / direct renderer callers | Explicit builder reacceptance or fail closed at the guard |

A current-revision finalized state is idempotent. A stale revision must be
rebuilt from captured state or rejected; changing only its revision string is
not a supported migration.

## Deterministic evidence

`tests/test_semantic_safety_hardening.py` covers row relations, polarity, subject,
history, transform lineage, partial-claim survival, valid US tables, direct
publication, archive reacceptance and independent rendered-table defense.
The final-defense test intentionally disables upstream collection/pruning; the
artifact must still block. Another test disables Markdown segmentation while
retaining rendered-table validation.

Captured run `01d7d9bb-c5e7-49a9-8f3f-3acde7fdb2b6` can be reaccepted without
regenerating reasoning or fetching sources. Preserve its source manifest;
record replay metadata separately. Compare exact UTF-8 bytes, without trimming,
between persisted accepted Markdown, the contract digest and the report file.

## Limits

This is a bounded deterministic language contract, not an unrestricted natural
language theorem prover. The final boundary is independent of upstream state
and Markdown segmentation, but deliberately reuses the execution policy rather
than introducing a competing policy. Ambiguous surviving execution components
remain unresolved; that may reduce availability rather than authorize a trade.
New live wording still requires separate production acceptance. No live LLM or
external data-provider calls are part of this repair's tests/replay.
