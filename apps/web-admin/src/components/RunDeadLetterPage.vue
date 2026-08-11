<script setup>
import { onMounted, ref } from 'vue'
import vInteractiveRow from './common/interactiveRowDirective'

const props = defineProps({ fetchJson: { type: Function, required: true }, sectionDomId: { type: Function, required: true } })
const items = ref([])
const auditItems = ref([])
const selected = ref(null)
const status = ref('OPEN')
const loading = ref(false)
const actionId = ref('')
const error = ref('')
const message = ref('')

function when(value) { return value ? new Date(value).toLocaleString() : '—' }

async function load() {
  loading.value = true
  error.value = ''
  try {
    const query = status.value ? `?status=${encodeURIComponent(status.value)}` : ''
    const response = await props.fetchJson('control', `/api/v1/admin/run-dead-letters${query}`)
    items.value = response?.items || []
  } catch (e) { error.value = e?.message || String(e) }
  finally { loading.value = false }
}

async function select(item) {
  selected.value = item
  auditItems.value = []
  error.value = ''
  try {
    const response = await props.fetchJson('control', `/api/v1/admin/run-dead-letters/${item.dead_letter_id}/audit`)
    auditItems.value = response?.items || []
  } catch (e) { error.value = e?.message || String(e) }
}

async function replay(item) {
  if (!window.confirm(`确认重放 Run ${item.run_id}？该操作会重新投递原始运行上下文。`)) return
  actionId.value = item.dead_letter_id
  error.value = ''
  message.value = ''
  try {
    const response = await props.fetchJson('control', `/api/v1/admin/run-dead-letters/${item.dead_letter_id}/replay`, { method: 'POST' })
    selected.value = response.item
    message.value = `Run ${item.run_id} 已进入重放队列`
    await load()
    await select(selected.value)
  } catch (e) { error.value = e?.message || String(e) }
  finally { actionId.value = '' }
}

async function dismiss(item) {
  if (!window.confirm(`确认忽略 Run ${item.run_id} 的这条死信？`)) return
  actionId.value = item.dead_letter_id
  error.value = ''
  try {
    await props.fetchJson('control', `/api/v1/admin/run-dead-letters/${item.dead_letter_id}/dismiss`, { method: 'POST' })
    message.value = `Run ${item.run_id} 的死信已忽略`
    selected.value = null
    auditItems.value = []
    await load()
  } catch (e) { error.value = e?.message || String(e) }
  finally { actionId.value = '' }
}

onMounted(load)
</script>

<template>
  <section class="content domain-section" :id="sectionDomId('main')" data-menu-section="main">
    <div class="panel">
      <div class="panel-title">运行死信</div>
      <p class="panel-sub">浏览执行失败记录，核对原始载荷与审计轨迹，并在确认后重放。</p>
      <div class="toolbar">
        <label>状态
          <select v-model="status" @change="load">
            <option value="">全部</option><option value="OPEN">待处理</option><option value="REPLAYING">重放中</option><option value="REPLAYED">已重放</option><option value="DISMISSED">已忽略</option>
          </select>
        </label>
        <button class="btn ghost" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新' }}</button>
      </div>
      <p v-if="message" class="banner ok">{{ message }}</p>
      <p v-if="error" class="banner err">{{ error }}</p>
      <div class="table-wrap">
        <table>
          <thead><tr><th>状态</th><th>Run</th><th>失败任务</th><th>错误</th><th>重放次数</th><th>创建时间</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="item in items" :key="item.dead_letter_id" v-interactive-row="{ activate: () => select(item), label: `查看运行死信 ${item.dead_letter_id}`, current: selected?.dead_letter_id === item.dead_letter_id }" :class="{ selected: selected?.dead_letter_id === item.dead_letter_id }" @click="select(item)">
              <td><span class="status">{{ item.status }}</span></td><td class="mono">{{ item.run_id }}</td><td class="mono">{{ item.failed_task_id }}</td><td class="error-cell" :title="item.error">{{ item.error || '—' }}</td><td>{{ item.replay_count }}</td><td>{{ when(item.created_at) }}</td>
              <td class="actions"><button class="btn primary" :disabled="item.status !== 'OPEN' || actionId === item.dead_letter_id" @click.stop="replay(item)">{{ actionId === item.dead_letter_id ? '提交中…' : '重放' }}</button><button class="btn ghost" :disabled="item.status !== 'OPEN' || actionId === item.dead_letter_id" @click.stop="dismiss(item)">忽略</button></td>
            </tr>
            <tr v-if="!loading && !items.length"><td colspan="7" class="empty" role="status" aria-live="polite" aria-atomic="true">当前筛选条件下没有死信</td></tr>
          </tbody>
        </table>
      </div>
    </div>
    <div v-if="selected" class="panel detail">
      <div class="panel-title">详情与审计</div>
      <dl><dt>来源队列</dt><dd>{{ selected.source_queue }}</dd><dt>最后错误</dt><dd>{{ selected.last_error || selected.error || '—' }}</dd><dt>重放请求</dt><dd>{{ when(selected.replay_requested_at) }}</dd><dt>操作者</dt><dd>{{ selected.replayed_by || '—' }}</dd></dl>
      <details><summary>原始载荷</summary><pre>{{ JSON.stringify(selected.payload, null, 2) }}</pre></details>
      <ol class="timeline"><li v-for="audit in auditItems" :key="audit.audit_id"><strong>{{ audit.action }}</strong><span>{{ when(audit.created_at) }} · {{ audit.actor_id }}</span><pre>{{ JSON.stringify(audit.detail, null, 2) }}</pre></li></ol>
    </div>
  </section>
</template>

<style scoped>
.toolbar,.actions{display:flex;align-items:end;gap:8px;flex-wrap:wrap}.toolbar{margin:14px 0}.table-wrap{overflow:auto}.mono{font-family:ui-monospace,monospace;max-width:220px}.error-cell{max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.selected{background:color-mix(in srgb,var(--accent,#4f46e5) 9%,transparent)}.empty{text-align:center;padding:28px}.detail{margin-top:16px}dl{display:grid;grid-template-columns:130px 1fr;gap:8px 16px}dt{color:var(--text-muted,#667085)}dd{margin:0;overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:rgba(127,127,127,.08);padding:10px;border-radius:8px}.timeline{display:grid;gap:12px;padding-left:20px}.timeline li span{display:block;color:var(--text-muted,#667085);font-size:12px;margin-top:3px}.status{font-size:12px;font-weight:700}@media(max-width:760px){dl{grid-template-columns:1fr}.mono{max-width:150px}}
</style>
