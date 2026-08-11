<script setup>
defineProps({
  chatSettingState: { type: Object, required: true },
})
</script>

<template>
  <div class="kb-col">
    <h3>会话消息（{{ chatSettingState.selectedSessionId || '未选择' }}）</h3>
    <div class="table-wrap">
      <table>
        <thead><tr><th>role</th><th>content</th><th>time</th></tr></thead>
        <tbody>
          <tr v-for="(msg, idx) in chatSettingState.messages" :key="idx">
            <td>{{ msg.role || '-' }}</td>
            <td :title="msg.content || msg.text">{{ msg.content || msg.text || '-' }}</td>
            <td>{{ msg.created_at || msg.createdAt || '-' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
