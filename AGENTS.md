# TradingAgents project instructions

## Live LLM and production-acceptance runs

- Use the existing provider abstraction for every real LLM or full live E2E run.
- MiniMax is the default provider for future real LLM and live E2E tests. Use DeepSeek only when the user explicitly requests it for that run.
- Read the MiniMax model from the project's actual configured selection/catalog. Do not guess or silently impose a model override.
- Read credentials only through the existing environment/secret configuration. Never print, log, serialize, fixture, report, or commit API keys, tokens, cookies, passwords, or `.env` contents.
- Deterministic replay and fixture tests must not call an LLM or depend on public network access.
