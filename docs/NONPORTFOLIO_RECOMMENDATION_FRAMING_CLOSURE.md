# Non-Portfolio recommendation framing closure

## Observed and reproduced

The immutable 37979ae artifacts expose Fundamentals titles 投资建议总结 / 投资建议与风险提示 and Research's 投资决策报告. Research also describes 本次决策 and 看空立场. These all have zero ownership claims with the prior classifier. Injecting them into a structurally valid exact composed research section (with a nonempty body), after all upstream cleanup, yields FINALIZED before this repair.

The final sentence 投资者应结合自身风险承受能力和投资目标做出最终决策 explicitly assigns agency to the investor. It is not the internal Research Manager's decision. It is retained and tested, rather than banning the word 决策.

## Proved root cause

Advice surfaces matched short/full or trailing role names. Summary/risk-note suffixes and decision-report genre escaped that title boundary. Prose classification required rating, directional recommendation, instrument selection or the already defined evaluative stance; self-owned decision framing without a rating value was absent. Final Contract correctly ran its independent exact-artifact scan, but reused this incomplete ownership proposition classifier, hence the false negative existed in both layers.

## Minimal repair under existing authority

- Title classification identifies the investment/advice/decision role independently of its report/summary suffix. It still respects external ownership, corporate investment decisions and educational/prohibitive mentions of advice. An exempt heading never exempts a recommendation underneath it.
- Prose framing uses decision agency (this/our/final decision) or a directional investment stance. Rewrite those semantic components to research assessment or upside/downside research judgment. Keep valuation, evidence, monitoring conditions, emphasis and list structure.
- Use existing SECONDARY_INTERNAL_RATING and claim SHA/owner/resolution. RECOMMENDATION_FRAMING is a subtype, not another authority system. Its deterministic replacement is recorded. Title surfaces retain the existing RECOMMENDATION_SURFACE behavior.
- Portfolio is exempt only through the existing owner boundary in canonical acceptance and exact artifact scanning. Its formal rating and validator-owned execution remain unchanged.
- Semantic revision is nonportfolio-recommendation-framing-2026-10 so previously accepted artifacts must pass the current acceptance boundary.

No execution classifier, freshness, Financial, Sentiment, JSF, macro binding, shared renderer, topology or horizon changes.

## Tests and artifact acceptance

Targeted ownership/fault-injection/previous-closure tests: 320 passed. Tests distinguish external broker/consensus, corporate investment decisions, investor independent decisions, pure withholding and educational titles. Exact composition injections independently BLOCK with domain_authority_consistent=false. Ordinary company-quality/bull-bear analysis remains, and canonical Underweight/Sell 8513/9500/3% and the US writer retain their original paths.

Final full offline repository suite: 2638 passed, 1 integration test deselected, 91 subtests passed; 20 non-failing catalog warnings. Ruff, diff-check and source/artifact secret scanning passed.

Six immutable states (969ce71b, 69760305, ca4e6ddb, 8dd4eec0, 0f29d2b8, 82da370c) run through scripts/replay_saved_states.py, with network/LLM disabled. Accepted Markdown/HTML is never manually edited. Source hashes, bundle and Market authority remain unchanged. Actual report differences, summary/state, claim identities and three-way exact bytes are reviewed and exported at the clean final commit, separately from the original source HEAD.

No fresh graph, LLM or external data API. No PDF. Offline closure requires a separately authorized fresh revalidation; it does not permit main merge or JPX Stage.
