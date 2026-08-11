function pickTenantId(item) {
  return item?.tenantId || item?.tenant_id || item?.id || ''
}
function pickUserId(item) {
  return item?.userId || item?.user_id || item?.id || ''
}

function parseRoles(csvText) {
  return String(csvText || '')
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean)
}

export function useTenantUserAdmin({ fetchJson, isControlLoggedIn, tenantUserState, ui }) {
  async function loadTenants() {
    if (!isControlLoggedIn.value) return
    tenantUserState.loading = true
    tenantUserState.error = ''
    try {
      const data = await fetchJson('control', '/api/v1/admin/tenants')
      tenantUserState.tenants = data?.items || []
      if (!tenantUserState.selectedTenantId && tenantUserState.tenants.length) {
        tenantUserState.selectedTenantId = pickTenantId(tenantUserState.tenants[0])
      }
      if (tenantUserState.selectedTenantId) await loadUsers(tenantUserState.selectedTenantId)
    } catch (err) {
      tenantUserState.error = String(err.message || err)
    } finally {
      tenantUserState.loading = false
    }
  }

  async function selectTenant(tenantId) {
    tenantUserState.selectedTenantId = tenantId
    await loadUsers(tenantId)
  }

  async function createTenant() {
    if (!isControlLoggedIn.value) return
    const tenantCode = tenantUserState.createTenant.tenantCode.trim()
    const name = tenantUserState.createTenant.name.trim()
    if (!tenantCode || !name) {
      tenantUserState.error = 'tenantCode 和 name 必填'
      return
    }
    tenantUserState.loading = true
    tenantUserState.error = ''
    try {
      await fetchJson('control', '/api/v1/admin/tenants', {
        method: 'POST',
        body: { tenantCode, name },
      })
      tenantUserState.createTenant.tenantCode = ''
      tenantUserState.createTenant.name = ''
      ui.message = '租户已创建'
      await loadTenants()
    } catch (err) {
      tenantUserState.error = String(err.message || err)
    } finally {
      tenantUserState.loading = false
    }
  }

  async function loadUsers(tenantId = tenantUserState.selectedTenantId) {
    if (!isControlLoggedIn.value || !tenantId) return
    tenantUserState.loading = true
    tenantUserState.error = ''
    try {
      const data = await fetchJson('control', `/api/v1/admin/tenants/${encodeURIComponent(tenantId)}/users`)
      tenantUserState.users = data?.items || []
    } catch (err) {
      tenantUserState.error = String(err.message || err)
      tenantUserState.users = []
    } finally {
      tenantUserState.loading = false
    }
  }

  async function createUser() {
    if (!isControlLoggedIn.value) return
    const tenantId = tenantUserState.selectedTenantId
    if (!tenantId) {
      tenantUserState.error = '请先选择租户'
      return
    }
    const email = tenantUserState.createUser.email.trim()
    const password = tenantUserState.createUser.password.trim()
    if (!email || !password) {
      tenantUserState.error = 'email 和 password 必填'
      return
    }
    tenantUserState.loading = true
    tenantUserState.error = ''
    try {
      await fetchJson('control', `/api/v1/admin/tenants/${encodeURIComponent(tenantId)}/users`, {
        method: 'POST',
        body: {
          email,
          password,
          displayName: tenantUserState.createUser.displayName.trim() || null,
          roles: parseRoles(tenantUserState.createUser.rolesCsv),
        },
      })
      tenantUserState.createUser.email = ''
      tenantUserState.createUser.password = ''
      tenantUserState.createUser.displayName = ''
      ui.message = '用户已创建'
      await loadUsers(tenantId)
    } catch (err) {
      tenantUserState.error = String(err.message || err)
    } finally {
      tenantUserState.loading = false
    }
  }

  function loadUserToPatchForm(user) {
    const userId = pickUserId(user)
    tenantUserState.patchUser.userId = userId
    tenantUserState.patchUser.displayName = user?.displayName || user?.display_name || ''
    tenantUserState.patchUser.status = user?.status || user?.user_status || ''
    tenantUserState.patchUser.rolesCsv = Array.isArray(user?.roles) ? user.roles.join(',') : ''
  }

  async function patchUser() {
    if (!isControlLoggedIn.value) return
    const tenantId = tenantUserState.selectedTenantId
    const userId = tenantUserState.patchUser.userId
    if (!tenantId || !userId) {
      tenantUserState.error = '请选择要修改的用户'
      return
    }
    tenantUserState.loading = true
    tenantUserState.error = ''
    try {
      await fetchJson(
        'control',
        `/api/v1/admin/tenants/${encodeURIComponent(tenantId)}/users/${encodeURIComponent(userId)}`,
        {
          method: 'PATCH',
          body: {
            displayName: tenantUserState.patchUser.displayName.trim() || null,
            status: tenantUserState.patchUser.status.trim() || null,
            roles: parseRoles(tenantUserState.patchUser.rolesCsv),
          },
        },
      )
      ui.message = '用户已更新'
      await loadUsers(tenantId)
    } catch (err) {
      tenantUserState.error = String(err.message || err)
    } finally {
      tenantUserState.loading = false
    }
  }

  return {
    loadTenants,
    selectTenant,
    createTenant,
    loadUsers,
    createUser,
    loadUserToPatchForm,
    patchUser,
    pickTenantId,
    pickUserId,
  }
}
