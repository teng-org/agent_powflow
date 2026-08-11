export function useKbAdmin({
  fetchJson,
  isControlLoggedIn,
  isExecutionLoggedIn,
  kbState,
  ui,
  getKbId,
  getKbName,
  getDocId,
  getDocName,
  getChunkId,
  getTaskId,
  onTaskRefresh,
}) {
  function numberOrDefault(value, fallback) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : fallback
  }

  async function loadControlTenants() {
    if (!isControlLoggedIn.value) return
    try {
      const data = await fetchJson('control', '/api/v1/admin/tenants')
      kbState.tenants = data?.items || []
      if (!kbState.bindTenantId && kbState.tenants.length) {
        kbState.bindTenantId = kbState.tenants[0].tenantId
      }
    } catch {
      kbState.tenants = []
    }
  }

  async function loadKbs() {
    if (!isControlLoggedIn.value) return
    kbState.loading = true
    kbState.error = ''
    try {
      const data = await fetchJson('control', '/api/v1/admin/kbs')
      kbState.items = data?.items || []
      if (!kbState.selectedKbId && kbState.items.length) {
        await selectKb(kbState.items[0])
      }
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function createKb() {
    const name = kbState.createName.trim()
    if (!name || !isControlLoggedIn.value) return
    kbState.loading = true
    kbState.error = ''
    try {
      await fetchJson('control', '/api/v1/admin/kbs', {
        method: 'POST',
        body: { name },
      })
      kbState.createName = ''
      await loadKbs()
      ui.message = `知识库已创建：${name}`
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function renameKb(item) {
    const kbId = getKbId(item)
    if (!kbId) return
    const currentName = getKbName(item)
    const next = window.prompt('输入新的知识库名称', currentName)
    if (!next || next.trim() === currentName) return
    kbState.loading = true
    kbState.error = ''
    try {
      await fetchJson('control', `/api/v1/admin/kbs/${encodeURIComponent(kbId)}`, {
        method: 'PATCH',
        body: { name: next.trim() },
      })
      await loadKbs()
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function deleteKb(item) {
    const kbId = getKbId(item)
    if (!kbId) return
    if (!window.confirm(`确认删除知识库 ${getKbName(item)} ?`)) return
    kbState.loading = true
    kbState.error = ''
    try {
      await fetchJson('control', `/api/v1/admin/kbs/${encodeURIComponent(kbId)}`, { method: 'DELETE' })
      if (kbState.selectedKbId === kbId) {
        kbState.selectedKbId = ''
        kbState.docs = []
        kbState.chunks = []
        kbState.bindings = []
      }
      await loadKbs()
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function summarizeKb() {
    if (!kbState.selectedKbId) return
    kbState.loading = true
    kbState.error = ''
    try {
      await fetchJson('control', `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/summarize`, {
        method: 'POST',
        body: {},
      })
      ui.message = '已触发库总结任务'
      await onTaskRefresh()
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function loadKbBindings() {
    if (!kbState.selectedKbId) return
    try {
      const data = await fetchJson(
        'control',
        `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/bindings`,
      )
      kbState.bindings = data?.items || data?.bindings || []
    } catch {
      kbState.bindings = []
    }
  }

  async function bindKbTenant() {
    if (!kbState.selectedKbId || !kbState.bindTenantId) return
    kbState.loading = true
    kbState.error = ''
    try {
      await fetchJson('control', `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/bindings`, {
        method: 'PUT',
        body: { target_tenant_id: kbState.bindTenantId },
      })
      await loadKbBindings()
      ui.message = '知识库租户绑定已更新'
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function unbindKbTenant(targetTenantId) {
    if (!kbState.selectedKbId || !targetTenantId) return
    kbState.loading = true
    kbState.error = ''
    try {
      await fetchJson(
        'control',
        `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/bindings/${encodeURIComponent(targetTenantId)}`,
        { method: 'DELETE' },
      )
      await loadKbBindings()
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function selectKb(item) {
    const kbId = getKbId(item)
    if (!kbId) return
    kbState.selectedKbId = kbId
    kbState.selectedKbName = getKbName(item)
    kbState.selectedDocId = ''
    kbState.selectedDocName = ''
    kbState.selectedDocIds = []
    kbState.selectedBatchId = ''
    kbState.batchDetail = null
    kbState.chunks = []
    await Promise.all([loadDocs(), loadKbTasks(), loadKbBindings()])
  }

  async function loadDocs() {
    if (!kbState.selectedKbId) return
    kbState.loading = true
    kbState.error = ''
    try {
      const data = await fetchJson('execution', `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/documents`)
      kbState.docs = data?.items || []
      const visibleIds = new Set(kbState.docs.map((item) => getDocId(item)).filter(Boolean))
      kbState.selectedDocIds = (kbState.selectedDocIds || []).filter((docId) => visibleIds.has(docId))
      if (!kbState.selectedDocId && kbState.docs.length) {
        await selectDoc(kbState.docs[0])
      }
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function loadKbTasks() {
    if (!kbState.selectedKbId) return
    try {
      const data = await fetchJson('control', `/api/v1/admin/tasks?kb_id=${encodeURIComponent(kbState.selectedKbId)}&limit=50`)
      kbState.tasks = data?.items || []
      if (!kbState.selectedTaskId && kbState.tasks.length) {
        await selectKbTask(kbState.tasks[0])
      }
    } catch {
      kbState.tasks = []
    }
  }

  async function selectKbTask(item) {
    kbState.selectedTaskId = getTaskId(item)
    const batchId = String(item?.batch_id || '')
    kbState.selectedBatchId = item?.batch_is_parent ? batchId : ''
    await Promise.all([loadKbTaskEvents(), kbState.selectedBatchId ? loadBatchDetail() : Promise.resolve()])
    if (!kbState.selectedBatchId) kbState.batchDetail = null
  }

  async function loadBatchDetail() {
    if (!kbState.selectedBatchId) return
    try {
      kbState.batchDetail = await fetchJson(
        'control',
        `/api/v1/admin/tasks/batches/${encodeURIComponent(kbState.selectedBatchId)}`,
      )
    } catch (error) {
      kbState.batchDetail = null
      kbState.error = error instanceof Error ? error.message : String(error)
    }
  }

  async function retryBatchFailed() {
    if (!kbState.selectedBatchId) return
    kbState.loading = true
    kbState.error = ''
    try {
      const result = await fetchJson(
        'control',
        `/api/v1/admin/tasks/batches/${encodeURIComponent(kbState.selectedBatchId)}/retry-failed`,
        { method: 'POST', body: {} },
      )
      ui.message = result?.accepted_count
        ? `已创建重试批次，共 ${result.accepted_count} 项`
        : '当前批次没有可重试的失败项'
      await Promise.all([loadKbTasks(), onTaskRefresh()])
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  function toggleDocSelection(docId, selected) {
    const id = String(docId || '')
    if (!id) return
    const next = new Set(kbState.selectedDocIds || [])
    if (selected) next.add(id)
    else next.delete(id)
    kbState.selectedDocIds = [...next]
  }

  function toggleAllDocs(selected) {
    kbState.selectedDocIds = selected
      ? kbState.docs.map((item) => getDocId(item)).filter(Boolean)
      : []
  }

  async function submitDocumentBatch() {
    const docIds = [...new Set(kbState.selectedDocIds || [])]
    if (!kbState.selectedKbId || !docIds.length) return
    const operation = String(kbState.batch.operation || '')
    if (operation === 'delete' && !window.confirm(`确认批量删除选中的 ${docIds.length} 个文档？`)) return
    const tags = String(kbState.batch.tags_csv || '').split(',').map((value) => value.trim()).filter(Boolean)
    const body = {
      operation,
      doc_ids: docIds,
      batch_name: kbState.batch.batch_name || undefined,
    }
    if (operation === 'chunk' || operation === 'embed') {
      Object.assign(body, {
        chunk_method: kbState.chunkRun.chunk_method,
        chunk_size: numberOrDefault(kbState.chunkRun.chunk_size, 500),
        chunk_overlap: numberOrDefault(kbState.chunkRun.chunk_overlap, 50),
        chunk_delimiter: kbState.chunkRun.chunk_delimiter || undefined,
      })
    }
    if (operation === 'move') body.target_kb_id = kbState.batch.target_kb_id
    if (operation === 'tag') Object.assign(body, { tags, mode: kbState.batch.mode })
    kbState.loading = true
    kbState.error = ''
    try {
      const result = await fetchJson(
        'control',
        `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/documents/batch`,
        { method: 'POST', body },
      )
      kbState.selectedDocIds = []
      kbState.selectedBatchId = result?.batch_id || ''
      ui.message = `批处理已提交：${result?.accepted_count || 0} 项`
      await Promise.all([loadKbTasks(), onTaskRefresh()])
      if (kbState.selectedBatchId) await loadBatchDetail()
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function loadKbTaskEvents() {
    if (!kbState.selectedTaskId) return
    try {
      const data = await fetchJson('control', `/api/v1/admin/tasks/${encodeURIComponent(kbState.selectedTaskId)}/events?limit=100`)
      kbState.taskEvents = data?.items || []
    } catch {
      kbState.taskEvents = []
    }
  }

  async function ingestDocText() {
    if (!kbState.selectedKbId) return
    kbState.loading = true
    kbState.error = ''
    try {
      const payload = {
        doc_id: kbState.ingest.doc_id || undefined,
        filename: kbState.ingest.filename || undefined,
        content: kbState.ingest.content,
        chunk_method: kbState.ingest.chunk_method,
        chunk_size: Number(kbState.ingest.chunk_size) || 500,
        chunk_overlap: numberOrDefault(kbState.ingest.chunk_overlap, 50),
      }
      await fetchJson('control', `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/documents/ingest`, {
        method: 'POST',
        body: payload,
      })
      ui.message = '文本入库已提交'
      await Promise.all([loadDocs(), onTaskRefresh()])
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  function onUploadFileChange(event) {
    const files = event?.target?.files
    kbState.upload.files = files ? Array.from(files) : []
  }

  async function uploadDocFile() {
    if (!kbState.selectedKbId || !kbState.upload.files?.length) return
    kbState.loading = true
    kbState.error = ''
    try {
      const form = new FormData()
      for (const file of kbState.upload.files) form.append('file', file)
      form.set('process_mode', kbState.upload.processMode)
      form.set('chunk_method', kbState.chunkRun.chunk_method)
      form.set('chunk_size', String(Number(kbState.chunkRun.chunk_size) || 500))
      form.set('chunk_overlap', String(numberOrDefault(kbState.chunkRun.chunk_overlap, 50)))
      if (kbState.chunkRun.chunk_delimiter) form.set('chunk_delimiter', kbState.chunkRun.chunk_delimiter)
      form.set(
        'auto_summary',
        kbState.upload.processMode === 'UPLOAD_CLEAN_CHUNK_EMBED' && kbState.upload.autoSummary ? 'true' : 'false',
      )
      const result = await fetchJson('control', `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/documents/upload`, {
        method: 'POST',
        body: form,
        headers: {},
      })
      kbState.upload.files = []
      kbState.upload.inputVersion = Number(kbState.upload.inputVersion || 0) + 1
      const accepted = Number(result?.accepted_count || 0)
      const skipped = Number(result?.skipped_count || 0)
      ui.message = `文件上传完成：接受 ${accepted} 个${skipped ? `，跳过 ${skipped} 个` : ''}`
      await Promise.all([loadDocs(), onTaskRefresh()])
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function selectDoc(item) {
    kbState.selectedDocId = getDocId(item)
    kbState.selectedDocName = getDocName(item)
    kbState.chunkDraftText = {}
    await loadChunks()
  }

  async function loadChunks() {
    if (!kbState.selectedKbId || !kbState.selectedDocId) return
    kbState.loading = true
    kbState.error = ''
    try {
      const data = await fetchJson(
        'execution',
        `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/documents/${encodeURIComponent(kbState.selectedDocId)}/chunks`,
      )
      kbState.chunks = data?.items || []
      const draftText = {}
      for (const chunk of kbState.chunks) {
        draftText[getChunkId(chunk)] = chunk?.draft_text || chunk?.draftText || ''
      }
      kbState.chunkDraftText = draftText
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
      kbState.chunks = []
      kbState.chunkDraftText = {}
    } finally {
      kbState.loading = false
    }
  }

  async function saveChunkDraft(chunk) {
    if (!kbState.selectedKbId || !kbState.selectedDocId) return
    const chunkId = getChunkId(chunk)
    const text = String(kbState.chunkDraftText[chunkId] || '')
    kbState.loading = true
    kbState.error = ''
    try {
      await fetchJson(
        'control',
        `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/documents/${encodeURIComponent(kbState.selectedDocId)}/chunks/${encodeURIComponent(chunkId)}/draft`,
        {
          method: 'PUT',
          body: { text },
        },
      )
      ui.message = `草稿已保存：${chunkId}`
      await loadChunks()
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function clearChunkDraft(chunk) {
    if (!kbState.selectedKbId || !kbState.selectedDocId) return
    const chunkId = getChunkId(chunk)
    kbState.loading = true
    kbState.error = ''
    try {
      await fetchJson(
        'control',
        `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/documents/${encodeURIComponent(kbState.selectedDocId)}/chunks/${encodeURIComponent(chunkId)}/draft`,
        { method: 'DELETE' },
      )
      kbState.chunkDraftText[chunkId] = ''
      await loadChunks()
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function toggleDocumentFreeze(item) {
    const docId = getDocId(item)
    if (!kbState.selectedKbId || !docId) return
    kbState.loading = true
    kbState.error = ''
    try {
      await fetchJson(
        'control',
        `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/documents/${encodeURIComponent(docId)}/freeze`,
        { method: 'PATCH', body: { frozen: !Boolean(item?.frozen) } },
      )
      ui.message = item?.frozen ? '文档已解冻' : '文档已冻结'
      await loadDocs()
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function toggleChunkFreeze(item) {
    if (!kbState.selectedKbId || !kbState.selectedDocId) return
    const chunkId = getChunkId(item)
    if (!chunkId) return
    kbState.loading = true
    kbState.error = ''
    try {
      await fetchJson(
        'control',
        `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/documents/${encodeURIComponent(kbState.selectedDocId)}/chunks/${encodeURIComponent(chunkId)}/freeze`,
        { method: 'PATCH', body: { frozen: !Boolean(item?.frozen) } },
      )
      ui.message = item?.frozen ? '切片已解冻' : '切片已冻结'
      await loadChunks()
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function commitChunkDrafts() {
    if (!kbState.selectedKbId || !kbState.selectedDocId) return
    kbState.loading = true
    kbState.error = ''
    try {
      await fetchJson(
        'control',
        `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/documents/${encodeURIComponent(kbState.selectedDocId)}/chunks/commit`,
        { method: 'POST', body: {} },
      )
      ui.message = '草稿已提交'
      await Promise.all([loadChunks(), onTaskRefresh()])
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function runChunk() {
    if (!kbState.selectedKbId || !kbState.selectedDocId) return
    kbState.loading = true
    kbState.error = ''
    try {
      await fetchJson(
        'control',
        `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/documents/${encodeURIComponent(kbState.selectedDocId)}/chunk`,
        {
          method: 'POST',
          body: {
            chunk_method: kbState.chunkRun.chunk_method,
            chunk_size: Number(kbState.chunkRun.chunk_size) || 500,
            chunk_overlap: numberOrDefault(kbState.chunkRun.chunk_overlap, 50),
            chunk_delimiter: kbState.chunkRun.chunk_delimiter || undefined,
          },
        },
      )
      ui.message = '重切片任务已提交'
      await Promise.all([onTaskRefresh(), loadChunks()])
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function summarizeDoc() {
    if (!kbState.selectedKbId || !kbState.selectedDocId) return
    kbState.loading = true
    kbState.error = ''
    try {
      await fetchJson(
        'control',
        `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/documents/${encodeURIComponent(kbState.selectedDocId)}/summarize`,
        { method: 'POST', body: {} },
      )
      ui.message = '文档总结任务已提交'
      await onTaskRefresh()
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  async function deleteDoc(item) {
    const docId = getDocId(item)
    if (!docId || !kbState.selectedKbId) return
    if (!window.confirm(`确认删除文档 ${getDocName(item)} ?`)) return
    kbState.loading = true
    kbState.error = ''
    try {
      await fetchJson(
        'control',
        `/api/v1/admin/kbs/${encodeURIComponent(kbState.selectedKbId)}/documents/${encodeURIComponent(docId)}`,
        { method: 'DELETE' },
      )
      ui.message = '文档删除任务已提交'
      await onTaskRefresh()
    } catch (error) {
      kbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      kbState.loading = false
    }
  }

  return {
    loadControlTenants,
    loadKbs,
    createKb,
    renameKb,
    deleteKb,
    summarizeKb,
    loadKbBindings,
    bindKbTenant,
    unbindKbTenant,
    selectKb,
    loadDocs,
    loadKbTasks,
    selectKbTask,
    loadBatchDetail,
    retryBatchFailed,
    toggleDocSelection,
    toggleAllDocs,
    submitDocumentBatch,
    loadKbTaskEvents,
    ingestDocText,
    onUploadFileChange,
    uploadDocFile,
    selectDoc,
    loadChunks,
    saveChunkDraft,
    clearChunkDraft,
    toggleDocumentFreeze,
    toggleChunkFreeze,
    commitChunkDrafts,
    runChunk,
    summarizeDoc,
    deleteDoc,
  }
}
