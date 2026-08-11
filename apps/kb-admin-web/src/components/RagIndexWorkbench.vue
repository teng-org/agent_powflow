<script setup>
import { computed, onMounted, reactive, watch } from 'vue'

const props = defineProps({
  baseUrl: { type: String, required: true },
  token: { type: String, required: true },
  kbId: { type: String, required: true },
  kbName: { type: String, default: '' },
  documents: { type: Array, default: () => [] },
})
const emit = defineEmits(['notify'])

const state = reactive({
  loading: false,
  busy: '',
  error: '',
  data: { active_index_revision: '', items: [] },
  expandedRevision: '',
  showBuild: false,
  showEvaluation: false,
  confirmAction: null,
})

const buildForm = reactive({ revision: '', require_evaluation: true, qdrant_batch_size: 16 })
const evaluationForm = reactive({
  revision: '',
  top_k: 10,
  min_recall: 0.8,
  min_mrr: 0.6,
  min_ndcg: 0.6,
  cases: [],
})

const manifests = computed(() => Array.isArray(state.data?.items) ? state.data.items : [])
const activeRevision = computed(() => String(state.data?.active_index_revision || ''))
const activeManifest = computed(() => manifests.value.find((item) => item.index_revision === activeRevision.value || item.status === 'ACTIVE'))
const indexedDocuments = computed(() => props.documents.filter((item) => Number(item.embedded_count || 0) > 0 || ['INDEXED', 'READY', 'SUCCEEDED'].includes(String(item.status || '').toUpperCase())))
const indexedChunks = computed(() => Number(activeManifest.value?.chunk_count || 0))
const lexicalCoverage = computed(() => {
  const total = Number(activeManifest.value?.chunk_count || 0)
  return total ? Number(activeManifest.value?.lexical_chunk_count || 0) / total : 0
})

function makeRevision() {
  const now = new Date()
  const stamp = [now.getFullYear(), now.getMonth() + 1, now.getDate(), now.getHours(), now.getMinutes(), now.getSeconds()]
    .map((value) => String(value).padStart(2, '0')).join('')
  return `rag2-${stamp}`
}

function safeError(status) {
  if (status === 401) return '登录状态已失效，请重新登录。'
  if (status === 403) return '你没有管理此知识库索引的权限。'
  if (status === 404) return '知识库或索引版本不存在。'
  if (status === 409) return '当前索引状态不允许执行此操作，请刷新后重试。'
  if (status >= 500) return '检索服务暂时不可用，请稍后重试。'
  return '操作未能完成，请检查填写内容。'
}

async function request(path, options = {}) {
  const response = await fetch(`${props.baseUrl}${path}`, {
    method: options.method || 'GET',
    headers: {
      Authorization: `Bearer ${props.token}`,
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  })
  if (!response.ok) throw new Error(safeError(response.status))
  const text = await response.text()
  return text ? JSON.parse(text) : {}
}

function endpoint(suffix = '') {
  return `/api/v1/knowledge-bases/${encodeURIComponent(props.kbId)}/rag/indexes${suffix}`
}

function notify(type, title, message = '') {
  emit('notify', { type, title, message })
}

async function load() {
  if (!props.kbId || !props.token) return
  state.loading = true
  state.error = ''
  try {
    state.data = await request(endpoint())
  } catch (error) {
    state.error = error instanceof Error ? error.message : '索引信息加载失败。'
  } finally {
    state.loading = false
  }
}

async function runAction(key, action, success) {
  state.busy = key
  state.error = ''
  try {
    await action()
    state.confirmAction = null
    await load()
    notify('success', success)
  } catch (error) {
    state.error = error instanceof Error ? error.message : '操作未能完成。'
    notify('error', '操作未能完成', state.error)
  } finally {
    state.busy = ''
  }
}

function openBuild() {
  Object.assign(buildForm, { revision: makeRevision(), require_evaluation: true, qdrant_batch_size: 16 })
  state.showBuild = true
}

async function buildIndex() {
  await runAction('build', () => request(endpoint('/build'), {
    method: 'POST',
    body: {
      revision: buildForm.revision.trim(),
      require_evaluation: buildForm.require_evaluation,
      qdrant_batch_size: Number(buildForm.qdrant_batch_size),
    },
  }), '新索引版本已构建')
  if (!state.error) state.showBuild = false
}

function addEvaluationCase() {
  evaluationForm.cases.push({ query: '', expected_doc_id: indexedDocuments.value[0]?.doc_id || '' })
}

function openEvaluation(item) {
  evaluationForm.revision = item.index_revision
  evaluationForm.top_k = 10
  evaluationForm.min_recall = 0.8
  evaluationForm.min_mrr = 0.6
  evaluationForm.min_ndcg = 0.6
  evaluationForm.cases = []
  addEvaluationCase()
  state.showEvaluation = true
}

async function evaluateIndex() {
  const cases = evaluationForm.cases
    .filter((item) => item.query.trim() && item.expected_doc_id)
    .map((item, index) => ({
      case_id: `case-${index + 1}`,
      query: item.query.trim(),
      rank_method: 'hybrid',
      expected_doc_ids: [item.expected_doc_id],
    }))
  if (!cases.length) {
    state.error = '请至少填写一条完整的评测问题与预期文档。'
    return
  }
  await runAction('evaluate', () => request(endpoint(`/${encodeURIComponent(evaluationForm.revision)}/evaluate`), {
    method: 'POST',
    body: {
      cases,
      top_k: Number(evaluationForm.top_k),
      min_recall: Number(evaluationForm.min_recall),
      min_mrr: Number(evaluationForm.min_mrr),
      min_ndcg: Number(evaluationForm.min_ndcg),
    },
  }), '检索质量评测已通过')
  if (!state.error) state.showEvaluation = false
}

function askAction(kind, item) {
  const config = {
    activate: { title: '激活这个索引版本？', body: '新请求将立即使用该版本；当前活动版本会保留，可随时回滚。', action: '激活版本' },
    rollback: { title: '回滚到这个版本？', body: '新请求将立即切换到所选历史版本，已有对话不会中断。', action: '确认回滚' },
    cleanup: { title: '清理失败版本？', body: '该版本的影子索引与构建记录将被删除，此操作无法撤销。', action: '确认清理' },
  }[kind]
  state.confirmAction = { kind, item, ...config }
}

async function confirmVersionAction() {
  const target = state.confirmAction
  if (!target) return
  const revision = encodeURIComponent(target.item.index_revision)
  if (target.kind === 'cleanup') {
    await runAction(`cleanup:${revision}`, () => request(endpoint(`/${revision}`), { method: 'DELETE' }), '失败版本已清理')
    return
  }
  await runAction(`${target.kind}:${revision}`, () => request(endpoint(`/${revision}/${target.kind}`), { method: 'POST' }), target.kind === 'activate' ? '索引版本已激活' : '索引版本已回滚')
}

function statusText(value) {
  return ({ ACTIVE: '活动', BUILDING: '候选', RETIRED: '历史', FAILED: '失败' })[String(value || '').toUpperCase()] || '未知'
}

function gateText(value) {
  return ({ PASSED: '已通过', PENDING: '待评测', FAILED: '未通过', WAIVED: '已豁免' })[String(value || '').toUpperCase()] || '未评测'
}

function metric(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN') : '0'
}

function percent(value) {
  const number = Number(value || 0)
  return `${Math.round(number * 100)}%`
}

function formatDate(value) {
  if (!value) return '尚未记录'
  return new Date(value).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

watch(() => [props.kbId, props.token], () => {
  state.expandedRevision = ''
  load()
})
onMounted(load)
</script>

<template>
  <section class="rag-workbench" aria-label="检索与索引工作台">
    <div v-if="state.error" class="rag-alert" role="alert"><span>!</span><p>{{ state.error }}</p><button type="button" @click="state.error = ''">关闭</button></div>

    <div class="rag-summary-grid">
      <article class="rag-summary-card spectral-card">
        <span>当前检索版本</span>
        <strong>{{ activeRevision || '尚未建立' }}</strong>
        <small>{{ activeManifest ? `${formatDate(activeManifest.activated_at || activeManifest.updated_at)} 激活` : '建立初始快照后才会承接检索流量' }}</small>
      </article>
      <article class="rag-summary-card">
        <span>质量门禁</span>
        <strong :class="`gate-${String(activeManifest?.gate_status || '').toLowerCase()}`">{{ gateText(activeManifest?.gate_status) }}</strong>
        <small>{{ activeManifest?.quality_report?.evaluation ? '已完成离线检索评测' : '完整性检查与检索评测' }}</small>
      </article>
      <article class="rag-summary-card">
        <span>索引覆盖</span>
        <strong>{{ metric(indexedChunks) }} <em>切片</em></strong>
        <small>{{ indexedDocuments.length }} 个可检索文档 · 词法覆盖 {{ percent(lexicalCoverage) }}</small>
      </article>
    </div>

    <section class="rag-lineage-panel">
      <header>
        <div><span class="rag-kicker">Index lineage</span><h3>索引版本轨道</h3><p>构建候选版本，评测通过后再原子切换；历史版本保留用于快速回滚。</p></div>
        <div class="rag-header-actions"><button class="btn ghost" type="button" :disabled="state.loading || !!state.busy" @click="load">刷新</button><button class="btn primary" type="button" :disabled="!indexedDocuments.length || !!state.busy" @click="openBuild">构建新版本</button></div>
      </header>

      <div v-if="state.loading" class="rag-loading"><span></span><span></span><span></span></div>
      <div v-else-if="!manifests.length" class="rag-empty">
        <span class="rag-empty-mark">◎</span><h3>还没有可管理的索引快照</h3><p>先从当前文档建立初始快照，之后即可安全构建、评测和切换新版本。</p>
        <button class="btn primary" type="button" :disabled="!indexedDocuments.length || !!state.busy" @click="runAction('bootstrap', () => request(endpoint('/bootstrap'), { method: 'POST' }), '初始索引快照已建立')">建立初始快照</button>
      </div>
      <template v-else>
        <div class="rag-rail" aria-label="索引版本时间轨">
          <div class="rag-rail-line"></div>
          <button v-for="item in [...manifests].reverse()" :key="item.index_revision" type="button" class="rag-rail-node" :class="[`status-${String(item.status).toLowerCase()}`, { active: item.index_revision === activeRevision }]" @click="state.expandedRevision = item.index_revision">
            <span class="rag-node-dot"></span><strong>{{ item.index_revision }}</strong><small>{{ statusText(item.status) }}</small>
          </button>
        </div>

        <div class="rag-version-list">
          <article v-for="item in manifests" :key="item.index_revision" class="rag-version-card" :class="{ active: item.index_revision === activeRevision, failed: item.status === 'FAILED' }">
            <button class="rag-version-main" type="button" @click="state.expandedRevision = state.expandedRevision === item.index_revision ? '' : item.index_revision">
              <span class="rag-version-icon" :class="`status-${String(item.status).toLowerCase()}`"></span>
              <span class="rag-version-copy"><span><strong>{{ item.index_revision }}</strong><b v-if="item.index_revision === activeRevision">正在承接流量</b></span><small>源自 {{ item.source_revision || '初始数据' }} · {{ formatDate(item.built_at || item.created_at) }}</small></span>
              <span class="rag-version-count"><strong>{{ metric(item.chunk_count) }}</strong><small>切片</small></span>
              <span class="rag-gate" :class="`gate-${String(item.gate_status || '').toLowerCase()}`">{{ gateText(item.gate_status) }}</span>
              <span class="rag-chevron">⌄</span>
            </button>
            <div v-if="state.expandedRevision === item.index_revision" class="rag-version-detail">
              <dl>
                <div><dt>文档快照</dt><dd>{{ metric(item.document_count) }}</dd></div>
                <div><dt>向量覆盖</dt><dd>{{ percent(item.quality_report?.embedding_coverage) }}</dd></div>
                <div><dt>词法覆盖</dt><dd>{{ percent(item.quality_report?.lexical_coverage) }}</dd></div>
                <div><dt>父子结构</dt><dd>{{ percent(item.quality_report?.parent_coverage) }}</dd></div>
                <div v-if="item.quality_report?.evaluation"><dt>Recall@K</dt><dd>{{ percent(item.quality_report.evaluation.aggregate?.recall_at_k) }}</dd></div>
                <div v-if="item.quality_report?.evaluation"><dt>MRR@K</dt><dd>{{ percent(item.quality_report.evaluation.aggregate?.mrr_at_k) }}</dd></div>
                <div v-if="item.quality_report?.evaluation"><dt>NDCG@K</dt><dd>{{ percent(item.quality_report.evaluation.aggregate?.ndcg_at_k) }}</dd></div>
              </dl>
              <div class="rag-version-actions">
                <button v-if="item.status === 'BUILDING' && item.gate_status === 'PENDING'" class="btn ghost" type="button" @click="openEvaluation(item)">创建质量评测</button>
                <button v-if="item.status === 'BUILDING' && ['PASSED', 'WAIVED'].includes(item.gate_status)" class="btn primary" type="button" @click="askAction('activate', item)">激活此版本</button>
                <button v-if="item.status === 'RETIRED'" class="btn ghost" type="button" @click="askAction('rollback', item)">回滚到此版本</button>
                <button v-if="item.status === 'FAILED'" class="btn rag-danger-button" type="button" @click="askAction('cleanup', item)">清理失败版本</button>
              </div>
            </div>
          </article>
        </div>
      </template>
    </section>

    <div v-if="state.showBuild" class="rag-modal" role="presentation">
      <form class="rag-dialog" @submit.prevent="buildIndex">
        <header><div><span class="rag-kicker">Shadow index</span><h2>构建候选索引</h2><p>构建过程不会影响当前在线检索。</p></div><button type="button" aria-label="关闭" @click="state.showBuild = false">×</button></header>
        <label><span>版本名称</span><input v-model.trim="buildForm.revision" required maxlength="80" pattern="[A-Za-z0-9][A-Za-z0-9._-]{0,79}" /></label>
        <label><span>写入批次</span><select v-model.number="buildForm.qdrant_batch_size"><option :value="8">8（低内存）</option><option :value="16">16（推荐）</option><option :value="32">32（更快）</option><option :value="64">64（高吞吐）</option></select></label>
        <label class="rag-check"><input v-model="buildForm.require_evaluation" type="checkbox" /><span><strong>激活前要求质量评测</strong><small>推荐用于生产知识库，避免未验证版本直接承接流量。</small></span></label>
        <footer><button class="btn ghost" type="button" @click="state.showBuild = false">取消</button><button class="btn primary" type="submit" :disabled="!buildForm.revision || !!state.busy">{{ state.busy === 'build' ? '正在构建…' : '开始构建' }}</button></footer>
      </form>
    </div>

    <div v-if="state.showEvaluation" class="rag-modal" role="presentation">
      <form class="rag-dialog rag-evaluation-dialog" @submit.prevent="evaluateIndex">
        <header><div><span class="rag-kicker">Quality evaluation</span><h2>检索质量评测</h2><p>为真实问题指定应命中的文档，系统将以混合检索验证候选版本。</p></div><button type="button" aria-label="关闭" @click="state.showEvaluation = false">×</button></header>
        <div class="rag-thresholds"><label><span>Top K</span><input v-model.number="evaluationForm.top_k" type="number" min="1" max="50" /></label><label><span>最低 Recall</span><input v-model.number="evaluationForm.min_recall" type="number" min="0" max="1" step="0.05" /></label><label><span>最低 MRR</span><input v-model.number="evaluationForm.min_mrr" type="number" min="0" max="1" step="0.05" /></label><label><span>最低 NDCG</span><input v-model.number="evaluationForm.min_ndcg" type="number" min="0" max="1" step="0.05" /></label></div>
        <div class="rag-cases">
          <div v-for="(item, index) in evaluationForm.cases" :key="index" class="rag-case">
            <label><span>评测问题 {{ index + 1 }}</span><input v-model.trim="item.query" required placeholder="例如：如何申请企业退款？" /></label>
            <label><span>预期命中文档</span><select v-model="item.expected_doc_id" required><option disabled value="">请选择文档</option><option v-for="doc in indexedDocuments" :key="doc.doc_id" :value="doc.doc_id">{{ doc.filename || '未命名文档' }}</option></select></label>
            <button v-if="evaluationForm.cases.length > 1" class="rag-remove-case" type="button" aria-label="删除评测问题" @click="evaluationForm.cases.splice(index, 1)">×</button>
          </div>
          <button class="btn ghost" type="button" @click="addEvaluationCase">＋ 添加评测问题</button>
        </div>
        <footer><button class="btn ghost" type="button" @click="state.showEvaluation = false">取消</button><button class="btn primary" type="submit" :disabled="!!state.busy">{{ state.busy === 'evaluate' ? '正在评测…' : '运行质量评测' }}</button></footer>
      </form>
    </div>

    <div v-if="state.confirmAction" class="rag-modal" role="presentation">
      <div class="rag-dialog rag-confirm-dialog" role="dialog" aria-modal="true">
        <span class="rag-confirm-mark">!</span><h2>{{ state.confirmAction.title }}</h2><p>{{ state.confirmAction.body }}</p>
        <footer><button class="btn ghost" type="button" @click="state.confirmAction = null">取消</button><button class="btn" :class="state.confirmAction.kind === 'cleanup' ? 'rag-danger-button' : 'primary'" type="button" :disabled="!!state.busy" @click="confirmVersionAction">{{ state.confirmAction.action }}</button></footer>
      </div>
    </div>
  </section>
</template>

<style scoped>
.rag-workbench{display:grid;gap:16px;min-width:0;padding-bottom:24px;color:var(--ink)}
.rag-alert{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:10px;padding:11px 13px;border:1px solid color-mix(in srgb,var(--danger) 28%,transparent);border-radius:13px;color:var(--danger);background:var(--danger-soft)}.rag-alert>span{display:grid;place-items:center;width:22px;height:22px;border-radius:50%;background:color-mix(in srgb,var(--danger) 14%,transparent);font-weight:800}.rag-alert p{margin:0;font-size:12px}.rag-alert button{border:0;color:inherit;background:transparent}
.rag-summary-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.rag-summary-card{position:relative;display:grid;align-content:start;gap:7px;min-height:126px;padding:18px;border:1px solid var(--border);border-radius:19px;background:var(--bg-card);box-shadow:0 10px 30px rgba(0,0,0,.08);overflow:hidden}.rag-summary-card>span{color:var(--ink-muted);font-size:11px}.rag-summary-card>strong{overflow:hidden;color:var(--ink-strong);font-size:22px;letter-spacing:-.025em;text-overflow:ellipsis;white-space:nowrap}.rag-summary-card>strong em{font-size:11px;font-style:normal;font-weight:600}.rag-summary-card>small{color:var(--ink-muted);font-size:10px;line-height:1.5}.spectral-card::after{position:absolute;right:-35px;bottom:-65px;width:180px;height:140px;border-radius:50%;background:conic-gradient(from 40deg,rgba(22,139,255,.3),rgba(108,92,255,.28),rgba(38,214,185,.18),rgba(22,139,255,.3));filter:blur(22px);content:""}
.rag-lineage-panel{display:grid;gap:18px;padding:20px;border:1px solid var(--border);border-radius:22px;background:var(--bg-card);box-shadow:var(--shadow)}.rag-lineage-panel>header{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.rag-lineage-panel h3{margin:3px 0 5px;color:var(--ink-strong);font-size:19px}.rag-lineage-panel header p{max-width:640px;margin:0;color:var(--ink-muted);font-size:11px;line-height:1.55}.rag-kicker{color:var(--accent);font-size:9px;font-weight:760;letter-spacing:.1em;text-transform:uppercase}.rag-header-actions,.rag-version-actions{display:flex;gap:8px;flex-wrap:wrap}
.rag-loading{display:grid;gap:9px;padding:20px}.rag-loading span{height:54px;border-radius:15px;background:linear-gradient(90deg,var(--bg-soft),color-mix(in srgb,var(--bg-soft) 40%,var(--accent-soft)),var(--bg-soft));background-size:200% 100%;animation:rag-shimmer 1.3s linear infinite}.rag-empty{display:grid;justify-items:center;padding:46px 20px;text-align:center}.rag-empty-mark{display:grid;place-items:center;width:54px;height:54px;border:1px solid color-mix(in srgb,var(--accent) 24%,transparent);border-radius:18px;color:var(--accent);background:var(--accent-soft);font-size:25px}.rag-empty h3{margin:16px 0 6px}.rag-empty p{max-width:460px;margin:0 0 18px;color:var(--ink-muted);font-size:11px;line-height:1.6}
.rag-rail{position:relative;display:flex;align-items:flex-start;gap:12px;min-height:98px;padding:12px 8px 0;overflow-x:auto;scrollbar-width:thin}.rag-rail-line{position:absolute;top:25px;left:30px;right:30px;height:2px;background:linear-gradient(90deg,rgba(38,214,185,.36),rgba(22,139,255,.75),rgba(110,85,255,.5))}.rag-rail-node{position:relative;z-index:1;display:grid;justify-items:center;gap:4px;min-width:126px;padding:0 6px;border:0;color:var(--ink-muted);background:transparent}.rag-node-dot{width:13px;height:13px;margin:7px 0 5px;border:3px solid var(--bg-card);border-radius:50%;background:var(--ink-muted);box-shadow:0 0 0 1px var(--border)}.rag-rail-node.active .rag-node-dot{background:#168bff;box-shadow:0 0 0 1px rgba(22,139,255,.45),0 0 22px 7px rgba(77,126,255,.38)}.rag-rail-node.status-failed .rag-node-dot{background:var(--danger)}.rag-rail-node.status-building .rag-node-dot{background:#7c69ff}.rag-rail-node strong{max-width:130px;overflow:hidden;color:var(--ink);font-size:10px;text-overflow:ellipsis;white-space:nowrap}.rag-rail-node small{font-size:9px}
.rag-version-list{display:grid;gap:9px}.rag-version-card{overflow:hidden;border:1px solid var(--border);border-radius:16px;background:color-mix(in srgb,var(--bg-card) 75%,var(--bg-soft))}.rag-version-card.active{border-color:color-mix(in srgb,var(--accent) 38%,var(--border));box-shadow:inset 3px 0 var(--accent)}.rag-version-card.failed{border-color:color-mix(in srgb,var(--danger) 24%,var(--border))}.rag-version-main{display:grid;grid-template-columns:auto minmax(0,1fr) 70px auto auto;align-items:center;gap:12px;width:100%;padding:14px;border:0;color:inherit;background:transparent;text-align:left}.rag-version-icon{width:9px;height:9px;border-radius:50%;background:var(--ink-muted)}.rag-version-icon.status-active{background:#23c9a5;box-shadow:0 0 12px rgba(35,201,165,.45)}.rag-version-icon.status-building{background:#7966ff}.rag-version-icon.status-failed{background:var(--danger)}.rag-version-copy{display:grid;gap:4px;min-width:0}.rag-version-copy>span{display:flex;align-items:center;gap:8px;min-width:0}.rag-version-copy strong{overflow:hidden;color:var(--ink-strong);font-size:12px;text-overflow:ellipsis;white-space:nowrap}.rag-version-copy b{flex:0 0 auto;padding:3px 6px;border-radius:999px;color:#23a585;background:rgba(35,201,165,.11);font-size:8px}.rag-version-copy small{color:var(--ink-muted);font-size:9px}.rag-version-count{display:grid;text-align:right}.rag-version-count strong{font-size:12px}.rag-version-count small{color:var(--ink-muted);font-size:8px}.rag-gate{padding:5px 8px;border-radius:999px;color:var(--ink-muted);background:var(--bg-soft);font-size:9px;font-weight:720}.gate-passed,.gate-waived{color:var(--green)!important}.rag-gate.gate-passed,.rag-gate.gate-waived{background:color-mix(in srgb,var(--green) 12%,transparent)}.gate-pending{color:#a985ff!important}.rag-gate.gate-pending{background:rgba(124,105,255,.12)}.gate-failed{color:var(--danger)!important}.rag-gate.gate-failed{background:var(--danger-soft)}.rag-chevron{color:var(--ink-muted)}
.rag-version-detail{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:end;gap:14px;padding:0 14px 14px 35px}.rag-version-detail dl{display:grid;grid-template-columns:repeat(7,minmax(68px,1fr));gap:7px;margin:0}.rag-version-detail dl div{display:grid;gap:3px;padding:9px;border-radius:10px;background:var(--bg-soft)}.rag-version-detail dt{color:var(--ink-muted);font-size:8px}.rag-version-detail dd{margin:0;color:var(--ink);font-size:11px;font-weight:700}.rag-danger-button{border:1px solid color-mix(in srgb,var(--danger) 30%,transparent)!important;color:var(--danger)!important;background:var(--danger-soft)!important}
.rag-modal{position:fixed;z-index:120;inset:0;display:grid;place-items:center;padding:18px;background:rgba(5,7,12,.62);backdrop-filter:blur(12px);animation:rag-modal-in .18s ease}.rag-dialog{display:grid;gap:16px;width:min(520px,100%);max-height:calc(100dvh - 36px);padding:24px;overflow:auto;border:1px solid var(--border);border-radius:24px;color:var(--ink);background:color-mix(in srgb,var(--bg-base) 10%,var(--select-popup-bg));box-shadow:0 30px 90px rgba(0,0,0,.42)}.rag-dialog header{display:flex;align-items:flex-start;justify-content:space-between;gap:18px}.rag-dialog h2{margin:4px 0;color:var(--ink-strong);font-size:21px}.rag-dialog p{margin:0;color:var(--ink-muted);font-size:11px;line-height:1.55}.rag-dialog header>button{display:grid;place-items:center;flex:0 0 auto;width:32px;height:32px;border:0;border-radius:50%;color:var(--ink-muted);background:var(--bg-soft);font-size:20px}.rag-dialog>label,.rag-thresholds label,.rag-case label{display:grid;gap:6px;color:var(--ink-muted);font-size:10px}.rag-dialog input,.rag-dialog select{width:100%;min-height:42px;padding:9px 11px;border:1px solid var(--border);border-radius:11px;color:var(--select-popup-ink);background:var(--select-popup-bg);outline:none}.rag-dialog input:focus,.rag-dialog select:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}.rag-dialog select,.rag-dialog option,.rag-dialog optgroup{color:var(--select-popup-ink);background:var(--select-popup-bg);color-scheme:dark}:global(html[data-theme='light']) .rag-dialog select,:global(html[data-theme='light']) .rag-dialog option,:global(html[data-theme='light']) .rag-dialog optgroup{color-scheme:light}.rag-check{display:flex!important;align-items:flex-start;gap:10px;padding:12px;border:1px solid var(--border);border-radius:13px;background:var(--bg-soft)}.rag-check input{width:17px!important;min-height:auto;margin:2px 0 0;accent-color:var(--accent)}.rag-check>span{display:grid;gap:3px}.rag-check strong{color:var(--ink);font-size:11px}.rag-check small{font-size:9px}.rag-dialog footer{display:flex;justify-content:flex-end;gap:8px}.rag-evaluation-dialog{width:min(780px,100%)}.rag-thresholds{display:grid;grid-template-columns:repeat(4,1fr);gap:9px}.rag-cases{display:grid;gap:9px}.rag-case{position:relative;display:grid;grid-template-columns:minmax(0,1.4fr) minmax(180px,1fr) auto;align-items:end;gap:9px;padding:12px;border:1px solid var(--border);border-radius:14px;background:var(--bg-soft)}.rag-remove-case{width:30px;height:42px;border:0;color:var(--danger);background:transparent;font-size:18px}.rag-confirm-dialog{justify-items:center;width:min(420px,100%);text-align:center}.rag-confirm-mark{display:grid;place-items:center;width:46px;height:46px;border-radius:50%;color:var(--accent);background:var(--accent-soft);font-size:20px;font-weight:800}.rag-confirm-dialog footer{width:100%;justify-content:center}
@keyframes rag-shimmer{to{background-position:-200% 0}}@keyframes rag-modal-in{from{opacity:0;transform:scale(.985)}}
@media(max-width:900px){.rag-summary-grid{grid-template-columns:1fr 1fr}.rag-summary-card:last-child{grid-column:1/-1}.rag-version-detail{grid-template-columns:1fr;padding-left:14px}.rag-version-detail dl{grid-template-columns:repeat(4,1fr)}}
@media(max-width:620px){.rag-summary-grid{grid-template-columns:1fr}.rag-summary-card:last-child{grid-column:auto}.rag-lineage-panel{padding:15px;border-radius:18px}.rag-lineage-panel>header{flex-direction:column}.rag-header-actions{width:100%}.rag-header-actions .btn{flex:1}.rag-version-main{grid-template-columns:auto minmax(0,1fr) auto}.rag-version-count{display:none}.rag-gate{grid-column:2}.rag-chevron{grid-column:3;grid-row:1/3}.rag-version-detail dl{grid-template-columns:repeat(2,1fr)}.rag-thresholds{grid-template-columns:1fr 1fr}.rag-case{grid-template-columns:1fr}.rag-remove-case{position:absolute;top:3px;right:3px;height:30px}.rag-dialog{padding:18px;border-radius:20px}}
@media(prefers-reduced-motion:reduce){.rag-loading span,.rag-modal{animation:none}.rag-rail-node,.rag-version-main{transition:none}}
</style>
