<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { marked } from 'marked'
import AuthPanel from './components/AuthPanel.vue'
import ConversationSidebar from './components/ConversationSidebar.vue'
import WorkspaceAssetsPage from './components/WorkspaceAssetsPage.vue'
import ToastStack from './components/ToastStack.vue'

const narrowWorkspaceViewport = window.matchMedia('(max-width: 1100px)')

const cfg = reactive({
  controlPlaneBaseUrl: '/control',
  chatBaseUrl: '/chat',
  executionBaseUrl: '/execution',
  chatToken: '',
  tenantCode: 'platform',
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
  workspaceOptions: [],
})

const userAuth = reactive({
  email: '',
  password: '',
  displayName: '',
  workspaceName: '',
  invitationToken: '',
  tenantCode: '',
})

const ui = reactive({
  productPage: 'home',
  appearance: localStorage.getItem('powflow.workspace.appearance') || 'dark',
  sidebarCollapsed: narrowWorkspaceViewport.matches,
  debugOpen: false,
  accountOpen: false,
  userCenterOpen: false,
  selectedSessionId: '',
  sourceOpen: {},
  eventOpen: {},
  stageOpen: {},
  draftSessionTitle: '',
})
const collapsedMessageLogs = reactive({})

function applyAppearance(value) {
  const target = value === 'light' ? 'light' : 'dark'
  ui.appearance = target
  document.documentElement.dataset.theme = target
  document.documentElement.style.colorScheme = target
  localStorage.setItem('powflow.workspace.appearance', target)
}

function toggleAppearance() {
  applyAppearance(ui.appearance === 'dark' ? 'light' : 'dark')
}

function handleWorkspaceViewportChange(event) {
  ui.sidebarCollapsed = event.matches
}

function switchProduct(page) {
  ui.productPage = page
  if (narrowWorkspaceViewport.matches) ui.sidebarCollapsed = true
}

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
  useAgent: false,
  agentDefaultEnabled: false,
  topK: 6,
  rankMethod: 'vector',
  promptPreview: '',
  promptDraft: '',
  kbList: [],
  sessions: [],
  retrievalSources: [],
  retrievalMeta: {
    requestedTopK: 0,
    returnedCount: 0,
    previewCount: 0,
    rankMethod: '',
    kbId: '',
    query: '',
  },
  agentActions: [],
  sessionRetrievalSources: [],
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
const notices = ref([])
let noticeSequence = 0
const messagesRef = ref(null)
const composerRef = ref(null)
const composerExpanded = ref(false)

const canSend = computed(() => !chat.running && input.value.trim().length > 0)
const activeSession = computed(() => chat.sessions.find((s) => s.session_id === ui.selectedSessionId) || null)
const displaySessions = computed(() => {
  const items = Array.isArray(chat.sessions) ? [...chat.sessions] : []
  if (ui.draftSessionTitle) {
    return [{ session_id: '__draft__', title: ui.draftSessionTitle, draft: true }, ...items]
  }
  return items
})
const sidebarUser = computed(() => ({
  email: account.email || userAuth.email || '',
  displayName: account.displayName || account.email || '当前用户',
  tenantId: account.tenantId || cfg.tenantId || '',
  roleText: accountRoleText(),
}))
const sidebarSessions = computed(() => displaySessions.value.map((item) => ({
  ...item,
  id: item.session_id,
  last_message: item.last_message || item.preview || '',
})))
const activeConversationId = computed(() => ui.selectedSessionId || (ui.draftSessionTitle ? '__draft__' : ''))
const activeTitle = computed(() => activeSession.value?.title || ui.draftSessionTitle || '新对话')
const activeModelLabel = computed(() => 'Powflow Chat')
const canEditPrompt = computed(() => {
  const roles = Array.isArray(account.roles) ? account.roles.map((x) => String(x)) : []
  return roles.includes('PLATFORM_ADMIN') || roles.includes('TENANT_ADMIN')
})

marked.setOptions({
  gfm: true,
  breaks: true,
})

function renderMarkdown(content) {
  const raw = String(content || '')
  const escaped = raw.replace(/</g, '&lt;')
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

function syncComposerHeight() {
  const el = composerRef.value
  if (!el) return
  el.style.height = '0px'
  const lineHeight = 28
  const maxHeight = 168
  const nextHeight = Math.min(Math.max(el.scrollHeight, lineHeight), maxHeight)
  el.style.height = `${nextHeight}px`
  el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden'
  composerExpanded.value = input.value.includes('\n') || el.scrollHeight > lineHeight + 6
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

watch(input, () => {
  nextTick(syncComposerHeight)
})

function authHeaders(extra = {}) {
  return {
    'X-Source-Service': cfg.sourceService,
    ...extra,
  }
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
  return String(cfg.tenantCode || 'platform').trim() || 'platform'
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

function clipText(value, max = 800) {
  const text = String(value || '').replace(/\r\n/g, '\n').trim()
  if (text.length <= max) return text
  return `${text.slice(0, max)}\n... truncated ${text.length - max} chars`
}

function formatScore(value, digits = 4) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '-'
}

function shortId(value, keep = 10) {
  const text = String(value || '').trim()
  if (!text) return '-'
  if (text.length <= keep * 2 + 1) return text
  return `${text.slice(0, keep)}...${text.slice(-keep)}`
}

function eventDetailText(ev) {
  const data = ev?.data
  if (!data || typeof data !== 'object') return ''
  const lines = []

  if (Array.isArray(data.retrieval_preview)) {
    lines.push(`requested_top_k: ${data.retrieval_requested_top_k ?? data.requested_top_k ?? '-'}`)
    lines.push(`returned_count: ${data.retrieval_returned_count ?? data.returned_count ?? '-'}`)
    lines.push(`preview_count: ${data.retrieval_preview_count ?? data.preview_count ?? data.retrieval_preview.length}`)
    if (data.rank_method) lines.push(`rank_method: ${data.rank_method}`)
    if (data.kb_id) lines.push(`kb_id: ${data.kb_id}`)
    if (data.retrieval_query || data.query) lines.push(`query: ${data.retrieval_query || data.query}`)
    lines.push('')
    lines.push(`retrieval_preview(${data.retrieval_preview.length}):`)
    data.retrieval_preview.forEach((p, idx) => {
      const text = clipText(String(p?.text_preview || '').replace(/\s+/g, ' '), 360)
      lines.push(`#${idx + 1} score=${formatScore(p?.score)} doc=${p?.doc_id || '-'} chunk=${p?.chunk_id || '-'} index=${p?.chunk_index ?? '-'} source=${p?.source || '-'}`)
      if (text) lines.push(`  ${text}`)
    })
    return lines.join('\n').trim()
  }

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

function stageWaitingText(stage, data = {}) {
  const info = data.info && typeof data.info === 'object' ? data.info : {}
  const returned = info.retrieval_returned_count ?? info.returned_count ?? info.count
  const map = {
    START: ['正在接收你的问题', '我会先判断是否需要查知识库或调用工具。'],
    PREPARE: ['正在整理问题上下文', '把当前对话、偏好和可用能力放到一起。'],
    NORMALIZE: ['正在改写检索问题', '让检索词更适合知识库匹配。'],
    MEMORY_LOAD: ['正在读取会话记忆', '看看之前有没有相关背景可以接上。'],
    RETRIEVAL: ['正在查找相关知识片段', '会优先找和你这轮问题最接近的内容。'],
    RETRIEVAL_DONE: ['已完成知识库检索', returned ? `找到了 ${returned} 条候选内容，正在筛选可引用信息。` : '正在筛选可用内容。'],
    LLM_REQUEST: ['正在调用模型生成回答', '已准备好上下文，开始组织最终回复。'],
    STREAMING: ['正在整理回复', '内容会边生成边显示出来。'],
    MEMORY_WRITE: ['正在保存本轮记忆', '把有用信息沉淀到会话上下文里。'],
    DONE: ['回复完成', '本轮对话已经处理完。'],
  }
  return map[stage] || [stageLabel(stage), '正在继续处理，请稍等。']
}

function traceWaitingText(data = {}) {
  const title = String(data.title || '')
  const info = data.info && typeof data.info === 'object' ? data.info : data
  if (Array.isArray(data.retrieval_preview)) {
    const count = data.retrieval_returned_count ?? data.returned_count ?? data.retrieval_preview.length
    return ['正在筛选检索结果', `已看到 ${count} 条候选片段，正在挑选最相关的依据。`]
  }
  if (Array.isArray(data.session_retrieval_preview)) {
    return ['正在回看会话记忆', `找到了 ${data.session_retrieval_preview.length} 条历史线索。`]
  }
  if (/Query Rewrite|改写|rewrite/i.test(title)) return ['正在优化你的问题', '让后续检索和推理更稳定。']
  if (/Memory/i.test(title)) return ['正在读取记忆', '加载与你相关的上下文。']
  if (/retrieval|检索/i.test(title)) return ['正在检索知识库', '查找可以支撑回答的资料。']
  if (/LLM|模型|api/i.test(title)) return ['正在连接模型', '把准备好的上下文交给模型处理。']
  if (info.prompt_messages_count) return ['正在组装提示词', `已准备 ${info.prompt_messages_count} 组消息。`]
  return title ? [title, '系统正在推进当前步骤。'] : ['正在处理请求', '系统还在推进当前任务。']
}

function updateAssistantWaiting(message, title, detail = '') {
  if (!message || message.role !== 'assistant') return
  const nextTitle = title || message.statusLabel || '正在处理'
  if (nextTitle !== message.statusLabel) {
    message.statusChangedAt = performance.now()
    message.thinkingPhraseIndex = 0
  }
  message.statusLabel = nextTitle
  const text = String(detail || '').trim()
  if (!text) return
  const lines = Array.isArray(message.progressLines) ? [...message.progressLines] : []
  if (lines[lines.length - 1] !== text) {
    lines.push(text)
  }
  message.progressLines = lines.slice(-4)
}

function buildThinkingPhrases(message) {
  const primary = String(message?.statusLabel || '正在处理你的请求').trim()
  const elapsed = Math.max(0, performance.now() - Number(message?.pendingStartedAt || performance.now()))
  const stageElapsed = Math.max(0, performance.now() - Number(message?.statusChangedAt || message?.pendingStartedAt || performance.now()))
  const phrases = [primary]

  if (message?.firstTokenAt) {
    phrases.push('生成回答中')
    if (stageElapsed > 9000) phrases.push('正在继续完善回答')
    if (elapsed > 22000) phrases.push('马上就好')
    return phrases
  }

  if (/模型|LLM|调用/i.test(primary)) {
    if (stageElapsed > 4500) phrases.push('等待模型回复中')
    if (stageElapsed > 11000) phrases.push('模型正在思考中，再等一会儿')
  } else if (/检索|知识|片段|召回/i.test(primary)) {
    if (stageElapsed > 5000) phrases.push('检索范围较大，正在继续匹配')
    if (stageElapsed > 12000) phrases.push('还在筛选更相关的信息')
  } else if (/记忆|上下文|提示词|整理|改写/i.test(primary)) {
    if (stageElapsed > 5000) phrases.push('上下文较多，正在压缩整理')
  } else if (stageElapsed > 6000) {
    phrases.push('分析中，再等一会儿')
  }

  if (elapsed > 18000) phrases.push('这一步稍久，还在处理复杂上下文')
  if (elapsed > 30000) phrases.push('马上就好')
  return phrases
}

function refreshAssistantRotatingText(message) {
  if (!message || message.role !== 'assistant') return
  if (message.disableRotating) {
    message.rotatingText = message.statusLabel || '正在处理'
    return
  }
  const phrases = buildThinkingPhrases(message)
  const stageElapsed = Math.max(0, performance.now() - Number(message.statusChangedAt || message.pendingStartedAt || performance.now()))
  const shouldComfort = phrases.length > 1 && stageElapsed > 4500
  const nextIndex = shouldComfort ? Number(message.thinkingPhraseIndex || 0) % phrases.length : 0
  message.thinkingPhraseIndex = nextIndex + 1
  message.rotatingText = phrases[nextIndex]
}

function buildPendingTiming(startedAt, firstTokenAt = 0) {
  const elapsed = Math.max(0, performance.now() - startedAt)
  const parts = [`等待 ${elapsed.toFixed(0)}ms`]
  if (firstTokenAt > 0) {
    parts.push(`首字 ${Math.max(0, firstTokenAt - startedAt).toFixed(0)}ms`)
  }
  return parts.join(' · ')
}

function formatSseLogLine(eventName, data = {}) {
  const title = data.title || data.stage || eventName
  const detail = data.detail || data.message || ''
  const compact = toHumanDetail(data.info && typeof data.info === 'object' ? data.info : data)
  return [`[${eventName}] ${title}`, detail, compact].filter(Boolean).join('\n')
}

function appendAssistantTrace(message, eventName, data = {}) {
  if (!message || message.role !== 'assistant') return
  const lines = Array.isArray(message.trace) ? [...message.trace] : []
  lines.push(formatSseLogLine(eventName, data))
  message.trace = lines.slice(-80)
}

function isMessageLogCollapsed(messageId, type = 'trace') {
  const key = `${messageId || 'active'}:${type}`
  return collapsedMessageLogs[key] !== false
}

function toggleMessageLog(messageId, type = 'trace') {
  const key = `${messageId || 'active'}:${type}`
  collapsedMessageLogs[key] = !isMessageLogCollapsed(messageId, type)
}

function closeOpenMessageLogs() {
  for (const key of Object.keys(collapsedMessageLogs)) {
    collapsedMessageLogs[key] = true
  }
}

function handleDocumentPointerDown(event) {
  const target = event.target
  if (target?.closest?.('.message-log-shell')) return
  closeOpenMessageLogs()
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
    if ((k === 'preview' || k === 'session_recall_breakdown' || k === 'keyword_context_preview' || k === 'agent_tool_steps') && Array.isArray(v)) continue
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
  if (Array.isArray(info.agent_tool_steps) && info.agent_tool_steps.length) {
    lines.push('')
    lines.push(`agent_tool_steps(${info.agent_tool_steps.length}):`)
    info.agent_tool_steps.forEach((p, i) => {
      lines.push(`#${i + 1} round=${p?.round ?? '-'} tool=${p?.tool || '-'} status=${p?.status || '-'}`)
      if (p?.query) lines.push(`  query=${p.query}`)
      if (p?.returned_count !== undefined) lines.push(`  returned=${p.returned_count}`)
      if (p?.summary_chars !== undefined) lines.push(`  summary_chars=${p.summary_chars}`)
      if (p?.reason) lines.push(`  reason=${p.reason}`)
    })
  }
  if (Array.isArray(info.session_recall_breakdown) && info.session_recall_breakdown.length) {
    lines.push('')
    lines.push(`session_recall_breakdown(${info.session_recall_breakdown.length}):`)
    info.session_recall_breakdown.forEach((p, i) => {
      const fusion = typeof p?.fusion_score === 'number' ? p.fusion_score.toFixed(4) : '-'
      const k = typeof p?.keyword_norm === 'number' ? p.keyword_norm.toFixed(3) : '-'
      const v = typeof p?.vector_norm === 'number' ? p.vector_norm.toFixed(3) : '-'
      const r = typeof p?.recency_norm === 'number' ? p.recency_norm.toFixed(3) : '-'
      const text = String(p?.text_preview || '').replace(/\s+/g, ' ').trim()
      lines.push(`#${i + 1} seg=${p?.segment_id || '-'} src=${p?.source || '-'} fusion=${fusion} (k=${k}, v=${v}, r=${r})`)
      if (text) lines.push(`  ${text}`)
    })
  }
  if (Array.isArray(info.keyword_context_preview) && info.keyword_context_preview.length) {
    lines.push('')
    lines.push(`keyword_context_preview(${info.keyword_context_preview.length}):`)
    info.keyword_context_preview.forEach((p, i) => {
      const score = typeof p?.score === 'number' ? p.score.toFixed(3) : '-'
      const overlap = typeof p?.keyword_overlap_count === 'number' ? p.keyword_overlap_count : '-'
      const text = String(p?.text_preview || '').replace(/\s+/g, ' ').trim()
      lines.push(`#${i + 1} seg=${p?.segment_id || '-'} score=${score} overlap=${overlap}`)
      if (text) lines.push(`  ${text}`)
    })
  }
  return lines.join('\n')
}

async function fetchJson(baseUrl, path, options = {}) {
  const url = `${baseUrl.replace(/\/$/, '')}${path}`
  const response = await fetch(url, options)
  const text = await response.text()
  let data = {}
  try { data = text ? JSON.parse(text) : {} } catch { data = { detail: text } }
  if (!response.ok) {
    const safeMessages = {
      400: '提交的信息不完整或格式不正确',
      401: '登录状态无效，请重新登录',
      403: '你没有执行此操作的权限',
      404: '请求的内容不存在或已被移除',
      409: '当前操作存在冲突，请刷新后重试',
      410: '此功能已更新，请刷新页面后重试',
      413: '提交的内容超过允许大小',
      422: '输入内容未通过校验',
      429: '操作过于频繁，请稍后再试',
    }
    const error = new Error(safeMessages[response.status] || (response.status >= 500 ? '服务暂时不可用，请稍后再试' : '请求未能完成'))
    error.status = response.status
    error.data = data
    throw error
  }
  return data
}

function pushNotice(type, title, message = '') {
  const item = { id: `notice-${Date.now()}-${noticeSequence++}`, type, title, message }
  notices.value = [...notices.value, item].slice(-3)
  window.setTimeout(() => dismissNotice(item.id), type === 'error' ? 6500 : 4200)
}

function dismissNotice(id) {
  notices.value = notices.value.filter((item) => item.id !== id)
}

function hydrateFromStorage() {
  try {
    const raw = localStorage.getItem('apf_webapp_auth')
    if (!raw) return
    const data = JSON.parse(raw)
    if (data.executionBaseUrl) cfg.executionBaseUrl = String(data.executionBaseUrl)
    if (data.controlPlaneBaseUrl) cfg.controlPlaneBaseUrl = String(data.controlPlaneBaseUrl)
    if (data.chatBaseUrl) cfg.chatBaseUrl = String(data.chatBaseUrl)
    if (/^https?:\/\/(localhost|127\.0\.0\.1):8099$/i.test(cfg.controlPlaneBaseUrl)) cfg.controlPlaneBaseUrl = '/control'
    if (/^https?:\/\/(localhost|127\.0\.0\.1):8092$/i.test(cfg.chatBaseUrl)) cfg.chatBaseUrl = '/chat'
    if (/^https?:\/\/(localhost|127\.0\.0\.1):8091$/i.test(cfg.executionBaseUrl)) cfg.executionBaseUrl = '/execution'
    if (data.chatToken && isLikelySignedToken(data.chatToken)) cfg.chatToken = String(data.chatToken)
    if (data.tenantCode) cfg.tenantCode = String(data.tenantCode)
    if (data.tenantId) cfg.tenantId = String(data.tenantId)
    if (data.actorId) cfg.actorId = String(data.actorId)
    if (data.topK) chat.topK = Number(data.topK)
    if (data.rankMethod) chat.rankMethod = String(data.rankMethod)
    if (data.useAgent !== undefined) chat.useAgent = Boolean(data.useAgent)
    if (data.authMode && ['login', 'register'].includes(String(data.authMode))) {
      auth.mode = String(data.authMode)
    }
    if (!data.tenantCode) {
      const maybeTenantId = String(data.tenantId || '').trim()
      const isUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(maybeTenantId)
      if (isUuid) cfg.tenantCode = 'platform'
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
      tenantCode: cfg.tenantCode,
      tenantId: cfg.tenantId,
      actorId: cfg.actorId,
      topK: chat.topK,
      rankMethod: chat.rankMethod,
      useAgent: chat.useAgent,
      authMode: auth.mode,
    }),
  )
}

async function refreshKbList() {
  chat.loadingKb = true
  try {
    const data = await fetchJson(cfg.chatBaseUrl, '/api/v1/chat/kbs', { headers: chatHeaders() })
    const items = Array.isArray(data.items) ? data.items : []
    chat.kbList = items
    if (!chat.mountedKbId && chat.kbList.length) chat.mountedKbId = chat.kbList[0].kb_id
    pushDebug('db', 'KB 数据库访问', `已载入 ${chat.kbList.length} 个知识库。`, {
      kb_count: chat.kbList.length,
      tenant_id: cfg.tenantId,
    })
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
    const data = await fetchJson(cfg.chatBaseUrl, '/api/v1/chat/config', { headers: chatHeaders() })
    const item = data.item || {}
    chat.topK = Number(item.chat_default_top_k || 6)
    chat.rankMethod = String(item.chat_default_rank_method || 'vector')
    chat.agentDefaultEnabled = Boolean(item.chat_agent_enabled)
    chat.useAgent = chat.agentDefaultEnabled
    chat.promptPreview = String(item.chat_system_prompt || '')
    chat.promptDraft = chat.promptPreview
    pushDebug('api', 'Chat 配置读取', '已读取对话默认参数与系统提示词。', {
      top_k: chat.topK,
      rank_method: chat.rankMethod,
      agent_enabled: chat.useAgent,
      prompt_chars: chat.promptPreview.length,
    })
  } catch (err) {
    // 用户态不强依赖配置接口，失败时回退默认值避免阻断登录链路
    chat.topK = Number(chat.topK || 6) || 6
    chat.rankMethod = String(chat.rankMethod || 'vector') || 'vector'
    chat.promptPreview = ''
    chat.promptDraft = ''
    chat.statusText = err instanceof Error ? err.message : String(err)
    pushDebug('warn', '配置读取降级', chat.statusText)
  } finally {
    chat.loadingConfig = false
  }
}

async function saveChatPrompt() {
  try {
    if (!canEditPrompt.value) {
      pushDebug('warn', '保存 Prompt 已拦截', '当前用户无管理员权限，不能修改系统 Prompt。')
      return
    }
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
    if (ui.selectedSessionId && !chat.sessions.find((s) => s.session_id === ui.selectedSessionId)) {
      ui.selectedSessionId = ''
    }
    if (!ui.selectedSessionId && messages.value.length === 0 && !ui.draftSessionTitle) {
      ui.draftSessionTitle = '新对话'
    }
  } catch (err) {
    pushDebug('error', '会话列表读取失败', err instanceof Error ? err.message : String(err))
  } finally {
    chat.loadingSessions = false
  }
}

async function createSession() {
  const title = String(ui.draftSessionTitle || '新对话')
  const data = await fetchJson(cfg.chatBaseUrl, '/api/v1/chat/sessions', {
    method: 'POST',
    headers: chatHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ title, kb_id: chat.mountedKbId || null }),
  })
  const sessionId = data.item?.session_id || ''
  if (!sessionId) throw new Error('session_id empty')
  ui.selectedSessionId = sessionId
  ui.draftSessionTitle = ''
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
  if (sessionId === '__draft__') {
    ui.selectedSessionId = ''
    return
  }
  ui.draftSessionTitle = ''
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

async function refreshSelectedSessionMessages() {
  const sessionId = String(ui.selectedSessionId || '').trim()
  if (!sessionId) return
  try {
    await selectSession(sessionId)
  } catch (err) {
    pushDebug('warn', '会话消息刷新失败', err instanceof Error ? err.message : String(err))
  }
}

function resetRunState() {
  chat.retrievalSources = []
  chat.retrievalMeta = {
    requestedTopK: Number(chat.topK || 0),
    returnedCount: 0,
    previewCount: 0,
    rankMethod: chat.rankMethod || '',
    kbId: chat.mountedKbId || '',
    query: '',
  }
  chat.agentActions = []
  chat.sessionRetrievalSources = []
  chat.stageState = []
  chat.runId = ''
  ui.sourceOpen = {}
}

function onAgentAction(data) {
  const item = {
    time: now(),
    title: data.title || `Tool ${data.tool || '-'}`,
    detail: data.detail || '',
    tool: data.tool || '',
    status: data.status || '',
    info: data.info || {},
  }
  chat.agentActions.push(item)
  if (chat.agentActions.length > 50) chat.agentActions.shift()
  chat.statusText = `${item.title} · ${item.status || 'running'}`
  pushDebug('agent', item.title, item.detail, item.info)
}

function updateRetrievalMeta(data = {}) {
  const info = data.info && typeof data.info === 'object' ? data.info : data
  const requested = info.retrieval_requested_top_k ?? info.requested_top_k ?? info.top_k ?? chat.retrievalMeta.requestedTopK
  const returned = info.retrieval_returned_count ?? info.returned_count ?? info.count ?? chat.retrievalMeta.returnedCount
  const preview = info.retrieval_preview_count ?? info.preview_count ?? (Array.isArray(info.preview) ? info.preview.length : chat.retrievalMeta.previewCount)
  chat.retrievalMeta = {
    requestedTopK: Number(requested || 0),
    returnedCount: Number(returned || 0),
    previewCount: Number(preview || 0),
    rankMethod: String(info.rank_method || chat.rankMethod || ''),
    kbId: String(info.kb_id || chat.mountedKbId || ''),
    query: String(info.retrieval_query || info.query || chat.retrievalMeta.query || ''),
  }
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
  if (stage === 'RETRIEVAL_DONE') {
    updateRetrievalMeta(data.info || {})
    chat.retrievalSources = Array.isArray(data.info?.preview) ? data.info.preview : []
  }
}

function onTrace(data) {
  const title = data.title || '运行事件'
  const detail = data.detail || ''
  const payload = {
    ...(data && typeof data === 'object' ? data : {}),
    ...(data.info && typeof data.info === 'object' ? data.info : {}),
    ...(Array.isArray(data.retrieval_preview) ? { retrieval_preview_count: data.retrieval_preview.length } : {}),
    ...(Array.isArray(data.session_retrieval_preview) ? { session_retrieval_preview_count: data.session_retrieval_preview.length } : {}),
    ...(Array.isArray(data.keyword_context_preview) ? { keyword_context_preview_count: data.keyword_context_preview.length } : {}),
  }
  pushDebug('trace', title, detail, payload)
  if (Array.isArray(data.retrieval_preview) && data.retrieval_preview.length) {
    updateRetrievalMeta(payload)
    chat.retrievalSources = data.retrieval_preview
  }
  if (Array.isArray(data.session_retrieval_preview) && data.session_retrieval_preview.length) {
    chat.sessionRetrievalSources = data.session_retrieval_preview
  }
}

async function sendMessage(options = {}) {
  const promptOverride = typeof options === 'string' ? options : options.prompt
  const appendUser = typeof options === 'object' && options.appendUser === false ? false : true
  const replaceAssistantIndex = typeof options === 'object' ? options.replaceAssistantIndex : -1
  if (!promptOverride && !canSend.value) return
  let sessionId = ui.selectedSessionId
  if (!sessionId) sessionId = await createSession()

  const prompt = String(promptOverride || input.value || '').trim()
  if (!prompt) return
  const startedAt = performance.now()
  if (!promptOverride) {
    input.value = ''
    await nextTick()
    syncComposerHeight()
  }
  if (appendUser) {
    messages.value.push({ role: 'user', content: prompt, time: now() })
  }
  const assistantRaw = {
    role: 'assistant',
    content: '',
    time: now(),
    streaming: true,
    html: '',
    id: `assistant_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    statusLabel: '正在理解你的问题',
    progressLines: ['我会根据需要读取记忆、检索知识库或调用工具。'],
    rotatingText: '思考中',
    thinkingPhraseIndex: 0,
    statusChangedAt: startedAt,
    trace: [],
    pendingTimingLabel: buildPendingTiming(startedAt),
    pendingStartedAt: startedAt,
    firstTokenAt: 0,
    disableRotating: false,
  }
  if (replaceAssistantIndex >= 0 && replaceAssistantIndex < messages.value.length) {
    messages.value.splice(replaceAssistantIndex, 1, assistantRaw)
  } else {
    messages.value.push(assistantRaw)
  }
  // IMPORTANT: mutate the proxied object inside the reactive array, not the raw object.
  const assistantIndex = replaceAssistantIndex >= 0 && replaceAssistantIndex < messages.value.length ? replaceAssistantIndex : messages.value.length - 1
  const assistant = messages.value[assistantIndex]

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
      use_agent: chat.useAgent,
      top_k: chat.topK,
      rank_method: chat.rankMethod,
      prompt_chars: prompt.length,
    },
  )

  const payload = {
    message: prompt,
    use_kb: chat.useKb,
    use_agent: chat.useAgent,
    kb_id: chat.mountedKbId || null,
    top_k: Number(chat.topK || 6),
    rank_method: chat.rankMethod,
    pipeline_id: 'default-chat-v1',
    metadata: { source: 'web-app' },
  }

  let timingTimer = null
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
    timingTimer = setInterval(() => {
      if (!assistant.streaming) return
      assistant.pendingTimingLabel = buildPendingTiming(startedAt, assistant.firstTokenAt || 0)
      refreshAssistantRotatingText(assistant)
    }, 3200)
    refreshAssistantRotatingText(assistant)

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
        const dataLines = []
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) eventName = line.slice(6).trim()
          if (line.startsWith('data:')) {
            const rawData = line.slice(5)
            dataLines.push(rawData.startsWith(' ') ? rawData.slice(1) : rawData)
          }
        }
        const dataText = dataLines.join('\n')
        if (!dataText) continue

        let data = {}
        try {
          data = JSON.parse(dataText)
        } catch {
          continue
        }

        if (eventName === 'run') {
          appendAssistantTrace(assistant, eventName, data)
          chat.runId = data.run_id || ''
          updateAssistantWaiting(assistant, '已创建本轮任务', '开始调度检索、工具和模型调用。')
          pushDebug('api', '创建运行实例', '已创建运行实例，可用于取消任务。', { run_id: chat.runId })
        } else if (eventName === 'stage') {
          appendAssistantTrace(assistant, eventName, data)
          onStage(data)
          const [title, detail] = stageWaitingText(data.stage, data)
          updateAssistantWaiting(assistant, title, detail)
        } else if (eventName === 'trace') {
          appendAssistantTrace(assistant, eventName, data)
          onTrace(data)
          const traceTitle = String(data.title || '')
          if (/LLM Stream Fallback|LLM Fallback Failed/i.test(traceTitle)) {
            const err = String(data.info?.error || data.detail || '').trim()
            const title = /Failed/i.test(traceTitle) ? '模型备用请求失败' : '模型流式输出失败，正在切换备用请求'
            updateAssistantWaiting(assistant, title, err || '模型连接失败，但系统仍在尝试备用路径。')
            assistant.disableRotating = true
            assistant.rotatingText = assistant.statusLabel
          } else {
            const [title, detail] = traceWaitingText(data)
            updateAssistantWaiting(assistant, title, detail)
          }
        } else if (eventName === 'agent_action') {
          appendAssistantTrace(assistant, eventName, data)
          onAgentAction(data)
          updateAssistantWaiting(
            assistant,
            data.title || 'Agent 正在调用工具',
            data.tool ? `正在使用 ${data.tool}。` : '正在判断下一步是否需要继续检索或总结。',
          )
        } else if (eventName === 'token') {
          if (!assistant.content && !pendingDelta) {
            assistant.disableRotating = false
            assistant.firstTokenAt = assistant.firstTokenAt || performance.now()
            assistant.pendingTimingLabel = buildPendingTiming(startedAt, assistant.firstTokenAt)
            updateAssistantWaiting(assistant, '正在输出回复', '已经开始生成正文。')
            refreshAssistantRotatingText(assistant)
          }
          if (data.replace) {
            pendingDelta = ''
            assistant.content = ''
          }
          pendingDelta += data.delta || ''
          scheduleFlush()
        } else if (eventName === 'done') {
          appendAssistantTrace(assistant, eventName, data)
          clearInterval(timingTimer)
          if (flushTimer) {
            clearTimeout(flushTimer)
            flushDelta()
          }
          scheduleMarkdownRender(true)
          assistant.streaming = false
          assistant.statusLabel = '回复完成'
          if (!assistant.content && data.response) assistant.content = data.response
          ensureAssistantHtml(assistant)
          const expectedChars = Number(data.response_chars || 0)
          if (expectedChars > 0 && assistant.content.length + 10 < expectedChars) {
            appendAssistantTrace(assistant, 'warn', {
              title: 'SSE Completeness Check',
              detail: `Local streamed chars=${assistant.content.length}, server response chars=${expectedChars}.`,
            })
            pushDebug(
              'warn',
              'SSE 内容可能不完整',
              `本地收到 ${assistant.content.length} 字，服务端完成文本 ${expectedChars} 字。`,
            )
          }
          chat.statusText = 'Completed'
          if (data.session_title_updated && ui.selectedSessionId) {
            const idx = chat.sessions.findIndex((s) => s.session_id === ui.selectedSessionId)
            if (idx >= 0) {
              chat.sessions[idx] = {
                ...chat.sessions[idx],
                title: data.session_title || chat.sessions[idx].title,
              }
            }
          }
          pushDebug('api', '对话完成', '本轮对话已完成并落库。', { response_chars: assistant.content.length })
        } else if (eventName === 'session_title') {
          appendAssistantTrace(assistant, eventName, data)
          const sid = String(data.session_id || ui.selectedSessionId || '')
          const title = String(data.session_title || '').trim()
          if (sid && title) {
            const idx = chat.sessions.findIndex((s) => s.session_id === sid)
            if (idx >= 0) {
              chat.sessions[idx] = { ...chat.sessions[idx], title }
            }
          }
          pushDebug('trace', '会话标题更新', `title=${title || '-'}, stable=${Boolean(data.session_title_stable)}`, {
            session_id: sid || '-',
            session_title: title,
            stable: Boolean(data.session_title_stable),
            updated: Boolean(data.session_title_updated),
            attempt_count: Number(data.attempt_count || 0),
            max_attempts: Number(data.max_attempts || 0),
          })
        } else if (eventName === 'error') {
          appendAssistantTrace(assistant, eventName, data)
          clearInterval(timingTimer)
          if (flushTimer) {
            clearTimeout(flushTimer)
            flushDelta()
          }
          scheduleMarkdownRender(true)
          assistant.streaming = false
          assistant.statusLabel = '处理失败'
          assistant.content = data.message || 'error'
          ensureAssistantHtml(assistant)
          chat.statusText = `Error: ${data.message || 'unknown'}`
          pushDebug('error', '对话执行失败', chat.statusText)
        } else if (eventName === 'cancelled') {
          appendAssistantTrace(assistant, eventName, data)
          clearInterval(timingTimer)
          if (flushTimer) {
            clearTimeout(flushTimer)
            flushDelta()
          }
          scheduleMarkdownRender(true)
          assistant.streaming = false
          assistant.statusLabel = '已取消'
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
    await refreshSelectedSessionMessages()
  } catch (err) {
    chat.statusText = err instanceof Error ? err.message : String(err)
    assistant.streaming = false
    assistant.content = `Request failed: ${chat.statusText}`
    ensureAssistantHtml(assistant)
    pushDebug('error', '流式请求失败', chat.statusText)
    await refreshSelectedSessionMessages()
  } finally {
    if (timingTimer) clearInterval(timingTimer)
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
  if (!isLikelySignedToken(cfg.chatToken)) throw new Error('valid chat token is required')
  await Promise.all([refreshKbList(), loadChatConfig(), loadSessions()])
}

function applyAuthPayload(payload = {}) {
  if (payload.email !== undefined) userAuth.email = String(payload.email || '').trim()
  if (payload.password !== undefined) userAuth.password = String(payload.password || '')
  if (payload.displayName !== undefined) userAuth.displayName = String(payload.displayName || '').trim()
  if (payload.workspaceName !== undefined) userAuth.workspaceName = String(payload.workspaceName || '').trim()
  if (payload.invitationToken !== undefined) userAuth.invitationToken = String(payload.invitationToken || '').trim()
  if (payload.tenantCode !== undefined) userAuth.tenantCode = String(payload.tenantCode || '').trim()
}

async function userLogin(payload = null, { loadWorkspace = true } = {}) {
  if (payload) applyAuthPayload(payload)
  const email = String(userAuth.email || '').trim()
  const password = String(userAuth.password || '')
  if (!email || !password) throw new Error('请输入邮箱或用户名和密码')
  const tenantCode = String(userAuth.tenantCode || '').trim()
  const data = await fetchJson(cfg.controlPlaneBaseUrl, '/api/v1/auth/login', {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      tenantCode: tenantCode || null,
      email,
      password,
    }),
  })
  cfg.chatToken = String(data.accessToken || '')
  if (!isLikelySignedToken(cfg.chatToken)) throw new Error('登录成功但 token 为空')
  cfg.tenantCode = String(data.tenantCode || tenantCode || cfg.tenantCode)
  userAuth.tenantCode = ''
  auth.workspaceOptions = []
  syncIdentityFromToken(cfg.chatToken)
  if (data.tenantId) cfg.tenantId = String(data.tenantId)
  if (data.actorId) cfg.actorId = String(data.actorId)
  if (loadWorkspace) {
    await loadMe()
    await Promise.all([loadSessions(), refreshKbList(), loadChatConfig()])
  }
}

async function userRegister(payload = null) {
  if (payload) applyAuthPayload(payload)
  const email = String(userAuth.email || '').trim()
  const password = String(userAuth.password || '')
  if (!email || !password) throw new Error('请输入邮箱和密码')
  if (password.length < 8) throw new Error('密码至少需要 8 个字符')
  const tenantCode = String(userAuth.tenantCode || '').trim().toLowerCase()
  if (!tenantCode) throw new Error('请输入要创建的工作空间代码')
  if (!/^[a-z0-9][a-z0-9-]{2,62}$/.test(tenantCode)) {
    throw new Error('工作空间代码必须是 3–63 位小写字母、数字或连字符')
  }
  await fetchJson(cfg.controlPlaneBaseUrl, '/api/v1/auth/register', {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      tenantCode,
      workspaceName: userAuth.workspaceName || tenantCode,
      email,
      password,
      displayName: userAuth.displayName || null,
    }),
  })
  auth.mode = 'login'
  userAuth.tenantCode = ''
}

async function acceptWorkspaceInvitation(payload = null) {
  if (payload) applyAuthPayload(payload)
  const email = String(userAuth.email || '').trim()
  const password = String(userAuth.password || '')
  const invitationToken = String(userAuth.invitationToken || '').trim()
  if (!email || !password || !invitationToken) throw new Error('请输入邮箱、密码和邀请令牌')
  if (!isLikelySignedToken(cfg.chatToken)) throw new Error('请先登录已有账户')
  const data = await fetchJson(cfg.controlPlaneBaseUrl, '/api/v1/workspace/invitations/accept', {
    method: 'POST',
    headers: chatHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ invitationToken }),
  })
  const targetTenantCode = String(data.tenant_code || '').trim()
  if (!targetTenantCode) throw new Error('邀请已接受，但目标工作空间信息为空')
  userAuth.invitationToken = ''
  await userLogin({ email, password, tenantCode: targetTenantCode })
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

async function login(payload = null) {
  auth.loggingIn = true
  auth.error = ''
  try {
    if (payload) applyAuthPayload(payload)
    if (auth.mode === 'register') {
      await userRegister()
      chat.statusText = '注册成功，请登录'
      pushNotice('success', '工作空间已创建', '现在可以使用新账户登录。')
      return
    }
    const shouldAcceptInvitation = Boolean(payload?.acceptInvitation)
    await userLogin(null, { loadWorkspace: !shouldAcceptInvitation })
    if (shouldAcceptInvitation) {
      await acceptWorkspaceInvitation()
      chat.statusText = '已加入受邀工作空间'
      pushNotice('success', '已加入工作空间')
    }
    auth.loggedIn = true
    chat.statusText = 'Logged in'
    persistToStorage()
  } catch (err) {
    if (err?.status === 409 && err?.data?.code === 'WORKSPACE_SELECTION_REQUIRED') {
      auth.workspaceOptions = Array.isArray(err.data.items) ? err.data.items : []
      auth.error = err.data.message || '请选择要进入的工作空间'
    } else {
      auth.error = ''
      const message = err?.status === 401 ? '邮箱或密码不正确' : (err instanceof Error ? err.message : '请稍后重试')
      pushNotice('error', auth.mode === 'register' ? '注册未能完成' : '登录未能完成', message)
    }
    auth.loggedIn = false
  } finally {
    auth.loggingIn = false
  }
}

async function loginFromPanel(payload) {
  auth.mode = 'login'
  await login(payload)
}

function resetWorkspaceSelection() {
  auth.workspaceOptions = []
  userAuth.tenantCode = ''
  if (auth.error === '请选择要进入的工作空间') auth.error = ''
}

watch(() => auth.mode, resetWorkspaceSelection)

async function registerFromPanel(payload) {
  auth.mode = 'register'
  await login(payload)
}

function logout() {
  auth.loggedIn = false
  auth.workspaceOptions = []
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
  ui.draftSessionTitle = '新对话'
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
  const docName = item.filename || item.document_name || item.doc_name || item.source || item.doc_id || '-'
  return `#${idx + 1} 文档:${docName} / Chunk:${item.chunk_id || '-'} / 分数:${score}`
}

function sessionSourceTitle(item, idx) {
  const score = typeof item.score === 'number' ? item.score.toFixed(3) : '-'
  const source = String(item.source || '-')
  return `#${idx + 1} seg:${item.segment_id || '-'} / session:${item.session_id || '-'} / ${source} / 分数:${score}`
}

async function copyMessage(text) {
  try {
    await navigator.clipboard.writeText(String(text || ''))
    pushNotice('success', '复制成功', '消息内容已复制到剪贴板')
  } catch {
    pushNotice('error', '复制失败', '请稍后重试')
  }
}

function reaskMessage(text) {
  input.value = String(text || '')
  nextTick(() => {
    syncComposerHeight()
    composerRef.value?.focus()
  })
}

function regenerateAssistant(index) {
  if (chat.running) return
  const msg = messages.value[index]
  if (!msg || msg.role !== 'assistant') return
  let prompt = ''
  for (let i = index - 1; i >= 0; i -= 1) {
    if (messages.value[i]?.role === 'user') {
      prompt = String(messages.value[i].content || '').trim()
      break
    }
  }
  if (!prompt) return
  sendMessage({ prompt, appendUser: false, replaceAssistantIndex: index })
}

onMounted(() => {
  applyAppearance(ui.appearance)
  hydrateFromStorage()
  nextTick(syncComposerHeight)
  document.addEventListener('pointerdown', handleDocumentPointerDown)
  narrowWorkspaceViewport.addEventListener('change', handleWorkspaceViewportChange)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
  narrowWorkspaceViewport.removeEventListener('change', handleWorkspaceViewportChange)
})
</script>

<template>
  <AuthPanel
    v-if="!auth.loggedIn"
    v-model:mode="auth.mode"
    v-model:remember="auth.remember"
    :error="auth.error"
    :loading="auth.loggingIn"
    :appearance="ui.appearance"
    :workspace-options="auth.workspaceOptions"
    @appearance-change="applyAppearance"
    @login="loginFromPanel"
    @register="registerFromPanel"
    @credentials-change="resetWorkspaceSelection"
  />

  <ToastStack :items="notices" @dismiss="dismissNotice" />

  <div v-if="auth.loggedIn" class="app-shell" :class="{ collapsed: ui.sidebarCollapsed }">
    <ConversationSidebar
      :collapsed="ui.sidebarCollapsed"
      :user="sidebarUser"
      :conversations="sidebarSessions"
      :active-id="activeConversationId"
      :product-page="ui.productPage"
      :appearance="ui.appearance"
      @new-conversation="startNewChat"
      @select-conversation="selectSession"
      @rename-conversation="renameSession"
      @delete-conversation="deleteSession"
      @open-settings="ui.userCenterOpen = true"
      @refresh="loadSessions"
      @logout="logout"
      @toggle-collapse="ui.sidebarCollapsed = !ui.sidebarCollapsed"
      @switch-product="switchProduct"
      @toggle-appearance="toggleAppearance"
    />

    <main v-if="ui.productPage === 'chat'" class="workspace" :class="{ expanded: ui.sidebarCollapsed, empty: !messages.length }">
      <header class="workspace-header">
        <div class="workspace-title-block" :class="{ compact: !messages.length }">
          <div class="model-selector">
            <button class="model-selector-button" type="button">
              <span>{{ activeModelLabel }}</span>
              <span class="model-selector-caret">⌄</span>
            </button>
          </div>
          <div class="workspace-subtitle">{{ activeTitle }} · {{ chat.statusText }}</div>
        </div>

        <div class="runtime-group">
          <button class="icon-btn debug-wrench-btn" type="button" title="打开调试工作台" @click="ui.debugOpen = true">
            ⚙
          </button>
        </div>
      </header>

      <section class="chat-stage" ref="messagesRef">
        <div v-if="!messages.length" class="empty-chat">
          <div class="empty-greeting">准备好了，随时开始</div>
          <p>可以直接提问，也可以打开右上角调试工作台配置 KB、TopK、Agent 和系统 Prompt。</p>
        </div>

        <article v-for="(message, index) in messages" :key="message.id || index" class="message-row" :class="message.role">
          <div class="message-content">
            <div class="message-bubble">
              <div v-if="message.streaming && message.role === 'assistant'" class="message-progress">
                <div class="message-progress-title-row">
                  <span class="message-progress-title">正在处理你的请求</span>
                </div>
                <div class="message-thinking-rotator">
                  <span :key="message.rotatingText">{{ message.rotatingText || message.statusLabel || '思考中' }}</span>
                </div>
                <div v-if="message.progressLines?.length" class="message-progress-human">
                  <div v-for="(line, lineIndex) in message.progressLines" :key="lineIndex">{{ line }}</div>
                </div>
                <div v-if="message.pendingTimingLabel" class="message-meta pending-meta">{{ message.pendingTimingLabel }}</div>
                <div v-if="message.content" class="message-text message-text-streaming" v-html="message.html || renderMarkdown(message.content || '')"></div>
              </div>
              <div v-else-if="message.role === 'assistant'" class="message-text" v-html="message.html || renderMarkdown(message.content || '')"></div>
              <div v-else class="message-text">{{ message.content }}</div>
              <div class="message-meta">{{ message.time }}</div>
            </div>

            <div
              v-if="message.trace?.length"
              class="message-log-shell"
              :class="{ open: !isMessageLogCollapsed(message.id, 'trace') }"
            >
              <button class="message-log-toggle" type="button" @click="toggleMessageLog(message.id, 'trace')">
                <span>过程日志</span>
                <strong>{{ message.trace.length }}</strong>
              </button>
              <div class="message-log-float" aria-live="polite">
                <div class="message-log-float-head">
                  <span>过程日志</span>
                  <strong>{{ message.trace.length }} 条</strong>
                </div>
                <div class="message-trace">
                  <div v-for="(line, traceIndex) in message.trace" :key="traceIndex" class="message-trace-line">{{ line }}</div>
                </div>
              </div>
            </div>

            <div v-if="message.role === 'user'" class="message-actions user-actions">
              <button class="action-btn" type="button" title="复制消息" @click="copyMessage(message.content)">⧉</button>
              <button class="action-btn" type="button" title="重新提问" @click="reaskMessage(message.content)">↻</button>
            </div>
            <div v-else-if="!message.streaming" class="message-actions assistant-actions">
              <button class="action-btn" type="button" title="复制消息" @click="copyMessage(message.content)">⧉</button>
              <button class="action-btn" type="button" title="重新生成回复" :disabled="chat.running" @click="regenerateAssistant(index)">↻</button>
            </div>
          </div>
        </article>
      </section>

      <footer class="composer" :class="{ empty: !messages.length }">
        <div class="composer-main" :class="{ 'empty-mode': !messages.length }">
          <div class="composer-input-shell" :class="{ expanded: composerExpanded }">
            <textarea
              ref="composerRef"
              v-model="input"
              :placeholder="messages.length ? '输入你的问题，Enter 发送，Shift+Enter 换行' : '有问题，尽管问'"
              @input="syncComposerHeight"
              @keydown.enter.exact.prevent="sendMessage"
            />
            <div class="composer-inline-tools">
              <label class="toggle-line compact">
                <input v-model="chat.useKb" type="checkbox" />
                <span>KB</span>
              </label>
              <label class="toggle-line compact">
                <input v-model="chat.useAgent" type="checkbox" />
                <span>Agent</span>
              </label>
              <div class="composer-inline-actions">
                <button class="inline-icon-btn" type="button" :disabled="!chat.running || chat.cancelling" title="取消" @click="cancelRun">
                  {{ chat.cancelling ? '…' : '×' }}
                </button>
                <button class="send-icon-btn inline" type="button" :disabled="!canSend" :title="chat.running ? '发送中' : '发送'" @click="sendMessage">
                  {{ chat.running ? '…' : '↑' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </footer>
    </main>

      <WorkspaceAssetsPage
      v-else
      :page="ui.productPage"
      :base-url="cfg.controlPlaneBaseUrl"
      :chat-base-url="cfg.chatBaseUrl"
      :token="cfg.chatToken"
      :appearance="ui.appearance"
      :roles="account.roles"
        :current-user-email="account.email"
        @notify="({ type, title, message }) => pushNotice(type, title, message)"
    />

    <div v-if="ui.productPage === 'chat' && ui.debugOpen" class="debug-workbench-mask" @pointerdown.self="ui.debugOpen = false">
      <section class="debug-workbench powflow-workbench">
        <div class="debug-workbench-head">
          <div>
            <h2>调试工作台</h2>
            <p>集中查看运行参数、Agent 动作、检索来源、阶段状态与事件。</p>
          </div>
          <button class="icon-btn subtle" type="button" @click="ui.debugOpen = false">✕</button>
        </div>

        <div class="workbench-grid">
          <section class="workbench-card config-card">
            <div class="settings-title-row">
              <div class="settings-title">对话参数</div>
              <button class="ghost-btn small" type="button" @click="persistToStorage">保存本地设置</button>
            </div>
            <label><span>Chat Base URL</span><input v-model="cfg.chatBaseUrl" /></label>
            <label><span>Control Base URL</span><input v-model="cfg.controlPlaneBaseUrl" /></label>
            <label><span>KB Mount</span>
              <select v-model="chat.mountedKbId">
                <option value="">不使用指定 KB</option>
                <option v-for="kb in chat.kbList" :key="kb.kb_id" :value="kb.kb_id">{{ kb.name || kb.kb_id }}</option>
              </select>
            </label>
            <div class="form-row compact-row">
              <label><span>TopK</span><input v-model.number="chat.topK" type="number" min="1" max="50" /></label>
              <label><span>Rank</span>
                <select v-model="chat.rankMethod">
                  <option value="vector">vector</option>
                  <option value="hybrid">hybrid</option>
                  <option value="rrf">rrf</option>
                </select>
              </label>
            </div>
            <label class="switch-row"><span>启用知识库检索</span><input v-model="chat.useKb" type="checkbox" /></label>
            <label class="switch-row"><span>启用 Agent 工具循环</span><input v-model="chat.useAgent" type="checkbox" /></label>
            <label><span>对话系统 Prompt</span><textarea v-model="chat.promptDraft" rows="7" :readonly="!canEditPrompt" /></label>
            <div class="modal-actions inline-actions">
              <button class="ghost-btn small" type="button" :disabled="chat.loadingConfig" @click="loadChatConfig">刷新配置</button>
              <button class="ghost-btn small" type="button" :disabled="!canEditPrompt" @click="saveChatPrompt">保存 Prompt</button>
              <button class="ghost-btn small" type="button" :disabled="chat.loadingKb" @click="refreshKbList">刷新 KB</button>
            </div>
          </section>

          <section class="workbench-card">
            <div class="settings-title">Agent 正在做什么</div>
            <div v-if="!chat.agentActions.length" class="empty-panel">当前运行暂无工具动作。</div>
            <div v-for="(item, idx) in chat.agentActions" :key="item.time + '-' + idx" class="agent-action-row">
              <div><strong>{{ item.title }}</strong><span>{{ item.status || 'running' }}</span></div>
              <p>{{ item.detail || item.tool || '-' }}</p>
            </div>
          </section>

          <section class="workbench-card">
            <div class="settings-title">检索概览</div>
            <div class="debug-metrics">
              <div><span>请求 TopK</span><strong>{{ chat.retrievalMeta.requestedTopK || chat.topK }}</strong></div>
              <div><span>实际返回</span><strong>{{ chat.retrievalMeta.returnedCount }}</strong></div>
              <div><span>当前展示</span><strong>{{ chat.retrievalMeta.previewCount || chat.retrievalSources.length }}</strong></div>
            </div>
            <div v-if="chat.retrievalMeta.query" class="source-query">{{ chat.retrievalMeta.query }}</div>
            <div v-if="chat.retrievalMeta.requestedTopK && chat.retrievalMeta.returnedCount < chat.retrievalMeta.requestedTopK" class="debug-warning">
              实际返回少于 TopK，通常是知识库有效切片不足、候选不足或过滤后数量减少。
            </div>
          </section>

          <section class="workbench-card">
            <div class="settings-title">知识库检索来源</div>
            <div v-if="!chat.retrievalSources.length" class="empty-panel">当前运行暂无检索来源。</div>
            <article v-for="(source, idx) in chat.retrievalSources" :key="(source.doc_id || '-') + '-' + (source.chunk_id || '-') + '-' + idx" class="source-card">
              <button class="source-head" type="button" @click="toggleSource(idx)">
                <span>{{ sourceTitle(source, idx) }}</span>
                <span>{{ ui.sourceOpen[idx] ? '收起' : '展开' }}</span>
              </button>
              <p>{{ source.text_preview || '' }}</p>
              <pre v-if="ui.sourceOpen[idx]">{{ JSON.stringify(source, null, 2) }}</pre>
            </article>
          </section>

          <section class="workbench-card">
            <div class="settings-title">会话记忆召回来源</div>
            <div v-if="!chat.sessionRetrievalSources.length" class="empty-panel">当前运行暂无会话召回来源。</div>
            <article v-for="(source, idx) in chat.sessionRetrievalSources" :key="(source.segment_id || '-') + '-' + idx" class="source-card">
              <button class="source-head" type="button" @click="toggleSource('srag-' + idx)">
                <span>{{ sessionSourceTitle(source, idx) }}</span>
                <span>{{ ui.sourceOpen['srag-' + idx] ? '收起' : '展开' }}</span>
              </button>
              <p>{{ source.text_preview || '' }}</p>
              <pre v-if="ui.sourceOpen['srag-' + idx]">{{ JSON.stringify(source, null, 2) }}</pre>
            </article>
          </section>

          <section class="workbench-card">
            <div class="settings-title">阶段状态</div>
            <div v-if="!chat.stageState.length" class="empty-panel">暂无阶段状态。</div>
            <div v-for="(stage, idx) in chat.stageState" :key="'stage-' + idx" class="timeline-item">
              <button class="source-head" type="button" @click="toggleStage('stage-' + idx)">
                <span><strong>{{ stage.stageText }}</strong> · {{ stage.progress }}</span>
                <span>{{ ui.stageOpen['stage-' + idx] ? '收起' : '展开' }}</span>
              </button>
              <div class="timeline-sub">{{ stage.time }}</div>
              <pre v-if="ui.stageOpen['stage-' + idx] && Object.keys(stage.info || {}).length">{{ stageDetailText(stage.info) }}</pre>
            </div>
          </section>

          <section class="workbench-card">
            <div class="settings-title">运行事件</div>
            <div v-if="!chat.debugEvents.length" class="empty-panel">暂无运行事件。</div>
            <div v-for="event in chat.debugEvents" :key="event.id" class="timeline-item">
              <button class="source-head" type="button" @click="toggleEvent(event.id)">
                <span>[{{ event.kind }}] {{ event.title }}</span>
                <span>{{ ui.eventOpen[event.id] ? '收起' : '展开' }}</span>
              </button>
              <div class="timeline-sub">{{ event.time }} · {{ event.detail }}</div>
              <pre v-if="ui.eventOpen[event.id] && (event.human || event.data)">{{ eventDetailText(event) || event.human || toHumanDetail(event.data) }}</pre>
            </div>
          </section>
        </div>
      </section>
    </div>

    <div class="modal-mask" v-if="ui.userCenterOpen" @pointerdown.self="ui.userCenterOpen = false">
      <div class="settings-modal profile-modal">
        <div class="modal-header">
          <div><div class="modal-eyebrow">Account</div><h2>用户中心</h2></div>
          <button class="icon-btn subtle" type="button" @click="ui.userCenterOpen = false">✕</button>
        </div>
        <div class="settings-section">
          <label><span>邮箱</span><input :value="account.email" readonly /></label>
          <label><span>租户</span><input :value="account.tenantId" readonly /></label>
          <label><span>角色</span><input :value="accountRoleText()" readonly /></label>
          <label><span>显示名称</span><input v-model="account.displayNameDraft" /></label>
          <div v-if="account.error" class="auth-error">{{ account.error }}</div>
        </div>
        <div class="modal-actions">
          <button class="ghost-btn" type="button" @click="ui.userCenterOpen = false">取消</button>
          <button class="primary-btn" type="button" :disabled="account.saving" @click="saveMe">{{ account.saving ? '保存中...' : '保存' }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
