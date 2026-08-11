export function useApiAdmin({
  fetchJson,
  fetchRaw,
  isExecutionLoggedIn,
  apiSettingState,
  ui,
  cfg,
  endpointIdOf,
  downloadBlob,
}) {
  function normalizeConsumerEndpoint(item) {
    const task = String(item?.task || '').trim()
    const pool = String(item?.pool || '').trim() || 'main'
    return {
      task,
      pool,
      label: String(item?.label || task).trim() || task,
      capability: String(item?.capability || '').trim(),
      description: String(item?.description || '').trim(),
    }
  }

  async function loadApiConsumerEndpoints() {
    const defaults = [
      { task: 'rerank', pool: 'main', label: 'RAG 候选重排', capability: 'rerank' },
      { task: 'embedding', pool: 'main', label: '知识库向量嵌入', capability: 'embedding' },
      { task: 'llm', pool: 'main', label: '执行平面通用 LLM', capability: 'llm' },
      { task: 'summary_llm', pool: 'aux', label: '文档/知识库总结 LLM', capability: 'llm' },
      { task: 'summary_embedding', pool: 'aux', label: '总结辅助 Embedding', capability: 'embedding' },
      { task: 'chat_llm', pool: 'chat', label: '对话主回复 LLM', capability: 'llm' },
      { task: 'chat_aux', pool: 'chat', label: '对话辅助 LLM', capability: 'llm' },
      { task: 'chat_summary', pool: 'chat', label: '对话记忆总结 LLM', capability: 'llm' },
      { task: 'chat_embedding', pool: 'chat', label: '对话记忆 Embedding', capability: 'embedding' },
    ]
    try {
      const [executionCatalog, chatCatalog] = await Promise.all([
        isExecutionLoggedIn.value
          ? fetchJson('execution', '/api/v1/admin/api-pool/consumer-endpoints')
          : Promise.resolve({ items: [] }),
        fetchJson('chat', '/api/v1/admin/chat/api-pool/consumer-endpoints'),
      ])
      const merged = [...(executionCatalog?.items || []), ...(chatCatalog?.items || [])]
        .map(normalizeConsumerEndpoint)
        .filter((x) => x.task)
      apiSettingState.consumerEndpoints = merged.length ? merged : defaults
    } catch {
      apiSettingState.consumerEndpoints = defaults
    }
  }

  async function loadPlatformUsageSummary() {
    if (!isExecutionLoggedIn.value) {
      apiSettingState.platformUsage.summary = []
      return
    }
    const f = apiSettingState.filters.platformUsage
    const query = new URLSearchParams({
      days: String(Number(f.days) || 30),
      tenant_scope: f.tenant_scope || 'current',
      limit: String(Number(f.limit) || 200),
    }).toString()
    const data = await fetchJson('execution', `/api/v1/admin/usage/api-tokens/summary?${query}`)
    apiSettingState.platformUsage.summary = data?.items || data?.summary || []
  }

  async function loadPlatformUsageEvents() {
    if (!isExecutionLoggedIn.value) {
      apiSettingState.platformUsage.events = []
      apiSettingState.platformUsage.eventsTotal = 0
      return
    }
    const f = apiSettingState.filters.platformEvents
    const query = new URLSearchParams({
      days: String(Number(f.days) || 30),
      tenant_scope: f.tenant_scope || 'current',
      page: String(Number(f.page) || 1),
      page_size: String(Number(f.page_size) || 50),
      ...(f.api_path ? { api_path: f.api_path } : {}),
      ...(f.model ? { model: f.model } : {}),
      ...(f.api_key_fingerprint ? { api_key_fingerprint: f.api_key_fingerprint } : {}),
      ...(f.task_category ? { task_category: f.task_category } : {}),
    }).toString()
    const data = await fetchJson('execution', `/api/v1/admin/usage/api-tokens/events?${query}`)
    apiSettingState.platformUsage.events = data?.items || data?.events || []
    apiSettingState.platformUsage.eventsTotal = data?.total || 0
  }

  async function loadPlatformBudgets() {
    if (!isExecutionLoggedIn.value) {
      apiSettingState.platformUsage.budgets = []
      return
    }
    const data = await fetchJson('execution', '/api/v1/admin/usage/api-tokens/budgets')
    apiSettingState.platformUsage.budgets = data?.items || data?.budgets || []
  }

  async function upsertPlatformBudget() {
    if (!isExecutionLoggedIn.value) return
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      const b = apiSettingState.budgetForm
      await fetchJson('execution', '/api/v1/admin/usage/api-tokens/budgets', {
        method: 'PUT',
        body: {
          ...(b.budget_id ? { budget_id: b.budget_id } : {}),
          scope_type: b.scope_type || 'model',
          scope_value: (b.scope_value || '').trim(),
          daily_token_limit: Number(b.daily_token_limit) || 0,
          daily_cost_limit: Number(b.daily_cost_limit) || 0,
          enabled: !!b.enabled,
        },
      })
      ui.message = '预算规则已保存'
      await loadPlatformBudgets()
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function deletePlatformBudget(budget) {
    const budgetId = budget?.budget_id || budget?.budgetId || budget?.id
    if (!budgetId || !isExecutionLoggedIn.value) return
    if (!window.confirm(`确认删除预算 ${budgetId} ?`)) return
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      await fetchJson('execution', `/api/v1/admin/usage/api-tokens/budgets/${encodeURIComponent(String(budgetId))}`, {
        method: 'DELETE',
      })
      await loadPlatformBudgets()
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function evaluatePlatformBudgets() {
    if (!isExecutionLoggedIn.value) return
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      await fetchJson('execution', '/api/v1/admin/usage/api-tokens/budgets/evaluate', {
        method: 'POST',
        body: {},
      })
      ui.message = '已触发预算评估'
      await loadPlatformAlerts()
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function loadPlatformAlerts() {
    if (!isExecutionLoggedIn.value) {
      apiSettingState.platformUsage.alerts = []
      return
    }
    const f = apiSettingState.filters.platformAlerts
    const query = new URLSearchParams({
      days: String(Number(f.days) || 30),
      status: f.status || 'OPEN',
      limit: String(Number(f.limit) || 200),
    }).toString()
    const data = await fetchJson('execution', `/api/v1/admin/usage/api-tokens/alerts?${query}`)
    apiSettingState.platformUsage.alerts = data?.items || data?.alerts || []
  }

  async function loadPricingVersions() {
    if (!isExecutionLoggedIn.value) {
      apiSettingState.platformUsage.pricingVersions = []
      return
    }
    const f = apiSettingState.filters.pricing
    const query = new URLSearchParams({
      ...(f.model ? { model: f.model } : {}),
      limit: String(Number(f.limit) || 200),
    }).toString()
    const data = await fetchJson('execution', `/api/v1/admin/usage/api-pricing/versions?${query}`)
    apiSettingState.platformUsage.pricingVersions = data?.items || []
  }

  async function upsertPricingVersion() {
    if (!isExecutionLoggedIn.value) return
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      const p = apiSettingState.pricingForm
      await fetchJson('execution', '/api/v1/admin/usage/api-pricing/versions', {
        method: 'PUT',
        body: {
          tenant_id: (p.tenant_id || '*').trim() || '*',
          model: (p.model || '*').trim() || '*',
          input_per_1k: Number(p.input_per_1k) || 0,
          output_per_1k: Number(p.output_per_1k) || 0,
          currency: (p.currency || 'USD').trim() || 'USD',
          effective_from: (p.effective_from || '').trim() || null,
          note: (p.note || '').trim(),
        },
      })
      ui.message = '定价版本已保存'
      await loadPricingVersions()
      await loadReconciliationPreview()
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function deletePricingVersion(item) {
    const versionId = item?.version_id || item?.versionId || item?.id
    if (!versionId || !isExecutionLoggedIn.value) return
    if (!window.confirm(`确认删除定价版本 ${versionId} ?`)) return
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      await fetchJson('execution', `/api/v1/admin/usage/api-pricing/versions/${encodeURIComponent(String(versionId))}`, {
        method: 'DELETE',
      })
      await loadPricingVersions()
      await loadReconciliationPreview()
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function loadReconciliationPreview() {
    if (!isExecutionLoggedIn.value) {
      apiSettingState.platformUsage.reconciliation = null
      apiSettingState.platformUsage.reconciliationBreakdown = []
      return
    }
    const f = apiSettingState.filters.reconciliation
    const query = new URLSearchParams({
      days: String(Number(f.days) || 30),
      tenant_scope: f.tenant_scope || 'current',
      limit: String(Number(f.limit) || 200),
    }).toString()
    const data = await fetchJson('execution', `/api/v1/admin/usage/api-tokens/reconciliation/preview?${query}`)
    apiSettingState.platformUsage.reconciliation = data || null
    await loadReconciliationBreakdown()
  }

  async function loadReconciliationBreakdown() {
    if (!isExecutionLoggedIn.value) {
      apiSettingState.platformUsage.reconciliationBreakdown = []
      return
    }
    const f = apiSettingState.filters.reconciliation
    const query = new URLSearchParams({
      days: String(Number(f.days) || 30),
      tenant_scope: f.tenant_scope || 'current',
      dimension: f.dimension || 'endpoint',
      limit: String(Number(f.limit) || 200),
    }).toString()
    const data = await fetchJson('execution', `/api/v1/admin/usage/api-tokens/reconciliation/breakdown?${query}`)
    apiSettingState.platformUsage.reconciliationBreakdown = data?.items || []
  }

  async function evaluateReconciliationAlerts() {
    if (!isExecutionLoggedIn.value) return
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      const f = apiSettingState.filters.reconciliation
      await fetchJson('execution', '/api/v1/admin/usage/api-tokens/reconciliation/alerts/evaluate', {
        method: 'POST',
        body: {
          days: Number(f.days) || 30,
          dimension: f.dimension || 'endpoint',
          limit: Number(f.limit) || 200,
        },
      })
      ui.message = '已触发对账偏差告警评估'
      await loadReconciliationAlerts()
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function loadReconciliationAlerts() {
    if (!isExecutionLoggedIn.value) {
      apiSettingState.platformUsage.reconciliationAlerts = []
      return
    }
    const f = apiSettingState.filters.reconciliationAlerts
    const query = new URLSearchParams({
      days: String(Number(f.days) || 30),
      status: f.status || 'OPEN',
      ...(f.dimension ? { dimension: f.dimension } : {}),
      limit: String(Number(f.limit) || 200),
    }).toString()
    const data = await fetchJson('execution', `/api/v1/admin/usage/api-tokens/reconciliation/alerts?${query}`)
    apiSettingState.platformUsage.reconciliationAlerts = data?.items || []
  }

  async function updateReconciliationAlertStatus(alert, status = 'RESOLVED') {
    const alertId = alert?.alert_id || alert?.alertId || alert?.id
    if (!alertId || !isExecutionLoggedIn.value) return
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      await fetchJson('execution', `/api/v1/admin/usage/api-tokens/reconciliation/alerts/${encodeURIComponent(String(alertId))}`, {
        method: 'PATCH',
        body: { status },
      })
      await loadReconciliationAlerts()
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function loadApiKeyLifecycle() {
    if (!isExecutionLoggedIn.value) {
      apiSettingState.platformUsage.keyLifecycle = []
      apiSettingState.platformUsage.keyLifecycleSummary = null
      return
    }
    const f = apiSettingState.filters.keyLifecycle
    const query = new URLSearchParams({
      pool: f.pool || 'all',
      tenant_scope: f.tenant_scope || 'current',
      near_expiry_days: String(Number(f.near_expiry_days) || 7),
      expiring_only: f.expiring_only ? 'true' : 'false',
      limit: String(Number(f.limit) || 200),
      ...(f.task ? { task: f.task } : {}),
    }).toString()
    const data = await fetchJson('execution', `/api/v1/admin/api-keys/lifecycle?${query}`)
    apiSettingState.platformUsage.keyLifecycle = data?.items || []
    apiSettingState.platformUsage.keyLifecycleSummary = data?.summary || null
  }

  async function rotateApiKey() {
    if (!isExecutionLoggedIn.value) return
    const rf = apiSettingState.keyRotateForm
    const endpointId = String(rf.endpoint_id || '').trim()
    const newApiKey = String(rf.new_api_key || '').trim()
    if (!endpointId || !newApiKey) {
      apiSettingState.error = 'endpoint_id 与 new_api_key 不能为空'
      return
    }
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      await fetchJson(
        'execution',
        `/api/v1/admin/api-keys/${encodeURIComponent(endpointId)}/rotate?pool=${encodeURIComponent(rf.pool || 'main')}`,
        {
          method: 'POST',
          body: {
            new_api_key: newApiKey,
            expires_at: String(rf.expires_at || '').trim() || null,
            note: String(rf.note || '').trim(),
          },
        }
      )
      apiSettingState.keyRotateForm.new_api_key = ''
      ui.message = `Key 已轮转：${endpointId}`
      await Promise.all([loadApiPools(), loadApiKeyLifecycle()])
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function evaluateApiKeyLifecycleAlerts() {
    if (!isExecutionLoggedIn.value) return
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      const f = apiSettingState.filters.keyLifecycle
      await fetchJson('execution', '/api/v1/admin/api-keys/lifecycle/alerts/evaluate', {
        method: 'POST',
        body: {
          pool: f.pool || 'all',
          task: f.task || '',
          tenant_scope: f.tenant_scope || 'current',
          near_expiry_days: Number(f.near_expiry_days) || 7,
          expiring_only: !!f.expiring_only,
          limit: Number(f.limit) || 200,
          warn_days: String(apiSettingState.keyLifecycleAlertEval.warn_days || '').trim() || undefined,
        },
      })
      ui.message = '已触发 Key 生命周期告警评估'
      await Promise.all([loadApiKeyLifecycleAlerts(), loadApiKeyLifecycleAlertTrend(), loadApiKeyLifecycleAlertSla()])
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function loadApiKeyLifecycleAlerts() {
    if (!isExecutionLoggedIn.value) {
      apiSettingState.platformUsage.keyLifecycleAlerts = []
      return
    }
    const f = apiSettingState.filters.keyLifecycleAlerts
    const query = new URLSearchParams({
      days: String(Number(f.days) || 30),
      status: f.status || 'OPEN',
      limit: String(Number(f.limit) || 200),
      ...(f.pool ? { pool: f.pool } : {}),
      ...(f.level ? { level: f.level } : {}),
    }).toString()
    const data = await fetchJson('execution', `/api/v1/admin/api-keys/lifecycle/alerts?${query}`)
    apiSettingState.platformUsage.keyLifecycleAlerts = data?.items || []
  }

  async function loadApiKeyLifecycleAlertTrend() {
    if (!isExecutionLoggedIn.value) {
      apiSettingState.platformUsage.keyLifecycleAlertTrend = []
      return
    }
    const f = apiSettingState.filters.keyLifecycleAlertTrend
    const query = new URLSearchParams({
      days: String(Number(f.days) || 30),
    }).toString()
    const data = await fetchJson('execution', `/api/v1/admin/api-keys/lifecycle/alerts/trend?${query}`)
    apiSettingState.platformUsage.keyLifecycleAlertTrend = data?.items || []
  }

  async function loadApiKeyLifecycleAlertSla() {
    if (!isExecutionLoggedIn.value) {
      apiSettingState.platformUsage.keyLifecycleAlertSla = null
      return
    }
    const f = apiSettingState.filters.keyLifecycleAlertSla
    const query = new URLSearchParams({
      days: String(Number(f.days) || 30),
      limit: String(Number(f.limit) || 20),
    }).toString()
    const data = await fetchJson('execution', `/api/v1/admin/api-keys/lifecycle/alerts/sla?${query}`)
    apiSettingState.platformUsage.keyLifecycleAlertSla = data || null
  }

  async function updateApiKeyLifecycleAlertStatus(alert, status = 'RESOLVED') {
    const alertId = alert?.alert_id || alert?.alertId || alert?.id
    if (!alertId || !isExecutionLoggedIn.value) return
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      await fetchJson('execution', `/api/v1/admin/api-keys/lifecycle/alerts/${encodeURIComponent(String(alertId))}`, {
        method: 'PATCH',
        body: { status },
      })
      await Promise.all([loadApiKeyLifecycleAlerts(), loadApiKeyLifecycleAlertTrend(), loadApiKeyLifecycleAlertSla()])
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function updatePlatformAlertStatus(alert, status = 'RESOLVED') {
    const alertId = alert?.alert_id || alert?.alertId || alert?.id
    if (!alertId || !isExecutionLoggedIn.value) return
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      await fetchJson('execution', `/api/v1/admin/usage/api-tokens/alerts/${encodeURIComponent(String(alertId))}`, {
        method: 'PATCH',
        body: { status },
      })
      await loadPlatformAlerts()
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  function loadPlatformBudgetToForm(budget) {
    apiSettingState.budgetForm.budget_id = String(budget?.budget_id || budget?.budgetId || budget?.id || '')
    apiSettingState.budgetForm.scope_type = budget?.scope_type || budget?.scopeType || 'model'
    apiSettingState.budgetForm.scope_value = budget?.scope_value || budget?.scopeValue || ''
    apiSettingState.budgetForm.daily_token_limit = Number(budget?.daily_token_limit ?? budget?.dailyTokenLimit ?? 0)
    apiSettingState.budgetForm.daily_cost_limit = Number(budget?.daily_cost_limit ?? budget?.dailyCostLimit ?? 0)
    apiSettingState.budgetForm.enabled = Boolean(budget?.enabled ?? true)
  }

  function resetPlatformBudgetForm() {
    apiSettingState.budgetForm.budget_id = ''
    apiSettingState.budgetForm.scope_type = 'model'
    apiSettingState.budgetForm.scope_value = ''
    apiSettingState.budgetForm.daily_token_limit = 200000
    apiSettingState.budgetForm.daily_cost_limit = 20
    apiSettingState.budgetForm.enabled = true
  }

  function endpointToForm(item, kind) {
    apiSettingState.editKind = kind
    apiSettingState.form.endpoint_id = endpointIdOf(item)
    apiSettingState.form.api_instance_id = item?.api_instance_id || item?.apiInstanceId || endpointIdOf(item)
    apiSettingState.form.api_instance_name = item?.api_instance_name || item?.apiInstanceName || item?.name || ''
    apiSettingState.form.api_instance_remark = item?.api_instance_remark || item?.apiInstanceRemark || ''
    apiSettingState.form.name = item?.name || ''
    apiSettingState.form.provider = item?.provider || 'openai_compatible'
    apiSettingState.form.base_url = item?.base_url || item?.baseUrl || ''
    apiSettingState.form.model = item?.model || ''
    apiSettingState.form.task = item?.task || ''
    apiSettingState.form.api_key = ''
    apiSettingState.form.tenant_id = item?.tenant_id || item?.tenantId || '*'
    apiSettingState.form.enabled = Boolean(item?.enabled ?? true)
    apiSettingState.form.frozen = Boolean(item?.frozen ?? false)
    apiSettingState.form.priority = Number(item?.priority ?? 100)
    apiSettingState.form.weight = Number(item?.weight ?? 100)
    apiSettingState.form.max_concurrency = Number(item?.max_concurrency ?? item?.maxConcurrency ?? 8)
    apiSettingState.form.timeout_seconds = Number(item?.timeout_seconds ?? item?.timeoutSeconds ?? 120)
    apiSettingState.form.retry_max = Number(item?.retry_max ?? item?.retryMax ?? 2)
    apiSettingState.form.circuit_fail_threshold = Number(item?.circuit_fail_threshold ?? item?.circuitFailThreshold ?? 5)
    apiSettingState.form.circuit_open_seconds = Number(item?.circuit_open_seconds ?? item?.circuitOpenSeconds ?? 30)
    apiSettingState.form.input_per_1k = Number(item?.input_per_1k ?? 0)
    apiSettingState.form.output_per_1k = Number(item?.output_per_1k ?? 0)
  }

  function resetApiForm(kind = apiSettingState.editKind) {
    apiSettingState.editKind = kind
    apiSettingState.form.endpoint_id = ''
    apiSettingState.form.api_instance_id = ''
    apiSettingState.form.api_instance_name = ''
    apiSettingState.form.api_instance_remark = ''
    apiSettingState.form.name = ''
    apiSettingState.form.provider = 'openai_compatible'
    apiSettingState.form.base_url = ''
    apiSettingState.form.model = ''
    apiSettingState.form.task = ''
    apiSettingState.form.api_key = ''
    apiSettingState.form.tenant_id = '*'
    apiSettingState.form.enabled = true
    apiSettingState.form.frozen = false
    apiSettingState.form.priority = 100
    apiSettingState.form.weight = 100
    apiSettingState.form.max_concurrency = 8
    apiSettingState.form.timeout_seconds = 120
    apiSettingState.form.retry_max = 2
    apiSettingState.form.circuit_fail_threshold = 5
    apiSettingState.form.circuit_open_seconds = 30
    apiSettingState.form.input_per_1k = 0
    apiSettingState.form.output_per_1k = 0
  }

  function apiPoolContext(kind) {
    if (kind === 'main') return { service: 'execution', basePath: '/api/v1/admin/api-pool' }
    if (kind === 'aux') return { service: 'execution', basePath: '/api/v1/admin/aux-api-pool' }
    return { service: 'chat', basePath: '/api/v1/admin/chat/api-pool' }
  }

  async function upsertApiEndpoint() {
    const form = apiSettingState.form
    const id = form.endpoint_id.trim()
    if (!id) return
    const apiKey = String(form.api_key || '').trim()
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      const { service, basePath } = apiPoolContext(apiSettingState.editKind)
      const body = {
        endpoint_id: id,
        api_instance_id: form.api_instance_id?.trim() || id,
        api_instance_name: form.api_instance_name?.trim() || form.name.trim() || id,
        api_instance_remark: form.api_instance_remark?.trim() || '',
        name: form.name.trim() || id,
        provider: form.provider,
        base_url: form.base_url.trim(),
        model: form.model.trim(),
        task: form.task.trim(),
        tenant_id: form.tenant_id.trim() || '*',
        enabled: !!form.enabled,
        frozen: !!form.frozen,
        priority: Number(form.priority) || 100,
        weight: Number(form.weight) || 100,
        max_concurrency: Number(form.max_concurrency) || 8,
        timeout_seconds: Number(form.timeout_seconds) || 120,
        retry_max: Number(form.retry_max) || 2,
        circuit_fail_threshold: Number(form.circuit_fail_threshold) || 5,
        circuit_open_seconds: Number(form.circuit_open_seconds) || 30,
        input_per_1k: Number(form.input_per_1k) || 0,
        output_per_1k: Number(form.output_per_1k) || 0,
      }
      if (apiKey) body.api_key = apiKey
      const saved = await fetchJson(service, basePath, {
        method: 'POST',
        body,
      })
      if (apiKey && !String(saved?.item?.key_fingerprint || '').trim()) {
        throw new Error('后端未返回 API Key 指纹，Key 可能未成功保存')
      }
      ui.message = `已保存端点：${id}`
      await loadApiPools()
      return saved
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
      return null
    } finally {
      apiSettingState.loading = false
    }
  }

  async function deleteApiEndpoint(kind, endpointId, options = {}) {
    if (!endpointId) return
    if (!options?.skipConfirm && !window.confirm(`确认删除端点 ${endpointId} ?`)) return
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      const { service, basePath } = apiPoolContext(kind)
      await fetchJson(service, `${basePath}/${encodeURIComponent(endpointId)}`, { method: 'DELETE' })
      if (!options?.silent) ui.message = `已删除端点：${endpointId}`
      if (apiSettingState.form.endpoint_id === endpointId) resetApiForm(kind)
      if (!options?.deferReload) await loadApiPools()
      return true
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
      return false
    } finally {
      apiSettingState.loading = false
    }
  }

  async function setDefaultApiEndpoint(kind, endpointId, task = '') {
    if (!endpointId) return
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      const { service, basePath } = apiPoolContext(kind)
      const query = kind === 'chat' && task ? `?task=${encodeURIComponent(task)}` : ''
      await fetchJson(service, `${basePath}/${encodeURIComponent(endpointId)}/set-default${query}`, {
        method: 'POST',
        body: {},
      })
      ui.message = `默认端点已更新：${endpointId}`
      await loadApiPools()
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function toggleFreezeChatEndpoint(endpointId, frozen) {
    if (!endpointId) return
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      const action = frozen ? 'unfreeze' : 'freeze'
      await fetchJson('chat', `/api/v1/admin/chat/api-pool/${encodeURIComponent(endpointId)}/${action}`, {
        method: 'POST',
        body: {},
      })
      await loadApiPools()
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function probeChatApiEndpoint(endpointId) {
    const id = String(endpointId || '').trim()
    if (!id) return null
    apiSettingState.error = ''
    try {
      const data = await fetchJson('chat', `/api/v1/admin/chat/api-pool/${encodeURIComponent(id)}/probe`, {
        method: 'POST',
        body: { max_tokens: 8, timeout_seconds: 30 },
      })
      await loadChatApiInsights()
      return data?.item || null
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
      return null
    }
  }

  async function loadApiPools() {
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      await loadApiConsumerEndpoints()
      const [mainPoolResult, auxPoolResult, chatPoolResult] = await Promise.allSettled([
        isExecutionLoggedIn.value ? fetchJson('execution', '/api/v1/admin/api-pool') : Promise.resolve({ items: [] }),
        isExecutionLoggedIn.value ? fetchJson('execution', '/api/v1/admin/aux-api-pool') : Promise.resolve({ items: [] }),
        fetchJson('chat', '/api/v1/admin/chat/api-pool'),
      ])
      if (mainPoolResult.status === 'fulfilled') apiSettingState.mainPool = mainPoolResult.value?.items || []
      if (auxPoolResult.status === 'fulfilled') apiSettingState.auxPool = auxPoolResult.value?.items || []
      if (chatPoolResult.status === 'fulfilled') apiSettingState.chatPool = chatPoolResult.value?.items || []
      if (chatPoolResult.status === 'rejected') throw chatPoolResult.reason

      await Promise.allSettled([
        loadApiObservability(),
        loadPlatformUsageSummary(),
        loadPlatformUsageEvents(),
        loadPlatformBudgets(),
        loadPlatformAlerts(),
        loadPricingVersions(),
        loadReconciliationPreview(),
        loadReconciliationAlerts(),
        loadApiKeyLifecycle(),
        loadApiKeyLifecycleAlerts(),
        loadApiKeyLifecycleAlertTrend(),
        loadApiKeyLifecycleAlertSla(),
      ])
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function loadApiObservability() {
    try {
      const af = apiSettingState.filters.alerts
      const lf = apiSettingState.filters.logs
      const alertQuery = new URLSearchParams({
        limit: '50',
        ...(af.rule_id ? { rule_id: af.rule_id } : {}),
        ...(af.severity ? { severity: af.severity } : {}),
        ...(af.acked !== '' ? { acked: af.acked } : {}),
      }).toString()
      const logQuery = new URLSearchParams({
        group_by: 'tenant_id,task,endpoint_id,model',
        limit: '50',
        sort_by: 'request_count',
        sort_dir: 'desc',
        ...(lf.tenant_id ? { tenant_id: lf.tenant_id } : {}),
        ...(lf.task ? { task: lf.task } : {}),
        ...(lf.endpoint_id ? { endpoint_id: lf.endpoint_id } : {}),
        ...(lf.ok !== '' ? { ok: lf.ok } : {}),
      }).toString()
      const [trends, logs, alerts, rules, ruleStats] = await Promise.all([
        fetchJson('chat', '/api/v1/admin/chat/api-pool/usage/trends?limit_days=30'),
        fetchJson('chat', `/api/v1/admin/chat/api-pool/logs/grouped?${logQuery}`),
        fetchJson('chat', `/api/v1/admin/chat/api-pool/alerts/events?${alertQuery}`),
        fetchJson('chat', '/api/v1/admin/chat/api-pool/alerts/rules'),
        fetchJson('chat', '/api/v1/admin/chat/api-pool/alerts/rules/stats?limit=200'),
      ])
      apiSettingState.usageTrends = trends?.items || trends?.trends || []
      apiSettingState.logGrouped = logs?.items || logs?.groups || []
      apiSettingState.alertEvents = alerts?.items || alerts?.events || []
      apiSettingState.alertRules = rules?.items || rules?.rules || []
      apiSettingState.alertRuleStats = ruleStats?.items || ruleStats?.stats || []
      await loadApiUsageGrouped()
      await loadChatApiInsights()
    } catch {
      apiSettingState.usageTrends = []
      apiSettingState.usageGrouped = []
      apiSettingState.logGrouped = []
      apiSettingState.alertEvents = []
      apiSettingState.alertRules = []
      apiSettingState.alertRuleStats = []
      apiSettingState.chatApiInsights.health = []
      apiSettingState.chatApiInsights.overview = null
      apiSettingState.chatApiInsights.dashboard = null
      apiSettingState.chatApiInsights.insights = []
      apiSettingState.chatApiInsights.usageDaily = []
    }
  }

  async function loadChatApiInsights() {
    const f = apiSettingState.filters.usage
    const day_from = f.day_from || ''
    const day_to = f.day_to || ''
    const tenant_id = f.tenant_id || ''
    const task = f.task || ''
    const endpoint_id = f.endpoint_id || ''
    const model = f.model || ''
    const pDashboard = new URLSearchParams({
      ...(day_from ? { day_from } : {}),
      ...(day_to ? { day_to } : {}),
      ...(tenant_id ? { tenant_id } : {}),
      ...(task ? { task } : {}),
      ...(endpoint_id ? { endpoint_id } : {}),
      ...(model ? { model } : {}),
    }).toString()
    const pInsights = new URLSearchParams({
      ...(day_from ? { day_from } : {}),
      ...(day_to ? { day_to } : {}),
      limit: '10',
    }).toString()
    const pDaily = new URLSearchParams({
      ...(day_from ? { day_from } : {}),
      ...(day_to ? { day_to } : {}),
      ...(tenant_id ? { tenant_id } : {}),
      ...(task ? { task } : {}),
      ...(endpoint_id ? { endpoint_id } : {}),
      ...(model ? { model } : {}),
      limit: '200',
    }).toString()
    const [health, overview, dashboard, insights, daily] = await Promise.all([
      fetchJson('chat', '/api/v1/admin/chat/api-pool/health'),
      fetchJson('chat', '/api/v1/admin/chat/api-pool/overview'),
      fetchJson('chat', `/api/v1/admin/chat/api-pool/dashboard${pDashboard ? `?${pDashboard}` : ''}`),
      fetchJson('chat', `/api/v1/admin/chat/api-pool/insights${pInsights ? `?${pInsights}` : ''}`),
      fetchJson('chat', `/api/v1/admin/chat/api-pool/usage/daily${pDaily ? `?${pDaily}` : ''}`),
    ])
    apiSettingState.chatApiInsights.health = health?.items || health?.endpoints || []
    apiSettingState.chatApiInsights.overview = overview || null
    apiSettingState.chatApiInsights.dashboard = dashboard || null
    apiSettingState.chatApiInsights.insights = insights?.items || insights?.tops || []
    apiSettingState.chatApiInsights.usageDaily = daily?.items || daily?.days || []
  }

  async function callApiWorkbench() {
    const w = apiSettingState.debugWorkbench
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      const service = w.service || 'chat'
      const method = (w.method || 'GET').toUpperCase()
      const path = String(w.path || '').trim()
      if (!path.startsWith('/')) throw new Error('path 必须以 / 开头')
      const queryText = String(w.queryText || '').trim()
      const fullPath = queryText ? `${path}?${queryText}` : path
      let body = undefined
      if (!['GET', 'HEAD'].includes(method)) {
        const raw = String(w.bodyText || '').trim()
        body = raw ? JSON.parse(raw) : {}
      }
      const data = await fetchJson(service, fullPath, {
        method,
        ...(body !== undefined ? { body } : {}),
      })
      w.responseStatus = 'OK'
      w.responseText = JSON.stringify(data ?? {}, null, 2)
    } catch (error) {
      w.responseStatus = 'ERROR'
      const msg = error instanceof Error ? error.message : String(error)
      w.responseText = msg
      apiSettingState.error = msg
    } finally {
      apiSettingState.loading = false
    }
  }

  async function loadApiUsageGrouped() {
    const page = Number(apiSettingState.usagePage) || 1
    const pageSize = Number(apiSettingState.usagePageSize) || 50
    const f = apiSettingState.filters.usage
    const query = new URLSearchParams({
      group_by: 'tenant,task,model,endpoint_id',
      sort_by: 'request_count',
      sort_dir: 'desc',
      page: String(page),
      page_size: String(pageSize),
      ...(f.day_from ? { day_from: f.day_from } : {}),
      ...(f.day_to ? { day_to: f.day_to } : {}),
      ...(f.tenant_id ? { tenant_id: f.tenant_id } : {}),
      ...(f.task ? { task: f.task } : {}),
      ...(f.endpoint_id ? { endpoint_id: f.endpoint_id } : {}),
      ...(f.model ? { model: f.model } : {}),
    }).toString()
    const data = await fetchJson('chat', `/api/v1/admin/chat/api-pool/usage/grouped?${query}`)
    apiSettingState.usageGrouped = data?.items || data?.groups || []
    apiSettingState.usageTotal = data?.total || 0
  }

  async function exportApiUsage(fmt = 'csv') {
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      const f = apiSettingState.filters.usage
      const query = new URLSearchParams({
        fmt,
        limit: '2000',
        ...(f.day_from ? { day_from: f.day_from } : {}),
        ...(f.day_to ? { day_to: f.day_to } : {}),
        ...(f.tenant_id ? { tenant_id: f.tenant_id } : {}),
        ...(f.task ? { task: f.task } : {}),
        ...(f.endpoint_id ? { endpoint_id: f.endpoint_id } : {}),
        ...(f.model ? { model: f.model } : {}),
      }).toString()
      const resp = await fetchRaw('chat', `/api/v1/admin/chat/api-pool/export/usage?${query}`)
      const text = await resp.text()
      const filename = `chat-api-usage-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.${fmt}`
      const ct = fmt === 'csv' ? 'text/csv;charset=utf-8' : 'application/json;charset=utf-8'
      downloadBlob(text, filename, ct)
      ui.message = '用量导出完成'
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function exportApiLogs(fmt = 'csv') {
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      const f = apiSettingState.filters.logs
      const query = new URLSearchParams({
        fmt,
        limit: '2000',
        ...(f.tenant_id ? { tenant_id: f.tenant_id } : {}),
        ...(f.task ? { task: f.task } : {}),
        ...(f.endpoint_id ? { endpoint_id: f.endpoint_id } : {}),
        ...(f.ok !== '' ? { ok: f.ok } : {}),
      }).toString()
      const resp = await fetchRaw('chat', `/api/v1/admin/chat/api-pool/export/logs?${query}`)
      const text = await resp.text()
      const filename = `chat-api-logs-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.${fmt}`
      const ct = fmt === 'csv' ? 'text/csv;charset=utf-8' : 'application/json;charset=utf-8'
      downloadBlob(text, filename, ct)
      ui.message = '日志导出完成'
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  function exportReconciliationBreakdownCsv() {
    const rows = apiSettingState.platformUsage.reconciliationBreakdown || []
    const header = ['dimension_value', 'calls', 'input_tokens', 'output_tokens', 'total_tokens', 'estimated_cost_usd', 'priced_cost_usd', 'diff_cost_usd']
    const lines = [header.join(',')]
    for (const one of rows) {
      const est = Number(one?.estimated_cost_usd || 0)
      const priced = Number(one?.priced_cost_usd || 0)
      const diff = priced - est
      const vals = [
        String(one?.dim_value ?? '').replaceAll(',', ' '),
        String(one?.calls ?? 0),
        String(one?.input_tokens ?? 0),
        String(one?.output_tokens ?? 0),
        String(one?.total_tokens ?? 0),
        String(est),
        String(priced),
        String(diff),
      ]
      lines.push(vals.join(','))
    }
    const text = lines.join('\n')
    const ts = new Date().toISOString().replace(/[:.]/g, '-')
    downloadBlob(text, `reconciliation-breakdown-${ts}.csv`, 'text/csv;charset=utf-8')
  }

  function loadAlertRuleToForm(rule) {
    apiSettingState.alertRuleForm.rule_id = rule?.rule_id || rule?.ruleId || ''
    apiSettingState.alertRuleForm.name = rule?.name || ''
    apiSettingState.alertRuleForm.enabled = Boolean(rule?.enabled ?? true)
    apiSettingState.alertRuleForm.metric = rule?.metric || 'daily_cost_total'
    apiSettingState.alertRuleForm.scope = rule?.scope || 'global'
    apiSettingState.alertRuleForm.tenant_id = rule?.tenant_id || '*'
    apiSettingState.alertRuleForm.task = rule?.task || ''
    apiSettingState.alertRuleForm.endpoint_id = rule?.endpoint_id || ''
    apiSettingState.alertRuleForm.model = rule?.model || ''
    apiSettingState.alertRuleForm.threshold = Number(rule?.threshold ?? 20)
    apiSettingState.alertRuleForm.window_days = Number(rule?.window_days ?? 1)
    apiSettingState.alertRuleForm.comparison = rule?.comparison || 'gte'
    apiSettingState.alertRuleForm.severity = rule?.severity || 'warning'
    apiSettingState.alertRuleForm.cooldown_seconds = Number(rule?.cooldown_seconds ?? 600)
  }

  function resetAlertRuleForm() {
    apiSettingState.alertRuleForm.rule_id = ''
    apiSettingState.alertRuleForm.name = ''
    apiSettingState.alertRuleForm.enabled = true
    apiSettingState.alertRuleForm.metric = 'daily_cost_total'
    apiSettingState.alertRuleForm.scope = 'global'
    apiSettingState.alertRuleForm.tenant_id = '*'
    apiSettingState.alertRuleForm.task = ''
    apiSettingState.alertRuleForm.endpoint_id = ''
    apiSettingState.alertRuleForm.model = ''
    apiSettingState.alertRuleForm.threshold = 20
    apiSettingState.alertRuleForm.window_days = 1
    apiSettingState.alertRuleForm.comparison = 'gte'
    apiSettingState.alertRuleForm.severity = 'warning'
    apiSettingState.alertRuleForm.cooldown_seconds = 600
  }

  async function upsertAlertRule() {
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      const r = apiSettingState.alertRuleForm
      await fetchJson('chat', '/api/v1/admin/chat/api-pool/alerts/rules', {
        method: 'POST',
        body: {
          rule_id: r.rule_id || undefined,
          name: r.name || 'unnamed rule',
          enabled: !!r.enabled,
          metric: r.metric,
          scope: r.scope,
          tenant_id: r.tenant_id || '*',
          task: r.task || '',
          endpoint_id: r.endpoint_id || '',
          model: r.model || '',
          threshold: Number(r.threshold) || 0,
          window_days: Number(r.window_days) || 1,
          comparison: r.comparison || 'gte',
          severity: r.severity || 'warning',
          cooldown_seconds: Number(r.cooldown_seconds) || 600,
        },
      })
      ui.message = '告警规则已保存'
      resetAlertRuleForm()
      await loadApiObservability()
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function deleteAlertRule(rule) {
    const ruleId = rule?.rule_id || rule?.ruleId
    if (!ruleId) return
    if (!window.confirm(`确认删除告警规则 ${ruleId} ?`)) return
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      await fetchJson('chat', `/api/v1/admin/chat/api-pool/alerts/rules/${encodeURIComponent(ruleId)}`, {
        method: 'DELETE',
      })
      await loadApiObservability()
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function evaluateAlertRules() {
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      await fetchJson('chat', '/api/v1/admin/chat/api-pool/alerts/evaluate', { method: 'POST', body: {} })
      ui.message = '已触发告警评估'
      await loadApiObservability()
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  async function ackAlertEvent(event) {
    const eventId = event?.event_id || event?.eventId
    if (!eventId) return
    apiSettingState.loading = true
    apiSettingState.error = ''
    try {
      await fetchJson('chat', `/api/v1/admin/chat/api-pool/alerts/events/${encodeURIComponent(eventId)}/ack`, {
        method: 'POST',
        body: { actor: cfg.control.username || 'admin' },
      })
      await loadApiObservability()
    } catch (error) {
      apiSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      apiSettingState.loading = false
    }
  }

  return {
    loadApiConsumerEndpoints,
    endpointToForm,
    resetApiForm,
    upsertApiEndpoint,
    deleteApiEndpoint,
    setDefaultApiEndpoint,
    toggleFreezeChatEndpoint,
    probeChatApiEndpoint,
    loadApiPools,
    loadApiObservability,
    loadChatApiInsights,
    callApiWorkbench,
    loadApiUsageGrouped,
    exportApiUsage,
    exportApiLogs,
    loadAlertRuleToForm,
    resetAlertRuleForm,
    upsertAlertRule,
    deleteAlertRule,
    evaluateAlertRules,
    ackAlertEvent,
    loadPlatformUsageSummary,
    loadPlatformUsageEvents,
    loadPlatformBudgets,
    upsertPlatformBudget,
    deletePlatformBudget,
    evaluatePlatformBudgets,
    loadPlatformAlerts,
    updatePlatformAlertStatus,
    loadPlatformBudgetToForm,
    resetPlatformBudgetForm,
    loadPricingVersions,
    upsertPricingVersion,
    deletePricingVersion,
    loadReconciliationPreview,
    loadReconciliationBreakdown,
    evaluateReconciliationAlerts,
    loadReconciliationAlerts,
    updateReconciliationAlertStatus,
    loadApiKeyLifecycle,
    rotateApiKey,
    evaluateApiKeyLifecycleAlerts,
    loadApiKeyLifecycleAlerts,
    loadApiKeyLifecycleAlertTrend,
    loadApiKeyLifecycleAlertSla,
    updateApiKeyLifecycleAlertStatus,
    exportReconciliationBreakdownCsv,
  }
}
