<script setup>
defineProps({
  apiSettingState: { type: Object, required: true },
  loadPlatformUsageSummary: { type: Function, required: true },
  loadPlatformUsageEvents: { type: Function, required: true },
  loadPlatformBudgets: { type: Function, required: true },
  upsertPlatformBudget: { type: Function, required: true },
  deletePlatformBudget: { type: Function, required: true },
  evaluatePlatformBudgets: { type: Function, required: true },
  loadPlatformAlerts: { type: Function, required: true },
  updatePlatformAlertStatus: { type: Function, required: true },
  loadPlatformBudgetToForm: { type: Function, required: true },
  resetPlatformBudgetForm: { type: Function, required: true },
  loadPricingVersions: { type: Function, required: true },
  upsertPricingVersion: { type: Function, required: true },
  deletePricingVersion: { type: Function, required: true },
  loadReconciliationPreview: { type: Function, required: true },
  loadReconciliationBreakdown: { type: Function, required: true },
  evaluateReconciliationAlerts: { type: Function, required: true },
  loadReconciliationAlerts: { type: Function, required: true },
  updateReconciliationAlertStatus: { type: Function, required: true },
  exportReconciliationBreakdownCsv: { type: Function, required: true },
  loadApiKeyLifecycle: { type: Function, required: true },
  rotateApiKey: { type: Function, required: true },
  evaluateApiKeyLifecycleAlerts: { type: Function, required: true },
  loadApiKeyLifecycleAlerts: { type: Function, required: true },
  loadApiKeyLifecycleAlertTrend: { type: Function, required: true },
  loadApiKeyLifecycleAlertSla: { type: Function, required: true },
  updateApiKeyLifecycleAlertStatus: { type: Function, required: true },
})
</script>

<template>
  <div class="kb-col">
    <h3>平台 API 用量与预算（Execution）</h3>
    <div class="row">
      <label>days<input v-model.number="apiSettingState.filters.platformUsage.days" type="number" min="1" /></label>
      <label>tenant_scope
        <select v-model="apiSettingState.filters.platformUsage.tenant_scope">
          <option value="current">current</option>
          <option value="all">all</option>
        </select>
      </label>
      <label>limit<input v-model.number="apiSettingState.filters.platformUsage.limit" type="number" min="1" /></label>
      <button class="btn ghost" @click="loadPlatformUsageSummary">刷新聚合</button>
      <button class="btn ghost" @click="loadPlatformUsageEvents">刷新事件</button>
      <button class="btn ghost" @click="loadPlatformBudgets">刷新预算</button>
      <button class="btn ghost" @click="loadPlatformAlerts">刷新告警</button>
      <button class="btn ghost" @click="evaluatePlatformBudgets">触发预算评估</button>
    </div>

    <h3>用量聚合</h3>
    <div class="table-wrap mini">
      <table>
        <thead><tr><th>scope_type</th><th>scope_value</th><th>tokens</th><th>cost</th><th>requests</th></tr></thead>
        <tbody>
          <tr v-for="(x, idx) in apiSettingState.platformUsage.summary" :key="idx">
            <td>{{ x.scope_type || x.scopeType || '-' }}</td>
            <td>{{ x.scope_value || x.scopeValue || '-' }}</td>
            <td>{{ x.total_tokens || x.tokens || 0 }}</td>
            <td>{{ x.total_cost || x.cost || 0 }}</td>
            <td>{{ x.request_count || x.requests || 0 }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <h3>用量事件</h3>
    <div class="row">
      <label>page<input v-model.number="apiSettingState.filters.platformEvents.page" type="number" min="1" /></label>
      <label>size<input v-model.number="apiSettingState.filters.platformEvents.page_size" type="number" min="1" /></label>
      <label>api_path<input v-model="apiSettingState.filters.platformEvents.api_path" /></label>
      <label>model<input v-model="apiSettingState.filters.platformEvents.model" /></label>
      <label>task_category<input v-model="apiSettingState.filters.platformEvents.task_category" /></label>
      <button class="btn ghost" @click="loadPlatformUsageEvents">应用筛选</button>
    </div>
    <div class="table-wrap short">
      <table>
        <thead><tr><th>time</th><th>task</th><th>model</th><th>api_path</th><th>tokens</th><th>cost</th></tr></thead>
        <tbody>
          <tr v-for="(e, idx) in apiSettingState.platformUsage.events" :key="idx">
            <td>{{ e.created_at || e.createdAt || '-' }}</td>
            <td>{{ e.task_category || e.task || '-' }}</td>
            <td>{{ e.model || '-' }}</td>
            <td>{{ e.api_path || '-' }}</td>
            <td>{{ e.total_tokens || e.tokens || 0 }}</td>
            <td>{{ e.total_cost || e.cost || 0 }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="pager"><span>总记录：{{ apiSettingState.platformUsage.eventsTotal }}</span></div>

    <h3>预算规则</h3>
    <div class="grid three">
      <label>budget_id<input v-model="apiSettingState.budgetForm.budget_id" placeholder="留空新增" /></label>
      <label>scope_type
        <select v-model="apiSettingState.budgetForm.scope_type">
          <option value="global">global</option>
          <option value="tenant">tenant</option>
          <option value="task">task</option>
          <option value="model">model</option>
          <option value="endpoint">endpoint</option>
          <option value="api_key_fingerprint">api_key_fingerprint</option>
        </select>
      </label>
      <label>scope_value<input v-model="apiSettingState.budgetForm.scope_value" /></label>
    </div>
    <div class="grid three">
      <label>daily_token_limit<input v-model.number="apiSettingState.budgetForm.daily_token_limit" type="number" min="0" /></label>
      <label>daily_cost_limit<input v-model.number="apiSettingState.budgetForm.daily_cost_limit" type="number" min="0" step="0.0001" /></label>
      <label>enabled
        <select v-model="apiSettingState.budgetForm.enabled">
          <option :value="true">true</option>
          <option :value="false">false</option>
        </select>
      </label>
    </div>
    <div class="row">
      <button class="btn primary" @click="upsertPlatformBudget">保存预算</button>
      <button class="btn ghost" @click="resetPlatformBudgetForm">重置</button>
    </div>

    <div class="table-wrap mini">
      <table>
        <thead><tr><th>budget_id</th><th>scope</th><th>tokens</th><th>cost</th><th>enabled</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="b in apiSettingState.platformUsage.budgets" :key="b.budget_id || b.id">
            <td>{{ b.budget_id || b.id }}</td>
            <td>{{ (b.scope_type || '-') + ':' + (b.scope_value || '-') }}</td>
            <td>{{ b.daily_token_limit ?? '-' }}</td>
            <td>{{ b.daily_cost_limit ?? '-' }}</td>
            <td>{{ b.enabled ? 'true' : 'false' }}</td>
            <td><div class="row tight">
              <button class="btn ghost" @click="loadPlatformBudgetToForm(b)">编辑</button>
              <button class="btn ghost danger-text" @click="deletePlatformBudget(b)">删除</button>
            </div></td>
          </tr>
        </tbody>
      </table>
    </div>

    <h3>预算告警</h3>
    <div class="row">
      <label>days<input v-model.number="apiSettingState.filters.platformAlerts.days" type="number" min="1" /></label>
      <label>status
        <select v-model="apiSettingState.filters.platformAlerts.status">
          <option value="OPEN">OPEN</option>
          <option value="ACK">ACK</option>
          <option value="MUTED">MUTED</option>
          <option value="RESOLVED">RESOLVED</option>
        </select>
      </label>
      <label>limit<input v-model.number="apiSettingState.filters.platformAlerts.limit" type="number" min="1" /></label>
      <button class="btn ghost" @click="loadPlatformAlerts">应用告警筛选</button>
    </div>
    <div class="table-wrap mini">
      <table>
        <thead><tr><th>alert_id</th><th>scope</th><th>status</th><th>reason</th><th>time</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="a in apiSettingState.platformUsage.alerts" :key="a.alert_id || a.id">
            <td>{{ a.alert_id || a.id }}</td>
            <td>{{ (a.scope_type || '-') + ':' + (a.scope_value || '-') }}</td>
            <td>{{ a.status || '-' }}</td>
            <td>{{ a.reason || a.message || '-' }}</td>
            <td>{{ a.created_at || a.createdAt || '-' }}</td>
            <td><div class="row tight">
              <button class="btn ghost" @click="updatePlatformAlertStatus(a, 'ACK')">确认</button>
              <button class="btn ghost" @click="updatePlatformAlertStatus(a, 'MUTED')">静默</button>
              <button class="btn ghost" @click="updatePlatformAlertStatus(a, 'RESOLVED')">标记解决</button>
            </div></td>
          </tr>
        </tbody>
      </table>
    </div>

    <h3>定价版本与对账预览（P1-B）</h3>
    <div class="row">
      <label>model<input v-model="apiSettingState.filters.pricing.model" placeholder="留空=全部" /></label>
      <label>limit<input v-model.number="apiSettingState.filters.pricing.limit" type="number" min="1" /></label>
      <button class="btn ghost" @click="loadPricingVersions">刷新定价版本</button>
    </div>
    <div class="grid three">
      <label>tenant_id<input v-model="apiSettingState.pricingForm.tenant_id" /></label>
      <label>model<input v-model="apiSettingState.pricingForm.model" /></label>
      <label>currency<input v-model="apiSettingState.pricingForm.currency" /></label>
    </div>
    <div class="grid three">
      <label>input_per_1k<input v-model.number="apiSettingState.pricingForm.input_per_1k" type="number" min="0" step="0.000001" /></label>
      <label>output_per_1k<input v-model.number="apiSettingState.pricingForm.output_per_1k" type="number" min="0" step="0.000001" /></label>
      <label>effective_from<input v-model="apiSettingState.pricingForm.effective_from" placeholder="可空；ISO 时间" /></label>
    </div>
    <div class="row">
      <label>note<input v-model="apiSettingState.pricingForm.note" /></label>
      <button class="btn primary" @click="upsertPricingVersion">保存定价版本</button>
    </div>
    <div class="table-wrap mini">
      <table>
        <thead><tr><th>id</th><th>tenant</th><th>model</th><th>input/1k</th><th>output/1k</th><th>effective_from</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="x in apiSettingState.platformUsage.pricingVersions" :key="x.version_id || x.id">
            <td>{{ x.version_id || x.id }}</td>
            <td>{{ x.tenant_id || '-' }}</td>
            <td>{{ x.model || '-' }}</td>
            <td>{{ x.input_per_1k ?? 0 }}</td>
            <td>{{ x.output_per_1k ?? 0 }}</td>
            <td>{{ x.effective_from || '-' }}</td>
            <td><button class="btn ghost danger-text" @click="deletePricingVersion(x)">删除</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="row">
      <label>days<input v-model.number="apiSettingState.filters.reconciliation.days" type="number" min="1" /></label>
      <label>tenant_scope
        <select v-model="apiSettingState.filters.reconciliation.tenant_scope">
          <option value="current">current</option>
          <option value="all">all</option>
        </select>
      </label>
      <label>dimension
        <select v-model="apiSettingState.filters.reconciliation.dimension">
          <option value="endpoint">endpoint</option>
          <option value="task">task</option>
          <option value="day">day</option>
          <option value="api_path">api_path</option>
          <option value="model">model</option>
        </select>
      </label>
      <label>limit<input v-model.number="apiSettingState.filters.reconciliation.limit" type="number" min="1" /></label>
      <button class="btn ghost" @click="loadReconciliationPreview">刷新对账预览</button>
      <button class="btn ghost" @click="loadReconciliationBreakdown">刷新对账下钻</button>
      <button class="btn ghost" @click="evaluateReconciliationAlerts">评估偏差告警</button>
      <button class="btn ghost" @click="exportReconciliationBreakdownCsv">导出下钻 CSV</button>
    </div>
    <div class="row" v-if="apiSettingState.platformUsage.reconciliation">
      <span>估算成本：{{ apiSettingState.platformUsage.reconciliation.estimated_cost_usd ?? 0 }}</span>
      <span>定价重算：{{ apiSettingState.platformUsage.reconciliation.priced_cost_usd ?? 0 }}</span>
      <span>差值：{{ apiSettingState.platformUsage.reconciliation.diff_cost_usd ?? 0 }}</span>
      <span>差异率：{{ (((apiSettingState.platformUsage.reconciliation.diff_ratio ?? 0) * 100).toFixed?.(2) || '0.00') + '%' }}</span>
    </div>
    <div class="table-wrap mini">
      <table>
        <thead><tr><th>model</th><th>calls</th><th>estimated</th><th>priced</th></tr></thead>
        <tbody>
          <tr v-for="(x, idx) in (apiSettingState.platformUsage.reconciliation?.by_model || [])" :key="idx">
            <td>{{ x.model || '-' }}</td>
            <td>{{ x.calls || 0 }}</td>
            <td>{{ x.estimated_cost_usd || 0 }}</td>
            <td>{{ x.priced_cost_usd || 0 }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="table-wrap mini">
      <table>
        <thead><tr><th>dimension</th><th>calls</th><th>estimated</th><th>priced</th><th>diff</th></tr></thead>
        <tbody>
          <tr v-for="(x, idx) in (apiSettingState.platformUsage.reconciliationBreakdown || [])" :key="`${idx}-${x.dim_value || ''}`">
            <td>{{ x.dim_value || '-' }}</td>
            <td>{{ x.calls || 0 }}</td>
            <td>{{ x.estimated_cost_usd || 0 }}</td>
            <td>{{ x.priced_cost_usd || 0 }}</td>
            <td>{{ ((Number(x.priced_cost_usd || 0) - Number(x.estimated_cost_usd || 0)).toFixed?.(6) || '0') }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="row">
      <label>alert_days<input v-model.number="apiSettingState.filters.reconciliationAlerts.days" type="number" min="1" /></label>
      <label>alert_status
        <select v-model="apiSettingState.filters.reconciliationAlerts.status">
          <option value="OPEN">OPEN</option>
          <option value="ACK">ACK</option>
          <option value="MUTED">MUTED</option>
          <option value="RESOLVED">RESOLVED</option>
          <option value="ALL">ALL</option>
        </select>
      </label>
      <label>alert_dimension
        <select v-model="apiSettingState.filters.reconciliationAlerts.dimension">
          <option value="">all</option>
          <option value="endpoint">endpoint</option>
          <option value="task">task</option>
          <option value="day">day</option>
          <option value="api_path">api_path</option>
          <option value="model">model</option>
        </select>
      </label>
      <label>alert_limit<input v-model.number="apiSettingState.filters.reconciliationAlerts.limit" type="number" min="1" /></label>
      <button class="btn ghost" @click="loadReconciliationAlerts">刷新偏差告警</button>
    </div>
    <div class="table-wrap mini">
      <table>
        <thead><tr><th>alert_id</th><th>level</th><th>dimension</th><th>dim_value</th><th>actual_ratio</th><th>status</th><th>message</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="a in (apiSettingState.platformUsage.reconciliationAlerts || [])" :key="a.alert_id || a.id">
            <td>{{ a.alert_id || a.id }}</td>
            <td>{{ a.level || 'WARN' }}</td>
            <td>{{ a.dimension || '-' }}</td>
            <td>{{ a.dim_value || '-' }}</td>
            <td>{{ (((Number(a.actual_ratio || 0)) * 100).toFixed?.(2) || '0.00') + '%' }}</td>
            <td>{{ a.status || '-' }}</td>
            <td>{{ a.message || '-' }}</td>
            <td><div class="row tight">
              <button class="btn ghost" @click="updateReconciliationAlertStatus(a, 'ACK')">确认</button>
              <button class="btn ghost" @click="updateReconciliationAlertStatus(a, 'MUTED')">静默</button>
              <button class="btn ghost" @click="updateReconciliationAlertStatus(a, 'RESOLVED')">解决</button>
            </div></td>
          </tr>
        </tbody>
      </table>
    </div>

    <h3>API Key 生命周期（P1-B）</h3>
    <div class="row">
      <label>pool
        <select v-model="apiSettingState.filters.keyLifecycle.pool">
          <option value="all">all</option>
          <option value="main">main</option>
          <option value="aux">aux</option>
        </select>
      </label>
      <label>task<input v-model="apiSettingState.filters.keyLifecycle.task" placeholder="llm / embedding" /></label>
      <label>tenant_scope
        <select v-model="apiSettingState.filters.keyLifecycle.tenant_scope">
          <option value="current">current</option>
          <option value="all">all</option>
        </select>
      </label>
      <label>near_expiry_days<input v-model.number="apiSettingState.filters.keyLifecycle.near_expiry_days" type="number" min="0" /></label>
      <label>expiring_only
        <select v-model="apiSettingState.filters.keyLifecycle.expiring_only">
          <option :value="false">false</option>
          <option :value="true">true</option>
        </select>
      </label>
      <label>limit<input v-model.number="apiSettingState.filters.keyLifecycle.limit" type="number" min="1" /></label>
      <button class="btn ghost" @click="loadApiKeyLifecycle">刷新生命周期</button>
    </div>
    <div class="row" v-if="apiSettingState.platformUsage.keyLifecycleSummary">
      <span>total：{{ apiSettingState.platformUsage.keyLifecycleSummary.total || 0 }}</span>
      <span>expired：{{ apiSettingState.platformUsage.keyLifecycleSummary.expired || 0 }}</span>
      <span>expiring：{{ apiSettingState.platformUsage.keyLifecycleSummary.expiring || 0 }}</span>
      <span>active：{{ apiSettingState.platformUsage.keyLifecycleSummary.active || 0 }}</span>
      <span>no_expiry：{{ apiSettingState.platformUsage.keyLifecycleSummary.no_expiry || 0 }}</span>
    </div>
    <div class="table-wrap mini">
      <table>
        <thead><tr><th>pool</th><th>endpoint</th><th>task</th><th>fingerprint</th><th>status</th><th>days_left</th><th>expires_at</th><th>rotation_state</th><th>last_rotated_at</th></tr></thead>
        <tbody>
          <tr v-for="x in (apiSettingState.platformUsage.keyLifecycle || [])" :key="`${x.pool}:${x.endpoint_id}`">
            <td>{{ x.pool || '-' }}</td>
            <td>{{ x.endpoint_id || '-' }}</td>
            <td>{{ x.task || '-' }}</td>
            <td>{{ x.key_fingerprint || '-' }}</td>
            <td>{{ x.status || '-' }}</td>
            <td>{{ x.days_to_expiry ?? '-' }}</td>
            <td>{{ x.expires_at || '-' }}</td>
            <td>{{ x.rotation_state || '-' }}</td>
            <td>{{ x.last_rotated_at || '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <h3>Key 轮转</h3>
    <div class="grid three">
      <label>pool
        <select v-model="apiSettingState.keyRotateForm.pool">
          <option value="main">main</option>
          <option value="aux">aux</option>
        </select>
      </label>
      <label>endpoint_id<input v-model="apiSettingState.keyRotateForm.endpoint_id" placeholder="端点 ID" /></label>
      <label>expires_at<input v-model="apiSettingState.keyRotateForm.expires_at" placeholder="可空；ISO 时间" /></label>
    </div>
    <div class="row">
      <label>new_api_key<input v-model="apiSettingState.keyRotateForm.new_api_key" type="password" placeholder="新 Key（必填）" /></label>
    </div>
    <div class="row">
      <label>note<input v-model="apiSettingState.keyRotateForm.note" placeholder="轮转备注（可空）" /></label>
      <button class="btn primary" @click="rotateApiKey">执行轮转</button>
    </div>

    <h3>Key 生命周期告警</h3>
    <div class="row">
      <label>warn_days<input v-model="apiSettingState.keyLifecycleAlertEval.warn_days" placeholder="7,3,1" /></label>
      <button class="btn ghost" @click="evaluateApiKeyLifecycleAlerts">评估到期告警</button>
    </div>
    <div class="row">
      <label>alert_days<input v-model.number="apiSettingState.filters.keyLifecycleAlerts.days" type="number" min="1" /></label>
      <label>alert_status
        <select v-model="apiSettingState.filters.keyLifecycleAlerts.status">
          <option value="OPEN">OPEN</option>
          <option value="ACK">ACK</option>
          <option value="MUTED">MUTED</option>
          <option value="RESOLVED">RESOLVED</option>
          <option value="ALL">ALL</option>
        </select>
      </label>
      <label>alert_pool
        <select v-model="apiSettingState.filters.keyLifecycleAlerts.pool">
          <option value="">all</option>
          <option value="main">main</option>
          <option value="aux">aux</option>
        </select>
      </label>
      <label>alert_level
        <select v-model="apiSettingState.filters.keyLifecycleAlerts.level">
          <option value="">all</option>
          <option value="WARN">WARN</option>
          <option value="HIGH">HIGH</option>
          <option value="CRITICAL">CRITICAL</option>
        </select>
      </label>
      <label>alert_limit<input v-model.number="apiSettingState.filters.keyLifecycleAlerts.limit" type="number" min="1" /></label>
      <button class="btn ghost" @click="loadApiKeyLifecycleAlerts">刷新生命周期告警</button>
      <button class="btn ghost" @click="loadApiKeyLifecycleAlertTrend">刷新趋势</button>
    </div>
    <div class="table-wrap mini">
      <table>
        <thead><tr><th>alert_id</th><th>pool</th><th>endpoint</th><th>level</th><th>days_left</th><th>expires_at</th><th>status</th><th>message</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="a in (apiSettingState.platformUsage.keyLifecycleAlerts || [])" :key="a.alert_id || a.id">
            <td>{{ a.alert_id || a.id }}</td>
            <td>{{ a.pool || '-' }}</td>
            <td>{{ a.endpoint_id || '-' }}</td>
            <td>{{ a.level || 'WARN' }}</td>
            <td>{{ a.days_to_expiry ?? '-' }}</td>
            <td>{{ a.expires_at || '-' }}</td>
            <td>{{ a.status || '-' }}</td>
            <td>{{ a.message || '-' }}</td>
            <td><div class="row tight">
              <button class="btn ghost" @click="updateApiKeyLifecycleAlertStatus(a, 'ACK')">确认</button>
              <button class="btn ghost" @click="updateApiKeyLifecycleAlertStatus(a, 'MUTED')">静默</button>
              <button class="btn ghost" @click="updateApiKeyLifecycleAlertStatus(a, 'RESOLVED')">解决</button>
            </div></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="row">
      <label>trend_days<input v-model.number="apiSettingState.filters.keyLifecycleAlertTrend.days" type="number" min="1" /></label>
      <button class="btn ghost" @click="loadApiKeyLifecycleAlertTrend">加载趋势</button>
    </div>
    <div class="table-wrap mini">
      <table>
        <thead><tr><th>day</th><th>total</th><th>warn</th><th>high</th><th>critical</th><th>open</th><th>resolved</th></tr></thead>
        <tbody>
          <tr v-for="(x, idx) in (apiSettingState.platformUsage.keyLifecycleAlertTrend || [])" :key="`${x.day || ''}-${idx}`">
            <td>{{ x.day || '-' }}</td>
            <td>{{ x.total || 0 }}</td>
            <td>{{ x.warn_count || 0 }}</td>
            <td>{{ x.high_count || 0 }}</td>
            <td>{{ x.critical_count || 0 }}</td>
            <td>{{ x.open_count || 0 }}</td>
            <td>{{ x.resolved_count || 0 }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <h3>Key 告警处置 SLA</h3>
    <div class="row">
      <label>sla_days<input v-model.number="apiSettingState.filters.keyLifecycleAlertSla.days" type="number" min="1" /></label>
      <label>sla_limit<input v-model.number="apiSettingState.filters.keyLifecycleAlertSla.limit" type="number" min="1" /></label>
      <button class="btn ghost" @click="loadApiKeyLifecycleAlertSla">刷新 SLA</button>
    </div>
    <div class="row" v-if="apiSettingState.platformUsage.keyLifecycleAlertSla?.summary">
      <span>total：{{ apiSettingState.platformUsage.keyLifecycleAlertSla.summary.total || 0 }}</span>
      <span>open：{{ apiSettingState.platformUsage.keyLifecycleAlertSla.summary.open_count || 0 }}</span>
      <span>ack：{{ apiSettingState.platformUsage.keyLifecycleAlertSla.summary.ack_count || 0 }}</span>
      <span>resolved：{{ apiSettingState.platformUsage.keyLifecycleAlertSla.summary.resolved_count || 0 }}</span>
      <span>avg_ack_seconds：{{ Number(apiSettingState.platformUsage.keyLifecycleAlertSla.summary.avg_ack_seconds || 0).toFixed(1) }}</span>
      <span>avg_resolve_seconds：{{ Number(apiSettingState.platformUsage.keyLifecycleAlertSla.summary.avg_resolve_seconds || 0).toFixed(1) }}</span>
    </div>
    <div class="table-wrap mini">
      <table>
        <thead><tr><th>alert_id</th><th>pool</th><th>endpoint</th><th>level</th><th>status</th><th>age_seconds</th><th>first_triggered_at</th><th>message</th></tr></thead>
        <tbody>
          <tr v-for="x in (apiSettingState.platformUsage.keyLifecycleAlertSla?.top_overdue || [])" :key="x.alert_id || x.id">
            <td>{{ x.alert_id || x.id }}</td>
            <td>{{ x.pool || '-' }}</td>
            <td>{{ x.endpoint_id || '-' }}</td>
            <td>{{ x.level || '-' }}</td>
            <td>{{ x.status || '-' }}</td>
            <td>{{ x.age_seconds || 0 }}</td>
            <td>{{ x.first_triggered_at || '-' }}</td>
            <td>{{ x.message || '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
