<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { marked } from 'marked'

const cfg = reactive({
  controlPlaneBaseUrl: 'http://127.0.0.1:8099',
  chatBaseUrl: 'http://localhost:8092',
  executionBaseUrl: 'http://localhost:8091',
  executionToken: '',
  chatToken: '',
  tenantId: 'platform',
  actorId: 'chat-user',
  sourceService: 'web-app',
})

const auth = reactive({
  loggedIn: false,
  loggingIn: false,
  error: '',
  remember: true,
  mode: 'login',
})

const userAuth = reactive({
  email: '',
  password: '',
  displayName: '',
})

const ui = reactive({
  sidebarCollapsed: false,
  debugOpen: false,
  accountOpen: false,
  userCenterOpen: false,
  selectedSessionId: '',
  sourceOpen: {},
  eventOpen: {},
  stageOpen: {},
})

const chat = reactive({
  loadingSessions: false,
  loadingKb: false,
  loadingConfig: false,
  running: false,
  cancelling: false,
  statusText: 'Ready',
  runId: '',
  mountedKbId: '',
  useKb: true,
  topK: 6,
  rankMethod: 'vector',
  promptPreview: '',
  promptDraft: '',
  kbList: [],
  sessions: [],
  retrievalSources: [],
  stageState: [],
  debugEvents: [],
})

const account = reactive({
  loading: false,
  saving: false,
  error: '',
  email: '',
  displayName: '',
  tenantId: '',
  roles: [],
  userStatus: '',
  displayNameDraft: '',
})

const input = ref('')
const messages = ref([])
const messagesRef = ref(null)

const canSend = computed(() => !chat.running && input.value.trim().length > 0)
const activeSession = computed(() => chat.sessions.find((s) => s.session_id === ui.selectedSessionId) || null)

marked.setOptions({
  gfm: true,
  breaks: true,
})

function renderMarkdown(content) {
  const raw = String(content || '')
  const escaped = raw.replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return marked.parse(escaped)
}

function ensureAssistantHtml(msg) {
  if (!msg || msg.role !== 'assistant') return
  msg.html = renderMarkdown(msg.content || '')
}

function now() {
  return new Date().toLocaleTimeString()
}

function scrollToBottom() {
  nextTick(() => {
    const el = messagesRef.value
    if (!el) return
    el.scrollTop = el.scrollHeight
  })
}

function nextFrame() {
  return new Promise((resolve) => {
    requestAnimationFrame(() => resolve(null))
  })
}

watch(
  () => messages.value.length,
  () => scrollToBottom(),
)

function authHeaders(extra = {}) {
  return {
    'X-Source-Service': cfg.sourceService,
    ...extra,
  }
}

function executionHeaders(extra = {}) {
  return authHeaders({
    ...(cfg.executionToken ? { Authorization: `Bearer ${cfg.executionToken}` } : {}),
    ...extra,
  })
}

function chatHeaders(extra = {}) {
  return {
    'X-Source-Service': cfg.sourceService,
    ...(cfg.chatToken ? { Authorization: `Bearer ${cfg.chatToken}` } : {}),
    ...extra,
  }
}

function isLikelySignedToken(token) {
  const t = String(token || '').trim()
  if (!t) return false
  const dots = (t.match(/\./g) || []).length
  return dots === 1 || dots === 2
}

function decodeJwtPayload(token) {
  try {
    const t = String(token || '').trim()
    const parts = t.split('.')
    if (parts.length !== 3) return null
    const payload = parts[1]
    const pad = '='.repeat((4 - (payload.length % 4)) % 4)
    const json = atob((payload + pad).replace(/-/g, '+').replace(/_/g, '/'))
    const data = JSON.parse(json)
    return data && typeof data === 'object' ? data : null
  } catch {
    return null
  }
}

function syncIdentityFromToken(token) {
  const payload = decodeJwtPayload(token)
  if (!payload) return
  const tenant = String(payload.tenant_id || '').trim()
  const actor = String(payload.sub || payload.actor_id || '').trim()
  if (tenant) cfg.tenantId = tenant
  if (actor) cfg.actorId = actor
}

function resolveTenantCode() {
  const q = new URLSearchParams(window.location.search).get('tenant')
  if (q && q.trim()) return q.trim()
  return String(cfg.tenantId || 'platform').trim() || 'platform'
}

function accountRoleText() {
  if (!Array.isArray(account.roles) || !account.roles.length) return 'USER'
  return account.roles.join(', ')
}

function toHumanDetail(data) {
  if (!data || typeof data !== 'object') return ''
  const lines = []
  for (const [k, v] of Object.entries(data)) {
    if (v === undefined || v === null || v === '') continue
    if (Array.isArray(v)) {
      lines.push(`${k}: ${v.length} 项`)
    } else if (typeof v === 'object') {
      lines.push(`${k}: ${Object.keys(v).length} 个字段`)
    } else {
      lines.push(`${k}: ${String(v)}`)
    }
  }
  return lines.join('\n')
}

function eventDetailText(ev) {
  const data = ev?.data
  if (!data || typeof data !== 'object') return ''
  const lines = []

  const simpleKeys = ['base_url', 'model', 'key_source', 'api_key_configured', 'prompt_messages_count']
  for (const key of simpleKeys) {
    if (data[key] !== undefined && data[key] !== null && data[key] !== '') {
      lines.push(`${key}: ${String(data[key])}`)
    }
  }

  if (Array.isArray(data.prompt_messages) && data.prompt_messages.length) {
    lines.push('')
    lines.push('prompt_messages:')
    data.prompt_messages.forEach((m, idx) => {
      const role = String(m?.role || 'unknown')
      const content = String(m?.content || '')
      lines.push(`#${idx + 1} [${role}]`)
      lines.push(content || '(empty)')
      lines.push('')
    })
  } else {
    const fallback = toHumanDetail(data)
    if (fallback) lines.push(fallback)
  }

  return lines.join('\n').trim()
}

function pushDebug(kind, title, detail, data = null) {
  const item = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
    time: now(),
    kind,
    title,
    detail,
    human: toHumanDetail(data),
    data,
  }
  chat.debugEvents.unshift(item)
  if (chat.debugEvents.length > 300) chat.debugEvents.length = 300
}

function stageLabel(stage) {
  const map = {
    START: '开始',
    PREPARE: '预处理',
    NORMALIZE: '规范化',
    MEMORY_LOAD: '记忆加载',
    RETRIEVAL: '知识检索',
    RETRIEVAL_DONE: '检索完成',
    LLM_REQUEST: 'LLM 调用',
    STREAMING: '流式输出',
    MEMORY_WRITE: '记忆写入',
    DONE: '完成',
  }
  return map[stage] || stage || '-'
}

function prettyValue(v) {
  if (v === null || v === undefined) return '-'
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  if (Array.isArray(v)) return `(${v.length} items)`
  if (typeof v === 'object') return `(${Object.keys(v).length} fields)`
  return String(v)
}

function stageDetailText(info) {
  if (!info || typeof info !== 'object') return ''
  const lines = []
  for (const [k, v] of Object.entries(info)) {
    if (k === 'preview' && Array.isArray(v)) continue
    lines.push(`${k}: ${prettyValue(v)}`)
  }
  if (Array.isArray(info.preview) && info.preview.length) {
    lines.push('')
    lines.push(`preview(${info.preview.length}):`)
    info.preview.forEach((p, i) => {
      const score = typeof p?.score === 'number' ? p.score.toFixed(3) : '-'
      const text = String(p?.text_preview || '').replace(/\s+/g, ' ').trim()
      lines.push(`#${i + 1} doc=${p?.doc_id || '-'} chunk=${p?.chunk_id || '-'} score=${score}`)
      if (text) lines.push(`  ${text}`)
    })
  }
  return lines.join('\n')
}

async function fetchJson(baseUrl, path, options = {}) {
  const url = `${baseUrl.replace(/\/$/, '')}${path}`
  const response = await fetch(url, options)
  if (!response.ok) {
    const text = await response.text()
    throw new Error(`${response.status} ${response.statusText}: ${text}`)
  }
  return response.json()
}

async function ensureExecutionLogin() {
  if (cfg.executionToken) return
  const data = await fetchJson(cfg.executionBaseUrl, '/api/v1/admin/auth/login', {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ username: cfg.adminUsername, password: cfg.adminPassword }),
  })
  cfg.executionToken = String(data.access_token || data.token || '')
  if (!cfg.executionToken) throw new Error('execution admin login failed: empty token')
  if (!isLikelySignedToken(cfg.chatToken)) {
    cfg.chatToken = cfg.executionToken
  }
}

function hydrateFromStorage() {
  try {
    const raw = localStorage.getItem('apf_webapp_auth')
    if (!raw) return
    const data = JSON.parse(raw)
    if (data.executionBaseUrl) cfg.executionBaseUrl = String(data.executionBaseUrl)
    if (data.controlPlaneBaseUrl) cfg.controlPlaneBaseUrl = String(data.controlPlaneBaseUrl)
    if (data.chatBaseUrl) cfg.chatBaseUrl = String(data.chatBaseUrl)
    if (data.chatToken && isLikelySignedToken(data.chatToken)) cfg.chatToken = String(data.chatToken)
    if (data.tenantId) cfg.tenantId = String(data.tenantId)
    if (data.actorId) cfg.actorId = String(data.actorId)
    if (data.topK) chat.topK = Number(data.topK)
    if (data.rankMethod) chat.rankMethod = String(data.rankMethod)
    if (data.authMode && ['login', 'register'].includes(String(data.authMode))) {
      auth.mode = String(data.authMode)
    }
  } catch {
    // noop
  }
}

function persistToStorage() {
  if (!auth.remember) return
  localStorage.setItem(
    'apf_webapp_auth',
    JSON.stringify({
      executionBaseUrl: cfg.executionBaseUrl,
      controlPlaneBaseUrl: cfg.controlPlaneBaseUrl,
      chatBaseUrl: cfg.chatBaseUrl,
      chatToken: cfg.chatToken,
      tenantId: cfg.tenantId,
      actorId: cfg.actorId,
      topK: chat.topK,
      rankMethod: chat.rankMethod,
      authMode: auth.mode,
    }),
  )
}

async function refreshKbList() {
  chat.loadingKb = true
  try {
    await ensureExecutionLogin()
    const data = await fetchJson(cfg.executionBaseUrl, '/api/v1/admin/kbs', { headers: executionHeaders() })
    chat.kbList = data.items || []
    if (!chat.mountedKbId && chat.kbList.length) chat.mountedKbId = chat.kbList[0].kb_id
    pushDebug('db', 'KB 数据库访问', `已载入 ${chat.kbList.length} 个知识库。`, { kb_count: chat.kbList.length })
  } catch (err) {
    chat.statusText = err instanceof Error ? err.message : String(err)
    pushDebug('error', 'KB 读取失败', chat.statusText)
  } finally {
    chat.loadingKb = false
  }
}

async function loadChatConfig() {
  chat.loadingConfig = true
  try {
    const data = await fetchJson(cfg.chatBaseUrl, '/api/v1/admin/chat/config', { headers: chatHeaders() })
    const item = data.item || {}
    chat.topK = Number(item.chat_default_top_k || 6)
    chat.rankMethod = String(item.chat_default_rank_method || 'vector')
    chat.promptPreview = String(item.chat_system_prompt || '')
    chat.promptDraft = chat.promptPreview
    pushDebug('api', 'Chat 配置读取', '已读取对话默认参数与系统提示词。', {
      top_k: chat.topK,
      rank_method: chat.rankMethod,
      prompt_chars: chat.promptPreview.length,
    })
  } catch (err) {
    chat.statusText = err instanceof Error ? err.message : String(err)
    pushDebug('error', '配置读取失败', chat.statusText)
  } finally {
    chat.loadingConfig = false
  }
}

async function saveChatPrompt() {
  try {
    const prompt = String(chat.promptDraft || '').trim()
    if (!prompt) throw new Error('Prompt 不能为空')
    await fetchJson(cfg.chatBaseUrl, '/api/v1/admin/chat/config', {
      method: 'PATCH',
      headers: chatHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ chat_system_prompt: prompt }),
    })
    chat.promptPreview = prompt
    pushDebug('api', 'Prompt 配置已保存', '已将新的系统 Prompt 写入 chat-plane 运行配置。', {
      prompt_chars: prompt.length,
    })
  } catch (err) {
    pushDebug('error', '保存 Prompt 失败', err instanceof Error ? err.message : String(err))
  }
}

async function loadSessions() {
  chat.loadingSessions = true
  try {
    const data = await fetchJson(cfg.chatBaseUrl, '/api/v1/chat/sessions?limit=100', { headers: chatHeaders() })
    chat.sessions = data.items || []
  } catch (err) {
    pushDebug('error', '会话列表读取失败', err instanceof Error ? err.message : String(err))
  } finally {
    chat.loadingSessions = false
  }
}

async function createSession() {
  const title = chat.mountedKbId ? `Chat-${chat.mountedKbId}` : `Chat-${Date.now()}`
  const data = await fetchJson(cfg.chatBaseUrl, '/api/v1/chat/sessions', {
    method: 'POST',
    headers: chatHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ title, kb_id: chat.mountedKbId || null }),
  })
  const sessionId = data.item?.session_id || ''
  if (!sessionId) throw new Error('session_id empty')
  ui.selectedSessionId = sessionId
  await loadSessions()
  return sessionId
}

async function renameSession(session) {
  const next = window.prompt('重命名会话', session?.title || '')
  if (next === null) return
  const title = String(next).trim()
  if (!title) return
  try {
    await fetchJson(cfg.chatBaseUrl, `/api/v1/chat/sessions/${session.session_id}`, {
      method: 'PATCH',
      headers: chatHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ title }),
    })
    await loadSessions()
  } catch (err) {
    pushDebug('error', '重命名会话失败', err instanceof Error ? err.message : String(err))
  }
}

async function deleteSession(session) {
  if (!window.confirm(`删除会话「${session?.title || session?.session_id}」？`)) return
  try {
    await fetchJson(cfg.chatBaseUrl, `/api/v1/chat/sessions/${session.session_id}`, {
      method: 'DELETE',
      headers: chatHeaders(),
    })
    if (ui.selectedSessionId === session.session_id) {
      ui.selectedSessionId = ''
      messages.value = []
      resetRunState()
    }
    await loadSessions()
  } catch (err) {
    pushDebug('error', '删除会话失败', err instanceof Error ? err.message : String(err))
  }
}

async function selectSession(sessionId) {
  ui.selectedSessionId = sessionId
  const data = await fetchJson(cfg.chatBaseUrl, `/api/v1/chat/sessions/${sessionId}/messages?limit=200`, {
    headers: chatHeaders(),
  })
  const items = data.items || []
  messages.value = items.map((m) => ({
    role: m.role === 'assistant' ? 'assistant' : 'user',
    content: m.content || '',
    time: m.created_at || now(),
    html: m.role === 'assistant' ? renderMarkdown(m.content || '') : '',
  }))
  scrollToBottom()
}

function resetRunState() {
  chat.retrievalSources = []
  chat.stageState = []
  chat.runId = ''
  ui.sourceOpen = {}
}

function onStage(data) {
  const current = Number(data.progress_current || 0)
  const total = Number(data.progress_total || 0)
  const stage = String(data.stage || '-')
  chat.statusText = `${stageLabel(stage)} ${current}/${total}`
  const stageItem = {
    stage,
    stageText: stageLabel(stage),
    progress: `${current}/${total}`,
    time: now(),
    info: data.info || {},
  }
  chat.stageState.push(stageItem)
  if (Array.isArray(data.info?.preview) && data.info.preview.length) {
    chat.retrievalSources = data.info.preview
  }
}

function onTrace(data) {
  const title = data.title || '运行事件'
  const detail = data.detail || ''
  const payload = {
    ...(data && typeof data === 'object' ? data : {}),
    ...(data.info && typeof data.info === 'object' ? data.info : {}),
    ...(Array.isArray(data.retrieval_preview) ? { retrieval_preview_count: data.retrieval_preview.length } : {}),
  }
  pushDebug('trace', title, detail, payload)
  if (Array.isArray(data.retrieval_preview) && data.retrieval_preview.length) {
    chat.retrievalSources = data.retrieval_preview
  }
}

async function sendMessage() {
  if (!canSend.value) return
  let sessionId = ui.selectedSessionId
  if (!sessionId) sessionId = await createSession()

  const prompt = input.value.trim()
  input.value = ''
  messages.value.push({ role: 'user', content: prompt, time: now() })
  const assistantRaw = { role: 'assistant', content: '', time: now(), streaming: true, html: '' }
  messages.value.push(assistantRaw)
  // IMPORTANT: mutate the proxied object inside the reactive array, not the raw object.
  const assistant = messages.value[messages.value.length - 1]

  chat.running = true
  chat.statusText = 'Streaming...'
  resetRunState()

  pushDebug(
    'api',
    '发起对话请求',
    '已提交对话请求，开始进入流式执行。',
    {
      session_id: sessionId,
      kb_id: chat.mountedKbId || '-',
      use_kb: chat.useKb,
      top_k: chat.topK,
      rank_method: chat.rankMethod,
      prompt_chars: prompt.length,
    },
  )

  const payload = {
    message: prompt,
    use_kb: chat.useKb,
    kb_id: chat.mountedKbId || null,
    top_k: Number(chat.topK || 6),
    rank_method: chat.rankMethod,
    pipeline_id: 'default-chat-v1',
    metadata: { source: 'web-app' },
  }

  try {
    const url = `${cfg.chatBaseUrl.replace(/\/$/, '')}/api/v1/chat/sessions/${sessionId}/messages/stream`
    const response = await fetch(url, {
      method: 'POST',
      headers: chatHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    })
    if (!response.ok || !response.body) throw new Error(`${response.status} ${response.statusText}`)

    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    let pendingDelta = ''
    let flushTimer = null
    let mdTimer = null
    let lastMdRenderAt = 0
    let lastScrollAt = 0
    let handledEvents = 0

    const flushDelta = () => {
      flushTimer = null
      if (!pendingDelta) return
      assistant.content += pendingDelta
      pendingDelta = ''
      scheduleMarkdownRender()
      const nowTs = Date.now()
      if (nowTs - lastScrollAt >= 80) {
        lastScrollAt = nowTs
        scrollToBottom()
      }
    }

    const scheduleFlush = () => {
      if (flushTimer) return
      flushTimer = setTimeout(flushDelta, 24)
    }

    const doMarkdownRender = () => {
      mdTimer = null
      ensureAssistantHtml(assistant)
      lastMdRenderAt = Date.now()
    }

    const scheduleMarkdownRender = (force = false) => {
      if (force) {
        if (mdTimer) {
          clearTimeout(mdTimer)
          mdTimer = null
        }
        doMarkdownRender()
        return
      }
      const nowTs = Date.now()
      const gap = nowTs - lastMdRenderAt
      if (gap >= 140) {
        doMarkdownRender()
        return
      }
      if (mdTimer) return
      mdTimer = setTimeout(doMarkdownRender, 140 - gap)
    }

    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const events = buffer.split('\n\n')
      buffer = events.pop() || ''

      for (const block of events) {
        let eventName = 'message'
        let dataText = ''
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) eventName = line.slice(6).trim()
          if (line.startsWith('data:')) dataText += line.slice(5).trim()
        }
        if (!dataText) continue

        let data = {}
        try {
          data = JSON.parse(dataText)
        } catch {
          continue
        }

        if (eventName === 'run') {
          chat.runId = data.run_id || ''
          pushDebug('api', '创建运行实例', '已创建运行实例，可用于取消任务。', { run_id: chat.runId })
        } else if (eventName === 'stage') {
          onStage(data)
        } else if (eventName === 'trace') {
          onTrace(data)
        } else if (eventName === 'token') {
          pendingDelta += data.delta || ''
          scheduleFlush()
        } else if (eventName === 'done') {
          if (flushTimer) {
            clearTimeout(flushTimer)
            flushDelta()
          }
          scheduleMarkdownRender(true)
          assistant.streaming = false
          if (data.response) assistant.content = data.response
          ensureAssistantHtml(assistant)
          chat.statusText = 'Completed'
          pushDebug('api', '对话完成', '本轮对话已完成并落库。', { response_chars: assistant.content.length })
        } else if (eventName === 'error') {
          if (flushTimer) {
            clearTimeout(flushTimer)
            flushDelta()
          }
          scheduleMarkdownRender(true)
          assistant.streaming = false
          assistant.content = data.message || 'error'
          ensureAssistantHtml(assistant)
          chat.statusText = `Error: ${data.message || 'unknown'}`
          pushDebug('error', '对话执行失败', chat.statusText)
        } else if (eventName === 'cancelled') {
          if (flushTimer) {
            clearTimeout(flushTimer)
            flushDelta()
          }
          scheduleMarkdownRender(true)
          assistant.streaming = false
          ensureAssistantHtml(assistant)
          chat.statusText = 'Cancelled'
          pushDebug('warn', '对话已取消', '运行已被取消。')
        }

        handledEvents += 1
        if (handledEvents % 24 === 0) {
          if (flushTimer) {
            clearTimeout(flushTimer)
            flushDelta()
          }
          await nextFrame()
        }
      }

      if (events.length > 0) {
        await nextFrame()
      }
    }

    if (flushTimer) {
      clearTimeout(flushTimer)
      flushDelta()
    }
    if (mdTimer) {
      clearTimeout(mdTimer)
      mdTimer = null
    }
    ensureAssistantHtml(assistant)
    scrollToBottom()

    await loadSessions()
  } catch (err) {
    chat.statusText = err instanceof Error ? err.message : String(err)
    assistant.streaming = false
    assistant.content = `Request failed: ${chat.statusText}`
    ensureAssistantHtml(assistant)
    pushDebug('error', '流式请求失败', chat.statusText)
  } finally {
    chat.running = false
    chat.cancelling = false
  }
}

async function cancelRun() {
  if (!chat.runId || chat.cancelling) return
  chat.cancelling = true
  try {
    await fetchJson(cfg.chatBaseUrl, `/api/v1/chat/runs/${chat.runId}/cancel`, {
      method: 'POST',
      headers: chatHeaders({ 'Content-Type': 'application/json' }),
      body: '{}',
    })
    chat.statusText = 'Cancel requested'
    pushDebug('warn', '请求取消运行', '取消请求已发送到 chat-plane。', { run_id: chat.runId })
  } catch (err) {
    chat.statusText = err instanceof Error ? err.message : String(err)
    pushDebug('error', '取消失败', chat.statusText)
  } finally {
    chat.cancelling = false
  }
}

async function adminLogin() {
  await ensureExecutionLogin()
  if (!isLikelySignedToken(cfg.chatToken)) {
    cfg.chatToken = cfg.executionToken
  }
  if (!isLikelySignedToken(cfg.chatToken)) throw new Error('valid chat token is required')
  await Promise.all([refreshKbList(), loadChatConfig(), loadSessions()])
}

async function userLogin() {
  const email = String(userAuth.email || '').trim()
  const password = String(userAuth.password || '')
  if (!email || !password) throw new Error('请输入邮箱和密码')
  const tenantCode = resolveTenantCode()
  const data = await fetchJson(cfg.controlPlaneBaseUrl, '/api/v1/auth/login', {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      tenantCode,
      email,
      password,
    }),
  })
  cfg.chatToken = String(data.accessToken || '')
  if (!isLikelySignedToken(cfg.chatToken)) throw new Error('登录成功但 token 为空')
  syncIdentityFromToken(cfg.chatToken)
  if (data.tenantId) cfg.tenantId = String(data.tenantId)
  if (data.actorId) cfg.actorId = String(data.actorId)
  await loadMe()
  await loadSessions()
}

async function userRegister() {
  const email = String(userAuth.email || '').trim()
  const password = String(userAuth.password || '')
  if (!email || !password) throw new Error('请输入邮箱和密码')
  const tenantCode = resolveTenantCode()
  await fetchJson(cfg.controlPlaneBaseUrl, '/api/v1/auth/register', {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      tenantCode,
      email,
      password,
      displayName: userAuth.displayName || null,
    }),
  })
  auth.mode = 'login'
}

async function loadMe() {
  account.loading = true
  account.error = ''
  try {
    const data = await fetchJson(cfg.controlPlaneBaseUrl, '/api/v1/auth/me', {
      headers: chatHeaders(),
    })
    const item = data.item || {}
    account.email = String(item.email || '')
    account.displayName = String(item.display_name || '')
    account.tenantId = String(item.tenant_id || cfg.tenantId || '')
    account.roles = Array.isArray(item.roles) ? item.roles : []
    account.userStatus = String(item.user_status || '')
    account.displayNameDraft = account.displayName
    if (account.tenantId) cfg.tenantId = account.tenantId
  } catch (err) {
    account.error = err instanceof Error ? err.message : String(err)
  } finally {
    account.loading = false
  }
}

async function saveMe() {
  if (account.saving) return
  account.saving = true
  account.error = ''
  try {
    await fetchJson(cfg.controlPlaneBaseUrl, '/api/v1/me', {
      method: 'PATCH',
      headers: chatHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ displayName: String(account.displayNameDraft || '').trim() }),
    })
    await loadMe()
    ui.userCenterOpen = false
  } catch (err) {
    account.error = err instanceof Error ? err.message : String(err)
  } finally {
    account.saving = false
  }
}

async function login() {
  auth.loggingIn = true
  auth.error = ''
  try {
    if (auth.mode === 'register') {
      await userRegister()
      chat.statusText = '注册成功，请登录'
      return
    } else {
      await userLogin()
    }
    auth.loggedIn = true
    chat.statusText = 'Logged in'
    persistToStorage()
  } catch (err) {
    auth.error = err instanceof Error ? err.message : String(err)
    auth.loggedIn = false
  } finally {
    auth.loggingIn = false
  }
}

function logout() {
  auth.loggedIn = false
  cfg.executionToken = ''
  cfg.chatToken = ''
  ui.selectedSessionId = ''
  ui.accountOpen = false
  ui.userCenterOpen = false
  messages.value = []
  chat.sessions = []
  resetRunState()
  account.email = ''
  account.displayName = ''
  account.tenantId = ''
  account.roles = []
  account.userStatus = ''
  account.displayNameDraft = ''
  account.error = ''
  chat.statusText = 'Logged out'
}

function startNewChat() {
  ui.selectedSessionId = ''
  messages.value = []
  resetRunState()
  chat.statusText = 'New chat'
}

function toggleSource(index) {
  ui.sourceOpen[index] = !ui.sourceOpen[index]
}

function toggleEvent(id) {
  ui.eventOpen[id] = !ui.eventOpen[id]
}

function toggleStage(id) {
  ui.stageOpen[id] = !ui.stageOpen[id]
}

function sourceTitle(item, idx) {
  const score = typeof item.score === 'number' ? item.score.toFixed(3) : '-'
  return `#${idx + 1} 文档:${item.doc_id || '-'} / Chunk:${item.chunk_id || '-'} / 分数:${score}`
}

onMounted(() => {
  hydrateFromStorage()
})
</script>

<template>
  <div class="chatgpt-shell" v-if="auth.loggedIn">
    <aside class="chat-sidebar" :class="{ collapsed: ui.sidebarCollapsed }">
      <button class="brand" @click="ui.sidebarCollapsed = !ui.sidebarCollapsed">
        <div class="brand-logo">APF</div>
        <div class="brand-text" v-if="!ui.sidebarCollapsed">
          <div class="brand-title">Agent Powflow</div>
          <div class="brand-sub">Chat Workspace</div>
        </div>
      </button>

      <button class="btn primary" @click="startNewChat">+ New Chat</button>

      <div class="session-wrap" v-if="!ui.sidebarCollapsed">
        <div class="section-title">Conversations</div>
        <div class="session-list">
          <div v-for="s in chat.sessions" :key="s.session_id" class="session-item" :class="{ active: s.session_id === ui.selectedSessionId }">
            <button class="session-main" @click="selectSession(s.session_id)" :title="s.title">{{ s.title || s.session_id }}</button>
            <div class="session-tools">
              <button class="tool-btn" @click="renameSession(s)" title="重命名">✎</button>
              <button class="tool-btn" @click="deleteSession(s)" title="删除">×</button>
            </div>
          </div>
        </div>
      </div>

      <div class="sidebar-actions" v-if="!ui.sidebarCollapsed">
        <button class="btn ghost" @click="loadSessions" :disabled="chat.loadingSessions">{{ chat.loadingSessions ? '...' : '刷新会话' }}</button>
        <div class="account-card">
          <button class="account-head" @click="ui.accountOpen = !ui.accountOpen">
            <span>账户</span>
            <span>{{ ui.accountOpen ? '▴' : '▾' }}</span>
          </button>
          <div class="account-body" v-if="ui.accountOpen">
            <div class="account-line"><strong>{{ account.displayName || account.email || '当前用户' }}</strong></div>
            <div class="account-line">{{ account.email || '-' }}</div>
            <div class="account-line">租户：{{ account.tenantId || '-' }}</div>
            <div class="account-line">角色：{{ accountRoleText() }}</div>
            <div class="account-line">状态：{{ account.userStatus || '-' }}</div>
            <div class="inline-actions">
              <button class="btn ghost" @click="loadMe" :disabled="account.loading">{{ account.loading ? '...' : '刷新' }}</button>
              <button class="btn ghost" @click="ui.userCenterOpen = true">用户中心</button>
            </div>
          </div>
        </div>
        <button class="btn ghost danger" @click="logout">Logout</button>
      </div>
    </aside>

    <main class="chat-main">
      <header class="topbar">
        <div>
          <div class="topbar-title">{{ activeSession?.title || 'New Session' }}</div>
          <div class="topbar-sub">{{ chat.statusText }}</div>
        </div>
        <div class="topbar-meta">
          <span class="pill">KB: {{ chat.mountedKbId || 'none' }}</span>
          <span class="pill">TopK: {{ chat.topK }}</span>
          <span class="pill">Rank: {{ chat.rankMethod }}</span>
        </div>
      </header>

      <section class="messages" ref="messagesRef">
        <div v-if="messages.length === 0" class="empty-state">
          <div class="empty-title">开始对话</div>
          <div class="empty-sub">可在右下角调试抽屉中切换 KB、检索参数与查看运行细节。</div>
        </div>

        <article v-for="(m, idx) in messages" :key="idx" class="message" :class="m.role">
          <div class="bubble">
            <div class="bubble-meta">{{ m.role }} · {{ m.time }}</div>
            <div v-if="m.role === 'assistant'" class="bubble-text bubble-markdown" v-html="m.html || (m.streaming ? '...' : '')"></div>
            <div v-else class="bubble-text">{{ m.content || (m.streaming ? '...' : '') }}</div>
          </div>
        </article>
      </section>

      <section class="composer">
        <textarea v-model="input" placeholder="输入问题，Enter 发送，Shift+Enter 换行" @keydown.enter.exact.prevent="sendMessage" />
        <div class="composer-actions">
          <button class="btn primary" :disabled="!canSend" @click="sendMessage">{{ chat.running ? 'Streaming...' : 'Send' }}</button>
          <button class="btn ghost" :disabled="!chat.running || chat.cancelling" @click="cancelRun">{{ chat.cancelling ? 'Cancelling...' : 'Cancel' }}</button>
        </div>
      </section>
    </main>

    <button class="debug-fab" @click="ui.debugOpen = !ui.debugOpen" :title="ui.debugOpen ? '收起调试面板' : '展开调试面板'">
      {{ ui.debugOpen ? '×' : '⚙' }}
    </button>

    <aside class="debug-drawer" :class="{ open: ui.debugOpen }">
      <div class="debug-head">
        <h2>调试面板</h2>
        <button class="btn ghost" @click="ui.debugOpen = false">关闭</button>
      </div>

      <section class="debug-section">
        <div class="section-title">运行参数</div>
        <label>Chat Base URL<input v-model="cfg.chatBaseUrl" /></label>
        <label>Execution Base URL<input v-model="cfg.executionBaseUrl" /></label>
        <label>Chat Token<input v-model="cfg.chatToken" /></label>
        <label>KB Mount
          <select v-model="chat.mountedKbId">
            <option value="">none</option>
            <option v-for="kb in chat.kbList" :key="kb.kb_id" :value="kb.kb_id">{{ kb.name }}</option>
          </select>
        </label>
        <div class="inline-grid">
          <label>TopK<input type="number" min="1" max="20" v-model="chat.topK" /></label>
          <label>Rank
            <select v-model="chat.rankMethod">
              <option value="vector">vector</option>
              <option value="hybrid">hybrid</option>
              <option value="rrf">rrf</option>
            </select>
          </label>
        </div>
        <label class="toggle-line"><input type="checkbox" v-model="chat.useKb" /> 启用知识库检索</label>
        <label>对话系统 Prompt<textarea v-model="chat.promptDraft" rows="6" /></label>
        <div class="inline-actions">
          <button class="btn ghost" @click="loadChatConfig" :disabled="chat.loadingConfig">刷新配置</button>
          <button class="btn ghost" @click="saveChatPrompt">保存 Prompt</button>
          <button class="btn ghost" @click="refreshKbList" :disabled="chat.loadingKb">刷新 KB</button>
        </div>
      </section>

      <section class="debug-section">
        <div class="section-title">知识库检索来源</div>
        <div v-if="!chat.retrievalSources.length" class="placeholder">当前运行暂无检索来源。</div>
        <div v-for="(s, idx) in chat.retrievalSources" :key="`${s.doc_id}-${s.chunk_id}-${idx}`" class="source-item">
          <button class="source-head" @click="toggleSource(idx)">
            <span>{{ sourceTitle(s, idx) }}</span>
            <span>{{ ui.sourceOpen[idx] ? '▴' : '▾' }}</span>
          </button>
          <div class="source-body" v-if="ui.sourceOpen[idx]">
            <div class="source-row"><strong>tenant</strong> {{ s.tenant_id || '-' }}</div>
            <div class="source-row"><strong>kb</strong> {{ s.kb_id || '-' }}</div>
            <div class="source-row"><strong>text</strong></div>
            <pre>{{ s.text_preview || '' }}</pre>
          </div>
        </div>
      </section>

      <section class="debug-section">
        <div class="section-title">阶段状态</div>
        <div v-if="!chat.stageState.length" class="placeholder">暂无阶段状态。</div>
        <div class="timeline" v-else>
          <div v-for="(s, i) in chat.stageState" :key="`stage-${i}`" class="timeline-item">
            <button class="event-head" @click="toggleStage(`stage-${i}`)">
              <span><strong>{{ s.stageText }}</strong> · {{ s.progress }}</span>
              <span>{{ ui.stageOpen[`stage-${i}`] ? '▴' : '▾' }}</span>
            </button>
            <div class="timeline-sub">{{ s.time }}</div>
            <div class="timeline-sub" v-if="Object.keys(s.info || {}).length && !ui.stageOpen[`stage-${i}`]">{{ toHumanDetail(s.info) }}</div>
            <pre v-if="ui.stageOpen[`stage-${i}`] && Object.keys(s.info || {}).length">{{ stageDetailText(s.info) }}</pre>
          </div>
        </div>
      </section>

      <section class="debug-section">
        <div class="section-title">对话运行细节（API/DB/检索/记忆）</div>
        <div v-if="!chat.debugEvents.length" class="placeholder">暂无运行事件。</div>
        <div class="timeline" v-else>
          <div v-for="ev in chat.debugEvents" :key="ev.id" class="timeline-item">
            <button class="event-head" @click="toggleEvent(ev.id)">
              <span>[{{ ev.kind }}] {{ ev.title }}</span>
              <span>{{ ui.eventOpen[ev.id] ? '▴' : '▾' }}</span>
            </button>
            <div class="timeline-sub">{{ ev.time }}</div>
            <div class="timeline-sub">{{ ev.detail }}</div>
            <pre v-if="ui.eventOpen[ev.id] && (ev.human || ev.data)">{{ eventDetailText(ev) || ev.human || toHumanDetail(ev.data) }}</pre>
          </div>
        </div>
      </section>
    </aside>
  </div>

  <div class="login-shell" v-else>
    <div class="login-card">
      <h1>Agent Powflow Chat</h1>
      <p>登录或注册后开始使用对话功能。</p>

      <div class="auth-tabs">
        <button class="btn ghost" :class="{ active: auth.mode === 'login' }" @click="auth.mode = 'login'">用户登录</button>
        <button class="btn ghost" :class="{ active: auth.mode === 'register' }" @click="auth.mode = 'register'">用户注册</button>
      </div>

      <label>邮箱<input v-model="userAuth.email" /></label>
      <label>密码<input type="password" v-model="userAuth.password" /></label>
      <label v-if="auth.mode === 'register'">显示名称<input v-model="userAuth.displayName" /></label>
      <label class="toggle-line"><input type="checkbox" v-model="auth.remember" /> 记住设置</label>
      <button class="btn primary" :disabled="auth.loggingIn" @click="login">
        {{ auth.loggingIn ? '处理中...' : (auth.mode === 'register' ? '注册' : '登录') }}
      </button>
      <div class="error" v-if="auth.error">{{ auth.error }}</div>
    </div>
  </div>

  <div class="modal-mask" v-if="ui.userCenterOpen" @click.self="ui.userCenterOpen = false">
    <div class="modal-card">
      <div class="modal-head">
        <h3>用户中心</h3>
        <button class="btn ghost" @click="ui.userCenterOpen = false">关闭</button>
      </div>
      <div class="modal-body">
        <label>邮箱<input :value="account.email" disabled /></label>
        <label>租户<input :value="account.tenantId" disabled /></label>
        <label>角色<input :value="accountRoleText()" disabled /></label>
        <label>显示名称<input v-model="account.displayNameDraft" /></label>
        <div class="error" v-if="account.error">{{ account.error }}</div>
      </div>
      <div class="modal-actions">
        <button class="btn ghost" @click="ui.userCenterOpen = false">取消</button>
        <button class="btn primary" @click="saveMe" :disabled="account.saving">{{ account.saving ? '保存中...' : '保存' }}</button>
      </div>
    </div>
  </div>
</template>
