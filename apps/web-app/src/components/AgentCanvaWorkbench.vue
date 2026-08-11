<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'

const props = defineProps({
  workflow: { type: Object, required: true },
  baseUrl: { type: String, required: true },
  token: { type: String, required: true },
  appearance: { type: String, default: 'dark' },
  knowledgeBases: { type: Array, default: () => [] },
  skills: { type: Array, default: () => [] },
  mcpConnections: { type: Array, default: () => [] },
})
const emit = defineEmits(['close', 'saved', 'published', 'notify'])

const NODE_WIDTH = 216
const NODE_HEIGHT = 84
const WORLD_WIDTH = 2200
const WORLD_HEIGHT = 1500
const canvasViewport = ref(null)
const uid = (prefix) => `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`

const fallbackPalette = [
  { group: '流程', items: [
    { type: 'start', label: '开始', icon: '▶', color: '#34c759', note: '接收流程输入' },
    { type: 'end', label: '结束', icon: '■', color: '#ff453a', note: '输出最终结果' },
    { type: 'condition.if', label: '条件判断', icon: '◇', color: '#ff9f0a', note: '按条件选择分支' },
  ] },
  { group: 'AI 能力', items: [
    { type: 'llm.generate', label: '大模型', icon: '✦', color: '#0a84ff', note: '生成、分析或总结' },
    { type: 'retrieval.search', label: '知识检索', icon: '⌕', color: '#30b0c7', note: '检索知识库证据' },
    { type: 'skill.call', label: 'Skill 调用', icon: 'S', color: '#5e5ce6', note: '调用已发布 Skill' },
  ] },
  { group: '工具与协作', items: [
    { type: 'tool.http', label: 'HTTP 请求', icon: '↗', color: '#bf5af2', note: '调用外部 API' },
    { type: 'tool.mcp', label: 'MCP 工具', icon: 'M', color: '#ff9f0a', note: '调用已接入工具' },
    { type: 'human.approval', label: '人工审批', icon: '✓', color: '#ff375f', note: '等待人工决策' },
    { type: 'parallel.fork', label: '并行分叉', icon: '⑂', color: '#5e5ce6', note: '启动多个并行分支' },
    { type: 'parallel.join', label: '并行汇合', icon: '⑁', color: '#64d2ff', note: '汇合并选择输出' },
  ] },
]
const palette = ref(fallbackPalette)
const nodeColors = { start:'#34c759',end:'#ff453a','condition.if':'#ff9f0a','llm.generate':'#0a84ff','retrieval.search':'#30b0c7','skill.call':'#5e5ce6','tool.http':'#bf5af2','tool.mcp':'#ff9f0a','human.approval':'#ff375f','parallel.fork':'#5e5ce6','parallel.join':'#64d2ff' }
const nodeDefinition = (type) => palette.value.flatMap((group) => group.items).find((item) => item.type === type) || { label: type, icon: '•', color: '#8e8e93', note: '' }

const form = reactive({ name: '', description: '', revision: 0, status: 'DRAFT', latestVersion: 0 })
const graph = reactive({ nodes: [], edges: [], entryNodeId: '', view: { x: 60, y: 70, zoom: 1 } })
const ui = reactive({
  loading: false,
  dirty: false,
  tab: 'design',
  selectedNodeId: '',
  selectedEdgeId: '',
  connectingFrom: '',
  error: '',
  validation: { valid: true, errors: [], warnings: [] },
  paletteOpen: true,
  inspectorOpen: true,
})
const debug = reactive({
  input: '',
  running: false,
  cancelling: false,
  conversationId: '',
  runId: '',
  status: 'IDLE',
  stage: '',
  progress: 0,
  output: null,
  citations: null,
  metrics: null,
  events: [],
  error: '',
  startedAt: '',
})
let debugPollTimer = null
let debugPollGeneration = 0
const drag = reactive({ active: false, nodeId: '', pointerId: null, startX: 0, startY: 0, nodeX: 0, nodeY: 0 })
const pan = reactive({ active: false, pointerId: null, startX: 0, startY: 0, viewX: 0, viewY: 0 })

const selectedNode = computed(() => graph.nodes.find((node) => node.id === ui.selectedNodeId) || null)
const selectedEdge = computed(() => graph.edges.find((edge) => edge.id === ui.selectedEdgeId) || null)
const issueNodeIds = computed(() => new Set((ui.validation.errors || []).map((item) => item.nodeId || item.node_id).filter(Boolean)))
const issueEdgeIds = computed(() => new Set((ui.validation.errors || []).map((item) => item.edgeId || item.edge_id).filter(Boolean)))
const nodeCountLabel = computed(() => `${graph.nodes.length} 个节点 · ${graph.edges.length} 条连线`)
const canPublish = computed(() => !ui.loading && !ui.dirty && ui.validation.valid && graph.nodes.length > 1)
const debugStatusLabel = computed(() => ({
  IDLE: '尚未运行', PENDING: '等待执行', RUNNING: '正在运行', WAITING_APPROVAL: '等待人工审批',
  SUCCEEDED: '运行成功', FAILED: '运行失败', CANCELLED: '已取消',
})[debug.status] || debug.status)
const debugTerminal = computed(() => ['SUCCEEDED', 'FAILED', 'CANCELLED', 'WAITING_APPROVAL'].includes(debug.status))
const debugNodeEvents = computed(() => {
  const latest = new Map()
  for (const event of debug.events) {
    const nodeId = event.payload?.node_id
    if (nodeId) latest.set(nodeId, event)
  }
  return graph.nodes.map((node) => ({ node, event: latest.get(node.id) || null }))
})

function clone(value) { return JSON.parse(JSON.stringify(value)) }
function normalizeLayout(layout = {}) {
  const positions = layout.positions || layout.nodes || {}
  return {
    positions,
    viewport: layout.viewport || { x: 60, y: 70, zoom: 1 },
  }
}

function hydrate(workflow) {
  const dsl = workflow.draft_dsl || {}
  const layout = normalizeLayout(workflow.draft_layout || {})
  form.name = workflow.name || '未命名 Workflow'
  form.description = workflow.description || ''
  form.revision = Number(workflow.revision || 0)
  form.status = workflow.status || 'DRAFT'
  form.latestVersion = Number(workflow.latest_version_no || 0)
  graph.entryNodeId = dsl.entry_node_id || ''
  graph.nodes.splice(0, graph.nodes.length, ...(dsl.nodes || []).map((node, index) => ({
    id: node.id,
    type: node.type,
    config: clone(node.config || {}),
    x: Number(layout.positions?.[node.id]?.x ?? 110 + (index % 4) * 280),
    y: Number(layout.positions?.[node.id]?.y ?? 130 + Math.floor(index / 4) * 180),
  })))
  graph.edges.splice(0, graph.edges.length, ...(dsl.edges || []).map((edge) => ({
    id: edge.id,
    sourceNodeId: edge.source_node_id,
    targetNodeId: edge.target_node_id,
    conditionLabel: edge.condition_label || '',
  })))
  Object.assign(graph.view, { x: Number(layout.viewport?.x ?? 60), y: Number(layout.viewport?.y ?? 70), zoom: Number(layout.viewport?.zoom ?? 1) })
  ui.validation = workflow.validation ? normalizeValidation(workflow.validation) : localValidation()
  ui.selectedNodeId = ''
  ui.selectedEdgeId = ''
  ui.connectingFrom = ''
  ui.dirty = false
  resetDebug()
}

watch(() => props.workflow.workflow_id, () => hydrate(props.workflow), { immediate: true })
watch(() => [form.name, form.description], () => { ui.dirty = true })

function defaultConfig(type) {
  if (type === 'llm.generate') return { prompt: '${text}', system_prompt: '', temperature: 0.2, max_tokens: 1024 }
  if (type === 'retrieval.search') return {
    kb_id: props.knowledgeBases[0]?.kb_id || '', query_field: 'text', output_field: 'evidence', top_k: 6,
    rank_method: 'hybrid', candidate_k: 30, max_chunks_per_doc: 3, context_window: 1,
    rerank_enabled: false, rerank_k: 30, rerank_required: false,
  }
  if (type === 'skill.call') { const item=props.skills.find(x=>x.latest_version_no>0); return item?{skill_id:item.skill_id,skill_name:item.name,version:item.latest_version_no,definition:clone(item.latest_definition||{}),prompt:'{input}'}:{skill_id:'',skill_name:'',version:0,definition:{},prompt:'{input}'} }
  if (type === 'condition.if') return { field: 'text', operator: 'truthy', value: '' }
  if (type === 'tool.http') return { method: 'GET', url: 'https://', timeout_seconds: 30, output_field: 'tool_response' }
  if (type === 'tool.mcp') { const item=props.mcpConnections.find(x=>(x.tools||[]).length); const tool=item?.tools?.[0]; return item&&tool?{connection_id:item.connection_id,connection_name:item.name,endpoint_url:item.endpoint_url,auth:{type:item.auth_type,secret_ref:item.secret_ref||''},tool_name:tool.name,tool_snapshot:clone(tool),arguments:{},output_field:'tool_response',timeout_seconds:30}:{connection_id:'',tool_name:'',arguments:{},output_field:'tool_response',timeout_seconds:30} }
  if (type === 'human.approval') return { title: '请审批此流程', instructions: '', assignee_roles: ['TENANT_ADMIN'], timeout_seconds: 86400, timeout_action: 'reject' }
  if (type === 'parallel.join') return { answer_branch: '' }
  return {}
}

function addNode(type) {
  if (type === 'start' && graph.nodes.some((node) => node.type === 'start')) return notify('warning', '开始节点只能有一个')
  const rect = canvasViewport.value?.getBoundingClientRect()
  const centerX = ((rect?.width || 900) / 2 - graph.view.x) / graph.view.zoom - NODE_WIDTH / 2
  const centerY = ((rect?.height || 600) / 2 - graph.view.y) / graph.view.zoom - NODE_HEIGHT / 2
  const node = { id: uid(type.replace('.', '-')), type, config: defaultConfig(type), x: Math.max(24, centerX), y: Math.max(24, centerY) }
  graph.nodes.push(node)
  if (type === 'start' || !graph.entryNodeId) graph.entryNodeId = node.id
  selectNode(node.id)
  markDirty()
}

function selectNode(nodeId) { ui.selectedNodeId = nodeId; ui.selectedEdgeId = ''; ui.inspectorOpen = true }
function selectEdge(edgeId) { ui.selectedEdgeId = edgeId; ui.selectedNodeId = ''; ui.inspectorOpen = true }
function clearSelection() { ui.selectedNodeId = ''; ui.selectedEdgeId = ''; ui.connectingFrom = '' }
function markDirty() { ui.dirty = true; ui.validation = localValidation() }
function chooseSkill(id){const item=props.skills.find(x=>x.skill_id===id&&x.latest_version_no>0);if(!item||!selectedNode.value)return;Object.assign(selectedNode.value.config,{skill_id:item.skill_id,skill_name:item.name,version:item.latest_version_no,definition:clone(item.latest_definition||{})});markDirty()}
function chooseMcpConnection(id){const item=props.mcpConnections.find(x=>x.connection_id===id);if(!item||!selectedNode.value)return;const tool=item.tools?.[0];Object.assign(selectedNode.value.config,{connection_id:item.connection_id,connection_name:item.name,endpoint_url:item.endpoint_url,auth:{type:item.auth_type,secret_ref:item.secret_ref||''},tool_name:tool?.name||'',tool_snapshot:clone(tool||{})});markDirty()}
function chooseMcpTool(name){const item=props.mcpConnections.find(x=>x.connection_id===selectedNode.value?.config?.connection_id);const tool=item?.tools?.find(x=>x.name===name);if(selectedNode.value)selectedNode.value.config.tool_snapshot=clone(tool||{});markDirty()}
async function loadManifest(){try{const data=await request('/api/v1/workflow-node-types');const grouped=new Map();for(const item of data.items||[]){const group=item.category||'其他';if(!grouped.has(group))grouped.set(group,[]);grouped.get(group).push({type:item.type,label:item.label,icon:item.icon,color:nodeColors[item.type]||'#8e8e93',note:item.description||''})}if(grouped.size)palette.value=[...grouped].map(([group,items])=>({group,items}))}catch{/* 保留内置目录，离线编辑仍可用 */}}
loadManifest()

function startNodeDrag(event, node) {
  if (event.button !== 0) return
  event.stopPropagation()
  event.currentTarget.setPointerCapture?.(event.pointerId)
  Object.assign(drag, { active: true, nodeId: node.id, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, nodeX: node.x, nodeY: node.y })
  selectNode(node.id)
}

function startPan(event) {
  if (event.button !== 0 || event.target.closest?.('.canva-node,button')) return
  event.currentTarget.setPointerCapture?.(event.pointerId)
  Object.assign(pan, { active: true, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, viewX: graph.view.x, viewY: graph.view.y })
  clearSelection()
}

function onPointerMove(event) {
  if (drag.active && event.pointerId === drag.pointerId) {
    const node = graph.nodes.find((item) => item.id === drag.nodeId)
    if (!node) return
    node.x = Math.max(0, Math.min(WORLD_WIDTH - NODE_WIDTH, drag.nodeX + (event.clientX - drag.startX) / graph.view.zoom))
    node.y = Math.max(0, Math.min(WORLD_HEIGHT - NODE_HEIGHT, drag.nodeY + (event.clientY - drag.startY) / graph.view.zoom))
    ui.dirty = true
  }
  if (pan.active && event.pointerId === pan.pointerId) {
    graph.view.x = pan.viewX + event.clientX - pan.startX
    graph.view.y = pan.viewY + event.clientY - pan.startY
  }
}

function endPointer(event) {
  if (drag.active && event.pointerId === drag.pointerId) drag.active = false
  if (pan.active && event.pointerId === pan.pointerId) pan.active = false
}

function onWheel(event) {
  const direction = event.deltaY > 0 ? -0.1 : 0.1
  setZoom(graph.view.zoom + direction)
}
function setZoom(value) { graph.view.zoom = Math.max(0.45, Math.min(1.65, Math.round(value * 100) / 100)) }

function fitView() {
  if (!graph.nodes.length || !canvasViewport.value) return
  const rect = canvasViewport.value.getBoundingClientRect()
  const minX = Math.min(...graph.nodes.map((node) => node.x))
  const minY = Math.min(...graph.nodes.map((node) => node.y))
  const maxX = Math.max(...graph.nodes.map((node) => node.x + NODE_WIDTH))
  const maxY = Math.max(...graph.nodes.map((node) => node.y + NODE_HEIGHT))
  const zoom = Math.max(0.45, Math.min(1.1, Math.min((rect.width - 120) / Math.max(1, maxX - minX), (rect.height - 120) / Math.max(1, maxY - minY))))
  graph.view.zoom = zoom
  graph.view.x = (rect.width - (maxX - minX) * zoom) / 2 - minX * zoom
  graph.view.y = (rect.height - (maxY - minY) * zoom) / 2 - minY * zoom
}

function edgePath(edge) {
  const source = graph.nodes.find((node) => node.id === edge.sourceNodeId)
  const target = graph.nodes.find((node) => node.id === edge.targetNodeId)
  if (!source || !target) return ''
  const x1 = source.x + NODE_WIDTH
  const y1 = source.y + NODE_HEIGHT / 2
  const x2 = target.x
  const y2 = target.y + NODE_HEIGHT / 2
  const bend = Math.max(70, Math.abs(x2 - x1) * 0.48)
  return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`
}

function startConnection(event, node) {
  event.stopPropagation()
  if (node.type === 'end') return notify('warning', '结束节点不能连接到其他节点')
  ui.connectingFrom = ui.connectingFrom === node.id ? '' : node.id
  selectNode(node.id)
}

function finishConnection(event, target) {
  event.stopPropagation()
  if (!ui.connectingFrom) return
  const sourceId = ui.connectingFrom
  ui.connectingFrom = ''
  const problem = connectionProblem(sourceId, target.id)
  if (problem) return notify('warning', problem)
  const source = graph.nodes.find((node) => node.id === sourceId)
  graph.edges.push({ id: uid('edge'), sourceNodeId: sourceId, targetNodeId: target.id, conditionLabel: nextBranchLabel(source) })
  markDirty()
}

function nextBranchLabel(source) {
  const labels = graph.edges.filter((edge) => edge.sourceNodeId === source.id).map((edge) => edge.conditionLabel)
  if (source.type === 'condition.if') return labels.includes('true') ? 'false' : 'true'
  if (source.type === 'human.approval') return labels.includes('approve') ? 'reject' : 'approve'
  if (source.type === 'parallel.fork') return `branch-${labels.length + 1}`
  return ''
}

function connectionProblem(sourceId, targetId) {
  if (sourceId === targetId) return '节点不能连接到自身'
  const source = graph.nodes.find((node) => node.id === sourceId)
  const target = graph.nodes.find((node) => node.id === targetId)
  if (!source || !target) return '连接节点不存在'
  if (target.type === 'start') return '开始节点不能接收入站连线'
  if (graph.edges.some((edge) => edge.sourceNodeId === sourceId && edge.targetNodeId === targetId)) return '这两个节点已经连接'
  const multi = ['condition.if', 'parallel.fork', 'human.approval']
  if (!multi.includes(source.type) && graph.edges.some((edge) => edge.sourceNodeId === sourceId)) return '该节点只能有一条出站连线'
  const adjacency = new Map(graph.nodes.map((node) => [node.id, []]))
  graph.edges.forEach((edge) => adjacency.get(edge.sourceNodeId)?.push(edge.targetNodeId))
  const stack = [targetId]
  const seen = new Set()
  while (stack.length) {
    const id = stack.pop()
    if (id === sourceId) return '此连线会形成循环，v1 Workflow 仅支持 DAG'
    if (seen.has(id)) continue
    seen.add(id)
    stack.push(...(adjacency.get(id) || []))
  }
  return ''
}

function removeSelection() {
  if (selectedNode.value) {
    if (selectedNode.value.type === 'start') return notify('warning', '开始节点不能删除')
    const id = selectedNode.value.id
    graph.nodes.splice(graph.nodes.findIndex((node) => node.id === id), 1)
    graph.edges.splice(0, graph.edges.length, ...graph.edges.filter((edge) => edge.sourceNodeId !== id && edge.targetNodeId !== id))
    ui.selectedNodeId = ''
  } else if (selectedEdge.value) {
    graph.edges.splice(graph.edges.findIndex((edge) => edge.id === selectedEdge.value.id), 1)
    ui.selectedEdgeId = ''
  } else return
  markDirty()
}

function localValidation() {
  const errors = []
  const add = (code, message, nodeId = null, edgeId = null, suggestion = '') => errors.push({ code, message, nodeId, edgeId, suggestion })
  const nodeIds = new Set()
  const adjacency = new Map()
  const incoming = new Map()
  for (const node of graph.nodes) {
    if (!node.id || nodeIds.has(node.id)) add('NODE_ID_INVALID', '每个节点都需要唯一标识。', node.id || null)
    nodeIds.add(node.id)
    adjacency.set(node.id, [])
    incoming.set(node.id, [])
  }
  const edgeIds = new Set()
  for (const edge of graph.edges) {
    if (!edge.id || edgeIds.has(edge.id)) add('EDGE_ID_INVALID', '每条连线都需要唯一标识。', null, edge.id || null)
    edgeIds.add(edge.id)
    if (!nodeIds.has(edge.sourceNodeId) || !nodeIds.has(edge.targetNodeId)) {
      add('EDGE_NODE_MISSING', '连线引用了不存在的节点。', null, edge.id)
      continue
    }
    adjacency.get(edge.sourceNodeId).push(edge.targetNodeId)
    incoming.get(edge.targetNodeId).push(edge.sourceNodeId)
  }
  const starts = graph.nodes.filter((node) => node.type === 'start')
  const ends = graph.nodes.filter((node) => node.type === 'end')
  if (starts.length !== 1) add('ENTRY_NOT_UNIQUE', '流程必须且只能包含一个开始节点。')
  if (starts.length === 1 && graph.entryNodeId !== starts[0].id) add('ENTRY_NODE_MISMATCH', '流程入口必须指向开始节点。', starts[0].id)
  if (!ends.length) add('END_NODE_MISSING', '流程至少需要一个结束节点。')

  const reachable = new Set()
  const visitReachable = (id) => {
    if (!id || reachable.has(id)) return
    reachable.add(id)
    for (const target of adjacency.get(id) || []) visitReachable(target)
  }
  visitReachable(graph.entryNodeId)
  for (const node of graph.nodes) if (!reachable.has(node.id)) add('NODE_UNREACHABLE', '该节点无法从开始节点到达。', node.id, null, '连接到主流程或删除该节点。')

  const visiting = new Set()
  const visited = new Set()
  const visitCycle = (id) => {
    if (visiting.has(id)) return true
    if (visited.has(id)) return false
    visiting.add(id)
    if ((adjacency.get(id) || []).some(visitCycle)) return true
    visiting.delete(id)
    visited.add(id)
    return false
  }
  if (graph.nodes.some((node) => visitCycle(node.id))) add('CYCLE_DETECTED', '当前连线形成了环路，v1 Workflow 仅支持 DAG。')

  for (const node of graph.nodes) {
    const outgoing = graph.edges.filter((edge) => edge.sourceNodeId === node.id)
    const labels = outgoing.map((edge) => String(edge.conditionLabel || '').trim())
    if (node.type === 'start' && (incoming.get(node.id) || []).length) add('ENTRY_HAS_INCOMING', '开始节点不能有入站连线。', node.id)
    if (node.type === 'end' && outgoing.length) add('END_NODE_HAS_OUTGOING', '结束节点不能有出站连线。', node.id)
    if (!['end', 'condition.if', 'parallel.fork', 'human.approval'].includes(node.type) && outgoing.length !== 1) add('NODE_OUTGOING_AMBIGUOUS', `${nodeDefinition(node.type).label}需要恰好一条出站连线。`, node.id)
    if (node.type === 'condition.if') {
      const allowed = labels.every((label) => ['true', 'false', 'default'].includes(label))
      const unique = new Set(labels).size === labels.length
      const validLabels = allowed && unique && (labels.includes('default') || labels.includes('true') && labels.includes('false'))
      if (!validLabels) add('CONDITION_BRANCH_INVALID', '条件节点需要一条 default，或 true/false 两条分支。', node.id)
      if (!String(node.config?.field || '').trim()) add('CONDITION_FIELD_INVALID', '条件节点需要填写判断字段。', node.id)
    }
    if (node.type === 'human.approval') {
      const uniqueLabels = new Set(labels)
      if (outgoing.length !== 2 || !uniqueLabels.has('approve') || !uniqueLabels.has('reject')) add('HUMAN_APPROVAL_BRANCH_INVALID', '人工审批需要 approve 和 reject 两条分支。', node.id)
      const roles = node.config?.assignee_roles || []
      const timeout = Number(node.config?.timeout_seconds)
      if (!String(node.config?.title || '').trim() || !roles.length || roles.length > 8 || timeout < 60 || timeout > 604800 || !['approve', 'reject'].includes(node.config?.timeout_action)) add('HUMAN_APPROVAL_CONFIG_INVALID', '请完善审批标题、角色、超时时间和超时动作。', node.id)
    }
    if (node.type === 'parallel.fork') {
      const uniqueLabels = new Set(labels)
      if (outgoing.length < 2 || outgoing.length > 8) add('PARALLEL_BRANCH_COUNT_INVALID', '并行分叉需要 2–8 条分支。', node.id)
      if (labels.some((label) => !label) || uniqueLabels.size !== labels.length) add('PARALLEL_BRANCH_LABEL_INVALID', '并行分支标签不能为空且不能重复。', node.id)
    }
    if (node.type === 'parallel.join') {
      if ((incoming.get(node.id) || []).length < 2) add('PARALLEL_JOIN_INPUT_INVALID', '并行汇合至少需要两条入站连线。', node.id)
      if (outgoing.length !== 1) add('PARALLEL_JOIN_OUTPUT_INVALID', '并行汇合需要恰好一条出站连线。', node.id)
      if (!String(node.config?.answer_branch || '').trim()) add('PARALLEL_ANSWER_BRANCH_INVALID', '并行汇合需要指定输出分支。', node.id)
    }
    if (node.type === 'retrieval.search') {
      if (!String(node.config?.kb_id || '').trim()) add('RETRIEVAL_CONFIG_INVALID', '知识检索节点需要选择知识库。', node.id)
      const topK = Number(node.config?.top_k)
      if (topK < 1 || topK > 20) add('RETRIEVAL_CONFIG_INVALID', 'Top K 必须在 1–20 之间。', node.id)
      if (!['vector', 'hybrid', 'rrf', 'keyword'].includes(String(node.config?.rank_method || 'hybrid'))) add('RETRIEVAL_CONFIG_INVALID', '请选择有效的检索策略。', node.id)
      const rerankK = Number(node.config?.rerank_k ?? 30)
      if (rerankK < 1 || rerankK > 100) add('RETRIEVAL_CONFIG_INVALID', '重排候选数必须在 1–100 之间。', node.id)
    }
    if (node.type === 'skill.call' && (!String(node.config?.skill_id||'').trim() || Number(node.config?.version||0)<1 || !node.config?.definition?.instructions)) add('SKILL_CONFIG_INVALID', '请选择一个已发布的 Skill 版本。', node.id)
    if (node.type === 'tool.http') {
      let validUrl = false
      try { const url = new URL(String(node.config?.url || '')); validUrl = ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && !url.hash } catch { validUrl = false }
      const timeout = Number(node.config?.timeout_seconds)
      if (!validUrl || !['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(node.config?.method) || timeout < 1 || timeout > 60) add('HTTP_TOOL_CONFIG_INVALID', '请填写有效的 HTTP(S) 地址、方法和 1–60 秒超时。', node.id)
    }
    if (node.type === 'tool.mcp' && (!String(node.config?.connection_id||'').trim() || !String(node.config?.endpoint_url||'').trim() || !String(node.config?.tool_name||'').trim())) add('MCP_TOOL_CONFIG_INVALID', '请选择 MCP 连接和工具。', node.id)
    if (node.type === 'llm.generate') {
      const promptInvalid = node.config?.prompt != null && typeof node.config.prompt !== 'string'
      const systemPromptInvalid = node.config?.system_prompt != null && typeof node.config.system_prompt !== 'string'
      const temperature = node.config?.temperature == null ? null : Number(node.config.temperature)
      const maxTokens = node.config?.max_tokens == null ? null : Number(node.config.max_tokens)
      if (promptInvalid || systemPromptInvalid || temperature != null && (!Number.isFinite(temperature) || temperature < 0 || temperature > 2) || maxTokens != null && (!Number.isFinite(maxTokens) || maxTokens < 1 || maxTokens > 32768)) add('LLM_CONFIG_INVALID', '请检查提示词、Temperature 和最大 Token。', node.id)
    }
  }
  return { valid: errors.length === 0, errors, warnings: [] }
}

function dslPayload() {
  return {
    entry_node_id: graph.entryNodeId,
    nodes: graph.nodes.map(({ id, type, config }) => ({ id, type, config: clone(config || {}) })),
    edges: graph.edges.map((edge) => ({ id: edge.id, source_node_id: edge.sourceNodeId, target_node_id: edge.targetNodeId, condition_label: edge.conditionLabel || '' })),
  }
}
function layoutPayload() {
  return {
    schema_version: 1,
    positions: Object.fromEntries(graph.nodes.map((node) => [node.id, { x: Math.round(node.x), y: Math.round(node.y) }])),
    viewport: { x: Math.round(graph.view.x), y: Math.round(graph.view.y), zoom: Math.round(graph.view.zoom * 100) / 100 },
  }
}

async function request(path, options = {}) {
  const response = await fetch(`${props.baseUrl}${path}`, {
    method: options.method || 'GET',
    headers: { Authorization: `Bearer ${props.token}`, ...(options.body ? { 'Content-Type': 'application/json' } : {}) },
    body: options.body ? JSON.stringify(options.body) : undefined,
  })
  const text = await response.text()
  let data = {}
  try { data = text ? JSON.parse(text) : {} } catch { data = {} }
  if (!response.ok) {
    const message = response.status === 409 ? '草稿已被其他会话更新，请刷新后重试' : response.status === 422 ? '编排规则校验未通过' : response.status >= 500 ? 'Workflow 服务暂时不可用' : '请求未能完成'
    throw new Error(message)
  }
  return data
}

async function saveDraft() {
  ui.loading = true; ui.error = ''
  try {
    const data = await request(`/api/v1/workflows/${props.workflow.workflow_id}/draft`, { method: 'PUT', body: {
      name: form.name, description: form.description, dsl: dslPayload(), layout: layoutPayload(), expected_revision: form.revision,
    } })
    const item = data.item
    form.revision = Number(item.revision)
    form.status = item.status
    ui.validation = normalizeValidation(data.validation)
    ui.dirty = false
    emit('saved', item)
    notify(ui.validation.valid ? 'success' : 'warning', ui.validation.valid ? 'Workflow 草稿已保存' : `草稿已保存，发现 ${ui.validation.errors.length} 个编排问题`)
    return item
  } catch (error) { ui.error = error.message; notify('error', '保存失败', error.message); return null }
  finally { ui.loading = false }
}

async function validateRemote() {
  const saved = ui.dirty ? await saveDraft() : props.workflow
  if (!saved) return
  ui.loading = true
  try {
    const data = await request(`/api/v1/workflows/${props.workflow.workflow_id}/validate`, { method: 'POST' })
    ui.validation = normalizeValidation(data)
    notify(ui.validation.valid ? 'success' : 'warning', ui.validation.valid ? '编排规则校验通过' : `发现 ${ui.validation.errors.length} 个问题`)
  } catch (error) { notify('error', '校验失败', error.message) }
  finally { ui.loading = false }
}

async function publish() {
  if (ui.dirty || !ui.validation.valid) return
  ui.loading = true
  try {
    const data = await request(`/api/v1/workflows/${props.workflow.workflow_id}/publish`, { method: 'POST', body: { expected_revision: form.revision } })
    form.latestVersion = Number(data.item?.version_no || form.latestVersion + 1)
    form.status = 'PUBLISHED'
    emit('published', data.item)
    notify('success', `Workflow v${form.latestVersion} 已发布`)
  } catch (error) { notify('error', '发布失败', error.message) }
  finally { ui.loading = false }
}

function stopDebugPolling() {
  debugPollGeneration += 1
  if (debugPollTimer) clearTimeout(debugPollTimer)
  debugPollTimer = null
}

function resetDebug() {
  stopDebugPolling()
  Object.assign(debug, {
    input: '', running: false, cancelling: false, conversationId: '', runId: '', status: 'IDLE',
    stage: '', progress: 0, output: null, citations: null, metrics: null, events: [], error: '', startedAt: '',
  })
}

function applyDebugRun(run = {}) {
  debug.runId = run.run_id || debug.runId
  debug.status = run.status || debug.status
  debug.stage = run.stage || ''
  debug.progress = Number(run.progress || 0)
  debug.output = run.output_payload ?? null
  debug.citations = run.citations ?? null
  debug.metrics = run.metrics ?? null
  debug.error = run.error_message || ''
  debug.startedAt = run.started_at || debug.startedAt
}

async function refreshDebug(generation = debugPollGeneration) {
  if (!debug.runId || generation !== debugPollGeneration) return
  try {
    const [runData, eventData] = await Promise.all([
      request(`/api/v1/runs/${debug.runId}`),
      request(`/api/v1/runs/${debug.runId}/events/history`),
    ])
    if (generation !== debugPollGeneration) return
    applyDebugRun(runData.item)
    debug.events = eventData.items || []
    if (debugTerminal.value) {
      debug.running = false
      if (debug.status === 'SUCCEEDED') notify('success', 'Workflow 运行成功', `已完成 ${debug.events.length} 个运行事件`)
      else if (debug.status === 'WAITING_APPROVAL') notify('warning', '运行等待人工审批', '请前往审批中心处理后再查看结果')
      else if (debug.status === 'FAILED') notify('error', 'Workflow 运行失败', debug.error || '请查看节点事件定位问题')
      return
    }
    debugPollTimer = setTimeout(() => refreshDebug(generation), 850)
  } catch (error) {
    if (generation !== debugPollGeneration) return
    debug.running = false
    debug.error = error.message
    notify('error', '调试状态更新失败', error.message)
  }
}

async function runWorkflow() {
  const input = debug.input.trim()
  if (!input) return notify('warning', '请输入测试内容')
  if (ui.dirty) return notify('warning', '请先保存当前草稿', '调试只运行已发布的不可变版本')
  if (!ui.validation.valid) return notify('warning', '编排规则尚未通过', '请先返回设计画布修复问题')
  if (form.latestVersion < 1) return notify('warning', '请先发布 Workflow', '运行调试需要一个不可变版本')

  stopDebugPolling()
  Object.assign(debug, {
    running: true, cancelling: false, conversationId: '', runId: '', status: 'PENDING', stage: 'dispatch', progress: 0,
    output: null, citations: null, metrics: null, events: [], error: '', startedAt: new Date().toISOString(),
  })
  const generation = debugPollGeneration
  try {
    const conversationData = await request('/api/v1/conversations', { method: 'POST', body: { title: `Agent Canva 调试 · ${form.name}` } })
    debug.conversationId = conversationData.item?.conversation_id || ''
    const runData = await request(`/api/v1/conversations/${debug.conversationId}/messages`, { method: 'POST', body: {
      content: { text: input }, workflow_id: props.workflow.workflow_id, workflow_version_no: form.latestVersion,
    } })
    applyDebugRun(runData.run)
    if (debugTerminal.value) debug.running = false
    else await refreshDebug(generation)
  } catch (error) {
    if (generation !== debugPollGeneration) return
    debug.running = false
    debug.status = 'FAILED'
    debug.error = error.message
    notify('error', 'Workflow 调试启动失败', error.message)
  }
}

async function cancelDebugRun() {
  if (!debug.runId || !debug.running || debug.cancelling) return
  debug.cancelling = true
  try {
    await request(`/api/v1/runs/${debug.runId}/cancel`, { method: 'POST' })
    stopDebugPolling()
    debug.status = 'CANCELLED'
    debug.running = false
    notify('success', '运行已取消')
  } catch (error) { notify('error', '取消失败', error.message) }
  finally { debug.cancelling = false }
}

function debugEventLabel(eventType) {
  return ({
    RUN_STARTED: '运行开始', RUN_SUCCEEDED: '运行完成', RUN_FAILED: '运行失败', RUN_CANCELLED: '运行取消',
    NODE_STARTED: '开始执行', NODE_ATTEMPT_STARTED: '开始尝试', NODE_ATTEMPT_SUCCEEDED: '尝试成功',
    NODE_ATTEMPT_FAILED: '尝试失败', NODE_COMPLETED: '节点完成', NODE_FAILED: '节点失败', NODE_WAITING_APPROVAL: '等待审批',
  })[eventType] || String(eventType || '运行事件').replaceAll('_', ' ')
}
function debugEventClass(event) { return String(event?.status || event?.event_type || '').toLowerCase().replaceAll('_', '-') }
function formatDebugValue(value) { return value == null ? '尚无输出' : typeof value === 'string' ? value : JSON.stringify(value, null, 2) }
function formatDebugTime(value) { if (!value) return ''; const date = new Date(value); return Number.isNaN(date.getTime()) ? '' : date.toLocaleTimeString('zh-CN', { hour12: false }) }

function normalizeValidation(data) {
  const normalize = (item) => ({
    nodeId: item.nodeId || item.node_id || null,
    edgeId: item.edgeId || item.edge_id || null,
    code: item.code || '', message: item.message || '编排问题', suggestion: item.suggestion || '',
  })
  return { valid: Boolean(data?.valid), errors: (data?.errors || []).map(normalize), warnings: (data?.warnings || []).map(normalize) }
}

function notify(type, title, message = '') { emit('notify', { type, title, message }) }
function updateRoles(value) { if (selectedNode.value) { selectedNode.value.config.assignee_roles = String(value).split(/[,，]/).map((item) => item.trim()).filter(Boolean); markDirty() } }
function onKeydown(event) {
  if ((event.key === 'Delete' || event.key === 'Backspace') && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) removeSelection()
  if (event.key === 'Escape') clearSelection()
}
window.addEventListener('keydown', onKeydown)
onBeforeUnmount(() => { window.removeEventListener('keydown', onKeydown); stopDebugPolling() })
nextTick(() => { if (graph.nodes.length && !(props.workflow.draft_layout?.positions)) fitView() })
</script>

<template>
  <section class="agent-canva" :class="`theme-${appearance}`">
    <header class="canva-header">
      <div class="canva-title-row">
        <button class="icon-button back" type="button" aria-label="返回 Workflow" @click="emit('close')">‹</button>
        <span class="canva-mark">◇</span>
        <div class="canva-heading"><span>Agent Canva</span><input v-model.trim="form.name" maxlength="128" aria-label="Workflow 名称" /><small>{{ nodeCountLabel }} · 草稿 r{{ form.revision }}</small></div>
      </div>
      <div class="canva-actions">
        <span class="status-chip" :class="form.status.toLowerCase()">{{ form.status === 'PUBLISHED' ? `已发布 v${form.latestVersion}` : '草稿' }}</span>
        <button class="soft-button" type="button" :disabled="ui.loading" @click="validateRemote">检查规则</button>
        <button class="soft-button" type="button" :disabled="ui.loading || !ui.dirty" @click="saveDraft">{{ ui.loading ? '保存中…' : '保存' }}</button>
        <button class="primary-button" type="button" :disabled="!canPublish" @click="publish">发布版本</button>
      </div>
    </header>

    <div v-if="ui.error" class="canva-banner error">{{ ui.error }}</div>
    <nav class="canva-tabs" aria-label="工作台模式"><button :class="{ active: ui.tab === 'design' }" @click="ui.tab = 'design'">设计</button><button :class="{ active: ui.tab === 'debug' }" @click="ui.tab = 'debug'">运行与调试</button></nav>

    <div v-if="ui.tab === 'design'" class="canva-layout" :class="{ 'palette-closed': !ui.paletteOpen, 'inspector-closed': !ui.inspectorOpen }">
      <aside class="node-library glass-surface">
        <div class="panel-heading"><div><strong>节点</strong><small>点击添加到画布</small></div><button class="icon-button" @click="ui.paletteOpen = !ui.paletteOpen">{{ ui.paletteOpen ? '‹' : '›' }}</button></div>
        <div class="node-library-scroll">
          <section v-for="group in palette" :key="group.group"><h2>{{ group.group }}</h2><button v-for="item in group.items" :key="item.type" class="palette-node" type="button" @click="addNode(item.type)"><span :style="{ '--node-color': item.color }">{{ item.icon }}</span><span><strong>{{ item.label }}</strong><small>{{ item.note }}</small></span><b>＋</b></button></section>
        </div>
      </aside>

      <main class="canvas-shell glass-surface">
        <div class="canvas-toolbar">
          <div><button class="icon-button" title="适应画布" @click="fitView">⌗</button><button class="icon-button" title="缩小" @click="setZoom(graph.view.zoom - .1)">−</button><span>{{ Math.round(graph.view.zoom * 100) }}%</span><button class="icon-button" title="放大" @click="setZoom(graph.view.zoom + .1)">＋</button></div>
          <div><span v-if="ui.connectingFrom" class="connect-hint">选择目标节点的输入端口</span><button class="icon-button" title="删除所选" :disabled="!selectedNode && !selectedEdge" @click="removeSelection">⌫</button></div>
        </div>
        <div ref="canvasViewport" class="canvas-viewport" @pointerdown="startPan" @pointermove="onPointerMove" @pointerup="endPointer" @pointercancel="endPointer" @wheel.prevent="onWheel">
          <div class="canvas-world" :style="{ width: `${WORLD_WIDTH}px`, height: `${WORLD_HEIGHT}px`, transform: `translate(${graph.view.x}px,${graph.view.y}px) scale(${graph.view.zoom})` }">
            <svg class="edge-layer" :viewBox="`0 0 ${WORLD_WIDTH} ${WORLD_HEIGHT}`" aria-label="Workflow 连线">
              <g v-for="edge in graph.edges" :key="edge.id" class="edge-group" :class="{ selected: ui.selectedEdgeId === edge.id, invalid: issueEdgeIds.has(edge.id) }" @pointerdown.stop="selectEdge(edge.id)">
                <path class="edge-hit" :d="edgePath(edge)" />
                <path class="edge-path" :d="edgePath(edge)" />
                <text v-if="edge.conditionLabel" class="edge-label" :x="(graph.nodes.find(n => n.id === edge.sourceNodeId)?.x || 0) + NODE_WIDTH + 24" :y="(graph.nodes.find(n => n.id === edge.sourceNodeId)?.y || 0) + NODE_HEIGHT / 2 - 9">{{ edge.conditionLabel }}</text>
              </g>
            </svg>
            <article v-for="node in graph.nodes" :key="node.id" class="canva-node" :class="{ selected: ui.selectedNodeId === node.id, invalid: issueNodeIds.has(node.id), connecting: ui.connectingFrom === node.id }" :style="{ left: `${node.x}px`, top: `${node.y}px`, '--node-color': nodeDefinition(node.type).color }" @pointerdown="startNodeDrag($event, node)" @dblclick.stop="selectNode(node.id)">
              <button v-if="node.type !== 'start'" class="node-port input" :aria-label="`连接到${nodeDefinition(node.type).label}`" @pointerdown="finishConnection($event, node)"></button>
              <div class="node-icon">{{ nodeDefinition(node.type).icon }}</div><div class="node-copy"><strong>{{ nodeDefinition(node.type).label }}</strong><small>{{ node.id }}</small></div>
              <button v-if="node.type !== 'end'" class="node-port output" :class="{ active: ui.connectingFrom === node.id }" :aria-label="`从${nodeDefinition(node.type).label}开始连接`" @pointerdown="startConnection($event, node)"></button>
            </article>
          </div>
        </div>
        <div class="canvas-status"><span :class="{ bad: !ui.validation.valid }">{{ ui.validation.valid ? '✓ 结构正常' : `! ${ui.validation.errors.length} 个问题` }}</span><span>{{ ui.dirty ? '有未保存更改' : '已保存' }}</span></div>
      </main>

      <aside class="node-inspector glass-surface">
        <div class="panel-heading"><div><strong>属性</strong><small>{{ selectedNode ? '配置当前节点' : selectedEdge ? '配置当前连线' : '选择画布元素' }}</small></div><button class="icon-button" @click="ui.inspectorOpen = !ui.inspectorOpen">{{ ui.inspectorOpen ? '›' : '‹' }}</button></div>
        <div v-if="selectedNode" class="inspector-scroll">
          <div class="selected-summary"><span :style="{ '--node-color': nodeDefinition(selectedNode.type).color }">{{ nodeDefinition(selectedNode.type).icon }}</span><div><strong>{{ nodeDefinition(selectedNode.type).label }}</strong><small>{{ selectedNode.type }}</small></div></div>
          <label>节点标识<input v-model="selectedNode.id" disabled /></label>
          <template v-if="selectedNode.type === 'llm.generate'">
            <label>系统提示词<textarea v-model="selectedNode.config.system_prompt" rows="4" @input="markDirty" /></label><label>用户提示词<textarea v-model="selectedNode.config.prompt" rows="4" @input="markDirty" /></label>
            <div class="field-grid"><label>Temperature<input v-model.number="selectedNode.config.temperature" type="number" min="0" max="2" step="0.1" @input="markDirty" /></label><label>最大 Token<input v-model.number="selectedNode.config.max_tokens" type="number" min="1" max="32768" @input="markDirty" /></label></div>
          </template>
          <template v-else-if="selectedNode.type === 'retrieval.search'">
            <label>知识库<select v-model="selectedNode.config.kb_id" @change="markDirty"><option value="">请选择知识库</option><option v-for="kb in knowledgeBases" :key="kb.kb_id" :value="kb.kb_id">{{ kb.name }}</option></select></label>
            <div class="field-grid"><label>查询字段<input v-model="selectedNode.config.query_field" @input="markDirty" /></label><label>Top K<input v-model.number="selectedNode.config.top_k" type="number" min="1" max="20" @input="markDirty" /></label></div>
            <div class="field-grid"><label>检索策略<select v-model="selectedNode.config.rank_method" @change="markDirty"><option value="vector">向量</option><option value="hybrid">混合</option><option value="rrf">RRF</option><option value="keyword">关键词</option></select></label><label>候选数<input v-model.number="selectedNode.config.candidate_k" type="number" min="1" max="200" @input="markDirty" /></label></div>
            <div class="field-grid"><label>单文档上限<input v-model.number="selectedNode.config.max_chunks_per_doc" type="number" min="1" max="20" @input="markDirty" /></label><label>上下文窗口<input v-model.number="selectedNode.config.context_window" type="number" min="0" max="2" @input="markDirty" /></label></div>
            <label class="toggle-field"><input v-model="selectedNode.config.rerank_enabled" type="checkbox" @change="markDirty" /><span>启用模型重排</span></label>
            <div v-if="selectedNode.config.rerank_enabled" class="field-grid"><label>重排候选数<input v-model.number="selectedNode.config.rerank_k" type="number" min="1" max="100" @input="markDirty" /></label><label class="toggle-field"><input v-model="selectedNode.config.rerank_required" type="checkbox" @change="markDirty" /><span>失败即终止</span></label></div>
          </template>
          <template v-else-if="selectedNode.type === 'skill.call'">
            <label>已发布 Skill<select :value="selectedNode.config.skill_id" @change="chooseSkill($event.target.value)"><option value="">请选择 Skill</option><option v-for="item in skills.filter(x=>x.latest_version_no>0)" :key="item.skill_id" :value="item.skill_id">{{ item.name }} · v{{ item.latest_version_no }}</option></select></label>
            <label>输入模板<textarea v-model="selectedNode.config.prompt" rows="4" @input="markDirty" /></label><small class="field-help">保存时固定 Skill 版本和定义快照，后续编辑 Skill 不会改变已发布 Workflow。</small>
          </template>
          <template v-else-if="selectedNode.type === 'condition.if'">
            <label>判断字段<input v-model="selectedNode.config.field" @input="markDirty" /></label><label>运算符<select v-model="selectedNode.config.operator" @change="markDirty"><option value="truthy">为真</option><option value="falsy">为假</option><option value="equals">等于</option><option value="not_equals">不等于</option><option value="contains">包含</option><option value="exists">存在</option></select></label><label>比较值<input v-model="selectedNode.config.value" @input="markDirty" /></label>
          </template>
          <template v-else-if="selectedNode.type === 'tool.http'">
            <div class="field-grid compact"><label>方法<select v-model="selectedNode.config.method" @change="markDirty"><option v-for="method in ['GET','POST','PUT','PATCH','DELETE']" :key="method">{{ method }}</option></select></label><label>超时（秒）<input v-model.number="selectedNode.config.timeout_seconds" type="number" min="1" max="60" @input="markDirty" /></label></div><label>请求地址<input v-model="selectedNode.config.url" @input="markDirty" /></label><label>输出字段<input v-model="selectedNode.config.output_field" @input="markDirty" /></label>
          </template>
          <template v-else-if="selectedNode.type === 'tool.mcp'">
            <label>MCP 连接<select :value="selectedNode.config.connection_id" @change="chooseMcpConnection($event.target.value)"><option value="">请选择连接</option><option v-for="item in mcpConnections" :key="item.connection_id" :value="item.connection_id">{{ item.name }}</option></select></label>
            <label>工具<select v-model="selectedNode.config.tool_name" @change="chooseMcpTool($event.target.value)"><option value="">请选择工具</option><option v-for="tool in (mcpConnections.find(x=>x.connection_id===selectedNode.config.connection_id)?.tools||[])" :key="tool.name" :value="tool.name">{{ tool.title||tool.name }}</option></select></label>
            <label>参数映射（JSON）<textarea :value="JSON.stringify(selectedNode.config.arguments||{},null,2)" rows="7" @change="selectedNode.config.arguments=JSON.parse($event.target.value);markDirty()" /></label>
            <label>输出字段<input v-model="selectedNode.config.output_field" @input="markDirty" /></label><small class="field-help">远端工具注解不视为可信；请在发布前核对工具用途和数据边界。</small>
          </template>
          <template v-else-if="selectedNode.type === 'human.approval'">
            <label>审批标题<input v-model="selectedNode.config.title" @input="markDirty" /></label><label>审批说明<textarea v-model="selectedNode.config.instructions" rows="4" @input="markDirty" /></label><label>审批角色<input :value="(selectedNode.config.assignee_roles || []).join('，')" @input="updateRoles($event.target.value)" /></label><div class="field-grid"><label>超时（秒）<input v-model.number="selectedNode.config.timeout_seconds" type="number" min="60" max="604800" @input="markDirty" /></label><label>超时动作<select v-model="selectedNode.config.timeout_action" @change="markDirty"><option value="reject">拒绝</option><option value="approve">通过</option></select></label></div>
          </template>
          <template v-else-if="selectedNode.type === 'parallel.join'"><label>输出分支<input v-model="selectedNode.config.answer_branch" placeholder="例如：branch-1" @input="markDirty" /></label></template>
          <div v-if="issueNodeIds.has(selectedNode.id)" class="issue-list"><strong>需要处理</strong><article v-for="item in ui.validation.errors.filter(x => x.nodeId === selectedNode.id)" :key="item.code"><b>{{ item.message }}</b><small>{{ item.suggestion }}</small></article></div>
        </div>
        <div v-else-if="selectedEdge" class="inspector-scroll"><div class="selected-summary edge"><span>→</span><div><strong>连线</strong><small>{{ selectedEdge.sourceNodeId }} → {{ selectedEdge.targetNodeId }}</small></div></div><label>分支标签<input v-model.trim="selectedEdge.conditionLabel" placeholder="普通连线可留空" @input="markDirty" /></label><button class="danger-button" @click="removeSelection">删除连线</button></div>
        <div v-else class="inspector-empty"><span>⌁</span><strong>选择节点或连线</strong><p>在这里配置模型、知识库、条件和分支规则。</p></div>
      </aside>
    </div>

    <div v-else class="debug-layout">
      <main class="debug-main">
        <section class="debug-console glass-surface">
          <div class="debug-console-head">
            <div><span class="debug-orb">▷</span><div><h2>测试运行</h2><p>运行已发布的 v{{ form.latestVersion || '—' }}，草稿修改不会影响本次结果。</p></div></div>
            <span class="run-status" :class="debug.status.toLowerCase()"><i></i>{{ debugStatusLabel }}</span>
          </div>
          <label class="debug-input-label">测试输入<textarea v-model="debug.input" rows="6" maxlength="12000" placeholder="输入一段真实业务内容，观察 Workflow 如何经过每个节点……" :disabled="debug.running"></textarea></label>
          <div class="debug-actions">
            <div><span v-if="ui.dirty">当前草稿尚未保存</span><span v-else-if="form.latestVersion < 1">发布首个版本后即可运行</span><span v-else>将运行 Workflow v{{ form.latestVersion }}</span></div>
            <button v-if="debug.running" class="danger-button compact" :disabled="debug.cancelling" @click="cancelDebugRun">{{ debug.cancelling ? '取消中…' : '停止运行' }}</button>
            <button v-else class="primary-button" :disabled="!debug.input.trim() || ui.dirty || !ui.validation.valid || form.latestVersion < 1" @click="runWorkflow">开始运行</button>
          </div>
        </section>

        <section class="debug-result glass-surface">
          <div class="panel-heading"><div><strong>运行结果</strong><small>{{ debug.runId ? `Run ${debug.runId.slice(0, 8)} · ${debug.stage || '准备中'}` : '启动调试后在这里查看输出' }}</small></div><b v-if="debug.runId">{{ debug.progress }}%</b></div>
          <div v-if="debug.runId" class="run-progress"><i :style="{ width: `${debug.progress}%` }"></i></div>
          <div v-if="debug.error" class="debug-error"><strong>运行未完成</strong><span>{{ debug.error }}</span></div>
          <pre v-else class="debug-output">{{ formatDebugValue(debug.output) }}</pre>
          <details v-if="debug.metrics || debug.citations" class="debug-details"><summary>指标与引用</summary><pre>{{ formatDebugValue({ metrics: debug.metrics, citations: debug.citations }) }}</pre></details>
        </section>
      </main>

      <aside class="debug-timeline glass-surface">
        <div class="panel-heading"><div><strong>节点运行轨迹</strong><small>{{ debug.events.length ? `${debug.events.length} 个事件` : '等待运行事件' }}</small></div><button class="icon-button" title="返回设计画布" @click="ui.tab = 'design'">◇</button></div>
        <div class="timeline-scroll">
          <article v-for="item in debugNodeEvents" :key="item.node.id" class="timeline-node" :class="item.event ? debugEventClass(item.event) : 'idle'">
            <span class="timeline-icon" :style="{ '--node-color': nodeDefinition(item.node.type).color }">{{ nodeDefinition(item.node.type).icon }}</span>
            <div><strong>{{ nodeDefinition(item.node.type).label }}</strong><small>{{ item.event ? debugEventLabel(item.event.event_type) : '等待执行' }}</small></div>
            <time>{{ formatDebugTime(item.event?.created_at) }}</time>
          </article>
          <div v-if="!debug.runId" class="timeline-empty"><span>⌁</span><p>输入测试内容并开始运行，节点状态会按执行顺序出现在这里。</p></div>
        </div>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.agent-canva{--canva-bg:#f3f4f7;--canva-panel:rgba(255,255,255,.78);--canva-panel-solid:#fff;--canva-text:#1d1d1f;--canva-muted:#6e6e73;--canva-line:rgba(0,0,0,.1);--canva-grid:rgba(29,29,31,.08);display:flex;min-height:100%;height:100vh;flex-direction:column;overflow:hidden;color:var(--canva-text);background:radial-gradient(circle at 48% -10%,rgba(10,132,255,.12),transparent 34%),var(--canva-bg);font-family:Inter,-apple-system,BlinkMacSystemFont,"SF Pro Text",system-ui,sans-serif}
.canva-header{display:flex;align-items:center;justify-content:space-between;gap:18px;min-height:74px;padding:11px 18px;border-bottom:1px solid var(--canva-line);background:color-mix(in srgb,var(--canva-panel) 90%,transparent);backdrop-filter:blur(28px) saturate(160%);z-index:5}.canva-title-row,.canva-actions{display:flex;align-items:center;gap:9px;min-width:0}.canva-mark{display:grid;place-items:center;flex:0 0 42px;width:42px;height:42px;border-radius:14px;color:#fff;background:linear-gradient(145deg,#0a84ff,#675df6);box-shadow:0 10px 28px rgba(10,132,255,.25)}.canva-heading{display:grid;min-width:0}.canva-heading>span{color:#0a84ff;font-size:9px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.canva-heading input{width:min(360px,30vw);padding:0;border:0;color:var(--canva-text);background:transparent;font-size:17px;font-weight:720;letter-spacing:-.02em;outline:none}.canva-heading small{color:var(--canva-muted);font-size:9px}.icon-button,.soft-button,.primary-button,.danger-button{border:1px solid var(--canva-line);color:var(--canva-text);background:rgba(127,127,127,.075);cursor:pointer}.icon-button{display:grid;place-items:center;width:34px;height:34px;padding:0;border-radius:10px}.icon-button.back{font-size:26px}.soft-button,.primary-button,.danger-button{min-height:36px;padding:0 13px;border-radius:11px;font-size:11px;font-weight:680}.primary-button{border-color:transparent;color:#fff;background:#0a84ff}.danger-button{color:#ff453a;background:rgba(255,69,58,.1)}button:disabled{opacity:.42;cursor:not-allowed}.status-chip{padding:6px 9px;border-radius:999px;color:var(--canva-muted);background:rgba(127,127,127,.1);font-size:9px;font-weight:750}.status-chip.published{color:#248a3d;background:rgba(52,199,89,.12)}.canva-tabs{display:flex;justify-content:center;gap:3px;padding:6px;border-bottom:1px solid var(--canva-line);background:color-mix(in srgb,var(--canva-panel) 84%,transparent)}.canva-tabs button{padding:7px 16px;border:0;border-radius:9px;color:var(--canva-muted);background:transparent;font-size:11px}.canva-tabs button.active{color:var(--canva-text);background:rgba(127,127,127,.14);box-shadow:0 1px 5px rgba(0,0,0,.08)}.canva-banner{padding:9px 18px;font-size:11px}.canva-banner.error{color:#ff6961;background:rgba(255,69,58,.1)}
.canva-layout{display:grid;grid-template-columns:244px minmax(0,1fr) 302px;gap:10px;min-height:0;flex:1;padding:10px}.glass-surface{border:1px solid var(--canva-line);background:var(--canva-panel);box-shadow:0 18px 50px rgba(0,0,0,.08);backdrop-filter:blur(24px) saturate(150%)}.node-library,.node-inspector,.canvas-shell{min-width:0;min-height:0;border-radius:18px;overflow:hidden}.panel-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 15px;border-bottom:1px solid var(--canva-line)}.panel-heading>div{display:grid;gap:2px}.panel-heading strong{font-size:12px}.panel-heading small{color:var(--canva-muted);font-size:9px}.node-library-scroll,.inspector-scroll{height:calc(100% - 64px);overflow:auto;padding:12px}.node-library-scroll section+section{margin-top:17px}.node-library-scroll h2{margin:0 0 7px 4px;color:var(--canva-muted);font-size:9px;font-weight:720;letter-spacing:.08em}.palette-node{display:grid;grid-template-columns:32px 1fr auto;align-items:center;gap:9px;width:100%;margin:5px 0;padding:8px;border:1px solid transparent;border-radius:12px;color:var(--canva-text);text-align:left;background:transparent}.palette-node:hover{border-color:var(--canva-line);background:rgba(127,127,127,.075)}.palette-node>span:first-child,.selected-summary>span{display:grid;place-items:center;width:31px;height:31px;border-radius:10px;color:var(--node-color);background:color-mix(in srgb,var(--node-color) 13%,transparent);font-weight:800}.palette-node>span:nth-child(2){display:grid;gap:1px}.palette-node strong{font-size:10px}.palette-node small{color:var(--canva-muted);font-size:8px}.palette-node b{color:var(--canva-muted);font-size:14px;font-weight:400}
.canvas-shell{position:relative;display:flex;flex-direction:column}.canvas-toolbar,.canvas-status{display:flex;align-items:center;justify-content:space-between;gap:10px;z-index:3}.canvas-toolbar{position:absolute;top:12px;right:12px;left:12px;pointer-events:none}.canvas-toolbar>div{display:flex;align-items:center;gap:5px;padding:4px;border:1px solid var(--canva-line);border-radius:12px;background:color-mix(in srgb,var(--canva-panel-solid) 88%,transparent);box-shadow:0 8px 24px rgba(0,0,0,.1);backdrop-filter:blur(18px);pointer-events:auto}.canvas-toolbar span{min-width:42px;color:var(--canva-muted);text-align:center;font-size:9px}.canvas-toolbar .connect-hint{min-width:auto;padding:0 7px;color:#0a84ff}.canvas-viewport{position:relative;min-height:0;flex:1;overflow:hidden;cursor:grab;background-color:color-mix(in srgb,var(--canva-bg) 68%,var(--canva-panel-solid));background-image:radial-gradient(var(--canva-grid) 1px,transparent 1px);background-size:20px 20px}.canvas-viewport:active{cursor:grabbing}.canvas-world{position:absolute;left:0;top:0;transform-origin:0 0;will-change:transform}.edge-layer{position:absolute;inset:0;width:100%;height:100%;overflow:visible}.edge-path{fill:none;stroke:rgba(118,118,128,.54);stroke-width:2;pointer-events:none}.edge-hit{fill:none;stroke:transparent;stroke-width:16;cursor:pointer}.edge-group.selected .edge-path{stroke:#0a84ff;stroke-width:3}.edge-group.invalid .edge-path{stroke:#ff453a;stroke-dasharray:6 4}.edge-label{fill:var(--canva-muted);font-size:10px;paint-order:stroke;stroke:var(--canva-panel-solid);stroke-width:4px}.canva-node{position:absolute;display:flex;align-items:center;gap:11px;width:216px;height:84px;padding:13px 17px;border:1px solid var(--canva-line);border-left:4px solid var(--node-color);border-radius:16px;background:color-mix(in srgb,var(--canva-panel-solid) 92%,transparent);box-shadow:0 12px 28px rgba(0,0,0,.1);cursor:grab;user-select:none;touch-action:none;transition:box-shadow .18s ease,border-color .18s ease}.canva-node:active{cursor:grabbing}.canva-node.selected{border-color:color-mix(in srgb,var(--node-color) 78%,white);box-shadow:0 0 0 3px color-mix(in srgb,var(--node-color) 16%,transparent),0 18px 38px rgba(0,0,0,.14)}.canva-node.invalid{box-shadow:0 0 0 2px rgba(255,69,58,.4),0 14px 32px rgba(0,0,0,.12)}.canva-node.connecting{box-shadow:0 0 0 4px rgba(10,132,255,.18),0 18px 38px rgba(0,0,0,.14)}.node-icon{display:grid;place-items:center;flex:0 0 38px;width:38px;height:38px;border-radius:12px;color:var(--node-color);background:color-mix(in srgb,var(--node-color) 13%,transparent);font-size:17px}.node-copy{display:grid;min-width:0;gap:3px}.node-copy strong{font-size:12px}.node-copy small{overflow:hidden;color:var(--canva-muted);font:8px/1.2 ui-monospace,SFMono-Regular,Consolas,monospace;text-overflow:ellipsis}.node-port{position:absolute;top:50%;z-index:2;width:14px;height:14px;padding:0;border:3px solid var(--canva-panel-solid);border-radius:50%;background:var(--node-color);box-shadow:0 0 0 1px var(--canva-line);transform:translateY(-50%);cursor:crosshair}.node-port.input{left:-9px}.node-port.output{right:-9px}.node-port.output.active{box-shadow:0 0 0 5px rgba(10,132,255,.22)}.canvas-status{height:30px;padding:0 11px;border-top:1px solid var(--canva-line);color:var(--canva-muted);font-size:9px}.canvas-status span:first-child{color:#248a3d}.canvas-status span.bad{color:#ff453a}
.selected-summary{display:flex;align-items:center;gap:10px;margin-bottom:16px;padding:11px;border-radius:13px;background:rgba(127,127,127,.07)}.selected-summary>div{display:grid;gap:2px}.selected-summary strong{font-size:11px}.selected-summary small{color:var(--canva-muted);font:8px ui-monospace,monospace}.selected-summary.edge>span{color:#0a84ff;background:rgba(10,132,255,.12)}.inspector-scroll label{display:grid;gap:6px;margin-top:12px;color:var(--canva-muted);font-size:9px}.inspector-scroll input,.inspector-scroll textarea,.inspector-scroll select{width:100%;box-sizing:border-box;padding:9px 10px;border:1px solid var(--canva-line);border-radius:10px;color:var(--canva-text);background:rgba(127,127,127,.07);font:10px/1.4 inherit;outline:none}.inspector-scroll input:focus,.inspector-scroll textarea:focus,.inspector-scroll select:focus{border-color:#0a84ff;box-shadow:0 0 0 3px rgba(10,132,255,.12)}.inspector-scroll input:disabled{opacity:.55}.field-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.field-grid.compact{grid-template-columns:.7fr 1fr}.issue-list{display:grid;gap:7px;margin-top:18px}.issue-list>strong{color:#ff6961;font-size:10px}.issue-list article{display:grid;gap:3px;padding:9px;border-radius:10px;color:#ff6961;background:rgba(255,69,58,.09)}.issue-list b{font-size:9px}.issue-list small{font-size:8px}.danger-button{width:100%;margin-top:16px}.inspector-empty{display:grid;place-items:center;align-content:center;height:calc(100% - 64px);padding:30px;color:var(--canva-muted);text-align:center}.inspector-empty>span{display:grid;place-items:center;width:48px;height:48px;border-radius:15px;color:#0a84ff;background:rgba(10,132,255,.11);font-size:22px}.inspector-empty strong{margin-top:13px;color:var(--canva-text);font-size:11px}.inspector-empty p{font-size:9px;line-height:1.5}
.inspector-scroll .toggle-field{display:flex;align-items:center;gap:8px}.inspector-scroll .toggle-field input{width:16px;height:16px;margin:0;accent-color:#0a84ff}.inspector-scroll .toggle-field span{color:var(--canva-text);font-size:10px}
.debug-layout{display:grid;grid-template-columns:minmax(0,1fr) 330px;gap:10px;min-height:0;flex:1;padding:10px}.debug-main{display:grid;grid-template-rows:auto minmax(260px,1fr);gap:10px;min-width:0;min-height:0}.debug-console,.debug-result,.debug-timeline{min-width:0;border-radius:18px;overflow:hidden}.debug-console{padding:18px}.debug-console-head,.debug-console-head>div,.debug-actions{display:flex;align-items:center;justify-content:space-between;gap:12px}.debug-console-head>div{justify-content:flex-start}.debug-orb{display:grid;place-items:center;flex:0 0 42px;width:42px;height:42px;border-radius:14px;color:#fff;background:linear-gradient(145deg,#0a84ff,#675df6);box-shadow:0 10px 28px rgba(10,132,255,.22);font-size:17px}.debug-console h2{margin:0;color:var(--canva-text);font-size:17px;letter-spacing:-.02em}.debug-console p{margin:3px 0 0;color:var(--canva-muted);font-size:9px}.run-status{display:flex;align-items:center;gap:6px;padding:7px 10px;border-radius:999px;color:var(--canva-muted);background:rgba(127,127,127,.1);font-size:9px;font-weight:720;white-space:nowrap}.run-status i{width:7px;height:7px;border-radius:50%;background:currentColor}.run-status.running,.run-status.pending{color:#0a84ff}.run-status.succeeded{color:#30d158}.run-status.failed,.run-status.cancelled{color:#ff6961}.run-status.waiting_approval{color:#ff9f0a}.run-status.running i,.run-status.pending i{animation:debugPulse 1.15s ease-in-out infinite}.debug-input-label{display:grid;gap:8px;margin-top:18px;color:var(--canva-muted);font-size:9px}.debug-input-label textarea{width:100%;box-sizing:border-box;resize:vertical;min-height:112px;max-height:240px;padding:13px 14px;border:1px solid var(--canva-line);border-radius:14px;color:var(--canva-text);background:rgba(127,127,127,.065);font:11px/1.65 inherit;outline:none}.debug-input-label textarea:focus{border-color:#0a84ff;box-shadow:0 0 0 4px rgba(10,132,255,.12)}.debug-input-label textarea:disabled{opacity:.65}.debug-actions{margin-top:12px}.debug-actions>div{display:grid;color:var(--canva-muted);font-size:9px}.debug-actions .danger-button.compact{width:auto;margin:0}.debug-result{display:flex;min-height:0;flex-direction:column}.debug-result .panel-heading>b{color:#0a84ff;font-size:10px}.run-progress{height:3px;background:rgba(127,127,127,.12)}.run-progress i{display:block;height:100%;border-radius:0 4px 4px 0;background:linear-gradient(90deg,#0a84ff,#5e5ce6);transition:width .25s ease}.debug-output{min-height:0;flex:1;overflow:auto;margin:0;padding:18px;color:var(--canva-text);white-space:pre-wrap;word-break:break-word;font:10px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace}.debug-output:only-child{color:var(--canva-muted)}.debug-error{display:grid;gap:5px;margin:16px;padding:14px;border-radius:13px;color:#ff6961;background:rgba(255,69,58,.09);font-size:10px}.debug-error span{line-height:1.6}.debug-details{margin:0 16px 16px;border-top:1px solid var(--canva-line);color:var(--canva-muted);font-size:9px}.debug-details summary{padding:11px 0;cursor:pointer}.debug-details pre{overflow:auto;margin:0;padding:10px;border-radius:10px;background:rgba(127,127,127,.06);font:9px/1.6 ui-monospace,monospace}.debug-timeline{display:flex;min-height:0;flex-direction:column}.timeline-scroll{position:relative;min-height:0;flex:1;overflow:auto;padding:12px}.timeline-node{position:relative;display:grid;grid-template-columns:34px minmax(0,1fr) auto;align-items:center;gap:9px;margin-bottom:7px;padding:10px;border:1px solid var(--canva-line);border-radius:13px;background:rgba(127,127,127,.045)}.timeline-node:not(:last-child)::after{position:absolute;left:26px;top:100%;width:1px;height:8px;background:var(--canva-line);content:""}.timeline-icon{display:grid;place-items:center;width:32px;height:32px;border-radius:10px;color:var(--node-color);background:color-mix(in srgb,var(--node-color) 13%,transparent);font-size:13px}.timeline-node>div{display:grid;gap:2px}.timeline-node strong{font-size:10px}.timeline-node small,.timeline-node time{color:var(--canva-muted);font-size:8px}.timeline-node.running,.timeline-node.pending{border-color:rgba(10,132,255,.35);background:rgba(10,132,255,.07)}.timeline-node.succeeded,.timeline-node.node-completed,.timeline-node.node-attempt-succeeded{border-color:rgba(48,209,88,.28)}.timeline-node.failed,.timeline-node.node-failed,.timeline-node.node-attempt-failed{border-color:rgba(255,69,58,.32);background:rgba(255,69,58,.06)}.timeline-node.waiting-approval,.timeline-node.node-waiting-approval{border-color:rgba(255,159,10,.32);background:rgba(255,159,10,.06)}.timeline-empty{display:grid;place-items:center;align-content:center;min-height:200px;padding:24px;color:var(--canva-muted);text-align:center}.timeline-empty>span{font-size:28px}.timeline-empty p{max-width:210px;font-size:9px;line-height:1.6}@keyframes debugPulse{50%{opacity:.35;transform:scale(.72)}}
.canva-layout.palette-closed{grid-template-columns:54px minmax(0,1fr) 302px}.palette-closed .node-library-scroll,.palette-closed .panel-heading>div{display:none}.canva-layout.inspector-closed{grid-template-columns:244px minmax(0,1fr) 54px}.inspector-closed .node-inspector>.inspector-scroll,.inspector-closed .node-inspector>.inspector-empty,.inspector-closed .node-inspector .panel-heading>div{display:none}.canva-layout.palette-closed.inspector-closed{grid-template-columns:54px minmax(0,1fr) 54px}
:global(html[data-theme="dark"] .agent-canva){--canva-bg:#090a0c;--canva-panel:rgba(28,28,30,.78);--canva-panel-solid:#1c1c1e;--canva-text:#f5f5f7;--canva-muted:#a1a1a6;--canva-line:rgba(255,255,255,.11);--canva-grid:rgba(255,255,255,.08)}
:global(html[data-theme="dark"] .agent-canva select),:global(html[data-theme="dark"] .agent-canva option),:global(html[data-theme="dark"] .agent-canva optgroup){color-scheme:dark;color:#f5f5f7;background-color:#242426}
:global(html[data-theme="light"] .agent-canva){--canva-bg:#f3f4f7;--canva-panel:rgba(255,255,255,.78);--canva-panel-solid:#fff;--canva-text:#1d1d1f;--canva-muted:#6e6e73;--canva-line:rgba(0,0,0,.1);--canva-grid:rgba(29,29,31,.08)}
@media(max-width:1100px){.canva-layout{grid-template-columns:220px minmax(0,1fr)}.node-inspector{grid-column:1/-1;max-height:300px}.inspector-scroll{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 14px}.inspector-scroll>.selected-summary,.inspector-scroll>.issue-list,.inspector-scroll>.danger-button{grid-column:1/-1}.canva-layout.inspector-closed{grid-template-columns:220px minmax(0,1fr)}.inspector-closed .node-inspector{display:none}.debug-layout{grid-template-columns:1fr;overflow:auto}.debug-main{grid-template-rows:auto minmax(300px,1fr)}.debug-timeline{min-height:280px}}
@media(max-width:760px){.agent-canva{height:100dvh}.canva-header{align-items:flex-start;padding:9px 10px}.canva-mark,.canva-heading>span,.canva-heading small,.status-chip,.soft-button:first-of-type{display:none}.canva-heading input{width:min(42vw,260px);font-size:14px}.canva-actions{overflow-x:auto;justify-content:flex-end}.canva-actions button{white-space:nowrap}.canva-layout,.canva-layout.inspector-closed{grid-template-columns:1fr;grid-template-rows:auto minmax(430px,1fr) auto;padding:7px}.node-library{max-height:132px}.node-library .panel-heading{display:none}.node-library-scroll{display:flex;height:auto;gap:8px;overflow-x:auto;padding:8px}.node-library-scroll section{display:flex;gap:6px;margin:0!important}.node-library-scroll h2,.palette-node small,.palette-node b{display:none}.palette-node{display:flex;flex:0 0 auto;width:auto;margin:0;padding:6px 9px}.palette-node>span:nth-child(2){display:block}.canvas-shell{min-height:52vh}.node-inspector{grid-column:auto;max-height:300px}.inspector-scroll{grid-template-columns:1fr}.inspector-scroll>*{grid-column:auto!important}.panel-heading .icon-button{display:none}.canva-tabs{justify-content:flex-start;padding-left:12px}.debug-layout{padding:7px}.debug-console{padding:14px}.debug-console-head{align-items:flex-start}.debug-console-head>div{min-width:0}.debug-console p{line-height:1.5}.debug-input-label textarea{min-height:128px}.debug-actions{align-items:flex-end}.debug-main{grid-template-rows:auto minmax(300px,1fr)}.debug-timeline{min-height:320px}}
@media(prefers-reduced-motion:reduce){.canva-node,.palette-node,.run-progress i{transition:none}.run-status i{animation:none!important}}
</style>
