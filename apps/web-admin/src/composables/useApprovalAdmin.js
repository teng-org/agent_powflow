import { computed, reactive } from 'vue'

const VALID_STATUSES = new Set(['', 'PENDING', 'DECIDED', 'CANCELLED'])
const VALID_ALERT_STATUSES = new Set(['', 'OPEN', 'ACKNOWLEDGED', 'RESOLVED'])

export function useApprovalAdmin(fetchJson) {
  const state = reactive({
    items: [],
    stats: null,
    trend: [],
    trendMeta: null,
    alerts: [],
    alertStats: null,
    alertConfig: null,
    selected: null,
    run: null,
    events: [],
    nodes: [],
    status: 'PENDING',
    dueWithinSeconds: 900,
    trendDays: 30,
    alertStatus: 'OPEN',
    loading: false,
    detailLoading: false,
    action: '',
    error: '',
    message: '',
    decisionDraft: null,
    reason: '',
  })

  const pendingCount = computed(() => state.items.filter((item) => item.status === 'PENDING').length)
  const canSubmitDecision = computed(() => {
    if (!state.selected || state.selected.status !== 'PENDING' || !state.decisionDraft || state.action) return false
    return state.decisionDraft !== 'reject' || Boolean(state.reason.trim())
  })

  function clearNotice() {
    state.error = ''
    state.message = ''
  }

  function normalizeStatus(status) {
    const value = String(status || '').trim().toUpperCase()
    if (!VALID_STATUSES.has(value)) throw new Error('不支持的审批状态')
    return value
  }

  function normalizeDueWindow(seconds) {
    const value = Number(seconds)
    if (!Number.isInteger(value) || value < 60 || value > 86400) throw new Error('临期窗口必须在 1 分钟到 24 小时之间')
    return value
  }

  function normalizeTrendDays(days) {
    const value = Number(days)
    if (!Number.isInteger(value) || value < 1 || value > 90) throw new Error('趋势窗口必须在 1 到 90 天之间')
    return value
  }

  function normalizeAlertStatus(status) {
    const value = String(status || '').trim().toUpperCase()
    if (!VALID_ALERT_STATUSES.has(value)) throw new Error('不支持的审批告警状态')
    return value
  }

  async function load({ preserveNotice = false } = {}) {
    state.loading = true
    if (!preserveNotice) clearNotice()
    try {
      const status = normalizeStatus(state.status)
      const dueWithinSeconds = normalizeDueWindow(state.dueWithinSeconds)
      const trendDays = normalizeTrendDays(state.trendDays)
      const alertStatus = normalizeAlertStatus(state.alertStatus)
      const query = status ? `?status=${encodeURIComponent(status)}` : ''
      const alertQuery = alertStatus ? `?status=${encodeURIComponent(alertStatus)}` : ''
      const [response, statsResponse, trendResponse, alertResponse, alertStatsResponse, alertConfigResponse] = await Promise.all([
        fetchJson('control', `/api/v1/approvals${query}`),
        fetchJson('control', `/api/v1/approvals/stats?due_within_seconds=${dueWithinSeconds}`),
        fetchJson('control', `/api/v1/approvals/trend?days=${trendDays}`),
        fetchJson('control', `/api/v1/admin/approval-alerts${alertQuery}`),
        fetchJson('control', '/api/v1/admin/approval-alerts/stats'),
        fetchJson('control', '/api/v1/admin/approval-alerts/config'),
      ])
      state.items = response?.items || []
      state.stats = statsResponse?.item || null
      state.trendMeta = trendResponse?.item || null
      state.trend = trendResponse?.item?.items || []
      state.alerts = alertResponse?.items || []
      state.alertStats = alertStatsResponse?.item || null
      state.alertConfig = alertConfigResponse?.item || null
      if (state.selected) {
        const fresh = state.items.find((item) => item.approval_id === state.selected.approval_id)
        if (fresh) state.selected = fresh
      }
      return state.items
    } catch (error) {
      state.error = error?.message || String(error)
      throw error
    } finally {
      state.loading = false
    }
  }

  async function select(item, { preserveNotice = false } = {}) {
    const approvalId = String(item?.approval_id || item || '').trim()
    if (!approvalId) return null
    state.detailLoading = true
    if (!preserveNotice) clearNotice()
    state.decisionDraft = null
    state.reason = ''
    try {
      const detail = await fetchJson('control', `/api/v1/approvals/${encodeURIComponent(approvalId)}`)
      state.selected = detail?.item || null
      const runId = state.selected?.run_id
      if (runId) {
        const [run, events, nodes] = await Promise.all([
          fetchJson('control', `/api/v1/admin/runs/${encodeURIComponent(runId)}`),
          fetchJson('control', `/api/v1/admin/runs/${encodeURIComponent(runId)}/events`),
          fetchJson('control', `/api/v1/admin/runs/${encodeURIComponent(runId)}/nodes`),
        ])
        state.run = run?.item || null
        state.events = events?.items || []
        state.nodes = nodes?.items || []
      }
      return state.selected
    } catch (error) {
      state.error = error?.message || String(error)
      throw error
    } finally {
      state.detailLoading = false
    }
  }

  function beginDecision(decision) {
    if (!state.selected || state.selected.status !== 'PENDING') return
    const value = String(decision || '').toLowerCase()
    if (!['approve', 'reject'].includes(value)) throw new Error('不支持的审批决定')
    clearNotice()
    state.decisionDraft = value
    state.reason = ''
  }

  function cancelDecision() {
    state.decisionDraft = null
    state.reason = ''
  }

  async function submitDecision() {
    if (!canSubmitDecision.value) throw new Error('审批决定尚未填写完整')
    const approvalId = state.selected.approval_id
    const decision = state.decisionDraft
    state.action = decision
    clearNotice()
    try {
      const response = await fetchJson(
        'control',
        `/api/v1/approvals/${encodeURIComponent(approvalId)}/decision`,
        {
          method: 'POST',
          body: { decision, reason: state.reason.trim(), revision: state.selected.revision },
        },
      )
      state.selected = response?.item || state.selected
      state.message = response?.duplicate
        ? `该审批已处理：${state.selected.decision === 'approve' ? '同意' : '拒绝'}`
        : `已${decision === 'approve' ? '同意' : '拒绝'}，工作流已进入恢复队列。`
      cancelDecision()
      await load({ preserveNotice: true })
      await select(state.selected, { preserveNotice: true })
      return state.selected
    } catch (error) {
      state.error = error?.message || String(error)
      throw error
    } finally {
      state.action = ''
    }
  }

  async function evaluateAlerts() {
    state.action = 'evaluate-alerts'
    clearNotice()
    try {
      const response = await fetchJson('control', '/api/v1/admin/approval-alerts/evaluate', { method: 'POST' })
      const created = Number(response?.item?.created_count) || 0
      state.message = created ? `已生成 ${created} 条新的审批告警。` : '告警评估完成，没有新的异常。'
      await load({ preserveNotice: true })
      return response?.item || null
    } catch (error) {
      state.error = error?.message || String(error)
      throw error
    } finally {
      state.action = ''
    }
  }

  async function acknowledgeAlert(alert) {
    const alertId = String(alert?.alert_id || '').trim()
    if (!alertId) return null
    state.action = `ack:${alertId}`
    clearNotice()
    try {
      const response = await fetchJson(
        'control',
        `/api/v1/admin/approval-alerts/${encodeURIComponent(alertId)}/acknowledge`,
        { method: 'POST' },
      )
      state.message = '告警已确认，底层审批完成后仍会自动解决。'
      await load({ preserveNotice: true })
      return response?.item || null
    } catch (error) {
      state.error = error?.message || String(error)
      throw error
    } finally {
      state.action = ''
    }
  }

  async function replayAlert(alert) {
    const alertId = String(alert?.alert_id || '').trim()
    if (!alertId) return null
    state.action = `replay:${alertId}`
    clearNotice()
    try {
      const response = await fetchJson(
        'control',
        `/api/v1/admin/approval-alerts/${encodeURIComponent(alertId)}/replay`,
        { method: 'POST' },
      )
      state.message = 'Webhook 投递已重新进入持久化队列。'
      await load({ preserveNotice: true })
      return response?.item || null
    } catch (error) {
      state.error = error?.message || String(error)
      throw error
    } finally {
      state.action = ''
    }
  }

  return {
    state,
    pendingCount,
    canSubmitDecision,
    load,
    select,
    beginDecision,
    cancelDecision,
    submitDecision,
    evaluateAlerts,
    acknowledgeAlert,
    replayAlert,
  }
}
