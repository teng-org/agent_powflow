import { describe, expect, it, vi } from 'vitest'
import { useCrossStoreRepairs } from '../useCrossStoreRepairs'

describe('useCrossStoreRepairs', () => {
  it('loads repairs, filters status, and derives actionable counts', async () => {
    const fetchJson = vi.fn(async () => ({
      items: [
        { repair_id: 1, status: 'PENDING' },
        { repair_id: 2, status: 'FAILED' },
        { repair_id: 3, status: 'SUCCEEDED' },
      ],
    }))
    const repairs = useCrossStoreRepairs(fetchJson)

    await repairs.load('FAILED')

    expect(fetchJson).toHaveBeenCalledWith('execution', '/api/v1/admin/system/cross-store-repairs?limit=200&status=FAILED')
    expect(repairs.statusCounts.value).toEqual({ PENDING: 1, IN_PROGRESS: 0, SUCCEEDED: 1, FAILED: 1 })
    expect(repairs.actionableCount.value).toBe(2)
    expect(repairs.state.loading).toBe(false)
  })

  it('executes a bounded repair batch and refreshes the ledger', async () => {
    const fetchJson = vi.fn()
      .mockResolvedValueOnce({ item: { claimed: 2, succeeded: 1, failed: 1, remaining: 1 } })
      .mockResolvedValueOnce({ items: [{ repair_id: 2, status: 'FAILED' }] })
      .mockResolvedValueOnce({ item: { health: 'CRITICAL', open_count: 1 } })
    const repairs = useCrossStoreRepairs(fetchJson)

    const result = await repairs.execute(999)

    expect(fetchJson.mock.calls[0]).toEqual([
      'execution',
      '/api/v1/admin/system/cross-store-repairs/execute?limit=200',
      { method: 'POST', body: {} },
    ])
    expect(result).toEqual({ claimed: 2, succeeded: 1, failed: 1, remaining: 1 })
    expect(repairs.state.message).toContain('成功 1，失败 1，剩余 1')
    expect(repairs.state.items).toEqual([{ repair_id: 2, status: 'FAILED' }])
    expect(repairs.state.stats).toEqual({ health: 'CRITICAL', open_count: 1 })
    expect(repairs.state.executing).toBe(false)
  })

  it('loads health statistics independently from the filtered ledger', async () => {
    const fetchJson = vi.fn(async () => ({ item: { health: 'WARN', oldest_open_age_seconds: 420 } }))
    const repairs = useCrossStoreRepairs(fetchJson)

    await repairs.loadStats()

    expect(fetchJson).toHaveBeenCalledWith('execution', '/api/v1/admin/system/cross-store-repairs/stats')
    expect(repairs.state.stats.health).toBe('WARN')
    expect(repairs.state.statsLoading).toBe(false)
  })

  it('retries exactly one repair and refreshes operational state', async () => {
    const fetchJson = vi.fn()
      .mockResolvedValueOnce({ item: { succeeded: true, record: { repair_id: 42, status: 'SUCCEEDED' } } })
      .mockResolvedValueOnce({ items: [{ repair_id: 42, status: 'SUCCEEDED' }] })
      .mockResolvedValueOnce({ item: { health: 'HEALTHY', open_count: 0 } })
      .mockResolvedValueOnce({ item: { health: 'HEALTHY' } })
    const repairs = useCrossStoreRepairs(fetchJson)

    const result = await repairs.executeOne(42)

    expect(fetchJson.mock.calls[0]).toEqual([
      'execution',
      '/api/v1/admin/system/cross-store-repairs/42/execute',
      { method: 'POST', body: {} },
    ])
    expect(result.succeeded).toBe(true)
    expect(repairs.state.message).toBe('修复 #42 已完成')
    expect(repairs.state.executingId).toBeNull()
    expect(repairs.state.stats.open_count).toBe(0)
  })

  it('loads object-storage durability controls', async () => {
    const fetchJson = vi.fn(async () => ({ item: { health: 'WARN', versioning_status: 'DISABLED' } }))
    const repairs = useCrossStoreRepairs(fetchJson)

    await repairs.loadStorageHealth()

    expect(fetchJson).toHaveBeenCalledWith('execution', '/api/v1/admin/system/object-storage/health')
    expect(repairs.state.objectStorage.versioning_status).toBe('DISABLED')
  })

  it('fails closed on an unsupported status without issuing a request', async () => {
    const fetchJson = vi.fn()
    const repairs = useCrossStoreRepairs(fetchJson)

    await expect(repairs.load('deleted')).rejects.toThrow('unsupported repair status')
    expect(fetchJson).not.toHaveBeenCalled()
  })
})
