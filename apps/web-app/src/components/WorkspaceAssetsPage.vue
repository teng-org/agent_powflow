<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import AgentWorkbench from './AgentWorkbench.vue'
import AgentCanvaWorkbench from './AgentCanvaWorkbench.vue'
import IntegrationManagementPage from './IntegrationManagementPage.vue'
import ToolWorkbenchPage from './ToolWorkbenchPage.vue'

const props = defineProps({
  page: { type: String, required: true },
  baseUrl: { type: String, required: true },
  chatBaseUrl: { type: String, required: true },
  token: { type: String, required: true },
  appearance: { type: String, default: 'dark' },
  roles: { type: Array, default: () => [] },
  currentUserEmail: { type: String, default: '' },
})
const emit = defineEmits(['notify'])
const workspaceRoot = ref(null)

const state = reactive({
  loading: false,
  error: '',
  message: '',
  knowledgeBases: [],
  agents: [],
  workflows: [],
  skills: [],
  mcpConnections: [],
  javascriptTools: [],
  deployments: [],
  apiKeys: {},
  invocations: {},
  deploymentTesters: {},
  documents: {},
  ragIndexes: {},
  members: [],
  invitations: [],
  activeKbId: '',
  activeWorkflowId: '',
  uploadFiles: [],
  revealedSecret: '',
  revealedDeploymentId: '',
  revealedInvitation: '',
  kbViewMode: localStorage.getItem('powflow.kb.view') === 'list' ? 'list' : 'card',
  kbModalOpen: false,
  kbEditingId: '',
  kbDeletingId: '',
  deleteTarget: null,
  activeAgentId: '',
  activeDeploymentId: '',
  deploymentModalOpen: false,
  autoGenerateKeyOnDeployment: true,
})

const forms = reactive({
  kb: { name: '', description: '', tags: '', visibility: 'PRIVATE' },
  workflowName: '',
  agent: { name: '', description: '', instructions: '', workflow_id: '', knowledge_base_ids: [] },
  deployment: { name: '', asset_type: 'AGENT', asset_id: '', asset_version_no: 1, rate_limit_per_minute: 60 },
  kbText: { filename: '', content: '' },
  key: { deployment_id: '', name: '默认密钥', expires_at: '' },
  invitation: { email: '', role_code: 'USER', expires_in_days: 7 },
})

const title = computed(() => ({
  home: '工作空间概览',
  knowledge: '知识库',
  agents: '智能体',
  workflows: 'Workflow',
  skills: 'Skill 管理',
  mcp: 'MCP 接入',
  tools: '工具工作台',
  deployments: 'API 发布',
  members: '团队与权限',
}[props.page] || '工作空间'))

const pageCopy = computed(() => ({
  home: '你的知识、智能体、流程和发布服务都在同一个资产边界内。',
  knowledge: '创建属于你的知识库；默认私有，可选择共享到当前工作空间。',
  agents: '组合 Prompt、知识库和已发布 Workflow，创建多个独立智能体实例。',
  workflows: '从可执行模板创建流程，发布后形成不可变版本供 Agent 或 API 使用。',
  skills: '把稳定的指令、输入输出契约和模型参数发布为可复用、可审计的能力版本。',
  mcp: '接入远程 MCP Server，管理工具目录和密钥引用，并在 Workflow 中安全调用。',
  tools: '陈列、测试和发布 JavaScript 工具，并控制 AI 与 Workflow 的使用范围。',
  deployments: '把已发布资产绑定到稳定调用地址，并创建可轮换、可吊销的 API Key。',
  members: '查看工作空间成员；管理员通过一次性邀请令牌安全地添加成员和分配角色。',
}[props.page] || ''))

const deployableAssets = computed(() => forms.deployment.asset_type === 'AGENT' ? state.agents : state.workflows)
const totalPublished = computed(() => state.agents.filter((x) => x.latest_version_no > 0).length + state.workflows.filter((x) => x.latest_version_no > 0).length)
const canManageMembers = computed(() => props.roles.includes('TENANT_ADMIN') || props.roles.includes('PLATFORM_ADMIN'))
const knowledgeRecords = computed(() => state.knowledgeBases.map((item) => {
  const docs = state.documents[item.kb_id] || []
  const rag = state.ragIndexes[item.kb_id] || { items: [] }
  const activeRevision = String(rag.active_index_revision || '')
  const activeIndex = (rag.items || []).find((index) => index.index_revision === activeRevision || index.status === 'ACTIVE') || null
  return {
    ...item,
    documentCount: docs.length,
    chunkCount: docs.reduce((total, doc) => total + Number(doc.chunk_count || 0), 0),
    readyCount: docs.filter((doc) => ['INDEXED', 'READY', 'SUCCEEDED'].includes(String(doc.status || '').toUpperCase())).length,
    totalSize: docs.reduce((total, doc) => total + Number(doc.size_bytes || doc.file_size || 0), 0),
    ragRevision: activeRevision,
    ragIndex: activeIndex,
    ragState: ragState(activeIndex),
  }
}))
const editingKnowledgeBase = computed(() => state.knowledgeBases.find((item) => item.kb_id === state.kbEditingId) || null)
const activeAgent = computed(() => state.agents.find((item) => item.agent_id === state.activeAgentId) || null)
const activeWorkflow = computed(() => state.workflows.find((item) => item.workflow_id === state.activeWorkflowId) || null)
const activeDeployment = computed(() => state.deployments.find((item) => item.deployment_id === state.activeDeploymentId) || null)

const deploymentStatusText = (status) => ({ ACTIVE: '运行中', PAUSED: '已暂停', ARCHIVED: '已归档' }[status] || status)
const invocationStatusText = (status) => ({ ACCEPTING: '接收中', ACCEPTED: '处理中', COMPLETED: '已完成', FAILED: '失败', CANCELLED: '已取消', REJECTED: '已拒绝', RATE_LIMITED: '已限流' }[status] || status)

function absoluteApiUrl(path) {
  if (String(path).startsWith('/api/v1/deployments/') && import.meta.env.VITE_PUBLIC_API_ORIGIN) {
    return new URL(path, import.meta.env.VITE_PUBLIC_API_ORIGIN).toString()
  }
  return new URL(`${props.baseUrl}${path}`, window.location.origin).toString()
}

function testerFor(item) {
  if (!state.deploymentTesters[item.deployment_id]) {
    state.deploymentTesters[item.deployment_id] = {
      open: false,
      apiKey: '',
      input: JSON.stringify({ message: '你好，请介绍你的能力' }, null, 2),
      stream: false,
      conversationId: '',
      loading: false,
      result: null,
      statusUrl: '',
    }
  }
  return state.deploymentTesters[item.deployment_id]
}

function curlExample(item) {
  let input = {}
  try { input = JSON.parse(testerFor(item).input) } catch { input = { message: '请填写有效 JSON' } }
  const tester = testerFor(item)
  const openAi = item.asset_type === 'AGENT'
  const path = openAi ? `/api/v1/deployments/${item.slug}/v1/chat/completions` : item.invoke_path
  const payload = openAi
    ? { model: item.slug, user: props.currentUserEmail || 'console-debugger', messages: [{ role: 'user', content: String(input.message || JSON.stringify(input)) }], stream: tester.stream, ...(tester.conversationId ? { conversation_id: tester.conversationId } : {}) }
    : { input }
  const body = JSON.stringify(payload).replace(/'/g, "'\\''")
  return `curl ${tester.stream && openAi ? '-N ' : ''}-X POST '${absoluteApiUrl(path)}' -H 'Authorization: Bearer $POWFLOW_API_KEY' -H 'Content-Type: application/json' -H 'X-Trace-Id: your-trace-id' -d '${body}'`
}

async function request(path, options = {}) {
  const response = await fetch(`${props.baseUrl}${path}`, {
    method: options.method || 'GET',
    headers: {
      Authorization: `Bearer ${props.token}`,
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: options.form || (options.body ? JSON.stringify(options.body) : undefined),
  })
  const text = await response.text()
  let data = null
  try { data = text ? JSON.parse(text) : {} } catch { data = { detail: text } }
  if (!response.ok) {
    const uploadLimitMessage = response.status === 413 && data?.error_code === 'UPLOAD_LIMIT_EXCEEDED'
      ? String(data.message || '上传内容超过允许大小')
      : ''
    const uploadFileMessage = response.status === 400 && data?.error_code === 'UPLOAD_FILE_REQUIRED'
      ? String(data.message || '没有收到待上传文件，请重新选择文件')
      : ''
    const assetConflictMessage = response.status === 409 && data?.error_code === 'ASSET_IN_USE'
      ? String(data.message || '该资产仍被其他资源使用')
      : ''
    const safe = uploadLimitMessage || uploadFileMessage || assetConflictMessage || (response.status === 401 ? '登录状态已失效' : response.status === 403 ? '你没有执行此操作的权限' : response.status === 404 ? '请求的内容不存在' : response.status === 409 ? '当前操作存在冲突，请刷新后重试' : response.status === 413 ? '上传内容超过允许大小' : response.status >= 500 ? '服务暂时不可用，请稍后再试' : '请求未能完成，请检查填写内容')
    const error = new Error(safe)
    error.status = response.status
    error.data = data
    throw error
  }
  return data
}

function run(action, successText) {
  state.error = ''
  state.message = ''
  state.loading = true
  return Promise.resolve(action())
    .then(() => {
      state.message = successText || ''
      if (successText) emit('notify', { type: 'success', title: successText, message: '' })
    })
    .catch((error) => {
      state.error = error instanceof Error ? error.message : '操作未能完成'
      emit('notify', { type: 'error', title: '操作未能完成', message: state.error })
    })
    .finally(() => { state.loading = false })
}

async function loadAll() {
  const requests = [
    request('/api/v1/knowledge-bases'),
    request('/api/v1/agents'),
    request('/api/v1/workflows'),
    request('/api/v1/deployments'),
    request('/api/v1/workspace/members'),
    request('/api/v1/skills'),
    request('/api/v1/mcp-connections'),
    request('/api/v1/javascript-tools'),
  ]
  if (canManageMembers.value) requests.push(request('/api/v1/workspace/invitations'))
  const results = await Promise.all(requests)
  state.knowledgeBases = results[0]?.items || []
  state.agents = results[1]?.items || []
  state.workflows = results[2]?.items || []
  state.deployments = results[3]?.items || []
  state.members = results[4]?.items || []
  state.skills = results[5]?.items || []
  state.mcpConnections = results[6]?.items || []
  state.javascriptTools = results[7]?.items || []
  state.invitations = results[8]?.items || []
  if (['home', 'knowledge'].includes(props.page)) await loadKnowledgeStats()
  if (props.page === 'deployments') {
    await loadDeploymentSummaries()
    if (state.activeDeploymentId && !state.deployments.some((item) => item.deployment_id === state.activeDeploymentId)) {
      state.activeDeploymentId = ''
    }
  }
}

function refresh() { return run(loadAll, '') }

function tagValues(value) {
  return [...new Set(String(value || '').split(/[,，]/).map((item) => item.trim()).filter(Boolean))].slice(0, 10)
}

async function loadKnowledgeStats() {
  await Promise.allSettled(state.knowledgeBases.map(async (item) => {
    const [documents, ragIndexes] = await Promise.allSettled([
      request(`/api/v1/knowledge-bases/${item.kb_id}/documents?limit=500`),
      request(`/api/v1/knowledge-bases/${item.kb_id}/rag/indexes`),
    ])
    state.documents[item.kb_id] = documents.status === 'fulfilled' ? (documents.value?.items || []) : []
    state.ragIndexes[item.kb_id] = ragIndexes.status === 'fulfilled' ? ragIndexes.value : { items: [] }
  }))
}

function ragState(index) {
  if (!index) return { tone: 'empty', label: '待建立索引', detail: '暂无活动版本' }
  const gate = String(index.gate_status || '').toUpperCase()
  if (String(index.status || '').toUpperCase() === 'FAILED' || gate === 'FAILED') return { tone: 'danger', label: '索引异常', detail: '质量检查未通过' }
  if (gate === 'PENDING') return { tone: 'pending', label: '待质量评测', detail: '候选版本尚未放量' }
  if (['PASSED', 'WAIVED'].includes(gate)) return { tone: 'ready', label: '混合检索就绪', detail: '向量与词法检索可用' }
  return { tone: 'neutral', label: '索引可用', detail: '建议完成质量评测' }
}

function openCreateKnowledgeBase() {
  state.kbEditingId = ''
  Object.assign(forms.kb, { name: '', description: '', tags: '', visibility: 'PRIVATE' })
  state.kbModalOpen = true
}

function openEditKnowledgeBase(item) {
  state.kbEditingId = item.kb_id
  Object.assign(forms.kb, {
    name: item.name || '',
    description: item.description || '',
    tags: Array.isArray(item.tags) ? item.tags.join('，') : '',
    visibility: item.visibility === 'WORKSPACE' ? 'WORKSPACE' : 'PRIVATE',
  })
  state.kbModalOpen = true
}

async function saveKnowledgeBase() {
  const body = { ...forms.kb, tags: tagValues(forms.kb.tags) }
  delete body.tagsText
  if (state.kbEditingId) {
    await request(`/api/v1/knowledge-bases/${state.kbEditingId}/metadata`, { method: 'PATCH', body })
  } else {
    await request('/api/v1/knowledge-bases', { method: 'POST', body })
  }
  state.kbModalOpen = false
  await loadAll()
}

async function deleteKnowledgeBase(item) {
  await request(`/api/v1/knowledge-bases/${item.kb_id}`, { method: 'DELETE' })
  state.kbDeletingId = ''
  await loadAll()
}

function setKbView(mode) {
  state.kbViewMode = mode
  localStorage.setItem('powflow.kb.view', mode)
}

function avatarStyle(item) {
  let hash = 0
  for (const char of String(item.name || 'Knowledge')) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0
  const hue = Math.abs(hash) % 360
  return { '--kb-hue': hue, '--kb-hue-two': (hue + 54) % 360 }
}

function avatarLabel(item) {
  const words = String(item.name || '知识库').trim().split(/\s+/)
  return words.length > 1 ? `${words[0][0]}${words[1][0]}`.toUpperCase() : words[0].slice(0, 2).toUpperCase()
}

function visibilityLabel(value) { return value === 'WORKSPACE' ? '工作空间可见' : '仅自己可见' }
function formatDate(value) { return value ? new Date(value).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' }) : '刚刚' }

const managerWindows = new Map()
function normalizedOrigin(value, fallback) {
  try { return new URL(String(value || fallback), window.location.origin).origin } catch { return fallback }
}
function managerOrigin() {
  return normalizedOrigin(import.meta.env.VITE_PUBLIC_KB_ORIGIN, `${window.location.protocol}//${window.location.hostname}:5180`)
}
function openKnowledgeManager(item, view = 'docs') {
  const url = `${managerOrigin()}/?kb=${encodeURIComponent(item.kb_id)}&source=workspace&view=${encodeURIComponent(view)}`
  const popup = window.open(url, `powflow-kb-${item.kb_id}`)
  if (!popup) {
    emit('notify', { type: 'warning', title: '新窗口被浏览器拦截', message: '请允许此站点打开窗口后重试。' })
    return
  }
  managerWindows.set(popup, item.kb_id)
}

function handleManagerMessage(event) {
  if (event.origin !== managerOrigin() || event.data?.type !== 'powflow-kb-ready') return
  const kbId = managerWindows.get(event.source)
  if (!kbId) return
  event.source.postMessage({
    type: 'powflow-kb-auth', token: props.token, kbId, appearance: props.appearance,
    controlPlaneBaseUrl: '/control',
  }, event.origin)
}

async function loadDocuments(kbId) {
  const data = await request(`/api/v1/knowledge-bases/${kbId}/documents`)
  state.activeKbId = kbId
  state.documents[kbId] = data?.items || []
}

async function ingestKnowledgeText() {
  const kbId = state.activeKbId
  await request(`/api/v1/knowledge-bases/${kbId}/documents/ingest`, {
    method: 'POST',
    body: { ...forms.kbText, chunk_method: 'sentence', chunk_size: 500, chunk_overlap: 50, auto_summary: false },
  })
  Object.assign(forms.kbText, { filename: '', content: '' })
  await loadDocuments(kbId)
}

function selectKnowledgeFiles(event) {
  state.uploadFiles = Array.from(event.target.files || [])
}

async function uploadKnowledgeFiles() {
  const data = new FormData()
  state.uploadFiles.forEach((file) => data.append('file', file))
  data.append('process_mode', 'UPLOAD_CLEAN_CHUNK_EMBED')
  data.append('chunk_method', 'sentence')
  data.append('chunk_size', '500')
  data.append('chunk_overlap', '50')
  const result = await request(`/api/v1/knowledge-bases/${state.activeKbId}/documents/upload`, { method: 'POST', form: data })
  if (Number(result?.skipped_count || 0) > 0) {
    const reason = String(result?.skipped?.[0]?.reason || '部分文件未处理')
    emit('notify', { type: 'warning', title: `${result.skipped_count} 个文件未处理`, message: reason })
  }
  state.uploadFiles = []
  await loadDocuments(state.activeKbId)
}

const defaultWorkflow = () => ({
  entry_node_id: 'start-1',
  nodes: [
    { id: 'start-1', type: 'start', config: {} },
    { id: 'llm-1', type: 'llm.generate', config: {} },
    { id: 'end-1', type: 'end', config: {} },
  ],
  edges: [
    { id: 'edge-1', source_node_id: 'start-1', target_node_id: 'llm-1', condition_label: '' },
    { id: 'edge-2', source_node_id: 'llm-1', target_node_id: 'end-1', condition_label: '' },
  ],
})

async function createWorkflow() {
  const data = await request('/api/v1/workflows', {
    method: 'POST',
    body: { name: forms.workflowName, description: '', dsl: defaultWorkflow(), layout: {} },
  })
  forms.workflowName = ''
  await loadAll()
  state.activeWorkflowId = data?.item?.workflow_id || ''
  nextTick(() => workspaceRoot.value?.scrollTo({ top: 0, behavior: 'auto' }))
}

async function publishWorkflow(item) {
  await request(`/api/v1/workflows/${item.workflow_id}/publish`, {
    method: 'POST', body: { expected_revision: item.revision },
  })
  await loadAll()
}

function editWorkflow(item) {
  state.activeWorkflowId = item.workflow_id
  nextTick(() => workspaceRoot.value?.scrollTo({ top: 0, behavior: 'auto' }))
}

function updateWorkflowInState(item) {
  const index = state.workflows.findIndex((workflow) => workflow.workflow_id === item?.workflow_id)
  if (index >= 0) state.workflows[index] = item
}

async function handleWorkflowPublished() {
  const activeId = state.activeWorkflowId
  await loadAll()
  state.activeWorkflowId = activeId
}

async function createAgent() {
  const data = await request('/api/v1/agents', {
    method: 'POST',
    body: {
      ...forms.agent,
      model_config: {},
      visibility: 'PRIVATE',
    },
  })
  Object.assign(forms.agent, { name: '', description: '', instructions: '', workflow_id: '', knowledge_base_ids: [] })
  await loadAll()
  state.activeAgentId = data?.item?.agent_id || ''
}

async function publishAgent(item) {
  await request(`/api/v1/agents/${item.agent_id}/publish`, { method: 'POST' })
  await loadAll()
}

function updateAgentInState(item) {
  const index = state.agents.findIndex((agent) => agent.agent_id === item?.agent_id)
  if (index >= 0) state.agents[index] = item
}

function openAgentWorkbench(item) {
  state.activeAgentId = item.agent_id
  nextTick(() => workspaceRoot.value?.scrollTo({ top: 0, behavior: 'auto' }))
}

async function handleAgentPublished() {
  await loadAll()
}

function openAssetDelete(kind, item) {
  const id = item?.agent_id || item?.workflow_id || item?.deployment_id || ''
  if (!id) return
  state.deleteTarget = {
    kind,
    id,
    name: item.name || '未命名资产',
    published: Number(item.latest_version_no || item.asset_version_no || 0) > 0,
  }
}

function deleteTargetType() {
  return ({ agent: '智能体', workflow: 'Workflow', deployment: 'API 发布' })[state.deleteTarget?.kind] || '资产'
}

function deleteTargetDescription() {
  if (state.deleteTarget?.kind === 'deployment') return '服务将立即停止，所有有效 API Key 会被吊销，历史调用记录仍会保留。'
  if (state.deleteTarget?.published) return '已发布版本会归档保存，资产将从工作空间隐藏。若仍被 API 发布或其他资产使用，系统会阻止删除。'
  return '未发布草稿将被永久删除，此操作无法撤销。'
}

async function deleteWorkspaceAsset() {
  const target = state.deleteTarget
  if (!target) return
  const path = target.kind === 'agent'
    ? `/api/v1/agents/${target.id}`
    : target.kind === 'workflow'
      ? `/api/v1/workflows/${target.id}`
      : `/api/v1/deployments/${target.id}`
  await request(path, { method: 'DELETE' })
  if (target.kind === 'agent' && state.activeAgentId === target.id) state.activeAgentId = ''
  if (target.kind === 'workflow' && state.activeWorkflowId === target.id) state.activeWorkflowId = ''
  if (target.kind === 'deployment' && state.activeDeploymentId === target.id) state.activeDeploymentId = ''
  state.deleteTarget = null
  await loadAll()
}

function selectDeploymentAsset() {
  const selected = deployableAssets.value.find((item) => (item.agent_id || item.workflow_id) === forms.deployment.asset_id)
  forms.deployment.asset_version_no = selected?.latest_version_no || 1
  if (!forms.deployment.name && selected) forms.deployment.name = `${selected.name} API`
}

async function createDeployment() {
  const data = await request('/api/v1/deployments', { method: 'POST', body: forms.deployment })
  const createdId = data?.item?.deployment_id || ''
  const createdName = data?.item?.name || forms.deployment.name
  Object.assign(forms.deployment, { name: '', asset_id: '', asset_version_no: 1, rate_limit_per_minute: 60 })
  state.deploymentModalOpen = false
  await loadAll()
  if (!createdId) return
  await openDeployment(state.deployments.find((item) => item.deployment_id === createdId))
  if (!state.autoGenerateKeyOnDeployment) {
    emit('notify', { type: 'success', title: 'API 发布已创建', message: '可在访问控制中签发 API Key' })
    return
  }
  try {
    await issueKey(createdId, `${createdName} 默认密钥`)
    emit('notify', { type: 'success', title: 'API 发布与密钥已就绪', message: '密钥已自动生成并填入在线调用' })
  } catch {
    emit('notify', { type: 'warning', title: 'API 发布已创建', message: '自动生成密钥失败，请在访问控制中重试' })
  }
}

async function pauseDeployment(item) {
  const status = item.status === 'ACTIVE' ? 'PAUSED' : 'ACTIVE'
  await request(`/api/v1/deployments/${item.deployment_id}/status`, { method: 'PATCH', body: { status } })
  await loadAll()
}

async function loadKeys(deploymentId) {
  const data = await request(`/api/v1/deployments/${deploymentId}/api-keys`)
  state.apiKeys[deploymentId] = data?.items || []
}

async function loadInvocations(deploymentId) {
  const data = await request(`/api/v1/deployments/${deploymentId}/invocations`)
  state.invocations[deploymentId] = data?.items || []
}

async function loadDeploymentSummaries() {
  await Promise.allSettled(state.deployments.map((item) => loadInvocations(item.deployment_id)))
}

async function openDeployment(item) {
  if (!item) return
  state.activeDeploymentId = item.deployment_id
  testerFor(item).open = true
  await Promise.all([loadKeys(item.deployment_id), loadInvocations(item.deployment_id)])
  nextTick(() => workspaceRoot.value?.scrollTo({ top: 0, behavior: 'auto' }))
}

function closeDeployment() {
  state.activeDeploymentId = ''
  state.revealedSecret = ''
  state.revealedDeploymentId = ''
  nextTick(() => workspaceRoot.value?.scrollTo({ top: 0, behavior: 'auto' }))
}

function deploymentLogs(item) {
  return state.invocations[item.deployment_id] || []
}

function deploymentLastCall(item) {
  return deploymentLogs(item)[0] || null
}

async function issueKey(deploymentId, name, expiresAt = null) {
  const data = await request(`/api/v1/deployments/${deploymentId}/api-keys`, {
    method: 'POST', body: { name, expires_at: expiresAt },
  })
  state.revealedSecret = data.secret || ''
  state.revealedDeploymentId = deploymentId
  testerFor({ deployment_id: deploymentId }).apiKey = data.secret || ''
  await loadKeys(deploymentId)
  return data
}

async function createKey() {
  await issueKey(
    forms.key.deployment_id,
    forms.key.name,
    forms.key.expires_at ? new Date(forms.key.expires_at).toISOString() : null,
  )
}

async function autoCreateKey(item) {
  const stamp = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date()).replaceAll('/', '').replaceAll(':', '').replaceAll(' ', '-')
  await issueKey(item.deployment_id, `自动密钥 ${stamp}`)
  emit('notify', { type: 'success', title: 'API Key 已自动生成', message: '密钥已填入在线调用，请立即复制保存' })
}

async function revokeKey(item) {
  await request(`/api/v1/deployment-api-keys/${item.api_key_id}/revoke`, { method: 'POST' })
  const tester = state.deploymentTesters[item.deployment_id]
  if (tester?.apiKey?.startsWith(item.key_prefix)) tester.apiKey = ''
  if (state.revealedDeploymentId === item.deployment_id && state.revealedSecret.startsWith(item.key_prefix)) {
    state.revealedSecret = ''
    state.revealedDeploymentId = ''
  }
  await loadKeys(item.deployment_id)
}

async function invokeDeployment(item) {
  const tester = testerFor(item)
  tester.loading = true
  tester.result = null
  tester.statusUrl = ''
  try {
    let input
    try { input = JSON.parse(tester.input) } catch { throw new Error('input 必须是有效的 JSON') }
    const isAgent = item.asset_type === 'AGENT'
    const path = isAgent ? `/api/v1/deployments/${item.slug}/v1/chat/completions` : item.invoke_path
    const payload = isAgent
      ? {
          model: item.slug,
          user: props.currentUserEmail || 'console-debugger',
          messages: [{ role: 'user', content: String(input.message || JSON.stringify(input)) }],
          stream: tester.stream,
          ...(tester.conversationId ? { conversation_id: tester.conversationId } : {}),
        }
      : { input }
    const response = await fetch(absoluteApiUrl(path), {
      method: 'POST',
      headers: { Authorization: `Bearer ${tester.apiKey.trim()}`, 'Content-Type': 'application/json', 'X-Trace-Id': crypto.randomUUID() },
      body: JSON.stringify(payload),
    })
    const data = tester.stream && isAgent && response.ok
      ? await readOpenAiStream(response, tester)
      : await response.json().catch(() => ({}))
    tester.result = { httpStatus: response.status, data }
    tester.statusUrl = data.status_url || ''
    tester.conversationId = data?.powflow?.conversation_id || tester.conversationId
    if (!response.ok) throw new Error(response.status === 401 ? 'API Key 无效、已过期或已吊销' : response.status === 429 ? '已触发发布限流' : '调用失败，请检查发布状态和输入')
    emit('notify', { type: 'success', title: item.asset_type === 'WORKFLOW' ? '工作流已提交' : '调用已完成', message: data.trace_id ? `Trace ${data.trace_id}` : '' })
    await loadInvocations(item.deployment_id)
  } finally {
    tester.loading = false
  }
}

async function readOpenAiStream(response, tester) {
  const reader = response.body?.getReader()
  if (!reader) return {}
  const decoder = new TextDecoder()
  let buffer = ''
  let content = ''
  let extension = {}
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() || ''
    for (const frame of frames) {
      const dataLine = frame.split('\n').find((line) => line.startsWith('data:'))
      const raw = dataLine?.slice(5).trim()
      if (!raw || raw === '[DONE]') continue
      try {
        const chunk = JSON.parse(raw)
        content += chunk?.choices?.[0]?.delta?.content || ''
        if (chunk.powflow) extension = { ...extension, ...chunk.powflow }
        tester.result = {
          httpStatus: response.status,
          data: { object: 'chat.completion.stream', choices: [{ message: { role: 'assistant', content } }], powflow: extension },
        }
      } catch {
        // Ignore comments and non-JSON SSE control frames.
      }
    }
    if (done) break
  }
  return { object: 'chat.completion.stream', choices: [{ message: { role: 'assistant', content } }], powflow: extension }
}

async function queryDeploymentRun(item) {
  const tester = testerFor(item)
  if (!tester.statusUrl) return
  tester.loading = true
  try {
    const response = await fetch(absoluteApiUrl(tester.statusUrl), { headers: { Authorization: `Bearer ${tester.apiKey.trim()}` } })
    const data = await response.json().catch(() => ({}))
    tester.result = { httpStatus: response.status, data }
    if (!response.ok) throw new Error(response.status === 401 ? 'API Key 无效、已过期或已吊销' : '运行结果查询失败')
    if (['succeeded', 'failed', 'cancelled'].includes(data.status)) tester.statusUrl = ''
    await loadInvocations(item.deployment_id)
  } finally {
    tester.loading = false
  }
}

async function createInvitation() {
  const data = await request('/api/v1/workspace/invitations', {
    method: 'POST',
    body: {
      email: forms.invitation.email,
      roleCode: forms.invitation.role_code,
      expiresInDays: Number(forms.invitation.expires_in_days || 7),
    },
  })
  state.revealedInvitation = data.invitation_token || ''
  Object.assign(forms.invitation, { email: '', role_code: 'USER', expires_in_days: 7 })
  await loadAll()
}

async function revokeInvitation(item) {
  await request(`/api/v1/workspace/invitations/${item.invitationId}`, { method: 'DELETE' })
  await loadAll()
}

async function patchMember(item, action) {
  const isAdmin = (item.roles || []).includes('TENANT_ADMIN')
  const body = action === 'status'
    ? { status: item.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE' }
    : { roles: isAdmin ? ['USER'] : ['USER', 'TENANT_ADMIN'] }
  await request(`/api/v1/workspace/members/${item.userId}`, { method: 'PATCH', body })
  await loadAll()
}

async function copy(value) {
  await navigator.clipboard.writeText(value)
  state.message = '已复制到剪贴板'
  emit('notify', { type: 'success', title: '复制成功', message: '已复制到剪贴板' })
}

watch(() => props.page, (page) => {
  state.error = ''; state.message = ''; state.revealedSecret = ''; state.revealedInvitation = ''; state.activeAgentId = ''; state.activeWorkflowId = ''; state.activeDeploymentId = ''; state.deploymentModalOpen = false
  if (page === 'deployments') loadDeploymentSummaries()
})
onMounted(() => { window.addEventListener('message', handleManagerMessage); refresh() })
onBeforeUnmount(() => window.removeEventListener('message', handleManagerMessage))
</script>

<template>
  <main ref="workspaceRoot" class="asset-workspace" :class="`theme-${appearance}`">
    <AgentWorkbench
      v-if="page === 'agents' && activeAgent"
      :agent="activeAgent"
      :base-url="baseUrl"
      :chat-base-url="chatBaseUrl"
      :token="token"
      :knowledge-bases="state.knowledgeBases"
      :workflows="state.workflows"
      :skills="state.skills"
      :mcp-connections="state.mcpConnections"
      :javascript-tools="state.javascriptTools"
      @close="state.activeAgentId = ''"
      @saved="updateAgentInState"
      @published="handleAgentPublished"
      @notify="(payload) => emit('notify', payload)"
    />
    <AgentCanvaWorkbench
      v-else-if="page === 'workflows' && activeWorkflow"
      :workflow="activeWorkflow"
      :base-url="baseUrl"
      :token="token"
      :appearance="appearance"
      :knowledge-bases="state.knowledgeBases"
      :skills="state.skills"
      :mcp-connections="state.mcpConnections"
      :javascript-tools="state.javascriptTools"
      @close="state.activeWorkflowId = ''"
      @saved="updateWorkflowInState"
      @published="handleWorkflowPublished"
      @notify="(payload) => emit('notify', payload)"
    />
    <template v-else>
    <header v-if="!(page === 'deployments' && activeDeployment)" class="asset-header">
      <div><span class="asset-eyebrow">Powflow Workspace</span><h1>{{ title }}</h1><p>{{ pageCopy }}</p></div>
      <button class="asset-button ghost" type="button" :disabled="state.loading" @click="refresh">{{ state.loading ? '刷新中…' : '刷新' }}</button>
    </header>

    <IntegrationManagementPage
      v-if="page === 'skills' || page === 'mcp'"
      :kind="page"
      :base-url="baseUrl"
      :token="token"
      @changed="loadAll"
      @notify="(payload) => emit('notify', payload)"
    />
    <ToolWorkbenchPage v-else-if="page === 'tools'" :base-url="baseUrl" :token="token" :items="state.javascriptTools" @changed="refresh" @notify="emit('notify',$event)" />

    <template v-else-if="page === 'home'">
      <section class="asset-stats">
        <article><span>知识库</span><strong>{{ state.knowledgeBases.length }}</strong><small>归属于当前用户</small></article>
        <article><span>智能体</span><strong>{{ state.agents.length }}</strong><small>可独立配置多个实例</small></article>
        <article><span>Workflow</span><strong>{{ state.workflows.length }}</strong><small>{{ totalPublished }} 个已发布资产版本</small></article>
        <article><span>API 发布</span><strong>{{ state.deployments.length }}</strong><small>稳定外部调用地址</small></article>
      </section>
      <section class="asset-panel welcome-panel">
        <div><span class="asset-eyebrow">推荐路径</span><h2>从知识到可调用的 AI 应用</h2></div>
        <ol><li>创建知识库并整理文档</li><li>创建并发布 Workflow</li><li>把 Workflow 与 Prompt 组合成 Agent</li><li>发布 Deployment 并签发 API Key</li></ol>
      </section>
    </template>

    <template v-else-if="page === 'knowledge'">
      <section class="knowledge-toolbar">
        <div><strong>{{ knowledgeRecords.length }} 个知识库</strong><span>集中管理你的资料、权限与索引状态</span></div>
        <div class="knowledge-toolbar-actions">
          <span class="view-switch" aria-label="显示方式"><button :class="{ active: state.kbViewMode === 'card' }" title="卡片视图" @click="setKbView('card')">▦</button><button :class="{ active: state.kbViewMode === 'list' }" title="列表视图" @click="setKbView('list')">☷</button></span>
          <button v-if="state.kbViewMode === 'list'" class="asset-button primary" @click="openCreateKnowledgeBase">＋ 新建知识库</button>
        </div>
      </section>

      <section v-if="state.kbViewMode === 'card'" class="asset-grid knowledge-grid">
        <button class="kb-create-card" type="button" @click="openCreateKnowledgeBase"><span>＋</span><strong>新建知识库</strong><small>添加资料与团队知识</small></button>
        <article v-for="item in knowledgeRecords" :key="item.kb_id" class="asset-card kb-product-card" :class="{ 'is-frozen': item.frozen }">
          <div class="asset-card-head">
            <span class="kb-avatar" :class="{ 'is-frozen': item.frozen }" :style="avatarStyle(item)">{{ avatarLabel(item) }}</span>
            <span class="kb-card-status">
              <span class="asset-pill">{{ visibilityLabel(item.visibility) }}</span>
              <span v-if="item.frozen" class="kb-freeze-pill"><svg aria-hidden="true" viewBox="0 0 16 16"><path d="M5 7V5.5a3 3 0 0 1 6 0V7m-7 0h8v6H4z" /></svg>已冻结</span>
            </span>
          </div>
          <h3>{{ item.name }}</h3><p>{{ item.description || item.summary || '还没有描述，编辑知识库补充用途说明。' }}</p>
          <div class="kb-tags"><span v-for="tag in item.tags || []" :key="tag">#{{ tag }}</span></div>
          <button class="kb-rag-status" :class="`is-${item.ragState.tone}`" type="button" @click="openKnowledgeManager(item, 'rag')"><span class="kb-rag-signal"><i></i><i></i><i></i></span><span><strong>{{ item.ragState.label }}</strong><small>{{ item.ragRevision || item.ragState.detail }}</small></span><b>查看 ›</b></button>
          <dl class="kb-metrics"><div><dt>文档</dt><dd>{{ item.documentCount }}</dd></div><div><dt>切片</dt><dd>{{ item.chunkCount }}</dd></div><div><dt>已就绪</dt><dd>{{ item.readyCount }}</dd></div></dl>
          <footer><span>更新于 {{ formatDate(item.updated_at) }}</span><span class="card-actions"><button class="asset-link" @click="openEditKnowledgeBase(item)">编辑</button><button class="asset-link" @click="openKnowledgeManager(item)">管理文档</button><button class="asset-link danger" @click="state.kbDeletingId = item.kb_id">删除</button></span></footer>
        </article>
      </section>

      <section v-else class="kb-table-shell">
        <table class="kb-table"><thead><tr><th>知识库</th><th>权限与状态</th><th>标签</th><th>文档</th><th>切片</th><th>检索状态</th><th>更新时间</th><th></th></tr></thead><tbody><tr v-for="item in knowledgeRecords" :key="item.kb_id" :class="{ 'is-frozen': item.frozen }"><td><span class="kb-table-name"><span class="kb-avatar small" :class="{ 'is-frozen': item.frozen }" :style="avatarStyle(item)">{{ avatarLabel(item) }}</span><span><strong>{{ item.name }}</strong><small>{{ item.description || '暂无描述' }}</small></span></span></td><td><span class="kb-table-state"><span class="asset-pill">{{ visibilityLabel(item.visibility) }}</span><span v-if="item.frozen" class="kb-freeze-pill"><svg aria-hidden="true" viewBox="0 0 16 16"><path d="M5 7V5.5a3 3 0 0 1 6 0V7m-7 0h8v6H4z" /></svg>已冻结</span></span></td><td><span class="kb-tags"><span v-for="tag in (item.tags || []).slice(0, 3)" :key="tag">#{{ tag }}</span><small v-if="!item.tags?.length">—</small></span></td><td>{{ item.documentCount }}</td><td>{{ item.chunkCount }}</td><td><button class="kb-table-rag" :class="`is-${item.ragState.tone}`" @click="openKnowledgeManager(item, 'rag')"><i></i>{{ item.ragState.label }}</button></td><td>{{ formatDate(item.updated_at) }}</td><td><span class="card-actions"><button class="asset-link" @click="openKnowledgeManager(item)">管理</button><button class="asset-link" @click="openEditKnowledgeBase(item)">编辑</button><button class="asset-link danger" @click="state.kbDeletingId = item.kb_id">删除</button></span></td></tr></tbody></table>
        <div v-if="!knowledgeRecords.length" class="asset-empty">还没有知识库，点击右上角开始创建。</div>
      </section>

      <div v-if="state.kbModalOpen" class="asset-modal">
        <form class="asset-modal-card" @submit.prevent="run(saveKnowledgeBase, state.kbEditingId ? '知识库信息已更新' : '知识库已创建')">
          <header><div><span class="asset-eyebrow">Knowledge Library</span><h2>{{ state.kbEditingId ? '编辑知识库' : '创建知识库' }}</h2><p>用清晰的名称和描述帮助团队理解资料边界。</p></div><button type="button" aria-label="关闭" @click="state.kbModalOpen = false">×</button></header>
          <label>名称<input v-model.trim="forms.kb.name" required maxlength="128" placeholder="例如：产品与服务手册" /></label>
          <label>描述<textarea v-model.trim="forms.kb.description" maxlength="1000" rows="4" placeholder="说明它包含什么，以及适合解决哪些问题" /></label>
          <label>标签<input v-model="forms.kb.tags" maxlength="330" placeholder="产品，客服，内部资料" /><small>使用逗号分隔，最多 10 个</small></label>
          <fieldset><legend>访问权限</legend><label><input v-model="forms.kb.visibility" type="radio" value="PRIVATE" /><span><strong>仅自己可见</strong><small>只有你可以管理和使用</small></span></label><label><input v-model="forms.kb.visibility" type="radio" value="WORKSPACE" /><span><strong>工作空间可见</strong><small>团队成员可以在智能体中使用</small></span></label></fieldset>
          <footer><button class="asset-button ghost" type="button" @click="state.kbModalOpen = false">取消</button><button class="asset-button primary" type="submit" :disabled="!forms.kb.name || state.loading">{{ state.kbEditingId ? '保存更改' : '创建知识库' }}</button></footer>
        </form>
      </div>

      <div v-if="state.kbDeletingId" class="asset-modal" @pointerdown.self="state.kbDeletingId = ''"><div class="asset-modal-card danger-dialog"><span class="danger-mark">!</span><h2>删除“{{ knowledgeRecords.find(x => x.kb_id === state.kbDeletingId)?.name }}”？</h2><p>库内文档、切片和向量索引将一并清理，此操作无法撤销。</p><footer><button class="asset-button ghost" @click="state.kbDeletingId = ''">取消</button><button class="asset-button danger" @click="run(() => deleteKnowledgeBase(knowledgeRecords.find(x => x.kb_id === state.kbDeletingId)), '知识库已删除')">确认删除</button></footer></div></div>
    </template>

    <template v-else-if="page === 'workflows'">
      <section class="asset-panel compact-form"><div><h2>新建 Workflow</h2><p>从 Start → LLM → End 可执行模板开始。</p></div><input v-model="forms.workflowName" placeholder="例如：客户问题处理" /><button class="asset-button primary" :disabled="!forms.workflowName || state.loading" @click="run(createWorkflow, 'Workflow 已创建')">创建</button></section>
      <section class="asset-grid">
        <article v-for="item in state.workflows" :key="item.workflow_id" class="asset-card workflow-product-card" @click="editWorkflow(item)"><div class="asset-card-head"><span class="asset-icon flow">◇</span><span class="asset-pill">{{ item.status === 'PUBLISHED' ? '已发布' : '草稿' }}</span></div><h3>{{ item.name }}</h3><p>{{ item.description || '在 Agent Canva 中编排 AI、知识和工具能力。' }}</p><footer><span>草稿 r{{ item.revision }} · 最新 v{{ item.latest_version_no }}</span><span class="card-actions"><span class="asset-link">打开 Agent Canva →</span><button class="asset-link" :disabled="state.loading" @click.stop="run(() => publishWorkflow(item), '已发布不可变版本')">快速发布</button><button class="asset-link danger" :disabled="state.loading" @click.stop="openAssetDelete('workflow', item)">删除</button></span></footer></article>
        <div v-if="!state.workflows.length" class="asset-empty">还没有 Workflow，从模板创建一个即可在 Agent 和 API 中复用。</div>
      </section>
    </template>

    <template v-else-if="page === 'agents'">
      <section class="agent-library-toolbar"><div><strong>{{ state.agents.length }} 个智能体</strong><span>每个实例拥有独立模型、Prompt、知识库与调试配置</span></div></section>
      <section class="asset-grid agent-library-grid">
        <article class="agent-create-card"><span class="agent-create-mark">＋</span><div><h3>创建智能体</h3><p>先给它一个名称，随后进入工作台完善能力。</p></div><input v-model.trim="forms.agent.name" maxlength="128" placeholder="例如：售后服务专家" @keydown.enter.prevent="forms.agent.name && run(createAgent, '智能体已创建')" /><button class="asset-button primary" :disabled="!forms.agent.name || state.loading" @click="run(createAgent, '智能体已创建')">创建并配置</button></article>
        <article v-for="item in state.agents" :key="item.agent_id" class="asset-card agent-product-card" @click="openAgentWorkbench(item)"><div class="asset-card-head"><span class="agent-dynamic-avatar" :style="avatarStyle(item)">✦</span><span class="asset-pill">{{ item.status === 'PUBLISHED' ? '已发布' : '草稿' }}</span></div><h3>{{ item.name }}</h3><p>{{ item.instructions || item.description || '进入工作台设置角色与系统指令。' }}</p><div class="agent-capabilities"><span>{{ item.knowledge_base_ids.length }} 个知识库</span><span>{{ item.model_config?.model_endpoint_id ? '已选模型' : '默认模型' }}</span><span>{{ item.workflow_id ? '已关联 Workflow（预留）' : '独立运行' }}</span></div><footer><span>{{ item.latest_version_no ? `最新 v${item.latest_version_no}` : '尚未发布' }}</span><span class="card-actions"><span class="asset-link">打开工作台 →</span><button class="asset-link danger" :disabled="state.loading" @click.stop="openAssetDelete('agent', item)">删除</button></span></footer></article>
      </section>
    </template>

    <template v-else-if="page === 'deployments'">
      <template v-if="!activeDeployment">
        <section class="deployment-library-toolbar"><div><strong>{{ state.deployments.length }} 个 API 发布</strong><span>卡片展示运行态，点击进入独立工作页管理调用能力。</span></div><button class="asset-button primary" @click="state.deploymentModalOpen = true">新建发布</button></section>
        <section class="deployment-library-grid">
          <button class="deployment-create-card" type="button" @click="state.deploymentModalOpen = true"><span>＋</span><strong>发布新的 API</strong><small>选择已发布的智能体或工作流</small></button>
          <article v-for="item in state.deployments" :key="item.deployment_id" class="deployment-product-card" tabindex="0" role="button" @click="run(() => openDeployment(item), '')" @keydown.enter.prevent="run(() => openDeployment(item), '')">
            <header><span :class="['deployment-avatar', item.asset_type.toLowerCase()]">{{ item.asset_type === 'AGENT' ? '✦' : '◇' }}</span><span :class="['deployment-status', item.status.toLowerCase()]">{{ deploymentStatusText(item.status) }}</span></header>
            <div class="deployment-card-title"><span>{{ item.asset_type === 'AGENT' ? '智能体 API' : '工作流 API' }} · v{{ item.asset_version_no }}</span><h3>{{ item.name }}</h3><code>/{{ item.slug }}</code></div>
            <dl class="deployment-card-metrics"><div><dt>分钟限流</dt><dd>{{ item.rate_limit_per_minute }}</dd></div><div><dt>调用记录</dt><dd>{{ deploymentLogs(item).length }}</dd></div><div><dt>最近状态</dt><dd>{{ deploymentLastCall(item) ? invocationStatusText(deploymentLastCall(item).status) : '暂无' }}</dd></div></dl>
            <footer><span>{{ deploymentLastCall(item) ? new Date(deploymentLastCall(item).created_at).toLocaleString() : '等待首次调用' }}</span><span class="card-actions"><strong>打开工作页 →</strong><button class="asset-link danger" :disabled="state.loading" @click.stop="openAssetDelete('deployment', item)">删除</button></span></footer>
          </article>
        </section>
      </template>

      <section v-else class="deployment-workspace">
        <header class="deployment-workspace-header"><button class="workbench-back" type="button" @click="closeDeployment">←</button><div><span class="asset-eyebrow">API Deployment</span><h1>{{ activeDeployment.name }}</h1><p>{{ activeDeployment.asset_type === 'AGENT' ? '智能体同步调用' : '工作流异步调用' }} · 固定版本 v{{ activeDeployment.asset_version_no }}</p></div><div class="deployment-workspace-actions"><span :class="['deployment-status', activeDeployment.status.toLowerCase()]">{{ deploymentStatusText(activeDeployment.status) }}</span><button class="asset-button ghost small" :disabled="activeDeployment.status === 'ARCHIVED'" @click="run(() => pauseDeployment(activeDeployment), '发布状态已更新')">{{ activeDeployment.status === 'ACTIVE' ? '暂停服务' : '启用服务' }}</button><button class="asset-button ghost small" @click="run(() => openDeployment(activeDeployment), '')">刷新</button><button class="asset-link danger" :disabled="state.loading" @click="openAssetDelete('deployment', activeDeployment)">删除发布</button></div></header>

        <section class="deployment-endpoint-hero"><div><span>{{ activeDeployment.asset_type === 'AGENT' ? 'OpenAI 兼容调用地址' : '公开调用地址' }}</span><code>{{ absoluteApiUrl(activeDeployment.asset_type === 'AGENT' ? `/api/v1/deployments/${activeDeployment.slug}/v1/chat/completions` : activeDeployment.invoke_path) }}</code></div><button class="asset-button primary small" @click="copy(absoluteApiUrl(activeDeployment.asset_type === 'AGENT' ? `/api/v1/deployments/${activeDeployment.slug}/v1/chat/completions` : activeDeployment.invoke_path))">复制地址</button><button class="asset-button ghost small" @click="copy(curlExample(activeDeployment))">复制 cURL</button></section>

        <section class="deployment-workspace-metrics"><article><span>每分钟上限</span><strong>{{ activeDeployment.rate_limit_per_minute }}</strong><small>请求</small></article><article><span>累计日志</span><strong>{{ deploymentLogs(activeDeployment).length }}</strong><small>最近 100 条</small></article><article><span>有效密钥</span><strong>{{ (state.apiKeys[activeDeployment.deployment_id] || []).filter((key) => key.status === 'ACTIVE').length }}</strong><small>可立即吊销</small></article><article><span>最近调用</span><strong>{{ deploymentLastCall(activeDeployment) ? invocationStatusText(deploymentLastCall(activeDeployment).status) : '暂无' }}</strong><small>{{ deploymentLastCall(activeDeployment) ? new Date(deploymentLastCall(activeDeployment).created_at).toLocaleTimeString() : '等待请求' }}</small></article></section>

        <section class="deployment-work-grid">
          <article class="deployment-work-panel key-work-panel"><header><div><span class="panel-kicker">访问控制</span><h2>API Key</h2><p>密钥由服务端安全生成，明文只显示一次，并自动填入在线调用。</p></div><button class="asset-button primary small" :disabled="state.loading" @click="run(() => autoCreateKey(activeDeployment), '')">自动生成 API Key</button></header><div class="key-create"><input v-model.trim="forms.key.name" maxlength="128" placeholder="自定义密钥名称" /><label><span>失效时间（可选）</span><input v-model="forms.key.expires_at" type="datetime-local" /></label><button class="asset-button ghost small" :disabled="!forms.key.name" @click="forms.key.deployment_id = activeDeployment.deployment_id; run(createKey, 'API Key 已创建，请立即保存')">按配置创建</button></div><div class="key-table"><div v-if="!(state.apiKeys[activeDeployment.deployment_id] || []).length" class="asset-meta">暂无密钥，可一键自动生成，或按名称与失效时间创建。</div><div v-for="key in state.apiKeys[activeDeployment.deployment_id] || []" :key="key.api_key_id" class="key-row"><code>{{ key.key_prefix }}••••</code><span>{{ key.name }} · {{ key.status === 'ACTIVE' ? '有效' : '已吊销' }}</span><span>{{ key.expires_at ? `失效于 ${new Date(key.expires_at).toLocaleString()}` : '长期有效' }}</span><span>{{ key.last_used_at ? `最近使用 ${new Date(key.last_used_at).toLocaleString()}` : '尚未使用' }}</span><button v-if="key.status === 'ACTIVE'" class="asset-link danger" @click="run(() => revokeKey(key), '密钥已吊销')">吊销</button></div></div></article>

          <article class="deployment-work-panel tester-work-panel"><header class="tester-heading"><div><span class="panel-kicker">Request Lab</span><h2>在线调用</h2><p>直接使用 API Key 请求公开地址；智能体采用 OpenAI 兼容协议，并可连续使用会话记忆。</p></div><button class="asset-link" @click="copy(curlExample(activeDeployment))">复制 cURL</button></header><div class="deployment-tester"><label>API Key<input v-model.trim="testerFor(activeDeployment).apiKey" type="password" autocomplete="off" placeholder="pf_live_…" /></label><label>input（JSON）<textarea v-model="testerFor(activeDeployment).input" rows="8" spellcheck="false"></textarea></label><label v-if="activeDeployment.asset_type === 'AGENT'" class="stream-toggle"><input v-model="testerFor(activeDeployment).stream" type="checkbox" /><span><strong>SSE 流式回复</strong><small>开启后实时接收 token；关闭时一次性返回。</small></span></label><label v-if="activeDeployment.asset_type === 'AGENT'">会话记忆 ID<input v-model.trim="testerFor(activeDeployment).conversationId" placeholder="首次留空，响应后自动保存" /></label><div class="tester-actions"><button class="asset-button primary small" :disabled="testerFor(activeDeployment).loading || !testerFor(activeDeployment).apiKey" @click="run(() => invokeDeployment(activeDeployment), '')">{{ testerFor(activeDeployment).loading ? '调用中…' : '发送请求' }}</button><button v-if="testerFor(activeDeployment).statusUrl" class="asset-button ghost small" :disabled="testerFor(activeDeployment).loading" @click="run(() => queryDeploymentRun(activeDeployment), '')">查询工作流结果</button></div><dl v-if="testerFor(activeDeployment).result?.data?.powflow?.memory" class="memory-observability"><div><dt>近期消息</dt><dd>{{ testerFor(activeDeployment).result.data.powflow.memory.recent_messages }}</dd></div><div><dt>摘要</dt><dd>{{ testerFor(activeDeployment).result.data.powflow.memory.summary_chars }} 字</dd></div><div><dt>长期记忆</dt><dd>{{ testerFor(activeDeployment).result.data.powflow.memory.episodic_items }}</dd></div><div><dt>历史召回</dt><dd>{{ testerFor(activeDeployment).result.data.powflow.memory.session_recall_items }}</dd></div><div><dt>注入上下文</dt><dd>{{ testerFor(activeDeployment).result.data.powflow.memory.injected_context_chars }} 字</dd></div></dl><pre v-if="testerFor(activeDeployment).result"><code>HTTP {{ testerFor(activeDeployment).result.httpStatus }}</code><code>{{ JSON.stringify(testerFor(activeDeployment).result.data, null, 2) }}</code></pre></div></article>

          <article class="deployment-work-panel invocation-work-panel"><header><div><span class="panel-kicker">Observability</span><h2>调用日志</h2><p>按 Trace 追踪最近请求、耗时和输出规模。</p></div><button class="asset-link" @click="run(() => loadInvocations(activeDeployment.deployment_id), '')">刷新日志</button></header><div class="invocation-panel"><div v-if="!deploymentLogs(activeDeployment).length" class="asset-meta">暂无调用日志</div><div v-for="log in deploymentLogs(activeDeployment)" :key="log.invocation_id" class="invocation-row"><span :class="['deployment-status', ['COMPLETED', 'ACCEPTED'].includes(log.status) ? 'active' : 'paused']">{{ invocationStatusText(log.status) }}</span><code :title="log.trace_id">{{ log.trace_id }}</code><span>{{ log.duration_ms ?? '-' }} ms</span><span>{{ log.output_bytes }} B</span><span>{{ new Date(log.created_at).toLocaleString() }}</span></div></div></article>
        </section>
        <div v-if="state.revealedSecret" class="secret-reveal"><div><strong>只显示一次的 API Key</strong><p>关闭前请保存。密钥已自动填入在线调用。</p></div><code>{{ state.revealedSecret }}</code><button class="asset-button primary" @click="copy(state.revealedSecret)">复制密钥</button><button class="asset-button ghost" @click="state.revealedSecret = ''; state.revealedDeploymentId = ''">我已保存</button></div>
      </section>

      <div v-if="state.deploymentModalOpen" class="asset-modal" role="dialog" aria-modal="true" aria-labelledby="deployment-modal-title"><section class="asset-modal-card deployment-modal-card"><header><div><span class="asset-eyebrow">New Deployment</span><h2 id="deployment-modal-title">发布新的 API</h2><p>绑定不可变资产版本，获得稳定公开地址。</p></div><button type="button" aria-label="关闭" @click="state.deploymentModalOpen = false">×</button></header><label>资产类型<select v-model="forms.deployment.asset_type" @change="forms.deployment.asset_id = ''"><option value="AGENT">智能体</option><option value="WORKFLOW">工作流</option></select></label><label>已发布资产<select v-model="forms.deployment.asset_id" @change="selectDeploymentAsset"><option value="">请选择</option><option v-for="item in deployableAssets.filter(x => x.latest_version_no > 0)" :key="item.agent_id || item.workflow_id" :value="item.agent_id || item.workflow_id">{{ item.name }} · v{{ item.latest_version_no }}</option></select></label><label>发布名称<input v-model.trim="forms.deployment.name" maxlength="128" placeholder="例如：客服助手生产 API" /></label><label>每分钟上限<input v-model.number="forms.deployment.rate_limit_per_minute" type="number" min="1" max="100000" /></label><label class="automatic-key-option"><input v-model="state.autoGenerateKeyOnDeployment" type="checkbox" /><span><strong>自动生成首枚 API Key</strong><small>创建完成后只显示一次，并直接填入在线调用。</small></span></label><footer><button class="asset-button ghost" type="button" @click="state.deploymentModalOpen = false">取消</button><button class="asset-button primary" :disabled="!forms.deployment.asset_id || !forms.deployment.name || state.loading" @click="run(createDeployment, '')">{{ state.loading ? '创建中…' : '创建并进入工作页' }}</button></footer></section></div>
    </template>

    <template v-else-if="page === 'members'">
      <section v-if="canManageMembers" class="asset-panel invitation-form">
        <div class="form-heading"><h2>邀请成员</h2><p>令牌只显示一次。已有工作空间不能通过公开注册加入。</p></div>
        <label>成员邮箱<input v-model.trim="forms.invitation.email" type="email" placeholder="member@example.com" /></label>
        <label>角色<select v-model="forms.invitation.role_code"><option value="USER">普通成员</option><option value="TENANT_ADMIN">空间管理员</option></select></label>
        <label>有效期（天）<input v-model.number="forms.invitation.expires_in_days" type="number" min="1" max="30" /></label>
        <button class="asset-button primary" :disabled="!forms.invitation.email || state.loading" @click="run(createInvitation, '邀请已创建，请安全发送一次性令牌')">创建邀请</button>
      </section>
      <section class="asset-panel member-panel">
        <div class="document-heading"><div><h2>工作空间成员</h2><p>{{ state.members.length }} 位成员</p></div></div>
        <div class="member-list">
          <article v-for="item in state.members" :key="item.userId"><div><strong>{{ item.displayName }}</strong><small>{{ item.email }}</small></div><span class="member-role">{{ (item.roles || []).join(' · ') }}</span><span class="member-status">{{ item.status }}</span><span v-if="canManageMembers && item.email !== currentUserEmail" class="member-actions"><button class="asset-link" @click="run(() => patchMember(item, 'role'), '成员角色已更新')">{{ (item.roles || []).includes('TENANT_ADMIN') ? '取消管理员' : '设为管理员' }}</button><button class="asset-link" :class="{ danger: item.status === 'ACTIVE' }" @click="run(() => patchMember(item, 'status'), '成员状态已更新')">{{ item.status === 'ACTIVE' ? '停用' : '启用' }}</button></span></article>
        </div>
      </section>
      <section v-if="canManageMembers" class="asset-panel member-panel">
        <div class="document-heading"><div><h2>邀请记录</h2><p>可撤销尚未使用的邀请。</p></div></div>
        <div class="member-list invitation-list">
          <article v-for="item in state.invitations" :key="item.invitationId"><div><strong>{{ item.email }}</strong><small>{{ item.tokenPrefix }}•••• · {{ new Date(item.expiresAt).toLocaleString() }}</small></div><span class="member-role">{{ item.roleCode }}</span><span class="member-status">{{ item.status }}</span><button v-if="item.status === 'PENDING'" class="asset-link danger" @click="run(() => revokeInvitation(item), '邀请已撤销')">撤销</button></article>
          <div v-if="!state.invitations.length" class="asset-empty">还没有邀请记录。</div>
        </div>
      </section>
      <div v-if="state.revealedInvitation" class="secret-reveal"><div><strong>只显示一次的邀请令牌</strong><p>请通过可信渠道发送给受邀成员。</p></div><code>{{ state.revealedInvitation }}</code><button class="asset-button primary" @click="copy(state.revealedInvitation)">复制令牌</button></div>
    </template>
    </template>
    <div v-if="state.deleteTarget" class="asset-modal" role="dialog" aria-modal="true" aria-labelledby="asset-delete-title">
      <div class="asset-modal-card danger-dialog">
        <span class="danger-mark">!</span>
        <h2 id="asset-delete-title">删除{{ deleteTargetType() }}“{{ state.deleteTarget.name }}”？</h2>
        <p>{{ deleteTargetDescription() }}</p>
        <footer><button class="asset-button ghost" type="button" :disabled="state.loading" @click="state.deleteTarget = null">取消</button><button class="asset-button danger" type="button" :disabled="state.loading" @click="run(deleteWorkspaceAsset, '资产已从工作空间移除')">{{ state.loading ? '处理中…' : '确认删除' }}</button></footer>
      </div>
    </div>
  </main>
</template>

<style scoped>
.asset-workspace{min-width:0;height:100vh;overflow:auto;padding:clamp(22px,4vw,56px);background:var(--workspace-bg,#f5f5f7);color:var(--workspace-text,#1d1d1f)}
.asset-header{display:flex;justify-content:space-between;align-items:flex-start;gap:24px;margin:0 auto 28px;max-width:1240px}.asset-header h1{margin:5px 0 8px;font-size:clamp(30px,4vw,48px);line-height:1.05;letter-spacing:-.035em}.asset-header p,.asset-panel p{margin:0;color:var(--workspace-muted,#6e6e73);line-height:1.6}.asset-eyebrow{color:#0071e3;font-size:12px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
.asset-banner,.secret-reveal{max-width:1240px;margin:0 auto 18px;padding:14px 18px;border-radius:16px}.asset-banner.error{background:#fff0f0;color:#a61b1b}.asset-banner.success{background:#eaf8ef;color:#17663a}
.asset-stats,.asset-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;max-width:1240px;margin:0 auto 20px}.asset-stats article,.asset-panel,.asset-card{border:1px solid rgba(0,0,0,.07);background:rgba(255,255,255,.82);box-shadow:0 12px 36px rgba(0,0,0,.06);backdrop-filter:blur(24px) saturate(160%)}.asset-stats article{display:grid;gap:8px;padding:24px;border-radius:24px}.asset-stats span{color:var(--workspace-muted,#6e6e73)}.asset-stats strong{font-size:38px;letter-spacing:-.03em}.asset-stats small{color:var(--workspace-muted,#6e6e73)}
.asset-panel{max-width:1240px;margin:0 auto 20px;padding:24px;border-radius:24px}.welcome-panel{display:flex;justify-content:space-between;gap:30px}.welcome-panel h2,.asset-panel h2{margin:6px 0 8px}.welcome-panel ol{margin:0;display:grid;gap:8px;color:var(--workspace-muted,#6e6e73)}
.compact-form{display:grid;grid-template-columns:minmax(240px,1fr) minmax(200px,380px) auto;align-items:center;gap:16px}.asset-button{min-height:42px;padding:0 17px;border:1px solid rgba(0,0,0,.1);border-radius:13px;transition:transform .12s ease,background-color .18s ease}.asset-button:active{transform:scale(.97)}.asset-button.primary{border-color:#0071e3;background:#0071e3;color:white;font-weight:650}.asset-button.ghost{background:rgba(255,255,255,.72)}.asset-button.small{min-height:34px;padding:0 12px;font-size:12px}.asset-button:disabled{opacity:.45;cursor:not-allowed}
.asset-workspace input,.asset-workspace textarea,.asset-workspace select{width:100%;border:1px solid rgba(0,0,0,.12);border-radius:13px;background:rgba(255,255,255,.78);color:inherit;padding:11px 13px;outline:none}.asset-workspace textarea{resize:none}.asset-workspace input:focus,.asset-workspace textarea:focus,.asset-workspace select:focus{border-color:#0071e3;box-shadow:0 0 0 3px rgba(0,113,227,.12)}
.asset-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.asset-card{display:flex;min-height:230px;flex-direction:column;padding:22px;border-radius:22px}.asset-card-head,.asset-card footer,.deployment-main,.deployment-actions,.key-row{display:flex;align-items:center;justify-content:space-between;gap:12px}.asset-icon{display:grid;place-items:center;width:42px;height:42px;border-radius:13px;background:#e8f2ff;color:#0066cc;font-size:12px;font-weight:800}.asset-icon.flow{background:#f0eaff;color:#6542c7}.asset-icon.agent{background:#e8f8ef;color:#18794e}.asset-pill{display:inline-flex;width:max-content;padding:5px 9px;border-radius:999px;background:rgba(118,118,128,.1);color:var(--workspace-muted,#6e6e73);font-size:11px;font-weight:700}.asset-card h3,.deployment-card h3{margin:20px 0 8px;font-size:20px}.asset-card p{display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:3}.asset-card footer{margin-top:auto;padding-top:20px;color:var(--workspace-muted,#6e6e73);font-size:12px}.asset-card code{overflow:hidden;text-overflow:ellipsis}.asset-link{padding:0;border:0;background:transparent;color:#0071e3;font-weight:650}.asset-link.danger{color:#d70015}.asset-meta{margin-top:14px;color:var(--workspace-muted,#6e6e73);font-size:12px}.asset-empty{grid-column:1/-1;padding:50px;text-align:center;color:var(--workspace-muted,#6e6e73)}
.agent-library-toolbar{display:flex;align-items:center;justify-content:space-between;max-width:1240px;margin:0 auto 18px}.agent-library-toolbar>div{display:grid;gap:5px}.agent-library-toolbar strong{font-size:15px}.agent-library-toolbar span{color:var(--workspace-muted,#6e6e73);font-size:12px}.agent-library-grid{align-items:stretch}.agent-create-card{display:grid;align-content:center;gap:13px;min-height:262px;padding:22px;border:1px dashed rgba(0,113,227,.38);border-radius:22px;background:linear-gradient(145deg,rgba(0,113,227,.07),rgba(103,93,246,.035))}.agent-create-mark{display:grid;place-items:center;width:44px;height:44px;border-radius:15px;color:white;background:linear-gradient(145deg,#168bff,#675df6);box-shadow:0 12px 30px rgba(44,113,238,.22);font-size:23px}.agent-create-card h3{margin:0 0 5px}.agent-create-card p{margin:0;color:var(--workspace-muted,#6e6e73);font-size:12px;line-height:1.5}.agent-create-card .asset-button{justify-self:start}.agent-product-card{min-height:262px;cursor:pointer;transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}.agent-product-card:hover{transform:translateY(-3px);border-color:rgba(0,113,227,.26);box-shadow:0 20px 48px rgba(0,0,0,.12)}.agent-product-card:active{transform:scale(.99)}.agent-dynamic-avatar{display:grid;place-items:center;width:46px;height:46px;border-radius:15px;color:#fff;background:radial-gradient(circle at 30% 20%,hsla(var(--kb-hue-two),90%,72%,.9),transparent 44%),linear-gradient(145deg,hsl(var(--kb-hue),72%,48%),hsl(var(--kb-hue-two),70%,38%));box-shadow:0 10px 25px hsla(var(--kb-hue),70%,35%,.2)}.agent-capabilities{display:flex;gap:6px;margin-top:15px;flex-wrap:wrap}.agent-capabilities span{padding:5px 8px;border-radius:9px;color:var(--workspace-muted,#6e6e73);background:rgba(127,127,127,.08);font-size:10px}
.agent-form,.deployment-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.form-heading{grid-column:1/-1}.agent-form label,.deployment-form label{display:grid;gap:7px;color:var(--workspace-muted,#6e6e73);font-size:12px}.agent-form button,.deployment-form button{justify-self:start}.deployment-list{display:grid;gap:16px;max-width:1240px;margin:0 auto}.deployment-card{margin:0}.deployment-status{padding:6px 10px;border-radius:999px;font-size:11px;font-weight:800}.deployment-status.active{background:#e8f8ef;color:#18794e}.deployment-status.paused,.deployment-status.archived{background:#fff4df;color:#8a5600}.endpoint-row{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:16px 0;padding:12px 14px;border-radius:14px;background:rgba(118,118,128,.08)}.endpoint-row code{overflow:auto}.deployment-actions{justify-content:flex-start;flex-wrap:wrap;color:var(--workspace-muted,#6e6e73)}.key-panel{margin-top:18px;padding-top:18px;border-top:1px solid rgba(0,0,0,.08)}.key-create{display:grid;grid-template-columns:minmax(180px,1fr) minmax(210px,1fr) auto;align-items:end;gap:10px;margin-bottom:10px}.key-create label{display:grid;gap:5px;color:var(--workspace-muted,#6e6e73);font-size:10px}.key-row{display:grid;grid-template-columns:minmax(150px,auto) minmax(130px,1fr) minmax(160px,1fr) minmax(160px,1fr) auto;padding:10px 0;border-top:1px solid rgba(0,0,0,.06);font-size:11px}.secret-reveal{position:sticky;bottom:18px;display:grid;grid-template-columns:1fr minmax(240px,auto) auto auto;align-items:center;gap:16px;background:#1d1d1f;color:white;box-shadow:0 18px 50px rgba(0,0,0,.28)}.secret-reveal p{margin:4px 0 0;color:#aaa}.secret-reveal code{overflow:auto}
.deployment-tester{display:grid;grid-template-columns:minmax(220px,.75fr) minmax(320px,1.25fr);gap:14px;margin-top:18px;padding:18px;border:1px solid rgba(127,127,127,.16);border-radius:18px;background:rgba(127,127,127,.055)}.tester-heading{grid-column:1/-1;display:flex;align-items:flex-start;justify-content:space-between;gap:18px}.tester-heading p{margin-top:4px;font-size:11px}.deployment-tester label{display:grid;align-content:start;gap:7px;color:var(--workspace-muted,#6e6e73);font-size:11px}.deployment-tester textarea{font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}.tester-actions{display:flex;align-items:center;gap:10px}.deployment-tester pre{grid-column:1/-1;display:grid;gap:8px;max-height:320px;margin:0;padding:15px;overflow:auto;border-radius:14px;color:#d7e8ff;background:#111318;font:11px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}.invocation-panel{display:grid;margin-top:18px;padding-top:12px;border-top:1px solid rgba(0,0,0,.08)}.invocation-row{display:grid;grid-template-columns:90px minmax(120px,1fr) 80px 70px minmax(150px,auto);align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid rgba(0,0,0,.06);font-size:12px}.invocation-row code{overflow:hidden;text-overflow:ellipsis}
.deployment-tester .stream-toggle{display:flex;align-items:center;gap:10px;padding:11px 12px;border:1px solid rgba(41,145,255,.18);border-radius:14px;background:rgba(41,145,255,.06)}.stream-toggle input{width:18px!important;height:18px;margin:0;accent-color:#0071e3}.stream-toggle span{display:grid;gap:2px}.stream-toggle strong{color:var(--workspace-text);font-size:11px}.stream-toggle small{font-size:9px}.memory-observability{grid-column:1/-1;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:7px;margin:0}.memory-observability div{display:grid;gap:4px;padding:10px;border-radius:12px;background:rgba(41,145,255,.075)}.memory-observability dt{color:var(--workspace-muted);font-size:9px}.memory-observability dd{margin:0;color:var(--workspace-text);font-size:12px;font-weight:720}
.deployment-library-toolbar{display:flex;align-items:center;justify-content:space-between;gap:18px;max-width:1240px;margin:0 auto 18px}.deployment-library-toolbar>div{display:grid;gap:5px}.deployment-library-toolbar strong{font-size:15px}.deployment-library-toolbar span{color:var(--workspace-muted,#6e6e73);font-size:12px}.deployment-library-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;max-width:1240px;margin:0 auto}.deployment-create-card,.deployment-product-card{min-height:300px;border-radius:24px}.deployment-create-card{display:grid;place-items:center;align-content:center;gap:8px;padding:24px;border:1px dashed rgba(0,113,227,.38);color:var(--workspace-muted,#6e6e73);background:linear-gradient(145deg,rgba(0,113,227,.07),rgba(103,93,246,.025));transition:transform .18s ease,border-color .18s ease,background .18s ease}.deployment-create-card:hover{transform:translateY(-3px);border-color:rgba(0,113,227,.72);background:linear-gradient(145deg,rgba(0,113,227,.12),rgba(103,93,246,.05))}.deployment-create-card>span{display:grid;place-items:center;width:48px;height:48px;margin-bottom:8px;border-radius:16px;color:#fff;background:linear-gradient(145deg,#168bff,#675df6);box-shadow:0 14px 30px rgba(44,113,238,.24);font-size:26px}.deployment-create-card strong{color:var(--workspace-text);font-size:15px}.deployment-create-card small{font-size:11px}.deployment-product-card{display:flex;min-width:0;flex-direction:column;padding:22px;border:1px solid rgba(0,0,0,.07);background:rgba(255,255,255,.82);box-shadow:0 12px 36px rgba(0,0,0,.06);cursor:pointer;backdrop-filter:blur(24px) saturate(160%);transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}.deployment-product-card:hover{transform:translateY(-3px);border-color:rgba(0,113,227,.3);box-shadow:0 20px 48px rgba(0,0,0,.12)}.deployment-product-card:focus-visible{outline:3px solid rgba(0,113,227,.28);outline-offset:3px}.deployment-product-card>header{display:flex;align-items:center;justify-content:space-between;gap:12px}.deployment-avatar{display:grid;place-items:center;width:46px;height:46px;border-radius:15px;color:#fff;font-size:17px;box-shadow:0 11px 28px rgba(0,0,0,.16)}.deployment-avatar.agent{background:linear-gradient(145deg,#168bff,#675df6)}.deployment-avatar.workflow{background:linear-gradient(145deg,#7457df,#d04fa3)}.deployment-card-title{display:grid;gap:6px;margin-top:22px}.deployment-card-title>span{color:var(--workspace-muted,#6e6e73);font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase}.deployment-card-title h3{margin:0;font-size:21px;letter-spacing:-.025em}.deployment-card-title code{overflow:hidden;color:var(--workspace-muted,#6e6e73);font-size:10px;text-overflow:ellipsis;white-space:nowrap}.deployment-card-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:18px 0 0}.deployment-card-metrics div{display:grid;gap:4px;padding:10px;border-radius:12px;background:rgba(127,127,127,.065)}.deployment-card-metrics dt{color:var(--workspace-muted,#6e6e73);font-size:9px}.deployment-card-metrics dd{margin:0;font-size:13px;font-weight:720;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.deployment-product-card footer{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:auto;padding-top:20px;color:var(--workspace-muted,#6e6e73);font-size:10px}.deployment-product-card footer strong{color:#2991ff;font-size:11px;white-space:nowrap}
.deployment-workspace{display:grid;gap:18px;max-width:1240px;margin:0 auto;padding-bottom:40px}.deployment-workspace-header{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:18px}.workbench-back{display:grid;place-items:center;width:42px;height:42px;border:1px solid rgba(127,127,127,.18);border-radius:14px;color:inherit;background:rgba(127,127,127,.08);font-size:19px}.deployment-workspace-header h1{margin:3px 0 5px;font-size:clamp(28px,4vw,44px);line-height:1.05;letter-spacing:-.035em}.deployment-workspace-header p{margin:0;color:var(--workspace-muted,#6e6e73);font-size:12px}.deployment-workspace-actions{display:flex;align-items:center;justify-content:flex-end;gap:9px;flex-wrap:wrap}.deployment-endpoint-hero{display:grid;grid-template-columns:minmax(0,1fr) auto auto;align-items:center;gap:10px;padding:20px;border:1px solid rgba(41,145,255,.2);border-radius:22px;background:linear-gradient(135deg,rgba(0,113,227,.12),rgba(103,93,246,.05));box-shadow:inset 0 1px rgba(255,255,255,.08)}.deployment-endpoint-hero>div{display:grid;gap:7px;min-width:0}.deployment-endpoint-hero span{color:var(--workspace-muted,#6e6e73);font-size:10px;font-weight:720}.deployment-endpoint-hero code{overflow:auto;font-size:12px;white-space:nowrap}.deployment-workspace-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.deployment-workspace-metrics article{display:grid;grid-template-columns:1fr auto;align-items:end;gap:5px;padding:18px;border:1px solid rgba(127,127,127,.14);border-radius:18px;background:rgba(255,255,255,.68);box-shadow:0 8px 24px rgba(0,0,0,.045)}.deployment-workspace-metrics span{grid-column:1/-1;color:var(--workspace-muted,#6e6e73);font-size:10px}.deployment-workspace-metrics strong{font-size:24px;letter-spacing:-.03em}.deployment-workspace-metrics small{color:var(--workspace-muted,#6e6e73);font-size:9px}.deployment-work-grid{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:16px}.deployment-work-panel{min-width:0;padding:22px;border:1px solid rgba(0,0,0,.07);border-radius:22px;background:rgba(255,255,255,.82);box-shadow:0 12px 36px rgba(0,0,0,.06);backdrop-filter:blur(24px) saturate(160%)}.deployment-work-panel>header{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;margin-bottom:18px}.deployment-work-panel h2{margin:3px 0 5px;font-size:20px}.deployment-work-panel header p{margin:0;color:var(--workspace-muted,#6e6e73);font-size:11px}.panel-kicker{color:#2991ff;font-size:9px;font-weight:750;letter-spacing:.08em;text-transform:uppercase}.key-work-panel{grid-column:1/-1}.tester-work-panel{grid-column:span 6}.invocation-work-panel{grid-column:span 6}.key-table{display:grid}.tester-work-panel .deployment-tester{display:grid;grid-template-columns:1fr;gap:13px;margin:0;padding:0;border:0;background:transparent}.tester-work-panel .tester-heading{grid-column:auto}.tester-work-panel .deployment-tester pre{grid-column:auto}.invocation-work-panel .invocation-panel{margin:0;padding:0;border:0}.invocation-work-panel .invocation-row{grid-template-columns:82px minmax(100px,1fr) 68px minmax(130px,auto)}.invocation-work-panel .invocation-row>span:nth-of-type(3){display:none}.deployment-modal-card{width:min(620px,100%);grid-template-columns:repeat(2,minmax(0,1fr))}.deployment-modal-card>header,.deployment-modal-card>footer{grid-column:1/-1}.deployment-modal-card>label{display:grid;gap:7px}.asset-workspace.theme-dark .deployment-modal-card select,.asset-workspace.theme-dark .deployment-modal-card option{color:#f5f5f7;background:#242426;color-scheme:dark}.asset-workspace.theme-light .deployment-modal-card select,.asset-workspace.theme-light .deployment-modal-card option{color:#1d1d1f;background:#fff;color-scheme:light}
.asset-workspace.theme-dark .deployment-product-card,.asset-workspace.theme-dark .deployment-work-panel{border-color:rgba(255,255,255,.1);background:rgba(28,28,30,.78);box-shadow:0 18px 48px rgba(0,0,0,.3)}.asset-workspace.theme-dark .deployment-workspace-metrics article{border-color:rgba(255,255,255,.1);background:rgba(28,28,30,.66);box-shadow:0 14px 34px rgba(0,0,0,.24)}.asset-workspace.theme-light .deployment-product-card,.asset-workspace.theme-light .deployment-work-panel{border-color:rgba(0,0,0,.07);background:rgba(255,255,255,.88)}.asset-workspace.theme-light .deployment-workspace-metrics article{background:rgba(255,255,255,.78)}
.deployment-modal-card>.automatic-key-option{grid-column:1/-1;display:flex;align-items:center;gap:12px;padding:13px 14px;border:1px solid rgba(41,145,255,.2);border-radius:15px;background:rgba(41,145,255,.07);cursor:pointer}.automatic-key-option input{width:18px!important;height:18px;margin:0;accent-color:#0071e3}.automatic-key-option span{display:grid;gap:3px}.automatic-key-option strong{color:var(--workspace-text,#1d1d1f);font-size:12px}.automatic-key-option small{color:var(--workspace-muted,#6e6e73);font-size:10px}
.document-manager{display:grid;gap:18px}.document-heading{display:flex;align-items:center;justify-content:space-between;gap:12px}.document-tools{display:grid;grid-template-columns:minmax(180px,1fr) auto minmax(180px,1fr);align-items:end;gap:12px}.document-tools label{display:grid;gap:7px;color:var(--workspace-muted,#6e6e73);font-size:12px}.document-tools .text-content{grid-column:1/3}.document-list{display:grid;border-top:1px solid rgba(127,127,127,.16)}.document-list article{display:grid;grid-template-columns:minmax(180px,1fr) 110px 100px;align-items:center;gap:12px;padding:13px 2px;border-bottom:1px solid rgba(127,127,127,.12);font-size:12px}.document-list strong,.document-list small{display:block}.document-list small{margin-top:3px;color:var(--workspace-muted,#6e6e73);overflow:hidden;text-overflow:ellipsis}
.card-actions{display:flex;gap:12px}.workflow-editor{display:grid;gap:16px}.workflow-fields{display:grid;grid-template-columns:1fr 2fr;gap:12px}.workflow-fields label,.dsl-editor{display:grid;gap:7px;color:var(--workspace-muted,#6e6e73);font-size:12px}.dsl-editor textarea{font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;tab-size:2}.workflow-editor>.asset-button{justify-self:start}
.workflow-product-card{cursor:pointer;transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}.workflow-product-card:hover{transform:translateY(-3px);border-color:rgba(103,93,246,.28);box-shadow:0 20px 48px rgba(0,0,0,.12)}.workflow-product-card:active{transform:scale(.99)}
.invitation-form{display:grid;grid-template-columns:2fr 1fr 1fr auto;align-items:end;gap:16px}.invitation-form label{display:grid;gap:7px;color:var(--workspace-muted,#6e6e73);font-size:12px}.member-list{display:grid;margin-top:18px;border-top:1px solid rgba(127,127,127,.16)}.member-list article{display:grid;grid-template-columns:minmax(220px,1fr) minmax(120px,auto) 90px minmax(150px,auto);align-items:center;gap:16px;padding:14px 2px;border-bottom:1px solid rgba(127,127,127,.12)}.invitation-list article{grid-template-columns:minmax(220px,1fr) minmax(120px,auto) 90px 50px}.member-list strong,.member-list small{display:block}.member-list small{margin-top:4px;color:var(--workspace-muted,#6e6e73)}.member-role,.member-status{font-size:12px}.member-status{color:var(--workspace-muted,#6e6e73)}.member-actions{display:flex;justify-content:flex-end;gap:12px;font-size:12px}
.knowledge-toolbar{display:flex;align-items:center;justify-content:space-between;gap:18px;max-width:1240px;margin:0 auto 18px}.knowledge-toolbar>div:first-child{display:grid;gap:5px}.knowledge-toolbar strong{font-size:15px}.knowledge-toolbar span{color:var(--workspace-muted,#6e6e73);font-size:12px}.knowledge-toolbar-actions{display:flex;align-items:center;gap:10px}.view-switch{display:flex;padding:3px;border:1px solid rgba(127,127,127,.16);border-radius:12px;background:rgba(127,127,127,.07)}.view-switch button{display:grid;place-items:center;width:34px;height:30px;padding:0;border:0;border-radius:9px;color:var(--workspace-muted,#6e6e73);background:transparent;font-size:17px}.view-switch button.active{color:var(--workspace-text);background:rgba(127,127,127,.18);box-shadow:0 2px 8px rgba(0,0,0,.08)}
.knowledge-grid{align-items:stretch}.kb-create-card{display:grid;place-items:center;align-content:center;min-height:230px;padding:22px;border:1px dashed rgba(0,113,227,.34);border-radius:22px;color:var(--workspace-muted,#6e6e73);background:rgba(0,113,227,.035);transition:transform .18s ease,border-color .18s ease,background .18s ease}.kb-create-card:hover{transform:translateY(-2px);border-color:rgba(0,113,227,.7);background:rgba(0,113,227,.07)}.kb-create-card:active{transform:scale(.985)}.kb-create-card>span{display:grid;place-items:center;width:46px;height:46px;margin-bottom:14px;border-radius:50%;color:#fff;background:linear-gradient(145deg,#168bff,#675df6);box-shadow:0 12px 30px rgba(44,113,238,.25);font-size:25px;font-weight:300}.kb-create-card strong{color:var(--workspace-text);font-size:15px}.kb-create-card small{margin-top:6px;color:var(--workspace-muted,#6e6e73)}
.kb-product-card{min-height:286px}.kb-avatar{display:grid;place-items:center;width:46px;height:46px;border:1px solid hsla(var(--kb-hue),80%,75%,.38);border-radius:15px;color:#fff;background:radial-gradient(circle at 30% 20%,hsla(var(--kb-hue-two),90%,72%,.88),transparent 44%),linear-gradient(145deg,hsl(var(--kb-hue),72%,48%),hsl(var(--kb-hue-two),70%,38%));box-shadow:inset 0 1px rgba(255,255,255,.3),0 10px 25px hsla(var(--kb-hue),70%,35%,.2);font-size:12px;font-weight:780}.kb-avatar.small{flex:0 0 auto;width:36px;height:36px;border-radius:11px;font-size:10px}.kb-product-card h3{margin-top:18px}.kb-product-card p{min-height:42px;margin:0;color:var(--workspace-muted,#6e6e73);font-size:13px;line-height:1.6}.kb-tags{display:flex;align-items:center;gap:6px;min-height:24px;margin-top:10px;flex-wrap:wrap}.kb-tags span{padding:3px 7px;border-radius:8px;color:#2873bd;background:rgba(40,127,212,.09);font-size:10px}.kb-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:14px 0 0}.kb-metrics div{display:grid;gap:3px;padding:9px;border-radius:11px;background:rgba(127,127,127,.065)}.kb-metrics dt{color:var(--workspace-muted,#6e6e73);font-size:9px}.kb-metrics dd{margin:0;font-size:15px;font-weight:680}.kb-product-card footer{gap:8px}.kb-product-card .card-actions{gap:10px;flex-wrap:wrap}
.kb-rag-status{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:9px;width:100%;margin-top:12px;padding:9px 10px;border:1px solid rgba(127,127,127,.13);border-radius:12px;color:var(--workspace-text);background:rgba(127,127,127,.055);text-align:left}.kb-rag-signal{display:flex;align-items:flex-end;gap:2px;width:18px;height:18px}.kb-rag-signal i{display:block;width:4px;border-radius:4px;background:#7e7e86}.kb-rag-signal i:nth-child(1){height:7px}.kb-rag-signal i:nth-child(2){height:13px}.kb-rag-signal i:nth-child(3){height:10px}.kb-rag-status>span:nth-child(2){display:grid;gap:2px;min-width:0}.kb-rag-status strong{font-size:10px}.kb-rag-status small{overflow:hidden;color:var(--workspace-muted,#6e6e73);font-size:8px;text-overflow:ellipsis;white-space:nowrap}.kb-rag-status b{color:#2991ff;font-size:9px}.kb-rag-status.is-ready{border-color:rgba(35,201,165,.2);background:rgba(35,201,165,.065)}.kb-rag-status.is-ready .kb-rag-signal i{background:#26b998}.kb-rag-status.is-pending .kb-rag-signal i{background:#8673ff}.kb-rag-status.is-danger .kb-rag-signal i{background:#ff5f6d}.kb-table-rag{display:inline-flex;align-items:center;gap:6px;padding:5px 8px;border:0;border-radius:999px;color:var(--workspace-muted,#6e6e73);background:rgba(127,127,127,.08);font-size:9px;font-weight:680;white-space:nowrap}.kb-table-rag i{width:6px;height:6px;border-radius:50%;background:#7e7e86}.kb-table-rag.is-ready{color:#179877;background:rgba(35,201,165,.09)}.kb-table-rag.is-ready i{background:#26b998}.kb-table-rag.is-pending i{background:#8673ff}.kb-table-rag.is-danger{color:#d70015;background:rgba(215,0,21,.07)}.kb-table-rag.is-danger i{background:#d70015}
.kb-card-status,.kb-table-state{display:flex;align-items:center;justify-content:flex-end;gap:6px;flex-wrap:wrap}.kb-freeze-pill{display:inline-flex;align-items:center;gap:4px;width:max-content;padding:5px 9px;border:1px solid rgba(91,169,255,.24);border-radius:999px;color:#2991ff;background:rgba(34,129,235,.11);font-size:11px;font-weight:720;line-height:1}.kb-freeze-pill svg{width:12px;height:12px;fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}.kb-product-card.is-frozen{position:relative;overflow:hidden;border-color:rgba(91,169,255,.28);background:linear-gradient(145deg,rgba(56,139,231,.09),rgba(127,127,127,.035) 48%),rgba(28,28,30,.78);box-shadow:inset 0 1px rgba(180,221,255,.12),0 18px 48px rgba(0,0,0,.25)}.kb-product-card.is-frozen::before{position:absolute;inset:0;z-index:0;pointer-events:none;background:radial-gradient(circle at 92% 8%,rgba(132,202,255,.16),transparent 34%),linear-gradient(120deg,transparent 35%,rgba(255,255,255,.035) 50%,transparent 65%);content:""}.kb-product-card.is-frozen>*{position:relative;z-index:1}.kb-avatar.is-frozen{filter:saturate(.52) brightness(.92);box-shadow:inset 0 1px rgba(255,255,255,.26),0 8px 22px rgba(37,118,201,.16)}
.kb-table-shell{max-width:1240px;margin:0 auto 20px;overflow:hidden;border:1px solid rgba(127,127,127,.15);border-radius:22px;background:rgba(255,255,255,.7);box-shadow:0 12px 36px rgba(0,0,0,.05);backdrop-filter:blur(22px)}.kb-table{width:100%;min-width:880px;border-collapse:collapse}.kb-table th{padding:13px 16px;color:var(--workspace-muted,#6e6e73);font-size:10px;font-weight:620;text-align:left;background:rgba(127,127,127,.045)}.kb-table td{padding:15px 16px;border-top:1px solid rgba(127,127,127,.11);font-size:12px}.kb-table-name{display:flex;align-items:center;gap:11px;min-width:220px}.kb-table-name>span:last-child{display:grid;gap:4px;min-width:0}.kb-table-name strong,.kb-table-name small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.kb-table-name strong{font-size:13px}.kb-table-name small{max-width:280px;color:var(--workspace-muted,#6e6e73)}
.kb-table tbody tr{transition:background-color .18s ease,box-shadow .18s ease}.kb-table tbody tr.is-frozen{background:linear-gradient(90deg,rgba(56,139,231,.1),rgba(56,139,231,.025) 52%,transparent);box-shadow:inset 3px 0 rgba(41,145,255,.58)}
.asset-modal{position:fixed;z-index:80;inset:0;display:grid;place-items:center;padding:20px;background:rgba(5,9,18,.5);backdrop-filter:blur(10px);animation:asset-modal-in .2s ease}.asset-modal-card{display:grid;gap:17px;width:min(520px,100%);max-height:min(720px,calc(100dvh - 40px));overflow:auto;padding:26px;border:1px solid rgba(255,255,255,.14);border-radius:26px;color:var(--workspace-text);background:rgba(28,28,31,.94);box-shadow:0 30px 90px rgba(0,0,0,.38)}.theme-light .asset-modal-card{border-color:rgba(0,0,0,.1);background:rgba(255,255,255,.95)}.asset-modal-card header{display:flex;align-items:flex-start;justify-content:space-between;gap:18px}.asset-modal-card header h2{margin:5px 0}.asset-modal-card header p{margin:0;color:var(--workspace-muted,#6e6e73);font-size:12px}.asset-modal-card header>button{width:34px;height:34px;padding:0;border:0;border-radius:50%;color:var(--workspace-muted,#6e6e73);background:rgba(127,127,127,.1);font-size:20px}.asset-modal-card>label{display:grid;gap:7px;color:var(--workspace-muted,#6e6e73);font-size:11px}.asset-modal-card>label small{font-size:9px}.asset-modal-card fieldset{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:0;padding:0;border:0}.asset-modal-card legend{margin-bottom:8px;color:var(--workspace-muted,#6e6e73);font-size:11px}.asset-modal-card fieldset label{display:flex;align-items:flex-start;gap:9px;padding:13px;border:1px solid rgba(127,127,127,.16);border-radius:14px;cursor:pointer}.asset-modal-card fieldset input{width:auto;margin-top:2px;accent-color:#168bff}.asset-modal-card fieldset span{display:grid;gap:4px}.asset-modal-card fieldset strong{font-size:11px}.asset-modal-card fieldset small{color:var(--workspace-muted,#6e6e73);font-size:9px}.asset-modal-card footer{display:flex;justify-content:flex-end;gap:10px;margin-top:2px}.asset-button.danger{border:0;color:#fff;background:#d92d3a}.danger-dialog{justify-items:center;width:min(420px,100%);text-align:center}.danger-dialog h2{margin:0;font-size:20px}.danger-dialog p{margin:0;color:var(--workspace-muted,#6e6e73);line-height:1.6}.danger-mark{display:grid;place-items:center;width:46px;height:46px;border-radius:50%;color:#ff707c;background:rgba(255,70,86,.12);font-size:22px;font-weight:700}.danger-dialog footer{width:100%;justify-content:center;margin-top:8px}@keyframes asset-modal-in{from{opacity:0}}
:global(html[data-theme="dark"]) .kb-table-shell{background:rgba(28,28,30,.78)}
@media(prefers-color-scheme:dark){.asset-workspace{--workspace-bg:#090a0c;--workspace-text:#f5f5f7;--workspace-muted:#a1a1a6}.asset-stats article,.asset-panel,.asset-card{border-color:rgba(255,255,255,.1);background:rgba(28,28,30,.78);box-shadow:0 18px 48px rgba(0,0,0,.3)}.asset-workspace input,.asset-workspace textarea,.asset-workspace select,.asset-button.ghost{border-color:rgba(255,255,255,.12);background:rgba(255,255,255,.06)}.endpoint-row,.asset-pill{background:rgba(255,255,255,.07)}.key-panel,.key-row{border-color:rgba(255,255,255,.09)}}
:global(html[data-theme="dark"]) .asset-workspace{--workspace-bg:#090a0c;--workspace-text:#f5f5f7;--workspace-muted:#a1a1a6}:global(html[data-theme="dark"]) .asset-stats article,:global(html[data-theme="dark"]) .asset-panel,:global(html[data-theme="dark"]) .asset-card{border-color:rgba(255,255,255,.1);background:rgba(28,28,30,.78);box-shadow:0 18px 48px rgba(0,0,0,.3)}:global(html[data-theme="dark"]) .asset-workspace input,:global(html[data-theme="dark"]) .asset-workspace textarea,:global(html[data-theme="dark"]) .asset-workspace select,:global(html[data-theme="dark"]) .asset-button.ghost{border-color:rgba(255,255,255,.12);background:rgba(255,255,255,.06)}
:global(html[data-theme="light"]) .asset-workspace{--workspace-bg:#f5f5f7;--workspace-text:#1d1d1f;--workspace-muted:#6e6e73}:global(html[data-theme="light"]) .asset-stats article,:global(html[data-theme="light"]) .asset-panel,:global(html[data-theme="light"]) .asset-card{border-color:rgba(0,0,0,.07);background:rgba(255,255,255,.82);box-shadow:0 12px 36px rgba(0,0,0,.06)}
.asset-workspace.theme-light{--workspace-bg:#f5f5f7;--workspace-text:#1d1d1f;--workspace-muted:#6e6e73;background:#f5f5f7;color:#1d1d1f}.theme-light .asset-stats article,.theme-light .asset-panel,.theme-light .asset-card{border-color:rgba(0,0,0,.07);background:rgba(255,255,255,.88);box-shadow:0 12px 36px rgba(0,0,0,.06)}.theme-light input,.theme-light textarea,.theme-light select,.theme-light .asset-button.ghost{border-color:rgba(0,0,0,.12);background:rgba(255,255,255,.84);color:#1d1d1f}.theme-light .endpoint-row,.theme-light .asset-pill{background:rgba(118,118,128,.1)}
.asset-workspace.theme-dark{--workspace-bg:#090a0c;--workspace-text:#f5f5f7;--workspace-muted:#a1a1a6;background:#090a0c;color:#f5f5f7}
.asset-workspace.theme-dark .kb-table-shell{border-color:rgba(255,255,255,.1);background:rgba(28,28,30,.78);box-shadow:0 18px 48px rgba(0,0,0,.3)}
:global(html[data-theme="dark"]) .kb-product-card.is-frozen,.asset-workspace.theme-dark .kb-product-card.is-frozen{border-color:rgba(91,169,255,.28);background:linear-gradient(145deg,rgba(56,139,231,.09),rgba(127,127,127,.035) 48%),rgba(28,28,30,.78);box-shadow:inset 0 1px rgba(180,221,255,.12),0 18px 48px rgba(0,0,0,.25)}
:global(html[data-theme="light"]) .kb-product-card.is-frozen,.asset-workspace.theme-light .kb-product-card.is-frozen{border-color:rgba(0,113,227,.22);background:linear-gradient(145deg,rgba(0,113,227,.07),rgba(255,255,255,.78) 52%),rgba(255,255,255,.88);box-shadow:inset 0 1px rgba(255,255,255,.8),0 12px 36px rgba(31,91,151,.1)}
@media(max-width:980px){.asset-stats{grid-template-columns:repeat(2,minmax(0,1fr))}.asset-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.compact-form,.document-tools,.workflow-fields,.invitation-form,.key-create,.deployment-tester{grid-template-columns:1fr}.document-tools .text-content{grid-column:auto}.secret-reveal{grid-template-columns:1fr}.invitation-form .form-heading,.tester-heading,.deployment-tester pre{grid-column:auto}.key-row{grid-template-columns:minmax(150px,1fr) minmax(120px,1fr) auto}.key-row span:nth-of-type(2),.key-row span:nth-of-type(3){display:none}.kb-table{min-width:0}.kb-table th:nth-child(3),.kb-table td:nth-child(3),.kb-table th:nth-child(7),.kb-table td:nth-child(7){display:none}}
@media(max-width:980px){.deployment-library-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.deployment-workspace-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.tester-work-panel,.invocation-work-panel{grid-column:1/-1}.invocation-work-panel .invocation-row{grid-template-columns:90px minmax(120px,1fr) 80px 70px minmax(150px,auto)}.invocation-work-panel .invocation-row>span:nth-of-type(3){display:block}}
@media(max-width:760px){.member-list article,.invitation-list article{grid-template-columns:minmax(0,1fr) auto}.member-status{display:none}.member-actions{grid-column:1/3;justify-content:flex-start}.invitation-list article .asset-link{grid-column:2}.kb-table th:nth-child(2),.kb-table td:nth-child(2),.kb-table th:nth-child(5),.kb-table td:nth-child(5){display:none}.kb-table-name{min-width:0}}
@media(max-width:620px){.asset-workspace{height:100dvh;padding:74px 14px 22px}.asset-header{display:grid}.asset-stats,.asset-grid,.agent-form,.deployment-form{grid-template-columns:1fr}.welcome-panel{display:grid}.asset-card footer,.deployment-actions{align-items:flex-start;flex-wrap:wrap}.key-create{grid-template-columns:1fr}.invocation-row{grid-template-columns:80px 1fr}.document-list article{grid-template-columns:1fr auto}.document-list article>span:last-child{grid-column:1/3}.secret-reveal{bottom:8px}.asset-header .asset-button{justify-self:start;min-width:88px;white-space:nowrap}.member-role{max-width:120px;text-align:right;overflow:hidden;text-overflow:ellipsis}.member-list small{overflow-wrap:anywhere}}
@media(max-width:620px){.deployment-library-toolbar{align-items:flex-start;flex-direction:column}.deployment-library-grid,.deployment-workspace-metrics{grid-template-columns:1fr}.deployment-create-card,.deployment-product-card{min-height:260px}.deployment-workspace-header{grid-template-columns:auto minmax(0,1fr)}.deployment-workspace-actions{grid-column:1/-1;justify-content:flex-start}.deployment-endpoint-hero{grid-template-columns:1fr 1fr}.deployment-endpoint-hero>div{grid-column:1/-1}.deployment-work-panel{padding:18px;border-radius:18px}.deployment-modal-card{grid-template-columns:1fr}.deployment-modal-card>header,.deployment-modal-card>footer{grid-column:auto}.invocation-work-panel .invocation-row{grid-template-columns:78px minmax(0,1fr)}.invocation-work-panel .invocation-row>span:not(:first-child){display:none}.invocation-work-panel .invocation-row>code{overflow:hidden;text-overflow:ellipsis}.deployment-product-card footer{align-items:flex-start;flex-direction:column}}
@media(max-width:620px){.knowledge-toolbar{align-items:flex-start}.knowledge-toolbar-actions{align-items:flex-end;flex-direction:column}.kb-product-card{min-height:260px}.asset-modal-card{padding:21px;border-radius:22px}.asset-modal-card fieldset{grid-template-columns:1fr}.kb-table-shell{border-radius:17px}.kb-table{table-layout:fixed}.kb-table th,.kb-table td{padding:12px 9px}.kb-table th:nth-child(4),.kb-table td:nth-child(4){width:38px;text-align:center}.kb-table th:last-child,.kb-table td:last-child{width:68px}.kb-table-name{gap:9px}.kb-table-name small{display:none}.kb-table .card-actions{display:grid;justify-items:end;gap:5px}}
@media(prefers-reduced-motion:reduce){.asset-button,.kb-create-card,.agent-product-card,.workflow-product-card,.deployment-create-card,.deployment-product-card{transition:none}.asset-button:active,.kb-create-card:active,.agent-product-card:active,.workflow-product-card:active,.deployment-create-card:active,.deployment-product-card:active{transform:none}.asset-modal{animation:none}}
</style>
