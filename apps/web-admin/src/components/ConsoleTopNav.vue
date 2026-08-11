<script setup>
defineProps({
  nav: { type: Object, required: true },
  appearance: { type: String, required: false, default: 'light' },
})

const emit = defineEmits(['switch-primary', 'toggle-appearance', 'logout'])

const primaryMenus = [
  { id: 'dashboard', name: '运营总览' },
  { id: 'assets', name: '资产治理' },
  { id: 'tenant', name: '用户权限' },
  { id: 'infrastructure', name: '基础设施' },
]
</script>

<template>
  <header class="top-nav">
    <div class="brand-wrap">
      <div class="brand-badge">APF</div>
      <div>
        <div class="brand-title">Powflow Platform Admin</div>
        <div class="brand-sub">平台治理与部署中心</div>
      </div>
    </div>

    <nav class="primary-nav" aria-label="主导航">
      <button
        v-for="item in primaryMenus"
        :key="item.id"
        class="primary-nav-item"
        :class="{ active: nav.primary === item.id }"
        type="button"
        :aria-current="nav.primary === item.id ? 'page' : undefined"
        @click="emit('switch-primary', item.id)"
      >
        {{ item.name }}
      </button>
    </nav>

    <div class="top-right">
      <button
        type="button"
        class="icon-btn appearance-toggle"
        :aria-label="appearance === 'dark' ? '切换到浅色模式' : '切换到深色模式'"
        :title="appearance === 'dark' ? '浅色模式' : '深色模式'"
        @click="emit('toggle-appearance')"
      >
        <svg v-if="appearance === 'dark'" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v2m0 14v2M3 12h2m14 0h2M5.64 5.64l1.42 1.42m9.88 9.88 1.42 1.42m0-12.72-1.42 1.42M7.06 16.94l-1.42 1.42M16.5 12a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Z"/></svg>
        <svg v-else viewBox="0 0 24 24" aria-hidden="true"><path d="M20.4 15.2A8.5 8.5 0 0 1 8.8 3.6 8.5 8.5 0 1 0 20.4 15.2Z"/></svg>
      </button>
      <button type="button" class="btn ghost logout-btn" @click="emit('logout')"><span class="logout-label">退出</span></button>
    </div>
  </header>
</template>

<style scoped>
.top-nav {
  height: 74px;
  border-bottom: 1px solid #e3e8f0;
  background: #fff;
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr) 120px;
  align-items: center;
  gap: 12px;
  padding: 0 14px;
}

.brand-wrap {
  display: flex;
  gap: 10px;
  align-items: center;
}

.brand-badge {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: linear-gradient(135deg, #3477f6, #69a3ff);
  color: #fff;
  display: grid;
  place-items: center;
  font-weight: 700;
}

.brand-title { font-size: 16px; font-weight: 700; }
.brand-sub { font-size: 12px; color: #6f8096; }

.primary-nav {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-top: 6px;
}

.primary-nav-item {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 2px 0;
  color: #304665;
  text-decoration: none;
  font-size: 18px;
  font-weight: 700;
  line-height: 1.2;
  cursor: pointer;
  border-top: 0;
  border-right: 0;
  border-left: 0;
  background: transparent;
  border-bottom: 2px solid transparent;
  transition: color 0.18s ease, border-color 0.18s ease;
}

.primary-nav-item.active {
  color: #1d4fa8;
  border-bottom-color: #7ea7f0;
}

.primary-nav-item:focus-visible {
  outline: 2px solid #5b8fdd;
  outline-offset: 4px;
  border-radius: 3px;
}

.primary-nav-item:active {
  transform: scale(.97);
}

@media (hover: hover) and (pointer: fine) {
  .primary-nav-item:hover { color: #245fbf; }
}

@media (prefers-reduced-motion: reduce) {
  .primary-nav-item { transition: color 100ms ease, border-color 100ms ease; }
  .primary-nav-item:active { transform: none; }
}

.top-right {
  display: flex;
  justify-content: end;
  align-items: center;
  gap: 8px;
}

@media (max-width: 680px) {
  .top-nav {
    height: 98px;
    grid-template-columns: minmax(0, 1fr) auto;
    grid-template-rows: 50px 40px;
    gap: 0 8px;
    padding: 4px 10px 0;
    min-width: 0;
  }

  .brand-wrap {
    min-width: 0;
  }

  .brand-badge {
    width: 34px;
    height: 34px;
    border-radius: 9px;
    flex: 0 0 auto;
    font-size: 13px;
  }

  .brand-title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 14px;
  }

  .brand-sub {
    font-size: 10px;
  }

  .primary-nav {
    grid-column: 1 / -1;
    min-width: 0;
    margin: 0;
    gap: 18px;
    overflow-x: auto;
    scrollbar-width: thin;
  }

  .primary-nav-item {
    flex: 0 0 auto;
    min-height: 36px;
    font-size: 15px;
    white-space: nowrap;
  }

  .top-right .btn {
    min-height: 34px;
    padding: 6px 10px;
  }
}
</style>
