<script setup>
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ConsoleTopNav from './components/ConsoleTopNav.vue'
import ConsoleSideNav from './components/ConsoleSideNav.vue'
import AsyncPageLoading from './components/common/AsyncPageLoading.vue'
import AsyncPageError from './components/common/AsyncPageError.vue'
import { useConsoleState } from './composables/useConsoleState'
import { useServiceClient } from './composables/useServiceClient'
import { useConsoleHelpers } from './composables/useConsoleHelpers'
import { useKbAdmin } from './composables/useKbAdmin'
import { useChatAdmin } from './composables/useChatAdmin'
import { useApiAdmin } from './composables/useApiAdmin'
import { useMonitorAdmin } from './composables/useMonitorAdmin'
import { useAuthAdmin } from './composables/useAuthAdmin'
import { useTenantUserAdmin } from './composables/useTenantUserAdmin'
import { usePlatformSettingsAdmin } from './composables/usePlatformSettingsAdmin'
import { createNavActionMap, useConsoleNav } from './composables/useConsoleNav'
import { useAppearance } from './composables/useAppearance'

function asyncPage(loader) {
  return defineAsyncComponent({
    loader,
    loadingComponent: AsyncPageLoading,
    errorComponent: AsyncPageError,
    delay: 120,
    timeout: 20_000,
    onError(_error, retry, fail, attempts) {
      if (attempts < 2) retry()
      else fail()
    },
  })
}

const MonitorOverview = asyncPage(() => import('./components/MonitorOverview.vue'))
const MonitorTasks = asyncPage(() => import('./components/MonitorTasks.vue'))
const RunTracePage = asyncPage(() => import('./components/RunTracePage.vue'))
const RunDeadLetterPage = asyncPage(() => import('./components/RunDeadLetterPage.vue'))
const ApprovalInboxPage = asyncPage(() => import('./components/ApprovalInboxPage.vue'))
const WorkflowManagementPage = asyncPage(() => import('./components/WorkflowManagementPage.vue'))
const WorkflowSecretsPage = asyncPage(() => import('./components/WorkflowSecretsPage.vue'))
const MonitorDatabase = asyncPage(() => import('./components/MonitorDatabase.vue'))
const MonitorApiGovernance = asyncPage(() => import('./components/MonitorApiGovernance.vue'))
const MonitorChatMemory = asyncPage(() => import('./components/MonitorChatMemory.vue'))
const KbSettingsPage = asyncPage(() => import('./components/KbSettingsPage.vue'))
const ChatSettingsPage = asyncPage(() => import('./components/ChatSettingsPage.vue'))
const ApiSettingsPage = asyncPage(() => import('./components/ApiSettingsPage.vue'))
const TenantUserSettingsPage = asyncPage(() => import('./components/TenantUserSettingsPage.vue'))
const PlatformSettingsPage = asyncPage(() => import('./components/PlatformSettingsPage.vue'))
const InstallationSetupPage = asyncPage(() => import('./components/InstallationSetupPage.vue'))
const PublishedApiAuditPage = asyncPage(() => import('./components/PublishedApiAuditPage.vue'))
const PlatformProductAssetsPage = asyncPage(() => import('./components/PlatformProductAssetsPage.vue'))

const {
  cfg,
  ui,
  nav,
  pages,
  currentMenu,
  currentMenuItem,
  overviewState,
  taskState,
  dbState,
  kbState,
  chatSettingState,
  apiSettingState,
  tenantUserState,
  platformSettingState,
  isControlLoggedIn,
  isExecutionLoggedIn,
} = useConsoleState()

const { fetchJson, fetchRaw } = useServiceClient(cfg)
const {
  getKbId,
  getKbName,
  getDocId,
  getDocName,
  getChunkId,
  getTaskId,
  normalizeSessionId,
  endpointIdOf,
  formatJson,
  downloadBlob,
} = useConsoleHelpers()

const contentRef = ref(null)
const observerRef = ref(null)
const sectionScrollHandlerRef = ref(null)
const apiSettingsFocusSection = ref('')
const apiSettingsFocusSeq = ref(0)
const { resolvedAppearance, toggleAppearance } = useAppearance()

function clearBanner() {
  ui.message = ''
  ui.error = ''
}

const {
  loadControlTenants,
  loadKbs,
  createKb,
  renameKb,
  deleteKb,
  summarizeKb,
  loadKbBindings,
  bindKbTenant,
  unbindKbTenant,
  selectKb,
  loadDocs,
  loadKbTasks,
  selectKbTask,
  ingestDocText,
  onUploadFileChange,
  uploadDocFile,
  selectDoc,
  saveChunkDraft,
  clearChunkDraft,
  toggleDocumentFreeze,
  toggleChunkFreeze,
  commitChunkDrafts,
  runChunk,
  summarizeDoc,
  deleteDoc,
  loadBatchDetail,
  retryBatchFailed,
  toggleDocSelection,
  toggleAllDocs,
  submitDocumentBatch,
} = useKbAdmin({
  fetchJson,
  isControlLoggedIn,
  isExecutionLoggedIn,
  kbState,
  ui,
  getKbId,
  getKbName,
  getDocId,
  getDocName,
  getChunkId,
  getTaskId,
  onTaskRefresh: () => loadTaskMonitor(),
})

const {
  loadChatConfig,
  saveChatConfig,
  loadChatSessions,
  createChatSession,
  selectSession,
  loadChatMessages,
  queryRunStatus,
  cancelRunStatus,
} = useChatAdmin({
  fetchJson,
  chatSettingState,
  ui,
  normalizeSessionId,
})

const {
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
  exportReconciliationBreakdownCsv,
  loadApiKeyLifecycle,
  rotateApiKey,
  evaluateApiKeyLifecycleAlerts,
  loadApiKeyLifecycleAlerts,
  loadApiKeyLifecycleAlertTrend,
  loadApiKeyLifecycleAlertSla,
  updateApiKeyLifecycleAlertStatus,
} = useApiAdmin({
  fetchJson,
  fetchRaw,
  isExecutionLoggedIn,
  apiSettingState,
  ui,
  cfg,
  endpointIdOf,
  downloadBlob,
})

const {
  loadOverview,
  loadSystemLogs,
  loadSystemLogStats3d,
  loadRuntimeSeries,
  loadPermissionWarnings,
  loadRiskRules,
  saveRiskRules,
  loadRiskEvents,
  ackRiskEvent,
  resolveRiskEvent,
  loadTaskMonitor,
  selectTaskForEvents,
  cancelTask,
  clearFinishedTasks,
  loadDbMeta,
  loadDbRows,
  dbRows,
  dbTotal,
  dbColumns,
} = useMonitorAdmin({
  fetchJson,
  isControlLoggedIn,
  isExecutionLoggedIn,
  overviewState,
  taskState,
  dbState,
  ui,
  getTaskId,
})

const { loginControl, loginExecution, logoutAll } = useAuthAdmin({
  fetchJson,
  cfg,
  ui,
  kbState,
  chatSettingState,
  apiSettingState,
  clearBanner,
  onAfterControlLogin: async () => {},
  onAfterExecutionLogin: async () => {},
})

const {
  loadTenants: loadTenantsForAdmin,
  selectTenant,
  createTenant,
  loadUsers,
  createUser,
  loadUserToPatchForm,
  patchUser,
  pickTenantId,
  pickUserId,
} = useTenantUserAdmin({
  fetchJson,
  isControlLoggedIn,
  tenantUserState,
  ui,
})

const {
  loadRuntimeConfig: loadRuntimeConfigForAdmin,
  saveRuntimeConfig,
  previewRuntimeConfigImpact,
  loadConfigHistory: loadConfigHistoryForAdmin,
  loadConfigAudit: loadConfigAuditForAdmin,
  loadConfigDiff: loadConfigDiffForAdmin,
  rollbackConfigRelease: rollbackConfigReleaseForAdmin,
  switchDefaultConfig: switchDefaultConfigForAdmin,
  deleteConfigRelease: deleteConfigReleaseForAdmin,
} =
  usePlatformSettingsAdmin({
    fetchJson,
    isExecutionLoggedIn,
    platformSettingState,
    overviewState,
    ui,
  })

const navActionMap = createNavActionMap({
  loadKbs,
  loadControlTenants,
  kbState,
  loadKbTasks,
  loadChatConfig,
  loadChatSessions,
  loadApiPools,
  loadTenantsForAdmin,
  loadRuntimeConfigForAdmin,
  loadTaskMonitor,
  loadApiObservability,
  loadChatApiInsights,
  loadPlatformUsageSummary,
  loadPlatformUsageEvents,
  loadPlatformAlerts,
  loadApiUsageGrouped,
})
const { switchPage, switchPrimary } = useConsoleNav({ nav, actionMap: navActionMap })

const isConsoleLoggedIn = computed(() => isControlLoggedIn.value && isExecutionLoggedIn.value)

const currentPageTitle = computed(() => currentMenuItem.value?.name || '未命名菜单')
const currentSections = computed(() => currentMenuItem.value?.sections || [{ id: 'main', name: '概览' }])

function openPrimary(primaryId) {
  const menus = pages[primaryId] || []
  switchPrimary(primaryId, menus)
}

async function openApiSettingsFromOverview() {
  switchPage('agent', 'api-settings')
  nav.activeSection = 'platform-usage'
  apiSettingsFocusSection.value = 'platform-usage'
  apiSettingsFocusSeq.value = Date.now()
  await Promise.allSettled([
    loadPlatformUsageSummary(),
    loadPlatformUsageEvents(),
    loadPlatformAlerts(),
    loadApiKeyLifecycle(),
    loadApiKeyLifecycleAlerts(),
    loadApiKeyLifecycleAlertTrend(),
    loadApiKeyLifecycleAlertSla(),
  ])
  await nextTick()
  jumpSection('platform-usage')
}

function openCrossStoreRepairsFromOverview() {
  switchPage('dashboard', 'db')
  nav.activeSection = 'main'
}

function sectionDomId(sectionId) {
  return `${nav.page}--${sectionId}`
}

function jumpSection(sectionId) {
  nav.activeSection = sectionId
  const root = contentRef.value
  if (!root) return
  const el = root.querySelector(`#${CSS.escape(sectionDomId(sectionId))}`)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function setupSectionObserver() {
  observerRef.value?.disconnect()
  const prevRoot = contentRef.value
  if (prevRoot && sectionScrollHandlerRef.value) {
    prevRoot.removeEventListener('scroll', sectionScrollHandlerRef.value)
  }
  const root = contentRef.value
  if (!root) return
  const sections = Array.from(root.querySelectorAll('[data-menu-section]'))
  if (!sections.length) return

  function pickBottomVisibleSection() {
    const rootRect = root.getBoundingClientRect()
    const candidates = sections
      .map((el) => {
        const rect = el.getBoundingClientRect()
        const visibleTop = Math.max(rect.top, rootRect.top)
        const visibleBottom = Math.min(rect.bottom, rootRect.bottom)
        const visibleHeight = visibleBottom - visibleTop
        return { el, top: rect.top, visibleHeight }
      })
      .filter((x) => x.visibleHeight > 0)
      .sort((a, b) => b.top - a.top)
    return candidates[0]?.el || null
  }

  function updateActiveSection() {
    const target = pickBottomVisibleSection()
    if (target?.dataset?.menuSection) {
      nav.activeSection = target.dataset.menuSection
    }
  }

  observerRef.value = new IntersectionObserver(
    () => updateActiveSection(),
    { root, threshold: [0.2, 0.5, 0.8] },
  )
  for (const el of sections) observerRef.value.observe(el)

  sectionScrollHandlerRef.value = () => updateActiveSection()
  root.addEventListener('scroll', sectionScrollHandlerRef.value, { passive: true })
  updateActiveSection()
}

async function loginConsole() {
  clearBanner()
  await loginControl()
  if (!cfg.control.token) {
    if (!ui.error) ui.error = 'Control Plane 登录失败：未获得 access token'
    return
  }
  await loginExecution()
  if (!cfg.execution.token) {
    if (!ui.error) ui.error = 'Execution Plane 登录失败：请检查执行平面账号/密码'
    return
  }
  if (cfg.control.token && cfg.execution.token) {
    ui.message = '控制台登录成功'
    await Promise.allSettled([
      loadOverview(),
      loadTaskMonitor(),
      loadDbMeta(),
      loadControlTenants(),
      loadKbs(),
    ])
    await nextTick()
    setupSectionObserver()
  }
}

function handleLogout() {
  logoutAll()
}

watch(
  () => [nav.primary, nav.page],
  async () => {
    nav.activeSection = currentSections.value[0]?.id || 'main'
    await nextTick()
    setupSectionObserver()
  },
)

onMounted(async () => {
  await loadOverview()
  if (isConsoleLoggedIn.value) {
    await nextTick()
    setupSectionObserver()
  }
})

onBeforeUnmount(() => {
  observerRef.value?.disconnect()
  const root = contentRef.value
  if (root && sectionScrollHandlerRef.value) {
    root.removeEventListener('scroll', sectionScrollHandlerRef.value)
  }
})
</script>

<template>
  <div class="app-root">
    <main v-if="!isConsoleLoggedIn" class="login-root">
      <form class="login-card" aria-labelledby="login-title" :aria-busy="ui.loading" @submit.prevent="loginConsole">
        <button
          type="button"
          class="icon-btn login-appearance-toggle"
          :aria-label="resolvedAppearance === 'dark' ? '切换到浅色模式' : '切换到深色模式'"
          :title="resolvedAppearance === 'dark' ? '浅色模式' : '深色模式'"
          @click="toggleAppearance"
        >
          <svg v-if="resolvedAppearance === 'dark'" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v2m0 14v2M3 12h2m14 0h2M5.64 5.64l1.42 1.42m9.88 9.88 1.42 1.42m0-12.72-1.42 1.42M7.06 16.94l-1.42 1.42M16.5 12a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Z"/></svg>
          <svg v-else viewBox="0 0 24 24" aria-hidden="true"><path d="M20.4 15.2A8.5 8.5 0 0 1 8.8 3.6 8.5 8.5 0 1 0 20.4 15.2Z"/></svg>
        </button>
        <h1 id="login-title">Powflow Console 登录</h1>
        <p class="subline">全局配置与运营管理入口</p>
        <p class="banner ok" v-if="ui.message" role="status" aria-live="polite">{{ ui.message }}</p>
        <p class="banner err" v-if="ui.error" role="alert">{{ ui.error }}</p>
        <div class="grid two">
          <label>管理员账号<input v-model="cfg.control.username" autocomplete="username" required /></label>
          <label>管理员密码<input v-model="cfg.control.password" type="password" autocomplete="current-password" required @keydown.enter.prevent="loginConsole" /></label>
        </div>
        <div class="row">
          <button type="submit" class="btn primary" :disabled="ui.loading">{{ ui.loading ? '登录中…' : '登录控制台' }}</button>
        </div>
        <details class="advanced">
          <summary>高级连接配置</summary>
          <div class="grid three">
            <label>Control Base URL<input v-model="cfg.controlPlaneBaseUrl" type="text" inputmode="url" autocomplete="off" spellcheck="false" /></label>
            <label>Execution Base URL<input v-model="cfg.executionBaseUrl" type="text" inputmode="url" autocomplete="off" spellcheck="false" /></label>
            <label>Chat Base URL<input v-model="cfg.chatBaseUrl" type="text" inputmode="url" autocomplete="off" spellcheck="false" /></label>
          </div>
          <div class="grid three">
            <label>Tenant Code<input v-model="cfg.control.tenantCode" autocomplete="off" spellcheck="false" /></label>
            <label>Execution 用户名<input v-model="cfg.execution.username" autocomplete="username" /></label>
            <label>Execution 密码<input v-model="cfg.execution.password" type="password" autocomplete="current-password" /></label>
          </div>
        </details>
      </form>
    </main>

    <section v-else class="shell-root">
      <a class="skip-link" href="#console-main">跳到主要内容</a>
      <ConsoleTopNav
        :nav="nav"
        :appearance="resolvedAppearance"
        @switch-primary="openPrimary"
        @toggle-appearance="toggleAppearance"
        @logout="handleLogout"
      />
      <div class="shell-body">
        <ConsoleSideNav
          :nav="nav"
          :pages="pages"
          :current-menu="currentMenu"
          :is-control-logged-in="isControlLoggedIn"
          :is-execution-logged-in="isExecutionLoggedIn"
          :overview-state="overviewState"
          @switch-page="switchPage"
          @jump-section="jumpSection"
        />

        <main id="console-main" ref="contentRef" tabindex="-1" class="main-content">
          <div class="content-body">
            <header class="content-header">
              <h1 class="page-title">{{ currentPageTitle }}</h1>
            </header>

            <p class="banner ok" v-if="ui.message" role="status" aria-live="polite">{{ ui.message }}</p>
            <p class="banner err" v-if="ui.error" role="alert">{{ ui.error }}</p>

            <section v-if="nav.page === 'overview'">
              <MonitorOverview
                :overview-state="overviewState"
                :section-dom-id="sectionDomId"
                :load-overview="loadOverview"
                :load-system-logs="loadSystemLogs"
                :load-system-log-stats3d="loadSystemLogStats3d"
                :load-runtime-series="loadRuntimeSeries"
                :load-permission-warnings="loadPermissionWarnings"
                :load-risk-rules="loadRiskRules"
                :save-risk-rules="saveRiskRules"
                :load-risk-events="loadRiskEvents"
                :ack-risk-event="ackRiskEvent"
                :resolve-risk-event="resolveRiskEvent"
                :save-runtime-config="saveRuntimeConfig"
                :on-open-api-key-risk="openApiSettingsFromOverview"
                :on-open-cross-store-repairs="openCrossStoreRepairsFromOverview"
                :evaluate-api-key-lifecycle-alerts="evaluateApiKeyLifecycleAlerts"
              />
            </section>

          <section
            v-if="nav.page === 'tasks'"
            class="domain-section"
            :id="sectionDomId('main')"
            data-menu-section="main"
          >
            <h2 class="domain-section-title">任务</h2>
            <MonitorTasks
              :task-state="taskState"
              :load-task-monitor="loadTaskMonitor"
              :clear-finished-tasks="clearFinishedTasks"
              :select-task-for-events="selectTaskForEvents"
              :cancel-task="cancelTask"
            />
          </section>

          <section
            v-if="nav.page === 'db'"
            class="domain-section"
            :id="sectionDomId('main')"
            data-menu-section="main"
          >
            <h2 class="domain-section-title">数据源</h2>
            <MonitorDatabase :db-state="dbState" :db-columns="dbColumns" :db-rows="dbRows" :db-total="dbTotal" :load-db-rows="loadDbRows" :fetch-json="fetchJson" />
          </section>

          <RunDeadLetterPage
            v-if="nav.page === 'run-dead-letters'"
            :fetch-json="fetchJson"
            :section-dom-id="sectionDomId"
          />

          <RunTracePage
            v-if="nav.page === 'run-trace'"
            :fetch-json="fetchJson"
            :section-dom-id="sectionDomId"
          />

          <ApprovalInboxPage
            v-if="nav.page === 'approval-inbox'"
            :fetch-json="fetchJson"
            :section-dom-id="sectionDomId"
          />

          <WorkflowManagementPage
            v-if="nav.page === 'workflow-management'"
            :fetch-json="fetchJson"
            :section-dom-id="sectionDomId"
          />

          <WorkflowSecretsPage
            v-if="nav.page === 'workflow-secrets'"
            :fetch-json="fetchJson"
            :section-dom-id="sectionDomId"
          />

          <KbSettingsPage
            v-if="nav.page === 'kb-settings'"
            :section-dom-id="sectionDomId"
            :kb-state="kbState"
            :is-execution-logged-in="isExecutionLoggedIn"
            :create-kb="createKb"
            :load-kbs="loadKbs"
            :get-kb-id="getKbId"
            :get-kb-name="getKbName"
            :select-kb="selectKb"
            :rename-kb="renameKb"
            :delete-kb="deleteKb"
            :bind-kb-tenant="bindKbTenant"
            :load-kb-bindings="loadKbBindings"
            :unbind-kb-tenant="unbindKbTenant"
            :load-docs="loadDocs"
            :get-doc-id="getDocId"
            :get-doc-name="getDocName"
            :select-doc="selectDoc"
            :summarize-kb="summarizeKb"
            :summarize-doc="summarizeDoc"
            :delete-doc="deleteDoc"
            :toggle-document-freeze="toggleDocumentFreeze"
            :ingest-doc-text="ingestDocText"
            :on-upload-file-change="onUploadFileChange"
            :upload-doc-file="uploadDocFile"
            :toggle-doc-selection="toggleDocSelection"
            :toggle-all-docs="toggleAllDocs"
            :submit-document-batch="submitDocumentBatch"
            :run-chunk="runChunk"
            :commit-chunk-drafts="commitChunkDrafts"
            :get-chunk-id="getChunkId"
            :save-chunk-draft="saveChunkDraft"
            :clear-chunk-draft="clearChunkDraft"
            :toggle-chunk-freeze="toggleChunkFreeze"
            :load-kb-tasks="loadKbTasks"
            :get-task-id="getTaskId"
            :select-kb-task="selectKbTask"
            :load-batch-detail="loadBatchDetail"
            :retry-batch-failed="retryBatchFailed"
          />

          <ChatSettingsPage
            v-if="nav.page === 'chat-settings'"
            :section-dom-id="sectionDomId"
            :chat-setting-state="chatSettingState"
            :load-chat-config="loadChatConfig"
            :save-chat-config="saveChatConfig"
            :create-chat-session="createChatSession"
            :load-chat-sessions="loadChatSessions"
            :normalize-session-id="normalizeSessionId"
            :select-session="selectSession"
            :query-run-status="queryRunStatus"
            :cancel-run-status="cancelRunStatus"
            :format-json="formatJson"
          />

          <ApiSettingsPage
            v-if="nav.page === 'api-settings'"
            :section-dom-id="sectionDomId"
            :api-setting-state="apiSettingState"
            :load-api-pools="loadApiPools"
            :export-api-usage="exportApiUsage"
            :export-api-logs="exportApiLogs"
            :reset-api-form="resetApiForm"
            :upsert-api-endpoint="upsertApiEndpoint"
            :endpoint-to-form="endpointToForm"
            :set-default-api-endpoint="setDefaultApiEndpoint"
            :delete-api-endpoint="deleteApiEndpoint"
            :endpoint-id-of="endpointIdOf"
            :toggle-freeze-chat-endpoint="toggleFreezeChatEndpoint"
            :probe-chat-api-endpoint="probeChatApiEndpoint"
            :load-api-usage-grouped="loadApiUsageGrouped"
            :load-chat-api-insights="loadChatApiInsights"
            :call-api-workbench="callApiWorkbench"
            :evaluate-alert-rules="evaluateAlertRules"
            :ack-alert-event="ackAlertEvent"
            :upsert-alert-rule="upsertAlertRule"
            :reset-alert-rule-form="resetAlertRuleForm"
            :load-alert-rule-to-form="loadAlertRuleToForm"
            :delete-alert-rule="deleteAlertRule"
            :load-api-observability="loadApiObservability"
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
            :focus-section="apiSettingsFocusSection"
            :focus-seq="apiSettingsFocusSeq"
          />

          <TenantUserSettingsPage
            v-if="nav.page === 'tenant-user-settings'"
            :section-dom-id="sectionDomId"
            :tenant-user-state="tenantUserState"
            :load-tenants="loadTenantsForAdmin"
            :select-tenant="selectTenant"
            :create-tenant="createTenant"
            :load-users="loadUsers"
            :create-user="createUser"
            :load-user-to-patch-form="loadUserToPatchForm"
            :patch-user="patchUser"
            :pick-tenant-id="pickTenantId"
            :pick-user-id="pickUserId"
          />

          <PlatformSettingsPage
            v-if="nav.page === 'platform-settings'"
            :section-dom-id="sectionDomId"
            :platform-setting-state="platformSettingState"
            :load-runtime-config="loadRuntimeConfigForAdmin"
            :save-runtime-config="saveRuntimeConfig"
            :preview-runtime-config-impact="previewRuntimeConfigImpact"
            :load-config-history="loadConfigHistoryForAdmin"
            :load-config-audit="loadConfigAuditForAdmin"
            :load-config-diff="loadConfigDiffForAdmin"
            :rollback-config-release="rollbackConfigReleaseForAdmin"
            :switch-default-config="switchDefaultConfigForAdmin"
            :delete-config-release="deleteConfigReleaseForAdmin"
          />

          <InstallationSetupPage
            v-if="nav.page === 'installation-setup'"
            :fetch-json="fetchJson"
            @open-ai-pool="switchPage('infrastructure', 'api-settings')"
            @open-storage="switchPage('infrastructure', 'db')"
          />

          <PublishedApiAuditPage
            v-if="nav.page === 'published-api-audit'"
            :fetch-json="fetchJson"
          />

          <PlatformProductAssetsPage
            v-if="nav.page === 'platform-product-assets'"
            :fetch-json="fetchJson"
          />

            <MonitorApiGovernance
              v-if="nav.page === 'api-monitor'"
              :api-setting-state="apiSettingState"
              :load-api-observability="loadApiObservability"
              :load-chat-api-insights="loadChatApiInsights"
              :load-platform-usage-summary="loadPlatformUsageSummary"
              :load-platform-usage-events="loadPlatformUsageEvents"
              :load-platform-alerts="loadPlatformAlerts"
              :load-api-usage-grouped="loadApiUsageGrouped"
              :section-dom-id="sectionDomId"
            />

            <MonitorChatMemory
              v-if="nav.page === 'chat-memory-monitor'"
              :task-state="taskState"
              :chat-setting-state="chatSettingState"
              :load-task-monitor="loadTaskMonitor"
              :load-chat-sessions="loadChatSessions"
              :select-session="selectSession"
              :load-chat-messages="loadChatMessages"
              :query-run-status="queryRunStatus"
              :cancel-run-status="cancelRunStatus"
              :format-json="formatJson"
              :section-dom-id="sectionDomId"
            />
          </div>
        </main>
      </div>
    </section>
  </div>
</template>

<style>
*, *::before, *::after { box-sizing: border-box; }
html, body, #app {
  width: 100%;
  height: 100%;
  max-height: 100dvh;
  margin: 0;
  padding: 0;
  overflow: hidden !important;
}
body {
  font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  background: #f4f6fa;
  color: #243245;
}

.app-root {
  position: fixed;
  inset: 0;
  width: 100%;
  height: 100dvh;
  overflow: hidden !important;
}

.login-root {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  overflow: auto;
}

.login-card {
  width: min(980px, 100%);
  margin: auto;
  background: #fff;
  border: 1px solid #dde5f0;
  border-radius: 12px;
  padding: 16px;
}

.shell-root {
  height: 100%;
  display: grid;
  grid-template-rows: 74px minmax(0, 1fr);
  overflow: hidden;
}

.skip-link {
  position: fixed;
  z-index: 1000;
  top: 8px;
  left: 8px;
  padding: 9px 12px;
  border-radius: 8px;
  background: #173f80;
  color: #fff;
  text-decoration: none;
  transform: translateY(calc(-100% - 16px));
  transition: transform 140ms cubic-bezier(.23, 1, .32, 1);
}

.skip-link:focus { transform: translateY(0); }

.shell-body {
  min-height: 0;
  display: grid;
  grid-template-columns: 232px minmax(0, 1fr);
}

.main-content {
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 12px 16px;
  border-top-left-radius: 10px;
}

.content-body {
  width: 90%;
  max-width: 90%;
  margin: 0 auto;
  padding: 0 0 28px;
  overflow-x: hidden;
}

.content-header {
  position: sticky;
  top: 0;
  z-index: 5;
  background: transparent;
  padding: 4px 0 8px;
  margin-bottom: 10px;
}

.page-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
}

.page-sub {
  color: #6e7f95;
  font-size: 13px;
  margin-top: 4px;
}

.content-body > * {
  max-width: 100%;
}

h1 { margin: 0; font-size: 24px; }
.subline { color: #6e7f95; margin-top: 4px; font-size: 13px; }

label {
  display: grid;
  gap: 6px;
  font-size: 12px;
  color: #5f728d;
  font-weight: 600;
}

input, select {
  width: 100%;
  max-width: 100%;
  border: 1px solid #c8d7eb;
  border-radius: 10px;
  padding: 8px 10px;
  min-height: 40px;
  font: inherit;
  background: #fff;
}

input:focus-visible,
select:focus-visible,
textarea:focus-visible,
summary:focus-visible,
.btn:focus-visible {
  outline: 2px solid #5b8fdd;
  outline-offset: 2px;
}
textarea {
  width: 100%;
  max-width: 100%;
  border: 1px solid #c8d7eb;
  border-radius: 10px;
  padding: 8px 10px;
  min-height: 40px;
  font: inherit;
  resize: none;
  background: #fff;
}

.grid { display: grid; gap: 10px; margin-bottom: 10px; }
.grid.two { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.grid.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }

.advanced {
  border: 1px dashed #d7e2f2;
  border-radius: 10px;
  padding: 8px 10px;
  margin-top: 8px;
  background: #fbfdff;
}

.advanced summary {
  cursor: pointer;
  color: #5b6f8b;
}

.global-filter, .panel {
  background: transparent;
  border: 0;
  border-radius: 0;
  padding: 0;
  margin-bottom: 16px;
}

.global-filter {
  display: grid;
  gap: 10px;
  padding: 6px 0 10px;
  border-bottom: 1px solid #dfe7f2;
  min-width: 0;
}

.global-filter-fields {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  min-width: 0;
}

.global-filter-fields > label {
  min-width: 0;
}

.global-filter-fields > label input,
.global-filter-fields > label select {
  min-width: 0;
  width: 100%;
}

.action-wrap {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
}

.btn {
  border: 1px solid #ccd8e8;
  border-radius: 10px;
  background: #fff;
  min-height: 40px;
  padding: 8px 12px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  white-space: nowrap;
  transition: transform 140ms cubic-bezier(.23, 1, .32, 1), background-color 140ms ease, border-color 140ms ease;
}
.btn.primary { background: #2c6ae6; color: #fff; border-color: #2c6ae6; }
.btn.ghost { background: #f7f9fd; }
.btn:disabled { opacity: .6; cursor: not-allowed; }
.btn:active:not(:disabled) { transform: scale(.97); }

.banner { margin: 0 0 10px; border-radius: 9px; padding: 8px 10px; font-size: 13px; }
.banner.ok { background: #ecf9f1; color: #1f7f50; border: 1px solid #bfe8d0; }
.banner.err { background: #fff1f1; color: #a33a3a; border: 1px solid #f0c4c4; }

.panel-title { font-size: 18px; font-weight: 700; }
.panel-sub { margin: 4px 0 12px; color: #687a92; }
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
  margin-bottom: 10px;
}

.kpi-card {
  border: 1px solid #e0e9f4;
  border-radius: 10px;
  padding: 10px;
  background: linear-gradient(180deg, #fbfdff, #f4f8ff);
}

.kpi-name { color: #72859d; font-size: 12px; margin-bottom: 6px; }
.kpi-value { font-size: 22px; font-weight: 700; color: #1f3f74; }

.json-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

pre {
  margin: 0;
  padding: 10px;
  border-radius: 10px;
  background: #0f1a2a;
  color: #d9e5f7;
  max-height: 320px;
  overflow: auto;
  font-size: 12px;
}

h3 { margin: 0 0 8px; font-size: 14px; color: #425a78; }
.two-col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.table-wrap {
  border: 1px solid #dce6f2;
  border-radius: 10px;
  overflow: auto;
  max-height: 420px;
}
.table-wrap.short { max-height: 260px; }
.table-wrap.mini { max-height: 180px; }

table {
  width: 100%;
  border-collapse: collapse;
  min-width: 0;
  table-layout: fixed;
}

th, td {
  padding: 8px 9px;
  border-bottom: 1px solid #edf2f8;
  font-size: 12px;
  text-align: left;
  vertical-align: top;
  word-break: break-word;
}

thead th {
  position: sticky;
  top: 0;
  background: #f8fbff;
  z-index: 1;
}
tbody tr.selected { background: #edf4ff; }

.row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
  align-items: flex-end;
  margin-bottom: 12px;
  min-width: 0;
}

.row > label {
  min-width: 0;
  margin-bottom: 0;
}

.row > div {
  min-width: 0;
}

.row > input,
.row > select,
.row > textarea {
  min-width: 0;
  width: 100%;
}

.row > .btn {
  width: fit-content;
  justify-self: start;
}

.row.tight {
  margin-bottom: 0;
  gap: 6px;
  display: inline-flex;
  width: auto;
  align-items: center;
}

.kb-col {
  border: 0;
  border-radius: 0;
  padding: 0;
  background: transparent;
  margin-bottom: 30px;
}

.kb-col > h3 {
  margin: 0 0 12px;
  padding: 0;
  font-size: 21px;
  font-weight: 800;
  color: #213956;
  background: transparent;
  border: 0;
  border-radius: 0;
}

.kb-col > h3:first-child {
  margin-top: 0;
}

.domain-section {
  margin-top: 26px;
  padding-top: 8px;
}

.domain-section-title {
  margin: 0 0 14px;
  font-size: 22px;
  font-weight: 800;
  color: #213956;
}

.domain-stack {
  display: grid;
  gap: 26px;
}

[data-menu-section] {
  margin-bottom: 16px;
}

.pager {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: end;
  margin-top: 10px;
}

@media (max-width: 900px) {
  .shell-body { grid-template-columns: 1fr; }
  .content-body { width: 100%; max-width: 100%; padding: 0 6px 24px; }
  .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .json-grid { grid-template-columns: 1fr; }
  .two-col { grid-template-columns: 1fr; }
  .grid.two, .grid.three { grid-template-columns: 1fr; }
  .global-filter-fields {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 680px) {
  .shell-root { grid-template-rows: 98px minmax(0, 1fr); }
}

@media (max-width: 520px) {
  .login-root { padding: 12px; }
  .login-card { padding: 14px; }
  .login-card h1 { font-size: 21px; line-height: 1.25; }
}

@media (prefers-reduced-motion: reduce) {
  .skip-link { transition: none; }
  .btn { transition: background-color 100ms ease, border-color 100ms ease; }
  .btn:active:not(:disabled) { transform: none; }
}

[data-keyboard-dismiss="true"],
[data-keyboard-dismiss="true"] * {
  animation-duration: 0s !important;
  transition-duration: 0s !important;
}

[data-interactive-row="true"] {
  cursor: pointer;
}

[data-interactive-row="true"]:focus-visible {
  outline: 2px solid var(--field-border-focus, #5b8fdd);
  outline-offset: -2px;
  box-shadow: inset 0 0 0 1px #fff;
}

@media (hover: hover) and (pointer: fine) {
  [data-interactive-row="true"]:hover {
    background-color: #f7faff;
  }
}
</style>
