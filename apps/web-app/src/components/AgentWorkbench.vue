<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

const props = defineProps({
  agent: { type: Object, required: true },
  baseUrl: { type: String, required: true },
  chatBaseUrl: { type: String, required: true },
  token: { type: String, required: true },
  knowledgeBases: { type: Array, default: () => [] },
  workflows: { type: Array, default: () => [] },
  skills: { type: Array, default: () => [] },
  mcpConnections: { type: Array, default: () => [] },
  javascriptTools: { type: Array, default: () => [] },
})
const emit = defineEmits(['close', 'saved', 'published', 'notify'])

const defaultConfig = () => ({
  role: '', response_style: '', model_endpoint_id: '', temperature: 0.2, top_p: 1,
  max_tokens: 4096, prohibited_rules: '',
  memory: {
    mode: 'long_term', preset: 'private_assistant', recent_messages_limit: 12,
    context_token_budget: 1800, compress_trigger_messages: 24, summary_enabled: true,
    user_profile_enabled: true, episodic_enabled: true, session_recall_enabled: true, memory_item_max: 200,
  },
  prompt_variables: [], skills: [], mcp_tools: [], javascript_tools: [],
  tool_policy: { mode: 'auto', max_rounds: 3, timeout_seconds: 30, result_max_chars: 12000, allow_write_tools: false, failure_mode: 'continue' },
  retrieval: {
    enabled: true, top_k: 6, rank_method: 'hybrid', score_threshold: 0,
    rerank_enabled: false, rerank_k: 30, rerank_required: false,
  },
  reply_examples: [],
})
const config = reactive({ ...defaultConfig(), ...(props.agent.model_config || {}) })
config.retrieval = { ...defaultConfig().retrieval, ...(props.agent.model_config?.retrieval || {}) }
config.memory = { ...defaultConfig().memory, ...(props.agent.model_config?.memory || {}) }
config.prompt_variables = Array.isArray(props.agent.model_config?.prompt_variables) ? props.agent.model_config.prompt_variables.map((item) => ({ ...item })) : []
config.skills = Array.isArray(props.agent.model_config?.skills) ? props.agent.model_config.skills.map((item) => ({ ...item })) : []
config.mcp_tools = Array.isArray(props.agent.model_config?.mcp_tools) ? props.agent.model_config.mcp_tools.map((item) => ({ ...item })) : []
config.javascript_tools = Array.isArray(props.agent.model_config?.javascript_tools) ? props.agent.model_config.javascript_tools.map((item) => ({ ...item })) : []
config.tool_policy = { ...defaultConfig().tool_policy, ...(props.agent.model_config?.tool_policy || {}) }
config.reply_examples = Array.isArray(props.agent.model_config?.reply_examples)
  ? props.agent.model_config.reply_examples.map((item) => ({ user: item.user || '', assistant: item.assistant || '' }))
  : []
const form = reactive({
  name: props.agent.name || '', description: props.agent.description || '', instructions: props.agent.instructions || '',
  visibility: props.agent.visibility || 'PRIVATE', workflow_id: props.agent.workflow_id || '',
  knowledge_base_ids: [...(props.agent.knowledge_base_ids || [])],
})
const ui = reactive({ tab: 'settings', saving: false, publishing: false, loadingModels: false, running: false, error: '' })
const models = ref([])
const debugInput = ref('')
const debugMessages = ref([])
const debugSessionId = ref('')
const debugToolEvents = ref([])

const selectedModel = computed(() => models.value.find((item) => item.endpoint_id === config.model_endpoint_id))
const selectedSkillIds = computed({
  get: () => config.skills.map((item) => item.skill_id),
  set: (ids) => { config.skills = ids.map((id) => {
    const item = props.skills.find((skill) => skill.skill_id === id)
    return { skill_id: id, name: item?.name || '', version: item?.latest_version_no || 1 }
  }) },
})
const selectedMcpToolIds = computed({
  get: () => config.mcp_tools.map((item) => `${item.connection_id}::${item.tool_name}`),
  set: (ids) => { config.mcp_tools = ids.map((value) => {
    const [connectionId, toolName] = value.split('::')
    return { connection_id: connectionId, tool_name: toolName }
  }) },
})
const mcpToolOptions = computed(() => props.mcpConnections.flatMap((connection) => (connection.tools || []).map((tool) => ({
  id: `${connection.connection_id}::${tool.name}`, label: `${connection.name} · ${tool.title || tool.name}`,
  readOnly: tool.annotations?.readOnlyHint === true, destructive: tool.annotations?.destructiveHint === true,
}))))
const selectedJavaScriptToolIds = computed({get:()=>config.javascript_tools.map(x=>x.tool_id),set:(ids)=>{config.javascript_tools=ids.map(id=>{const item=props.javascriptTools.find(x=>x.tool_id===id);return{tool_id:id,version:item?.latest_version_no||1}})}})
const publishLabel = computed(() => props.agent.latest_version_no > 0 ? `发布 v${props.agent.latest_version_no + 1}` : '发布首个版本')

function headers(json = true) {
  return { Authorization: `Bearer ${props.token}`, ...(json ? { 'Content-Type': 'application/json' } : {}) }
}

async function jsonRequest(base, path, options = {}) {
  const response = await fetch(`${base.replace(/\/$/, '')}${path}`, {
    method: options.method || 'GET', headers: headers(Boolean(options.body)),
    body: options.body ? JSON.stringify(options.body) : undefined,
  })
  const text = await response.text()
  let data = {}
  try { data = text ? JSON.parse(text) : {} } catch { data = {} }
  if (!response.ok) throw new Error(response.status === 401 ? '登录状态已失效' : response.status >= 500 ? '服务暂时不可用，请稍后再试' : data.detail || '请求未能完成')
  return data
}

function runtimeConfig() {
  return {
    role: String(config.role || '').trim(), response_style: String(config.response_style || '').trim(),
    model_endpoint_id: config.model_endpoint_id || '', temperature: Number(config.temperature),
    top_p: Number(config.top_p), max_tokens: Number(config.max_tokens),
    prohibited_rules: String(config.prohibited_rules || '').trim(),
    memory: { ...config.memory },
    prompt_variables: config.prompt_variables.map((item) => ({ ...item })),
    skills: config.skills.map((item) => ({ skill_id: item.skill_id, name: item.name, version: Number(item.version) })),
    mcp_tools: config.mcp_tools.map((item) => ({ connection_id: item.connection_id, tool_name: item.tool_name })),
    javascript_tools: config.javascript_tools.map((item) => ({ tool_id:item.tool_id, version:Number(item.version) })),
    tool_policy: { ...config.tool_policy },
    retrieval: {
      enabled: Boolean(config.retrieval.enabled), top_k: Number(config.retrieval.top_k),
      rank_method: config.retrieval.rank_method, score_threshold: Number(config.retrieval.score_threshold),
      rerank_enabled: Boolean(config.retrieval.rerank_enabled),
      rerank_k: Number(config.retrieval.rerank_k),
      rerank_required: Boolean(config.retrieval.rerank_required),
    },
    reply_examples: config.reply_examples
      .map((item) => ({ user: String(item.user || '').trim(), assistant: String(item.assistant || '').trim() }))
      .filter((item) => item.user || item.assistant),
  }
}

function applyMemoryPreset(preset) {
  const presets = {
    private_assistant: { mode: 'long_term', recent_messages_limit: 16, context_token_budget: 2400, compress_trigger_messages: 24, summary_enabled: true, user_profile_enabled: true, episodic_enabled: true, session_recall_enabled: true, memory_item_max: 500 },
    customer_service: { mode: 'short_term', recent_messages_limit: 8, context_token_budget: 1200, compress_trigger_messages: 24, summary_enabled: false, user_profile_enabled: false, episodic_enabled: false, session_recall_enabled: false, memory_item_max: 100 },
    consulting: { mode: 'long_term', recent_messages_limit: 12, context_token_budget: 2200, compress_trigger_messages: 20, summary_enabled: true, user_profile_enabled: false, episodic_enabled: true, session_recall_enabled: true, memory_item_max: 300 },
    stateless: { mode: 'none', recent_messages_limit: 1, context_token_budget: 400, compress_trigger_messages: 24, summary_enabled: false, user_profile_enabled: false, episodic_enabled: false, session_recall_enabled: false, memory_item_max: 20 },
  }
  Object.assign(config.memory, presets[preset], { preset })
  resetDebugConversation()
}

function toggleDateTimeVariable(enabled) {
  config.prompt_variables = enabled ? [{ key: 'current_datetime', enabled: true, calendar: 'both', timezone: 'Asia/Shanghai' }] : []
}

function insertDateTime(target) {
  const token = '{{current_datetime}}'
  if (target === 'rules') config.prohibited_rules = `${config.prohibited_rules || ''}${config.prohibited_rules ? '\n' : ''}${token}`
  else form.instructions = `${form.instructions || ''}${form.instructions ? '\n' : ''}${token}`
}

function resetDebugConversation() {
  debugSessionId.value = ''
  debugMessages.value = []
  debugToolEvents.value = []
}

function debugSkillConfig() {
  return config.skills.map((selected) => {
    const source = props.skills.find((item) => item.skill_id === selected.skill_id)
    const definition = source?.latest_definition || {}
    return {
      ...selected,
      description: definition.description || source?.description || '',
      instructions: definition.instructions || '',
      input_schema: definition.input_schema || { type: 'object', properties: {} },
      output_schema: definition.output_schema || { type: 'object', properties: {} },
      max_tokens: Number(definition.max_tokens || 1200),
    }
  })
}

function debugMcpConfig() {
  return config.mcp_tools.map((selected) => {
    const connection = props.mcpConnections.find((item) => item.connection_id === selected.connection_id)
    const tool = connection?.tools?.find((item) => item.name === selected.tool_name) || {}
    return {
      ...selected, connection_name: connection?.name || '', endpoint_url: connection?.endpoint_url || '',
      auth: { type: connection?.auth_type || 'NONE', secret_ref: connection?.secret_ref || '' },
      title: tool.title || selected.tool_name, description: tool.description || '',
      input_schema: tool.inputSchema || { type: 'object' }, annotations: tool.annotations || {},
    }
  })
}

function debugJavaScriptConfig(){return config.javascript_tools.map(selected=>{const source=props.javascriptTools.find(x=>x.tool_id===selected.tool_id);return{...selected,name:source?.name||'',description:source?.description||'',definition:source?.latest_definition||{}}})}

async function loadModels() {
  ui.loadingModels = true
  try {
    const data = await jsonRequest(props.chatBaseUrl, '/api/v1/chat/models')
    models.value = data.items || []
    if (!config.model_endpoint_id && models.value.length) {
      config.model_endpoint_id = (models.value.find((item) => item.is_default) || models.value[0]).endpoint_id
    }
  } catch (error) { ui.error = error.message } finally { ui.loadingModels = false }
}

async function save() {
  ui.saving = true; ui.error = ''
  try {
    const data = await jsonRequest(props.baseUrl, `/api/v1/agents/${props.agent.agent_id}`, {
      method: 'PUT',
      body: { ...form, model_config: runtimeConfig(), expected_revision: props.agent.revision },
    })
    emit('saved', data.item)
    emit('notify', { type: 'success', title: '智能体配置已保存', message: '调试与后续发布将使用这组实例参数。' })
  } catch (error) { ui.error = error.message } finally { ui.saving = false }
}

async function publish() {
  ui.publishing = true; ui.error = ''
  try {
    await save()
    const data = await jsonRequest(props.baseUrl, `/api/v1/agents/${props.agent.agent_id}/publish`, { method: 'POST' })
    emit('published', data.item)
    emit('notify', { type: 'success', title: '智能体版本已发布', message: '当前配置已生成不可变版本快照。' })
  } catch (error) { ui.error = error.message } finally { ui.publishing = false }
}

function addExample() {
  if (config.reply_examples.length < 8) config.reply_examples.push({ user: '', assistant: '' })
}

function consumeSseBlock(block, assistant) {
  const line = block.split('\n').find((item) => item.startsWith('data:'))
  if (!line) return
  try {
    const event = JSON.parse(line.slice(5).trim())
    if (event.event === 'token') assistant.content += event.delta || ''
    if (event.event === 'agent_action' || (event.event === 'trace' && /Tool|Skill|MCP/i.test(`${event.title || ''} ${event.detail || ''}`))) {
      debugToolEvents.value.push({
        title: event.title || '工具调用', detail: event.detail || '', info: event.info || {},
      })
      if (debugToolEvents.value.length > 20) debugToolEvents.value.shift()
    }
    if (event.event === 'error') throw new Error(event.message || '调试运行失败')
  } catch (error) {
    if (error instanceof SyntaxError) return
    throw error
  }
}

async function runDebug() {
  const message = debugInput.value.trim()
  if (!message || ui.running) return
  ui.running = true; ui.error = ''
  debugToolEvents.value = []
  debugMessages.value.push({ role: 'user', content: message })
  const assistant = reactive({ role: 'assistant', content: '' })
  debugMessages.value.push(assistant)
  debugInput.value = ''
  try {
    const kbId = config.retrieval.enabled ? (form.knowledge_base_ids[0] || null) : null
    if (!debugSessionId.value) {
      const sessionData = await jsonRequest(props.chatBaseUrl, '/api/v1/chat/sessions', {
        method: 'POST', body: { title: `${form.name} · 调试`, kb_id: kbId },
      })
      debugSessionId.value = sessionData.item?.session_id || ''
    }
    const sessionId = debugSessionId.value
    const response = await fetch(`${props.chatBaseUrl.replace(/\/$/, '')}/api/v1/chat/sessions/${sessionId}/messages/stream`, {
      method: 'POST', headers: headers(),
      body: JSON.stringify({
        message, use_kb: Boolean(kbId), use_agent: false, kb_id: kbId,
        top_k: Number(config.retrieval.top_k), rank_method: config.retrieval.rank_method,
        rerank_enabled: Boolean(config.retrieval.rerank_enabled),
        rerank_k: Number(config.retrieval.rerank_k),
        rerank_required: Boolean(config.retrieval.rerank_required),
        pipeline_id: 'default-chat-v1', metadata: { source: 'agent-workbench', agent_id: props.agent.agent_id },
        agent_config: { agent_id: props.agent.agent_id, instructions: form.instructions, ...runtimeConfig(), skills: debugSkillConfig(), mcp_tools: debugMcpConfig(), javascript_tools: debugJavaScriptConfig() },
      }),
    })
    if (!response.ok || !response.body) throw new Error(response.status >= 500 ? '调试服务暂时不可用' : '无法启动调试会话')
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''
    while (true) {
      const { value, done } = await reader.read(); if (done) break
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
      let split = buffer.indexOf('\n\n')
      while (split >= 0) {
        consumeSseBlock(buffer.slice(0, split), assistant); buffer = buffer.slice(split + 2); split = buffer.indexOf('\n\n')
      }
    }
    if (buffer.trim()) consumeSseBlock(buffer, assistant)
  } catch (error) {
    assistant.content = assistant.content || '本次调试未能完成。'
    ui.error = error.message
  } finally { ui.running = false }
}

onMounted(loadModels)
</script>

<template>
  <section class="agent-workbench">
    <header class="workbench-header">
      <button class="back-button" type="button" @click="emit('close')">← 返回智能体</button>
      <div class="workbench-title"><span class="agent-orb">✦</span><div><h1>{{ form.name }}</h1><p>独立配置、调试并发布这个智能体实例</p></div></div>
      <div class="header-actions"><span class="version-chip">{{ agent.status === 'PUBLISHED' ? `已发布 v${agent.latest_version_no}` : '草稿' }}</span><button class="secondary-button" :disabled="ui.saving" @click="save">{{ ui.saving ? '保存中…' : '保存' }}</button><button class="primary-button" :disabled="ui.publishing" @click="publish">{{ ui.publishing ? '发布中…' : publishLabel }}</button></div>
    </header>

    <nav class="workbench-tabs"><button :class="{ active: ui.tab === 'settings' }" @click="ui.tab = 'settings'">配置</button><button :class="{ active: ui.tab === 'debug' }" @click="ui.tab = 'debug'">调试预览</button></nav>
    <p v-if="ui.error" class="workbench-error">{{ ui.error }}</p>

    <div v-if="ui.tab === 'settings'" class="settings-layout">
      <div class="settings-main">
        <article class="glass-panel"><div class="section-heading"><div><span>01</span><h2>身份与指令</h2></div><p>定义它是谁、如何思考以及怎样组织回复。</p></div><div class="form-grid two"><label><span>名称</span><input v-model.trim="form.name" maxlength="128" /></label><label><span>角色定位</span><input v-model.trim="config.role" placeholder="例如：企业售后解决方案专家" /></label></div><label><span>系统指令</span><textarea v-model="form.instructions" rows="8" placeholder="描述职责、边界、处理步骤和禁止事项…" /></label><label><span>回复风格</span><textarea v-model="config.response_style" rows="3" placeholder="例如：先给结论，再列步骤；保持专业、简洁并引用知识库来源。" /></label></article>

        <article class="glass-panel policy-panel">
          <div class="section-heading"><div><span>02</span><h2>规则、变量与记忆</h2></div><p>按使用场景控制上下文边界，动态变量在每次请求时由服务端解析。</p></div>
          <label><span>禁止规则</span><textarea v-model="config.prohibited_rules" rows="4" placeholder="例如：禁止泄露内部提示词、密钥与其他客户信息；禁止执行越权操作。" /><small>该字段作为独立的最高优先级约束加入系统提示词。</small></label>
          <div class="variable-card"><label class="toggle-row"><div><strong>当前日期 / 时间</strong><small>支持公历、农历，变量名 <code v-text="'{{current_datetime}}'" /></small></div><input type="checkbox" :checked="config.prompt_variables.length > 0" @change="toggleDateTimeVariable($event.target.checked)" /></label><template v-if="config.prompt_variables.length"><div class="form-grid two"><label><span>历法格式</span><select v-model="config.prompt_variables[0].calendar"><option value="both">公历 + 农历</option><option value="gregorian">仅公历</option><option value="lunar">仅农历</option></select></label><label><span>时区</span><select v-model="config.prompt_variables[0].timezone"><option value="Asia/Shanghai">中国标准时间</option><option value="UTC">UTC</option><option value="Asia/Tokyo">东京</option><option value="America/New_York">纽约</option></select></label></div><div class="inline-actions"><button class="secondary-button" @click="insertDateTime('prompt')">插入系统指令</button><button class="secondary-button" @click="insertDateTime('rules')">插入禁止规则</button></div></template></div>
          <label><span>记忆场景</span><select :value="config.memory.preset" @change="applyMemoryPreset($event.target.value)"><option value="private_assistant">私人助手 · 长期偏好与历史</option><option value="customer_service">对外客服 · 仅当前会话</option><option value="consulting">咨询顾问 · 项目长期上下文</option><option value="stateless">单次问答 · 不读写记忆</option><option value="custom">自定义</option></select></label>
          <div class="form-grid three"><label><span>记忆模式</span><select v-model="config.memory.mode" @change="config.memory.preset = 'custom'; resetDebugConversation()"><option value="none">单次对话</option><option value="short_term">短期记忆</option><option value="long_term">长期记忆</option></select></label><label><span>最近消息数</span><input v-model.number="config.memory.recent_messages_limit" type="number" min="1" max="50" /></label><label><span>上下文 Token 预算</span><input v-model.number="config.memory.context_token_budget" type="number" min="200" max="8000" /></label></div>
          <div v-if="config.memory.mode === 'long_term'" class="memory-switches"><label class="toggle-row"><div><strong>自动摘要</strong><small>压缩较长历史</small></div><input v-model="config.memory.summary_enabled" type="checkbox" /></label><label class="toggle-row"><div><strong>用户画像</strong><small>跨会话保留稳定偏好</small></div><input v-model="config.memory.user_profile_enabled" type="checkbox" /></label><label class="toggle-row"><div><strong>情节记忆</strong><small>保留重要事件与结论</small></div><input v-model="config.memory.episodic_enabled" type="checkbox" /></label><label class="toggle-row"><div><strong>历史语义召回</strong><small>从当前用户会话中检索相关片段</small></div><input v-model="config.memory.session_recall_enabled" type="checkbox" /></label></div>
        </article>

        <article class="glass-panel"><div class="section-heading"><div><span>03</span><h2>模型与生成</h2></div><p>模型来自平台已挂载且当前空间可用的对话端点。</p></div><label><span>主模型</span><select v-model="config.model_endpoint_id" :disabled="ui.loadingModels"><option value="">跟随平台默认模型</option><option v-for="item in models" :key="item.endpoint_id" :value="item.endpoint_id">{{ item.name }} · {{ item.model }}{{ item.is_default ? '（默认）' : '' }}</option></select><small v-if="selectedModel">{{ selectedModel.provider }} · {{ selectedModel.model }}</small></label><div class="parameter-grid"><label><span>Temperature <b>{{ Number(config.temperature).toFixed(1) }}</b></span><input v-model.number="config.temperature" type="range" min="0" max="2" step="0.1" /></label><label><span>Top P <b>{{ Number(config.top_p).toFixed(2) }}</b></span><input v-model.number="config.top_p" type="range" min="0.05" max="1" step="0.05" /></label><label><span>最大输出 Token</span><input v-model.number="config.max_tokens" type="number" min="64" max="32768" /></label></div></article>

        <article class="glass-panel"><div class="section-heading"><div><span>04</span><h2>回复示例</h2></div><p>用少量高质量示例稳定语气与输出结构。</p></div><div v-for="(example, index) in config.reply_examples" :key="index" class="example-card"><div><span>示例 {{ index + 1 }}</span><button @click="config.reply_examples.splice(index, 1)">移除</button></div><label><span>用户</span><textarea v-model="example.user" rows="2" /></label><label><span>智能体</span><textarea v-model="example.assistant" rows="3" /></label></div><button class="add-example" :disabled="config.reply_examples.length >= 8" @click="addExample">＋ 添加回复示例</button></article>
      </div>

      <aside class="settings-side">
        <article class="glass-panel sticky-panel">
          <div class="section-heading compact"><div><span>05</span><h2>知识与工具</h2></div></div>
          <label class="toggle-row"><div><strong>启用知识库检索</strong><small>调试时使用已选择的第一个知识库</small></div><input v-model="config.retrieval.enabled" type="checkbox" /></label>
          <label><span>关联知识库</span><select v-model="form.knowledge_base_ids" multiple size="5"><option v-for="item in knowledgeBases" :key="item.kb_id" :value="item.kb_id">{{ item.name }}</option></select></label>
          <div class="form-grid two"><label><span>Top K</span><input v-model.number="config.retrieval.top_k" type="number" min="1" max="50" /></label><label><span>排序策略</span><select v-model="config.retrieval.rank_method"><option value="vector">向量排序</option><option value="hybrid">混合检索</option><option value="rrf">RRF 融合</option><option value="keyword">关键词检索</option></select></label></div>
          <label><span>相关度阈值</span><input v-model.number="config.retrieval.score_threshold" type="number" min="0" max="1" step="0.05" /></label>
          <label class="toggle-row"><div><strong>模型重排</strong><small>需要在主 API 池配置 rerank 端点；不可用时自动保留融合顺序。</small></div><input v-model="config.retrieval.rerank_enabled" type="checkbox" /></label>
          <div v-if="config.retrieval.rerank_enabled" class="form-grid two"><label><span>重排候选数</span><input v-model.number="config.retrieval.rerank_k" type="number" min="1" max="100" /></label><label class="toggle-row"><div><strong>重排失败即终止</strong><small>适用于必须使用指定模型的严格场景。</small></div><input v-model="config.retrieval.rerank_required" type="checkbox" /></label></div>
          <div class="divider"></div>
          <label><span>可用 Skills</span><select v-model="selectedSkillIds" multiple size="4"><option v-for="item in skills.filter(x => x.latest_version_no > 0)" :key="item.skill_id" :value="item.skill_id">{{ item.name }} · v{{ item.latest_version_no }}</option></select><small>Skill 作为受控子任务工具调用，发布时锁定版本、Schema 与指令快照。</small></label>
          <label><span>MCP 工具</span><select v-model="selectedMcpToolIds" multiple size="5"><option v-for="item in mcpToolOptions" :key="item.id" :value="item.id">{{ item.label }}{{ item.destructive ? ' · 高风险' : item.readOnly ? ' · 只读' : ' · 待确认权限' }}</option></select><small>仅展示已发现的工具。密钥只保留引用，不会进入模型上下文。</small></label>
          <label><span>JavaScript 工具</span><select v-model="selectedJavaScriptToolIds" multiple size="4"><option v-for="item in javascriptTools.filter(x=>x.latest_version_no>0&&x.latest_definition?.allow_agent)" :key="item.tool_id" :value="item.tool_id">{{ item.name }} · v{{ item.latest_version_no }}</option></select><small>仅显示已发布且分配给 AI 的纯沙箱函数。</small></label>
          <label><span>调用方式</span><select v-model="config.tool_policy.mode"><option value="auto">模型按需调用</option><option value="required">首轮必须调用工具</option><option value="none">禁用工具调用</option></select></label>
          <div class="form-grid two"><label><span>最多调用轮次</span><input v-model.number="config.tool_policy.max_rounds" type="number" min="1" max="8" /></label><label><span>单次超时（秒）</span><input v-model.number="config.tool_policy.timeout_seconds" type="number" min="1" max="120" /></label></div>
          <label><span>失败策略</span><select v-model="config.tool_policy.failure_mode"><option value="continue">记录失败并继续回答</option><option value="fail">立即终止本次运行</option></select></label>
          <label class="toggle-row risk-toggle"><div><strong>允许写入型 MCP 工具</strong><small>可能创建、修改、发送或删除外部数据；默认关闭。</small></div><input v-model="config.tool_policy.allow_write_tools" type="checkbox" /></label>
          <label><span>预留 Workflow 关联</span><select v-model="form.workflow_id"><option value="">暂不关联</option><option v-for="item in workflows.filter(x => x.latest_version_no > 0)" :key="item.workflow_id" :value="item.workflow_id">{{ item.name }} · v{{ item.latest_version_no }}</option></select><small>Agent 当前独立运行；后续将 Workflow 接入相同的工具执行协议。</small></label>
          <label><span>可见范围</span><select v-model="form.visibility"><option value="PRIVATE">仅自己可见</option><option value="WORKSPACE">工作空间可见</option></select></label>
        </article>
      </aside>
    </div>

    <div v-else class="debug-layout">
      <article class="debug-chat glass-panel"><div class="debug-head"><div><h2>实时调试</h2><p>使用当前表单参数运行，不必先发布版本。</p></div><span class="model-badge">{{ selectedModel?.model || '平台默认模型' }}</span></div><div class="debug-thread"><div v-if="!debugMessages.length" class="debug-empty"><span>✦</span><h3>测试你的智能体</h3><p>发送一条真实问题，检查角色、知识检索和回复风格是否符合预期。</p></div><div v-for="(message, index) in debugMessages" :key="index" class="debug-message" :class="message.role"><span>{{ message.role === 'assistant' ? '✦' : '你' }}</span><p>{{ message.content || '正在思考…' }}</p></div></div><div class="debug-composer"><textarea v-model="debugInput" rows="3" placeholder="输入测试问题…" @keydown.enter.exact.prevent="runDebug" /><button :disabled="!debugInput.trim() || ui.running" @click="runDebug">{{ ui.running ? '运行中…' : '发送' }}</button></div></article>
      <aside class="debug-inspector glass-panel"><h2>本次运行参数</h2><dl><div><dt>角色</dt><dd>{{ config.role || '未设置' }}</dd></div><div><dt>知识库</dt><dd>{{ form.knowledge_base_ids.length }} 个</dd></div><div><dt>Skills / MCP</dt><dd>{{ config.skills.length }} / {{ config.mcp_tools.length }}</dd></div><div><dt>调用策略</dt><dd>{{ config.tool_policy.mode }} · 最多 {{ config.tool_policy.max_rounds }} 轮</dd></div><div><dt>检索</dt><dd>Top {{ config.retrieval.top_k }} · {{ config.retrieval.rank_method }}</dd></div><div><dt>Temperature</dt><dd>{{ config.temperature }}</dd></div><div><dt>最大输出</dt><dd>{{ config.max_tokens }} tokens</dd></div><div><dt>Workflow 关联</dt><dd>{{ form.workflow_id ? '已关联（预留）' : '未关联' }}</dd></div></dl><section v-if="debugToolEvents.length" class="tool-events"><h3>工具调用轨迹</h3><article v-for="(event, index) in debugToolEvents" :key="index"><strong>{{ event.title }}</strong><small>{{ event.detail }}</small></article></section><button class="secondary-button wide" @click="ui.tab = 'settings'">返回调整参数</button></aside>
    </div>
  </section>
</template>

<style scoped>
.agent-workbench{width:100%;min-height:100%;padding:28px 34px 52px;color:var(--workspace-text,#f5f5f7);background:radial-gradient(circle at 72% -10%,rgba(57,135,255,.12),transparent 34%)}
.workbench-header{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:18px;align-items:center}.back-button{grid-column:1/-1;width:max-content;border:0;background:transparent;color:var(--workspace-muted,#a1a1aa);font-size:13px;cursor:pointer}.workbench-title{display:flex;align-items:center;gap:14px;min-width:0}.workbench-title>div{min-width:0}.workbench-title h1{margin:0;max-width:100%;overflow-wrap:anywhere;font-size:28px;letter-spacing:-.04em}.workbench-title p{margin:5px 0 0;overflow-wrap:anywhere;color:var(--workspace-muted,#a1a1aa);font-size:13px}.agent-orb{display:grid;place-items:center;flex:0 0 48px;width:48px;height:48px;border-radius:16px;color:white;background:linear-gradient(145deg,#0a84ff,#745cff);box-shadow:0 12px 32px rgba(10,132,255,.28)}.header-actions{display:flex;align-items:center;gap:9px}.version-chip,.model-badge{padding:7px 10px;border:1px solid rgba(127,127,127,.2);border-radius:999px;color:var(--workspace-muted,#a1a1aa);font-size:11px}.primary-button,.secondary-button,.add-example{min-height:38px;padding:0 15px;border-radius:11px;border:1px solid rgba(127,127,127,.2);font-weight:650;cursor:pointer}.primary-button{border-color:transparent;color:white;background:#0a84ff}.secondary-button,.add-example{color:var(--workspace-text,#f5f5f7);background:rgba(127,127,127,.09)}button:disabled{opacity:.5;cursor:not-allowed}.workbench-tabs{display:flex;gap:4px;margin:26px 0 18px;padding:4px;width:max-content;border-radius:12px;background:rgba(127,127,127,.1)}.workbench-tabs button{padding:8px 18px;border:0;border-radius:9px;color:var(--workspace-muted,#a1a1aa);background:transparent;cursor:pointer}.workbench-tabs button.active{color:var(--workspace-text,#f5f5f7);background:rgba(127,127,127,.18);box-shadow:0 1px 4px rgba(0,0,0,.12)}.workbench-error{padding:12px 14px;border:1px solid rgba(255,69,58,.28);border-radius:12px;color:#ff6961;background:rgba(255,69,58,.08)}
.settings-layout,.debug-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(280px,360px);gap:18px;align-items:start}.settings-main{display:grid;gap:18px}.glass-panel{border:1px solid rgba(127,127,127,.18);border-radius:22px;padding:22px;background:color-mix(in srgb,var(--workspace-panel,#1c1c1e) 88%,transparent);box-shadow:0 16px 46px rgba(0,0,0,.12);backdrop-filter:blur(24px)}.section-heading{display:flex;align-items:end;justify-content:space-between;gap:18px;margin-bottom:20px}.section-heading>div{display:flex;align-items:center;gap:10px}.section-heading span{color:#0a84ff;font-size:11px;font-weight:750}.section-heading h2{margin:0;font-size:18px;letter-spacing:-.02em}.section-heading p{margin:0;color:var(--workspace-muted,#a1a1aa);font-size:12px}.section-heading.compact{margin-bottom:14px}.sticky-panel{position:sticky;top:22px}.form-grid{display:grid;gap:13px}.form-grid.two{grid-template-columns:repeat(2,minmax(0,1fr))}label{display:grid;gap:7px;margin-top:14px;color:var(--workspace-muted,#a1a1aa);font-size:12px}input,textarea,select{width:100%;box-sizing:border-box;border:1px solid rgba(127,127,127,.22);border-radius:12px;padding:11px 12px;color:var(--workspace-text,#f5f5f7);background:rgba(127,127,127,.09);font:inherit;outline:none}textarea{resize:vertical;line-height:1.5}input:focus,textarea:focus,select:focus{border-color:#0a84ff;box-shadow:0 0 0 3px rgba(10,132,255,.13)}select[multiple]{min-height:130px}small{color:var(--workspace-muted,#a1a1aa);line-height:1.45}.parameter-grid{display:grid;grid-template-columns:1fr 1fr .8fr;gap:14px}.parameter-grid label span{display:flex;justify-content:space-between}.parameter-grid b{color:#0a84ff}.toggle-row{display:flex;align-items:center;justify-content:space-between;margin:0;padding:12px;border-radius:14px;background:rgba(127,127,127,.07)}.toggle-row div{display:grid;gap:3px}.toggle-row strong{color:var(--workspace-text,#f5f5f7);font-size:13px}.toggle-row input{width:18px}.divider{height:1px;margin:20px 0 6px;background:rgba(127,127,127,.15)}.example-card{margin-top:12px;padding:14px;border:1px solid rgba(127,127,127,.15);border-radius:16px;background:rgba(127,127,127,.045)}.example-card>div{display:flex;justify-content:space-between;color:var(--workspace-muted,#a1a1aa);font-size:12px}.example-card button{border:0;color:#ff6961;background:transparent;cursor:pointer}.add-example{margin-top:14px}.wide{width:100%}
.agent-workbench textarea{resize:none}.form-grid.three{grid-template-columns:repeat(3,minmax(0,1fr))}.variable-card{margin-top:16px;padding:12px;border:1px solid rgba(127,127,127,.15);border-radius:16px;background:rgba(127,127,127,.04)}.inline-actions{display:flex;align-items:center;gap:8px;margin-top:10px}.memory-switches{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin-top:14px}.risk-toggle{margin-top:14px;border:1px solid rgba(255,159,10,.18);background:rgba(255,159,10,.06)}
:global(html[data-theme="dark"] .agent-workbench select){color-scheme:dark;border-color:rgba(255,255,255,.14);color:#f5f5f7;background-color:#2b2b2e}
:global(html[data-theme="dark"] .agent-workbench select option),:global(html[data-theme="dark"] .agent-workbench select optgroup){color:#f5f5f7;background-color:#242426}
:global(html[data-theme="dark"] .agent-workbench select option:checked){color:#fff;background-color:#0a67c7}
:global(html[data-theme="light"] .agent-workbench select){color-scheme:light;color:#1d1d1f;background-color:rgba(255,255,255,.88)}
:global(html[data-theme="light"] .agent-workbench select option),:global(html[data-theme="light"] .agent-workbench select optgroup){color:#1d1d1f;background-color:#fff}
.debug-chat{display:flex;flex-direction:column;min-height:620px}.debug-head{display:flex;justify-content:space-between;align-items:start}.debug-head h2,.debug-inspector h2{margin:0;font-size:18px}.debug-head p{margin:6px 0 0;color:var(--workspace-muted,#a1a1aa);font-size:12px}.debug-thread{display:flex;flex:1;flex-direction:column;gap:16px;padding:28px 0;overflow:auto}.debug-empty{margin:auto;text-align:center;color:var(--workspace-muted,#a1a1aa)}.debug-empty>span{display:grid;place-items:center;width:54px;height:54px;margin:0 auto 14px;border-radius:18px;color:#0a84ff;background:rgba(10,132,255,.12)}.debug-empty h3{margin:0;color:var(--workspace-text,#f5f5f7)}.debug-empty p{max-width:420px}.debug-message{display:flex;gap:10px;max-width:82%}.debug-message>span{display:grid;place-items:center;flex:0 0 30px;height:30px;border-radius:10px;background:rgba(10,132,255,.13);color:#0a84ff;font-size:11px}.debug-message p{margin:0;padding:11px 13px;border-radius:15px;background:rgba(127,127,127,.09);white-space:pre-wrap;line-height:1.55}.debug-message.user{align-self:flex-end;flex-direction:row-reverse}.debug-message.user p{color:white;background:#0a84ff}.debug-composer{display:flex;align-items:end;gap:10px;padding:10px;border:1px solid rgba(127,127,127,.2);border-radius:17px;background:rgba(127,127,127,.07)}.debug-composer textarea{margin:0;padding:5px;border:0;background:transparent;box-shadow:none}.debug-composer button{min-width:70px;height:38px;border:0;border-radius:11px;color:white;background:#0a84ff;cursor:pointer}.debug-inspector dl{display:grid;gap:0;margin:16px 0 22px}.debug-inspector dl div{display:flex;justify-content:space-between;gap:12px;padding:12px 0;border-bottom:1px solid rgba(127,127,127,.12)}dt{color:var(--workspace-muted,#a1a1aa);font-size:12px}dd{margin:0;text-align:right;font-size:12px}
.tool-events{display:grid;gap:8px;margin:0 0 18px}.tool-events h3{margin:0 0 3px;font-size:13px}.tool-events article{display:grid;gap:4px;padding:10px;border:1px solid rgba(10,132,255,.18);border-radius:12px;background:rgba(10,132,255,.06)}.tool-events strong{font-size:12px}.tool-events small{overflow-wrap:anywhere}
@media(max-width:1000px){.agent-workbench{padding:22px 18px 42px}.settings-layout,.debug-layout{grid-template-columns:1fr}.sticky-panel{position:static}.header-actions{grid-column:1/-1}.parameter-grid{grid-template-columns:1fr 1fr}}
@media(max-width:640px){.workbench-header{display:flex;align-items:stretch;flex-direction:column}.header-actions{overflow:auto}.version-chip{display:none}.form-grid.two,.form-grid.three,.parameter-grid,.memory-switches{grid-template-columns:1fr}.glass-panel{padding:17px;border-radius:18px}.section-heading{align-items:start;flex-direction:column}.settings-layout{gap:14px}.agent-workbench{padding:18px 12px 32px}.workbench-title h1{font-size:23px}}
</style>
