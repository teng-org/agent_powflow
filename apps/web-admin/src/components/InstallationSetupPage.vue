<script setup>
import { computed, onMounted, reactive } from 'vue'

const props = defineProps({
  fetchJson: { type: Function, required: true },
})
const emit = defineEmits(['open-ai-pool', 'open-storage'])

const state = reactive({ loading: false, saving: false, completing: false, error: '', message: '', item: null })
const form = reactive({ site_name: 'Powflow', public_console_url: '', public_api_url: '' })

const checks = computed(() => [
  { name: 'PostgreSQL', ready: !!state.item?.database_ready, detail: '租户、用户、资产与审计权威存储' },
  { name: 'Execution Runtime', ready: !!state.item?.execution_ready, detail: '文档、Workflow 和 Agent 执行服务' },
  { name: '存储链路', ready: !!state.item?.storage_ready, detail: '数据库与执行平面基础连接' },
  { name: 'LLM Provider', ready: (state.item?.llm_endpoint_count || 0) > 0, detail: '对话与 Workflow 推理模型' },
  { name: 'Embedding Provider', ready: (state.item?.embedding_endpoint_count || 0) > 0, detail: '知识库索引和检索向量模型' },
])
const readyCount = computed(() => checks.value.filter((item) => item.ready).length)
const canComplete = computed(() => readyCount.value === checks.value.length && form.site_name.trim())

function syncForm(item) {
  form.site_name = item?.site_name || 'Powflow'
  form.public_console_url = item?.public_console_url || ''
  form.public_api_url = item?.public_api_url || ''
}

async function run(flag, action, success = '') {
  state.error = ''
  state.message = ''
  state[flag] = true
  try {
    const data = await action()
    state.item = data?.item || null
    if (success) state.message = success
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error)
  } finally {
    state[flag] = false
  }
}

async function load() {
  await run('loading', () => props.fetchJson('control', '/api/v1/admin/setup/check', { method: 'POST' }))
  syncForm(state.item)
}

async function saveSite() {
  await run('saving', () => props.fetchJson('control', '/api/v1/admin/setup/site', { method: 'PUT', body: form }), '站点信息已保存')
}

async function complete() {
  await run('completing', () => props.fetchJson('control', '/api/v1/admin/setup/complete', { method: 'POST' }), '平台初始化已完成')
}

onMounted(load)
</script>

<template>
  <section class="setup-page">
    <header class="setup-hero">
      <div><span class="setup-eyebrow">Deployment Assistant</span><h1>平台初始化</h1><p>像 WordPress 安装向导一样，确认站点、存储和第三方 AI 能力后再向用户开放平台。</p></div>
      <div class="setup-progress"><strong>{{ readyCount }}/{{ checks.length }}</strong><span>关键检查通过</span></div>
    </header>

    <div v-if="state.error" class="setup-banner error">{{ state.error }}</div>
    <div v-if="state.message" class="setup-banner success">{{ state.message }}</div>

    <div class="setup-layout">
      <section class="setup-card site-card">
        <div class="step-number">1</div><div class="step-heading"><h2>站点与公开地址</h2><p>公开地址只用于生成链接和 SDK 示例，不改变容器内部拓扑。</p></div>
        <label>站点名称<input v-model="form.site_name" placeholder="Powflow" /></label>
        <label>用户控制台 URL<input v-model="form.public_console_url" placeholder="https://console.example.com" /></label>
        <label>公开 API URL<input v-model="form.public_api_url" placeholder="https://api.example.com" /></label>
        <button class="btn primary" type="button" :disabled="state.saving || !form.site_name.trim()" @click="saveSite">{{ state.saving ? '保存中…' : '保存站点信息' }}</button>
      </section>

      <section class="setup-card checks-card">
        <div class="step-number">2</div><div class="step-heading"><h2>基础设施检查</h2><p>启动前配置只检查，不在运行中的网页里热改数据库密码或主密钥。</p></div>
        <div class="check-list"><article v-for="item in checks.slice(0, 3)" :key="item.name"><span :class="['check-dot', { ready: item.ready }]">{{ item.ready ? '✓' : '!' }}</span><div><strong>{{ item.name }}</strong><p>{{ item.detail }}</p></div><span>{{ item.ready ? '已就绪' : '待处理' }}</span></article></div>
        <div class="setup-actions"><button class="btn ghost" type="button" @click="emit('open-storage')">查看数据库与存储</button><button class="btn ghost" type="button" :disabled="state.loading" @click="load">重新检查</button></div>
      </section>

      <section class="setup-card provider-card">
        <div class="step-number">3</div><div class="step-heading"><h2>第三方 AI Provider</h2><p>至少配置一个 LLM 和一个 Embedding 端点；密钥由全局调用池统一治理。</p></div>
        <div class="provider-counts"><div><strong>{{ state.item?.llm_endpoint_count || 0 }}</strong><span>可用 LLM</span></div><div><strong>{{ state.item?.embedding_endpoint_count || 0 }}</strong><span>可用 Embedding</span></div><div><strong>{{ state.item?.enabled_endpoint_count || 0 }}</strong><span>全部端点</span></div></div>
        <div class="setup-actions"><button class="btn primary" type="button" @click="emit('open-ai-pool')">配置全局 AI 调用池</button><button class="btn ghost" type="button" :disabled="state.loading" @click="load">验证配置</button></div>
      </section>

      <section class="setup-card finish-card">
        <div class="step-number">4</div><div class="step-heading"><h2>启用用户工作台</h2><p>完成后，用户可以创建知识库、Agent、Workflow 和 API 发布；未就绪时保持明确告警。</p></div>
        <div class="finish-state"><span :class="['readiness-orb', { ready: canComplete }]" aria-hidden="true"></span><div><strong>{{ state.item?.state === 'READY' ? '平台已经初始化' : canComplete ? '可以完成初始化' : '仍有必要配置未完成' }}</strong><p>当前状态：{{ state.item?.state || '检查中' }}</p></div></div>
        <button class="btn primary" type="button" :disabled="!canComplete || state.completing || state.item?.state === 'READY'" @click="complete">{{ state.completing ? '正在完成…' : state.item?.state === 'READY' ? '初始化已完成' : '完成初始化' }}</button>
      </section>
    </div>
  </section>
</template>

<style scoped>
.setup-page{padding:clamp(20px,3vw,42px);max-width:1500px;margin:auto}.setup-hero{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;margin-bottom:24px}.setup-eyebrow{font-size:11px;font-weight:800;letter-spacing:.12em;color:var(--accent,#0071e3)}.setup-hero h1{margin:7px 0 8px;font-size:clamp(30px,4vw,48px);line-height:1;letter-spacing:-.035em}.setup-hero p,.step-heading p,.check-list p,.finish-state p{margin:0;color:var(--muted,#6e6e73);line-height:1.55}.setup-progress{display:grid;justify-items:end}.setup-progress strong{font-size:34px}.setup-progress span{color:var(--muted,#6e6e73)}.setup-banner{margin-bottom:16px;padding:13px 16px;border-radius:14px}.setup-banner.error{background:rgba(255,59,48,.12);color:#ff453a}.setup-banner.success{background:rgba(48,209,88,.12);color:#30d158}.setup-layout{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.setup-card{display:grid;align-content:start;grid-template-columns:40px minmax(0,1fr);gap:18px;padding:24px;border:1px solid var(--line,#d8d8dc);border-radius:24px;background:var(--surface,#fff);box-shadow:0 14px 42px rgba(0,0,0,.06)}.step-number{display:grid;place-items:center;width:36px;height:36px;border-radius:12px;background:rgba(0,113,227,.1);color:#0071e3;font-weight:800}.step-heading h2{margin:2px 0 6px}.setup-card>label,.setup-card>.check-list,.setup-card>.provider-counts,.setup-card>.setup-actions,.setup-card>.finish-state,.setup-card>.btn{grid-column:2}.setup-card label{display:grid;gap:7px;color:var(--muted,#6e6e73);font-size:12px}.setup-card input{padding:11px 13px;border-radius:12px}.site-card{grid-row:span 2}.check-list{display:grid}.check-list article{display:grid;grid-template-columns:28px 1fr auto;align-items:center;gap:12px;padding:13px 0;border-bottom:1px solid var(--line,#ececf0)}.check-list article>span:last-child{font-size:12px;color:var(--muted,#6e6e73)}.check-dot{display:grid;place-items:center;width:24px;height:24px;border-radius:999px;background:rgba(255,159,10,.14);color:#b56a00;font-weight:800}.check-dot.ready{background:rgba(48,209,88,.14);color:#198844}.provider-counts{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.provider-counts div{display:grid;gap:4px;padding:15px;border-radius:16px;background:var(--surface-muted,#f5f5f7)}.provider-counts strong{font-size:26px}.provider-counts span{font-size:11px;color:var(--muted,#6e6e73)}.setup-actions{display:flex;flex-wrap:wrap;gap:10px}.finish-state{display:flex;align-items:center;gap:13px}.readiness-orb{width:22px;height:22px;border-radius:999px;background:#ff9f0a;box-shadow:0 0 0 7px rgba(255,159,10,.12)}.readiness-orb.ready{background:#30d158;box-shadow:0 0 0 7px rgba(48,209,88,.12)}
@media(max-width:900px){.setup-layout{grid-template-columns:1fr}.site-card{grid-row:auto}}@media(max-width:620px){.setup-hero{display:grid}.setup-progress{justify-items:start}.setup-card{grid-template-columns:1fr}.setup-card>*{grid-column:1!important}.provider-counts{grid-template-columns:1fr}.check-list article{grid-template-columns:28px 1fr}.check-list article>span:last-child{grid-column:2}}
</style>
