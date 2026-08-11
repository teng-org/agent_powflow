<script setup>
import { computed, onMounted, reactive } from 'vue'

const props = defineProps({ fetchJson: { type: Function, required: true } })
const state = reactive({ loading: false, error: '', message: '', agents: [], deployments: [] })
const tenants = computed(() => new Set([...state.agents, ...state.deployments].map((item) => item.tenant_id)).size)

async function load() {
  state.loading = true; state.error = ''; state.message = ''
  try {
    const [agents, deployments] = await Promise.all([
      props.fetchJson('control', '/api/v1/admin/product-assets/agents'),
      props.fetchJson('control', '/api/v1/admin/product-assets/deployments'),
    ])
    state.agents = agents?.items || []
    state.deployments = deployments?.items || []
  } catch (error) { state.error = error instanceof Error ? error.message : String(error) }
  finally { state.loading = false }
}

async function toggleDeployment(item) {
  const status = item.status === 'ACTIVE' ? 'PAUSED' : 'ACTIVE'
  state.error = ''; state.message = ''
  try {
    await props.fetchJson('control', `/api/v1/admin/product-assets/deployments/${item.deployment_id}/status`, { method: 'PATCH', body: { status } })
    state.message = status === 'PAUSED' ? '发布入口已暂停' : '发布入口已恢复'
    await load()
  } catch (error) { state.error = error instanceof Error ? error.message : String(error) }
}

onMounted(load)
</script>

<template>
  <section class="product-assets">
    <header><div><span>Cross-tenant Governance</span><h2>智能体与发布资产</h2><p>平台管理员只做全局检索、归属审计和紧急停用，不在这里替用户编辑业务 Prompt。</p></div><button type="button" :disabled="state.loading" @click="load">刷新资产</button></header>
    <div class="summary"><article><span>智能体</span><strong>{{ state.agents.length }}</strong></article><article><span>API 发布</span><strong>{{ state.deployments.length }}</strong></article><article><span>租户范围</span><strong>{{ tenants }}</strong></article></div>
    <p v-if="state.error" class="banner error">{{ state.error }}</p><p v-if="state.message" class="banner success">{{ state.message }}</p>
    <section class="panel"><div class="panel-title"><div><span>Agents</span><h3>跨租户智能体</h3></div></div><div class="rows head"><span>名称</span><span>租户 / 所有者</span><span>状态</span><span>版本</span></div><article v-for="item in state.agents" :key="item.agent_id" class="rows"><strong>{{ item.name }}</strong><span class="identity">{{ item.tenant_id }}<small>{{ item.owner_user_id }}</small></span><span class="pill">{{ item.status }}</span><span>v{{ item.latest_version_no }}</span></article><p v-if="!state.agents.length" class="empty">暂无智能体资产</p></section>
    <section class="panel"><div class="panel-title"><div><span>Deployments</span><h3>API 发布治理</h3></div></div><div class="rows deployment head"><span>名称 / 路径</span><span>租户 / 所有者</span><span>状态</span><span>限流</span><span>操作</span></div><article v-for="item in state.deployments" :key="item.deployment_id" class="rows deployment"><span><strong>{{ item.name }}</strong><small>{{ item.invoke_path }}</small></span><span class="identity">{{ item.tenant_id }}<small>{{ item.owner_user_id }}</small></span><span class="pill">{{ item.status }}</span><span>{{ item.rate_limit_per_minute }}/min</span><button type="button" @click="toggleDeployment(item)">{{ item.status === 'ACTIVE' ? '紧急暂停' : '恢复发布' }}</button></article><p v-if="!state.deployments.length" class="empty">暂无 API 发布</p></section>
  </section>
</template>

<style scoped>
.product-assets{display:grid;gap:18px;color:var(--color-text-primary,#f5f5f7)}header,.panel,.summary article{border:1px solid var(--color-border-subtle,rgba(255,255,255,.1));background:var(--color-surface-elevated,rgba(28,28,30,.78));box-shadow:0 14px 38px rgba(0,0,0,.12);backdrop-filter:blur(22px)}header{display:flex;justify-content:space-between;align-items:flex-end;gap:20px;padding:26px;border-radius:24px}header span,.panel-title span{color:#0a84ff;font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase}h2{margin:7px 0 5px;font-size:30px}header p{margin:0;color:var(--color-text-secondary,#a1a1a6)}button{min-height:36px;padding:0 14px;border:0;border-radius:999px;background:#0a84ff;color:white;font-weight:650;cursor:pointer}.summary{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.summary article{padding:18px;border-radius:18px}.summary span{color:var(--color-text-secondary,#a1a1a6)}.summary strong{display:block;margin-top:8px;font-size:28px}.panel{overflow:hidden;border-radius:22px}.panel-title{padding:20px}.panel-title h3{margin:5px 0 0}.rows{display:grid;grid-template-columns:minmax(180px,1.2fr) minmax(220px,1fr) 110px 90px;align-items:center;gap:14px;padding:13px 20px;border-top:1px solid var(--color-border-subtle,rgba(255,255,255,.08));font-size:13px}.rows.deployment{grid-template-columns:minmax(230px,1.4fr) minmax(200px,1fr) 90px 80px 110px}.rows.head{color:var(--color-text-tertiary,#8e8e93);font-size:11px}.rows small,.identity small{display:block;margin-top:3px;color:var(--color-text-tertiary,#8e8e93);overflow:hidden;text-overflow:ellipsis}.pill{justify-self:start;padding:5px 8px;border-radius:999px;background:rgba(127,127,127,.13);font-size:11px;font-weight:700}.banner{margin:0;padding:12px 15px;border-radius:14px}.banner.error{color:#ff9f9a;background:rgba(255,69,58,.12)}.banner.success{color:#30d158;background:rgba(48,209,88,.12)}.empty{padding:30px;text-align:center;color:var(--color-text-tertiary,#8e8e93)}
@media(max-width:760px){header{align-items:stretch;flex-direction:column}.summary{grid-template-columns:repeat(3,1fr)}.rows.head{display:none}.rows,.rows.deployment{grid-template-columns:1fr auto}.rows .identity{grid-column:1/3}.rows.deployment>span:first-child{grid-column:1/3}.rows.deployment button{grid-column:1/3;justify-self:start}}
@media(max-width:480px){.summary{grid-template-columns:1fr}.rows,.rows.deployment{padding:14px}}
@media(prefers-color-scheme:light){.product-assets{color:#1d1d1f}header,.panel,.summary article{background:rgba(255,255,255,.86);border-color:rgba(60,60,67,.14);box-shadow:0 14px 34px rgba(0,0,0,.06)}}
</style>
