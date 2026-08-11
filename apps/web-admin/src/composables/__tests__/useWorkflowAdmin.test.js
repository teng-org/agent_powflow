import { describe, expect, it, vi } from 'vitest'
import { parseWorkflowJson, useWorkflowAdmin } from '../useWorkflowAdmin'

const workflow = {
  workflow_id: 'wf-1',
  name: '客服助手',
  description: '回答产品问题',
  status: 'DRAFT',
  latest_version_no: 0,
  revision: 4,
  draft_dsl: { entry_node_id: 'start', nodes: [], edges: [] },
  draft_layout: { start: { x: 10, y: 20 } },
}

describe('useWorkflowAdmin', () => {
  it('parses only JSON objects and reports a useful local error', () => {
    expect(parseWorkflowJson('{"nodes":[]}', 'DSL')).toEqual({ nodes: [] })
    expect(() => parseWorkflowJson('[1]', 'DSL')).toThrow('DSL 必须是 JSON 对象')
    expect(() => parseWorkflowJson('{', 'Layout')).toThrow('Layout 不是有效 JSON')
  })

  it('loads a workflow and saves with optimistic revision protection', async () => {
    const fetchJson = vi.fn()
      .mockResolvedValueOnce({ item: workflow, validation: { valid: true, errors: [], warnings: [] } })
      .mockResolvedValueOnce({ items: [] })
      .mockResolvedValueOnce({ item: { ...workflow, revision: 5, name: '客服助手 v2' }, validation: { valid: true, errors: [], warnings: [] } })
      .mockResolvedValueOnce({ items: [{ ...workflow, revision: 5, name: '客服助手 v2' }] })
    const admin = useWorkflowAdmin(fetchJson)

    await admin.select('wf-1')
    admin.state.editor.name = '客服助手 v2'
    await admin.save()

    expect(fetchJson.mock.calls[2]).toEqual([
      'control',
      '/api/v1/admin/workflows/wf-1/draft',
      expect.objectContaining({ method: 'PUT', body: expect.objectContaining({ expected_revision: 4, name: '客服助手 v2' }) }),
    ])
    expect(admin.state.item.revision).toBe(5)
    expect(admin.state.message).toContain('通过校验')
  })

  it('does not send malformed editor JSON to the backend', async () => {
    const fetchJson = vi.fn()
    const admin = useWorkflowAdmin(fetchJson)
    admin.newDraft()
    admin.state.editor.dslText = '{'

    await expect(admin.save()).rejects.toThrow('DSL 不是有效 JSON')
    expect(fetchJson).not.toHaveBeenCalled()
    expect(admin.state.saving).toBe(false)
  })

  it('publishes an immutable version and refreshes detail, history, and list', async () => {
    const published = { ...workflow, status: 'PUBLISHED', latest_version_no: 1, revision: 5 }
    const version = { version_id: 'wv-1', workflow_id: 'wf-1', version_no: 1, checksum: 'a'.repeat(64) }
    const fetchJson = vi.fn()
      .mockResolvedValueOnce({ item: version })
      .mockResolvedValueOnce({ item: published, validation: { valid: true, errors: [], warnings: [] } })
      .mockResolvedValueOnce({ items: [version] })
      .mockResolvedValueOnce({ items: [published] })
    const admin = useWorkflowAdmin(fetchJson)
    admin.state.item = workflow
    admin.state.selectedId = workflow.workflow_id
    admin.state.validation = { valid: true, errors: [], warnings: [] }

    const result = await admin.publish()

    expect(fetchJson.mock.calls[0]).toEqual([
      'control', '/api/v1/admin/workflows/wf-1/publish',
      { method: 'POST', body: { expected_revision: 4 } },
    ])
    expect(result.version_no).toBe(1)
    expect(admin.state.versions).toEqual([version])
    expect(admin.state.message).toBe('已发布不可变版本 v1。')
  })

  it('allows permanent deletion only after archive', async () => {
    const fetchJson = vi.fn()
      .mockResolvedValueOnce({ status: 'ok', deleted_workflow_id: 'wf-1' })
      .mockResolvedValueOnce({ items: [] })
    const admin = useWorkflowAdmin(fetchJson)
    admin.state.item = { ...workflow, status: 'ARCHIVED' }

    await admin.remove()

    expect(fetchJson.mock.calls[0]).toEqual(['control', '/api/v1/admin/workflows/wf-1', { method: 'DELETE' }])
    expect(admin.state.item).toBeNull()
    expect(admin.state.message).toContain('永久删除')
  })
})
