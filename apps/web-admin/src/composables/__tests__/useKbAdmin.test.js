import { describe, expect, it, vi } from 'vitest'
import { useKbAdmin } from '../useKbAdmin'

function harness(overrides = {}) {
  const fetchJson = overrides.fetchJson || vi.fn().mockResolvedValue({ items: [] })
  const kbState = {
    loading: false,
    error: '',
    items: [],
    createName: '',
    selectedKbId: '',
    selectedKbName: '',
    selectedDocId: '',
    selectedDocName: '',
    selectedDocIds: [],
    docs: [],
    chunks: [],
    chunkDraftText: {},
    bindings: [],
    tenants: [],
    bindTenantId: '',
    tasks: [],
    taskEvents: [],
    selectedBatchId: '',
    batchDetail: null,
    upload: { files: [], inputVersion: 0, processMode: 'UPLOAD_CLEAN_CHUNK_EMBED', autoSummary: false },
    chunkRun: { chunk_method: 'sentence', chunk_size: 500, chunk_overlap: 50, chunk_delimiter: '' },
    batch: { operation: 'chunk', target_kb_id: '', tags_csv: '', mode: 'add', batch_name: '' },
    ...overrides.kbState,
  }
  const ui = { message: '' }
  const admin = useKbAdmin({
    fetchJson,
    isControlLoggedIn: { value: true },
    isExecutionLoggedIn: { value: true },
    kbState,
    ui,
    getKbId: (item) => item?.kb_id || '',
    getKbName: (item) => item?.name || '',
    getDocId: (item) => item?.doc_id || '',
    getDocName: (item) => item?.filename || '',
    getChunkId: (item) => item?.chunk_id || '',
    getTaskId: (item) => item?.task_id || '',
    onTaskRefresh: vi.fn(),
  })
  return { admin, fetchJson, kbState, ui }
}

describe('useKbAdmin authority routing', () => {
  it('loads and creates KB metadata through Control Plane', async () => {
    const { admin, fetchJson, kbState } = harness()
    kbState.createName = 'Authority KB'

    await admin.loadKbs()
    await admin.createKb()

    expect(fetchJson.mock.calls[0]).toEqual(['control', '/api/v1/admin/kbs'])
    expect(fetchJson.mock.calls[1]).toEqual([
      'control',
      '/api/v1/admin/kbs',
      { method: 'POST', body: { name: 'Authority KB' } },
    ])
  })

  it('routes tenant bindings through Control Plane', async () => {
    const { admin, fetchJson, kbState } = harness({
      kbState: { selectedKbId: 'kb-a', bindTenantId: 'tenant-b' },
    })

    await admin.bindKbTenant()

    expect(fetchJson.mock.calls[0]).toEqual([
      'control',
      '/api/v1/admin/kbs/kb-a/bindings',
      { method: 'PUT', body: { target_tenant_id: 'tenant-b' } },
    ])
  })

  it('keeps document reads on Execution Plane', async () => {
    const { admin, fetchJson } = harness({ kbState: { selectedKbId: 'kb-a' } })

    await admin.loadDocs()

    expect(fetchJson).toHaveBeenCalledWith('execution', '/api/v1/admin/kbs/kb-a/documents')
  })

  it('submits document mutation commands through Control and preserves zero overlap', async () => {
    const { admin, fetchJson } = harness({
      kbState: {
        selectedKbId: 'kb-a',
        selectedDocId: 'doc-a',
        chunkRun: {
          chunk_method: 'sentence',
          chunk_size: 100,
          chunk_overlap: 0,
          chunk_delimiter: '',
        },
      },
    })

    await admin.runChunk()
    await admin.summarizeDoc()
    await admin.commitChunkDrafts()

    expect(fetchJson).toHaveBeenCalledWith(
      'control',
      '/api/v1/admin/kbs/kb-a/documents/doc-a/chunk',
      {
        method: 'POST',
        body: {
          chunk_method: 'sentence',
          chunk_size: 100,
          chunk_overlap: 0,
          chunk_delimiter: undefined,
        },
      },
    )
    expect(fetchJson).toHaveBeenCalledWith(
      'control',
      '/api/v1/admin/kbs/kb-a/documents/doc-a/summarize',
      { method: 'POST', body: {} },
    )
    expect(fetchJson).toHaveBeenCalledWith(
      'control',
      '/api/v1/admin/kbs/kb-a/documents/doc-a/chunks/commit',
      { method: 'POST', body: {} },
    )
  })

  it('submits text ingest commands through Control Plane', async () => {
    const { admin, fetchJson } = harness({
      kbState: {
        selectedKbId: 'kb-a',
        ingest: {
          doc_id: 'doc-a',
          filename: 'sample.txt',
          content: 'hello authority',
          chunk_method: 'fixed',
          chunk_size: 500,
          chunk_overlap: 50,
        },
      },
    })

    await admin.ingestDocText()

    expect(fetchJson).toHaveBeenCalledWith(
      'control',
      '/api/v1/admin/kbs/kb-a/documents/ingest',
      {
        method: 'POST',
        body: {
          doc_id: 'doc-a',
          filename: 'sample.txt',
          content: 'hello authority',
          chunk_method: 'fixed',
          chunk_size: 500,
          chunk_overlap: 50,
        },
      },
    )
  })

  it('submits multipart upload commands through Control Plane', async () => {
    const file = new File(['hello file authority'], 'sample.txt', { type: 'text/plain' })
    const { admin, fetchJson } = harness({
      kbState: {
        selectedKbId: 'kb-a',
        upload: { files: [file], processMode: 'UPLOAD_CLEAN_CHUNK_EMBED', autoSummary: true },
      },
    })

    await admin.uploadDocFile()

    const uploadCall = fetchJson.mock.calls.find((call) => call[1] === '/api/v1/admin/kbs/kb-a/documents/upload')
    expect(uploadCall?.[0]).toBe('control')
    expect(uploadCall?.[2]?.method).toBe('POST')
    expect(uploadCall?.[2]?.body).toBeInstanceOf(FormData)
    expect(uploadCall?.[2]?.body.get('file')).toBe(file)
    expect(uploadCall?.[2]?.body.get('process_mode')).toBe('UPLOAD_CLEAN_CHUNK_EMBED')
    expect(uploadCall?.[2]?.body.get('chunk_method')).toBe('sentence')
    expect(uploadCall?.[2]?.body.get('auto_summary')).toBe('true')
  })

  it('submits multiple non-index uploads with summaries disabled', async () => {
    const first = new File(['raw one'], 'one.txt', { type: 'text/plain' })
    const second = new File(['raw two'], 'two.txt', { type: 'text/plain' })
    const { admin, fetchJson, ui } = harness({
      fetchJson: vi.fn().mockResolvedValue({ accepted_count: 2, skipped_count: 0, accepted: [] }),
      kbState: {
        selectedKbId: 'kb-a',
        upload: { files: [first, second], processMode: 'UPLOAD_ONLY', autoSummary: true },
      },
    })

    await admin.uploadDocFile()

    const body = fetchJson.mock.calls[0][2].body
    expect(body.getAll('file')).toEqual([first, second])
    expect(body.get('process_mode')).toBe('UPLOAD_ONLY')
    expect(body.get('auto_summary')).toBe('false')
    expect(ui.message).toContain('接受 2 个')
  })

  it('loads task state and events through Control Plane', async () => {
    const { admin, fetchJson, kbState } = harness({
      kbState: { selectedKbId: 'kb-a', selectedTaskId: 'task-a' },
    })

    await admin.loadKbTasks()
    await admin.loadKbTaskEvents()

    expect(fetchJson).toHaveBeenCalledWith('control', '/api/v1/admin/tasks?kb_id=kb-a&limit=50')
    expect(fetchJson).toHaveBeenCalledWith('control', '/api/v1/admin/tasks/task-a/events?limit=100')
  })

  it('routes draft and freeze metadata writes through Control Plane', async () => {
    const { admin, fetchJson } = harness({
      kbState: {
        selectedKbId: 'kb-a',
        selectedDocId: 'doc-a',
        chunkDraftText: { 'chunk-a': 'revised text' },
      },
    })
    const chunk = { chunk_id: 'chunk-a', frozen: false }
    const document = { doc_id: 'doc-a', frozen: false }

    await admin.saveChunkDraft(chunk)
    await admin.clearChunkDraft(chunk)
    await admin.toggleDocumentFreeze(document)
    await admin.toggleChunkFreeze(chunk)

    expect(fetchJson).toHaveBeenCalledWith(
      'control',
      '/api/v1/admin/kbs/kb-a/documents/doc-a/chunks/chunk-a/draft',
      { method: 'PUT', body: { text: 'revised text' } },
    )
    expect(fetchJson).toHaveBeenCalledWith(
      'control',
      '/api/v1/admin/kbs/kb-a/documents/doc-a/chunks/chunk-a/draft',
      { method: 'DELETE' },
    )
    expect(fetchJson).toHaveBeenCalledWith(
      'control',
      '/api/v1/admin/kbs/kb-a/documents/doc-a/freeze',
      { method: 'PATCH', body: { frozen: true } },
    )
    expect(fetchJson).toHaveBeenCalledWith(
      'control',
      '/api/v1/admin/kbs/kb-a/documents/doc-a/chunks/chunk-a/freeze',
      { method: 'PATCH', body: { frozen: true } },
    )
  })

  it('submits selected documents as a Control-owned batch and loads its detail', async () => {
    const fetchJson = vi.fn().mockImplementation(async (plane, path) => {
      if (path.endsWith('/documents/batch')) {
        return { batch_id: 'batch-a', accepted_count: 2 }
      }
      if (path === '/api/v1/admin/tasks/batches/batch-a') {
        return { item: { batch_id: 'batch-a', status: 'RUNNING' }, items: [] }
      }
      return { items: [] }
    })
    const { admin, kbState } = harness({
      fetchJson,
      kbState: {
        selectedKbId: 'kb-a',
        selectedDocIds: ['doc-a', 'doc-b'],
        batch: { operation: 'tag', target_kb_id: '', tags_csv: 'reviewed, important', mode: 'replace', batch_name: 'review batch' },
      },
    })

    await admin.submitDocumentBatch()

    expect(fetchJson).toHaveBeenCalledWith(
      'control',
      '/api/v1/admin/kbs/kb-a/documents/batch',
      {
        method: 'POST',
        body: {
          operation: 'tag',
          doc_ids: ['doc-a', 'doc-b'],
          batch_name: 'review batch',
          tags: ['reviewed', 'important'],
          mode: 'replace',
        },
      },
    )
    expect(fetchJson).toHaveBeenCalledWith('control', '/api/v1/admin/tasks/batches/batch-a')
    expect(kbState.selectedDocIds).toEqual([])
    expect(kbState.batchDetail?.item?.status).toBe('RUNNING')
  })
})
