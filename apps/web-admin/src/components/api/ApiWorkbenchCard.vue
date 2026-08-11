<script setup>
defineProps({
  apiSettingState: { type: Object, required: true },
  callApiWorkbench: { type: Function, required: true },
})
</script>

<template>
  <div class="kb-col">
    <h3>API 调试工作台（首版）</h3>
    <div class="row">
      <label>service
        <select v-model="apiSettingState.debugWorkbench.service">
          <option value="execution">execution</option>
          <option value="chat">chat</option>
          <option value="control">control</option>
        </select>
      </label>
      <label>method
        <select v-model="apiSettingState.debugWorkbench.method">
          <option value="GET">GET</option>
          <option value="POST">POST</option>
          <option value="PATCH">PATCH</option>
          <option value="PUT">PUT</option>
          <option value="DELETE">DELETE</option>
        </select>
      </label>
      <label>path<input v-model="apiSettingState.debugWorkbench.path" placeholder="/api/v1/..." /></label>
    </div>
    <div class="row">
      <label>query（raw）<input v-model="apiSettingState.debugWorkbench.queryText" placeholder="a=1&b=2" /></label>
      <button class="btn primary" @click="callApiWorkbench">发送请求</button>
    </div>
    <label>body JSON
      <textarea v-model="apiSettingState.debugWorkbench.bodyText" rows="4" />
    </label>
    <div class="row"><strong>状态：</strong>{{ apiSettingState.debugWorkbench.responseStatus || '-' }}</div>
    <pre>{{ apiSettingState.debugWorkbench.responseText || '-' }}</pre>
  </div>
</template>
