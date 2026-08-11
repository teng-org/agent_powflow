<script setup>
defineProps({
  chatSettingState: { type: Object, required: true },
  loadChatConfig: { type: Function, required: true },
  saveChatConfig: { type: Function, required: true },
})
</script>

<template>
  <div class="kb-col">
    <h3>平台兜底配置</h3>
    <p class="config-migration-note">智能体的角色、系统指令、模型与检索参数已迁移到用户控制台的“智能体工作台”。这里仅显示未指定实例参数时使用的兼容默认值。</p>
    <dl class="fallback-config">
      <div><dt>默认召回数量</dt><dd>{{ chatSettingState.config.chat_default_top_k }}</dd></div>
      <div><dt>默认排序策略</dt><dd>{{ chatSettingState.config.chat_default_rank_method }}</dd></div>
      <div class="prompt-row"><dt>默认系统指令</dt><dd>{{ chatSettingState.config.chat_system_prompt || '未设置' }}</dd></div>
    </dl>
    <div class="row">
      <button class="btn ghost" @click="loadChatConfig">刷新配置</button>
    </div>
  </div>
</template>

<style scoped>
.config-migration-note{margin:0 0 16px;padding:12px 14px;border:1px solid rgba(10,132,255,.18);border-radius:13px;color:var(--muted,#a1a1aa);background:rgba(10,132,255,.07);font-size:12px;line-height:1.6}.fallback-config{display:grid;margin:0}.fallback-config>div{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;padding:12px 0;border-bottom:1px solid rgba(127,127,127,.14)}.fallback-config dt{color:var(--muted,#a1a1aa);font-size:12px}.fallback-config dd{margin:0;text-align:right;font-size:12px}.fallback-config .prompt-row{display:grid}.fallback-config .prompt-row dd{max-height:100px;overflow:auto;text-align:left;white-space:pre-wrap;line-height:1.55}
</style>
