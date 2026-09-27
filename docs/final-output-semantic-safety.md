# Final-output semantic safety

## Ownership

`validated_execution` remains the sole execution authority. Agents reason;
canonical acceptance collects and removes unapproved plans; Audit proves claim
closure; renderers display the accepted artifact. No provider, agent topology,
financial authority or US execution policy is changed by this revision.

The v5 semantic revision is `relational-execution-lineage-2026-09`.

## Review findings and fixes

* **R1:** Cell-by-cell classification discarded condition/response relationships.
  Collection and pruning now retain Markdown row/header context. Action columns
  and explicit conditional antecedents give imperative responses their context;
  subject/object columns preserve descriptive company actions. Final validation
  also extracts rows from the actual rendered HTML, independently of upstream
  Markdown segmentation and Audit collection.
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
