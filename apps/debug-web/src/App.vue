<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { endpointGroups } from './endpoints'

const globalConfig = reactive({
  controlPlaneBaseUrl: 'http://127.0.0.1:8099',
  executionBaseUrl: 'http://localhost:8091',
  chatBaseUrl: 'http://localhost:8092',
  tenantId: 'platform',
  actorId: 'debug-user',
  sourceService: 'debug-web',
  traceId: '',
  adminToken: '',
  controlPlaneToken: '',
  adminUsername: 'admin',
  adminPassword: '',
})

const auth = reactive({
  loggedIn: false,
  loggingIn: false,
  error: '',
})

const flatEndpoints = endpointGroups.flatMap((g) =>
  g.endpoints.map((e) => ({ ...e, group: g.group })),
)

const selectedId = ref(flatEndpoints[0]?.id ?? '')

const state = reactive(
  Object.fromEntries(
    flatEndpoints.map((ep) => [
      ep.id,
      {
        pathParams: { ...ep.pathParams },
        queryParams: { ...ep.queryParams },
        headers: { ...ep.headers },
        bodyText: ep.body === null ? '' : JSON.stringify(ep.body, null, 2),
      },
    ]),
  ),
)

const result = reactive({
  loading: false,
  status: '',
  durationMs: 0,
  requestPreview: '',
  responseHeaders: '',
  responseBody: '',
  error: '',
})

const selectedEndpoint = computed(() =>
  flatEndpoints.find((ep) => ep.id === selectedId.value),
)

const selectedState = computed(() =>
  selectedEndpoint.value ? state[selectedEndpoint.value.id] : null,
)

const selectedEndpointDoc = computed(() => {
  const ep = selectedEndpoint.value
  const st = selectedState.value
  if (!ep || !st) return null
  const docMap = parseParamDocs(ep.paramDocs || [])
  return {
    requestExample: {
      method: ep.method,
      url: buildUrl(ep, st),
      headers: {
        ...st.headers,
        ...(ep.path.startsWith('/api/v1/admin/')
          ? { Authorization: `Bearer ${ep.baseKey === 'controlPlaneBaseUrl' ? (globalConfig.controlPlaneToken || globalConfig.adminToken) : globalConfig.adminToken}` }
          : {}),
        'X-Tenant-Id': globalConfig.tenantId,
        'X-Actor-Id': globalConfig.actorId,
        'X-Source-Service': globalConfig.sourceService,
        ...(globalConfig.traceId?.trim() ? { 'X-Trace-Id': globalConfig.traceId.trim() } : {}),
      },
      ...(ep.method !== 'GET' && ep.method !== 'DELETE' && st.bodyText?.trim()
        ? { body: safeJson(st.bodyText) }
        : {}),
    },
    responseExample:
      ep.responseExample || {
        status: ep.method === 'POST' ? '200/202' : '200',
        data: { status: 'ok', trace_id: '<trace-id>' },
      },
    fields: buildFieldRows(ep, st, docMap),
  }
})

const dbviz = reactive({
  loading: false,
  error: '',
  tenantScope: 'current',
  source: 'postgres',
  overview: null,
  pgTables: [],
  pgTable: 'documents',
  pgRows: [],
  pgPage: 1,
  pgPageSize: 20,
  pgTotal: 0,
  cpOverview: null,
  cpTables: [],
  cpTable: 'users',
  cpRows: [],
  cpPage: 1,
  cpPageSize: 20,
  cpTotal: 0,
  qdrantCollections: [],
  qdrantCollection: '',
  qdrantPoints: [],
  qdrantPageSize: 20,
  qdrantOffset: '',
  qdrantNextOffset: null,
  qdrantDetailLoading: false,
  qdrantDetailError: '',
  qdrantDetail: null,
  mongoCollections: [],
  mongoCollection: '',
  mongoRows: [],
  mongoPage: 1,
  mongoPageSize: 20,
  mongoTotal: 0,
  usageDays: 30,
  usageLimit: 200,
  usageSummary: null,
  usageEvents: [],
  usagePage: 1,
  usagePageSize: 50,
  usageTotal: 0,
  usageApiPath: '',
  usageModel: '',
  usageApiKeyFingerprint: '',
  usageTaskCategory: '',
  usageBudgets: [],
  usageAlerts: [],
  usageAlertDays: 30,
  usageAlertStatus: 'OPEN',
  usageAlertLimit: 200,
  usageBudgetForm: {
    scope_type: 'tenant',
    scope_value: '*',
    daily_token_limit: '',
    daily_cost_limit: '',
    enabled: true,
  },
  statusChecks: [],
  statusTasks: null,
  statusTaskItems: [],
  statusKbs: 0,
  chatStoreStatus: null,
  statusLastUpdated: '',
  statusAutoRefresh: true,
  statusRefreshSeconds: 5,
  runtimeConfig: {},
  runtimeConfigDraft: {},
  runtimeConfigKeys: [],
  runtimeConfigDirty: false,
})
const pgTotalPages = computed(() => Math.max(1, Math.ceil((dbviz.pgTotal || 0) / (dbviz.pgPageSize || 1))))
const cpTotalPages = computed(() => Math.max(1, Math.ceil((dbviz.cpTotal || 0) / (dbviz.cpPageSize || 1))))
const mongoTotalPages = computed(() => Math.max(1, Math.ceil((dbviz.mongoTotal || 0) / (dbviz.mongoPageSize || 1))))
const usageTotalPages = computed(() => Math.max(1, Math.ceil((dbviz.usageTotal || 0) / (dbviz.usagePageSize || 1))))

function runtimeConfigGroupOf(key) {
  const k = String(key || '')
  if (k.startsWith('execution_') || k.startsWith('global_kb_')) return 'Execution'
  if (k.startsWith('embedding_')) return 'Embedding'
  if (k.startsWith('summary_')) return 'Summary'
  if (k.startsWith('chat_')) return 'Chat'
  if (k.startsWith('memory_')) return 'Memory'
  if (k.startsWith('api_slot_') || k.startsWith('api_model_')) return 'API Slot/Pricing'
  return 'Other'
}

const runtimeConfigGroupedKeys = computed(() => {
  const preferredOrder = [
    'Execution',
    'Embedding',
    'Summary',
    'Chat',
    'Memory',
    'API Slot/Pricing',
    'Other',
  ]
  const groups = {}
  for (const key of dbviz.runtimeConfigKeys || []) {
    const g = runtimeConfigGroupOf(key)
    if (!groups[g]) groups[g] = []
    groups[g].push(key)
  }
  return preferredOrder
    .filter((name) => Array.isArray(groups[name]) && groups[name].length > 0)
    .map((name) => ({ name, keys: groups[name] }))
})

function formatCny(value, digits = 4) {
  const n = Number(value || 0)
  if (!Number.isFinite(n)) return `¥0.${'0'.repeat(digits)}`
  return `¥${n.toFixed(digits)}`
}

const methodClass = (method) => `method-${method.toLowerCase()}`

function isLikelySignedToken(token) {
  const t = String(token || '').trim()
  if (!t) return false
  const dots = (t.match(/\./g) || []).length
  return dots === 1 || dots === 2
}

function pathWithParams(endpoint, pathParams) {
  let path = endpoint.path
  Object.entries(pathParams).forEach(([k, v]) => {
    path = path.replace(`{${k}}`, encodeURIComponent(v ?? ''))
  })
  return path
}

function buildUrl(endpoint, st) {
  const base = globalConfig[endpoint.baseKey]?.trim().replace(/\/$/, '')
  const path = pathWithParams(endpoint, st.pathParams)
  const url = new URL(`${base}${path}`)

  Object.entries(st.queryParams).forEach(([k, v]) => {
    if (v !== '' && v !== null && v !== undefined) {
      url.searchParams.set(k, String(v))
    }
  })

  return url.toString()
}

function safeJson(text) {
  if (!text || !text.trim()) return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function parseParamDocs(items) {
  const map = {}
  for (const line of items || []) {
    const text = String(line || '').trim()
    const m = text.match(/^(Path|Query|Header|Body)\.([A-Za-z0-9_:-]+)\s*(.*)$/i)
    if (m) {
      map[`${m[1].toLowerCase()}.${m[2]}`] = (m[3] || '').trim()
    }
  }
  return map
}

function buildFieldRows(endpoint, st, docMap) {
  const rows = []
  Object.keys(st.pathParams || {}).forEach((k) => {
    rows.push({ location: 'Path', name: k, required: 'yes', example: String(st.pathParams[k] ?? ''), desc: docMap[`path.${k}`] || '' })
  })
  Object.keys(st.queryParams || {}).forEach((k) => {
    rows.push({ location: 'Query', name: k, required: 'no', example: String(st.queryParams[k] ?? ''), desc: docMap[`query.${k}`] || '' })
  })
  Object.keys(st.headers || {}).forEach((k) => {
    rows.push({ location: 'Header', name: k, required: k.toLowerCase() === 'authorization' ? 'yes' : 'no', example: String(st.headers[k] ?? ''), desc: docMap[`header.${k}`] || '' })
  })
  if (endpoint.method !== 'GET' && endpoint.method !== 'DELETE') {
    const body = safeJson(st.bodyText)
    if (body && typeof body === 'object' && !Array.isArray(body)) {
      Object.keys(body).forEach((k) => {
        rows.push({ location: 'Body', name: k, required: 'depends', example: typeof body[k] === 'object' ? JSON.stringify(body[k]) : String(body[k] ?? ''), desc: docMap[`body.${k}`] || '' })
      })
    }
  }
  return rows
}

async function invokeEndpoint() {
  const endpoint = selectedEndpoint.value
  const st = selectedState.value
  if (!endpoint || !st) return

  result.loading = true
  result.error = ''
  result.responseBody = ''
  result.responseHeaders = ''
  result.status = ''

  const headers = {
    ...st.headers,
    'X-Tenant-Id': globalConfig.tenantId,
    'X-Actor-Id': globalConfig.actorId,
    'X-Source-Service': globalConfig.sourceService,
  }
  if (endpoint.baseKey === 'controlPlaneBaseUrl' && endpoint.path.startsWith('/api/v1/admin/')) {
    await ensureControlPlaneToken()
  }
  if (endpoint.path.startsWith('/api/v1/admin/')) {
    const token = endpoint.baseKey === 'controlPlaneBaseUrl'
      ? (globalConfig.controlPlaneToken || globalConfig.adminToken)
      : globalConfig.adminToken
    headers.Authorization = `Bearer ${token}`
  }

  if (globalConfig.traceId?.trim()) {
    headers['X-Trace-Id'] = globalConfig.traceId.trim()
  }

  const url = buildUrl(endpoint, st)
  const options = {
    method: endpoint.method,
    headers,
  }

  if (endpoint.method !== 'GET' && endpoint.method !== 'DELETE') {
    headers['Content-Type'] = 'application/json'
    if (st.bodyText.trim()) {
      options.body = st.bodyText
    }
  }

  result.requestPreview = JSON.stringify({ url, ...options }, null, 2)

  const startedAt = performance.now()
  try {
    const response = await fetch(url, options)
    result.durationMs = Math.round(performance.now() - startedAt)
    result.status = `${response.status} ${response.statusText}`

    const headerObj = {}
    response.headers.forEach((value, key) => {
      headerObj[key] = value
    })
    result.responseHeaders = JSON.stringify(headerObj, null, 2)

    const contentType = response.headers.get('content-type') || ''
    if (contentType.includes('application/json')) {
      const json = await response.json()
      result.responseBody = JSON.stringify(json, null, 2)
    } else {
      result.responseBody = await response.text()
    }
  } catch (err) {
    result.durationMs = Math.round(performance.now() - startedAt)
    result.error = err instanceof Error ? err.message : String(err)
  } finally {
    result.loading = false
  }
}

function resetEndpoint() {
  const endpoint = selectedEndpoint.value
  if (!endpoint) return
  state[endpoint.id] = {
    pathParams: { ...endpoint.pathParams },
    queryParams: { ...endpoint.queryParams },
    headers: { ...endpoint.headers },
    bodyText: endpoint.body === null ? '' : JSON.stringify(endpoint.body, null, 2),
  }
}

function dbvizHeaders() {
  const token = dbviz.source === 'cp-postgres'
    ? (globalConfig.controlPlaneToken || globalConfig.adminToken)
    : globalConfig.adminToken
  return {
    Authorization: `Bearer ${token}`,
    'X-Tenant-Id': globalConfig.tenantId,
    'X-Actor-Id': globalConfig.actorId,
    'X-Source-Service': globalConfig.sourceService,
    ...(globalConfig.traceId?.trim() ? { 'X-Trace-Id': globalConfig.traceId.trim() } : {}),
  }
}

async function fetchJsonByBase(baseUrl, path, query = null) {
  const base = baseUrl.trim().replace(/\/$/, '')
  const url = new URL(`${base}${path}`)
  if (query) {
    Object.entries(query).forEach(([k, v]) => {
      if (v !== '' && v !== null && v !== undefined) {
        url.searchParams.set(k, String(v))
      }
    })
  }
  const response = await fetch(url.toString(), { headers: dbvizHeaders() })
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} (${path})`)
  }
  return response.json()
}

async function fetchJson(path, query = null) {
  return fetchJsonByBase(globalConfig.executionBaseUrl, path, query)
}

async function fetchJsonControlPlane(path, query = null) {
  await ensureControlPlaneToken()
  return fetchJsonByBase(globalConfig.controlPlaneBaseUrl, path, query)
}

async function loadDbviz() {
  dbviz.loading = true
  dbviz.error = ''
  try {
    if (dbviz.source === 'cp-postgres') {
      const scopeQuery = { tenant_scope: dbviz.tenantScope }
      const [cpOverviewResp, cpTablesResp] = await Promise.all([
        fetchJsonControlPlane('/api/v1/admin/dbviz/overview', scopeQuery),
        fetchJsonControlPlane('/api/v1/admin/dbviz/postgres/tables', scopeQuery),
      ])
      dbviz.cpOverview = cpOverviewResp
      dbviz.cpTables = cpTablesResp.items || []
      if (!dbviz.cpTable && dbviz.cpTables.length) dbviz.cpTable = dbviz.cpTables[0].table
      await loadControlPlaneRows()
      return
    }

    const scopeQuery = { tenant_scope: dbviz.tenantScope }
    const [overviewResp, pgTablesResp, qdrantResp, mongoResp] = await Promise.all([
      fetchJson('/api/v1/admin/dbviz/overview', scopeQuery),
      fetchJson('/api/v1/admin/dbviz/postgres/tables', scopeQuery),
      fetchJson('/api/v1/admin/dbviz/qdrant/collections'),
      fetchJson('/api/v1/admin/dbviz/mongo/collections'),
    ])
    dbviz.overview = overviewResp
    dbviz.pgTables = pgTablesResp.items || []
    if (!dbviz.pgTable && dbviz.pgTables.length) dbviz.pgTable = dbviz.pgTables[0].table
    dbviz.qdrantCollections = qdrantResp.items || []
    if (!dbviz.qdrantCollection && dbviz.qdrantCollections.length) dbviz.qdrantCollection = dbviz.qdrantCollections[0].name
    dbviz.mongoCollections = mongoResp.items || []
    if (!dbviz.mongoCollection && dbviz.mongoCollections.length) dbviz.mongoCollection = dbviz.mongoCollections[0].name
    if (dbviz.source === 'postgres') {
      await loadPgRows()
    } else if (dbviz.source === 'qdrant') {
      await loadQdrantPoints(true)
    } else if (dbviz.source === 'mongo') {
      await loadMongoRows()
    }
    if (dbviz.source === 'usage') {
      await loadUsage()
    }
    if (dbviz.source === 'status') {
      await loadStatus()
    }
  } catch (err) {
    dbviz.error = err instanceof Error ? err.message : String(err)
  } finally {
    dbviz.loading = false
  }
}

async function loadPgRows() {
  if (!dbviz.pgTable) return
  const resp = await fetchJson(`/api/v1/admin/dbviz/postgres/table/${encodeURIComponent(dbviz.pgTable)}`, {
    tenant_scope: dbviz.tenantScope,
    page: dbviz.pgPage,
    page_size: dbviz.pgPageSize,
  })
  dbviz.pgRows = resp.items || []
  dbviz.pgTotal = Number(resp.total || 0)
}

async function loadControlPlaneRows() {
  if (!dbviz.cpTable) return
  const resp = await fetchJsonControlPlane(`/api/v1/admin/dbviz/postgres/table/${encodeURIComponent(dbviz.cpTable)}`, {
    tenant_scope: dbviz.tenantScope,
    page: dbviz.cpPage,
    page_size: dbviz.cpPageSize,
  })
  dbviz.cpRows = resp.items || []
  dbviz.cpTotal = Number(resp.total || 0)
}

async function loadQdrantPoints(reset = false) {
  if (!dbviz.qdrantCollection) return
  const offset = reset ? '' : dbviz.qdrantOffset
  const resp = await fetchJson(`/api/v1/admin/dbviz/qdrant/collections/${encodeURIComponent(dbviz.qdrantCollection)}/points`, {
    page_size: dbviz.qdrantPageSize,
    ...(offset ? { offset } : {}),
  })
  dbviz.qdrantPoints = resp.items || []
  dbviz.qdrantNextOffset = resp.next_offset ?? null
  dbviz.qdrantOffset = resp.next_offset ? String(resp.next_offset) : ''
  dbviz.qdrantDetail = null
  dbviz.qdrantDetailError = ''
}

async function loadQdrantPointDetail(pointId, fullVector = false) {
  if (!dbviz.qdrantCollection || !pointId) return
  dbviz.qdrantDetailLoading = true
  dbviz.qdrantDetailError = ''
  try {
    const resp = await fetchJson(
      `/api/v1/admin/dbviz/qdrant/collections/${encodeURIComponent(dbviz.qdrantCollection)}/points/${encodeURIComponent(String(pointId))}`,
      { vector_preview_dims: 32, full_vector: fullVector ? 'true' : 'false' },
    )
    dbviz.qdrantDetail = resp.item || null
  } catch (err) {
    dbviz.qdrantDetailError = err instanceof Error ? err.message : String(err)
    dbviz.qdrantDetail = null
  } finally {
    dbviz.qdrantDetailLoading = false
  }
}

async function loadMongoRows() {
  if (!dbviz.mongoCollection) {
    dbviz.mongoRows = []
    dbviz.mongoTotal = 0
    return
  }
  const resp = await fetchJson(`/api/v1/admin/dbviz/mongo/collection/${encodeURIComponent(dbviz.mongoCollection)}`, {
    page: dbviz.mongoPage,
    page_size: dbviz.mongoPageSize,
  })
  dbviz.mongoRows = resp.items || []
  dbviz.mongoTotal = Number(resp.total || 0)
}

async function loadUsageSummary() {
  const resp = await fetchJson('/api/v1/admin/usage/api-tokens/summary', {
    tenant_scope: dbviz.tenantScope,
    days: dbviz.usageDays,
    limit: dbviz.usageLimit,
  })
  dbviz.usageSummary = resp
}

async function loadUsageEvents() {
  const resp = await fetchJson('/api/v1/admin/usage/api-tokens/events', {
    tenant_scope: dbviz.tenantScope,
    days: dbviz.usageDays,
    page: dbviz.usagePage,
    page_size: dbviz.usagePageSize,
    ...(dbviz.usageApiPath ? { api_path: dbviz.usageApiPath } : {}),
    ...(dbviz.usageModel ? { model: dbviz.usageModel } : {}),
    ...(dbviz.usageApiKeyFingerprint ? { api_key_fingerprint: dbviz.usageApiKeyFingerprint } : {}),
    ...(dbviz.usageTaskCategory ? { task_category: dbviz.usageTaskCategory } : {}),
  })
  dbviz.usageEvents = resp.items || []
  dbviz.usageTotal = Number(resp.total || 0)
}

async function loadUsage() {
  await Promise.all([loadUsageSummary(), loadUsageEvents(), loadUsageBudgets(), loadUsageAlerts()])
}

async function loadUsageBudgets() {
  const resp = await fetchJson('/api/v1/admin/usage/api-tokens/budgets')
  dbviz.usageBudgets = resp.items || []
}

async function loadUsageAlerts() {
  const resp = await fetchJson('/api/v1/admin/usage/api-tokens/alerts', {
    days: dbviz.usageAlertDays,
    status: dbviz.usageAlertStatus,
    limit: dbviz.usageAlertLimit,
  })
  dbviz.usageAlerts = resp.items || []
}

async function evaluateUsageBudgets() {
  const base = globalConfig.executionBaseUrl.trim().replace(/\/$/, '')
  const response = await fetch(`${base}/api/v1/admin/usage/api-tokens/budgets/evaluate`, {
    method: 'POST',
    headers: dbvizHeaders(),
  })
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} (/api/v1/admin/usage/api-tokens/budgets/evaluate)`)
  }
  await loadUsageAlerts()
}

async function saveUsageBudget() {
  const form = dbviz.usageBudgetForm
  const body = {
    scope_type: form.scope_type,
    scope_value: String(form.scope_value || '').trim(),
    enabled: Boolean(form.enabled),
    daily_token_limit: String(form.daily_token_limit).trim() === '' ? null : Number(form.daily_token_limit),
    daily_cost_limit: String(form.daily_cost_limit).trim() === '' ? null : Number(form.daily_cost_limit),
  }
  const base = globalConfig.executionBaseUrl.trim().replace(/\/$/, '')
  const response = await fetch(`${base}/api/v1/admin/usage/api-tokens/budgets`, {
    method: 'PUT',
    headers: {
      ...dbvizHeaders(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} (/api/v1/admin/usage/api-tokens/budgets)`)
  }
  await Promise.all([loadUsageBudgets(), loadUsageAlerts()])
}

async function deleteUsageBudget(budgetId) {
  const base = globalConfig.executionBaseUrl.trim().replace(/\/$/, '')
  const response = await fetch(`${base}/api/v1/admin/usage/api-tokens/budgets/${budgetId}`, {
    method: 'DELETE',
    headers: dbvizHeaders(),
  })
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} (/api/v1/admin/usage/api-tokens/budgets/${budgetId})`)
  }
  await Promise.all([loadUsageBudgets(), loadUsageAlerts()])
}

async function updateUsageAlertStatus(alertId, nextStatus) {
  const base = globalConfig.executionBaseUrl.trim().replace(/\/$/, '')
  const response = await fetch(`${base}/api/v1/admin/usage/api-tokens/alerts/${alertId}`, {
    method: 'PATCH',
    headers: {
      ...dbvizHeaders(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ status: nextStatus }),
  })
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} (/api/v1/admin/usage/api-tokens/alerts/${alertId})`)
  }
  await loadUsageAlerts()
}

async function loadStatus() {
  const timed = async (name, loader) => {
    const t0 = performance.now()
    try {
      const data = await loader()
      return {
        name,
        ok: true,
        durationMs: Math.round(performance.now() - t0),
        message: 'ok',
        data,
      }
    } catch (err) {
      return {
        name,
        ok: false,
        durationMs: Math.round(performance.now() - t0),
        message: err instanceof Error ? err.message : String(err),
        data: null,
      }
    }
  }

  const [kbsCheck, tasksCheck, apiPoolCheck, auxApiPoolCheck, usageCheck, chatPlaneCheck, chatStoreCheck] = await Promise.all([
    timed('知识库列表', () => fetchJson('/api/v1/admin/kbs')),
    timed('任务系统', () => fetchJson('/api/v1/admin/tasks', { limit: 200 })),
    timed('主 API 池', () => fetchJson('/api/v1/admin/api-pool')),
    timed('辅助 API 池', () => fetchJson('/api/v1/admin/aux-api-pool')),
    timed('Token 用量统计', () => fetchJson('/api/v1/admin/usage/api-tokens/summary', {
      tenant_scope: dbviz.tenantScope,
      days: 7,
      limit: 20,
    })),
    timed('Chat Plane 健康', async () => {
      const base = globalConfig.chatBaseUrl.trim().replace(/\/$/, '')
      const resp = await fetch(`${base}/api/v1/system/health`, { headers: dbvizHeaders() })
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText} (/api/v1/system/health@chat)`)
      return resp.json()
    }),
    timed('Chat Store 状态', async () => {
      const base = globalConfig.chatBaseUrl.trim().replace(/\/$/, '')
      const resp = await fetch(`${base}/api/v1/admin/chat/store/status`, { headers: dbvizHeaders() })
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText} (/api/v1/admin/chat/store/status)`)
      return resp.json()
    }),
  ])

  const checks = [kbsCheck, tasksCheck, apiPoolCheck, auxApiPoolCheck, usageCheck, chatPlaneCheck, chatStoreCheck]
  dbviz.statusChecks = checks
  dbviz.chatStoreStatus = chatStoreCheck?.data?.item || null

  const kbsItems = kbsCheck?.data?.items || []
  dbviz.statusKbs = kbsItems.length

  const taskItems = tasksCheck?.data?.items || []
  const taskStatus = (s) => String(s || '').toUpperCase()
  const taskStage = (x) => taskStatus(x?.stage)
  const isTerminal = (x) => ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(taskStatus(x?.status))
  const isCancelling = (x) => taskStatus(x?.status) === 'CANCELLING' || taskStage(x) === 'CANCELLING'
  const isRunningLike = (x) =>
    !isTerminal(x) &&
    !isCancelling(x) &&
    (
      ['PENDING', 'RUNNING'].includes(taskStatus(x?.status)) ||
      ['PARSING', 'CHUNKING', 'EMBEDDING', 'SUMMARIZING', 'SUMMARIZING_DOC', 'SUMMARIZING_KB', 'INDEXING', 'RUNNING', 'BATCH_RUNNING'].includes(taskStage(x))
    )
  dbviz.statusTasks = {
    total: taskItems.length,
    running: taskItems.filter((x) => isRunningLike(x)).length,
    cancelling: taskItems.filter((x) => isCancelling(x)).length,
    failed: taskItems.filter((x) => taskStatus(x.status) === 'FAILED').length,
    cancelled: taskItems.filter((x) => taskStatus(x.status) === 'CANCELLED').length,
    done: taskItems.filter((x) => taskStatus(x.status) === 'DONE').length,
  }
  dbviz.statusTaskItems = taskItems.filter((x) => isRunningLike(x)).slice(0, 20)
  dbviz.statusLastUpdated = new Date().toLocaleString()
  const runtimeResp = await fetchJson('/api/v1/admin/system/runtime-config')
  const cfg = runtimeResp?.item || {}
  dbviz.runtimeConfig = cfg
  dbviz.runtimeConfigDraft = { ...cfg }
  dbviz.runtimeConfigKeys = runtimeResp?.allowed_keys || Object.keys(cfg)
  dbviz.runtimeConfigDirty = false
}

async function flushChatStore() {
  const base = globalConfig.chatBaseUrl.trim().replace(/\/$/, '')
  const response = await fetch(`${base}/api/v1/admin/chat/store/flush`, {
    method: 'POST',
    headers: dbvizHeaders(),
  })
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} (/api/v1/admin/chat/store/flush)`)
  }
  await loadStatus()
}

let statusTimer = null
function clearStatusTimer() {
  if (statusTimer) {
    clearInterval(statusTimer)
    statusTimer = null
  }
}
function setupStatusTimer() {
  clearStatusTimer()
  if (!dbviz.statusAutoRefresh || selectedId.value !== '__dbviz__' || dbviz.source !== 'status') return
  const sec = Math.max(2, Number(dbviz.statusRefreshSeconds || 5))
  statusTimer = setInterval(async () => {
    try {
      await loadStatus()
    } catch {
      // keep silent in polling loop; error is shown by explicit refresh path
    }
  }, sec * 1000)
}

async function saveRuntimeConfig() {
  const updates = {}
  for (const key of Object.keys(dbviz.runtimeConfigDraft || {})) {
    if (JSON.stringify(dbviz.runtimeConfigDraft[key]) !== JSON.stringify(dbviz.runtimeConfig[key])) {
      updates[key] = dbviz.runtimeConfigDraft[key]
    }
  }
  if (!Object.keys(updates).length) return
  const base = globalConfig.executionBaseUrl.trim().replace(/\/$/, '')
  const response = await fetch(`${base}/api/v1/admin/system/runtime-config`, {
    method: 'PATCH',
    headers: {
      ...dbvizHeaders(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ updates }),
  })
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} (/api/v1/admin/system/runtime-config)`)
  }
  await loadStatus()
}

function updateRuntimeConfigField(key, rawValue, originalValue) {
  let value = rawValue
  if (typeof originalValue === 'number') {
    const n = Number(rawValue)
    value = Number.isFinite(n) ? n : originalValue
  } else if (typeof originalValue === 'boolean') {
    value = String(rawValue).toLowerCase() === 'true'
  }
  dbviz.runtimeConfigDraft[key] = value
  dbviz.runtimeConfigDirty = Object.keys(dbviz.runtimeConfigDraft || {}).some(
    (k) => JSON.stringify(dbviz.runtimeConfigDraft[k]) !== JSON.stringify(dbviz.runtimeConfig[k]),
  )
}

async function loginAdminToken() {
  dbviz.error = ''
  try {
    const base = globalConfig.executionBaseUrl.trim().replace(/\/$/, '')
    const response = await fetch(`${base}/api/v1/admin/auth/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Tenant-Id': globalConfig.tenantId,
        'X-Actor-Id': globalConfig.actorId,
        'X-Source-Service': globalConfig.sourceService,
        ...(globalConfig.traceId?.trim() ? { 'X-Trace-Id': globalConfig.traceId.trim() } : {}),
      },
      body: JSON.stringify({
        username: globalConfig.adminUsername,
        password: globalConfig.adminPassword,
      }),
    })
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText} (/api/v1/admin/auth/login)`)
    }
    const data = await response.json()
    if (!data?.access_token) {
      throw new Error('Login success but access_token is empty')
    }
    globalConfig.adminToken = data.access_token
  } catch (err) {
    dbviz.error = err instanceof Error ? err.message : String(err)
  }
}

async function doControlPlaneLogin() {
  const base = globalConfig.controlPlaneBaseUrl.trim().replace(/\/$/, '')
  const response = await fetch(`${base}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      tenantCode: globalConfig.tenantId || null,
      username: globalConfig.adminUsername,
      password: globalConfig.adminPassword,
    }),
  })
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText} (/api/v1/auth/login@control-plane)`)
  }
  const data = await response.json()
  if (!data?.accessToken) {
    throw new Error('Control-plane login success but accessToken is empty')
  }
  globalConfig.controlPlaneToken = data.accessToken
}

async function ensureControlPlaneToken() {
  if (isLikelySignedToken(globalConfig.controlPlaneToken)) return
  await doControlPlaneLogin()
}

async function loginControlPlaneToken() {
  dbviz.error = ''
  try {
    await doControlPlaneLogin()
  } catch (err) {
    dbviz.error = err instanceof Error ? err.message : String(err)
  }
}

async function loginAndEnter() {
  auth.error = ''
  auth.loggingIn = true
  try {
    const base = globalConfig.executionBaseUrl.trim().replace(/\/$/, '')
    const response = await fetch(`${base}/api/v1/admin/auth/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Tenant-Id': globalConfig.tenantId,
        'X-Actor-Id': globalConfig.actorId,
        'X-Source-Service': globalConfig.sourceService,
        ...(globalConfig.traceId?.trim() ? { 'X-Trace-Id': globalConfig.traceId.trim() } : {}),
      },
      body: JSON.stringify({
        username: globalConfig.adminUsername,
        password: globalConfig.adminPassword,
      }),
    })
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText} (/api/v1/admin/auth/login)`)
    }
    const data = await response.json()
    if (!data?.access_token) {
      throw new Error('Login success but access_token is empty')
    }
    globalConfig.adminToken = data.access_token
    await loginControlPlaneToken()
    auth.loggedIn = true
    if (selectedId.value === '__dbviz__') {
      await loadDbviz()
    }
  } catch (err) {
    auth.error = err instanceof Error ? err.message : String(err)
  } finally {
    auth.loggingIn = false
  }
}

function logoutAndBackToLogin() {
  auth.loggedIn = false
}

watch(
  () => selectedId.value,
  async (v) => {
    if (v === '__dbviz__') {
      await loadDbviz()
    }
  },
)

watch(
  () => dbviz.source,
  async (v) => {
    if (selectedId.value !== '__dbviz__') return
    if (v === 'usage' || v === 'status' || v === 'cp-postgres') {
      if (v === 'cp-postgres' && dbviz.tenantScope === 'current') {
        dbviz.tenantScope = 'all'
      }
      dbviz.loading = true
      dbviz.error = ''
      try {
        if (v === 'usage') {
          await loadUsage()
        } else if (v === 'cp-postgres') {
          await loadDbviz()
        } else {
          await loadStatus()
        }
      } catch (err) {
        dbviz.error = err instanceof Error ? err.message : String(err)
      } finally {
        dbviz.loading = false
      }
    }
    setupStatusTimer()
  },
)

watch(() => dbviz.statusAutoRefresh, () => setupStatusTimer())
watch(() => dbviz.statusRefreshSeconds, () => setupStatusTimer())
watch(() => selectedId.value, () => setupStatusTimer())
onBeforeUnmount(() => clearStatusTimer())
</script>

<template>
  <div class="login-shell" v-if="!auth.loggedIn">
    <section class="login-card panel">
      <h2>Admin Login</h2>
      <p class="subtitle">登录后自动写入 Admin Token，用于所有 `/api/v1/admin/*` 接口调试。</p>
      <div class="grid two">
        <label>
          Control Plane URL
          <input v-model="globalConfig.controlPlaneBaseUrl" />
        </label>
        <label>
          Execution URL
          <input v-model="globalConfig.executionBaseUrl" />
        </label>
        <label>
          X-Tenant-Id
          <input v-model="globalConfig.tenantId" />
        </label>
        <label>
          Admin Username
          <input v-model="globalConfig.adminUsername" />
        </label>
        <label>
          Admin Password
          <input type="password" v-model="globalConfig.adminPassword" @keyup.enter="loginAndEnter" />
        </label>
      </div>
      <div class="actions">
        <button class="primary" @click="loginAndEnter" :disabled="auth.loggingIn">
          {{ auth.loggingIn ? 'Logging in...' : 'Login' }}
        </button>
      </div>
      <p v-if="auth.error" class="error-text">{{ auth.error }}</p>
    </section>
  </div>

  <div class="layout" v-else>
    <aside class="sidebar">
      <h1 class="brand">Debug Web</h1>
      <p class="subtitle">Agent Powflow API Console</p>
      <div class="actions">
        <button class="ghost" @click="logoutAndBackToLogin">退出登录</button>
      </div>
      <button
        class="menu-item dbviz-entry"
        :class="{ active: selectedId === '__dbviz__' }"
        @click="selectedId = '__dbviz__'"
      >
        <span class="method method-get">VIEW</span>
        <span class="name">数据库可视化</span>
      </button>
      <div v-for="group in endpointGroups" :key="group.group" class="menu-group">
        <h2>{{ group.group }}</h2>
        <p class="group-desc" v-if="group.description">{{ group.description }}</p>
        <button
          v-for="ep in group.endpoints"
          :key="ep.id"
          class="menu-item"
          :class="{ active: selectedId === ep.id }"
          @click="selectedId = ep.id"
        >
          <span class="method" :class="methodClass(ep.method)">{{ ep.method }}</span>
          <span class="name">{{ ep.name }}</span>
        </button>
      </div>
    </aside>

    <main class="main" v-if="selectedId === '__dbviz__'">
      <section class="panel config">
        <h2>Database Visualizer</h2>
        <div class="grid four">
          <label>
            Execution URL
            <input v-model="globalConfig.executionBaseUrl" />
          </label>
          <label>
            Control Plane URL
            <input v-model="globalConfig.controlPlaneBaseUrl" />
          </label>
          <label>
            Chat Plane URL
            <input v-model="globalConfig.chatBaseUrl" />
          </label>
          <label>
            Execution Admin Token
            <input v-model="globalConfig.adminToken" />
          </label>
          <label>
            Control Plane Token
            <input v-model="globalConfig.controlPlaneToken" />
          </label>
          <label>
            Admin Username
            <input v-model="globalConfig.adminUsername" />
          </label>
          <label>
            Admin Password
            <input type="password" v-model="globalConfig.adminPassword" />
          </label>
          <label>
            X-Tenant-Id
            <input v-model="globalConfig.tenantId" />
          </label>
          <label>
            X-Actor-Id
            <input v-model="globalConfig.actorId" />
          </label>
          <label>
            Tenant Scope
            <select v-model="dbviz.tenantScope">
              <option value="current">current</option>
              <option value="all">all</option>
            </select>
          </label>
          <label>
            数据源
            <select v-model="dbviz.source">
              <option value="status">系统状态</option>
              <option value="postgres">PostgreSQL</option>
              <option value="cp-postgres">ControlPlane PostgreSQL</option>
              <option value="qdrant">Qdrant</option>
              <option value="mongo">MongoDB</option>
              <option value="usage">API Token Usage</option>
            </select>
          </label>
        </div>
        <div class="actions">
          <button class="ghost" @click="loginAdminToken">获取 Execution JWT</button>
          <button class="ghost" @click="loginControlPlaneToken">获取 Control-Plane JWT</button>
          <button class="primary" @click="loadDbviz" :disabled="dbviz.loading">{{ dbviz.loading ? 'Loading...' : 'Refresh' }}</button>
        </div>
        <p v-if="dbviz.error" class="error-text">{{ dbviz.error }}</p>
      </section>

      <section class="panel" v-if="dbviz.source !== 'cp-postgres'">
        <div class="dbviz-cards">
          <div class="dbviz-card">
            <strong>PG documents</strong>
            <span>{{ dbviz.overview?.postgres?.tables?.documents || 0 }}</span>
          </div>
          <div class="dbviz-card">
            <strong>PG chunks</strong>
            <span>{{ dbviz.overview?.postgres?.tables?.chunks || 0 }}</span>
          </div>
          <div class="dbviz-card">
            <strong>PG tasks</strong>
            <span>{{ dbviz.overview?.postgres?.tables?.tasks || 0 }}</span>
          </div>
          <div class="dbviz-card">
            <strong>Qdrant collections</strong>
            <span>{{ dbviz.overview?.qdrant?.collections || 0 }}</span>
          </div>
        </div>
      </section>

      <section class="panel" v-if="dbviz.source === 'cp-postgres'">
        <div class="dbviz-cards">
          <div class="dbviz-card">
            <strong>Tenant Scope</strong>
            <span>{{ dbviz.tenantScope }}</span>
          </div>
          <div class="dbviz-card" v-for="item in (dbviz.cpOverview?.items || [])" :key="`cp-overview-${item.table}`">
            <strong>{{ item.table }}</strong>
            <span>{{ item.rows || 0 }}</span>
          </div>
        </div>
      </section>

      <section class="panel" v-if="dbviz.source === 'status'">
        <div class="endpoint-head">
          <h2>知识库系统状态测试</h2>
          <p class="subtitle">last_updated={{ dbviz.statusLastUpdated || '-' }}</p>
        </div>

        <div class="dbviz-cards">
          <div class="dbviz-card">
            <strong>KB 数量</strong>
            <span>{{ dbviz.statusKbs || 0 }}</span>
          </div>
          <div class="dbviz-card">
            <strong>任务总数</strong>
            <span>{{ dbviz.statusTasks?.total || 0 }}</span>
          </div>
          <div class="dbviz-card">
            <strong>运行中任务</strong>
            <span>{{ dbviz.statusTasks?.running || 0 }}</span>
          </div>
          <div class="dbviz-card">
            <strong>失败/取消中/已取消</strong>
            <span>{{ (dbviz.statusTasks?.failed || 0) + (dbviz.statusTasks?.cancelling || 0) + (dbviz.statusTasks?.cancelled || 0) }}</span>
          </div>
          <div class="dbviz-card">
            <strong>Chat Store 后端</strong>
            <span>{{ dbviz.chatStoreStatus?.persist_backend || '-' }}</span>
          </div>
          <div class="dbviz-card">
            <strong>Chat Store 脏标记</strong>
            <span>{{ dbviz.chatStoreStatus?.disk_dirty ? 'DIRTY' : 'CLEAN' }}</span>
          </div>
          <div class="dbviz-card">
            <strong>Chat Store 写入</strong>
            <span>disk {{ dbviz.chatStoreStatus?.disk_write_count || 0 }} / pg {{ dbviz.chatStoreStatus?.pg_write_count || 0 }}</span>
          </div>
        </div>

        <div class="actions" style="margin: 10px 0 6px;">
          <label style="display:flex;align-items:center;gap:6px;">
            <input type="checkbox" v-model="dbviz.statusAutoRefresh" />
            自动刷新
          </label>
          <label style="display:flex;align-items:center;gap:6px;">
            间隔(秒)
            <input type="number" min="2" max="60" v-model.number="dbviz.statusRefreshSeconds" style="width:78px;" />
          </label>
        </div>

        <div class="table-wrap">
          <table class="db-table">
            <thead>
              <tr>
                <th>检查项</th>
                <th>状态</th>
                <th>耗时(ms)</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in dbviz.statusChecks" :key="item.name">
                <td>{{ item.name }}</td>
                <td>
                  <span class="badge" :class="{ ok: item.ok, error: !item.ok }">{{ item.ok ? 'OK' : 'FAIL' }}</span>
                </td>
                <td class="mono">{{ item.durationMs }}</td>
                <td class="mono">
                  <span class="cell-ellipsis" :title="item.message">{{ item.message }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div class="panel-sub">
          <div class="endpoint-head">
            <h3>Chat Store 持久化状态</h3>
            <div class="actions">
              <button class="ghost" @click="loadStatus">刷新</button>
              <button class="primary" @click="flushChatStore">强制 Flush</button>
            </div>
          </div>
          <div class="table-wrap">
            <table class="db-table">
              <thead>
                <tr>
                  <th>字段</th>
                  <th>值</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(value, key) in (dbviz.chatStoreStatus || {})" :key="`chat-store-${key}`">
                  <td class="mono">{{ key }}</td>
                  <td class="mono">
                    <span class="cell-ellipsis" :title="typeof value === 'object' ? JSON.stringify(value) : String(value)">
                      {{ typeof value === 'object' ? JSON.stringify(value) : value }}
                    </span>
                  </td>
                </tr>
                <tr v-if="!dbviz.chatStoreStatus">
                  <td colspan="2" class="mono">无数据</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="panel-sub">
          <h3>运行中任务明细（最多 20 条）</h3>
          <div class="table-wrap">
            <table class="db-table">
              <thead>
                <tr>
                  <th>文件</th>
                  <th>任务类型</th>
                  <th>状态</th>
                  <th>阶段</th>
                  <th>进度</th>
                  <th>更新时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in dbviz.statusTaskItems" :key="item.task_id">
                  <td><span class="cell-ellipsis" :title="String(item.filename || item.doc_id || item.task_id)">{{ item.filename || item.doc_id || item.task_id }}</span></td>
                  <td class="mono">{{ item.task_type }}</td>
                  <td class="mono">{{ item.status }}</td>
                  <td class="mono">{{ item.stage }}</td>
                  <td class="mono">{{ item.progress_current || 0 }}/{{ item.progress_total || 0 }} ({{ item.progress_pct || 0 }}%)</td>
                  <td class="mono"><span class="cell-ellipsis" :title="String(item.updated_at || '')">{{ item.updated_at }}</span></td>
                </tr>
                <tr v-if="!dbviz.statusTaskItems.length">
                  <td colspan="6" class="mono">暂无运行中任务（请确认 X-Tenant-Id 是否与任务所属租户一致）</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="panel-sub">
          <div class="endpoint-head">
            <h3>可配置参数可视化（runtime-config）</h3>
            <div class="actions">
              <span
                v-for="group in runtimeConfigGroupedKeys"
                :key="`cfg-badge-${group.name}`"
                class="badge"
                style="margin-right:6px;"
              >
                {{ group.name }} · {{ group.keys.length }}
              </span>
            </div>
            <div class="actions">
              <button class="ghost" @click="dbviz.runtimeConfigDraft = { ...dbviz.runtimeConfig }; dbviz.runtimeConfigDirty = false">重置</button>
              <button class="primary" :disabled="!dbviz.runtimeConfigDirty" @click="saveRuntimeConfig">保存参数</button>
            </div>
          </div>
          <div class="table-wrap">
            <table class="db-table">
              <thead>
                <tr>
                  <th>分组</th>
                  <th>参数名</th>
                  <th>当前值</th>
                  <th>编辑值</th>
                </tr>
              </thead>
              <tbody>
                <template v-for="group in runtimeConfigGroupedKeys" :key="`cfg-group-${group.name}`">
                  <tr class="group-row">
                    <td colspan="4"><strong>{{ group.name }}</strong></td>
                  </tr>
                  <tr v-for="key in group.keys" :key="`cfg-${group.name}-${key}`">
                    <td class="mono">{{ group.name }}</td>
                    <td class="mono">{{ key }}</td>
                    <td class="mono">
                      <span class="cell-ellipsis" :title="String(dbviz.runtimeConfig[key] ?? '')">{{ dbviz.runtimeConfig[key] }}</span>
                    </td>
                    <td class="mono">
                      <template v-if="typeof dbviz.runtimeConfig[key] === 'boolean'">
                        <select
                          :value="String(dbviz.runtimeConfigDraft[key])"
                          @change="updateRuntimeConfigField(key, $event.target.value, dbviz.runtimeConfig[key])"
                        >
                          <option value="true">true</option>
                          <option value="false">false</option>
                        </select>
                      </template>
                      <template v-else-if="typeof dbviz.runtimeConfig[key] === 'number'">
                        <input
                          type="number"
                          :value="dbviz.runtimeConfigDraft[key]"
                          @input="updateRuntimeConfigField(key, $event.target.value, dbviz.runtimeConfig[key])"
                        />
                      </template>
                      <template v-else>
                        <input
                          :value="dbviz.runtimeConfigDraft[key]"
                          @input="updateRuntimeConfigField(key, $event.target.value, dbviz.runtimeConfig[key])"
                        />
                      </template>
                    </td>
                  </tr>
                </template>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section class="panel" v-if="dbviz.source === 'postgres'">
        <div class="endpoint-head">
          <h2>PostgreSQL</h2>
          <label class="inline-select">
            Table
            <select v-model="dbviz.pgTable" @change="dbviz.pgPage = 1; loadPgRows()">
              <option v-for="item in dbviz.pgTables" :key="item.table" :value="item.table">
                {{ item.table }} ({{ item.row_count }})
              </option>
            </select>
          </label>
          <div class="actions">
            <button class="ghost" @click="dbviz.pgPage = Math.max(1, dbviz.pgPage - 1); loadPgRows()">Prev</button>
            <button class="ghost" @click="dbviz.pgPage += 1; loadPgRows()">Next</button>
          </div>
        </div>
        <p class="subtitle">page={{ dbviz.pgPage }}/{{ pgTotalPages }}, size={{ dbviz.pgPageSize }}, total={{ dbviz.pgTotal }}</p>
        <div class="table-wrap">
          <table class="db-table">
            <thead>
              <tr>
                <th v-for="col in (dbviz.pgRows[0] ? Object.keys(dbviz.pgRows[0]) : [])" :key="col">{{ col }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, idx) in dbviz.pgRows" :key="idx">
                <td v-for="col in Object.keys(row)" :key="col" class="mono">
                  <span
                    class="cell-ellipsis"
                    :title="typeof row[col] === 'object' ? JSON.stringify(row[col]) : String(row[col] ?? '')"
                  >
                    {{ typeof row[col] === 'object' ? JSON.stringify(row[col]) : row[col] }}
                  </span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel" v-if="dbviz.source === 'cp-postgres'">
        <div class="endpoint-head">
          <h2>Control-Plane PostgreSQL</h2>
          <label class="inline-select">
            Table
            <select v-model="dbviz.cpTable" @change="dbviz.cpPage = 1; loadControlPlaneRows()">
              <option v-for="item in dbviz.cpTables" :key="item.table" :value="item.table">
                {{ item.table }} ({{ item.rows }})
              </option>
            </select>
          </label>
          <div class="actions">
            <button class="ghost" @click="dbviz.cpPage = Math.max(1, dbviz.cpPage - 1); loadControlPlaneRows()">Prev</button>
            <button class="ghost" @click="dbviz.cpPage += 1; loadControlPlaneRows()">Next</button>
          </div>
        </div>
        <p class="subtitle">page={{ dbviz.cpPage }}/{{ cpTotalPages }}, size={{ dbviz.cpPageSize }}, total={{ dbviz.cpTotal }}</p>
        <div class="table-wrap">
          <table class="db-table">
            <thead>
              <tr>
                <th v-for="col in (dbviz.cpRows[0] ? Object.keys(dbviz.cpRows[0]) : [])" :key="col">{{ col }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, idx) in dbviz.cpRows" :key="`cp-row-${idx}`">
                <td v-for="(value, col) in row" :key="`cp-cell-${idx}-${col}`" class="mono">
                  <span class="cell-ellipsis" :title="String(value ?? '')">{{ value }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel" v-if="dbviz.source === 'qdrant'">
        <div class="endpoint-head">
          <h2>Qdrant</h2>
          <label class="inline-select">
            Collection
            <select v-model="dbviz.qdrantCollection" @change="dbviz.qdrantOffset = ''; loadQdrantPoints(true)">
              <option v-for="c in dbviz.qdrantCollections" :key="c.name" :value="c.name">
                {{ c.name }} ({{ c.points_count }})
              </option>
            </select>
          </label>
          <div class="actions">
            <button class="ghost" @click="loadQdrantPoints(true)">First</button>
            <button class="ghost" @click="loadQdrantPoints(false)">Next</button>
          </div>
        </div>
        <p class="subtitle">page_size={{ dbviz.qdrantPageSize }}, next_offset={{ dbviz.qdrantNextOffset ?? 'null' }}</p>
        <div class="table-wrap">
          <table class="db-table">
            <thead>
              <tr><th>ID</th><th>tenant</th><th>kb</th><th>doc</th><th>chunk</th><th>text_preview</th></tr>
            </thead>
            <tbody>
              <tr v-for="p in dbviz.qdrantPoints" :key="String(p.id)">
                <td class="mono"><span class="cell-ellipsis" :title="String(p.id ?? '')">{{ p.id }}</span></td>
                <td><span class="cell-ellipsis" :title="String(p.tenant_id ?? '')">{{ p.tenant_id }}</span></td>
                <td class="mono"><span class="cell-ellipsis" :title="String(p.kb_id ?? '')">{{ p.kb_id }}</span></td>
                <td class="mono"><span class="cell-ellipsis" :title="String(p.doc_id ?? '')">{{ p.doc_id }}</span></td>
                <td class="mono"><span class="cell-ellipsis" :title="String(p.chunk_id ?? '')">{{ p.chunk_id }}</span></td>
                <td>
                  <button class="text-link" @click="loadQdrantPointDetail(p.id)" :title="String(p.text_preview ?? '')">
                    <span class="cell-ellipsis">{{ p.text_preview }}</span>
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="panel-sub" v-if="dbviz.qdrantDetailLoading || dbviz.qdrantDetail || dbviz.qdrantDetailError">
          <h3>Point 详情（按需加载）</h3>
          <p v-if="dbviz.qdrantDetailLoading" class="mono">Loading point detail...</p>
          <p v-else-if="dbviz.qdrantDetailError" class="error-text">{{ dbviz.qdrantDetailError }}</p>
          <template v-else-if="dbviz.qdrantDetail">
            <p class="mono">id: {{ dbviz.qdrantDetail.id }} · dim: {{ dbviz.qdrantDetail.vector_dim }}</p>
            <label class="json-editor">
              Payload
              <textarea :value="JSON.stringify(dbviz.qdrantDetail.payload, null, 2)" rows="8" readonly></textarea>
            </label>
            <label class="json-editor">
              <div class="vector-title-row">
                <span>Vector Preview</span>
                <button
                  v-if="!dbviz.qdrantDetail.vector_is_full && (dbviz.qdrantDetail.vector_dim || 0) > (dbviz.qdrantDetail.vector_preview || []).length"
                  class="ghost"
                  @click="loadQdrantPointDetail(dbviz.qdrantDetail.id, true)"
                  :disabled="dbviz.qdrantDetailLoading"
                >
                  查看完整向量
                </button>
              </div>
              <div class="vector-inline mono" :title="JSON.stringify(dbviz.qdrantDetail.vector_preview || [])">
                {{ JSON.stringify(dbviz.qdrantDetail.vector_preview || []) }}
              </div>
            </label>
          </template>
        </div>
      </section>

      <section class="panel" v-if="dbviz.source === 'mongo'">
        <div class="endpoint-head">
          <h2>MongoDB</h2>
          <label class="inline-select">
            Collection
            <select v-model="dbviz.mongoCollection" @change="dbviz.mongoPage = 1; loadMongoRows()">
              <option v-for="c in dbviz.mongoCollections" :key="c.name" :value="c.name">
                {{ c.name }} ({{ c.count }})
              </option>
            </select>
          </label>
          <div class="actions">
            <button class="ghost" @click="dbviz.mongoPage = Math.max(1, dbviz.mongoPage - 1); loadMongoRows()">Prev</button>
            <button class="ghost" @click="dbviz.mongoPage += 1; loadMongoRows()">Next</button>
          </div>
        </div>
        <p class="subtitle">page={{ dbviz.mongoPage }}/{{ mongoTotalPages }}, size={{ dbviz.mongoPageSize }}, total={{ dbviz.mongoTotal }}</p>
        <div class="table-wrap">
          <table class="db-table">
            <thead>
              <tr>
                <th v-for="col in (dbviz.mongoRows[0] ? Object.keys(dbviz.mongoRows[0]) : [])" :key="col">{{ col }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, idx) in dbviz.mongoRows" :key="idx">
                <td v-for="col in Object.keys(row)" :key="col" class="mono">
                  <span class="cell-ellipsis" :title="String(row[col] ?? '')">{{ row[col] }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel" v-if="dbviz.source === 'usage'">
        <div class="endpoint-head">
          <h2>API Token Usage (30d)</h2>
          <div class="actions">
            <button class="ghost" @click="dbviz.usagePage = 1; loadUsageEvents()">刷新明细</button>
            <button class="ghost" @click="evaluateUsageBudgets">触发预算评估</button>
            <button class="primary" @click="dbviz.usagePage = 1; loadUsage()">刷新全部</button>
          </div>
        </div>
        <div class="grid four">
          <label>
            Days
            <input type="number" min="1" max="30" v-model.number="dbviz.usageDays" />
          </label>
          <label>
            Summary Limit
            <input type="number" min="20" max="1000" v-model.number="dbviz.usageLimit" />
          </label>
          <label>
            Event Page Size
            <input type="number" min="10" max="200" v-model.number="dbviz.usagePageSize" />
          </label>
          <label>
            Task Category
            <input v-model="dbviz.usageTaskCategory" placeholder="doc_embedding / summary_llm ..." />
          </label>
          <label>
            API Path
            <input v-model="dbviz.usageApiPath" placeholder="/embeddings" />
          </label>
          <label>
            Model
            <input v-model="dbviz.usageModel" placeholder="BAAI/bge-m3" />
          </label>
          <label>
            API Key Fingerprint
            <input v-model="dbviz.usageApiKeyFingerprint" placeholder="12-hex fingerprint" />
          </label>
          <label>
            Alert Days
            <input type="number" min="1" max="30" v-model.number="dbviz.usageAlertDays" />
          </label>
          <label>
            Alert Status
            <select v-model="dbviz.usageAlertStatus">
              <option value="OPEN">OPEN</option>
              <option value="RESOLVED">RESOLVED</option>
              <option value="ALL">ALL</option>
            </select>
          </label>
          <label>
            Alert Limit
            <input type="number" min="10" max="500" v-model.number="dbviz.usageAlertLimit" />
          </label>
        </div>

        <div class="dbviz-cards" v-if="dbviz.usageSummary?.totals">
          <div class="dbviz-card">
            <strong>Total Calls</strong>
            <span>{{ dbviz.usageSummary.totals.calls || 0 }}</span>
          </div>
          <div class="dbviz-card">
            <strong>Input Tokens</strong>
            <span>{{ dbviz.usageSummary.totals.input_tokens || 0 }}</span>
          </div>
          <div class="dbviz-card">
            <strong>Output Tokens</strong>
            <span>{{ dbviz.usageSummary.totals.output_tokens || 0 }}</span>
          </div>
          <div class="dbviz-card">
            <strong>Total Tokens</strong>
            <span>{{ dbviz.usageSummary.totals.total_tokens || 0 }}</span>
          </div>
          <div class="dbviz-card">
            <strong>Estimated Cost (CNY)</strong>
            <span>{{ formatCny(dbviz.usageSummary.totals.estimated_cost_usd || 0) }}</span>
          </div>
        </div>

        <div class="grid two usage-grid">
          <div class="panel-sub">
            <h3>By API</h3>
            <div class="table-wrap">
              <table class="db-table">
                <thead>
                  <tr><th>api_path</th><th>calls</th><th>total_tokens</th></tr>
                </thead>
                <tbody>
                  <tr v-for="item in (dbviz.usageSummary?.by_api || [])" :key="item.api_path">
                    <td class="mono"><span class="cell-ellipsis" :title="item.api_path">{{ item.api_path }}</span></td>
                    <td class="mono">{{ item.calls }}</td>
                    <td class="mono">{{ item.total_tokens }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="panel-sub">
            <h3>By Model</h3>
            <div class="table-wrap">
              <table class="db-table">
                <thead>
                  <tr><th>model</th><th>calls</th><th>total_tokens</th></tr>
                </thead>
                <tbody>
                  <tr v-for="item in (dbviz.usageSummary?.by_model || [])" :key="item.model">
                    <td class="mono"><span class="cell-ellipsis" :title="item.model">{{ item.model }}</span></td>
                    <td class="mono">{{ item.calls }}</td>
                    <td class="mono">{{ item.total_tokens }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="panel-sub">
            <h3>By API Key</h3>
            <div class="table-wrap">
              <table class="db-table">
                <thead>
                  <tr><th>api_key_fingerprint</th><th>calls</th><th>total_tokens</th></tr>
                </thead>
                <tbody>
                  <tr v-for="item in (dbviz.usageSummary?.by_api_key || [])" :key="item.api_key_fingerprint">
                    <td class="mono"><span class="cell-ellipsis" :title="item.api_key_fingerprint">{{ item.api_key_fingerprint }}</span></td>
                    <td class="mono">{{ item.calls }}</td>
                    <td class="mono">{{ item.total_tokens }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="panel-sub">
            <h3>By Task Category</h3>
            <div class="table-wrap">
              <table class="db-table">
                <thead>
                  <tr><th>task_category</th><th>calls</th><th>total_tokens</th></tr>
                </thead>
                <tbody>
                  <tr v-for="item in (dbviz.usageSummary?.by_task_category || [])" :key="item.task_category">
                    <td class="mono"><span class="cell-ellipsis" :title="item.task_category">{{ item.task_category }}</span></td>
                    <td class="mono">{{ item.calls }}</td>
                    <td class="mono">{{ item.total_tokens }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="panel-sub">
            <h3>By KB</h3>
            <div class="table-wrap">
              <table class="db-table">
                <thead>
                  <tr><th>kb_id</th><th>calls</th><th>total_tokens</th><th>cost_cny</th></tr>
                </thead>
                <tbody>
                  <tr v-for="item in (dbviz.usageSummary?.by_kb || [])" :key="item.kb_id">
                    <td class="mono"><span class="cell-ellipsis" :title="item.kb_id || '-'">{{ item.kb_id || '-' }}</span></td>
                    <td class="mono">{{ item.calls }}</td>
                    <td class="mono">{{ item.total_tokens }}</td>
                    <td class="mono">{{ formatCny(item.estimated_cost_usd || 0) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="panel-sub">
            <h3>By Day x Model</h3>
            <div class="table-wrap">
              <table class="db-table">
                <thead>
                  <tr><th>day</th><th>model</th><th>calls</th><th>tokens</th><th>cost_cny</th></tr>
                </thead>
                <tbody>
                  <tr v-for="item in (dbviz.usageSummary?.by_day_model || [])" :key="`${item.day}-${item.model}`">
                    <td class="mono">{{ item.day }}</td>
                    <td class="mono"><span class="cell-ellipsis" :title="item.model">{{ item.model }}</span></td>
                    <td class="mono">{{ item.calls }}</td>
                    <td class="mono">{{ item.total_tokens }}</td>
                    <td class="mono">{{ formatCny(item.estimated_cost_usd || 0) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="panel-sub">
            <h3>By Day x Task</h3>
            <div class="table-wrap">
              <table class="db-table">
                <thead>
                  <tr><th>day</th><th>task_category</th><th>calls</th><th>tokens</th><th>cost_cny</th></tr>
                </thead>
                <tbody>
                  <tr v-for="item in (dbviz.usageSummary?.by_day_task_category || [])" :key="`${item.day}-${item.task_category}`">
                    <td class="mono">{{ item.day }}</td>
                    <td class="mono"><span class="cell-ellipsis" :title="item.task_category">{{ item.task_category }}</span></td>
                    <td class="mono">{{ item.calls }}</td>
                    <td class="mono">{{ item.total_tokens }}</td>
                    <td class="mono">{{ formatCny(item.estimated_cost_usd || 0) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div class="grid two usage-grid">
          <div class="panel-sub">
            <div class="endpoint-head">
              <h3>Budgets</h3>
              <div class="actions">
                <button class="ghost" @click="loadUsageBudgets">刷新</button>
              </div>
            </div>
            <div class="grid four">
              <label>
                Scope Type
                <select v-model="dbviz.usageBudgetForm.scope_type">
                  <option value="tenant">tenant</option>
                  <option value="kb">kb</option>
                  <option value="model">model</option>
                  <option value="task_category">task_category</option>
                  <option value="api_path">api_path</option>
                </select>
              </label>
              <label>
                Scope Value
                <input v-model="dbviz.usageBudgetForm.scope_value" placeholder="* / kb_id / model..." />
              </label>
              <label>
                Daily Token Limit
                <input type="number" min="0" v-model="dbviz.usageBudgetForm.daily_token_limit" placeholder="empty = no limit" />
              </label>
              <label>
                Daily Cost Limit (CNY)
                <input type="number" min="0" step="0.0001" v-model="dbviz.usageBudgetForm.daily_cost_limit" placeholder="empty = no limit" />
              </label>
            </div>
            <div class="actions" style="margin-bottom: 10px;">
              <button class="primary" @click="saveUsageBudget">保存预算</button>
            </div>
            <div class="table-wrap">
              <table class="db-table">
                <thead>
                  <tr><th>id</th><th>scope</th><th>token_limit</th><th>cost_limit</th><th>enabled</th><th>action</th></tr>
                </thead>
                <tbody>
                  <tr v-for="item in dbviz.usageBudgets" :key="item.budget_id">
                    <td class="mono">{{ item.budget_id }}</td>
                    <td class="mono"><span class="cell-ellipsis" :title="`${item.scope_type}:${item.scope_value}`">{{ item.scope_type }}:{{ item.scope_value }}</span></td>
                    <td class="mono">{{ item.daily_token_limit ?? '-' }}</td>
                    <td class="mono">{{ item.daily_cost_limit == null ? '-' : formatCny(item.daily_cost_limit) }}</td>
                    <td class="mono">{{ item.enabled ? 'yes' : 'no' }}</td>
                    <td><button class="ghost" @click="deleteUsageBudget(item.budget_id)">删除</button></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="panel-sub">
            <div class="endpoint-head">
              <h3>Alerts</h3>
              <div class="actions">
                <button class="ghost" @click="loadUsageAlerts">刷新</button>
              </div>
            </div>
            <div class="table-wrap">
              <table class="db-table">
                <thead>
                  <tr><th>id</th><th>scope</th><th>severity</th><th>status</th><th>actual</th><th>limit</th><th>created_at</th><th>action</th></tr>
                </thead>
                <tbody>
                  <tr v-for="item in dbviz.usageAlerts" :key="item.alert_id">
                    <td class="mono">{{ item.alert_id }}</td>
                    <td class="mono"><span class="cell-ellipsis" :title="`${item.scope_type}:${item.scope_value}`">{{ item.scope_type }}:{{ item.scope_value }}</span></td>
                    <td class="mono">{{ item.severity }}</td>
                    <td class="mono">{{ item.status }}</td>
                    <td class="mono">{{ item.actual_value }}</td>
                    <td class="mono">{{ item.limit_value }}</td>
                    <td class="mono"><span class="cell-ellipsis" :title="String(item.created_at || '')">{{ item.created_at }}</span></td>
                    <td class="actions">
                      <button class="ghost" v-if="item.status !== 'RESOLVED'" @click="updateUsageAlertStatus(item.alert_id, 'RESOLVED')">Resolve</button>
                      <button class="ghost" v-else @click="updateUsageAlertStatus(item.alert_id, 'OPEN')">Reopen</button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div class="panel-sub">
          <div class="endpoint-head">
            <h3>Events</h3>
            <div class="actions">
              <button class="ghost" @click="dbviz.usagePage = Math.max(1, dbviz.usagePage - 1); loadUsageEvents()">Prev</button>
              <button class="ghost" @click="dbviz.usagePage += 1; loadUsageEvents()">Next</button>
            </div>
          </div>
          <p class="subtitle">page={{ dbviz.usagePage }}/{{ usageTotalPages }}, size={{ dbviz.usagePageSize }}, total={{ dbviz.usageTotal }}</p>
          <div class="table-wrap">
            <table class="db-table">
              <thead>
                <tr>
                  <th>created_at</th>
                  <th>api_path</th>
                  <th>model</th>
                  <th>task_category</th>
                  <th>api_key_fingerprint</th>
                  <th>input</th>
                  <th>output</th>
                  <th>total</th>
                  <th>cost_cny</th>
                  <th>success</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in dbviz.usageEvents" :key="item.event_id">
                  <td class="mono"><span class="cell-ellipsis" :title="String(item.created_at ?? '')">{{ item.created_at }}</span></td>
                  <td class="mono"><span class="cell-ellipsis" :title="String(item.api_path ?? '')">{{ item.api_path }}</span></td>
                  <td class="mono"><span class="cell-ellipsis" :title="String(item.model ?? '')">{{ item.model }}</span></td>
                  <td class="mono"><span class="cell-ellipsis" :title="String(item.task_category ?? '')">{{ item.task_category }}</span></td>
                  <td class="mono"><span class="cell-ellipsis" :title="String(item.api_key_fingerprint ?? '')">{{ item.api_key_fingerprint }}</span></td>
                  <td class="mono">{{ item.input_tokens }}</td>
                  <td class="mono">{{ item.output_tokens }}</td>
                  <td class="mono">{{ item.total_tokens }}</td>
                  <td class="mono">{{ formatCny(item.estimated_cost_usd || 0) }}</td>
                  <td class="mono">{{ item.success ? 'ok' : 'fail' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </main>

    <main class="main" v-else-if="selectedEndpoint && selectedState">
      <section class="panel config">
        <h2>Global Config</h2>
        <div class="grid four">
          <label>
            Control Plane URL
            <input v-model="globalConfig.controlPlaneBaseUrl" />
          </label>
          <label>
            Execution URL
            <input v-model="globalConfig.executionBaseUrl" />
          </label>
          <label>
            X-Tenant-Id
            <input v-model="globalConfig.tenantId" />
          </label>
          <label>
            X-Actor-Id
            <input v-model="globalConfig.actorId" />
          </label>
          <label>
            X-Source-Service
            <input v-model="globalConfig.sourceService" />
          </label>
          <label>
            X-Trace-Id (optional)
            <input v-model="globalConfig.traceId" placeholder="auto if empty" />
          </label>
          <label>
            Admin Token
            <input v-model="globalConfig.adminToken" />
          </label>
          <label>
            Control Plane Token
            <input v-model="globalConfig.controlPlaneToken" />
          </label>
        </div>
      </section>

      <section class="panel endpoint">
        <div class="endpoint-head">
          <div>
            <h2>{{ selectedEndpoint.name }}</h2>
            <p class="endpoint-summary" v-if="selectedEndpoint.summary">{{ selectedEndpoint.summary }}</p>
            <p class="path-preview">
              <span class="method" :class="methodClass(selectedEndpoint.method)">{{ selectedEndpoint.method }}</span>
              {{ selectedEndpoint.path }}
            </p>
          </div>
          <div class="actions">
            <button class="ghost" @click="resetEndpoint">Reset</button>
            <button class="primary" @click="invokeEndpoint" :disabled="result.loading">
              {{ result.loading ? 'Running...' : 'Invoke' }}
            </button>
          </div>
        </div>

        <div class="grid three" v-if="Object.keys(selectedState.pathParams).length">
          <label v-for="(value, key) in selectedState.pathParams" :key="`path-${key}`">
            Path: {{ key }}
            <input v-model="selectedState.pathParams[key]" />
          </label>
        </div>

        <div class="grid three" v-if="Object.keys(selectedState.queryParams).length">
          <label v-for="(value, key) in selectedState.queryParams" :key="`query-${key}`">
            Query: {{ key }}
            <input v-model="selectedState.queryParams[key]" />
          </label>
        </div>

        <div class="grid three" v-if="Object.keys(selectedState.headers).length">
          <label v-for="(value, key) in selectedState.headers" :key="`header-${key}`">
            Header: {{ key }}
            <input v-model="selectedState.headers[key]" />
          </label>
        </div>

        <label class="json-editor" v-if="selectedEndpoint.method !== 'GET' && selectedEndpoint.method !== 'DELETE'">
          Request Body (JSON)
          <textarea v-model="selectedState.bodyText" rows="10" spellcheck="false"></textarea>
        </label>
      </section>

      <section class="panel response">
        <h2>Request & Response</h2>
        <div class="status-row">
          <span class="badge" :class="{ ok: result.status.startsWith('2'), error: !!result.error }">
            {{ result.error ? 'ERROR' : result.status || 'IDLE' }}
          </span>
          <span>{{ result.durationMs }} ms</span>
        </div>
        <p v-if="result.error" class="error-text">{{ result.error }}</p>

        <label class="json-editor">
          Request Preview
          <textarea :value="result.requestPreview" rows="7" readonly></textarea>
        </label>

        <label class="json-editor">
          Response Headers
          <textarea :value="result.responseHeaders" rows="6" readonly></textarea>
        </label>

        <label class="json-editor">
          Response Body
          <textarea :value="result.responseBody" rows="12" readonly></textarea>
        </label>
      </section>

      <section class="panel" v-if="selectedEndpointDoc">
        <h2>接口示例与说明</h2>

        <div class="doc-block" v-if="selectedEndpoint.detail || selectedEndpoint.paramDocs?.length || selectedEndpoint.responseDocs?.length || selectedEndpoint.debugTips?.length">
          <h3>接口说明</h3>
          <p class="doc-detail" v-if="selectedEndpoint.detail">{{ selectedEndpoint.detail }}</p>

          <div v-if="selectedEndpoint.paramDocs?.length">
            <h4>参数说明</h4>
            <ul>
              <li v-for="(item, idx) in selectedEndpoint.paramDocs" :key="`param-${idx}`">{{ item }}</li>
            </ul>
          </div>

          <div v-if="selectedEndpoint.responseDocs?.length">
            <h4>返回说明</h4>
            <ul>
              <li v-for="(item, idx) in selectedEndpoint.responseDocs" :key="`resp-${idx}`">{{ item }}</li>
            </ul>
          </div>

          <div v-if="selectedEndpoint.debugTips?.length">
            <h4>调试建议</h4>
            <ul>
              <li v-for="(item, idx) in selectedEndpoint.debugTips" :key="`tip-${idx}`">{{ item }}</li>
            </ul>
          </div>
        </div>

        <div class="grid two">
          <label class="json-editor">
            请求示例
            <textarea :value="JSON.stringify(selectedEndpointDoc.requestExample, null, 2)" rows="12" readonly></textarea>
          </label>
          <label class="json-editor">
            响应示例
            <textarea :value="JSON.stringify(selectedEndpointDoc.responseExample, null, 2)" rows="12" readonly></textarea>
          </label>
        </div>
        <div class="table-wrap">
          <table class="db-table">
            <thead>
              <tr>
                <th>Location</th>
                <th>Field</th>
                <th>Required</th>
                <th>Example</th>
                <th>Description</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in selectedEndpointDoc.fields" :key="`${row.location}-${row.name}`">
                <td class="mono">{{ row.location }}</td>
                <td class="mono">{{ row.name }}</td>
                <td>{{ row.required }}</td>
                <td class="mono"><span class="cell-ellipsis" :title="row.example">{{ row.example }}</span></td>
                <td><span class="cell-ellipsis" :title="row.desc || '-'">{{ row.desc || '-' }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </main>
  </div>
</template>
