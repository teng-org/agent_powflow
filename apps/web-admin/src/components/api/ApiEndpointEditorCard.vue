<script setup>
import { computed } from 'vue'

const props = defineProps({
  apiSettingState: { type: Object, required: true },
  resetApiForm: { type: Function, required: true },
  upsertApiEndpoint: { type: Function, required: true },
  deleteApiEndpoint: { type: Function, required: true },
})

const fieldHelp = {
  pool: '选择端点所属资源池。5173 对话主回复必须使用 chat 池的 chat_llm。',
  apiInstanceId: '同一个供应商账号或同一个 API Key 的稳定标识。同实例下新增端点可继承已有 Key。',
  endpointId: '端点唯一 ID。编辑已有端点时保持不变；新增端点时不要与现有 ID 重复。',
  provider: '供应商标识，仅用于分类和观测。OpenAI 兼容接口可填写 openai_compatible 或供应商名。',
  baseUrl: 'OpenAI 兼容接口根地址，通常以 /v1 结尾。',
  task: '消费点决定业务用途。对话主回复是 chat_llm，辅助调用是 chat_aux/chat_summary，向量是 chat_embedding。',
  apiKey: '只在保存时提交，列表不会回显明文。保存成功后应出现 key_fingerprint；留空表示沿用旧 Key 或同实例继承。',
  tenant: '* 表示所有租户；填写具体租户 ID 时仅该租户可用。',
  priority: '数值越小优先级越高。',
  weight: '同优先级内的流量权重。',
  concurrency: '该端点允许同时进行的请求数。',
  timeout: '请求上游 API 的超时时间。',
  retry: '遇到超时、断连、429 或 5xx 时的最大重试次数。',
  circuit: '连续失败达到阈值后临时熔断，恢复秒数后再尝试。',
  price: '用于用量和成本统计，不影响实际 API 调用。',
}

function helpTitle(key) {
  return fieldHelp[key] || ''
}

const consumerOptionsForEditPool = computed(() => {
  const fromState = Array.isArray(props.apiSettingState.consumerEndpoints)
    ? props.apiSettingState.consumerEndpoints
    : []
  const fallback = [
    { task: 'rerank', pool: 'main', label: 'RAG 候选重排' },
    { task: 'embedding', pool: 'main', label: '知识库向量嵌入' },
    { task: 'llm', pool: 'main', label: '执行平面通用 LLM' },
    { task: 'summary_llm', pool: 'aux', label: '文档/知识库总结 LLM' },
    { task: 'summary_embedding', pool: 'aux', label: '总结辅助 Embedding' },
    { task: 'chat_llm', pool: 'chat', label: '对话主回复 LLM' },
    { task: 'chat_aux', pool: 'chat', label: '对话辅助 LLM' },
    { task: 'chat_summary', pool: 'chat', label: '对话记忆总结 LLM' },
    { task: 'chat_embedding', pool: 'chat', label: '对话记忆 Embedding' },
  ]
  return (fromState.length ? fromState : fallback)
    .filter((item) => String(item?.pool || '') === props.apiSettingState.editKind)
    .map((item) => ({
      task: String(item?.task || '').trim(),
      label: String(item?.label || item?.task || '').trim(),
    }))
    .filter((item) => item.task)
})
</script>

<template>
  <div class="kb-col">
    <h3>端点编辑器</h3>
    <div class="row">
      <label><span class="endpoint-label-row">Pool<button type="button" class="endpoint-help" :title="helpTitle('pool')">?</button></span>
        <select v-model="apiSettingState.editKind">
          <option value="main">main</option>
          <option value="aux">aux</option>
          <option value="chat">chat</option>
        </select>
      </label>
      <button class="btn ghost" @click="resetApiForm(apiSettingState.editKind)">重置表单</button>
    </div>
    <div class="grid three">
      <label><span class="endpoint-label-row">api_instance_id<button type="button" class="endpoint-help" :title="helpTitle('apiInstanceId')">?</button></span><input v-model="apiSettingState.form.api_instance_id" placeholder="同一 API Key 实例共享" /></label>
      <label>api_instance_name<input v-model="apiSettingState.form.api_instance_name" /></label>
      <label>api_instance_remark<input v-model="apiSettingState.form.api_instance_remark" /></label>
    </div>
    <div class="grid three">
      <label><span class="endpoint-label-row">endpoint_id<button type="button" class="endpoint-help" :title="helpTitle('endpointId')">?</button></span><input v-model="apiSettingState.form.endpoint_id" /></label>
      <label>name<input v-model="apiSettingState.form.name" /></label>
      <label><span class="endpoint-label-row">provider<button type="button" class="endpoint-help" :title="helpTitle('provider')">?</button></span><input v-model="apiSettingState.form.provider" /></label>
    </div>
    <div class="grid three">
      <label><span class="endpoint-label-row">base_url<button type="button" class="endpoint-help" :title="helpTitle('baseUrl')">?</button></span><input v-model="apiSettingState.form.base_url" /></label>
      <label>model<input v-model="apiSettingState.form.model" /></label>
      <label><span class="endpoint-label-row">task<button type="button" class="endpoint-help" :title="helpTitle('task')">?</button></span>
        <select v-model="apiSettingState.form.task">
          <option v-for="item in consumerOptionsForEditPool" :key="item.task" :value="item.task">
            {{ item.label }} ({{ item.task }})
          </option>
        </select>
      </label>
    </div>
    <div class="grid three">
      <label><span class="endpoint-label-row">tenant_id<button type="button" class="endpoint-help" :title="helpTitle('tenant')">?</button></span><input v-model="apiSettingState.form.tenant_id" /></label>
      <label><span class="endpoint-label-row">api_key<button type="button" class="endpoint-help" :title="helpTitle('apiKey')">?</button></span><input v-model="apiSettingState.form.api_key" placeholder="留空表示不更新；新增同实例端点时继承" /></label>
      <label>enabled
        <select v-model="apiSettingState.form.enabled">
          <option :value="true">true</option>
          <option :value="false">false</option>
        </select>
      </label>
    </div>
    <div class="grid three">
      <label><span class="endpoint-label-row">priority<button type="button" class="endpoint-help" :title="helpTitle('priority')">?</button></span><input v-model.number="apiSettingState.form.priority" type="number" /></label>
      <label><span class="endpoint-label-row">weight<button type="button" class="endpoint-help" :title="helpTitle('weight')">?</button></span><input v-model.number="apiSettingState.form.weight" type="number" /></label>
      <label><span class="endpoint-label-row">max_concurrency<button type="button" class="endpoint-help" :title="helpTitle('concurrency')">?</button></span><input v-model.number="apiSettingState.form.max_concurrency" type="number" /></label>
    </div>
    <div class="grid three">
      <label><span class="endpoint-label-row">timeout_seconds<button type="button" class="endpoint-help" :title="helpTitle('timeout')">?</button></span><input v-model.number="apiSettingState.form.timeout_seconds" type="number" /></label>
      <label><span class="endpoint-label-row">retry_max<button type="button" class="endpoint-help" :title="helpTitle('retry')">?</button></span><input v-model.number="apiSettingState.form.retry_max" type="number" /></label>
      <label><span class="endpoint-label-row">circuit_fail_threshold<button type="button" class="endpoint-help" :title="helpTitle('circuit')">?</button></span><input v-model.number="apiSettingState.form.circuit_fail_threshold" type="number" /></label>
    </div>
    <div class="grid three">
      <label><span class="endpoint-label-row">circuit_open_seconds<button type="button" class="endpoint-help" :title="helpTitle('circuit')">?</button></span><input v-model.number="apiSettingState.form.circuit_open_seconds" type="number" /></label>
      <label><span class="endpoint-label-row">input_per_1k<button type="button" class="endpoint-help" :title="helpTitle('price')">?</button></span><input v-model.number="apiSettingState.form.input_per_1k" type="number" step="0.0001" /></label>
      <label><span class="endpoint-label-row">output_per_1k<button type="button" class="endpoint-help" :title="helpTitle('price')">?</button></span><input v-model.number="apiSettingState.form.output_per_1k" type="number" step="0.0001" /></label>
    </div>
    <div class="row">
      <button class="btn primary" :disabled="apiSettingState.loading" @click="upsertApiEndpoint">保存端点</button>
      <button
        class="btn ghost danger-text"
        :disabled="apiSettingState.loading || !apiSettingState.form.endpoint_id"
        @click="deleteApiEndpoint(apiSettingState.editKind, apiSettingState.form.endpoint_id)"
      >删除端点</button>
    </div>
  </div>
</template>

<style scoped>
.endpoint-label-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.endpoint-help {
  width: 18px;
  height: 18px;
  border: 1px solid #b9c8db;
  border-radius: 999px;
  background: #f7fbff;
  color: #496987;
  font-size: 12px;
  line-height: 16px;
  font-weight: 800;
  cursor: help;
  padding: 0;
}

.endpoint-help:hover {
  border-color: #7fa8ef;
  background: #eaf3ff;
  color: #26559a;
}
</style>
