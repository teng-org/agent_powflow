import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const STORAGE_KEY = 'powflow-console-appearance'
const appearance = ref('system')
const systemIsDark = ref(false)
let mediaQuery = null

function readStoredAppearance() {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return ['light', 'dark', 'system'].includes(stored) ? stored : 'system'
  } catch {
    return 'system'
  }
}

function applyAppearance() {
  const resolved = appearance.value === 'system'
    ? (systemIsDark.value ? 'dark' : 'light')
    : appearance.value

  document.documentElement.dataset.theme = resolved
  document.documentElement.dataset.appearance = appearance.value
  document.documentElement.style.colorScheme = resolved
}

export function useAppearance() {
  const resolvedAppearance = computed(() => (
    appearance.value === 'system'
      ? (systemIsDark.value ? 'dark' : 'light')
      : appearance.value
  ))

  function setAppearance(value) {
    if (!['light', 'dark', 'system'].includes(value)) return
    appearance.value = value
    try {
      window.localStorage.setItem(STORAGE_KEY, value)
    } catch {
      // Theme selection remains available when storage is restricted.
    }
    applyAppearance()
  }

  function toggleAppearance() {
    setAppearance(resolvedAppearance.value === 'dark' ? 'light' : 'dark')
  }

  function onSystemAppearanceChange(event) {
    systemIsDark.value = event.matches
    if (appearance.value === 'system') applyAppearance()
  }

  if (typeof window !== 'undefined') {
    appearance.value = readStoredAppearance()
    mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    systemIsDark.value = mediaQuery.matches
    applyAppearance()
  }

  onMounted(() => {
    mediaQuery.addEventListener?.('change', onSystemAppearanceChange)
  })

  onBeforeUnmount(() => {
    mediaQuery?.removeEventListener?.('change', onSystemAppearanceChange)
  })

  return {
    appearance,
    resolvedAppearance,
    setAppearance,
    toggleAppearance,
  }
}
