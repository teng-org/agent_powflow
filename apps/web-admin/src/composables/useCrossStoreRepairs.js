import { computed, reactive } from 'vue'

const ALLOWED_STATUSES = new Set(['', 'PENDING', 'IN_PROGRESS', 'SUCCEEDED', 'FAILED'])

export function useCrossStoreRepairs(fetchJson) {
  const state = reactive({
    items: [],
    status: '',
    loading: false,
    statsLoading: false,
    executing: false,
    executingId: null,
    error: '',
    message: '',
    lastResult: null,
    stats: null,
    objectStorage: null,
  })

  const statusCounts = computed(() => {
    const counts = { PENDING: 0, IN_PROGRESS: 0, SUCCEEDED: 0, FAILED: 0 }
    for (const item of state.items) {
      const status = String(item?.status || '').toUpperCase()
      if (Object.hasOwn(counts, status)) counts[status] += 1
    }
    return counts
  })

  const actionableCount = computed(() => statusCounts.value.PENDING + statusCounts.value.FAILED)

  async function load(status = state.status) {
    const normalized = String(status || '').trim().toUpperCase()
    if (!ALLOWED_STATUSES.has(normalized)) throw new Error(`unsupported repair status: ${normalized}`)
    state.status = normalized
    state.loading = true
    state.error = ''
    try {
      const query = new URLSearchParams({ limit: '200' })
      if (normalized) query.set('status', normalized)
      const response = await fetchJson('execution', `/api/v1/admin/system/cross-store-repairs?${query.toString()}`)
      state.items = Array.isArray(response?.items) ? response.items : []
    } catch (error) {
      state.items = []
      state.error = error instanceof Error ? error.message : String(error)
    } finally {
      state.loading = false
    }
  }

  async function loadStats() {
    state.statsLoading = true
    try {
      const response = await fetchJson('execution', '/api/v1/admin/system/cross-store-repairs/stats')
      state.stats = response?.item || null
      return state.stats
    } catch (error) {
      state.stats = null
      state.error = error instanceof Error ? error.message : String(error)
      return null
    } finally {
      state.statsLoading = false
    }
  }

  async function loadStorageHealth() {
    try {
      const response = await fetchJson('execution', '/api/v1/admin/system/object-storage/health')
      state.objectStorage = response?.item || null
      return state.objectStorage
    } catch (error) {
      state.objectStorage = null
      state.error = error instanceof Error ? error.message : String(error)
      return null
    }
  }

  async function loadAll() {
    await Promise.all([load(), loadStats(), loadStorageHealth()])
  }

  async function execute(limit = 50) {
    state.executing = true
    state.error = ''
    state.message = ''
    try {
      const safeLimit = Math.max(1, Math.min(200, Number(limit) || 50))
      const response = await fetchJson(
        'execution',
        `/api/v1/admin/system/cross-store-repairs/execute?limit=${safeLimit}`,
        { method: 'POST', body: {} },
      )
      state.lastResult = response?.item || null
      const result = state.lastResult || {}
      state.message = `修复完成：成功 ${Number(result.succeeded || 0)}，失败 ${Number(result.failed || 0)}，剩余 ${Number(result.remaining || 0)}`
      await loadAll()
      return state.lastResult
    } catch (error) {
      state.error = error instanceof Error ? error.message : String(error)
      return null
    } finally {
      state.executing = false
    }
  }

  async function executeOne(repairId) {
    const normalizedId = Number(repairId)
    if (!Number.isInteger(normalizedId) || normalizedId < 1) throw new Error('invalid repair id')
    state.executingId = normalizedId
    state.error = ''
    state.message = ''
    try {
      const response = await fetchJson(
        'execution',
        `/api/v1/admin/system/cross-store-repairs/${normalizedId}/execute`,
        { method: 'POST', body: {} },
      )
      const result = response?.item || {}
      const record = result.record || null
      state.message = result.succeeded
        ? `修复 #${normalizedId} 已完成`
        : `修复 #${normalizedId} 仍未完成：${record?.last_error || '正在由其他执行器处理'}`
      await loadAll()
      return result
    } catch (error) {
      state.error = error instanceof Error ? error.message : String(error)
      return null
    } finally {
      state.executingId = null
    }
  }

  return { state, statusCounts, actionableCount, load, loadStats, loadStorageHealth, loadAll, execute, executeOne }
}
