<script setup>
import KbListBindingsCard from './kb/KbListBindingsCard.vue'
import KbDocumentsIngestCard from './kb/KbDocumentsIngestCard.vue'
import KbChunksTasksCard from './kb/KbChunksTasksCard.vue'

defineProps({
  kbState: { type: Object, required: true },
  isExecutionLoggedIn: { type: Boolean, required: true },
  createKb: { type: Function, required: true },
  loadKbs: { type: Function, required: true },
  getKbId: { type: Function, required: true },
  getKbName: { type: Function, required: true },
  selectKb: { type: Function, required: true },
  renameKb: { type: Function, required: true },
  deleteKb: { type: Function, required: true },
  bindKbTenant: { type: Function, required: true },
  loadKbBindings: { type: Function, required: true },
  unbindKbTenant: { type: Function, required: true },
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
  runChunk: { type: Function, required: true },
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
  sectionDomId: { type: Function, default: null },
})
</script>

<template>
  <section class="content">
    <div class="panel">
      <div class="panel-title">知识库设置</div>
      <p class="panel-sub">KB / 文档 / 切片 / 绑定管理（Sprint-2 首版）</p>
      <p class="banner err" v-if="kbState.error">{{ kbState.error }}</p>

      <div class="domain-stack">
        <div class="domain-section" :id="sectionDomId ? sectionDomId('kb-list') : undefined" data-menu-section="kb-list">
          <h2 class="domain-section-title">库与绑定</h2>
          <KbListBindingsCard
            :kb-state="kbState"
            :is-execution-logged-in="isExecutionLoggedIn"
            :create-kb="createKb"
            :load-kbs="loadKbs"
            :get-kb-id="getKbId"
            :get-kb-name="getKbName"
            :select-kb="selectKb"
            :rename-kb="renameKb"
            :delete-kb="deleteKb"
            :bind-kb-tenant="bindKbTenant"
            :load-kb-bindings="loadKbBindings"
            :unbind-kb-tenant="unbindKbTenant"
          />
        </div>
        <div class="domain-section" :id="sectionDomId ? sectionDomId('docs') : undefined" data-menu-section="docs">
          <h2 class="domain-section-title">文档与入库</h2>
          <KbDocumentsIngestCard
            :kb-state="kbState"
            :load-docs="loadDocs"
            :summarize-kb="summarizeKb"
            :get-doc-id="getDocId"
            :get-doc-name="getDocName"
            :select-doc="selectDoc"
            :summarize-doc="summarizeDoc"
            :delete-doc="deleteDoc"
            :toggle-document-freeze="toggleDocumentFreeze"
            :ingest-doc-text="ingestDocText"
            :on-upload-file-change="onUploadFileChange"
            :upload-doc-file="uploadDocFile"
            :toggle-doc-selection="toggleDocSelection"
            :toggle-all-docs="toggleAllDocs"
            :submit-document-batch="submitDocumentBatch"
          />
        </div>
        <div class="domain-section" :id="sectionDomId ? sectionDomId('chunks') : undefined" data-menu-section="chunks">
          <h2 class="domain-section-title">切片与任务</h2>
          <KbChunksTasksCard
            :kb-state="kbState"
            :run-chunk="runChunk"
            :summarize-doc="summarizeDoc"
            :commit-chunk-drafts="commitChunkDrafts"
            :get-chunk-id="getChunkId"
            :save-chunk-draft="saveChunkDraft"
            :clear-chunk-draft="clearChunkDraft"
            :toggle-chunk-freeze="toggleChunkFreeze"
            :load-kb-tasks="loadKbTasks"
            :get-task-id="getTaskId"
            :select-kb-task="selectKbTask"
            :load-batch-detail="loadBatchDetail"
            :retry-batch-failed="retryBatchFailed"
          />
        </div>
      </div>
    </div>
  </section>
</template>
