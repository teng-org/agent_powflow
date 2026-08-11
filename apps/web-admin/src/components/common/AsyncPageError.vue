<script setup>
defineProps({ error: { type: Error, default: null } })

function reloadPage() {
  window.location.reload()
}
</script>

<template>
  <section class="async-page-error" role="alert">
    <strong>页面模块加载失败</strong>
    <p>{{ error?.message || '请检查网络连接后重试。' }}</p>
    <button type="button" @click="reloadPage">重新加载</button>
  </section>
</template>

<style scoped>
.async-page-error {
  margin: 16px;
  padding: 18px;
  border: 1px solid #efc3c3;
  border-radius: 10px;
  background: #fff5f5;
  color: #8d3030;
}

.async-page-error p { margin: 6px 0 12px; }

.async-page-error button {
  min-height: 36px;
  padding: 0 14px;
  border: 1px solid #d99a9a;
  border-radius: 8px;
  background: #fff;
  color: #8d3030;
  cursor: pointer;
  transition: transform 140ms cubic-bezier(0.23, 1, 0.32, 1), background-color 140ms ease;
}

.async-page-error button:active { transform: scale(.97); }

@media (prefers-reduced-motion: reduce) {
  .async-page-error button { transition: background-color 140ms ease; }
  .async-page-error button:active { transform: none; }
}
</style>
