# Analyst estimate revisions are not realized performance

Source run: `8d911d52-d281-4c07-ab43-0a2c34203847`, source HEAD
`9d7d74128b1a4f6f248b785c02d41aa926133d1d`. Previous review HEAD:
`bf10410769efff6a66bfa7f569922cb974c2f4bc`.

## Observed fact and proved cause

Portfolio published “3 項盈利指標 30 天內上調，基本面改善仍在進行”.
Registry `E-a23a2bfeb6a7` and the saved Minkabu analyst-expectations bundle
describe FY2027 forecasts dated 2026/10/05: three upward metric revisions in
30D, not individual analyst upgrade counts or observed earnings. Current
Actual and Company Guidance both remain insufficient/freshness unverified.

The existing Actual gate looked for named current quarters or realized metric
changes. It omitted an ongoing fundamental-change predicate; number membership
could check 3/30 but did not check the forecast-to-Actual relation. This let
the same type error bypass both enforcement and the exact final-artifact gate.

## Minimal repair

Use the existing `SEMANTIC_MISMATCH`/Actual gate and shared final-artifact
validation. A revision premise plus an asserted ongoing/realized performance
outcome cannot replace unavailable Current Actual. The deterministic replacement
preserves the revision count/window and makes the expectation type explicit.
It does not invent a realized value or alter any source evidence or reasoning.

The existing qualitative Actual predicate also covers ongoing fundamental
change if the revision premise is removed/reworded. Ordinary quality/factors,
dated historical Actuals, external consensus, explicit forecasts/hypotheses,
withholding, and genuinely available canonical Actual authority remain allowed.

Claim SHA refers to the original row/sentence; Audit records the source class,
required authority, owner, status, evidence ID, and final artifact SHA. A
reworded invalid financial outcome prevents closure independently of literal
equality. Post-composition fault injection must BLOCK without upstream findings.

## Boundaries

No changes to financial collection, freshness, Guidance, execution, Market,
sentiment, JSF, rendering, horizon, Agent topology or upstream main. Canonical
8d911 Sell 8745/7500/null, 75a Buy 8050/7600/null and 8dd4 Sell 8513/9500/3%
must survive. Replay all eight immutable original states without LLM/network/PDF.
Offline closure is not fresh acceptance; stop at NEEDS_FRESH_REVALIDATION.
