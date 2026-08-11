<script setup>
import { computed, onMounted, ref } from 'vue'
import { buildNodeTrace, runStatusTone } from '../composables/runTraceModel'
import vInteractiveRow from './common/interactiveRowDirective'

const props = defineProps({ fetchJson: { type: Function, required: true }, sectionDomId: { type: Function, required: true } })
const runId = ref('')
const run = ref(null)
const events = ref([])
const nodeExecutions = ref([])
const recent = ref([])
const recentStatus = ref('')
const loading = ref(false)
const error = ref('')
const nodes = computed(() => nodeExecutions.value.length ? nodeExecutions.value.map((node) => ({
  ...node,
  status: node.status === 'SUCCEEDED' ? 'COMPLETED' : node.status,
  details: node.details || {},
  attempts: node.attempts || [],
})) : buildNodeTrace(events.value))

function when(value) { return value ? new Date(value).toLocaleString() : '—' }
function json(value) { return JSON.stringify(value ?? {}, null, 2) }

async function load() {
  const id = runId.value.trim()
  if (!id) { error.value = '请输入 Run ID'; return }
  loading.value = true
  error.value = ''
  run.value = null
  events.value = []
  nodeExecutions.value = []
  try {
    const encoded = encodeURIComponent(id)
    const [runResponse, eventResponse, nodeResponse] = await Promise.all([
      props.fetchJson('control', `/api/v1/admin/runs/${encoded}`),
      props.fetchJson('control', `/api/v1/admin/runs/${encoded}/events`),
      props.fetchJson('control', `/api/v1/admin/runs/${encoded}/nodes`),
    ])
    run.value = runResponse?.item || null
    events.value = eventResponse?.items || []
    nodeExecutions.value = nodeResponse?.items || []
  } catch (e) { error.value = e?.message || String(e) }
  finally { loading.value = false }
}

async function loadRecent() {
  error.value = ''
  try {
    const query = recentStatus.value ? `?status=${encodeURIComponent(recentStatus.value)}` : ''
    const response = await props.fetchJson('control', `/api/v1/admin/runs${query}`)
    recent.value = response?.items || []
  } catch (e) { error.value = e?.message || String(e) }
}

async function selectRecent(item) {
  runId.value = item.run_id
  await load()
}

onMounted(loadRecent)
</script>

<template>
  <section class="content domain-section" :id="sectionDomId('main')" data-menu-section="main">
    <div class="panel">
      <div class="panel-title">运行追踪</div>
      <p class="panel-sub">按租户权限查看 Run 快照、实际节点路径、条件决策和完整事件序列。</p>
      <form class="search" @submit.prevent="load">
        <label>Run ID<input v-model.trim="runId" autocomplete="off" placeholder="输入 Control Plane run_id" /></label>
        <button class="btn primary" :disabled="loading">{{ loading ? '查询中…' : '查询' }}</button>
      </form>
      <p v-if="error" class="banner err">{{ error }}</p>
    </div>

    <div class="panel recent-panel">
      <div class="panel-title">最近运行</div>
      <div class="recent-filter"><label>状态<select v-model="recentStatus" @change="loadRecent"><option value="">全部</option><option value="PENDING">PENDING</option><option value="RUNNING">RUNNING</option><option value="SUCCEEDED">SUCCEEDED</option><option value="FAILED">FAILED</option><option value="CANCELLED">CANCELLED</option></select></label><button class="btn ghost" @click="loadRecent">刷新</button></div>
      <div class="table-wrap"><table><thead><tr><th>状态</th><th>Run</th><th>阶段</th><th>进度</th><th>创建时间</th></tr></thead><tbody>
        <tr v-for="item in recent" :key="item.run_id" v-interactive-row="{ activate: () => selectRecent(item), label: `查看运行 ${item.run_id}`, current: run?.run_id === item.run_id }" class="clickable" @click="selectRecent(item)"><td :class="`tone-${runStatusTone(item.status)}`">{{ item.status }}</td><td class="mono">{{ item.run_id }}</td><td>{{ item.stage }}</td><td>{{ item.progress }}%</td><td>{{ when(item.created_at) }}</td></tr>
        <tr v-if="!recent.length"><td colspan="5" class="empty" role="status" aria-live="polite" aria-atomic="true">暂无运行记录</td></tr>
      </tbody></table></div>
    </div>

    <template v-if="run">
      <div class="summary-grid">
        <article class="panel metric"><span>状态</span><strong :class="`tone-${runStatusTone(run.status)}`">{{ run.status }}</strong></article>
        <article class="panel metric"><span>阶段</span><strong>{{ run.stage }}</strong></article>
        <article class="panel metric"><span>进度</span><strong>{{ run.progress }}%</strong></article>
        <article class="panel metric"><span>工作流版本</span><strong>v{{ run.workflow_version_no }}</strong></article>
      </div>
      <div class="panel">
        <div class="progress"><i :style="{ width: `${Math.max(0, Math.min(100, run.progress || 0))}%` }"></i></div>
        <dl><dt>Run</dt><dd class="mono">{{ run.run_id }}</dd><dt>Workflow checksum</dt><dd class="mono">{{ run.workflow_checksum }}</dd><dt>开始</dt><dd>{{ when(run.started_at) }}</dd><dt>结束</dt><dd>{{ when(run.finished_at) }}</dd><dt>错误</dt><dd>{{ run.error_code ? `${run.error_code}: ${run.error_message}` : '—' }}</dd></dl>
      </div>

      <div class="panel">
        <div class="panel-title">实际节点路径</div>
        <div class="table-wrap"><table><thead><tr><th>#</th><th>节点</th><th>类型</th><th>状态</th><th>耗时</th><th>尝试</th><th>分支/缓存</th></tr></thead><tbody>
          <tr v-for="node in nodes" :key="node.node_id"><td>{{ node.ordinal + 1 }}</td><td class="mono">{{ node.node_id }}</td><td>{{ node.node_type }}</td><td>{{ node.status }}</td><td>{{ node.duration_ms == null ? '—' : `${node.duration_ms} ms` }}</td><td class="attempt-cell"><details v-if="node.attempts?.length"><summary>{{ node.attempts.length }} 次</summary><ol class="attempt-list"><li v-for="attempt in node.attempts" :key="attempt.node_attempt_id"><strong>#{{ attempt.attempt_no }} {{ attempt.status }}</strong><span>{{ attempt.duration_ms == null ? '—' : `${attempt.duration_ms} ms` }}</span><span v-if="attempt.retry_delay_ms">退避 {{ attempt.retry_delay_ms }} ms</span><span v-if="attempt.error_message" class="attempt-error">{{ attempt.error_type ? `${attempt.error_type}: ` : '' }}{{ attempt.error_message }}</span></li></ol></details><span v-else>1 次</span></td><td class="mono small">{{ Object.keys(node.details).length ? json(node.details) : '—' }}</td></tr>
          <tr v-if="!nodes.length"><td colspan="7" class="empty">尚无逐节点事件</td></tr>
        </tbody></table></div>
      </div>

      <div class="panel">
        <div class="panel-title">事件时间线</div>
        <ol class="timeline"><li v-for="event in events" :key="event.event_id"><span class="seq">{{ event.sequence_no }}</span><div><strong>{{ event.event_type }}</strong><p>{{ when(event.created_at) }} · {{ event.stage }} · {{ event.progress }}%</p><details><summary>事件载荷</summary><pre>{{ json(event.payload) }}</pre></details></div></li></ol>
      </div>

      <div class="panel payloads">
        <details><summary>输入</summary><pre>{{ json(run.input_payload) }}</pre></details><details><summary>输出</summary><pre>{{ json(run.output_payload) }}</pre></details><details><summary>指标</summary><pre>{{ json(run.metrics) }}</pre></details><details><summary>引用</summary><pre>{{ json(run.citations) }}</pre></details>
      </div>
    </template>
  </section>
</template>

<style scoped>
.search,.recent-filter{display:flex;align-items:end;gap:10px;flex-wrap:wrap}.search{margin-top:16px}.search label{flex:1;min-width:260px}.recent-panel{margin-top:16px}.recent-filter{margin:12px 0}.clickable{cursor:pointer}.clickable:hover{background:rgba(79,70,229,.07)}.summary-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:16px 0}.metric span{display:block;color:var(--text-muted,#667085);font-size:12px}.metric strong{display:block;margin-top:8px;font-size:18px}.tone-success{color:#16803c}.tone-danger{color:#c33131}.tone-active{color:#315ed1}.tone-muted{color:#667085}.progress{height:8px;background:rgba(127,127,127,.16);border-radius:99px;overflow:hidden;margin-bottom:16px}.progress i{display:block;height:100%;background:var(--accent,#4f46e5)}dl{display:grid;grid-template-columns:150px 1fr;gap:8px 14px}dt{color:var(--text-muted,#667085)}dd{margin:0;overflow-wrap:anywhere}.mono{font-family:ui-monospace,monospace}.small{font-size:11px;white-space:pre-wrap}.table-wrap{overflow:auto}.empty{text-align:center;padding:24px}.attempt-cell{min-width:170px;vertical-align:top}.attempt-cell summary{cursor:pointer}.attempt-list{margin:8px 0 0;padding-left:18px;display:grid;gap:8px;font-size:11px}.attempt-list li{display:grid;gap:2px}.attempt-error{color:#c33131;overflow-wrap:anywhere}.timeline{list-style:none;padding:0;display:grid;gap:12px}.timeline li{display:grid;grid-template-columns:34px 1fr;gap:10px}.seq{display:grid;place-items:center;width:30px;height:30px;border-radius:50%;background:rgba(79,70,229,.12);font-weight:700}.timeline p{margin:3px 0;color:var(--text-muted,#667085);font-size:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:rgba(127,127,127,.08);padding:10px;border-radius:8px}.payloads{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}@media(max-width:850px){.summary-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.payloads{grid-template-columns:1fr}}@media(max-width:560px){.summary-grid{grid-template-columns:1fr}dl{grid-template-columns:1fr}.search label{min-width:100%}}
</style>
