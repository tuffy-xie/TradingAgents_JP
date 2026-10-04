# TradingAgents Japan 长期工作章程

本文件是本项目的 canonical `AGENTS.md`。除非用户明确修改规则，后续任务默认继承，不需要重复询问或重新确认。`CLAUDE.md` 如存在，只作兼容入口，不复制规则正文。

遵守系统、平台和安全约束；用户当前明确指令优先于本文件、Skill、历史记忆和默认偏好。面向用户默认使用简体中文，代码和技术标识保持英文。

## 1. 自主工作与根因调查

普通工程问题自主完成：调查 → 证明根因 → 选择最小合理方案 → 实现 → 测试 → 回归 → 自检 → commit → 普通 push。

不要因 merge conflict、import/path、parser、fixture、config、state schema、observer、renderer、checkpoint、compatibility、dependency、test failure、logging、Web backend adaptation 或 deterministic replay failure 停下来询问用户。

只有以下情况允许停止并询问：

1. 真正的产品或架构取舍。
2. 需要新的 secret、账号、权限或付费服务。
3. 两种方案都会改变现有 contract，需要用户选择。
4. 已合理调查代码、Git history、tests、state 和 logs，仍无法证明根因。
5. 外部服务不可用，且无法通过本地 deterministic 工作继续。
6. 必须执行用户此前明确禁止的 Git 操作。

不要将“不确定怎么改”当作询问理由。收到异常先记录 observed fact，调查可能路径并证明 root cause，再修复。修复报告必须区分 observed fact、proved root cause 和 implemented fix；不要先假设原因再硬改。

## 2. Git 分支边界

- `main`：保持纯上游 TradingAgents 基线，不放 Japan 定制，除非用户未来明确改变策略。
- `fix/japan-evidence-authority-acceptance`：Japan 产品开发分支；authority、evidence、execution、reporting、Web 等定制均在此线发展。
- `sync/upstream-*`：临时迁移分支，用于把新版 main/upstream 整合进 Japan 开发分支。

上游同步顺序：Japan 最新开发 HEAD → 创建 sync branch → merge 最新纯上游 main → 迁移/测试/replay → 离线通过 → 合回 Japan 开发分支 → 最终 feature HEAD 上做 fresh acceptance。

main 默认保持不动。普通 merge、commit、push 可以自主执行，但不能跨越上述分支和阶段边界。除非用户明确授权，禁止 force push、rebase、reset、stash、clean 和重写公开历史。

## 3. 上游迁移

上游结构优先，Japan 产品 contract 不变。

允许迁移目录、import、function/class 名称、state 字段、tool registration、graph implementation、memory implementation 和 config layout。同一功能只保留一个 canonical implementation；必要时使用 thin compatibility adapter，不为保留旧路径维护两套完整实现。

按依赖顺序自行解决冲突：

1. dependency / directory / interface。
2. data / tool / evidence。
3. graph / state / memory / parallelism。
4. recommendation / execution / Audit / reporting。
5. Web compatibility。

不得在 upstream migration 中顺手混入新的产品 Stage。

## 4. Market Authority

Authority 必须依赖 lossless structured metadata：requested range、retrieval timestamp、expected latest completed JP trading day、actual latest complete OHLCV date、complete row count、indicator underlying date、payload identity/SHA、provenance 和 cache freshness。

不能证明最新就 fail closed。不得从 truncated CSV、display text、Agent prose 或 diagnostic snapshot 反推 CURRENT。snapshot 只作为 diagnostic。

## 5. Financial Authority

Actual 与 Guidance 独立。无法证明 current 时使用 `INSUFFICIENT_DATA` / `FRESHNESS_UNVERIFIED`。

不得用 stale data 冒充最新官方结果；不得混用 FY/H1/Q/TTM/Forecast/analyst estimate；不得用 analyst consensus 冒充 company guidance；不得在 IFRS 下造 ordinary_profit。

## 6. Sentiment 与 JSF

只有 investor/social samples 参与 sentiment score 和 direction；新闻和宏观不得进入 sentiment aggregation。missing source != zero attention。

JSF 保留 source-native 融资余额、贷株余额、差引余额语义，不得描述成全市场空头、机构仓位或 JPX individual-stock margin。JPX Daily Margin 属于后续独立 Stage，不得自动启动。

## 7. Recommendation Authority

Portfolio Manager 是唯一系统最终投资评级和 recommendation authority。

其他 Agent 不得发布自身系统最终 Buy/Sell/Hold、Overweight/Underweight、Strong Buy、买入/卖出/持有/增持/减持、買い/売り或等价隐式最终 recommendation。明确归因的外部券商评级和 analyst consensus 可以作为外部 evidence。

## 8. Execution Authority

`validated_execution` 是唯一正式 execution authority。非 canonical path 不得发布 buy/sell、add/reduce、maintain-position plan、portfolio hedge、entry、stop、target、position sizing 或 future staged execution。

Hold 不授权新的 execution plan。`Unknown != Hold`。canonical action/entry/stop/target/position 必须一致。

不得将公司业务执行、自然业务对冲、经营资源配置或纯 withholding 语言误判成用户交易授权；业务 heading 也不能豁免其下属真实用户交易正文。

## 9. Macro Evidence

概率必须绑定 source event、country、year/horizon、outcome 和 Yes/No semantics。

不得从 event probability 自动推出 soft landing 已确认、company demand 获得保障、carry risk 已降低、financial conditions 当前宽松、market fully priced 或 realized macro regime，除非存在独立 evidence。

允许明确标识为条件性或假设性的分析。复用现有 Evidence/Audit，不建立平行 macro authority。

## 10. Audit 与 Final Contract

继续使用 claim-level Audit，至少关注：

- `SUPPORTED_FACT`
- `UNSUPPORTED_CLAIM`
- `STALE_EVIDENCE_USE`
- `MARKET_AUTHORITY_CLAIM`
- `SECONDARY_INTERNAL_RATING`
- `UNAPPROVED_EXECUTION_CLAIM`
- `SEMANTIC_MISMATCH`
- `EXECUTION_GATE`

`UNRESOLVED=0` 不等于 PASS。Audit resolution 必须 claim-level。Final Contract 必须独立检查 exact candidate，不能只信 upstream cleanup。fault injection 必须能证明 Final Contract 自身会 BLOCK 非法 claim。

## 11. Artifact、SHA 与 Presentation

用户可见 artifact 必须经过 canonical publication path。严格验证：

`Contract.accepted_report_sha256` = `accepted_state.accepted_report_markdown` UTF-8 SHA = `complete_report.md` exact bytes SHA。

必须 exact match；禁止 trim 后比较、newline normalization 后比较、renderer 后偷偷修改正文。

Markdown → HTML → PDF 使用 shared renderer。最终用户 artifact 不得出现 LLM process narration、internal engineering labels、raw enum、debug fields、local path、secret、raw Markdown leakage 或 `latest_complete=None` 等内部表达。

PDF 只有在 Markdown/HTML semantic PASS 后才能生成，并必须实际检查页面。小分页留白不算 blocker，除非影响可读性。

## 12. Web

保留现有中文 Web，不为同步 upstream 重做前端，也不退回 CLI。Web 必须调用同一套 production backend、graph、authority 和 publication path，不建立第二套业务逻辑。

同步 upstream 时只做兼容性：provider/model/language/analyst 参数、graph lifecycle、Agent 状态、progress、report publication gate、history、shared renderer、checkpoint/error handling。

后续增强优先顺序：设置记忆 → 运行统计 → 真实 Portfolio 输入 → 其他 Web 功能。不得恢复已删除的 custom horizon。

## 13. Fresh-live 生命周期与次数

单次 acceptance 任务最多允许 `compiled_graph_execution_started = 1`。只有真正 graph event 出现才算 started；observer entry 不等于 graph started。

生命周期必须区分：

- `propagate_requested`
- `observer_entered`
- `compiled_stream_calls`
- `compiled_graph_execution_started`
- `first_agent_or_graph_step_observed`
- `graph_completed`

若 started = 0：preflight/observer/harness 普通问题自行修复，deterministic tests 通过后，可以重新启动本次 acceptance。

若 started = 1：禁止第二次 fresh。产品问题处理顺序为保存现场 → root cause → deterministic repair → 使用该失败 saved state offline replay → regressions → full tests → commit → 普通 push，然后返回 `NEEDS_FRESH_REVALIDATION`，等待新的用户授权。不得偷偷第二次 live。

## 14. Provider 与 Secret

所有真实 LLM/full live E2E 必须使用现有 provider abstraction。

Fresh acceptance 固定：

- provider：`minimax`
- endpoint：`https://api.minimax.io/v1`
- Deep：`MiniMax-M3`
- Quick：`MiniMax-M2.7-highspeed`

禁止 provider fallback。MiniMax 失败不能自动切 DeepSeek、OpenAI 或其他 provider。Provider overload、429、529、timeout 单独分类为 `PROVIDER_INTERRUPTION`，不算产品 acceptance FAIL。

非 fresh acceptance 的真实 LLM 任务仍默认 MiniMax；仅在用户明确指定某次运行时使用 DeepSeek。非固定验收配置的 model 读取现有 selection/catalog，不猜测或静默覆盖。

正常配置系统可以从既有 env/secret config 读取 credential。禁止打印/保存 key、URL/query 泄漏 key、Authorization/Bearer header 落日志、HTTP error 泄漏 prepared credential URL、artifact/state/report 保存 secret，或修改/提交 `.env`。`.env.example` 必须无真实 secret。

Deterministic replay 和 fixture tests 不得调用 LLM，不得依赖公网；offline replay 不访问外部数据 API。

## 15. Saved-state regression

涉及 authority、execution 或 upstream migration 时，优先使用已有生产保存状态：`969ce71b`、`6976`、`ca4e`、`8dd4`、`0f29`、`82da`。

saved-state replay 证明历史语义不退步；新版 graph 结构另用 deterministic stub tests 验证，两者不能互相替代。

特别保持 `8dd4` canonical Sell：entry `8513`、stop `9500`、position `3%`。Hold-specific gate 不得误杀合法 Sell。

## 16. 阶段推进与新功能顺序

当前目标的 tests、saved-state replay 和 exact artifact manual review 已通过，就进入下一阶段，不为“更放心”无限追加泛化静态规则。

- 迁移离线通过：进入最终 feature merge/smoke。
- 最终 feature deterministic validation 通过：请求或使用已有 fresh 授权。
- fresh actual artifact PASS：进入 merge/release review，main 仍遵守纯上游边界。

当前 upstream migration/acceptance 完成后，新功能优先顺序：

1. Market OHLCV freshness。
2. Financial Actual/Guidance。
3. Web 设置记忆。
4. Web 运行统计。
5. 真实 Portfolio 输入。
6. ≥0.5% short disclosure 完善。
7. JPX Daily Margin。
8. 其他日本官方数据源。

这些新 Stage 不得混入 upstream migration。

## 17. 工作报告与默认行为

每阶段结束报告：做了什么、root cause、修复、tests/replays、artifact manual review、Git HEAD、working tree、当前状态和下一步。没有对应内容就省略；不要把普通工程过程变成用户审批流程。

用户说“继续”“继续干”或给出上一轮运行结果时，先判断当前阶段，再自主推进到该阶段自然停止点。普通工程问题不要询问用户。

只有真正 blocker、需要用户决策、fresh 需要新授权或 migration/release 阶段需要明确跨阶段批准时停止。既有明确授权在其范围内继续有效，不重复确认。
