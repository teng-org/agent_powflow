<script setup>
defineProps({
  apiSettingState: { type: Object, required: true },
  loadApiUsageGrouped: { type: Function, required: true },
})
</script>

<template>
  <div class="kb-col">
    <h3>用量分组统计（分页）</h3>
    <div class="row">
      <label>Tenant<input v-model="apiSettingState.filters.usage.tenant_id" placeholder="tenant" /></label>
      <label>Task<input v-model="apiSettingState.filters.usage.task" placeholder="task" /></label>
      <label>Endpoint<input v-model="apiSettingState.filters.usage.endpoint_id" placeholder="endpoint" /></label>
      <label>Model<input v-model="apiSettingState.filters.usage.model" placeholder="model" /></label>
    </div>
    <div class="row">
      <label>Day From<input v-model="apiSettingState.filters.usage.day_from" type="date" /></label>
      <label>Day To<input v-model="apiSettingState.filters.usage.day_to" type="date" /></label>
      <label>Page<input v-model.number="apiSettingState.usagePage" type="number" min="1" /></label>
      <label>Page Size
        <select v-model.number="apiSettingState.usagePageSize">
          <option :value="20">20</option>
          <option :value="50">50</option>
          <option :value="100">100</option>
        </select>
      </label>
      <button class="btn ghost" @click="loadApiUsageGrouped">刷新分组</button>
    </div>
    <div class="table-wrap short">
      <table>
        <thead><tr><th>tenant</th><th>task</th><th>model</th><th>endpoint</th><th>requests</th><th>errors</th><th>cost</th></tr></thead>
        <tbody>
          <tr v-for="(x, idx) in apiSettingState.usageGrouped" :key="idx">
            <td>{{ x.tenant_id || x.tenant || '-' }}</td>
            <td>{{ x.task || '-' }}</td>
            <td>{{ x.model || '-' }}</td>
            <td>{{ x.endpoint_id || '-' }}</td>
            <td>{{ x.request_count || 0 }}</td>
            <td>{{ x.error_count || 0 }}</td>
            <td>{{ x.cost_total || 0 }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="pager"><span>总记录：{{ apiSettingState.usageTotal }}</span></div>
  </div>
</template>
