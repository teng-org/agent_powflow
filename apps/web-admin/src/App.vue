<script setup>
import { computed, reactive, ref } from 'vue'

const cfg = reactive({
  controlPlaneBaseUrl: 'http://127.0.0.1:8099',
  tenantCode: 'platform',
  username: 'admin',
  password: '',
  token: '',
})

const auth = reactive({
  loading: false,
  error: '',
  me: null,
})

const tenantState = reactive({
  loading: false,
  error: '',
  items: [],
  create: { tenantCode: '', name: '' },
})

const userState = reactive({
  loading: false,
  error: '',
  selectedTenantId: '',
  items: [],
  create: { email: '', password: '', displayName: '', rolesCsv: 'USER' },
  patch: {},
})
const ui = reactive({
  message: '',
  error: '',
})

const isLoggedIn = computed(() => !!cfg.token)

function headers(json = false) {
  const h = { Authorization: `Bearer ${cfg.token}` }
  if (json) h['Content-Type'] = 'application/json'
  return h
}

async function call(path, init = {}) {
  const base = cfg.controlPlaneBaseUrl.trim().replace(/\/$/, '')
  const res = await fetch(`${base}${path}`, init)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  if (res.status === 204) return null
  return res.json()
}

async function login() {
  auth.loading = true
  auth.error = ''
  try {
    const data = await call('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tenantCode: cfg.tenantCode || null,
        username: cfg.username,
        password: cfg.password,
      }),
    })
    cfg.token = data.accessToken || ''
    await loadMe()
    await loadTenants()
  } catch (err) {
    auth.error = err instanceof Error ? err.message : String(err)
  } finally {
    auth.loading = false
  }
}

function logout() {
  cfg.token = ''
  auth.me = null
  tenantState.items = []
  userState.items = []
}

async function loadMe() {
  auth.me = await call('/api/v1/auth/me', { headers: headers(false) })
}

async function loadTenants() {
  tenantState.loading = true
  tenantState.error = ''
  try {
    const data = await call('/api/v1/admin/tenants', { headers: headers(false) })
    tenantState.items = data.items || []
    if (!userState.selectedTenantId && tenantState.items.length) {
      userState.selectedTenantId = tenantState.items[0].tenantId
      await loadUsers()
    }
  } catch (err) {
    tenantState.error = err instanceof Error ? err.message : String(err)
  } finally {
    tenantState.loading = false
  }
}

async function createTenant() {
  const tenantCode = tenantState.create.tenantCode.trim()
  const name = tenantState.create.name.trim()
  if (!tenantCode || !name) return
  await call('/api/v1/admin/tenants', {
    method: 'POST',
    headers: headers(true),
    body: JSON.stringify({ tenantCode, name }),
  })
  tenantState.create.tenantCode = ''
  tenantState.create.name = ''
  await loadTenants()
}

async function loadUsers() {
  if (!userState.selectedTenantId) return
  userState.loading = true
  userState.error = ''
  try {
    const data = await call(`/api/v1/admin/tenants/${encodeURIComponent(userState.selectedTenantId)}/users`, {
      headers: headers(false),
    })
    userState.items = data.items || []
    const nextPatch = {}
    for (const item of userState.items) {
      nextPatch[item.userId] = {
        displayName: item.displayName || '',
        status: item.status || 'ACTIVE',
        rolesCsv: (item.roles || []).join(','),
      }
    }
    userState.patch = nextPatch
  } catch (err) {
    userState.error = err instanceof Error ? err.message : String(err)
  } finally {
    userState.loading = false
  }
}

async function createUser() {
  ui.error = ''
  ui.message = ''
  if (!userState.selectedTenantId) {
    ui.error = '请先选择一个租户后再创建用户。'
    return
  }
  const email = userState.create.email.trim()
  const password = userState.create.password
  if (!email || !password) {
    ui.error = 'email 和 password 为必填。'
    return
  }
  const roles = userState.create.rolesCsv
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)
  await call(`/api/v1/admin/tenants/${encodeURIComponent(userState.selectedTenantId)}/users`, {
    method: 'POST',
    headers: headers(true),
    body: JSON.stringify({
      email,
      password,
      displayName: userState.create.displayName.trim() || null,
      roles,
    }),
  })
  userState.create.email = ''
  userState.create.password = ''
  userState.create.displayName = ''
  userState.create.rolesCsv = 'USER'
  await loadUsers()
  ui.message = '用户创建成功。'
}

async function patchUser(userId) {
  if (!userState.selectedTenantId) return
  const patch = userState.patch[userId]
  if (!patch) return
  const roles = String(patch.rolesCsv || '')
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)
  await call(`/api/v1/admin/tenants/${encodeURIComponent(userState.selectedTenantId)}/users/${encodeURIComponent(userId)}`, {
    method: 'PATCH',
    headers: headers(true),
    body: JSON.stringify({
      displayName: patch.displayName || null,
      status: patch.status || null,
      roles,
    }),
  })
  await loadUsers()
}
</script>

<template>
  <div class="page">
    <header class="topbar">
      <div>
        <h1>Admin Console</h1>
        <p>用户/租户基础控制台</p>
      </div>
      <div class="row" v-if="isLoggedIn">
        <button class="ghost" @click="loadTenants">刷新</button>
        <button class="danger" @click="logout">退出</button>
      </div>
    </header>

    <section class="panel" v-if="!isLoggedIn">
      <h2>登录</h2>
      <div class="grid four">
        <label>Control Plane URL<input v-model="cfg.controlPlaneBaseUrl" /></label>
        <label>Tenant Code<input v-model="cfg.tenantCode" /></label>
        <label>Username<input v-model="cfg.username" /></label>
        <label>Password<input v-model="cfg.password" type="password" /></label>
      </div>
      <div class="row">
        <button class="primary" :disabled="auth.loading" @click="login">{{ auth.loading ? '登录中...' : '登录' }}</button>
      </div>
      <p class="error" v-if="auth.error">{{ auth.error }}</p>
    </section>

    <template v-else>
      <section class="panel">
        <h2>当前账号</h2>
        <pre>{{ JSON.stringify(auth.me, null, 2) }}</pre>
      </section>

      <section class="panel">
        <div class="row spread">
          <h2>租户管理</h2>
          <span v-if="tenantState.loading">加载中...</span>
        </div>
        <div class="grid three">
          <label>tenant_code<input v-model="tenantState.create.tenantCode" /></label>
          <label>name<input v-model="tenantState.create.name" /></label>
          <div class="row"><button class="primary" @click="createTenant">创建租户</button></div>
        </div>
        <p class="error" v-if="tenantState.error">{{ tenantState.error }}</p>
        <p class="error" v-if="ui.error">{{ ui.error }}</p>
        <p class="ok" v-if="ui.message">{{ ui.message }}</p>
        <div class="table-wrap">
          <table>
            <thead><tr><th>tenant_id</th><th>tenant_code</th><th>name</th><th>status</th></tr></thead>
            <tbody>
              <tr v-for="item in tenantState.items" :key="item.tenantId" @click="userState.selectedTenantId = item.tenantId; loadUsers()">
                <td>{{ item.tenantId }}</td>
                <td>{{ item.tenantCode }}</td>
                <td>{{ item.name }}</td>
                <td>{{ item.status }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel">
        <div class="row spread">
          <h2>用户管理</h2>
          <div class="row">
            <label>租户
              <select v-model="userState.selectedTenantId" @change="loadUsers">
                <option v-for="t in tenantState.items" :value="t.tenantId" :key="t.tenantId">{{ t.tenantCode }} ({{ t.name }})</option>
              </select>
            </label>
            <button class="ghost" @click="loadUsers">刷新用户</button>
          </div>
        </div>

        <div class="grid four">
          <label>email<input v-model="userState.create.email" /></label>
          <label>password<input v-model="userState.create.password" type="password" /></label>
          <label>display_name<input v-model="userState.create.displayName" /></label>
          <label>roles (csv)<input v-model="userState.create.rolesCsv" /></label>
        </div>
        <div class="row">
          <button class="primary" @click="createUser">创建用户</button>
        </div>

        <p class="error" v-if="ui.error">{{ ui.error }}</p>
        <p class="ok" v-if="ui.message">{{ ui.message }}</p>
        <p class="error" v-if="userState.error">{{ userState.error }}</p>
        <div class="table-wrap">
          <table>
            <thead>
              <tr><th>email</th><th>display_name</th><th>status</th><th>roles</th><th>action</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in userState.items" :key="item.userId">
                <td>{{ item.email }}</td>
                <td><input v-model="userState.patch[item.userId].displayName" /></td>
                <td>
                  <select v-model="userState.patch[item.userId].status">
                    <option value="ACTIVE">ACTIVE</option>
                    <option value="DISABLED">DISABLED</option>
                  </select>
                </td>
                <td><input v-model="userState.patch[item.userId].rolesCsv" /></td>
                <td><button class="ghost" @click="patchUser(item.userId)">保存</button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
:global(*) { box-sizing: border-box; }
:global(body) { margin: 0; font-family: "Segoe UI", "PingFang SC", sans-serif; background: #f3f5f8; color: #213547; }
.page { min-height: 100vh; padding: 20px; }
.topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
h1 { margin: 0; font-size: 24px; }
h2 { margin: 0 0 12px; font-size: 18px; }
p { margin: 4px 0 0; color: #516173; }
.panel { background: #fff; border: 1px solid #d8e0ea; border-radius: 12px; padding: 14px; margin-bottom: 14px; box-shadow: 0 8px 22px rgba(13, 22, 34, 0.06); }
.grid { display: grid; gap: 10px; margin-bottom: 10px; }
.grid.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.grid.four { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.row { display: flex; gap: 8px; align-items: center; }
.row.spread { justify-content: space-between; }
label { display: grid; gap: 6px; font-size: 13px; color: #607286; }
input, select, button { font: inherit; }
input, select { width: 100%; border: 1px solid #ccd8e6; border-radius: 8px; padding: 8px 10px; background: #fff; }
button { border: 1px solid #ccd8e6; border-radius: 8px; padding: 8px 12px; cursor: pointer; background: #fff; }
button.primary { background: #1f6feb; color: #fff; border-color: #1f6feb; }
button.danger { background: #ffe9e9; border-color: #f5b9b9; color: #af2f2f; }
button.ghost { background: #f7fafc; }
button:disabled { opacity: .6; cursor: not-allowed; }
.error { color: #b62828; }
.ok { color: #1f7a3d; }
.table-wrap { overflow: auto; border: 1px solid #d8e0ea; border-radius: 10px; }
table { width: 100%; border-collapse: collapse; min-width: 900px; }
th, td { padding: 8px 10px; border-bottom: 1px solid #eef2f6; text-align: left; vertical-align: middle; }
tbody tr:hover { background: #f8fbff; }
pre { margin: 0; padding: 10px; background: #0e1624; color: #e8eef7; border-radius: 8px; overflow: auto; font-size: 12px; }
</style>
