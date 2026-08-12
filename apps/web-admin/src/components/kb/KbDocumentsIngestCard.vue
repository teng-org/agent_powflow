<script setup>
import { computed } from 'vue'

const props = defineProps({
  kbState: { type: Object, required: true },
  loadDocs: { type: Function, required: true },
  summarizeKb: { type: Function, required: true },
  getDocId: { type: Function, required: true },
  getDocName: { type: Function, required: true },
  selectDoc: { type: Function, required: true },
  summarizeDoc: { type: Function, required: true },
  deleteDoc: { type: Function, required: true },
  toggleDocumentFreeze: { type: Function, required: true },
  ingestDocText: { type: Function, required: true },
  onUploadFileChange: { type: Function, required: true },
  uploadDocFile: { type: Function, required: true },
  toggleDocSelection: { type: Function, required: true },
  toggleAllDocs: { type: Function, required: true },
  submitDocumentBatch: { type: Function, required: true },
})

const selectedCount = computed(() => props.kbState.selectedDocIds?.length || 0)
const allSelected = computed(() => props.kbState.docs.length > 0 && selectedCount.value === props.kbState.docs.length)
const batchBlocked = computed(() => {
  if (!selectedCount.value || props.kbState.loading) return true
  if (props.kbState.batch.operation === 'move' && !props.kbState.batch.target_kb_id) return true
  if (props.kbState.batch.operation === 'tag' && !props.kbState.batch.tags_csv.trim()) return true
  return false
})
</script>

<template>
  <div class="kb-col">
    <h3>文档列表（{{ kbState.selectedKbName || '未选择知识库' }}）</h3>
    <div class="row">
      <button class="btn ghost" :disabled="!kbState.selectedKbId" @click="loadDocs">刷新文档</button>
      <button class="btn ghost" :disabled="!kbState.selectedKbId" @click="summarizeKb">触发库总结</button>
    </div>
    <div class="table-wrap short">
      <table>
        <thead><tr><th><input type="checkbox" :checked="allSelected" aria-label="选择全部文档" @change="toggleAllDocs($event.target.checked)" /></th><th>文档名</th><th>ID</th><th>chunks</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="item in kbState.docs" :key="getDocId(item)" :class="{ selected: kbState.selectedDocId === getDocId(item) }">
            <td><input type="checkbox" :checked="kbState.selectedDocIds.includes(getDocId(item))" :aria-label="`选择文档 ${getDocName(item)}`" @change="toggleDocSelection(getDocId(item), $event.target.checked)" /></td>
            <td :title="getDocName(item)">{{ getDocName(item) }}</td>
            <td :title="getDocId(item)">{{ getDocId(item) }}</td>
            <td>{{ item.chunk_count || item.chunks || '-' }}</td>
            <td><div class="row tight">
              <button class="btn ghost" @click="selectDoc(item)">切片</button>
              <button class="btn ghost" @click="kbState.selectedDocId = getDocId(item); summarizeDoc()">总结</button>
              <button class="btn ghost" @click="toggleDocumentFreeze(item)">{{ item.frozen ? '解冻' : '冻结' }}</button>
              <button class="btn ghost danger-text" @click="deleteDoc(item)">删除</button>
            </div></td>
          </tr>
        </tbody>
      </table>
    </div>

    <h3>批量处理 <span class="muted">已选 {{ selectedCount }} 项</span></h3>
    <div class="grid three">
      <label>操作
        <select v-model="kbState.batch.operation">
          <option value="chunk">批量分块</option>
          <option value="embed">批量重索引</option>
          <option value="summarize">批量总结</option>
          <option value="tag">批量标签</option>
          <option value="move">批量移动</option>
          <option value="delete">批量删除</option>
        </select>
      </label>
      <label>批次名称<input v-model="kbState.batch.batch_name" placeholder="留空自动生成" /></label>
      <label v-if="kbState.batch.operation === 'move'">目标知识库
        <select v-model="kbState.batch.target_kb_id">
          <option value="">请选择</option>
          <option v-for="kb in kbState.items.filter((kb) => (kb.kb_id || kb.kbId || kb.id) !== kbState.selectedKbId)" :key="kb.kb_id || kb.kbId || kb.id" :value="kb.kb_id || kb.kbId || kb.id">{{ kb.name || kb.kb_id || kb.kbId || kb.id }}</option>
        </select>
      </label>
      <label v-if="kbState.batch.operation === 'tag'">标签（逗号分隔）<input v-model="kbState.batch.tags_csv" placeholder="reviewed, important" /></label>
      <label v-if="kbState.batch.operation === 'tag'">标签模式
        <select v-model="kbState.batch.mode"><option value="add">追加</option><option value="replace">替换</option></select>
      </label>
    </div>
    <p v-if="kbState.batch.operation === 'chunk' || kbState.batch.operation === 'embed'" class="muted">使用下方“切片与任务”中的 method / size / overlap 参数。</p>
    <div class="row">
      <button class="btn primary" :disabled="batchBlocked" @click="submitDocumentBatch">提交批处理</button>
      <button class="btn ghost" :disabled="!selectedCount" @click="toggleAllDocs(false)">清空选择</button>
    </div>

    <h3>文本入库（ingest）</h3>
    <div class="grid three">
      <label>doc_id<input v-model="kbState.ingest.doc_id" placeholder="可选" /></label>
      <label>filename<input v-model="kbState.ingest.filename" /></label>
      <label>chunk_method
        <select v-model="kbState.ingest.chunk_method">
          <option value="sentence">sentence</option>
          <option value="fixed">fixed</option>
          <option value="page">page</option>
          <option value="delimiter">delimiter</option>
        </select>
      </label>
    </div>
    <div class="grid two">
      <label>chunk_size<input v-model.number="kbState.ingest.chunk_size" type="number" /></label>
      <label>chunk_overlap<input v-model.number="kbState.ingest.chunk_overlap" type="number" /></label>
    </div>
    <label>content<textarea v-model="kbState.ingest.content" rows="4" /></label>
    <div class="row"><button class="btn primary" :disabled="!kbState.selectedKbId" @click="ingestDocText">提交文本入库</button></div>

    <h3>文件上传（multipart）</h3>
    <div class="grid two">
      <label>处理模式
        <select v-model="kbState.upload.processMode">
          <option value="UPLOAD_CLEAN_CHUNK_EMBED">清洗、切片并建立索引</option>
          <option value="UPLOAD_AND_CLEAN">仅上传并清洗</option>
          <option value="UPLOAD_ONLY">仅上传原始内容</option>
        </select>
      </label>
      <label>选择文件
        <input :key="kbState.upload.inputVersion" type="file" multiple @change="onUploadFileChange" />
      </label>
    </div>
    <p class="muted">
      三种模式都会创建可审计的控制平面任务；非索引模式完成后不会进入切片与向量化队列。
    </p>
    <div class="row">
      <label v-if="kbState.upload.processMode === 'UPLOAD_CLEAN_CHUNK_EMBED'">
        <input v-model="kbState.upload.autoSummary" type="checkbox" /> 入库完成后自动生成文档总结（调用模型）
      </label>
      <span v-if="kbState.upload.files?.length" class="muted">已选择 {{ kbState.upload.files.length }} 个文件</span>
      <button class="btn primary" :disabled="!kbState.selectedKbId || !kbState.upload.files?.length || kbState.loading" @click="uploadDocFile">
        上传文件
      </button>
    </div>
  </div>
</template>
