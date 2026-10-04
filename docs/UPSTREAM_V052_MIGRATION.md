# Japan branch / upstream v0.5.2 migration

## Branch boundary

The integration target is the fork's pure upstream `main` at
`8b22d43d01d9ddda5d686d093d5385884622f3de` (v0.5.2). Japan development starts
from `551ec634cf7a3b599e9fe4a454540175eb6b8e1b` and is integrated on
`sync/upstream-v0.5.2-jp`, then fast-forwarded into
`fix/japan-evidence-authority-acceptance`. Neither upstream/main nor fork/main
is rewritten. A newer upstream version is a separate migration, not silently
included here.

## Canonical interfaces

- Agents use upstream `agents/context.py`, `state.py`, `structured.py`,
  `rating.py` and `tools.py`; vendors live under `dataflows/vendors/`.
  The old full implementations under `agents/utils/` and flat dataflow paths
  are not maintained in parallel.
- Each parallel Analyst has private messages and tools. Only its report,
  Evidence Registry and claim-level Audit are returned at the parent join.
  Evidence merges by evidence identity, Audit by exact finding identity, never
  merely by report field. No cross-Analyst tool-message leakage is permitted.
- Production `create_run_state`, `stream_run`, `record_decision` and checkpoint
  interfaces serve propagate, CLI and Chinese Web. `get_graph_args` still owns
  call arguments; AcceptanceGraphObserver remains observation-only.
- Managers now produce top-level `investment_plan` and `final_trade_decision`.
  A thin JP state adapter feeds the existing acceptance boundary, then mirrors
  its accepted research report into the upstream field. A raw manager alias
  must not be persisted as an accepted section. US keeps the upstream fields.
- Memory uses the upstream log, point-in-time settlement and portfolio context.
  Checkpoint signatures include the parallel graph shape and run settings.
  Previous sequential checkpoints cannot silently resume into the new shape.
- The Chinese Web keeps its UI, current/history guards and shared renderer.
  Provisional reports are progress only; publication follows acceptance.
  CLI section-file persistence is likewise deferred until JP acceptance closes.

## Japan invariants

Formal stock/indicator tools retain lossless metadata captured before display
truncation. Incomplete or not-yet-completed Japan OHLCV rows cannot advance the
canonical date. JP data is not gap-filled to manufacture a current bar.
Verified snapshot remains diagnostic. Upstream US loader/cache/fill behavior
remains on its canonical path.

Japan source defaults, official disclosure coverage limits, source TTLs and
the bounded provider timeout are retained. Financial Actual/Guidance,
investor-only sentiment, source-native JSF, Portfolio recommendation ownership,
validator-owned execution, macro event binding, secret redaction, horizon
removal and dynamic cover cards retain their established contracts.

## Concrete artifact defect discovered during migration review

The saved 969ce71b report contained a traditional-script stop-price instruction
under Hold despite the previous exact scanner reporting zero violations.
The existing stop-parameter grammar omitted `損` and colon-separated values.
The repair recognizes the same stop/price relation across simplified and
traditional script, including cross-cell parameter/value rows. It neither
adds an authority system nor weakens Hold/business/withholding boundaries.
The semantic revision advances to
`macro-outcome-recommendation-stop-2026-10`; stale published artifacts must pass
reacceptance rather than bypass this correction. Fault injection proves the
exact final contract blocks the stop instruction without upstream pruning.

## Offline verification and next boundary

The test suite disables sockets and vendor HTTP for non-integration tests.
Scripted models verify the actual parallel graph, production argument forwarding,
evidence join, JP acceptance, US decisions, checkpoint resume and Web/CLI gates.
These deterministic model stubs are not fresh live acceptance.

`scripts/replay_saved_states.py --state <saved-state.json> --output <directory>`
reaccepts immutable production evidence, blocks LLM/network calls, checks exact
artifact scanners and Audit identities, compares shared HTML DOM/tables and
verifies Contract/state/file SHA without trimming or newline conversion.
Six saved runs are reviewed: 969ce71b, 6976, ca4e, 8dd4, 0f29 and 82da.
8dd4 must retain canonical Sell / 8513 / 9500 / 3%; Hold-specific gates cannot
remove that legal plan. Restored business execution, external ratings,
attributed headlines and hypothetical monitoring must remain visible.

Replay output is local and ignored by Git. Source manifests retain their original
HEAD; replay summaries record the migration code HEAD independently. No source
artifact or Agent reasoning is regenerated. No PDF or browser is needed for
this offline phase. Final offline success means `NEEDS_FRESH_REVALIDATION`,
not a fresh-live PASS. A new fresh acceptance requires explicit authorization
on the final Japan feature HEAD and allows at most one started fresh graph.
