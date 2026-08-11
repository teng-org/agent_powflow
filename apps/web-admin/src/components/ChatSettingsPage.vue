<script setup>
import ChatConfigCard from './chat/ChatConfigCard.vue'
import ChatSessionCard from './chat/ChatSessionCard.vue'
import ChatMessagesCard from './chat/ChatMessagesCard.vue'
import ChatRunCard from './chat/ChatRunCard.vue'

defineProps({
  chatSettingState: { type: Object, required: true },
  loadChatConfig: { type: Function, required: true },
  saveChatConfig: { type: Function, required: true },
  createChatSession: { type: Function, required: true },
  loadChatSessions: { type: Function, required: true },
  normalizeSessionId: { type: Function, required: true },
  selectSession: { type: Function, required: true },
  queryRunStatus: { type: Function, required: true },
  cancelRunStatus: { type: Function, required: true },
  formatJson: { type: Function, required: true },
  sectionDomId: { type: Function, default: null },
})
</script>

<template>
  <section class="content">
    <div class="panel">
      <div class="panel-title">对话设置</div>
      <p class="panel-sub">平台对话运行状态、兼容默认值与会话诊断。智能体能力参数由各实例独立管理。</p>
      <p class="banner err" v-if="chatSettingState.error">{{ chatSettingState.error }}</p>

      <div class="domain-stack">
        <div class="two-col">
          <div
            class="domain-section"
            :id="sectionDomId ? sectionDomId('config') : undefined"
            data-menu-section="config"
          >
            <h2 class="domain-section-title">配置</h2>
            <ChatConfigCard
              :chat-setting-state="chatSettingState"
              :load-chat-config="loadChatConfig"
              :save-chat-config="saveChatConfig"
            />
          </div>
          <div
            class="domain-section"
            :id="sectionDomId ? sectionDomId('sessions') : undefined"
            data-menu-section="sessions"
          >
            <h2 class="domain-section-title">会话</h2>
            <ChatSessionCard
              :chat-setting-state="chatSettingState"
              :create-chat-session="createChatSession"
              :load-chat-sessions="loadChatSessions"
              :normalize-session-id="normalizeSessionId"
              :select-session="selectSession"
            />
          </div>
        </div>

        <div class="domain-section" :id="sectionDomId ? sectionDomId('messages') : undefined" data-menu-section="messages">
          <h2 class="domain-section-title">消息</h2>
          <ChatMessagesCard :chat-setting-state="chatSettingState" />
        </div>
        <div class="domain-section" :id="sectionDomId ? sectionDomId('run') : undefined" data-menu-section="run">
          <h2 class="domain-section-title">运行态</h2>
          <ChatRunCard
            :chat-setting-state="chatSettingState"
            :query-run-status="queryRunStatus"
            :cancel-run-status="cancelRunStatus"
            :format-json="formatJson"
          />
        </div>
      </div>
    </div>
  </section>
</template>
