<script setup>
defineProps({
  pages: { type: Object, required: true },
  nav: { type: Object, required: true },
})

const emit = defineEmits(['switch-page'])

function switchPage(section, page) {
  emit('switch-page', section, page)
}
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <div class="brand-badge">APF</div>
      <div>
        <div class="brand-title">Powflow Console</div>
        <div class="brand-sub">Admin Control Center</div>
      </div>
    </div>

    <div class="nav-block">
      <div class="nav-header">监控面板</div>
      <button
        v-for="item in pages.monitor"
        :key="item.id"
        class="nav-item"
        :class="{ active: nav.section === 'monitor' && nav.page === item.id }"
        @click="switchPage('monitor', item.id)"
      >
        {{ item.name }}
      </button>
    </div>

    <div class="nav-block">
      <div class="nav-header">功能设置</div>
      <button
        v-for="item in pages.settings"
        :key="item.id"
        class="nav-item"
        :class="{ active: nav.section === 'settings' && nav.page === item.id }"
        @click="switchPage('settings', item.id)"
      >
        {{ item.name }}
      </button>
    </div>
  </aside>
</template>
