<script setup>
import { computed, onMounted } from 'vue'
import { useCrossStoreRepairs } from '../composables/useCrossStoreRepairs'
import UiPager from './common/UiPager.vue'

const props = defineProps({
  dbState: { type: Object, required: true },
  dbColumns: { type: Array, required: true },
  dbRows: { type: Array, required: true },
  dbTotal: { type: Number, required: true },
  loadDbRows: { type: Function, required: true },
  fetchJson: { type: Function, required: true },
})

const repairs = useCrossStoreRepairs(props.fetchJson)
const dbPageCount = computed(() => Math.max(1, Math.ceil(Number(props.dbTotal || 0) / Math.max(1, Number(props.dbState.pageSize || 20)))))
const dbPageStart = computed(() => (props.dbRows.length ? (Number(props.dbState.page || 1) - 1) * Number(props.dbState.pageSize || 20) + 1 : 0))
const dbPageEnd = computed(() => (props.dbRows.length ? dbPageStart.value + props.dbRows.length - 1 : 0))

function goDbPage(page) {
  const next = Math.max(1, Math.min(dbPageCount.value, Number(page || 1)))
  if (next === Number(props.dbState.page || 1)) return
  props.dbState.page = next
  props.loadDbRows()
}

function when(value) {
  return value ? new Date(value).toLocaleString() : '—'
}

function statusLabel(status) {
  return { PENDING: '待执行', IN_PROGRESS: '执行中', SUCCEEDED: '已完成', FAILED: '失败' }[status] || status || '未知'
}

function operationLabel(operation) {
  return {
    MOVE_DOCUMENT_POINTS: '移动文档向量对账',
    DELETE_DOCUMENT_POINTS: '删除文档向量',
    DELETE_COLLECTION: '删除向量集合',
    DELETE_OBJECT: '删除对象',
  }[operation] || operation || '未知操作'
}

function duration(seconds) {
  const value = Math.max(0, Number(seconds || 0))
  if (value < 60) return `${Math.round(value)} 秒`
  if (value < 3600) return `${Math.round(value / 60)} 分钟`
  if (value < 86400) return `${Math.round(value / 3600)} 小时`
  return `${Math.round(value / 86400)} 天`
}

function bytes(value) {
  const size = Math.max(0, Number(value || 0))
  if (size < 1024) return `${Math.round(size)} B`
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KiB`
  if (size < 1024 ** 3) return `${(size / 1024 ** 2).toFixed(1)} MiB`
  if (size < 1024 ** 4) return `${(size / 1024 ** 3).toFixed(2)} GiB`
  return `${(size / 1024 ** 4).toFixed(2)} TiB`
}

function percent(value) {
  return value == null ? '—' : `${(Number(value || 0) * 100).toFixed(1)}%`
}

async function executeRepairs() {
  if (!window.confirm(`确认执行最多 50 条跨存储修复义务？当前可执行 ${repairs.actionableCount.value} 条。`)) return
  await repairs.execute(50)
}

async function executeRepair(item) {
  if (!window.confirm(`确认只重试修复 #${item.repair_id}（${operationLabel(item.operation)}）？`)) return
  await repairs.executeOne(item.repair_id)
}

onMounted(() => repairs.loadAll())
</script>

<template>
  <section class="content">
    <div class="panel">
      <div class="panel-title">数据库监控</div>
      <p class="panel-sub">Execution / Control PostgreSQL 可视化</p>
      <div class="row">
        <button class="btn ghost" :class="{ active: dbState.source === 'execution' }" @click="dbState.source = 'execution'; dbState.page = 1; loadDbRows()">Execution PG</button>
        <button class="btn ghost" :class="{ active: dbState.source === 'control' }" @click="dbState.source = 'control'; dbState.page = 1; loadDbRows()">Control PG</button>
      </div>
      <p class="banner err" v-if="dbState.error">{{ dbState.error }}</p>
      <div class="row">
        <label v-if="dbState.source === 'execution'">Execution Table
          <select v-model="dbState.selectedExecutionTable" @change="dbState.page = 1; loadDbRows()">
            <option v-for="t in dbState.executionTables" :key="t" :value="t">{{ t }}</option>
          </select>
        </label>
        <label v-if="dbState.source === 'control'">Control Table
          <select v-model="dbState.selectedControlTable" @change="dbState.page = 1; loadDbRows()">
            <option v-for="t in dbState.controlTables" :key="t" :value="t">{{ t }}</option>
          </select>
        </label>
        <label>Page Size
          <select v-model.number="dbState.pageSize" @change="dbState.page = 1; loadDbRows()">
            <option :value="20">20</option>
            <option :value="50">50</option>
            <option :value="100">100</option>
          </select>
        </label>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th v-for="col in dbColumns" :key="col">{{ col }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in dbRows" :key="index">
              <td v-for="col in dbColumns" :key="col" :title="String(row[col] ?? '')">{{ String(row[col] ?? '') }}</td>
            </tr>
            <tr v-if="!dbState.loading && !dbRows.length">
              <td :colspan="Math.max(1, dbColumns.length)" class="empty" role="status" aria-live="polite" aria-atomic="true">当前表暂无记录</td>
            </tr>
          </tbody>
        </table>
      </div>
      <UiPager
        :page="Number(dbState.page || 1)"
        :page-count="dbPageCount"
        :total-items="dbTotal"
        :start-item="dbPageStart"
        :end-item="dbPageEnd"
        aria-label="数据库记录分页"
        @page="goDbPage"
      />
    </div>

    <div class="panel repair-panel">
      <div class="repair-heading">
        <div>
          <div class="panel-title">跨存储一致性修复</div>
          <p class="panel-sub">追踪 PostgreSQL 提交后未完成的 Qdrant / MinIO 清理义务；记录持久化，可安全重试。</p>
        </div>
        <div class="repair-actions">
          <label>状态
            <select v-model="repairs.state.status" @change="repairs.load()">
              <option value="">全部</option>
              <option value="PENDING">待执行</option>
              <option value="IN_PROGRESS">执行中</option>
              <option value="FAILED">失败</option>
              <option value="SUCCEEDED">已完成</option>
            </select>
          </label>
          <button class="btn ghost" :disabled="repairs.state.loading || repairs.state.statsLoading || repairs.state.executing" @click="repairs.loadAll()">
            {{ repairs.state.loading ? '刷新中…' : '刷新' }}
          </button>
          <button class="btn primary" :disabled="repairs.state.executing || repairs.actionableCount.value === 0" @click="executeRepairs">
            {{ repairs.state.executing ? '修复中…' : `执行修复 (${repairs.actionableCount.value})` }}
          </button>
        </div>
      </div>

      <div v-if="repairs.state.stats" class="repair-health" :class="`health-${String(repairs.state.stats.health || '').toLowerCase()}`">
        <div>
          <span class="health-label">{{ repairs.state.stats.health }}</span>
          <strong>跨存储修复健康状态</strong>
          <p>{{ repairs.state.stats.health_reasons?.join('；') || '当前没有未完成的远端清理义务。' }}</p>
        </div>
        <dl>
          <div><dt>未完成</dt><dd>{{ repairs.state.stats.open_count }}</dd></div>
          <div><dt>最老积压</dt><dd>{{ duration(repairs.state.stats.oldest_open_age_seconds) }}</dd></div>
          <div><dt>累计尝试</dt><dd>{{ repairs.state.stats.total_attempts }}</dd></div>
          <div><dt>失败率</dt><dd>{{ (Number(repairs.state.stats.failure_ratio || 0) * 100).toFixed(1) }}%</dd></div>
        </dl>
      </div>

      <div v-if="repairs.state.objectStorage" class="storage-health" :class="`health-${String(repairs.state.objectStorage.health || '').toLowerCase()}`">
        <div>
          <span class="health-label">{{ repairs.state.objectStorage.health }}</span>
          <strong>MinIO 治理状态</strong>
          <p>{{ repairs.state.objectStorage.reasons?.join('；') || '版本化、生命周期和 TLS 控制均已配置。' }}</p>
        </div>
        <dl>
          <div><dt>Bucket</dt><dd class="mono">{{ repairs.state.objectStorage.bucket }}</dd></div>
          <div><dt>版本化</dt><dd>{{ repairs.state.objectStorage.versioning_status }}</dd></div>
          <div><dt>生命周期规则</dt><dd>{{ repairs.state.objectStorage.lifecycle_rule_count }}</dd></div>
          <div><dt>托管规则</dt><dd>{{ repairs.state.objectStorage.managed_lifecycle_rule_count || 0 }}</dd></div>
          <div><dt>旧版本策略</dt><dd>{{ repairs.state.objectStorage.noncurrent_expiry_days || '—' }} 天 / 保留 {{ repairs.state.objectStorage.retain_noncurrent_versions || '—' }} 个</dd></div>
          <div><dt>TLS</dt><dd>{{ repairs.state.objectStorage.secure_transport ? '启用' : '未启用' }}</dd></div>
        </dl>
      </div>

      <div
        v-if="repairs.state.objectStorage?.capacity"
        class="capacity-health"
        :class="`health-${String(repairs.state.objectStorage.capacity.health || '').toLowerCase()}`"
      >
        <div class="capacity-heading">
          <div>
            <span class="health-label">{{ repairs.state.objectStorage.capacity.health }}</span>
            <strong>对象存储逻辑容量</strong>
            <p>{{ repairs.state.objectStorage.capacity.health_reasons?.join('；') || '租户容量处于安全阈值内。' }}</p>
          </div>
          <dl>
            <div><dt>已提交</dt><dd>{{ bytes(repairs.state.objectStorage.capacity.used_bytes) }}</dd></div>
            <div><dt>上传预留</dt><dd>{{ bytes(repairs.state.objectStorage.capacity.reserved_bytes) }}</dd></div>
            <div><dt>单租户配额</dt><dd>{{ repairs.state.objectStorage.capacity.quota_enabled ? bytes(repairs.state.objectStorage.capacity.quota_bytes) : '未配置' }}</dd></div>
            <div><dt>最高水位</dt><dd>{{ percent(repairs.state.objectStorage.capacity.max_usage_ratio) }}</dd></div>
          </dl>
        </div>
        <div v-if="repairs.state.objectStorage.capacity.items?.length" class="capacity-table-wrap">
          <table class="capacity-table">
            <thead><tr><th>租户</th><th>文档</th><th>源文件</th><th>解析文本</th><th>预留</th><th>水位</th></tr></thead>
            <tbody>
              <tr v-for="item in repairs.state.objectStorage.capacity.items.slice(0, 8)" :key="item.tenant_id">
                <td class="mono">{{ item.tenant_id }}</td>
                <td>{{ item.document_count }}</td>
                <td>{{ bytes(item.source_bytes) }}</td>
                <td>{{ bytes(item.extracted_bytes) }}</td>
                <td>{{ bytes(item.reserved_bytes) }}</td>
                <td>{{ percent(item.usage_ratio) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="repairs.state.objectStorage.capacity.truncated || repairs.state.objectStorage.capacity.items.length > 8" class="capacity-note">仅展示容量最高的前 8 个租户。</p>
        </div>
      </div>

      <div
        v-if="repairs.state.objectStorage?.physical_capacity"
        class="physical-health"
        :class="`health-${String(repairs.state.objectStorage.physical_capacity.health || '').toLowerCase()}`"
      >
        <div class="capacity-heading">
          <div>
            <span class="health-label">{{ repairs.state.objectStorage.physical_capacity.health }}</span>
            <strong>MinIO 物理容量</strong>
            <p>{{ repairs.state.objectStorage.physical_capacity.health_reasons?.join('；') || 'Prometheus 抓取正常，磁盘和节点处于安全状态。' }}</p>
          </div>
          <dl>
            <div><dt>可用空间</dt><dd>{{ repairs.state.objectStorage.physical_capacity.configured ? bytes(repairs.state.objectStorage.physical_capacity.usable_free_bytes) : '未配置' }}</dd></div>
            <div><dt>可用总量</dt><dd>{{ repairs.state.objectStorage.physical_capacity.configured ? bytes(repairs.state.objectStorage.physical_capacity.usable_total_bytes) : '—' }}</dd></div>
            <div><dt>剩余比例</dt><dd>{{ percent(repairs.state.objectStorage.physical_capacity.usable_free_ratio) }}</dd></div>
            <div><dt>节点 / 磁盘</dt><dd>{{ Number(repairs.state.objectStorage.physical_capacity.node_online || 0) }} / {{ Number(repairs.state.objectStorage.physical_capacity.drive_online || 0) }}</dd></div>
            <div><dt>写入准入</dt><dd>{{ repairs.state.objectStorage.physical_capacity.write_admission_enforced ? '强制' : '仅监控' }}</dd></div>
          </dl>
        </div>
        <p v-if="repairs.state.objectStorage.physical_capacity.configured" class="capacity-note">
          告警阈值：剩余 {{ percent(repairs.state.objectStorage.physical_capacity.warn_free_ratio) }} 警告，{{ percent(repairs.state.objectStorage.physical_capacity.critical_free_ratio) }} 严重；
          离线节点 {{ Number(repairs.state.objectStorage.physical_capacity.node_offline || 0) }}，离线磁盘 {{ Number(repairs.state.objectStorage.physical_capacity.drive_offline || 0) }}。
        </p>
      </div>

      <div class="repair-stats" aria-label="跨存储修复状态统计">
        <span>待执行 <strong>{{ repairs.statusCounts.value.PENDING }}</strong></span>
        <span>执行中 <strong>{{ repairs.statusCounts.value.IN_PROGRESS }}</strong></span>
        <span>失败 <strong>{{ repairs.statusCounts.value.FAILED }}</strong></span>
        <span>已完成 <strong>{{ repairs.statusCounts.value.SUCCEEDED }}</strong></span>
      </div>
      <p v-if="repairs.state.message" class="banner ok">{{ repairs.state.message }}</p>
      <p v-if="repairs.state.error" class="banner err">{{ repairs.state.error }}</p>

      <div class="table-wrap">
        <table>
          <thead><tr><th>状态</th><th>存储 / 操作</th><th>资源</th><th>尝试</th><th>最后错误</th><th>更新时间</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="item in repairs.state.items" :key="item.repair_id">
              <td><span class="repair-status" :class="`is-${String(item.status || '').toLowerCase()}`">{{ statusLabel(item.status) }}</span></td>
              <td><strong>{{ item.store_name }}</strong><br><span>{{ operationLabel(item.operation) }}</span><br><span class="mono">{{ item.operation }}</span></td>
              <td class="resource-cell">
                <template v-if="item.operation === 'MOVE_DOCUMENT_POINTS'">
                  <span class="mono">源 KB {{ item.payload_json?.source_kb_id || item.kb_id }}</span><br>
                  <span class="mono">目标 KB {{ item.payload_json?.target_kb_id || '—' }}</span><br>
                  <span class="mono">Doc {{ item.doc_id }}</span><br>
                  <small>发布屏障：{{ item.payload_json?.original_status || '—' }}</small>
                </template>
                <template v-else>
                  <span class="mono">KB {{ item.kb_id }}</span><br><span v-if="item.doc_id" class="mono">Doc {{ item.doc_id }}</span>
                </template>
              </td>
              <td>{{ item.attempts }}</td>
              <td class="error-cell" :title="item.last_error || ''">{{ item.last_error || '—' }}</td>
              <td>{{ when(item.updated_at) }}</td>
              <td>
                <button
                  v-if="item.status === 'PENDING' || item.status === 'FAILED'"
                  class="btn ghost"
                  :disabled="repairs.state.executing || repairs.state.executingId !== null"
                  @click="executeRepair(item)"
                >{{ repairs.state.executingId === item.repair_id ? '重试中…' : '重试此项' }}</button>
                <span v-else>—</span>
              </td>
            </tr>
            <tr v-if="!repairs.state.loading && !repairs.state.items.length"><td colspan="7" class="empty" role="status" aria-live="polite" aria-atomic="true">当前筛选条件下没有修复义务</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<style scoped>
.repair-panel{margin-top:16px}.repair-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}.repair-actions{display:flex;align-items:end;justify-content:flex-end;gap:8px;flex-wrap:wrap}.repair-health,.storage-health,.capacity-health,.physical-health{margin:16px 0;padding:14px;border:1px solid rgba(127,127,127,.18);border-radius:12px;background:rgba(127,127,127,.05)}.repair-health,.storage-health,.capacity-heading{display:flex;justify-content:space-between;align-items:center;gap:18px}.repair-health>div>p,.storage-health>div>p,.capacity-heading>div>p{margin:5px 0 0;color:var(--text-muted,#667085);font-size:13px}.health-label{display:inline-flex;margin-right:9px;padding:3px 8px;border-radius:999px;font-size:12px;font-weight:800;background:rgba(18,183,106,.14);color:#067647}.health-degraded .health-label,.health-unconfigured .health-label,.health-warn .health-label{background:rgba(247,144,9,.15);color:#b54708}.health-critical .health-label{background:rgba(240,68,56,.14);color:#b42318}.repair-health dl,.storage-health dl,.capacity-heading dl{display:flex;gap:18px;margin:0}.repair-health dl div,.storage-health dl div,.capacity-heading dl div{min-width:70px}.repair-health dt,.storage-health dt,.capacity-heading dt{font-size:12px;color:var(--text-muted,#667085)}.repair-health dd,.storage-health dd,.capacity-heading dd{margin:3px 0 0;font-weight:750}.capacity-table-wrap{margin-top:14px;overflow-x:auto;border-top:1px solid rgba(127,127,127,.16);padding-top:12px}.capacity-table{width:100%;font-size:13px}.capacity-table th,.capacity-table td{padding:7px 9px;text-align:left;white-space:nowrap}.capacity-note{margin:8px 0 0;color:var(--text-muted,#667085);font-size:12px}.repair-stats{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}.repair-stats span{padding:7px 10px;border-radius:8px;background:rgba(127,127,127,.08);font-size:13px}.repair-status{display:inline-flex;padding:3px 8px;border-radius:999px;font-size:12px;font-weight:700;background:rgba(127,127,127,.12)}.repair-status.is-failed{color:#b42318;background:rgba(240,68,56,.12)}.repair-status.is-pending{color:#b54708;background:rgba(247,144,9,.14)}.repair-status.is-succeeded{color:#067647;background:rgba(18,183,106,.12)}.repair-status.is-in_progress{color:#175cd3;background:rgba(46,144,250,.12)}.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}.resource-cell{max-width:260px;overflow-wrap:anywhere}.error-cell{max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.empty{text-align:center;padding:28px;color:var(--text-muted,#667085)}@media(max-width:860px){.repair-heading{display:block}.repair-actions{justify-content:flex-start;margin-top:12px}.repair-health,.storage-health,.capacity-heading{display:block}.repair-health dl,.storage-health dl,.capacity-heading dl{margin-top:14px;display:grid;grid-template-columns:repeat(2,1fr)}}
</style>
