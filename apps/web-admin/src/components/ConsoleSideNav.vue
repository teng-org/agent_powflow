<script setup>
defineProps({
  nav: { type: Object, required: true },
  pages: { type: Object, required: true },
  currentMenu: { type: Array, required: true },
  isControlLoggedIn: { type: Boolean, required: false, default: false },
  isExecutionLoggedIn: { type: Boolean, required: false, default: false },
  overviewState: { type: Object, required: false, default: () => ({}) },
})

const emit = defineEmits(['switch-page', 'jump-section'])

</script>

<template>
  <aside class="side-nav" aria-label="页面导航">
    <div class="side-scroll">
      <div v-for="item in currentMenu" :key="item.id" class="menu-block">
        <button
          class="menu-item"
          :class="{ active: nav.page === item.id }"
          :aria-current="nav.page === item.id ? 'page' : undefined"
          :aria-expanded="item.sections?.length ? nav.page === item.id : undefined"
          :aria-controls="item.sections?.length ? `console-sections-${item.id}` : undefined"
          @click="emit('switch-page', nav.primary, item.id)"
        >
          {{ item.name }}
        </button>
        <Transition name="submenu">
          <div v-if="nav.page === item.id && item.sections?.length" :id="`console-sections-${item.id}`" class="section-list" :aria-label="`${item.name}分区`">
            <button
              v-for="s in item.sections"
              :key="s.id"
              class="section-item"
              :class="{ active: nav.activeSection === s.id }"
              :aria-current="nav.activeSection === s.id ? 'location' : undefined"
              @click="emit('jump-section', s.id)"
            >
              {{ s.name }}
            </button>
          </div>
        </Transition>
      </div>
    </div>
    <div class="status-foot" role="status" aria-live="polite" aria-label="服务连接状态">
      <div class="status-line">
        <span :class="['dot', isControlLoggedIn ? 'ok' : 'bad']" aria-hidden="true"></span>
        控制平面：{{ isControlLoggedIn ? '已连接' : '未连接' }}
      </div>
      <div class="status-line">
        <span :class="['dot', isExecutionLoggedIn ? 'ok' : 'bad']" aria-hidden="true"></span>
        执行平面：{{ isExecutionLoggedIn ? '已连接' : '未连接' }}
      </div>
    </div>
  </aside>
</template>

<style scoped>
.side-nav {
  background: #ffffff;
  border-right: 1px solid #e3e8f0;
  padding: 14px 12px;
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  gap: 10px;
}

.side-scroll {
  min-height: 0;
  overflow: auto;
  display: grid;
  align-content: start;
  gap: 10px;
}

.menu-item {
  width: 100%;
  text-align: left;
  border: 1px solid #e2e8f2;
  background: #fff;
  color: #304056;
  border-radius: 10px;
  padding: 10px 12px;
  margin: 0;
  cursor: pointer;
}

.menu-item:focus-visible,
.section-item:focus-visible {
  outline: 2px solid #5b8fdd;
  outline-offset: 2px;
}

.menu-item:active,
.section-item:active { transform: scale(.98); }

.menu-item.mini {
  font-size: 12px;
  padding: 8px 10px;
}

.menu-item.active {
  background: #eaf2ff;
  border-color: #9fbfff;
  color: #1d4fa8;
}

.menu-block {
  display: grid;
  gap: 6px;
  margin: 0;
}

.section-list {
  display: grid;
  gap: 5px;
  margin: 0;
  padding-left: 14px;
}

.section-item {
  width: 100%;
  text-align: left;
  border: 1px dashed #d7e2f2;
  background: #fbfdff;
  color: #4a607b;
  border-radius: 8px;
  padding: 8px 10px 8px 14px;
  margin: 0;
  cursor: pointer;
  position: relative;
}

.section-item::before {
  content: '';
  position: absolute;
  left: 6px;
  top: 6px;
  bottom: 6px;
  width: 2px;
  border-radius: 999px;
  background: #d7e2f2;
}

.section-item.active {
  border-style: solid;
  border-color: #8fb4ff;
  color: #2256ae;
  background: #eef4ff;
  font-weight: 600;
}

.section-item.active::before {
  background: #4f7fda;
}

.submenu-enter-active,
.submenu-leave-active {
  transition:
    max-height 0.26s ease,
    opacity 0.22s ease,
    transform 0.22s ease;
  overflow: hidden;
}

.submenu-enter-from,
.submenu-leave-to {
  max-height: 0;
  opacity: 0;
  transform: translateY(-4px);
}

.submenu-enter-to,
.submenu-leave-from {
  max-height: 420px;
  opacity: 1;
  transform: translateY(0);
}

.status-foot {
  border-top: 1px solid #e8eef7;
  padding-top: 10px;
  display: grid;
  gap: 6px;
}

.status-line {
  font-size: 12px;
  color: #50647e;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  display: inline-block;
}

.dot.ok {
  background: #1f9d5e;
}

.dot.bad {
  background: #c64b4b;
}

@media (prefers-reduced-motion: reduce) {
  .submenu-enter-active,
  .submenu-leave-active { transition: opacity 100ms ease; }
  .submenu-enter-from,
  .submenu-leave-to,
  .submenu-enter-to,
  .submenu-leave-from { transform: none; }
  .menu-item:active,
  .section-item:active { transform: none; }
}

@media (max-width: 900px) {
  .side-nav {
    display: block;
    padding: 8px 12px;
    border-right: 0;
    border-bottom: 1px solid #e3e8f0;
    min-width: 0;
  }

  .side-scroll {
    display: flex;
    gap: 8px;
    overflow-x: auto;
    scrollbar-width: thin;
  }

  .menu-block {
    flex: 0 0 auto;
    margin: 0;
  }

  .menu-item {
    width: auto;
    min-height: 38px;
    margin: 0;
    white-space: nowrap;
  }

  .section-list,
  .status-foot {
    display: none;
  }
}
</style>
