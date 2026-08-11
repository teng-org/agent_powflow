<script setup>
defineProps({
  taskState: { type: Object, required: true },
  loadTaskMonitor: { type: Function, required: true },
  clearFinishedTasks: { type: Function, required: true },
  selectTaskForEvents: { type: Function, required: true },
  cancelTask: { type: Function, required: true },
})
</script>

<template>
  <section class="content">
    <div class="panel">
      <div class="panel-title">任务监控</div>
      <p class="panel-sub">Execution 任务与 Chat 记忆重排作业</p>
      <div class="row">
        <button class="btn ghost" @click="loadTaskMonitor">刷新任务</button>
        <button class="btn ghost danger-text" @click="clearFinishedTasks">清理已完成</button>
      </div>
      <div class="kpi-grid">
        <div class="kpi-card" v-for="(count, key) in taskState.statusCount" :key="key">
          <div class="kpi-name">{{ key }}</div>
          <div class="kpi-value">{{ count }}</div>
        </div>
      </div>
      <p class="banner err" v-if="taskState.error">{{ taskState.error }}</p>
      <div class="two-col">
        <div>
          <h3>Execution Tasks</h3>
          <div class="table-wrap">
            <table>
              <thead><tr><th>名称</th><th>类型</th><th>状态</th><th>阶段</th><th>更新时间</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="item in taskState.list" :key="item.task_id || item.id">
                  <td :title="item.title || item.name">{{ item.title || item.name || '-' }}</td>
                  <td>{{ item.task_type || item.type || '-' }}</td>
                  <td>{{ item.status || '-' }}</td>
                  <td>{{ item.stage || '-' }}</td>
                  <td>{{ item.updated_at || item.updatedAt || '-' }}</td>
                  <td>
                    <div class="row tight">
                      <button class="btn ghost" @click="selectTaskForEvents(item)">事件</button>
                      <button class="btn ghost danger-text" @click="cancelTask(item)">取消</button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
        <div>
          <h3>Memory Rerank Jobs</h3>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Job ID</th><th>Session</th><th>状态</th><th>创建时间</th></tr></thead>
              <tbody>
                <tr v-for="item in taskState.rerankJobs" :key="item.job_id || item.jobId">
                  <td :title="item.job_id || item.jobId">{{ item.job_id || item.jobId || '-' }}</td>
                  <td>{{ item.session_id || item.sessionId || '-' }}</td>
                  <td>{{ item.status || '-' }}</td>
                  <td>{{ item.created_at || item.createdAt || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
      <div class="kb-col">
        <h3>任务事件（{{ taskState.selectedTaskId || '未选择' }}）</h3>
        <div class="table-wrap mini">
          <table>
            <thead><tr><th>stage</th><th>status</th><th>message</th><th>time</th></tr></thead>
            <tbody>
              <tr v-for="(e, idx) in taskState.taskEvents" :key="idx">
                <td>{{ e.stage || '-' }}</td>
                <td>{{ e.status || '-' }}</td>
                <td :title="e.message || e.detail">{{ e.message || e.detail || '-' }}</td>
                <td>{{ e.created_at || e.createdAt || e.time || '-' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </section>
</template>
