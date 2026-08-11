<script setup>
defineProps({
  apiSettingState: { type: Object, required: true },
  evaluateAlertRules: { type: Function, required: true },
  ackAlertEvent: { type: Function, required: true },
  upsertAlertRule: { type: Function, required: true },
  resetAlertRuleForm: { type: Function, required: true },
  loadAlertRuleToForm: { type: Function, required: true },
  deleteAlertRule: { type: Function, required: true },
  loadApiObservability: { type: Function, required: true },
})
</script>

<template>
  <div class="kb-col">
    <h3>告警事件（最新 50）</h3>
    <div class="row">
      <label>rule_id<input v-model="apiSettingState.filters.alerts.rule_id" placeholder="rule_id" /></label>
      <label>severity<input v-model="apiSettingState.filters.alerts.severity" placeholder="warning/critical" /></label>
      <label>acked
        <select v-model="apiSettingState.filters.alerts.acked">
          <option value="">all</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      </label>
      <button class="btn ghost" @click="loadApiObservability">应用筛选</button>
      <button class="btn ghost" @click="evaluateAlertRules">手动评估告警</button>
    </div>
    <div class="table-wrap mini">
      <table>
        <thead><tr><th>rule</th><th>severity</th><th>acked</th><th>time</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="(e, idx) in apiSettingState.alertEvents" :key="idx">
            <td>{{ e.rule_name || e.rule_id || '-' }}</td>
            <td>{{ e.severity || '-' }}</td>
            <td>{{ e.acked ? 'yes' : 'no' }}</td>
            <td>{{ e.created_at || e.createdAt || '-' }}</td>
            <td><button class="btn ghost" :disabled="!!e.acked" @click="ackAlertEvent(e)">确认</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <h3>告警规则</h3>
    <div class="grid three">
      <label>rule_id<input v-model="apiSettingState.alertRuleForm.rule_id" placeholder="留空则新增" /></label>
      <label>name<input v-model="apiSettingState.alertRuleForm.name" /></label>
      <label>metric<input v-model="apiSettingState.alertRuleForm.metric" /></label>
    </div>
    <div class="grid three">
      <label>scope<input v-model="apiSettingState.alertRuleForm.scope" /></label>
      <label>tenant_id<input v-model="apiSettingState.alertRuleForm.tenant_id" /></label>
      <label>task<input v-model="apiSettingState.alertRuleForm.task" /></label>
    </div>
    <div class="grid three">
      <label>endpoint_id<input v-model="apiSettingState.alertRuleForm.endpoint_id" /></label>
      <label>model<input v-model="apiSettingState.alertRuleForm.model" /></label>
      <label>comparison
        <select v-model="apiSettingState.alertRuleForm.comparison">
          <option value="gte">gte</option>
          <option value="gt">gt</option>
          <option value="lte">lte</option>
          <option value="lt">lt</option>
        </select>
      </label>
    </div>
    <div class="grid three">
      <label>threshold<input v-model.number="apiSettingState.alertRuleForm.threshold" type="number" step="0.0001" /></label>
      <label>window_days<input v-model.number="apiSettingState.alertRuleForm.window_days" type="number" /></label>
      <label>cooldown_seconds<input v-model.number="apiSettingState.alertRuleForm.cooldown_seconds" type="number" /></label>
    </div>
    <div class="grid three">
      <label>severity
        <select v-model="apiSettingState.alertRuleForm.severity">
          <option value="warning">warning</option>
          <option value="critical">critical</option>
        </select>
      </label>
      <label>enabled
        <select v-model="apiSettingState.alertRuleForm.enabled">
          <option :value="true">true</option>
          <option :value="false">false</option>
        </select>
      </label>
      <div class="action-wrap">
        <button class="btn primary" @click="upsertAlertRule">保存规则</button>
        <button class="btn ghost" @click="resetAlertRuleForm">重置</button>
      </div>
    </div>

    <div class="table-wrap short">
      <table>
        <thead><tr><th>rule_id</th><th>name</th><th>metric</th><th>enabled</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="r in apiSettingState.alertRules" :key="r.rule_id || r.ruleId">
            <td>{{ r.rule_id || r.ruleId }}</td>
            <td>{{ r.name || '-' }}</td>
            <td>{{ r.metric || '-' }}</td>
            <td>{{ r.enabled ? 'true' : 'false' }}</td>
            <td><div class="row tight">
              <button class="btn ghost" @click="loadAlertRuleToForm(r)">编辑</button>
              <button class="btn ghost danger-text" @click="deleteAlertRule(r)">删除</button>
            </div></td>
          </tr>
        </tbody>
      </table>
    </div>

    <h3>规则命中统计</h3>
    <div class="table-wrap mini">
      <table>
        <thead><tr><th>rule_id</th><th>hits</th><th>last_hit_at</th></tr></thead>
        <tbody>
          <tr v-for="(s, idx) in apiSettingState.alertRuleStats" :key="idx">
            <td>{{ s.rule_id || s.ruleId || '-' }}</td>
            <td>{{ s.hit_count || s.hits || 0 }}</td>
            <td>{{ s.last_hit_at || s.lastHitAt || '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <h3>日志筛选</h3>
    <div class="row">
      <label>tenant<input v-model="apiSettingState.filters.logs.tenant_id" placeholder="tenant" /></label>
      <label>task<input v-model="apiSettingState.filters.logs.task" placeholder="task" /></label>
      <label>endpoint<input v-model="apiSettingState.filters.logs.endpoint_id" placeholder="endpoint" /></label>
      <label>ok
        <select v-model="apiSettingState.filters.logs.ok">
          <option value="">all</option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      </label>
      <button class="btn ghost" @click="loadApiObservability">应用日志筛选</button>
    </div>
  </div>
</template>
