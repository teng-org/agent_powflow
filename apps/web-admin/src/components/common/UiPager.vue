<script setup>
import { computed } from 'vue'
import { clampPage, pagerState } from './pagerModel'

const props = defineProps({
  page: { type: Number, default: 1 },
  pageCount: { type: Number, default: 1 },
  totalItems: { type: Number, default: null },
  startItem: { type: Number, default: null },
  endItem: { type: Number, default: null },
  loading: { type: Boolean, default: false },
  showEdges: { type: Boolean, default: false },
  ariaLabel: { type: String, default: '表格分页' },
})

const emit = defineEmits(['page'])
const state = computed(() => pagerState(props))

function go(page) {
  const target = clampPage(page, state.value.pageCount)
  if (props.loading || target === state.value.page) return
  emit('page', target)
}
</script>

<template>
  <nav class="ui-pager" :aria-label="ariaLabel">
    <div class="ui-pager-actions">
      <button
        v-if="showEdges"
        type="button"
        class="btn ghost mini"
        :disabled="loading || !state.canPrevious"
        aria-label="第一页"
        @click="go(1)"
      >首页</button>
      <button
        type="button"
        class="btn ghost mini"
        :disabled="loading || !state.canPrevious"
        :aria-label="`上一页，当前第 ${state.page} 页`"
        @click="go(state.page - 1)"
      >上一页</button>
    </div>
    <span class="ui-pager-status" role="status" aria-live="polite" aria-atomic="true">{{ state.statusText }}</span>
    <div class="ui-pager-actions">
      <button
        type="button"
        class="btn ghost mini"
        :disabled="loading || !state.canNext"
        :aria-label="`下一页，当前第 ${state.page} 页`"
        @click="go(state.page + 1)"
      >下一页</button>
      <button
        v-if="showEdges"
        type="button"
        class="btn ghost mini"
        :disabled="loading || !state.canNext"
        aria-label="最后一页"
        @click="go(state.pageCount)"
      >末页</button>
    </div>
  </nav>
</template>

<style scoped>
.ui-pager {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.ui-pager-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.ui-pager-status {
  min-width: 110px;
  text-align: center;
  color: #536982;
  font-size: 12px;
}

@media (max-width: 520px) {
  .ui-pager {
    justify-content: center;
  }

  .ui-pager-status {
    order: -1;
    flex: 1 0 100%;
  }
}
</style>
