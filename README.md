# TradingAgents JP/US Enhanced Edition

基于 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 的增强版本。

本分支重点强化 **日本股票分析**，同时保持原有 **美股路径与数据源隔离**。

> 本项目仅用于研究与辅助分析，不构成投资建议。

---

## 主要改修

### 日股 / 美股隔离

- 自动识别 JP / US ticker
- `market=JP` 才构建 Japan Bundle
- `market=US` 不调用 J-Quants、TDnet、EDINET、JSF 等日本 Provider
- 保留原版美股数据和 Agent 路径

示例：

```text
JP: 6981.T / 5801.T / 8002.T / 285A.T
US: NVDA / CRCL / AAPL
```

### 日股数据源

已接入：

- **J-Quants V2**：股票资料、日线、财务摘要
- **EDINET**：大量保有 / 变更报告
- **TDnet**：财报、业绩修正、回购、配当、M&A 等
- **Company IR**：自动扫描最新 IR 与官方 PDF
- **JSF / 日证金**：融资 / 贷株历史与 5D / 20D 趋势
- **Japan Macro**：USD/JPY、Nikkei、JGB、BOJ 等
- **US Cross-Market**：S&P 500、Nasdaq、SOX、VIX、US10Y、Fed、DXY
- **日本新闻**：Yahoo Finance Japan、株探
- **日本情绪**：Yahoo Finance Japan 掲示板
- **分析师共识**：みんかぶ

### Source of Truth / Evidence

| 数据类型 | 权威来源 |
|---|---|
| 当前价 / OHLCV / 技术指标 | Verified Market Snapshot |
| 公司正式业绩 / Guidance | TDnet / Company IR → J-Quants |
| 分析师预期 | Analyst Consensus |
| 大量保有 | EDINET |
| 信用供需 | JSF |
| 日本政策 | BOJ / 官方统计源 |

禁止：

- FY / Q1 / TTM 混算
- Analyst Estimate 当 Company Guidance
- 新闻 / 社区帖子当官方事实
- 无来源精确数字
- 从市值等字段反推当前股价

### Agent Evidence Enforcement

以下 Agent 输出会经过证据校验：

- Market / Fundamentals / News / Sentiment
- Bull / Bear
- Research Manager
- Trader
- Aggressive / Conservative / Neutral Risk
- Portfolio Manager

无依据精确数字会被降级或移除。

详细审计保存在：

```text
evidence_audit
full_agent_log.md
```

### Decision Context

最终决策统一参考：

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

核心规则：

```text
DATA_UNAVAILABLE != bearish
```

缺数据只降低置信度，不自动变成利空。

### 日本数据窗口

日本数据源按各自 freshness/cadence 契约使用固定、可审计的回看窗口；这些窗口不代表用户投资期限，也不会作为交易偏好注入 Agent。Portfolio Manager 仍可按原始 TradingAgents 语义，在证据支持时自主给出可选的建议持有期。

### 报告与日志

默认报告不再输出完整 Bull/Bear 与三类 Risk debate。

完整多 Agent 推理与风控过程单独保存在：

```text
full_agent_log.md
```

### LLM 稳定性

默认单次 LLM hard deadline：

```env
TRADINGAGENTS_LLM_TIMEOUT_SECONDS=180
```

DeepSeek 已知不支持的 structured-output 模型会直接走 free-text fallback。

当前超时语义：

```text
TIMEOUT_CLIENT_DETACHED
```

即主流程停止等待，但底层 Provider 请求不保证被 Python 强制取消。

---

## 安装

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

创建环境变量文件：

```bash
cp .env.example .env
```

---

## 推荐 `.env`

如果主要使用 DeepSeek + 日股功能：

```env
# LLM
DEEPSEEK_API_KEY=

# Japan official data
JQUANTS_API_KEY=
EDINET_API_KEY=

# US / Macro
FRED_API_KEY=

# Optional
ALPHA_VANTAGE_API_KEY=
OPENAI_API_KEY=
OPENROUTER_API_KEY=
GOOGLE_API_KEY=
ANTHROPIC_API_KEY=
XAI_API_KEY=

# LLM timeout
TRADINGAGENTS_LLM_TIMEOUT_SECONDS=180
```

如果使用其他 Provider，再填写对应 Key。

> 不要把真实 `.env` 提交到 Git。

---

## API Key 获取地址

### J-Quants

官网：

https://jpx-jquants.com/

```env
JQUANTS_API_KEY=
```

### EDINET

官网：

https://disclosure2.edinet-fsa.go.jp/

API Key 注册：

https://api.edinet-fsa.go.jp/api/auth/index.aspx?mode=1

```env
EDINET_API_KEY=
```

### FRED

https://fred.stlouisfed.org/docs/api/api_key.html

```env
FRED_API_KEY=
```

### DeepSeek

https://platform.deepseek.com/

```env
DEEPSEEK_API_KEY=
```

### OpenAI

https://platform.openai.com/

```env
OPENAI_API_KEY=
```

### OpenRouter

https://openrouter.ai/

```env
OPENROUTER_API_KEY=
```

### Alpha Vantage

https://www.alphavantage.co/support/#api-key

```env
ALPHA_VANTAGE_API_KEY=
```

---

## 不需要 Key 的主要数据源

- TDnet
- Company IR
- JSF / 日证金
- BOJ
- 日本总务省统计局
- Yahoo Finance / Yahoo Finance Japan
- 株探
- Yahoo 掲示板
- みんかぶ

无法可靠取得时会返回 `DATA_UNAVAILABLE`，不会用低质量数据补齐。

---

## 运行

CLI：

```bash
tradingagents
```

或：

```bash
python -m cli.main
```

Web 版启动后一般访问：

```text
http://127.0.0.1:8000
```

---

## 当前验证状态

当前稳定性测试：

```text
pytest: 682 passed, 1 skipped
ruff check .: passed
```

CRCL 美股真实 E2E 已完整成功：

- Japan Bundle = {}
- Reddit 429 不再中断整个流程
- DeepSeek structured-output 400 不再出现
- 主报告正常生成
- `full_agent_log.md` 正常生成

---

## 已知限制

- J-Quants 历史范围、延迟和端点权限取决于账户方案
- TOPIX / 日本 CPI 等无法可靠取得时返回 `DATA_UNAVAILABLE`
- Reddit RSS 可能触发 429
- 免费分析师共识通常没有完整的单家券商逐笔评级历史
- `TIMEOUT_CLIENT_DETACHED` 不代表底层 Provider 请求真正取消
- LLM 输出仍具有非确定性

---

## Key 安全

`.gitignore` 至少应包含：

```gitignore
.env
.env.*
!.env.example
*.pem
*.key
credentials*.json
secrets*.json
tokens*.json
```

如果真实 Key 曾经提交到 GitHub，请立即撤销并重新生成。

---

## Upstream

本项目基于：

https://github.com/TauricResearch/TradingAgents

建议保留 upstream：

```bash
git remote add upstream https://github.com/TauricResearch/TradingAgents.git
git fetch upstream
```

---

## Citation

请保留对原 TradingAgents 项目的引用：

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

## License

请遵循本仓库 `LICENSE` 以及上游 TradingAgents 的许可条款。
