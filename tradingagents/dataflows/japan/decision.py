"""Transparent, provider-free decision context for Japan final-decision nodes."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_OFFICIAL = {"TDnet", "Company IR", "J-Quants", "EDINET", "JPX", "JSF"}
_CORE_WEIGHTS = {
    "technical": 2.0,
    "fundamentals": 3.0,
    "official_catalysts": 3.0,
    "supply_demand": 2.0,
    "analyst_expectations": 2.0,
    "macro_cross_market": 1.0,
    "news": 1.0,
    "sentiment": 0.25,
}


def build_japan_decision_context(bundle: Mapping[str, Any] | None, snapshot: str = "") -> dict[str, Any]:
    """Summarize normalized evidence without fetching, scoring, or deriving prices.

    It intentionally reports an unavailable dimension as unavailable rather
    than negative.  The small weighted direction tally is a consistency aid,
    not a trading score and never chooses an action itself.
    """
    bundle = bundle or {}
    items = [item for item in bundle.get("items", []) if isinstance(item, Mapping)]
    freshness = _freshness_index(items)
    official = _fresh_items(
        [item for item in items if item.get("source") in _OFFICIAL], freshness
    )
    official_events = _dedupe_official_events(official)
    analyst = _fresh_items(
        [item for item in items if item.get("source_type") == "japan_analyst_expectations"], freshness
    )
    supply = _fresh_items(
        [item for item in items if item.get("source") in {"JSF", "JPX"}], freshness
    )
    news = _fresh_items(
        [item for item in items if item.get("source_type") == "japan_stock_news"], freshness
    )
    macro = _fresh_items(
        [item for item in items if item.get("source_type") == "jp_cross_market_macro_context"], freshness
    )
    sentiment = _fresh_items(
        [item for item in items if item.get("layer") == "MARKET_SENTIMENT"], freshness
    )

    dimensions = {
        "technical": _dimension(
            "neutral" if snapshot else "unavailable",
            "medium" if snapshot else "low",
            1 if snapshot else 0,
            "CURRENT" if snapshot else "DATA_UNAVAILABLE",
            "Verified Market Snapshot is present; direction is left to its explicit indicators.",
        ),
        "fundamentals": _official_dimension(official, "financial"),
        "official_catalysts": _official_dimension(official_events, "catalyst"),
        "news": _news_dimension(news),
        "supply_demand": _supply_dimension(supply),
        "analyst_expectations": _analyst_dimension(analyst),
        "macro_cross_market": _macro_dimension(macro),
        "sentiment": _sentiment_dimension(sentiment),
        "risk": _risk_dimension(official_events, supply),
    }
    overall = _overall(dimensions)
    return {
        "market": "JP",
        "dimensions": dimensions,
        "overall_direction": overall["direction"],
        "overall_confidence": overall["confidence"],
        "evidence_count": sum(value["evidence_count"] for value in dimensions.values()),
        "missing_dimensions": [
            name for name, value in dimensions.items() if value["direction"] == "unavailable"
        ],
        "rules": [
            "DATA_UNAVAILABLE is a confidence limitation, not a bearish signal.",
            "Official disclosures are core catalysts; news is only confirmation or market-reaction context.",
            "Company guidance and analyst consensus remain separate bases and periods.",
            "Community sentiment is auxiliary and cannot reverse the conclusion by itself.",
        ],
    }


def render_japan_decision_context(context: Mapping[str, Any] | None) -> str:
    """Render a compact, fact-bound instruction block for final JP agents."""
    if not context or context.get("market") != "JP":
        return ""
    lines = [
        "## Japan Decision Context (deterministic; not a trading score)",
        f"- Overall direction: {context.get('overall_direction', 'unavailable')}",
        f"- Overall confidence: {context.get('overall_confidence', 'low')}",
    ]
    for name, value in (context.get("dimensions") or {}).items():
        lines.append(
            "- "
            f"{name}: direction={value.get('direction')}; confidence={value.get('confidence')}; "
            f"evidence_count={value.get('evidence_count')}; freshness={value.get('freshness')}"
        )
    lines.extend(
        [
            "Rules: unavailable data reduces confidence only; it is not bearish. Count an official disclosure once even if news repeats it. Keep company guidance and analyst consensus separate. Sentiment is auxiliary and may not independently reverse a conclusion.",
            "If final action conflicts with the Trader, material risk, low confidence, or lacks an invalidation condition, explicitly explain the exception or downgrade to Hold.",
        ]
    )
    return "\n".join(lines)


def _dimension(direction: str, confidence: str, count: int, freshness: str, note: str) -> dict[str, Any]:
    return {
        "direction": direction,
        "confidence": confidence,
        "evidence_count": count,
        "freshness": freshness,
        "note": note,
    }


def _official_dimension(items: list[Mapping[str, Any]], kind: str) -> dict[str, Any]:
    if not items:
        return _dimension("unavailable", "low", 0, "DATA_UNAVAILABLE", "No fresh official evidence.")
    directions = [_official_direction(item) for item in items]
    direction = _combine(directions)
    note = "Official disclosures only; duplicate news is excluded from this dimension."
    if kind == "financial":
        items = [item for item in items if item.get("source_type") in {"official_financial_summary", "earnings_or_quarterly", "guidance_revision"}]
        if not items:
            return _dimension("unavailable", "low", 0, "DATA_UNAVAILABLE", "No fresh official financial/guidance evidence.")
        directions = [_official_direction(item) for item in items]
        direction = _combine(directions)
        note = "Company guidance is retained separately from analyst consensus."
    return _dimension(direction, _confidence(items), len(items), _freshness(items), note)


def _news_dimension(items: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not items:
        return _dimension("unavailable", "low", 0, "DATA_UNAVAILABLE", "No fresh verified-timestamp news.")
    clusters = {item.get("metadata", {}).get("cluster_id") or item.get("url") or item.get("title") for item in items}
    sentiments = [str(item.get("metadata", {}).get("sentiment", "neutral")).lower() for item in items]
    return _dimension(_combine(sentiments), _confidence(items), len(clusters), _freshness(items), "Clustered news; not counted as additional official catalysts.")


def _supply_dimension(items: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not items:
        return _dimension("unavailable", "low", 0, "DATA_UNAVAILABLE", "No fresh JSF/JPX evidence.")
    trend = next((item.get("metadata", {}) for item in items if item.get("source_type") == "securities_finance_trend"), {})
    if trend.get("rapid_finance_buildup"):
        direction, note = "bearish", "JSF flags rapid financing buildup; treat as supply-demand risk, not a price forecast."
    elif trend.get("continuous_deleveraging"):
        direction, note = "neutral", "JSF flags deleveraging; no directional price inference is made."
    else:
        direction, note = "neutral", "JSF/JPX observations are available but no confirmed directional trend is inferred."
    return _dimension(direction, _confidence(items), len(items), _freshness(items), note)


def _analyst_dimension(items: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not items:
        return _dimension("unavailable", "low", 0, "DATA_UNAVAILABLE", "No dated analyst consensus.")
    payload = items[0].get("metadata", {}).get("analyst_expectations", {})
    revisions = payload.get("revision_trend", {}).get("30D", {})
    up = revisions.get("metric_upward_revisions", 0) or 0
    down = revisions.get("metric_downward_revisions", 0) or 0
    rating = str(payload.get("consensus_rating", "")).lower()
    direction = "bullish" if up > down or rating in {"buy", "買い"} else "bearish" if down > up else "neutral"
    return _dimension(direction, _confidence(items), 1, _freshness(items), "Analyst consensus only; never substitutes for company guidance.")


def _macro_dimension(items: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not items:
        return _dimension("unavailable", "low", 0, "DATA_UNAVAILABLE", "No current Japan cross-market context.")
    return _dimension("neutral", _confidence(items), 1, _freshness(items), "Observed macro co-movements are not issuer-specific causal claims.")


def _sentiment_dimension(items: list[Mapping[str, Any]]) -> dict[str, Any]:
    posts = [item for item in items if item.get("source_type") == "japan_investor_post"]
    if not posts:
        return _dimension("unavailable", "low", 0, "DATA_UNAVAILABLE", "No sufficient local sentiment sample.")
    values = [str(item.get("metadata", {}).get("sentiment", "neutral")).lower() for item in posts]
    return _dimension(_combine(values), "low", len(posts), _freshness(posts), "Auxiliary local sentiment only; cannot reverse the core conclusion.")


def _risk_dimension(official: list[Mapping[str, Any]], supply: list[Mapping[str, Any]]) -> dict[str, Any]:
    adverse = [item for item in official if _official_direction(item) == "bearish"]
    if adverse:
        return _dimension("bearish", _confidence(adverse), len(adverse), _freshness(adverse), "Concrete official downside event requires final-action explanation.")
    if supply:
        return _dimension("neutral", "medium", len(supply), _freshness(supply), "No automatic risk direction from missing data or a single supply-demand observation.")
    return _dimension("unavailable", "low", 0, "DATA_UNAVAILABLE", "Risk evidence unavailable; this lowers confidence only.")


def _official_direction(item: Mapping[str, Any]) -> str:
    metadata = item.get("metadata", {}) or {}
    values = " ".join(str(metadata.get(key, "")) for key in ("guidance_change", "capital_policy_change", "event_type"))
    title = str(item.get("title", ""))
    text = f"{values} {title}".lower()
    if any(token in text for token in ("upward", "share_buyback", "buyback", "自社株", "上方")):
        return "bullish"
    if any(token in text for token in ("downward", "capital_increase", "dilution", "増資", "下方")):
        return "bearish"
    return "neutral"


def _combine(values: Iterable[str]) -> str:
    normalized = [value.lower() for value in values]
    bullish = sum(value in {"bullish", "positive", "upward", "buy"} for value in normalized)
    bearish = sum(value in {"bearish", "negative", "downward", "sell"} for value in normalized)
    if bullish > bearish:
        return "bullish" if bullish > 1 else "weak_bullish"
    if bearish > bullish:
        return "bearish" if bearish > 1 else "weak_bearish"
    return "neutral"


def _freshness_index(items: list[Mapping[str, Any]]) -> dict[tuple[str, str, str], str]:
    governance = next((item for item in items if item.get("source_type") == "source_of_truth_assessment"), {})
    return {
        (entry.get("source", ""), entry.get("source_type", ""), entry.get("timestamp", "")): entry.get("status", "DATA_UNAVAILABLE")
        for entry in governance.get("metadata", {}).get("freshness", [])
    }


def _fresh_items(items: list[Mapping[str, Any]], freshness: Mapping[tuple[str, str, str], str]) -> list[Mapping[str, Any]]:
    return [item for item in items if freshness.get((item.get("source", ""), item.get("source_type", ""), item.get("timestamp", "")), "CURRENT") != "STALE"]


def _freshness(items: list[Mapping[str, Any]]) -> str:
    # ``_fresh_items`` has already removed stale observations.  The remaining
    # data is source-specific current/recent evidence, never a guessed value.
    return "CURRENT" if items else "DATA_UNAVAILABLE"


def _confidence(items: list[Mapping[str, Any]]) -> str:
    if len(items) >= 3:
        return "high"
    if items:
        return "medium"
    return "low"


def _dedupe_official_events(items: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    seen: set[str] = set()
    result = []
    for item in items:
        if item.get("source") not in {"TDnet", "Company IR"}:
            continue
        title = re.sub(r"[^0-9a-zA-Z一-龯ぁ-んァ-ン]", "", str(item.get("title", "")).lower())
        key = f"{item.get('timestamp', '')[:10]}:{title}"
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _overall(dimensions: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    """Use a small transparent vote; unavailable inputs never cast a vote."""
    score = 0.0
    covered = 0
    for name, value in dimensions.items():
        if name in {"risk", "sentiment"}:
            continue
        direction = value.get("direction")
        if direction == "unavailable":
            continue
        covered += 1
        weight = _CORE_WEIGHTS[name]
        if direction in {"bullish", "weak_bullish"}:
            score += weight
        elif direction in {"bearish", "weak_bearish"}:
            score -= weight
    if score >= 3:
        direction = "bullish"
    elif score > 0:
        direction = "weak_bullish"
    elif score <= -3:
        direction = "bearish"
    elif score < 0:
        direction = "weak_bearish"
    else:
        direction = "neutral" if covered else "unavailable"
    confidence = "high" if covered >= 6 else "medium" if covered >= 3 else "low"
    return {"direction": direction, "confidence": confidence}
