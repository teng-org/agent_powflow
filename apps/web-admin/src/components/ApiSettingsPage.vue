<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import ApiPoolTablesCard from './api/ApiPoolTablesCard.vue'
import ApiTrendLogsCard from './api/ApiTrendLogsCard.vue'
import ApiUsageGroupedCard from './api/ApiUsageGroupedCard.vue'
import ApiAlertsCard from './api/ApiAlertsCard.vue'
import ApiPlatformUsageCard from './api/ApiPlatformUsageCard.vue'
import ApiChatInsightsCard from './api/ApiChatInsightsCard.vue'
import ApiWorkbenchCard from './api/ApiWorkbenchCard.vue'

const props = defineProps({
  apiSettingState: { type: Object, required: true },
  loadApiPools: { type: Function, required: true },
  exportApiUsage: { type: Function, required: true },
  exportApiLogs: { type: Function, required: true },
  resetApiForm: { type: Function, required: true },
  upsertApiEndpoint: { type: Function, required: true },
  endpointToForm: { type: Function, required: true },
  setDefaultApiEndpoint: { type: Function, required: true },
  deleteApiEndpoint: { type: Function, required: true },
  endpointIdOf: { type: Function, required: true },
  toggleFreezeChatEndpoint: { type: Function, required: true },
  probeChatApiEndpoint: { type: Function, required: true },
  loadApiUsageGrouped: { type: Function, required: true },
  loadChatApiInsights: { type: Function, required: true },
  callApiWorkbench: { type: Function, required: true },
  evaluateAlertRules: { type: Function, required: true },
  ackAlertEvent: { type: Function, required: true },
  upsertAlertRule: { type: Function, required: true },
  resetAlertRuleForm: { type: Function, required: true },
  loadAlertRuleToForm: { type: Function, required: true },
  deleteAlertRule: { type: Function, required: true },
  loadApiObservability: { type: Function, required: true },
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
  sectionDomId: { type: Function, default: null },
  focusSection: { type: String, default: '' },
  focusSeq: { type: Number, default: 0 },
})

const pulseSection = ref('')
let pulseTimer = null

watch(
  () => [Number(props.focusSeq || 0), String(props.focusSection || '')],
  ([seq, section]) => {
    if (!seq || !section) return
    pulseSection.value = section
    if (pulseTimer) clearTimeout(pulseTimer)
    pulseTimer = setTimeout(() => {
      pulseSection.value = ''
      pulseTimer = null
    }, 2200)
  },
)

onBeforeUnmount(() => {
  if (pulseTimer) clearTimeout(pulseTimer)
  pulseTimer = null
})
</script>

<template>
  <section class="content">
    <div class="panel">
      <div class="panel-title">API 设置</div>
      <p class="panel-sub">主池 / AUX / Chat API 池端点治理（Sprint-2 可编辑）</p>
      <p class="banner err" v-if="apiSettingState.error">{{ apiSettingState.error }}</p>
      <div class="row">
        <button class="btn ghost" @click="loadApiPools">刷新 API 池</button>
        <button class="btn ghost" @click="exportApiUsage('csv')">导出用量 CSV</button>
        <button class="btn ghost" @click="exportApiLogs('csv')">导出日志 CSV</button>
      </div>

      <div class="domain-stack">
        <div class="domain-section" :class="{ 'section-pulse': pulseSection === 'pool-tables' }" :id="sectionDomId ? sectionDomId('pool-tables') : undefined" data-menu-section="pool-tables">
          <h2 class="domain-section-title">池列表</h2>
          <ApiPoolTablesCard
            :api-setting-state="apiSettingState"
            :reset-api-form="resetApiForm"
            :upsert-api-endpoint="upsertApiEndpoint"
            :endpoint-to-form="endpointToForm"
            :set-default-api-endpoint="setDefaultApiEndpoint"
            :delete-api-endpoint="deleteApiEndpoint"
            :endpoint-id-of="endpointIdOf"
            :toggle-freeze-chat-endpoint="toggleFreezeChatEndpoint"
            :probe-chat-api-endpoint="probeChatApiEndpoint"
          />
        </div>
        <div class="domain-section" :class="{ 'section-pulse': pulseSection === 'usage' }" :id="sectionDomId ? sectionDomId('usage') : undefined" data-menu-section="usage">
          <h2 class="domain-section-title">用量与日志</h2>
          <ApiTrendLogsCard :api-setting-state="apiSettingState" />
          <ApiUsageGroupedCard :api-setting-state="apiSettingState" :load-api-usage-grouped="loadApiUsageGrouped" />
          <ApiChatInsightsCard :api-setting-state="apiSettingState" :load-chat-api-insights="loadChatApiInsights" />
        </div>
        <div class="domain-section" :class="{ 'section-pulse': pulseSection === 'alerts' }" :id="sectionDomId ? sectionDomId('alerts') : undefined" data-menu-section="alerts">
          <h2 class="domain-section-title">告警</h2>
          <ApiAlertsCard
            :api-setting-state="apiSettingState"
            :evaluate-alert-rules="evaluateAlertRules"
            :ack-alert-event="ackAlertEvent"
            :upsert-alert-rule="upsertAlertRule"
            :reset-alert-rule-form="resetAlertRuleForm"
            :load-alert-rule-to-form="loadAlertRuleToForm"
            :delete-alert-rule="deleteAlertRule"
            :load-api-observability="loadApiObservability"
          />
        </div>
        <div class="domain-section" :class="{ 'section-pulse': pulseSection === 'platform-usage' }" :id="sectionDomId ? sectionDomId('platform-usage') : undefined" data-menu-section="platform-usage">
          <h2 class="domain-section-title">平台用量</h2>
          <ApiPlatformUsageCard
            :api-setting-state="apiSettingState"
            :load-platform-usage-summary="loadPlatformUsageSummary"
            :load-platform-usage-events="loadPlatformUsageEvents"
            :load-platform-budgets="loadPlatformBudgets"
            :upsert-platform-budget="upsertPlatformBudget"
            :delete-platform-budget="deletePlatformBudget"
            :evaluate-platform-budgets="evaluatePlatformBudgets"
            :load-platform-alerts="loadPlatformAlerts"
            :update-platform-alert-status="updatePlatformAlertStatus"
            :load-platform-budget-to-form="loadPlatformBudgetToForm"
            :reset-platform-budget-form="resetPlatformBudgetForm"
            :load-pricing-versions="loadPricingVersions"
            :upsert-pricing-version="upsertPricingVersion"
            :delete-pricing-version="deletePricingVersion"
            :load-reconciliation-preview="loadReconciliationPreview"
            :load-reconciliation-breakdown="loadReconciliationBreakdown"
            :evaluate-reconciliation-alerts="evaluateReconciliationAlerts"
            :load-reconciliation-alerts="loadReconciliationAlerts"
            :update-reconciliation-alert-status="updateReconciliationAlertStatus"
            :export-reconciliation-breakdown-csv="exportReconciliationBreakdownCsv"
            :load-api-key-lifecycle="loadApiKeyLifecycle"
            :rotate-api-key="rotateApiKey"
            :evaluate-api-key-lifecycle-alerts="evaluateApiKeyLifecycleAlerts"
            :load-api-key-lifecycle-alerts="loadApiKeyLifecycleAlerts"
            :load-api-key-lifecycle-alert-trend="loadApiKeyLifecycleAlertTrend"
            :load-api-key-lifecycle-alert-sla="loadApiKeyLifecycleAlertSla"
            :update-api-key-lifecycle-alert-status="updateApiKeyLifecycleAlertStatus"
          />
        </div>
        <div class="domain-section" :class="{ 'section-pulse': pulseSection === 'workbench' }" :id="sectionDomId ? sectionDomId('workbench') : undefined" data-menu-section="workbench">
          <h2 class="domain-section-title">调试工作台</h2>
          <ApiWorkbenchCard :api-setting-state="apiSettingState" :call-api-workbench="callApiWorkbench" />
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.section-pulse {
  animation: sectionPulse 2.1s ease;
}

@keyframes sectionPulse {
  0% {
    box-shadow: 0 0 0 0 rgba(53, 110, 215, 0.35);
    border-color: #8eb4f0;
    background: #f8fbff;
  }
  65% {
    box-shadow: 0 0 0 10px rgba(53, 110, 215, 0);
    border-color: #bcd2f4;
    background: #fbfdff;
  }
  100% {
    box-shadow: none;
    border-color: #dbe5f2;
    background: #fff;
  }
}
</style>
