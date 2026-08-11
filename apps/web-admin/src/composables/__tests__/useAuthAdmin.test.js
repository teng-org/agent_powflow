import { describe, expect, it, vi } from 'vitest'
import { useAuthAdmin } from '../useAuthAdmin'

function makeState() {
  return {
    cfg: {
      control: { tenantCode: 'platform', username: 'admin', password: 'admin123', token: '' },
      execution: { username: 'admin', password: 'admin123', token: '' },
    },
    ui: { loading: false, message: '', error: '' },
    kbState: { items: [1], docs: [1], chunks: [1], bindings: [1], tenants: [1], selectedKbId: 'k1', selectedDocId: 'd1' },
    chatSettingState: { sessions: [1], messages: [1], selectedSessionId: 's1' },
    apiSettingState: { mainPool: [1], auxPool: [1], chatPool: [1] },
  }
}

describe('useAuthAdmin', () => {
  it('logs in control and calls post actions', async () => {
    const s = makeState()
    const after = vi.fn(async () => {})
    const fetchJson = vi.fn(async () => ({ accessToken: 'ctl-token' }))
    const clearBanner = vi.fn(() => {
      s.ui.message = ''
      s.ui.error = ''
    })

    const { loginControl } = useAuthAdmin({
      fetchJson,
      cfg: s.cfg,
      ui: s.ui,
      kbState: s.kbState,
      chatSettingState: s.chatSettingState,
      apiSettingState: s.apiSettingState,
      clearBanner,
      onAfterControlLogin: after,
      onAfterExecutionLogin: async () => {},
    })

    await loginControl()
    expect(s.cfg.control.token).toBe('ctl-token')
    expect(after).toHaveBeenCalledTimes(1)
    expect(s.ui.message).toContain('Control Plane 登录成功')
  })

  it('reuses the control password when the execution password is blank', async () => {
    const s = makeState()
    s.cfg.control.password = 'shared-password'
    s.cfg.execution.password = ''
    const fetchJson = vi.fn(async () => ({ access_token: 'exec-token' }))

    const { loginExecution } = useAuthAdmin({
      fetchJson,
      cfg: s.cfg,
      ui: s.ui,
      kbState: s.kbState,
      chatSettingState: s.chatSettingState,
      apiSettingState: s.apiSettingState,
      clearBanner: () => {},
      onAfterControlLogin: async () => {},
      onAfterExecutionLogin: async () => {},
    })

    await loginExecution()

    expect(fetchJson).toHaveBeenCalledWith('execution', '/api/v1/admin/auth/login', {
      method: 'POST',
      body: { username: 'admin', password: 'shared-password' },
    })
    expect(s.cfg.execution.token).toBe('exec-token')
  })

  it('logout resets token and cached states', () => {
    const s = makeState()
    s.cfg.control.token = 'a'
    s.cfg.execution.token = 'b'
    const { logoutAll } = useAuthAdmin({
      fetchJson: async () => ({}),
      cfg: s.cfg,
      ui: s.ui,
      kbState: s.kbState,
      chatSettingState: s.chatSettingState,
      apiSettingState: s.apiSettingState,
      clearBanner: () => {
        s.ui.message = ''
        s.ui.error = ''
      },
      onAfterControlLogin: async () => {},
      onAfterExecutionLogin: async () => {},
    })

    logoutAll()
    expect(s.cfg.control.token).toBe('')
    expect(s.cfg.execution.token).toBe('')
    expect(s.kbState.items).toEqual([])
    expect(s.chatSettingState.sessions).toEqual([])
    expect(s.apiSettingState.chatPool).toEqual([])
  })
})
