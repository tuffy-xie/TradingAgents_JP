<script setup>
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { marked } from 'marked'

// ── Config form state ──────────────────────────────────
const form = reactive({
  ticker: 'NVDA',
  date: new Date().toISOString().split('T')[0],
  provider: 'deepseek',
  quickModel: '',
  deepModel: '',
  analysts: ['market', 'social', 'news', 'fundamentals'],
  depth: 1,
  entryCondition: '',
  stopLossCondition: '',
  takeProfitCondition: '',
  maxPositionPct: null,
  language: 'Chinese',
  effort: '',                  // provider-specific reasoning effort
  temperature: null,           // null = leave provider default
  temperatureEnabled: false,
  checkpoint: false,
})

// ── API data ───────────────────────────────────────────
const providers = ref([])
const models = ref({ quick: [], deep: [], effort_kind: null })
const languages = ref([])

// Crypto auto-detection — kept in sync with cli/utils.CRYPTO_SUFFIXES
const CRYPTO_SUFFIXES = ['-USD', '-USDT', '-USDC', '-BTC', '-ETH']
const isCrypto = computed(() => {
  const t = form.ticker.trim().toUpperCase()
  return CRYPTO_SUFFIXES.some(s => t.endsWith(s))
})
const assetType = computed(() => isCrypto.value ? 'crypto' : 'stock')

// Drop fundamentals when crypto is detected (mirrors the CLI filter)
watch(isCrypto, (crypto) => {
  if (crypto && form.analysts.includes('fundamentals')) {
    form.analysts = form.analysts.filter(a => a !== 'fundamentals')
  }
})

// Effort options per provider — mirrors cli/utils ask_*_effort/thinking
const EFFORT_OPTIONS = {
  openai_reasoning_effort: [
    { value: '',       label: '默认 (medium)' },
    { value: 'low',    label: 'Low (更快)' },
    { value: 'medium', label: 'Medium' },
    { value: 'high',   label: 'High (更彻底)' },
  ],
  anthropic_effort: [
    { value: '',       label: '默认' },
    { value: 'low',    label: 'Low (更快/更省)' },
    { value: 'medium', label: 'Medium' },
    { value: 'high',   label: 'High (推荐)' },
  ],
  google_thinking_level: [
    { value: '',        label: '默认' },
    { value: 'minimal', label: '最小/关闭思考' },
    { value: 'high',    label: '启用思考 (推荐)' },
  ],
}
const effortOptions = computed(() => EFFORT_OPTIONS[models.value.effort_kind] || [])
const effortLabel = computed(() => ({
  openai_reasoning_effort: '推理强度',
  anthropic_effort:        'Effort 等级',
  google_thinking_level:   '思考模式',
}[models.value.effort_kind] || ''))

// ── Analysis runtime state ─────────────────────────────
const agents = ref([])          // [{display, team, node}]
const agentStatus = reactive({}) // display → 'pending'|'in_progress'|'completed'
const sections = ref({})         // key → markdown string
const finalDecision = ref('')
const isRunning = ref(false)
const isDone = ref(false)
const error = ref('')
let es = null

// ── Top-level view switch (分析 / 历史记录) ──────────────
const view = ref('analyze')   // 'analyze' | 'history'
const history = ref([])
const historyLoading = ref(false)
const historyError = ref('')

async function loadHistory() {
  historyLoading.value = true
  historyError.value = ''
  try {
    const res = await fetch('/api/history')
    history.value = (await res.json()).history
  } catch (e) {
    historyError.value = '无法加载历史记录。'
  } finally {
    historyLoading.value = false
  }
}

function switchView(v) {
  view.value = v
  if (v === 'history') loadHistory()
}

// Decision badge styling, keyed by the backend's derived action.
const ACTION_META = {
  buy:  { label: '买入', cls: 'buy' },
  sell: { label: '卖出', cls: 'sell' },
  hold: { label: '持有', cls: 'hold' },
}

// ── History sorting ────────────────────────────────────
// Default: newest run first (mtime desc). Clicking a column header re-sorts;
// clicking the active column toggles direction.
const sortKey = ref('mtime')
const sortDir = ref('desc')
function setSort(key) {
  if (sortKey.value === key) {
    sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortKey.value = key
    sortDir.value = 'desc'
  }
}
const sortedHistory = computed(() => {
  const dir = sortDir.value === 'asc' ? 1 : -1
  const k = sortKey.value
  return [...history.value].sort((a, b) => {
    const av = a[k] ?? '', bv = b[k] ?? ''
    if (av < bv) return -1 * dir
    if (av > bv) return 1 * dir
    return 0
  })
})
function sortArrow(key) {
  if (sortKey.value !== key) return ''
  return sortDir.value === 'asc' ? '▲' : '▼'
}

function reportUrl(entry, forPrint) {
  const base = `/api/report/${encodeURIComponent(entry.dir)}/${encodeURIComponent(entry.date)}`
  return forPrint ? `${base}?print=1` : base
}
function viewReport(entry) { window.open(reportUrl(entry, false), '_blank') }
function downloadPdf(entry) { window.open(reportUrl(entry, true), '_blank') }

// ── Load providers on mount ────────────────────────────
onMounted(async () => {
  const [pRes, lRes] = await Promise.all([
    fetch('/api/providers'),
    fetch('/api/languages'),
  ])
  providers.value = (await pRes.json()).providers
  languages.value = (await lRes.json()).languages
  await loadModels(form.provider)
})

async function loadModels(provider) {
  const res = await fetch(`/api/models/${provider}`)
  const data = await res.json()
  models.value = data
  form.quickModel = data.quick[0]?.value || ''
  form.deepModel  = data.deep[0]?.value  || ''
  // Reset effort when provider changes — different providers expose
  // different effort knobs (or none at all).
  form.effort = ''
}

watch(() => form.provider, loadModels)

// ── Analyst options ────────────────────────────────────
const ANALYST_OPTIONS = [
  { id: 'market',       label: '市场',   icon: '📊' },
  { id: 'social',       label: '情绪',   icon: '💬' },
  { id: 'news',         label: '新闻',   icon: '📰' },
  { id: 'fundamentals', label: '基本面', icon: '📋' },
]
const visibleAnalystOptions = computed(() =>
  ANALYST_OPTIONS.filter(a => !(isCrypto.value && a.id === 'fundamentals'))
)

// ── Run analysis ───────────────────────────────────────
function startAnalysis() {
  if (isRunning.value) return

  // Reset
  agents.value = []
  Object.keys(agentStatus).forEach(k => delete agentStatus[k])
  sections.value = {}
  finalDecision.value = ''
  error.value = ''
  isRunning.value = true
  isDone.value = false

  if (es) { es.close(); es = null }

  const params = new URLSearchParams({
    ticker:          form.ticker.trim().toUpperCase(),
    date:            form.date,
    provider:        form.provider,
    deep_model:      form.deepModel,
    quick_model:     form.quickModel,
    analysts:        form.analysts.join(','),
    research_depth:  form.depth,
    output_language: form.language,
    checkpoint:      form.checkpoint ? 'true' : 'false',
  })
  if (form.effort) params.set('effort', form.effort)
  if (form.entryCondition.trim()) params.set('entry_condition', form.entryCondition.trim())
  if (form.stopLossCondition.trim()) params.set('stop_loss_condition', form.stopLossCondition.trim())
  if (form.takeProfitCondition.trim()) params.set('take_profit_condition', form.takeProfitCondition.trim())
  if (form.maxPositionPct !== null) params.set('max_position_pct', String(form.maxPositionPct))
  if (form.temperatureEnabled && form.temperature !== null) {
    params.set('temperature', String(form.temperature))
  }

  es = new EventSource(`/api/analyze?${params}`)

  es.onmessage = (e) => {
    const evt = JSON.parse(e.data)
    handleEvent(evt)
  }

  es.onerror = () => {
    if (isRunning.value) {
      error.value = '连接中断，请查看终端错误信息。'
      isRunning.value = false
    }
    es?.close()
  }
}

const runAssetType = ref('stock')

function handleEvent(evt) {
  switch (evt.type) {
    case 'init':
      agents.value = evt.agents
      evt.agents.forEach(a => { agentStatus[a.display] = 'pending' })
      runAssetType.value = evt.asset_type || 'stock'
      break

    case 'agent_update':
      agentStatus[evt.id] = evt.status
      break

    case 'section':
      sections.value = { ...sections.value, [evt.key]: evt.content }
      break

    case 'final':
      finalDecision.value = evt.content
      break

    case 'done':
      isRunning.value = false
      isDone.value = true
      es?.close()
      break

    case 'error':
      error.value = evt.message
      isRunning.value = false
      es?.close()
      break
  }
}

// ── Agent name translations ────────────────────────────
const AGENT_ZH = {
  'Market Analyst':       '市场分析师',
  'Sentiment Analyst':    '情绪分析师',
  'News Analyst':         '新闻分析师',
  'Fundamentals Analyst': '基本面分析师',
  'Bull Researcher':      '多头研究员',
  'Bear Researcher':      '空头研究员',
  'Research Manager':     '研究经理',
  'Trader':               '交易员',
  'Aggressive Analyst':   '激进分析师',
  'Conservative Analyst': '保守分析师',
  'Neutral Analyst':      '中性分析师',
  'Portfolio Manager':    '投资组合经理',
}

// ── Agent grouping ─────────────────────────────────────
const TEAM_META = {
  analysts: { label: '分析师团队', icon: '📊', color: '#4F46E5' },
  research: { label: '研究团队',   icon: '🔬', color: '#7C3AED' },
  trading:  { label: '交易',       icon: '📈', color: '#0891B2' },
  risk:     { label: '风险管理',   icon: '⚠️', color: '#D97706' },
  portfolio:{ label: '投资组合',   icon: '💼', color: '#059669' },
}

const agentsByTeam = computed(() => {
  const map = {}
  for (const a of agents.value) {
    if (!map[a.team]) map[a.team] = []
    map[a.team].push(a)
  }
  return map
})

const totalAgents   = computed(() => agents.value.length)
const doneCount     = computed(() => agents.value.filter(a => agentStatus[a.display] === 'completed').length)
const progressPct   = computed(() => totalAgents.value ? Math.round(doneCount.value / totalAgents.value * 100) : 0)

// ── Report section labels ──────────────────────────────
const SECTION_META = {
  market_report:       { label: '市场分析', icon: '📊' },
  sentiment_report:    { label: '情绪分析', icon: '💬' },
  news_report:         { label: '新闻分析', icon: '📰' },
  fundamentals_report: { label: '基本面',   icon: '📋' },
  research_decision:   { label: '研究决策', icon: '🔬' },
  trader_plan:         { label: '交易计划', icon: '📈' },
}

// ── Markdown renderer ──────────────────────────────────
function renderMd(text) {
  if (!text) return ''
  marked.setOptions({ breaks: true })
  return marked(text)
}

// ── Depth options ──────────────────────────────────────
const DEPTH_OPTIONS = [
  { v: 1, label: '浅层', desc: '快速' },
  { v: 3, label: '中等', desc: '均衡' },
  { v: 5, label: '深入', desc: '全面' },
]

const activeSection = ref(null)
function toggleSection(key) {
  activeSection.value = activeSection.value === key ? null : key
}
</script>

<template>
  <div class="app">
    <!-- ── Header ────────────────────────────────────── -->
    <header class="header">
      <div class="header-brand">
        <span class="brand-icon">📈</span>
        <span class="brand-name">TradingAgents</span>
        <span class="brand-sub">多智能体分析</span>
      </div>

      <nav class="header-tabs">
        <button
          class="tab-btn"
          :class="{ active: view === 'analyze' }"
          @click="switchView('analyze')"
        >分析</button>
        <button
          class="tab-btn"
          :class="{ active: view === 'history' }"
          @click="switchView('history')"
        >历史记录</button>
      </nav>

      <div class="header-status" v-show="view === 'analyze'">
        <template v-if="isRunning">
          <div class="progress-bar-wrap">
            <div class="progress-bar-fill" :style="{ width: progressPct + '%' }"></div>
          </div>
          <span class="status-badge running">
            <span class="spinner"></span>
            {{ doneCount }}/{{ totalAgents }} 个智能体
          </span>
        </template>
        <span v-else-if="isDone" class="status-badge done">✓ 分析完成</span>
        <span v-else class="status-badge idle">就绪</span>
      </div>
    </header>

    <!-- ── History view ───────────────────────────────── -->
    <main v-if="view === 'history'" class="history-view">
      <div class="history-head">
        <h2>历史记录</h2>
        <button class="refresh-btn" @click="loadHistory" :disabled="historyLoading">
          {{ historyLoading ? '加载中...' : '↻ 刷新' }}
        </button>
      </div>

      <div v-if="historyError" class="error-box"><span>⚠️</span> {{ historyError }}</div>

      <div v-else-if="!historyLoading && history.length === 0" class="empty-state">
        <div class="empty-icon">🗂️</div>
        <h2>暂无历史记录</h2>
        <p>完成一次分析后,这里会列出每天分析的股票与报告。</p>
      </div>

      <div v-else class="history-card card">
        <table class="history-table">
          <thead>
            <tr>
              <th><button class="sort-th" @click="setSort('mtime')">运行时间 <span class="sort-arrow">{{ sortArrow('mtime') }}</span></button></th>
              <th><button class="sort-th" @click="setSort('date')">分析日期 <span class="sort-arrow">{{ sortArrow('date') }}</span></button></th>
              <th><button class="sort-th" @click="setSort('ticker')">股票代码 <span class="sort-arrow">{{ sortArrow('ticker') }}</span></button></th>
              <th><button class="sort-th" @click="setSort('action')">决策 <span class="sort-arrow">{{ sortArrow('action') }}</span></button></th>
              <th class="col-actions">报告</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(e, i) in sortedHistory" :key="e.dir + e.date + i">
              <td class="col-time">{{ e.datetime || '—' }}</td>
              <td class="col-date">{{ e.date }}</td>
              <td class="col-ticker">{{ e.ticker }}</td>
              <td>
                <span
                  v-if="ACTION_META[e.action]"
                  class="action-badge"
                  :class="ACTION_META[e.action].cls"
                >{{ ACTION_META[e.action].label }}</span>
                <span v-else class="action-badge none">—</span>
              </td>
              <td class="col-actions">
                <button
                  class="link-btn"
                  :disabled="!e.has_report"
                  @click="viewReport(e)"
                >查看</button>
                <button
                  class="link-btn pdf"
                  :disabled="!e.has_report"
                  @click="downloadPdf(e)"
                >📄 PDF</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </main>

    <!-- ── Layout ─────────────────────────────────────── -->
    <div v-show="view === 'analyze'" class="layout">

      <!-- ── Sidebar ───────────────────────────────── -->
      <aside class="sidebar">
        <div class="sidebar-inner">

          <section class="form-section">
            <h4 class="section-title">目标</h4>
            <div class="form-row">
              <div class="form-group">
                <label>
                  股票代码
                  <span v-if="isCrypto" class="asset-badge crypto">加密货币</span>
                </label>
                <input
                  v-model="form.ticker"
                  placeholder="NVDA / BTC-USD / 0700.HK"
                  :disabled="isRunning"
                  class="input-ticker"
                />
              </div>
              <div class="form-group">
                <label>日期</label>
                <input
                  type="date"
                  v-model="form.date"
                  :disabled="isRunning"
                />
              </div>
            </div>
          </section>

          <section class="form-section">
            <h4 class="section-title">模型</h4>
            <div class="form-group">
              <label>服务商</label>
              <select v-model="form.provider" :disabled="isRunning">
                <option v-for="p in providers" :key="p.id" :value="p.id">{{ p.label }}</option>
              </select>
            </div>
            <div class="form-group">
              <label>快速思考 <span class="label-hint">分析师 / 研究员</span></label>
              <select v-model="form.quickModel" :disabled="isRunning">
                <option v-for="m in models.quick" :key="m.value" :value="m.value">{{ m.label }}</option>
              </select>
            </div>
            <div class="form-group">
              <label>深度思考 <span class="label-hint">管理层 / 投资组合</span></label>
              <select v-model="form.deepModel" :disabled="isRunning">
                <option v-for="m in models.deep" :key="m.value" :value="m.value">{{ m.label }}</option>
              </select>
            </div>
            <div v-if="models.effort_kind" class="form-group">
              <label>{{ effortLabel }} <span class="label-hint">服务商专属</span></label>
              <select v-model="form.effort" :disabled="isRunning">
                <option v-for="o in effortOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
              </select>
            </div>
            <div class="form-group">
              <label class="toggle-row">
                <input
                  type="checkbox"
                  v-model="form.temperatureEnabled"
                  :disabled="isRunning"
                />
                <span>采样温度 (Temperature)</span>
              </label>
              <input
                v-if="form.temperatureEnabled"
                type="number"
                v-model.number="form.temperature"
                min="0"
                max="2"
                step="0.1"
                placeholder="0.0 - 2.0"
                :disabled="isRunning"
              />
              <span v-else class="label-hint">未启用 — 使用服务商默认值</span>
            </div>
          </section>

          <section class="form-section">
            <h4 class="section-title">输出</h4>
            <div class="form-group">
              <label>报告语言</label>
              <select v-model="form.language" :disabled="isRunning">
                <option v-for="l in languages" :key="l.value" :value="l.value">{{ l.label }}</option>
              </select>
            </div>
          </section>

          <section class="form-section">
            <h4 class="section-title">
              分析师
              <span v-if="isCrypto" class="label-hint">（加密货币禁用基本面）</span>
            </h4>
            <div class="analyst-grid">
              <label
                v-for="a in visibleAnalystOptions"
                :key="a.id"
                class="analyst-chip"
                :class="{ active: form.analysts.includes(a.id), disabled: isRunning }"
              >
                <input
                  type="checkbox"
                  :value="a.id"
                  v-model="form.analysts"
                  :disabled="isRunning"
                  style="display:none"
                />
                <span>{{ a.icon }}</span>
                <span>{{ a.label }}</span>
              </label>
            </div>
          </section>

          <section class="form-section">
            <h4 class="section-title">研究深度</h4>
            <div class="depth-row">
              <button
                v-for="d in DEPTH_OPTIONS"
                :key="d.v"
                class="depth-btn"
                :class="{ active: form.depth === d.v }"
                :disabled="isRunning"
                @click="form.depth = d.v"
              >
                <span class="depth-label">{{ d.label }}</span>
                <span class="depth-desc">{{ d.desc }}</span>
              </button>
            </div>
          </section>

          <section class="form-section">
            <h4 class="section-title">交易计划 <span class="label-hint">约束会进入研究与最终决策</span></h4>
            <div class="form-group">
              <label>入场条件 <span class="label-hint">可选；留空由模型制定</span></label>
              <input v-model.trim="form.entryCondition" :disabled="isRunning" placeholder="例如：放量突破 5 日高点后入场" />
            </div>
            <div class="form-group">
              <label>止损条件 <span class="label-hint">可选；留空由模型制定</span></label>
              <input v-model.trim="form.stopLossCondition" :disabled="isRunning" placeholder="例如：跌破入场价 5% 或关键支撑位" />
            </div>
            <div class="form-group">
              <label>止盈／减仓条件 <span class="label-hint">可选；留空由模型制定</span></label>
              <input v-model.trim="form.takeProfitCondition" :disabled="isRunning" placeholder="例如：到阻力位分批减仓，或盈利 15% 后上移止损" />
            </div>
            <div class="form-group">
              <label>仓位上限 (%) <span class="label-hint">可选；留空由模型保守建议</span></label>
              <input v-model.number="form.maxPositionPct" type="number" min="0.1" max="100" step="0.1" :disabled="isRunning" placeholder="例如：10" />
            </div>
          </section>

          <section class="form-section">
            <h4 class="section-title">执行</h4>
            <label class="toggle-row">
              <input
                type="checkbox"
                v-model="form.checkpoint"
                :disabled="isRunning"
              />
              <span>启用检查点 <span class="label-hint">崩溃后可从最后一步恢复</span></span>
            </label>
          </section>

          <button
            class="run-btn"
            :disabled="isRunning || form.analysts.length === 0"
            @click="startAnalysis"
          >
            <span v-if="isRunning" class="spinner white"></span>
            <span v-else>▶</span>
            {{ isRunning ? '分析中...' : '开始分析' }}
          </button>

          <div v-if="error" class="error-box">
            <span>⚠️</span> {{ error }}
          </div>
        </div>
      </aside>

      <!-- ── Main content ──────────────────────────── -->
      <main class="content">

        <!-- Empty state -->
        <div v-if="agents.length === 0 && !isRunning" class="empty-state">
          <div class="empty-icon">🤖</div>
          <h2>准备就绪</h2>
          <p>在左侧配置参数，点击 <strong>开始分析</strong> 按钮。</p>
        </div>

        <!-- Agent progress grid -->
        <div v-if="agents.length > 0" class="card">
          <div class="card-header">
            <h3 class="card-title">智能体进度</h3>
            <span v-if="runAssetType === 'crypto'" class="asset-badge crypto card-asset-badge">加密货币</span>
            <span v-if="isRunning" class="card-subtitle">正在分析...</span>
            <span v-else-if="isDone" class="card-subtitle success">所有智能体已完成</span>
          </div>

          <div class="teams-grid">
            <div
              v-for="(teamAgents, team) in agentsByTeam"
              :key="team"
              class="team-block"
              :style="{ '--team-color': TEAM_META[team]?.color }"
            >
              <div class="team-header">
                <span class="team-icon">{{ TEAM_META[team]?.icon }}</span>
                <span class="team-name">{{ TEAM_META[team]?.label }}</span>
              </div>
              <div class="agent-list">
                <div
                  v-for="agent in teamAgents"
                  :key="agent.display"
                  class="agent-row"
                  :class="agentStatus[agent.display] || 'pending'"
                >
                  <span class="agent-dot">
                    <span v-if="agentStatus[agent.display] === 'in_progress'" class="dot-pulse"></span>
                    <span v-else-if="agentStatus[agent.display] === 'completed'" class="dot-check">✓</span>
                    <span v-else class="dot-idle"></span>
                  </span>
                  <span class="agent-label">{{ AGENT_ZH[agent.display] || agent.display }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Final decision -->
        <div v-if="finalDecision" class="card decision-card">
          <div class="card-header">
            <h3 class="card-title">🎯 最终决策</h3>
            <span class="card-subtitle success">投资组合经理</span>
          </div>
          <div class="markdown-body" v-html="renderMd(finalDecision)"></div>
        </div>

        <!-- Report sections -->
        <div v-if="Object.keys(sections).length > 0" class="card">
          <div class="card-header">
            <h3 class="card-title">📋 分析报告</h3>
            <span class="card-subtitle">{{ Object.keys(sections).length }} 个板块</span>
          </div>
          <div class="sections-list">
            <div
              v-for="(content, key) in sections"
              :key="key"
              class="section-item"
            >
              <button
                class="section-toggle"
                @click="toggleSection(key)"
              >
                <span class="section-icon">{{ SECTION_META[key]?.icon || '📄' }}</span>
                <span class="section-name">{{ SECTION_META[key]?.label || key }}</span>
                <span class="section-arrow" :class="{ open: activeSection === key }">›</span>
              </button>
              <div v-show="activeSection === key" class="section-body">
                <div class="markdown-body" v-html="renderMd(content)"></div>
              </div>
            </div>
          </div>
        </div>

      </main>
    </div>
  </div>
</template>

<style scoped>
/* ── App shell ────────────────────────────────────────── */
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  overflow: hidden;
}

/* ── Header ───────────────────────────────────────────── */
.header {
  height: 56px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  flex-shrink: 0;
}
.header-brand { display: flex; align-items: center; gap: 10px; }
.brand-icon   { font-size: 22px; }
.brand-name   { font-size: 16px; font-weight: 700; color: var(--text); }
.brand-sub    { font-size: 12px; color: var(--text-muted); margin-left: 4px; }

.header-status { display: flex; align-items: center; gap: 12px; }
.progress-bar-wrap {
  width: 120px; height: 4px; background: var(--border); border-radius: 2px; overflow: hidden;
}
.progress-bar-fill {
  height: 100%; background: var(--primary); border-radius: 2px;
  transition: width 0.4s ease;
}

.status-badge {
  font-size: 12px; font-weight: 500; padding: 4px 10px;
  border-radius: 20px; display: flex; align-items: center; gap: 6px;
}
.status-badge.idle    { background: var(--surface-2); color: var(--text-muted); }
.status-badge.running { background: var(--running-light); color: var(--running); }
.status-badge.done    { background: var(--success-light); color: var(--success); }

/* ── Layout ───────────────────────────────────────────── */
.layout {
  display: flex;
  flex: 1;
  overflow: hidden;
  min-height: 0;
}

/* ── Sidebar ──────────────────────────────────────────── */
.sidebar {
  width: 300px;
  min-width: 300px;
  min-height: 0;
  background: var(--surface);
  border-right: 1px solid var(--border);
  overflow-y: auto;
}
.sidebar-inner { padding: 20px; display: flex; flex-direction: column; gap: 20px; }

.form-section {}
.section-title {
  font-size: 11px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--text-muted); margin-bottom: 10px;
}
.label-hint { font-size: 11px; color: var(--text-subtle); font-weight: 400; text-transform: none; letter-spacing: 0; }

.asset-badge {
  display: inline-block; margin-left: 6px;
  font-size: 10px; font-weight: 600;
  padding: 2px 7px; border-radius: 10px;
  letter-spacing: 0.04em; text-transform: none;
}
.asset-badge.crypto {
  background: #FEF3C7; color: #92400E;
  border: 1px solid #FDE68A;
}
.card-asset-badge { margin-left: 10px; }

.toggle-row {
  display: flex; align-items: center; gap: 8px;
  font-size: 12.5px; color: var(--text-2); cursor: pointer; user-select: none;
}
.toggle-row input[type="checkbox"] {
  width: 14px; height: 14px; margin: 0; padding: 0;
  accent-color: var(--primary); cursor: pointer;
}

.form-row   { display: flex; gap: 8px; }
.form-group { display: flex; flex-direction: column; gap: 5px; flex: 1; }
.form-group label { font-size: 12px; font-weight: 500; color: var(--text-2); }

input, select {
  width: 100%; padding: 8px 10px; border: 1.5px solid var(--border);
  border-radius: var(--radius-sm); font-size: 13px; font-family: inherit;
  color: var(--text); background: var(--surface);
  transition: border-color 0.15s;
  outline: none;
}
input:focus, select:focus { border-color: var(--primary); }
input:disabled, select:disabled { background: var(--surface-2); color: var(--text-muted); cursor: not-allowed; }
.input-ticker { font-weight: 600; letter-spacing: 0.04em; }

/* Analyst chips */
.analyst-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.analyst-chip {
  display: flex; align-items: center; gap: 6px; padding: 7px 10px;
  border: 1.5px solid var(--border); border-radius: var(--radius-sm);
  font-size: 12px; font-weight: 500; color: var(--text-2);
  cursor: pointer; transition: all 0.15s; user-select: none;
  background: var(--surface);
}
.analyst-chip:hover:not(.disabled) { border-color: var(--primary); color: var(--primary); background: var(--primary-light); }
.analyst-chip.active  { border-color: var(--primary); background: var(--primary-light); color: var(--primary); }
.analyst-chip.disabled{ opacity: 0.5; cursor: not-allowed; }

/* Depth buttons */
.depth-row { display: flex; gap: 6px; }
.depth-btn {
  flex: 1; padding: 8px 6px; border: 1.5px solid var(--border);
  border-radius: var(--radius-sm); background: var(--surface);
  cursor: pointer; display: flex; flex-direction: column; align-items: center; gap: 2px;
  transition: all 0.15s; font-family: inherit;
}
.depth-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.depth-btn.active   { border-color: var(--primary); background: var(--primary-light); }
.depth-label { font-size: 12px; font-weight: 600; color: var(--text); }
.depth-desc  { font-size: 11px; color: var(--text-muted); }
.depth-btn.active .depth-label { color: var(--primary); }

/* Run button */
.run-btn {
  width: 100%; padding: 11px; border: none; border-radius: var(--radius-sm);
  background: var(--primary); color: #fff; font-size: 14px; font-weight: 600;
  cursor: pointer; font-family: inherit; display: flex; align-items: center;
  justify-content: center; gap: 8px; transition: background 0.15s;
}
.run-btn:hover:not(:disabled) { background: var(--primary-hover); }
.run-btn:disabled { opacity: 0.55; cursor: not-allowed; }

.error-box {
  padding: 10px 12px; background: var(--danger-light); border: 1px solid #FECACA;
  border-radius: var(--radius-sm); color: var(--danger); font-size: 12px;
  display: flex; align-items: flex-start; gap: 6px;
}

/* ── Main content ─────────────────────────────────────── */
.content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 24px;
}
.content > * + * { margin-top: 20px; }

/* Empty state */
.empty-state {
  display: flex; flex-direction: column;
  align-items: center; justify-content: center; gap: 12px;
  color: var(--text-muted);
  height: 100%;
  min-height: 300px;
}
.empty-icon { font-size: 48px; }
.empty-state h2 { font-size: 18px; color: var(--text-2); }
.empty-state p  { font-size: 14px; max-width: 320px; text-align: center; }

/* ── Cards ────────────────────────────────────────────── */
.card {
  background: var(--surface); border-radius: var(--radius);
  box-shadow: var(--shadow);
}
.card-header {
  padding: 16px 20px; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; gap: 10px;
}
.card-title  { font-size: 14px; font-weight: 600; color: var(--text); }
.card-subtitle       { font-size: 12px; color: var(--text-muted); margin-left: auto; }
.card-subtitle.success { color: var(--success); }

/* ── Agent progress ───────────────────────────────────── */
.teams-grid {
  padding: 16px 20px;
  display: flex; flex-wrap: wrap; gap: 16px;
}
.team-block {
  background: var(--surface-2); border-radius: var(--radius-sm);
  border: 1px solid var(--border); padding: 12px 14px; min-width: 160px;
}
.team-header {
  display: flex; align-items: center; gap: 6px; margin-bottom: 10px;
}
.team-icon { font-size: 14px; }
.team-name { font-size: 11px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--team-color, var(--text-muted)); }

.agent-list { display: flex; flex-direction: column; gap: 6px; }
.agent-row  {
  display: flex; align-items: center; gap: 8px;
  font-size: 12.5px; color: var(--text-muted);
  transition: color 0.2s;
}
.agent-row.in_progress { color: var(--running); font-weight: 500; }
.agent-row.completed   { color: var(--text-2); }

.agent-dot { width: 16px; height: 16px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.dot-idle  { width: 6px; height: 6px; border-radius: 50%; background: var(--border); }
.dot-pulse {
  width: 8px; height: 8px; border-radius: 50%; background: var(--running);
  animation: pulse-scale 1s ease-in-out infinite;
}
.dot-check { font-size: 11px; color: var(--success); font-weight: 700; }

/* ── Decision card ────────────────────────────────────── */
.decision-card .card-header { background: linear-gradient(135deg, #EEF2FF 0%, #F0FDF4 100%); }
.decision-card .markdown-body { padding: 20px; }

/* ── Report sections ──────────────────────────────────── */
.sections-list { padding: 8px 0; }
.section-item  { border-bottom: 1px solid var(--border); }
.section-item:last-child { border-bottom: none; }

.section-toggle {
  width: 100%; padding: 12px 20px; background: none; border: none;
  display: flex; align-items: center; gap: 10px; cursor: pointer;
  font-family: inherit; text-align: left; transition: background 0.1s;
}
.section-toggle:hover { background: var(--surface-2); }
.section-icon { font-size: 14px; }
.section-name { font-size: 13px; font-weight: 500; color: var(--text-2); flex: 1; }
.section-arrow {
  font-size: 18px; color: var(--text-muted); transition: transform 0.2s; line-height: 1;
}
.section-arrow.open { transform: rotate(90deg); }
.section-body { padding: 4px 20px 20px; }

/* ── Spinner ──────────────────────────────────────────── */
.spinner {
  width: 14px; height: 14px; border: 2px solid rgba(37,99,235,0.25);
  border-top-color: var(--running); border-radius: 50%;
  animation: spin 0.7s linear infinite; display: inline-block; flex-shrink: 0;
}
.spinner.white {
  border-color: rgba(255,255,255,0.3);
  border-top-color: #fff;
}

/* ── Header tabs ──────────────────────────────────────── */
.header-tabs { display: flex; gap: 4px; margin-left: 28px; margin-right: auto; }
.tab-btn {
  font: inherit; font-size: 13px; font-weight: 500;
  padding: 6px 14px; border: none; border-radius: var(--radius-sm);
  background: none; color: var(--text-muted); cursor: pointer;
  transition: all 0.15s;
}
.tab-btn:hover  { color: var(--text); background: var(--surface-2); }
.tab-btn.active { color: var(--primary); background: var(--primary-light); font-weight: 600; }

/* ── History view ─────────────────────────────────────── */
.history-view {
  flex: 1; min-height: 0; overflow-y: auto; padding: 24px;
  max-width: 920px; width: 100%; margin: 0 auto;
}
.history-head {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 16px;
}
.history-head h2 { font-size: 18px; color: var(--text); }
.refresh-btn {
  font: inherit; font-size: 13px; padding: 7px 14px;
  border: 1.5px solid var(--border); border-radius: var(--radius-sm);
  background: var(--surface); color: var(--text-2); cursor: pointer;
}
.refresh-btn:hover:not(:disabled) { border-color: var(--primary); color: var(--primary); }
.refresh-btn:disabled { opacity: 0.55; cursor: not-allowed; }

.history-card { overflow: hidden; }
.history-table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
.history-table th {
  text-align: left; font-size: 11px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--text-muted);
  padding: 12px 16px; border-bottom: 1px solid var(--border); background: var(--surface-2);
}
.history-table td { padding: 12px 16px; border-bottom: 1px solid var(--border); color: var(--text-2); }
.history-table tr:last-child td { border-bottom: none; }
.history-table tbody tr:hover { background: var(--surface-2); }
.col-time   { font-variant-numeric: tabular-nums; color: var(--text); white-space: nowrap; }
.col-date   { font-variant-numeric: tabular-nums; color: var(--text-muted); white-space: nowrap; }
.col-ticker { font-weight: 700; letter-spacing: 0.03em; color: var(--text); }
.col-actions { text-align: right; white-space: nowrap; }

/* Sortable column headers */
.history-table th { padding: 0; }
.sort-th {
  width: 100%; font: inherit; font-size: 11px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted);
  background: none; border: none; cursor: pointer; text-align: left;
  padding: 12px 16px; display: flex; align-items: center; gap: 6px;
  transition: color 0.15s;
}
.sort-th:hover { color: var(--primary); }
.sort-arrow { font-size: 9px; color: var(--primary); min-width: 9px; }

.action-badge {
  display: inline-block; font-size: 11px; font-weight: 600;
  padding: 3px 10px; border-radius: 12px;
}
.action-badge.buy  { background: var(--success-light); color: var(--success); }
.action-badge.sell { background: var(--danger-light);  color: var(--danger); }
.action-badge.hold { background: #FEF3C7; color: #92400E; }
.action-badge.none { background: var(--surface-2); color: var(--text-muted); }

.link-btn {
  font: inherit; font-size: 12.5px; font-weight: 500;
  padding: 5px 11px; margin-left: 6px;
  border: 1.5px solid var(--border); border-radius: var(--radius-sm);
  background: var(--surface); color: var(--text-2); cursor: pointer;
  transition: all 0.15s;
}
.link-btn:hover:not(:disabled) { border-color: var(--primary); color: var(--primary); background: var(--primary-light); }
.link-btn.pdf:hover:not(:disabled) { border-color: var(--danger); color: var(--danger); background: var(--danger-light); }
.link-btn:disabled { opacity: 0.45; cursor: not-allowed; }
</style>
