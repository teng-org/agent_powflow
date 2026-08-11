import { computed } from 'vue'

export function useMonitorAdmin({
  fetchJson,
  isControlLoggedIn,
  isExecutionLoggedIn,
  overviewState,
  taskState,
  dbState,
  ui,
  getTaskId,
}) {
  function isLogDebugEnabled() {
    try {
      return window?.localStorage?.getItem('powflow:logDebug') === '1'
    } catch {
      return false
    }
  }

  function logDebug(...args) {
    if (!isLogDebugEnabled()) return
    // eslint-disable-next-line no-console
    console.debug('[PowflowLogDebug]', ...args)
  }

  function normalizeLogItem(item) {
    return {
      id: item?.id || item?.log_id || item?.event_id || `${item?.ts || item?.time || ''}-${item?.message || ''}`,
      event_id: item?.event_id || item?.id || item?.log_id || '',
      ts: item?.ts || item?.time || item?.created_at || item?.createdAt || '',
      level: String(item?.level || item?.severity || 'info').toLowerCase(),
      category: item?.category || item?.field || 'system',
      message: item?.message || item?.detail || '-',
      source: item?.source || item?.caller || '-',
      role: item?.role || item?.user_role || '-',
      status: item?.status || item?.access_status || '-',
      path: item?.path || item?.api_path || '-',
      trace_id: item?.trace_id || item?.request_id || item?.traceId || '',
      raw: item,
    }
  }

  function normalizeChatApiLogItem(item) {
    const okRaw = item?.ok
    const ok = okRaw === true || String(okRaw).toLowerCase() === 'true' || String(okRaw) === '1'
    const ts =
      item?.ts ||
      item?.time ||
      item?.created_at ||
      item?.createdAt ||
      item?.finished_at ||
      item?.finishedAt ||
      item?.started_at ||
      item?.startedAt ||
      ''
    const task = String(item?.task || '').trim()
    const model = String(item?.model || '').trim()
    const endpoint = String(item?.endpoint_id || item?.endpointId || '').trim()
    const msg = String(item?.error || item?.error_message || item?.message || '').trim()
    const summary = msg || [task, model, endpoint].filter(Boolean).join(' | ') || 'chat api 调用'
    return {
      id:
        item?.id ||
        item?.log_id ||
        item?.event_id ||
        `${ts}-${task}-${model}-${endpoint}-${ok ? 'ok' : 'fail'}`,
      event_id: item?.event_id || item?.id || item?.log_id || '',
      ts,
      level: ok ? 'info' : 'error',
      category: 'chat_api',
      message: summary,
      source: 'chat-plane',
      role: String(item?.actor_role || item?.user_role || '-'),
      status: ok ? 'SUCCESS' : 'FAIL',
      path: endpoint || '/api/v1/admin/chat/api-pool/logs',
      trace_id: item?.trace_id || item?.request_id || item?.traceId || '',
      raw: item,
    }
  }

  function normalizeSeries(items) {
    return (items || []).map((x) => ({
      day: x?.day || x?.date || '-',
      token: Number(x?.total_tokens || x?.tokens || 0),
      users: Number(x?.active_users || x?.users || 0),
      chats: Number(x?.new_chats || x?.chat_sessions || x?.sessions || 0),
      cost: Number(x?.api_cost_total || x?.cost_total || x?.cost || 0),
    }))
  }

  async function loadSystemLogs({
    page = overviewState.systemLogsPage || 1,
    pageSize = overviewState.systemLogsPageSize || 20,
    days = 30,
    field = 'message',
    level = '',
    category = '',
    source = 'all',
    q = '',
    from = '',
    to = '',
    includeNoise = false,
    riskLevels = [],
  } = {}) {
    if (!(isExecutionLoggedIn.value || isControlLoggedIn.value)) return
    const params = new URLSearchParams()
    params.set('page', '1')
    const requestedPageSize = Math.max(20, Number(pageSize || 20))
    const fetchPageSize = Math.max(100, Math.min(400, requestedPageSize * 2))
    params.set('page_size', String(fetchPageSize))
    params.set('days', String(days))
    if (level) params.set('level', level)
    if (category) params.set('category', category)
    if (field) params.set('field', String(field))
    if (q) params.set('q', q)
    if (from) params.set('from_ts', from)
    if (to) params.set('to_ts', to)
    params.set('include_noise', includeNoise ? 'true' : 'false')
    try {
      const sourceMode = String(source || 'all').toLowerCase()
      const needExecution = sourceMode === 'all' || sourceMode === 'execution'
      const needChat = sourceMode === 'all' || sourceMode === 'chat'
      const normalizedRiskLevels = Array.from(
        new Set((Array.isArray(riskLevels) ? riskLevels : []).map((x) => String(x || '').toLowerCase()).filter(Boolean)),
      )
      const mapRiskLevelToEventLevel = (lv) => {
        const key = String(lv || '').toLowerCase()
        if (key === 'warn' || key === 'warning' || key === 'medium') return 'MEDIUM'
        if (key === 'error' || key === 'high') return 'HIGH'
        if (key === 'critical') return 'CRITICAL'
        if (key === 'low') return 'LOW'
        return ''
      }
      let riskEventLink = null
      if (isExecutionLoggedIn.value && needExecution && normalizedRiskLevels.length > 0) {
        const mergedEvents = []
        const levelSet = new Set(normalizedRiskLevels.map((lv) => mapRiskLevelToEventLevel(lv)).filter(Boolean))
        for (const lv of levelSet) {
          const rp = new URLSearchParams()
          rp.set('page', '1')
          rp.set('page_size', '200')
          rp.set('days', String(days))
          rp.set('risk_level', lv)
          const eventResp = await fetchJson('execution', `/api/v1/admin/system/risk/events?${rp.toString()}`)
          const rows = Array.isArray(eventResp?.items) ? eventResp.items : []
          mergedEvents.push(...rows)
        }
        const eventIds = new Set()
        const traceIds = new Set()
        for (const row of mergedEvents) {
          const eventId = String(row?.event_id || row?.id || '').trim()
          const traceId = String(row?.trace_id || '').trim()
          if (eventId) eventIds.add(eventId)
          if (traceId) traceIds.add(traceId)
        }
        riskEventLink = {
          eventIds,
          traceIds,
          totalEvents: mergedEvents.length,
        }
      }

      const jobs = []
      if (isExecutionLoggedIn.value && needExecution) {
        const p = new URLSearchParams(params.toString())
        // 风险筛选优先走 risk-events 关联，不再直接按日志 level 过滤
        if (normalizedRiskLevels.length > 0) p.delete('level')
        jobs.push(
          fetchJson('execution', `/api/v1/admin/system/logs?${p.toString()}`, {
            timeoutMs: 30000,
          }),
        )
      } else {
        jobs.push(Promise.resolve({ items: [] }))
      }
      if ((isControlLoggedIn.value || isExecutionLoggedIn.value) && needChat) {
        const chatParams = new URLSearchParams()
        const queryHeavy = Boolean(from || to || q || category || level)
        chatParams.set('limit', String(queryHeavy ? 1200 : Math.max(200, Math.min(800, Number(pageSize || 20) * 4))))
        jobs.push(
          fetchJson('chat', `/api/v1/admin/chat/api-pool/logs?${chatParams.toString()}`, {
            timeoutMs: 20000,
          }),
        )
      } else {
        jobs.push(Promise.resolve({ items: [] }))
      }
      const [executionRes, chatRes] = await Promise.allSettled(jobs)
      const executionData = executionRes.status === 'fulfilled' ? executionRes.value : { items: [] }
      const chatData = chatRes.status === 'fulfilled' ? chatRes.value : { items: [] }
      const executionRows = executionData?.items || executionData?.logs || []
      let execItems = executionRows.map(normalizeLogItem)
      if (riskEventLink) {
        execItems = execItems.filter((item) => {
          const eventId = String(item?.event_id || '').trim()
          const traceId = String(item?.trace_id || '').trim()
          if (eventId && riskEventLink.eventIds.has(eventId)) return true
          if (traceId && riskEventLink.traceIds.has(traceId)) return true
          return false
        })
      }
      const chatItems = normalizedRiskLevels.length > 0 ? [] : (chatData?.items || []).map(normalizeChatApiLogItem)
      const dedupMap = new Map()
      for (const item of [...execItems, ...chatItems]) {
        const fp = [
          item.ts,
          item.level,
          item.category,
          item.message,
          item.source,
          item.role,
          item.status,
          item.path,
        ].join('|')
        if (!dedupMap.has(fp)) dedupMap.set(fp, item)
      }
      const merged = Array.from(dedupMap.values()).sort((a, b) => String(b.ts || '').localeCompare(String(a.ts || '')))
      const levelStats = merged.reduce((acc, item) => {
        const lv = String(item?.level || '').toLowerCase() || 'unknown'
        acc[lv] = (acc[lv] || 0) + 1
        return acc
      }, {})
      const currentPage = Math.max(1, Number(page || 1))
      const cap = Math.max(1, Number(pageSize || 20))
      const offset = (currentPage - 1) * cap
      const paged = merged.slice(offset, offset + cap).map((item, idx) => ({
        ...item,
        id: `${item.id || 'log'}#${offset + idx}`,
      }))
      logDebug('loadSystemLogs', {
        params: { page, pageSize, days, field, level, category, source, q, from, to, includeNoise, riskLevels: normalizedRiskLevels },
        fetched: {
          execution: execItems.length,
          chat: chatItems.length,
          merged: merged.length,
          paged: paged.length,
          riskEvents: riskEventLink?.totalEvents || 0,
        },
        levelStats,
        sampleTop5: merged.slice(0, 5).map((x) => ({ ts: x.ts, level: x.level, category: x.category, source: x.source, message: x.message })),
      })
      overviewState.systemLogs = paged
      overviewState.systemLogsTotal = Number(merged.length || 0)
      overviewState.systemLogsPage = Number(page)
      overviewState.systemLogsPageSize = Number(pageSize)
      if (executionRes.status === 'rejected' || chatRes.status === 'rejected') {
        const errs = []
        if (executionRes.status === 'rejected') errs.push(`execution日志失败: ${executionRes.reason?.message || executionRes.reason || 'unknown'}`)
        if (chatRes.status === 'rejected') errs.push(`chat日志失败: ${chatRes.reason?.message || chatRes.reason || 'unknown'}`)
        overviewState.error = errs.join(' | ')
      }
    } catch {
      overviewState.systemLogs = []
      overviewState.systemLogsTotal = 0
      overviewState.error = '事务日志加载失败，请检查筛选条件或后端日志接口状态'
      logDebug('loadSystemLogs failed')
    }
  }

  async function loadSystemLogStats3d({ days = 3, riskLevels = [], category = '', source = '', q = '' } = {}) {
    if (!isExecutionLoggedIn.value) return
    try {
      const params = new URLSearchParams()
      params.set('days', String(Math.max(1, Math.min(365, Number(days || 3)))))
      const normalizedRiskLevels = Array.from(
        new Set((Array.isArray(riskLevels) ? riskLevels : []).map((x) => String(x || '').toLowerCase()).filter(Boolean)),
      )
      if (normalizedRiskLevels.length === 1) params.set('risk_level', normalizedRiskLevels[0] === 'warning' ? 'warn' : normalizedRiskLevels[0])
      if (q) params.set('q', String(q))
      // execution 风控事件接口按 source/category 仅支持 q 过滤，这里拼接到 q，避免丢失筛选意图
      const extQ = [String(q || '').trim(), String(category || '').trim(), String(source || '').trim()]
        .filter(Boolean)
        .join(' ')
      if (extQ) params.set('q', extQ)
      const data = await fetchJson('execution', `/api/v1/admin/system/risk/events/stats?${params.toString()}`, {
        timeoutMs: 30000,
      })
      const byLevel = data?.by_level || {}
      overviewState.systemLogStats3d = {
        warn: Number(byLevel.MEDIUM || byLevel.medium || 0),
        error: Number(byLevel.HIGH || byLevel.high || 0),
        critical: Number(byLevel.CRITICAL || byLevel.critical || 0),
      }
    } catch {
      const now = Date.now()
      const cutoff = now - Math.max(1, Number(days || 3)) * 24 * 3600 * 1000
      const stats = { warn: 0, error: 0, critical: 0 }
      for (const item of overviewState.systemLogs || []) {
        const ts = Date.parse(item.ts || '')
        if (!Number.isFinite(ts) || ts < cutoff) continue
        if (item.level === 'critical') stats.critical += 1
        else if (item.level === 'error') stats.error += 1
        else if (item.level === 'warn' || item.level === 'warning') stats.warn += 1
      }
      overviewState.systemLogStats3d = stats
    }
  }

  async function loadRuntimeSeries(days = 30) {
    if (!(isControlLoggedIn.value || isExecutionLoggedIn.value)) return
    try {
      const dayCount = Math.max(1, Math.min(365, Number(days || 30)))
      const byDay = new Map()

      // 1) 优先复用 debug-web 已验证的趋势接口（chat-api-pool）
      try {
        const trendsResp = await fetchJson('chat', `/api/v1/admin/chat/api-pool/usage/trends?limit_days=${dayCount}`)
        const trends = Array.isArray(trendsResp?.items) ? trendsResp.items : []
        for (const row of trends) {
          const day = String(row?.date || row?.day || '')
          if (!day) continue
          const current = byDay.get(day) || { day, token: 0, users: 0, chats: 0, cost: 0 }
          const inputTokens = Number(row?.input_tokens || 0)
          const outputTokens = Number(row?.output_tokens || 0)
          const requestCount = Number(row?.request_count || 0)
          const costTotal = Number(row?.cost_total || 0)
          byDay.set(day, {
            day,
            token: Math.max(0, inputTokens + outputTokens),
            users: current.users,
            chats: Math.max(0, requestCount),
            cost: Number.isFinite(costTotal) ? costTotal : 0,
          })
        }
      } catch {
        // ignore, fallback below
      }

      // 2) execution runtime-stats 作为补充（尤其 active_users）
      if (isExecutionLoggedIn.value) {
        try {
          const dailyResp = await fetchJson('execution', `/api/v1/admin/system/runtime-stats/daily?days=${dayCount}`)
          const rows = normalizeSeries(dailyResp?.items || dailyResp?.daily || [])
          for (const row of rows) {
            const day = String(row?.day || '')
            if (!day) continue
            const current = byDay.get(day) || { day, token: 0, users: 0, chats: 0, cost: 0 }
            byDay.set(day, {
              day,
              token: current.token || row.token || 0,
              users: row.users || current.users || 0,
              chats: current.chats || row.chats || 0,
              cost: current.cost || row.cost || 0,
            })
          }
        } catch {
          // ignore, fallback below
        }
      }

      const series = Array.from(byDay.values())
        .sort((a, b) => String(a.day).localeCompare(String(b.day)))
        .slice(-dayCount)

      if (series.length > 0) {
        overviewState.runtimeSeries = {
          token: series.map((x) => ({ day: x.day, value: x.token })),
          users: series.map((x) => ({ day: x.day, value: x.users })),
          chats: series.map((x) => ({ day: x.day, value: x.chats })),
          cost: series.map((x) => ({ day: x.day, value: x.cost })),
        }
        return
      }

      throw new Error('runtime series empty')
    } catch {
      overviewState.runtimeSeries = {
        token: [],
        users: [],
        chats: [],
        cost: [],
      }
    }
  }

  async function loadAlertPreview() {
    if (!isExecutionLoggedIn.value) return
    try {
      const data = await fetchJson('execution', '/api/v1/admin/system/alerts/preview?limit=50')
      overviewState.alertsPreview = Array.isArray(data?.items) ? data.items : []
    } catch {
      overviewState.alertsPreview = []
    }
  }

  async function loadPermissionWarnings({
    page = 1,
    pageSize = 50,
    days = 30,
    riskLevel = '',
    source = '',
    role = '',
    q = '',
  } = {}) {
    if (!isExecutionLoggedIn.value) return
    try {
      const params = new URLSearchParams()
      params.set('page', String(Math.max(1, Number(page || 1))))
      params.set('page_size', String(Math.max(1, Math.min(500, Number(pageSize || 50)))))
      params.set('days', String(Math.max(1, Math.min(365, Number(days || 30)))))
      if (riskLevel) params.set('risk_level', String(riskLevel))
      if (source) params.set('source', String(source))
      if (role) params.set('role', String(role))
      if (q) params.set('q', String(q))
      const data = await fetchJson('execution', `/api/v1/admin/system/permission-warnings?${params.toString()}`, {
        timeoutMs: 30000,
      })
      overviewState.permissionWarnings = Array.isArray(data?.items) ? data.items : []
      overviewState.permissionWarningsTotal = Number(data?.total || overviewState.permissionWarnings.length || 0)
      overviewState.permissionWarningsPage = Number(data?.page || page || 1)
      overviewState.permissionWarningsPageSize = Number(data?.page_size || pageSize || 50)
    } catch {
      overviewState.permissionWarnings = []
      overviewState.permissionWarningsTotal = 0
      overviewState.permissionWarningsPage = 1
      overviewState.permissionWarningsPageSize = Number(pageSize || 50)
    }
  }

  async function loadRiskOverview(days = 30) {
    if (!isExecutionLoggedIn.value) return
    try {
      const n = Math.max(1, Math.min(365, Number(days || 30)))
      const data = await fetchJson('execution', `/api/v1/admin/system/risk/overview?days=${n}`)
      overviewState.riskOverview = data || null
    } catch {
      overviewState.riskOverview = null
    }
  }

  async function loadRiskRules() {
    if (!isExecutionLoggedIn.value) return
    try {
      const data = await fetchJson('execution', '/api/v1/admin/system/risk/rules', { timeoutMs: 30000 })
      overviewState.riskRules = Array.isArray(data?.rules) ? data.rules : []
      overviewState.riskThresholds = data?.thresholds && typeof data.thresholds === 'object' ? data.thresholds : {}
    } catch {
      overviewState.riskRules = []
      overviewState.riskThresholds = {}
    }
  }

  async function saveRiskRules({ rules = [], thresholds = {}, note = '' } = {}) {
    if (!isExecutionLoggedIn.value) return { status: 'skipped' }
    return fetchJson('execution', '/api/v1/admin/system/risk/rules', {
      method: 'PATCH',
      body: {
        security_risk_rules: Array.isArray(rules) ? rules : [],
        security_alert_thresholds: thresholds && typeof thresholds === 'object' ? thresholds : {},
        note: String(note || ''),
      },
    })
  }

  async function loadRiskEvents({
    page = 1,
    pageSize = 20,
    days = 30,
    riskLevel = '',
    riskTag = '',
    decision = '',
    reasonCode = '',
    eventStatus = '',
    q = '',
  } = {}) {
    if (!isExecutionLoggedIn.value) return
    try {
      const params = new URLSearchParams()
      params.set('page', String(Math.max(1, Number(page || 1))))
      params.set('page_size', String(Math.max(1, Math.min(500, Number(pageSize || 20)))))
      params.set('days', String(Math.max(1, Math.min(365, Number(days || 30)))))
      if (riskLevel) params.set('risk_level', String(riskLevel))
      if (riskTag) params.set('risk_tag', String(riskTag))
      if (decision) params.set('decision', String(decision))
      if (reasonCode) params.set('reason_code', String(reasonCode))
      if (eventStatus) params.set('event_status', String(eventStatus))
      if (q) params.set('q', String(q))
      const data = await fetchJson('execution', `/api/v1/admin/system/risk/events?${params.toString()}`, {
        timeoutMs: 30000,
      })
      overviewState.riskEvents = Array.isArray(data?.items) ? data.items : []
      overviewState.riskEventsTotal = Number(data?.total || overviewState.riskEvents.length || 0)
      overviewState.riskEventsPage = Number(data?.page || page || 1)
      overviewState.riskEventsPageSize = Number(data?.page_size || pageSize || 20)
    } catch {
      overviewState.riskEvents = []
      overviewState.riskEventsTotal = 0
      overviewState.riskEventsPage = 1
      overviewState.riskEventsPageSize = Number(pageSize || 20)
    }
  }

  async function ackRiskEvent(eventId, note = '') {
    if (!isExecutionLoggedIn.value || !eventId) return { status: 'skipped' }
    return fetchJson('execution', `/api/v1/admin/system/risk/events/${encodeURIComponent(eventId)}/ack`, {
      method: 'POST',
      body: { note: String(note || '') },
    })
  }

  async function resolveRiskEvent(eventId, note = '') {
    if (!isExecutionLoggedIn.value || !eventId) return { status: 'skipped' }
    return fetchJson('execution', `/api/v1/admin/system/risk/events/${encodeURIComponent(eventId)}/resolve`, {
      method: 'POST',
      body: { note: String(note || '') },
    })
  }

  async function loadOverview() {
    overviewState.loading = true
    overviewState.error = ''
    try {
      const jobs = []
      jobs.push(fetchJson('execution', '/api/v1/system/health'))
      jobs.push(fetchJson('chat', '/api/v1/system/health'))
      if (isExecutionLoggedIn.value) {
        jobs.push(fetchJson('execution', '/api/v1/admin/system/runtime-config'))
        jobs.push(isControlLoggedIn.value
          ? fetchJson('control', '/api/v1/admin/tasks?limit=200')
          : Promise.resolve({ items: [] }))
        jobs.push(fetchJson('execution', '/api/v1/admin/usage/api-tokens/summary?days=30&tenant_scope=current&limit=200'))
        jobs.push(fetchJson('execution', '/api/v1/admin/usage/api-tokens/budgets'))
        jobs.push(fetchJson('execution', '/api/v1/admin/api-keys/lifecycle?pool=all&status=all&limit=500'))
        jobs.push(fetchJson('execution', '/api/v1/admin/api-keys/lifecycle/alerts?pool=all&status=OPEN&page=1&page_size=1'))
        jobs.push(fetchJson('execution', '/api/v1/admin/api-keys/lifecycle/alerts/trend?days=30'))
        jobs.push(fetchJson('execution', '/api/v1/admin/system/storage/usage'))
        jobs.push(fetchJson('execution', '/api/v1/admin/system/log-index/health'))
      } else {
        jobs.push(Promise.resolve(null))
        jobs.push(Promise.resolve(null))
        jobs.push(Promise.resolve(null))
        jobs.push(Promise.resolve({ items: [] }))
        jobs.push(Promise.resolve({ items: [] }))
        jobs.push(Promise.resolve({ total: 0 }))
        jobs.push(Promise.resolve({ items: [] }))
        jobs.push(Promise.resolve({ item: null }))
        jobs.push(Promise.resolve({ item: null }))
      }
      if (isControlLoggedIn.value || isExecutionLoggedIn.value) {
        jobs.push(fetchJson('chat', '/api/v1/admin/chat/store/status'))
        jobs.push(fetchJson('chat', '/api/v1/admin/chat/api-pool'))
      } else {
        jobs.push(Promise.resolve(null))
        jobs.push(Promise.resolve({ items: [] }))
      }
      const settled = await Promise.allSettled(jobs)
      const getVal = (idx, fallback = null) =>
        settled[idx]?.status === 'fulfilled' ? settled[idx].value : fallback

      const executionHealth = getVal(0, null)
      const chatHealth = getVal(1, null)
      const runtimeConfig = getVal(2, null)
      const tasks = getVal(3, { items: [] })
      const usageSummary = getVal(4, null)
      const usageBudgets = getVal(5, { items: [] })
      const keyLifecycle = getVal(6, { items: [] })
      const keyAlertsOpen = getVal(7, { total: 0 })
      const keyAlertsTrend = getVal(8, { items: [] })
      const systemStorage = getVal(9, { item: null })
      const logIndexHealth = getVal(10, { item: null })
      const chatStore = getVal(11, null)
      const chatApiPool = getVal(12, { items: [] })

      overviewState.executionHealth = executionHealth
      overviewState.chatHealth = chatHealth
      overviewState.runtimeConfig = runtimeConfig
      overviewState.usageSummary = usageSummary
      overviewState.usageBudgets = Array.isArray(usageBudgets?.items) ? usageBudgets.items : []
      {
        const rows = Array.isArray(keyLifecycle?.items) ? keyLifecycle.items : []
        let expiringSoon = 0
        let expired = 0
        for (const row of rows) {
          const s = String(row?.status || '').toLowerCase()
          if (s === 'expiring_soon') expiringSoon += 1
          if (s === 'expired') expired += 1
        }
        overviewState.apiKeyLifecycleSummary = {
          total: rows.length,
          expiring_soon: expiringSoon,
          expired,
          open_alerts: Number(keyAlertsOpen?.total || 0),
        }
      }
      overviewState.apiKeyLifecycleTrend = Array.isArray(keyAlertsTrend?.items) ? keyAlertsTrend.items : []
      overviewState.systemStorage = systemStorage?.item || null
      overviewState.logIndexHealth = logIndexHealth?.item || null
      overviewState.chatApiPool = Array.isArray(chatApiPool?.items) ? chatApiPool.items : []
      const taskItems = Array.isArray(tasks?.items) ? tasks.items : []
      overviewState.taskCount = taskItems.length
      overviewState.activeTaskCount = taskItems.filter((x) => {
        const s = String(x?.status || '').toUpperCase()
        return ['RUNNING', 'PENDING', 'QUEUED', 'PROCESSING'].includes(s)
      }).length
      overviewState.dailyTaskActive = taskItems
        .reduce((acc, item) => {
          const raw = item?.updated_at || item?.updatedAt || item?.created_at || item?.createdAt
          const day = raw ? String(raw).slice(0, 10) : 'unknown'
          acc[day] = (acc[day] || 0) + 1
          return acc
        }, {})
      overviewState.dailyTaskActive = Object.entries(overviewState.dailyTaskActive)
        .map(([day, value]) => ({ day, value }))
        .sort((a, b) => a.day.localeCompare(b.day))
        .slice(-14)
      overviewState.chatStore = chatStore
      const topLevelErrors = settled
        .map((r, idx) => ({ r, idx }))
        .filter((x) => x.r.status === 'rejected')
        .map((x) => `overview[${x.idx}]: ${x.r.reason?.message || x.r.reason || 'unknown'}`)
      if (topLevelErrors.length > 0) {
        overviewState.error = topLevelErrors.join(' | ')
      }
      // 预加载轻量统计（包含 3 日风险计数），避免必须先打开日志中心才显示数值。
      const subJobs = [loadRuntimeSeries(30), loadAlertPreview()]
      if (isExecutionLoggedIn.value) {
        subJobs.push(loadSystemLogStats3d({ days: 3 }))
        subJobs.push(loadPermissionWarnings({ page: 1, pageSize: 20, days: 7 }))
        subJobs.push(loadRiskEvents({ page: 1, pageSize: 20, days: 7 }))
      }
      const subSettled = await Promise.allSettled(subJobs)
      const subErrors = subSettled
        .filter((x) => x.status === 'rejected')
        .map((x) => x.reason?.message || x.reason || 'unknown')
      if (subErrors.length > 0) {
        overviewState.error = [overviewState.error, ...subErrors].filter(Boolean).join(' | ')
      }
    } catch (error) {
      overviewState.error = error instanceof Error ? error.message : String(error)
    } finally {
      overviewState.loading = false
    }
  }

  function buildTaskStatusCount(items) {
    const map = {}
    for (const item of items || []) {
      const status = String(item?.status || 'UNKNOWN').toUpperCase()
      map[status] = (map[status] || 0) + 1
    }
    return map
  }

  async function loadTaskEvents(taskId) {
    if (!isControlLoggedIn.value || !taskId) return
    try {
      const data = await fetchJson('control', `/api/v1/admin/tasks/${encodeURIComponent(taskId)}/events?limit=200`)
      taskState.taskEvents = data?.items || data?.events || []
    } catch {
      taskState.taskEvents = []
    }
  }

  async function loadTaskMonitor() {
    taskState.loading = true
    taskState.error = ''
    try {
      const list = isControlLoggedIn.value
        ? await fetchJson('control', '/api/v1/admin/tasks?limit=200')
        : { items: [] }
      const rerank = isControlLoggedIn.value || isExecutionLoggedIn.value
        ? await fetchJson('chat', '/api/v1/admin/chat/memory/rerank/jobs?limit=200')
        : { items: [] }
      taskState.list = Array.isArray(list?.items) ? list.items : []
      taskState.rerankJobs = Array.isArray(rerank?.items) ? rerank.items : []
      taskState.statusCount = buildTaskStatusCount(taskState.list)
      if (taskState.selectedTaskId) {
        await loadTaskEvents(taskState.selectedTaskId)
      }
    } catch (error) {
      taskState.error = error instanceof Error ? error.message : String(error)
    } finally {
      taskState.loading = false
    }
  }

  async function selectTaskForEvents(task) {
    const taskId = getTaskId(task)
    if (!taskId) return
    taskState.selectedTaskId = taskId
    await loadTaskEvents(taskId)
  }

  async function cancelTask(task) {
    const taskId = getTaskId(task)
    if (!taskId || !isControlLoggedIn.value) return
    taskState.loading = true
    taskState.error = ''
    try {
      await fetchJson('control', `/api/v1/admin/tasks/${encodeURIComponent(taskId)}/cancel`, {
        method: 'POST',
        body: {},
      })
      ui.message = `已请求取消任务：${taskId}`
      await loadTaskMonitor()
    } catch (error) {
      taskState.error = error instanceof Error ? error.message : String(error)
    } finally {
      taskState.loading = false
    }
  }

  async function clearFinishedTasks() {
    if (!isControlLoggedIn.value) return
    taskState.loading = true
    taskState.error = ''
    try {
      await fetchJson('control', '/api/v1/admin/tasks', { method: 'DELETE' })
      ui.message = '已清理已完成历史任务'
      taskState.selectedTaskId = ''
      taskState.taskEvents = []
      await loadTaskMonitor()
    } catch (error) {
      taskState.error = error instanceof Error ? error.message : String(error)
    } finally {
      taskState.loading = false
    }
  }

  async function loadDbRows() {
    dbState.loading = true
    dbState.error = ''
    try {
      if (dbState.source === 'execution' && dbState.selectedExecutionTable && isExecutionLoggedIn.value) {
        const rows = await fetchJson(
          'execution',
          `/api/v1/admin/dbviz/postgres/table/${encodeURIComponent(dbState.selectedExecutionTable)}?page=${dbState.page}&page_size=${dbState.pageSize}`,
        )
        dbState.executionRows = rows?.items || []
        dbState.executionTotal = rows?.total || 0
      }
      if (dbState.source === 'control' && dbState.selectedControlTable && isControlLoggedIn.value) {
        const rows = await fetchJson(
          'control',
          `/api/v1/admin/dbviz/postgres/table/${encodeURIComponent(dbState.selectedControlTable)}?tenant_scope=current&page=${dbState.page}&page_size=${dbState.pageSize}`,
        )
        dbState.controlRows = rows?.items || []
        dbState.controlTotal = rows?.total || 0
      }
    } catch (error) {
      dbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      dbState.loading = false
    }
  }

  async function loadDbMeta() {
    dbState.loading = true
    dbState.error = ''
    try {
      const jobs = []
      if (isExecutionLoggedIn.value) {
        jobs.push(fetchJson('execution', '/api/v1/admin/dbviz/overview'))
        jobs.push(fetchJson('execution', '/api/v1/admin/dbviz/postgres/tables'))
      } else {
        jobs.push(Promise.resolve(null))
        jobs.push(Promise.resolve({ tables: [] }))
      }
      if (isControlLoggedIn.value) {
        jobs.push(fetchJson('control', '/api/v1/admin/dbviz/overview?tenant_scope=current'))
        jobs.push(fetchJson('control', '/api/v1/admin/dbviz/postgres/tables?tenant_scope=current'))
      } else {
        jobs.push(Promise.resolve(null))
        jobs.push(Promise.resolve({ tables: [] }))
      }
      const [executionOverview, executionTables, controlOverview, controlTables] = await Promise.all(jobs)
      dbState.executionOverview = executionOverview
      dbState.controlOverview = controlOverview
      dbState.executionTables = executionTables?.tables || []
      dbState.controlTables = controlTables?.tables || []
      if (!dbState.selectedExecutionTable && dbState.executionTables.length) {
        dbState.selectedExecutionTable = dbState.executionTables[0]
      }
      if (!dbState.selectedControlTable && dbState.controlTables.length) {
        dbState.selectedControlTable = dbState.controlTables[0]
      }
      await loadDbRows()
    } catch (error) {
      dbState.error = error instanceof Error ? error.message : String(error)
    } finally {
      dbState.loading = false
    }
  }

  const dbRows = computed(() => (dbState.source === 'execution' ? dbState.executionRows : dbState.controlRows))
  const dbTotal = computed(() => (dbState.source === 'execution' ? dbState.executionTotal : dbState.controlTotal))
  const dbColumns = computed(() => {
    const first = dbRows.value[0]
    return first ? Object.keys(first) : []
  })

  return {
    loadOverview,
    loadSystemLogs,
    loadSystemLogStats3d,
    loadRuntimeSeries,
    loadAlertPreview,
    loadPermissionWarnings,
    loadRiskOverview,
    loadRiskRules,
    saveRiskRules,
    loadRiskEvents,
    ackRiskEvent,
    resolveRiskEvent,
    loadTaskMonitor,
    loadTaskEvents,
    selectTaskForEvents,
    cancelTask,
    clearFinishedTasks,
    loadDbMeta,
    loadDbRows,
    dbRows,
    dbTotal,
    dbColumns,
  }
}
