<script setup>
defineProps({
  kbState: { type: Object, required: true },
  runChunk: { type: Function, required: true },
  summarizeDoc: { type: Function, required: true },
  commitChunkDrafts: { type: Function, required: true },
  getChunkId: { type: Function, required: true },
  saveChunkDraft: { type: Function, required: true },
  clearChunkDraft: { type: Function, required: true },
  toggleChunkFreeze: { type: Function, required: true },
  loadKbTasks: { type: Function, required: true },
  getTaskId: { type: Function, required: true },
  selectKbTask: { type: Function, required: true },
  loadBatchDetail: { type: Function, required: true },
  retryBatchFailed: { type: Function, required: true },
})
</script>

<template>
  <div class="kb-col">
    <h3>切片预览（{{ kbState.selectedDocName || '未选择文档' }}）</h3>
    <div class="row">
      <label>method
        <select v-model="kbState.chunkRun.chunk_method">
          <option value="sentence">sentence</option>
          <option value="fixed">fixed</option>
          <option value="page">page</option>
          <option value="delimiter">delimiter</option>
        </select>
      </label>
      <label>size<input v-model.number="kbState.chunkRun.chunk_size" type="number" /></label>
      <label>overlap<input v-model.number="kbState.chunkRun.chunk_overlap" type="number" /></label>
      <button class="btn ghost" :disabled="!kbState.selectedDocId" @click="runChunk">重切片</button>
      <button class="btn ghost" :disabled="!kbState.selectedDocId" @click="summarizeDoc">文档总结</button>
      <button class="btn ghost" :disabled="!kbState.selectedDocId" @click="commitChunkDrafts">提交草稿</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>chunk_id</th><th>text_preview</th><th>draft</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="item in kbState.chunks" :key="getChunkId(item)">
            <td :title="getChunkId(item)">{{ getChunkId(item) }}</td>
            <td :title="item.text || item.text_preview">{{ item.text_preview || item.text || '-' }}</td>
            <td><textarea v-model="kbState.chunkDraftText[getChunkId(item)]" rows="2" placeholder="编辑草稿文本" /></td>
            <td><div class="row tight">
              <button class="btn ghost" @click="saveChunkDraft(item)">保存草稿</button>
              <button class="btn ghost danger-text" @click="clearChunkDraft(item)">清除草稿</button>
              <button class="btn ghost" @click="toggleChunkFreeze(item)">{{ item.frozen ? '解冻' : '冻结' }}</button>
            </div></td>
          </tr>
        </tbody>
      </table>
    </div>

    <h3>KB 任务列表</h3>
    <div class="row"><button class="btn ghost" :disabled="!kbState.selectedKbId" @click="loadKbTasks">刷新任务</button></div>
    <div class="table-wrap short">
      <table>
        <thead><tr><th>task_id</th><th>type</th><th>status</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="t in kbState.tasks" :key="getTaskId(t)" :class="{ selected: kbState.selectedTaskId === getTaskId(t) }">
            <td :title="getTaskId(t)">{{ getTaskId(t) }}</td>
            <td>{{ t.task_type || t.type || '-' }}</td>
            <td>{{ t.status || '-' }}</td>
            <td><button class="btn ghost" @click="selectKbTask(t)">事件</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <template v-if="kbState.batchDetail?.item">
      <h3>批次详情（{{ kbState.batchDetail.item.batch_name }}）</h3>
      <div class="row">
        <span>成功 {{ kbState.batchDetail.item.succeeded_items }}</span>
        <span>失败 {{ kbState.batchDetail.item.failed_items }}</span>
        <span>取消 {{ kbState.batchDetail.item.cancelled_items }}</span>
        <span>运行 {{ kbState.batchDetail.item.running_items }}</span>
        <button class="btn ghost" @click="loadBatchDetail">刷新批次</button>
        <button class="btn ghost" :disabled="!kbState.batchDetail.item.failed_items" @click="retryBatchFailed">重试失败项</button>
      </div>
      <div class="table-wrap mini">
        <table>
          <thead><tr><th>文档</th><th>类型</th><th>状态</th><th>进度</th></tr></thead>
          <tbody>
            <tr v-for="child in kbState.batchDetail.items.filter((item) => !item.batch_is_parent)" :key="child.task_id">
              <td :title="child.doc_id">{{ child.filename || child.doc_id }}</td>
              <td>{{ child.task_type }}</td>
              <td>{{ child.status }}</td>
              <td>{{ child.progress_pct || 0 }}%</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <h3>任务事件（{{ kbState.selectedTaskId || '未选择' }}）</h3>
    <div class="table-wrap mini">
      <table>
        <thead><tr><th>stage</th><th>status</th><th>message</th><th>time</th></tr></thead>
        <tbody>
          <tr v-for="(e, idx) in kbState.taskEvents" :key="idx">
            <td>{{ e.stage || '-' }}</td>
            <td>{{ e.status || '-' }}</td>
            <td :title="e.message || e.detail">{{ e.message || e.detail || '-' }}</td>
            <td>{{ e.created_at || e.createdAt || e.time || '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
