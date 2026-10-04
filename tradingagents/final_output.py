"""Build the single accepted user-facing state after all agents have run.

Agents keep ownership of reasoning, while source authority, evidence auditing,
and execution validation remain machine contracts.  This module is the sole
place where those contracts are applied to the final user-visible prose.  The
original agent output is retained separately for the technical agent log.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Mapping
from typing import Any

from tradingagents.agents.evidence_enforcement import enforce_agent_output
from tradingagents.agents.execution_validation import (
    EXECUTION_PLAN_FIELDS,
    parse_execution_action,
    reconcile_execution_authority,
    validate_execution_plan,
)
from tradingagents.agents.market_authority import canonical_market_authority
from tradingagents.agents.market_claims import (
    current_market_claims,
    remove_current_market_claims,
)
from tradingagents.agents.rating import parse_explicit_rating
from tradingagents.dataflows.japan.context import (
    render_japan_financial_report,
    render_japan_report_sections,
    render_japan_sentiment_report,
)
from tradingagents.rating_authority import (
    artifact_rating_violations,
    attributed_rating_fact,
    internal_rating_claims,
    normalize_technical_outlook_labels,
    remove_internal_ratings,
)
from tradingagents.report_artifacts import (
    render_markdown_fragment,
    rendered_table_rows,
    validate_rendered_html,
)
from tradingagents.report_consistency import canonical_report_metadata
from tradingagents.secret_redaction import sanitize_text

_REPORT_FIELDS = (
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "investment_plan",
    "trader_investment_plan",
    "final_trade_decision",
)
_DEBATE_FIELDS = ("investment_debate_state", "risk_debate_state")
_CONTRACT_VERSION = "v5"
_CONTRACT_SEMANTIC_REVISION = "relational-macro-withheld-plan-2026-10"
_VIOLATION_CATEGORIES = {
    "UNSUPPORTED_CLAIM",
    "STALE_EVIDENCE_USE",
    "FUTURE_DATA_USE",
    "SEMANTIC_MISMATCH",
    "UNIT_MISMATCH",
    "ARITHMETIC_MISMATCH",
}
_CURRENT_FINANCIAL_HEADINGS = (
    "current financial authority summary",
    "latest actual",
    "current company guidance",
    "japan financial authority assessment",
    "最新季度业绩",
    "最新实际",
    "最新实绩",
    "管理层指引",
    "公司指引",
    "当前财务权威摘要",
)
_CORPORATE_OPERATION = re.compile(
    r"(?:股票|股份|share\s+|stock\s+)?(?:回购|回購|buyback|repurchase)|"
    r"资本开支|資本支出|capex|capital\s+expenditure|订单|訂單|order\s+fulfilment|"
    r"(?:company|customer|sales|purchase)\s+orders?|"
    r"并购|併購|收购|收購|merger|acquisition|经营|營運|business\s+operations?", re.I,
)
_BUSINESS_EXECUTION_OBJECT = re.compile(
    r"业务|产业|产品|研发|经营资源|战略|生产(?:设备|基地|网络)|供应链|供應鏈|"
    r"地缘(?:集中)?风险|地緣風險|海外收入|汇率|外汇|5G|6G|海外市场|"
    r"business|product|R&D|production\s+(?:network|base|facilit)|supply.chain|"
    r"currency|foreign.exchange|operating\s+resources?", re.I,
)
_PORTFOLIO_EXECUTION_OBJECT = re.compile(
    r"股价|股票|该股|標的|标的|投资者|持仓|期货|期權|期权|ETF|指数|"
    r"逢低|逢高|回调|支撑|阻力|价位|仓位|敞口|"
    r"\b(?:stock|shares|investors?|portfolio|position|exposure|futures?|options?|index)\b", re.I,
)
_EXECUTION_HEADING = re.compile(
    r"(?:execution|trading?\s+plan|trade\s+parameters?|actionable|"
    r"可操作|执行|行动建议|交易者|交易(?:建议|策略|计划|参数|执行)|"
    r"操作(?:建议|策略|计划)|取引(?:レコメンデーション|戦略|計画)|"
    r"入场|止损|止盈|仓位)",
    re.I,
)
_WITHHELD_EXECUTION_HEADINGS = {
    "trading team plan",
    "交易执行状态",
    "执行许可",
}
_EXECUTION_LINE = re.compile(
    r"(?:entry(?: price| condition)?|stop(?:[ -]?loss)?|price target|position(?: sizing)?|"
    r"入场(?:价|条件)?|建仓价|止损(?:价)?|止盈|目标价|仓位(?:上限)?)"
    r"(?:\*\*)?\s*[:：|]",
    re.I,
)
_JSF_CLAIM = re.compile(
    r"(?:\bJSF\b|日证金|日證金|貸株|贷株|借券|融券|融資|融资|securities\s+finance)",
    re.I,
)
_EXECUTION_INSTRUCTIONS = re.compile(
    r"(?:严守|严格|设置|设定|执行|触发|强制)(?:止损|止盈)|强制离场|"
    r"(?:小|轻|重|试探|少量)[仓倉](?:位)?|"
    r"(?:建议|可以|可|应|宜|考虑|评估|确认后|破位后|逢高|择机|伺机|开始|建立)[^。；;\n]{0,80}(?:做空|试空|试多|开空|开多|建仓|入场|介入|进场|加仓|减仓|买入|卖出)|"
    r"(?:做空|建仓|入场|开空|开多)[^。；;\n]{0,80}(?:条件|触发|执行|建议)|"
    r"可执行方案|执行纪律|(?:仓位|敞口)[^。；;\n]{0,8}(?:纪律|配置)|价格触发条件|"
    r"\b(?:enter|initiate|open|take)\b[^.;\n]{0,30}\b(?:short|long|position|trade)\b|"
    r"\b(?:stop[ -]?loss|small position|forced exit)\b",
    re.I,
)
_EXECUTION_ACTION = re.compile(
    r"(?:买入|賣出|卖出|增持|減持|减持|加仓|加倉|减仓|減倉|建仓|建倉|"
    r"开仓|開倉|平仓|平倉|清仓|清倉|做多|做空|介入|入场|進場|进场|"
    r"了结|了結|退出|布局|配置|对冲|對沖|高(?:抛|賣|卖)低(?:吸|買|买)|逢高减码|逢高減碼|"
    r"hedg(?:e|ing)|buy\s+low\s+and\s+sell\s+high|"
    r"调整仓位|調整倉位|调整敞口|調整敞口|"
    r"open\s+(?:a\s+)?position|increase\s+(?:the\s+)?position|"
    r"reduce\s+(?:the\s+)?position|close\s+(?:the\s+)?position|"
    r"(?:add(?:ing)?|increas(?:e|ing)|reduc(?:e|ing))\s+(?:the\s+)?exposure|"
    r"enter(?:ing)?\s+(?:a\s+)?(?:long|short)(?:\s+position)?)",
    re.I,
)
_EXECUTION_DIRECTIVE_CONTEXT = re.compile(
    r"(?:建议|建議|应|應|应该|應該|宜|可(?:以)?|考虑|考慮|等待[^。；;\n]{0,20}后|"
    r"确认[^。；;\n]{0,20}后|突破[^。；;\n]{0,20}后|跌破[^。；;\n]{0,20}后|"
    r"逢低|逢高|分批|分层|分層|支持|积极|積極|现有持仓|現有持倉|未投资者|未投資者|"
    r"目标水平|目標水平|策略|框架|操作|计划|計畫|plan|recommend|should|consider|if\b)",
    re.I,
)
_EXECUTION_OPPORTUNITY = re.compile(
    r"(?:买入|買入|卖出|賣出|建仓|建倉|加仓|加倉|配置|对冲|對沖)\s*(?:良机|机会|機會)|"
    r"\b(?:opportunity|opportunities)\s+to\s+(?:buy|sell|hedge|enter|add)\b",
    re.I,
)
_COMPOUND_TRADE_STRATEGY = re.compile(r"高(?:抛|賣|卖)低(?:吸|買|买)|\bbuy\s+low\s+and\s+sell\s+high\b", re.I)
_EXECUTION_PROHIBITION = re.compile(
    r"(?:不(?:支持|建议|建議|采用|實施|实施|應|应|要|可|宜|追高|新增|入场|買入|买入|賣出|卖出|"
    r"加仓|加倉|建仓|建倉|减仓|減倉|做空|执行|提供)|"
    r"暂不|暫不|禁止|不得|避免|没有获准|未获批准|尚未获准|"
    r"(?:无法|無法|不能|未能)(?:支持|提供|批准|授權|授权)|"
    r"\b(?:do not|don't|must not|should not|not recommended|not authorized|"
    r"no approved|avoid|cannot\s+(?:support|provide|approve|authorize)|"
    r"unable\s+to\s+(?:support|provide|approve|authorize))\b)",
    re.I,
)
_CONDITIONAL_EXECUTION = re.compile(
    r"(?:若|如果|如若|一旦|除非|否则|则|則|隨後|随后|之后|之後|确认后|確認後|突破后|跌破后|"
    r"\b(?:unless|after confirmation|then|if)\b)",
    re.I,
)
_CONDITION_ACTION_LINK = re.compile(
    r"(?:→|⇒|=>|->|\bthen\b|则|則|就)"
    r"[^。；;\n|]{0,40}(?:买入|買入|卖出|賣出|增持|减持|減持|加仓|加倉|"
    r"减仓|減倉|建仓|建倉|开仓|開倉|平仓|平倉|清仓|清倉|做多|做空|"
    r"介入|入场|進場|进场|退出|逢低布局|逢高减码|逢高減碼|"
    r"\b(?:buy|sell|short|add\s+to|reduce|trim|close|open|enter)(?![\w-]))",
    re.I,
)
_EXECUTION_CONTRAST_BOUNDARY = re.compile(
    r"(?:(?<=[，,])\s*|\s+)(?:但(?:是)?|不过|不過|然而|可是|but|however)\s*[，,:：]?\s*",
    re.I,
)
_ACTIONABLE_PRICE = re.compile(
    r"(?:\b(?:at|above|below|under|over)\s*|[在于於低高破超]\s*|"
    r"(?:跌破|突破|低于|高于|以下|以上|以内|以內)\s*)"
    r"[¥￥]?\s*\d[\d,]*(?:\.\d+)?|"
    r"\d[\d,]*(?:\.\d+)?\s*(?:以下|以上|以内|以內|附近|円|元)",
    re.I,
)
_ACTIONABLE_HOLDING_PERIOD = re.compile(
    r"(?:持仓|持倉|持有|仓位|倉位|\b(?:hold|holding|position)\b)"
    r"[^。；;\n]{0,24}(?:周期|週期|期限|时间|時間|\b(?:period|duration|for)\b)"
    r"[^。；;\n]{0,24}\d[\d-]*\s*(?:个?交易日|天|日(?!元|均线)|周|週|月|\b(?:days?|weeks?|months?)\b)|"
    r"(?:持仓|持倉|持有)\s*(?:严格|嚴格|控制|為|为|至|约|約)?\s*"
    r"\d[\d-]*\s*(?:个?交易日|天|日(?!元|均线)|周|週|月)|"
    r"\bhold\s+(?:for|until)\s+\d[\d-]*\s*(?:days?|weeks?|months?)",
    re.I,
)
_ENGLISH_TRADE_ACTION = re.compile(
    r"(?<![\w-])(?:buy|sell|short|add\s+to|reduce|trim|close|open|enter)(?![\w-])",
    re.I,
)
_ACTION_AMOUNT = re.compile(
    r"(?:买入|賣出|卖出|增持|減持|减持|加仓|加倉|减仓|減倉|建仓|建倉|"
    r"开仓|開倉|平仓|平倉|清仓|清倉|做多|做空)"
    r"[^。；;\n]{0,16}(?:至|到|为|為|成|\bto\b)\s*"
    r"(?:[¥￥]?\d[\d,]*(?:\.\d+)?\s*%?|一半|半仓|半倉)",
    re.I,
)
_STOP_TRIGGER = re.compile(
    r"(?:跌破|突破|低于|高于|低於|高於)\s*[¥￥]?\d[\d,]*(?:\.\d+)?"
    r"[^。；;\n]{0,24}(?:止[损損]|止盈)|"
    r"(?:止[损損]|止盈)(?:价|價|位|设在|設在|设置在|設置在|于|於)?\s*[:：]?\s*"
    r"[¥￥]?\d[\d,]*(?:\.\d+)?|"
    r"(?:执行|執行|严守|嚴守|设置|設置|设定|設定)\s*[¥￥]?\d[\d,]*(?:\.\d+)?"
    r"\s*(?:日元|日圓|円|元|美元|USD|JPY)?\s*(?:止[损損]|止盈)",
    re.I,
)
_EXECUTION_TRIGGER_ACTION = re.compile(
    r"(?:触发|跌破|突破|达到|到达|命中)[^。；;\n]{0,24}"
    r"(?:止损|止盈|离场|離場|平仓|平倉)[^。；;\n]{0,16}"
    r"(?:执行|執行|离场|離場|平仓|平倉|卖出|賣出)|"
    r"(?:止损|止盈)(?:位|条件|條件)?[^。；;\n]{0,16}"
    r"(?:即|必须|必須|应当|應當|应该|應該)?\s*"
    r"(?:执行|執行|离场|離場|平仓|平倉|卖出|賣出)",
    re.I,
)
_ACTIONABLE_HORIZON_FIELD = re.compile(
    r"^\s*(?:\*\*)?(?:time\s+horizon|持仓周期|持倉週期|交易周期|交易週期)"
    r"(?:\*\*)?\s*[:：][^。；;\n]*\d[\d-]*\s*"
    r"(?:个?交易日|天|日(?!元|均线)|周|週|月|days?|weeks?|months?)",
    re.I,
)
_POSITION_ENTITY = re.compile(r"仓位|倉位|持仓|持倉|敞口|\b(?:positions?|exposure|holdings?|holders?)\b", re.I)
_POSITION_PRESERVATION = re.compile(
    r"维持|維持|保持|保留|继续持有|繼續持有|"
    r"\b(?:maintain(?:ed|ing)?|retain(?:ed|ing)?|keep|kept|hold)\b", re.I,
)
_EXECUTION_TRIGGER_SETUP = re.compile(
    r"(?:设置|設置|设定|設定|建立)[^。；;\n]{0,16}"
    r"(?:触发条件|觸發條件|交易条件|交易條件|执行条件|執行條件)",
    re.I,
)
_SENTIMENT_AUTHORITY = re.compile(
    r"(?:投资者|投資者|社交|市场|市場)?情绪[^。；;\n|]{0,40}"
    r"(?:\d+(?:\.\d+)?\s*/\s*10|分数|分數|评分|評分|偏多|偏空|中性|"
    r"温和偏多|溫和偏多|温和偏空|溫和偏空|bullish|bearish|neutral)|"
    r"(?:sentiment\s+(?:score|band|sample|confidence)|overall_band)",
    re.I,
)
_PRESENTATION_HEADING_TRANSLATIONS = {
    "final transaction proposal": "最终研究结论",
    "executive summary": "研究摘要",
    "final decision": "最终研究结论",
    "recommendation": "研究建议",
    "strategic actions": "策略说明",
    "rating": "评级",
    "investment thesis": "投资逻辑",
    "rationale": "研究依据",
    "time horizon": "研究周期",
    "action": "交易动作",
    "trading plan": "交易计划",
    "price target": "目标价",
    "entry price": "入场价",
    "stop loss": "止损价",
    "position sizing": "仓位规模",
    "maximum position": "仓位上限",
}
_POSITION_RECOMMENDATION = re.compile(
    r"(?:仓位|倉位|净敞口|净暴露|组合总值|position(?: size| sizing)?|net exposure|allocation)"
    r"[^。；;\n]*(?:\d|%|≤|≥|上限|不超过)|"
    r"\d[\d.]*\s*%[^。；;\n]*(?:组合|portfolio|position|仓位(?:配置|分配|上限))",
    re.I,
)
_POSITION_DIRECTIVE = re.compile(
    r"(?:(?:仓位|倉位|敞口|净暴露|position|allocation)"
    r"[^。；;\n]{0,32}(?:建议|必须|应当|应该|应|宜|控制|缩放|配置|调整|限制|降低|增加)|"
    r"(?:建议|必须|应当|应该|应|宜|控制|缩放|配置|调整|限制|降低|增加)"
    r"[^。；;\n]{0,32}(?:仓位|倉位|敞口|净暴露|position|allocation))",
    re.I,
)
_EXECUTION_PARAMETER = re.compile(
    r"(?:入场|建仓|目标价|第一目标|第二目标|\bentry\b|\bstop\b|\btarget\b)"
    r"[^。；;\n]*\d",
    re.I,
)
_TRADER_WITHHELD = "确定性执行校验未通过，因此没有获准的入场、止损、目标价或仓位计划。"
_EXECUTION_WITHHELD = (
    "确定性执行校验未通过。本报告不提供可执行的入场、止损、目标价或仓位参数；"
    "方向性研究结论不等于已批准交易指令。"
)
_PUBLIC_NEWS_TEXT = {
    "Canonical Market authority": "正式行情证据",
    "Canonical Financial authority": "正式财务证据",
    "Market authority": "行情证据",
    "Financial authority": "财务证据",
    "Japan company-news authority": "已核验的日本公司新闻",
    "The following timestamped Japan bundle headlines are available; a separate tool's empty result does not mean there is no company news.": "以下公司新闻均有明确发布时间；其他新闻工具返回空结果，不代表本次没有公司新闻。",
}
_INTERNAL_STATUS = {
    "UNAVAILABLE": "不可用",
    "OK": "通过",
    "UNKNOWN": "未确认",
    "VERIFIED_FINANCIAL_AUTHORITY": "已核验的财务权威数据",
    "VERIFIED_TOOL_OUTPUT": "已核验的工具数据",
    "CURRENT_STRUCTURED_CONFIRMED": "截至分析日已确认最新",
    "CURRENT_OFFICIAL": "截至分析日最新官方数据",
    "FRESHNESS_UNVERIFIED": "新鲜度未确认",
    "INSUFFICIENT_DATA": "证据不足",
    "DATA_UNAVAILABLE": "数据不可用",
    "NOT_APPLICABLE": "不适用",
    "NOT_PROVIDED": "公司未提供",
    "LATEST_AVAILABLE": "来源当前最新可得",
    "STRUCTURED_SOURCE": "结构化补充来源",
    "NO_COMPLETE_OFFICIAL_AUTHORITY_PATH": "官方披露覆盖链路不完整",
    "OUTLIER_20D_CHANGE_GT_300_PCT": "异常变化，需复核",
    "NO_CONFIRMED_TREND": "趋势未确认",
    "UNCONFIRMED": "未确认",
    "INCOMPLETE": "不完整",
    "AUTH_REQUIRED": "需要认证",
    "RATE_LIMITED": "请求频率受限",
    "TIMEOUT": "请求超时",
    "API_ERROR": "数据源请求失败",
    "FETCH_FAILED": "数据获取失败",
    "PARSE_FAILED": "数据解析失败",
    "INVALID_RESPONSE": "数据源响应无效",
    "LOW_SAMPLE": "样本量不足",
    "DISABLED": "未启用",
    "VENDOR_FORWARD_ESTIMATE": "供应商远期估计",
    "COMPANY_GUIDANCE": "公司业绩指引",
    "ANALYST_CONSENSUS": "分析师一致预期",
    "J_GAAP": "日本会计准则",
    "US_GAAP": "美国会计准则",
}
_MACHINE_ENUM = re.compile(r"(?<![A-Za-z0-9])(?:[A-Z][A-Z0-9]*)(?:_[A-Z0-9]+)+(?![A-Za-z0-9])")
_INTERNAL_ENGINEERING_ASSIGNMENT = re.compile(
    r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\s*=\s*(?:true|false|null|none|unknown|unavailable|\d+(?:\.\d+)?)\b",
    re.IGNORECASE,
)
_INTERNAL_TOOL_IDENTIFIER = re.compile(
    r"\b(?:get|fetch|load|resolve|retrieve|query|search)_[a-z][a-z0-9_]*\b",
    re.IGNORECASE,
)
_DEPRECATED_USER_HORIZON_CLAIM = re.compile(
    r"(?:用户|客户|委托人).{0,60}(?:投资期限|交易周期|持仓周期|分析窗口|时间窗口|时间框架|期限|窗口)"
    r"|(?:用户|客户|委托人).{0,50}(?:交易|投资|持仓|操作|指令|要求).{0,40}"
    r"\d+\s*(?:[-–—~～]|至|到)\s*\d+\s*(?:个)?(?:交易日|日|天|周|週|个月|個月|月)"
    r"|\b(?:user|client)(?:[-\s]+(?:selected|specified|requested))?.{0,40}"
    r"(?:investment\s+horizon|trading\s+horizon|time\s+horizon|timeframe|holding\s+period|analysis\s+window)\b",
    re.IGNORECASE,
)


def build_canonical_final_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return the sole accepted final state; leave non-Japan runs unchanged."""
    result = copy.deepcopy(dict(state))
    if (result.get("market_context") or {}).get("market") != "JP":
        return result
    needs_horizon_migration = _has_deprecated_horizon_state(result)
    result = _drop_deprecated_horizon_metadata(result)
    contract = result.get("final_output_contract") or {}
    if (
        contract.get("status") == "FINALIZED"
        and contract.get("version") == _CONTRACT_VERSION
        and contract.get("semantic_revision") == _CONTRACT_SEMANTIC_REVISION
        and not needs_horizon_migration
        and not _execution_cross_state_issues(result, bool(contract.get("execution_allowed")))
        and not _rating_artifact_claims(result, str(result.get("accepted_report_markdown") or ""))
        and not _artifact_market_claims(result, str(result.get("accepted_report_markdown") or ""))
        and not _validate_final_artifact(result, bool(contract.get("execution_allowed")),
                                        accepted_report=str(result.get("accepted_report_markdown") or ""))
    ):
        return _sync_upstream_manager_reports(result)
    if contract and isinstance(result.get("raw_agent_outputs"), Mapping):
        # Re-accept archived v1 state under the current output contract.
        result.update(copy.deepcopy(result["raw_agent_outputs"]))
    # Upstream v0.5 publishes managers at top-level; archived JP state used
    # judge_decision. Feed the same acceptance boundary, not another pipeline.
    for debate_key, report_key in (("investment_debate_state", "investment_plan"),
                                  ("risk_debate_state", "final_trade_decision")):
        debate = dict(result.get(debate_key) or {})
        if not debate.get("judge_decision") and isinstance(result.get(report_key), str):
            debate["judge_decision"] = result[report_key]
        result[debate_key] = debate
    # A reacceptance must derive Portfolio authority anew. Publishers use the
    # frozen rating in the completed contract, never a later raw-prose mutation.
    result.pop("final_output_contract", None)
    result = _drop_deprecated_horizon_metadata(result)
    if not result.get("trader_investment_plan") and result.get("trader_investment_decision"):
        result["trader_investment_plan"] = result["trader_investment_decision"]

    raw_outputs = _snapshot_agent_outputs(result)
    result["raw_agent_outputs"] = raw_outputs
    # Remove retired user-horizon attribution before numeric evidence
    # enforcement can turn its day/month range into placeholder fragments.
    result = _map_report_text(result, _remove_deprecated_user_horizon_claims)
    # Artifact violations describe one concrete rendered candidate.  Archived
    # state can be re-accepted under a newer contract, so carry forward the
    # evidence audit but recompute final-artifact findings from the new exact
    # accepted_report_markdown below.
    audit = [
        item
        for item in (result.get("evidence_audit") or [])
        if not (
            isinstance(item, Mapping)
            and (
                item.get("category")
                in {
                    "FINAL_ARTIFACT_VIOLATION",
                    "EXECUTION_ACTION_CONSISTENCY",
                    "EXECUTION_GATE",
                }
                or item.get("warning")
            )
        )
    ]

    for field in _REPORT_FIELDS:
        value = result.get(field)
        if not isinstance(value, str):
            continue
        result[field], audit = _accept_text(result, value, field, audit)

    for debate_field in _DEBATE_FIELDS:
        debate = result.get(debate_field)
        if not isinstance(debate, Mapping):
            continue
        accepted = dict(debate)
        for key, value in debate.items():
            if isinstance(value, str):
                accepted[key], audit = _accept_text(result, value, f"{debate_field}.{key}", audit)
        result[debate_field] = accepted

    financial = render_japan_financial_report(result)
    if financial:
        base = _remove_current_financial_sections(result.get("fundamentals_report", ""))
        result["fundamentals_report"] = _fold_empty_sections(
            (base.rstrip() + "\n\n" + financial).strip()
        )

    # The source-native aggregate is the only published JP sentiment
    # authority. Agent prose remains available in raw_agent_outputs/full log.
    result["sentiment_report"] = render_japan_sentiment_report(result.get("japan_data_bundle"))
    result["market_report"] = _canonicalize_market_report(result)
    result, market_findings = _enforce_market_authority_ownership(result)
    audit.extend(market_findings)
    result = _enforce_domain_authority_ownership(result)
    result = _correct_financial_authority_wording(result)

    raw_trader = raw_outputs.get("trader_investment_plan")
    raw_portfolio = raw_outputs.get("final_trade_decision")
    trader_action = parse_execution_action(raw_trader) if isinstance(raw_trader, str) else None
    portfolio_rating = (
        parse_explicit_rating(raw_portfolio) if isinstance(raw_portfolio, str) else None
    )
    prior_validation = dict(result.get("validated_execution") or {})
    # Revalidate an already-authorized plan from its raw Trader output. This may
    # revoke old permission after a parser/contract upgrade, but an archived
    # unavailable plan is never promoted into a new authorization during replay.
    if prior_validation.get("status") == "OK" and isinstance(raw_trader, str):
        prior_validation = validate_execution_plan(raw_trader)
    reconciled = reconcile_execution_authority(
        prior_validation,
        trader_action=trader_action,
        portfolio_rating=portfolio_rating,
    )
    result["validated_execution"] = reconciled
    if (state.get("validated_execution") or {}).get("status") == "OK" and reconciled.get(
        "status"
    ) != "OK":
        audit.append(
            {
                "category": "EXECUTION_ACTION_CONSISTENCY",
                "agent": "Canonical Final State",
                "field": "validated_execution",
                "detail": reconciled.get("detail"),
                "trader_action": trader_action,
                "portfolio_rating": portfolio_rating,
                "resolution": "PENDING_FINAL_ARTIFACT_VALIDATION",
                "execution_blocking": True,
            }
        )

    execution_allowed = (result.get("validated_execution") or {}).get("status") == "OK"
    # Permission belongs to the deterministic publisher, not to every Agent.
    # Capture original claims before either approved or withheld replacement.
    audit.extend(_collect_unapproved_execution_claims(result))
    result, rating_findings = _enforce_portfolio_rating_ownership(result)
    audit.extend(rating_findings)
    if execution_allowed:
        result = _apply_validated_execution(result)
    else:
        result = _withhold_unvalidated_execution(result)
        audit.append(
            {
                "category": "EXECUTION_GATE",
                "agent": "Canonical Final State",
                "field": "executable_plan",
                "detail": "Execution inputs were not approved by the deterministic validator.",
                "resolution": "PENDING_FINAL_ARTIFACT_VALIDATION",
                "execution_blocking": True,
            }
        )

    result = _publicize_state_text(result)
    result, rating_findings = _enforce_portfolio_rating_ownership(result)
    audit.extend(rating_findings)
    result = _map_report_text(result, _remove_deprecated_user_horizon_claims)
    accepted_report = sanitize_text(compose_user_report_markdown(result))
    artifact_issues = _validate_final_artifact(
        result, execution_allowed, accepted_report=accepted_report
    )
    audit, audit_issues = _finalize_audit(
        audit,
        result,
        accepted_report=accepted_report,
        execution_allowed=execution_allowed,
    )
    artifact_issues = list(dict.fromkeys([*artifact_issues, *audit_issues]))
    for issue in artifact_issues:
        audit.append(
            {
                "category": "FINAL_ARTIFACT_VIOLATION",
                "agent": "Canonical Final State",
                "detail": issue,
                "resolution": "UNRESOLVED",
                "execution_blocking": True,
            }
        )
    result["evidence_audit"] = audit
    result["accepted_report_markdown"] = accepted_report
    if result.get("trader_investment_plan"):
        # Archived web states use this historical key. Keep it as a display
        # alias of the accepted plan so raw legacy prose cannot bypass the gate.
        result["trader_investment_decision"] = result["trader_investment_plan"]
    artifact_sha256 = hashlib.sha256(accepted_report.encode("utf-8")).hexdigest()
    result["final_output_contract"] = {
        "version": _CONTRACT_VERSION,
        "semantic_revision": _CONTRACT_SEMANTIC_REVISION,
        "status": "FINALIZED" if not artifact_issues else "BLOCKED",
        "authority": "CANONICAL_FINAL_STATE",
        "execution_allowed": execution_allowed,
        "portfolio_rating": portfolio_rating,
        "audit_finalized_after_artifact_validation": True,
        "artifact_issues": artifact_issues,
        "accepted_report_sha256": artifact_sha256,
        "audit_closure_status": "CLOSED" if not artifact_issues else "UNRESOLVED",
        "validation_dimensions": {
            "structural_artifact_valid": not any(
                issue.startswith(
                    (
                        "HEADING_",
                        "NUMBERED_",
                        "CIRCLED_",
                        "INLINE_",
                        "EMPTY_",
                        "MALFORMED_",
                        "RAW_MARKDOWN",
                        "MARKDOWN_SWALLOWED",
                    )
                )
                for issue in artifact_issues
            ),
            "evidence_closure": not audit_issues,
            "domain_authority_consistent": not any(
                issue.startswith("CROSS_DOMAIN_AUTHORITY") for issue in artifact_issues
            ),
            "execution_consistent": not any(
                issue.startswith(
                    (
                        "EXECUTION_",
                        "UNAPPROVED_",
                        "UNVALIDATED_",
                        "POSITION_SIZE_",
                        "FINAL_ARTIFACT_UNAUTHORIZED_EXECUTION",
                    )
                )
                for issue in artifact_issues
            ),
            "presentation_valid": not any(
                issue.startswith(
                    ("PROCESS_PROSE", "UNLOCALIZED_", "EMPTY_", "INTERNAL_")
                )
                for issue in artifact_issues
            ),
            "persistence_byte_contract": "UTF8_EXACT_NO_APPENDED_BYTES",
        },
    }
    return _sync_upstream_manager_reports(result)


def _sync_upstream_manager_reports(state: dict[str, Any]) -> dict[str, Any]:
    """Thin state adapter: upstream's report field is the accepted JP decision.

    Gate rules and artifact assembly still have one implementation. The CLI and
    report writer now read upstream's top-level field, so it cannot retain an
    unaccepted copy of the raw research decision after acceptance.
    """
    research = state.get("investment_debate_state") or {}
    if isinstance(research.get("judge_decision"), str):
        state["investment_plan"] = research["judge_decision"]
    return state


def compose_user_report_markdown(state: Mapping[str, Any], *, ticker: str | None = None) -> str:
    """Compose the exact JP user artifact from accepted canonical state.

    This is deterministic and side-effect free. Renderers may translate the
    Markdown to another presentation format, but must not reselect facts,
    execution parameters, or Agent prose.
    """
    metadata = canonical_report_metadata(state)
    display_symbol = metadata["symbol"] or ticker or ""
    instrument_type = {
        "EQUITY": "股票",
        "ETF": "ETF",
    }.get(metadata["instrument_type"], metadata["instrument_type"])
    manifest = state.get("run_manifest") or {}
    generated = manifest.get("runtime_timestamp_jst")
    header = [
        f"# TradingAgents 日本股票分析报告：{display_symbol}",
        "",
        f"市场：{metadata['market']} | 货币：{metadata['currency']} | 标的类型：{instrument_type}",
    ]
    if generated:
        header.extend(["", f"生成时间：{generated}"])

    sections: list[str] = []
    japan_section = render_japan_report_sections(state.get("japan_data_bundle"))
    if japan_section:
        sections.append(japan_section)

    analysts = [
        ("市场分析", state.get("market_report")),
        ("情绪分析", state.get("sentiment_report")),
        ("新闻分析", state.get("news_report")),
        ("基本面分析", state.get("fundamentals_report")),
    ]
    analyst_parts = [
        _agent_report_section(name, text)
        for name, text in analysts
        if isinstance(text, str) and text.strip()
    ]
    if analyst_parts:
        sections.append("## I. 分析师报告\n\n" + "\n\n".join(analyst_parts))

    research = state.get("investment_debate_state") or {}
    if isinstance(research, Mapping) and isinstance(research.get("judge_decision"), str):
        text = research["judge_decision"].strip()
        if text:
            sections.append("## II. 研究团队结论\n\n" + _agent_report_section("研究经理", text))

    trader = state.get("trader_investment_plan")
    if isinstance(trader, str) and trader.strip():
        sections.append("## III. 交易团队计划\n\n" + _agent_report_section("交易员", trader))

    risk = state.get("risk_debate_state") or {}
    if isinstance(risk, Mapping) and isinstance(risk.get("judge_decision"), str):
        text = risk["judge_decision"].strip()
        if text:
            sections.append(
                "## IV. 投资组合经理结论\n\n" + _agent_report_section("投资组合经理", text)
            )

    return normalize_markdown_structure("\n\n".join(["\n".join(header), *sections]))


def _agent_report_section(name: str, text: str) -> str:
    """Nest Agent headings below the deterministic report wrapper."""

    def nested_heading(match: re.Match[str]) -> str:
        level = len(match.group(1)) + 3
        return (("#" * level + " ") if level <= 6 else "") + match.group(2)

    nested = re.sub(r"(?m)^(#{1,6})\s+(.+)$", nested_heading, text.strip())
    return f"### {name}\n{nested}"


def require_canonical_final_state(state: Mapping[str, Any]) -> None:
    """Fail closed when a JP renderer receives non-canonical business state."""
    if (state.get("market_context") or {}).get("market") != "JP":
        return
    contract = state.get("final_output_contract") or {}
    if contract.get("status") != "FINALIZED" or contract.get("version") != _CONTRACT_VERSION:
        raise ValueError("Japan report input is not an accepted canonical final state")
    if contract.get("semantic_revision") != _CONTRACT_SEMANTIC_REVISION:
        raise ValueError("Japan report semantic revision requires canonical reacceptance")
    if _execution_cross_state_issues(state, bool(contract.get("execution_allowed"))):
        raise ValueError("Japan report execution authority is internally inconsistent")
    if _rating_artifact_claims(state, str(state.get("accepted_report_markdown") or "")):
        raise ValueError("Japan report has a secondary or inconsistent investment rating")
    if _artifact_market_claims(state, str(state.get("accepted_report_markdown") or "")):
        raise ValueError("Japan report has current technical claims without Market authority")
    accepted = state.get("accepted_report_markdown")
    if _validate_final_artifact(state, bool(contract.get("execution_allowed")),
                                accepted_report=str(accepted or "")):
        raise ValueError("Japan report exact artifact violates canonical publication authority")
    expected_hash = contract.get("accepted_report_sha256")
    if (
        not isinstance(accepted, str)
        or hashlib.sha256(accepted.encode("utf-8")).hexdigest() != expected_hash
    ):
        raise ValueError("Japan accepted report does not match its canonical digest")


def _snapshot_agent_outputs(state: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = {key: copy.deepcopy(state.get(key)) for key in _REPORT_FIELDS}
    snapshot.update({key: copy.deepcopy(state.get(key)) for key in _DEBATE_FIELDS})
    return snapshot


def _accept_text(
    state: Mapping[str, Any], text: str, field: str, audit: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    agent = _agent_for_field(field)
    for claim in _generation_process_claims(text):
        audit.append({
            "category": "SEMANTIC_MISMATCH", "semantic_type": "PROCESS_NARRATION",
            "agent": agent, "field": field, "warning": "PROCESS_PROSE_VISIBLE",
            "original_claim": claim,
            "claim_sha256": hashlib.sha256(claim.encode("utf-8")).hexdigest(),
            "authority_owner": "User report presentation",
            "enforcement_action": "REMOVE_ASSISTANT_PROCESS_NARRATION",
            "resolution": "PENDING_FINAL_ARTIFACT_VALIDATION", "execution_blocking": True,
        })
    checked = enforce_agent_output(state, text, agent)
    for finding in checked.findings:
        audit.append(
            {
                "category": _category_for_warning(finding.warning),
                "agent": agent,
                "field": field,
                "warning": finding.warning,
                "claim_sha256": finding.claim_sha256,
                "original_claim": finding.original_claim,
                "replacement_claim": finding.replacement_claim,
                "enforcement_action": finding.action,
                "resolution": "PENDING_FINAL_ARTIFACT_VALIDATION",
                "execution_blocking": True,
            }
        )
    return _fold_empty_sections(
        _remove_generation_process_prose(_remove_jsf_agent_claims(checked.text))
    ), audit


_GENERATION_PROCESS_PROSE = re.compile(
    r"(?im)^\s*(?:based\s+on\s+the\s+(?:available|collected|comprehensive)[^\n]*,\s*)?"
    r"(?:i(?:'ll|\s+will|\s+am\s+going\s+to)|we(?:'ll|\s+will))\s+"
    r"(?:now\s+)?(?:compile|prepare|provide|write|generate)\b[^\n]*(?:report|analysis)[^\n]*\.?\s*$"
)
_ZH_GENERATION_PROCESS_PROSE = re.compile(
    r"(?im)^\s*(?:现在|接下来|下面)?(?:让(?:我|我们)|我(?:将|来))"
    r"[^\n]{0,40}(?:基于|根据)[^\n]{0,80}(?:提供|生成|撰写|整理)"
    r"[^\n]{0,30}(?:报告|分析)[。.!]?\s*$"
)


def _remove_generation_process_prose(text: str) -> str:
    """Remove model process narration, not research content."""
    process_clause = re.compile(
        r"(?:现在|接下来|下面)?(?:让(?:我|我们)|我(?:将|来))"
        r"[^。！？\n]{0,60}(?:基于|根据)[^。！？\n]{0,100}"
        r"(?:提供|生成|撰写|整理)[^。！？\n]{0,40}(?:报告|分析)[。！？]?"
    )
    lines = []
    for line in text.splitlines(keepends=True):
        if _process_quotation(line):
            lines.append(line)
            continue
        for claim in _generation_process_claims(line):
            line = line.replace(claim, "")
        line = _GENERATION_PROCESS_PROSE.sub("", line)
        line = _ZH_GENERATION_PROCESS_PROSE.sub("", line)
        lines.append(process_clause.sub("", line))
    return "".join(lines)


def _process_quotation(line: str) -> bool:
    return line.lstrip().startswith(('>', '"', '“', '「')) or bool(re.search(
        r'(?:管理层|公司|management|company).*(?:表示|称|said|stated)\s*[:：]', line, re.I
    ))


def _generation_process_claims(text: str) -> list[str]:
    """Assistant agency + report-production/data-preparation intent.

    Quoted speech and business explanations do not have assistant agency.
    Inspect separate sentences, not a blacklist of entire model preambles.
    """
    claims = []
    for line in text.splitlines():
        # Preserve attributed speech, blockquotes and quoted first-person text.
        if _process_quotation(line):
            continue
        for unit in re.findall(r"[^.!?。！？\n]+[.!?。！？]?", line):
            plain = unit.strip().strip('*')
            agency = re.match(
                r"(?:(?:now|next|first|finally)[,:]?\s+)?(?:i\b|we\b|let\s+(?:me|us)\b|here\s+is\b)|"
                r"^(?:现在|接下来|下面)?(?:我将|我来|让我们|让我)", plain, re.I
            )
            work = re.search(r"\b(?:compile|prepare|produce|generate|write|provide|analy[sz]e|gathered|collected)\b|生成|撰写|整理", plain, re.I)
            object_ = re.search(r"\b(?:report|analysis|data|information)\b|报告|分析", plain, re.I)
            ready = re.search(r"\bhave\b.*\b(?:all|required|needed|enough)\b.*\b(?:data|information)\b|\bhave\b.*\b(?:data|information)\b.*\b(?:required|needed)\b", plain, re.I)
            if agency and ((work and object_) or ready or re.match(r"here\s+is\s+(?:the|my|our)\s+(?:report|analysis)\b", plain, re.I)):
                claims.append(unit)
    return claims


def _canonicalize_market_report(state: Mapping[str, Any]) -> str:
    """Publish current Market prose only when its OHLC authority is current.

    An analyst can reason over historical bars, but a stale or incomplete tool
    window cannot be promoted to a current technical assessment.  The raw prose
    remains in ``raw_agent_outputs`` for audit.
    """
    report = str(state.get("market_report") or "")
    authority = canonical_market_authority(state)
    if authority["status"] == "CURRENT":
        return report
    latest_text = authority.get("latest_complete_ohlcv_date") or "未获完整证据核验"
    return (
        "## 当前市场数据状态\n\n"
        f"截至 {authority.get('analysis_as_of') or state.get('trade_date')}，"
        f"正式行情工具可核验的最近完整 OHLCV 日期为 {latest_text}；"
        f"预期最近已完成交易日为 {authority.get('expected_latest_complete_date') or '未取得'}。"
        "当前行情与技术指标证据不足，因此不发布当前技术方向、指标分数或交易含义。"
        "历史行情仍保留在技术审计日志中。"
    )


def _artifact_market_claims(state: Mapping[str, Any], text: str):
    authority = canonical_market_authority(state)
    if authority["status"] == "CURRENT":
        return []
    return current_market_claims(
        text, analysis_as_of=str(authority.get("analysis_as_of") or state.get("trade_date") or "")
    )


def _enforce_market_authority_ownership(
    state: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Remove current-technical inference from every published section."""
    result = dict(state)
    authority = canonical_market_authority(state)
    if authority["status"] == "CURRENT":
        return result, []
    as_of = str(authority.get("analysis_as_of") or state.get("trade_date") or "")
    findings: list[dict[str, Any]] = []
    fields = (
        "market_report", "news_report", "fundamentals_report", "sentiment_report",
        "investment_debate_state.judge_decision", "trader_investment_plan",
        "risk_debate_state.judge_decision", "final_trade_decision",
    )
    for field in fields:
        text = _published_field_text(result, field)
        if not text:
            continue
        for claim in current_market_claims(text, analysis_as_of=as_of):
            findings.append({
                "category": "MARKET_AUTHORITY_CLAIM",
                "agent": _agent_for_field(field),
                "field": field,
                "original_claim": claim.text,
                "claim_sha256": claim.sha256,
                "semantic_type": claim.semantic_type,
                "required_domain_authority": "CURRENT_MARKET",
                "authority_state": authority["status"],
                "enforcement_action": "REMOVED",
                "resolution": "PENDING_FINAL_ARTIFACT_VALIDATION",
                "execution_blocking": True,
            })
        without_claim_sections = _filter_markdown_sections(
            text,
            lambda heading: not current_market_claims(
                heading, analysis_as_of=as_of
            ),
        )
        accepted = _fold_empty_sections(remove_current_market_claims(
            without_claim_sections, analysis_as_of=as_of
        ))
        if "." in field:
            outer, inner = field.split(".", 1)
            result[outer] = dict(result[outer]) | {inner: accepted}
        else:
            result[field] = accepted
    return result, findings


def _correct_financial_authority_wording(state: Mapping[str, Any]) -> dict[str, Any]:
    """Do not relabel dated vendor balance sheets as Current Financial Authority."""
    authority = ((state.get("japan_data_bundle") or {}).get("provider_metadata") or {}).get(
        "Japan Financial Authority"
    ) or {}
    actual = authority.get("actual") or {}
    guidance = authority.get("guidance") or {}
    if actual.get("status") == guidance.get("status") == "OK":
        return dict(state)
    historical_vendor = any(
        isinstance(entry, Mapping)
        and entry.get("source") == "get_balance_sheet"
        and entry.get("claim_type") == "FACT"
        for entry in state.get("evidence_registry") or []
    )
    result = dict(state)
    replacement = (
        "历史供应商资产负债表，非当前官方实绩确认"
        if historical_vendor else "未经当前官方实绩确认"
    )
    for field in ("investment_debate_state", "risk_debate_state"):
        debate = result.get(field)
        if isinstance(debate, Mapping) and isinstance(debate.get("judge_decision"), str):
            result[field] = dict(debate) | {
                "judge_decision": debate["judge_decision"].replace(
                    "VERIFIED_FINANCIAL_AUTHORITY", replacement
                )
            }
    for field in (
        "market_report", "news_report", "fundamentals_report",
        "trader_investment_plan", "final_trade_decision",
    ):
        if isinstance(result.get(field), str):
            result[field] = result[field].replace(
                "VERIFIED_FINANCIAL_AUTHORITY", replacement
            )
    return result


def _enforce_domain_authority_ownership(state: Mapping[str, Any]) -> dict[str, Any]:
    """Keep source-native sentiment aggregates in their one canonical field."""
    result = dict(state)
    for field in _REPORT_FIELDS:
        if field == "sentiment_report" or not isinstance(result.get(field), str):
            continue
        result[field] = _remove_cross_domain_sentiment_authority(result[field])
    for field in _DEBATE_FIELDS:
        debate = result.get(field)
        if not isinstance(debate, Mapping):
            continue
        result[field] = {
            key: _remove_cross_domain_sentiment_authority(value)
            if isinstance(value, str)
            else value
            for key, value in debate.items()
        }
    return result


def _remove_cross_domain_sentiment_authority(text: str) -> str:
    text = _filter_markdown_sections(
        text,
        lambda heading: not _SENTIMENT_AUTHORITY.search(heading),
    )
    kept: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            if not _SENTIMENT_AUTHORITY.search(line):
                kept.append(line)
            continue
        clauses = re.split(r"(?<=[。！？；;])", line)
        kept.append("".join(part for part in clauses if not _SENTIMENT_AUTHORITY.search(part)))
    return _fold_empty_sections("\n".join(kept))


def _enforce_portfolio_rating_ownership(state: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Publish only Portfolio's own rating; preserve attributed source ratings.

    This happens after execution reconciliation, so the raw Trader action is
    still available to the validator. Original reasoning remains in the log.
    """
    result = dict(state)
    findings = []
    fields = ("market_report", "sentiment_report", "news_report", "fundamentals_report",
              "trader_investment_plan", "investment_debate_state.judge_decision")
    for field in fields:
        text = _published_field_text(result, field)
        if not text:
            continue
        if field == "market_report":
            text = normalize_technical_outlook_labels(text)
        for claim in internal_rating_claims(text):
            findings.append({
                "category": "SECONDARY_INTERNAL_RATING",
                "agent": _agent_for_field(field), "field": field,
                "original_claim": claim.text,
                "claim_sha256": hashlib.sha256(claim.text.encode("utf-8")).hexdigest(),
                "rating": claim.rating,
                "detected_recommendation": claim.rating or claim.semantic_type,
                "detected_from_rating": claim.from_rating,
                "detected_target_rating": claim.rating,
                "recommendation_semantics": claim.semantic_type,
                "authority_owner": "Portfolio Manager",
                "enforcement_action": ("RELABEL_UNOWNED_RECOMMENDATION_SURFACE"
                                       if claim.semantic_type == "RECOMMENDATION_SURFACE"
                                       else "REMOVE_SECONDARY_INTERNAL_RECOMMENDATION"),
                "replacement_claim": "研究分析" if claim.semantic_type == "RECOMMENDATION_SURFACE" else "",
                "resolution": "PENDING_FINAL_ARTIFACT_VALIDATION",
                "execution_blocking": True,
            })
        accepted = _fold_empty_sections(remove_internal_ratings(text))
        if "." in field:
            outer, inner = field.split(".", 1)
            result[outer] = dict(result[outer]) | {inner: accepted}
        else:
            result[field] = accepted
    return result, findings


def _rating_artifact_claims(state: Mapping[str, Any], text: str):
    # Portfolio prose, not the Trader or an execution-plan side field, owns
    # investment rating. Execution authorization is a separate contract.
    contract = state.get("final_output_contract") or {}
    rating = (
        contract.get("portfolio_rating")
        if contract.get("semantic_revision") == _CONTRACT_SEMANTIC_REVISION
        and "portfolio_rating" in contract
        else parse_explicit_rating(str(state.get("final_trade_decision") or ""))
    )
    return artifact_rating_violations(text, rating)


def _agent_for_field(field: str) -> str:
    if "fundamentals" in field:
        return "Fundamentals Analyst"
    if "market" in field:
        return "Market Analyst"
    if "news" in field:
        return "News Analyst"
    if "sentiment" in field:
        return "Sentiment Analyst"
    if "trader" in field:
        return "Trader"
    if "risk_debate_state" in field or "final_trade" in field:
        return "Portfolio Manager"
    return "Research Manager"


def _category_for_warning(warning: str) -> str:
    if warning == "stale_data_as_current":
        return "STALE_EVIDENCE_USE"
    if warning == "unit_mismatch":
        return "UNIT_MISMATCH"
    if warning in {
        "source_type_confusion",
        "guidance_consensus_mixed",
        "guidance_vendor_forward_mixed",
        "analyst_estimate_as_guidance",
        "guidance_as_analyst_consensus",
        "short_absence_overclaim",
        "short_pressure_overclaim",
        "historical_outcome_as_current_evidence",
        "critical_gate_bypassed",
        "probability_event_mismatch",
        "collapsed_provenance_types",
        "financial_provenance_collapsed",
    }:
        return "SEMANTIC_MISMATCH"
    if warning in {
        "verified_market_fact_downgraded",
        "verified_news_fact_downgraded",
        "financial_authority_replaced",
    }:
        return "SUPPORTED_FACT"
    return "UNSUPPORTED_CLAIM"


def _remove_current_financial_sections(text: str) -> str:
    return _filter_markdown_sections(
        text,
        lambda heading: (
            not any(token in heading.casefold() for token in _CURRENT_FINANCIAL_HEADINGS)
        ),
    )


def _filter_markdown_sections(text: str, keep_heading) -> str:
    lines = text.splitlines(keepends=True)
    output: list[str] = []
    excluded_level: int | None = None
    for line in lines:
        level = _heading_level(line)
        if level is not None:
            if excluded_level is not None and level <= excluded_level:
                excluded_level = None
            if excluded_level is None and not keep_heading(line.lstrip("#").strip()):
                excluded_level = level
        if excluded_level is None:
            output.append(line)
    return "".join(output).strip()


def _fold_empty_sections(text: str) -> str:
    """Drop empty table subsections together with conclusions that depend on them."""
    lines = [
        line
        for line in text.splitlines(keepends=True)
        if "|" not in line or line.lstrip().startswith("|")
    ]
    lines = _drop_empty_table_subsections(lines)
    lines = _normalize_markdown_tables(lines)
    lines = _drop_empty_table_subsections(lines)
    preamble: list[str] = []
    sections: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if re.match(r"^##\s+", line):
            current = [line]
            sections.append(current)
        elif current is None:
            preamble.append(line)
        else:
            current.append(line)
    kept = preamble
    for section in sections:
        table_lines = [line for line in section if line.lstrip().startswith("|")]
        if table_lines and (
            _is_table_separator(table_lines[0]) or not _table_has_data_row(table_lines)
        ):
            continue
        body = "".join(section[1:]).strip()
        if body:
            kept.extend(section)
    return normalize_markdown_structure(_clean_empty_markdown("".join(kept)))


def _clean_empty_markdown(text: str) -> str:
    """Fold empty headings/lead-ins after pruning, without deleting their siblings."""
    lines = text.splitlines()
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if (
            stripped
            and not re.sub(r"[\s*_#>+\-\d.)]", "", stripped)
            and stripped not in {"---", "***", "___"}
        ):
            continue
        # Clause pruning can leave one end of an emphasis span behind.
        if line.count("**") % 2:
            line = line.replace("**", "")
        kept.append(line)
    # Removing an empty child can empty its parent: work to a fixed point.
    while True:
        result: list[str] = []
        for index, line in enumerate(kept):
            heading = _heading_level(line)
            label = line.strip().strip("*_ ")
            lead_in = label.endswith((":", "：")) and not line.lstrip().startswith("|")
            bold_heading = (
                line.strip().startswith("**")
                and line.strip().endswith("**")
                and not re.search(r"[。；;.!?：:]", re.sub(r"^\d{1,2}[.)]\s+", "", label))
            )
            if heading is not None or lead_in or bold_heading:
                next_index = next(
                    (
                        i
                        for i in range(index + 1, len(kept))
                        if kept[i].strip() and _SEPARATOR.match(kept[i].strip()) is None
                    ),
                    len(kept),
                )
                following = kept[next_index] if next_index < len(kept) else ""
                next_level = _heading_level(following)
                boundary = not following
                if heading is not None:
                    boundary = boundary or (next_level is not None and next_level <= heading)
                else:
                    boundary = (
                        boundary
                        or next_level is not None
                        or (following.strip().startswith("**") and following.strip().endswith("**"))
                    )
                    if (
                        lead_in
                        and next_index > index + 1
                        and not re.match(r"\s*(?:[-*+>]\s|\d+[.)]\s|\|)", following)
                    ):
                        boundary = True
                if boundary:
                    continue
            result.append(line)
        if result == kept:
            break
        kept = result
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


_NUMBERED_HEADING = re.compile(
    r"^(?P<prefix>\s*)(?:(?P<arabic>\d+)(?P<arabic_punct>[.)])"
    r"(?P<arabic_space>\s+)|(?P<chinese>[一二三四五六七八九十百千万]+)"
    r"(?P<chinese_punct>[、.])(?P<chinese_space>\s*)|(?P<roman>[IVXLCDM]+)"
    r"(?P<roman_punct>[.)])(?P<roman_space>\s+))(?P<body>.+?)\s*$",
    re.IGNORECASE,
)
_NUMBERED_LIST = re.compile(
    r"^(?P<indent>\s*)(?P<number>\d+|[一二三四五六七八九十百千万]+)"
    r"(?P<punct>[.、)])(?P<space>\s+)(?P<body>.+?)\s*$"
)
_DECIMAL_HEADING = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\s+(?P<body>.+?)\s*$")
_SEPARATOR = re.compile(r"^\s*(?P<char>[-*_])(?:\s*(?P=char)){2,}\s*$")
_CIRCLED_LIST = re.compile(
    r"^(?P<indent>\s*)(?P<number>[①②③④⑤⑥⑦⑧⑨⑩])(?P<space>\s+)(?P<body>.+?)\s*$"
)
_HEADING_COUNT_CLAIM = re.compile(
    r"[（(]\s*\d+\s*个[^）)]{0,24}(?:指标|要点|项目|因素|维度|信号)[^）)]*[）)]"
)


def _chinese_ordinal(value: int) -> str:
    """Render the small ordinal range used by report headings in Chinese."""
    digits = "零一二三四五六七八九"
    if value < 10:
        return digits[value]
    if value < 20:
        return "十" if value == 10 else "十" + digits[value - 10]
    if value < 100:
        tens, ones = divmod(value, 10)
        return digits[tens] + "十" + (digits[ones] if ones else "")
    return str(value)


def _ordinal_value(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    chinese = {char: index for index, char in enumerate("零一二三四五六七八九")}
    if value in chinese:
        return chinese[value]
    if value == "十":
        return 10
    if len(value) == 2 and value[0] == "十" and value[1] in chinese:
        return 10 + chinese[value[1]]
    if len(value) == 2 and value[1] == "十" and value[0] in chinese:
        return chinese[value[0]] * 10
    if len(value) == 3 and value[1] == "十" and value[0] in chinese and value[2] in chinese:
        return chinese[value[0]] * 10 + chinese[value[2]]
    return None


def _roman_value(value: str) -> int | None:
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    previous = 0
    for char in reversed(value.upper()):
        current = values.get(char)
        if current is None:
            return None
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total


def _heading_number_kind(match: re.Match[str]) -> str:
    if match.group("arabic") is not None:
        return "arabic"
    if match.group("chinese") is not None:
        return "chinese"
    return "roman"


def _reset_heading_counters(
    counters: dict[tuple[int, tuple[str, ...], str], int],
    level: int,
    parent: tuple[str, ...],
) -> None:
    for key in tuple(counters):
        if key[0] == level and key[1] == parent:
            counters.pop(key, None)


def _is_ordinary_year(value: str) -> bool:
    return value.isdigit() and len(value) == 4 and 1900 <= int(value) <= 2100


def _normalize_heading_numbers(lines: list[str]) -> list[str]:
    """Renumber numbered sibling headings without touching ordinary numbers."""
    output = list(lines)
    stack: list[tuple[int, str, int | None]] = []
    counters: dict[tuple[int, tuple[str, ...], str], int] = {}
    for index, line in enumerate(output):
        level = _heading_level(line)
        if level is None:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = tuple(title for _, title, _ in stack)
        decimal = _DECIMAL_HEADING.match(match.group(2))
        if decimal:
            key = (level, parent, "decimal")
            counters[key] = counters.get(key, 0) + 1
            minor = counters[key]
            parent_number = next(
                (number for _, _, number in reversed(stack) if number is not None),
                None,
            )
            major = parent_number or int(decimal.group("major"))
            line_ending = "\n" if line.endswith("\n") else ""
            output[index] = f"{match.group(1)} {major}.{minor} {decimal.group('body')}{line_ending}"
            stack.append((level, f"{major}.{minor} {decimal.group('body')}", minor))
            continue
        numbered = _NUMBERED_HEADING.match(match.group(2))
        kind = _heading_number_kind(numbered) if numbered else "plain"
        key = (level, parent, kind)
        if numbered and _is_ordinary_year(numbered.group("arabic") or ""):
            numbered = None
            kind = "plain"
            key = (level, parent, kind)
        # A non-numbered sibling starts a new heading sequence.
        heading_number: int | None = None
        if not numbered:
            _reset_heading_counters(counters, level, parent)
        else:
            counters[key] = counters.get(key, 0) + 1
            number = counters[key]
            heading_number = number
            kind = _heading_number_kind(numbered)
            replacement = (
                str(number)
                if kind == "arabic"
                else _chinese_ordinal(number)
                if kind == "chinese"
                else _int_to_roman(number)
            )
            body = numbered.group("body")
            punctuation = (
                numbered.group("arabic_punct")
                or numbered.group("chinese_punct")
                or numbered.group("roman_punct")
                or ""
            )
            space = (
                numbered.group("arabic_space")
                or numbered.group("chinese_space")
                or numbered.group("roman_space")
                or ""
            )
            line_ending = "\n" if line.endswith("\n") else ""
            output[index] = f"{match.group(1)} {replacement}{punctuation}{space}{body}{line_ending}"
        stack.append((level, output[index].strip().lstrip("# "), heading_number))
    return output


def _normalize_heading_count_claims(lines: list[str]) -> list[str]:
    output = list(lines)
    for index, line in enumerate(output):
        level = _heading_level(line)
        if level is None:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)(\n?)$", line)
        if not match:
            continue
        body = _HEADING_COUNT_CLAIM.sub("", match.group(2)).strip()
        if body != match.group(2):
            output[index] = f"{match.group(1)} {body}{match.group(3)}"
    return output


def _int_to_roman(value: int) -> str:
    parts = ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))
    result = []
    for unit, symbol in parts:
        count, value = divmod(value, unit)
        result.append(symbol * count)
    return "".join(result)


def _normalize_circled_lists(lines: list[str]) -> list[str]:
    output = list(lines)
    index = 0
    while index < len(output):
        match = _CIRCLED_LIST.match(output[index])
        if not match:
            index += 1
            continue
        indent = match.group("indent")
        current = index
        number = 1
        while current < len(output):
            item = _CIRCLED_LIST.match(output[current])
            if item and item.group("indent") == indent:
                symbol = "①②③④⑤⑥⑦⑧⑨⑩"[number - 1] if number <= 10 else str(number)
                ending = "\n" if output[current].endswith("\n") else ""
                output[current] = (
                    f"{indent}{symbol}{item.group('space')}{item.group('body')}{ending}"
                )
                number += 1
                current += 1
                continue
            if not output[current].strip() and current + 1 < len(output):
                next_item = _CIRCLED_LIST.match(output[current + 1])
                if next_item and next_item.group("indent") == indent:
                    current += 1
                    continue
            break
        index = max(current, index + 1)
    return output


def _normalize_numbered_lists(lines: list[str]) -> list[str]:
    """Renumber list items across their indented continuation paragraphs."""
    output = list(lines)
    index = 0
    while index < len(output):
        match = _NUMBERED_LIST.match(output[index])
        if not match or _is_ordinary_year(match.group("number")):
            index += 1
            continue
        indent = match.group("indent")
        style = match.group("number").isdigit()
        current = index
        number = 1
        while current < len(output):
            item = _NUMBERED_LIST.match(output[current])
            if (
                item
                and not _is_ordinary_year(item.group("number"))
                and item.group("indent") == indent
                and item.group("number").isdigit() == style
            ):
                replacement = str(number) if style else _chinese_ordinal(number)
                output[current] = (
                    f"{indent}{replacement}{item.group('punct')}"
                    f"{item.group('space')}{item.group('body')}"
                    f"{chr(10) if output[current].endswith(chr(10)) else ''}"
                )
                number += 1
                current += 1
                continue
            stripped = output[current].strip()
            if not stripped or len(output[current]) - len(output[current].lstrip()) > len(indent):
                current += 1
                continue
            break
        index = max(current, index + 1)
    return output


_INLINE_PAREN_NUMBER = re.compile(
    r"(?P<prefix>^|(?<=[。；;]))(?P<space>\s*)[（(](?P<number>\d{1,2})[）)]"
)
_INLINE_DOT_NUMBER = re.compile(r"(?:^|(?<=[。；;:：]))\s*(?P<number>\d{1,2})\.\s+")
_INLINE_LIST_LABEL = re.compile(r"^\s*(?:\*\*[^*\n]+\*\*|[^\n:：]{1,40})\s*[:：]\s*$")


def _normalize_inline_parenthetical_numbers(lines: list[str]) -> list[str]:
    """Renumber inline list items without touching years or ordinary numbers."""
    output = list(lines)
    for index, line in enumerate(output):
        # Structured prose can carry a list on the same line as its label.
        # Clause pruning leaves gaps (1 -> 4) or a single surviving item (4).
        # Require a leading label/list relation; decimals, years and quoted
        # source prose are not formatting markers.
        dots = list(_INLINE_DOT_NUMBER.finditer(line))
        label = line[:dots[0].start()].rstrip() if dots else ""
        if dots and _INLINE_LIST_LABEL.fullmatch(label) and not line.lstrip().startswith(('>', '`')):
            items = [line[match.end():dots[i + 1].start() if i + 1 < len(dots) else len(line)].strip()
                     for i, match in enumerate(dots)]
            ending = '\n' if line.endswith('\n') else ''
            output[index] = label + '\n\n' + '\n'.join(
                f'{i}. {item}' for i, item in enumerate(items, 1)
            ) + ending
            continue
        matches = list(_INLINE_PAREN_NUMBER.finditer(line))
        if len(matches) < 2:
            continue
        rebuilt: list[str] = []
        position = 0
        for sequence, match in enumerate(matches, start=1):
            rebuilt.append(line[position : match.start()])
            rebuilt.append(f"{match.group('prefix')}{match.group('space')}({sequence})")
            position = match.end()
        rebuilt.append(line[position:])
        output[index] = "".join(rebuilt)
    return output


def _normalize_markdown_tables(lines: list[str]) -> list[str]:
    """Keep only well-formed Markdown table rows; never guess how to split a row."""
    result: list[str] = []
    index = 0
    while index < len(lines):
        if not lines[index].lstrip().startswith("|"):
            result.append(lines[index])
            index += 1
            continue
        start = index
        while index < len(lines) and lines[index].lstrip().startswith("|"):
            index += 1
        block = lines[start:index]
        if len(block) < 2 or not _is_table_separator(block[1]):
            continue
        header = [cell.strip() for cell in block[0].strip().strip("|").split("|")]
        separator = [cell.strip() for cell in block[1].strip().strip("|").split("|")]
        if not header or len(header) != len(separator):
            continue
        valid = block[:2]
        for row in block[2:]:
            cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
            if (
                len(cells) == len(header)
                and not _is_semantically_empty_table_row(cells, header)
            ):
                valid.append(row)
        if len(valid) > 2 and _table_has_sequence_column(header, valid[2:]):
            for sequence, row_index in enumerate(range(2, len(valid)), start=1):
                cells = [cell.strip() for cell in valid[row_index].strip().strip("|").split("|")]
                cells[0] = str(sequence)
                ending = "\n" if valid[row_index].endswith("\n") else ""
                valid[row_index] = "| " + " | ".join(cells) + " |" + ending
        if len(valid) > 2:
            if result and result[-1].strip():
                result.append("\n")
            result.extend(valid)
            # Python-Markdown's table extension continues a table across a
            # nonblank line.  A hard block boundary is therefore part of the
            # accepted Markdown contract, not cosmetic whitespace.
            if index < len(lines) and lines[index].strip():
                result.append("\n")
    return result


def _remove_orphan_structures(lines: list[str]) -> list[str]:
    """Remove markers and headings/lead-ins emptied by an earlier prune."""
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            cleaned.append(line)
            continue
        if _SEPARATOR.match(stripped):
            if not cleaned or cleaned[-1].strip() != "---":
                cleaned.append("---\n")
            continue
        if re.fullmatch(r"(?:[*_#>-]|\d+[.)]|[①②③④⑤⑥⑦⑧⑨⑩])+(?:\s*)", stripped):
            continue
        cleaned.append(line)

    # A heading or colon-ended lead-in with no following content is an orphan.
    changed = True
    while changed:
        changed = False
        output: list[str] = []
        for index, line in enumerate(cleaned):
            heading = _heading_level(line)
            label = line.strip().strip("*_ ")
            lead_in = label.endswith((":", "：")) and not line.lstrip().startswith("|")
            if heading is None and not lead_in:
                output.append(line)
                continue
            next_index = next(
                (
                    i
                    for i in range(index + 1, len(cleaned))
                    if cleaned[i].strip() and _SEPARATOR.match(cleaned[i].strip()) is None
                ),
                len(cleaned),
            )
            following = cleaned[next_index] if next_index < len(cleaned) else ""
            next_level = _heading_level(following)
            boundary = not following
            if heading is not None:
                boundary = boundary or (next_level is not None and next_level <= heading)
            else:
                boundary = (
                    boundary
                    or next_level is not None
                    or re.fullmatch(r"(?:[-*+]|\d+[.)]|[①②③④⑤⑥⑦⑧⑨⑩])\s*", following.strip() or "")
                    is not None
                )
            if boundary:
                changed = True
                continue
            output.append(line)
        cleaned = output
    return cleaned


def normalize_markdown_structure(text: str) -> str:
    """Normalize user-facing Markdown after semantic pruning.

    This function only changes Markdown structure: numbering, table shape,
    separators, and empty fragments. It never edits facts or their values.
    """
    if not text:
        return ""
    lines = [line + "\n" for line in text.splitlines()]
    lines = _strip_inert_markdown_inside_fences(lines)
    lines = _normalize_markdown_tables(lines)
    lines = _normalize_heading_numbers(lines)
    lines = _normalize_heading_count_claims(lines)
    lines = _normalize_numbered_lists(lines)
    lines = _normalize_bold_numbered_leadins(lines)
    lines = _normalize_inline_parenthetical_numbers(lines)
    lines = _normalize_circled_lists(lines)
    lines = _remove_orphan_structures(lines)
    # Collapse separators once more after orphan removal.
    output: list[str] = []
    last_nonblank_separator = False
    for line in lines:
        if _SEPARATOR.match(line.strip()):
            if last_nonblank_separator:
                while output and not output[-1].strip():
                    output.pop()
                continue
            output.append("---\n")
            last_nonblank_separator = True
        else:
            output.append(line)
            if line.strip():
                last_nonblank_separator = False
    return re.sub(r"\n{3,}", "\n\n", "".join(output)).strip()


def _normalize_bold_numbered_leadins(lines: list[str]) -> list[str]:
    """Treat standalone emphasized list labels as a sequence, not fact values."""
    output: list[str] = []
    number = 0
    for line in lines:
        if _heading_level(line) is not None:
            number = 0
        match = re.fullmatch(r"(\s*)\*\*(\d{1,2})[.)]\s+([^*\n]+)\*\*(\n?)", line)
        if match:
            number += 1
            line = f"{match.group(1)}**{number}. {match.group(3)}**{match.group(4)}"
        output.append(line)
    return output


def _strip_inert_markdown_inside_fences(lines: list[str]) -> list[str]:
    """Render explicitly declared Markdown; keep genuine code blocks inert.

    Unwrap only a complete matching fence. An incomplete block must remain
    visible to the structural validator rather than silently gaining validity.
    """
    expanded: list[str] = []
    index = 0
    while index < len(lines):
        opening = re.match(r"^\s*(`{3,}|~{3,})([^\n]*)$", lines[index])
        if opening:
            fence = opening.group(1)
            closing = next((end for end in range(index + 1, len(lines))
                            if re.fullmatch(rf"\s*{re.escape(fence[0])}{{{len(fence)},}}\s*", lines[end])), None)
            if closing is not None:
                if opening.group(2).strip().lower() in {"markdown", "md"}:
                    expanded.extend(lines[index + 1:closing])
                else:
                    expanded.extend(lines[index:closing + 1])
                index = closing + 1
                continue
            expanded.extend(lines[index:])
            break
        expanded.append(lines[index])
        index += 1
    output: list[str] = []
    fence: str | None = None
    for line in expanded:
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)[0]
            if fence is None:
                fence = token
            elif fence == token:
                fence = None
            output.append(line)
            continue
        if fence is not None:
            line = line.replace("**", "").replace("__", "")
        output.append(line)
    return output


def _markdown_structure_issues(text: str) -> list[str]:
    """Return structural defects that must never reach a user artifact."""
    lines = text.splitlines()
    issues: list[str] = []

    normalized_heading_lines = _normalize_heading_numbers([line + "\n" for line in lines])
    if any(
        original.strip() != normalized.strip()
        for original, normalized in zip(lines, normalized_heading_lines, strict=True)
        if _heading_level(original) is not None
    ):
        issues.append("HEADING_NUMBERING_DISCONTINUITY")

    normalized_inline_numbers = _normalize_inline_parenthetical_numbers(
        [line + "\n" for line in lines]
    )
    if any(
        original.strip() != normalized.strip()
        for original, normalized in zip(lines, normalized_inline_numbers, strict=True)
    ):
        issues.append("INLINE_NUMBERING_DISCONTINUITY")

    # Heading sequence validation mirrors the renumbering state machine.
    stack: list[tuple[int, str]] = []
    counters: dict[tuple[int, tuple[str, ...], str], int] = {}
    for line in lines:
        level = _heading_level(line)
        if level is None:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = tuple(title for _, title in stack)
        numbered = _NUMBERED_HEADING.match(match.group(2))
        kind = _heading_number_kind(numbered) if numbered else "plain"
        key = (level, parent, kind)
        if numbered and _is_ordinary_year(numbered.group("arabic") or ""):
            numbered = None
            kind = "plain"
            key = (level, parent, kind)
        if not numbered:
            _reset_heading_counters(counters, level, parent)
        else:
            counters[key] = counters.get(key, 0) + 1
            raw_number = (
                numbered.group("arabic")
                or numbered.group("chinese")
                or numbered.group("roman")
                or ""
            )
            parsed = _ordinal_value(raw_number) if kind != "roman" else _roman_value(raw_number)
            if parsed != counters[key]:
                issues.append("HEADING_NUMBERING_DISCONTINUITY")
        if _HEADING_COUNT_CLAIM.search(match.group(2)):
            issues.append("STALE_HEADING_COUNT_CLAIM")
        stack.append((level, match.group(2)))

    # Numbered list blocks must start at one and increase without gaps.
    index = 0
    while index < len(lines):
        match = _NUMBERED_LIST.match(lines[index])
        if not match or _is_ordinary_year(match.group("number")):
            index += 1
            continue
        indent = match.group("indent")
        style = match.group("number").isdigit()
        expected = 1
        current = index
        while current < len(lines):
            item = _NUMBERED_LIST.match(lines[current])
            if (
                item
                and not _is_ordinary_year(item.group("number"))
                and item.group("indent") == indent
                and item.group("number").isdigit() == style
            ):
                parsed = _ordinal_value(item.group("number"))
                if parsed != expected:
                    issues.append("NUMBERED_LIST_DISCONTINUITY")
                expected += 1
                current += 1
                continue
            stripped = lines[current].strip()
            if not stripped or len(lines[current]) - len(lines[current].lstrip()) > len(indent):
                current += 1
                continue
            break
        index = max(current, index + 1)

    # Circled-number lead-ins are list items too, even when they are not
    # rendered as Markdown list markers.
    index = 0
    circled = "①②③④⑤⑥⑦⑧⑨⑩"
    while index < len(lines):
        match = _CIRCLED_LIST.match(lines[index])
        if not match:
            index += 1
            continue
        indent = match.group("indent")
        expected = 1
        current = index
        while current < len(lines):
            item = _CIRCLED_LIST.match(lines[current])
            if item and item.group("indent") == indent:
                if circled.index(item.group("number")) + 1 != expected:
                    issues.append("CIRCLED_NUMBER_DISCONTINUITY")
                expected += 1
                current += 1
                continue
            if not lines[current].strip() and current + 1 < len(lines):
                next_item = _CIRCLED_LIST.match(lines[current + 1])
                if next_item and next_item.group("indent") == indent:
                    current += 1
                    continue
            break
        index = max(current, index + 1)

    for start, end in _table_blocks(lines, 0, len(lines)):
        block = lines[start:end]
        if len(block) < 2 or not _is_table_separator(block[1]):
            issues.append("MALFORMED_MARKDOWN_TABLE")
            continue
        header_count = len(block[0].strip().strip("|").split("|"))
        separator_count = len(block[1].strip().strip("|").split("|"))
        if header_count != separator_count:
            issues.append("MALFORMED_MARKDOWN_TABLE")
            continue
        for row in block[2:]:
            cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
            if len(cells) != header_count:
                issues.append("MALFORMED_MARKDOWN_TABLE")
                break
            if _is_semantically_empty_table_row(cells, _table_cells(block[0])):
                issues.append("EMPTY_MARKDOWN_TABLE_ROW")
                break
        header = [cell.strip() for cell in block[0].strip().strip("|").split("|")]
        rows = block[2:]
        if _table_has_sequence_column(header, rows):
            values = [int(row.strip().strip("|").split("|")[0].strip()) for row in rows]
            if values != list(range(1, len(values) + 1)):
                issues.append("NUMBERED_TABLE_DISCONTINUITY")

    meaningful = [line.strip() for line in lines if line.strip()]
    for previous, current in zip(meaningful, meaningful[1:], strict=False):
        if _SEPARATOR.match(previous) and _SEPARATOR.match(current):
            issues.append("DUPLICATE_SEPARATOR")
            break

    for index, line in enumerate(lines):
        if re.fullmatch(r"\s*(?:[*_#>-]|\d+[.)]|[①②③④⑤⑥⑦⑧⑨⑩])\s*", line):
            issues.append("ORPHAN_MARKDOWN_MARKER")
            break
        heading = _heading_level(line)
        if heading is None:
            continue
        next_index = next(
            (
                i
                for i in range(index + 1, len(lines))
                if lines[i].strip() and _SEPARATOR.match(lines[i].strip()) is None
            ),
            len(lines),
        )
        if next_index == len(lines):
            issues.append("ORPHAN_HEADING")
            break
        following = lines[next_index]
        next_level = _heading_level(following)
        if next_level is not None and next_level <= heading:
            issues.append("ORPHAN_HEADING")
            break
    return list(dict.fromkeys(issues))


def _drop_empty_table_subsections(lines: list[str]) -> list[str]:
    """Remove the nearest Markdown subsection when all of its tables are empty.

    Work from the deepest heading upward so an empty H3 is removed without
    discarding a valid sibling table in its H2 parent.  This is structural
    cleanup: it does not inspect company names, metrics, or prose wording.
    """
    result = list(lines)
    for level in range(6, 0, -1):
        headings = [index for index, line in enumerate(result) if _heading_level(line) == level]
        ranges: list[tuple[int, int]] = []
        for start in headings:
            end = len(result)
            for index in range(start + 1, len(result)):
                next_level = _heading_level(result[index])
                if next_level is not None and next_level <= level:
                    end = index
                    break
            table_blocks = _table_blocks(result, start + 1, end)
            if table_blocks and not any(
                _table_has_data_row(result[table_start:table_end])
                for table_start, table_end in table_blocks
            ):
                ranges.append((start, end))
        for start, end in reversed(ranges):
            del result[start:end]

    for start, end in reversed(_table_blocks(result, 0, len(result))):
        if not _table_has_data_row(result[start:end]):
            del result[start:end]
    return result


def _heading_level(line: str) -> int | None:
    match = re.match(r"^(#{1,6})\s+", line)
    return len(match.group(1)) if match else None


def _table_blocks(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    index = start
    while index < end:
        if not lines[index].lstrip().startswith("|"):
            index += 1
            continue
        table_start = index
        while index < end and lines[index].lstrip().startswith("|"):
            index += 1
        blocks.append((table_start, index))
    return blocks


def _table_has_data_row(lines: list[str]) -> bool:
    separator_seen = False
    header = _table_cells(lines[0]) if lines else []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if _is_table_separator(line):
            separator_seen = True
            continue
        if (
            separator_seen
            and not _is_semantically_empty_table_row(cells, header)
        ):
            return True
    return False


def _is_semantically_empty_table_row(cells: list[str], headers: list[str] | None = None) -> bool:
    """Empty glyphs carry no value; explicit availability states do carry information.

    A single-column observation is its own value, not a row label. A leading
    cell is ignored only when the table declares it a label/period, or it is
    an unambiguous period identifier. Unknown text is retained, not guessed empty.
    """
    placeholders = {
        "",
        "-",
        "—",
        "–",
    }

    def meaningful(cell: str) -> bool:
        normalized = re.sub(r"[*_`~]", "", cell).strip().casefold()
        return normalized not in placeholders

    if not any(meaningful(cell) for cell in cells):
        return True
    if len(cells) < 2 or any(meaningful(cell) for cell in cells[1:]):
        return False
    header = re.sub(r"[*_`~]", "", (headers or [""])[0]).strip().casefold()
    label_column = bool(re.fullmatch(
        r"(?:period|year|metric|item|kind|category|label|期间|年度|年份|指标|项目|类别|种类)", header
    ))
    label = re.sub(r"[*_`~]", "", cells[0]).strip()
    period = bool(re.fullmatch(r"(?:FY\s*)?\d{4}(?:[-/]\d{1,2}){0,2}|(?:FY\s*)?\d{4}\s*Q[1-4]", label, re.I))
    return label_column or period


def _table_has_sequence_column(header: list[str], rows: list[str]) -> bool:
    if not header or not re.fullmatch(r"(?:序号|編號|编号|no\.?|#)", header[0], re.I):
        return False
    values = [row.strip().strip("|").split("|")[0].strip() for row in rows]
    return bool(values) and all(value.isdigit() for value in values)


def _is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _withhold_unvalidated_execution(state: dict[str, Any]) -> dict[str, Any]:
    # Every potentially displayed Agent field obeys the same policy, including
    # Research Manager and legacy investment_plan aliases.
    result = _map_report_text(state, _prune_unapproved_execution)
    decision = str(result.get("final_trade_decision") or "")
    gate = "## 执行许可\n\n" + _EXECUTION_WITHHELD
    accepted = (decision.rstrip() + "\n\n" + gate).strip() if decision else gate
    result["trader_investment_plan"] = "## 交易执行状态\n\n" + _TRADER_WITHHELD
    result["final_trade_decision"] = accepted
    risk = result.get("risk_debate_state")
    if isinstance(risk, Mapping):
        risk = dict(risk)
        risk["judge_decision"] = accepted
        result["risk_debate_state"] = risk
    return result


def _collect_unapproved_execution_claims(
    state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Record each unauthorized published claim before deterministic pruning.

    The execution gate still validates the exact final artifact.  These entries
    preserve the identity of every removed clause so audit closure can prove
    that the original claim, rather than merely a sibling line, disappeared.
    """
    findings: list[dict[str, Any]] = []
    fields = (
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "trader_investment_plan",
        "investment_debate_state.judge_decision",
        "risk_debate_state.judge_decision",
    )
    for field in fields:
        text = _published_field_text(state, field)
        if not text:
            continue
        for line, headers in _execution_lines(text):
            for clause in re.split(r"(?<=[。！？；;])", line):
                claim = clause.strip()
                warning = (
                    "UNAPPROVED_EXECUTION_SECTION"
                    if _heading_level(claim) and _is_execution_heading(claim.lstrip("# "))
                    else _execution_violation(claim, headers)
                )
                if not claim or not warning:
                    continue
                findings.append(
                    {
                        "category": "UNAPPROVED_EXECUTION_CLAIM",
                        "agent": _agent_for_field(field),
                        "field": field,
                        "warning": warning,
                        "claim_sha256": hashlib.sha256(claim.encode("utf-8")).hexdigest(),
                        "original_claim": claim,
                        "semantic_type": "EXECUTION_RECOMMENDATION",
                        "authority_owner": "validated_execution",
                        "detected_actions": list(dict.fromkeys(
                            match.group() for pattern in (
                                _EXECUTION_ACTION, _ENGLISH_TRADE_ACTION,
                                *([_POSITION_PRESERVATION] if _position_preservation_instruction(claim) else []),
                            )
                            for match in pattern.finditer(claim)
                        )),
                        "canonical_action": (state.get("validated_execution") or {}).get("action"),
                        "replacement_claim": _EXECUTION_WITHHELD,
                        "enforcement_action": "REMOVED_BY_EXECUTION_GATE",
                        "resolution": "PENDING_FINAL_ARTIFACT_VALIDATION",
                        "execution_blocking": True,
                    }
                )
    return findings


def _execution_lines(text: str):
    """Keep table header and row scope through collection, pruning and validation."""
    lines = text.splitlines()
    headers = None
    for index, line in enumerate(lines):
        if line.strip().startswith("|"):
            if index + 1 < len(lines) and all(
                re.fullmatch(r":?-+:?", cell) for cell in _table_cells(lines[index + 1])
            ):
                # The renderer accepts short delimiter cells too. Execution
                # collection must run before stricter structural normalization.
                headers = _table_cells(line)
                yield line, None
                continue
            yield line, headers
        else:
            headers = None
            yield line, None


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table_header_roles(header: str) -> set[str]:
    """Compose semantic roles from header concepts, not full label spellings.

    Modifiers, emphasis and word order do not change an action/response role.
    A role supplies context only: the cell must still express an actual trading
    instruction. Company agency and descriptive columns are carried separately.
    """
    plain = re.sub(r"[*`#_]", " ", header).casefold()
    words = set(re.findall(r"[a-z]+", plain))
    concepts = {
        "condition": ({"condition", "conditions", "trigger", "triggers", "event", "events", "scenario", "scenarios"},
                      ("条件", "触发", "情景", "场景", "事件", "因素")),
        "response": ({"action", "actions", "response", "responses", "strategy", "strategies", "instruction", "instructions", "adjustment"},
                     ("操作", "动作", "行动", "应对", "策略", "指令", "仓位调整")),
        "description": ({"impact", "effect", "effects", "assessment", "evaluation", "description", "observation"},
                        ("影响", "评价", "评估", "描述", "观察")),
        "company": ({"company", "corporate", "issuer", "firm"}, ("公司", "企业", "发行人")),
        "subject": ({"subject", "actor", "entity"}, ("主体",)),
        "object": ({"object", "target"}, ("标的", "对象")),
    }
    return {role for role, (english, chinese) in concepts.items()
            if words & english or any(term in plain for term in chinese)}


def _table_execution_violation(cells: list[str], headers: list[str] | None) -> str | None:
    """A condition and response in different cells still form one plan.

    Trigger and response roles form a relation regardless of column order.
    Roles never authorize trading by themselves: risk effects, prohibitions and
    descriptive corporate actions still use the existing action classifier.
    """
    roles = [_table_header_roles(headers[index]) if headers and index < len(headers) else set()
             for index in range(len(cells))]
    subject = " ".join(cell for cell, role in zip(cells, roles, strict=True) if "subject" in role)
    target = " ".join(cell for cell, role in zip(cells, roles, strict=True) if "object" in role)
    conditions = {index for index, (cell, role) in enumerate(zip(cells, roles, strict=True))
                  if cell.strip() and ("condition" in role or _CONDITIONAL_EXECUTION.search(cell))}
    for index, cell in enumerate(cells):
        role = roles[index]
        actor = subject or ("company" if "company" in role else "")
        contextual = " ".join(part for part in (actor, cell, target) if part)
        # A mixed response/impact column can contain either. The action-cell
        # classifier decides; an added descriptive label cannot cancel a plan.
        response = "response" in role
        conditional = bool(conditions - {index}) and not role.intersection({"condition", "description"})
        if (response or conditional) and _execution_clause_violation("then " + contextual):
            return "UNAPPROVED_EXECUTION_INSTRUCTION"
        if _execution_clause_violation(contextual):
            return "UNAPPROVED_EXECUTION_INSTRUCTION"
    # Parameter label/value relations can cross cells just like actions do.
    # Cell-local scanning alone cannot see `Entry | 8513` or `仓位 | 3%`.
    joined = " ".join(cells)
    if (_EXECUTION_PARAMETER.search(joined) or _POSITION_RECOMMENDATION.search(joined)
            or _STOP_TRIGGER.search(joined)):
        return _execution_clause_violation(joined)
    return None


def _execution_violation(text: str, headers: list[str] | None = None) -> str | None:
    """Classify executable authorization across prose and Markdown cells.

    Conditions and actions retain row context; contrast separates a prohibition
    from a later authorization. Layout is never itself trading permission.
    """
    if "\n" in text:
        return next((v for line, header in _execution_lines(text)
                     if (v := _execution_violation(line, header))), None)
    if text.strip().startswith("|"):
        return _table_execution_violation(_table_cells(text), headers)
    for unit in _execution_semantic_units(text):
        violation = _execution_clause_violation(unit)
        if violation:
            return violation
    return None


def _execution_semantic_units(text: str) -> list[str]:
    raw_units: list[str]
    stripped = text.strip()
    if stripped.startswith("|") and stripped.endswith("|"):
        raw_units = [cell.strip() for cell in stripped.strip("|").split("|")]
    else:
        raw_units = [text]
    units: list[str] = []
    for raw in raw_units:
        for sentence in re.split(r"(?<=[。！？；;])|(?<=[.!?])\s+", raw):
            for contrast in _EXECUTION_CONTRAST_BOUNDARY.split(sentence):
                cleaned = contrast.strip()
                if cleaned:
                    units.append(cleaned)
    return units


def _execution_clause_violation(text: str) -> str | None:
    plain = text.replace("**", "").replace("`", "").strip()
    if not plain:
        return None
    # Attribution owns only the reported rating, not additional position plans.
    rating_actions = {"买入", "卖出", "增持", "减持", "買い", "売り"}
    if (attributed_rating_fact(plain)
            and all(match.group() in rating_actions for match in _EXECUTION_ACTION.finditer(plain))
            and not _POSITION_DIRECTIVE.search(plain)
            and not _ACTION_AMOUNT.search(plain)):
        return None
    action = _EXECUTION_ACTION.search(plain) or _ENGLISH_TRADE_ACTION.search(plain)
    actions = sorted(
        [*_EXECUTION_ACTION.finditer(plain), *_ENGLISH_TRADE_ACTION.finditer(plain)],
        key=lambda match: match.start(),
    )
    authorized_stop = any(not _non_authorizing_action(plain, match)
                          for match in _STOP_TRIGGER.finditer(plain))
    if actions and all(_non_authorizing_action(plain, match) for match in actions) and not (
        _ACTIONABLE_HOLDING_PERIOD.search(plain)
        or _POSITION_DIRECTIVE.search(plain)
        or _position_preservation_instruction(plain)
        or authorized_stop
    ):
        # Polarity/subject belong to the action, not to a conditional elsewhere.
        return None
    if _POSITION_DIRECTIVE.search(plain):
        return "POSITION_SIZE_RECOMMENDATION"
    if _position_preservation_instruction(plain):
        return "UNAPPROVED_EXECUTION_INSTRUCTION"
    if _POSITION_RECOMMENDATION.search(plain):
        return "POSITION_SIZE_RECOMMENDATION"
    if _ACTIONABLE_HOLDING_PERIOD.search(plain):
        return "UNAPPROVED_EXECUTION_INSTRUCTION"
    if _ACTIONABLE_HORIZON_FIELD.search(plain):
        return "UNAPPROVED_EXECUTION_INSTRUCTION"
    if _EXECUTION_TRIGGER_SETUP.search(plain):
        return "UNAPPROVED_EXECUTION_INSTRUCTION"
    if (
        _ACTION_AMOUNT.search(plain)
        or authorized_stop
        or _EXECUTION_TRIGGER_ACTION.search(plain)
        or _CONDITION_ACTION_LINK.search(plain)
    ):
        return "UNAPPROVED_EXECUTION_INSTRUCTION"
    # Action-bearing clauses already had action-local polarity checked above.
    # Only action-free withholding text can use a clause-wide exemption.
    if not action and _EXECUTION_PROHIBITION.search(plain):
        return None
    if _EXECUTION_INSTRUCTIONS.search(plain):
        return "UNAPPROVED_EXECUTION_INSTRUCTION"
    if _COMPOUND_TRADE_STRATEGY.search(plain):
        return "UNAPPROVED_EXECUTION_INSTRUCTION"
    imperative = _ENGLISH_TRADE_ACTION.match(plain)
    if imperative and re.match(
        r"\s+(?:the|a|an|your|this|that|shares|stock|position|at|below|above)\b",
        plain[imperative.end():], re.I,
    ):
        return "UNAPPROVED_EXECUTION_INSTRUCTION"
    if action and (
        _EXECUTION_DIRECTIVE_CONTEXT.search(plain)
        or _CONDITIONAL_EXECUTION.search(plain)
        or _ACTIONABLE_PRICE.search(plain)
        or _EXECUTION_OPPORTUNITY.search(plain)
    ):
        return "UNAPPROVED_EXECUTION_INSTRUCTION"
    if _EXECUTION_PARAMETER.search(plain):
        # A sourced valuation target is an analytical fact, not a trade exit.
        if re.search(r"(?:分析师|券商|consensus|analyst|broker).*(?:目标价|target)", plain, re.I):
            return None
        return "UNVALIDATED_EXECUTABLE_PLAN"
    return None


def _non_authorizing_action(text: str, action: re.Match[str]) -> bool:
    """Recognize action-local prohibition and explicitly descriptive agency.

    A company action in the antecedent does not excuse a later investor order.
    Likewise a prohibition never carries across contrast or clause boundaries.
    """
    before = re.split(r"[，,。；;!?！？]|→|⇒|=>|->", text[:action.start()])[-1]
    after = text[action.end():]
    # An opportunity is an authorization only when asserted, not withheld or
    # still awaiting confirmation. This scope belongs to this action/object.
    if re.search(r"(?:不存在|没有|尚无|未有|\bno\b)\s*$", before, re.I):
        return True
    if re.match(r"\s*(?:良机|机会|機會)\s*(?:尚未|未|没有|不)[^。；;]{0,12}(?:确认|確認|成立|出现|出現)", after):
        return True
    prohibitions = list(_EXECUTION_PROHIBITION.finditer(before))
    if prohibitions:
        # A later modal/coordination starts a new authorization scope. A ban
        # on chasing prices cannot negate a separate instruction to buy.
        tail = before[prohibitions[-1].end():]
        if not re.search(r"(?:但|且|并|而|可(?:以)?|应当|应该|建议|\b(?:but|and|then|can|may|should|recommend)\b)", tail, re.I):
            return True
    if action.group().casefold() in {"布局", "配置", "对冲", "對沖", "hedge", "hedging"}:
        # Business deployment has a different object from investor exposure.
        # Keep action-local negation above, and let price/portfolio context
        # take precedence over an incidental business noun.
        # Resolve the action's business object, not a short lexical window.
        # Agency can precede a comma (a production network, then its effect).
        # A later investor action must independently resolve to trade scope.
        context = text[:action.start()] + text[action.end():]
        business = _BUSINESS_EXECUTION_OBJECT.search(context)
        hedge = action.group().casefold() in {"对冲", "對沖", "hedge", "hedging"}
        operating_agency = re.search(
            r"公司|企业|生产(?:设备|基地|网络)|供应链|供應鏈|收入|成本|自然|"
            r"\b(?:company|firm|issuer|production|supply.chain|revenue|cost|natural)\b", context, re.I,
        )
        if business and (not hedge or operating_agency) and not _PORTFOLIO_EXECUTION_OBJECT.search(context):
            return True
    subject = re.search(r"(?:公司|企业|企業|发行人|發行人|\b(?:company|issuer|firm)\b)", before, re.I)
    target = text[action.end():]
    corporate_target = re.match(
        r"\s*(?:过|了|其|非核心|核心|部分|non-core\s+|core\s+|its\s+)?"
        r"(?:资产|資產|业务|業務|子公司|assets?\b|business\b|subsidiar)", target, re.I
    )
    if subject and corporate_target:
        return True
    historical = re.search(r"(?:历史上|歷史上|此前|去年|曾经|曾經|曾|\bhistorically\b|\blast year\b)", before, re.I)
    return bool(historical and not re.search(r"(?:建议|建議|应当|recommend|should)", before, re.I))


def _position_preservation_instruction(text: str) -> bool:
    """Position object + preservation predicate is a plan in either order.

    Keep the existing action-local polarity/history boundary. A generic
    business allocation or a call to remain observant has no portfolio object;
    a prohibition on adding later cannot waive an earlier keep-position plan.
    """
    for clause in re.split(r"[，,。；;!?！？]", text):
        if not _POSITION_ENTITY.search(clause):
            continue
        for predicate in _POSITION_PRESERVATION.finditer(clause):
            before = clause[:predicate.start()]
            corporate = re.search(r"公司|企业|企業|\b(?:company|issuer|firm)\b", before, re.I)
            investor_directive = re.search(
                r"投资者|投資者|建议|建議|应当|應當|\b(?:investors?|recommend|should|your)\b",
                clause, re.I,
            )
            operating_position = re.search(r"\b(?:competitive|leadership|business|industry)\s+position\b", clause, re.I)
            investor_recipient = re.search(r"投资者|投資者|用户|用戶|\b(?:investors?|your|you)\b", clause, re.I)
            if corporate and (not investor_directive or operating_position and not investor_recipient):
                continue
            if not _non_authorizing_action(clause, predicate):
                return True
    return False


def _prune_unapproved_execution(text: str) -> str:
    text = _filter_markdown_sections(
        text,
        lambda heading: not _is_execution_heading(heading),
    )
    lines = []
    for line, headers in _execution_lines(text):
        if line.strip() in {_TRADER_WITHHELD, _EXECUTION_WITHHELD}:
            lines.append(line)
            continue
        # A structured execution field is one logical unit. Splitting it at a
        # sentence boundary can orphan the dependent trigger clause and make
        # an unapproved plan appear without its label.
        if _EXECUTION_LINE.search(line):
            continue
        if line.lstrip().startswith("|"):
            if not _execution_violation(line, headers):
                lines.append(line)
            continue
        # Remove complete clauses, retaining independent directional judgments.
        clauses = re.split(r"(?<=[。！？；;])", line)
        lines.append("".join(clause for clause in clauses if not _execution_violation(clause)))
    return _clean_empty_markdown(_fold_empty_sections("\n".join(lines)))


def _apply_validated_execution(state: dict[str, Any]) -> dict[str, Any]:
    """Expose only calculator-approved execution numbers in the user artifact."""
    result = _map_report_text(state, _prune_unapproved_execution)
    validation = result.get("validated_execution") or {}
    decision = _strip_execution_plan(
        str(result.get("final_trade_decision") or ""), include_target=True
    )
    block = _validated_execution_report(validation)
    result["trader_investment_plan"] = block
    result["final_trade_decision"] = (decision.rstrip() + "\n\n" + block).strip()
    for field in ("market_report", "sentiment_report", "news_report", "fundamentals_report"):
        if isinstance(result.get(field), str):
            result[field] = _remove_unapproved_execution_lines(result[field])
    research = result.get("investment_debate_state")
    if isinstance(research, Mapping):
        research = dict(research)
        if isinstance(research.get("judge_decision"), str):
            research["judge_decision"] = _remove_execution_sections(research["judge_decision"])
        result["investment_debate_state"] = research
    risk = result.get("risk_debate_state")
    if isinstance(risk, Mapping):
        risk = dict(risk)
        risk["judge_decision"] = result["final_trade_decision"]
        result["risk_debate_state"] = risk
    return result


def _validated_execution_report(validation: Mapping[str, Any]) -> str:
    lines = [
        "## 已验证交易执行参数",
        "",
        "| 项目 | 确定性校验值 |",
        "|---|---:|",
        f"| 交易方向 | {'买入' if validation.get('action') == 'Buy' else '卖出'} |",
        f"| 入场价 | {validation.get('entry')} |",
        f"| 止损价 | {validation.get('stop')} |",
        f"| 止损距离 | {validation.get('distance')} |",
        f"| 入场至止损风险 | {validation.get('risk_pct')}% |",
    ]
    if validation.get("position_pct") is not None:
        lines.append(f"| 仓位 | {validation['position_pct']}% |")
    if validation.get("portfolio_stop_risk_pct") is not None:
        lines.append(f"| 组合止损风险 | {validation['portfolio_stop_risk_pct']}% |")
    return "\n".join(lines)


def _remove_jsf_agent_claims(text: str) -> str:
    """Keep JSF observable facts in its deterministic source section only."""
    text = _filter_markdown_sections(text, lambda heading: _JSF_CLAIM.search(heading) is None)
    lines = []
    for line in text.splitlines():
        if _JSF_CLAIM.search(line) and not line.lstrip().startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines)


def _remove_execution_sections(text: str) -> str:
    text = _filter_markdown_sections(
        text,
        lambda heading: not _is_execution_heading(heading),
    )
    lines = []
    for line in text.splitlines():
        if _EXECUTION_LINE.search(line):
            continue
        lines.append(line)
    return _fold_empty_sections("\n".join(lines))


def _is_execution_heading(heading: str) -> bool:
    """Classify execution sections without treating valuation facts as plans."""
    folded = heading.strip().casefold()
    if folded in _WITHHELD_EXECUTION_HEADINGS:
        return False
    if re.search(r"(?:分析师|券商|consensus|analyst|broker).*(?:目标价|target)", folded):
        return False
    # "Execution" alone is ambiguous: an issuer executes orders, capex and
    # buybacks too. A business heading cannot waive a real trade instruction
    # in the heading or in its independently scanned subordinate content.
    if (_CORPORATE_OPERATION.search(folded)
            and not _execution_clause_violation(folded)
            and all(match.group().casefold() in {"执行", "execution"}
                    for match in _EXECUTION_HEADING.finditer(folded))
            and not _PORTFOLIO_EXECUTION_OBJECT.search(_CORPORATE_OPERATION.sub("", folded))):
        return False
    # A strategy-bearing title owns its subordinate trigger/parameter context.
    # Dropping only the title would orphan conditional plan fragments and can
    # make a hypothetical price trigger read like a current market assertion.
    return _EXECUTION_HEADING.search(folded) is not None or _execution_clause_violation(folded) is not None


def _strip_execution_plan(text: str, *, include_target: bool) -> str:
    text = _remove_execution_sections(text)
    return _remove_unapproved_execution_lines(text, include_target=include_target)


def _remove_unapproved_execution_lines(text: str, *, include_target: bool = False) -> str:
    """Remove numeric execution instructions, while retaining factual target news."""
    terms = r"entry|stop(?:[ -]?loss)?|position(?: sizing)?|入场|建仓|止损|止盈|仓位"
    if include_target:
        terms += r"|price target|目标价|第一目标|第二目标"
    execution_term = re.compile(rf"(?:{terms})", re.I)
    numeric = re.compile(r"\d")
    lines = [
        line
        for line in text.splitlines()
        if not (execution_term.search(line) and numeric.search(line))
    ]
    return _fold_empty_sections("\n".join(lines))


def _publicize_state_text(state: dict[str, Any]) -> dict[str, Any]:
    return _map_report_text(state, lambda text: _fold_empty_sections(_publicize_inline_text(text)))


def _publicize_inline_text(text: str) -> str:
    """Deterministic display transforms, also used to preserve audit lineage."""
    for internal, display in _PUBLIC_NEWS_TEXT.items():
        text = text.replace(internal, display)
    for internal in sorted(_INTERNAL_STATUS, key=len, reverse=True):
        text = re.sub(
            rf"(?<![A-Za-z0-9_]){re.escape(internal)}(?![A-Za-z0-9_])",
            _INTERNAL_STATUS[internal], text,
        )
    text = text.replace("DATA UNAVAILABLE", "数据不可用")
    for value, label in (("true", "可用于当前判断"), ("false", "不可用于当前判断")):
        text = re.sub(rf"\bcurrent_eligible\s*=\s*{value}\b", label, text, flags=re.I)
    text = _INTERNAL_ENGINEERING_ASSIGNMENT.sub("", text)
    text = re.sub(r"[（(][\s,，;；/]*[）)]", "", text)
    # All-tool parentheses are omitted; standalone identifiers retain a source label.
    text = re.sub(
        r"[（(]\s*(?:(?:get|fetch|load|resolve|retrieve|query|search)_[a-z][a-z0-9_]*\s*[、,，/]?\s*)+[）)]",
        "", text, flags=re.I,
    )
    text = _INTERNAL_TOOL_IDENTIFIER.sub("上游数据源", text)
    # Internal enforcement labels are not user-facing financial terminology.
    text = re.sub(r"(?:关键数据|关键指标|证据|数据)门控\s*[:：]?\s*", "", text)
    return _localize_presentation_labels(text)


def _drop_deprecated_horizon_metadata(state: dict[str, Any]) -> dict[str, Any]:
    """Remove the retired user-selected horizon without touching source windows.

    ``window_policy`` belongs to source freshness/lookback contracts and is
    deliberately preserved.  This migration also makes archived states safe
    to replay under the current contract.
    """
    result = dict(state)
    result.pop("trading_horizon", None)
    constraints = result.get("trade_constraints")
    if isinstance(constraints, Mapping):
        constraints = dict(constraints)
        constraints.pop("horizon", None)
        result["trade_constraints"] = constraints
    bundle = result.get("japan_data_bundle")
    if isinstance(bundle, Mapping):
        bundle = dict(bundle)
        bundle.pop("trading_horizon", None)
        result["japan_data_bundle"] = bundle
    return result


def _has_deprecated_horizon_state(state: Mapping[str, Any]) -> bool:
    constraints = state.get("trade_constraints")
    bundle = state.get("japan_data_bundle")
    accepted = state.get("accepted_report_markdown")
    return bool(
        "trading_horizon" in state
        or (isinstance(constraints, Mapping) and "horizon" in constraints)
        or (isinstance(bundle, Mapping) and "trading_horizon" in bundle)
        or (isinstance(accepted, str) and _DEPRECATED_USER_HORIZON_CLAIM.search(accepted))
    )


def _remove_deprecated_user_horizon_claims(text: str) -> str:
    """Drop prose that attributes an investment horizon to a user choice.

    Agents may still recommend their own optional holding period, matching
    upstream TradingAgents.  Only the retired *user-selected* horizon
    attribution is removed, including during deterministic legacy replay.
    """
    output: list[str] = []
    for line in text.splitlines():
        if not _DEPRECATED_USER_HORIZON_CLAIM.search(line):
            output.append(line)
            continue
        if line.lstrip().startswith("|"):
            continue
        clauses = re.split(r"(?<=[。！？；;])|(?<=[.!?])\s+", line)
        retained = [
            clause
            for clause in clauses
            if clause.strip() and not _DEPRECATED_USER_HORIZON_CLAIM.search(clause)
        ]
        output.append("".join(retained))
    return _fold_empty_sections("\n".join(output))


def _localize_presentation_labels(text: str) -> str:
    """Translate standard report labels without rewriting business prose."""
    output: list[str] = []
    labels = "|".join(re.escape(label) for label in _PRESENTATION_HEADING_TRANSLATIONS)
    pattern = re.compile(
        rf"(?i)(?P<prefix>^(?:#{{1,6}}\s+)?|^\*\*)"
        rf"(?P<label>{labels})(?P<suffix>\*\*)?(?P<colon>\s*[:：])?"
    )
    for line in text.splitlines():
        match = pattern.search(line)
        if match:
            source = match.group("label").casefold()
            translated = _PRESENTATION_HEADING_TRANSLATIONS[source]
            line = line[: match.start("label")] + translated + line[match.end("label") :]
        # A model may put a Markdown heading in the value of a labelled field,
        # e.g. ``**Strategic Actions**: ## Plan``.  Once it is inline the
        # heading marker has no structural meaning and would be printed raw.
        line = re.sub(r"([:：])\s*#{1,6}\s+", r"\1 ", line)
        output.append(line)
    return "\n".join(output)


def _map_report_text(state: Mapping[str, Any], transform) -> dict[str, Any]:
    result = dict(state)
    for field in _REPORT_FIELDS:
        if isinstance(result.get(field), str):
            result[field] = transform(result[field])
    for field in _DEBATE_FIELDS:
        debate = result.get(field)
        if isinstance(debate, Mapping):
            result[field] = {
                key: transform(value) if isinstance(value, str) else value
                for key, value in debate.items()
            }
    return result


def _validate_final_artifact(
    state: Mapping[str, Any],
    execution_allowed: bool,
    *,
    accepted_report: str,
) -> list[str]:
    # The accepted report is the sole user artifact.  Raw reports and debate
    # histories remain available in full_agent_log for technical audit, but
    # validating those omitted fragments here would let non-published prose
    # falsely block a clean canonical artifact.
    issues = validate_final_report_text(
        accepted_report, execution_allowed=execution_allowed, check_structure=True
    )
    unauthorized = _artifact_without_validated_execution(state, accepted_report) if execution_allowed else accepted_report
    issues.extend(
        "FINAL_ARTIFACT_UNAUTHORIZED_EXECUTION:"
        + hashlib.sha256(claim.encode("utf-8")).hexdigest()
        for claim in _artifact_execution_claims(unauthorized)
    )
    issues.extend(_execution_cross_state_issues(state, execution_allowed))
    issues.extend(_domain_authority_issues(state))
    issues.extend(
        "CROSS_DOMAIN_AUTHORITY:INVESTMENT_RATING:"
        + hashlib.sha256(claim.text.encode("utf-8")).hexdigest()
        for claim in _rating_artifact_claims(state, accepted_report)
    )
    # Independent exact-artifact defense: no upstream finding or pruning is
    # needed for an unsupported current technical claim to block publication.
    issues.extend(
        "CROSS_DOMAIN_AUTHORITY:MARKET:"
        + claim.sha256
        for claim in _artifact_market_claims(state, accepted_report)
    )
    # Re-run the existing Actual gate on the exact artifact, independently of
    # upstream enforcement/Audit. A qualitative current-quarter assertion is
    # not exempt merely because it contains no precise number.
    issues.extend(
        "CROSS_DOMAIN_AUTHORITY:"
        + ("FINANCIAL:" if finding.warning == "critical_gate_bypassed" else "NEWS_PROBABILITY:")
        + finding.claim_sha256
        for finding in _artifact_evidence_gate_findings(state, accepted_report)
    )
    rendered = render_markdown_fragment(accepted_report)
    issues.extend(validate_rendered_html(rendered))
    return list(dict.fromkeys(issues))


def _artifact_evidence_gate_findings(state: Mapping[str, Any], text: str):
    """Reuse evidence gates, but inspect the final published bytes."""
    return [finding for finding in enforce_agent_output(state, text, "Canonical Final State").findings
            if finding.warning in {"critical_gate_bypassed", "probability_event_mismatch"}]


def _artifact_without_validated_execution(state: Mapping[str, Any], text: str) -> str:
    """Exempt only an exact deterministic plan in its two publication slots.

    Approval is not a blanket permission for Analyst prose, or for a modified
    plan even in the Trader/Portfolio section. Compare all canonical values;
    Markdown heading nesting and delimiter spacing are presentation only.
    """
    validation = state.get("validated_execution") or {}
    if validation.get("status") != "OK":
        return text
    expected = [_table_cells(line) for line in _validated_execution_report(validation).splitlines()
                if line.startswith("|") and not all(re.fullmatch(r":?-+:?", cell) for cell in _table_cells(line))]
    lines = text.splitlines(keepends=True)
    kept = []
    wrapper = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        heading = re.match(r"^##\s+(.+)$", line.strip())
        if heading:
            wrapper = heading[1]
        owner = bool(re.fullmatch(r"(?:[IVX]+\.\s*)?(?:交易团队计划|投资组合经理结论)", wrapper))
        if owner and re.fullmatch(r"#{3,6}\s+已验证交易执行参数", line.strip()):
            end = index + 1
            while end < len(lines) and not lines[end].strip():
                end += 1
            start = end
            while end < len(lines) and lines[end].lstrip().startswith("|"):
                end += 1
            rows = [_table_cells(row) for row in lines[start:end]
                    if not all(re.fullmatch(r":?-+:?", cell) for cell in _table_cells(row))]
            if rows == expected:
                index = end
                continue
        kept.append(line)
        index += 1
    return "".join(kept)


def _artifact_execution_claims(text: str) -> list[str]:
    """Return exact unauthorized claims from the final publishable artifact.

    This deliberately runs after composition and structural normalization.  It
    is the Final Output Contract's independent defense if field-level finding
    collection or pruning ever misses a newly formatted claim.
    """
    claims: list[str] = []
    for line, headers in _execution_lines(text):
        if line.strip() in {_TRADER_WITHHELD, _EXECUTION_WITHHELD}:
            continue
        if line.strip().startswith("|"):
            if _table_execution_violation(_table_cells(line), headers):
                claims.append(line.strip())
            continue
        for unit in _execution_semantic_units(line):
            if _execution_clause_violation(unit):
                claims.append(unit)
    # Independently recover row/header relations from the actual renderer,
    # including raw HTML tables. No upstream findings or Markdown cell-splitting
    # decisions are trusted at this final boundary.
    for headers, cells in rendered_table_rows(render_markdown_fragment(text)):
        if _table_execution_violation(cells, headers):
            claims.append("| " + " | ".join(cells) + " |")
    return list(dict.fromkeys(claims))


def _execution_cross_state_issues(state: Mapping[str, Any], execution_allowed: bool) -> list[str]:
    """Reject executable plans without two compatible decision authorities."""
    if not execution_allowed:
        validation = state.get("validated_execution") or {}
        return ["EXECUTION_WITHHELD_PARAMETERS_PRESENT"] if any(
            validation.get(field) is not None for field in EXECUTION_PLAN_FIELDS
        ) else []
    validation = state.get("validated_execution") or {}
    action = validation.get("action")
    rating = validation.get("portfolio_rating") or parse_explicit_rating(
        str(state.get("final_trade_decision") or "")
    )
    if action not in {"Buy", "Sell"}:
        return ["EXECUTION_ACTION_NOT_AUTHORIZED"]
    compatible = (action == "Buy" and rating in {"Buy", "Overweight"}) or (
        action == "Sell" and rating in {"Sell", "Underweight"}
    )
    return [] if compatible else ["EXECUTION_PORTFOLIO_RATING_CONFLICT"]


def validate_final_report_text(
    text: str, *, execution_allowed: bool, check_structure: bool = True
) -> list[str]:
    """Detect violations in accepted text or a composed artifact; never edit it."""
    issues: list[str] = []
    for token in _INTERNAL_STATUS:
        if re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", text):
            issues.append(f"INTERNAL_STATUS_VISIBLE:{token}")
    for token in _MACHINE_ENUM.findall(text):
        issues.append(f"INTERNAL_MACHINE_ENUM_VISIBLE:{token}")
    if _INTERNAL_ENGINEERING_ASSIGNMENT.search(text):
        issues.append("INTERNAL_ENGINEERING_METADATA_VISIBLE")
    if _INTERNAL_TOOL_IDENTIFIER.search(text):
        issues.append("INTERNAL_TOOL_IDENTIFIER_VISIBLE")
    if re.search(r"(?:关键数据|关键指标|证据|数据)门控", text):
        issues.append("INTERNAL_GATE_LABEL_VISIBLE")
    if re.search(r"(?:无|没有|缺乏)(?:明显|任何)?(?:轧空|軋空|short[ -]?squeeze)", text, re.I):
        issues.append("SHORT_MARKET_OVERCLAIM")
    if _remove_generation_process_prose(text) != text:
        issues.append("PROCESS_PROSE_VISIBLE")
    if _unlocalized_presentation_label(text):
        issues.append("UNLOCALIZED_PRESENTATION_LABEL")
    if re.search(r"[:：]\s*#{1,6}\s+", text):
        issues.append("INLINE_MARKDOWN_HEADING_VISIBLE")
    for internal in _PUBLIC_NEWS_TEXT:
        if internal in text:
            issues.append("UNLOCALIZED_NEWS_AUTHORITY")
    for line, headers in _execution_lines(text):
        if line.strip() in {_TRADER_WITHHELD, _EXECUTION_WITHHELD}:
            continue
        if not execution_allowed:
            if _heading_level(line) and _is_execution_heading(line.lstrip("# ")):
                issues.append("UNAPPROVED_EXECUTION_SECTION")
            if _EXECUTION_LINE.search(line):
                issues.append("UNVALIDATED_EXECUTABLE_PLAN")
            violation = _execution_violation(line, headers)
            if violation:
                issues.append(violation)
    if _clean_empty_markdown(text) != re.sub(r"\n{3,}", "\n\n", text).strip():
        issues.append("EMPTY_MARKDOWN_STRUCTURE")
    lines = text.splitlines()
    if any(
        not _table_has_data_row(lines[start:end])
        for start, end in _table_blocks(lines, 0, len(lines))
    ):
        issues.append("EMPTY_MARKDOWN_TABLE")
    if check_structure:
        issues.extend(_markdown_structure_issues(text))
    return list(dict.fromkeys(issues))


def _domain_authority_issues(state: Mapping[str, Any]) -> list[str]:
    """Ensure an authoritative aggregate is published in exactly one domain."""
    issues: list[str] = []
    for field in _REPORT_FIELDS:
        if field == "sentiment_report":
            continue
        value = state.get(field)
        if isinstance(value, str) and _SENTIMENT_AUTHORITY.search(value):
            issues.append(f"CROSS_DOMAIN_AUTHORITY:SENTIMENT:{field}")
    for outer in _DEBATE_FIELDS:
        value = state.get(outer)
        if not isinstance(value, Mapping):
            continue
        for inner, text in value.items():
            if isinstance(text, str) and _SENTIMENT_AUTHORITY.search(text):
                issues.append(f"CROSS_DOMAIN_AUTHORITY:SENTIMENT:{outer}.{inner}")
    return issues


def _unlocalized_presentation_label(text: str) -> bool:
    labels = "|".join(re.escape(label) for label in _PRESENTATION_HEADING_TRANSLATIONS)
    return (
        re.search(
            rf"(?im)^(?:#{{1,6}}\s+|\*\*)?(?:{labels})(?:\*\*)?\s*(?::|$)",
            text,
        )
        is not None
    )


def _published_field_text(state: Mapping[str, Any], field: str) -> str | None:
    if field in {"market_report", "sentiment_report", "news_report", "fundamentals_report"}:
        value = state.get(field)
        return value if isinstance(value, str) else None
    if field in {"trader_investment_plan", "final_trade_decision"}:
        value = state.get(field)
        return value if isinstance(value, str) else None
    if field in {"investment_debate_state.judge_decision", "risk_debate_state.judge_decision"}:
        outer, inner = field.split(".", 1)
        container = state.get(outer)
        value = container.get(inner) if isinstance(container, Mapping) else None
        return value if isinstance(value, str) else None
    return None


def _finalize_audit(
    audit: list[dict[str, Any]],
    state: Mapping[str, Any],
    *,
    accepted_report: str,
    execution_allowed: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Close findings only when their concrete claim is absent or supported.

    A warning code identifies a rule, not a claim.  Re-running that rule over a
    whole report field can therefore falsely close one finding after a sibling
    clause was removed.  Closure follows the recorded original/replacement
    claim identity and the exact accepted artifact instead.
    """
    finalized: list[dict[str, Any]] = []
    issues: list[str] = []
    seen: set[tuple[Any, ...]] = set()
    for raw in audit:
        entry = dict(raw)
        if entry.get("semantic_type") == "PROCESS_NARRATION":
            surviving = bool(_generation_process_claims(accepted_report))
            entry["accepted_artifact_sha256"] = hashlib.sha256(accepted_report.encode("utf-8")).hexdigest()
            entry["resolution"] = "UNRESOLVED" if surviving else "CLAIM_REMOVED_OR_REPLACED"
            entry["resolution_basis"] = "PROCESS_NARRATION_PRESENT" if surviving else "PROCESS_NARRATION_ABSENT_FROM_EXACT_ARTIFACT"
            entry["execution_blocking"] = surviving
            if surviving:
                issues.append("UNRESOLVED_PROCESS_NARRATION")
        elif entry.get("category") == "MARKET_AUTHORITY_CLAIM":
            survivors = _artifact_market_claims(state, accepted_report)
            surviving = any(
                claim.semantic_type == entry.get("semantic_type")
                for claim in survivors
            )
            entry["accepted_artifact_sha256"] = hashlib.sha256(
                accepted_report.encode("utf-8")
            ).hexdigest()
            entry["resolution"] = (
                "UNRESOLVED" if surviving else "CLAIM_REMOVED_OR_REPLACED"
            )
            entry["resolution_basis"] = (
                "SAME_CLASS_CURRENT_MARKET_CLAIM_IN_EXACT_ARTIFACT"
                if surviving else "MARKET_CLAIM_CLASS_ABSENT_FROM_EXACT_ARTIFACT"
            )
            entry["execution_blocking"] = surviving
            if surviving:
                issues.append(
                    f"UNRESOLVED_MARKET_CLAIM:{entry.get('field')}:{entry.get('claim_sha256')}"
                )
        elif entry.get("category") == "SECONDARY_INTERNAL_RATING":
            # Presentation changes cannot close a surviving secondary rating.
            # Re-scan the exact artifact by ownership and canonical rating,
            # not literal equality of an Agent field before composition.
            survivors = _rating_artifact_claims(state, accepted_report)
            surviving = any(claim.rating == entry.get("rating")
                            and (claim.rating is not None or claim.semantic_type == entry.get("recommendation_semantics"))
                            for claim in survivors)
            entry["accepted_artifact_sha256"] = hashlib.sha256(accepted_report.encode("utf-8")).hexdigest()
            entry["resolution"] = "UNRESOLVED" if surviving else "CLAIM_REMOVED_OR_REPLACED"
            entry["resolution_basis"] = "SECONDARY_RATING_AUTHORITY_PRESENT" if surviving else "SECONDARY_RATING_AUTHORITY_ABSENT_FROM_EXACT_ARTIFACT"
            entry["execution_blocking"] = surviving
            if surviving:
                issues.append(f"UNRESOLVED_RATING_CLAIM:{entry.get('field')}:{entry.get('claim_sha256')}")
        elif entry.get("category") == "UNAPPROVED_EXECUTION_CLAIM":
            original = str(entry.get("original_claim") or "").strip()
            entry["accepted_artifact_sha256"] = hashlib.sha256(
                accepted_report.encode("utf-8")
            ).hexdigest()
            # Compare presentation-stable components as well as whole claims.
            # A source-label/numbering change is not removal of the action.
            unauthorized = _artifact_without_validated_execution(state, accepted_report) if execution_allowed else accepted_report
            surviving = _execution_claim_survives(original, unauthorized)
            entry["presentation_claim_sha256"] = hashlib.sha256(
                _claim_identity(original).encode("utf-8")
            ).hexdigest()
            if original and not surviving:
                entry["resolution"] = "CLAIM_REMOVED_OR_REPLACED"
                entry["resolution_basis"] = (
                    "UNAUTHORIZED_CLAIM_ABSENT_OUTSIDE_EXACT_VALIDATOR_OWNED_BLOCKS"
                    if execution_allowed else
                    "EXECUTION_CLAIM_COMPONENTS_ABSENT_FROM_EXACT_ACCEPTED_ARTIFACT"
                )
                entry["execution_blocking"] = False
            else:
                entry["resolution"] = "UNRESOLVED"
                entry["execution_blocking"] = True
                issues.append(
                    f"UNRESOLVED_EXECUTION_CLAIM:{entry.get('field')}:{entry.get('claim_sha256')}"
                )
        elif entry.get("category") in {
            "EXECUTION_ACTION_CONSISTENCY",
            "EXECUTION_GATE",
        }:
            execution_issues = (
                []
                if execution_allowed
                else [
                    issue
                    for issue in validate_final_report_text(
                        accepted_report,
                        execution_allowed=False,
                        check_structure=False,
                    )
                    if issue.startswith(("UNAPPROVED_", "UNVALIDATED_", "POSITION_SIZE_"))
                ]
            )
            if execution_issues:
                entry["resolution"] = "UNRESOLVED"
                entry["execution_blocking"] = True
                issues.extend(f"UNRESOLVED_EXECUTION:{issue}" for issue in execution_issues)
            else:
                entry["resolution"] = "EXECUTABLE_PLAN_WITHHELD"
                entry["resolution_basis"] = "EXACT_ACCEPTED_ARTIFACT_HAS_NO_EXECUTABLE_PLAN"
                entry["execution_blocking"] = False
        elif entry.get("category") in _VIOLATION_CATEGORIES or entry.get("warning"):
            field = str(entry.get("field") or "")
            published = _published_field_text(state, field)
            if published is None:
                entry["resolution"] = "NOT_PUBLISHED_IN_FINAL_ARTIFACT"
                entry["resolution_basis"] = "FIELD_EXCLUDED_FROM_ACCEPTED_REPORT"
                entry["execution_blocking"] = False
            else:
                warning = entry.get("warning")
                entry["published_field_sha256"] = hashlib.sha256(
                    published.encode("utf-8")
                ).hexdigest()
                entry["accepted_artifact_sha256"] = hashlib.sha256(
                    accepted_report.encode("utf-8")
                ).hexdigest()
                original = str(entry.get("original_claim") or "").strip()
                replacement = str(entry.get("replacement_claim") or "").strip()
                original_published = bool(
                    original and _claim_is_published(original, accepted_report)
                )
                replacement_published = bool(
                    replacement and _claim_is_published(replacement, accepted_report)
                )
                replacement_supported = bool(
                    replacement_published
                    and warning
                    and warning
                    not in enforce_agent_output(
                        state, replacement, _agent_for_field(field)
                    ).warnings
                )
                if (
                    warning
                    and original
                    and not original_published
                    and (not replacement_published or replacement_supported)
                ):
                    entry["resolution"] = "CLAIM_REMOVED_OR_REPLACED"
                    entry["resolution_basis"] = (
                        "ORIGINAL_CLAIM_ABSENT_FROM_EXACT_ACCEPTED_ARTIFACT"
                        if not replacement_published
                        else "SUPPORTED_REPLACEMENT_PRESENT_IN_EXACT_ACCEPTED_ARTIFACT"
                    )
                    entry["execution_blocking"] = False
                elif not warning and entry.get("category") == "ARITHMETIC_MISMATCH":
                    entry["resolution"] = "REPLACED_BY_VALIDATED_EXECUTION"
                    entry["resolution_basis"] = "DETERMINISTIC_EXECUTION_BLOCK"
                    entry["execution_blocking"] = False
                else:
                    entry["resolution"] = "UNRESOLVED"
                    entry["execution_blocking"] = True
                    issue = f"UNRESOLVED_EVIDENCE:{field}:{warning or entry.get('category')}"
                    issues.append(issue)
        key = (
            entry.get("category"),
            entry.get("agent"),
            entry.get("field"),
            entry.get("warning"),
            entry.get("claim_sha256"),
            entry.get("detail"),
        )
        if key not in seen:
            finalized.append(entry)
            seen.add(key)
    # No upstream rating finding is required to block audit closure. Otherwise
    # a missed collection could report CLOSED beside a blocked final artifact.
    issues.extend(
        "UNRESOLVED_RATING_AUTHORITY:"
        + hashlib.sha256(claim.text.encode("utf-8")).hexdigest()
        for claim in _rating_artifact_claims(state, accepted_report)
    )
    issues.extend(
        "UNRESOLVED_EVIDENCE_AUTHORITY:" + finding.claim_sha256
        for finding in _artifact_evidence_gate_findings(state, accepted_report)
    )
    unauthorized = _artifact_without_validated_execution(state, accepted_report) if execution_allowed else accepted_report
    issues.extend("UNRESOLVED_EXECUTION_AUTHORITY:" + hashlib.sha256(claim.encode("utf-8")).hexdigest()
                  for claim in _artifact_execution_claims(unauthorized))
    return finalized, list(dict.fromkeys(issues))


def _claim_is_published(claim: str, accepted_report: str) -> bool:
    """Match one whole published claim, never a substring of another claim."""
    normalized_claim = _claim_identity(claim)
    if not normalized_claim:
        return False
    candidates: list[str] = []
    for line in accepted_report.splitlines():
        normalized_line = _claim_identity(line)
        if normalized_line:
            candidates.append(normalized_line)
        candidates.extend(
            _claim_identity(clause)
            for clause in re.split(r"(?<=[。！？；;.!?])", line)
            if clause.strip()
        )
    return normalized_claim in candidates


def _claim_identity(text: str) -> str:
    """Ignore only deterministic presentation transforms, never business values."""
    text = _publicize_inline_text(text)
    text = re.sub(r"[*`#]", "", text)
    text = re.sub(r"(?m)^\s*(?:[-+]\s+|\(?\d+\)[.、]?\s*|\d+[.、]\s+|[①-⑳]\s*)", "", text)
    return re.sub(r"\s+", " ", text).strip().rstrip("。.;；")


def _execution_claim_survives(original: str, artifact: str) -> bool:
    if _claim_is_published(original, artifact):
        return True
    # A row may lose an incidental provenance cell, or prose may be split into
    # bullets. Retain action-bearing components and compare whole components,
    # not substrings such as Buy inside a withholding sentence.
    components = {
        _claim_identity(unit)
        for unit in _execution_semantic_units(original)
        if _EXECUTION_ACTION.search(unit) or _ENGLISH_TRADE_ACTION.search(unit)
    }
    for line in artifact.splitlines():
        if components.intersection(_claim_identity(unit) for unit in _execution_semantic_units(line)):
            return True
    for _, cells in rendered_table_rows(render_markdown_fragment(artifact)):
        if components.intersection(_claim_identity(cell) for cell in cells):
            return True
    # When composition changes clause boundaries, do not claim proof of removal
    # while an unauthorized action of the same identity remains. This is
    # intentionally conservative: unrelated remaining violations must be fixed
    # before closure, rather than guessing which original finding they belong to.
    actions = {m.group().casefold() for pattern in (_EXECUTION_ACTION, _ENGLISH_TRADE_ACTION)
               for m in pattern.finditer(original)}
    for claim in _artifact_execution_claims(artifact):
        remaining = {m.group().casefold() for pattern in (_EXECUTION_ACTION, _ENGLISH_TRADE_ACTION)
                     for m in pattern.finditer(claim)}
        if actions & remaining:
            return True
    return False
