<script setup>
import { computed, reactive, ref, watch } from 'vue'
import ApiEndpointEditorCard from './ApiEndpointEditorCard.vue'
import UiPager from '../common/UiPager.vue'
import vModalFocus from '../common/modalFocusDirective'
import vInteractiveRow from '../common/interactiveRowDirective'

const props = defineProps({
  apiSettingState: { type: Object, required: true },
  resetApiForm: { type: Function, required: true },
  upsertApiEndpoint: { type: Function, required: true },
  endpointToForm: { type: Function, required: true },
  setDefaultApiEndpoint: { type: Function, required: true },
  deleteApiEndpoint: { type: Function, required: true },
  endpointIdOf: { type: Function, required: true },
  toggleFreezeChatEndpoint: { type: Function, required: true },
  probeChatApiEndpoint: { type: Function, required: true },
})

const filters = reactive({
  q: '',
  pool: 'all',
  status: 'all',
  mount: 'all',
  task: '',
})

const detailPoolTab = ref('main')
const instancePage = ref(1)
const instancePageSize = 8
const selectedInstanceKey = ref('')
const detailModalOpen = ref(false)
const endpointConfigModalOpen = ref(false)
const createModalOpen = ref(false)
const instanceEditModalOpen = ref(false)
const editorModalOpen = ref(false)
const detailTab = ref('overview')
const createSubmitting = ref(false)
const endpointSubmitting = ref(false)
const openMenuKey = ref('')
const menuStyle = reactive({ top: '0px', left: '0px' })
const endpointEditorModalOpen = ref(false)
const localNotice = ref('')
const probingEndpointId = ref('')
const probeResults = reactive({})

const createForm = reactive({
  pool: 'main',
  api_instance_id: '',
  name: '',
  remark: '',
  model: '',
  provider: 'openai_compatible',
  base_url: '',
  api_key: '',
  task: '',
  tenant_id: '*',
  enabled: true,
  priority: 100,
  weight: 100,
  max_concurrency: 8,
  timeout_seconds: 120,
  retry_max: 2,
  circuit_fail_threshold: 5,
  circuit_open_seconds: 30,
  input_per_1k: 0,
  output_per_1k: 0,
})

const instanceEditForm = reactive({
  api_instance_name: '',
  api_instance_remark: '',
  provider: '',
  base_url: '',
  api_key: '',
})

const endpointDraft = reactive({
  mode: 'create',
  pool: 'main',
  endpoint_id: '',
  model: '',
  task: '',
  name: '',
  provider: 'openai_compatible',
  base_url: '',
  api_key: '',
  tenant_id: '*',
  enabled: true,
  frozen: false,
  priority: 100,
  weight: 100,
  max_concurrency: 8,
  timeout_seconds: 120,
  retry_max: 2,
  circuit_fail_threshold: 5,
  circuit_open_seconds: 30,
  input_per_1k: 0,
  output_per_1k: 0,
})

const fieldHelp = {
  pool: '选择端点写入的资源池。5173 对话主回复必须配置 Chat 池里的 chat_llm，不会读取 Main/Aux 池的 LLM。',
  apiInstanceId: '同一个供应商账号或同一个 API Key 的稳定标识。同实例下新增端点可继承已有 Key。',
  provider: '供应商标识，仅用于分类和观测。OpenAI 兼容接口可填写 openai_compatible 或供应商名。',
  baseUrl: 'OpenAI 兼容接口根地址，通常以 /v1 结尾，系统会在后面拼接 /chat/completions 或 /embeddings。',
  apiKey: '只在保存时提交，列表不会回显明文。保存成功后应出现 key_fingerprint；若仍显示 NO_KEY，说明 Key 没有入库。',
  task: '消费点决定哪个业务会使用该端点。对话主回复是 chat_llm，标题/规划等辅助调用是 chat_aux/chat_summary，记忆向量是 chat_embedding。',
  tenant: '租户可见范围。* 表示所有租户；填写具体租户 ID 时，其他租户不会选中该端点。',
  priority: '数值越小优先级越高。系统先在最高优先级的一组端点里选择。',
  weight: '同优先级内的流量权重。权重越大，被选中的概率越高。',
  concurrency: '该端点允许同时进行的请求数，超过后会进入忙碌或等待逻辑。',
  timeout: '请求上游 API 的超时时间。流式对话模型较慢时可以适当调大。',
  retry: '遇到超时、断连、429 或 5xx 时的最大重试次数。',
  circuit: '连续失败达到阈值后临时熔断，熔断期内不会优先选择该端点。',
  price: '用于用量和成本统计，不影响实际 API 调用。',
  enabled: '禁用后端点不会被业务调用；冻结通常用于临时风控或故障隔离。',
}

function helpTitle(key) {
  return fieldHelp[key] || ''
}

function normalizePoolItems(items, poolKind) {
  return (Array.isArray(items) ? items : []).map((item) => {
    const endpointId = String(item?.endpoint_id || item?.endpointId || '').trim()
    const task = String(item?.task || '').trim()
    const model = String(item?.model || '').trim()
    const fingerprint = String(item?.key_fingerprint || '').trim()
    const instanceId = String(item?.api_instance_id || item?.apiInstanceId || endpointId || '').trim()
    const instanceName = String(item?.api_instance_name || item?.apiInstanceName || item?.name || instanceId).trim()
    const instanceRemark = String(item?.api_instance_remark || item?.apiInstanceRemark || '').trim()
    const rotationState = String(item?.rotation_state || '').trim().toUpperCase()
    const expiresAt = String(item?.expires_at || '').trim()
    return {
      raw: item,
      poolKind,
      endpointId,
      instanceId,
      instanceName,
      instanceRemark,
      task,
      model,
      fingerprint,
      enabled: Boolean(item?.enabled ?? true),
      frozen: Boolean(item?.frozen ?? false),
      provider: String(item?.provider || '').trim(),
      baseUrl: String(item?.base_url || item?.baseUrl || '').trim(),
      rotationState: rotationState || 'ACTIVE',
      expiresAt,
      mountedKey: `${poolKind}:${task || 'default'}:${endpointId || 'unknown'}`,
    }
  })
}

const chatHealthByEndpoint = computed(() => Object.fromEntries(
  (props.apiSettingState.chatApiInsights?.health || []).map((item) => [
    String(item?.endpoint_id || item?.endpointId || ''),
    item,
  ]),
))

const allEndpoints = computed(() => [
  ...normalizePoolItems(props.apiSettingState.mainPool, 'main'),
  ...normalizePoolItems(props.apiSettingState.auxPool, 'aux'),
  ...normalizePoolItems(props.apiSettingState.chatPool, 'chat'),
].map((endpoint) => ({
  ...endpoint,
  health: endpoint.poolKind === 'chat' ? chatHealthByEndpoint.value[endpoint.endpointId] || null : null,
})))

const consumerEndpointOptions = computed(() => {
  const fromState = Array.isArray(props.apiSettingState.consumerEndpoints)
    ? props.apiSettingState.consumerEndpoints
    : []
  const fallback = [
    { task: 'rerank', pool: 'main', label: 'RAG 候选重排', capability: 'rerank' },
    { task: 'embedding', pool: 'main', label: '知识库向量嵌入', capability: 'embedding' },
    { task: 'llm', pool: 'main', label: '执行平面通用 LLM', capability: 'llm' },
    { task: 'summary_llm', pool: 'aux', label: '文档/知识库总结 LLM', capability: 'llm' },
    { task: 'summary_embedding', pool: 'aux', label: '总结辅助 Embedding', capability: 'embedding' },
    { task: 'chat_llm', pool: 'chat', label: '对话主回复 LLM', capability: 'llm' },
    { task: 'chat_aux', pool: 'chat', label: '对话辅助 LLM', capability: 'llm' },
    { task: 'chat_summary', pool: 'chat', label: '对话记忆总结 LLM', capability: 'llm' },
    { task: 'chat_embedding', pool: 'chat', label: '对话记忆 Embedding', capability: 'embedding' },
  ]
  const seen = new Set()
  return (fromState.length ? fromState : fallback)
    .map((item) => ({
      task: String(item?.task || '').trim(),
      pool: String(item?.pool || '').trim() || 'main',
      label: String(item?.label || item?.task || '').trim(),
      capability: String(item?.capability || '').trim(),
      description: String(item?.description || '').trim(),
    }))
    .filter((item) => {
      const key = `${item.pool}:${item.task}`
      if (!item.task || seen.has(key)) return false
      seen.add(key)
      return true
    })
})

const consumerOptionsForCreatePool = computed(() => {
  return consumerEndpointOptions.value.filter((item) => item.pool === createForm.pool)
})

const consumerFilterOptions = computed(() => {
  const seen = new Set()
  return consumerEndpointOptions.value.filter((item) => {
    if (seen.has(item.task)) return false
    seen.add(item.task)
    return true
  })
})

const consumerCatalogRows = computed(() => {
  return consumerEndpointOptions.value.map((item) => {
    const mounted = allEndpoints.value.filter((ep) => ep.poolKind === item.pool && ep.task === item.task)
    return {
      ...item,
      mountedCount: mounted.length,
      enabledCount: mounted.filter((ep) => ep.enabled && !ep.frozen).length,
      models: Array.from(new Set(mounted.map((ep) => ep.model).filter(Boolean))),
    }
  })
})

function consumerLabel(task, pool = '') {
  const found = consumerEndpointOptions.value.find((item) => item.task === task && (!pool || item.pool === pool))
    || consumerEndpointOptions.value.find((item) => item.task === task)
  if (!found) return task || 'default'
  return `${found.label || found.task} (${found.task})`
}

function poolLabel(kind) {
  if (kind === 'main') return 'Main'
  if (kind === 'aux') return 'Aux'
  return 'Chat'
}

function fingerprintLabel(value) {
  const text = String(value || '').trim()
  if (!text) return 'NO_KEY'
  if (text.length <= 18) return text
  return `${text.slice(0, 10)}...${text.slice(-4)}`
}

function hostLabel(baseUrl) {
  const raw = String(baseUrl || '').trim()
  if (!raw) return '-'
  try {
    return new URL(raw).host
  } catch {
    return raw
  }
}

function formatDateTime(value) {
  const text = String(value || '').trim()
  if (!text) return '-'
  const ts = Date.parse(text)
  if (!Number.isFinite(ts)) return text
  const date = new Date(ts)
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  const hh = String(date.getHours()).padStart(2, '0')
  const mm = String(date.getMinutes()).padStart(2, '0')
  return `${y}-${m}-${d} ${hh}:${mm}`
}

function instanceCreatedAt(instance) {
  const values = (instance?.endpoints || [])
    .map((ep) => ep.raw?.created_at || ep.raw?.createdAt || ep.raw?.updated_at || ep.raw?.updatedAt || '')
    .map((value) => ({ value, ts: Date.parse(value) }))
    .filter((x) => Number.isFinite(x.ts))
    .sort((a, b) => a.ts - b.ts)
  return values[0]?.value || ''
}

function instanceCreatedTs(instance) {
  const raw = instanceCreatedAt(instance)
  const ts = Date.parse(raw || '')
  return Number.isFinite(ts) ? ts : 0
}

const openMenuInstance = computed(() => {
  return filteredInstances.value.find((x) => x.instanceKey === openMenuKey.value) || null
})

function showNotice(message) {
  localNotice.value = String(message || '').trim()
}

function toggleInstanceMenu(instanceKey, event) {
  if (openMenuKey.value === instanceKey) {
    closeInstanceMenu()
    return
  }
  const rect = event?.currentTarget?.getBoundingClientRect?.()
  if (rect) {
    const width = 168
    const estimatedHeight = 236
    const viewportWidth = window?.innerWidth || 1280
    const viewportHeight = window?.innerHeight || 800
    const dropDownTop = rect.bottom + 8
    const dropUpTop = rect.top - estimatedHeight - 8
    const top = dropDownTop + estimatedHeight > viewportHeight - 8
      ? Math.max(8, dropUpTop)
      : dropDownTop
    const left = Math.max(8, Math.min(rect.right - width, viewportWidth - width - 8))
    menuStyle.top = `${Math.round(top)}px`
    menuStyle.left = `${Math.round(left)}px`
  }
  openMenuKey.value = instanceKey
}

function closeInstanceMenu() {
  openMenuKey.value = ''
}

function expiresSoon(expiresAt) {
  const text = String(expiresAt || '').trim()
  if (!text) return false
  const ts = Date.parse(text)
  if (!Number.isFinite(ts)) return false
  const now = Date.now()
  return ts > now && ts - now <= 7 * 24 * 3600 * 1000
}

function isExpired(expiresAt) {
  const text = String(expiresAt || '').trim()
  if (!text) return false
  const ts = Date.parse(text)
  return Number.isFinite(ts) && ts <= Date.now()
}

function instanceStatus(item) {
  if (item.expiredCount > 0) return 'expired'
  if (item.frozenCount > 0 && item.mountedCount > 0) return 'risk'
  if (item.warningCount > 0) return 'warning'
  if (item.enabledCount > 0) return 'healthy'
  return 'idle'
}

function statusLabel(status) {
  if (status === 'expired') return '已过期'
  if (status === 'risk') return '风险'
  if (status === 'warning') return '预警'
  if (status === 'healthy') return '健康'
  return '空闲'
}

function statusTone(status) {
  if (status === 'expired') return 'critical'
  if (status === 'risk') return 'danger'
  if (status === 'warning') return 'warning'
  if (status === 'healthy') return 'success'
  return 'neutral'
}

function riskRank(status) {
  if (status === 'expired') return 5
  if (status === 'risk') return 4
  if (status === 'warning') return 3
  if (status === 'healthy') return 2
  return 1
}

const apiInstances = computed(() => {
  const map = new Map()
  for (const item of allEndpoints.value) {
    const key = item.instanceId || item.fingerprint || item.endpointId || 'NO_INSTANCE'
    if (!map.has(key)) {
      map.set(key, {
        instanceKey: key,
        instanceId: item.instanceId || '-',
        instanceName: item.instanceName || item.model || '-',
        instanceRemark: item.instanceRemark || '',
        model: item.instanceName || item.model || '-',
        fingerprint: item.fingerprint || '',
        providers: new Set(),
        baseUrls: new Set(),
        pools: new Set(),
        tasks: new Set(),
        mountedCount: 0,
        enabledCount: 0,
        frozenCount: 0,
        expiredCount: 0,
        warningCount: 0,
        endpoints: [],
      })
    }
    const row = map.get(key)
    row.endpoints.push(item)
    row.mountedCount += 1
    if (!row.fingerprint && item.fingerprint) row.fingerprint = item.fingerprint
    if (item.enabled) row.enabledCount += 1
    if (item.frozen) row.frozenCount += 1
    if (isExpired(item.expiresAt)) row.expiredCount += 1
    if (expiresSoon(item.expiresAt) || item.rotationState === 'ROTATING') row.warningCount += 1
    if (item.provider) row.providers.add(item.provider)
    if (item.baseUrl) row.baseUrls.add(item.baseUrl)
    row.pools.add(poolLabel(item.poolKind))
    if (item.task) row.tasks.add(item.task)
  }
  return Array.from(map.values())
    .map((item) => {
      const status = instanceStatus(item)
      return {
        ...item,
        status,
        statusLabel: statusLabel(status),
        statusTone: statusTone(status),
        providers: Array.from(item.providers),
        baseUrls: Array.from(item.baseUrls),
        pools: Array.from(item.pools),
        tasks: Array.from(item.tasks),
      }
    })
})

const filteredInstances = computed(() => {
  const q = String(filters.q || '').trim().toLowerCase()
  const rows = apiInstances.value.filter((item) => {
    if (filters.pool !== 'all' && !item.pools.map((x) => x.toLowerCase()).includes(filters.pool)) return false
    if (filters.status !== 'all' && item.status !== filters.status) return false
    if (filters.mount === 'single' && item.mountedCount !== 1) return false
    if (filters.mount === 'multi' && item.mountedCount <= 1) return false
    if (filters.mount === 'empty' && item.mountedCount !== 0) return false
    if (filters.task && !item.tasks.join(' ').toLowerCase().includes(String(filters.task).toLowerCase())) return false
    if (q) {
      const bag = [
        item.instanceId,
        item.instanceName,
        item.instanceRemark,
        item.model,
        item.fingerprint,
        item.providers.join(' '),
        item.baseUrls.join(' '),
        item.pools.join(' '),
        item.tasks.join(' '),
        ...item.endpoints.map((x) => x.endpointId),
      ].join(' ').toLowerCase()
      if (!bag.includes(q)) return false
    }
    return true
  })

  rows.sort((a, b) =>
    instanceCreatedTs(b) - instanceCreatedTs(a)
    || b.mountedCount - a.mountedCount
    || a.model.localeCompare(b.model),
  )
  return rows
})

const instancePageTotal = computed(() => {
  return Math.max(1, Math.ceil(filteredInstances.value.length / instancePageSize))
})

const currentInstancePage = computed(() => {
  return Math.max(1, Math.min(Number(instancePage.value || 1), instancePageTotal.value))
})

const pagedInstances = computed(() => {
  const start = (currentInstancePage.value - 1) * instancePageSize
  return filteredInstances.value.slice(start, start + instancePageSize)
})

const instancePageStart = computed(() => {
  if (!filteredInstances.value.length) return 0
  return (currentInstancePage.value - 1) * instancePageSize + 1
})

const instancePageEnd = computed(() => {
  if (!filteredInstances.value.length) return 0
  return Math.min(filteredInstances.value.length, instancePageStart.value + instancePageSize - 1)
})

function setInstancePage(page) {
  closeInstanceMenu()
  instancePage.value = Math.max(1, Math.min(Number(page || 1), instancePageTotal.value))
}

const selectedInstance = computed(() => {
  const rows = filteredInstances.value
  if (!rows.length) return null
  return rows.find((x) => x.instanceKey === selectedInstanceKey.value) || rows[0]
})

const summary = computed(() => {
  const rows = apiInstances.value
  return {
    total: rows.length,
    healthy: rows.filter((x) => x.status === 'healthy').length,
    warning: rows.filter((x) => x.status === 'warning').length,
    risk: rows.filter((x) => x.status === 'risk' || x.status === 'expired').length,
    mounted: rows.reduce((sum, x) => sum + x.mountedCount, 0),
  }
})

const activePoolRows = computed(() => {
  if (detailPoolTab.value === 'aux') return props.apiSettingState.auxPool || []
  if (detailPoolTab.value === 'chat') {
    return (props.apiSettingState.chatPool || []).map((item) => ({
      ...item,
      poolKind: 'chat',
      endpointId: props.endpointIdOf(item),
      health: chatHealthByEndpoint.value[props.endpointIdOf(item)] || null,
    }))
  }
  return props.apiSettingState.mainPool || []
})

watch(
  () => filteredInstances.value.map((x) => x.instanceKey).join('|'),
  () => {
    if (instancePage.value > instancePageTotal.value) {
      instancePage.value = instancePageTotal.value
    }
    if (!selectedInstance.value && filteredInstances.value.length > 0) {
      selectedInstanceKey.value = filteredInstances.value[0].instanceKey
    }
    if (selectedInstanceKey.value && !filteredInstances.value.find((x) => x.instanceKey === selectedInstanceKey.value)) {
      selectedInstanceKey.value = filteredInstances.value[0]?.instanceKey || ''
    }
  },
  { immediate: true },
)

watch(
  () => [filters.q, filters.pool, filters.status, filters.mount, filters.task].join('|'),
  () => {
    closeInstanceMenu()
    instancePage.value = 1
  },
)

function openDetailModal(instanceKey) {
  selectedInstanceKey.value = instanceKey
  detailTab.value = 'overview'
  detailModalOpen.value = true
}

function closeDetailModal() {
  detailModalOpen.value = false
}

function openEndpointConfigModal(instance) {
  if (!instance) return
  selectedInstanceKey.value = instance.instanceKey
  resetEndpointDraft(instance)
  endpointConfigModalOpen.value = true
  closeInstanceMenu()
}

function closeEndpointConfigModal() {
  endpointConfigModalOpen.value = false
  endpointEditorModalOpen.value = false
}

function openCreateModal(kind = 'main') {
  resetCreateForm(kind)
  createModalOpen.value = true
}

function openInstanceEditModal(instance) {
  if (!instance) return
  selectedInstanceKey.value = instance.instanceKey
  instanceEditForm.api_instance_name = instance.instanceName || ''
  instanceEditForm.api_instance_remark = instance.instanceRemark || ''
  instanceEditForm.provider = inheritedProvider(instance)
  instanceEditForm.base_url = inheritedBaseUrl(instance)
  instanceEditForm.api_key = ''
  instanceEditModalOpen.value = true
  closeInstanceMenu()
}

function closeInstanceEditModal() {
  instanceEditModalOpen.value = false
}

function openEndpointEditor(item, kind) {
  props.endpointToForm(item, kind)
  editorModalOpen.value = true
}

function closeEditorModal() {
  editorModalOpen.value = false
}

function closeEndpointEditorModal() {
  endpointEditorModalOpen.value = false
}

function closeCreateModal() {
  createModalOpen.value = false
}

function baseEndpointForInstance(instance) {
  return instance?.endpoints?.[0] || {}
}

function inheritedBaseUrl(instance = selectedInstance.value) {
  return String(instance?.baseUrls?.[0] || baseEndpointForInstance(instance)?.baseUrl || '').trim()
}

function inheritedProvider(instance = selectedInstance.value) {
  return String(instance?.providers?.[0] || baseEndpointForInstance(instance)?.provider || 'openai_compatible').trim()
}

function inheritedPool(instance = selectedInstance.value) {
  return baseEndpointForInstance(instance)?.poolKind || 'main'
}

async function deleteInstance(instance) {
  if (!instance || !Array.isArray(instance.endpoints) || !instance.endpoints.length) return
  const endpointIds = instance.endpoints.map((x) => `${poolLabel(x.poolKind)}:${x.endpointId}`).join('\n')
  if (!window.confirm(`确认删除 API 实例 ${instance.model} 下的 ${instance.endpoints.length} 个端点吗？\n\n${endpointIds}`)) return
  for (let i = 0; i < instance.endpoints.length; i += 1) {
    const ep = instance.endpoints[i]
    await props.deleteApiEndpoint(ep.poolKind, ep.endpointId, {
      skipConfirm: true,
      silent: true,
      deferReload: i < instance.endpoints.length - 1,
    })
    if (props.apiSettingState.error) return
  }
  closeDetailModal()
  closeEndpointConfigModal()
}

function suggestedTask(kind) {
  const found = consumerEndpointOptions.value.find((item) => item.pool === kind)
  if (found?.task) return found.task
  if (kind === 'chat') return 'chat_llm'
  if (kind === 'aux') return 'summary_llm'
  return 'embedding'
}

function slugify(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

function resetCreateForm(kind = 'main') {
  createForm.pool = ['main', 'aux', 'chat'].includes(kind) ? kind : 'main'
  createForm.api_instance_id = ''
  createForm.name = ''
  createForm.remark = ''
  createForm.model = ''
  createForm.provider = 'openai_compatible'
  createForm.base_url = ''
  createForm.api_key = ''
  createForm.task = suggestedTask(createForm.pool)
  createForm.tenant_id = '*'
  createForm.enabled = true
  createForm.priority = 100
  createForm.weight = 100
  createForm.max_concurrency = 8
  createForm.timeout_seconds = 120
  createForm.retry_max = 2
  createForm.circuit_fail_threshold = 5
  createForm.circuit_open_seconds = 30
  createForm.input_per_1k = 0
  createForm.output_per_1k = 0
}

watch(
  () => createForm.pool,
  (pool) => {
    const allowed = consumerEndpointOptions.value.filter((item) => item.pool === pool).map((item) => item.task)
    if (!createForm.task || !allowed.includes(createForm.task)) {
      createForm.task = suggestedTask(pool)
    }
  },
)

const createEndpointId = computed(() => {
  const prefix = createForm.pool === 'chat' ? 'chat' : createForm.pool
  const model = slugify(createForm.model || 'endpoint')
  const task = slugify(createForm.task || 'task')
  const instance = slugify(createForm.api_instance_id || createForm.name || 'instance')
  const stamp = Date.now().toString(36)
  return `${prefix}-${instance}-${task}-${model}-${stamp}`.slice(0, 96)
})

const draftConsumerOptions = computed(() => {
  return consumerEndpointOptions.value.filter((item) => item.pool === endpointDraft.pool)
})

const endpointDraftId = computed(() => {
  if (endpointDraft.mode === 'edit') return String(endpointDraft.endpoint_id || '').trim()
  const prefix = endpointDraft.pool === 'chat' ? 'chat' : endpointDraft.pool
  const instance = slugify(selectedInstance.value?.instanceId || selectedInstance.value?.instanceName || 'instance')
  const task = slugify(endpointDraft.task || 'task')
  const model = slugify(endpointDraft.model || 'model')
  return `${prefix}-${instance}-${task}-${model}-${Date.now().toString(36)}`.slice(0, 96)
})

const endpointDraftReady = computed(() => {
  return Boolean(
    selectedInstance.value
    && String(selectedInstance.value.fingerprint || '').trim()
    && String(endpointDraft.model || '').trim()
    && String(endpointDraft.task || '').trim()
    && inheritedBaseUrl(selectedInstance.value)
  )
})

const createInstanceId = computed(() => {
  const prefix = createForm.pool === 'chat' ? 'chat' : createForm.pool
  return (slugify(createForm.api_instance_id || createForm.name) || `${prefix}-api-${Date.now().toString(36)}`).slice(0, 80)
})

const createFormReady = computed(() => {
  return Boolean(
    String(createForm.model || '').trim() &&
    String(createForm.base_url || '').trim() &&
    String(createForm.api_key || '').trim(),
  )
})

const instanceEditReady = computed(() => {
  return Boolean(
    selectedInstance.value
    && String(instanceEditForm.provider || '').trim()
    && String(instanceEditForm.base_url || '').trim()
    && (String(instanceEditForm.api_key || '').trim() || String(selectedInstance.value.fingerprint || '').trim()),
  )
})

async function submitCreateInstance() {
  if (!createFormReady.value || createSubmitting.value) return
  createSubmitting.value = true
  try {
    props.resetApiForm(createForm.pool)
    props.apiSettingState.editKind = createForm.pool
    props.apiSettingState.form.endpoint_id = createEndpointId.value
    props.apiSettingState.form.api_instance_id = createInstanceId.value
    props.apiSettingState.form.api_instance_name = String(createForm.name || '').trim() || createInstanceId.value
    props.apiSettingState.form.api_instance_remark = String(createForm.remark || '').trim()
    props.apiSettingState.form.name = String(createForm.name || '').trim() || createEndpointId.value
    props.apiSettingState.form.provider = createForm.provider
    props.apiSettingState.form.base_url = String(createForm.base_url || '').trim()
    props.apiSettingState.form.model = String(createForm.model || '').trim()
    props.apiSettingState.form.task = String(createForm.task || '').trim()
    props.apiSettingState.form.api_key = String(createForm.api_key || '').trim()
    props.apiSettingState.form.tenant_id = String(createForm.tenant_id || '*').trim() || '*'
    props.apiSettingState.form.enabled = !!createForm.enabled
    props.apiSettingState.form.frozen = false
    props.apiSettingState.form.priority = Number(createForm.priority) || 100
    props.apiSettingState.form.weight = Number(createForm.weight) || 100
    props.apiSettingState.form.max_concurrency = Number(createForm.max_concurrency) || 8
    props.apiSettingState.form.timeout_seconds = Number(createForm.timeout_seconds) || 120
    props.apiSettingState.form.retry_max = Number(createForm.retry_max) || 2
    props.apiSettingState.form.circuit_fail_threshold = Number(createForm.circuit_fail_threshold) || 5
    props.apiSettingState.form.circuit_open_seconds = Number(createForm.circuit_open_seconds) || 30
    props.apiSettingState.form.input_per_1k = Number(createForm.input_per_1k) || 0
    props.apiSettingState.form.output_per_1k = Number(createForm.output_per_1k) || 0
    await props.upsertApiEndpoint()
    if (!props.apiSettingState.error) {
      detailPoolTab.value = createForm.pool
      createModalOpen.value = false
      showNotice(`已创建 API 实例：${props.apiSettingState.form.api_instance_name || createInstanceId.value}`)
    }
  } finally {
    createSubmitting.value = false
  }
}

function openAddEndpointForInstance(instance) {
  if (!instance) return
  selectedInstanceKey.value = instance.instanceKey
  resetEndpointDraft(instance)
  endpointEditorModalOpen.value = true
  closeInstanceMenu()
}

function resetEndpointDraft(instance = selectedInstance.value) {
  const first = baseEndpointForInstance(instance)
  const kind = first.poolKind || 'main'
  endpointDraft.mode = 'create'
  endpointDraft.pool = kind
  endpointDraft.endpoint_id = ''
  endpointDraft.model = ''
  endpointDraft.task = suggestedTask(kind)
  endpointDraft.name = ''
  endpointDraft.provider = first.provider || 'openai_compatible'
  endpointDraft.base_url = first.baseUrl || ''
  endpointDraft.api_key = ''
  endpointDraft.tenant_id = first.raw?.tenant_id || first.raw?.tenantId || '*'
  endpointDraft.enabled = true
  endpointDraft.frozen = false
  endpointDraft.priority = Number(first.raw?.priority ?? 100)
  endpointDraft.weight = Number(first.raw?.weight ?? 100)
  endpointDraft.max_concurrency = Number(first.raw?.max_concurrency ?? first.raw?.maxConcurrency ?? 8)
  endpointDraft.timeout_seconds = Number(first.raw?.timeout_seconds ?? first.raw?.timeoutSeconds ?? 120)
  endpointDraft.retry_max = Number(first.raw?.retry_max ?? first.raw?.retryMax ?? 2)
  endpointDraft.circuit_fail_threshold = Number(first.raw?.circuit_fail_threshold ?? first.raw?.circuitFailThreshold ?? 5)
  endpointDraft.circuit_open_seconds = Number(first.raw?.circuit_open_seconds ?? first.raw?.circuitOpenSeconds ?? 30)
  endpointDraft.input_per_1k = Number(first.raw?.input_per_1k ?? 0)
  endpointDraft.output_per_1k = Number(first.raw?.output_per_1k ?? 0)
}

function editEndpointInConfig(ep) {
  if (!ep) return
  endpointDraft.mode = 'edit'
  endpointDraft.pool = ep.poolKind || 'main'
  endpointDraft.endpoint_id = ep.endpointId || ''
  endpointDraft.model = ep.model || ''
  endpointDraft.task = ep.task || suggestedTask(endpointDraft.pool)
  endpointDraft.name = ep.raw?.name || ep.endpointId || ''
  endpointDraft.provider = ep.provider || 'openai_compatible'
  endpointDraft.base_url = ep.baseUrl || ''
  endpointDraft.api_key = ''
  endpointDraft.tenant_id = ep.raw?.tenant_id || ep.raw?.tenantId || '*'
  endpointDraft.enabled = Boolean(ep.raw?.enabled ?? ep.enabled ?? true)
  endpointDraft.frozen = Boolean(ep.raw?.frozen ?? ep.frozen ?? false)
  endpointDraft.priority = Number(ep.raw?.priority ?? 100)
  endpointDraft.weight = Number(ep.raw?.weight ?? 100)
  endpointDraft.max_concurrency = Number(ep.raw?.max_concurrency ?? ep.raw?.maxConcurrency ?? 8)
  endpointDraft.timeout_seconds = Number(ep.raw?.timeout_seconds ?? ep.raw?.timeoutSeconds ?? 120)
  endpointDraft.retry_max = Number(ep.raw?.retry_max ?? ep.raw?.retryMax ?? 2)
  endpointDraft.circuit_fail_threshold = Number(ep.raw?.circuit_fail_threshold ?? ep.raw?.circuitFailThreshold ?? 5)
  endpointDraft.circuit_open_seconds = Number(ep.raw?.circuit_open_seconds ?? ep.raw?.circuitOpenSeconds ?? 30)
  endpointDraft.input_per_1k = Number(ep.raw?.input_per_1k ?? 0)
  endpointDraft.output_per_1k = Number(ep.raw?.output_per_1k ?? 0)
}

function openEndpointDraftEditor(ep) {
  editEndpointInConfig(ep)
  endpointEditorModalOpen.value = true
}

async function saveEndpointDraft() {
  const instance = selectedInstance.value
  if (!endpointDraftReady.value || !instance || endpointSubmitting.value) return
  endpointSubmitting.value = true
  const id = endpointDraftId.value
  try {
    props.resetApiForm(endpointDraft.pool)
    props.apiSettingState.editKind = endpointDraft.pool
    props.apiSettingState.form.endpoint_id = id
    props.apiSettingState.form.api_instance_id = instance.instanceId
    props.apiSettingState.form.api_instance_name = instance.instanceName
    props.apiSettingState.form.api_instance_remark = instance.instanceRemark || ''
    props.apiSettingState.form.name = String(endpointDraft.name || '').trim() || id
    props.apiSettingState.form.provider = inheritedProvider(instance)
    props.apiSettingState.form.base_url = inheritedBaseUrl(instance)
    props.apiSettingState.form.model = String(endpointDraft.model || '').trim()
    props.apiSettingState.form.task = String(endpointDraft.task || '').trim()
    props.apiSettingState.form.api_key = ''
    props.apiSettingState.form.tenant_id = String(endpointDraft.tenant_id || '*').trim() || '*'
    props.apiSettingState.form.enabled = !!endpointDraft.enabled
    props.apiSettingState.form.frozen = !!endpointDraft.frozen
    props.apiSettingState.form.priority = Number(endpointDraft.priority) || 100
    props.apiSettingState.form.weight = Number(endpointDraft.weight) || 100
    props.apiSettingState.form.max_concurrency = Number(endpointDraft.max_concurrency) || 8
    props.apiSettingState.form.timeout_seconds = Number(endpointDraft.timeout_seconds) || 120
    props.apiSettingState.form.retry_max = Number(endpointDraft.retry_max) || 2
    props.apiSettingState.form.circuit_fail_threshold = Number(endpointDraft.circuit_fail_threshold) || 5
    props.apiSettingState.form.circuit_open_seconds = Number(endpointDraft.circuit_open_seconds) || 30
    props.apiSettingState.form.input_per_1k = Number(endpointDraft.input_per_1k) || 0
    props.apiSettingState.form.output_per_1k = Number(endpointDraft.output_per_1k) || 0
    await props.upsertApiEndpoint()
    if (!props.apiSettingState.error) {
      showNotice(`${endpointDraft.mode === 'edit' ? '已保存' : '已添加'}端点：${id}`)
      endpointEditorModalOpen.value = false
      resetEndpointDraft(selectedInstance.value)
    }
  } finally {
    endpointSubmitting.value = false
  }
}

async function submitInstanceEdit() {
  const instance = selectedInstance.value
  const first = baseEndpointForInstance(instance)
  if (!instanceEditReady.value || !instance || !first?.endpointId || endpointSubmitting.value) return
  endpointSubmitting.value = true
  try {
    const kind = first.poolKind || inheritedPool(instance)
    props.resetApiForm(kind)
    props.apiSettingState.editKind = kind
    props.apiSettingState.form.endpoint_id = first.endpointId
    props.apiSettingState.form.api_instance_id = instance.instanceId
    props.apiSettingState.form.api_instance_name = String(instanceEditForm.api_instance_name || '').trim() || instance.instanceId
    props.apiSettingState.form.api_instance_remark = String(instanceEditForm.api_instance_remark || '').trim()
    props.apiSettingState.form.name = first.raw?.name || first.endpointId
    props.apiSettingState.form.provider = String(instanceEditForm.provider || '').trim()
    props.apiSettingState.form.base_url = String(instanceEditForm.base_url || '').trim()
    props.apiSettingState.form.model = first.model || first.raw?.model || ''
    props.apiSettingState.form.task = first.task || suggestedTask(kind)
    props.apiSettingState.form.api_key = String(instanceEditForm.api_key || '').trim()
    props.apiSettingState.form.tenant_id = first.raw?.tenant_id || first.raw?.tenantId || '*'
    props.apiSettingState.form.enabled = Boolean(first.raw?.enabled ?? first.enabled ?? true)
    props.apiSettingState.form.frozen = Boolean(first.raw?.frozen ?? first.frozen ?? false)
    props.apiSettingState.form.priority = Number(first.raw?.priority ?? 100)
    props.apiSettingState.form.weight = Number(first.raw?.weight ?? 100)
    props.apiSettingState.form.max_concurrency = Number(first.raw?.max_concurrency ?? first.raw?.maxConcurrency ?? 8)
    props.apiSettingState.form.timeout_seconds = Number(first.raw?.timeout_seconds ?? first.raw?.timeoutSeconds ?? 120)
    props.apiSettingState.form.retry_max = Number(first.raw?.retry_max ?? first.raw?.retryMax ?? 2)
    props.apiSettingState.form.circuit_fail_threshold = Number(first.raw?.circuit_fail_threshold ?? first.raw?.circuitFailThreshold ?? 5)
    props.apiSettingState.form.circuit_open_seconds = Number(first.raw?.circuit_open_seconds ?? first.raw?.circuitOpenSeconds ?? 30)
    props.apiSettingState.form.input_per_1k = Number(first.raw?.input_per_1k ?? 0)
    props.apiSettingState.form.output_per_1k = Number(first.raw?.output_per_1k ?? 0)
    await props.upsertApiEndpoint()
    if (!props.apiSettingState.error) {
      showNotice(`已保存 API 实例：${props.apiSettingState.form.api_instance_name}`)
      instanceEditForm.api_key = ''
      instanceEditModalOpen.value = false
    }
  } finally {
    endpointSubmitting.value = false
  }
}

async function deleteEndpointFromConfig(ep) {
  if (!ep?.endpointId) return
  await props.deleteApiEndpoint(ep.poolKind, ep.endpointId)
  if (!props.apiSettingState.error) {
    showNotice(`已删除端点：${ep.endpointId}`)
    endpointEditorModalOpen.value = false
    resetEndpointDraft(selectedInstance.value)
  }
}

async function freezeInstance(instance) {
  if (!instance?.endpoints?.length) return
  closeInstanceMenu()
  const shouldUnfreeze = instance.frozenCount > 0
  for (const ep of instance.endpoints) {
    if (ep.poolKind === 'chat') {
      await props.toggleFreezeChatEndpoint(ep.endpointId, ep.frozen)
      if (props.apiSettingState.error) return
    } else {
      editEndpointInConfig(ep)
      endpointDraft.frozen = !shouldUnfreeze
      await saveEndpointDraft()
      if (props.apiSettingState.error) return
    }
  }
}

function formatLatency(value) {
  const number = Number(value || 0)
  if (!number) return '-'
  return number >= 1000 ? `${(number / 1000).toFixed(1)}s` : `${Math.round(number)}ms`
}

function healthLabel(ep) {
  if (ep?.frozen) return '已冻结'
  const health = ep?.health || {}
  const state = String(health.circuit_state || health.circuitState || '').toUpperCase()
  if (state === 'OPEN') return '熔断中'
  if (state === 'HALF_OPEN') return '探测恢复'
  if (Number(health.consecutive_failures || 0) > 0) return '近期失败'
  return Number(health.success_count || 0) > 0 ? '正常' : '未探测'
}

async function probeEndpoint(ep) {
  if (!ep?.endpointId || ep.poolKind !== 'chat' || probingEndpointId.value) return
  probingEndpointId.value = ep.endpointId
  probeResults[ep.endpointId] = null
  try {
    const result = await props.probeChatApiEndpoint(ep.endpointId)
    probeResults[ep.endpointId] = result
    if (result) {
      const firstToken = result.first_token_ms ? `，首字 ${formatLatency(result.first_token_ms)}` : ''
      const degraded = result.result_status === 'DEGRADED'
        ? '；连接成功，但短探测预算内没有可见正文，建议检查推理预算或模型适配'
        : ''
      showNotice(`端点 ${ep.endpointId} 探测${degraded ? '降级' : '成功'}：总耗时 ${formatLatency(result.total_ms)}${firstToken}${degraded}`)
    }
  } finally {
    probingEndpointId.value = ''
  }
}

async function testInstance(instance) {
  closeInstanceMenu()
  const first = baseEndpointForInstance(instance)
  if (first.poolKind === 'chat') {
    await probeEndpoint(first)
    return
  }
  const w = props.apiSettingState.debugWorkbench
  if (w) {
    w.service = first.poolKind === 'chat' ? 'chat' : 'execution'
    w.method = 'GET'
    w.path = first.poolKind === 'chat' ? '/api/v1/admin/chat/api-pool/health' : '/api/v1/admin/api-pool'
    w.queryText = first.task ? `task=${encodeURIComponent(first.task)}` : ''
    w.bodyText = '{}'
    w.responseStatus = ''
    w.responseText = `已选择测试目标：${instance.instanceName} / ${first.endpointId || '-'}`
  }
  window.alert(`已将 ${instance.instanceName} 的测试目标写入调试工作台。`)
}

watch(
  consumerOptionsForCreatePool,
  (options) => {
    if (!options.length) return
    if (!options.find((item) => item.task === createForm.task)) {
      createForm.task = options[0].task
    }
  },
  { immediate: true },
)

watch(
  () => endpointDraft.pool,
  (pool) => {
    const allowed = consumerEndpointOptions.value.filter((item) => item.pool === pool).map((item) => item.task)
    if (!endpointDraft.task || !allowed.includes(endpointDraft.task)) {
      endpointDraft.task = suggestedTask(pool)
    }
  },
)
</script>

<template>
  <section class="api-workbench">
    <p v-if="localNotice" class="api-local-notice">{{ localNotice }}</p>
    <div class="api-summary-grid">
      <div class="api-summary-card">
        <span class="api-summary-label">实例总数</span>
        <strong class="api-summary-value">{{ summary.total }}</strong>
      </div>
      <div class="api-summary-card tone-success">
        <span class="api-summary-label">健康实例</span>
        <strong class="api-summary-value">{{ summary.healthy }}</strong>
      </div>
      <div class="api-summary-card tone-warning">
        <span class="api-summary-label">预警实例</span>
        <strong class="api-summary-value">{{ summary.warning }}</strong>
      </div>
      <div class="api-summary-card tone-danger">
        <span class="api-summary-label">风险实例</span>
        <strong class="api-summary-value">{{ summary.risk }}</strong>
      </div>
      <div class="api-summary-card tone-accent">
        <span class="api-summary-label">挂载消费点</span>
        <strong class="api-summary-value">{{ summary.mounted }}</strong>
      </div>
    </div>

    <div class="api-console-shell">
      <div class="api-endpoint-bank">
        <div class="api-panel-heading">
          <div>
            <h3>系统消费端点目录</h3>
            <p>系统内所有会消费 API Key 的功能端点统一在这里收集，配置时从目录选择。</p>
          </div>
          <div class="api-panel-meta">{{ consumerCatalogRows.length }} 个消费点</div>
        </div>
        <div class="api-table-wrap compact">
          <table>
            <thead>
              <tr>
                <th>消费点</th>
                <th>池</th>
                <th>能力</th>
                <th>已挂载</th>
                <th>可用</th>
                <th>模型</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in consumerCatalogRows" :key="`${item.pool}:${item.task}`">
                <td>
                  <strong>{{ item.label }}</strong>
                  <div class="api-muted">{{ item.task }}</div>
                </td>
                <td>{{ poolLabel(item.pool) }}</td>
                <td>{{ item.capability || '-' }}</td>
                <td>{{ item.mountedCount }}</td>
                <td>{{ item.enabledCount }}</td>
                <td>{{ item.models.slice(0, 3).join(' / ') || '-' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="api-toolbar-panel">
        <div class="api-panel-heading">
          <div>
            <h3>实例筛选</h3>
            <p>先定位模型实例，再进入挂载端点与治理操作。</p>
          </div>
          <div class="api-inline-actions">
            <button class="api-btn primary" @click="openCreateModal(filters.pool !== 'all' ? filters.pool : 'main')">新增 API 实例</button>
          </div>
        </div>
        <div class="api-toolbar-grid">
        <label class="api-toolbar-field api-toolbar-search">
          <span>搜索</span>
          <input v-model="filters.q" placeholder="模型 / endpoint / fingerprint / provider / task" />
        </label>
        <label class="api-toolbar-field">
          <span>池</span>
          <select v-model="filters.pool">
            <option value="all">全部</option>
            <option value="main">Main</option>
            <option value="aux">Aux</option>
            <option value="chat">Chat</option>
          </select>
        </label>
        <label class="api-toolbar-field">
          <span>状态</span>
          <select v-model="filters.status">
            <option value="all">全部</option>
            <option value="healthy">健康</option>
            <option value="warning">预警</option>
            <option value="risk">风险</option>
            <option value="expired">已过期</option>
            <option value="idle">空闲</option>
          </select>
        </label>
        <label class="api-toolbar-field">
          <span>挂载</span>
          <select v-model="filters.mount">
            <option value="all">全部</option>
            <option value="single">单挂载</option>
            <option value="multi">多挂载</option>
            <option value="empty">空挂载</option>
          </select>
        </label>
        <label class="api-toolbar-field">
          <span>消费点</span>
          <select v-model="filters.task">
            <option value="">全部</option>
            <option v-for="item in consumerFilterOptions" :key="item.task" :value="item.task">
              {{ consumerLabel(item.task) }}
            </option>
          </select>
        </label>
        </div>
      </div>

      <div class="api-instance-section">
        <div class="api-panel-heading">
          <div>
            <h3>API 实例</h3>
            <p>按 API Key 实例聚合，最新创建的实例排在最前；每页固定展示 {{ instancePageSize }} 条。</p>
          </div>
          <div class="api-panel-meta">{{ filteredInstances.length }} 个结果 · 第 {{ currentInstancePage }} / {{ instancePageTotal }} 页</div>
        </div>

        <div class="api-table-wrap instances">
          <table class="api-instance-table">
            <thead>
              <tr>
                <th>序号</th>
                <th>名称</th>
                <th>添加时间</th>
                <th>平台</th>
                <th>消费端点数量</th>
                <th>状态</th>
                <th class="api-action-col">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(item, index) in pagedInstances"
                :key="item.instanceKey"
                v-interactive-row="{ activate: () => { selectedInstanceKey = item.instanceKey }, label: `选择 API 实例 ${item.instanceName}`, current: selectedInstance?.instanceKey === item.instanceKey }"
                :class="{ active: selectedInstance && selectedInstance.instanceKey === item.instanceKey }"
                @click="selectedInstanceKey = item.instanceKey"
              >
                <td>{{ instancePageStart + index }}</td>
                <td>
                  <strong>{{ item.instanceName }}</strong>
                  <div class="api-muted">{{ item.instanceId }} · {{ fingerprintLabel(item.fingerprint) }}</div>
                </td>
                <td>{{ formatDateTime(instanceCreatedAt(item)) }}</td>
                <td>
                  <div>{{ item.providers.join(' / ') || '-' }}</div>
                  <div class="api-muted">{{ hostLabel(item.baseUrls[0] || '-') }}</div>
                </td>
                <td>
                  <strong>{{ item.mountedCount }}</strong>
                  <div class="api-muted">{{ item.tasks.length ? item.tasks.map((x) => consumerLabel(x)).join(' / ') : 'default' }}</div>
                </td>
                <td>
                  <span class="api-status-pill" :class="`pill-${item.statusTone}`">{{ item.statusLabel }}</span>
                </td>
                <td class="api-action-cell" @click.stop>
                  <button class="api-icon-btn" @click="toggleInstanceMenu(item.instanceKey, $event)">...</button>
                </td>
              </tr>
              <tr v-if="!pagedInstances.length">
                <td colspan="7" class="api-empty-state" role="status" aria-live="polite" aria-atomic="true">当前筛选条件下没有实例。</td>
              </tr>
            </tbody>
          </table>
        </div>
        <UiPager
          :page="currentInstancePage"
          :page-count="instancePageTotal"
          :total-items="filteredInstances.length"
          :start-item="instancePageStart"
          :end-item="instancePageEnd"
          show-edges
          aria-label="API 实例分页"
          @page="setInstancePage"
        />
      </div>

      <Teleport to="body">
        <div v-if="openMenuInstance" class="api-global-menu-layer" @click="closeInstanceMenu">
          <div
            class="api-global-menu"
            :style="menuStyle"
            @click.stop
          >
            <button @click="openEndpointConfigModal(openMenuInstance)">配置端点</button>
            <button @click="openInstanceEditModal(openMenuInstance)">编辑实例</button>
            <button @click="openEndpointConfigModal(openMenuInstance)">编辑端点</button>
            <button @click="testInstance(openMenuInstance)">测试</button>
            <button @click="freezeInstance(openMenuInstance)">{{ openMenuInstance.frozenCount > 0 ? '解冻' : '冻结' }}</button>
            <button class="danger-text" @click="closeInstanceMenu(); deleteInstance(openMenuInstance)">删除</button>
          </div>
        </div>
      </Teleport>

      <div class="api-endpoint-bank">
        <div class="api-panel-heading">
          <div>
            <h3>端点明细</h3>
            <p>配置层视图，用于逐条核对与兜底操作。</p>
          </div>
          <div class="api-panel-meta">仅数据库配置</div>
        </div>

        <div class="api-bank-tabs">
          <button class="api-bank-tab" :class="{ active: detailPoolTab === 'main' }" @click="detailPoolTab = 'main'">Main</button>
          <button class="api-bank-tab" :class="{ active: detailPoolTab === 'aux' }" @click="detailPoolTab = 'aux'">Aux</button>
          <button class="api-bank-tab" :class="{ active: detailPoolTab === 'chat' }" @click="detailPoolTab = 'chat'">Chat</button>
        </div>

        <div class="api-table-wrap short">
          <table>
            <thead>
              <tr>
                <th>endpoint_id</th>
                <th>task</th>
                <th>model</th>
                <th>fingerprint</th>
                <th>enabled</th>
                <th v-if="detailPoolTab === 'chat'">健康 / 延迟</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in activePoolRows" :key="item.endpoint_id || item.endpointId">
                <td>{{ item.endpoint_id || item.endpointId }}</td>
                <td>{{ consumerLabel(item.task, detailPoolTab) }}</td>
                <td>{{ item.model }}</td>
                <td>{{ fingerprintLabel(item.key_fingerprint) }}</td>
                <td>{{ item.enabled }}</td>
                <td v-if="detailPoolTab === 'chat'">
                  <div class="api-endpoint-health">
                    <span>{{ healthLabel(item) }}</span>
                    <small>EMA {{ formatLatency(item.health?.latency_ema_ms) }}</small>
                    <small v-if="probeResults[props.endpointIdOf(item)]">
                      最近探测 {{ probeResults[props.endpointIdOf(item)]?.result_status === 'DEGRADED' ? '降级' : '通过' }}
                      · {{ formatLatency(probeResults[props.endpointIdOf(item)]?.total_ms) }}
                    </small>
                  </div>
                </td>
                <td>
                  <div class="api-inline-actions">
                    <button class="api-btn ghost" @click="openEndpointEditor(item, detailPoolTab)">编辑</button>
                    <button
                      v-if="detailPoolTab === 'chat'"
                      class="api-btn ghost"
                      :disabled="Boolean(probingEndpointId)"
                      @click="probeEndpoint(item)"
                    >{{ probingEndpointId === props.endpointIdOf(item) ? '探测中…' : '实测' }}</button>
                    <button
                      v-if="detailPoolTab !== 'chat'"
                      class="api-btn ghost"
                      @click="setDefaultApiEndpoint(detailPoolTab, props.endpointIdOf(item))"
                    >默认</button>
                    <button
                      v-else
                      class="api-btn ghost"
                      @click="setDefaultApiEndpoint('chat', props.endpointIdOf(item), item.task || 'chat_llm')"
                    >默认</button>
                    <button
                      v-if="detailPoolTab === 'chat'"
                      class="api-btn ghost"
                      @click="toggleFreezeChatEndpoint(props.endpointIdOf(item), item.frozen)"
                    >{{ item.frozen ? '解冻' : '冻结' }}</button>
                    <button
                      v-else
                      class="api-btn ghost danger-text"
                      @click="deleteApiEndpoint(detailPoolTab, props.endpointIdOf(item))"
                    >删除</button>
                  </div>
                </td>
              </tr>
              <tr v-if="!activePoolRows.length">
                <td :colspan="detailPoolTab === 'chat' ? 7 : 6" class="api-empty-state" role="status" aria-live="polite" aria-atomic="true">当前池暂无端点。</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <Teleport to="body">
      <div v-if="detailModalOpen && selectedInstance" v-modal-focus="{ close: closeDetailModal, label: 'API 实例详情' }" class="api-modal-mask" @pointerdown.self="closeDetailModal">
        <div class="api-modal-shell large">
          <div class="api-modal-head">
            <div>
              <h3>{{ selectedInstance.model }}</h3>
              <p>{{ fingerprintLabel(selectedInstance.fingerprint) }}</p>
            </div>
            <div class="api-inline-actions">
              <span class="api-status-pill large" :class="`pill-${selectedInstance.statusTone}`">{{ selectedInstance.statusLabel }}</span>
              <button class="api-btn ghost" @click="openInstanceEditModal(selectedInstance)">编辑实例</button>
              <button class="api-btn ghost danger-text" @click="deleteInstance(selectedInstance)">删除实例</button>
              <button class="api-btn ghost" @click="closeDetailModal">关闭</button>
            </div>
          </div>

          <div class="api-modal-tabs">
            <button class="api-bank-tab" :class="{ active: detailTab === 'overview' }" @click="detailTab = 'overview'">概览</button>
            <button class="api-bank-tab" :class="{ active: detailTab === 'mounts' }" @click="detailTab = 'mounts'">挂载端点</button>
          </div>

          <div v-if="detailTab === 'overview'" class="api-modal-body">
            <div class="api-detail-grid">
              <div class="api-detail-topline">
                <div class="api-metric-card">
                  <span>挂载</span>
                  <strong>{{ selectedInstance.mountedCount }}</strong>
                </div>
                <div class="api-metric-card">
                  <span>启用</span>
                  <strong>{{ selectedInstance.enabledCount }}</strong>
                </div>
                <div class="api-metric-card">
                  <span>冻结</span>
                  <strong>{{ selectedInstance.frozenCount }}</strong>
                </div>
                <div class="api-metric-card">
                  <span>预警</span>
                  <strong>{{ selectedInstance.warningCount }}</strong>
                </div>
              </div>

              <div class="api-detail-info-grid">
                <div class="api-detail-field">
                  <span class="api-field-label">实例 ID</span>
                  <span class="api-field-value">{{ selectedInstance.instanceId || '-' }}</span>
                </div>
                <div class="api-detail-field">
                  <span class="api-field-label">备注</span>
                  <span class="api-field-value">{{ selectedInstance.instanceRemark || '-' }}</span>
                </div>
                <div class="api-detail-field">
                  <span class="api-field-label">Provider</span>
                  <span class="api-field-value">{{ selectedInstance.providers.join(' / ') || '-' }}</span>
                </div>
                <div class="api-detail-field">
                  <span class="api-field-label">池</span>
                  <span class="api-field-value">{{ selectedInstance.pools.join(' / ') || '-' }}</span>
                </div>
                <div class="api-detail-field">
                  <span class="api-field-label">消费点</span>
                  <span class="api-field-value">{{ selectedInstance.tasks.map((x) => consumerLabel(x)).join(' / ') || 'default' }}</span>
                </div>
                <div class="api-detail-field">
                  <span class="api-field-label">主机</span>
                  <span class="api-field-value truncate" :title="selectedInstance.baseUrls.join('\n')">{{ hostLabel(selectedInstance.baseUrls[0] || '-') }}</span>
                </div>
              </div>
            </div>
          </div>

          <div v-else class="api-modal-body">
            <div class="api-mount-head">
              <div class="api-mount-title">挂载端点</div>
              <div class="api-mount-sub">端点配置与默认绑定、冻结、删除操作统一在这里处理。</div>
              <button class="api-btn primary mini" @click="openAddEndpointForInstance(selectedInstance)">添加功能端点</button>
            </div>
            <div class="api-mount-grid">
              <div v-for="ep in selectedInstance.endpoints" :key="ep.mountedKey" class="api-mount-card">
                <div class="api-mount-main">
                  <span class="api-pool-chip">{{ poolLabel(ep.poolKind) }}</span>
                  <strong>{{ ep.endpointId }}</strong>
                  <span class="api-mount-task">{{ consumerLabel(ep.task, ep.poolKind) }}</span>
                  <span class="api-mount-task">{{ ep.model || '-' }}</span>
                </div>
                <div class="api-mount-subline">
                  <span>{{ ep.provider || '-' }}</span>
                  <span>{{ hostLabel(ep.baseUrl) }}</span>
                  <span>{{ ep.rotationState }}</span>
                  <span>{{ ep.expiresAt ? formatDateTime(ep.expiresAt) : '无到期时间' }}</span>
                  <span v-if="ep.poolKind === 'chat'">{{ healthLabel(ep) }} · EMA {{ formatLatency(ep.health?.latency_ema_ms) }}</span>
                </div>
                <div class="api-inline-actions">
                  <button class="api-btn ghost mini" @click.stop="openEndpointDraftEditor(ep)">编辑</button>
                  <button
                    v-if="ep.poolKind === 'chat'"
                    class="api-btn ghost mini"
                    :disabled="Boolean(probingEndpointId)"
                    @click.stop="probeEndpoint(ep)"
                  >{{ probingEndpointId === ep.endpointId ? '探测中…' : '实测' }}</button>
                  <button
                    v-if="ep.poolKind !== 'chat'"
                    class="api-btn ghost mini"
                    @click.stop="setDefaultApiEndpoint(ep.poolKind, ep.endpointId)"
                  >设默认</button>
                  <button
                    v-else
                    class="api-btn ghost mini"
                    @click.stop="setDefaultApiEndpoint('chat', ep.endpointId, ep.task || 'chat_llm')"
                  >设默认</button>
                  <button
                    v-if="ep.poolKind === 'chat'"
                    class="api-btn ghost mini"
                    @click.stop="toggleFreezeChatEndpoint(ep.endpointId, ep.frozen)"
                  >{{ ep.frozen ? '解冻' : '冻结' }}</button>
                  <button
                    v-else
                    class="api-btn ghost mini danger-text"
                    @click.stop="deleteApiEndpoint(ep.poolKind, ep.endpointId)"
                  >删除</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="endpointConfigModalOpen && selectedInstance" v-modal-focus="{ close: closeEndpointConfigModal, label: '端点配置' }" class="api-modal-mask" @pointerdown.self="closeEndpointConfigModal">
        <div class="api-modal-shell endpoint-config">
          <div class="api-modal-head">
            <div>
              <h3>配置端点：{{ selectedInstance.instanceName }}</h3>
              <p>{{ selectedInstance.instanceId }} · {{ fingerprintLabel(selectedInstance.fingerprint) }}</p>
            </div>
            <div class="api-inline-actions">
              <button class="api-btn ghost" @click="openAddEndpointForInstance(selectedInstance)">新增端点</button>
              <button class="api-btn ghost" @click="closeEndpointConfigModal">关闭</button>
            </div>
          </div>
          <div class="api-modal-body endpoint-config-body">
            <div class="api-endpoint-list-panel">
              <div class="api-section-title">实例信息</div>
              <div class="api-detail-info-grid compact">
                <div class="api-detail-field">
                  <span class="api-field-label">实例 ID</span>
                  <span class="api-field-value">{{ selectedInstance.instanceId || '-' }}</span>
                </div>
                <div class="api-detail-field">
                  <span class="api-field-label">Key 指纹</span>
                  <span class="api-field-value">{{ fingerprintLabel(selectedInstance.fingerprint) }}</span>
                </div>
                <div class="api-detail-field">
                  <span class="api-field-label">Provider</span>
                  <span class="api-field-value">{{ inheritedProvider(selectedInstance) }}</span>
                </div>
                <div class="api-detail-field">
                  <span class="api-field-label">Base URL</span>
                  <span class="api-field-value">{{ inheritedBaseUrl(selectedInstance) || '-' }}</span>
                </div>
              </div>
            </div>

            <div class="api-endpoint-list-panel">
              <div class="api-panel-heading compact">
                <div>
                  <div class="api-section-title">端点列表</div>
                  <div class="api-muted">端点继承该实例的 API Key 和 Base URL，只配置业务挂载与调度参数。</div>
                </div>
                <button class="api-btn primary mini" @click="openAddEndpointForInstance(selectedInstance)">新增端点</button>
              </div>
              <div class="api-endpoint-config-list">
                <div
                  v-for="ep in selectedInstance.endpoints"
                  :key="ep.mountedKey"
                  class="api-endpoint-config-row"
                >
                  <div>
                    <strong>{{ ep.model || '-' }}</strong>
                    <div class="api-muted">{{ consumerLabel(ep.task, ep.poolKind) }}</div>
                    <div class="api-muted">{{ poolLabel(ep.poolKind) }} · {{ ep.endpointId }}</div>
                  </div>
                  <div class="api-endpoint-config-meta">
                    <span class="api-status-pill" :class="ep.frozen ? 'pill-danger' : ep.enabled ? 'pill-success' : 'pill-neutral'">
                      {{ ep.frozen ? '冻结' : ep.enabled ? '启用' : '禁用' }}
                    </span>
                    <span>权重 {{ ep.raw?.weight ?? 100 }}</span>
                    <button class="api-btn ghost mini" @click="openEndpointDraftEditor(ep)">编辑</button>
                  </div>
                </div>
                <div v-if="!selectedInstance.endpoints.length" class="api-empty-state">暂无端点。</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="endpointEditorModalOpen && selectedInstance" v-modal-focus="{ close: closeEndpointEditorModal, label: '端点编辑' }" class="api-modal-mask stacked" @pointerdown.self="closeEndpointEditorModal">
        <div class="api-modal-shell endpoint-editor">
          <div class="api-modal-head">
            <div>
              <h3>{{ endpointDraft.mode === 'edit' ? '编辑端点' : '新增端点' }}</h3>
              <p>{{ selectedInstance.instanceName }} · 继承 {{ fingerprintLabel(selectedInstance.fingerprint) }}</p>
            </div>
            <div class="api-inline-actions">
              <button class="api-btn ghost" @click="closeEndpointEditorModal">关闭</button>
            </div>
          </div>
          <div class="api-modal-body">
            <div class="api-inherit-strip">
              <div>
                <span class="api-field-label">继承 Provider</span>
                <strong>{{ inheritedProvider(selectedInstance) }}</strong>
              </div>
              <div>
                <span class="api-field-label">继承 Base URL</span>
                <strong>{{ inheritedBaseUrl(selectedInstance) || '-' }}</strong>
              </div>
              <div>
                <span class="api-field-label">继承 API Key</span>
                <strong>{{ fingerprintLabel(selectedInstance.fingerprint) }}</strong>
              </div>
            </div>
            <p v-if="!selectedInstance.fingerprint" class="api-inline-warning">
              当前实例没有可继承的 API Key，请先编辑实例并补录 Key。
            </p>

            <div class="api-form-grid cols-3">
              <label class="api-form-field">
                <span class="api-label-row"><span>Pool</span><button type="button" class="api-help" :title="helpTitle('pool')">?</button></span>
                <select v-model="endpointDraft.pool" :disabled="endpointDraft.mode === 'edit'">
                  <option value="main">Main</option>
                  <option value="aux">Aux</option>
                  <option value="chat">Chat</option>
                </select>
              </label>
              <label class="api-form-field">
                <span class="api-label-row"><span>消费端点</span><button type="button" class="api-help" :title="helpTitle('task')">?</button></span>
                <select v-model="endpointDraft.task">
                  <option v-for="item in draftConsumerOptions" :key="`${item.pool}:${item.task}`" :value="item.task">
                    {{ item.label }} ({{ item.task }})
                  </option>
                </select>
              </label>
              <label class="api-form-field">
                <span class="api-label-row"><span>启用状态</span><button type="button" class="api-help" :title="helpTitle('enabled')">?</button></span>
                <select v-model="endpointDraft.enabled">
                  <option :value="true">启用</option>
                  <option :value="false">禁用</option>
                </select>
              </label>
              <label class="api-form-field wide">
                <span>模型</span>
                <input v-model="endpointDraft.model" placeholder="模型 ID" />
              </label>
              <label class="api-form-field">
                <span>端点名称</span>
                <input v-model="endpointDraft.name" placeholder="可空" />
              </label>
              <label class="api-form-field">
                <span class="api-label-row"><span>租户</span><button type="button" class="api-help" :title="helpTitle('tenant')">?</button></span>
                <input v-model="endpointDraft.tenant_id" placeholder="*" />
              </label>
              <label class="api-form-field">
                <span class="api-label-row"><span>优先级</span><button type="button" class="api-help" :title="helpTitle('priority')">?</button></span>
                <input v-model.number="endpointDraft.priority" type="number" min="1" />
              </label>
              <label class="api-form-field">
                <span class="api-label-row"><span>权重</span><button type="button" class="api-help" :title="helpTitle('weight')">?</button></span>
                <input v-model.number="endpointDraft.weight" type="number" min="1" />
              </label>
              <label class="api-form-field">
                <span class="api-label-row"><span>最大并发</span><button type="button" class="api-help" :title="helpTitle('concurrency')">?</button></span>
                <input v-model.number="endpointDraft.max_concurrency" type="number" min="1" />
              </label>
              <label class="api-form-field">
                <span class="api-label-row"><span>超时秒数</span><button type="button" class="api-help" :title="helpTitle('timeout')">?</button></span>
                <input v-model.number="endpointDraft.timeout_seconds" type="number" min="5" />
              </label>
              <label class="api-form-field">
                <span class="api-label-row"><span>重试次数</span><button type="button" class="api-help" :title="helpTitle('retry')">?</button></span>
                <input v-model.number="endpointDraft.retry_max" type="number" min="0" />
              </label>
              <label class="api-form-field">
                <span>冻结</span>
                <select v-model="endpointDraft.frozen">
                  <option :value="false">否</option>
                  <option :value="true">是</option>
                </select>
              </label>
              <label class="api-form-field">
                <span class="api-label-row"><span>熔断阈值</span><button type="button" class="api-help" :title="helpTitle('circuit')">?</button></span>
                <input v-model.number="endpointDraft.circuit_fail_threshold" type="number" min="1" />
              </label>
              <label class="api-form-field">
                <span class="api-label-row"><span>熔断恢复秒数</span><button type="button" class="api-help" :title="helpTitle('circuit')">?</button></span>
                <input v-model.number="endpointDraft.circuit_open_seconds" type="number" min="5" />
              </label>
              <label class="api-form-field">
                <span class="api-label-row"><span>输入价格 / 1k</span><button type="button" class="api-help" :title="helpTitle('price')">?</button></span>
                <input v-model.number="endpointDraft.input_per_1k" type="number" min="0" step="0.000001" />
              </label>
              <label class="api-form-field">
                <span class="api-label-row"><span>输出价格 / 1k</span><button type="button" class="api-help" :title="helpTitle('price')">?</button></span>
                <input v-model.number="endpointDraft.output_per_1k" type="number" min="0" step="0.000001" />
              </label>
            </div>
            <div class="api-create-footer">
              <div class="api-create-hint">
                <span class="api-field-label">endpoint_id</span>
                <strong class="api-create-id">{{ endpointDraftId }}</strong>
              </div>
              <div class="api-inline-actions">
                <button
                  v-if="endpointDraft.mode === 'edit'"
                  class="api-btn ghost danger-text"
                  @click="deleteEndpointFromConfig({ poolKind: endpointDraft.pool, endpointId: endpointDraft.endpoint_id })"
                >删除端点</button>
                <button class="api-btn primary" :disabled="!endpointDraftReady || props.apiSettingState.loading || endpointSubmitting" @click="saveEndpointDraft">
                  {{ endpointSubmitting ? '保存中...' : endpointDraft.mode === 'edit' ? '保存端点' : '添加端点' }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="instanceEditModalOpen && selectedInstance" v-modal-focus="{ close: closeInstanceEditModal, label: '编辑 API 实例' }" class="api-modal-mask" @pointerdown.self="closeInstanceEditModal">
        <div class="api-modal-shell create">
          <div class="api-modal-head">
            <div>
              <h3>编辑 API 实例</h3>
              <p>{{ selectedInstance.instanceId }} · {{ fingerprintLabel(selectedInstance.fingerprint) }}</p>
            </div>
            <div class="api-inline-actions">
              <button class="api-btn ghost" @click="closeInstanceEditModal">关闭</button>
            </div>
          </div>
          <div class="api-modal-body">
            <div class="api-create-layout">
              <div class="api-create-section">
                <div class="api-section-title">实例级配置</div>
                <div class="api-form-grid cols-3">
                  <label class="api-form-field">
                    <span>实例名称</span>
                    <input v-model="instanceEditForm.api_instance_name" placeholder="显示名称" />
                  </label>
                  <label class="api-form-field">
                    <span>备注</span>
                    <input v-model="instanceEditForm.api_instance_remark" placeholder="用途、账号归属或额度说明" />
                  </label>
                  <label class="api-form-field">
                    <span class="api-label-row"><span>Provider</span><button type="button" class="api-help" :title="helpTitle('provider')">?</button></span>
                    <input v-model="instanceEditForm.provider" placeholder="openai_compatible" />
                  </label>
                  <label class="api-form-field wide">
                    <span class="api-label-row"><span>Base URL</span><button type="button" class="api-help" :title="helpTitle('baseUrl')">?</button></span>
                    <input v-model="instanceEditForm.base_url" placeholder="https://api.xxx.com/v1" />
                  </label>
                  <label class="api-form-field wide">
                    <span class="api-label-row"><span>API Key</span><button type="button" class="api-help" :title="helpTitle('apiKey')">?</button></span>
                    <input v-model="instanceEditForm.api_key" type="password" placeholder="留空表示沿用当前实例 Key；NO_KEY 时必须填写" />
                  </label>
                </div>
              </div>
              <div class="api-create-footer">
                <div class="api-create-hint">
                  <span class="api-field-label">保存后会同步到同实例下所有端点</span>
                  <strong class="api-create-id">{{ selectedInstance.endpoints.length }} 个端点</strong>
                </div>
                <div class="api-inline-actions">
                  <button class="api-btn ghost" @click="closeInstanceEditModal">取消</button>
                  <button class="api-btn primary" :disabled="!instanceEditReady || endpointSubmitting" @click="submitInstanceEdit">
                    {{ endpointSubmitting ? '保存中...' : '保存实例' }}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="createModalOpen" v-modal-focus="{ close: closeCreateModal, label: '创建 API 实例' }" class="api-modal-mask" @pointerdown.self="closeCreateModal">
        <div class="api-modal-shell create">
          <div class="api-modal-head">
            <div>
              <h3>新增 API 实例</h3>
              <p>先定义一个 API Key 实例，再生成首个功能端点。后续可在详情中继续补充端点。</p>
            </div>
            <div class="api-inline-actions">
              <button class="api-btn ghost" @click="closeCreateModal">关闭</button>
            </div>
          </div>
          <div class="api-modal-body">
            <div class="api-create-layout">
              <div class="api-create-section">
                <div class="api-section-title">实例基础</div>
                <div class="api-form-grid cols-3">
                  <label class="api-form-field">
                    <span class="api-label-row"><span>Pool</span><button type="button" class="api-help" :title="helpTitle('pool')">?</button></span>
                    <select v-model="createForm.pool">
                      <option value="main">Main</option>
                      <option value="aux">Aux</option>
                      <option value="chat">Chat</option>
                    </select>
                  </label>
                  <label class="api-form-field">
                    <span class="api-label-row"><span>实例 ID</span><button type="button" class="api-help" :title="helpTitle('apiInstanceId')">?</button></span>
                    <input v-model="createForm.api_instance_id" placeholder="可空；按名称自动生成" />
                  </label>
                  <label class="api-form-field">
                    <span>实例名称</span>
                    <input v-model="createForm.name" placeholder="可空；默认自动生成" />
                  </label>
                  <label class="api-form-field">
                    <span>备注</span>
                    <input v-model="createForm.remark" placeholder="用途、账号归属或额度说明" />
                  </label>
                  <label class="api-form-field">
                    <span class="api-label-row"><span>Provider</span><button type="button" class="api-help" :title="helpTitle('provider')">?</button></span>
                    <input v-model="createForm.provider" placeholder="openai_compatible" />
                  </label>
                  <label class="api-form-field wide">
                    <span class="api-label-row"><span>Base URL</span><button type="button" class="api-help" :title="helpTitle('baseUrl')">?</button></span>
                    <input v-model="createForm.base_url" placeholder="https://api.xxx.com/v1" />
                  </label>
                  <label class="api-form-field wide">
                    <span class="api-label-row"><span>API Key</span><button type="button" class="api-help" :title="helpTitle('apiKey')">?</button></span>
                    <input v-model="createForm.api_key" type="password" placeholder="输入实例对应的 API Key" />
                  </label>
                </div>
              </div>

              <div class="api-create-section">
                <div class="api-section-title">首个挂载端点</div>
                <div class="api-form-grid cols-4">
                  <label class="api-form-field wide">
                    <span>模型名称</span>
                    <input v-model="createForm.model" placeholder="如 deepseek-ai/DeepSeek-V3.2" />
                  </label>
                  <label class="api-form-field">
                    <span class="api-label-row"><span>消费点 Task</span><button type="button" class="api-help" :title="helpTitle('task')">?</button></span>
                    <select v-model="createForm.task">
                      <option
                        v-for="item in consumerOptionsForCreatePool"
                        :key="`${item.pool}:${item.task}`"
                        :value="item.task"
                      >
                        {{ item.label }} ({{ item.task }})
                      </option>
                    </select>
                  </label>
                  <label class="api-form-field">
                    <span class="api-label-row"><span>租户范围</span><button type="button" class="api-help" :title="helpTitle('tenant')">?</button></span>
                    <input v-model="createForm.tenant_id" placeholder="*" />
                  </label>
                  <label class="api-form-field">
                    <span class="api-label-row"><span>优先级</span><button type="button" class="api-help" :title="helpTitle('priority')">?</button></span>
                    <input v-model.number="createForm.priority" type="number" />
                  </label>
                  <label class="api-form-field">
                    <span class="api-label-row"><span>权重</span><button type="button" class="api-help" :title="helpTitle('weight')">?</button></span>
                    <input v-model.number="createForm.weight" type="number" />
                  </label>
                  <label class="api-form-field">
                    <span class="api-label-row"><span>最大并发</span><button type="button" class="api-help" :title="helpTitle('concurrency')">?</button></span>
                    <input v-model.number="createForm.max_concurrency" type="number" min="1" />
                  </label>
                  <label class="api-form-field">
                    <span class="api-label-row"><span>超时秒数</span><button type="button" class="api-help" :title="helpTitle('timeout')">?</button></span>
                    <input v-model.number="createForm.timeout_seconds" type="number" min="1" />
                  </label>
                  <label class="api-form-field">
                    <span class="api-label-row"><span>重试次数</span><button type="button" class="api-help" :title="helpTitle('retry')">?</button></span>
                    <input v-model.number="createForm.retry_max" type="number" min="0" />
                  </label>
                  <label class="api-form-field">
                    <span>启用状态</span>
                    <select v-model="createForm.enabled">
                      <option :value="true">启用</option>
                      <option :value="false">禁用</option>
                    </select>
                  </label>
                </div>
              </div>

              <div class="api-create-section">
                <div class="api-section-title">线路与计费</div>
                <div class="api-form-grid cols-4">
                  <label class="api-form-field">
                    <span class="api-label-row"><span>熔断阈值</span><button type="button" class="api-help" :title="helpTitle('circuit')">?</button></span>
                    <input v-model.number="createForm.circuit_fail_threshold" type="number" min="1" />
                  </label>
                  <label class="api-form-field">
                    <span class="api-label-row"><span>熔断恢复秒数</span><button type="button" class="api-help" :title="helpTitle('circuit')">?</button></span>
                    <input v-model.number="createForm.circuit_open_seconds" type="number" min="1" />
                  </label>
                  <label class="api-form-field">
                    <span class="api-label-row"><span>输入价格 / 1k</span><button type="button" class="api-help" :title="helpTitle('price')">?</button></span>
                    <input v-model.number="createForm.input_per_1k" type="number" min="0" step="0.000001" />
                  </label>
                  <label class="api-form-field">
                    <span class="api-label-row"><span>输出价格 / 1k</span><button type="button" class="api-help" :title="helpTitle('price')">?</button></span>
                    <input v-model.number="createForm.output_per_1k" type="number" min="0" step="0.000001" />
                  </label>
                </div>
              </div>

              <div class="api-create-footer">
                <div class="api-create-hint">
                  <span class="api-field-label">将要创建的 endpoint_id</span>
                  <strong class="api-create-id">{{ createEndpointId }}</strong>
                </div>
                <div class="api-inline-actions">
                  <button class="api-btn ghost" @click="resetCreateForm(createForm.pool)">重置</button>
                  <button class="api-btn primary" :disabled="!createFormReady || createSubmitting" @click="submitCreateInstance">
                    {{ createSubmitting ? '创建中...' : '创建实例' }}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="editorModalOpen" v-modal-focus="{ close: closeEditorModal, label: 'API 端点编辑器' }" class="api-modal-mask" @pointerdown.self="closeEditorModal">
        <div class="api-modal-shell editor">
          <div class="api-modal-head">
            <div>
              <h3>{{ props.apiSettingState.form.endpoint_id ? '编辑 API 端点' : '新增 API 实例' }}</h3>
              <p>实例新增与端点编辑共用同一套配置表单。</p>
            </div>
            <div class="api-inline-actions">
              <button class="api-btn ghost" @click="closeEditorModal">关闭</button>
            </div>
          </div>
          <div class="api-modal-body">
            <ApiEndpointEditorCard
              :api-setting-state="props.apiSettingState"
              :reset-api-form="props.resetApiForm"
              :upsert-api-endpoint="props.upsertApiEndpoint"
              :delete-api-endpoint="props.deleteApiEndpoint"
            />
          </div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.api-workbench {
  display: grid;
  gap: 20px;
}

.api-local-notice {
  margin: 0;
  padding: 10px 12px;
  border: 1px solid #bfe1cc;
  border-radius: 12px;
  background: #f0fbf4;
  color: #24693f;
  font-size: 13px;
  font-weight: 700;
}

.api-summary-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 14px;
}

.api-summary-card {
  min-height: 106px;
  border: 1px solid #dbe6f4;
  border-radius: 22px;
  padding: 18px 18px 16px;
  background: linear-gradient(180deg, #fcfdff, #f4f7fb);
  box-shadow: 0 10px 24px rgba(28, 57, 94, 0.06);
}

.api-summary-card.tone-success {
  background: linear-gradient(180deg, #fcfffd, #eef8f2);
}

.api-summary-card.tone-warning {
  background: linear-gradient(180deg, #fffefb, #fff5e8);
}

.api-summary-card.tone-danger {
  background: linear-gradient(180deg, #fffafb, #fff0f1);
}

.api-summary-card.tone-accent {
  background: linear-gradient(180deg, #fafcff, #edf4ff);
}

.api-summary-label {
  display: block;
  color: #607894;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: .04em;
}

.api-summary-value {
  display: block;
  margin-top: 18px;
  color: #1f3f74;
  font-size: 34px;
  line-height: 1;
}

.api-console-shell {
  border: 1px solid #dbe6f4;
  border-radius: 28px;
  background:
    radial-gradient(circle at top right, rgba(107, 157, 240, 0.10), transparent 28%),
    linear-gradient(180deg, #fbfdff 0%, #f5f8fc 100%);
  padding: 20px;
  display: grid;
  gap: 18px;
}

.api-toolbar-panel,
.api-instance-section,
.api-endpoint-bank {
  border: 1px solid #dbe6f4;
  border-radius: 22px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 10px 24px rgba(41, 72, 116, 0.05);
  padding: 18px;
  overflow: visible;
}

.api-panel-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.api-panel-heading h3 {
  margin: 0;
  color: #1f3654;
  font-size: 18px;
}

.api-panel-heading p {
  margin: 4px 0 0;
  color: #6a8099;
  font-size: 12px;
}

.api-panel-meta {
  color: #5d7692;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.api-toolbar-grid {
  display: grid;
  grid-template-columns: 2fr repeat(5, minmax(0, 1fr));
  gap: 12px;
}

.api-toolbar-field {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.api-toolbar-field span {
  font-size: 12px;
  font-weight: 700;
  color: #56708d;
}

.api-toolbar-field input,
.api-toolbar-field select {
  width: 100%;
  min-width: 0;
  height: 42px;
  border: 1px solid #cfdbec;
  border-radius: 14px;
  background: #fff;
  padding: 0 12px;
  color: #20364f;
}

.api-toolbar-search {
  grid-column: span 2;
}

.api-instance-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(290px, 1fr));
  gap: 14px;
}

.api-instance-card {
  position: relative;
  width: 100%;
  border: 1px solid #dce6f2;
  border-radius: 18px;
  background: #fff;
  padding: 16px 16px 16px 20px;
  text-align: left;
  cursor: pointer;
  transition: transform .14s ease, box-shadow .14s ease, border-color .14s ease;
  box-sizing: border-box;
}

.api-instance-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 18px rgba(52, 82, 127, 0.08);
}

.api-instance-card.active {
  border-color: #7fa8ef;
  box-shadow: 0 0 0 3px rgba(79, 130, 226, 0.10);
}

.api-card-accent {
  position: absolute;
  left: 0;
  top: 14px;
  bottom: 14px;
  width: 4px;
  border-radius: 999px;
  background: #d0dae6;
}

.tone-success .api-card-accent {
  background: #2b9957;
}

.tone-warning .api-card-accent {
  background: #d88b18;
}

.tone-danger .api-card-accent,
.tone-critical .api-card-accent {
  background: #cc4c4c;
}

.tone-neutral .api-card-accent {
  background: #8aa0ba;
}

.api-card-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.api-card-title-block {
  min-width: 0;
  flex: 1;
}

.api-card-model {
  min-width: 0;
  font-size: 16px;
  color: #1e3552;
  line-height: 1.3;
  word-break: break-word;
  display: block;
}

.api-card-count {
  min-width: 28px;
  height: 28px;
  padding: 0 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999px;
  background: linear-gradient(135deg, #3b75da, #66a4ff);
  color: #fff;
  font-size: 13px;
  font-weight: 800;
}

.api-card-sub {
  margin-top: 6px;
  color: #6a819b;
  font-size: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", "Courier New", monospace;
}

.api-card-host {
  margin-top: 10px;
  color: #475f7a;
  font-size: 13px;
  font-weight: 600;
}

.api-card-tags {
  margin-top: 12px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.api-status-pill {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
}

.api-status-pill.large {
  min-height: 28px;
}

.pill-success {
  background: #e9f8ef;
  color: #25734b;
}

.pill-warning {
  background: #fff3e5;
  color: #9a6120;
}

.pill-danger,
.pill-critical {
  background: #ffe9ea;
  color: #b33b43;
}

.pill-neutral {
  background: #edf2f7;
  color: #687a92;
}

.api-meta-chip {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 0 10px;
  border: 1px solid #d8e4f1;
  border-radius: 999px;
  background: #f7fbff;
  color: #617890;
  font-size: 12px;
}

.api-card-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
}

.api-mini-stat {
  padding: 10px 12px;
  border: 1px solid #e1e9f3;
  border-radius: 14px;
  background: #f8fbff;
  text-align: center;
}

.api-mini-stat span {
  display: block;
  font-size: 11px;
  color: #677e97;
}

.api-mini-stat strong {
  display: block;
  margin-top: 4px;
  font-size: 20px;
  color: #23416f;
}

.api-detail-grid {
  display: grid;
  grid-template-columns: 340px minmax(0, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}

.api-detail-topline {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.api-metric-card {
  padding: 12px;
  border: 1px solid #e1e9f3;
  border-radius: 14px;
  background: #fbfdff;
}

.api-metric-card span {
  display: block;
  font-size: 11px;
  color: #677e97;
}

.api-metric-card strong {
  display: block;
  margin-top: 6px;
  font-size: 22px;
  color: #23416f;
}

.api-detail-info-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.api-detail-field {
  padding: 12px;
  border: 1px solid #e1e9f3;
  border-radius: 14px;
  background: #fbfdff;
}

.api-field-label {
  display: block;
  color: #6b8098;
  font-size: 11px;
  font-weight: 700;
}

.api-field-value {
  display: block;
  margin-top: 6px;
  color: #22384f;
  font-size: 13px;
}

.api-mount-head {
  margin-bottom: 10px;
}

.api-mount-title {
  color: #324f72;
  font-size: 13px;
  font-weight: 800;
}

.api-mount-sub {
  margin-top: 3px;
  color: #70859e;
  font-size: 12px;
}

.api-mount-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
}

.api-mount-card {
  padding: 12px;
  border: 1px solid #e1e9f3;
  border-radius: 14px;
  background: #fbfdff;
}

.api-mount-main {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.api-mount-main strong {
  color: #223b58;
}

.api-mount-task {
  color: #5e7794;
  font-size: 12px;
}

.api-mount-subline {
  margin-top: 5px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  color: #6c829b;
  font-size: 12px;
}

.api-pool-chip {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border-radius: 999px;
  background: #edf4fb;
  color: #48617f;
  font-size: 11px;
  font-weight: 700;
}

.api-bank-tabs {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}

.api-bank-tab {
  min-height: 34px;
  padding: 0 14px;
  border: 1px solid #d5e0ef;
  border-radius: 999px;
  background: #f6f9fd;
  color: #55708d;
  font-weight: 700;
  cursor: pointer;
}

.api-bank-tab.active {
  border-color: #8bb1ef;
  background: #eaf3ff;
  color: #26559a;
}

.api-inline-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.api-endpoint-health {
  display: grid;
  gap: 2px;
  min-width: 108px;
}

.api-endpoint-health span {
  color: #1f3654;
  font-weight: 700;
}

.api-endpoint-health small {
  color: #6d7f96;
  white-space: nowrap;
}

.api-btn {
  border: 1px solid #ccd8e8;
  border-radius: 12px;
  background: #fff;
  min-height: 38px;
  padding: 8px 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  white-space: nowrap;
  color: #294a74;
}

.api-btn.primary {
  background: #2c6ae6;
  border-color: #2c6ae6;
  color: #fff;
}

.api-btn.ghost {
  background: #f7f9fd;
}

.api-btn.mini {
  min-height: 32px;
  padding: 6px 10px;
  font-size: 12px;
}

.api-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.danger-text {
  color: #b64848;
}

.api-table-wrap {
  border: 1px solid #dce6f2;
  border-radius: 14px;
  overflow: auto;
  max-height: 420px;
}

.api-table-wrap.short {
  max-height: 280px;
}

.api-table-wrap.instances {
  max-height: none;
  overflow: visible;
}

.api-table-wrap.compact {
  max-height: 260px;
}

.api-instance-table tr.active {
  background: #f4f8ff;
}

.api-action-col,
.api-action-cell {
  width: 86px;
  text-align: right;
}

.api-action-cell {
  position: relative;
}

.api-icon-btn {
  width: 34px;
  height: 34px;
  border: 1px solid #cfdbec;
  border-radius: 10px;
  background: #fff;
  color: #27415f;
  font-weight: 800;
  cursor: pointer;
}

.api-icon-btn:hover {
  border-color: #8fb3e5;
  background: #f4f8ff;
}

.api-row-menu {
  position: absolute;
  top: 38px;
  right: 8px;
  z-index: 20;
  min-width: 148px;
  border: 1px solid #d8e3f1;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 18px 38px rgba(35, 61, 94, 0.18);
  padding: 6px;
  display: grid;
  gap: 2px;
}

.api-global-menu-layer {
  position: fixed;
  inset: 0;
  z-index: 5000;
  background: transparent;
}

.api-global-menu {
  position: fixed;
  z-index: 5001;
  width: 168px;
  border: 1px solid #d8e3f1;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 18px 38px rgba(35, 61, 94, 0.18);
  padding: 6px;
  display: grid;
  gap: 2px;
}

.api-global-menu button {
  border: 0;
  border-radius: 8px;
  background: transparent;
  padding: 9px 10px;
  color: #25415f;
  text-align: left;
  cursor: pointer;
  font: inherit;
}

.api-global-menu button:hover {
  background: #f2f6fc;
}

.api-muted {
  margin-top: 4px;
  color: #7a8da5;
  font-size: 12px;
}

.api-empty-state {
  padding: 24px 12px;
  text-align: center;
  color: #7b8fa6;
}

.truncate {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.api-modal-mask {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(17, 28, 44, 0.42);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 28px;
}

.api-modal-mask.stacked {
  z-index: 2600;
}

.api-modal-shell {
  width: min(1240px, calc(100vw - 56px));
  max-height: calc(100vh - 56px);
  overflow: auto;
  border: 1px solid #dbe6f4;
  border-radius: 26px;
  background: #f8fbff;
  box-shadow: 0 24px 60px rgba(16, 32, 58, 0.22);
}

.api-modal-shell.editor {
  width: min(1180px, calc(100vw - 56px));
}

.api-modal-shell.create {
  width: min(1320px, calc(100vw - 56px));
}

.api-modal-shell.endpoint-config {
  width: min(1240px, calc(100vw - 56px));
}

.api-modal-shell.endpoint-editor {
  width: min(960px, calc(100vw - 56px));
}

.api-modal-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 22px 24px 16px;
  border-bottom: 1px solid #e4edf7;
}

.api-modal-head h3 {
  margin: 0;
  font-size: 22px;
  color: #1f3654;
}

.api-modal-head p {
  margin: 6px 0 0;
  color: #6a8099;
  font-size: 13px;
}

.api-modal-tabs {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 24px 0;
}

.api-modal-body {
  padding: 20px 24px 24px;
}

.endpoint-config-body {
  display: grid;
  grid-template-columns: minmax(300px, 0.75fr) minmax(520px, 1.45fr);
  gap: 18px;
  align-items: start;
}

.api-panel-heading.compact {
  margin-bottom: 12px;
}

.api-detail-info-grid.compact {
  grid-template-columns: 1fr;
}

.api-inherit-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}

.api-inherit-strip > div {
  min-width: 0;
  border: 1px solid #dce6f2;
  border-radius: 14px;
  background: #fbfdff;
  padding: 12px;
}

.api-inherit-strip strong {
  display: block;
  margin-top: 6px;
  color: #233f5e;
  font-size: 13px;
  word-break: break-all;
}

.api-inline-warning {
  margin: -6px 0 16px;
  padding: 10px 12px;
  border: 1px solid #ffd8a8;
  border-radius: 12px;
  background: #fff8ec;
  color: #8a5a16;
  font-size: 13px;
}

.api-endpoint-list-panel,
.api-endpoint-form-panel {
  border: 1px solid #dce6f2;
  border-radius: 18px;
  background: #fbfdff;
  padding: 16px;
}

.api-endpoint-config-list {
  display: grid;
  gap: 10px;
  max-height: 560px;
  overflow: auto;
}

.api-endpoint-config-row {
  border: 1px solid #d8e4f2;
  border-radius: 14px;
  background: #fff;
  padding: 12px;
  display: grid;
  gap: 10px;
  cursor: pointer;
}

.api-endpoint-config-row:hover,
.api-endpoint-config-row.active {
  border-color: #8fb3e5;
  background: #f5f9ff;
}

.api-endpoint-config-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  color: #6b7f98;
  font-size: 12px;
}

.api-create-layout {
  display: grid;
  gap: 18px;
}

.api-create-section {
  border: 1px solid #dce6f2;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.95);
  padding: 18px;
}

.api-section-title {
  margin-bottom: 14px;
  color: #2b4669;
  font-size: 14px;
  font-weight: 800;
}

.api-form-grid {
  display: grid;
  gap: 12px;
}

.api-form-grid.cols-3 {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.api-form-grid.cols-4 {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.api-form-field {
  display: grid;
  gap: 6px;
  min-width: 0;
}

.api-form-field.wide {
  grid-column: span 3;
}

.api-form-field > span {
  color: #56708d;
  font-size: 12px;
  font-weight: 700;
}

.api-label-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  width: fit-content;
}

.api-help {
  width: 18px;
  height: 18px;
  border: 1px solid #b9c8db;
  border-radius: 999px;
  background: #f7fbff;
  color: #496987;
  font-size: 12px;
  line-height: 16px;
  font-weight: 800;
  cursor: help;
  padding: 0;
}

.api-help:hover {
  border-color: #7fa8ef;
  background: #eaf3ff;
  color: #26559a;
}

.api-form-field input,
.api-form-field select {
  width: 100%;
  min-width: 0;
  height: 42px;
  border: 1px solid #cfdbec;
  border-radius: 14px;
  background: #fff;
  padding: 0 12px;
  color: #20364f;
}

.api-create-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  border: 1px solid #dce6f2;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.95);
  padding: 16px 18px;
}

.api-create-hint {
  min-width: 0;
}

.api-create-id {
  display: block;
  margin-top: 6px;
  color: #1f3654;
  font-size: 14px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", "Courier New", monospace;
  word-break: break-all;
}

@media (max-width: 1440px) {
  .api-toolbar-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .api-toolbar-search {
    grid-column: span 3;
  }
}

@media (max-width: 1180px) {
  .api-summary-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .api-detail-grid {
    grid-template-columns: 1fr;
  }

  .endpoint-config-body {
    grid-template-columns: 1fr;
  }

  .api-inherit-strip {
    grid-template-columns: 1fr;
  }

  .api-form-grid.cols-3,
  .api-form-grid.cols-4 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .api-form-field.wide {
    grid-column: span 2;
  }
}

@media (max-width: 860px) {
  .api-summary-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .api-toolbar-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .api-toolbar-search {
    grid-column: span 2;
  }

  .api-detail-topline,
  .api-detail-info-grid {
    grid-template-columns: 1fr;
  }

  .api-form-grid.cols-3,
  .api-form-grid.cols-4 {
    grid-template-columns: 1fr;
  }

  .api-form-field.wide {
    grid-column: span 1;
  }

  .api-create-footer {
    flex-direction: column;
    align-items: stretch;
  }

  .api-modal-mask {
    padding: 12px;
  }

  .api-modal-shell,
  .api-modal-shell.editor {
    width: calc(100vw - 24px);
    max-height: calc(100vh - 24px);
  }
}
</style>
