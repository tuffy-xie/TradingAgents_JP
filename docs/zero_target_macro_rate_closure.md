# Zero target / annual-rate evidence closure

Source: `1be5bcc8-b6d5-45e4-b412-4dafd1f4a1fb`, production HEAD
`04c8db8aa6e8a2dd3ec4834474c660ce5bc92923`. Review baseline:
`9dfb191b86fc7a9bfa1bfd391bda75bb82fbbff1`. No new graph, reasoning,
external data, or PDF is used in this repair.

## Execution: observed fact and proved cause

The Trader labelled Sell, Entry 7500, Stop 8200 and Position Sizing 0%.
Portfolio labelled Sell and explicitly instructed exit/reduce to a **target**
position of zero. The caller's `portfolio_context` is empty. The calculator
parsed the first percentage as traded exposure, accepted entry and stop, and
multiplied zero by price risk, returning OK and portfolio risk 0%. Reconciliation
checked directional agreement only. The final boundary repeated that same
direction check. Thus a target account balance acquired new-trade permission.

An exit of an existing long, a target flat book and a new short are distinct.
Neither missing holdings nor a Sell label proves an existing long or authorizes
a short. A zero-size order also cannot become executable by adding entry/stop.
The existing entry/stop calculator does not validate account transitions or
closing quantities. Target-flat proposals therefore fail closed (also when a
book is supplied) rather than invent a closing amount. The legal Sell rating
remains; execution fields and derived risk are withheld. `TARGET_FLAT` records
intent, not a fabricated holding or order. Pure prohibitions are not targets.

This does not reinterpret all legacy Sell plans as short orders or enforce a
new stop-direction convention. The independently accepted 8dd4 Sell
8513/9500/3%, 8d911 Sell 8745/7500/null and 75a Buy 8050/7600/null remain.
Implementing a fully account-aware closing-order validator is a separate
product task; this repair does not create one.

## PCE: observed fact and proved cause

All 96 Registry entries were examined for PCE observations. The only verified
PCE source is `E-60f572e9df4b`, `get_macro_indicators`, FRED PCEPILFE:
2026-08-01, 130.455, Units **Index 2017=100**, one dated row. Its window change
is +0.00% over the identical date, not a year-on-year observation. The Japan
macro bundle's `us_pce` is unavailable. Agent INFERENCE entries are not source
evidence. No same-series annual rate or prior-year same-month base exists.

The original News inferred 2.8–3.0% YoY and above the Fed's 2% target. The
number catalogue could license tokens occurring elsewhere; it had no
index-level/annual-rate identity check. The exact artifact scan reused those
gates and missed both the sentence and the summary row.

The existing SEMANTIC_MISMATCH gate now binds a PCE annual-rate assertion to
verified source series, core/headline definition, observation date and explicit
year-on-year units. A window change, future date, inference, conflicting rate,
or different series cannot bind it. Heading identity applies to child clauses;
index facts, conditional monitoring and withholding remain legal. No synthetic
YoY or data fetch is introduced. Since this source has no annual witness, the
unsupported annual/target claims are removed rather than replaced with a
made-up rate. Same-class rewording cannot resolve its Audit finding.

## Independent defenses and artifacts

Final Contract independently rejects authorized-zero exposure and stale
permission/status conflicts, and reruns annual-rate binding on exact candidate
bytes. Fault injection bypasses upstream execution or evidence cleanup.
Claim identity/SHA and accepted-artifact SHA remain in the existing Audit.
Semantic revision: `execution-zero-target-and-macro-rate-identity-2026-10`.
Nine saved states must reaccept through the sole publication pipeline, with
actual Markdown/HTML review and exact contract/state/file SHA equality.
