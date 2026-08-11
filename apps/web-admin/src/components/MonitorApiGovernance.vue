<script setup>
import { computed, ref } from 'vue'
import vInteractiveRow from './common/interactiveRowDirective'
import UiPager from './common/UiPager.vue'

const props = defineProps({
  apiSettingState: { type: Object, required: true },
  loadApiObservability: { type: Function, required: true },
  loadChatApiInsights: { type: Function, required: true },
  loadPlatformUsageSummary: { type: Function, required: true },
  loadPlatformUsageEvents: { type: Function, required: true },
  loadPlatformAlerts: { type: Function, required: true },
  loadApiUsageGrouped: { type: Function, required: true },
  sectionDomId: { type: Function, default: null },
})

function secId(name) {
  return props.sectionDomId ? props.sectionDomId(name) : ''
}

const selectedHealth = ref(null)
const selectedAlert = ref(null)
const selectedUsage = ref(null)
const selectedPlatformAlert = ref(null)

const trendRows = computed(() => {
  const items = Array.isArray(props.apiSettingState.usageTrends) ? props.apiSettingState.usageTrends.slice(-14) : []
  const maxReq = Math.max(1, ...items.map((x) => Number(x.request_count || x.requests || 0)))
  const maxErr = Math.max(1, ...items.map((x) => Number(x.error_count || x.errors || 0)))
  return items.map((x) => {
    const req = Number(x.request_count || x.requests || 0)
    const err = Number(x.error_count || x.errors || 0)
    return {
      day: x.day || x.date || '-',
      req,
      err,
      reqPct: Math.max(3, Math.round((req / maxReq) * 100)),
      errPct: Math.max(3, Math.round((err / maxErr) * 100)),
    }
  })
})

const detailTitle = computed(() => {
  if (selectedPlatformAlert.value) return '详情：平台预算告警'
  if (selectedUsage.value) return '详情：平台用量汇总'
  if (selectedAlert.value) return '详情：规则告警'
  if (selectedHealth.value) return '详情：Chat API Health'
  return ''
})

const detailPayload = computed(() => {
  return selectedPlatformAlert.value || selectedUsage.value || selectedAlert.value || selectedHealth.value || {}
})

const hasDetailSelection = computed(() => !!(selectedPlatformAlert.value || selectedUsage.value || selectedAlert.value || selectedHealth.value))

const platformEventPageCount = computed(() =>
  Math.max(
    1,
    Math.ceil(
      Number(props.apiSettingState.platformUsage.eventsTotal || 0) /
        Math.max(1, Number(props.apiSettingState.filters.platformEvents.page_size || 50)),
    ),
  ),
)

async function setPlatformEventPage(page) {
  const f = props.apiSettingState.filters.platformEvents
  const next = Math.max(1, Math.min(platformEventPageCount.value, Number(page || 1)))
  if (next === Number(f.page || 1)) return
  f.page = next
  await props.loadPlatformUsageEvents()
}

async function refreshApiByFilters() {
  await Promise.all([props.loadApiUsageGrouped(), props.loadChatApiInsights(), props.loadApiObservability()])
}

async function refreshPlatformByFilters() {
  await Promise.all([props.loadPlatformUsageSummary(), props.loadPlatformUsageEvents(), props.loadPlatformAlerts()])
}
</script>

<template>
  <section class="content">
    <div class="monitor-grid" :class="{ 'has-detail': hasDetailSelection }">
      <div class="panel">
      <div class="panel-title">API 治理监控</div>
      <p class="panel-sub">端点健康、调用趋势、预算与告警的监控视图</p>
      <div class="row">
        <button class="btn ghost" :disabled="apiSettingState.loading" @click="loadApiObservability">刷新治理监控</button>
        <button class="btn ghost" :disabled="apiSettingState.loading" @click="loadChatApiInsights">刷新洞察</button>
        <button class="btn ghost" :disabled="apiSettingState.loading" @click="loadPlatformUsageSummary">刷新用量汇总</button>
        <button class="btn ghost" :disabled="apiSettingState.loading" @click="loadPlatformUsageEvents">刷新调用事件</button>
        <button class="btn ghost" :disabled="apiSettingState.loading" @click="loadPlatformAlerts">刷新预算告警</button>
      </div>
      <p class="banner err" v-if="apiSettingState.error">{{ apiSettingState.error }}</p>

      <div class="kb-col domain-section" :id="secId('filters')" data-menu-section="filters">
        <h2 class="domain-section-title">筛选区</h2>
        <h3>Chat API 筛选</h3>
        <div class="row filter-row">
          <label>day_from<input v-model="apiSettingState.filters.usage.day_from" type="date" /></label>
          <label>day_to<input v-model="apiSettingState.filters.usage.day_to" type="date" /></label>
          <label>tenant<input v-model="apiSettingState.filters.usage.tenant_id" placeholder="tenant_id" /></label>
          <label>task<input v-model="apiSettingState.filters.usage.task" placeholder="task" /></label>
          <label>endpoint<input v-model="apiSettingState.filters.usage.endpoint_id" placeholder="endpoint_id" /></label>
          <label>model<input v-model="apiSettingState.filters.usage.model" placeholder="model" /></label>
          <button class="btn ghost" :disabled="apiSettingState.loading" @click="refreshApiByFilters">按筛选刷新</button>
        </div>
      </div>

      <div class="kb-col">
        <h3>平台用量筛选</h3>
        <div class="row filter-row">
          <label>days<input v-model.number="apiSettingState.filters.platformUsage.days" type="number" min="1" /></label>
          <label>tenant_scope
            <select v-model="apiSettingState.filters.platformUsage.tenant_scope">
              <option value="current">current</option>
              <option value="all">all</option>
            </select>
          </label>
          <label>limit<input v-model.number="apiSettingState.filters.platformUsage.limit" type="number" min="1" /></label>
          <label>event page_size<input v-model.number="apiSettingState.filters.platformEvents.page_size" type="number" min="1" /></label>
          <button class="btn ghost" :disabled="apiSettingState.loading" @click="refreshPlatformByFilters">按筛选刷新</button>
        </div>
      </div>

      <div class="kpi-grid">
        <div class="kpi-card">
          <div class="kpi-name">Chat API 端点健康数</div>
          <div class="kpi-value">{{ apiSettingState.chatApiInsights.health?.length || 0 }}</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-name">近 30 天趋势点</div>
          <div class="kpi-value">{{ apiSettingState.usageTrends?.length || 0 }}</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-name">未确认告警（规则）</div>
          <div class="kpi-value">{{ apiSettingState.alertEvents?.length || 0 }}</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-name">预算告警事件</div>
          <div class="kpi-value">{{ apiSettingState.platformUsage.alerts?.length || 0 }}</div>
        </div>
      </div>

      <div class="kb-col domain-section" :id="secId('trend')" data-menu-section="trend">
        <h2 class="domain-section-title">趋势</h2>
        <h3>调用趋势（近 14 天）</h3>
        <div class="table-wrap short">
          <table>
            <thead><tr><th>day</th><th>requests</th><th>errors</th></tr></thead>
            <tbody>
              <tr v-for="x in trendRows" :key="x.day">
                <td>{{ x.day }}</td>
                <td>
                  <div class="trend-row">
                    <span>{{ x.req }}</span>
                    <span class="trend-bar"><i class="trend-fill req" :style="{ width: `${x.reqPct}%` }"></i></span>
                  </div>
                </td>
                <td>
                  <div class="trend-row">
                    <span>{{ x.err }}</span>
                    <span class="trend-bar"><i class="trend-fill err" :style="{ width: `${x.errPct}%` }"></i></span>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="domain-section" :id="secId('events')" data-menu-section="events">
        <h2 class="domain-section-title">事件与告警</h2>
      <div class="two-col">
        <div class="kb-col">
          <h3>Chat API Health</h3>
          <div class="table-wrap short">
            <table>
              <thead><tr><th>endpoint_id</th><th>ok</th><th>latency_ms</th><th>updated_at</th></tr></thead>
              <tbody>
                <tr
                  v-for="(x, idx) in apiSettingState.chatApiInsights.health"
                  :key="x.endpoint_id || x.endpointId || idx"
                  v-interactive-row="{ activate: () => { selectedHealth = x }, label: `查看 API 健康 ${x.endpoint_id || x.endpointId || ''}`, current: selectedHealth === x }"
                  :class="{ selected: selectedHealth === x }"
                  @click="selectedHealth = x"
                >
                  <td>{{ x.endpoint_id || x.endpointId || '-' }}</td>
                  <td>{{ x.ok ?? '-' }}</td>
                  <td>{{ x.latency_ms ?? x.latencyMs ?? '-' }}</td>
                  <td>{{ x.updated_at || x.updatedAt || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="kb-col">
          <h3>Chat API 规则告警事件</h3>
          <div class="table-wrap short">
            <table>
              <thead><tr><th>rule_id</th><th>severity</th><th>acked</th><th>triggered_at</th></tr></thead>
              <tbody>
                <tr
                  v-for="(x, idx) in apiSettingState.alertEvents"
                  :key="x.event_id || x.eventId || idx"
                  v-interactive-row="{ activate: () => { selectedAlert = x }, label: `查看规则告警 ${x.rule_id || x.ruleId || ''}`, current: selectedAlert === x }"
                  :class="{ selected: selectedAlert === x }"
                  @click="selectedAlert = x"
                >
                  <td>{{ x.rule_id || x.ruleId || '-' }}</td>
                  <td>{{ x.severity || '-' }}</td>
                  <td>{{ x.acked ?? '-' }}</td>
                  <td>{{ x.triggered_at || x.created_at || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
      </div>

      <div class="two-col">
        <div class="kb-col">
          <h3>平台 API Token 用量汇总</h3>
          <div class="table-wrap short">
            <table>
              <thead><tr><th>day</th><th>tenant</th><th>task</th><th>model</th><th>requests</th><th>cost</th></tr></thead>
              <tbody>
                <tr
                  v-for="(x, idx) in apiSettingState.platformUsage.summary"
                  :key="idx"
                  v-interactive-row="{ activate: () => { selectedUsage = x }, label: `查看平台用量 ${x.day || x.date || ''} ${x.tenant_id || x.tenant || ''}`, current: selectedUsage === x }"
                  :class="{ selected: selectedUsage === x }"
                  @click="selectedUsage = x"
                >
                  <td>{{ x.day || x.date || '-' }}</td>
                  <td>{{ x.tenant_id || x.tenant || '-' }}</td>
                  <td>{{ x.task_category || x.task || '-' }}</td>
                  <td>{{ x.model || '-' }}</td>
                  <td>{{ x.request_count || x.requests || '-' }}</td>
                  <td>{{ x.cost_total ?? x.cost ?? '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div class="kb-col">
          <h3>平台预算告警</h3>
          <div class="table-wrap short">
            <table>
              <thead><tr><th>alert_id</th><th>status</th><th>scope</th><th>created_at</th></tr></thead>
              <tbody>
                <tr
                  v-for="(x, idx) in apiSettingState.platformUsage.alerts"
                  :key="x.alert_id || x.alertId || idx"
                  v-interactive-row="{ activate: () => { selectedPlatformAlert = x }, label: `查看预算告警 ${x.alert_id || x.alertId || ''}`, current: selectedPlatformAlert === x }"
                  :class="{ selected: selectedPlatformAlert === x }"
                  @click="selectedPlatformAlert = x"
                >
                  <td>{{ x.alert_id || x.alertId || '-' }}</td>
                  <td>{{ x.status || '-' }}</td>
                  <td>{{ x.scope_type || x.scope || '-' }}:{{ x.scope_value || '-' }}</td>
                  <td>{{ x.created_at || x.createdAt || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="kb-col">
        <h3>平台调用事件（分页）</h3>
        <div class="table-wrap short">
          <table>
            <thead><tr><th>time</th><th>tenant</th><th>task</th><th>model</th><th>path</th><th>tokens</th><th>cost</th></tr></thead>
            <tbody>
              <tr v-for="(x, idx) in apiSettingState.platformUsage.events" :key="x.event_id || x.id || idx">
                <td>{{ x.ts || x.created_at || '-' }}</td>
                <td>{{ x.tenant_id || x.tenant || '-' }}</td>
                <td>{{ x.task_category || x.task || '-' }}</td>
                <td>{{ x.model || '-' }}</td>
                <td :title="x.api_path || x.path">{{ x.api_path || x.path || '-' }}</td>
                <td>{{ x.total_tokens ?? '-' }}</td>
                <td>{{ x.cost ?? '-' }}</td>
              </tr>
              <tr v-if="!apiSettingState.loading && !apiSettingState.platformUsage.events.length">
                <td colspan="7" class="empty" role="status" aria-live="polite" aria-atomic="true">当前筛选条件下暂无平台调用事件</td>
              </tr>
            </tbody>
          </table>
        </div>
        <UiPager
          :page="Number(apiSettingState.filters.platformEvents.page || 1)"
          :page-count="platformEventPageCount"
          :total-items="Number(apiSettingState.platformUsage.eventsTotal || 0)"
          :loading="apiSettingState.loading"
          aria-label="平台调用事件分页"
          @page="setPlatformEventPage"
        />
      </div>

      <div class="json-grid">
        <div>
          <h3>选中：Chat API Health</h3>
          <pre>{{ JSON.stringify(selectedHealth || {}, null, 2) }}</pre>
        </div>
        <div>
          <h3>选中：规则告警</h3>
          <pre>{{ JSON.stringify(selectedAlert || {}, null, 2) }}</pre>
        </div>
      </div>
      <div class="json-grid">
        <div>
          <h3>选中：平台用量汇总</h3>
          <pre>{{ JSON.stringify(selectedUsage || {}, null, 2) }}</pre>
        </div>
        <div>
          <h3>选中：平台预算告警</h3>
          <pre>{{ JSON.stringify(selectedPlatformAlert || {}, null, 2) }}</pre>
        </div>
      </div>
      </div>

      <aside
        v-if="hasDetailSelection"
        class="panel detail-panel domain-section"
        :id="secId('detail')"
        data-menu-section="detail"
      >
        <h2 class="domain-section-title">详情</h2>
        <div class="panel-title small">{{ detailTitle }}</div>
        <pre>{{ JSON.stringify(detailPayload, null, 2) }}</pre>
      </aside>
    </div>
  </section>
</template>

<style scoped>
.monitor-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
}

.monitor-grid.has-detail {
  grid-template-columns: minmax(0, 1fr) 360px;
}

.detail-panel {
  position: sticky;
  top: 12px;
  max-height: calc(100vh - 24px);
  overflow: auto;
}

.panel-title.small {
  font-size: 16px;
}

.filter-row {
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
}

.filter-row > .btn {
  width: 100%;
  justify-self: stretch;
}

.trend-row {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 8px;
  align-items: center;
}

.trend-bar {
  display: block;
  width: 100%;
  height: 8px;
  background: #edf3fb;
  border-radius: 999px;
  overflow: hidden;
}

.trend-fill {
  display: block;
  height: 100%;
}

.trend-fill.req {
  background: linear-gradient(90deg, #5a8fe8, #7eb0ff);
}

.trend-fill.err {
  background: linear-gradient(90deg, #e17f7f, #ef9b9b);
}

@media (max-width: 1400px) {
  .monitor-grid {
    grid-template-columns: 1fr;
  }

  .detail-panel {
    position: static;
    max-height: none;
  }
}

@media (min-width: 1200px) {
  .filter-row > .btn {
    width: auto;
    justify-self: end;
  }
}
</style>
