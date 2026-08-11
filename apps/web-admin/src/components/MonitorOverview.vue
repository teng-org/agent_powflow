<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { RUNTIME_CONFIG_DEFAULTS } from '../constants/runtimeConfigDefaults'
import UiSelect from './common/UiSelect.vue'
import UiPager from './common/UiPager.vue'
import vModalFocus from './common/modalFocusDirective'
import vInteractiveRow from './common/interactiveRowDirective'

const props = defineProps({
  overviewState: { type: Object, required: true },
  sectionDomId: { type: Function, default: null },
  loadOverview: { type: Function, required: false, default: null },
  loadSystemLogs: { type: Function, required: false, default: null },
  loadSystemLogStats3d: { type: Function, required: false, default: null },
  loadRuntimeSeries: { type: Function, required: false, default: null },
  loadPermissionWarnings: { type: Function, required: false, default: null },
  loadRiskRules: { type: Function, required: false, default: null },
  saveRiskRules: { type: Function, required: false, default: null },
  loadRiskEvents: { type: Function, required: false, default: null },
  ackRiskEvent: { type: Function, required: false, default: null },
  resolveRiskEvent: { type: Function, required: false, default: null },
  saveRuntimeConfig: { type: Function, required: false, default: null },
  onOpenApiKeyRisk: { type: Function, required: false, default: null },
  onOpenCrossStoreRepairs: { type: Function, required: false, default: null },
  evaluateApiKeyLifecycleAlerts: { type: Function, required: false, default: null },
})

const showLogModal = ref(false)
const metricKey = ref('token')
const chartDays = ref(30)
const chartMode = ref('line')
const hoveredPoint = ref(null)
const ringMetricKey = ref('token_day')
const chartAnimating = ref(false)
const ringAnimating = ref(false)
const lastRefreshAt = ref(null)
const refreshInFlight = ref(false)
const logsLoading = ref(false)
const logsRequesting = ref(false)
const LOG_PAGE_SIZE = 240
const LOG_WINDOW_LIMIT = 5000
const logScrollRef = ref(null)
const logPinnedBottom = ref(true)
const logViewMode = ref('history') // history | live
const logLiveEnabled = ref(true)
const logLiveIntervalSec = ref(2)
const logLastUpdatedAt = ref('')
const activeRiskLevels = ref([])
const activeQuickPreset = ref('')
const savedLogViews = ref([])
const selectedSavedViewId = ref('')
const showSaveViewModal = ref(false)
const saveViewDraftName = ref('')
const saveViewInputRef = ref(null)
const viewOpNotice = ref('')
let viewOpNoticeTimer = null
const copyNotice = ref('')
let copyNoticeTimer = null
const showPolicyModal = ref(false)
const showPermissionModal = ref(false)
const permissionDetailTab = ref('warnings') // warnings | risk_events | risk_rules
const permissionLoading = ref(false)
const selectedPermissionWarn = ref(null)
const riskEventsLoading = ref(false)
const selectedRiskEvent = ref(null)
const riskActionLoadingId = ref('')
const riskRulesLoading = ref(false)
const riskRulesSaving = ref(false)
const riskRulesDraft = ref([])
const selectedRiskRuleIndex = ref(-1)
const riskRuleFilters = reactive({
  q: '',
  riskLevel: '',
  decision: '',
  enabled: '',
  page: 1,
  pageSize: 20,
})
const riskRulesValidationErrors = ref([])
const riskThresholdsDraftText = ref('{}')
const riskThresholdsParseError = ref('')
const showRiskRuleImportModal = ref(false)
const riskRuleImportText = ref('')
const riskRuleImportError = ref('')
const showRiskActionModal = ref(false)
const riskActionDraft = reactive({
  action: '',
  eventId: '',
  note: '',
  item: null,
})
const policySaving = ref(false)
const policyDraft = ref({
  hotRetentionDays: 30,
  archiveRetentionDays: 60,
  archiveEnabled: true,
  archivePeriod: 'month',
  expiredAction: 'delete',
  pathChangeStrategy: 'migrate',
})
const permissionFilters = reactive({
  riskLevel: '',
  source: '',
  role: '',
  q: '',
  page: 1,
  pageSize: 50,
  days: 7,
})
const riskEventFilters = reactive({
  page: 1,
  pageSize: 20,
  days: 7,
  riskLevel: '',
  riskTag: '',
  decision: '',
  reasonCode: '',
  eventStatus: '',
  q: '',
})
const onlyNewLogs = ref(false)
const selectedLogItem = ref(null)
const newLogIds = ref(new Set())
const sessionNewLogIds = ref(new Set())
const seenLogIds = ref(new Set())
const locatingLogFingerprint = ref('')
let locatingLogTimer = null
const freshTimerMap = new Map()
const REFRESH_NOISE_PATHS = new Set([
  '/api/v1/system/health',
  '/api/v1/admin/system/runtime-config',
  'Control /api/v1/admin/tasks',
  '/api/v1/admin/chat/store/status',
  '/api/v1/admin/system/logs',
  '/api/v1/admin/system/logs/stats',
  '/api/v1/admin/system/runtime-stats/daily',
  '/api/v1/admin/system/alerts/preview',
  '/api/v1/admin/system/permission-warnings',
  '/api/v1/admin/system/log-index/health',
  '/api/v1/admin/system/risk/overview',
  '/api/v1/admin/system/risk/events',
  '/api/v1/admin/system/risk/events/stats',
])
let autoRefreshTimer = null
let logLiveTimer = null
let chartSwitchTimer = null
let ringSwitchTimer = null
let logsLoadingWatchdog = null
let riskEventsLoadingWatchdog = null
const logFilters = reactive({
  field: 'message',
  keyword: '',
  level: '',
  category: '',
  source: 'all',
  from: '',
  to: '',
  periodStart: '00:00',
  periodEnd: '23:59',
  days: 7,
  includeNoise: false,
})
const LOG_FILTER_DEFAULTS = {
  field: 'message',
  keyword: '',
  level: '',
  category: '',
  source: 'all',
  from: '',
  to: '',
  periodStart: '00:00',
  periodEnd: '23:59',
  days: 7,
  includeNoise: false,
}

const metrics = [
  { key: 'token', label: 'Token 用量', unit: 'tokens' },
  { key: 'users', label: '用户增长', unit: 'users' },
  { key: 'chats', label: '新建对话', unit: 'sessions' },
  { key: 'cost', label: 'API 总费率', unit: 'usd' },
]
const ringMetrics = [
  { key: 'token_day', label: '日 Token 阈值', unit: 'tokens' },
  { key: 'cost_day', label: '日费率阈值', unit: 'usd' },
  { key: 'concurrency_day', label: '并发阈值', unit: 'count' },
  { key: 'storage_day', label: '日志存储阈值', unit: 'mb' },
]
const fieldOptions = [
  { label: 'message', value: 'message' },
  { label: 'path', value: 'path' },
  { label: 'source', value: 'source' },
  { label: 'role', value: 'role' },
  { label: 'trace_id', value: 'trace_id' },
  { label: 'event_id', value: 'event_id' },
  { label: 'request_id', value: 'request_id' },
]
const levelOptions = [
  { label: '全部', value: '' },
  { label: 'warn', value: 'warn' },
  { label: 'error', value: 'error' },
  { label: 'critical', value: 'critical' },
  { label: 'info', value: 'info' },
]
const categoryOptions = [
  { label: '全部', value: '' },
  { label: 'system', value: 'system' },
  { label: 'auth', value: 'auth' },
  { label: 'task', value: 'task' },
  { label: 'security', value: 'security' },
  { label: 'error', value: 'error' },
  { label: 'chat_api', value: 'chat_api' },
]
const sourceOptions = [
  { label: '全部', value: 'all' },
  { label: 'execution', value: 'execution' },
  { label: 'chat', value: 'chat' },
]
const logContentOptions = [
  { label: '业务日志（默认）', value: false },
  { label: '全部日志（含系统噪音）', value: true },
]
const DEFAULT_VIEW_ID = '__default__'
const dayWindowOptions = [
  { label: '近3天', value: 3 },
  { label: '近30天', value: 30 },
  { label: '近60天', value: 60 },
  { label: '近180天', value: 180 },
]
const liveIntervalOptions = [
  { label: '1s', value: 1 },
  { label: '2s', value: 2 },
  { label: '3s', value: 3 },
  { label: '5s', value: 5 },
]
const savedViewOptions = computed(() => [
  { label: '默认视图', value: DEFAULT_VIEW_ID },
  ...savedLogViews.value.map((v) => ({ label: v.name, value: v.id })),
])

const chartRect = { w: 1000, h: 320, left: 64, right: 965, top: 22, bottom: 280 }

const systemLogCount3d = computed(() => {
  const s = props.overviewState.systemLogStats3d || {}
  return Number(s.warn || 0) + Number(s.error || 0) + Number(s.critical || 0)
})

const formattedSystemLogCount3d = computed(() => (systemLogCount3d.value > 999 ? '999+' : String(systemLogCount3d.value)))
const riskStats3d = computed(() => {
  const s = props.overviewState.systemLogStats3d || {}
  return {
    warn: Number(s.warn || s.warning || 0),
    error: Number(s.error || 0),
    critical: Number(s.critical || 0),
  }
})

const alertCount = computed(() => {
  const n = Array.isArray(props.overviewState.alertsPreview) ? props.overviewState.alertsPreview.length : 0
  return n > 999 ? '999+' : String(n)
})

const permissionWarnCount = computed(() => {
  const n = Number(props.overviewState.permissionWarningsTotal || 0)
  return n > 999 ? '999+' : String(n)
})

const crossStoreAlert = computed(() =>
  (Array.isArray(props.overviewState.alertsPreview) ? props.overviewState.alertsPreview : [])
    .find((item) => item?.alert_type === 'cross_store_repair') || null,
)

const alertSummary = computed(() => {
  if (crossStoreAlert.value?.message) return crossStoreAlert.value.message
  const first = Array.isArray(props.overviewState.alertsPreview) ? props.overviewState.alertsPreview[0] : null
  return first?.message || '当前没有开放告警'
})

function openCrossStoreAlert() {
  if (crossStoreAlert.value && props.onOpenCrossStoreRepairs) props.onOpenCrossStoreRepairs()
}
const riskEventWarnCount = computed(() => {
  const n = Number(props.overviewState.riskEventsTotal || 0)
  return n > 999 ? '999+' : String(n)
})
const apiKeyLifecycleSummary = computed(() => {
  const s = props.overviewState?.apiKeyLifecycleSummary || {}
  return {
    total: Number(s.total || 0),
    expiringSoon: Number(s.expiring_soon || 0),
    expired: Number(s.expired || 0),
    openAlerts: Number(s.open_alerts || 0),
  }
})
const apiKeyLifecycleTrend = computed(() =>
  Array.isArray(props.overviewState?.apiKeyLifecycleTrend) ? props.overviewState.apiKeyLifecycleTrend : [],
)
const apiKeyTrendMetric = ref('open')
const apiKeyTrendMetricOptions = [
  { key: 'open', label: '开放告警', field: 'open_count' },
  { key: 'critical', label: '严重告警', field: 'critical_count' },
  { key: 'total', label: '总告警', field: 'total' },
]
const apiKeyTrendSpark = computed(() => {
  const metric = apiKeyTrendMetricOptions.find((x) => x.key === apiKeyTrendMetric.value) || apiKeyTrendMetricOptions[0]
  const rows = apiKeyLifecycleTrend.value
    .slice()
    .sort((a, b) => String(a?.day || '').localeCompare(String(b?.day || '')))
  const series = rows.map((x) => Number(x?.[metric.field] || 0))
  const spark = (arr) => {
    const data = Array.isArray(arr) ? arr : []
    if (!data.length) return '--'
    const chars = ['▁', '▂', '▃', '▄', '▅', '▆', '▇', '█']
    const max = Math.max(...data)
    const min = Math.min(...data)
    const span = Math.max(1, max - min)
    return data
      .map((v) => chars[Math.max(0, Math.min(chars.length - 1, Math.round(((v - min) / span) * (chars.length - 1))))])
      .join('')
  }
  const last7 = series.slice(-7)
  const last30 = series.slice(-30)
  return {
    label: metric.label,
    spark7: spark(last7),
    spark30: spark(last30),
    sum7: last7.reduce((a, b) => a + Number(b || 0), 0),
    sum30: last30.reduce((a, b) => a + Number(b || 0), 0),
  }
})
const apiKeyRiskTrend = ref({
  openAlertsDelta: 0,
  expiringSoonDelta: 0,
  expiredDelta: 0,
})
const prevApiKeyLifecycleSummary = ref(null)
const apiKeyRiskEvaluating = ref(false)
const apiKeyRiskLevel = computed(() => {
  const s = apiKeyLifecycleSummary.value
  if (s.expired > 0 || s.openAlerts >= 5) return 'high'
  if (s.expiringSoon > 0 || s.openAlerts > 0) return 'medium'
  return 'low'
})

function trendMark(delta) {
  const n = Number(delta || 0)
  if (n > 0) return '↑'
  if (n < 0) return '↓'
  return '→'
}

function trendClass(delta) {
  const n = Number(delta || 0)
  if (n > 0) return 'up'
  if (n < 0) return 'down'
  return 'flat'
}

async function openApiKeyRisk() {
  if (!props.onOpenApiKeyRisk) return
  await props.onOpenApiKeyRisk()
}

async function evaluateApiKeyRiskNow() {
  if (!props.evaluateApiKeyLifecycleAlerts || apiKeyRiskEvaluating.value) return
  apiKeyRiskEvaluating.value = true
  try {
    await props.evaluateApiKeyLifecycleAlerts()
    await refreshOverview()
  } finally {
    apiKeyRiskEvaluating.value = false
  }
}
const selectedRiskRule = computed(() => {
  const i = Number(selectedRiskRuleIndex.value)
  if (!Number.isInteger(i) || i < 0 || i >= riskRulesDraft.value.length) return null
  return riskRulesDraft.value[i]
})
const filteredRiskRules = computed(() => {
  const q = String(riskRuleFilters.q || '').trim().toLowerCase()
  const lv = String(riskRuleFilters.riskLevel || '').trim().toUpperCase()
  const dc = String(riskRuleFilters.decision || '').trim().toUpperCase()
  const en = String(riskRuleFilters.enabled || '').trim()
  const rows = []
  for (let i = 0; i < riskRulesDraft.value.length; i += 1) {
    const r = riskRulesDraft.value[i] || {}
    if (lv && String(r.risk_level || '').toUpperCase() !== lv) continue
    if (dc && String(r.decision || '').toUpperCase() !== dc) continue
    if (en === '1' && !Boolean(r.enabled)) continue
    if (en === '0' && Boolean(r.enabled)) continue
    if (q) {
      const bag = [
        r.id,
        r.method,
        r.path_pattern,
        r.reason_code,
        Array.isArray(r.risk_tags) ? r.risk_tags.join(',') : '',
      ]
        .join(' ')
        .toLowerCase()
      if (!bag.includes(q)) continue
    }
    rows.push({ row: r, index: i })
  }
  return rows
})
const riskRulesPageTotal = computed(() => filteredRiskRules.value.length)
const riskRulePageCount = computed(() => Math.max(1, Math.ceil(
  riskRulesPageTotal.value / Math.max(1, Number(riskRuleFilters.pageSize || 20)),
)))
const permissionWarningsPageCount = computed(() => Math.max(1, Math.ceil(
  Number(props.overviewState.permissionWarningsTotal || 0)
    / Math.max(1, Number(props.overviewState.permissionWarningsPageSize || 50)),
)))
const riskEventsPageCount = computed(() => Math.max(1, Math.ceil(
  Number(props.overviewState.riskEventsTotal || 0)
    / Math.max(1, Number(props.overviewState.riskEventsPageSize || 20)),
)))
const pagedRiskRules = computed(() => {
  const p = Math.max(1, Number(riskRuleFilters.page || 1))
  const ps = Math.max(1, Number(riskRuleFilters.pageSize || 20))
  const start = (p - 1) * ps
  return filteredRiskRules.value.slice(start, start + ps)
})

const logIndexHealthText = computed(() => {
  const item = props.overviewState?.logIndexHealth || null
  if (!item) return '索引状态：未知'
  const stale = Number(item.staleness_seconds ?? -1)
  const ok = stale >= 0 && stale <= 45 && !String(item.last_error || '').trim()
  const syncAt = item.last_success_at ? formatTsZh(item.last_success_at) : '-'
  return `${ok ? '索引正常' : '索引滞后'} · 最近同步 ${syncAt}`
})

function timeToMinutes(value) {
  const s = String(value || '').trim()
  if (!s || !s.includes(':')) return null
  const [hhRaw, mmRaw] = s.split(':')
  const hh = Number(hhRaw)
  const mm = Number(mmRaw)
  if (!Number.isInteger(hh) || !Number.isInteger(mm)) return null
  if (hh < 0 || hh > 23 || mm < 0 || mm > 59) return null
  return hh * 60 + mm
}

const isPeriodCrossDay = computed(() => {
  const start = timeToMinutes(logFilters.periodStart)
  const end = timeToMinutes(logFilters.periodEnd)
  return start != null && end != null && end < start
})

const filteredLogs = computed(() => {
  const list = Array.isArray(props.overviewState.systemLogs) ? props.overviewState.systemLogs : []
  const keyword = logFilters.keyword.trim().toLowerCase()
  const fromMs = logFilters.from ? new Date(`${logFilters.from}T00:00:00`).getTime() : null
  const toMs = logFilters.to ? new Date(`${logFilters.to}T23:59:59.999`).getTime() : null
  const periodStartMin = timeToMinutes(logFilters.periodStart)
  const periodEndMin = timeToMinutes(logFilters.periodEnd)
  return list.filter((x) => {
    const path = String(x.path || '')
    const source = String(x.source || '')
    if (!logFilters.includeNoise && source === 'execution-api' && REFRESH_NOISE_PATHS.has(path)) return false
    if (logFilters.level && String(x.level || '').toLowerCase() !== logFilters.level.toLowerCase()) return false
    if (logFilters.category && String(x.category || '').toLowerCase() !== logFilters.category.toLowerCase()) return false
    if (logFilters.source !== 'all') {
      if (logFilters.source === 'execution' && source !== 'execution-api') return false
      if (logFilters.source === 'chat' && source !== 'chat-plane') return false
    }
    const tsMs = Date.parse(String(x.ts || ''))
    if (fromMs != null && Number.isFinite(tsMs) && tsMs < fromMs) return false
    if (toMs != null && Number.isFinite(tsMs) && tsMs > toMs) return false
    if (periodStartMin != null && periodEndMin != null && Number.isFinite(tsMs)) {
      const d = new Date(tsMs)
      const logMin = d.getHours() * 60 + d.getMinutes()
      const inPeriod =
        periodEndMin >= periodStartMin
          ? logMin >= periodStartMin && logMin <= periodEndMin
          : logMin >= periodStartMin || logMin <= periodEndMin
      if (!inPeriod) return false
    }
    if (keyword) {
      const fieldValue = String(x?.[logFilters.field] || '').toLowerCase()
      return fieldValue.includes(keyword)
    }
    return true
  })
})

const quickPresetFilteredLogs = computed(() => {
  const list = filteredLogs.value
  const preset = activeQuickPreset.value
  if (!preset) return list
  if (preset === 'api_fail') {
    return list.filter((x) => ['fail', 'error', 'forbidden', 'unauthorized', 'deny'].includes(String(x.status || '').toLowerCase()))
  }
  if (preset === 'auth_fail') {
    return list.filter((x) => {
      const path = String(x.path || '').toLowerCase()
      const category = String(x.category || '').toLowerCase()
      const status = String(x.status || '').toLowerCase()
      return (path.includes('/auth/') || category === 'auth') && ['fail', 'error', 'forbidden', 'unauthorized', 'deny'].includes(status)
    })
  }
  if (preset === 'task_error') {
    return list.filter((x) => {
      const path = String(x.path || '').toLowerCase()
      const category = String(x.category || '').toLowerCase()
      const level = String(x.level || '').toLowerCase()
      return (path.includes('/tasks') || category === 'task') && ['error', 'critical'].includes(level)
    })
  }
  return list
})

const descWindowLogs = computed(() => {
  const list = [...quickPresetFilteredLogs.value]
  list.sort((a, b) => {
    const ta = Date.parse(String(a.ts || ''))
    const tb = Date.parse(String(b.ts || ''))
    if (Number.isFinite(ta) && Number.isFinite(tb)) return tb - ta
    if (Number.isFinite(tb)) return 1
    if (Number.isFinite(ta)) return -1
    return String(b.ts || '').localeCompare(String(a.ts || ''))
  })
  return list.slice(0, LOG_WINDOW_LIMIT)
})

const RISK_ORDER = { critical: 3, error: 2, warn: 1, warning: 1 }

function normalizeRiskLevel(level) {
  const v = String(level || '').toLowerCase()
  if (v === 'warning') return 'warn'
  return v
}

function isLogDebugEnabled() {
  try {
    return window?.localStorage?.getItem('powflow:logDebug') === '1'
  } catch {
    return false
  }
}

function logDebug(...args) {
  if (!isLogDebugEnabled()) return
  // eslint-disable-next-line no-console
  console.debug('[PowflowLogDebug][Overview]', ...args)
}

const LOG_VIEWS_KEY = 'powflow:systemLogViews'
const RUNTIME_LOG_VIEWS_KEY = 'system_log_saved_views'

function getRuntimeConfigBag() {
  const cfg = props.overviewState?.runtimeConfig
  if (!cfg || typeof cfg !== 'object') return {}
  if (cfg.item && typeof cfg.item === 'object') return cfg.item
  return cfg
}

function sanitizeLogViewFilters(filters) {
  const out = { ...LOG_FILTER_DEFAULTS }
  const src = filters && typeof filters === 'object' ? filters : {}
  for (const k of Object.keys(LOG_FILTER_DEFAULTS)) {
    if (k in src) out[k] = src[k]
  }
  return out
}

function sanitizeSavedLogViews(rawList) {
  const rows = Array.isArray(rawList) ? rawList : []
  return rows
    .map((x) => ({
      id: String(x?.id || `view_${Date.now()}`),
      name: String(x?.name || '未命名视图').slice(0, 80),
      created_at: String(x?.created_at || new Date().toISOString()),
      filters: sanitizeLogViewFilters(x?.filters),
      quickPreset: String(x?.quickPreset || ''),
      riskLevels: Array.isArray(x?.riskLevels) ? x.riskLevels.map((v) => String(v || '').toLowerCase()).filter(Boolean) : [],
    }))
    .slice(0, 20)
}

function loadSavedLogViews() {
  const runtimeViews = getRuntimeConfigBag()?.[RUNTIME_LOG_VIEWS_KEY]
  if (Array.isArray(runtimeViews)) {
    savedLogViews.value = sanitizeSavedLogViews(runtimeViews)
    if (selectedSavedViewId.value && selectedSavedViewId.value !== DEFAULT_VIEW_ID && !savedLogViews.value.find((x) => x.id === selectedSavedViewId.value)) {
      selectedSavedViewId.value = ''
    }
    return
  }
  try {
    const raw = window?.localStorage?.getItem(LOG_VIEWS_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    savedLogViews.value = sanitizeSavedLogViews(parsed)
  } catch {
    savedLogViews.value = []
  }
  if (selectedSavedViewId.value && selectedSavedViewId.value !== DEFAULT_VIEW_ID && !savedLogViews.value.find((x) => x.id === selectedSavedViewId.value)) {
    selectedSavedViewId.value = ''
  }
}

async function persistSavedLogViews() {
  const cleanViews = sanitizeSavedLogViews(savedLogViews.value)
  savedLogViews.value = cleanViews
  if (props.saveRuntimeConfig) {
    try {
      await props.saveRuntimeConfig({ [RUNTIME_LOG_VIEWS_KEY]: cleanViews }, { versioned: false })
    } catch (err) {
      logDebug('persistSavedLogViews runtime-config failed, fallback local', err)
    }
  }
  try {
    window?.localStorage?.setItem(LOG_VIEWS_KEY, JSON.stringify(cleanViews))
  } catch {
    // noop
  }
}

const riskFilteredLogs = computed(() => {
  if (!activeRiskLevels.value.length) return descWindowLogs.value
  const selected = new Set(activeRiskLevels.value)
  return descWindowLogs.value.filter((x) => selected.has(normalizeRiskLevel(x.level)))
})

const visibleLogs = computed(() => {
  const list = onlyNewLogs.value
    ? riskFilteredLogs.value.filter((x) => sessionNewLogIds.value.has(String(x.id)))
    : [...riskFilteredLogs.value]
  if (!activeRiskLevels.value.length) return list.reverse()
  list.sort((a, b) => {
    const la = normalizeRiskLevel(a.level)
    const lb = normalizeRiskLevel(b.level)
    const sa = RISK_ORDER[la] || 0
    const sb = RISK_ORDER[lb] || 0
    if (sa !== sb) return sb - sa
    const ta = Date.parse(String(a.ts || ''))
    const tb = Date.parse(String(b.ts || ''))
    if (Number.isFinite(ta) && Number.isFinite(tb)) return tb - ta
    return 0
  })
  return list
})

const selectedMetric = computed(() => metrics.find((x) => x.key === metricKey.value) || metrics[0])

const selectedMetricSeries = computed(() => {
  const seriesMap = props.overviewState.runtimeSeries || {}
  const raw = Array.isArray(seriesMap[metricKey.value]) ? seriesMap[metricKey.value] : []
  return raw.slice(-Math.max(7, Number(chartDays.value || 30)))
})

function niceMax(value) {
  const n = Math.max(1, Number(value || 0))
  const p = 10 ** Math.floor(Math.log10(n))
  const m = n / p
  const base = m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10
  return base * p
}

function formatMetricValue(value, unit) {
  const n = Number(value || 0)
  if (unit === 'usd') return `$${n.toFixed(n >= 100 ? 0 : 2)}`
  if (unit === 'mb') {
    if (n >= 1024) return `${(n / 1024).toFixed(2)}GB`
    return `${n.toFixed(n >= 100 ? 0 : 1)}MB`
  }
  if (unit === 'tokens') {
    if (n >= 1000000) return `${(n / 1000000).toFixed(2)}M`
    if (n >= 1000) return `${(n / 1000).toFixed(1)}K`
    return `${Math.round(n)}`
  }
  return Math.round(n).toLocaleString()
}

function formatDayLabel(day) {
  const s = String(day || '')
  if (!s) return '-'
  const parts = s.split('-')
  if (parts.length === 3) return `${parts[1]}-${parts[2]}`
  return s
}

function dayKeyOf(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function buildAxisSlots(dayWindow, targetCount, rows) {
  const lastRow = Array.isArray(rows) && rows.length ? rows[rows.length - 1] : null
  const lastDayKey = dayKeyOf(lastRow?.day) || dayKeyOf(new Date())
  const lastDate = new Date(lastDayKey)
  const out = []
  const span = Math.max(1, dayWindow - 1)
  for (let i = 0; i < targetCount; i += 1) {
    const ratio = targetCount <= 1 ? 1 : i / (targetCount - 1)
    const offset = Math.round(span * ratio)
    const d = new Date(lastDate)
    d.setDate(lastDate.getDate() - (span - offset))
    const key = dayKeyOf(d)
    out.push({ key, label: formatDayLabel(key) })
  }
  return out
}

function barPath(x, y, w, h, bottomY) {
  if (h <= 0 || w <= 0) return ''
  const r = Math.min(6, w / 2, h)
  return [
    `M ${x} ${bottomY}`,
    `L ${x} ${y + r}`,
    `Q ${x} ${y} ${x + r} ${y}`,
    `L ${x + w - r} ${y}`,
    `Q ${x + w} ${y} ${x + w} ${y + r}`,
    `L ${x + w} ${bottomY}`,
    'Z',
  ].join(' ')
}

const chartModel = computed(() => {
  const rows = selectedMetricSeries.value
  const dayWindow = Number(chartDays.value || 30)
  const targetCount = dayWindow <= 7 ? 7 : dayWindow <= 30 ? 14 : 18
  const axisSlots = buildAxisSlots(dayWindow, targetCount, rows)
  const unit = selectedMetric.value.unit
  const valueByDay = new Map()
  for (const row of rows) {
    const key = dayKeyOf(row?.day)
    if (!key) continue
    valueByDay.set(key, Number(row?.value || 0))
  }

  const points = axisSlots.map((slot) => ({
    day: slot.key,
    label: slot.label,
    value: valueByDay.has(slot.key) ? Number(valueByDay.get(slot.key) || 0) : null,
  }))
  const valued = points.filter((x) => x.value != null)
  const hasData = valued.length > 0
  const maxValue = hasData ? niceMax(Math.max(1, ...valued.map((x) => Number(x.value || 0)))) : 0
  const minValue = 0
  const ySpan = Math.max(1, maxValue - minValue)
  const drawW = chartRect.right - chartRect.left
  const drawH = chartRect.bottom - chartRect.top
  const xSpan = Math.max(1, points.length - 1)
  const slot = xSpan > 0 ? drawW / xSpan : drawW
  const startX = chartRect.left
  const barW = dayWindow <= 7 ? 12 : dayWindow <= 30 ? 9 : 7

  const plot = points.map((p, i) => {
    const x = startX + i * slot
    const rawValue = p.value == null ? null : Number(p.value || 0)
    const y = rawValue == null ? null : chartRect.bottom - ((rawValue - minValue) / ySpan) * drawH
    const barXRaw = x - barW / 2
    const barX = Math.max(chartRect.left + 1, Math.min(barXRaw, chartRect.right - barW - 1))
    const barH = y == null ? 0 : Math.max(1, chartRect.bottom - y)
    return {
      ...p,
      slotIndex: i,
      x,
      y,
      barX,
      barY: y == null ? chartRect.bottom : y,
      barH,
      barW,
      barPath: y == null ? '' : barPath(barX, y, barW, barH, chartRect.bottom),
      valueText: rawValue == null ? '-' : formatMetricValue(rawValue, unit),
    }
  })
  const valuedPlot = plot.filter((p) => p.y != null)
  const lineSegments = []
  for (let i = 1; i < valuedPlot.length; i += 1) {
    const prev = valuedPlot[i - 1]
    const curr = valuedPlot[i]
    lineSegments.push({
      x1: prev.x,
      y1: prev.y,
      x2: curr.x,
      y2: curr.y,
      dashed: curr.slotIndex - prev.slotIndex > 1,
    })
  }

  const yTicks = hasData
    ? Array.from({ length: 6 }).map((_, i) => {
        const ratio = i / 5
        const value = maxValue * (1 - ratio)
        const y = chartRect.top + ratio * drawH
        return { y, value, label: formatMetricValue(value, unit) }
      })
    : []

  const step = Math.max(1, Math.floor(plot.length / 8))
  const xTicks = plot
    .filter((_, idx) => idx % step === 0 || idx === plot.length - 1)
    .map((p) => ({ x: p.x, label: p.label }))

  return {
    unit,
    maxValue,
    hasData,
    points: valuedPlot,
    bars: valuedPlot,
    lineSegments,
    yTicks,
    xTicks,
    lastText: valuedPlot.length ? valuedPlot[valuedPlot.length - 1].valueText : '-',
  }
})

function lastSeriesValue(seriesKey) {
  const seriesMap = props.overviewState.runtimeSeries || {}
  const list = Array.isArray(seriesMap[seriesKey]) ? seriesMap[seriesKey] : []
  return Number(list.length ? list[list.length - 1].value || 0 : 0)
}

const ringMetricModel = computed(() => {
  const cfg = {
    ...RUNTIME_CONFIG_DEFAULTS,
    ...getRuntimeConfigBag(),
  }
  const usageSummary = props.overviewState.usageSummary || {}
  const usageBudgets = Array.isArray(props.overviewState.usageBudgets) ? props.overviewState.usageBudgets : []
  const chatApiPool = Array.isArray(props.overviewState.chatApiPool) ? props.overviewState.chatApiPool : []
  const systemStorage = props.overviewState.systemStorage || {}
  const summaryTenantId = String(usageSummary.tenant_id || '').trim()
  const toNumberOrNull = (v) => {
    const n = Number(v)
    return Number.isFinite(n) && n > 0 ? n : null
  }
  const byDay = Array.isArray(usageSummary.by_day) ? usageSummary.by_day : []
  const latestByDay = byDay
    .slice()
    .sort((a, b) => String(a?.day || a?.date || '').localeCompare(String(b?.day || b?.date || '')))
    .slice(-1)[0] || {}

  const pickBudgetLimit = (fieldName) => {
    const candidates = usageBudgets
      .filter((x) => x && x.enabled !== false)
      .filter((x) => toNumberOrNull(x[fieldName]) != null)

    const preferTenantExact = candidates.find(
      (x) => String(x.scope_type || '').toLowerCase() === 'tenant' && summaryTenantId && String(x.scope_value || '') === summaryTenantId,
    )
    if (preferTenantExact) return toNumberOrNull(preferTenantExact[fieldName])

    const preferTenantWildcard = candidates.find(
      (x) => String(x.scope_type || '').toLowerCase() === 'tenant' && String(x.scope_value || '') === '*',
    )
    if (preferTenantWildcard) return toNumberOrNull(preferTenantWildcard[fieldName])

    const preferGlobal = candidates.find((x) => String(x.scope_type || '').toLowerCase() === 'global')
    if (preferGlobal) return toNumberOrNull(preferGlobal[fieldName])

    return null
  }

  const poolConcurrencyCapacity = chatApiPool
    .filter((x) => x && x.enabled !== false && x.frozen !== true)
    .reduce((sum, x) => sum + Math.max(0, Number(x.max_concurrency || 0)), 0)

  const thresholdMap = {
    // 系统配置优先：便于与“系统配置”页面阈值保持同步；
    // 若未配置，再回退到预算规则。
    token_day: toNumberOrNull(cfg.api_daily_token_threshold) ?? pickBudgetLimit('daily_token_limit'),
    cost_day: toNumberOrNull(cfg.api_daily_cost_threshold) ?? pickBudgetLimit('daily_cost_limit'),
    concurrency_day:
      toNumberOrNull(cfg.api_concurrency_threshold) ??
      toNumberOrNull(poolConcurrencyCapacity) ??
      toNumberOrNull(cfg.api_slot_max_per_endpoint),
    storage_day:
      toNumberOrNull(cfg.system_log_storage_threshold) ??
      toNumberOrNull(cfg.system_log_storage_threshold_mb) ??
      (toNumberOrNull(cfg.system_log_storage_threshold_gb) != null
        ? Number(toNumberOrNull(cfg.system_log_storage_threshold_gb)) * 1024
        : null) ??
      toNumberOrNull(systemStorage.threshold_mb),
  }
  const currentMap = {
    token_day: Number(latestByDay?.total_tokens || 0),
    cost_day: Number(latestByDay?.estimated_cost_usd || latestByDay?.cost_total || 0),
    concurrency_day: Number(props.overviewState.activeTaskCount || 0),
    storage_day: Number(systemStorage.used_mb || 0),
  }
  const key = ringMetricKey.value
  const threshold = thresholdMap[key]
  const current = Math.max(0, Number(currentMap[key] || 0))
  const hasThreshold = threshold != null
  const percentRaw = hasThreshold ? (current / threshold) * 100 : null
  const percent = hasThreshold ? Math.max(0, Math.min(100, Math.round(percentRaw))) : null
  const dash = hasThreshold ? 314 - (314 * percent) / 100 : 314
  const active = ringMetrics.find((x) => x.key === key) || ringMetrics[0]
  return {
    ...active,
    current,
    threshold,
    hasThreshold,
    percent,
    dash,
    currentText: formatMetricValue(current, active.unit === 'count' ? 'users' : active.unit),
    thresholdText: hasThreshold ? formatMetricValue(threshold, active.unit === 'count' ? 'users' : active.unit) : '未配置',
  }
})

const archivePolicyText = computed(() => {
  const cfg = getRuntimeConfigBag()
  const keepDays = Number(cfg.system_log_hot_retention_days || 30)
  const archiveUntilDays = Number(cfg.system_log_archive_retention_days || 60)
  const archive = cfg.system_log_archive_enabled === false ? '关闭压缩归档' : '开启压缩归档'
  const action = String(cfg.system_log_expired_action || 'delete')
  return `${keepDays}天热存，${archiveUntilDays}天归档窗口，${archive}，超期动作：${action}`
})

const archivePolicySummary = computed(() => {
  const cfg = getRuntimeConfigBag()
  const keepDays = Number(cfg.system_log_hot_retention_days || 30)
  const archiveUntilDays = Number(cfg.system_log_archive_retention_days || 60)
  const archiveEnabled = cfg.system_log_archive_enabled !== false ? '开启' : '关闭'
  const action = String(cfg.system_log_expired_action || 'delete')
  return `热存 ${keepDays} 天 | 归档窗口 ${archiveUntilDays} 天 | 压缩 ${archiveEnabled} | 超期 ${action}`
})

function openPolicyModal() {
  const cfg = getRuntimeConfigBag()
  policyDraft.value = {
    hotRetentionDays: Number(cfg.system_log_hot_retention_days || 30),
    archiveRetentionDays: Number(cfg.system_log_archive_retention_days || 60),
    archiveEnabled: cfg.system_log_archive_enabled !== false,
    archivePeriod: String(cfg.system_log_archive_period || 'month'),
    expiredAction: String(cfg.system_log_expired_action || 'delete'),
    pathChangeStrategy: String(cfg.system_log_path_change_strategy || 'migrate'),
  }
  showPolicyModal.value = true
}

function closePolicyModal() {
  if (policySaving.value) return
  showPolicyModal.value = false
}

async function savePolicyConfig() {
  if (!props.saveRuntimeConfig || policySaving.value) return
  const patch = {
    system_log_hot_retention_days: Math.max(1, Number(policyDraft.value.hotRetentionDays || 1)),
    system_log_archive_retention_days: Math.max(1, Number(policyDraft.value.archiveRetentionDays || 1)),
    system_log_archive_enabled: Boolean(policyDraft.value.archiveEnabled),
    system_log_archive_period: String(policyDraft.value.archivePeriod || 'month'),
    system_log_expired_action: String(policyDraft.value.expiredAction || 'delete'),
    system_log_path_change_strategy: String(policyDraft.value.pathChangeStrategy || 'migrate'),
  }
  policySaving.value = true
  try {
    await props.saveRuntimeConfig(patch)
    await refreshOverview()
    showPolicyModal.value = false
  } finally {
    policySaving.value = false
  }
}

function stopLogStreaming() {
  if (logLiveTimer) {
    clearInterval(logLiveTimer)
    logLiveTimer = null
  }
}

function startLogStreaming() {
  stopLogStreaming()
  if (!showLogModal.value || !logLiveEnabled.value || logViewMode.value !== 'live') return
  const intervalMs = Math.max(1000, Number(logLiveIntervalSec.value || 2) * 1000)
  logLiveTimer = setInterval(async () => {
    if (!showLogModal.value || !logLiveEnabled.value || logViewMode.value !== 'live') return
    await reloadLogs(1, { silent: true })
  }, intervalMs)
}

async function reloadLogs(page = 1, { silent = false } = {}) {
  if (!props.loadSystemLogs) return
  if (silent && logViewMode.value !== 'live') return
  if (logsRequesting.value) return
  logsRequesting.value = true
  if (!silent) {
    logsLoading.value = true
    if (logsLoadingWatchdog) clearTimeout(logsLoadingWatchdog)
    logsLoadingWatchdog = setTimeout(() => {
      if (logsLoading.value) {
        logsLoading.value = false
        pushViewOpNotice('日志加载超时，已停止等待')
      }
      logsLoadingWatchdog = null
    }, 30000)
  }
  try {
    await props.loadSystemLogs({
      page,
      pageSize: LOG_PAGE_SIZE,
      days: logFilters.days,
      field: logFilters.field,
      level: logFilters.level,
      category: logFilters.category,
      source: logFilters.source,
      q: logFilters.keyword,
      from: logFilters.from,
      to: logFilters.to,
      includeNoise: logFilters.includeNoise,
      riskLevels: [...activeRiskLevels.value],
    })
    if (props.loadSystemLogStats3d) {
      await props.loadSystemLogStats3d({
        days: 3,
        riskLevels: [...activeRiskLevels.value],
        category: logFilters.category,
        source: logFilters.source,
        q: logFilters.keyword,
      })
    }
    logLastUpdatedAt.value = new Date().toLocaleTimeString()
  } finally {
    logsRequesting.value = false
    if (!silent) {
      logsLoading.value = false
      if (logsLoadingWatchdog) {
        clearTimeout(logsLoadingWatchdog)
        logsLoadingWatchdog = null
      }
    }
  }
}

async function openLogCenter() {
  showLogModal.value = true
  newLogIds.value = new Set()
  sessionNewLogIds.value = new Set()
  seenLogIds.value = new Set()
  await reloadLogs(1)
  if (logViewMode.value === 'live') startLogStreaming()
  await nextTick()
  if (logScrollRef.value) {
    logScrollRef.value.scrollTop = logScrollRef.value.scrollHeight
    logPinnedBottom.value = true
  }
}

async function reloadRiskEvents(page = 1) {
  if (!props.loadRiskEvents) return
  riskEventsLoading.value = true
  if (riskEventsLoadingWatchdog) clearTimeout(riskEventsLoadingWatchdog)
  riskEventsLoadingWatchdog = setTimeout(() => {
    if (riskEventsLoading.value) {
      riskEventsLoading.value = false
      pushViewOpNotice('风险事件加载超时，已停止等待')
    }
    riskEventsLoadingWatchdog = null
  }, 30000)
  try {
    riskEventFilters.page = Math.max(1, Number(page || 1))
    await props.loadRiskEvents({
      page: riskEventFilters.page,
      pageSize: riskEventFilters.pageSize,
      days: riskEventFilters.days,
      riskLevel: riskEventFilters.riskLevel,
      riskTag: riskEventFilters.riskTag,
      decision: riskEventFilters.decision,
      reasonCode: riskEventFilters.reasonCode,
      eventStatus: riskEventFilters.eventStatus,
      q: riskEventFilters.q,
    })
  } finally {
    riskEventsLoading.value = false
    if (riskEventsLoadingWatchdog) {
      clearTimeout(riskEventsLoadingWatchdog)
      riskEventsLoadingWatchdog = null
    }
  }
}

function cloneRiskRule(rule = {}) {
  return {
    id: String(rule.id || ''),
    enabled: Boolean(rule.enabled !== false),
    priority: Number.isFinite(Number(rule.priority)) ? Number(rule.priority) : 0,
    method: String(rule.method || '*').toUpperCase(),
    path_pattern: String(rule.path_pattern || ''),
    risk_level: String(rule.risk_level || 'LOW').toUpperCase(),
    risk_tags: Array.isArray(rule.risk_tags) ? rule.risk_tags.map((x) => String(x || '').trim()).filter(Boolean) : [],
    decision: String(rule.decision || 'ALERT').toUpperCase(),
    reason_code: String(rule.reason_code || ''),
  }
}

function syncRiskRulesDraftFromState() {
  const srcRules = Array.isArray(props.overviewState.riskRules) ? props.overviewState.riskRules : []
  riskRulesDraft.value = srcRules.map((r) => cloneRiskRule(r))
  const thresholds = props.overviewState.riskThresholds && typeof props.overviewState.riskThresholds === 'object'
    ? props.overviewState.riskThresholds
    : {}
  riskThresholdsDraftText.value = JSON.stringify(thresholds, null, 2)
  riskRulesValidationErrors.value = []
  riskThresholdsParseError.value = ''
  selectedRiskRuleIndex.value = riskRulesDraft.value.length ? 0 : -1
  riskRuleFilters.page = 1
}

function riskRulesMethodsOverlap(a, b) {
  const x = String(a || '*').toUpperCase()
  const y = String(b || '*').toUpperCase()
  return x === '*' || y === '*' || x === y
}

function riskRulePathMatch(path, pattern) {
  const p = String(path || '').toLowerCase()
  const pat = String(pattern || '').toLowerCase()
  if (!pat) return false
  if (!pat.includes('*')) return p === pat
  if (pat === '*') return true
  const parts = pat.split('*').filter(Boolean)
  if (!parts.length) return true
  let idx = 0
  for (const part of parts) {
    const pos = p.indexOf(part, idx)
    if (pos < 0) return false
    idx = pos + part.length
  }
  if (!pat.startsWith('*') && !p.startsWith(parts[0])) return false
  if (!pat.endsWith('*') && !p.endsWith(parts[parts.length - 1])) return false
  return true
}

function riskRulePathPatternsMayOverlap(aRaw, bRaw) {
  const a = String(aRaw || '').toLowerCase()
  const b = String(bRaw || '').toLowerCase()
  if (!a || !b) return false
  if (a === '*' || b === '*') return true
  if (!a.includes('*') && !b.includes('*')) return a === b
  if (!a.includes('*')) return riskRulePathMatch(a, b)
  if (!b.includes('*')) return riskRulePathMatch(b, a)
  const aPrefix = a.split('*', 1)[0]
  const bPrefix = b.split('*', 1)[0]
  if (aPrefix && bPrefix && !(aPrefix.startsWith(bPrefix) || bPrefix.startsWith(aPrefix))) return false
  const aSuffix = a.split('*').pop()
  const bSuffix = b.split('*').pop()
  if (aSuffix && bSuffix && !(aSuffix.endsWith(bSuffix) || bSuffix.endsWith(aSuffix))) return false
  return true
}

function validateRiskRulesDraft() {
  const out = []
  const seenIds = new Set()
  const rules = Array.isArray(riskRulesDraft.value) ? riskRulesDraft.value : []
  const allowedMethods = new Set(['*', 'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'])
  const allowedLevels = new Set(['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'])
  const allowedDecision = new Set(['ALLOW', 'ALERT'])
  const prior = []
  rules.forEach((raw, i) => {
    const row = cloneRiskRule(raw)
    const rid = row.id
    if (!rid) out.push(`rule[${i}] 缺少 id`)
    else if (seenIds.has(rid)) out.push(`rule[${i}] id 重复：${rid}`)
    else seenIds.add(rid)
    if (!row.path_pattern) out.push(`rule[${i}] 缺少 path_pattern`)
    if (!allowedMethods.has(row.method)) out.push(`rule[${i}] method 非法：${row.method}`)
    if (!allowedLevels.has(row.risk_level)) out.push(`rule[${i}] risk_level 非法：${row.risk_level}`)
    if (!allowedDecision.has(row.decision)) out.push(`rule[${i}] decision 非法：${row.decision}`)
    if (!Number.isInteger(row.priority) || row.priority < 0 || row.priority > 100000) {
      out.push(`rule[${i}] priority 必须是 0~100000 的整数`)
    }
    for (const prev of prior) {
      if (prev.priority !== row.priority) continue
      if (!riskRulesMethodsOverlap(prev.method, row.method)) continue
      if (riskRulePathPatternsMayOverlap(prev.path_pattern, row.path_pattern)) {
        out.push(`rule[${i}] 与 rule[${prev.i}] 存在优先级冲突（method/path_pattern 重叠）`)
      }
    }
    prior.push({ i, method: row.method, path_pattern: row.path_pattern, priority: row.priority })
  })
  riskRulesValidationErrors.value = out
  return out.length === 0
}

function parseRiskThresholdsDraft() {
  const text = String(riskThresholdsDraftText.value || '').trim() || '{}'
  try {
    const obj = JSON.parse(text)
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
      throw new Error('阈值必须是 JSON 对象')
    }
    riskThresholdsParseError.value = ''
    return obj
  } catch (err) {
    riskThresholdsParseError.value = err instanceof Error ? err.message : '阈值 JSON 解析失败'
    return null
  }
}

async function reloadRiskRules() {
  if (!props.loadRiskRules) return
  riskRulesLoading.value = true
  try {
    await props.loadRiskRules()
    syncRiskRulesDraftFromState()
  } finally {
    riskRulesLoading.value = false
  }
}

function addRiskRule() {
  riskRulesDraft.value.push(
    cloneRiskRule({
      id: `rule-${Date.now()}`,
      enabled: true,
      priority: 0,
      method: '*',
      path_pattern: '/api/v1/admin/',
      risk_level: 'MEDIUM',
      risk_tags: [],
      decision: 'ALERT',
      reason_code: 'CUSTOM_RULE',
    }),
  )
  selectedRiskRuleIndex.value = riskRulesDraft.value.length - 1
}

function removeRiskRule(index) {
  if (index < 0 || index >= riskRulesDraft.value.length) return
  riskRulesDraft.value.splice(index, 1)
  if (!riskRulesDraft.value.length) {
    selectedRiskRuleIndex.value = -1
    return
  }
  selectedRiskRuleIndex.value = Math.min(index, riskRulesDraft.value.length - 1)
}

function moveRiskRule(index, delta) {
  const list = riskRulesDraft.value
  const from = Number(index)
  const to = from + Number(delta)
  if (!Array.isArray(list) || from < 0 || from >= list.length || to < 0 || to >= list.length) return
  const [row] = list.splice(from, 1)
  list.splice(to, 0, row)
  if (selectedRiskRuleIndex.value === from) selectedRiskRuleIndex.value = to
}

function normalizeRiskRulePriorities() {
  const list = Array.isArray(riskRulesDraft.value) ? riskRulesDraft.value : []
  let p = list.length * 10
  for (const row of list) {
    row.priority = p
    p -= 10
  }
}

function normalizeRiskRuleTags(index) {
  const row = riskRulesDraft.value[index]
  if (!row) return
  row.risk_tags = Array.isArray(row.risk_tags)
    ? row.risk_tags
    : String(row.risk_tags || '')
        .split(',')
        .map((x) => String(x || '').trim())
        .filter(Boolean)
}

function setRiskRuleTagsFromInput(index, value) {
  const row = riskRulesDraft.value[index]
  if (!row) return
  row.risk_tags = String(value || '')
    .split(',')
    .map((x) => String(x || '').trim())
    .filter(Boolean)
}

async function saveRiskRulesDraft() {
  if (!props.saveRiskRules || riskRulesSaving.value) return
  const ok = validateRiskRulesDraft()
  if (!ok) return
  const thresholds = parseRiskThresholdsDraft()
  if (!thresholds) return
  riskRulesSaving.value = true
  try {
    const rules = riskRulesDraft.value.map((r) => {
      const x = cloneRiskRule(r)
      x.risk_tags = Array.isArray(x.risk_tags)
        ? x.risk_tags
        : String(x.risk_tags || '')
            .split(',')
            .map((v) => String(v || '').trim())
            .filter(Boolean)
      return x
    })
    await props.saveRiskRules({
      rules,
      thresholds,
      note: 'console update risk rules',
    })
    pushViewOpNotice('风险规则已保存并发布')
    await Promise.all([
      reloadRiskRules(),
      reloadRiskEvents(1),
      props.loadSystemLogStats3d
        ? props.loadSystemLogStats3d({
            days: 3,
            riskLevels: [...activeRiskLevels.value],
            category: logFilters.category,
            source: logFilters.source,
            q: logFilters.keyword,
          })
        : Promise.resolve(),
    ])
  } finally {
    riskRulesSaving.value = false
  }
}

function exportRiskRulesDraft() {
  const payload = {
    security_risk_rules: (riskRulesDraft.value || []).map((x) => cloneRiskRule(x)),
    security_alert_thresholds: parseRiskThresholdsDraft() || {},
  }
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `risk-rules-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
  pushViewOpNotice('已导出风险规则')
}

function openRiskRuleImportModal() {
  riskRuleImportText.value = ''
  riskRuleImportError.value = ''
  showRiskRuleImportModal.value = true
}

function selectRiskRule(index) {
  selectedRiskRuleIndex.value = Number(index)
}

function resetRiskRuleFilters() {
  riskRuleFilters.q = ''
  riskRuleFilters.riskLevel = ''
  riskRuleFilters.decision = ''
  riskRuleFilters.enabled = ''
  riskRuleFilters.page = 1
}

function setRiskRulePage(page) {
  riskRuleFilters.page = Math.max(1, Math.min(Number(page || 1), riskRulePageCount.value))
}

function closeRiskRuleImportModal() {
  if (riskRulesSaving.value) return
  showRiskRuleImportModal.value = false
  riskRuleImportError.value = ''
}

function applyRiskRuleImport() {
  const text = String(riskRuleImportText.value || '').trim()
  if (!text) {
    riskRuleImportError.value = '请输入 JSON 内容'
    return
  }
  try {
    const data = JSON.parse(text)
    if (!data || typeof data !== 'object' || Array.isArray(data)) {
      throw new Error('JSON 根节点必须是对象')
    }
    const rules = Array.isArray(data.security_risk_rules) ? data.security_risk_rules : []
    const thresholds = data.security_alert_thresholds
    if (thresholds != null && (typeof thresholds !== 'object' || Array.isArray(thresholds))) {
      throw new Error('security_alert_thresholds 必须是对象')
    }
    riskRulesDraft.value = rules.map((x) => cloneRiskRule(x))
    riskThresholdsDraftText.value = JSON.stringify(thresholds || {}, null, 2)
    riskRuleImportError.value = ''
    showRiskRuleImportModal.value = false
    validateRiskRulesDraft()
    pushViewOpNotice('已导入风险规则草稿，请检查后保存发布')
  } catch (err) {
    riskRuleImportError.value = err instanceof Error ? err.message : 'JSON 解析失败'
  }
}

async function runRiskEventAction(action, item) {
  const eventId = String(item?.event_id || item?.id || '').trim()
  if (!eventId) return
  if (isRiskActionDisabled(action, item)) return
  await executeRiskEventAction(String(action || ''), eventId, '')
}

function closeRiskActionModal() {
  if (riskActionLoadingId.value) return
  showRiskActionModal.value = false
  riskActionDraft.action = ''
  riskActionDraft.eventId = ''
  riskActionDraft.note = ''
  riskActionDraft.item = null
}

function riskActionDisabledReason(action, item) {
  const status = String(item?.event_status || '').toUpperCase()
  if (!status) return ''
  if (status === 'RESOLVED') return '已关闭事件不可再次操作'
  if (action === 'ack' && status === 'ACKED') return '事件已确认'
  return ''
}

function isRiskActionDisabled(action, item) {
  const eventId = String(item?.event_id || item?.id || '').trim()
  if (!eventId) return true
  if (riskActionLoadingId.value === `${action}:${eventId}`) return true
  return !!riskActionDisabledReason(action, item)
}

async function confirmRiskAction() {
  const action = String(riskActionDraft.action || '')
  const eventId = String(riskActionDraft.eventId || '')
  if (!action || !eventId) return
  await executeRiskEventAction(action, eventId, String(riskActionDraft.note || ''))
  closeRiskActionModal()
}

async function executeRiskEventAction(action, eventId, note = '') {
  const fn = action === 'resolve' ? props.resolveRiskEvent : props.ackRiskEvent
  if (!fn) return
  riskActionLoadingId.value = `${action}:${eventId}`
  try {
    const res = await fn(eventId, note)
    if (res?.no_change) {
      pushViewOpNotice(`事件 ${eventId} 无变化（已是目标状态）`)
    } else if (action === 'resolve') {
      pushViewOpNotice(`事件 ${eventId} 已关闭`)
    } else {
      pushViewOpNotice(`事件 ${eventId} 已确认`)
    }
    await Promise.all([
      reloadRiskEvents(riskEventFilters.page),
      reloadLogs(1, { silent: true }),
      props.loadSystemLogStats3d
        ? props.loadSystemLogStats3d({
            days: 3,
            riskLevels: [...activeRiskLevels.value],
            category: logFilters.category,
            source: logFilters.source,
            q: logFilters.keyword,
          })
        : Promise.resolve(),
    ])
  } catch (err) {
    const msg = err instanceof Error ? err.message : '操作失败'
    pushViewOpNotice(`事件 ${eventId} 操作失败：${msg}`)
  } finally {
    riskActionLoadingId.value = ''
  }
}

function closeLogCenter() {
  stopLogStreaming()
  showLogModal.value = false
  onlyNewLogs.value = false
  selectedLogItem.value = null
  sessionNewLogIds.value = new Set()
}

async function reloadPermissionWarnings(page = 1) {
  if (!props.loadPermissionWarnings) return
  permissionLoading.value = true
  try {
    permissionFilters.page = Math.max(1, Number(page || 1))
    await props.loadPermissionWarnings({
      page: permissionFilters.page,
      pageSize: permissionFilters.pageSize,
      days: permissionFilters.days,
      riskLevel: permissionFilters.riskLevel,
      source: permissionFilters.source,
      role: permissionFilters.role,
      q: permissionFilters.q,
    })
  } finally {
    permissionLoading.value = false
  }
}

async function openPermissionWarnings() {
  showPermissionModal.value = true
  permissionDetailTab.value = 'warnings'
  selectedPermissionWarn.value = null
  await Promise.all([reloadPermissionWarnings(1), reloadRiskEvents(1)])
}

function closePermissionWarnings() {
  showPermissionModal.value = false
  permissionDetailTab.value = 'warnings'
  selectedPermissionWarn.value = null
}

async function switchPermissionDetailTab(tab) {
  const next = String(tab || '')
  if (next !== 'warnings' && next !== 'risk_events' && next !== 'risk_rules') return
  permissionDetailTab.value = next
  selectedPermissionWarn.value = null
  selectedRiskEvent.value = null
  if (next === 'warnings') {
    await reloadPermissionWarnings(1)
    return
  }
  if (next === 'risk_events') {
    await reloadRiskEvents(1)
    return
  }
  await reloadRiskRules()
}

function permissionRiskClass(level) {
  const v = String(level || '').toLowerCase()
  if (v === 'critical') return 'level-crit'
  if (v === 'high') return 'level-err'
  if (v === 'medium') return 'level-warn'
  return 'level-info'
}

function toggleChartMode() {
  if (chartSwitchTimer) clearTimeout(chartSwitchTimer)
  chartAnimating.value = true
  chartMode.value = chartMode.value === 'line' ? 'bar' : 'line'
  chartSwitchTimer = setTimeout(() => {
    chartAnimating.value = false
  }, 320)
}

function switchRingMetric() {
  if (ringSwitchTimer) clearTimeout(ringSwitchTimer)
  ringAnimating.value = true
  const idx = ringMetrics.findIndex((x) => x.key === ringMetricKey.value)
  const next = (idx + 1) % ringMetrics.length
  ringMetricKey.value = ringMetrics[next].key
  ringSwitchTimer = setTimeout(() => {
    ringAnimating.value = false
  }, 360)
}

function setHoveredPoint(p) {
  hoveredPoint.value = {
    day: p.day,
    valueText: p.valueText,
    left: (p.x / chartRect.w) * 100,
    top: (p.y / chartRect.h) * 100,
  }
}

function clearHoveredPoint() {
  hoveredPoint.value = null
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function escapeRegExp(value) {
  return String(value ?? '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function renderHighlighted(value, field) {
  const text = String(value ?? '')
  const html = escapeHtml(text)
  const activeField = String(logFilters.field || '')
  const keyword = String(logFilters.keyword || '').trim()
  if (!keyword || activeField !== field) return html
  try {
    const pattern = new RegExp(escapeRegExp(keyword), 'ig')
    return html.replace(pattern, (m) => `<mark>${m}</mark>`)
  } catch {
    return html
  }
}

function logRowFingerprint(item) {
  if (!item || typeof item !== 'object') return ''
  return [
    String(item.ts || ''),
    String(item.level || ''),
    String(item.category || ''),
    String(item.message || ''),
    String(item.source || ''),
    String(item.role || ''),
    String(item.status || ''),
    String(item.path || ''),
    String(item.trace_id || ''),
    String(item.event_id || ''),
  ].join('|')
}

function buildLogRowKey(item, idx) {
  return `${logRowFingerprint(item)}#${idx}`
}

function isNearBottom(el, threshold = 12) {
  if (!el) return true
  return el.scrollTop + el.clientHeight >= el.scrollHeight - threshold
}

function onLogScroll() {
  const el = logScrollRef.value
  if (!el) return
  logPinnedBottom.value = isNearBottom(el)
}

function toggleLogViewMode() {
  logViewMode.value = logViewMode.value === 'live' ? 'history' : 'live'
}

async function toggleRiskLevel(levelKey) {
  const key = String(levelKey || '').toLowerCase()
  if (!['warn', 'error', 'critical'].includes(key)) return
  const count = riskStats3d.value[key] || 0
  if (count <= 0) return
  logDebug('toggleRiskLevel clicked', {
    level: key,
    count3d: count,
    activeBefore: [...activeRiskLevels.value],
    filtersAfterPreset: { ...logFilters },
  })
  if (activeRiskLevels.value.includes(key)) {
    activeRiskLevels.value = activeRiskLevels.value.filter((x) => x !== key)
    if (showLogModal.value) await reloadLogs(1)
    logDebug('toggleRiskLevel removed', {
      activeAfter: [...activeRiskLevels.value],
      visibleCount: visibleLogs.value.length,
    })
    return
  }
  // 风险视图优先：开启风险筛选时清空其他筛选条件，避免“卡片有数、日志无结果”的交互冲突
  Object.assign(logFilters, LOG_FILTER_DEFAULTS)
  activeQuickPreset.value = ''
  onlyNewLogs.value = false
  activeRiskLevels.value = [...activeRiskLevels.value, key]
  if (showLogModal.value) await reloadLogs(1)
  logDebug('toggleRiskLevel added', {
    activeAfter: [...activeRiskLevels.value],
    visibleCount: visibleLogs.value.length,
  })
}

function isRiskLevelActive(levelKey) {
  return activeRiskLevels.value.includes(String(levelKey || '').toLowerCase())
}

function applyQuickPreset(preset) {
  activeQuickPreset.value = activeQuickPreset.value === preset ? '' : preset
}

function selectLogItem(item) {
  selectedLogItem.value = item
}

function buildRiskLocateTarget(item) {
  const traceId = String(item?.trace_id || '').trim()
  if (traceId) return { field: 'trace_id', keyword: traceId, hint: `trace_id=${traceId}` }
  const eventId = String(item?.event_id || item?.id || '').trim()
  if (eventId) return { field: 'event_id', keyword: eventId, hint: `event_id=${eventId}` }
  const reason = String(item?.reason_code || '').trim()
  if (reason) return { field: 'message', keyword: reason, hint: `reason=${reason}` }
  const path = String(item?.path || '').trim()
  if (path) return { field: 'path', keyword: path, hint: `path=${path}` }
  return { field: 'message', keyword: '', hint: '' }
}

async function onSelectRiskEvent(item) {
  selectedRiskEvent.value = item
  await locateRiskEventLogs(item, { openCenter: false })
}

async function locateRiskEventLogs(item, { openCenter = false } = {}) {
  const eventId = String(item?.event_id || item?.id || '').trim()
  const traceId = String(item?.trace_id || item?.request_id || '').trim()
  const reason = String(item?.reason_code || '').trim()
  const path = String(item?.path || '').trim()
  const eventTs = String(item?.ts || '').trim()
  const sourceRaw = String(item?.source || '').toLowerCase()
  const sourceHint = sourceRaw.includes('chat') ? 'chat' : sourceRaw.includes('execution') ? 'execution' : 'all'
  const probes = traceId
    ? [{ field: 'trace_id', keyword: traceId, hint: `trace_id=${traceId}` }]
    : [
        eventId ? { field: 'id', keyword: eventId, hint: `id=${eventId}` } : null,
        eventId ? { field: 'event_id', keyword: eventId, hint: `event_id=${eventId}` } : null,
        reason ? { field: 'message', keyword: reason, hint: `reason=${reason}` } : null,
        path ? { field: 'path', keyword: path, hint: `path=${path}` } : null,
      ].filter(Boolean)
  if (!probes.length) {
    if (openCenter) {
      await openLogCenter()
    }
    return
  }
  Object.assign(logFilters, LOG_FILTER_DEFAULTS)
  const tsObj = eventTs ? new Date(eventTs) : null
  if (tsObj && !Number.isNaN(tsObj.getTime())) {
    const now = Date.now()
    const diff = Math.max(0, now - tsObj.getTime())
    const diffDays = Math.ceil(diff / 86400000)
    logFilters.days = Math.max(7, Math.min(365, diffDays + 2))
  } else {
    logFilters.days = Math.max(30, Number(LOG_FILTER_DEFAULTS.days || 7))
  }
  logFilters.source = sourceHint
  activeQuickPreset.value = ''
  onlyNewLogs.value = false
  if (openCenter) {
    logViewMode.value = 'history'
    await openLogCenter()
  }
  let matched = null
  for (const probe of probes) {
    logFilters.field = probe.field
    logFilters.keyword = probe.keyword
    await reloadLogs(1)
    if (visibleLogs.value.length > 0) {
      matched = probe
      break
    }
  }
  if (!matched) {
    // 全量来源再尝试一次，避免 source 过滤误伤
    logFilters.source = 'all'
    for (const probe of probes) {
      logFilters.field = probe.field
      logFilters.keyword = probe.keyword
      await reloadLogs(1)
      if (visibleLogs.value.length > 0) {
        matched = probe
        break
      }
    }
  }
  await nextTick()
  if (visibleLogs.value.length > 0) {
    selectedLogItem.value = visibleLogs.value[0]
    locatingLogFingerprint.value = logRowFingerprint(visibleLogs.value[0])
    if (locatingLogTimer) clearTimeout(locatingLogTimer)
    locatingLogTimer = setTimeout(() => {
      locatingLogFingerprint.value = ''
      locatingLogTimer = null
    }, 1800)
  } else {
    locatingLogFingerprint.value = ''
  }
  const el = logScrollRef.value
  if (el) el.scrollTop = 0
  if (matched) {
    pushViewOpNotice(openCenter ? `已打开日志中心并定位：${matched.hint}` : `已定位日志：${matched.hint}`)
  } else {
    pushViewOpNotice('未命中日志，已按 trace_id/event_id/reason/path 尝试检索')
  }
}

function clearSelectedLogItem() {
  selectedLogItem.value = null
}

function closeSaveLogViewModal() {
  showSaveViewModal.value = false
  saveViewDraftName.value = ''
}

function pushViewOpNotice(text) {
  viewOpNotice.value = String(text || '')
  if (viewOpNoticeTimer) clearTimeout(viewOpNoticeTimer)
  viewOpNoticeTimer = setTimeout(() => {
    viewOpNotice.value = ''
    viewOpNoticeTimer = null
  }, 1800)
}

function pushCopyNotice(text) {
  copyNotice.value = String(text || '')
  if (copyNoticeTimer) clearTimeout(copyNoticeTimer)
  copyNoticeTimer = setTimeout(() => {
    copyNotice.value = ''
    copyNoticeTimer = null
  }, 1400)
}

async function copyText(value, label = '内容') {
  const text = String(value || '').trim()
  if (!text || text === '-') return
  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      pushCopyNotice(`${label} 已复制`)
      return
    }
  } catch {
    // fallback below
  }
  try {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', 'readonly')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    document.execCommand('copy')
    document.body.removeChild(area)
    pushCopyNotice(`${label} 已复制`)
  } catch {
    pushCopyNotice(`${label} 复制失败`)
  }
}

function openSaveLogViewModal() {
  showSaveViewModal.value = true
  saveViewDraftName.value = ''
  nextTick(() => {
    saveViewInputRef.value?.focus?.()
  })
}

async function confirmSaveCurrentLogView() {
  const name = String(saveViewDraftName.value || '').trim()
  if (!name) return
  const item = {
    id: `view_${Date.now()}`,
    name,
    created_at: new Date().toISOString(),
    filters: sanitizeLogViewFilters(logFilters),
    quickPreset: activeQuickPreset.value,
    riskLevels: [...activeRiskLevels.value],
  }
  savedLogViews.value = [item, ...savedLogViews.value].slice(0, 20)
  selectedSavedViewId.value = item.id
  await persistSavedLogViews()
  closeSaveLogViewModal()
  pushViewOpNotice('视图已保存')
}

function normalizeViewIdInput(viewId) {
  if (viewId && typeof viewId === 'object' && 'type' in viewId) return selectedSavedViewId.value
  return viewId || selectedSavedViewId.value
}

async function applySavedLogView(viewId = selectedSavedViewId.value) {
  const targetId = normalizeViewIdInput(viewId)
  if (!targetId) return
  if (targetId === DEFAULT_VIEW_ID) {
    await resetToDefaultLogView()
    pushViewOpNotice('已切换到默认视图')
    return
  }
  selectedSavedViewId.value = String(targetId)
  const target = savedLogViews.value.find((x) => x.id === selectedSavedViewId.value)
  if (!target) return
  Object.assign(logFilters, LOG_FILTER_DEFAULTS)
  Object.assign(logFilters, sanitizeLogViewFilters(target.filters))
  activeQuickPreset.value = String(target.quickPreset || '')
  activeRiskLevels.value = Array.isArray(target.riskLevels) ? [...target.riskLevels] : []
  await reloadLogs(1)
  pushViewOpNotice(`已应用视图：${target.name}`)
}

async function deleteSavedLogView(viewId = selectedSavedViewId.value) {
  const targetId = normalizeViewIdInput(viewId)
  if (!targetId || targetId === DEFAULT_VIEW_ID) return
  const removed = savedLogViews.value.find((x) => x.id === targetId)
  if (!removed) return
  savedLogViews.value = savedLogViews.value.filter((x) => x.id !== targetId)
  if (selectedSavedViewId.value === targetId) selectedSavedViewId.value = ''
  await persistSavedLogViews()
  pushViewOpNotice(`已删除视图：${removed.name}`)
}

async function resetToDefaultLogView() {
  selectedSavedViewId.value = DEFAULT_VIEW_ID
  Object.assign(logFilters, LOG_FILTER_DEFAULTS)
  activeQuickPreset.value = ''
  activeRiskLevels.value = []
  onlyNewLogs.value = false
  if (logViewMode.value === 'live') {
    logViewMode.value = 'history'
  }
  await reloadLogs(1)
}

function formatTsZh(ts) {
  const d = new Date(ts || '')
  if (Number.isNaN(d.getTime())) return ts || '-'
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${y}年${m}月${day}日 ${hh}:${mm}:${ss}`
}

function levelClass(level) {
  const x = String(level || '').toLowerCase()
  if (x === 'critical') return 'level-crit'
  if (x === 'error') return 'level-err'
  if (x === 'warn' || x === 'warning') return 'level-warn'
  return 'level-info'
}

function statusClass(status) {
  const x = String(status || '').toUpperCase()
  if (['FAIL', 'ERROR', 'DENY', 'FORBIDDEN', 'UNAUTHORIZED'].includes(x)) return 'status-bad'
  if (['WARN', 'DEGRADED'].includes(x)) return 'status-warn'
  if (['SUCCESS', 'OK', 'ALLOW'].includes(x)) return 'status-ok'
  return 'status-neutral'
}

async function refreshRuntimeStats() {
  if (props.loadRuntimeSeries) await props.loadRuntimeSeries(Number(chartDays.value || 30))
}

async function refreshOverview() {
  if (!props.loadOverview || refreshInFlight.value) return
  refreshInFlight.value = true
  try {
    await props.loadOverview()
    lastRefreshAt.value = new Date()
  } finally {
    refreshInFlight.value = false
  }
}

const lastRefreshText = computed(() => {
  if (!lastRefreshAt.value) return '上次刷新：-'
  return `上次刷新：${lastRefreshAt.value.toLocaleTimeString()}`
})

onMounted(async () => {
  loadSavedLogViews()
  syncRiskRulesDraftFromState()
  lastRefreshAt.value = new Date()
  if (!props.overviewState.executionHealth && !props.overviewState.chatHealth) {
    await refreshOverview()
  }
  if (props.loadRuntimeSeries) await props.loadRuntimeSeries(30)
  autoRefreshTimer = setInterval(async () => {
    if (showLogModal.value) return
    await refreshOverview()
  }, 20000)
})

onBeforeUnmount(() => {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer)
    autoRefreshTimer = null
  }
  stopLogStreaming()
  freshTimerMap.forEach((timer) => clearTimeout(timer))
  freshTimerMap.clear()
  if (chartSwitchTimer) clearTimeout(chartSwitchTimer)
  if (ringSwitchTimer) clearTimeout(ringSwitchTimer)
  if (viewOpNoticeTimer) clearTimeout(viewOpNoticeTimer)
  if (copyNoticeTimer) clearTimeout(copyNoticeTimer)
  if (locatingLogTimer) clearTimeout(locatingLogTimer)
  if (logsLoadingWatchdog) clearTimeout(logsLoadingWatchdog)
  if (riskEventsLoadingWatchdog) clearTimeout(riskEventsLoadingWatchdog)
})

watch(
  () => [showLogModal.value, logLiveEnabled.value, logLiveIntervalSec.value, logViewMode.value],
  () => {
    if (showLogModal.value && logLiveEnabled.value && logViewMode.value === 'live') startLogStreaming()
    else stopLogStreaming()
  },
)

watch(
  () => logViewMode.value,
  async (mode) => {
    if (mode === 'live') {
      logLiveEnabled.value = true
      if (showLogModal.value) startLogStreaming()
    } else {
      logLiveEnabled.value = false
      onlyNewLogs.value = false
      sessionNewLogIds.value = new Set()
      stopLogStreaming()
      if (showLogModal.value) await reloadLogs(1)
    }
  },
)

watch(
  () => visibleLogs.value.length,
  async (nextLen, prevLen) => {
    if (!showLogModal.value) return
    await nextTick()
    const el = logScrollRef.value
    if (!el) return
    if (prevLen === 0 || logPinnedBottom.value) {
      el.scrollTop = el.scrollHeight
      logPinnedBottom.value = true
    }
    if (nextLen < prevLen && el.scrollTop > el.scrollHeight) {
      el.scrollTop = el.scrollHeight
    }
  },
)

watch(
  () => [activeRiskLevels.value.join(','), filteredLogs.value.length, riskFilteredLogs.value.length, visibleLogs.value.length],
  ([active, filtered, riskFiltered, visible]) => {
    logDebug('risk pipeline', {
      active,
      filtered,
      riskFiltered,
      visible,
      riskStats3d: riskStats3d.value,
    })
  },
)

watch(
  () => getRuntimeConfigBag()?.[RUNTIME_LOG_VIEWS_KEY],
  () => {
    loadSavedLogViews()
  },
  { deep: false },
)

watch(
  () => [props.overviewState.riskRules, props.overviewState.riskThresholds],
  () => {
    if (!riskRulesSaving.value && !riskRulesLoading.value) {
      syncRiskRulesDraftFromState()
    }
  },
  { deep: false },
)
watch(
  () => [apiKeyLifecycleSummary.value.total, apiKeyLifecycleSummary.value.expiringSoon, apiKeyLifecycleSummary.value.expired, apiKeyLifecycleSummary.value.openAlerts],
  () => {
    const cur = {
      total: Number(apiKeyLifecycleSummary.value.total || 0),
      expiringSoon: Number(apiKeyLifecycleSummary.value.expiringSoon || 0),
      expired: Number(apiKeyLifecycleSummary.value.expired || 0),
      openAlerts: Number(apiKeyLifecycleSummary.value.openAlerts || 0),
    }
    const prev = prevApiKeyLifecycleSummary.value
    if (prev) {
      apiKeyRiskTrend.value = {
        openAlertsDelta: cur.openAlerts - prev.openAlerts,
        expiringSoonDelta: cur.expiringSoon - prev.expiringSoon,
        expiredDelta: cur.expired - prev.expired,
      }
    } else {
      apiKeyRiskTrend.value = {
        openAlertsDelta: 0,
        expiringSoonDelta: 0,
        expiredDelta: 0,
      }
    }
    prevApiKeyLifecycleSummary.value = cur
  },
  { immediate: true },
)
watch(
  () => [riskRuleFilters.q, riskRuleFilters.riskLevel, riskRuleFilters.decision, riskRuleFilters.enabled, riskRuleFilters.pageSize],
  () => {
    riskRuleFilters.page = 1
  },
  { deep: false },
)

watch(
  () => props.overviewState.systemLogs,
  (rows) => {
    const items = Array.isArray(rows) ? rows : []
    const currentIds = items.map((x) => String(x.id))
    const nextSeen = new Set(seenLogIds.value)
    const nextFresh = new Set(newLogIds.value)
    const nextSessionNew = new Set(sessionNewLogIds.value)
    const shouldTrackFresh = showLogModal.value && logViewMode.value === 'live'

    for (const id of currentIds) {
      if (shouldTrackFresh && !nextSeen.has(id)) {
        nextFresh.add(id)
        nextSessionNew.add(id)
        if (freshTimerMap.has(id)) clearTimeout(freshTimerMap.get(id))
        freshTimerMap.set(
          id,
          setTimeout(() => {
            const s = new Set(newLogIds.value)
            s.delete(id)
            newLogIds.value = s
            freshTimerMap.delete(id)
          }, 12000),
        )
      }
      nextSeen.add(id)
    }
    seenLogIds.value = nextSeen
    newLogIds.value = nextFresh
    sessionNewLogIds.value = nextSessionNew
  },
  { deep: false },
)
</script>

<template>
  <section class="content">
    <div class="panel">
      <section
        class="domain-section"
        :id="sectionDomId ? sectionDomId('system-messages') : undefined"
        data-menu-section="system-messages"
      >
        <div class="section-head-inline">
          <h2 class="domain-section-title">系统消息</h2>
          <div class="refresh-meta">{{ lastRefreshText }}</div>
          <button class="btn ghost" :disabled="overviewState.loading" @click="refreshOverview">刷新</button>
        </div>
        <div class="msg-grid">
          <button class="msg-card" @click="openLogCenter">
            <div class="msg-head">事务日志</div>
            <div class="msg-num">{{ formattedSystemLogCount3d }}</div>
            <div class="msg-sub">近 3 日风险日志</div>
            <div class="msg-sub">{{ logIndexHealthText }}</div>
          </button>

          <button
            class="msg-card alert-card"
            :class="{ critical: crossStoreAlert?.severity === 'critical', warning: crossStoreAlert?.severity === 'warning', muted: !crossStoreAlert }"
            :disabled="!crossStoreAlert"
            @click="openCrossStoreAlert"
          >
            <div class="msg-head">告警信息 <span v-if="crossStoreAlert" class="alert-source">跨存储</span></div>
            <div class="msg-num">{{ alertCount }}</div>
            <div class="msg-sub alert-summary" :title="alertSummary">{{ alertSummary }}</div>
          </button>

          <button class="msg-card muted" @click="openPermissionWarnings">
            <div class="msg-head">权限警告</div>
            <div class="msg-num">{{ permissionWarnCount }}</div>
            <div class="msg-sub">查看出现异常的敏感请求</div>
          </button>

          <button class="msg-card muted api-risk-card" :class="`risk-${apiKeyRiskLevel}`" @click="openApiKeyRisk">
            <div class="msg-head api-risk-head-row">
              <span>API Key 风险</span>
              <button
                class="btn ghost mini api-risk-eval-btn"
                :disabled="apiKeyRiskEvaluating"
                @click.stop="evaluateApiKeyRiskNow"
              >{{ apiKeyRiskEvaluating ? '评估中...' : '立即评估' }}</button>
            </div>
            <div class="msg-num">{{ apiKeyLifecycleSummary.openAlerts }}</div>
            <div class="msg-sub">
              即将过期 {{ apiKeyLifecycleSummary.expiringSoon }}
              <span class="risk-trend" :class="trendClass(apiKeyRiskTrend.expiringSoonDelta)">{{ trendMark(apiKeyRiskTrend.expiringSoonDelta) }}</span>
              · 已过期 {{ apiKeyLifecycleSummary.expired }}
              <span class="risk-trend" :class="trendClass(apiKeyRiskTrend.expiredDelta)">{{ trendMark(apiKeyRiskTrend.expiredDelta) }}</span>
            </div>
            <div class="msg-sub">
              开放告警趋势
              <span class="risk-trend" :class="trendClass(apiKeyRiskTrend.openAlertsDelta)">
                {{ trendMark(apiKeyRiskTrend.openAlertsDelta) }} {{ Math.abs(Number(apiKeyRiskTrend.openAlertsDelta || 0)) }}
              </span>
              · 总端点 {{ apiKeyLifecycleSummary.total }}
            </div>
            <div class="api-risk-metric-tabs" @click.stop>
              <button
                v-for="item in apiKeyTrendMetricOptions"
                :key="item.key"
                class="risk-metric-tab"
                :class="{ active: apiKeyTrendMetric === item.key }"
                @click="apiKeyTrendMetric = item.key"
              >{{ item.label }}</button>
            </div>
            <div class="msg-sub api-sparkline">
              {{ apiKeyTrendSpark.label }} · 7日 {{ apiKeyTrendSpark.spark7 }} ({{ apiKeyTrendSpark.sum7 }}) · 30日 {{ apiKeyTrendSpark.spark30 }} ({{ apiKeyTrendSpark.sum30 }})
            </div>
          </button>

          <div class="msg-card task-card">
            <div class="msg-head">进行中任务</div>
            <div class="msg-num">{{ overviewState.activeTaskCount }}</div>
            <div class="msg-sub">当前运行中的任务数量</div>
          </div>
        </div>
      </section>

      <section
        class="domain-section"
        :id="sectionDomId ? sectionDomId('runtime-stats') : undefined"
        data-menu-section="runtime-stats"
      >
        <div class="runtime-head">
          <h2 class="domain-section-title">运行统计</h2>
          <div class="runtime-actions">
            <select v-model.number="chartDays" @change="refreshRuntimeStats">
              <option :value="7">近7天</option>
              <option :value="30">近30天</option>
              <option :value="90">近90天</option>
            </select>
          </div>
        </div>

        <div class="metric-tabs">
          <button
            v-for="m in metrics"
            :key="m.key"
            class="metric-tab"
            :class="{ active: metricKey === m.key }"
            @click="metricKey = m.key"
          >
            {{ m.label }}
          </button>
        </div>

        <div class="runtime-main">
          <div class="mega-chart" :class="{ animating: chartAnimating }" @click="toggleChartMode">
            <div class="chart-meta">
              <strong>{{ selectedMetric.label }}</strong>
              <span>当前值：{{ chartModel.lastText }}</span>
              <span class="mode-tip">点击切换：{{ chartMode === 'line' ? '曲线图' : '柱状图' }}</span>
            </div>

            <div v-if="hoveredPoint" class="chart-tip" :style="{ left: `${hoveredPoint.left}%`, top: `${hoveredPoint.top}%` }">
              {{ hoveredPoint.day }} | {{ hoveredPoint.valueText }}
            </div>

            <svg viewBox="0 0 1000 320" preserveAspectRatio="none" class="line-svg">
              <line
                v-for="(t, idx) in chartModel.yTicks"
                :key="`y-${idx}`"
                :x1="chartRect.left"
                :x2="chartRect.right"
                :y1="t.y"
                :y2="t.y"
                class="grid-line"
              />
              <line
                v-for="(t, idx) in chartModel.xTicks"
                :key="`x-${idx}`"
                :x1="t.x"
                :x2="t.x"
                :y1="chartRect.top"
                :y2="chartRect.bottom"
                class="grid-line v"
              />
              <line :x1="chartRect.left" :x2="chartRect.right" :y1="chartRect.bottom" :y2="chartRect.bottom" class="axis" />
              <line
                v-if="chartModel.hasData"
                :x1="chartRect.left"
                :x2="chartRect.left"
                :y1="chartRect.top"
                :y2="chartRect.bottom"
                class="axis"
              />

              <g v-if="chartModel.hasData" class="chart-layer line-layer" :class="{ active: chartMode === 'line' }">
                <line
                  v-for="(seg, idx) in chartModel.lineSegments"
                  :key="`seg-${idx}`"
                  :x1="seg.x1"
                  :y1="seg.y1"
                  :x2="seg.x2"
                  :y2="seg.y2"
                  class="line-path"
                  :class="{ dashed: seg.dashed }"
                />
                <circle
                  v-for="p in chartModel.points"
                  :key="`${p.day}-${p.value}`"
                  :cx="p.x"
                  :cy="p.y"
                  r="3.2"
                  class="line-dot"
                  @mouseenter="setHoveredPoint(p)"
                  @mouseleave="clearHoveredPoint"
                />
              </g>

              <g v-if="chartModel.hasData" class="chart-layer bar-layer" :class="{ active: chartMode === 'bar' }">
                <path
                  v-for="p in chartModel.bars"
                  :key="`${p.day}-${p.value}`"
                  :d="p.barPath"
                  class="bar"
                  @mouseenter="setHoveredPoint(p)"
                  @mouseleave="clearHoveredPoint"
                />
              </g>

              <text v-if="!chartModel.hasData" x="500" y="155" text-anchor="middle" class="axis-label">暂无数据</text>
              <text
                v-for="(t, idx) in chartModel.yTicks"
                :key="`yl-${idx}`"
                :x="8"
                :y="t.y + 4"
                class="axis-label"
              >{{ t.label }}</text>
              <text
                v-for="(t, idx) in chartModel.xTicks"
                :key="`xl-${idx}`"
                :x="t.x"
                :y="304"
                text-anchor="middle"
                class="axis-label"
              >{{ t.label }}</text>
            </svg>
          </div>

          <button class="ring-panel" :class="{ animating: ringAnimating }" @click="switchRingMetric">
            <div class="ring-head">{{ ringMetricModel.label }}</div>
            <svg viewBox="0 0 120 120" class="ring-svg">
              <defs>
                <linearGradient id="ringGrad" x1="0%" x2="100%" y1="0%" y2="100%">
                  <stop offset="0%" stop-color="#5a95f2" />
                  <stop offset="100%" stop-color="#39c2a3" />
                </linearGradient>
              </defs>
              <circle cx="60" cy="60" r="50" class="ring-bg" />
              <circle
                cx="60"
                cy="60"
                r="50"
                class="ring-fg"
                :class="{ muted: !ringMetricModel.hasThreshold }"
                :style="{ strokeDashoffset: ringMetricModel.dash }"
              />
              <text x="60" y="58" text-anchor="middle" class="ring-percent">
                {{ ringMetricModel.hasThreshold ? `${ringMetricModel.percent}%` : '--' }}
              </text>
              <text x="60" y="74" text-anchor="middle" class="ring-value">{{ ringMetricModel.currentText }}</text>
            </svg>
            <div class="ring-sub">阈值：{{ ringMetricModel.thresholdText }}</div>
            <div class="ring-tip">点击切换指标</div>
          </button>
        </div>
      </section>

      <p class="banner err" v-if="overviewState.error">{{ overviewState.error }}</p>
    </div>

    <transition name="modal">
      <div v-if="showLogModal" v-modal-focus="{ close: closeLogCenter, label: '事务日志中心' }" class="modal-mask log-modal-mask">
        <div class="modal-card">
          <div class="modal-head">
            <div class="modal-title">事务日志中心</div>
            <div class="modal-head-actions">
              <button type="button" class="icon-close" aria-label="关闭事务日志中心" @click="closeLogCenter"><span aria-hidden="true">×</span></button>
            </div>
          </div>

          <div class="modal-body">
            <div class="filter-panel">
              <div class="filter-row query-row">
                <label class="compact-select">字段
                  <UiSelect v-model="logFilters.field" :options="fieldOptions" />
                </label>
                <label class="query-keyword">关键字<input v-model="logFilters.keyword" placeholder="请输入关键字" /></label>
                <label class="compact-select">级别
                  <UiSelect v-model="logFilters.level" :options="levelOptions" />
                </label>
              <label class="compact-select">分类
                <UiSelect v-model="logFilters.category" :options="categoryOptions" />
              </label>
                <label class="compact-select">来源
                  <UiSelect v-model="logFilters.source" :options="sourceOptions" />
                </label>
                <label class="compact-select">日志内容
                  <UiSelect v-model="logFilters.includeNoise" :options="logContentOptions" />
                </label>
              </div>

              <div class="filter-row time-row">
                <label class="time-date time-cell">起始时间
                  <input
                    v-model="logFilters.from"
                    type="date"
                    :disabled="logViewMode === 'live'"
                  />
                </label>
                <label class="time-date time-cell">结束时间
                  <input
                    v-model="logFilters.to"
                    type="date"
                    :disabled="logViewMode === 'live'"
                  />
                </label>
                <div class="period-field time-cell">
                  <div class="period-title">
                    时段设置
                    <span
                      v-if="isPeriodCrossDay"
                      class="period-next-day"
                      title="结束时段早于起始时段，按跨天时段筛选（次日 +1）"
                    >+1</span>
                  </div>
                  <div class="period-range">
                    <input
                      v-model="logFilters.periodStart"
                      type="time"
                      :disabled="logViewMode === 'live'"
                    />
                    <span class="period-sep">~</span>
                    <input
                      v-model="logFilters.periodEnd"
                      type="time"
                      :disabled="logViewMode === 'live'"
                    />
                  </div>
                </div>
                <label class="window-field time-cell">查询窗口
                  <UiSelect v-model="logFilters.days" :options="dayWindowOptions" :disabled="logViewMode === 'live'" />
                </label>
                <div class="live-inline time-cell no-label">
                  <div
                    class="mode-switch"
                    :class="{ live: logViewMode === 'live' }"
                    role="tablist"
                    aria-label="日志模式切换"
                    @click="toggleLogViewMode"
                  >
                    <span class="mode-thumb" aria-hidden="true"></span>
                    <button
                      type="button"
                      class="mode-btn"
                      :class="{ active: logViewMode === 'history' }"
                    >
                      历史查询
                    </button>
                    <button
                      type="button"
                      class="mode-btn"
                      :class="{ active: logViewMode === 'live' }"
                    >
                      实时监听
                    </button>
                  </div>
                  <UiSelect
                    class="live-interval-select"
                    v-model="logLiveIntervalSec"
                    :options="liveIntervalOptions"
                    :disabled="logViewMode !== 'live'"
                    aria-label="刷新间隔"
                  />
                  <label class="inline-switch new-only">
                    <input v-model="onlyNewLogs" type="checkbox" :disabled="logViewMode !== 'live'" />
                    <span>仅看新增</span>
                  </label>
                  <div class="inline-actions">
                    <button class="btn ghost" :disabled="logViewMode === 'live'" @click="resetToDefaultLogView">重置视图</button>
                    <button class="btn ghost" :disabled="logViewMode === 'live'" @click="reloadLogs(1)">查询</button>
                  </div>
                </div>
              </div>
            </div>

            <div class="log-kpis">
              <button
                class="kpi-card kpi-toggle"
                :class="{ active: isRiskLevelActive('warn'), disabled: riskStats3d.warn <= 0 }"
                :disabled="riskStats3d.warn <= 0"
                @click="toggleRiskLevel('warn')"
              >
                <div class="kpi-name">3日警告</div>
                <div class="kpi-value">{{ riskStats3d.warn }}</div>
              </button>
              <button
                class="kpi-card kpi-toggle"
                :class="{ active: isRiskLevelActive('error'), disabled: riskStats3d.error <= 0 }"
                :disabled="riskStats3d.error <= 0"
                @click="toggleRiskLevel('error')"
              >
                <div class="kpi-name">3日错误</div>
                <div class="kpi-value">{{ riskStats3d.error }}</div>
              </button>
              <button
                class="kpi-card kpi-toggle"
                :class="{ active: isRiskLevelActive('critical'), disabled: riskStats3d.critical <= 0 }"
                :disabled="riskStats3d.critical <= 0"
                @click="toggleRiskLevel('critical')"
              >
                <div class="kpi-name">3日严重</div>
                <div class="kpi-value">{{ riskStats3d.critical }}</div>
              </button>
              <button
                class="kpi-card kpi-toggle strategy-card"
                :disabled="!props.saveRuntimeConfig || policySaving"
                @click="openPolicyModal"
              >
                <div class="kpi-name">归档策略 <span class="kpi-inline-tip">点击配置策略</span></div>
                <div class="kpi-mini">{{ archivePolicySummary }}</div>
              </button>
            </div>

            <div ref="logScrollRef" class="table-wrap log-scroll-wrap" @scroll="onLogScroll">
              <div v-if="logsLoading" class="table-loading">
                <span class="spinner"></span>
                <span>加载中...</span>
              </div>
              <table class="log-table">
                <colgroup>
                  <col class="col-time" />
                  <col class="col-level" />
                  <col class="col-category" />
                  <col class="col-message" />
                  <col class="col-source" />
                  <col class="col-role" />
                  <col class="col-status" />
                  <col class="col-trace" />
                  <col class="col-event" />
                  <col class="col-path" />
                </colgroup>
                <thead><tr><th>time</th><th>level</th><th>category</th><th>message</th><th>source</th><th>role</th><th>status</th><th>trace_id</th><th>event_id</th><th>path</th></tr></thead>
                <tbody>
                  <tr
                    v-for="(item, idx) in visibleLogs"
                    :key="buildLogRowKey(item, idx)"
                    v-interactive-row="{ activate: () => selectLogItem(item), label: `查看日志 ${item.level || ''} ${item.message || ''}`, current: selectedLogItem === item }"
                    :class="{
                      'row-fresh': newLogIds.has(String(item.id)),
                      selected: selectedLogItem === item,
                      'row-locating': locatingLogFingerprint && logRowFingerprint(item) === locatingLogFingerprint,
                    }"
                    @click="selectLogItem(item)"
                  >
                    <td>{{ formatTsZh(item.ts) }}</td>
                    <td :class="levelClass(item.level)">{{ item.level || '-' }}</td>
                    <td>{{ item.category || '-' }}</td>
                    <td class="cell-message" :title="item.message" v-html="renderHighlighted(item.message, 'message')"></td>
                    <td v-html="renderHighlighted(item.source || '-', 'source')"></td>
                    <td v-html="renderHighlighted(item.role || '-', 'role')"></td>
                    <td :class="statusClass(item.status)">{{ item.status || '-' }}</td>
                    <td :title="item.trace_id || '-'" v-html="renderHighlighted(item.trace_id || '-', 'trace_id')"></td>
                    <td :title="item.event_id || '-'" v-html="renderHighlighted(item.event_id || '-', 'event_id')"></td>
                    <td :title="item.path" v-html="renderHighlighted(item.path || '-', 'path')"></td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div v-if="selectedLogItem" class="log-detail-drawer">
              <div class="drawer-head">
                <div class="drawer-title">日志详情</div>
                <button class="btn ghost mini" @click="clearSelectedLogItem">关闭详情</button>
              </div>
              <div class="drawer-grid">
                <div><b>time</b><span>{{ formatTsZh(selectedLogItem.ts) }}</span></div>
                <div><b>level</b><span>{{ selectedLogItem.level || '-' }}</span></div>
                <div><b>category</b><span>{{ selectedLogItem.category || '-' }}</span></div>
                <div><b>source</b><span>{{ selectedLogItem.source || '-' }}</span></div>
                <div><b>role</b><span>{{ selectedLogItem.role || '-' }}</span></div>
                <div><b>status</b><span>{{ selectedLogItem.status || '-' }}</span></div>
                <div>
                  <b>trace_id</b>
                  <span class="copy-row">
                    <span>{{ selectedLogItem.trace_id || '-' }}</span>
                    <button class="btn ghost mini copy-btn" @click="copyText(selectedLogItem.trace_id, 'trace_id')">复制</button>
                  </span>
                </div>
                <div>
                  <b>event_id</b>
                  <span class="copy-row">
                    <span>{{ selectedLogItem.event_id || '-' }}</span>
                    <button class="btn ghost mini copy-btn" @click="copyText(selectedLogItem.event_id, 'event_id')">复制</button>
                  </span>
                </div>
                <div class="wide"><b>message</b><span>{{ selectedLogItem.message || '-' }}</span></div>
                <div class="wide">
                  <b>path</b>
                  <span class="copy-row">
                    <span>{{ selectedLogItem.path || '-' }}</span>
                    <button class="btn ghost mini copy-btn" @click="copyText(selectedLogItem.path, 'path')">复制</button>
                  </span>
                </div>
              </div>
            </div>
            <div class="log-window-meta">
              <div>
                已展示 {{ visibleLogs.length }} 条（窗口上限 {{ LOG_WINDOW_LIMIT }} 条）
                <span v-if="activeRiskLevels.length && descWindowLogs.length > riskFilteredLogs.length">
                  ，已隐藏 {{ descWindowLogs.length - riskFilteredLogs.length }} 条记录
                </span>
                <span v-if="activeRiskLevels.length">
                  ，风险筛选：{{ activeRiskLevels.join(' + ') }}（按严重级降序）
                </span>
              </div>
              <div class="log-meta-right">
                <div class="quick-inline">
                  <span class="preset-title">快筛</span>
                  <button class="chip-btn" :class="{ active: activeQuickPreset === 'api_fail' }" @click="applyQuickPreset('api_fail')">仅失败状态</button>
                  <button class="chip-btn" :class="{ active: activeQuickPreset === 'auth_fail' }" @click="applyQuickPreset('auth_fail')">仅鉴权失败</button>
                  <button class="chip-btn" :class="{ active: activeQuickPreset === 'task_error' }" @click="applyQuickPreset('task_error')">仅任务异常</button>
                </div>
                <span class="live-meta">
                  {{ logLastUpdatedAt ? `最近更新 ${logLastUpdatedAt}` : '最近更新 --:--:--' }}
                </span>
              </div>
            </div>
          </div>

          <div class="modal-foot">
            <div class="modal-foot-left">
              <span class="preset-title">视图</span>
              <span class="view-storage-hint">已保存到系统配置（Admin 持久）</span>
              <UiSelect class="saved-view-select" v-model="selectedSavedViewId" :options="savedViewOptions" placement="top" aria-label="已保存视图" />
              <button class="btn ghost mini" :disabled="!selectedSavedViewId" @click="applySavedLogView()">应用</button>
              <button class="btn ghost mini" @click="openSaveLogViewModal">保存当前视图</button>
              <button class="btn ghost mini" :disabled="!selectedSavedViewId || selectedSavedViewId === DEFAULT_VIEW_ID" @click="deleteSavedLogView()">删除选中</button>
              <span v-if="viewOpNotice" class="view-op-notice">{{ viewOpNotice }}</span>
              <span v-if="copyNotice" class="view-op-notice">{{ copyNotice }}</span>
            </div>
            <button class="btn ghost" @click="closeLogCenter">关闭</button>
          </div>

          <div v-if="showSaveViewModal" v-modal-focus="{ close: closeSaveLogViewModal, label: '保存筛选视图' }" class="save-view-overlay" @pointerdown.self="closeSaveLogViewModal">
            <div class="save-view-dialog">
              <div class="save-view-title">保存筛选视图</div>
              <label class="save-view-field">
                <span>视图名称</span>
                <input
                  ref="saveViewInputRef"
                  v-model="saveViewDraftName"
                  type="text"
                  maxlength="40"
                  placeholder="请输入视图名称"
                  @keydown.enter.prevent="confirmSaveCurrentLogView"
                />
              </label>
              <div class="save-view-actions">
                <button class="btn ghost" @click="closeSaveLogViewModal">取消</button>
                <button class="btn primary" :disabled="!String(saveViewDraftName || '').trim()" @click="confirmSaveCurrentLogView()">保存</button>
              </div>
            </div>
          </div>

          <div v-if="showPolicyModal" v-modal-focus="{ close: closePolicyModal, label: '日志归档策略' }" class="save-view-overlay" @pointerdown.self="closePolicyModal">
            <div class="save-view-dialog policy-dialog">
              <div class="save-view-head">
                <div class="save-view-title">日志归档策略</div>
                <button type="button" class="icon-close mini" aria-label="关闭归档策略" :disabled="policySaving" @click="closePolicyModal"><span aria-hidden="true">×</span></button>
              </div>
              <div class="policy-grid">
                <label>热日志保留（天）<input v-model.number="policyDraft.hotRetentionDays" type="number" min="1" step="1" /></label>
                <label>归档窗口（天）<input v-model.number="policyDraft.archiveRetentionDays" type="number" min="1" step="1" /></label>
                <label>压缩归档
                  <UiSelect
                    v-model="policyDraft.archiveEnabled"
                    :options="[{ label: '开启', value: true }, { label: '关闭', value: false }]"
                  />
                </label>
                <label>归档周期
                  <UiSelect
                    v-model="policyDraft.archivePeriod"
                    :options="[{ label: '按月', value: 'month' }, { label: '按周', value: 'week' }, { label: '按日', value: 'day' }]"
                  />
                </label>
                <label>超期动作
                  <UiSelect
                    v-model="policyDraft.expiredAction"
                    :options="[{ label: '删除', value: 'delete' }, { label: '保留', value: 'keep' }]"
                  />
                </label>
                <label>路径变更策略
                  <UiSelect
                    v-model="policyDraft.pathChangeStrategy"
                    :options="[{ label: '迁移历史数据', value: 'migrate' }, { label: '保留原目录', value: 'keep' }, { label: '删除历史数据', value: 'delete' }]"
                  />
                </label>
              </div>
              <div class="save-view-actions">
                <button class="btn ghost" :disabled="policySaving" @click="closePolicyModal">取消</button>
                <button class="btn primary" :disabled="policySaving" @click="savePolicyConfig">保存策略</button>
              </div>
            </div>
          </div>

          <div v-if="showRiskActionModal" v-modal-focus="{ close: closeRiskActionModal, label: '风险事件操作' }" class="save-view-overlay" @pointerdown.self="closeRiskActionModal">
            <div class="save-view-dialog risk-action-dialog">
              <div class="save-view-head">
                <div class="save-view-title">
                  {{ riskActionDraft.action === 'resolve' ? '关闭风险事件' : '确认风险事件' }}
                </div>
                <button type="button" class="icon-close mini" aria-label="关闭风险事件操作" :disabled="!!riskActionLoadingId" @click="closeRiskActionModal"><span aria-hidden="true">×</span></button>
              </div>
              <div class="drawer-grid">
                <div><b>event_id</b><span>{{ riskActionDraft.eventId || '-' }}</span></div>
                <div><b>当前状态</b><span>{{ riskActionDraft.item?.event_status || '-' }}</span></div>
                <div class="wide"><b>reason</b><span>{{ riskActionDraft.item?.reason_code || '-' }}</span></div>
                <div class="wide"><b>path</b><span>{{ riskActionDraft.item?.path || '-' }}</span></div>
              </div>
              <label class="save-view-field">
                <span>备注（可选）</span>
                <textarea
                  v-model.trim="riskActionDraft.note"
                  rows="3"
                  maxlength="500"
                  placeholder="输入备注会写入事件状态记录"
                ></textarea>
              </label>
              <div class="save-view-actions">
                <button class="btn ghost" :disabled="!!riskActionLoadingId" @click="closeRiskActionModal">取消</button>
                <button class="btn primary" :disabled="!!riskActionLoadingId" @click="confirmRiskAction">
                  {{ riskActionDraft.action === 'resolve' ? '确认关闭' : '确认操作' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </transition>

    <transition name="modal">
      <div v-if="showPermissionModal" v-modal-focus="{ close: closePermissionWarnings, label: '权限风险中心' }" class="modal-mask permission-modal-mask">
        <div class="modal-card permission-modal">
          <div class="modal-head">
            <div class="modal-title">权限警告详情</div>
            <div class="modal-head-actions">
              <button
                class="tab-btn"
                :class="{ active: permissionDetailTab === 'warnings' }"
                @click="switchPermissionDetailTab('warnings')"
              >权限警告</button>
              <button
                class="tab-btn tab-risk"
                :class="{ active: permissionDetailTab === 'risk_events' }"
                @click="switchPermissionDetailTab('risk_events')"
              >
                风险事件
                <span v-if="Number(overviewState.riskEventsTotal || 0) > 0" class="risk-badge">{{ riskEventWarnCount }}</span>
              </button>
              <button
                class="tab-btn"
                :class="{ active: permissionDetailTab === 'risk_rules' }"
                @click="switchPermissionDetailTab('risk_rules')"
              >风险规则</button>
              <button type="button" class="icon-close" aria-label="关闭权限风险中心" @click="closePermissionWarnings"><span aria-hidden="true">×</span></button>
            </div>
          </div>
          <div class="modal-body">
            <template v-if="permissionDetailTab === 'warnings'">
            <div class="filter-panel">
              <div class="filter-row query-row">
                <label class="compact-select">风险级别
                  <UiSelect
                    v-model="permissionFilters.riskLevel"
                    :options="[
                      { label: '全部', value: '' },
                      { label: 'critical', value: 'critical' },
                      { label: 'high', value: 'high' },
                      { label: 'medium', value: 'medium' },
                      { label: 'low', value: 'low' },
                    ]"
                  />
                </label>
                <label class="compact-select">来源
                  <UiSelect
                    v-model="permissionFilters.source"
                    :options="[
                      { label: '全部', value: '' },
                      { label: 'execution-api', value: 'execution-api' },
                      { label: 'chat-plane', value: 'chat-plane' },
                      { label: 'debug-web', value: 'debug-web' },
                    ]"
                  />
                </label>
                <label class="query-keyword">角色<input v-model.trim="permissionFilters.role" placeholder="角色关键字" /></label>
                <label class="query-keyword">关键字<input v-model.trim="permissionFilters.q" placeholder="message/path/reason" /></label>
              </div>
              <div class="filter-row time-row">
                <label class="window-field time-cell">查询窗口
                  <UiSelect v-model="permissionFilters.days" :options="dayWindowOptions" />
                </label>
                <label class="window-field time-cell">每页条数
                  <UiSelect
                    v-model="permissionFilters.pageSize"
                    :options="[
                      { label: '20', value: 20 },
                      { label: '50', value: 50 },
                      { label: '100', value: 100 },
                    ]"
                  />
                </label>
                <div class="inline-actions">
                  <button class="btn ghost" :disabled="permissionLoading" @click="reloadPermissionWarnings(1)">查询</button>
                </div>
              </div>
            </div>

            <div class="table-wrap permission-table-wrap">
              <div v-if="permissionLoading" class="table-loading">
                <span class="spinner"></span>
                <span>加载中...</span>
              </div>
              <table class="log-table">
                <colgroup>
                  <col style="width: 180px" />
                  <col style="width: 72px" />
                  <col style="width: 96px" />
                  <col style="width: 132px" />
                  <col style="width: 100px" />
                  <col style="width: 180px" />
                  <col style="width: 360px" />
                  <col style="width: 180px" />
                </colgroup>
                <thead>
                  <tr>
                    <th>time</th>
                    <th>risk</th>
                    <th>decision</th>
                    <th>reason</th>
                    <th>source</th>
                    <th>role</th>
                    <th>path</th>
                    <th>trace</th>
                  </tr>
                </thead>
                <tbody>
                  <tr
                    v-for="(item, idx) in (overviewState.permissionWarnings || [])"
                    :key="`perm-${item.ts}-${item.path}-${item.trace_id}-${item.reason_code}-${idx}`"
                    v-interactive-row="{ activate: () => { selectedPermissionWarn = item }, label: `查看权限警告 ${item.risk_level || ''} ${item.path || ''}`, current: selectedPermissionWarn === item }"
                    :class="{ selected: selectedPermissionWarn && selectedPermissionWarn === item }"
                    @click="selectedPermissionWarn = item"
                  >
                    <td>{{ formatTsZh(item.ts) }}</td>
                    <td :class="permissionRiskClass(item.risk_level)">{{ item.risk_level || '-' }}</td>
                    <td>{{ item.decision || '-' }}</td>
                    <td>{{ item.reason_code || '-' }}</td>
                    <td>{{ item.source || '-' }}</td>
                    <td>{{ item.role || '-' }}</td>
                    <td :title="item.path">{{ item.path || '-' }}</td>
                    <td :title="item.trace_id || ''">{{ String(item.trace_id || '-').slice(0, 24) }}</td>
                  </tr>
                  <tr v-if="!(overviewState.permissionWarnings || []).length">
                    <td colspan="8" class="release-empty" role="status" aria-live="polite" aria-atomic="true">暂无权限警告</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div v-if="selectedPermissionWarn" class="log-detail-drawer">
              <div class="drawer-head">
                <div class="drawer-title">警告详情</div>
                <button class="btn ghost mini" @click="selectedPermissionWarn = null">关闭详情</button>
              </div>
              <div class="drawer-grid">
                <div><b>time</b><span>{{ formatTsZh(selectedPermissionWarn.ts) }}</span></div>
                <div><b>risk</b><span>{{ selectedPermissionWarn.risk_level || '-' }}</span></div>
                <div><b>decision</b><span>{{ selectedPermissionWarn.decision || '-' }}</span></div>
                <div><b>reason</b><span>{{ selectedPermissionWarn.reason_code || '-' }}</span></div>
                <div><b>source</b><span>{{ selectedPermissionWarn.source || '-' }}</span></div>
                <div><b>role</b><span>{{ selectedPermissionWarn.role || '-' }}</span></div>
                <div class="wide"><b>path</b><span>{{ selectedPermissionWarn.path || '-' }}</span></div>
                <div class="wide"><b>message</b><span>{{ selectedPermissionWarn.message || '-' }}</span></div>
                <div class="wide"><b>risk tags</b><span>{{ (selectedPermissionWarn.risk_tags || []).join(', ') || '-' }}</span></div>
                <div class="wide"><b>trace_id</b><span>{{ selectedPermissionWarn.trace_id || '-' }}</span></div>
              </div>
            </div>
            </template>
            <template v-else-if="permissionDetailTab === 'risk_events'">
              <div class="filter-panel">
                <div class="filter-row query-row">
                  <label class="compact-select mini">决策
                    <UiSelect
                      v-model="riskEventFilters.decision"
                      :options="[
                        { label: '全部', value: '' },
                        { label: 'ALERT', value: 'ALERT' },
                        { label: 'ALLOW', value: 'ALLOW' },
                      ]"
                    />
                  </label>
                  <label class="compact-select mini">状态
                    <UiSelect
                      v-model="riskEventFilters.eventStatus"
                      :options="[
                        { label: '全部', value: '' },
                        { label: 'NEW', value: 'NEW' },
                        { label: 'ACKED', value: 'ACKED' },
                        { label: 'RESOLVED', value: 'RESOLVED' },
                      ]"
                    />
                  </label>
                  <label class="compact-select mini">级别
                    <UiSelect
                      v-model="riskEventFilters.riskLevel"
                      :options="[
                        { label: '全部', value: '' },
                        { label: 'critical', value: 'critical' },
                        { label: 'high', value: 'high' },
                        { label: 'medium', value: 'medium' },
                        { label: 'low', value: 'low' },
                      ]"
                    />
                  </label>
                  <label class="query-keyword mini"><input v-model.trim="riskEventFilters.reasonCode" placeholder="reason_code" /></label>
                  <label class="query-keyword mini"><input v-model.trim="riskEventFilters.riskTag" placeholder="risk_tag" /></label>
                  <label class="query-keyword mini"><input v-model.trim="riskEventFilters.q" placeholder="关键字过滤" /></label>
                </div>
                <div class="filter-row time-row">
                  <label class="window-field time-cell">查询窗口
                    <UiSelect v-model="riskEventFilters.days" :options="dayWindowOptions" />
                  </label>
                  <label class="window-field time-cell">每页条数
                    <UiSelect
                      v-model="riskEventFilters.pageSize"
                      :options="[
                        { label: '20', value: 20 },
                        { label: '50', value: 50 },
                        { label: '100', value: 100 },
                      ]"
                    />
                  </label>
                  <div class="inline-actions">
                    <button class="btn ghost" :disabled="riskEventsLoading" @click="reloadRiskEvents(1)">查询</button>
                  </div>
                </div>
              </div>

              <div class="table-wrap risk-events-wrap">
                <div v-if="riskEventsLoading" class="table-loading">
                  <span class="spinner"></span>
                  <span>事件加载中...</span>
                </div>
                <table class="log-table risk-event-table">
                  <colgroup>
                    <col style="width: 168px" />
                    <col style="width: 74px" />
                    <col style="width: 80px" />
                    <col style="width: 160px" />
                    <col style="width: 260px" />
                    <col style="width: 120px" />
                    <col style="width: 160px" />
                  </colgroup>
                  <thead><tr><th>time</th><th>risk</th><th>status</th><th>reason</th><th>path</th><th>source</th><th>action</th></tr></thead>
                  <tbody>
                    <tr
                      v-for="(item, idx) in (overviewState.riskEvents || [])"
                      :key="`risk-${item.event_id || item.id || item.trace_id || item.ts || 'x'}-${idx}`"
                      v-interactive-row="{ activate: () => onSelectRiskEvent(item), label: `查看风险事件 ${item.risk_level || ''} ${item.event_id || ''}`, current: selectedRiskEvent?.event_id === item.event_id }"
                      :class="{ selected: selectedRiskEvent && selectedRiskEvent.event_id === item.event_id }"
                      @click="onSelectRiskEvent(item)"
                    >
                      <td>{{ formatTsZh(item.ts) }}</td>
                      <td :class="permissionRiskClass(item.risk_level)">{{ item.risk_level || '-' }}</td>
                      <td>{{ item.event_status || '-' }}</td>
                      <td :title="item.reason_code">{{ item.reason_code || '-' }}</td>
                      <td :title="item.path">{{ item.path || '-' }}</td>
                      <td>{{ item.source || '-' }}</td>
                      <td class="risk-actions-cell">
                        <button
                          class="btn ghost mini"
                          :title="riskActionDisabledReason('ack', item)"
                          :disabled="isRiskActionDisabled('ack', item)"
                          @click.stop="runRiskEventAction('ack', item)"
                        >确认</button>
                        <button
                          class="btn ghost mini danger"
                          :title="riskActionDisabledReason('resolve', item)"
                          :disabled="isRiskActionDisabled('resolve', item)"
                          @click.stop="runRiskEventAction('resolve', item)"
                        >关闭</button>
                      </td>
                    </tr>
                    <tr v-if="!(overviewState.riskEvents || []).length">
                      <td colspan="7" class="release-empty" role="status" aria-live="polite" aria-atomic="true">暂无风险事件</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <div v-if="selectedRiskEvent" class="log-detail-drawer risk-detail-drawer">
                <div class="drawer-head">
                  <div class="drawer-title">风险事件详情</div>
                  <button class="btn ghost mini" @click="selectedRiskEvent = null">关闭详情</button>
                </div>
                <div class="drawer-grid">
                  <div><b>event_id</b><span>{{ selectedRiskEvent.event_id || '-' }}</span></div>
                  <div><b>time</b><span>{{ formatTsZh(selectedRiskEvent.ts) }}</span></div>
                  <div><b>risk</b><span>{{ selectedRiskEvent.risk_level || '-' }}</span></div>
                  <div><b>status</b><span>{{ selectedRiskEvent.event_status || '-' }}</span></div>
                  <div><b>decision</b><span>{{ selectedRiskEvent.decision || '-' }}</span></div>
                  <div><b>reason</b><span>{{ selectedRiskEvent.reason_code || '-' }}</span></div>
                  <div class="wide"><b>tags</b><span>{{ (selectedRiskEvent.risk_tags || []).join(', ') || '-' }}</span></div>
                  <div><b>trigger_rule</b><span>{{ selectedRiskEvent.trigger_rule || '-' }}</span></div>
                  <div class="wide"><b>trace_id</b><span>{{ selectedRiskEvent.trace_id || '-' }}</span></div>
                  <div class="wide"><b>message</b><span>{{ selectedRiskEvent.message || '-' }}</span></div>
                  <div class="wide"><b>path</b><span>{{ selectedRiskEvent.path || '-' }}</span></div>
                </div>
                <div class="risk-sop-block">
                  <div class="risk-sop-title">处理建议（SOP）</div>
                  <ol class="risk-sop-list">
                    <li>先确认事件并指定处理人，避免重复处理。</li>
                    <li>核对 reason/path/message，判断是否误报。</li>
                    <li>用 trace_id 或 event_id 在事务日志回溯上下游请求。</li>
                    <li>完成处置后执行关闭；若误报，调整规则后再发布。</li>
                  </ol>
                  <div class="inline-actions">
                    <button class="btn ghost mini" @click="locateRiskEventLogs(selectedRiskEvent, { openCenter: true })">打开事务日志并定位</button>
                    <button class="btn ghost mini" @click="copyText(selectedRiskEvent.trace_id, 'trace_id')">复制 trace_id</button>
                  </div>
                </div>
              </div>
            </template>

            <template v-else>
              <div class="filter-panel">
                <div class="filter-row query-row">
                  <label class="query-keyword">关键字
                    <input v-model.trim="riskRuleFilters.q" placeholder="id/path/reason/tag" />
                  </label>
                  <label class="compact-select">风险级别
                    <UiSelect
                      v-model="riskRuleFilters.riskLevel"
                      :options="[
                        { label: '全部', value: '' },
                        { label: 'LOW', value: 'LOW' },
                        { label: 'MEDIUM', value: 'MEDIUM' },
                        { label: 'HIGH', value: 'HIGH' },
                        { label: 'CRITICAL', value: 'CRITICAL' },
                      ]"
                    />
                  </label>
                  <label class="compact-select">决策
                    <UiSelect
                      v-model="riskRuleFilters.decision"
                      :options="[
                        { label: '全部', value: '' },
                        { label: 'ALLOW', value: 'ALLOW' },
                        { label: 'ALERT', value: 'ALERT' },
                      ]"
                    />
                  </label>
                  <label class="compact-select">启用状态
                    <UiSelect
                      v-model="riskRuleFilters.enabled"
                      :options="[
                        { label: '全部', value: '' },
                        { label: '启用', value: '1' },
                        { label: '禁用', value: '0' },
                      ]"
                    />
                  </label>
                  <label class="compact-select">每页条数
                    <UiSelect
                      v-model="riskRuleFilters.pageSize"
                      :options="[
                        { label: '10', value: 10 },
                        { label: '20', value: 20 },
                        { label: '50', value: 50 },
                      ]"
                    />
                  </label>
                </div>
                <div class="filter-row time-row">
                  <div class="inline-actions">
                    <button class="btn ghost mini" :disabled="riskRulesLoading" @click="reloadRiskRules">刷新规则</button>
                    <button class="btn ghost mini" @click="addRiskRule">新增规则</button>
                    <button class="btn ghost mini" @click="resetRiskRuleFilters">重置筛选</button>
                    <button class="btn ghost mini" @click="exportRiskRulesDraft">导出JSON</button>
                    <button class="btn ghost mini" @click="openRiskRuleImportModal">导入JSON</button>
                    <button class="btn primary mini" :disabled="riskRulesSaving" @click="saveRiskRulesDraft">保存发布</button>
                  </div>
                </div>
              </div>
                <div v-if="riskRulesValidationErrors.length" class="risk-validate-errors">
                  <div v-for="(err, idx) in riskRulesValidationErrors" :key="`prr-${idx}`">{{ err }}</div>
                </div>
                <div v-if="riskThresholdsParseError" class="risk-validate-errors">
                  <div>阈值配置错误：{{ riskThresholdsParseError }}</div>
                </div>
                <div class="table-wrap risk-rules-wrap">
                  <table class="log-table risk-rules-table">
                    <thead>
                      <tr>
                        <th>启用</th>
                        <th>规则ID</th>
                        <th>风险级别</th>
                        <th>决策</th>
                        <th>匹配路径</th>
                        <th>优先级</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr
                        v-for="(entry, idx) in pagedRiskRules"
                        :key="`prr-row-${entry.index}-${entry.row.id || 'new'}-${idx}`"
                        v-interactive-row="{ activate: () => selectRiskRule(entry.index), label: `编辑风险规则 ${entry.row.id || '新规则'}`, current: selectedRiskRuleIndex === entry.index }"
                        :class="{ selected: selectedRiskRuleIndex === entry.index }"
                        @click="selectRiskRule(entry.index)"
                      >
                        <td>
                          <span :class="entry.row.enabled ? 'rule-state-on' : 'rule-state-off'">
                            {{ entry.row.enabled ? '启用' : '禁用' }}
                          </span>
                        </td>
                        <td :title="entry.row.id">{{ entry.row.id || '-' }}</td>
                        <td :class="permissionRiskClass(entry.row.risk_level)">{{ entry.row.risk_level || '-' }}</td>
                        <td>{{ entry.row.decision || '-' }}</td>
                        <td :title="entry.row.path_pattern">{{ entry.row.path_pattern || '-' }}</td>
                        <td>{{ entry.row.priority }}</td>
                        <td><span class="row-edit-tip">点击行编辑</span></td>
                      </tr>
                      <tr v-if="!pagedRiskRules.length">
                        <td colspan="7" class="release-empty" role="status" aria-live="polite" aria-atomic="true">暂无规则，点击“新增规则”开始配置</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div v-if="selectedRiskRule" class="log-detail-drawer risk-detail-drawer">
                  <div class="drawer-head">
                    <div class="drawer-title">规则详情编辑</div>
                    <div class="inline-actions">
                      <button class="btn ghost mini" @click="normalizeRiskRulePriorities">重排优先级</button>
                      <button class="btn ghost mini" :disabled="selectedRiskRuleIndex <= 0" @click="moveRiskRule(selectedRiskRuleIndex, -1)">上移</button>
                      <button class="btn ghost mini" :disabled="selectedRiskRuleIndex >= riskRulesDraft.length - 1" @click="moveRiskRule(selectedRiskRuleIndex, 1)">下移</button>
                      <button class="btn ghost mini danger" @click="removeRiskRule(selectedRiskRuleIndex)">删除</button>
                    </div>
                  </div>
                  <div class="drawer-grid risk-rule-grid">
                    <div><b>启用</b><input v-model="selectedRiskRule.enabled" class="rule-enable-check" type="checkbox" /></div>
                    <div><b>priority</b><input v-model.number="selectedRiskRule.priority" class="rule-input" type="number" min="0" max="100000" step="1" /></div>
                    <div class="wide"><b>id</b><input v-model.trim="selectedRiskRule.id" class="rule-input" /></div>
                    <div>
                      <b>method</b>
                      <UiSelect
                        v-model="selectedRiskRule.method"
                        aria-label="method"
                        :options="[
                          { label: '*', value: '*' },
                          { label: 'GET', value: 'GET' },
                          { label: 'POST', value: 'POST' },
                          { label: 'PUT', value: 'PUT' },
                          { label: 'PATCH', value: 'PATCH' },
                          { label: 'DELETE', value: 'DELETE' },
                          { label: 'OPTIONS', value: 'OPTIONS' },
                          { label: 'HEAD', value: 'HEAD' },
                        ]"
                      />
                    </div>
                    <div>
                      <b>risk_level</b>
                      <UiSelect
                        v-model="selectedRiskRule.risk_level"
                        aria-label="risk_level"
                        :options="[
                          { label: 'LOW', value: 'LOW' },
                          { label: 'MEDIUM', value: 'MEDIUM' },
                          { label: 'HIGH', value: 'HIGH' },
                          { label: 'CRITICAL', value: 'CRITICAL' },
                        ]"
                      />
                    </div>
                    <div>
                      <b>decision</b>
                      <UiSelect
                        v-model="selectedRiskRule.decision"
                        aria-label="decision"
                        :options="[
                          { label: 'ALLOW', value: 'ALLOW' },
                          { label: 'ALERT', value: 'ALERT' },
                        ]"
                      />
                    </div>
                    <div class="wide"><b>path_pattern</b><input v-model.trim="selectedRiskRule.path_pattern" class="rule-input" /></div>
                    <div class="wide"><b>reason_code</b><input v-model.trim="selectedRiskRule.reason_code" class="rule-input" /></div>
                    <div class="wide">
                      <b>risk_tags</b>
                      <input
                        :value="(selectedRiskRule.risk_tags || []).join(', ')"
                        class="rule-input"
                        placeholder="tag1,tag2"
                        @change="setRiskRuleTagsFromInput(selectedRiskRuleIndex, $event.target.value)"
                        @blur="normalizeRiskRuleTags(selectedRiskRuleIndex)"
                      />
                    </div>
                  </div>
                </div>
                <label class="save-view-field risk-threshold-field">
                  <span>告警阈值（JSON 对象）</span>
                  <textarea
                    v-model="riskThresholdsDraftText"
                    rows="4"
                    maxlength="4000"
                    placeholder='{"high_daily_count":100,"critical_daily_count":20}'
                  ></textarea>
                </label>
                <div class="risk-events-foot">
                  <UiPager
                    :page="Number(riskRuleFilters.page || 1)"
                    :page-count="riskRulePageCount"
                    :total-items="riskRulesPageTotal"
                    :loading="riskRulesLoading"
                    aria-label="风险规则分页"
                    @page="setRiskRulePage"
                  />
                </div>
            </template>
          </div>
          <div class="modal-foot">
            <div class="modal-foot-left">
              <span v-if="permissionDetailTab === 'warnings'">总计 {{ Number(overviewState.permissionWarningsTotal || 0) }} 条</span>
              <span v-else-if="permissionDetailTab === 'risk_events'">总计 {{ Number(overviewState.riskEventsTotal || 0) }} 条</span>
              <span v-else>总计 {{ riskRulesPageTotal }} 条规则</span>
            </div>
            <div class="inline-actions permission-modal-pagination">
              <UiPager
                v-if="permissionDetailTab === 'warnings'"
                :page="Number(overviewState.permissionWarningsPage || 1)"
                :page-count="permissionWarningsPageCount"
                :total-items="Number(overviewState.permissionWarningsTotal || 0)"
                :loading="permissionLoading"
                aria-label="权限警告分页"
                @page="reloadPermissionWarnings"
              />
              <UiPager
                v-else-if="permissionDetailTab === 'risk_events'"
                :page="Number(overviewState.riskEventsPage || 1)"
                :page-count="riskEventsPageCount"
                :total-items="Number(overviewState.riskEventsTotal || 0)"
                :loading="riskEventsLoading"
                aria-label="风险事件分页"
                @page="reloadRiskEvents"
              />
              <button class="btn ghost" @click="closePermissionWarnings">关闭</button>
            </div>
          </div>
        </div>
      </div>
    </transition>

    <div v-if="showRiskRuleImportModal" v-modal-focus="{ close: closeRiskRuleImportModal, label: '导入风险规则 JSON' }" class="save-view-overlay" @pointerdown.self="closeRiskRuleImportModal">
      <div class="save-view-dialog risk-action-dialog">
        <div class="save-view-head">
          <div class="save-view-title">导入风险规则 JSON</div>
          <button type="button" class="icon-close mini" aria-label="关闭风险规则导入" :disabled="riskRulesSaving" @click="closeRiskRuleImportModal"><span aria-hidden="true">×</span></button>
        </div>
        <label class="save-view-field">
          <span>粘贴 JSON（包含 security_risk_rules/security_alert_thresholds）</span>
          <textarea
            v-model="riskRuleImportText"
            rows="10"
            maxlength="100000"
            placeholder='{"security_risk_rules":[...],"security_alert_thresholds":{...}}'
          ></textarea>
        </label>
        <div v-if="riskRuleImportError" class="risk-validate-errors">
          <div>导入失败：{{ riskRuleImportError }}</div>
        </div>
        <div class="save-view-actions">
          <button class="btn ghost" :disabled="riskRulesSaving" @click="closeRiskRuleImportModal">取消</button>
          <button class="btn primary" :disabled="riskRulesSaving" @click="applyRiskRuleImport">应用草稿</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.panel > .domain-section:first-of-type {
  margin-top: 0;
  padding-top: 0;
}

.section-head-inline {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}

.section-head-inline > .domain-section-title {
  margin: 0;
}

.refresh-meta {
  margin-left: auto;
  margin-right: 8px;
  font-size: 12px;
  color: #6f84a0;
  white-space: nowrap;
}

.save-view-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.msg-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.msg-card {
  text-align: left;
  border: 1px solid #dce8f6;
  border-radius: 12px;
  background: linear-gradient(180deg, #fbfdff, #f4f8ff);
  padding: 12px;
  cursor: pointer;
  min-height: 102px;
  display: grid;
  grid-template-rows: auto auto auto;
  align-content: start;
  transition: border-color .2s ease, box-shadow .2s ease, transform .12s ease;
}

.msg-card:hover {
  border-color: #b7cdee;
  box-shadow: 0 6px 18px rgba(52, 89, 148, 0.10);
}

.alert-card.warning{border-color:#f2ddb2;background:linear-gradient(180deg,#fffdf7,#fff5e9)}
.alert-card.critical{border-color:#efc6c6;background:linear-gradient(180deg,#fffafb,#fff0f0)}
.alert-card:disabled{cursor:default;opacity:1}.alert-source{margin-left:5px;padding:2px 6px;border-radius:999px;background:rgba(190,58,52,.1);font-size:10px;color:#a62924}.alert-summary{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

.api-risk-card.risk-low {
  border-color: #c9e8d6;
  background: linear-gradient(180deg, #f8fffb, #edf9f2);
  cursor: pointer;
}

.api-risk-card.risk-medium {
  border-color: #f2ddb2;
  background: linear-gradient(180deg, #fffdf7, #fff5e9);
  cursor: pointer;
}

.api-risk-card.risk-high {
  border-color: #efc6c6;
  background: linear-gradient(180deg, #fffafb, #fff0f0);
  cursor: pointer;
}

.risk-trend {
  display: inline-block;
  margin-left: 4px;
  font-size: 12px;
  font-weight: 700;
}

.risk-trend.up {
  color: #be3a34;
}

.risk-trend.down {
  color: #2a8a4b;
}

.risk-trend.flat {
  color: #5f7390;
}

.api-sparkline {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  letter-spacing: .2px;
}

.api-risk-head-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.api-risk-eval-btn {
  height: 24px;
  padding: 0 8px;
  font-size: 11px;
  line-height: 1;
  white-space: nowrap;
}

.api-risk-metric-tabs {
  margin-top: 4px;
  display: inline-flex;
  gap: 4px;
}

.risk-metric-tab {
  border: 1px solid #ccd9ec;
  background: #f7fbff;
  color: #4f6785;
  border-radius: 999px;
  font-size: 11px;
  padding: 1px 7px;
  line-height: 1.4;
  cursor: pointer;
}

.risk-metric-tab.active {
  border-color: #8eb4f0;
  color: #214f92;
  background: #eaf3ff;
}

.risk-events-block {
  margin-top: 10px;
  border: 1px solid #dfe8f5;
  border-radius: 10px;
  background: #fbfdff;
  padding: 10px;
}

.risk-events-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.risk-events-title {
  font-size: 14px;
  font-weight: 700;
  color: #38506f;
}

.risk-events-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
}

.risk-events-actions .query-keyword.mini input {
  min-width: 180px;
  height: 30px;
}

.risk-events-wrap {
  max-height: 240px;
  overflow: auto;
}

.risk-rules-wrap {
  max-height: 280px;
  overflow: auto;
}

.risk-rules-table .rule-input {
  width: 100%;
  min-width: 0;
  height: 30px;
  border: 1px solid #cfdcf1;
  border-radius: 6px;
  padding: 0 8px;
  font: inherit;
  color: #1f344d;
  background: #fff;
}

.risk-rules-table input[type='checkbox'] {
  width: 16px;
  height: 16px;
  vertical-align: middle;
}

.risk-rules-table {
  table-layout: fixed;
  width: 100%;
}

.risk-rules-table td,
.risk-rules-table th {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: middle;
}

.risk-rules-table td:nth-child(5),
.risk-rules-table th:nth-child(5) {
  white-space: normal;
  word-break: break-all;
}

.risk-rules-table th:nth-child(1),
.risk-rules-table td:nth-child(1) {
  width: 72px;
  min-width: 72px;
  text-align: center;
}

.risk-rules-table th:nth-child(2),
.risk-rules-table td:nth-child(2) {
  width: 240px;
}

.risk-rules-table th:nth-child(3),
.risk-rules-table td:nth-child(3) {
  width: 110px;
}

.risk-rules-table th:nth-child(4),
.risk-rules-table td:nth-child(4),
.risk-rules-table th:nth-child(6),
.risk-rules-table td:nth-child(6),
.risk-rules-table th:nth-child(7),
.risk-rules-table td:nth-child(7) {
  width: 96px;
}

.risk-rules-table th:nth-child(5),
.risk-rules-table td:nth-child(5) {
  width: auto;
  min-width: 280px;
}

.risk-validate-errors {
  margin-bottom: 8px;
  padding: 8px 10px;
  border: 1px solid #efc7c7;
  background: #fff6f6;
  border-radius: 8px;
  color: #a63a3a;
  font-size: 12px;
  display: grid;
  gap: 4px;
}

.rule-state-on,
.rule-state-off {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 44px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}

.rule-state-on {
  color: #17734a;
  background: #ebf9f1;
  border: 1px solid #bde8d2;
}

.rule-state-off {
  color: #6e84a0;
  background: #f2f6fb;
  border: 1px solid #d8e2ef;
}

.risk-threshold-field {
  margin-top: 4px;
}

.risk-threshold-field textarea {
  width: 100%;
  resize: vertical;
  min-height: 92px;
  max-height: 180px;
  border: 1px solid #cfdcf1;
  border-radius: 8px;
  padding: 8px 10px;
  font: inherit;
  color: #213a57;
  background: #fff;
}

.risk-rules-table th:nth-child(4),
.risk-rules-table td:nth-child(4),
.risk-rules-table th:nth-child(6),
.risk-rules-table td:nth-child(6),
.risk-rules-table th:nth-child(7),
.risk-rules-table td:nth-child(7) {
  min-width: 0;
}

.risk-detail-drawer {
  margin-top: 8px;
}

.risk-sop-block {
  margin-top: 10px;
  border-top: 1px dashed #d9e4f3;
  padding-top: 10px;
  display: grid;
  gap: 8px;
}

.risk-sop-title {
  font-size: 13px;
  font-weight: 700;
  color: #38506f;
}

.risk-sop-list {
  margin: 0;
  padding-left: 18px;
  color: #4c6483;
  font-size: 12px;
  line-height: 1.6;
}

.risk-rule-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.risk-rule-grid > div:first-child {
  max-width: 180px;
}

.rule-enable-check {
  width: 18px !important;
  height: 18px !important;
  min-width: 18px !important;
  min-height: 18px !important;
  margin: 0;
  padding: 0;
  appearance: auto;
  -webkit-appearance: checkbox;
  border-radius: 3px;
  accent-color: #4d83d6;
  vertical-align: middle;
}

.risk-action-dialog {
  width: min(620px, 96vw);
}

.risk-action-dialog textarea {
  width: 100%;
  resize: vertical;
  min-height: 88px;
  border: 1px solid #cfdcf1;
  border-radius: 8px;
  padding: 8px 10px;
  font: inherit;
  color: #213a57;
  background: #fff;
}

.risk-events-block textarea {
  width: 100%;
  resize: vertical;
  min-height: 86px;
  border: 1px solid #cfdcf1;
  border-radius: 8px;
  padding: 8px 10px;
  font: inherit;
  color: #213a57;
  background: #fff;
}

.risk-actions-cell {
  display: flex;
  gap: 6px;
}

.risk-events-foot {
  margin-top: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #607995;
  font-size: 12px;
}

.risk-events-foot :deep(.ui-pager),
.permission-modal-pagination :deep(.ui-pager) {
  margin-top: 0;
}

.btn.danger {
  color: #b54040;
  border-color: #e9c9c9;
  background: #fff7f7;
}

.row-locating {
  animation: rowLocatePulse 1.8s ease;
}

.copy-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.copy-btn {
  white-space: nowrap;
}

.cell-message mark,
td mark {
  background: #fff1a8;
  color: #1f2f46;
  padding: 0 1px;
  border-radius: 2px;
}

@keyframes rowLocatePulse {
  0% { background: #eaf5ff; }
  40% { background: #f4faff; }
  100% { background: transparent; }
}

.msg-card.muted {
  cursor: default;
}

.task-card {
  overflow: hidden;
  cursor: default;
}

.msg-head {
  font-size: 14px;
  color: #49617f;
  font-weight: 700;
}

.msg-num {
  margin-top: 8px;
  font-size: 32px;
  line-height: 1;
  font-weight: 800;
  color: #1f3f74;
}

.msg-sub {
  margin-top: 4px;
  font-size: 11px;
  line-height: 1.2;
  color: #607995;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.runtime-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.runtime-actions {
  display: inline-flex;
  gap: 8px;
}

.metric-tabs {
  margin-top: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.metric-tab {
  border: 1px solid #cfe0f5;
  background: #f7faff;
  color: #395374;
  border-radius: 999px;
  min-height: 34px;
  padding: 6px 12px;
  cursor: pointer;
}

.metric-tab.active {
  border-color: #87abeb;
  background: #ebf2ff;
  color: #1d4fa8;
  font-weight: 700;
}

.mega-chart {
  border: 1px solid #dbe7f7;
  border-radius: 12px;
  padding: 12px;
  background: #fbfdff;
  position: relative;
  cursor: pointer;
  height: 100%;
  user-select: none;
  -webkit-user-select: none;
  -ms-user-select: none;
}

.mega-chart * {
  user-select: none;
  -webkit-user-select: none;
  -ms-user-select: none;
}

.runtime-main {
  margin-top: 12px;
  display: grid;
  grid-template-columns: minmax(0, 2.15fr) minmax(260px, 0.85fr);
  gap: 12px;
  align-items: stretch;
}

.chart-meta {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 12px;
  align-items: center;
  margin-bottom: 8px;
  color: #385271;
}

.mode-tip {
  color: #6780a0;
  font-size: 12px;
}

.chart-tip {
  position: absolute;
  z-index: 2;
  transform: translate(-50%, calc(-100% - 10px));
  background: #1c2f49;
  color: #fff;
  font-size: 12px;
  padding: 4px 8px;
  border-radius: 6px;
  white-space: nowrap;
  pointer-events: none;
}

.line-svg {
  width: 100%;
  height: 320px;
}

.chart-layer {
  opacity: 0;
  transform: translateY(6px) scale(0.992);
  transform-origin: 50% 50%;
  transition:
    opacity 0.3s cubic-bezier(0.22, 1, 0.36, 1),
    transform 0.3s cubic-bezier(0.22, 1, 0.36, 1);
  pointer-events: none;
}

.chart-layer.active {
  opacity: 1;
  transform: translateY(0) scale(1);
  pointer-events: auto;
}

.grid-line {
  stroke: #e8effa;
  stroke-width: 1;
}

.grid-line.v {
  stroke: #f0f5fc;
}

.axis {
  stroke: #9fb0c9;
  stroke-width: 1.2;
}

.axis-label {
  fill: #6f849f;
  font-size: 11px;
}

.line-path {
  fill: none;
  stroke: #3f76d2;
  stroke-width: 3;
  stroke-linecap: round;
  stroke-linejoin: round;
  transition: opacity 0.28s ease;
}

.line-path.dashed {
  stroke-dasharray: 7 6;
  opacity: 0.9;
}

.line-dot {
  fill: #3f76d2;
  transition:
    opacity 0.24s ease,
    r 0.24s ease;
}

.bar {
  fill: #5f8fe2;
  opacity: 0.9;
  transition: opacity 0.28s ease;
}

.ring-panel {
  border: 1px solid #dbe7f7;
  border-radius: 12px;
  background: #fbfdff;
  padding: 12px;
  display: grid;
  justify-items: center;
  align-content: center;
  gap: 8px;
  cursor: pointer;
  height: 100%;
  transition:
    transform 0.24s ease,
    box-shadow 0.24s ease;
}

.ring-head {
  font-weight: 700;
  color: #365475;
  transition: opacity 0.24s ease, transform 0.24s ease;
}

.ring-svg {
  width: 170px;
  height: 170px;
  transition: transform 0.3s cubic-bezier(0.22, 1, 0.36, 1);
}

.ring-bg {
  fill: none;
  stroke: #e8effa;
  stroke-width: 11;
}

.ring-fg {
  fill: none;
  stroke: url(#ringGrad);
  stroke-width: 11;
  stroke-linecap: round;
  stroke-dasharray: 314;
  stroke-dashoffset: 314;
  transform: rotate(-90deg);
  transform-origin: 60px 60px;
  transition: stroke-dashoffset 0.48s cubic-bezier(0.22, 1, 0.36, 1);
}

.ring-fg.muted {
  stroke: #b7c6da;
}

.ring-percent {
  fill: #234a7b;
  font-size: 19px;
  font-weight: 800;
  transition: opacity 0.24s ease, transform 0.24s ease;
}

.ring-value {
  fill: #567091;
  font-size: 10px;
  font-weight: 600;
  transition: opacity 0.24s ease, transform 0.24s ease;
}

.ring-sub {
  color: #567091;
  font-size: 12px;
}

.ring-tip {
  color: #7a8ea8;
  font-size: 11px;
}

.ring-panel.animating {
  transform: translateY(-1px);
  box-shadow: 0 8px 20px rgba(56, 92, 145, 0.12);
}

.ring-panel.animating .ring-svg {
  transform: scale(1.03);
}

.ring-panel.animating .ring-head,
.ring-panel.animating .ring-percent,
.ring-panel.animating .ring-value {
  opacity: 0.78;
  transform: translateY(-1px);
}

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(19, 28, 40, 0.35);
  display: grid;
  place-items: center;
  z-index: 999;
  padding: 18px;
}

.permission-modal-mask {
  z-index: 999;
}

.log-modal-mask {
  z-index: 1001;
}

.modal-card {
  position: relative;
  width: min(1280px, 96vw);
  height: min(90vh, 920px);
  overflow: hidden;
  background: #fff;
  border-radius: 12px;
  border: 1px solid #d8e5f5;
  box-shadow: 0 20px 48px rgba(20, 32, 48, 0.2);
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
}

.modal-head {
  position: sticky;
  top: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid #e5edf8;
  background: #fff;
}

.modal-title {
  font-size: 18px;
  font-weight: 700;
}

.modal-head-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.mode-switch {
  position: relative;
  display: grid;
  grid-template-columns: 1fr 1fr;
  align-items: center;
  border: 1px solid #d6e4f6;
  border-radius: 999px;
  background: #f7fbff;
  padding: 2px;
  flex: 0 0 auto;
  white-space: nowrap;
  min-width: 184px;
  height: 32px;
  overflow: hidden;
  cursor: pointer;
}

.mode-thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: calc(50% - 2px);
  height: calc(100% - 4px);
  border-radius: 999px;
  background: #fff;
  box-shadow: 0 1px 2px rgba(20, 44, 77, 0.12);
  transition: transform 0.22s ease;
  z-index: 0;
}

.mode-switch.live .mode-thumb {
  transform: translateX(100%);
}

.mode-btn {
  min-height: 26px;
  height: 26px;
  padding: 0 10px;
  min-width: 0;
  width: 100%;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: #5a6f8f;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  white-space: nowrap;
  flex: 1 1 auto;
  position: relative;
  z-index: 1;
  transition: color 0.2s ease;
  pointer-events: none;
}

.mode-btn.active {
  color: #1f4f91;
}

.mode-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.inline-switch {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: #445e80;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
}

.inline-switch input {
  width: 14px;
  height: 14px;
  min-height: unset;
  min-width: 14px;
  margin: 0;
  padding: 0;
  border-radius: 3px;
  border: 1px solid #9eb2cf;
  appearance: auto;
  -webkit-appearance: checkbox;
  box-sizing: border-box;
  background: #fff;
}

.live-meta {
  color: #6f84a0;
  font-size: 11px;
  white-space: nowrap;
  display: inline-block;
  width: 128px;
  flex: 0 0 128px;
  font-variant-numeric: tabular-nums;
}

.new-only {
  margin-left: 4px;
  min-height: 32px;
  align-items: center;
  flex: 0 0 auto;
  white-space: nowrap;
}

.new-only span {
  white-space: nowrap;
  line-height: 1;
}

.live-interval-select {
  min-height: 32px;
  height: 32px;
  padding: 4px 8px;
  border-radius: 8px;
  width: auto;
  min-width: 64px;
}

.icon-close {
  width: 28px;
  height: 28px;
  border-radius: 999px;
  border: 1px solid #ccdaee;
  background: #f7faff;
  cursor: pointer;
}

.modal-body {
  padding: 10px 12px;
  min-height: 0;
  overflow: hidden;
  display: grid;
  grid-template-rows: auto auto minmax(0, 1fr) auto;
  gap: 8px;
}

.permission-modal .modal-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  overflow: auto;
}

.filter-panel {
  --field-border-default: #c8d7eb;
  --field-border-hover: #9db8df;
  --field-border-active: #6f9fe3;
  --field-border-focus: #5b8fdd;
  --field-bg-default: #fff;
  --field-bg-hover: #fcfeff;
  --field-bg-active: #f6faff;
  --field-bg-focus: #f7fbff;
  --field-ring-active: rgba(111, 159, 227, 0.18);
  --field-ring-focus: rgba(91, 143, 221, 0.2);
  border: 1px solid #e3ebf7;
  border-radius: 10px;
  background: #f9fbff;
  padding: 8px;
  display: grid;
  gap: 6px;
}

.preset-row {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  flex-wrap: nowrap;
  gap: 24px;
  overflow: visible;
  padding-bottom: 0;
}

.filter-panel .preset-row {
  display: flex;
  grid-template-columns: none;
}

.preset-inline {
  display: inline-flex;
  align-items: center;
  gap: 12px;
  flex-wrap: nowrap;
  flex: 0 0 auto;
  border: 0;
  border-radius: 0;
  background: transparent;
  padding: 0;
}

.preset-inline.right {
  display: grid;
  grid-template-columns: auto minmax(220px, 300px) auto auto auto;
  align-items: center;
  justify-content: flex-end;
  margin-left: auto;
  padding-left: 16px;
  border-left: 1px solid #dbe6f5;
  column-gap: 10px;
  min-width: 0;
  flex: 1 1 auto;
}

.preset-title {
  font-size: 12px;
  color: #5f738f;
  font-weight: 700;
  white-space: nowrap;
  flex: 0 0 auto;
}

.chip-btn {
  border: 1px solid #d4e1f2;
  background: #fff;
  color: #4f6888;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  flex: 0 0 auto;
}

.preset-inline .chip-btn + .chip-btn,
.preset-inline.right .btn.mini + .btn.mini {
  margin-left: 2px;
}

.chip-btn.active {
  border-color: #5f8fcf;
  background: #eaf3ff;
  color: #1f4f91;
}

.saved-view-select {
  width: 100%;
  min-width: 0;
  max-width: none;
  flex: initial;
}

.modal-foot-left .saved-view-select {
  width: 320px;
  min-width: 320px;
  max-width: 320px;
}

.btn.mini {
  min-height: 30px;
  height: 30px;
  padding: 4px 9px;
  font-size: 12px;
  white-space: nowrap;
  flex: 0 0 auto;
}

.filter-row {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
  align-items: end;
}

.query-row {
  grid-template-columns: max-content minmax(260px, 1fr) max-content max-content max-content max-content;
}

.filter-row.time-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  flex-wrap: nowrap;
}

.filter-row.time-row > .time-cell {
  min-width: 0;
  display: grid;
  align-content: start;
  gap: 3px;
}

.filter-row.time-row > .time-cell.no-label::before {
  content: '';
  display: block;
  height: 14px;
}

.filter-row.time-row > label.time-date {
  flex: 0 0 180px;
}

.filter-row.time-row > .period-field {
  flex: 0 0 auto;
  width: fit-content;
}

.filter-row.time-row > label.window-field {
  flex: 0 0 150px;
}

.period-range {
  min-height: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
  gap: 4px;
  width: fit-content;
}

.period-field {
  display: grid;
  gap: 3px;
  font-size: 11px;
  color: #5f728d;
  font-weight: 600;
  min-width: 0;
}

.period-title {
  line-height: 1;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.period-range > input[type='time'] {
  width: 84px;
  min-width: 84px;
  max-width: 84px;
}

.period-sep {
  color: #67809f;
  font-size: 12px;
  line-height: 1;
}

.period-next-day {
  color: #1f5bb8;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  white-space: nowrap;
}

.filter-panel label {
  gap: 3px;
  font-size: 11px;
}

.filter-panel label,
.filter-panel .preset-title,
.filter-panel .chip-btn,
.filter-panel .btn,
.filter-panel .inline-switch span,
.filter-panel .live-meta,
.filter-panel .period-title,
.filter-panel .period-next-day,
.filter-panel .period-sep {
  user-select: none;
  -webkit-user-select: none;
}

/* Prevent container/label blank area from focusing inner controls.
   Only the controls themselves remain clickable. */
.filter-panel label,
.filter-panel .inline-switch {
  pointer-events: none;
}

.filter-panel label :is(input, select, textarea, button),
.filter-panel .inline-switch input {
  pointer-events: auto;
}

.filter-panel :is(input, select) {
  min-height: 32px;
  height: 32px;
  padding: 5px 8px;
  border-radius: 8px;
  font-size: 12px;
  width: 100%;
  box-sizing: border-box;
  border-color: var(--field-border-default);
  background: var(--field-bg-default);
  transition:
    border-color 0.2s cubic-bezier(0.22, 1, 0.36, 1),
    box-shadow 0.22s cubic-bezier(0.22, 1, 0.36, 1),
    background-color 0.2s ease,
    transform 0.12s ease;
}

.filter-panel select {
  cursor: pointer;
  will-change: border-color, box-shadow, background-color;
}

.filter-panel :is(input, select):hover:not(:disabled) {
  border-color: var(--field-border-hover);
  background: var(--field-bg-hover);
}

.filter-panel :is(input, select):active:not(:disabled) {
  border-color: var(--field-border-active);
  box-shadow: 0 0 0 2px var(--field-ring-active);
  background: var(--field-bg-active);
  transform: translateY(0);
}

.filter-panel :is(input, select):focus,
.filter-panel :is(input, select):focus-visible {
  outline: none;
  border-color: var(--field-border-focus);
  box-shadow: 0 0 0 2px var(--field-ring-focus);
  background: var(--field-bg-focus);
}

.query-row > label.compact-select > select {
  width: 100%;
  min-width: 0;
  max-width: 100%;
  justify-self: start;
}

.query-row > label.compact-select {
  width: 100%;
  min-width: 140px;
  justify-items: start;
}

.query-row > label.query-keyword {
  min-width: 220px;
}

.filter-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex: 0 0 auto;
  margin-left: auto;
  align-self: auto;
}

.inline-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-left: 8px;
  white-space: nowrap;
}

.live-inline {
  display: inline-grid;
  grid-auto-flow: column;
  grid-auto-columns: max-content;
  align-items: center;
  column-gap: 8px;
  justify-content: start;
  min-height: 32px;
  padding: 0;
  flex-wrap: nowrap;
  min-width: 0;
  flex: 0 0 auto;
  white-space: nowrap;
  align-self: auto;
}

.live-inline > * {
  align-self: center;
}

.live-inline .live-interval-select {
  width: 68px;
  min-width: 68px;
  max-width: 68px;
  min-height: 32px;
  height: 32px;
  display: flex;
  align-items: center;
}

.live-inline .live-interval-select :deep(.ui-select-trigger) {
  min-height: 32px;
  height: 32px;
  padding: 0 22px 0 8px;
}

.filter-actions .btn,
.inline-actions .btn {
  min-height: 32px;
  height: 32px;
  padding: 6px 12px;
  white-space: nowrap;
  flex: 0 0 auto;
}

.log-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  align-items: stretch;
}

.log-kpis .kpi-card {
  padding: 7px 12px 5px;
  min-height: 50px;
  height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
}

.log-kpis .kpi-name {
  margin: 0;
  line-height: 1.15;
  font-size: 12px;
}

.log-kpis .kpi-toggle {
  width: 100%;
  border: 1px solid #d8e5f6;
  border-radius: 10px;
  background: #fff;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.16s ease, box-shadow 0.16s ease, transform 0.12s ease;
}

.log-kpis .kpi-toggle:hover:not(.disabled) {
  border-color: #9fc0eb;
  box-shadow: 0 3px 12px rgba(36, 75, 132, 0.12);
}

.log-kpis .kpi-toggle.active {
  border-color: #5f8fcf;
  box-shadow: 0 0 0 2px rgba(95, 143, 207, 0.18);
}

.log-kpis .kpi-toggle.disabled {
  opacity: 0.58;
  cursor: not-allowed;
}

.log-kpis .kpi-name {
  margin-bottom: 4px;
}

.log-kpis .kpi-value {
  margin-top: 1px;
  font-size: 22px;
  line-height: 1;
}

.strategy-card .kpi-mini {
  margin-top: 2px;
  line-height: 1.3;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 12px;
}

.kpi-inline-tip {
  margin-left: 6px;
  font-size: 11px;
  color: #5a78a0;
  white-space: nowrap;
  font-weight: 600;
}

.modal-body .table-wrap {
  min-height: 0;
  max-height: none;
  overflow: auto;
  position: relative;
}

.modal-body table {
  table-layout: fixed;
}

.log-scroll-wrap .log-table {
  table-layout: fixed;
  width: auto;
  min-width: max-content;
}

.log-scroll-wrap .log-table th,
.log-scroll-wrap .log-table td {
  padding: 7px 6px;
}

.log-scroll-wrap .log-table .col-time { width: 220px; }
.log-scroll-wrap .log-table .col-level { width: 64px; }
.log-scroll-wrap .log-table .col-category { width: 100px; }
.log-scroll-wrap .log-table .col-message { width: 128px; }
.log-scroll-wrap .log-table .col-source { width: 128px; }
.log-scroll-wrap .log-table .col-role { width: 170px; }
.log-scroll-wrap .log-table .col-status { width: 96px; }
.log-scroll-wrap .log-table .col-path { width: 560px; }

.log-scroll-wrap .log-table .cell-message {
  max-width: 128px;
  width: 128px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.modal-body td {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.log-scroll-wrap tbody tr {
  cursor: pointer;
}

.log-scroll-wrap tbody tr.selected {
  background: #eef5ff;
}

.log-scroll-wrap tbody tr.row-fresh {
  animation: row-fresh-glow 1.4s ease-out 1;
}

.log-scroll-wrap {
  border-radius: 10px;
}

.permission-modal {
  width: min(1360px, 97vw);
}

.permission-modal .save-view-overlay {
  position: fixed;
  inset: 0;
  z-index: 60;
}

.tab-btn {
  position: relative;
  border: 1px solid #bfd0e7;
  background: #f4f8ff;
  color: #355274;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.tab-btn.active {
  background: #e7f0ff;
  border-color: #89aee0;
  color: #1f3f67;
}

.tab-risk {
  padding-right: 28px;
}

.risk-badge {
  position: absolute;
  top: -5px;
  right: -5px;
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  border-radius: 999px;
  background: #d43f3f;
  color: #fff;
  font-size: 10px;
  line-height: 16px;
  text-align: center;
}

.permission-table-wrap {
  max-height: 44vh;
  overflow: auto;
}

.log-window-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  color: #6c809b;
  font-size: 12px;
}

.log-meta-right {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.quick-inline {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

.log-detail-drawer {
  border: 1px solid #dbe8f8;
  border-radius: 10px;
  background: #fbfdff;
  padding: 10px;
}

.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.drawer-title {
  font-size: 13px;
  font-weight: 700;
  color: #365171;
}

.drawer-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 12px;
}

.drawer-grid > div {
  display: grid;
  gap: 2px;
}

.drawer-grid > div.wide {
  grid-column: 1 / -1;
}

.drawer-grid b {
  font-size: 11px;
  color: #68809f;
}

.drawer-grid span {
  font-size: 13px;
  color: #2a3f5a;
  word-break: break-word;
}

.level-crit { color: #a32828; font-weight: 700; }
.level-err { color: #c23a3a; font-weight: 700; }
.level-warn { color: #b27d00; font-weight: 700; }
.level-info { color: #5f738e; }

.status-bad { color: #b23a3a; font-weight: 700; }
.status-warn { color: #b27d00; font-weight: 700; }
.status-ok { color: #1f7f50; font-weight: 700; }
.status-neutral { color: #607995; }

.table-loading {
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.72);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  z-index: 2;
  color: #3f5879;
  font-size: 12px;
}

.spinner {
  width: 14px;
  height: 14px;
  border-radius: 999px;
  border: 2px solid #c5d7f2;
  border-top-color: #4f84dd;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes row-fresh-glow {
  0% { background: #e7f7f0; }
  100% { background: transparent; }
}

.kpi-mini {
  margin-top: 6px;
  font-size: 12px;
  color: #50647f;
}

.modal-foot {
  position: sticky;
  bottom: 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-top: 1px solid #e5edf8;
  background: #fff;
}

.modal-foot-left {
  display: inline-grid;
  grid-auto-flow: column;
  grid-auto-columns: max-content;
  align-items: center;
  column-gap: 10px;
  min-width: 0;
}

.view-op-notice {
  color: #1f7f50;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.view-storage-hint {
  color: #6e84a1;
  font-size: 12px;
  white-space: nowrap;
}

.modal-enter-active,
.modal-leave-active {
  transition: opacity 0.18s ease;
}

.modal-enter-active .modal-card,
.modal-leave-active .modal-card {
  transition: transform 0.2s ease, opacity 0.2s ease;
}

.modal-enter-from,
.modal-leave-to {
  opacity: 0;
}

.modal-enter-from .modal-card,
.modal-leave-to .modal-card {
  transform: translateY(14px) scale(0.98);
  opacity: 0;
}

@media (max-width: 1320px) {
  .msg-grid,
  .log-kpis {
    grid-template-columns: 1fr;
  }

  .filter-row:not(.time-row) {
    grid-template-columns: 1fr;
  }

  .filter-row.time-row {
    display: flex;
    align-items: flex-end;
    flex-wrap: nowrap;
  }

  .chart-meta {
    grid-template-columns: 1fr;
    gap: 6px;
  }

  .runtime-main {
    grid-template-columns: 1fr;
  }

  .line-svg {
    height: 250px;
  }
}

.save-view-overlay {
  position: absolute;
  inset: 0;
  z-index: 12;
  background: rgba(17, 29, 44, 0.36);
  display: grid;
  place-items: center;
  padding: 16px;
}

.save-view-dialog {
  width: min(420px, 92vw);
  border: 1px solid #d8e5f6;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 16px 32px rgba(17, 31, 52, 0.22);
  padding: 14px;
  display: grid;
  gap: 12px;
}

.save-view-title {
  font-size: 16px;
  font-weight: 700;
  color: #1f3654;
}

.save-view-field {
  display: grid;
  gap: 6px;
}

.save-view-field > span {
  color: #5c728f;
  font-size: 12px;
  font-weight: 700;
}

.save-view-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.policy-dialog {
  width: min(640px, 94vw);
}

.policy-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.policy-grid > label {
  display: grid;
  gap: 4px;
  color: #5c728f;
  font-size: 12px;
  font-weight: 700;
}

@media (max-width: 1680px) {
  .preset-inline.right {
    justify-content: flex-start;
  }

  .filter-row.time-row {
    display: flex;
    align-items: flex-end;
    flex-wrap: nowrap;
  }

  .filter-actions {
    justify-content: flex-start;
  }
}
</style>
