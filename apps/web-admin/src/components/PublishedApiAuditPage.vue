<script setup>
import { computed, onMounted, reactive } from 'vue'

const props = defineProps({ fetchJson: { type: Function, required: true } })
const state = reactive({ loading: false, error: '', items: [] })

const accepted = computed(() => state.items.filter((item) => item.status === 'ACCEPTED').length)
const rejected = computed(() => state.items.filter((item) => item.status !== 'ACCEPTED').length)
const tenants = computed(() => new Set(state.items.map((item) => item.tenant_id)).size)

function formatTime(value) {
  return value ? new Date(value).toLocaleString() : '—'
}

async function load() {
  state.loading = true
  state.error = ''
  try {
    const data = await props.fetchJson('control', '/api/v1/admin/product-audit/invocations')
    state.items = data?.items || []
  } catch (error) {
    state.error = error instanceof Error ? error.message : String(error)
  } finally {
    state.loading = false
  }
}

onMounted(load)
</script>

<template>
  <section class="audit-page">
    <header class="audit-hero">
      <div><span>Published Runtime</span><h2>发布调用审计</h2><p>跨租户查看已发布 Agent 与 Workflow 的调用结果、耗时和追踪标识。</p></div>
      <button type="button" :disabled="state.loading" @click="load">{{ state.loading ? '刷新中…' : '刷新日志' }}</button>
    </header>

    <div class="metric-grid">
      <article><span>最近调用</span><strong>{{ state.items.length }}</strong></article>
      <article><span>已接受</span><strong>{{ accepted }}</strong></article>
      <article><span>异常 / 拒绝</span><strong>{{ rejected }}</strong></article>
      <article><span>涉及租户</span><strong>{{ tenants }}</strong></article>
    </div>

    <p v-if="state.error" class="banner error">{{ state.error }}</p>
    <section class="audit-card">
      <div class="table-head"><span>状态</span><span>租户 / 部署</span><span>Trace ID</span><span>耗时</span><span>时间</span></div>
      <div v-if="!state.loading && !state.items.length" class="empty">暂无发布调用记录</div>
      <article v-for="item in state.items" :key="item.invocation_id" class="audit-row">
        <span :class="['status', item.status === 'ACCEPTED' ? 'ok' : 'risk']">{{ item.status }}</span>
        <span class="identity"><strong>{{ item.tenant_id }}</strong><small>{{ item.deployment_id }}</small></span>
        <code>{{ item.trace_id || '—' }}</code>
        <span>{{ item.duration_ms ?? '—' }} ms</span>
        <time>{{ formatTime(item.created_at) }}</time>
      </article>
    </section>
  </section>
</template>

<style scoped>
.audit-page{display:grid;gap:18px;color:var(--color-text-primary,#f5f5f7)}
.audit-hero,.audit-card,.metric-grid article{border:1px solid var(--color-border-subtle,rgba(255,255,255,.1));background:var(--color-surface-elevated,rgba(28,28,30,.78));box-shadow:0 16px 38px rgba(0,0,0,.12);backdrop-filter:blur(22px) saturate(1.2)}
.audit-hero{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding:26px;border-radius:24px}.audit-hero span{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#0a84ff}.audit-hero h2{margin:7px 0 4px;font-size:30px;letter-spacing:-.035em}.audit-hero p{margin:0;color:var(--color-text-secondary,#a1a1a6)}
button{min-height:40px;padding:0 17px;border:0;border-radius:999px;background:#0a84ff;color:white;font-weight:650;cursor:pointer}button:disabled{opacity:.55;cursor:wait}
.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.metric-grid article{padding:18px;border-radius:18px}.metric-grid span{display:block;color:var(--color-text-secondary,#a1a1a6);font-size:13px}.metric-grid strong{display:block;margin-top:9px;font-size:28px;letter-spacing:-.04em}
.banner{margin:0;padding:12px 15px;border-radius:14px}.banner.error{color:#ff9f9a;background:rgba(255,69,58,.12)}
.audit-card{overflow:hidden;border-radius:22px}.table-head,.audit-row{display:grid;grid-template-columns:120px minmax(200px,1.25fr) minmax(170px,1fr) 100px 170px;align-items:center;gap:14px;padding:14px 18px}.table-head{font-size:12px;color:var(--color-text-tertiary,#8e8e93);background:rgba(127,127,127,.08)}.audit-row{border-top:1px solid var(--color-border-subtle,rgba(255,255,255,.08));font-size:13px}.identity{min-width:0}.identity strong,.identity small{display:block;overflow:hidden;text-overflow:ellipsis}.identity small{margin-top:3px;color:var(--color-text-tertiary,#8e8e93)}code{overflow:hidden;text-overflow:ellipsis;color:var(--color-text-secondary,#a1a1a6)}.status{justify-self:start;padding:5px 9px;border-radius:999px;font-size:11px;font-weight:700}.status.ok{color:#30d158;background:rgba(48,209,88,.13)}.status.risk{color:#ff9f0a;background:rgba(255,159,10,.13)}.empty{padding:44px;text-align:center;color:var(--color-text-tertiary,#8e8e93)}
@media(max-width:900px){.metric-grid{grid-template-columns:repeat(2,1fr)}.table-head{display:none}.audit-row{grid-template-columns:100px 1fr auto}.audit-row code{grid-column:2/4}.audit-row time{grid-column:2/4;color:var(--color-text-tertiary,#8e8e93)}}
@media(max-width:560px){.audit-hero{align-items:stretch;flex-direction:column;padding:20px}.audit-hero h2{font-size:25px}.metric-grid{grid-template-columns:1fr 1fr}.audit-row{grid-template-columns:1fr auto;padding:14px}.audit-row .status{grid-column:1}.audit-row .identity{grid-column:1/3}.audit-row code{grid-column:1/3}.audit-row time{grid-column:1/3}.audit-row>span:nth-of-type(3){grid-column:2;grid-row:1}}
@media(prefers-color-scheme:light){.audit-page{color:#1d1d1f}.audit-hero,.audit-card,.metric-grid article{background:rgba(255,255,255,.82);border-color:rgba(60,60,67,.14);box-shadow:0 14px 34px rgba(0,0,0,.06)}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
</style>
