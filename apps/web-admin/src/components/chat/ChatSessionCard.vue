<script setup>
defineProps({
  chatSettingState: { type: Object, required: true },
  createChatSession: { type: Function, required: true },
  loadChatSessions: { type: Function, required: true },
  normalizeSessionId: { type: Function, required: true },
  selectSession: { type: Function, required: true },
})
</script>

<template>
  <div class="kb-col">
    <h3>创建会话</h3>
    <div class="grid two">
      <label>title<input v-model="chatSettingState.create.title" placeholder="可选标题" /></label>
      <label>kb_ids (csv)<input v-model="chatSettingState.create.kb_ids_csv" placeholder="kb1,kb2" /></label>
    </div>
    <div class="row">
      <button class="btn primary" @click="createChatSession">创建会话</button>
      <button class="btn ghost" @click="loadChatSessions">刷新会话</button>
    </div>
    <div class="table-wrap short">
      <table>
        <thead><tr><th>session_id</th><th>title</th><th>kb_ids</th><th>操作</th></tr></thead>
        <tbody>
          <tr
            v-for="item in chatSettingState.sessions"
            :key="normalizeSessionId(item)"
            :class="{ selected: chatSettingState.selectedSessionId === normalizeSessionId(item) }"
          >
            <td :title="normalizeSessionId(item)">{{ normalizeSessionId(item) }}</td>
            <td :title="item.title || item.summary">{{ item.title || item.summary || '-' }}</td>
            <td>{{ (item.kb_ids || item.kbIds || []).join(', ') || '-' }}</td>
            <td><button class="btn ghost" @click="selectSession(item)">查看消息</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
