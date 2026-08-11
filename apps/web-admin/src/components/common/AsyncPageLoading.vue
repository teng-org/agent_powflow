<template>
  <section class="async-page-state" role="status" aria-live="polite">
    <span class="async-page-spinner" aria-hidden="true"></span>
    <div>
      <strong>正在加载页面</strong>
      <p>首次打开会加载对应的管理模块。</p>
    </div>
  </section>
</template>

<style scoped>
.async-page-state {
  min-height: 132px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #506580;
}

.async-page-state p {
  margin: 3px 0 0;
  color: #7b8da3;
  font-size: 13px;
}

.async-page-spinner {
  width: 22px;
  height: 22px;
  flex: 0 0 auto;
  border: 2px solid #dbe5f2;
  border-top-color: #2c6ae6;
  border-radius: 50%;
  animation: async-page-spin 650ms linear infinite;
}

@keyframes async-page-spin {
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .async-page-spinner { animation: none; border-right-color: #2c6ae6; }
}
</style>
