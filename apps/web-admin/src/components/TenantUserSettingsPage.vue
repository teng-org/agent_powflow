<script setup>
defineProps({
  tenantUserState: { type: Object, required: true },
  loadTenants: { type: Function, required: true },
  selectTenant: { type: Function, required: true },
  createTenant: { type: Function, required: true },
  loadUsers: { type: Function, required: true },
  createUser: { type: Function, required: true },
  loadUserToPatchForm: { type: Function, required: true },
  patchUser: { type: Function, required: true },
  pickTenantId: { type: Function, required: true },
  pickUserId: { type: Function, required: true },
  sectionDomId: { type: Function, default: null },
})
</script>

<template>
  <section class="content">
    <div class="panel">
      <div class="panel-title">租户与用户</div>
      <p class="panel-sub">租户、用户与角色的基础管理接口面板</p>
      <p class="banner err" v-if="tenantUserState.error">{{ tenantUserState.error }}</p>

      <div class="domain-stack">
        <div class="two-col">
          <div class="domain-section kb-col" :id="sectionDomId ? sectionDomId('tenants') : undefined" data-menu-section="tenants">
            <h2 class="domain-section-title">租户</h2>
            <div class="row">
              <button class="btn ghost" :disabled="tenantUserState.loading" @click="loadTenants">刷新租户</button>
            </div>
            <div class="row">
              <label>tenantCode<input v-model="tenantUserState.createTenant.tenantCode" placeholder="tenant-a" /></label>
              <label>name<input v-model="tenantUserState.createTenant.name" placeholder="Tenant A" /></label>
              <button class="btn primary" :disabled="tenantUserState.loading" @click="createTenant">创建租户</button>
            </div>
            <div class="table-wrap short">
              <table>
                <thead><tr><th>tenantId</th><th>tenantCode</th><th>name</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-for="t in tenantUserState.tenants" :key="pickTenantId(t)" :class="{ selected: tenantUserState.selectedTenantId === pickTenantId(t) }">
                    <td>{{ pickTenantId(t) }}</td>
                    <td>{{ t.tenantCode || t.tenant_code || '-' }}</td>
                    <td>{{ t.name || '-' }}</td>
                    <td><button class="btn ghost" @click="selectTenant(pickTenantId(t))">查看用户</button></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div class="domain-section kb-col" :id="sectionDomId ? sectionDomId('users') : undefined" data-menu-section="users">
            <h2 class="domain-section-title">用户</h2>
            <div class="row">
              <div>当前租户：{{ tenantUserState.selectedTenantId || '-' }}</div>
              <button class="btn ghost" :disabled="!tenantUserState.selectedTenantId || tenantUserState.loading" @click="loadUsers()">刷新用户</button>
            </div>
            <div class="row">
              <label>email<input v-model="tenantUserState.createUser.email" placeholder="user@example.com" /></label>
              <label>password<input v-model="tenantUserState.createUser.password" type="password" placeholder="******" /></label>
            </div>
            <div class="row">
              <label>displayName<input v-model="tenantUserState.createUser.displayName" placeholder="张三" /></label>
              <label>roles(csv)<input v-model="tenantUserState.createUser.rolesCsv" placeholder="TENANT_USER,TENANT_ADMIN" /></label>
              <button class="btn primary" :disabled="tenantUserState.loading || !tenantUserState.selectedTenantId" @click="createUser">创建用户</button>
            </div>
            <div class="table-wrap short">
              <table>
                <thead><tr><th>userId</th><th>email</th><th>displayName</th><th>status</th><th>roles</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-for="u in tenantUserState.users" :key="pickUserId(u)">
                    <td>{{ pickUserId(u) }}</td>
                    <td>{{ u.email || '-' }}</td>
                    <td>{{ u.displayName || u.display_name || '-' }}</td>
                    <td>{{ u.status || u.user_status || '-' }}</td>
                    <td>{{ Array.isArray(u.roles) ? u.roles.join(',') : '-' }}</td>
                    <td><button class="btn ghost" @click="loadUserToPatchForm(u)">载入编辑</button></td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div class="row">
              <label>userId<input v-model="tenantUserState.patchUser.userId" placeholder="从上表载入" /></label>
              <label>displayName<input v-model="tenantUserState.patchUser.displayName" /></label>
            </div>
            <div class="row">
              <label>status<input v-model="tenantUserState.patchUser.status" placeholder="ACTIVE / DISABLED" /></label>
              <label>roles(csv)<input v-model="tenantUserState.patchUser.rolesCsv" placeholder="TENANT_USER,TENANT_ADMIN" /></label>
              <button class="btn ghost" :disabled="tenantUserState.loading || !tenantUserState.selectedTenantId" @click="patchUser">更新用户</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
