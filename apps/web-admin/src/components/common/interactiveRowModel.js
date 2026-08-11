export function isRowActivationKey(key) {
  return key === 'Enter' || key === ' '
}

export function shouldActivateRow({ key, directTarget, disabled = false }) {
  return !disabled && directTarget && isRowActivationKey(key)
}

export function normalizeInteractiveRowBinding(value) {
  return {
    activate: typeof value?.activate === 'function' ? value.activate : null,
    label: String(value?.label || '').trim(),
    current: Boolean(value?.current),
    disabled: Boolean(value?.disabled),
  }
}
