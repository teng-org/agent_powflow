import { describe, expect, it, vi } from 'vitest'
import { useApprovalAdmin } from '../useApprovalAdmin'

function pendingApproval() {
  return {
    approval_id: 'approval-1',
    run_id: 'run-1',
    node_id: 'review',
    node_type: 'human.approval',
    title: '发布前审核',
    assignee_roles: ['TENANT_ADMIN'],
    status: 'PENDING',
    decision: '',
    revision: 0,
  }
}

describe('useApprovalAdmin', () => {
  it('loads pending approvals and their run audit context', async () => {
    const item = pendingApproval()
    const fetchJson = vi.fn(async (_service, path) => {
      if (path === '/api/v1/approvals?status=PENDING') return { items: [item] }
      if (path === '/api/v1/approvals/stats?due_within_seconds=900') return { item: { health: 'WARN', pending_count: 1, due_soon_count: 1 } }
      if (path === '/api/v1/approvals/trend?days=30') return { item: { days: 30, timezone: 'UTC', items: [{ date: '2026-08-03', created_count: 2, decided_count: 1 }] } }
      if (path === '/api/v1/admin/approval-alerts?status=OPEN') return { items: [{ alert_id: 'alert-1', status: 'OPEN' }] }
      if (path === '/api/v1/admin/approval-alerts/stats') return { item: { health: 'WARN', open_count: 1 } }
      if (path === '/api/v1/admin/approval-alerts/config') return { item: { destination_configured: false } }
      if (path === '/api/v1/approvals/approval-1') return { item }
      if (path === '/api/v1/admin/runs/run-1') return { item: { run_id: 'run-1', status: 'WAITING_APPROVAL' } }
      if (path === '/api/v1/admin/runs/run-1/events') return { items: [{ event_id: 'event-1' }] }
      if (path === '/api/v1/admin/runs/run-1/nodes') return { items: [{ node_execution_id: 'node-1' }] }
      throw new Error(`unexpected path ${path}`)
    })
    const approvals = useApprovalAdmin(fetchJson)

    await approvals.load()
    await approvals.select(item)

    expect(approvals.pendingCount.value).toBe(1)
    expect(approvals.state.stats.health).toBe('WARN')
    expect(approvals.state.trend).toHaveLength(1)
    expect(approvals.state.alerts).toHaveLength(1)
    expect(approvals.state.run.status).toBe('WAITING_APPROVAL')
    expect(approvals.state.events).toHaveLength(1)
    expect(approvals.state.nodes).toHaveLength(1)
  })

  it('requires a rejection reason and sends revision for an idempotent decision', async () => {
    const item = pendingApproval()
    const decided = { ...item, status: 'DECIDED', decision: 'reject', decision_reason: '证据不足', revision: 1 }
    const fetchJson = vi.fn(async (_service, path, options) => {
      if (path === '/api/v1/approvals/approval-1/decision') {
        expect(options).toEqual({
          method: 'POST',
          body: { decision: 'reject', reason: '证据不足', revision: 0 },
        })
        return { item: decided, duplicate: false }
      }
      if (path === '/api/v1/approvals?status=PENDING') return { items: [] }
      if (path === '/api/v1/approvals/stats?due_within_seconds=900') return { item: { health: 'HEALTHY', pending_count: 0 } }
      if (path === '/api/v1/approvals/trend?days=30') return { item: { days: 30, timezone: 'UTC', items: [] } }
      if (path === '/api/v1/admin/approval-alerts?status=OPEN') return { items: [] }
      if (path === '/api/v1/admin/approval-alerts/stats') return { item: { health: 'HEALTHY', open_count: 0 } }
      if (path === '/api/v1/admin/approval-alerts/config') return { item: { destination_configured: false } }
      if (path === '/api/v1/approvals/approval-1') return { item: decided }
      if (path === '/api/v1/admin/runs/run-1') return { item: { run_id: 'run-1', status: 'PENDING' } }
      if (path.endsWith('/events') || path.endsWith('/nodes')) return { items: [] }
      throw new Error(`unexpected path ${path}`)
    })
    const approvals = useApprovalAdmin(fetchJson)
    approvals.state.selected = item
    approvals.beginDecision('reject')

    expect(approvals.canSubmitDecision.value).toBe(false)
    approvals.state.reason = '证据不足'
    expect(approvals.canSubmitDecision.value).toBe(true)
    await approvals.submitDecision()

    expect(approvals.state.selected.decision).toBe('reject')
    expect(approvals.state.message).toContain('已拒绝')
  })

  it('fails closed for unsupported filters', async () => {
    const approvals = useApprovalAdmin(vi.fn())
    approvals.state.status = 'UNKNOWN'
    await expect(approvals.load()).rejects.toThrow('不支持的审批状态')
  })

  it('fails closed for an unsafe due-soon window', async () => {
    const approvals = useApprovalAdmin(vi.fn())
    approvals.state.dueWithinSeconds = 30
    await expect(approvals.load()).rejects.toThrow('临期窗口必须在 1 分钟到 24 小时之间')
  })

  it('fails closed for a trend window over the retained range', async () => {
    const approvals = useApprovalAdmin(vi.fn())
    approvals.state.trendDays = 91
    await expect(approvals.load()).rejects.toThrow('趋势窗口必须在 1 到 90 天之间')
  })

  it('evaluates and acknowledges durable approval alerts', async () => {
    const alert = { alert_id: 'alert-1', status: 'OPEN', delivery: { status: 'DISABLED' } }
    const fetchJson = vi.fn(async (_service, path, options) => {
      if (path === '/api/v1/admin/approval-alerts/evaluate') {
        expect(options).toEqual({ method: 'POST' })
        return { item: { examined_count: 2, created_count: 1 } }
      }
      if (path === '/api/v1/admin/approval-alerts/alert-1/acknowledge') {
        expect(options).toEqual({ method: 'POST' })
        return { item: { ...alert, status: 'ACKNOWLEDGED' } }
      }
      if (path === '/api/v1/approvals?status=PENDING') return { items: [] }
      if (path === '/api/v1/approvals/stats?due_within_seconds=900') return { item: { health: 'HEALTHY' } }
      if (path === '/api/v1/approvals/trend?days=30') return { item: { items: [] } }
      if (path === '/api/v1/admin/approval-alerts?status=OPEN') return { items: [alert] }
      if (path === '/api/v1/admin/approval-alerts/stats') return { item: { open_count: 1 } }
      if (path === '/api/v1/admin/approval-alerts/config') return { item: { destination_configured: false } }
      throw new Error(`unexpected path ${path}`)
    })
    const approvals = useApprovalAdmin(fetchJson)

    await approvals.evaluateAlerts()
    expect(approvals.state.message).toContain('1 条新的审批告警')
    await approvals.acknowledgeAlert(alert)
    expect(approvals.state.message).toContain('告警已确认')
  })

  it('fails closed for an unsupported alert filter', async () => {
    const approvals = useApprovalAdmin(vi.fn())
    approvals.state.alertStatus = 'MUTED'
    await expect(approvals.load()).rejects.toThrow('不支持的审批告警状态')
  })
})
