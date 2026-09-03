"""Sentiment analyst — multi-source sentiment analysis for a target ticker.

Previously named ``social_media_analyst``. Renamed and redesigned because
the old version had a prompt that demanded social-media analysis but the
only tool available was Yahoo Finance news — which led LLMs to fabricate
Reddit/X/StockTwits content under prompt pressure (verified live).

The redesigned agent pre-fetches three complementary data sources before
the LLM is invoked and injects them into the prompt as structured blocks:

  1. News headlines     — Yahoo Finance (institutional framing)
  2. StockTwits messages — retail-trader posts indexed by cashtag, with
                           user-labeled Bullish/Bearish sentiment tags
  3. Reddit posts        — r/wallstreetbets, r/stocks, r/investing

The agent does not use tool-calling; the data is in the prompt from
turn 0. Output uses the structured-output pattern (json_schema for
OpenAI/xAI, response_schema for Gemini, tool-use for Anthropic), falling
back to free-text generation for providers that lack native support, so
the sentiment header (band + score + confidence) is deterministic across
runs and providers instead of free-form per-model prose.

See: https://github.com/TauricResearch/TradingAgents/issues/557
See: https://github.com/TauricResearch/TradingAgents/issues/796
"""

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.schemas import SentimentReport, render_sentiment_report
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_japan_sentiment_context_from_state,
    get_language_instruction,
    get_news,
)
from tradingagents.agents.utils.structured import (
    NO_EXTERNAL_TOOLS,
    bind_structured,
    invoke_structured_or_freetext,
)
from tradingagents.dataflows.reddit import fetch_reddit_posts
from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages


def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def create_sentiment_analyst(llm):
    """Create a sentiment analyst node for the trading graph.

    Pre-fetches news + StockTwits + Reddit data, injects them into the
    prompt as structured blocks, and produces a deterministic sentiment
    report via structured output (with a free-text fallback for providers
    that do not support it).
    """
    structured_llm = bind_structured(llm, SentimentReport, "Sentiment Analyst")

    def sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        end_date = state["trade_date"]
        start_date = _seven_days_back(end_date)
        instrument_context = get_instrument_context_from_state(state)
        japan_data_context = get_japan_sentiment_context_from_state(state)
        social_authority = _japan_social_authority(state)

        # Keep the original sentiment preload for every market. Japan
        # sentiment is supplemental context, not a replacement for the
        # existing Yahoo/StockTwits/Reddit path.
        news_block = get_news.func(ticker, start_date, end_date)
        is_japan = (state.get("market_context") or {}).get("market") == "JP"
        if is_japan:
            stocktwits_block = fetch_stocktwits_messages(
                ticker, limit=30, start_date=start_date, end_date=end_date
            )
            reddit_block = fetch_reddit_posts(
                ticker, start_date=start_date, end_date=end_date
            )
        else:
            # Preserve the original US path byte-for-byte.
            stocktwits_block = fetch_stocktwits_messages(ticker, limit=30)
            reddit_block = fetch_reddit_posts(ticker)
        system_message = _build_system_message(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            news_block=news_block,
            stocktwits_block=stocktwits_block,
            reddit_block=reddit_block,
        )
        if is_japan:
            system_message += "\n\n" + _build_japan_system_message(
                ticker, start_date, end_date, japan_data_context
            )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    # No tool-calling here: the data is pre-fetched into the
                    # prompt, so tool-range wording would only invite a
                    # hallucinated tool call (#1130).
                    " Today's date is {current_date}; treat it as 'now' for all analysis. {instrument_context}"
                    " " + NO_EXTERNAL_TOOLS +
                    "\n{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=end_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        # Format the template into a concrete message list so the structured
        # and free-text paths receive the same input. No bind_tools — the
        # data is already in the prompt.
        formatted_messages = prompt.format_messages(messages=state["messages"])

        if is_japan and not _has_real_social_sample(
            stocktwits_block,
            reddit_block,
            has_japan_sample=social_authority is not None,
        ):
            report_text = (
                "**Overall Sentiment:** **DATA_UNAVAILABLE**\n"
                "**Score:** DATA_UNAVAILABLE\n"
                "**Confidence:** Low\n\n"
                "暂无可用日本情绪数据。社区/投资者样本不可用或为空，"
                "因此不得用新闻、财务、分析师预期、JSF 或宏观数据补成 Neutral、5.0 或方向性分数。"
            )
        else:
            report_text = invoke_structured_or_freetext(
                structured_llm,
                llm,
                formatted_messages,
                render_sentiment_report,
                "Sentiment Analyst",
            )
        report_text = _apply_source_status_integrity(
            report_text,
            reddit_block,
        )
        if is_japan:
            report_text = _apply_japan_sentiment_domain_integrity(
                report_text, social_authority
            )
            if social_authority is None:
                report_text = _apply_low_sample_integrity(
                    report_text, stocktwits_block, reddit_block, japan_data_context
                )

        return {
            "messages": [AIMessage(content=report_text)],
            "sentiment_report": report_text,
        }

    return sentiment_analyst_node


def _apply_source_status_integrity(report_text: str, reddit_block: str) -> str:
    """Add a binding availability note for sources that could not be queried."""
    unavailable = {
        "RATE_LIMITED": "本次因限流不可用",
        "TIMEOUT": "本次请求超时",
        "FETCH_FAILED": "本次获取失败",
    }
    for status, reason in unavailable.items():
        if f"reddit status={status}" in reddit_block:
            return (
                "## 数据源状态（必须遵守）\n"
                f"Reddit：{reason}；帖子数量为未知，情绪与社区热度均无法判断。"
                "不得将此来源视为零提及、低热度、无人关注或任何方向性证据。\n\n"
                + report_text
            )
    return report_text


def _has_real_social_sample(
    stocktwits_block: str,
    reddit_block: str,
    *,
    has_japan_sample: bool = False,
) -> bool:
    stocktwits = stocktwits_block.strip().lower()
    reddit = reddit_block.strip().lower()
    stocktwits_available = bool(stocktwits) and not stocktwits.startswith("<")
    reddit_available = bool(reddit) and not reddit.startswith("<")
    return stocktwits_available or reddit_available or has_japan_sample


def _apply_low_sample_integrity(
    report_text: str,
    stocktwits_block: str,
    reddit_block: str,
    japan_data_context: str,
) -> str:
    counts = []
    for block in (stocktwits_block, reddit_block, japan_data_context):
        for pattern in (
            r"Total:\s*(\d+)",
            r"sample_count[=:]\s*(\d+)",
            r"message_count[=:]\s*(\d+)",
        ):
            counts.extend(int(value) for value in re.findall(pattern, block, re.I))
    if not counts or sum(counts) >= 5:
        return report_text
    bounded = re.sub(
        r"\*\*Confidence:\*\*\s*(?:Medium|High)",
        "**Confidence:** Low",
        report_text,
        flags=re.I,
    )
    return (
        "## Sample quality\nLOW_SAMPLE: fewer than five verified sentiment samples; "
        "confidence is deterministically capped at Low.\n\n"
        + bounded
    )


def _japan_social_authority(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the source-native JP investor/social aggregate, if usable.

    News, macro, filings, consensus and credit data are intentionally absent
    from this calculation.  The provider aggregate is the only authority for
    a JP sentiment direction or score.
    """
    bundle = state.get("japan_data_bundle") or {}
    for item in bundle.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        if item.get("source_type") != "japan_investor_sentiment_aggregate":
            continue
        metadata = item.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        sample_count = metadata.get("sample_count")
        score = metadata.get("sentiment_score")
        if not isinstance(sample_count, int) or sample_count <= 0:
            continue
        if not isinstance(score, (int, float)) or not -1 <= float(score) <= 1:
            continue
        return {
            "source": str(item.get("source") or "Japan investor/social sentiment"),
            "sample_count": sample_count,
            "positive_count": int(metadata.get("positive_count") or 0),
            "neutral_count": int(metadata.get("neutral_count") or 0),
            "negative_count": int(metadata.get("negative_count") or 0),
            "sentiment_score": float(score),
            "confidence": "Low" if sample_count < 5 else metadata.get("confidence", "Low"),
        }
    return None


_SENTIMENT_HEADER = re.compile(
    r"^\s*(?:##\s+Investor/social sentiment authority|"
    r"\*\*(?:Overall Sentiment|Score|Confidence|Sample|Domain boundary):\*\*.*)"
    r"(?:\n|$)",
    re.I | re.M,
)
_ALTERNATE_SENTIMENT_AUTHORITY = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:[-*]\s*)?(?:\*\*)?"
    r"(?:overall[_\s-]*(?:band|score)|overall[_\s-]*sentiment|sentiment[_\s-]*score)"
    r"(?:\*\*)?\s*[:：].*(?:\n|$)",
    re.I | re.M,
)
_NON_SOCIAL_DIRECTION = re.compile(
    r"(?=.*(?:news|macro|headline|新闻|宏观))"
    r"(?=.*(?:sentiment|bullish|bearish|positive|negative|情绪|看多|看空|利多|利空|印证|强化|支持))",
    re.I,
)
_BACKGROUND_ONLY = re.compile(
    r"(?:background\s+only|excluded\s+from|背景(?:信息)?|不计入|不得计入|不参与)",
    re.I,
)
_OVERALL_CONFIDENCE_CLAIM = re.compile(
    r"(?:overall\s+)?confidence|(?:整体|总体|综合)?(?:置信度|信心)", re.I
)
_ALTERNATE_SENTIMENT_SCORE_CLAIM = re.compile(
    r"(?:overall[_\s-]*score|(?:情绪)?得分|情绪评分|sentiment\s+score)"
    r"[^\n]*(?:5\.0|0\s*[–—-]\s*10|映射|mapped)",
    re.I,
)


def _apply_japan_sentiment_domain_integrity(
    report_text: str, authority: Mapping[str, Any] | None
) -> str:
    """Bind JP direction/score to social samples and keep news as background.

    The LLM may summarize news in the narrative, but it cannot use news or
    macro observations to strengthen, corroborate, or otherwise change the
    source-native investor/social score and band.
    """
    if authority is None:
        return report_text

    score = float(authority["sentiment_score"])
    sample_count = int(authority["sample_count"])
    if sample_count < 5:
        if score > 0.1:
            band = "Weak Positive Observation (LOW_SAMPLE)"
        elif score < -0.1:
            band = "Weak Negative Observation (LOW_SAMPLE)"
        else:
            band = "Mixed Observation (LOW_SAMPLE)"
        confidence = "Low"
    else:
        band = _social_band(score)
        confidence = _social_confidence(authority.get("confidence"))

    without_llm_header = _SENTIMENT_HEADER.sub("", report_text)
    without_llm_header = _ALTERNATE_SENTIMENT_AUTHORITY.sub(
        "", without_llm_header
    ).strip()
    cleaned_lines = []
    for line in without_llm_header.splitlines():
        # The source-native authority above owns the run-level confidence.
        # Retaining a second LLM-authored High/Low statement created two
        # mutually incompatible confidence labels in one report.
        if _OVERALL_CONFIDENCE_CLAIM.search(line):
            continue
        if _ALTERNATE_SENTIMENT_SCORE_CLAIM.search(line):
            continue
        cleaned_lines.append(_remove_non_social_directional_claims(line))
    narrative = "\n".join(line for line in cleaned_lines if line.strip()).strip()
    header = (
        "## Investor/social sentiment authority\n"
        f"**Overall Sentiment:** **{band}**\n"
        f"**Score:** {score:.4g} (source-native signed scale)\n"
        f"**Confidence:** {confidence}\n"
        f"**Sample:** n={sample_count}; positive={authority['positive_count']}; "
        f"neutral={authority['neutral_count']}; negative={authority['negative_count']}\n"
        "**Domain boundary:** News and macro observations are background only; "
        "they are excluded from this score and band."
    )
    return header + ("\n\n" + narrative if narrative else "")


def _remove_non_social_directional_claims(line: str) -> str:
    parts = re.split(r"(?<=[。！？；;])", line)
    kept = []
    for part in parts:
        if _NON_SOCIAL_DIRECTION.search(part) and not _BACKGROUND_ONLY.search(part):
            continue
        kept.append(part)
    return "".join(kept)


def _social_band(score: float) -> str:
    if score >= 0.5:
        return "Bullish"
    if score >= 0.1:
        return "Mildly Bullish"
    if score <= -0.5:
        return "Bearish"
    if score <= -0.1:
        return "Mildly Bearish"
    return "Mixed"


def _social_confidence(value: Any) -> str:
    if isinstance(value, (int, float)):
        if float(value) < 0.45:
            return "Low"
        if float(value) < 0.7:
            return "Medium"
        return "High"
    label = str(value or "Low").strip().lower()
    return {"low": "Low", "medium": "Medium", "high": "High"}.get(label, "Low")


def _build_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    news_block: str,
    stocktwits_block: str,
    reddit_block: str,
) -> str:
    """Assemble the sentiment-analyst system message with structured data blocks."""
    return f"""You are a financial market sentiment analyst. Your task is to produce a comprehensive sentiment report for {ticker} covering the period from {start_date} to {end_date}, drawing on three complementary data sources that have already been collected for you.

## Data sources (pre-fetched, in this prompt)

### News headlines — Yahoo Finance, past 7 days
Institutional framing. Fact-driven, slower-moving signal.

<start_of_news>
{news_block}
<end_of_news>

### StockTwits messages — retail-trader social platform indexed by cashtag
Fast-moving signal. Each message carries a user-labeled sentiment tag (Bullish / Bearish / no-label) plus the message body.

<start_of_stocktwits>
{stocktwits_block}
<end_of_stocktwits>

### Reddit posts — r/wallstreetbets, r/stocks, r/investing (past 7 days)
Community discussion. Engagement signal via upvote score and comment count. Subreddit character matters (r/wallstreetbets is often contrarian/exuberant; r/stocks more measured; r/investing longer-term).

<start_of_reddit>
{reddit_block}
<end_of_reddit>

## How to analyze this data (best practices)

1. **Read the StockTwits Bullish/Bearish ratio as a leading retail-sentiment signal.** A 70/30 bullish/bearish split is moderately bullish; ≥90/10 may indicate over-extension and contrarian risk; 50/50 is uncertainty. Sample size matters — base rates on the actual message count, not percentages alone.

2. **Look for cross-source divergences.** If news framing is bearish but StockTwits is overwhelmingly bullish, that mismatch is itself a signal — it can mean retail is leaning into a thesis the news flow hasn't caught up to (or vice versa, that retail is chasing while institutions are cautious).

3. **Weight Reddit posts by engagement.** A 400-upvote / 200-comment thread reflects community attention; a 3-upvote post is noise. Read the body excerpts for context — the title alone often misleads.

4. **Distinguish opinion from event.** A news headline ("Nvidia announces $500M Corning deal") is an event; a StockTwits post ("buying NVDA, this is going to moon") is opinion. Both are inputs but should be weighted differently in your conclusions.

5. **Identify recurring narrative themes.** What topic keeps coming up across sources? That's the dominant narrative driving current sentiment.

6. **Be honest about data limits.** If StockTwits returned only a handful of messages, or one or more sources returned an "<unavailable>" placeholder, the sentiment read is less robust — flag this explicitly in the `confidence` field and the narrative. If the sources are silent on a given subreddit, say so.

7. **Critical status rule:** a Reddit block marked RATE_LIMITED or FETCH_FAILED
   has an UNKNOWN count, not zero. Do not describe it as zero mentions, low
   attention, lack of FOMO, no retail participation, or a directional signal.
   It may only lower confidence. Only an explicit successful no-posts block
   supports a zero-post statement.

8. **Identify catalysts and risks** that emerge across sources — news of upcoming earnings, product launches, competitive threats, macro headlines, etc.

9. **Past sentiment is not predictive.** Frame your conclusions as signal for the trader to weigh alongside fundamentals and technicals, not as a price call.

## Output fields

Fill the following fields:

- **overall_band**: Exactly one of Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish. Use Mixed when sources point in clearly different directions; Neutral only when all sources are genuinely silent.
- **overall_score**: A number from 0 (maximally bearish) to 10 (maximally bullish); 5 is neutral. Keep it consistent with overall_band.
- **confidence**: low / medium / high, based on data quality and sample size.
- **narrative**: Full source-by-source breakdown, divergences, dominant narrative themes, catalysts and risks, and a markdown summary table of key sentiment signals (direction, source, supporting evidence).

{get_language_instruction()}"""


def _build_japan_system_message(ticker: str, start_date: str, end_date: str, japan_data_context: str) -> str:
    """Build supplemental JP context without replacing the original sources."""
    return f"""Supplemental Japanese-market context for {ticker}, covering {start_date} to {end_date}.

The StockTwits, Reddit and verified Japan investor/social samples remain active for this ticker. Yahoo/news and macro blocks are background only: they must not change, corroborate, strengthen, or weaken the sentiment score or overall band. The data below is supplemental Japanese context, not automatically a community-sentiment sample. Do not fabricate Japanese community metrics. If no Japanese community data is present, set confidence to low and state exactly: 暂无可用日本情绪数据。

{japan_data_context}

Use official disclosures only as facts, distinguish them from AI inference, and explain whether the available financing/short-position data creates a risk or merely a limitation. Do not use a single supply-demand observation as a price prediction.

## Output fields
- **overall_band**: Exactly one of Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish only when real sentiment samples exist. If no real sample exists, the caller emits DATA_UNAVAILABLE without invoking this schema.
- **overall_score**: 0 to 10 only when real samples support a score. Never synthesize 5.0 or another neutral score from source unavailability.
- **confidence**: low / medium / high. It must be low without direct Japanese community data.
- **narrative**: Separate VERIFIED FACT, MARKET SENTIMENT, and AI INFERENCE. Include a markdown table of data availability and constraints. When no direct Japanese sample exists, include exactly “暂无可用日本情绪数据”。

{get_language_instruction()}"""


# ---------------------------------------------------------------------------
# Backwards-compatibility shim
# ---------------------------------------------------------------------------
def create_social_media_analyst(llm):
    """Deprecated alias for :func:`create_sentiment_analyst`.

    Kept so existing code that imports ``create_social_media_analyst``
    continues to work.

    .. deprecated::
        Import :func:`create_sentiment_analyst` directly instead.
    """
    import warnings
    warnings.warn(
        "create_social_media_analyst is deprecated and will be removed in a "
        "future version. Use create_sentiment_analyst instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_sentiment_analyst(llm)
