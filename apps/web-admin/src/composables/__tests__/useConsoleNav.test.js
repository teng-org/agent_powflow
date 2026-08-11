import { describe, expect, it, vi } from 'vitest'
import { createNavActionMap, useConsoleNav } from '../useConsoleNav'

describe('useConsoleNav', () => {
  it('executes mapped actions on switch', () => {
    const nav = { primary: 'dashboard', page: 'overview' }
    const loadKbs = vi.fn()
    const loadControlTenants = vi.fn()
    const loadKbTasks = vi.fn()
    const loadChatConfig = vi.fn()
    const loadChatSessions = vi.fn()
    const loadApiPools = vi.fn()

    const actionMap = createNavActionMap({
      loadKbs,
      loadControlTenants,
      kbState: { selectedKbId: 'kb-1' },
      loadKbTasks,
      loadChatConfig,
      loadChatSessions,
      loadApiPools,
    })

    const { switchPage } = useConsoleNav({ nav, actionMap })
    switchPage('assets', 'kb-settings')
    expect(nav.primary).toBe('assets')
    expect(nav.page).toBe('kb-settings')
    expect(loadKbs).toHaveBeenCalledTimes(1)
    expect(loadControlTenants).toHaveBeenCalledTimes(1)
    expect(loadKbTasks).toHaveBeenCalledTimes(1)

    switchPage('infrastructure', 'api-settings')
    expect(loadApiPools).toHaveBeenCalledTimes(1)
  })
})
