<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

const config = reactive({
  executionBaseUrl: 'http://localhost:8091',
  tenantId: 'platform',
  actorId: 'kb-admin',
  sourceService: 'kb-admin-web',
  traceId: '',
})

const loginForm = reactive({ username: 'admin', password: 'admin123' })
const token = ref('')
const role = ref('')
const loginError = ref('')
const streamConnected = ref(false)
let taskStream = null
const notices = ref([])
const taskStatusMap = ref({})
const taskStageMap = ref({})
const taskStatusReady = ref(false)
const optimisticTaskMap = ref({})
const retryLockedTaskIds = ref({})
const retryLockedBatchIds = ref({})
const taskNameByTaskId = ref({})
let docsRefreshTimer = null
let kbsRefreshTimer = null
let noticeSeq = 0
const UI_PREFS_KEY = 'kb-admin-ui-prefs-v1'
let uiPrefsLoaded = false

const ui = reactive({
  sidebarCollapsed: false,
  showTaskDrawer: false,
  showCreateKbModal: false,
  showRenameKbModal: false,
  showUploadModal: false,
  showCommitConfirmModal: false,
  showDocDeleteCommitModal: false,
  showDeleteKbConfirmModal: false,
  showBatchModal: false,
  showDocTagModal: false,
  showChunkDetailModal: false,
  chunkEditMode: false,
  chunkDiffMode: false,
  currentView: 'docs', // docs | chunks
})

const kbForm = reactive({ name: '' })
const renameKbForm = reactive({ kb_id: '', name: '' })
const deleteKbForm = reactive({ kb_id: '', name: '' })
const uploadChunkConfig = reactive({ chunk_size: 500, chunk_overlap: 50, chunk_method: 'sentence', chunk_delimiter: '\\n\\n' })
const chunkRunConfig = reactive({ chunk_method: 'sentence', chunk_size: 500, chunk_overlap: 50, chunk_delimiter: '\\n\\n' })
const uploadProcessMode = ref('UPLOAD_CLEAN_CHUNK_EMBED')
const docChunkMethodMap = ref({})
const docTagInput = ref('')
const docTagModal = reactive({
  doc_id: '',
  kb_id: '',
  filename: '',
  tags: [],
})
const uploadInputRef = ref(null)
const uploadQueue = ref([])
const uploadLimitMb = ref(20)
const editDraftForm = reactive({ chunk_id: '', text: '' })
const dragActive = ref(false)
const pendingDocDeletes = ref({})

const kbs = ref([])
const docs = ref([])
const chunks = ref([])
const tasks = ref([])
const taskChildrenMap = ref({})
const taskChildrenLoading = ref({})
const taskChildrenRefreshAt = ref({})
const expandedTaskIds = ref({})
const selectedKbId = ref('')
const kbMenuOpenId = ref('')
const selectedDocId = ref('')
const selectedChunk = ref(null)
const chunkSearch = ref('')
const chunkSearchCaseSensitive = ref(false)
const activeChunkMatchIndex = ref(0)
const scrollEnabledChunkId = ref('')
let chunkHoverTimer = null
const docCardPointer = ref(null)
const kbSearch = ref('')
const kbSort = ref('name_asc')
const docSearch = ref('')
const docSort = ref('order_desc')
const docStatusFilter = ref('all')
const docTagFilter = ref('all')
const docTagSearch = ref('')
const selectedDocIds = ref([])
const batchActionForm = reactive({
  operation: 'chunk',
  chunk_method: 'sentence',
  chunk_size: 500,
  chunk_overlap: 50,
  chunk_delimiter: '\\n\\n',
  target_kb_id: '',
  tags_text: '',
})
const batchShowPrereqList = ref(false)
const batchDetailMap = ref({})
const batchDetailLoading = ref({})

const loading = reactive({
  login: false,
  kbs: false,
  docs: false,
  chunks: false,
  tasks: false,
  createKb: false,
  renameKb: false,
  deleteKb: false,
  commitDocDelete: false,
  upload: false,
  commit: false,
  clearTasks: false,
})

const isLoggedIn = computed(() => !!token.value)
const selectedKb = computed(() => kbs.value.find((k) => k.kb_id === selectedKbId.value) || null)
const selectedDoc = computed(() => docs.value.find((d) => d.doc_id === selectedDocId.value) || null)
const kbStats = computed(() => selectedKb.value?.stats || {})
const visibleKbs = computed(() => {
  const keyword = kbSearch.value.trim().toLowerCase()
  const list = kbs.value.filter((kb) => {
    if (!keyword) return true
    return `${kb.name || ''} ${kb.kb_id || ''} ${kb.summary || ''}`.toLowerCase().includes(keyword)
  })
  return list.slice().sort((a, b) => {
    if (kbSort.value === 'name_desc') return (b.name || b.kb_id || '').localeCompare(a.name || a.kb_id || '')
    if (kbSort.value === 'id_asc') return (a.kb_id || '').localeCompare(b.kb_id || '')
    if (kbSort.value === 'id_desc') return (b.kb_id || '').localeCompare(a.kb_id || '')
    return (a.name || a.kb_id || '').localeCompare(b.name || b.kb_id || '')
  })
})
const visibleDocs = computed(() => {
  const keyword = docSearch.value.trim().toLowerCase()
  const tagKeyword = docTagSearch.value.trim().toLowerCase()
  const list = docs.value.filter((doc) => {
    const statusText = docPrimaryBadge(doc).text
    const tags = Array.isArray(doc?.tags_json) ? doc.tags_json : []
    const hasTag = tags.length > 0
    if (keyword && !`${doc.filename || ''} ${doc.doc_id || ''}`.toLowerCase().includes(keyword)) return false
    if (docStatusFilter.value === 'processing' && !isDocTaskLocked(doc)) return false
    if (docStatusFilter.value === 'need_chunk' && statusText !== 'need chunk') return false
    if (docStatusFilter.value === 'need_embed' && statusText !== 'need embed') return false
    if (docStatusFilter.value === 'need_summary' && statusText !== 'need summary') return false
    if (docStatusFilter.value === 'ok' && statusText !== 'ok') return false
    if (docStatusFilter.value === 'failed' && String(doc?.status || '').toUpperCase() !== 'FAILED') return false
    if (docStatusFilter.value === 'cancelled' && String(doc?.status || '').toUpperCase() !== 'CANCELLED') return false
    if (docStatusFilter.value === 'frozen' && !doc?.frozen) return false
    if (docStatusFilter.value === 'active' && !!doc?.frozen) return false
    if (docTagFilter.value === 'has' && !hasTag) return false
    if (docTagFilter.value === 'none' && hasTag) return false
    if (tagKeyword) {
      if (!hasTag) return false
      const joined = tags.map((t) => String(t || '').toLowerCase()).join(' ')
      if (!joined.includes(tagKeyword)) return false
    }
    return true
  })
  const getOrder = (doc) => docSequenceNo(doc)
  return list.slice().sort((a, b) => {
    if (docSort.value === 'order_asc') return getOrder(a) - getOrder(b)
    if (docSort.value === 'order_desc') return getOrder(b) - getOrder(a)
    if (docSort.value === 'status') return (a.status || '').localeCompare(b.status || '')
    const at = Date.parse(a.updated_at || '') || 0
    const bt = Date.parse(b.updated_at || '') || 0
    if (docSort.value === 'updated_asc') return at - bt
    return bt - at
  })
})

function loadUiPrefs() {
  try {
    const raw = localStorage.getItem(UI_PREFS_KEY)
    if (!raw) return
    const prefs = JSON.parse(raw || '{}')
    if (typeof prefs.kbSort === 'string') kbSort.value = prefs.kbSort
    if (typeof prefs.docSort === 'string') docSort.value = prefs.docSort
    if (typeof prefs.docStatusFilter === 'string') docStatusFilter.value = prefs.docStatusFilter
    if (typeof prefs.docTagFilter === 'string') docTagFilter.value = prefs.docTagFilter
    if (typeof prefs.selectedKbId === 'string') selectedKbId.value = prefs.selectedKbId
    if (typeof prefs.sidebarCollapsed === 'boolean') ui.sidebarCollapsed = prefs.sidebarCollapsed
    if (typeof prefs.showTaskDrawer === 'boolean') ui.showTaskDrawer = prefs.showTaskDrawer
    if (typeof prefs.kbSearch === 'string') kbSearch.value = prefs.kbSearch
    if (typeof prefs.docSearch === 'string') docSearch.value = prefs.docSearch
    if (typeof prefs.docTagSearch === 'string') docTagSearch.value = prefs.docTagSearch
  } catch {
    // ignore corrupted prefs
  } finally {
    uiPrefsLoaded = true
  }
}

function saveUiPrefs() {
  if (!uiPrefsLoaded) return
  const payload = {
    selectedKbId: String(selectedKbId.value || ''),
    kbSort: String(kbSort.value || 'name_asc'),
    docSort: String(docSort.value || 'order_desc'),
    docStatusFilter: String(docStatusFilter.value || 'all'),
    docTagFilter: String(docTagFilter.value || 'all'),
    sidebarCollapsed: !!ui.sidebarCollapsed,
    showTaskDrawer: !!ui.showTaskDrawer,
    kbSearch: String(kbSearch.value || ''),
    docSearch: String(docSearch.value || ''),
    docTagSearch: String(docTagSearch.value || ''),
  }
  try {
    localStorage.setItem(UI_PREFS_KEY, JSON.stringify(payload))
  } catch {
    // ignore storage errors
  }
}
const pendingDraftSummary = computed(() => {
  const summary = {
    deleteCount: 0,
    editCount: 0,
    items: [],
  }
  for (const ch of chunks.value) {
    if (!ch.draft) continue
    if (ch.draft.action === 'delete') summary.deleteCount += 1
    if (ch.draft.action === 'edit') summary.editCount += 1
    summary.items.push({
      chunk_id: ch.chunk_id,
      action: ch.draft.action,
      preview: (ch.draft.text || ch.text || '').slice(0, 80),
    })
  }
  return summary
})
const pendingDocDeleteItems = computed(() => Object.values(pendingDocDeletes.value))
const pendingDocDeleteCount = computed(() => pendingDocDeleteItems.value.length)
const selectedDocSet = computed(() => new Set(selectedDocIds.value))
const selectedDocs = computed(() => visibleDocs.value.filter((d) => selectedDocSet.value.has(d.doc_id)))
const selectedDocCount = computed(() => selectedDocs.value.length)
const allVisibleDocsChecked = computed(() => visibleDocs.value.length > 0 && visibleDocs.value.every((d) => selectedDocSet.value.has(d.doc_id)))
const batchEligibility = computed(() => {
  const op = String(batchActionForm.operation || 'chunk')
  const eligibleDocIds = []
  const excluded = []
  for (const doc of selectedDocs.value) {
    if (isDocTaskLocked(doc)) {
      const stage = docTaskState(doc)?.stage || String(doc?.status || '').toUpperCase()
      excluded.push({
        doc_id: doc.doc_id,
        filename: doc.filename || doc.doc_id,
        reason: `文档有运行中任务（${stageLabel(stage)}）`,
      })
      continue
    }
    if (op === 'embed' && Number(doc?.chunk_count || 0) <= 0) {
      excluded.push({
        doc_id: doc.doc_id,
        filename: doc.filename || doc.doc_id,
        reason: '需先切片（当前无 chunks）',
      })
      continue
    }
    eligibleDocIds.push(doc.doc_id)
  }
  return {
    selectedCount: selectedDocs.value.length,
    eligibleCount: eligibleDocIds.length,
    excludedCount: excluded.length,
    eligibleDocIds,
    excluded,
  }
})
const canRunBatchAction = computed(() => batchEligibility.value.eligibleCount > 0 && !!selectedKbId.value)
const runningTaskCount = computed(() => tasks.value.filter((t) => isRunningTask({ status: effectiveTaskStatus(t) })).length)
const latestTaskByDoc = computed(() => {
  const map = {}
  for (const task of tasks.value) {
    if (!task.doc_id) continue
    const prev = map[task.doc_id]
    if (!prev) {
      map[task.doc_id] = task
      continue
    }
    const ta = Date.parse(task.updated_at || '') || 0
    const tb = Date.parse(prev.updated_at || '') || 0
    if (ta >= tb) map[task.doc_id] = task
  }
  return map
})
const docStatusById = computed(() => {
  const map = {}
  for (const doc of docs.value) {
    if (!doc?.doc_id) continue
    map[doc.doc_id] = String(doc.status || '').toUpperCase()
  }
  return map
})
const docFilenameById = computed(() => {
  const map = {}
  for (const doc of docs.value) {
    if (!doc?.doc_id) continue
    map[doc.doc_id] = doc.filename || ''
  }
  return map
})
const docSequenceMap = computed(() => {
  const ordered = docs.value
    .slice()
    .sort((a, b) => {
      const ca = Date.parse(a.created_at || '') || Date.parse(a.updated_at || '') || 0
      const cb = Date.parse(b.created_at || '') || Date.parse(b.updated_at || '') || 0
      if (ca !== cb) return ca - cb
      return (a.doc_id || '').localeCompare(b.doc_id || '')
    })
  const map = {}
  ordered.forEach((doc, idx) => {
    if (doc?.doc_id) map[doc.doc_id] = idx + 1
  })
  return map
})
const chunkMatchIds = computed(() => {
  const keyword = chunkSearch.value || ''
  if (!keyword.trim()) return []
  return chunks.value
    .filter((ch) => chunkContainsKeyword(ch, keyword, chunkSearchCaseSensitive.value))
    .map((ch) => ch.chunk_id)
})
const chunkMatchCount = computed(() => chunkMatchIds.value.length)
const activeChunkMatchId = computed(() => {
  if (!chunkMatchIds.value.length) return ''
  const idx = Math.max(0, Math.min(activeChunkMatchIndex.value, chunkMatchIds.value.length - 1))
  return chunkMatchIds.value[idx] || ''
})
const uploadWillChunk = computed(() => uploadProcessMode.value === 'UPLOAD_CLEAN_CHUNK_EMBED')
const displayTasks = computed(() => {
  const seenBatchParent = new Set()
  const out = []
  for (const t of tasks.value) {
    const batchId = String(t?.batch_id || '')
    const isParent = !!t?.batch_is_parent
    if (batchId) {
      if (isParent) {
        seenBatchParent.add(batchId)
        out.push(t)
      }
      continue
    }
    out.push(t)
  }
  // Backward compatibility: if old tasks have batch_id but no parent task present, show children.
  for (const t of tasks.value) {
    const batchId = String(t?.batch_id || '')
    if (!batchId || !!t?.batch_is_parent) continue
    if (seenBatchParent.has(batchId)) continue
    out.push(t)
  }
  return out
})

function formatBytes(bytes) {
  const n = Number(bytes || 0)
  if (!Number.isFinite(n) || n <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let size = n
  let idx = 0
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024
    idx += 1
  }
  return `${size.toFixed(size >= 100 ? 0 : size >= 10 ? 1 : 2)} ${units[idx]}`
}

function formatFileSize(bytes) {
  return formatBytes(bytes)
}

function escapeHtml(text) {
  return String(text || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function chunkEffectiveText(chunk) {
  if (chunk?.draft?.action === 'edit') {
    const draftText = String(chunk?.draft?.text || '')
    if (draftText) return draftText
  }
  return String(chunk?.text || '')
}

function chunkContainsKeyword(chunk, keyword, caseSensitive) {
  const text = chunkEffectiveText(chunk)
  const kw = String(keyword || '').trim()
  if (!kw) return false
  if (caseSensitive) return text.includes(kw)
  return text.toLowerCase().includes(kw.toLowerCase())
}

function firstMatchIndex(text, keyword, caseSensitive) {
  if (!keyword.trim()) return -1
  if (caseSensitive) return text.indexOf(keyword)
  return text.toLowerCase().indexOf(keyword.toLowerCase())
}

function highlightHtml(text, keyword, caseSensitive) {
  const raw = String(text || '')
  const kw = String(keyword || '').trim()
  if (!kw) return escapeHtml(raw)
  const source = caseSensitive ? raw : raw.toLowerCase()
  const target = caseSensitive ? kw : kw.toLowerCase()
  let start = 0
  let idx = source.indexOf(target, start)
  if (idx < 0) return escapeHtml(raw)
  let out = ''
  while (idx >= 0) {
    out += escapeHtml(raw.slice(start, idx))
    out += `<mark class="chunk-hit">${escapeHtml(raw.slice(idx, idx + kw.length))}</mark>`
    start = idx + kw.length
    idx = source.indexOf(target, start)
  }
  out += escapeHtml(raw.slice(start))
  return out
}

function chunkPreviewHtml(chunk) {
  const text = chunkEffectiveText(chunk)
  const keyword = String(chunkSearch.value || '').trim()
  if (!keyword) return escapeHtml(text.slice(0, 360))
  const first = firstMatchIndex(text, keyword, chunkSearchCaseSensitive.value)
  if (first < 0) return escapeHtml(text.slice(0, 360))
  const radius = 180
  const start = Math.max(0, first - radius)
  const end = Math.min(text.length, first + keyword.length + radius)
  const snippet = text.slice(start, end)
  const prefix = start > 0 ? '... ' : ''
  const suffix = end < text.length ? ' ...' : ''
  return `${prefix}${highlightHtml(snippet, keyword, chunkSearchCaseSensitive.value)}${suffix}`
}

function chunkDetailHtml(chunk) {
  const text = chunkEffectiveText(chunk)
  const keyword = String(chunkSearch.value || '').trim()
  if (!keyword) return escapeHtml(text)
  return highlightHtml(text, keyword, chunkSearchCaseSensitive.value)
}

function buildDiffSlices(original, draft) {
  const a = String(original || '')
  const b = String(draft || '')
  if (a === b) {
    return { prefix: a, removed: '', added: '', suffix: '' }
  }
  let start = 0
  const max = Math.min(a.length, b.length)
  while (start < max && a[start] === b[start]) start += 1
  let end = 0
  while (
    end < a.length - start &&
    end < b.length - start &&
    a[a.length - 1 - end] === b[b.length - 1 - end]
  ) {
    end += 1
  }
  return {
    prefix: a.slice(0, start),
    removed: a.slice(start, a.length - end),
    added: b.slice(start, b.length - end),
    suffix: a.slice(a.length - end),
  }
}

function chunkDiffOriginalHtml() {
  const original = String(selectedChunk.value?.text || '')
  const draft = String(editDraftForm.text || '')
  const diff = buildDiffSlices(original, draft)
  const removed = diff.removed ? `<mark class="diff-remove">${escapeHtml(diff.removed)}</mark>` : ''
  const added = diff.added ? `<mark class="diff-add">${escapeHtml(diff.added)}</mark>` : ''
  return `${escapeHtml(diff.prefix)}${removed}${added}${escapeHtml(diff.suffix)}`
}

function isChunkMatched(chunk) {
  return !!chunkSearch.value.trim() && chunkContainsKeyword(chunk, chunkSearch.value, chunkSearchCaseSensitive.value)
}

function isChunkActiveMatch(chunk) {
  return !!activeChunkMatchId.value && chunk?.chunk_id === activeChunkMatchId.value
}

function focusChunkCard(chunkId) {
  if (!chunkId) return
  const el = document.querySelector(`[data-chunk-id="${chunkId}"]`)
  if (el && typeof el.scrollIntoView === 'function') {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

function jumpChunkMatch(step) {
  if (!chunkMatchIds.value.length) return
  const total = chunkMatchIds.value.length
  const current = (activeChunkMatchIndex.value + step + total) % total
  activeChunkMatchIndex.value = current
  focusChunkCard(chunkMatchIds.value[current])
}

function onChunkSearchEnter() {
  if (!chunkMatchIds.value.length) return
  jumpChunkMatch(1)
}

function enableChunkScrollAfterDelay(chunkId) {
  if (chunkHoverTimer) clearTimeout(chunkHoverTimer)
  chunkHoverTimer = setTimeout(() => {
    scrollEnabledChunkId.value = chunkId
  }, 600)
}

function disableChunkScroll(chunkId) {
  if (chunkHoverTimer) {
    clearTimeout(chunkHoverTimer)
    chunkHoverTimer = null
  }
  if (scrollEnabledChunkId.value === chunkId) {
    scrollEnabledChunkId.value = ''
  }
}

function pushNotice(type, message) {
  const text = String(message || '').trim()
  if (!text) return
  const id = `n-${Date.now()}-${noticeSeq++}`
  notices.value = [{ id, type, message: text }, ...notices.value].slice(0, 4)
  const ttl = type === 'error' ? 9000 : 5000
  setTimeout(() => {
    notices.value = notices.value.filter((n) => n.id !== id)
  }, ttl)
}

function taskTypeLabel(task) {
  const t = String(task?.task_type || '').toUpperCase()
  if (t === 'BATCH_DOC_CHUNK') return '批量切片'
  if (t === 'BATCH_DOC_EMBED') return '批量重嵌入'
  if (t === 'BATCH_DOC_SUMMARIZE') return '批量总结'
  if (t === 'BATCH_DOC_MOVE') return '批量移动'
  if (t === 'BATCH_DOC_TAG') return '批量标签'
  if (t === 'BATCH_DOC_DELETE') return '批量删除'
  if (t === 'KB_SUMMARIZE') return '库总结'
  if (t === 'DOC_INGEST') return '上传处理'
  if (t === 'DOC_CHUNK') return '切片'
  if (t === 'DOC_RECHUNK') return '重切片'
  if (t === 'DOC_REINDEX') return '重建嵌入'
  if (t === 'DOC_SUMMARIZE') return '总结'
  if (t === 'DOC_MOVE') return '移动'
  if (t === 'DOC_TAG') return '打标签'
  if (t === 'DOC_DELETE') return '删除'
  if (t === 'CHUNK_COMMIT') return '提交草稿'
  if (t === 'DOC_UPLOAD') return '上传'
  return '处理'
}

function resolveTaskName(task) {
  const taskId = String(task?.task_id || '')
  return (
    task?.filename ||
    taskNameByTaskId.value[taskId] ||
    docFilenameById.value[task?.doc_id || ''] ||
    task?.task_name ||
    '文件任务'
  )
}

function summarizeTask(task) {
  const name = resolveTaskName(task)
  const op = taskTypeLabel(task)
  const status = effectiveTaskStatus(task)
  if (status === 'SUCCEEDED') return `${name} ${op}已完成`
  if (status === 'FAILED') return `${name} ${op}失败: ${task.error_message || task.error_code || '执行异常'}`
  if (status === 'CANCELLED') return `${name} ${op}已取消`
  return ''
}

function normalizeStatus(status) {
  return String(status || '').toUpperCase()
}

function taskStatusClass(status) {
  const v = normalizeStatus(status)
  if (v === 'SUCCEEDED') return 'status-success'
  if (v === 'FAILED') return 'status-failed'
  if (v === 'CANCELLED') return 'status-cancelled'
  if (v === 'RUNNING') return 'status-running'
  if (v === 'PENDING') return 'status-pending'
  if (v === 'CANCELLING') return 'status-cancelling'
  return 'status-unknown'
}

function taskRowClass(task) {
  const effective = effectiveTaskStatus(task)
  if (effective === 'CANCELLED' || effective === 'FAILED' || effective === 'SUCCEEDED') {
    return taskStatusClass(effective)
  }
  const stage = resolveTaskStage(task)
  if (stage === 'PARSING') return 'status-pending'
  return taskStatusClass(effective)
}

function isRunningTask(task) {
  return ['PENDING', 'RUNNING', 'CANCELLING'].includes(normalizeStatus(task?.status))
}

function canCancelTask(task) {
  const s = normalizeStatus(effectiveTaskStatus(task))
  return s === 'PENDING' || s === 'RUNNING'
}

function canRetryTask(task) {
  const s = normalizeStatus(effectiveTaskStatus(task))
  if (s !== 'FAILED') return false
  if (retryLockedTaskIds.value[String(task?.task_id || '')]) return false
  if (task?.batch_is_parent) return false
  if (task?.batch_id) return false
  const taskType = normalizeStatus(task?.task_type)
  return ['DOC_INGEST', 'DOC_UPLOAD', 'DOC_CHUNK', 'DOC_REINDEX', 'DOC_SUMMARIZE', 'CHUNK_COMMIT', 'DOC_MOVE', 'DOC_TAG', 'DOC_DELETE'].includes(taskType)
}

function normalizeStage(task) {
  return String(task?.stage || '').toUpperCase()
}

function resolveTaskStage(task) {
  const stage = normalizeStage(task)
  const docStatus = docStatusById.value[task?.doc_id || '']
  // Task stage should be the source of truth while task is active.
  if (stage) return stage
  if (docStatus) return docStatus
  return ''
}

function effectiveTaskStatus(task) {
  const raw = normalizeStatus(task?.status)
  const cancelRequested = Boolean(task?.cancel_requested)
  if (cancelRequested && raw !== 'SUCCEEDED') return 'CANCELLED'
  if (!['PENDING', 'RUNNING', 'CANCELLING'].includes(raw)) return raw
  const stage = resolveTaskStage(task)
  const hasActiveStage = [
    'UPLOADING',
    'PARSING',
    'CHUNKING',
    'SUMMARIZING',
    'SUMMARIZING_DOC',
    'SUMMARY_EMBEDDING_DOC',
    'SUMMARIZING_KB',
    'SUMMARY_EMBEDDING_KB',
    'EMBEDDING',
    'INDEXING',
    'MOVING',
    'TAGGING',
    'DELETING',
  ].includes(stage)
  if (stage === 'INDEXED') return 'SUCCEEDED'
  if (stage === 'SUMMARIZED') return 'SUCCEEDED'
  if (stage === 'UPLOADED_RAW' || stage === 'CLEANED') return 'SUCCEEDED'
  if (stage === 'MOVED' || stage === 'TAGGED') return 'SUCCEEDED'
  if (stage === 'DELETED') return 'SUCCEEDED'
  if (hasActiveStage) return raw
  const docStatus = docStatusById.value[task?.doc_id || '']
  if (docStatus === 'UPLOADED_RAW' || docStatus === 'CLEANED') return 'SUCCEEDED'
  if (docStatus === 'INDEXED') return 'SUCCEEDED'
  if (docStatus === 'FAILED') return 'FAILED'
  if (docStatus === 'CANCELLED') return 'CANCELLED'
  return raw
}

function taskProgressLabel(task) {
  const name = resolveTaskName(task)
  const stage = resolveTaskStage(task)
  const status = effectiveTaskStatus(task)
  const op = taskTypeLabel(task)
  if (status === 'CANCELLED') return `${name} ${op}已取消`
  if (status === 'FAILED') return `${name} ${op}失败`
  if (status === 'SUCCEEDED') return `${name} ${op}已完成`
  if (stage === 'BATCH_RUNNING') return `${name} ${op}：${taskStagePercentText(task, '处理中')}`
  if (stage === 'BATCH_COMPLETED') return `${name} ${op}已完成`
  if (stage === 'BATCH_FAILED') return `${name} ${op}失败`
  if (stage === 'UPLOADING') return `${name} 上传中`
  if (stage === 'PARSING') return `${name} ${op}：解析中`
  if (stage === 'CHUNKING') return `${name} ${op}：切片中`
  if (stage === 'SUMMARIZING' || stage === 'SUMMARIZING_DOC') return `${name} ${op}：${taskStagePercentText(task, '文档总结中')}`
  if (stage === 'SUMMARY_EMBEDDING_DOC') return `${name} ${op}：${taskStagePercentText(task, '总结嵌入中')}`
  if (stage === 'SUMMARIZING_KB') return `${name} ${op}：${taskStagePercentText(task, '库总结中')}`
  if (stage === 'SUMMARY_EMBEDDING_KB') return `${name} ${op}：${taskStagePercentText(task, '库总结嵌入中')}`
  if (stage === 'EMBEDDING') return `${name} ${op}：${taskEmbeddingProgressText(task, '嵌入中')}`
  if (stage === 'INDEXING') return `${name} ${op}：索引中`
  if (stage === 'MOVING') return `${name} ${op}：移动中`
  if (stage === 'TAGGING') return `${name} ${op}：打标签中`
  if (stage === 'DELETING') return `${name} ${op}：删除中`
  if (stage === 'UPLOADED_RAW') return `${name} 已上传`
  if (stage === 'CLEANED') return `${name} 已清洗`
  if (stage === 'MOVED') return `${name} 已移动`
  if (stage === 'TAGGED') return `${name} 已打标签`
  if (stage === 'DELETED') return `${name} 已删除`
  return `${name} ${op}处理中`
}

function taskDisplayName(task) {
  return resolveTaskName(task)
}

function taskStatusText(task) {
  const stage = resolveTaskStage(task)
  const status = effectiveTaskStatus(task)
  const op = taskTypeLabel(task)
  if (status === 'CANCELLED') return `${op}已取消`
  if (status === 'FAILED') return `${op}失败`
  if (status === 'SUCCEEDED') return `${op}已完成`
  if (stage === 'BATCH_RUNNING') return `${op}：${taskStagePercentText(task, '处理中')}`
  if (stage === 'BATCH_COMPLETED') return `${op}已完成`
  if (stage === 'BATCH_FAILED') return `${op}失败`
  if (stage === 'UPLOADING') return '上传中'
  if (stage === 'PARSING') return `${op}：解析中`
  if (stage === 'CHUNKING') return `${op}：切片中`
  if (stage === 'SUMMARIZING' || stage === 'SUMMARIZING_DOC') return `${op}：${taskStagePercentText(task, '文档总结中')}`
  if (stage === 'SUMMARY_EMBEDDING_DOC') return `${op}：${taskStagePercentText(task, '总结嵌入中')}`
  if (stage === 'SUMMARIZING_KB') return `${op}：${taskStagePercentText(task, '库总结中')}`
  if (stage === 'SUMMARY_EMBEDDING_KB') return `${op}：${taskStagePercentText(task, '库总结嵌入中')}`
  if (stage === 'EMBEDDING') return `${op}：${taskEmbeddingPercentText(task, '嵌入中')}`
  if (stage === 'INDEXING') return `${op}：索引中`
  if (stage === 'MOVING') return `${op}：移动中`
  if (stage === 'TAGGING') return `${op}：打标签中`
  if (stage === 'DELETING') return `${op}：删除中`
  if (stage === 'UPLOADED_RAW') return '已上传'
  if (stage === 'CLEANED') return '已清洗'
  if (stage === 'MOVED') return '已移动'
  if (stage === 'TAGGED') return '已打标签'
  if (stage === 'DELETED') return '已删除'
  return `${op}处理中`
}

function taskEmbeddingProgressText(task, fallback = '嵌入中') {
  const current = Number(task?.progress_current || 0)
  const total = Number(task?.progress_total || 0)
  const pctRaw = Number(task?.progress_pct || 0)
  if (!Number.isFinite(total) || total <= 0) return fallback
  const pct = Number.isFinite(pctRaw) ? Math.max(0, Math.min(100, Math.floor(pctRaw))) : Math.floor((current * 100) / total)
  return `嵌入中 ${current}/${total} (${pct}%)`
}

function taskEmbeddingPercentText(task, fallback = '嵌入中') {
  const total = Number(task?.progress_total || 0)
  const pctRaw = Number(task?.progress_pct || 0)
  if (!Number.isFinite(total) || total <= 0) return fallback
  const pct = Number.isFinite(pctRaw) ? Math.max(0, Math.min(100, Math.floor(pctRaw))) : 0
  return `嵌入中 ${pct}%`
}

function taskStagePercentText(task, fallback) {
  const total = Number(task?.progress_total || 0)
  const pctRaw = Number(task?.progress_pct || 0)
  if (!Number.isFinite(total) || total <= 0) return fallback
  const pct = Number.isFinite(pctRaw) ? Math.max(0, Math.min(100, Math.floor(pctRaw))) : 0
  return `${fallback} ${pct}%`
}

function docEmbeddingProgress(doc) {
  const task = latestTaskByDoc.value[doc?.doc_id || '']
  if (!task) return null
  if (resolveTaskStage(task) !== 'EMBEDDING') return null
  const current = Number(task?.progress_current || 0)
  const total = Number(task?.progress_total || 0)
  const pctRaw = Number(task?.progress_pct || 0)
  if (!Number.isFinite(total) || total <= 0) return { current: 0, total: 0, pct: 0 }
  const pct = Number.isFinite(pctRaw) ? Math.max(0, Math.min(100, Math.floor(pctRaw))) : Math.floor((current * 100) / total)
  return { current: Math.max(0, current), total, pct }
}

function docEmbeddingProgressPercent(doc) {
  const p = docEmbeddingProgress(doc)
  return p ? p.pct : 0
}

function docEmbeddingProgressTitle(doc) {
  const p = docEmbeddingProgress(doc)
  if (!p || p.total <= 0) return '嵌入中'
  return `嵌入中 ${p.current}/${p.total} (${p.pct}%)`
}

function docEmbeddingProgressLabel(doc) {
  const p = docEmbeddingProgress(doc)
  if (!p || p.total <= 0) return 'Embed again'
  return `Embed ${p.pct}%`
}

function docEmbeddingButtonStyle(doc) {
  const pct = docEmbeddingProgressPercent(doc)
  return {
    '--prog-pct': `${pct}%`,
  }
}

function formatTaskTime(value) {
  if (!value) return '--日 --:--:--'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return '--日 --:--:--'
  const day = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${day}日 ${hh}:${mm}:${ss}`
}

function docSequenceNo(doc) {
  if (!doc?.doc_id) return 0
  return Number(docSequenceMap.value[doc.doc_id] || 0)
}

function displayDocName(name) {
  const text = String(name || '')
  const chars = Array.from(text)
  if (chars.length <= 50) return text
  return `${chars.slice(0, 50).join('')}...`
}

function toggleSelectAllVisibleDocs() {
  if (allVisibleDocsChecked.value) {
    const keep = selectedDocIds.value.filter((id) => !visibleDocs.value.some((d) => d.doc_id === id))
    selectedDocIds.value = keep
    return
  }
  const merged = new Set(selectedDocIds.value)
  for (const doc of visibleDocs.value) merged.add(doc.doc_id)
  selectedDocIds.value = Array.from(merged)
}

function toggleSelectDoc(docId) {
  const set = new Set(selectedDocIds.value)
  if (set.has(docId)) set.delete(docId)
  else set.add(docId)
  selectedDocIds.value = Array.from(set)
}

function clearSelectedDocs() {
  selectedDocIds.value = []
}

function stageLabel(stage) {
  const s = String(stage || '').toUpperCase()
  if (s === 'BATCH_RUNNING') return '批次处理中'
  if (s === 'BATCH_COMPLETED') return '批次完成'
  if (s === 'BATCH_FAILED') return '批次失败'
  if (s === 'UPLOADING') return '上传'
  if (s === 'PARSING') return '解析'
  if (s === 'CHUNKING') return '切片'
  if (s === 'SUMMARIZING') return '总结'
  if (s === 'SUMMARIZING_DOC') return '文档总结'
  if (s === 'SUMMARY_EMBEDDING_DOC') return '文档总结嵌入'
  if (s === 'SUMMARIZING_KB') return '库总结'
  if (s === 'SUMMARY_EMBEDDING_KB') return '库总结嵌入'
  if (s === 'EMBEDDING') return '嵌入'
  if (s === 'INDEXING') return '索引'
  if (s === 'MOVING') return '移动'
  if (s === 'TAGGING') return '打标签'
  if (s === 'DELETING') return '删除'
  if (s === 'SUMMARIZED') return '已总结'
  if (s === 'INDEXED') return '完成'
  if (s === 'UPLOADED_RAW') return '已上传'
  if (s === 'CLEANED') return '已清洗'
  if (s === 'MOVED') return '已移动'
  if (s === 'TAGGED') return '已打标签'
  if (s === 'DELETED') return '已删除'
  if (s === 'FAILED') return '失败'
  if (s === 'CANCELLED') return '取消'
  return s || '阶段'
}

function stageStatusClass(status) {
  const s = normalizeStatus(status)
  if (s === 'SUCCEEDED') return 'child-success'
  if (s === 'FAILED') return 'child-failed'
  if (s === 'CANCELLED') return 'child-cancelled'
  if (s === 'RUNNING' || s === 'PENDING' || s === 'CANCELLING') return 'child-running'
  return 'child-unknown'
}

function deriveEventStatus(evt) {
  const payloadStatus = normalizeStatus(evt?.payload?.status)
  if (payloadStatus) return payloadStatus
  const et = String(evt?.event_type || '').toUpperCase()
  if (et === 'TASK_COMPLETED') return 'SUCCEEDED'
  if (et === 'TASK_FAILED') return 'FAILED'
  if (et === 'TASK_CANCELLED') return 'CANCELLED'
  return 'RUNNING'
}

function eventStatusRank(status) {
  const s = normalizeStatus(status)
  if (s === 'FAILED') return 5
  if (s === 'CANCELLED') return 4
  if (s === 'SUCCEEDED') return 3
  if (s === 'CANCELLING') return 2
  if (s === 'RUNNING') return 1
  if (s === 'PENDING') return 0
  return -1
}

function buildTaskChildren(events) {
  const stageOrder = [
    'UPLOADING',
    'PARSING',
    'CHUNKING',
    'SUMMARIZING',
    'SUMMARIZING_DOC',
    'SUMMARY_EMBEDDING_DOC',
    'SUMMARIZING_KB',
    'SUMMARY_EMBEDDING_KB',
    'EMBEDDING',
    'INDEXING',
    'MOVING',
    'TAGGING',
    'DELETING',
    'UPLOADED_RAW',
    'CLEANED',
    'SUMMARIZED',
    'INDEXED',
    'MOVED',
    'TAGGED',
    'DELETED',
    'FAILED',
    'CANCELLED',
  ]
  const grouped = {}
  for (const evt of events || []) {
    const stage = String(evt?.stage || '').toUpperCase()
    if (!stageOrder.includes(stage)) continue
    const status = deriveEventStatus(evt)
    const prev = grouped[stage]
    const ts = evt?.ts
    if (!prev) {
      grouped[stage] = {
        key: stage,
        stage,
        label: stageLabel(stage),
        status,
        ts,
        cls: stageStatusClass(status),
        progressText: '',
      }
    } else {
      const prevRank = eventStatusRank(prev.status)
      const nextRank = eventStatusRank(status)
      if (nextRank > prevRank) {
        prev.status = status
        prev.cls = stageStatusClass(status)
      }
      const prevTs = Date.parse(prev.ts || '') || 0
      const nextTs = Date.parse(ts || '') || 0
      if (nextTs >= prevTs) {
        prev.ts = ts
      }
    }
    const target = grouped[stage]
    const curr = Number(evt?.payload?.current || 0)
    const total = Number(evt?.payload?.total || 0)
    const pctRaw = Number(evt?.payload?.progress_pct || 0)
    if (Number.isFinite(total) && total > 0) {
      const pct = Number.isFinite(pctRaw) ? Math.max(0, Math.min(100, Math.floor(pctRaw))) : Math.floor((curr * 100) / total)
      target.progressText = `${curr}/${total} (${pct}%)`
    }
  }
  const items = stageOrder
    .filter((stage) => !!grouped[stage])
    .map((stage) => ({
      key: grouped[stage].key,
      stage: grouped[stage].stage,
      label: grouped[stage].label,
      status: grouped[stage].status,
      ts: grouped[stage].ts,
      cls: grouped[stage].cls,
      progressText: grouped[stage].progressText || '',
    }))
  return items
}

async function loadTaskChildren(taskId, options = {}) {
  if (!taskId) return
  const silent = !!options.silent
  if (taskChildrenLoading.value[taskId]) return
  taskChildrenRefreshAt.value = { ...taskChildrenRefreshAt.value, [taskId]: Date.now() }
  const hasExisting = Array.isArray(taskChildrenMap.value[taskId]) && taskChildrenMap.value[taskId].length > 0
  const showLoading = !silent || !hasExisting
  if (showLoading) {
    taskChildrenLoading.value = { ...taskChildrenLoading.value, [taskId]: true }
  }
  try {
    const data = await callApi(`/api/v1/admin/tasks/${encodeURIComponent(taskId)}/events?limit=300`, {
      headers: withHeaders(),
    })
    taskChildrenMap.value = { ...taskChildrenMap.value, [taskId]: buildTaskChildren(data.items || []) }
  } finally {
    if (showLoading) {
      const next = { ...taskChildrenLoading.value }
      delete next[taskId]
      taskChildrenLoading.value = next
    }
  }
}

async function toggleTaskExpand(task) {
  const taskId = task?.task_id
  if (!taskId) return
  const next = !expandedTaskIds.value[taskId]
  expandedTaskIds.value = { ...expandedTaskIds.value, [taskId]: next }
  if (!next) return
  if (task?.batch_is_parent && task?.batch_id) {
    if (!batchDetailMap.value[task.batch_id]) {
      await loadBatchDetails(task.batch_id)
    }
    return
  }
  if (!taskChildrenMap.value[taskId]) {
    await loadTaskChildren(taskId)
  }
}

function docTaskState(doc) {
  const task = latestTaskByDoc.value[doc.doc_id]
  const docStatus = String(doc?.status || '').toUpperCase()
  if (!task && !docStatus) return null
  const stage = task ? resolveTaskStage(task) : docStatus
  const running = task ? isRunningTask(task) : false
  const status = task ? task.status : 'SUCCEEDED'
  return {
    status,
    stage,
    cls: taskStatusClass(status),
    running,
    chunking: running && ['PARSING', 'CHUNKING'].includes(stage),
    embedding: running && ['EMBEDDING', 'INDEXING'].includes(stage),
    summarizing: running && ['SUMMARIZING', 'SUMMARIZING_DOC', 'SUMMARY_EMBEDDING_DOC', 'SUMMARIZING_KB', 'SUMMARY_EMBEDDING_KB'].includes(stage),
  }
}

function docPrimaryBadge(doc) {
  if (doc?.frozen) return { text: 'frozen', cls: 'badge-muted' }
  if (isDocChunking(doc)) return { text: 'chunking', cls: 'badge-info' }
  if (isDocEmbedding(doc)) return { text: 'embedding', cls: 'badge-info' }
  if (isDocSummarizing(doc)) return { text: 'summarizing', cls: 'badge-info' }

  const chunkCount = Number(doc?.chunk_count || 0)
  const embeddedCount = Number(doc?.embedded_count || 0)
  const hasSummary = !!String(doc?.summary_text || '').trim()

  if (chunkCount <= 0) return { text: 'need chunk', cls: 'badge-warn' }
  if (doc?.embedding_dirty || embeddedCount < chunkCount) return { text: 'need embed', cls: 'badge-warn' }
  if (doc?.summary_dirty || !hasSummary) return { text: 'need summary', cls: 'badge-warn' }
  return { text: 'ok', cls: 'badge-ok' }
}

function chunkBadgeClass(doc) {
  return (doc?.chunk_count || 0) > 0 ? 'badge-info' : 'badge-muted'
}

function embeddedBadgeClass(doc) {
  return (doc?.embedded_count || 0) > 0 ? 'badge-success' : 'badge-muted'
}

function docSummaryPreview(doc) {
  if (isDocSummarizing(doc)) return '总结中...'
  const summary = String(doc?.summary_text || '').trim()
  if (summary) return summary
  return '-'
}

function summarizeActionLabel(doc) {
  if (isDocSummarizing(doc)) return '总结中...'
  if (doc?.summary_text && doc?.summary_dirty) return '重总结*'
  if (doc?.summary_text) return '重总结'
  return '总结'
}

function isDocChunking(doc) {
  return !!docTaskState(doc)?.chunking
}

function isDocEmbedding(doc) {
  return !!docTaskState(doc)?.embedding
}

function isDocSummarizing(doc) {
  return !!docTaskState(doc)?.summarizing
}

function isDocTaskLocked(doc) {
  return isDocChunking(doc) || isDocEmbedding(doc) || isDocSummarizing(doc)
}

function getDocChunkMethod(doc) {
  const key = doc?.doc_id || ''
  return (
    docChunkMethodMap.value[key] ||
    String(doc?.chunk_method || '').trim() ||
    chunkRunConfig.chunk_method ||
    'sentence'
  )
}

function setDocChunkMethod(doc, method) {
  const key = doc?.doc_id || ''
  if (!key) return
  const next = method || 'sentence'
  docChunkMethodMap.value = { ...docChunkMethodMap.value, [key]: next }
  if (doc && typeof doc === 'object') {
    doc.chunk_method = next
  }
}

function normalizeTagList(tags) {
  const out = []
  const seen = new Set()
  for (const t of tags || []) {
    const v = String(t || '').trim()
    if (!v) continue
    const key = v.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    out.push(v)
    if (out.length >= 10) break
  }
  return out
}

function openDocTagModal(doc) {
  if (!doc?.doc_id) return
  docTagModal.doc_id = String(doc.doc_id || '')
  docTagModal.kb_id = String(doc.kb_id || selectedKbId.value || '')
  docTagModal.filename = String(doc.filename || doc.doc_id || '')
  docTagModal.tags = normalizeTagList(doc.tags_json || [])
  docTagInput.value = ''
  ui.showDocTagModal = true
}

function closeDocTagModal() {
  ui.showDocTagModal = false
  docTagInput.value = ''
}

function addDocTagChip() {
  const next = normalizeTagList([...(docTagModal.tags || []), docTagInput.value])
  if (next.length === (docTagModal.tags || []).length) {
    if ((docTagModal.tags || []).length >= 10) {
      pushNotice('warn', '最多支持 10 个标签')
    }
  }
  docTagModal.tags = next
  docTagInput.value = ''
}

function removeDocTagChip(idx) {
  const arr = [...(docTagModal.tags || [])]
  if (idx < 0 || idx >= arr.length) return
  arr.splice(idx, 1)
  docTagModal.tags = arr
}

async function saveDocTagModal() {
  if (!docTagModal.doc_id || !docTagModal.kb_id) return
  const tags = normalizeTagList(docTagModal.tags || [])
  if (!tags.length) {
    pushNotice('warn', '请至少保留一个标签')
    return
  }
  try {
    await callApi(`/api/v1/admin/kbs/${encodeURIComponent(docTagModal.kb_id)}/documents/${encodeURIComponent(docTagModal.doc_id)}/tags`, {
      method: 'POST',
      headers: withHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ mode: 'replace', tags }),
    })
    await loadDocs()
    closeDocTagModal()
    pushNotice('success', '标签已更新')
  } catch (err) {
    pushNotice('error', err instanceof Error ? err.message : String(err))
  }
}

function refreshExpandedRunningTaskChildren() {
  const now = Date.now()
  for (const task of tasks.value) {
    const taskId = task?.task_id
    if (!taskId) continue
    if (!expandedTaskIds.value[taskId]) continue
    if (!isRunningTask({ status: effectiveTaskStatus(task) })) continue
    if (task?.batch_is_parent && task?.batch_id) {
      loadBatchDetails(task.batch_id, { silent: true }).catch(() => {})
      continue
    }
    const last = Number(taskChildrenRefreshAt.value[taskId] || 0)
    if (now - last < 1200) continue
    loadTaskChildren(taskId, { silent: true }).catch(() => {})
  }
}

function isDelimiterMethod(method) {
  return String(method || '').toLowerCase() === 'delimiter'
}

function sanitizeChunkNumber(value, fallback) {
  const n = Number(value)
  if (!Number.isFinite(n) || n <= 0) return fallback
  return Math.floor(n)
}

function buildChunkPayload(method, options) {
  const normalizedMethod = String(method || 'sentence')
  const payload = {
    chunk_method: normalizedMethod,
    chunk_size: sanitizeChunkNumber(options?.chunk_size, 500),
    chunk_overlap: sanitizeChunkNumber(options?.chunk_overlap, 50),
  }
  if (isDelimiterMethod(normalizedMethod)) {
    payload.chunk_delimiter = String(options?.chunk_delimiter || '').trim()
  }
  return payload
}

function getDocChunkConfig(doc) {
  const method = getDocChunkMethod(doc)
  const cfg = {
    chunk_method: method,
    chunk_size: sanitizeChunkNumber(doc?.chunk_size, 500),
    chunk_overlap: sanitizeChunkNumber(doc?.chunk_overlap, 50),
    chunk_delimiter: String(doc?.chunk_delimiter || '').trim() || '\\n\\n',
  }
  if (isDelimiterMethod(method) && !cfg.chunk_delimiter) {
    cfg.chunk_delimiter = '\\n\\n'
  }
  return cfg
}

function applyDocChunkConfigToRun(doc) {
  const cfg = getDocChunkConfig(doc)
  chunkRunConfig.chunk_method = cfg.chunk_method
  chunkRunConfig.chunk_size = cfg.chunk_size
  chunkRunConfig.chunk_overlap = cfg.chunk_overlap
  chunkRunConfig.chunk_delimiter = cfg.chunk_delimiter
}

function validateChunkPayload(payload, label = '分块') {
  if (isDelimiterMethod(payload.chunk_method) && !String(payload.chunk_delimiter || '').trim()) {
    pushNotice('warn', `${label}方式为分隔符时，必须填写分隔符`)
    return false
  }
  return true
}

function chunkActionLabel(doc) {
  const count = Number(doc?.chunk_count || 0)
  const status = String(doc?.status || '').toUpperCase()
  if (count > 0 || ['INDEXED', 'FAILED', 'EMBEDDING', 'INDEXING'].includes(status)) return 'Rechunk'
  return 'Chunk'
}

function canEmbedAgain(doc) {
  const count = Number(doc?.chunk_count || 0)
  return count > 0
}

function mergeOptimisticTasks(nextTasks) {
  const serverItems = Array.isArray(nextTasks) ? nextTasks : []
  const now = Date.now()
  const byId = {}
  const previousOrder = new Map((tasks.value || []).map((t, idx) => [t.task_id, idx]))
  for (const item of serverItems) {
    if (!item?.task_id) continue
    const optimistic = optimisticTaskMap.value[item.task_id]
    byId[item.task_id] = optimistic ? { ...optimistic, ...item } : item
    if (optimistic) {
      delete optimisticTaskMap.value[item.task_id]
    }
  }
  for (const [taskId, task] of Object.entries(optimisticTaskMap.value)) {
    const ts = Date.parse(task.updated_at || '') || 0
    if (now - ts > 120000) {
      delete optimisticTaskMap.value[taskId]
      continue
    }
    if (!byId[taskId]) byId[taskId] = task
  }
  return Object.values(byId).sort((a, b) => {
    const aOld = previousOrder.has(a.task_id)
    const bOld = previousOrder.has(b.task_id)
    if (aOld && bOld) {
      return Number(previousOrder.get(a.task_id)) - Number(previousOrder.get(b.task_id))
    }
    if (aOld !== bOld) {
      // New tasks on top; existing tasks keep stable order to avoid jumping.
      return aOld ? 1 : -1
    }
    const ca = Date.parse(a.created_at || '') || 0
    const cb = Date.parse(b.created_at || '') || 0
    if (ca !== cb) return cb - ca
    const ua = Date.parse(a.updated_at || '') || 0
    const ub = Date.parse(b.updated_at || '') || 0
    return ub - ua
  })
}

async function refreshDocsFromTaskChange() {
  if (!isLoggedIn.value) return
  if (docsRefreshTimer) clearTimeout(docsRefreshTimer)
  docsRefreshTimer = setTimeout(async () => {
    docsRefreshTimer = null
    try {
      await loadDocs()
    } catch {
      // ignore docs refresh failure
    }
  }, 700)
}

async function refreshKbsFromTaskChange() {
  if (!isLoggedIn.value) return
  if (kbsRefreshTimer) clearTimeout(kbsRefreshTimer)
  kbsRefreshTimer = setTimeout(async () => {
    kbsRefreshTimer = null
    try {
      await loadKbs()
    } catch {
      // ignore kb refresh failure
    }
  }, 700)
}

function applyTaskUpdate(nextTasks) {
  tasks.value = mergeOptimisticTasks(nextTasks)
  const nextTaskNameMap = { ...taskNameByTaskId.value }
  for (const task of tasks.value) {
    const taskId = String(task?.task_id || '')
    if (!taskId) continue
    const name = String(task?.filename || task?.task_name || '').trim()
    if (name) nextTaskNameMap[taskId] = name
  }
  taskNameByTaskId.value = nextTaskNameMap
  const alive = new Set(tasks.value.map((t) => t.task_id))
  const aliveBatches = new Set(tasks.value.filter((t) => !!t?.batch_id).map((t) => String(t.batch_id)))
  const nextChildren = {}
  const nextChildrenRefreshAt = {}
  for (const [taskId, list] of Object.entries(taskChildrenMap.value)) {
    if (alive.has(taskId)) {
      nextChildren[taskId] = list
      nextChildrenRefreshAt[taskId] = taskChildrenRefreshAt.value[taskId] || 0
    }
  }
  taskChildrenMap.value = nextChildren
  taskChildrenRefreshAt.value = nextChildrenRefreshAt
  const nextBatchDetailMap = {}
  for (const [batchId, detail] of Object.entries(batchDetailMap.value)) {
    if (aliveBatches.has(batchId)) nextBatchDetailMap[batchId] = detail
  }
  batchDetailMap.value = nextBatchDetailMap
  const nextMap = {}
  const nextStageMap = {}
  let hasTaskChanged = false
  for (const task of tasks.value) {
    const prevStatus = taskStatusMap.value[task.task_id]
    const currStatus = task.status
    const prevStage = taskStageMap.value[task.task_id]
    const currStage = task.stage
    nextMap[task.task_id] = currStatus
    nextStageMap[task.task_id] = currStage
    if (prevStatus !== currStatus) {
      hasTaskChanged = true
    }
    if (prevStage !== currStage) {
      hasTaskChanged = true
    }
    if (prevStatus === undefined) continue
    if (!taskStatusReady.value) continue
    if (prevStatus === currStatus) continue
    if (task?.batch_id && !task?.batch_is_parent) continue
    const effective = effectiveTaskStatus(task)
    const prevEffective = normalizeStatus(prevStatus)
    if (['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(effective)) {
      expandedTaskIds.value = { ...expandedTaskIds.value, [task.task_id]: false }
    }
    if (prevEffective !== effective) {
      if (effective === 'SUCCEEDED') pushNotice('success', summarizeTask(task))
      if (effective === 'FAILED') pushNotice('error', summarizeTask(task))
      if (effective === 'CANCELLED') pushNotice('warn', summarizeTask(task))
    }
  }
  if (Object.keys(taskStatusMap.value).length !== Object.keys(nextMap).length) {
    hasTaskChanged = true
  }
  taskStatusMap.value = nextMap
  taskStageMap.value = nextStageMap
  // Progress can change while status/stage stays RUNNING+EMBEDDING.
  // Keep expanded running task children polling in that case.
  refreshExpandedRunningTaskChildren()
  if (hasTaskChanged) {
    refreshDocsFromTaskChange()
    refreshKbsFromTaskChange()
    if (ui.currentView === 'chunks' && selectedDocId.value) {
      loadChunks().catch(() => {})
    }
  }
  if (!taskStatusReady.value) taskStatusReady.value = true
}

function withHeaders(extra = {}) {
  const headers = {
    Authorization: `Bearer ${token.value}`,
    'X-Tenant-Id': config.tenantId,
    'X-Actor-Id': config.actorId,
    'X-Source-Service': config.sourceService,
    ...extra,
  }
  if (config.traceId.trim()) headers['X-Trace-Id'] = config.traceId.trim()
  return headers
}

async function parseJsonResponse(resp) {
  const contentType = resp.headers.get('content-type') || ''
  if (contentType.includes('application/json')) return await resp.json()
  return { raw: await resp.text() }
}

function normalizeApiErrorMessage(data, status) {
  const detail = data?.detail ?? data?.error ?? data?.raw
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail) && detail.length) {
    return detail
      .map((x) => {
        if (typeof x === 'string') return x
        if (x && typeof x === 'object') {
          const loc = Array.isArray(x.loc) ? x.loc.join('.') : ''
          const msg = String(x.msg || x.message || '').trim()
          if (loc && msg) return `${loc}: ${msg}`
          if (msg) return msg
          try {
            return JSON.stringify(x)
          } catch {
            return String(x)
          }
        }
        return String(x)
      })
      .filter(Boolean)
      .join('; ')
  }
  if (detail && typeof detail === 'object') {
    if (typeof detail.message === 'string' && detail.message.trim()) return detail.message
    if (typeof detail.reason === 'string' && detail.reason.trim()) return detail.reason
    try {
      return JSON.stringify(detail)
    } catch {
      return String(detail)
    }
  }
  return `Request failed: ${status}`
}

async function callApi(path, options = {}) {
  const resp = await fetch(`${config.executionBaseUrl}${path}`, options)
  const data = await parseJsonResponse(resp)
  if (!resp.ok) throw new Error(normalizeApiErrorMessage(data, resp.status))
  return data
}

async function login() {
  loginError.value = ''
  loading.login = true
  try {
    const data = await callApi('/api/v1/admin/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(loginForm),
    })
    token.value = data.access_token
    role.value = data.role || ''
    await refreshAll()
    connectTaskStream()
    pushNotice('success', '登录成功')
  } catch (err) {
    loginError.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.login = false
  }
}

function onLoginEnter() {
  if (!loading.login) {
    login()
  }
}

function logout() {
  disconnectTaskStream()
  if (docsRefreshTimer) {
    clearTimeout(docsRefreshTimer)
    docsRefreshTimer = null
  }
  if (kbsRefreshTimer) {
    clearTimeout(kbsRefreshTimer)
    kbsRefreshTimer = null
  }
  token.value = ''
  role.value = ''
  kbs.value = []
  docs.value = []
  chunks.value = []
  tasks.value = []
  notices.value = []
  taskStatusMap.value = {}
  taskStageMap.value = {}
  taskNameByTaskId.value = {}
  taskChildrenMap.value = {}
  taskChildrenLoading.value = {}
  taskChildrenRefreshAt.value = {}
  batchDetailMap.value = {}
  batchDetailLoading.value = {}
  expandedTaskIds.value = {}
  taskStatusReady.value = false
  selectedDocIds.value = []
  selectedKbId.value = ''
  selectedDocId.value = ''
}

function connectTaskStream() {
  disconnectTaskStream()
  if (!token.value) return
  const params = new URLSearchParams()
  params.set('token', token.value)
  if (selectedKbId.value) {
    params.set('kb_id', selectedKbId.value)
  }
  const url = `${config.executionBaseUrl}/api/v1/admin/tasks/stream?${params.toString()}`
  taskStream = new EventSource(url)
  taskStream.onopen = () => {
    streamConnected.value = true
  }
  taskStream.onerror = () => {
    streamConnected.value = false
  }
  taskStream.addEventListener('tasks', (ev) => {
    try {
      const payload = JSON.parse(ev.data || '{}')
      applyTaskUpdate(payload.items || [])
    } catch {
      // ignore malformed stream payload
    }
  })
}

function disconnectTaskStream() {
  if (taskStream) {
    taskStream.close()
    taskStream = null
  }
  streamConnected.value = false
}

async function loadKbs() {
  loading.kbs = true
  try {
    const data = await callApi('/api/v1/admin/kbs', { headers: withHeaders() })
    kbs.value = data.items || []
    const hasSelected = kbs.value.some((kb) => kb.kb_id === selectedKbId.value)
    if (!hasSelected) {
      selectedKbId.value = kbs.value.length ? kbs.value[0].kb_id : ''
    }
  } finally {
    loading.kbs = false
  }
}

async function createKb() {
  loading.createKb = true
  try {
    const data = await callApi('/api/v1/admin/kbs', {
      method: 'POST',
      headers: withHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(kbForm),
    })
    kbForm.name = ''
    ui.showCreateKbModal = false
    await loadKbs()
    if (data?.name_adjusted) {
      pushNotice('warn', `名称重复，已自动调整为「${data?.item?.name || ''}」`)
    } else {
      pushNotice('success', '知识库创建成功')
    }
  } finally {
    loading.createKb = false
  }
}

function onCreateKbEnter() {
  if (!loading.createKb && kbForm.name.trim()) {
    createKb()
  }
}

function openRenameKbModal(kb) {
  kbMenuOpenId.value = ''
  renameKbForm.kb_id = kb.kb_id
  renameKbForm.name = kb.name || ''
  ui.showRenameKbModal = true
}

async function renameKb() {
  if (!renameKbForm.kb_id || !renameKbForm.name.trim()) return
  loading.renameKb = true
  try {
    const data = await callApi(`/api/v1/admin/kbs/${encodeURIComponent(renameKbForm.kb_id)}`, {
      method: 'PATCH',
      headers: withHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ name: renameKbForm.name.trim() }),
    })
    ui.showRenameKbModal = false
    await loadKbs()
    if (data?.name_adjusted) {
      pushNotice('warn', `名称重复，已自动调整为「${data?.item?.name || ''}」`)
    } else {
      pushNotice('success', '知识库已重命名')
    }
  } finally {
    loading.renameKb = false
  }
}

function onRenameKbEnter() {
  if (!loading.renameKb && renameKbForm.name.trim()) {
    renameKb()
  }
}

async function deleteKb(kbId) {
  loading.deleteKb = true
  try {
    await callApi(`/api/v1/admin/kbs/${encodeURIComponent(kbId)}`, {
      method: 'DELETE',
      headers: withHeaders(),
    })
    if (selectedKbId.value === kbId) {
      selectedKbId.value = ''
      docs.value = []
      chunks.value = []
    }
    await loadKbs()
    pushNotice('success', '知识库已删除')
  } finally {
    loading.deleteKb = false
  }
}

function requestDeleteKb(kb) {
  kbMenuOpenId.value = ''
  deleteKbForm.kb_id = kb.kb_id
  deleteKbForm.name = kb.name || kb.kb_id
  ui.showDeleteKbConfirmModal = true
}

function toggleKbMenu(kbId) {
  kbMenuOpenId.value = kbMenuOpenId.value === kbId ? '' : kbId
}

async function confirmDeleteKb() {
  if (!deleteKbForm.kb_id) return
  ui.showDeleteKbConfirmModal = false
  await deleteKb(deleteKbForm.kb_id)
  deleteKbForm.kb_id = ''
  deleteKbForm.name = ''
}

async function summarizeKb(kb) {
  if (!kb?.kb_id) return
  if (Number(kb?.stats?.document_count || 0) <= 0) {
    pushNotice('warn', '库内无文档，无法执行库总结')
    return
  }
  try {
    await callApi(`/api/v1/admin/kbs/${encodeURIComponent(kb.kb_id)}/summarize`, {
      method: 'POST',
      headers: withHeaders(),
    })
    kbMenuOpenId.value = ''
    await loadTasks()
    pushNotice('info', `已提交「${kb.name || kb.kb_id}」库总结任务`)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    pushNotice('error', msg)
  }
}

async function setKbFrozen(kb, frozen) {
  if (!kb?.kb_id) return
  try {
    await callApi(`/api/v1/admin/kbs/${encodeURIComponent(kb.kb_id)}/freeze`, {
      method: 'PATCH',
      headers: withHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ frozen: !!frozen }),
    })
    kbMenuOpenId.value = ''
    await loadKbs()
    if (selectedKbId.value === kb.kb_id) {
      await loadDocs()
    }
    pushNotice('success', `${kb.name || kb.kb_id} 已${frozen ? '冻结' : '解冻'}`)
  } catch (err) {
    pushNotice('error', err instanceof Error ? err.message : String(err))
  }
}

async function setDocumentFrozen(doc, frozen) {
  const kbId = String(doc?.kb_id || selectedKbId.value || '')
  const docId = String(doc?.doc_id || '')
  if (!kbId || !docId) return
  try {
    await callApi(`/api/v1/admin/kbs/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(docId)}/freeze`, {
      method: 'PATCH',
      headers: withHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ frozen: !!frozen }),
    })
    await loadDocs()
    if (selectedDocId.value === docId && ui.currentView === 'chunks') {
      await loadChunks()
    }
    pushNotice('success', `${doc.filename || docId} 已${frozen ? '冻结' : '解冻'}`)
  } catch (err) {
    pushNotice('error', err instanceof Error ? err.message : String(err))
  }
}

async function setChunkFrozen(chunk, frozen) {
  const kbId = String(selectedKbId.value || '')
  const docId = String(selectedDocId.value || '')
  const chunkId = String(chunk?.chunk_id || '')
  if (!kbId || !docId || !chunkId) return
  try {
    await callApi(
      `/api/v1/admin/kbs/${encodeURIComponent(kbId)}/documents/${encodeURIComponent(docId)}/chunks/${encodeURIComponent(chunkId)}/freeze`,
      {
        method: 'PATCH',
        headers: withHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ frozen: !!frozen }),
      },
    )
    chunks.value = chunks.value.map((x) => (x.chunk_id === chunkId ? { ...x, frozen: !!frozen } : x))
    if (selectedChunk.value?.chunk_id === chunkId) {
      selectedChunk.value = { ...selectedChunk.value, frozen: !!frozen }
    }
    pushNotice('success', `Chunk #${Number(chunk?.chunk_index || 0) + 1} 已${frozen ? '冻结' : '解冻'}`)
  } catch (err) {
    pushNotice('error', err instanceof Error ? err.message : String(err))
  }
}

async function loadDocs() {
  loading.docs = true
  try {
    if (!selectedKbId.value) {
      const allDocs = []
      for (const kb of kbs.value) {
        const data = await callApi(`/api/v1/admin/kbs/${encodeURIComponent(kb.kb_id)}/documents?limit=200`, {
          headers: withHeaders(),
        })
        for (const doc of data.items || []) {
          allDocs.push({ ...doc, kb_name: kb.name, kb_id: kb.kb_id })
        }
      }
      docs.value = allDocs
      const nextMethodMap = {}
      for (const d of allDocs) {
        if (!d?.doc_id) continue
        nextMethodMap[d.doc_id] = String(d.chunk_method || '').trim() || 'sentence'
      }
      docChunkMethodMap.value = nextMethodMap
      const validKeys = new Set(allDocs.map((d) => docDeleteKey(d.doc_id, d.kb_id)))
      pendingDocDeletes.value = Object.fromEntries(
        Object.entries(pendingDocDeletes.value).filter(([k]) => validKeys.has(k)),
      )
      const validDocIds = new Set(allDocs.map((d) => d.doc_id))
      selectedDocIds.value = selectedDocIds.value.filter((id) => validDocIds.has(id))
      return
    }
    const data = await callApi(`/api/v1/admin/kbs/${encodeURIComponent(selectedKbId.value)}/documents?limit=200`, {
      headers: withHeaders(),
    })
    docs.value = data.items || []
    const nextMethodMap = {}
    for (const d of docs.value) {
      if (!d?.doc_id) continue
      nextMethodMap[d.doc_id] = String(d.chunk_method || '').trim() || 'sentence'
    }
    docChunkMethodMap.value = nextMethodMap
    const validKeys = new Set((docs.value || []).map((d) => docDeleteKey(d.doc_id, selectedKbId.value)))
    pendingDocDeletes.value = Object.fromEntries(
      Object.entries(pendingDocDeletes.value).filter(([k]) => validKeys.has(k)),
    )
    const validDocIds = new Set((docs.value || []).map((d) => d.doc_id))
    selectedDocIds.value = selectedDocIds.value.filter((id) => validDocIds.has(id))
  } finally {
    loading.docs = false
  }
}

async function loadTasks() {
  loading.tasks = true
  try {
    const kbQ = selectedKbId.value ? `?kb_id=${encodeURIComponent(selectedKbId.value)}&limit=100` : '?limit=100'
    const data = await callApi(`/api/v1/admin/tasks${kbQ}`, { headers: withHeaders() })
    applyTaskUpdate(data.items || [])
  } finally {
    loading.tasks = false
  }
}

async function clearTasks() {
  const scopeText = selectedKbId.value ? '当前知识库' : '全部知识库'
  const ok = window.confirm(`确认清空${scopeText}的已完成/失败/取消任务记录吗？`)
  if (!ok) return
  loading.clearTasks = true
  try {
    const query = selectedKbId.value ? `?kb_id=${encodeURIComponent(selectedKbId.value)}` : ''
    const data = await callApi(`/api/v1/admin/tasks${query}`, {
      method: 'DELETE',
      headers: withHeaders(),
    })
    const deletedTasks = Number(data?.result?.deleted_tasks || 0)
    const deletedEvents = Number(data?.result?.deleted_events || 0)
    // Clear optimistic local-only tasks to avoid ghost rows reappearing after clear.
    optimisticTaskMap.value = {}
    await loadTasks()
    if (deletedTasks > 0) {
      pushNotice('success', `已清空 ${deletedTasks} 条任务（事件 ${deletedEvents} 条）`)
    } else {
      pushNotice('info', '没有可清空的历史任务')
    }
  } finally {
    loading.clearTasks = false
  }
}

async function loadUploadSettings() {
  try {
    const data = await callApi('/api/v1/admin/system/upload-settings', { headers: withHeaders() })
    uploadLimitMb.value = Number(data?.item?.max_file_size_mb || 20)
  } catch {
    uploadLimitMb.value = 20
  }
}

async function loadChunks() {
  if (!selectedKbId.value || !selectedDocId.value) return
  loading.chunks = true
  try {
    const data = await callApi(
      `/api/v1/admin/kbs/${encodeURIComponent(selectedKbId.value)}/documents/${encodeURIComponent(selectedDocId.value)}/chunks?limit=400`,
      { headers: withHeaders() },
    )
    chunks.value = data.items || []
  } finally {
    loading.chunks = false
  }
}

function openUploadModal() {
  uploadQueue.value = []
  uploadProcessMode.value = 'UPLOAD_CLEAN_CHUNK_EMBED'
  uploadChunkConfig.chunk_method = 'sentence'
  uploadChunkConfig.chunk_size = 500
  uploadChunkConfig.chunk_overlap = 50
  uploadChunkConfig.chunk_delimiter = '\\n\\n'
  loadUploadSettings()
  ui.showUploadModal = true
}

async function uploadDocument() {
  if (!selectedKbId.value) return
  if (!uploadQueue.value.length) {
    pushNotice('info', '请先添加待上传文件')
    return
  }
  const queuedItems = uploadQueue.value.slice()
  const localTaskMeta = []
  const localTaskIds = []
  const nowIso = new Date().toISOString()
  const isAsyncPipeline = uploadProcessMode.value === 'UPLOAD_CLEAN_CHUNK_EMBED'
  for (let i = 0; i < queuedItems.length; i += 1) {
    const item = queuedItems[i]
    const localTaskId = `local-upload-${Date.now()}-${i}`
    localTaskIds.push(localTaskId)
    localTaskMeta.push({ task_id: localTaskId, filename: item.name, idx: i })
    optimisticTaskMap.value[localTaskId] = {
      task_id: localTaskId,
      tenant_id: config.tenantId,
      kb_id: selectedKbId.value,
      doc_id: '',
      filename: item.name,
      task_name: item.name,
      task_type: isAsyncPipeline ? 'DOC_INGEST' : 'DOC_UPLOAD',
      status: 'RUNNING',
      stage: 'UPLOADING',
      updated_at: nowIso,
      local_only: true,
    }
    taskNameByTaskId.value[localTaskId] = item.name
  }
  applyTaskUpdate(tasks.value)
  ui.showTaskDrawer = true
  ui.showUploadModal = false
  uploadQueue.value = []
  loading.upload = true
  try {
    const uploadChunkPayload = buildChunkPayload(uploadChunkConfig.chunk_method, uploadChunkConfig)
    if (uploadWillChunk.value && !validateChunkPayload(uploadChunkPayload, '上传分块')) return
    const form = new FormData()
    form.append('process_mode', uploadProcessMode.value)
    form.append('chunk_method', uploadChunkPayload.chunk_method)
    form.append('chunk_size', String(uploadChunkPayload.chunk_size))
    form.append('chunk_overlap', String(uploadChunkPayload.chunk_overlap))
    if (uploadChunkPayload.chunk_delimiter) {
      form.append('chunk_delimiter', uploadChunkPayload.chunk_delimiter)
    }
    for (const item of queuedItems) {
      form.append('files', item.file, item.name)
    }
    const data = await callApi(`/api/v1/admin/kbs/${encodeURIComponent(selectedKbId.value)}/documents/upload`, {
      method: 'POST',
      headers: withHeaders(),
      body: form,
    })
    if (isAsyncPipeline) {
      for (const localTaskId of localTaskIds) {
        delete optimisticTaskMap.value[localTaskId]
      }
      const acceptedAt = new Date().toISOString()
      for (const item of data.accepted || []) {
        if (!item?.task_id) continue
        if (item.filename) taskNameByTaskId.value[item.task_id] = item.filename
        optimisticTaskMap.value[item.task_id] = {
          task_id: item.task_id,
          tenant_id: config.tenantId,
          kb_id: selectedKbId.value,
          doc_id: item.doc_id,
          filename: item.filename,
          task_name: item.filename,
          task_type: 'DOC_INGEST',
          status: 'RUNNING',
          stage: 'UPLOADING',
          updated_at: acceptedAt,
        }
      }
    } else {
      const acceptedAt = new Date().toISOString()
      const accepted = data.accepted || []
      const skipped = data.skipped || []
      const finalStage = uploadProcessMode.value === 'UPLOAD_ONLY' ? 'UPLOADED_RAW' : 'CLEANED'
      for (const info of localTaskMeta) {
        const hit = accepted[info.idx]
        if (hit) {
          optimisticTaskMap.value[info.task_id] = {
            ...optimisticTaskMap.value[info.task_id],
            doc_id: hit.doc_id || '',
            status: 'SUCCEEDED',
            stage: finalStage,
            updated_at: acceptedAt,
            local_only: true,
          }
        } else {
          const reason = skipped[Math.max(0, info.idx - accepted.length)]?.reason || '上传失败'
          optimisticTaskMap.value[info.task_id] = {
            ...optimisticTaskMap.value[info.task_id],
            status: 'FAILED',
            stage: 'FAILED',
            error_message: reason,
            updated_at: acceptedAt,
            local_only: true,
          }
        }
      }
    }
    await loadTasks()
    await loadDocs()
    await loadKbs()
    const accepted = Number(data.accepted_count || 0)
    const skipped = Number(data.skipped_count || 0)
    if (accepted > 0 && uploadProcessMode.value === 'UPLOAD_CLEAN_CHUNK_EMBED') {
      pushNotice('info', `已提交 ${accepted} 个文件到处理队列`)
    } else if (accepted > 0 && uploadProcessMode.value === 'UPLOAD_AND_CLEAN') {
      pushNotice('success', `已上传并清洗 ${accepted} 个文件`)
    } else if (accepted > 0) {
      pushNotice('success', `已上传 ${accepted} 个文件（未处理）`)
    }
    if (skipped > 0) {
      const firstReason = data.skipped?.[0]?.reason || '部分文件解析失败'
      pushNotice('warn', `${skipped} 个文件未处理：${firstReason}`)
    }
  } catch (err) {
    for (const localTaskId of localTaskIds) {
      delete optimisticTaskMap.value[localTaskId]
    }
    uploadQueue.value = queuedItems
    ui.showUploadModal = true
    pushNotice('error', err instanceof Error ? err.message : String(err))
  } finally {
    loading.upload = false
  }
}

async function cancelTask(taskId) {
  await callApi(`/api/v1/admin/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: 'POST',
    headers: withHeaders(),
  })
  await loadTasks()
  pushNotice('warn', '已请求取消任务')
}

async function commitDraftsForDoc(docId, kbId = null) {
  const targetKbId = selectedKbId.value || kbId
  if (!targetKbId || !docId) return
  await callApi(
    `/api/v1/admin/kbs/${encodeURIComponent(targetKbId)}/documents/${encodeURIComponent(docId)}/chunks/commit`,
    {
      method: 'POST',
      headers: withHeaders(),
    },
  )
  if (ui.currentView === 'chunks' && selectedDocId.value === docId) {
    await loadChunks()
  }
  await loadDocs()
  await loadTasks()
}

async function retryTask(task) {
  const taskType = normalizeStatus(task?.task_type)
  const taskId = String(task?.task_id || '')
  const docId = task?.doc_id
  const kbId = task?.kb_id || selectedKbId.value
  if (!docId || !kbId) {
    pushNotice('warn', '任务缺少文档或知识库信息，无法重试')
    return
  }
  if (taskId) {
    retryLockedTaskIds.value = { ...retryLockedTaskIds.value, [taskId]: true }
  }
  try {
    if (taskType === 'DOC_CHUNK') {
      await chunkDocument(docId, kbId)
      return
    }
    if (taskType === 'DOC_REINDEX' || taskType === 'DOC_INGEST' || taskType === 'DOC_UPLOAD') {
      await reindexDocument(docId, kbId)
      return
    }
    if (taskType === 'DOC_SUMMARIZE') {
      await summarizeDocument(docId, kbId)
      return
    }
    if (taskType === 'CHUNK_COMMIT') {
      await commitDraftsForDoc(docId, kbId)
      pushNotice('info', '草稿提交重试已发起')
      return
    }
    pushNotice('warn', '当前任务类型暂不支持重试')
  } catch (err) {
    if (taskId) {
      const next = { ...retryLockedTaskIds.value }
      delete next[taskId]
      retryLockedTaskIds.value = next
    }
    pushNotice('error', err instanceof Error ? err.message : String(err))
  }
}

async function deleteDocument(docId, kbId = null, silent = false) {
  const targetKbId = selectedKbId.value || kbId
  if (!targetKbId) return
  await callApi(`/api/v1/admin/kbs/${encodeURIComponent(targetKbId)}/documents/${encodeURIComponent(docId)}`, {
    method: 'DELETE',
    headers: withHeaders(),
  })
  if (selectedDocId.value === docId) {
    selectedDocId.value = ''
    chunks.value = []
    ui.currentView = 'docs'
  }
  if (!silent) {
    await loadDocs()
    await loadTasks()
    pushNotice('success', '文档已删除，并清理相关数据')
  }
}

function docDeleteKey(docId, kbId) {
  return `${kbId}::${docId}`
}

function togglePendingDeleteDoc(doc) {
  const kbId = doc.kb_id || selectedKbId.value
  if (!kbId) return
  const key = docDeleteKey(doc.doc_id, kbId)
  if (pendingDocDeletes.value[key]) {
    delete pendingDocDeletes.value[key]
    pendingDocDeletes.value = { ...pendingDocDeletes.value }
    return
  }
  pendingDocDeletes.value[key] = {
    key,
    kb_id: kbId,
    doc_id: doc.doc_id,
    filename: doc.filename,
  }
  pendingDocDeletes.value = { ...pendingDocDeletes.value }
}

function openDocDeleteCommitModal() {
  if (!pendingDocDeleteCount.value) {
    pushNotice('info', '当前没有待删除文档')
    return
  }
  ui.showDocDeleteCommitModal = true
}

async function commitPendingDocDeletes() {
  if (!pendingDocDeleteCount.value) return
  loading.commitDocDelete = true
  try {
    const items = pendingDocDeleteItems.value.slice()
    let success = 0
    for (const item of items) {
      try {
        await deleteDocument(item.doc_id, item.kb_id, true)
        success += 1
      } catch (err) {
        pushNotice('error', `删除失败：${item.filename}`)
      }
    }
    pendingDocDeletes.value = {}
    ui.showDocDeleteCommitModal = false
    await loadDocs()
    await loadTasks()
    if (success > 0) pushNotice('success', `已删除 ${success} 个文档`)
  } finally {
    loading.commitDocDelete = false
  }
}

async function reindexDocument(docId, kbId = null) {
  const targetKbId = selectedKbId.value || kbId
  if (!targetKbId) return
  try {
    const targetDoc = docs.value.find((d) => d.doc_id === docId)
    const chunkOptions =
      targetDoc && !(ui.currentView === 'chunks' && selectedDocId.value === docId)
        ? getDocChunkConfig(targetDoc)
        : chunkRunConfig
    const chunkMethod = targetDoc ? getDocChunkMethod(targetDoc) : chunkRunConfig.chunk_method
    const payload = buildChunkPayload(chunkMethod, chunkOptions)
    if (!validateChunkPayload(payload, '重嵌入')) return
    await callApi(
      `/api/v1/admin/kbs/${encodeURIComponent(targetKbId)}/documents/${encodeURIComponent(docId)}/reindex`,
      {
        method: 'POST',
        headers: withHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
      },
    )
    await loadTasks()
    await loadDocs()
    if (ui.currentView === 'chunks' && selectedDocId.value === docId) {
      await loadChunks()
    }
    pushNotice('info', '重建任务已提交')
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    if (/not found/i.test(msg)) {
      pushNotice('warn', '文档不存在或已被删除，已刷新文档列表')
      await loadDocs()
      return
    }
    pushNotice('error', msg)
  }
}

async function summarizeDocument(docId, kbId = null) {
  const targetKbId = selectedKbId.value || kbId
  if (!targetKbId) return
  try {
    await callApi(
      `/api/v1/admin/kbs/${encodeURIComponent(targetKbId)}/documents/${encodeURIComponent(docId)}/summarize`,
      {
        method: 'POST',
        headers: withHeaders(),
      },
    )
    await loadTasks()
    await loadDocs()
    pushNotice('info', '总结任务已提交')
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    if (/not found/i.test(msg)) {
      pushNotice('warn', '文档不存在或已被删除，已刷新文档列表')
      await loadDocs()
      return
    }
    pushNotice('error', msg)
  }
}

async function chunkDocument(docId, kbId = null) {
  const targetKbId = selectedKbId.value || kbId
  if (!targetKbId) return
  try {
    const targetDoc = docs.value.find((d) => d.doc_id === docId)
    const chunkOptions =
      targetDoc && !(ui.currentView === 'chunks' && selectedDocId.value === docId)
        ? getDocChunkConfig(targetDoc)
        : chunkRunConfig
    const chunkMethod = targetDoc ? getDocChunkMethod(targetDoc) : chunkRunConfig.chunk_method
    const payload = buildChunkPayload(chunkMethod, chunkOptions)
    if (!validateChunkPayload(payload, '切片')) return
    await callApi(
      `/api/v1/admin/kbs/${encodeURIComponent(targetKbId)}/documents/${encodeURIComponent(docId)}/chunk`,
      {
        method: 'POST',
        headers: withHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
      },
    )
    await loadTasks()
    await loadDocs()
    if (ui.currentView === 'chunks' && selectedDocId.value === docId) {
      await loadChunks()
    }
    pushNotice('info', 'Chunk/Rechunk 任务已提交')
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    if (/not found/i.test(msg)) {
      pushNotice('warn', '文档不存在或已被删除，已刷新文档列表')
      await loadDocs()
      return
    }
    pushNotice('error', msg)
  }
}

function buildBatchRequestPayload() {
  const op = String(batchActionForm.operation || 'chunk')
  const base = {
    operation: op,
    doc_ids: batchEligibility.value.eligibleDocIds.slice(),
  }
  if (op === 'chunk') {
    const payload = buildChunkPayload(batchActionForm.chunk_method, batchActionForm)
    return {
      ...base,
      chunk_method: payload.chunk_method,
      chunk_size: payload.chunk_size,
      chunk_overlap: payload.chunk_overlap,
      chunk_delimiter: payload.chunk_delimiter || null,
    }
  }
  if (op === 'embed') {
    return base
  }
  if (op === 'move') {
    return { ...base, target_kb_id: (batchActionForm.target_kb_id || '').trim() }
  }
  if (op === 'tag') {
    const tags = String(batchActionForm.tags_text || '')
      .split(/[,\n，]/g)
      .map((x) => x.trim())
      .filter(Boolean)
    return { ...base, tags }
  }
  return base
}

function openBatchActionModal() {
  if (selectedDocCount.value <= 0 || !selectedKbId.value) {
    pushNotice('info', '请先选择文档')
    return
  }
  if (!batchActionForm.operation) batchActionForm.operation = 'chunk'
  batchShowPrereqList.value = false
  batchActionForm.chunk_method = chunkRunConfig.chunk_method
  batchActionForm.chunk_size = chunkRunConfig.chunk_size
  batchActionForm.chunk_overlap = chunkRunConfig.chunk_overlap
  batchActionForm.chunk_delimiter = chunkRunConfig.chunk_delimiter
  ui.showBatchModal = true
}

async function runBatchAction() {
  if (!canRunBatchAction.value) {
    if (batchEligibility.value.selectedCount > 0 && batchEligibility.value.eligibleCount <= 0) {
      pushNotice('warn', '当前选中文档均不满足该批量操作条件，请先处理前置步骤')
      return
    }
    pushNotice('info', '请先选择文档')
    return
  }
  const payload = buildBatchRequestPayload()
  if (payload.operation === 'chunk' && !validateChunkPayload(payload, '批量任务')) return
  if (payload.operation === 'move' && !payload.target_kb_id) {
    pushNotice('warn', '请选择目标知识库')
    return
  }
  if (payload.operation === 'tag' && (!payload.tags || !payload.tags.length)) {
    pushNotice('warn', '请填写至少一个标签')
    return
  }
  try {
    const excludedCount = Number(batchEligibility.value.excludedCount || 0)
    const data = await callApi(`/api/v1/admin/kbs/${encodeURIComponent(selectedKbId.value)}/documents/batch`, {
      method: 'POST',
      headers: withHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(payload),
    })
    clearSelectedDocs()
    await loadTasks()
    await loadDocs()
    if (payload.operation === 'move') {
      await loadKbs()
    }
    ui.showBatchModal = false
    const extraText = excludedCount > 0 ? `，排除 ${excludedCount}` : ''
    pushNotice('info', `批量任务已提交：成功 ${Number(data.accepted_count || 0)}，失败 ${Number(data.failed_count || 0)}${extraText}`)
    if (Array.isArray(data.failed) && data.failed.length > 0) {
      const top = data.failed
        .slice(0, 3)
        .map((x) => `${x.filename || x.doc_id || '文档'}: ${x.reason || '失败'}`)
        .join('；')
      pushNotice('warn', `失败原因（部分）：${top}`)
    }
    if (data.batch_id) {
      ui.showTaskDrawer = true
    }
  } catch (err) {
    pushNotice('error', err instanceof Error ? err.message : String(err))
  }
}

watch(
  () => batchActionForm.operation,
  () => {
    batchShowPrereqList.value = false
  },
)

async function loadBatchDetails(batchId, options = {}) {
  if (!batchId) return
  if (batchDetailLoading.value[batchId]) return
  const silent = !!options.silent
  if (!silent) {
    batchDetailLoading.value = { ...batchDetailLoading.value, [batchId]: true }
  }
  try {
    const data = await callApi(`/api/v1/admin/tasks/batches/${encodeURIComponent(batchId)}?limit=500`, {
      headers: withHeaders(),
    })
    batchDetailMap.value = {
      ...batchDetailMap.value,
      [batchId]: { item: data.item || {}, items: (data.items || []).filter((x) => !x.batch_is_parent) },
    }
  } finally {
    if (!silent) {
      const next = { ...batchDetailLoading.value }
      delete next[batchId]
      batchDetailLoading.value = next
    }
  }
}

async function retryBatchFailed(batchId) {
  if (!batchId) return
  const key = String(batchId)
  if (retryLockedBatchIds.value[key]) return
  retryLockedBatchIds.value = { ...retryLockedBatchIds.value, [key]: true }
  try {
    await callApi(`/api/v1/admin/tasks/batches/${encodeURIComponent(batchId)}/retry-failed`, {
      method: 'POST',
      headers: withHeaders(),
    })
    await loadTasks()
    await loadBatchDetails(batchId)
    pushNotice('info', '已提交批次失败项重试')
  } catch (err) {
    const next = { ...retryLockedBatchIds.value }
    delete next[key]
    retryLockedBatchIds.value = next
    pushNotice('error', err instanceof Error ? err.message : String(err))
  }
}

async function setDraftDelete(chunkId) {
  await callApi(
    `/api/v1/admin/kbs/${encodeURIComponent(selectedKbId.value)}/documents/${encodeURIComponent(selectedDocId.value)}/chunks/${encodeURIComponent(chunkId)}/draft`,
    {
      method: 'PUT',
      headers: withHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ action: 'delete' }),
    },
  )
  await loadChunks()
}

async function setDraftEdit(chunkId) {
  await callApi(
    `/api/v1/admin/kbs/${encodeURIComponent(selectedKbId.value)}/documents/${encodeURIComponent(selectedDocId.value)}/chunks/${encodeURIComponent(chunkId)}/draft`,
    {
      method: 'PUT',
      headers: withHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ action: 'edit', text: editDraftForm.text }),
    },
  )
  editDraftForm.chunk_id = ''
  editDraftForm.text = ''
  await loadChunks()
}

async function clearDraft(chunkId) {
  await callApi(
    `/api/v1/admin/kbs/${encodeURIComponent(selectedKbId.value)}/documents/${encodeURIComponent(selectedDocId.value)}/chunks/${encodeURIComponent(chunkId)}/draft`,
    {
      method: 'DELETE',
      headers: withHeaders(),
    },
  )
  await loadChunks()
}

async function commitDrafts() {
  loading.commit = true
  try {
    await callApi(
      `/api/v1/admin/kbs/${encodeURIComponent(selectedKbId.value)}/documents/${encodeURIComponent(selectedDocId.value)}/chunks/commit`,
      {
        method: 'POST',
        headers: withHeaders(),
      },
    )
    await loadChunks()
    await loadDocs()
    pushNotice('success', '草稿已提交，后台已处理')
  } finally {
    loading.commit = false
  }
}

function openCommitConfirm() {
  if (pendingDraftSummary.value.items.length === 0) {
    pushNotice('info', '当前没有待提交的修改')
    return
  }
  ui.showCommitConfirmModal = true
}

async function confirmCommitDrafts() {
  ui.showCommitConfirmModal = false
  await commitDrafts()
}

function openChunks(docId, kbId = null) {
  if (!selectedKbId.value && kbId) {
    selectedKbId.value = kbId
  }
  if (!selectedKbId.value) return
  selectedDocId.value = docId
  const doc = docs.value.find((d) => d.doc_id === docId)
  if (doc) applyDocChunkConfigToRun(doc)
  ui.currentView = 'chunks'
  loadChunks()
}

function onDocCardMouseDown(doc, ev) {
  if (ev.button !== 0) return
  docCardPointer.value = {
    doc_id: doc.doc_id,
    kb_id: doc.kb_id || null,
    x: ev.clientX,
    y: ev.clientY,
  }
}

function onDocCardMouseLeave() {
  docCardPointer.value = null
}

function onDocCardMouseUp(doc, ev) {
  if (ev.button !== 0) return
  if (ev.target instanceof Element && ev.target.closest('button,select,input,label,textarea,a')) return
  const pointer = docCardPointer.value
  docCardPointer.value = null
  if (!pointer) return
  if (pointer.doc_id !== doc.doc_id || pointer.kb_id !== (doc.kb_id || null)) return
  const moved = Math.hypot((ev.clientX || 0) - pointer.x, (ev.clientY || 0) - pointer.y)
  if (moved > 6) return
  const selectedText = window.getSelection ? window.getSelection()?.toString().trim() : ''
  if (selectedText) return
  openChunks(doc.doc_id, doc.kb_id)
}

function openChunkDetail(chunk) {
  selectedChunk.value = chunk
  ui.chunkEditMode = false
  ui.showChunkDetailModal = true
}

function openChunkEditor(chunk) {
  if (chunk?.draft?.action === 'delete') return
  selectedChunk.value = chunk
  editDraftForm.chunk_id = chunk.chunk_id
  editDraftForm.text = chunkEffectiveText(chunk)
  ui.chunkEditMode = true
  ui.chunkDiffMode = false
  ui.showChunkDetailModal = true
}

function enterChunkEditMode() {
  if (!selectedChunk.value) return
  if (selectedChunk.value?.draft?.action === 'delete') return
  editDraftForm.chunk_id = selectedChunk.value.chunk_id
  editDraftForm.text = chunkEffectiveText(selectedChunk.value)
  ui.chunkEditMode = true
  ui.chunkDiffMode = false
}

function closeChunkDetailModal() {
  ui.showChunkDetailModal = false
  ui.chunkEditMode = false
  ui.chunkDiffMode = false
}

async function saveChunkDraftFromModal() {
  if (!selectedChunk.value) return
  if (!editDraftForm.chunk_id) {
    editDraftForm.chunk_id = selectedChunk.value.chunk_id
  }
  await setDraftEdit(editDraftForm.chunk_id)
  ui.chunkEditMode = false
  ui.chunkDiffMode = false
  ui.showChunkDetailModal = false
  selectedChunk.value = null
}

async function refreshAll() {
  await loadKbs()
  await loadDocs()
  await loadTasks()
  await loadUploadSettings()
  if (selectedDocId.value && ui.currentView === 'chunks') await loadChunks()
}

function chunkDraftCardClass(chunk) {
  const action = String(chunk?.draft?.action || '')
  return {
    'chunk-card-frozen': !!chunk?.frozen,
    'chunk-card-delete': action === 'delete',
    'chunk-card-edit': action === 'edit',
    'chunk-card-hit': isChunkMatched(chunk),
    'chunk-card-hit-active': isChunkActiveMatch(chunk),
    'scroll-enabled': scrollEnabledChunkId.value === chunk?.chunk_id,
  }
}

watch([chunkSearch, chunkSearchCaseSensitive, chunks], () => {
  activeChunkMatchIndex.value = 0
})

watch(activeChunkMatchId, (next) => {
  if (next) focusChunkCard(next)
})

watch(selectedKbId, () => {
  taskStatusMap.value = {}
  taskStageMap.value = {}
  taskChildrenMap.value = {}
  taskChildrenLoading.value = {}
  taskChildrenRefreshAt.value = {}
  batchDetailMap.value = {}
  batchDetailLoading.value = {}
  expandedTaskIds.value = {}
  selectedDocIds.value = []
  taskStatusReady.value = false
  if (isLoggedIn.value) connectTaskStream()
})

watch(selectedDoc, (doc) => {
  if (!doc) return
  if (ui.currentView !== 'chunks') return
  applyDocChunkConfigToRun(doc)
})

watch(
  [
    selectedKbId,
    kbSort,
    docSort,
    docStatusFilter,
    docTagFilter,
    kbSearch,
    docSearch,
    docTagSearch,
    () => ui.sidebarCollapsed,
    () => ui.showTaskDrawer,
  ],
  () => {
    saveUiPrefs()
  },
)

onMounted(() => {
  // Force login screen on every open to avoid stale token format conflicts.
  loadUiPrefs()
  token.value = ''
  role.value = ''
})

onBeforeUnmount(() => {
  if (docsRefreshTimer) {
    clearTimeout(docsRefreshTimer)
    docsRefreshTimer = null
  }
  if (kbsRefreshTimer) {
    clearTimeout(kbsRefreshTimer)
    kbsRefreshTimer = null
  }
  if (chunkHoverTimer) {
    clearTimeout(chunkHoverTimer)
    chunkHoverTimer = null
  }
  disconnectTaskStream()
})

function handleDrop(ev) {
  ev.preventDefault()
  dragActive.value = false
  const files = Array.from(ev.dataTransfer?.files || [])
  if (!files.length) return
  addUploadFiles(files)
}

function handleDragOver(ev) {
  ev.preventDefault()
  dragActive.value = true
}

function handleDragLeave(ev) {
  ev.preventDefault()
  dragActive.value = false
}

function handleFilePick(ev) {
  const files = Array.from(ev.target.files || [])
  if (!files.length) return
  addUploadFiles(files)
  ev.target.value = ''
}

function triggerFilePicker() {
  if (uploadInputRef.value) uploadInputRef.value.click()
}

function removeUploadFile(id) {
  uploadQueue.value = uploadQueue.value.filter((item) => item.id !== id)
}

function addUploadFiles(files) {
  const maxBytes = uploadLimitMb.value * 1024 * 1024
  for (const file of files) {
    const id = `${file.name}-${file.size}-${file.lastModified}`
    if (uploadQueue.value.some((item) => item.id === id)) continue
    if (file.size > maxBytes) {
      pushNotice('warn', `${file.name} 超过 ${uploadLimitMb.value}MB，已跳过`)
      continue
    }
    uploadQueue.value.push({
      id,
      name: file.name,
      size: file.size,
      file,
    })
  }
}
</script>

<template>
  <div v-if="!isLoggedIn" class="kb-login">
    <div class="kb-login-card fade-up">
      <div class="kb-login-left">
        <div class="badge">Knowledge Base</div>
        <h1>进入知识库</h1>
        <p class="muted">
          管理员登录后可进行多库管理、异步入库、切片与嵌入治理。
        </p>
        <ul class="kb-bullets">
          <li>上传、切片、嵌入均异步执行</li>
          <li>文档删除会联动清理任务与向量</li>
          <li>仅管理权限可访问本页</li>
        </ul>
      </div>
      <div class="kb-login-right" @keydown.enter.prevent="onLoginEnter">
        <label class="field">
          用户名
          <input v-model="loginForm.username" />
        </label>
        <label class="field">
          密码
          <input v-model="loginForm.password" type="password" />
        </label>
        <div class="kb-actions">
          <button class="btn primary full" @click="login" :disabled="loading.login">
            {{ loading.login ? '验证中...' : '登录知识库' }}
          </button>
        </div>
        <div class="kb-status" :class="{ error: !!loginError }">
          {{ loginError || '等待验证' }}
        </div>
      </div>
    </div>
  </div>

  <div v-else class="page admin-layout">
    <div class="notice-stack">
      <div v-for="notice in notices" :key="notice.id" class="notice" :class="`notice-${notice.type}`">
        {{ notice.message }}
      </div>
    </div>
    <aside class="card panel fade-up">
      <div class="panel-header">
        <div>
          <div class="section-title">知识库</div>
          <strong>库管理</strong>
        </div>
        <div class="stack-row">
          <button class="btn ghost" @click="logout">退出</button>
        </div>
      </div>

      <div class="panel-header">
        <input v-model="kbSearch" placeholder="Search libraries" />
        <select v-model="kbSort">
          <option value="id_asc">ID asc</option>
          <option value="id_desc">ID desc</option>
          <option value="name_asc">Name A-Z</option>
          <option value="name_desc">Name Z-A</option>
        </select>
      </div>

      <button class="btn primary library-create-btn" :disabled="loading.createKb" @click="ui.showCreateKbModal = true">
        {{ loading.createKb ? "Creating..." : "New Library" }}
      </button>

      <div class="list library-list" @click="kbMenuOpenId = ''">
        <div
          class="list-item kb-card kb-card-all"
          :class="{ active: !selectedKbId }"
          @click="selectedKbId = ''; selectedDocId = ''; ui.currentView = 'docs'; chunks = []; loadDocs(); loadTasks(); kbMenuOpenId = ''"
        >
          <div class="list-row kb-card-head">
            <strong class="list-title">All Libraries</strong>
          </div>
          <div class="meta kb-card-subtitle">Browse everything</div>
        </div>
        <div
          v-for="kb in visibleKbs"
          :key="kb.kb_id"
          class="list-item kb-card"
          :class="{ active: kb.kb_id === selectedKbId, 'menu-open': kbMenuOpenId === kb.kb_id, 'kb-frozen': !!kb.frozen }"
          @click="selectedKbId = kb.kb_id; loadDocs(); loadTasks(); kbMenuOpenId = ''"
        >
          <div class="list-row kb-card-head">
            <span v-if="kb.frozen" class="freeze-icon" title="已冻结">🧊</span>
            <strong class="list-title">{{ kb.name }}</strong>
            <button class="btn ghost kb-menu-btn" @click.stop="toggleKbMenu(kb.kb_id)" title="库菜单">⋯</button>
            <div v-if="kbMenuOpenId === kb.kb_id" class="kb-card-menu" @click.stop>
              <button class="btn ghost kb-card-menu-item" @click.stop="setKbFrozen(kb, !kb.frozen)">
                {{ kb.frozen ? '解冻库' : '冻结库' }}
              </button>
              <button
                class="btn ghost kb-card-menu-item"
                :disabled="Number(kb.stats?.document_count || 0) <= 0"
                :title="Number(kb.stats?.document_count || 0) <= 0 ? '库内无文档，无法总结' : ''"
                @click.stop="summarizeKb(kb)"
              >
                库总结
              </button>
              <button class="btn ghost kb-card-menu-item" @click.stop="openRenameKbModal(kb)">Rename</button>
              <button class="btn ghost btn-danger kb-card-menu-item" @click.stop="requestDeleteKb(kb)">Delete</button>
            </div>
          </div>
          <div class="tag-row kb-tag-row">
            <span class="badge badge-info">docs {{ kb.stats?.document_count || 0 }}</span>
            <span class="badge badge-info">chunks {{ kb.stats?.total_chunks || 0 }}</span>
          </div>
          <div class="meta kb-stats-line">
            <span>size: {{ formatBytes(kb.stats?.total_size_bytes) }}</span>
            <span>words: {{ kb.stats?.total_words || 0 }}</span>
          </div>
          <div v-if="kb.frozen" class="meta kb-frozen-tip">已冻结：库内文档/切片不会参与检索和排序</div>
          <div
            class="meta summary-line truncated kb-summary"
            :class="{ 'kb-summary-dirty': kb.summary_dirty }"
            :title="kb.summary || '-'"
          >
            summary: {{ kb.summary || "-" }}
          </div>
          <div v-if="Number(kb.stats?.document_count || 0) <= 0" class="meta kb-summary-empty">库内无文档，已清空总结</div>
          <div v-else-if="kb.summary_dirty" class="meta kb-summary-empty">文档或文档总结已变化，请重新库总结</div>
        </div>
      </div>
    </aside>

    <section class="card panel fade-up" style="animation-delay: 0.08s">
      <div class="panel-header">
        <div>
          <div class="section-title">文档</div>
          <strong v-if="ui.currentView === 'docs'">Collection</strong>
          <strong v-else>Chunks</strong>
        </div>
        <button class="btn ghost" @click="refreshAll">Refresh</button>
      </div>

      <template v-if="ui.currentView === 'docs'">
        <div class="panel-header panel-header-tight">
          <div class="stack-row doc-filter-row">
            <input class="doc-filter-search" v-model="docSearch" placeholder="Search docs" />
            <select class="doc-filter-sort" v-model="docStatusFilter" title="状态筛选">
              <option value="all">状态：全部</option>
              <option value="processing">状态：处理中</option>
              <option value="need_chunk">状态：需切片</option>
              <option value="need_embed">状态：需嵌入</option>
              <option value="need_summary">状态：需总结</option>
              <option value="ok">状态：已就绪</option>
              <option value="failed">状态：失败</option>
              <option value="cancelled">状态：已取消</option>
              <option value="active">冻结：仅未冻结</option>
              <option value="frozen">冻结：仅冻结</option>
            </select>
            <select class="doc-filter-sort" v-model="docTagFilter" title="标签筛选">
              <option value="all">标签：全部</option>
              <option value="has">标签：有标签</option>
              <option value="none">标签：无标签</option>
            </select>
            <input class="doc-filter-search doc-tag-search" v-model="docTagSearch" placeholder="Search tags" />
            <select class="doc-filter-sort" v-model="docSort">
              <option value="order_desc"># 序号 ↓</option>
              <option value="order_asc"># 序号 ↑</option>
              <option value="updated_desc">⏱ 更新时间 ↓</option>
              <option value="updated_asc">⏱ 更新时间 ↑</option>
              <option value="status">◎ 状态</option>
            </select>
            <div class="stack-row doc-filter-actions">
              <button class="btn soft" :disabled="!selectedKbId" @click="openUploadModal">Upload</button>
              <button class="btn btn-danger" @click="openDocDeleteCommitModal" :disabled="pendingDocDeleteCount === 0">
              提交修改（{{ pendingDocDeleteCount }}）
              </button>
            </div>
          </div>
        </div>
        <div class="panel-header panel-header-tight">
          <div class="stack-row batch-toolbar-row">
            <label class="batch-check-all">
              <input type="checkbox" :checked="allVisibleDocsChecked" @change="toggleSelectAllVisibleDocs" />
              <span>全选当前列表</span>
            </label>
            <span class="batch-selected-text">已选 {{ selectedDocCount }} 项</span>
            <button class="btn ghost" :disabled="selectedDocCount === 0" @click="clearSelectedDocs">清空选择</button>
            <button class="btn primary" :disabled="!canRunBatchAction" @click="openBatchActionModal">批量操作</button>
          </div>
        </div>

        <div class="list">
          <div v-if="!visibleDocs.length" class="list-item">
            No documents
          </div>
          <div
            v-for="doc in visibleDocs"
            :key="doc.doc_id"
            class="list-item doc-card"
            :class="{
              active: doc.doc_id === selectedDocId,
              'doc-card-frozen': !!doc.frozen,
              'pending-delete': !!pendingDocDeletes[docDeleteKey(doc.doc_id, doc.kb_id || selectedKbId)]
            }"
            @mousedown="onDocCardMouseDown(doc, $event)"
            @mouseup="onDocCardMouseUp(doc, $event)"
            @mouseleave="onDocCardMouseLeave"
          >
            <div class="doc-card-head">
              <div class="list-row doc-title-row">
                <label class="doc-select-check" @click.stop>
                  <input
                    type="checkbox"
                    :checked="selectedDocSet.has(doc.doc_id)"
                    @change.stop="toggleSelectDoc(doc.doc_id)"
                  />
                </label>
                <span class="doc-seq">#{{ docSequenceNo(doc) }}</span>
                <span
                  v-if="doc.frozen"
                  class="freeze-icon"
                  title="文档已冻结（不参与检索与排序）"
                >
                  🧊
                </span>
                <span
                  v-if="isDocTaskLocked(doc)"
                  class="doc-lock-indicator"
                  :title="`运行中：${stageLabel(docTaskState(doc)?.stage || String(doc?.status || '').toUpperCase())}`"
                >
                  🔒
                </span>
                <strong class="list-title doc-title-text" :title="doc.filename">{{ displayDocName(doc.filename) }}</strong>
                <span class="tag-row doc-title-tags" @click.stop>
                  <span v-if="!(doc.tags_json || []).length" class="badge badge-muted">无标签</span>
                  <span v-for="tag in (doc.tags_json || []).slice(0, 10)" :key="`${doc.doc_id}-head-tag-${tag}`" class="badge badge-info">
                    #{{ tag }}
                  </span>
                  <span v-if="(doc.tags_json || []).length > 10" class="badge badge-muted">+{{ (doc.tags_json || []).length - 10 }}</span>
                </span>
              </div>
              <div v-if="!selectedKbId" class="meta doc-card-subtle">library: {{ doc.kb_name || doc.kb_id }}</div>
            </div>
            <div class="tag-row doc-tag-row">
              <span class="badge" :class="docPrimaryBadge(doc).cls">
                {{ docPrimaryBadge(doc).text }}
              </span>
              <span class="badge" :class="chunkBadgeClass(doc)">
                chunks {{ doc.chunk_count || 0 }}
              </span>
              <span class="badge" :class="embeddedBadgeClass(doc)">
                embedded {{ doc.embedded_count || 0 }}
              </span>
            </div>
            <div
              class="meta summary-line truncated doc-summary-preview"
              :title="docSummaryPreview(doc)"
            >
              summary: {{ docSummaryPreview(doc) }}
            </div>
            <div v-if="doc.summary_dirty" class="meta doc-summary-dirty">内容已修改，未重新总结</div>
            <div class="doc-card-divider"></div>
            <div class="doc-actions">
              <button
                class="btn ghost"
                type="button"
                :disabled="isDocTaskLocked(doc)"
                @click.stop="chunkDocument(doc.doc_id, doc.kb_id)"
              >
                {{ chunkActionLabel(doc) }}
              </button>
              <select
                class="btn ghost doc-method-select"
                :value="getDocChunkMethod(doc)"
                :disabled="isDocTaskLocked(doc)"
                @click.stop
                @mousedown.stop
                @change.stop="setDocChunkMethod(doc, $event.target.value)"
              >
                <option value="fixed">Chunk: fixed</option>
                <option value="sentence">Chunk: sentence</option>
                <option value="paragraph">Chunk: paragraph</option>
                <option value="markdown">Chunk: markdown</option>
                <option value="delimiter">Chunk: delimiter</option>
              </select>
              <button
                v-if="canEmbedAgain(doc)"
                class="btn ghost"
                :class="{ 'is-loading': isDocEmbedding(doc), 'btn-progress-full': isDocEmbedding(doc) }"
                :style="isDocEmbedding(doc) ? docEmbeddingButtonStyle(doc) : null"
                :disabled="isDocTaskLocked(doc)"
                @click.stop="reindexDocument(doc.doc_id, doc.kb_id)"
                :title="isDocEmbedding(doc) ? docEmbeddingProgressTitle(doc) : 'Embed again'"
              >
                <span v-if="isDocEmbedding(doc)" class="mini-spinner"></span>
                <span>{{ docEmbeddingProgressLabel(doc) }}</span>
              </button>
              <button
                class="btn ghost"
                :disabled="isDocTaskLocked(doc)"
                @click.stop="openDocTagModal(doc)"
              >
                Tag
              </button>
              <button
                class="btn ghost"
                :disabled="isDocTaskLocked(doc)"
                @click.stop="setDocumentFrozen(doc, !doc.frozen)"
              >
                {{ doc.frozen ? '解冻' : '冻结' }}
              </button>
              <button
                class="btn ghost"
                :disabled="isDocTaskLocked(doc)"
                @click.stop="summarizeDocument(doc.doc_id, doc.kb_id)"
              >
                {{ summarizeActionLabel(doc) }}
              </button>
              <button
                class="btn ghost"
                :class="pendingDocDeletes[docDeleteKey(doc.doc_id, doc.kb_id || selectedKbId)] ? 'btn-danger' : 'btn-warn'"
                :disabled="isDocTaskLocked(doc)"
                @click.stop="togglePendingDeleteDoc(doc)"
                @keydown.enter.prevent
              >
                {{ pendingDocDeletes[docDeleteKey(doc.doc_id, doc.kb_id || selectedKbId)] ? '取消删除' : '预删除' }}
              </button>
            </div>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="panel-header panel-header-tight">
          <div class="stack-row">
            <button class="btn ghost" @click="ui.currentView = 'docs'">Back</button>
            <strong>{{ selectedDoc?.filename || selectedDocId }}</strong>
            <span class="chunk-param-line">
              <span class="chunk-param-title">当前切片参数</span>
              <span class="chunk-param-chip">
                <span class="k">Method</span>
                <span class="v">{{ selectedDoc ? getDocChunkMethod(selectedDoc) : chunkRunConfig.chunk_method }}</span>
              </span>
              <template v-if="!isDelimiterMethod(selectedDoc ? getDocChunkMethod(selectedDoc) : chunkRunConfig.chunk_method)">
                <span class="chunk-param-chip">
                  <span class="k">Size</span>
                  <span class="v">{{ chunkRunConfig.chunk_size }}</span>
                </span>
                <span class="chunk-param-chip">
                  <span class="k">Overlap</span>
                  <span class="v">{{ chunkRunConfig.chunk_overlap }}</span>
                </span>
              </template>
              <template v-else>
                <span class="chunk-param-chip">
                  <span class="k">Delimiter</span>
                  <span class="v">{{ chunkRunConfig.chunk_delimiter || '(empty)' }}</span>
                </span>
              </template>
              <span class="chunk-param-chip">
                <span class="k">Words</span>
                <span class="v">{{ Number(selectedDoc?.word_count || 0) > 0 ? Number(selectedDoc?.word_count || 0) : String(selectedDoc?.raw_text || '').split(/\s+/).filter(Boolean).length }}</span>
              </span>
            </span>
          </div>
          <div class="chunk-control-row">
            <div class="chunk-left-controls">
              <label class="inline-field">
                <span>Chunk Method</span>
                <select v-model="chunkRunConfig.chunk_method">
                  <option value="fixed">fixed</option>
                  <option value="sentence">sentence</option>
                  <option value="paragraph">paragraph</option>
                  <option value="markdown">markdown</option>
                  <option value="delimiter">delimiter</option>
                </select>
              </label>
              <label
                class="inline-field"
                title="每个 chunk 的目标长度（字符数），推荐 300~1000"
              >
                <span>Chunk Size</span>
                <input
                  v-model.number="chunkRunConfig.chunk_size"
                  type="number"
                  min="32"
                  max="4000"
                  step="1"
                  class="chunk-number"
                  placeholder="500"
                  :disabled="isDelimiterMethod(chunkRunConfig.chunk_method)"
                />
              </label>
              <label
                class="inline-field"
                title="相邻 chunk 的重叠长度（字符数），用于保留上下文，通常为 chunk_size 的 10%~20%"
              >
                <span>Chunk Overlap</span>
                <input
                  v-model.number="chunkRunConfig.chunk_overlap"
                  type="number"
                  min="0"
                  max="1000"
                  step="1"
                  class="chunk-number"
                  placeholder="50"
                  :disabled="isDelimiterMethod(chunkRunConfig.chunk_method)"
                />
              </label>
              <input
                v-if="isDelimiterMethod(chunkRunConfig.chunk_method)"
                v-model="chunkRunConfig.chunk_delimiter"
                class="chunk-delimiter"
                placeholder="delimiter (e.g. \n\n)"
              />
              <button class="btn ghost" @click="chunkDocument(selectedDocId, selectedKbId)">重新切片</button>
              <button class="btn primary" @click="openCommitConfirm" :disabled="loading.commit">提交修改</button>
            </div>
            <div class="chunk-search-inline">
              <input
                v-model="chunkSearch"
                class="chunk-search-input"
                placeholder="Search in chunks"
                @keydown.enter.prevent="onChunkSearchEnter"
              />
              <label class="chunk-search-toggle">
                <input v-model="chunkSearchCaseSensitive" type="checkbox" />
                Aa
              </label>
              <span class="meta">
                {{ chunkMatchCount ? `${activeChunkMatchIndex + 1}/${chunkMatchCount}` : '0/0' }}
              </span>
              <button class="btn ghost nav-arrow-btn" :disabled="!chunkMatchCount" title="上一个" @click="jumpChunkMatch(-1)">↑</button>
              <button class="btn ghost nav-arrow-btn" :disabled="!chunkMatchCount" title="下一个" @click="jumpChunkMatch(1)">↓</button>
            </div>
          </div>
        </div>

        <div class="chunk-grid chunk-grid-wide">
          <div
            v-for="ch in chunks"
            :key="ch.chunk_id"
            class="chunk-card"
            :class="chunkDraftCardClass(ch)"
            :data-chunk-id="ch.chunk_id"
            @mouseenter="enableChunkScrollAfterDelay(ch.chunk_id)"
            @mouseleave="disableChunkScroll(ch.chunk_id)"
            @click="openChunkDetail(ch)"
          >
            <div class="list-row chunk-card-head">
              <span v-if="ch.frozen" class="freeze-icon" title="Chunk 已冻结">🧊</span>
              <strong class="chunk-corner-badge">#{{ ch.chunk_index + 1 }}</strong>
              <span class="chunk-card-state">
                {{ ch.frozen ? '冻结' : (ch.draft?.action === 'delete' ? '预删除' : ch.draft?.action === 'edit' ? '已编辑' : '') }}
              </span>
            </div>
            <pre v-html="chunkPreviewHtml(ch)"></pre>
            <div class="chunk-card-actions">
              <button class="btn ghost" :disabled="ch.draft?.action === 'delete'" @click.stop="openChunkEditor(ch)">编辑草稿</button>
              <button class="btn ghost" @click.stop="setChunkFrozen(ch, !ch.frozen)">
                {{ ch.frozen ? '解冻' : '冻结' }}
              </button>
              <button
                class="btn ghost"
                @click.stop="ch.draft?.action === 'delete' ? clearDraft(ch.chunk_id) : setDraftDelete(ch.chunk_id)"
                @keydown.enter.prevent
              >
                {{ ch.draft?.action === 'delete' ? '取消删除' : '预删除' }}
              </button>
            </div>
          </div>
        </div>
      </template>
    </section>

    <button class="task-fab" :class="{ active: ui.showTaskDrawer }" @click="ui.showTaskDrawer = !ui.showTaskDrawer">
      <span v-if="runningTaskCount > 0" class="fab-spinner"></span>
      <span v-else class="fab-dot"></span>
      <span class="fab-count">{{ displayTasks.length }}</span>
    </button>

    <aside v-if="ui.showTaskDrawer" class="task-drawer">
      <div class="task-drawer-head">
        <strong>Tasks</strong>
        <div class="stack-row">
          <button class="btn ghost" @click="clearTasks" :disabled="loading.clearTasks || !tasks.length">
            {{ loading.clearTasks ? '清空中...' : '清空' }}
          </button>
          <button class="btn ghost" @click="ui.showTaskDrawer = false">收起</button>
        </div>
      </div>
      <div class="task-drawer-list">
        <div v-if="!displayTasks.length" class="task-item status-unknown">暂无任务</div>
        <article v-for="task in displayTasks" :key="task.task_id" class="task-item" :class="taskRowClass(task)">
          <div class="task-line">
            <span class="task-icon-slot">
              <span v-if="isRunningTask({ status: effectiveTaskStatus(task) })" class="mini-spinner"></span>
              <span
                v-else
                class="task-icon-mark"
                :class="`icon-${String(effectiveTaskStatus(task) || '').toUpperCase()}`"
              >
                {{ String(effectiveTaskStatus(task) || '').toUpperCase() === 'SUCCEEDED'
                  ? '✓'
                  : String(effectiveTaskStatus(task) || '').toUpperCase() === 'FAILED'
                    ? 'x'
                    : String(effectiveTaskStatus(task) || '').toUpperCase() === 'CANCELLED'
                      ? '-'
                      : '○' }}
              </span>
            </span>
            <span class="task-name" :title="taskDisplayName(task)">{{ taskDisplayName(task) }}</span>
            <span class="task-status">{{ taskStatusText(task) }}</span>
            <span class="task-time">{{ formatTaskTime(task.updated_at || task.created_at) }}</span>
            <button
              class="btn ghost task-cancel-btn"
              :disabled="!(canCancelTask(task) || canRetryTask(task))"
              :title="retryLockedTaskIds[String(task.task_id || '')] ? '已提交重试' : (canRetryTask(task) ? '重试任务' : '取消任务')"
              @click.stop="canRetryTask(task) ? retryTask(task) : cancelTask(task.task_id)"
            >
              {{ retryLockedTaskIds[String(task.task_id || '')] ? '已重试' : (canRetryTask(task) ? '重试' : '取消') }}
            </button>
            <button
              class="btn ghost task-expand-btn"
              :title="expandedTaskIds[task.task_id] ? '收起' : '展开'"
              @click="toggleTaskExpand(task)"
            >
              <span class="task-chevron" :class="{ expanded: !!expandedTaskIds[task.task_id] }">▾</span>
            </button>
          </div>
          <div v-if="expandedTaskIds[task.task_id]" class="task-children">
            <template v-if="task.batch_is_parent && task.batch_id">
              <div v-if="batchDetailLoading[task.batch_id]" class="task-child child-running">加载中...</div>
              <div v-else class="task-child batch-summary">
                <span class="task-child-label">
                  成功 {{ batchDetailMap[task.batch_id]?.item?.succeeded_items || 0 }} / 失败 {{ batchDetailMap[task.batch_id]?.item?.failed_items || 0 }} / 运行中 {{ batchDetailMap[task.batch_id]?.item?.running_items || 0 }}
                </span>
                <button
                  class="btn ghost btn-xs"
                  :disabled="retryLockedBatchIds[String(task.batch_id || '')] || Number(batchDetailMap[task.batch_id]?.item?.failed_items || 0) <= 0 || isRunningTask({ status: effectiveTaskStatus(task) })"
                  @click.stop="retryBatchFailed(task.batch_id)"
                >
                  {{ retryLockedBatchIds[String(task.batch_id || '')] ? '已重试' : '重试失败项' }}
                </button>
              </div>
              <div v-if="!(batchDetailMap[task.batch_id]?.items || []).length" class="task-child child-unknown">暂无批次子任务</div>
              <div
                v-for="childTask in (batchDetailMap[task.batch_id]?.items || [])"
                :key="childTask.task_id"
                class="task-child"
                :class="taskStatusClass(effectiveTaskStatus(childTask))"
              >
                <span class="task-child-label">{{ taskDisplayName(childTask) }} - {{ taskStatusText(childTask) }}</span>
                <span class="task-child-time">{{ formatTaskTime(childTask.updated_at || childTask.created_at) }}</span>
              </div>
            </template>
            <template v-else>
              <div v-if="taskChildrenLoading[task.task_id]" class="task-child child-running">加载中...</div>
              <div v-else-if="!(taskChildrenMap[task.task_id] || []).length" class="task-child child-unknown">暂无子任务记录</div>
              <div v-for="child in (taskChildrenMap[task.task_id] || [])" :key="child.key" class="task-child" :class="child.cls">
                <span class="task-child-label">
                  {{ child.label }}<span v-if="child.progressText"> {{ child.progressText }}</span>
                </span>
                <span class="task-child-time">{{ formatTaskTime(child.ts) }}</span>
              </div>
            </template>
          </div>
        </article>
      </div>
    </aside>

    <div v-if="ui.showCreateKbModal" class="modal" @mousedown.self="ui.showCreateKbModal = false">
      <div class="modal-card" @keydown.enter.prevent="onCreateKbEnter">
        <div class="panel-header">
          <strong>创建知识库</strong>
        </div>
        <label class="field">
          名称
          <input v-model="kbForm.name" placeholder="知识库名称" />
        </label>
        <div class="stack-row actions-right">
          <button class="btn primary" @click="createKb" :disabled="loading.createKb">创建</button>
          <button class="btn ghost" @click="ui.showCreateKbModal = false">取消</button>
        </div>
      </div>
    </div>

    <div v-if="ui.showUploadModal" class="modal" @mousedown.self="ui.showUploadModal = false">
      <div class="modal-card upload">
        <div class="panel-header">
          <strong>上传文档</strong>
          <span class="meta">单文件上限 {{ uploadLimitMb }}MB</span>
        </div>
        <div class="upload-config-grid">
          <label class="field field-tight upload-field upload-field-mode">
            处理模式
            <select v-model="uploadProcessMode">
              <option value="UPLOAD_CLEAN_CHUNK_EMBED">上传并嵌入（默认）</option>
              <option value="UPLOAD_AND_CLEAN">仅上传并清洗</option>
              <option value="UPLOAD_ONLY">仅上传（不处理）</option>
            </select>
          </label>
          <label class="field field-tight upload-field">
            Chunk 方法
            <select v-model="uploadChunkConfig.chunk_method" :disabled="!uploadWillChunk">
              <option value="fixed">fixed</option>
              <option value="sentence">sentence（默认）</option>
              <option value="paragraph">paragraph</option>
              <option value="markdown">markdown</option>
              <option value="delimiter">delimiter（按分隔符）</option>
            </select>
          </label>
          <label class="field field-tight upload-field upload-field-number">
            大小
            <input
              v-model.number="uploadChunkConfig.chunk_size"
              type="number"
              min="32"
              max="4000"
              step="1"
              :disabled="!uploadWillChunk || isDelimiterMethod(uploadChunkConfig.chunk_method)"
            />
          </label>
          <label class="field field-tight upload-field upload-field-number">
            Overlap
            <input
              v-model.number="uploadChunkConfig.chunk_overlap"
              type="number"
              min="0"
              max="1000"
              step="1"
              :disabled="!uploadWillChunk || isDelimiterMethod(uploadChunkConfig.chunk_method)"
            />
          </label>
        </div>
        <label v-if="isDelimiterMethod(uploadChunkConfig.chunk_method)" class="field field-tight upload-field upload-field-delimiter">
          分隔符（支持 \n / \t）
          <input
            v-model="uploadChunkConfig.chunk_delimiter"
            placeholder="例如：\n\n 或 ###"
            :disabled="!uploadWillChunk"
          />
        </label>
        <div
          class="dropzone"
          :class="{ active: dragActive }"
          @click="triggerFilePicker"
          @drop="handleDrop"
          @dragover="handleDragOver"
          @dragleave="handleDragLeave"
        >
          <p>拖拽文件到此处，或点击选择文件</p>
          <input
            ref="uploadInputRef"
            type="file"
            multiple
            accept=".txt,.md,.markdown,.html,.htm,.pdf,.docx"
            @change="handleFilePick"
            style="display: none"
          />
        </div>

        <div class="upload-list">
          <div v-if="!uploadQueue.length" class="list-item muted">暂无待上传文件</div>
          <div v-for="item in uploadQueue" :key="item.id" class="list-item upload-item">
            <div>
              <strong>{{ item.name }}</strong>
              <div class="meta">大小: {{ formatFileSize(item.size) }}</div>
            </div>
            <button class="btn ghost" @click="removeUploadFile(item.id)" :disabled="loading.upload">移除</button>
          </div>
        </div>

        <div class="stack-row actions-right">
          <button class="btn primary" @click="uploadDocument" :disabled="loading.upload || !uploadQueue.length">提交入库</button>
          <button class="btn ghost" @click="ui.showUploadModal = false">取消</button>
        </div>
      </div>
    </div>

    <div v-if="ui.showBatchModal" class="modal" @mousedown.self="ui.showBatchModal = false">
      <div class="modal-card" @keydown.enter.prevent="runBatchAction">
        <div class="panel-header">
          <strong>批量操作（{{ selectedDocCount }} 项）</strong>
        </div>
        <div class="meta">
          当前选中 {{ batchEligibility.selectedCount }} 条文档，
          可直接执行 {{ batchEligibility.eligibleCount }} 条
          <template v-if="batchEligibility.excludedCount > 0">
            ，{{ batchEligibility.excludedCount }} 条需额外操作
            <button class="btn-link-inline" @click="batchShowPrereqList = !batchShowPrereqList">
              {{ batchShowPrereqList ? '收起' : '查看' }}
            </button>
          </template>
        </div>
        <div v-if="batchShowPrereqList && batchEligibility.excludedCount > 0" class="batch-prereq-list">
          <div v-for="item in batchEligibility.excluded" :key="item.doc_id" class="batch-prereq-item">
            <span class="name" :title="item.filename">{{ item.filename }}</span>
            <span class="reason">{{ item.reason }}</span>
          </div>
        </div>
        <div class="stack-col">
          <label class="field field-tight">
            操作类型
            <select v-model="batchActionForm.operation">
              <option value="chunk">批量重切片</option>
              <option value="embed">批量重嵌入</option>
              <option value="summarize">批量重总结</option>
              <option value="move">批量移动文档</option>
              <option value="tag">批量打标签</option>
              <option value="delete">批量删除</option>
            </select>
          </label>
          <template v-if="batchActionForm.operation === 'chunk'">
            <label class="field field-tight">
              Chunk 方法
              <select v-model="batchActionForm.chunk_method">
                <option value="fixed">fixed</option>
                <option value="sentence">sentence（默认）</option>
                <option value="paragraph">paragraph</option>
                <option value="markdown">markdown</option>
                <option value="delimiter">delimiter（按分隔符）</option>
              </select>
            </label>
            <label class="field field-tight">
              Chunk 大小
              <input
                v-model.number="batchActionForm.chunk_size"
                type="number"
                min="32"
                max="4000"
                step="1"
                :disabled="isDelimiterMethod(batchActionForm.chunk_method)"
              />
            </label>
            <label class="field field-tight">
              Chunk Overlap
              <input
                v-model.number="batchActionForm.chunk_overlap"
                type="number"
                min="0"
                max="1000"
                step="1"
                :disabled="isDelimiterMethod(batchActionForm.chunk_method)"
              />
            </label>
            <label v-if="isDelimiterMethod(batchActionForm.chunk_method)" class="field field-tight">
              分隔符（支持 \n / \t）
              <input
                v-model="batchActionForm.chunk_delimiter"
                placeholder="例如：\n\n 或 ###"
              />
            </label>
          </template>
          <label v-if="batchActionForm.operation === 'move'" class="field field-tight">
            目标知识库
            <select v-model="batchActionForm.target_kb_id">
              <option value="">选择目标知识库</option>
              <option v-for="kb in kbs" :key="kb.kb_id" :value="kb.kb_id" :disabled="kb.kb_id === selectedKbId">{{ kb.name }}</option>
            </select>
          </label>
          <label v-if="batchActionForm.operation === 'tag'" class="field field-tight">
            标签（逗号分隔）
            <input v-model="batchActionForm.tags_text" placeholder="例如：教材, 已审核" />
          </label>
        </div>
        <div class="stack-row actions-right">
          <button class="btn primary" :disabled="!canRunBatchAction" @click="runBatchAction">执行批量操作</button>
          <button class="btn ghost" @click="ui.showBatchModal = false">取消</button>
        </div>
      </div>
    </div>

    <div v-if="ui.showDocTagModal" class="modal" @mousedown.self="closeDocTagModal">
      <div class="modal-card doc-tag-modal" @keydown.enter.prevent="addDocTagChip">
        <div class="panel-header">
          <strong>文档标签</strong>
        </div>
        <div class="meta truncated" :title="docTagModal.filename">{{ docTagModal.filename }}</div>
        <div class="doc-tag-edit-row">
          <input
            v-model="docTagInput"
            class="doc-tag-modal-input"
            placeholder="输入标签后点击 +"
            maxlength="40"
          />
          <button class="btn ghost icon-btn" @click="addDocTagChip" :disabled="!docTagInput.trim()">+</button>
        </div>
        <div class="doc-tag-chip-board">
          <div v-if="!(docTagModal.tags || []).length" class="meta">暂无标签</div>
          <div
            v-for="(tag, idx) in (docTagModal.tags || [])"
            :key="`doc-tag-${idx}-${tag}`"
            class="doc-tag-chip"
            :title="tag"
          >
            <span class="text">#{{ tag }}</span>
            <button class="remove" @click="removeDocTagChip(idx)">x</button>
          </div>
        </div>
        <div class="meta">最多 10 个标签</div>
        <div class="stack-row actions-right">
          <button class="btn primary" @click="saveDocTagModal" :disabled="!(docTagModal.tags || []).length">保存标签</button>
          <button class="btn ghost" @click="closeDocTagModal">取消</button>
        </div>
      </div>
    </div>

    <div v-if="ui.showRenameKbModal" class="modal" @mousedown.self="ui.showRenameKbModal = false">
      <div class="modal-card" @keydown.enter.prevent="onRenameKbEnter">
        <div class="panel-header">
          <strong>重命名知识库</strong>
        </div>
        <label class="field">
          名称
          <input v-model="renameKbForm.name" placeholder="知识库名称" />
        </label>
        <div class="stack-row actions-right">
          <button class="btn primary" @click="renameKb" :disabled="loading.renameKb || !renameKbForm.name.trim()">
            保存
          </button>
          <button class="btn ghost" @click="ui.showRenameKbModal = false">取消</button>
        </div>
      </div>
    </div>

    <div v-if="ui.showCommitConfirmModal" class="modal" @mousedown.self="ui.showCommitConfirmModal = false">
      <div class="modal-card" @keydown.enter.prevent>
        <div class="panel-header">
          <strong>确认提交修改</strong>
        </div>
        <p>预删除: {{ pendingDraftSummary.deleteCount }}，预编辑重嵌入: {{ pendingDraftSummary.editCount }}</p>
        <ul>
          <li v-for="item in pendingDraftSummary.items" :key="item.chunk_id">
            {{ item.chunk_id.slice(0, 10) }} - {{ item.action }} - {{ item.preview }}
          </li>
        </ul>
        <div class="stack-row actions-right">
          <button class="btn primary" @click="confirmCommitDrafts">确认提交</button>
          <button class="btn ghost" @click="ui.showCommitConfirmModal = false">取消</button>
        </div>
      </div>
    </div>

    <div v-if="ui.showDocDeleteCommitModal" class="modal" @mousedown.self="ui.showDocDeleteCommitModal = false">
      <div class="modal-card" @keydown.enter.prevent>
        <div class="panel-header">
          <strong>确认提交文档删除</strong>
        </div>
        <p>以下文档将在提交后删除：</p>
        <ul>
          <li v-for="item in pendingDocDeleteItems" :key="item.key">
            {{ item.filename }}（{{ item.doc_id.slice(0, 10) }}）
          </li>
        </ul>
        <div class="stack-row actions-right">
          <button class="btn btn-danger" @click="commitPendingDocDeletes" :disabled="loading.commitDocDelete">
            {{ loading.commitDocDelete ? '删除中...' : '确认提交' }}
          </button>
          <button class="btn ghost" @click="ui.showDocDeleteCommitModal = false">取消</button>
        </div>
      </div>
    </div>

    <div v-if="ui.showDeleteKbConfirmModal" class="modal" @mousedown.self="ui.showDeleteKbConfirmModal = false">
      <div class="modal-card" @keydown.enter.prevent>
        <div class="panel-header">
          <strong>确认删除知识库</strong>
        </div>
        <p>即将删除：{{ deleteKbForm.name }}</p>
        <p class="meta">该操作会删除库下文档、切片、向量和相关任务记录。</p>
        <div class="stack-row actions-right">
          <button class="btn btn-danger" @click="confirmDeleteKb" :disabled="loading.deleteKb">
            {{ loading.deleteKb ? '删除中...' : '确认删除' }}
          </button>
          <button class="btn ghost" @click="ui.showDeleteKbConfirmModal = false">取消</button>
        </div>
      </div>
    </div>

    <div v-if="ui.showChunkDetailModal && selectedChunk" class="modal" @mousedown.self="closeChunkDetailModal">
      <div class="modal-card" :class="{ 'chunk-detail-modal-wide': ui.chunkEditMode && ui.chunkDiffMode }" @keydown.enter.prevent>
        <div class="panel-header">
          <span v-if="selectedChunk?.frozen" class="freeze-icon" title="Chunk 已冻结">🧊</span>
          <strong>Chunk #{{ Number(selectedChunk.chunk_index || 0) + 1 }} 详情</strong>
        </div>
        <template v-if="ui.chunkEditMode">
          <div v-if="!ui.chunkDiffMode" class="chunk-edit-single">
            <textarea
              class="chunk-detail-text"
              v-model="editDraftForm.text"
              rows="16"
            />
          </div>
          <div v-else class="chunk-diff-panel">
            <div class="chunk-diff-title">对比模式</div>
            <div class="chunk-diff-grid split-edit">
              <div class="chunk-diff-col">
                <div class="chunk-diff-label">原文</div>
                <div class="chunk-diff-box" v-html="chunkDiffOriginalHtml()"></div>
              </div>
              <div class="chunk-diff-col">
                <div class="chunk-diff-label">草稿（可编辑）</div>
                <textarea
                  class="chunk-detail-text chunk-detail-text-split"
                  v-model="editDraftForm.text"
                  rows="16"
                />
              </div>
            </div>
          </div>
        </template>
        <div
          v-else
          class="chunk-detail-preview"
          v-html="chunkDetailHtml(selectedChunk)"
        ></div>
        <div class="stack-row actions-right">
          <button
            v-if="!ui.chunkEditMode"
            class="btn ghost"
            :disabled="selectedChunk?.draft?.action === 'delete'"
            @click="enterChunkEditMode"
          >
            编辑草稿
          </button>
          <button
            v-if="!ui.chunkEditMode"
            class="btn ghost"
            @click="setChunkFrozen(selectedChunk, !selectedChunk?.frozen)"
          >
            {{ selectedChunk?.frozen ? '解冻' : '冻结' }}
          </button>
          <button
            v-if="!ui.chunkEditMode"
            class="btn ghost"
            @click="selectedChunk?.draft?.action === 'delete' ? clearDraft(selectedChunk.chunk_id) : setDraftDelete(selectedChunk.chunk_id)"
          >
            {{ selectedChunk?.draft?.action === 'delete' ? '取消删除' : '预删除' }}
          </button>
          <button
            v-if="ui.chunkEditMode"
            class="btn ghost"
            @click="ui.chunkDiffMode = !ui.chunkDiffMode"
          >
            {{ ui.chunkDiffMode ? '关闭对比' : '对比' }}
          </button>
          <button
            v-if="ui.chunkEditMode"
            class="btn primary"
            :disabled="!editDraftForm.text.trim()"
            @click="saveChunkDraftFromModal"
          >
            保存草稿
          </button>
          <button class="btn ghost" @click="closeChunkDetailModal">取消</button>
        </div>
      </div>
    </div>

  </div>
</template>
