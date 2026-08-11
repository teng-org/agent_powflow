<template>
  <div class="toast-stack" role="region" aria-label="系统通知" aria-live="polite">
    <TransitionGroup name="toast-item">
      <article v-for="item in items" :key="item.id" class="toast-bubble" :class="`is-${item.type}`">
        <span class="toast-symbol" aria-hidden="true">{{ symbols[item.type] || 'i' }}</span>
        <div><strong>{{ item.title }}</strong><p v-if="item.message">{{ item.message }}</p></div>
        <button type="button" aria-label="关闭通知" @click="$emit('dismiss', item.id)">×</button>
      </article>
    </TransitionGroup>
  </div>
</template>

<script setup>
defineProps({ items: { type: Array, default: () => [] } })
defineEmits(['dismiss'])
const symbols = { success: '✓', error: '!', warning: '!', info: 'i' }
</script>
