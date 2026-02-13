<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { endpointGroups } from './endpoints'

const globalConfig = reactive({
  controlPlaneBaseUrl: 'http://localhost:8080',
  executionBaseUrl: 'http://localhost:8091',
  tenantId: 'platform',
  actorId: 'debug-user',
  sourceService: 'debug-web',
  traceId: '',
  adminToken: 'dev-admin-token',
  adminUsername: 'admin',
  adminPassword: 'admin123',
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
})
const pgTotalPages = computed(() => Math.max(1, Math.ceil((dbviz.pgTotal || 0) / (dbviz.pgPageSize || 1))))
const mongoTotalPages = computed(() => Math.max(1, Math.ceil((dbviz.mongoTotal || 0) / (dbviz.mongoPageSize || 1))))

const methodClass = (method) => `method-${method.toLowerCase()}`

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
  return {
    Authorization: `Bearer ${globalConfig.adminToken}`,
    'X-Tenant-Id': globalConfig.tenantId,
    'X-Actor-Id': globalConfig.actorId,
    'X-Source-Service': globalConfig.sourceService,
    ...(globalConfig.traceId?.trim() ? { 'X-Trace-Id': globalConfig.traceId.trim() } : {}),
  }
}

async function fetchJson(path, query = null) {
  const base = globalConfig.executionBaseUrl.trim().replace(/\/$/, '')
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

async function loadDbviz() {
  dbviz.loading = true
  dbviz.error = ''
  try {
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
    await Promise.all([loadPgRows(), loadQdrantPoints(true), loadMongoRows()])
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

watch(
  () => selectedId.value,
  async (v) => {
    if (v === '__dbviz__') {
      await loadDbviz()
    }
  },
)
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <h1 class="brand">Debug Web</h1>
      <p class="subtitle">Agent Powflow API Console</p>
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
            Admin Token
            <input v-model="globalConfig.adminToken" />
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
              <option value="postgres">PostgreSQL</option>
              <option value="qdrant">Qdrant</option>
              <option value="mongo">MongoDB</option>
            </select>
          </label>
        </div>
        <div class="actions">
          <button class="ghost" @click="loginAdminToken">获取 JWT</button>
          <button class="primary" @click="loadDbviz" :disabled="dbviz.loading">{{ dbviz.loading ? 'Loading...' : 'Refresh' }}</button>
        </div>
        <p v-if="dbviz.error" class="error-text">{{ dbviz.error }}</p>
      </section>

      <section class="panel">
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
    </main>
  </div>
</template>
