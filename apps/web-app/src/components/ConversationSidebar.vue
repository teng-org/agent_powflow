<template>
  <aside ref="sidebarRoot" class="sidebar" :class="{ collapsed }">
    <div class="sidebar-top">
      <div class="sidebar-brand">
        <button
          class="brand-mark brand-button"
          type="button"
          :title="collapsed ? '展开侧栏' : '切换侧栏'"
          @click="$emit('toggle-collapse')"
        >
          <span class="brand-glyph" aria-hidden="true">
            <span></span>
            <span></span>
            <span></span>
          </span>
          <span v-if="collapsed" class="brand-expand-arrow" aria-hidden="true">→</span>
        </button>
        <div v-if="!collapsed" class="brand-text">Powflow Workspace</div>
        <button v-if="!collapsed" class="icon-btn sidebar-toggle" title="收起侧栏" type="button" @click="$emit('toggle-collapse')">
          ←
        </button>
      </div>

      <nav class="workspace-nav" aria-label="工作空间导航">
        <button
          v-for="item in productNav"
          :key="item.id"
          class="workspace-nav-item"
          :class="{ active: productPage === item.id }"
          type="button"
          :title="item.name"
          @click="$emit('switch-product', item.id)"
        >
          <span class="workspace-nav-icon">{{ item.icon }}</span>
          <span v-if="!collapsed">{{ item.name }}</span>
        </button>
      </nav>

      <div v-if="productPage === 'chat'" class="sidebar-tools">
        <button class="sidebar-action" type="button" @click="$emit('new-conversation')">
          <span class="sidebar-action-icon">+</span>
          <span v-if="!collapsed">新聊天</span>
        </button>

        <label v-if="!collapsed" class="sidebar-search">
          <span class="sidebar-action-icon">⌕</span>
          <input v-model.trim="query" type="text" placeholder="搜索聊天" />
        </label>
      </div>
    </div>

    <div v-if="productPage === 'chat' && !collapsed" class="conversation-list">
      <div class="sidebar-section-title">最近</div>
      <article
        v-for="item in filteredConversations"
        :key="item.id"
        class="conversation-row"
        :class="{ active: item.id === activeId }"
        :title="rowTitle(item)"
        @click="$emit('select-conversation', item.id)"
      >
        <div class="conversation-row-main">
          <div class="conversation-title">{{ item.title || '新对话' }}</div>
          <div v-if="item.last_message" class="conversation-preview">{{ item.last_message }}</div>
        </div>
        <div class="conversation-actions row-menu-wrap">
          <button
            v-if="!item.draft"
            class="icon-btn subtle"
            title="更多"
            type="button"
            @click.stop="toggleRowMenu(item.id)"
          >
            ...
          </button>
          <div v-if="rowMenuId === item.id" class="row-menu">
            <button class="row-menu-item" type="button" @click.stop="renameFromMenu(item)">重命名</button>
            <button class="row-menu-item danger" type="button" @click.stop="deleteFromMenu(item)">删除</button>
          </div>
        </div>
      </article>
      <div v-if="!filteredConversations.length" class="sidebar-empty">没有匹配的对话。</div>
    </div>

    <div ref="menuRoot" class="sidebar-bottom">
      <button class="profile-card profile-trigger" type="button" @click="menuOpen = !menuOpen">
        <div class="profile-avatar">{{ initials }}</div>
        <div v-if="!collapsed">
          <div class="profile-name">{{ user.displayName || user.email || '当前用户' }}</div>
          <div class="profile-meta">{{ user.roleText || user.email || user.tenantId || 'USER' }}</div>
        </div>
        <div v-if="!collapsed" class="profile-caret">...</div>
      </button>

      <div v-if="menuOpen" class="profile-menu" :class="{ collapsed }">
        <button class="profile-menu-item" type="button" @click="handleMenu('appearance')">{{ appearance === 'dark' ? '切换到浅色模式' : '切换到深色模式' }}</button>
        <button class="profile-menu-item" type="button" @click="handleMenu('settings')">用户中心</button>
        <button class="profile-menu-item" type="button" @click="handleMenu('refresh')">刷新会话</button>
        <button class="profile-menu-item danger" type="button" @click="handleMenu('logout')">退出</button>
      </div>
    </div>
  </aside>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps({
  user: { type: Object, required: true },
  conversations: { type: Array, default: () => [] },
  activeId: { type: String, default: '' },
  collapsed: { type: Boolean, default: false },
  productPage: { type: String, default: 'chat' },
  appearance: { type: String, default: 'dark' },
})

const productNav = [
  { id: 'home', name: '概览', icon: '⌂' },
  { id: 'chat', name: '对话测试', icon: '◌' },
  { id: 'knowledge', name: '知识库', icon: '▤' },
  { id: 'agents', name: '智能体', icon: '✦' },
  { id: 'workflows', name: 'Workflow', icon: '◇' },
  { id: 'skills', name: 'Skill 管理', icon: 'S' },
  { id: 'mcp', name: 'MCP 接入', icon: 'M' },
  { id: 'tools', name: '工具工作台', icon: 'JS' },
  { id: 'deployments', name: 'API 发布', icon: '↗' },
  { id: 'members', name: '团队与权限', icon: '◎' },
]

const emit = defineEmits([
  'new-conversation',
  'select-conversation',
  'rename-conversation',
  'delete-conversation',
  'open-settings',
  'refresh',
  'logout',
  'toggle-collapse',
  'switch-product',
  'toggle-appearance',
])

const initials = computed(() => {
  const name = props.user.displayName || props.user.email || '?'
  return String(name).slice(0, 1).toUpperCase()
})

const query = ref('')
const menuOpen = ref(false)
const menuRoot = ref(null)
const sidebarRoot = ref(null)
const rowMenuId = ref('')

const filteredConversations = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  if (!keyword) return props.conversations
  return props.conversations.filter((item) => {
    const title = String(item.title || '').toLowerCase()
    const preview = String(item.last_message || '').toLowerCase()
    return title.includes(keyword) || preview.includes(keyword)
  })
})

function formatTime(value) {
  if (!value) return '--'
  return new Date(value).toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function handleMenu(action) {
  menuOpen.value = false
  if (action === 'settings') emit('open-settings')
  if (action === 'appearance') emit('toggle-appearance')
  if (action === 'refresh') emit('refresh')
  if (action === 'logout') emit('logout')
}

function toggleRowMenu(id) {
  rowMenuId.value = rowMenuId.value === id ? '' : id
}

function renameFromMenu(item) {
  rowMenuId.value = ''
  emit('rename-conversation', item)
}

function deleteFromMenu(item) {
  rowMenuId.value = ''
  emit('delete-conversation', item)
}

function rowTitle(item) {
  return `${String(item.title || '新对话')}\n${formatTime(item.updated_at || item.created_at)}`
}

function handleDocumentPointer(event) {
  const target = event.target
  if (menuOpen.value && menuRoot.value && !menuRoot.value.contains(target)) menuOpen.value = false
  if (rowMenuId.value && !target.closest('.row-menu-wrap')) rowMenuId.value = ''
}

onMounted(() => {
  document.addEventListener('pointerdown', handleDocumentPointer)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointer)
})
</script>
