# TradingAgents JP/US Enhanced Edition

> 基于 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 的增强分支。
> 本分支重点强化 **日本股票（日股）分析**，同时保持原有 **美股 / US 路径** 的数据源、Agent Graph 与行为隔离，不将项目改造成 Japan-only。

---

## 免责声明

本项目仅用于研究、学习与辅助分析，不构成投资建议、交易建议或任何形式的收益承诺。

LLM 输出具有非确定性；市场数据、新闻、社区情绪、分析师预期以及第三方页面结构也可能变化。请在真实交易前自行核验关键事实、价格、财报、公告与风险。

---

## 项目定位

原版 TradingAgents 通过多 Agent 协作完成技术面、基本面、新闻、情绪、研究辩论、Trader、风险管理与最终 Portfolio Manager 决策。

本分支在保留原有框架的基础上，增加了：

- 日股 / 美股市场自动识别
- Japan Bundle 日股专用数据总线
- J-Quants V2 官方数据
- EDINET 大量保有 / 变更报告
- TDnet / Company IR 官方披露正文解析
- 日证金（JSF）信用供需历史趋势
- 日本 + 美国跨市场宏观上下文
- 日本新闻层
- 日本投资者情绪层
- 日本分析师预期 / 共识层
- Source-of-Truth / Evidence Integrity
- Agent 输出证据强制校验
- Decision Context / 决策一致性
- 交易周期对应的数据窗口
- Bull/Bear / Risk 详细辩论单独归档
- LLM hard deadline、structured-output capability 与稳定性增强

---

# 核心架构

```text
Ticker
  │
  ▼
MarketResolver / InstrumentContext
  │
  ├──────────── US ────────────────┐
  │                                 │
  │                         原有 US Providers
  │                         FRED / Yahoo / Reddit
  │                         StockTwits / Alpha Vantage ...
  │                                 │
  │                                 ▼
  │                          Existing Agent Graph
  │
  └──────────── JP ────────────────┐
                                    │
                             Japan Data Bundle
                                    │
              ┌─────────────────────┼──────────────────────┐
              │                     │                      │
           Market                Official               Context
              │                     │                      │
        Verified Snapshot       J-Quants             JP/US Macro
                              TDnet / IR               News
                              EDINET                   Sentiment
                              JSF                      Expectations
              └─────────────────────┼──────────────────────┘
                                    ▼
                           Evidence / Truth Layer
                                    ▼
                              Agent Graph
                                    ▼
                            Decision Context
                                    ▼
                       Trader → Risk → Portfolio
                                    ▼
                  Main Report + full_agent_log.md
```

---

# 市场识别与隔离

## 日本股票

推荐使用 Yahoo Finance 风格代码：

```text
6981.T
5801.T
8002.T
285A.T
```

也支持在解析层处理明确的 TSE / TYO 标识。

## 美国股票

```text
AAPL
NVDA
CRCL
SPY
```

### 隔离原则

- `market=JP`：加载 Japan Bundle。
- `market=US`：Japan Bundle 必须为 `{}`。
- 美股不会调用 J-Quants、TDnet、EDINET、JSF、日本新闻、日本掲示板或 Japan Macro Provider。
- 原有 US 数据源与 Agent 路径保持独立。

---

# 日股数据层

## 1. Verified Market Snapshot

**当前价格、OHLCV、技术指标的唯一权威入口。**

Agent 不允许：

- 从市值 / 流通股数反推当前价格
- 从分析师页面重新定义当前价格
- 用新闻中的旧价格覆盖当前行情
- 用其他 Provider 的价格静默覆盖 Verified Snapshot

如存在不同数据源价格差异，将区分：

```text
MATCH
CONFLICT
DIFFERENT_BASIS
STALE_SOURCE
UNCOMPARABLE_PERIOD
```

---

## 2. J-Quants API V2

用于日本股票的官方基础资料、日线和财务摘要。

已接入：

- `/v2/equities/master`
- `/v2/equities/bars/daily`
- `/v2/fins/summary`

账户权限允许时可继续扩展其他端点。

当前实现会：

- 区分原始价 / 复权价
- 保留财报期间
- 不做跨期间财务推导
- 429 / 403 / 可访问日期范围均明确保留
- 不使用 yfinance 静默覆盖冲突

> J-Quants 的历史范围、延迟和端点权限取决于你的订阅方案。不要在代码里假设所有账户拥有相同权限。

---

## 3. EDINET

主要用于：

- 大量保有报告
- 变更报告
- 当前持股比例
- 前次持股比例
- 增持 / 减持 / 不变
- 持有目的
- 报告义务发生日
- 提交日

结构化字段包括：

```text
submit_date
event_date
holder_name
issuer_name
security_code
current_holding_ratio
previous_holding_ratio
position_change
purpose_of_holding
document_id
source_url
extraction_status
```

严格规则：

- 只有当前比例和前次比例都能确认时，才判定 `INCREASED / DECREASED / UNCHANGED`
- 无法确定时为 `UNDETERMINED`
- PDF 解析失败时保留元数据并标记 `PARSE_FAILED`
- 不因为近期 0 条报告就推断“机构没有兴趣”

---

## 4. TDnet / Company IR

用于结构化官方事件：

- 决算 / 季度财报
- 业绩预测修正
- 配当
- 自社株回购
- 增资 / 稀释
- M&A
- 重大合同
- 业务重组
- 其他重大适时披露

主要字段：

```text
event_type
date
title
source
key_facts
key_numbers
guidance_change
dividend_change
buyback_amount
capital_policy_change
confidence
extraction_status
```

实现原则：

- 不只检查 IR landing page
- 自动扫描近期 IR 列表与资料页
- 优先读取官方 PDF 正文
- 标题与正文分类冲突时，以正文明确事实为准并保留冲突记录
- PDF 失败时 `PARSE_FAILED`，不补造内容

---

## 5. 日证金 JSF 信用供需

使用官方个股历史数据，不再只看单日。

已实现：

- 最近历史交易日数据
- 融资新规 / 偿还 / 余额
- 贷株新规 / 偿还 / 余额
- 5 日变化
- 20 日变化
- 连续增加 / 减少天数
- 新规 / 偿还比
- 连续去杠杆
- 融资快速堆积
- 贷株快速增加
- 极端跳变异常保护

单位严格来自官方字段。

例如官方是：

```text
股
```

就必须保存为：

```text
股
```

禁止 AI 推测成“百万日元”等其他单位。

缺少官方字段时：

```text
settlement_date = None
report_type = UNSPECIFIED
```

不进行猜测。

---

# 日本 + 美国跨市场宏观

对日股分析，不仅看日本，也保留美股隔夜影响。

## Japan Macro

包括可得数据：

- USD/JPY
- Nikkei 225
- 日本 10 年国债收益率
- BOJ 最新政策
- 日本 CPI（官方源优先）
- TOPIX（若可靠数据不可得则保持 unavailable）

## US / Overnight Context

包括：

- S&P 500
- Nasdaq 100
- Nasdaq Composite
- SOX
- VIX
- US 10Y
- Fed
- 美国 CPI / PCE / 就业
- DXY

市场类数据可计算：

```text
1D / 5D / 20D
```

宏观数据统一保存：

```text
value
timestamp
source
status
frequency
as_of
```

### 重要原则

- 不同频率不能混为“当前值”
- 月度 CPI 不得包装成实时数据
- BOJ 属于事件型政策数据
- Cross-market 只记录实际同时期变化与背景，不无依据宣称因果关系

---

# 日本新闻层

当前正式启用：

- Yahoo! Finance Japan
- 株探（Kabutan）

当前未作为有效正式源：

- Reuters Japan：没有配置授权、稳定的结构化 feed
- みんかぶ新闻：只有满足正文、真实时间、直接个股相关性时才进入
- 不用低质量链接凑数量

新闻进入 Japan Bundle 前必须满足：

1. 是真正新闻正文
2. 与目标 ticker / issuer 直接相关
3. 有可验证 `published_at`
4. 在分析时间窗口内
5. 完成重复事件聚类

过滤：

- 登录
- 注册
- 导航
- 广告
- 排行
- 相关推荐
- 关联股票链接
- 无发布时间条目
- 窗口外旧新闻

同一事件多家媒体报道时，Agent 默认按一个事件处理。

---

# 日本投资者情绪

当前正式启用：

- Yahoo! Finance Japan 掲示板

未启用：

- 5ch
- X / Twitter
- 不可验证的株探 / みんかぶ帖子流

## 缺失数据规则

```text
sample_count = 0
sentiment_score = None
status = DATA_UNAVAILABLE
```

**禁止：**

```text
Neutral 5.0
50%
```

用来代替无数据。

社区情绪：

- 权重低
- 只能辅助确认
- 不允许单独反转最终决策
- 不允许升级为官方事实

---

# 分析师预期 / Consensus

当前来源：

- みんかぶ公开证券分析师预测 / 共识页面

可获得时包括：

- 共识评级
- 分析师人数
- 平均目标价
- 一周前目标价比较
- FY 营收预期
- FY 净利润预期
- FY EPS 预期
- 30D / 90D 修正趋势

## EPS 单位

推荐输出：

```text
EPS: 193.29 JPY/share
```

中文：

```text
每股收益（EPS）：193.29 日元/股
```

不要仅写成 `¥193.29` 而不说明“每股”。

## 严格区分

```text
Company Guidance != Analyst Consensus
```

公司指引属于官方事实层。

分析师一致预期属于市场预期层。

两者不能覆盖、相加或混成同一个字段。

---

# Evidence Integrity / Source of Truth

本分支加入统一证据层。

关键 Evidence 尽可能保存：

```text
value
source
source_type
timestamp
as_of
frequency
status
confidence
unit
period
is_verified
```

## Source Type

```text
official
market_data
analyst_consensus
news
community_sentiment
derived
```

## 权威规则

| 数据 | Source of Truth |
|---|---|
| 当前价 / OHLCV / 技术指标 | Verified Market Snapshot |
| 公司正式业绩 / Guidance | TDnet / Company IR → J-Quants |
| Analyst Consensus | 独立预期层，不覆盖 Guidance |
| 大量保有 | EDINET |
| 信用供需 | JSF |
| 日本央行政策 | BOJ |
| 日本 CPI | 总务省统计局等官方源 |
| 市场指数 / 汇率 | 可靠市场行情源 |

---

# 财务期间保护

严格区分：

```text
FY
H1
Q1
Q2
Q3
Q4
TTM
Forecast
Analyst Estimate
```

禁止：

```text
TTM EPS - Q1 EPS
```

再除以剩余季度等无依据推导。

不同财年 / 不同期间只能在口径明确可比时比较。

---

# Freshness

不同数据类型拥有不同 freshness 逻辑。

状态包括：

```text
CURRENT
RECENT
STALE
DATA_UNAVAILABLE
```

不会使用一个统一时间阈值。

---

# Agent Evidence Enforcement

日股 Agent 在 LangGraph 统一节点出口执行轻量证据校验。

覆盖：

- Market Analyst
- Fundamentals Analyst
- News Analyst
- Sentiment Analyst
- Bull / Bear
- Research Manager
- Trader
- Aggressive / Conservative / Neutral Risk
- Portfolio Manager

会拦截：

- 非 Verified Snapshot 的“当前价格”
- 无来源精确目标价
- 无来源 EPS / 利润 / 余额
- 把 analyst estimate 当 company guidance
- 把社区情绪写成官方事实
- 把 stale 数据描述成当前
- FY / Quarter / TTM 混算

违规精确 claim 会进行句子级自然降级。

详细审计写入内部：

```text
evidence_audit
```

以及：

```text
full_agent_log.md
```

不污染主报告正文。

---

# Decision Context

最终决策链统一汇总：

```text
technical
fundamentals
official_catalysts
news
supply_demand
analyst_expectations
macro_cross_market
sentiment
risk
```

每项包含：

```text
direction
confidence
evidence_count
freshness
note
```

额外：

```text
overall_direction
overall_confidence
missing_dimensions
```

## 决策规则

```text
DATA_UNAVAILABLE != bearish
```

缺数据只应降低置信度或 coverage，不应自动变成利空。

同一事件：

```text
TDnet
Company IR
Yahoo News
Kabutan
```

只允许计为一个核心 catalyst。

新闻只作为传播 / 市场反应补充。

社区情绪权重很低，不能独立翻转结论。

---

# 交易周期与数据窗口

官方重大事件窗口根据交易周期调整：

| 交易周期 | 官方重大事件窗口 |
|---|---:|
| 日内 | 7 天 |
| 数日 | 14 天 |
| 数周 | 30 天 |
| 长期 | 45 天 |

其他维度保持各自独立窗口：

- 新闻：近期窗口
- 社区情绪：1D / 3D / 7D
- JSF：5D / 20D
- 分析师预期：30D / 90D
- 市场 / 宏观：1D / 5D / 20D

官方事件会保留：

```text
event_date
age_days
freshness
event_type
source
```

重大事件不会因为超过固定 7 日直接消失，但老旧事件也不会被包装成“刚发生”。

---

# 报告与调试 Log

## 默认报告

默认 Markdown / HTML 报告不再拼接：

- Bull / Bear 完整辩论全文
- Aggressive / Conservative / Neutral Risk 完整辩论全文

主报告只保留最终分析与必要结论。

## full_agent_log.md

完整多 Agent 推理与风控正文单独保存在：

```text
full_agent_log.md
```

用于：

- Debug
- 复盘
- Evidence audit
- Bull / Bear 辩论
- Research Manager
- Trader 原始方案
- Risk debate
- Portfolio Manager 原始决策

具体生成路径由每次 run 的报告归档目录决定。

---

# LLM 稳定性与 Timeout

默认：

```text
TRADINGAGENTS_LLM_TIMEOUT_SECONDS=180
```

含义：

- 单次 LLM 调用 hard deadline
- 不是整只股票分析的总时限
- `.env` 可覆盖

## TIMEOUT_CLIENT_DETACHED

当前同步 OpenAI-compatible SDK 调用在 Python 线程中运行。

如果达到 hard deadline：

- LangGraph 调用方立即得到 TimeoutError
- 主流程不再继续等待该调用
- 但底层 HTTP Provider 请求不保证能被 Python 安全强制取消

因此日志会明确标识：

```text
TIMEOUT_CLIENT_DETACHED
```

这不是 `CANCELLED`。

对 detached 请求不应立即自动重复发起相同 retry，避免后台旧请求仍运行时重复消耗 Provider 资源 / token。

---

# Structured Output Capability

不同 OpenAI-compatible Provider 并不假设拥有相同 structured-output 能力。

当前策略：

## DeepSeek

```text
FREE_TEXT_FALLBACK
```

已知不支持的模型不会反复发送会得到：

```text
This response_format type is unavailable now
```

的请求。

## MiniMax

保留已验证 tool-call schema 路径。

## Generic OpenAI-compatible

默认：

```text
FREE_TEXT_FALLBACK
```

只有 provider / model 明确声明支持时才使用原生 structured output。

---

# 数据源与 API Key

## 日股核心源

| 数据源 | 用途 | 是否需要 Key | 环境变量 |
|---|---|---:|---|
| J-Quants API V2 | 日股官方资料 / 日线 / 财务摘要 | 是 | `JQUANTS_API_KEY` |
| EDINET API V2 | 大量保有 / 变更报告 / PDF | 是 | `EDINET_API_KEY` |
| TDnet | 官方适时披露 | 否 | - |
| Company IR | 官方 IR PDF / 说明资料 | 否 | - |
| JSF / 日証金 | 融资 / 贷株历史 | 否 | - |
| BOJ | 日本央行政策 | 否 | - |
| 总务省统计局 | 日本 CPI | 否 | - |
| Yahoo Finance / yfinance | 行情 / 指数 / 汇率 | 否 | - |
| Yahoo Finance Japan | 日股新闻 | 否 | - |
| 株探 Kabutan | 日股新闻 | 否 | - |
| Yahoo 掲示板 | 日股情绪 | 否 | - |
| みんかぶ | 分析师共识 | 否 | - |

> 公开网页来源可能因页面结构、条款、反爬策略或发布时间字段变化而临时不可用。项目遵循“宁缺毋滥”，不以低质量数据填满 Bundle。

---

## 美国 / 宏观相关

| 数据源 | 用途 | 是否需要 Key | 环境变量 |
|---|---|---:|---|
| FRED | US10Y / Fed / CPI / PCE / 就业等 | 是 | `FRED_API_KEY` |
| Alpha Vantage | 原版可选股票 / 新闻 / 基本面源 | 可选 | `ALPHA_VANTAGE_API_KEY` |
| Reddit RSS | US 社区情绪 best-effort | 否 | - |
| StockTwits | 原版 US 情绪 | 视现有实现 | - |
| Yahoo Finance | US 行情 | 否 | - |

### Reddit 当前说明

当前 US 路径中的 Reddit RSS 可能出现：

```text
429 Too Many Requests
```

此时应快速降级为：

```text
RATE_LIMITED
```

而不是让整个股票分析失败。

本分支当前没有要求配置 Reddit OAuth Key。

---

# API Key 官方获取地址

## J-Quants

官方网站：

https://jpx-jquants.com/

步骤：

1. 注册 / 登录 J-Quants
2. 选择可用方案
3. 进入 Dashboard
4. 获取 API Key
5. 写入：

```env
JQUANTS_API_KEY=your_key
```

J-Quants API V2 使用 API Key 认证。

---

## EDINET

EDINET：

https://disclosure2.edinet-fsa.go.jp/

API Key 注册：

https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1

如果登录后 API 注册页面空白，请确认浏览器允许：

```text
https://api.edinet-fsa.go.jp/
```

的弹出窗口 / Pop-up。

写入：

```env
EDINET_API_KEY=your_key
```

---

## FRED

API Key 页面：

https://fred.stlouisfed.org/docs/api/api_key.html

写入：

```env
FRED_API_KEY=your_key
```

---

## DeepSeek

开放平台：

https://platform.deepseek.com/

写入：

```env
DEEPSEEK_API_KEY=your_key
```

---

## OpenAI

API Platform：

https://platform.openai.com/

写入：

```env
OPENAI_API_KEY=your_key
```

---

## Google Gemini

Google AI Studio：

https://ai.google.dev/aistudio

本项目沿用 TradingAgents 当前环境变量名：

```env
GOOGLE_API_KEY=your_key
```

> Google 官方 Gemini 文档可能使用 `GEMINI_API_KEY`，但请以本仓库当前代码读取的变量名为准，不要自行改名。

---

## Anthropic

Console：

https://console.anthropic.com/

```env
ANTHROPIC_API_KEY=your_key
```

---

## xAI

Console：

https://console.x.ai/

```env
XAI_API_KEY=your_key
```

---

## OpenRouter

https://openrouter.ai/

```env
OPENROUTER_API_KEY=your_key
```

---

## Alpha Vantage

免费 API Key：

https://www.alphavantage.co/support/#api-key

```env
ALPHA_VANTAGE_API_KEY=your_key
```

---

# 推荐 .env

先复制：

```bash
cp .env.example .env
```

然后只填写自己真正使用的 Key。

## 推荐：DeepSeek + 日股完整数据

```env
# ============================================================
# LLM
# ============================================================

DEEPSEEK_API_KEY=

# 如果使用其他模型，再填写对应 Key
OPENAI_API_KEY=
GOOGLE_API_KEY=
ANTHROPIC_API_KEY=
XAI_API_KEY=
OPENROUTER_API_KEY=

# 原版 TradingAgents 其他可选 Provider
DASHSCOPE_API_KEY=
DASHSCOPE_CN_API_KEY=
ZHIPU_API_KEY=
ZHIPU_CN_API_KEY=
MINIMAX_API_KEY=
MINIMAX_CN_API_KEY=
MISTRAL_API_KEY=
MOONSHOT_API_KEY=
GROQ_API_KEY=
NVIDIA_API_KEY=

# ============================================================
# JAPAN OFFICIAL DATA
# ============================================================

JQUANTS_API_KEY=
EDINET_API_KEY=

# ============================================================
# US / MACRO
# ============================================================

FRED_API_KEY=

# 原版可选
ALPHA_VANTAGE_API_KEY=

# ============================================================
# OPENAI-COMPATIBLE / LOCAL
# ============================================================

OPENAI_COMPATIBLE_API_KEY=
#OLLAMA_BASE_URL=http://localhost:11434/v1

# ============================================================
# TRADINGAGENTS
# ============================================================

# 单次 LLM hard deadline
TRADINGAGENTS_LLM_TIMEOUT_SECONDS=180

# 选择 Provider
#TRADINGAGENTS_LLM_PROVIDER=deepseek

# 模型
#TRADINGAGENTS_DEEP_THINK_LLM=deepseek-v4-pro
#TRADINGAGENTS_QUICK_THINK_LLM=deepseek-v4-flash

# OpenAI-compatible endpoint
#TRADINGAGENTS_LLM_BACKEND_URL=

# 输出语言
#TRADINGAGENTS_OUTPUT_LANGUAGE=Chinese

# Debate / Risk rounds
#TRADINGAGENTS_MAX_DEBATE_ROUNDS=1
#TRADINGAGENTS_MAX_RISK_ROUNDS=1

# Checkpoint
#TRADINGAGENTS_CHECKPOINT_ENABLED=false

# Temperature
#TRADINGAGENTS_TEMPERATURE=0.0

# SDK retry budget
# 注意：TIMEOUT_CLIENT_DETACHED 后不应立即重复相同请求
#TRADINGAGENTS_LLM_MAX_RETRIES=2
```
---

# 安装

推荐 Python 3.11 / 3.12。

```bash
git clone <YOUR_REPOSITORY_URL>
cd TradingAgents
```

创建环境：

```bash
conda create -n tradingagents python=3.12
conda activate tradingagents
```

安装：

```bash
pip install .
```

开发环境：

```bash
pip install -e ".[dev]"
```

---

# CLI

```bash
tradingagents
```

或：

```bash
python -m cli.main
```

程序会让你选择：

- Ticker
- Analysis date
- LLM Provider
- Deep / Quick model
- Analyst
- Research depth
- Trading horizon
- Output language
- Checkpoint

---

# Web UI

本分支包含 `web/` 服务与前端。

请使用仓库当前的 Web 启动脚本启动 FastAPI / Uvicorn 服务。

启动成功后，通常访问：

```text
http://127.0.0.1:8000
```

日志中会看到：

```text
Uvicorn running on http://127.0.0.1:8000
```

如果端口被占用，请先结束占用 8000 的旧进程，或更换端口。

---

# Python Usage

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()

ta = TradingAgentsGraph(
    debug=True,
    config=config,
)

_, decision = ta.propagate("5801.T", "2026-08-14")

print(decision)
```

美股示例：

```python
_, decision = ta.propagate("CRCL", "2026-08-14")
```

---

# Persistence / Checkpoint

原版 Decision Log / Checkpoint 机制继续保留。

Checkpoint 用于分析中断后的恢复。

例如：

```bash
tradingagents analyze --checkpoint
```

清理 checkpoint：

```bash
tradingagents analyze --clear-checkpoints
```

如果运行长周期、高 research depth 分析，建议启用 checkpoint。

---

# 当前已知限制

## 1. J-Quants 权限取决于方案

不同账户可能出现：

```text
403
429
历史范围受限
数据延迟
```

项目会透明记录，不静默伪造数据。

## 2. TOPIX

如果免费可靠近期数据源无法获得：

```text
DATA_UNAVAILABLE
```

## 3. Japan CPI

优先使用日本官方数据。

如果当前 CSV / 页面无法可靠解析最新数值，不会使用 stale 数据伪装成当前 CPI。

## 4. Reuters Japan

当前没有授权稳定的结构化 feed：

```text
DATA_UNAVAILABLE
```

不会通过脆弱抓取强行接入。

## 5. 社区情绪

Yahoo掲示板是辅助源。

论坛语义分类可能偏保守，不应单独作为交易信号。

## 6. Analyst Consensus

当前免费层主要是整体共识。

通常无法获得：

- 每家券商逐条评级日期
- 单家机构目标价变动明细
- 完整 analyst revision history

因此不能凭空写：

```text
某券商今日把目标价从 X 上调至 Y
```

除非上游证据真实存在。

## 7. Timeout Client Detached

180 秒 hard deadline 后 Graph 不再等待，但底层同步 SDK 请求不保证真实取消。

详见：

```text
TIMEOUT_CLIENT_DETACHED
```

---

# 测试与质量门槛

当前开发流程要求至少：

```bash
pytest
ruff check .
```

截至当前稳定性阶段，确定性测试结果：

```text
682 passed
1 skipped
ruff check . passed
```

真实 E2E 仍应继续验证：

- US：CRCL / NVDA
- JP：5801.T / 8002.T / 6981.T

只有 E2E 能完整生成最终报告和 `full_agent_log.md` 时，才视为最终运行链路通过。

---

# 开发原则

本分支遵循：

1. Japan Provider 只服务 JP。
2. US 路径不因日股增强被改成 Japan-only。
3. 数据缺失不等于看空。
4. `DATA_UNAVAILABLE` 优于猜测。
5. 官方事实优先于新闻和社区观点。
6. 当前价格只认 Verified Market Snapshot。
7. Company Guidance 与 Analyst Consensus 永远分开。
8. FY / Quarter / TTM 不跨期间乱算。
9. 精确数字必须能追溯到上游 Evidence。
10. 同一 catalyst 不重复计权。
11. Bull/Bear/Risk 详细过程写 log，不污染默认报告。
12. 不绕过付费墙。
13. 不提交 API Key。
14. `main` 保持稳定，开发优先在 feature branch 完成并验收。

---

# 数据源官网

## 日本

- J-Quants: https://jpx-jquants.com/
- EDINET: https://disclosure2.edinet-fsa.go.jp/
- EDINET API 注册: https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1
- TDnet: https://www.release.tdnet.info/inbs/I_main_00.html
- JSF / 日証金: https://www.taisyaku.jp/
- BOJ: https://www.boj.or.jp/
- 日本 CPI: https://www.stat.go.jp/data/cpi/
- Yahoo Finance Japan: https://finance.yahoo.co.jp/
- Yahoo 掲示板: https://finance.yahoo.co.jp/cm/
- 株探: https://kabutan.jp/
- みんかぶ: https://minkabu.jp/

## 美国 / 全球

- FRED: https://fred.stlouisfed.org/
- Alpha Vantage: https://www.alphavantage.co/
- DeepSeek: https://platform.deepseek.com/
- OpenAI: https://platform.openai.com/
- Google AI Studio: https://ai.google.dev/aistudio
- Anthropic: https://console.anthropic.com/
- xAI: https://console.x.ai/
- OpenRouter: https://openrouter.ai/

---

# Upstream

本项目基于：

**TauricResearch / TradingAgents**

https://github.com/TauricResearch/TradingAgents

建议保留 upstream remote，方便以后同步官方更新：

---

# Citation

如果本项目对你的研究有帮助，请同时尊重并引用 TradingAgents 原作者：

```bibtex
@misc{xiao2025tradingagentsmultiagentsllmfinancial,
      title={TradingAgents: Multi-Agents LLM Financial Trading Framework},
      author={Yijia Xiao and Edward Sun and Di Luo and Wei Wang},
      year={2025},
      eprint={2412.20138},
      archivePrefix={arXiv},
      primaryClass={q-fin.TR},
      url={https://arxiv.org/abs/2412.20138}
}
```

---

# License

请遵循上游项目及本仓库 `LICENSE` 中的实际许可条款。

---
