export function useAuthAdmin({
  fetchJson,
  cfg,
  ui,
  kbState,
  chatSettingState,
  apiSettingState,
  clearBanner,
  onAfterControlLogin,
  onAfterExecutionLogin,
}) {
  function pickToken(data) {
    if (!data || typeof data !== 'object') return ''
    return String(
      data.accessToken ||
      data.access_token ||
      data.token ||
      data.jwt ||
      '',
    ).trim()
  }

  async function loginControl() {
    clearBanner()
    ui.loading = true
    try {
      const data = await fetchJson('control', '/api/v1/auth/login', {
        method: 'POST',
        body: {
          tenantCode: cfg.control.tenantCode || null,
          username: cfg.control.username,
          password: cfg.control.password,
        },
      })
      cfg.control.token = pickToken(data)
      if (!cfg.control.token) {
        throw new Error('Control Plane 登录响应缺少 access token')
      }
      await onAfterControlLogin()
      ui.message = 'Control Plane 登录成功'
    } catch (error) {
      ui.error = error instanceof Error ? error.message : String(error)
    } finally {
      ui.loading = false
    }
  }

  async function loginExecution() {
    clearBanner()
    ui.loading = true
    try {
      const executionPassword = cfg.execution.password || cfg.control.password
      const data = await fetchJson('execution', '/api/v1/admin/auth/login', {
        method: 'POST',
        body: {
          username: cfg.execution.username,
          password: executionPassword,
        },
      })
      cfg.execution.token = pickToken(data)
      if (!cfg.execution.token) {
        throw new Error('Execution Plane 登录响应缺少 access token')
      }
      await onAfterExecutionLogin()
      ui.message = 'Execution Plane 登录成功'
    } catch (error) {
      ui.error = error instanceof Error ? error.message : String(error)
    } finally {
      ui.loading = false
    }
  }

  function logoutAll() {
    cfg.control.token = ''
    cfg.execution.token = ''

    kbState.items = []
    kbState.docs = []
    kbState.chunks = []
    kbState.bindings = []
    kbState.tenants = []
    kbState.selectedKbId = ''
    kbState.selectedDocId = ''

    chatSettingState.sessions = []
    chatSettingState.messages = []
    chatSettingState.selectedSessionId = ''

    apiSettingState.mainPool = []
    apiSettingState.auxPool = []
    apiSettingState.chatPool = []

    clearBanner()
  }

  return { loginControl, loginExecution, logoutAll }
}
