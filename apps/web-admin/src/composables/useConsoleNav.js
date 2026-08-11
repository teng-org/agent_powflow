export function createNavActionMap({
  loadKbs,
  loadControlTenants,
  kbState,
  loadKbTasks,
  loadChatConfig,
  loadChatSessions,
  loadApiPools,
  loadTenantsForAdmin,
  loadRuntimeConfigForAdmin,
  loadTaskMonitor,
  loadApiObservability,
  loadChatApiInsights,
  loadPlatformUsageSummary,
  loadPlatformUsageEvents,
  loadPlatformAlerts,
  loadApiUsageGrouped,
}) {
  const noop = () => Promise.resolve()
  return {
    'dashboard/tasks': [() => (loadTaskMonitor ? loadTaskMonitor() : noop())],
    'dashboard/api-monitor': [
      () => (loadApiObservability ? loadApiObservability() : noop()),
      () => (loadChatApiInsights ? loadChatApiInsights() : noop()),
      () => (loadApiUsageGrouped ? loadApiUsageGrouped() : noop()),
      () => (loadPlatformUsageSummary ? loadPlatformUsageSummary() : noop()),
      () => (loadPlatformUsageEvents ? loadPlatformUsageEvents() : noop()),
      () => (loadPlatformAlerts ? loadPlatformAlerts() : noop()),
    ],
    'dashboard/chat-memory-monitor': [() => (loadTaskMonitor ? loadTaskMonitor() : noop()), () => (loadChatSessions ? loadChatSessions() : noop())],
    'assets/kb-settings': [
      () => (loadKbs ? loadKbs() : noop()),
      () => (loadControlTenants ? loadControlTenants() : noop()),
      () => (kbState.selectedKbId && loadKbTasks ? loadKbTasks() : Promise.resolve()),
    ],
    'infrastructure/db': [],
    'infrastructure/api-settings': [() => (loadApiPools ? loadApiPools() : noop())],
    'tenant/tenant-user-settings': [() => (loadTenantsForAdmin ? loadTenantsForAdmin() : noop())],
    'infrastructure/platform-settings': [() => (loadRuntimeConfigForAdmin ? loadRuntimeConfigForAdmin() : noop())],
  }
}

export function useConsoleNav({ nav, actionMap }) {
  function switchPage(primary, page) {
    nav.primary = primary
    nav.page = page
    const key = `${primary}/${page}`
    for (const action of actionMap[key] || []) action()
  }

  function switchPrimary(primary, pages = []) {
    nav.primary = primary
    const first = pages[0]
    if (first) {
      switchPage(primary, first.id)
    }
  }

  return { switchPage, switchPrimary }
}
