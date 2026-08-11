<script setup>
defineProps({
  chatSettingState: { type: Object, required: true },
  queryRunStatus: { type: Function, required: true },
  cancelRunStatus: { type: Function, required: true },
  formatJson: { type: Function, required: true },
})
</script>

<template>
  <div class="kb-col">
    <h3>Run 状态查询 / 取消</h3>
    <div class="row">
      <input v-model="chatSettingState.run.runId" placeholder="输入 run_id" />
      <button class="btn ghost" @click="queryRunStatus">查询</button>
      <button class="btn ghost danger-text" @click="cancelRunStatus">取消 Run</button>
    </div>
    <p class="banner err" v-if="chatSettingState.run.error">{{ chatSettingState.run.error }}</p>
    <pre>{{ formatJson(chatSettingState.run.detail) }}</pre>
  </div>
</template>
