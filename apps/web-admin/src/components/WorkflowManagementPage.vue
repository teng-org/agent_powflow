<script setup>
import { computed, onMounted } from 'vue'
import { useWorkflowAdmin } from '../composables/useWorkflowAdmin'

const props = defineProps({
  fetchJson: { type: Function, required: true },
  sectionDomId: { type: Function, required: true },
})

const workflows = useWorkflowAdmin(props.fetchJson)
const state = workflows.state

const validationIssues = computed(() => [
  ...(state.validation?.errors || []).map((item) => ({ ...item, level: 'error' })),
  ...(state.validation?.warnings || []).map((item) => ({ ...item, level: 'warning' })),
])

function when(value) {
  return value ? new Date(value).toLocaleString() : '—'
}

function statusLabel(status) {
  return { DRAFT: '草稿', PUBLISHED: '已发布', ARCHIVED: '已归档' }[status] || status || '未知'
}

async function safely(action) {
  try { await action() } catch { /* composable exposes the actionable error */ }
}

function confirmPublish() {
  if (!window.confirm('发布后会生成不可变版本。确认发布当前已保存的草稿？')) return
  safely(workflows.publish)
}

function confirmArchive() {
  if (!window.confirm('归档后不能继续编辑或发布，但历史版本仍会保留。确认归档？')) return
  safely(workflows.archive)
}

function confirmDelete() {
  if (!window.confirm(`永久删除“${state.item?.name || ''}”及其全部版本？此操作不可恢复。`)) return
  safely(workflows.remove)
}

onMounted(() => safely(workflows.loadList))
</script>

<template>
  <section class="workflow-page">
    <section class="domain-section" :id="sectionDomId('list')" data-menu-section="list">
      <div class="hero-row">
        <div>
          <h2 class="domain-section-title">工作流</h2>
          <p class="section-copy">管理租户级草稿、校验结果和不可变发布版本。运行始终引用已发布版本，不会读取正在修改的草稿。</p>
        </div>
        <div class="actions">
          <button class="btn ghost" :disabled="state.loading" @click="safely(workflows.loadList)">{{ state.loading ? '刷新中…' : '刷新' }}</button>
          <button class="btn primary" @click="workflows.newDraft">新建工作流</button>
        </div>
      </div>

      <p v-if="state.error" class="banner err" role="alert">{{ state.error }}</p>
      <p v-if="state.message" class="banner ok" role="status">{{ state.message }}</p>

      <div class="workflow-layout">
        <aside class="workflow-list" aria-label="工作流列表">
          <button
            v-for="item in state.items"
            :key="item.workflow_id"
            type="button"
            :class="['workflow-row', { selected: state.selectedId === item.workflow_id }]"
            @click="safely(() => workflows.select(item.workflow_id))"
          >
            <span class="workflow-row-main"><strong>{{ item.name }}</strong><small>{{ item.description || '暂无说明' }}</small></span>
            <span :class="['status-pill', String(item.status || '').toLowerCase()]">{{ statusLabel(item.status) }}</span>
            <span class="workflow-meta">v{{ item.latest_version_no || 0 }} · 修订 {{ item.revision }}</span>
          </button>
          <div v-if="!state.loading && !state.items.length" class="empty-state">
            <strong>还没有工作流</strong>
            <span>创建一个草稿，从开始、模型调用到结束节点逐步扩展。</span>
          </div>
        </aside>

        <article class="workflow-summary">
          <template v-if="state.item">
            <div class="eyebrow">当前选择</div>
            <div class="summary-title"><strong>{{ state.item.name }}</strong><span :class="['status-pill', String(state.item.status || '').toLowerCase()]">{{ statusLabel(state.item.status) }}</span></div>
            <dl>
              <dt>Workflow ID</dt><dd class="mono">{{ state.item.workflow_id }}</dd>
              <dt>最新版本</dt><dd>v{{ state.item.latest_version_no || 0 }}</dd>
              <dt>草稿修订</dt><dd>{{ state.item.revision }}</dd>
              <dt>更新时间</dt><dd>{{ when(state.item.updated_at) }}</dd>
            </dl>
          </template>
          <template v-else>
            <div class="eyebrow">编辑器</div>
            <strong>{{ state.editor.name || '新工作流' }}</strong>
            <p>保存后会获得 Workflow ID，并启用服务端校验和发布。</p>
          </template>
        </article>
      </div>
    </section>

    <section class="domain-section" :id="sectionDomId('editor')" data-menu-section="editor">
      <div class="section-heading">
        <div><h2 class="domain-section-title">草稿编辑</h2><p class="section-copy">采用乐观锁保存；其他管理员更新过同一草稿时，服务端会拒绝覆盖。</p></div>
        <div class="actions">
          <button class="btn ghost" :disabled="!state.item || Boolean(state.action)" @click="safely(workflows.validate)">重新校验</button>
          <button class="btn primary" :disabled="state.saving || workflows.selectedIsArchived.value" @click="safely(workflows.save)">{{ state.saving ? '保存中…' : '保存草稿' }}</button>
        </div>
      </div>

      <div class="form-grid">
        <label>名称<input v-model="state.editor.name" maxlength="128" :disabled="workflows.selectedIsArchived.value" /></label>
        <label>说明<input v-model="state.editor.description" maxlength="1000" :disabled="workflows.selectedIsArchived.value" placeholder="说明用途、输入和预期输出" /></label>
      </div>
      <div class="editor-grid">
        <label><span>Workflow DSL</span><textarea v-model="state.editor.dslText" class="code-editor" rows="22" spellcheck="false" :disabled="workflows.selectedIsArchived.value" /></label>
        <label><span>Canvas Layout</span><textarea v-model="state.editor.layoutText" class="code-editor" rows="22" spellcheck="false" :disabled="workflows.selectedIsArchived.value" /></label>
      </div>

      <div :class="['validation-card', state.validation?.valid ? 'valid' : 'invalid']">
        <div>
          <span class="eyebrow">服务端校验</span>
          <strong v-if="state.validation?.valid">DSL 可以发布</strong>
          <strong v-else-if="state.validation">发现 {{ validationIssues.length }} 个问题</strong>
          <strong v-else>保存草稿后执行校验</strong>
        </div>
        <ul v-if="validationIssues.length" class="issue-list">
          <li v-for="(issue, index) in validationIssues" :key="`${issue.code}-${index}`">
            <span :class="['issue-level', issue.level]">{{ issue.level === 'error' ? '错误' : '警告' }}</span>
            <div><strong>{{ issue.code }}</strong><p>{{ issue.message }}</p><small v-if="issue.node_id || issue.edge_id">{{ issue.node_id ? `节点 ${issue.node_id}` : `边 ${issue.edge_id}` }}</small><small v-if="issue.suggestion">{{ issue.suggestion }}</small></div>
          </li>
        </ul>
      </div>

      <div class="danger-zone" v-if="state.item">
        <div><strong>生命周期操作</strong><p>发布生成不可变快照；归档会冻结草稿；只有归档后才能永久删除。</p></div>
        <div class="actions">
          <button class="btn primary" :disabled="!workflows.canPublish.value || Boolean(state.action)" @click="confirmPublish">{{ state.action === 'publish' ? '发布中…' : '发布新版本' }}</button>
          <button class="btn ghost" :disabled="workflows.selectedIsArchived.value || Boolean(state.action)" @click="confirmArchive">归档</button>
          <button class="btn danger" :disabled="!workflows.selectedIsArchived.value || Boolean(state.action)" @click="confirmDelete">永久删除</button>
        </div>
      </div>
    </section>

    <section class="domain-section" :id="sectionDomId('versions')" data-menu-section="versions">
      <div class="section-heading"><div><h2 class="domain-section-title">发布版本</h2><p class="section-copy">每个版本包含不可变 DSL、布局和 SHA-256 校验和，可用于运行审计。</p></div></div>
      <div class="version-layout">
        <div class="table-wrap">
          <table>
            <thead><tr><th>版本</th><th>校验和</th><th>发布人</th><th>发布时间</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="version in state.versions" :key="version.version_id">
                <td><strong>v{{ version.version_no }}</strong></td><td class="mono checksum">{{ version.checksum }}</td><td class="mono">{{ version.published_by }}</td><td>{{ when(version.published_at) }}</td>
                <td><button class="btn ghost compact" :disabled="Boolean(state.action)" @click="safely(() => workflows.viewVersion(version.version_no))">查看</button></td>
              </tr>
              <tr v-if="!state.versions.length"><td colspan="5" class="empty-cell">尚未发布版本</td></tr>
            </tbody>
          </table>
        </div>
        <article v-if="state.selectedVersion" class="version-detail">
          <div class="summary-title"><strong>v{{ state.selectedVersion.version_no }}</strong><span class="status-pill published">不可变</span></div>
          <p class="mono checksum-full">{{ state.selectedVersion.checksum }}</p>
          <details open><summary>DSL</summary><pre>{{ JSON.stringify(state.selectedVersion.dsl, null, 2) }}</pre></details>
          <details><summary>Layout</summary><pre>{{ JSON.stringify(state.selectedVersion.layout, null, 2) }}</pre></details>
        </article>
      </div>
    </section>
  </section>
</template>

<style scoped>
.workflow-page{display:grid;gap:12px}.hero-row,.section-heading,.danger-zone{display:flex;justify-content:space-between;align-items:flex-start;gap:20px}.section-copy{margin:4px 0 0;color:#687a92;max-width:760px;line-height:1.55}.actions{display:flex;gap:8px;flex-wrap:wrap}.workflow-layout{display:grid;grid-template-columns:minmax(0,1.7fr) minmax(260px,.8fr);gap:14px;margin-top:14px}.workflow-list{border:1px solid #dce6f2;border-radius:12px;overflow:hidden;background:#fff}.workflow-row{width:100%;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 12px;padding:13px 14px;text-align:left;border:0;border-bottom:1px solid #edf2f8;background:#fff;color:inherit;cursor:pointer;transition:background-color 140ms ease,transform 140ms cubic-bezier(.23,1,.32,1)}.workflow-row:last-child{border-bottom:0}.workflow-row:active,.btn:active:not(:disabled){transform:scale(.97)}.workflow-row.selected{background:#edf4ff}.workflow-row-main{display:grid;gap:3px;min-width:0}.workflow-row-main small{color:#687a92;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.workflow-meta{font-size:11px;color:#7a8ba1}.status-pill{align-self:start;display:inline-flex;padding:3px 8px;border-radius:999px;font-size:11px;font-weight:750;background:#eef1f5;color:#566477}.status-pill.draft{background:#fff4d8;color:#865a00}.status-pill.published{background:#e5f6eb;color:#18723a}.status-pill.archived{background:#eef0f3;color:#5c6675}.workflow-summary{border:1px solid #dce6f2;border-radius:12px;padding:16px;background:linear-gradient(160deg,#fbfdff,#f3f7fc)}.workflow-summary>strong{font-size:18px}.workflow-summary p{color:#687a92;line-height:1.5}.summary-title{display:flex;align-items:center;justify-content:space-between;gap:12px;font-size:18px}.eyebrow{text-transform:uppercase;letter-spacing:.08em;color:#74869d;font-size:10px;font-weight:800;margin-bottom:6px}.workflow-summary dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:8px 12px;margin:16px 0 0;font-size:12px}.workflow-summary dt{color:#74869d}.workflow-summary dd{margin:0;word-break:break-all}.form-grid,.editor-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:14px}.form-grid label,.editor-grid label{display:grid;gap:7px;color:#53667e;font-size:12px;font-weight:700}.code-editor{font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;resize:vertical;tab-size:2;min-height:360px;background:#101b2a;color:#dce8f8;border-color:#263a52}.code-editor:disabled{opacity:.72}.validation-card{display:grid;grid-template-columns:220px minmax(0,1fr);gap:18px;margin-top:14px;padding:14px;border:1px solid #dce6f2;border-left:4px solid #91a2b8;border-radius:10px;background:#fafcff}.validation-card.valid{border-left-color:#2f9e5b;background:#f4fbf6}.validation-card.invalid{border-left-color:#d49a21}.validation-card>div{display:grid;align-content:start}.issue-list{display:grid;gap:8px;list-style:none;margin:0;padding:0}.issue-list li{display:grid;grid-template-columns:auto 1fr;gap:9px}.issue-list p{margin:2px 0;color:#455b74}.issue-list small{display:block;color:#74869d}.issue-level{height:fit-content;padding:2px 6px;border-radius:5px;font-size:10px;font-weight:800}.issue-level.error{background:#fee9e7;color:#a92d24}.issue-level.warning{background:#fff3d6;color:#855900}.danger-zone{margin-top:14px;padding:14px;border:1px solid #dce6f2;border-radius:10px}.danger-zone p{margin:4px 0 0;color:#687a92}.btn{transition:transform 140ms cubic-bezier(.23,1,.32,1),background-color 140ms ease,border-color 140ms ease}.btn.danger{color:#b42318;border-color:#efc3bf;background:#fff8f7}.btn.compact{min-height:30px;padding:4px 8px}.version-layout{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(320px,1fr);gap:14px}.checksum{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.version-detail{min-width:0;border:1px solid #dce6f2;border-radius:12px;padding:14px}.checksum-full{font-size:11px;color:#687a92;word-break:break-all}.version-detail details{margin-top:10px}.version-detail summary{cursor:pointer;font-weight:700;color:#405975}.version-detail pre{margin-top:8px;max-height:280px}.empty-state{display:grid;gap:5px;padding:28px 18px;text-align:center;color:#687a92}.empty-state strong{color:#405975}.empty-cell{text-align:center;padding:28px;color:#74869d}.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}@media(hover:hover) and (pointer:fine){.workflow-row:hover{background:#f6f9fd}}@media(max-width:960px){.workflow-layout,.version-layout,.validation-card{grid-template-columns:1fr}.editor-grid{grid-template-columns:1fr}.hero-row,.section-heading,.danger-zone{align-items:stretch;flex-direction:column}}@media(prefers-reduced-motion:reduce){.workflow-row,.btn{transition:background-color 100ms ease,border-color 100ms ease}.workflow-row:active,.btn:active:not(:disabled){transform:none}}
</style>
