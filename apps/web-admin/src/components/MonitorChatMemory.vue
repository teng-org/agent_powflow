<script setup>
import { computed, ref } from 'vue'
import vInteractiveRow from './common/interactiveRowDirective'

const props = defineProps({
  taskState: { type: Object, required: true },
  chatSettingState: { type: Object, required: true },
  loadTaskMonitor: { type: Function, required: true },
  loadChatSessions: { type: Function, required: true },
  selectSession: { type: Function, required: true },
  loadChatMessages: { type: Function, required: true },
  queryRunStatus: { type: Function, required: true },
  cancelRunStatus: { type: Function, required: true },
  formatJson: { type: Function, required: true },
  sectionDomId: { type: Function, default: null },
})

function secId(name) {
  return props.sectionDomId ? props.sectionDomId(name) : ''
}

const selectedSession = ref(null)
const selectedRerankJob = ref(null)
const selectedChatTask = ref(null)
const chatTaskKeyword = ref('')

const sessionRows = computed(() => {
  const list = Array.isArray(props.chatSettingState.sessions) ? props.chatSettingState.sessions : []
  return list.map((x) => ({
    id: x.session_id || x.id || '-',
    title: x.title || '-',
    updated: x.updated_at || x.updatedAt || x.created_at || '-',
  }))
})

const messageRoleStats = computed(() => {
  const map = { user: 0, assistant: 0, system: 0, tool: 0, other: 0 }
  for (const msg of props.chatSettingState.messages || []) {
    const role = String(msg?.role || '').toLowerCase()
    if (role in map) map[role] += 1
    else map.other += 1
  }
  return map
})

const detailTitle = computed(() => {
  if (selectedChatTask.value) return '详情：对话/记忆任务'
  if (selectedRerankJob.value) return '详情：记忆重排作业'
  if (selectedSession.value) return '详情：会话'
  return ''
})

const detailPayload = computed(() => {
  return selectedChatTask.value || selectedRerankJob.value || selectedSession.value || {}
})

const hasDetailSelection = computed(() => !!(selectedChatTask.value || selectedRerankJob.value || selectedSession.value))

const filteredChatTasks = computed(() => {
  const keyword = chatTaskKeyword.value.trim().toLowerCase()
  const tasks = Array.isArray(props.taskState.list) ? props.taskState.list : []
  const chatLike = tasks.filter((x) => {
    const type = String(x?.task_type || x?.type || '').toLowerCase()
    const title = String(x?.title || x?.name || '').toLowerCase()
    return type.includes('chat') || type.includes('memory') || title.includes('chat') || title.includes('memory')
  })
  if (!keyword) return chatLike
  return chatLike.filter((x) => JSON.stringify(x).toLowerCase().includes(keyword))
})

async function onSelectSession(item) {
  selectedSession.value = item
  await props.selectSession(item)
}

async function refreshAll() {
  await Promise.all([props.loadTaskMonitor(), props.loadChatSessions()])
}
</script>

<template>
  <section class="content">
    <div class="monitor-grid" :class="{ 'has-detail': hasDetailSelection }">
      <div class="panel">
      <div class="panel-title">对话与记忆监控</div>
      <p class="panel-sub">会话运行状态、记忆重排作业和运行详情监控</p>

      <div class="row">
        <button class="btn ghost" :disabled="taskState.loading || chatSettingState.loading" @click="refreshAll">刷新任务与会话</button>
        <button class="btn ghost" :disabled="chatSettingState.loading" @click="loadChatSessions">刷新会话列表</button>
        <button class="btn ghost" :disabled="chatSettingState.loading || !chatSettingState.selectedSessionId" @click="loadChatMessages">刷新会话消息</button>
        <button class="btn ghost" :disabled="chatSettingState.loading || !chatSettingState.run.runId" @click="queryRunStatus">查询 Run</button>
        <button class="btn ghost danger-text" :disabled="chatSettingState.loading || !chatSettingState.run.runId" @click="cancelRunStatus">取消 Run</button>
      </div>

      <div class="domain-section" :id="secId('overview')" data-menu-section="overview">
        <h2 class="domain-section-title">概览</h2>
        <div class="kpi-grid">
          <div class="kpi-card">
            <div class="kpi-name">会话数量</div>
            <div class="kpi-value">{{ chatSettingState.sessions?.length || 0 }}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-name">消息数量（当前会话）</div>
            <div class="kpi-value">{{ chatSettingState.messages?.length || 0 }}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-name">记忆重排作业数</div>
            <div class="kpi-value">{{ taskState.rerankJobs?.length || 0 }}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-name">Execution 运行任务数</div>
            <div class="kpi-value">{{ taskState.list?.length || 0 }}</div>
          </div>
        </div>
        <div class="kb-col">
          <h3>消息角色分布（当前会话）</h3>
          <div class="kpi-grid">
            <div class="kpi-card">
              <div class="kpi-name">user</div>
              <div class="kpi-value">{{ messageRoleStats.user }}</div>
            </div>
            <div class="kpi-card">
              <div class="kpi-name">assistant</div>
              <div class="kpi-value">{{ messageRoleStats.assistant }}</div>
            </div>
            <div class="kpi-card">
              <div class="kpi-name">system</div>
              <div class="kpi-value">{{ messageRoleStats.system }}</div>
            </div>
            <div class="kpi-card">
              <div class="kpi-name">other</div>
              <div class="kpi-value">{{ messageRoleStats.other + messageRoleStats.tool }}</div>
            </div>
          </div>
        </div>
      </div>

      <div class="domain-section" :id="secId('sessions')" data-menu-section="sessions">
        <h2 class="domain-section-title">会话与作业</h2>
      <div class="two-col">
        <div class="kb-col">
          <h3>会话列表（前 100）</h3>
          <div class="table-wrap short">
            <table>
              <thead><tr><th>session_id</th><th>title</th><th>updated_at</th></tr></thead>
              <tbody>
                <tr
                  v-for="x in sessionRows"
                  :key="x.id"
                  v-interactive-row="{ activate: () => onSelectSession(chatSettingState.sessions.find((s) => (s.session_id || s.id || '-') === x.id)), label: `查看会话 ${x.title || x.id}`, current: selectedSession && (selectedSession.session_id || selectedSession.id) === x.id }"
                  :class="{ selected: selectedSession && (selectedSession.session_id || selectedSession.id) === x.id }"
                  @click="onSelectSession(chatSettingState.sessions.find((s) => (s.session_id || s.id || '-') === x.id))"
                >
                  <td>{{ x.id }}</td>
                  <td>{{ x.title || '-' }}</td>
                  <td>{{ x.updated }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="kb-col">
          <h3>记忆重排作业（Rerank Jobs）</h3>
          <div class="table-wrap short">
            <table>
              <thead><tr><th>job_id</th><th>session_id</th><th>status</th><th>progress</th><th>created_at</th></tr></thead>
              <tbody>
                <tr
                  v-for="(x, idx) in taskState.rerankJobs"
                  :key="x.job_id || x.jobId || idx"
                  v-interactive-row="{ activate: () => { selectedRerankJob = x }, label: `查看重排作业 ${x.job_id || x.jobId || ''}`, current: selectedRerankJob === x }"
                  :class="{ selected: selectedRerankJob === x }"
                  @click="selectedRerankJob = x"
                >
                  <td>{{ x.job_id || x.jobId || '-' }}</td>
                  <td>{{ x.session_id || x.sessionId || '-' }}</td>
                  <td>{{ x.status || '-' }}</td>
                  <td>{{ x.progress || x.progress_pct || '-' }}</td>
                  <td>{{ x.created_at || x.createdAt || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
      </div>

      <div class="kb-col domain-section" :id="secId('tasks')" data-menu-section="tasks">
        <h2 class="domain-section-title">任务与运行</h2>
        <h3>对话/记忆相关任务（Execution）</h3>
        <div class="row">
          <label>关键词筛选<input v-model="chatTaskKeyword" placeholder="task_type / title / status" /></label>
        </div>
        <div class="table-wrap short">
          <table>
            <thead><tr><th>task_id</th><th>type</th><th>status</th><th>stage</th><th>updated_at</th></tr></thead>
            <tbody>
              <tr
                v-for="x in filteredChatTasks"
                :key="x.task_id || x.id"
                v-interactive-row="{ activate: () => { selectedChatTask = x }, label: `查看对话任务 ${x.task_id || x.id || ''}`, current: selectedChatTask === x }"
                :class="{ selected: selectedChatTask === x }"
                @click="selectedChatTask = x"
              >
                <td>{{ x.task_id || x.id || '-' }}</td>
                <td>{{ x.task_type || x.type || '-' }}</td>
                <td>{{ x.status || '-' }}</td>
                <td>{{ x.stage || '-' }}</td>
                <td>{{ x.updated_at || x.updatedAt || '-' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="kb-col">
        <h3>Run 状态查询</h3>
        <div class="row">
          <label>Run ID<input v-model="chatSettingState.run.runId" placeholder="chat run id" /></label>
        </div>
        <p class="banner err" v-if="chatSettingState.run.error">{{ chatSettingState.run.error }}</p>
        <pre>{{ formatJson(chatSettingState.run.detail) }}</pre>
      </div>

      <div class="json-grid">
        <div>
          <h3>选中：Session</h3>
          <pre>{{ formatJson(selectedSession) }}</pre>
        </div>
        <div>
          <h3>选中：Rerank Job</h3>
          <pre>{{ formatJson(selectedRerankJob) }}</pre>
        </div>
      </div>
      <div class="kb-col">
        <h3>选中：对话/记忆任务</h3>
        <pre>{{ formatJson(selectedChatTask) }}</pre>
      </div>
      </div>

      <aside
        v-if="hasDetailSelection"
        class="panel detail-panel domain-section"
        :id="secId('detail')"
        data-menu-section="detail"
      >
        <h2 class="domain-section-title">详情</h2>
        <div class="panel-title small">{{ detailTitle }}</div>
        <pre>{{ formatJson(detailPayload) }}</pre>
        <div class="row">
          <button class="btn ghost" :disabled="chatSettingState.loading || !chatSettingState.selectedSessionId" @click="loadChatMessages">刷新该会话消息</button>
          <button class="btn ghost" :disabled="chatSettingState.loading || !chatSettingState.run.runId" @click="queryRunStatus">刷新 Run</button>
        </div>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.monitor-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
}

.monitor-grid.has-detail {
  grid-template-columns: minmax(0, 1fr) 360px;
}

.detail-panel {
  position: sticky;
  top: 12px;
  max-height: calc(100vh - 24px);
  overflow: auto;
}

.panel-title.small {
  font-size: 16px;
}

@media (max-width: 1400px) {
  .monitor-grid {
    grid-template-columns: 1fr;
  }

  .detail-panel {
    position: static;
    max-height: none;
  }
}
</style>
