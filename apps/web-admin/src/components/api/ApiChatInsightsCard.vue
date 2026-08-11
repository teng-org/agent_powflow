<script setup>
defineProps({
  apiSettingState: { type: Object, required: true },
  loadChatApiInsights: { type: Function, required: true },
})

function circuitLabel(value) {
  return {
    CLOSED: '正常',
    OPEN: '已熔断',
    HALF_OPEN: '半开探测',
  }[String(value || '').toUpperCase()] || '未知'
}

function circuitTone(value) {
  return {
    CLOSED: 'healthy',
    OPEN: 'blocked',
    HALF_OPEN: 'probing',
  }[String(value || '').toUpperCase()] || 'unknown'
}

function formatLatency(value) {
  const latency = Number(value)
  return Number.isFinite(latency) ? `${latency.toFixed(1)} ms` : '-'
}
</script>

<template>
  <div class="kb-col">
    <h3>Chat API 洞察与健康</h3>
    <div class="row">
      <button class="btn ghost" @click="loadChatApiInsights">刷新洞察</button>
    </div>

    <h3>健康状态</h3>
    <div class="table-wrap mini">
      <table>
        <thead><tr><th>端点</th><th>消费点</th><th>熔断状态</th><th>并发</th><th>延迟 EMA</th><th>连续失败</th><th>累计成功 / 失败</th></tr></thead>
        <tbody>
          <tr v-for="(h, idx) in apiSettingState.chatApiInsights.health" :key="h.endpoint_id || idx">
            <td>{{ h.endpoint_id || h.endpoint || '-' }}</td>
            <td>{{ h.task || '-' }}</td>
            <td><span class="circuit-badge" :class="`is-${circuitTone(h.circuit_state)}`">{{ circuitLabel(h.circuit_state) }}</span></td>
            <td>{{ Number(h.inflight || 0) }} / {{ Number(h.max_concurrency || 0) }}</td>
            <td>{{ formatLatency(h.latency_ema_ms) }}</td>
            <td>{{ Number(h.consecutive_failures || 0) }}</td>
            <td>{{ Number(h.success_count || 0) }} / {{ Number(h.fail_count || 0) }}</td>
          </tr>
          <tr v-if="!apiSettingState.chatApiInsights.health.length"><td colspan="7" class="empty-health">暂无 Chat API 端点健康数据</td></tr>
        </tbody>
      </table>
    </div>

    <h3>Top Insights</h3>
    <div class="table-wrap mini">
      <table>
        <thead><tr><th>metric</th><th>scope</th><th>value</th></tr></thead>
        <tbody>
          <tr v-for="(i, idx) in apiSettingState.chatApiInsights.insights" :key="idx">
            <td>{{ i.metric || '-' }}</td>
            <td>{{ i.scope || i.name || '-' }}</td>
            <td>{{ i.value ?? '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <h3>按天用量</h3>
    <div class="table-wrap short">
      <table>
        <thead><tr><th>day</th><th>requests</th><th>errors</th><th>cost</th></tr></thead>
        <tbody>
          <tr v-for="(d, idx) in apiSettingState.chatApiInsights.usageDaily" :key="idx">
            <td>{{ d.day || d.date || '-' }}</td>
            <td>{{ d.request_count || d.requests || 0 }}</td>
            <td>{{ d.error_count || d.errors || 0 }}</td>
            <td>{{ d.cost_total || d.cost || 0 }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <h3>Overview / Dashboard</h3>
    <div class="two-col">
      <pre>{{ JSON.stringify(apiSettingState.chatApiInsights.overview || {}, null, 2) }}</pre>
      <pre>{{ JSON.stringify(apiSettingState.chatApiInsights.dashboard || {}, null, 2) }}</pre>
    </div>
  </div>
</template>

<style scoped>
.circuit-badge {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 2px 9px;
  border: 1px solid var(--line, #d8dee9);
  border-radius: 999px;
  font-size: 12px;
  font-weight: 650;
  white-space: nowrap;
}

.circuit-badge.is-healthy {
  color: #17663a;
  background: #eaf8ef;
  border-color: #a8dfbc;
}

.circuit-badge.is-probing {
  color: #7a5200;
  background: #fff6d8;
  border-color: #ebcf72;
}

.circuit-badge.is-blocked {
  color: #a12626;
  background: #fff0f0;
  border-color: #edb2b2;
}

.circuit-badge.is-unknown {
  color: #596273;
  background: #f4f6f8;
}

.empty-health {
  padding: 24px 12px;
  color: var(--muted, #667085);
  text-align: center;
}
</style>
