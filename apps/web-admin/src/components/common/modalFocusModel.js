export const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
  '[contenteditable="true"]',
].join(',')

export function nextTabStopIndex(length, currentIndex, backwards = false) {
  if (length <= 0) return -1
  if (currentIndex < 0 || currentIndex >= length) return backwards ? length - 1 : 0
  if (backwards) return currentIndex === 0 ? length - 1 : currentIndex - 1
  return currentIndex === length - 1 ? 0 : currentIndex + 1
}

export function normalizeModalBinding(value) {
  if (typeof value === 'function') return { close: value, label: '' }
  return {
    close: typeof value?.close === 'function' ? value.close : null,
    label: String(value?.label || '').trim(),
  }
}
