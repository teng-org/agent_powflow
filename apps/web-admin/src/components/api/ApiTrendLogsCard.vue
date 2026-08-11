<script setup>
defineProps({
  apiSettingState: { type: Object, required: true },
})
</script>

<template>
  <div class="two-col">
    <div class="kb-col">
      <h3>30 天用量趋势（Chat API）</h3>
      <div class="table-wrap short">
        <table>
          <thead><tr><th>day</th><th>requests</th><th>errors</th><th>cost</th></tr></thead>
          <tbody>
            <tr v-for="(x, idx) in apiSettingState.usageTrends" :key="idx">
              <td>{{ x.day || x.date || '-' }}</td>
              <td>{{ x.request_count || x.requests || 0 }}</td>
              <td>{{ x.error_count || x.errors || 0 }}</td>
              <td>{{ x.cost_total || x.cost || 0 }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <div class="kb-col">
      <h3>调用日志聚合（Top 50）</h3>
      <div class="table-wrap short">
        <table>
          <thead><tr><th>tenant</th><th>task</th><th>endpoint</th><th>requests</th><th>errors</th></tr></thead>
          <tbody>
            <tr v-for="(x, idx) in apiSettingState.logGrouped" :key="idx">
              <td>{{ x.tenant_id || x.tenant || '-' }}</td>
              <td>{{ x.task || '-' }}</td>
              <td>{{ x.endpoint_id || x.endpoint || '-' }}</td>
              <td>{{ x.request_count || x.requests || 0 }}</td>
              <td>{{ x.error_count || x.errors || 0 }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
