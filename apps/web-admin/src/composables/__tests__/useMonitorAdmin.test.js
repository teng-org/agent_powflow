import { describe, expect, it, vi } from 'vitest'
import { useMonitorAdmin } from '../useMonitorAdmin'

describe('useMonitorAdmin alert preview', () => {
  it('preserves cross-store health alerts for the overview', async () => {
    const alert = {
      alert_type: 'cross_store_repair',
      severity: 'critical',
      scope: 'platform/cross-store',
      message: '1 failed repair obligations',
    }
    const fetchJson = vi.fn(async () => ({ items: [alert] }))
    const overviewState = { alertsPreview: [] }
    const monitor = useMonitorAdmin({
      fetchJson,
      isControlLoggedIn: { value: true },
      isExecutionLoggedIn: { value: true },
      overviewState,
      taskState: {},
      dbState: {},
      ui: {},
      getTaskId: () => '',
    })

    await monitor.loadAlertPreview()

    expect(fetchJson).toHaveBeenCalledWith('execution', '/api/v1/admin/system/alerts/preview?limit=50')
    expect(overviewState.alertsPreview).toEqual([alert])
  })

  it('routes task list, events, cancellation and retention through Control Plane', async () => {
    const fetchJson = vi.fn(async () => ({ items: [] }))
    const taskState = { list: [], rerankJobs: [], selectedTaskId: '', taskEvents: [] }
    const monitor = useMonitorAdmin({
      fetchJson,
      isControlLoggedIn: { value: true },
      isExecutionLoggedIn: { value: true },
      overviewState: {},
      taskState,
      dbState: {},
      ui: {},
      getTaskId: (task) => task.task_id,
    })

    await monitor.loadTaskMonitor()
    await monitor.selectTaskForEvents({ task_id: 'task-a' })
    await monitor.cancelTask({ task_id: 'task-a' })
    await monitor.clearFinishedTasks()

    expect(fetchJson).toHaveBeenCalledWith('control', '/api/v1/admin/tasks?limit=200')
    expect(fetchJson).toHaveBeenCalledWith('control', '/api/v1/admin/tasks/task-a/events?limit=200')
    expect(fetchJson).toHaveBeenCalledWith('control', '/api/v1/admin/tasks/task-a/cancel', {
      method: 'POST',
      body: {},
    })
    expect(fetchJson).toHaveBeenCalledWith('control', '/api/v1/admin/tasks', { method: 'DELETE' })
  })
})
