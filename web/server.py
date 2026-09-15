"""FastAPI + SSE backend for TradingAgents web interface."""
import asyncio
import html
import json
import logging
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

sys.path.insert(0, str(Path(__file__).parent.parent))

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    resolve_instrument_identity,
)
from tradingagents.agents.utils.evidence_registry import build_run_manifest
from tradingagents.agents.utils.rating import parse_rating
from tradingagents.dataflows.market import enrich_market_context, resolve_market_context
from tradingagents.dataflows.market_data_validator import build_verified_market_snapshot
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.final_output import (
    build_canonical_final_state,
    require_canonical_final_state,
)
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS, get_model_options
from tradingagents.report_artifacts import render_markdown_fragment, validate_rendered_html
from tradingagents.report_consistency import canonical_report_metadata
from tradingagents.secret_redaction import safe_exception_text, sanitize_data, sanitize_text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="TradingAgents Web API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

executor = ThreadPoolExecutor(max_workers=2)

PROVIDER_URLS = {
    "openai":     "https://api.openai.com/v1",
    "anthropic":  "https://api.anthropic.com/",
    "google":     None,
    "xai":        "https://api.x.ai/v1",
    "deepseek":   "https://api.deepseek.com",
    "qwen":       "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    "glm":        "https://open.bigmodel.cn/api/paas/v4/",
    "minimax":    "https://api.minimax.io/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama":     "http://localhost:11434/v1",
    "azure":      None,
}

PROVIDER_LABELS = {
    "openai": "OpenAI", "anthropic": "Anthropic", "google": "Google",
    "xai": "xAI (Grok)", "deepseek": "DeepSeek", "qwen": "Qwen (Alibaba)",
    "glm": "GLM (Zhipu)", "minimax": "MiniMax", "openrouter": "OpenRouter",
    "azure": "Azure OpenAI", "ollama": "Ollama (Local)",
}

# Provider → which reasoning-effort knob the UI should surface.
# Mirrors cli/main.py Step-8 branching.
PROVIDER_EFFORT_KIND = {
    "openai": "openai_reasoning_effort",
    "azure":  "openai_reasoning_effort",
    "anthropic": "anthropic_effort",
    "google": "google_thinking_level",
}

# Crypto ticker suffix detection — kept in sync with cli/utils.py
CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")


def detect_asset_type(ticker: str) -> str:
    return "crypto" if ticker.strip().upper().endswith(CRYPTO_SUFFIXES) else "stock"


def _safe_web_event(data: dict) -> dict:
    """Return an SSE/error payload safe for browser and network presentation."""
    return sanitize_data(data)

# Graph node name → {display, team}
AGENT_INFO = {
    "Market Analyst":       {"display": "Market Analyst",       "team": "analysts"},
    "Social Analyst":       {"display": "Sentiment Analyst",    "team": "analysts"},
    "News Analyst":         {"display": "News Analyst",         "team": "analysts"},
    "Fundamentals Analyst": {"display": "Fundamentals Analyst", "team": "analysts"},
    "Bull Researcher":      {"display": "Bull Researcher",      "team": "research"},
    "Bear Researcher":      {"display": "Bear Researcher",      "team": "research"},
    "Research Manager":     {"display": "Research Manager",     "team": "research"},
    "Trader":               {"display": "Trader",               "team": "trading"},
    "Aggressive Analyst":   {"display": "Aggressive Analyst",   "team": "risk"},
    "Conservative Analyst": {"display": "Conservative Analyst", "team": "risk"},
    "Neutral Analyst":      {"display": "Neutral Analyst",      "team": "risk"},
    "Portfolio Manager":    {"display": "Portfolio Manager",    "team": "portfolio"},
}

ANALYST_NODE = {
    "market": "Market Analyst",
    "social": "Social Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}


def build_agent_sequence(analyst_list: list) -> list:
    agents = []
    for a in analyst_list:
        node = ANALYST_NODE.get(a)
        if node:
            agents.append({**AGENT_INFO[node], "node": node})
    for node in [
        "Bull Researcher", "Bear Researcher", "Research Manager",
        "Trader",
        "Aggressive Analyst", "Conservative Analyst", "Neutral Analyst",
        "Portfolio Manager",
    ]:
        agents.append({**AGENT_INFO[node], "node": node})
    return agents


@app.get("/api/providers")
def list_providers():
    result = []
    for key in MODEL_OPTIONS:
        if key.endswith("-cn"):
            continue
        result.append({"id": key, "label": PROVIDER_LABELS.get(key, key.title())})
    return {"providers": result}


@app.get("/api/models/{provider}")
def list_models(provider: str):
    try:
        quick = [{"label": label, "value": value} for label, value in get_model_options(provider, "quick") if value != "custom"]
        deep = [{"label": label, "value": value} for label, value in get_model_options(provider, "deep") if value != "custom"]
        return {
            "quick": quick,
            "deep": deep,
            "effort_kind": PROVIDER_EFFORT_KIND.get(provider),
        }
    except KeyError:
        return {"quick": [], "deep": [], "effort_kind": None}


@app.get("/api/languages")
def list_languages():
    # Mirrors cli/utils.ask_output_language. ``value`` is what gets
    # forwarded to ``config["output_language"]`` (the prompt template
    # passes the raw string straight into the LLM).
    return {"languages": [
        {"value": "Chinese",    "label": "简体中文"},
        {"value": "English",    "label": "English"},
        {"value": "Japanese",   "label": "日本語"},
        {"value": "Korean",     "label": "한국어"},
        {"value": "Hindi",      "label": "हिन्दी"},
        {"value": "Spanish",    "label": "Español"},
        {"value": "Portuguese", "label": "Português"},
        {"value": "French",     "label": "Français"},
        {"value": "German",     "label": "Deutsch"},
        {"value": "Arabic",     "label": "العربية"},
        {"value": "Russian",    "label": "Русский"},
    ]}


@app.get("/api/asset-type")
def asset_type_endpoint(ticker: str):
    """Match CLI's `detect_asset_type` so the UI can warn before submit."""
    return {"asset_type": detect_asset_type(ticker)}


@app.get("/api/analyze")
async def analyze(
    ticker: str,
    date: str,
    provider: str,
    deep_model: str,
    quick_model: str,
    analysts: str = "market,social,news,fundamentals",
    research_depth: int = 1,
    trading_horizon: str = "multi_day",
    entry_condition: str = "",
    stop_loss_condition: str = "",
    take_profit_condition: str = "",
    max_position_pct: float | None = None,
    backend_url: str | None = None,
    output_language: str = "Chinese",
    effort: str | None = None,
    temperature: float | None = None,
    checkpoint: bool = False,
):
    allowed_horizons = {"intraday", "multi_day", "multi_week", "long_term"}
    if trading_horizon not in allowed_horizons:
        raise HTTPException(status_code=422, detail="invalid trading_horizon")
    if max_position_pct is not None and not 0 < max_position_pct <= 100:
        raise HTTPException(status_code=422, detail="max_position_pct must be between 0 and 100")
    trade_constraints = {
        "horizon": trading_horizon,
        "entry_condition": entry_condition.strip(),
        "stop_loss_condition": stop_loss_condition.strip(),
        "take_profit_condition": take_profit_condition.strip(),
        "max_position_pct": max_position_pct,
    }
    analyst_list = [a.strip() for a in analysts.split(",") if a.strip()]
    resolved_url = backend_url or PROVIDER_URLS.get(provider)
    asset_type = detect_asset_type(ticker)
    # CLI filters fundamentals for crypto — mirror that on the web path
    # so the agent graph isn't asked to run a tool that has no data source.
    if asset_type == "crypto":
        analyst_list = [a for a in analyst_list if a != "fundamentals"]

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop_event = threading.Event()  # set when client disconnects → stop streaming

    def put(data: dict):
        if not stop_event.is_set():
            asyncio.run_coroutine_threadsafe(queue.put(_safe_web_event(data)), loop)

    def run():
        try:
            base_market_context = resolve_market_context(ticker)
            identity = resolve_instrument_identity(base_market_context.symbol)
            market_context = enrich_market_context(base_market_context, identity)
            canonical_ticker = market_context.symbol
            put({"type": "init", "agents": build_agent_sequence(analyst_list),
                 "ticker": canonical_ticker, "date": date, "asset_type": asset_type,
                 "market": market_context.market})

            config = DEFAULT_CONFIG.copy()
            config.update({
                "llm_provider":            provider,
                "deep_think_llm":          deep_model,
                "quick_think_llm":         quick_model,
                "backend_url":             resolved_url,
                "max_debate_rounds":       research_depth,
                "max_risk_discuss_rounds": research_depth,
                "output_language":         output_language,
                "checkpoint_enabled":      checkpoint,
            })
            if temperature is not None:
                config["temperature"] = temperature
            # Provider-specific reasoning knob. ``effort`` is a single
            # string; route it to the matching config key so untouched
            # providers stay at their defaults.
            effort_key = PROVIDER_EFFORT_KIND.get(provider)
            if effort and effort_key:
                config[effort_key] = effort

            ta = TradingAgentsGraph(selected_analysts=analyst_list, config=config)
            # ``propagate()`` normally sets this; the web path drives the graph
            # manually, so set it here. Without it ``_log_state`` calls
            # ``safe_ticker_component(None)`` → ValueError, the JSON state log is
            # never written, and the run never shows up in /api/history.
            ta.ticker = canonical_ticker
            ta._resolve_pending_entries(canonical_ticker)

            past_ctx = ta.memory_log.get_past_context(canonical_ticker)
            # Mirror the CLI: resolve instrument identity once at start so
            # every agent anchors to the real company, not just the raw
            # ticker (graph/trading_graph.py:resolve_instrument_context).
            instrument_ctx = build_instrument_context(canonical_ticker, asset_type, identity)
            # The graph's programmatic ``propagate`` path collects this itself.
            # The web path constructs state directly, so mirror that behavior.
            from tradingagents.dataflows.japan.context import collect_japan_data_bundle

            japan_data_bundle = collect_japan_data_bundle(
                market_context, date, trading_horizon=trading_horizon
            )
            try:
                verified_market_snapshot = build_verified_market_snapshot(canonical_ticker, date)
            except Exception as exc:
                logger.warning("[VerifiedSnapshot] unavailable ticker=%s class=%s", canonical_ticker, type(exc).__name__)
                verified_market_snapshot = (
                    "VERIFIED_MARKET_SNAPSHOT_UNAVAILABLE: optional diagnostic unavailable; "
                    "use valid Market Analyst market-tool results."
                )
            init_state = ta.propagator.create_initial_state(
                canonical_ticker, date,
                asset_type=asset_type,
                past_context=past_ctx,
                instrument_context=instrument_ctx,
                trade_constraints=trade_constraints,
                market_context=market_context,
                japan_data_bundle=japan_data_bundle,
                verified_market_snapshot=verified_market_snapshot,
                run_manifest=build_run_manifest(
                    analysis_as_of=date,
                    market_context=market_context.to_dict(),
                    config=config,
                ),
            )
            graph_args = ta.propagator.get_graph_args()
            graph_args["stream_mode"] = "updates"

            # Seed final_state with init fields; add defaults for fields set only by nodes
            final_state: dict = dict(init_state)
            final_state.setdefault("investment_plan", "")
            final_state.setdefault("trader_investment_plan", "")
            final_state.setdefault("final_trade_decision", "")
            done_agents: set = set()

            for chunk in ta.graph.stream(init_state, **graph_args):
                if stop_event.is_set():
                    logger.info("Client disconnected — stopping analysis for %s", ticker)
                    return

                for node_name, updates in chunk.items():
                    if node_name.startswith("tools_") or node_name.startswith("Msg Clear"):
                        continue

                    # Mark agent in_progress on first fire
                    if node_name in AGENT_INFO:
                        info = AGENT_INFO[node_name]
                        if info["display"] not in done_agents:
                            put({"type": "agent_update", "id": info["display"],
                                 "team": info["team"], "status": "in_progress"})

                    # Merge updates into final_state
                    for k, v in updates.items():
                        if k in ("investment_debate_state", "risk_debate_state") \
                                and isinstance(final_state.get(k), dict):
                            final_state[k].update(v or {})
                        elif k == "messages":
                            final_state.setdefault("messages", [])
                            final_state["messages"].extend(v if isinstance(v, list) else [v])
                        else:
                            final_state[k] = v

                    # ── Analyst reports: trigger only from this chunk's updates ──
                    for report_key, display_id in [
                        ("market_report",       "Market Analyst"),
                        ("sentiment_report",     "Sentiment Analyst"),
                        ("news_report",          "News Analyst"),
                        ("fundamentals_report",  "Fundamentals Analyst"),
                    ]:
                        if updates.get(report_key) and display_id not in done_agents:
                            done_agents.add(display_id)
                            put({"type": "agent_update", "id": display_id, "status": "completed"})

                    # ── Research debate ──────────────────────────────────────
                    debate = updates.get("investment_debate_state") or {}

                    if debate.get("bull_history") and "Bull Researcher" not in done_agents:
                        done_agents.add("Bull Researcher")
                        put({"type": "agent_update", "id": "Bull Researcher", "status": "completed"})

                    if debate.get("bear_history") and "Bear Researcher" not in done_agents:
                        done_agents.add("Bear Researcher")
                        put({"type": "agent_update", "id": "Bear Researcher", "status": "completed"})

                    judge = debate.get("judge_decision") or final_state["investment_debate_state"].get("judge_decision", "")
                    if judge and "Research Manager" not in done_agents:
                        done_agents.add("Research Manager")
                        put({"type": "agent_update", "id": "Research Manager", "status": "completed"})

                    # ── Trader ───────────────────────────────────────────────
                    if updates.get("trader_investment_plan") and "Trader" not in done_agents:
                        done_agents.add("Trader")
                        put({"type": "agent_update", "id": "Trader", "status": "completed"})

                    # ── Risk team — count comes from merged state ─────────────
                    risk_count = final_state["risk_debate_state"].get("count", 0)
                    if risk_count >= 1 and "Aggressive Analyst" not in done_agents:
                        done_agents.add("Aggressive Analyst")
                        put({"type": "agent_update", "id": "Aggressive Analyst", "status": "completed"})
                    if risk_count >= 2 and "Conservative Analyst" not in done_agents:
                        done_agents.add("Conservative Analyst")
                        put({"type": "agent_update", "id": "Conservative Analyst", "status": "completed"})
                    if risk_count >= 3 and "Neutral Analyst" not in done_agents:
                        done_agents.add("Neutral Analyst")
                        put({"type": "agent_update", "id": "Neutral Analyst", "status": "completed"})

                    # ── Final decision ───────────────────────────────────────
                    if updates.get("final_trade_decision") and "Portfolio Manager" not in done_agents:
                        done_agents.add("Portfolio Manager")
                        put({"type": "agent_update", "id": "Portfolio Manager", "status": "completed"})

            # Persist results.  The web path drives the graph directly, so it
            # must establish the same canonical final state as CLI/API runs
            # before any persisted or user-facing final artifact is created.
            final_state = build_canonical_final_state(final_state)
            if (final_state.get("final_output_contract") or {}).get("status") != "FINALIZED":
                try:
                    ta._log_state(date, final_state)
                except Exception as log_exc:
                    logger.warning(
                        "Blocked state logging failed (non-fatal): %s",
                        safe_exception_text(log_exc),
                    )
                put(
                    {
                        "type": "error",
                        "message": "最终报告未通过证据与执行一致性校验。",
                    }
                )
                return
            # Agent text remains provisional until the complete artifact has
            # passed the final contract. Publish only the accepted variants to
            # the user-facing stream; raw prose remains in full_agent_log.md.
            for report_key in (
                "market_report",
                "sentiment_report",
                "news_report",
                "fundamentals_report",
            ):
                if final_state.get(report_key):
                    put(
                        {
                            "type": "section",
                            "key": report_key,
                            "content": final_state[report_key],
                        }
                    )
            research = final_state.get("investment_debate_state") or {}
            if research.get("judge_decision"):
                put(
                    {
                        "type": "section",
                        "key": "research_decision",
                        "content": research["judge_decision"],
                    }
                )
            if final_state.get("trader_investment_plan"):
                put(
                    {
                        "type": "section",
                        "key": "trader_plan",
                        "content": final_state["trader_investment_plan"],
                    }
                )
            if final_state.get("final_trade_decision"):
                put({"type": "final", "content": final_state["final_trade_decision"]})
            try:
                ta._log_state(date, final_state)
            except Exception as log_exc:
                logger.warning(
                    "State logging failed (non-fatal): %s", safe_exception_text(log_exc)
                )

            report_path = None
            try:
                report_path = ta.save_reports(final_state, canonical_ticker)
            except Exception as report_exc:
                logger.warning(
                    "Report archive failed (non-fatal): %s",
                    safe_exception_text(report_exc),
                )

            if final_state.get("final_trade_decision"):
                try:
                    ta.memory_log.store_decision(
                        ticker=ticker, trade_date=date,
                        final_trade_decision=final_state["final_trade_decision"],
                    )
                except Exception as mem_exc:
                    logger.warning(
                        "Memory store failed (non-fatal): %s",
                        safe_exception_text(mem_exc),
                    )

            put({"type": "done", "report_path": str(report_path) if report_path else None})

        except Exception as exc:
            safe_error = safe_exception_text(exc)
            logger.exception("Analysis failed: %s", safe_error)
            put({"type": "error", "message": safe_error})

    loop.run_in_executor(executor, run)

    async def generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=60)
                except asyncio.TimeoutError:
                    yield {"data": json.dumps({"type": "ping"})}
                    continue
                yield {"data": json.dumps(event, ensure_ascii=False)}
                if event["type"] in ("done", "error"):
                    break
        finally:
            stop_event.set()  # always signal thread to stop on generator exit

    return EventSourceResponse(generator())


# ── History & report export ──────────────────────────────────────────
# Past runs are persisted by ``TradingAgentsGraph._log_state`` as
# ``{results_dir}/{ticker}/TradingAgentsStrategy_logs/full_states_log_{date}.json``.
# These endpoints expose that archive to the UI: a list of (date, ticker,
# decision) and a print-optimized HTML report that the browser turns into a PDF.

RESULTS_DIR = Path(DEFAULT_CONFIG["results_dir"])
LOG_SUBDIR = "TradingAgentsStrategy_logs"
LOG_PREFIX = "full_states_log_"

# Section order + Chinese labels for the rendered report. Each tuple is
# (json_key, markdown_file_stem, label). Two storage layouts feed the same
# sections: the web path writes a single JSON (``full_states_log_*.json``,
# keyed by ``json_key``), while CLI runs write one markdown file per section
# under ``{ticker}/{date}/reports/{stem}.md``. The trader section is the only
# place the two layouts disagree on naming.
REPORT_SECTIONS = [
    ("market_report",              "market_report",          "📊 市场分析"),
    ("sentiment_report",           "sentiment_report",       "💬 情绪分析"),
    ("news_report",                "news_report",            "📰 新闻分析"),
    ("fundamentals_report",        "fundamentals_report",    "📋 基本面分析"),
    ("investment_plan",            "investment_plan",        "🔬 研究决策"),
    ("trader_investment_decision", "trader_investment_plan", "📈 交易计划"),
    ("final_trade_decision",       "final_trade_decision",   "🎯 最终决策"),
]

# The Portfolio Manager's 5-tier rating, collapsed to the three badge buckets
# the history list renders: Buy/Overweight are bullish, Underweight/Sell
# bearish, Hold neutral.
_RATING_TO_ACTION = {
    "Buy":         "buy",
    "Overweight":  "buy",
    "Hold":        "hold",
    "Underweight": "sell",
    "Sell":        "sell",
}


def _derive_action(decision: str) -> str:
    """BUY/SELL/HOLD badge derived from the final decision's 5-tier rating.

    Delegates to the canonical ``parse_rating`` (which reads the Portfolio
    Manager's ``**Rating**: X`` header) rather than scanning the whole essay for
    keywords. The decision text argues the buy, sell *and* hold cases regardless
    of the verdict, so a first-match keyword scan systematically mislabels the
    call — almost always as "buy", since some bullish phrase appears somewhere.
    Returns "" when the text carries no recognisable rating.
    """
    if not (decision or "").strip():
        return ""
    return _RATING_TO_ACTION.get(parse_rating(decision, default=""), "")


def _md(text: str) -> str:
    return render_markdown_fragment(text)


def _accepted_portfolio_text(accepted_report: str) -> str:
    """Return the final published decision section from the accepted artifact."""
    positions = [
        accepted_report.rfind(marker)
        for marker in ("### 投资组合经理", "### Portfolio Manager")
    ]
    start = max(positions)
    return accepted_report[start:] if start >= 0 else accepted_report


def _json_path(dir_name: str, date: str) -> Path:
    return RESULTS_DIR / dir_name / LOG_SUBDIR / f"{LOG_PREFIX}{date}.json"


def _reports_dir(dir_name: str, date: str) -> Path:
    return RESULTS_DIR / dir_name / date / "reports"


def _run_mtime(dir_name: str, date: str) -> float:
    """When the run was actually written (epoch secs).

    ``trade_date`` is only a calendar date, so the wall-clock time of a run —
    needed to sort same-day analyses and show 时分 — comes from the file
    modification time: the JSON state log, or the newest section markdown.
    """
    jp = _json_path(dir_name, date)
    if jp.is_file():
        return jp.stat().st_mtime
    rdir = _reports_dir(dir_name, date)
    if rdir.is_dir():
        mtimes = [f.stat().st_mtime for f in rdir.glob("*.md")]
        if mtimes:
            return max(mtimes)
        return rdir.stat().st_mtime
    return 0.0


def _load_run(dir_name: str, date: str) -> dict | None:
    """Load one archived run from whichever layout exists.

    Prefers the JSON state log (richer — includes the bull/bear/risk debate),
    falling back to the per-section markdown files written by CLI runs.
    Returns a dict shaped like the JSON state log, or ``None`` if neither
    layout holds anything.
    """
    json_path = _json_path(dir_name, date)
    if json_path.is_file():
        try:
            return json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(
                "Skipping unreadable log %s: %s", json_path, safe_exception_text(exc)
            )

    rdir = _reports_dir(dir_name, date)
    if rdir.is_dir():
        data: dict = {"company_of_interest": dir_name, "trade_date": date}
        found = False
        for json_key, stem, _label in REPORT_SECTIONS:
            f = rdir / f"{stem}.md"
            if f.is_file():
                data[json_key] = f.read_text(encoding="utf-8")
                found = True
        return data if found else None
    return None


@app.get("/api/history")
def list_history():
    """List every archived analysis (both storage layouts), newest first."""
    # Keyed by (dir, date) so a ticker analyzed via both CLI and web shows once.
    runs: dict = {}
    if RESULTS_DIR.exists():
        for ticker_dir in RESULTS_DIR.iterdir():
            if not ticker_dir.is_dir():
                continue
            # JSON layout: {ticker}/TradingAgentsStrategy_logs/full_states_log_{date}.json
            log_dir = ticker_dir / LOG_SUBDIR
            if log_dir.is_dir():
                for log_file in log_dir.glob(f"{LOG_PREFIX}*.json"):
                    runs[(ticker_dir.name, log_file.stem[len(LOG_PREFIX):])] = True
            # Markdown layout: {ticker}/{date}/reports/*.md
            for date_dir in ticker_dir.iterdir():
                if date_dir.is_dir() and (date_dir / "reports").is_dir():
                    runs[(ticker_dir.name, date_dir.name)] = True

    entries = []
    for (dir_name, date) in runs:
        data = _load_run(dir_name, date)
        if not data:
            continue
        decision = data.get("final_trade_decision", "") or ""
        mtime = _run_mtime(dir_name, date)
        entries.append({
            "ticker":   data.get("company_of_interest") or dir_name,
            # ``dir`` is the on-disk folder name used to build report URLs.
            "dir":      dir_name,
            "date":     data.get("trade_date") or date,
            # Run wall-clock time, to the minute, plus a raw epoch for sorting.
            "datetime": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "",
            "mtime":    mtime,
            "action":   _derive_action(decision),
            "has_report": bool(decision or data.get("market_report")),
        })
    # Newest run first; the front-end allows re-sorting by any column.
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return {"history": entries}


def _render_report_html(data: dict, *, auto_print: bool) -> str:
    require_canonical_final_state(data)
    data = sanitize_data(data)
    metadata = canonical_report_metadata(data)
    ticker = metadata["symbol"]
    date = data.get("trade_date", "")
    accepted_report = data.get("accepted_report_markdown")
    if metadata["market"] == "JP":
        if not isinstance(accepted_report, str) or not accepted_report.strip():
            raise ValueError("Canonical Japan report artifact is unavailable")
        decision = _accepted_portfolio_text(accepted_report)
    else:
        decision = data.get("final_trade_decision", "") or ""

    def decision_field(label: str, default: str) -> str:
        match = re.search(
            rf"(?:\*\*)?{label}(?:\*\*)?\s*[:：]\s*([^\n*]+)",
            decision, flags=re.IGNORECASE,
        )
        return match.group(1).strip().strip("*") if match else default

    rating = decision_field("Rating", "暂无评级")
    rating = {"Buy": "买入", "Overweight": "增持", "Hold": "持有", "Underweight": "减持", "Sell": "卖出"}.get(rating, rating)
    execution_allowed = bool(
        (data.get("final_output_contract") or {}).get("execution_allowed")
    )
    target = decision_field("Price Target", "—") if execution_allowed else "—"
    horizon = decision_field("Time Horizon", "—")
    summary_match = re.search(
        r"(?:\*\*)?Executive Summary(?:\*\*)?\s*[:：]\s*(.*?)(?=\n\s*\n(?:\*\*)?[A-Za-z ]+(?:\*\*)?\s*[:：]|\Z)",
        decision, flags=re.IGNORECASE | re.DOTALL,
    )
    summary = summary_match.group(1).strip() if summary_match else decision[:1200]

    if metadata["market"] == "JP":
        sections_html = (
            '<section class="report-section canonical-report">'
            + _md(sanitize_text(accepted_report))
            + "</section>"
        )
    else:
        blocks = []
        for json_key, _stem, label in REPORT_SECTIONS:
            content = data.get(json_key, "")
            if not content:
                continue
            blocks.append(
                f'<section class="report-section"><h2>{html.escape(label)}</h2>'
                f"{_md(content)}</section>"
            )
        sections_html = "\n".join(blocks) or "<p>该记录暂无报告内容。</p>"
    auto = "<script>window.addEventListener('load',()=>window.print())</script>" if auto_print else ""

    generated = (
        (data.get("run_manifest") or {}).get("runtime_timestamp_jst")
        if metadata["market"] == "JP"
        else datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    document = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{html.escape(ticker)} {html.escape(str(date))} 分析报告</title>
<style>
  :root {{ --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb; --brand:#4F46E5; }}
  * {{ box-sizing:border-box; }}
  body {{
    font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",
                -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    color:var(--ink); line-height:1.7; margin:0;
    background:#f3f4f6; -webkit-print-color-adjust:exact; print-color-adjust:exact;
  }}
  .page {{ max-width:820px; margin:0 auto; padding:48px 56px; background:#fff;
           min-height:100vh; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  .report-head {{ border-bottom:3px solid var(--brand); padding-bottom:16px; margin-bottom:28px; }}
  .report-head h1 {{ font-size:26px; margin:0 0 6px; }}
  .report-head .meta {{ color:var(--muted); font-size:14px; }}
  .toolbar {{ margin-bottom:20px; }}
  .toolbar button {{ font:inherit; font-size:14px; padding:8px 16px; border:none;
    border-radius:8px; background:var(--brand); color:#fff; cursor:pointer; }}
  .report-cover {{ min-height:calc(100vh - 96px); padding-top:24px; }}
  .cover-brand {{ color:#1e3a6a; font:700 15px/1.2 Arial,sans-serif; letter-spacing:2px;
    padding-bottom:20px; border-bottom:3px solid #1e3a6a; }}
  .cover-brand span {{ color:var(--muted); font:600 14px "PingFang SC","Microsoft YaHei",sans-serif;
    letter-spacing:0; margin-left:18px; padding-left:18px; border-left:1px solid var(--line); }}
  .cover-title {{ margin:26px 0 8px; font-size:29px; }}
  .cover-title span {{ font-size:19px; font-weight:400; color:#4b5563; margin-left:10px; }}
  .cover-meta {{ color:var(--muted); font-size:14px; }}
  .cover-cards {{ display:grid; grid-template-columns:repeat(4,1fr); margin:28px 0 38px;
    border:1px solid #cbd5e1; }}
  .cover-card {{ min-height:88px; padding:14px 16px; background:#fafbfd; border-right:1px solid #cbd5e1; }}
  .cover-card:last-child {{ border-right:0; }}
  .cover-card small {{ display:block; color:var(--muted); font-size:14px; margin-bottom:7px; }}
  .cover-card strong {{ font:700 20px/1.2 Arial,"PingFang SC","Microsoft YaHei",sans-serif; }}
  .cover-card .rating {{ color:#bf3b36; }}
  .cover-advice {{ background:#f4f6f9; border:1px solid #cbd5e1; border-top:3px solid #1e3a6a;
    padding:20px 24px 24px; }}
  .cover-advice h2 {{ font-size:20px; margin:0 0 14px; padding:0 0 10px 10px;
    border-left:4px solid #1e3a6a; border-bottom:1px solid #cbd5e1; }}
  .cover-advice p {{ margin:0 0 8px; font-size:16px; line-height:1.8; }}
  .report-section {{ margin:0 0 28px; }}
  .report-section h2 {{ font-size:18px; border-left:4px solid var(--brand);
    padding-left:10px; margin:28px 0 12px; }}
  .debate-divider h2 {{ color:var(--muted); border-left-color:var(--muted); }}
  .report-section h3 {{ font-size:15px; margin:16px 0 8px; }}
  table {{ border-collapse:collapse; width:100%; margin:12px 0; font-size:13.5px; }}
  th,td {{ border:1px solid var(--line); padding:7px 10px; text-align:left; }}
  th {{ background:#f9fafb; }}
  code {{ background:#f3f4f6; padding:1px 5px; border-radius:4px; font-size:13px; }}
  pre {{ background:#f9fafb; padding:12px; border-radius:8px; overflow:auto; }}
  blockquote {{ border-left:3px solid var(--line); margin:12px 0; padding:4px 14px; color:var(--muted); }}
  @page {{ size:auto; margin:0; }}
  @media print {{
    body {{ background:#fff; }}
    .page {{ box-shadow:none; max-width:none; padding:12mm; }}
    .toolbar {{ display:none; }}
    .report-cover {{ min-height:0; break-after:page; page-break-after:always; }}
    .report-section {{ break-inside:avoid-page; }}
  }}
</style>
{auto}
</head>
<body>
<div class="page">
  <div class="toolbar"><button onclick="window.print()">🖨️ 打印 / 保存为 PDF</button></div>
  <section class="report-cover">
    <div class="cover-brand">TRADINGAGENTS 研究<span>证券研究报告</span></div>
    <h1 class="cover-title">{html.escape(ticker)} <span>多智能体分析报告</span></h1>
    <div class="cover-meta">分析日期 {html.escape(str(date))} ｜ 生成于 {html.escape(str(generated or date))}</div>
    <div class="cover-cards">
      <div class="cover-card"><small>投资评级</small><strong class="rating">{html.escape(rating)}</strong></div>
      <div class="cover-card"><small>目标价</small><strong>{html.escape(target)}</strong></div>
      <div class="cover-card"><small>投资期限</small><strong>{html.escape(horizon)}</strong></div>
      <div class="cover-card"><small>分析师覆盖</small><strong>{sum(bool(data.get(key)) for key, _, _ in REPORT_SECTIONS[:4])} 项</strong></div>
    </div>
    <div class="cover-advice"><h2>投资建议</h2>{_md(summary)}</div>
  </section>
  {sections_html}
</div>
</body>
</html>"""
    rendered_issues = validate_rendered_html(document)
    if rendered_issues:
        raise ValueError(
            "Rendered report failed structural validation: "
            + ", ".join(rendered_issues)
        )
    return document


@app.get("/api/report/{ticker}/{date}", response_class=HTMLResponse)
def get_report(ticker: str, date: str, print: bool = False):
    """Print-optimized HTML report for one archived run.

    ``print=1`` makes the page call ``window.print()`` on load so the
    front-end "下载 PDF" button lands the user straight in the save-as-PDF
    dialog (the browser handles CJK fonts natively).
    """
    try:
        safe_ticker_component(ticker)  # reject path-traversal in the dir name
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid ticker") from exc
    data = _load_run(ticker, date)
    if not data:
        raise HTTPException(status_code=404, detail="report not found")
    # Historical archives may predate the final-output contract. Establish the
    # accepted state at the controller boundary; the HTML renderer only displays it.
    data = build_canonical_final_state(data)
    return HTMLResponse(_render_report_html(data, auto_print=print))


# The checked-in Vite build makes the web UI runnable without a local Node.js
# installation.  Mount it after API routes so /api/* always reaches FastAPI.
FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
