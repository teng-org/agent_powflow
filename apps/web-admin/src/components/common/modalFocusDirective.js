import { FOCUSABLE_SELECTOR, nextTabStopIndex, normalizeModalBinding } from './modalFocusModel'

const modalStack = []
const modalState = new WeakMap()
const inertCounts = new WeakMap()
const inertOriginal = new WeakMap()
let bodyLockCount = 0
let originalBodyOverflow = ''

function focusableElements(root) {
  return [...root.querySelectorAll(FOCUSABLE_SELECTOR)].filter((element) => {
    if (element.closest('[inert]')) return false
    return element.getClientRects().length > 0
  })
}

function setBackgroundInert(root) {
  const acquired = []
  let branch = root
  while (branch?.parentElement && branch !== document.body) {
    const parent = branch.parentElement
    for (const sibling of parent.children) {
      if (sibling === branch || !(sibling instanceof HTMLElement)) continue
      const count = inertCounts.get(sibling) || 0
      if (count === 0) {
        inertOriginal.set(sibling, sibling.inert)
        sibling.inert = true
      }
      inertCounts.set(sibling, count + 1)
      acquired.push(sibling)
    }
    branch = parent
  }
  return acquired
}

function releaseBackgroundInert(acquired) {
  for (const element of acquired) {
    const count = inertCounts.get(element) || 0
    if (count <= 1) {
      element.inert = inertOriginal.get(element) || false
      inertCounts.delete(element)
      inertOriginal.delete(element)
    } else {
      inertCounts.set(element, count - 1)
    }
  }
}

function lockBodyScroll() {
  if (bodyLockCount === 0) {
    originalBodyOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
  }
  bodyLockCount += 1
}

function unlockBodyScroll() {
  bodyLockCount = Math.max(0, bodyLockCount - 1)
  if (bodyLockCount === 0) document.body.style.overflow = originalBodyOverflow
}

function focusInitial(root) {
  const preferred = root.querySelector('[autofocus], [data-modal-autofocus]')
  const target = preferred || focusableElements(root)[0] || root
  target.focus({ preventScroll: true })
}

function onDocumentKeydown(event) {
  const entry = modalStack.at(-1)
  if (!entry || event.defaultPrevented) return

  if (event.key === 'Escape' && entry.binding.close) {
    event.preventDefault()
    event.stopPropagation()
    entry.root.dataset.keyboardDismiss = 'true'
    entry.binding.close()
    return
  }

  if (event.key !== 'Tab') return
  const focusable = focusableElements(entry.root)
  if (!focusable.length) {
    event.preventDefault()
    entry.root.focus({ preventScroll: true })
    return
  }

  const currentIndex = focusable.indexOf(document.activeElement)
  const atBoundary = currentIndex < 0
    || (!event.shiftKey && currentIndex === focusable.length - 1)
    || (event.shiftKey && currentIndex === 0)
  if (!atBoundary) return

  event.preventDefault()
  focusable[nextTabStopIndex(focusable.length, currentIndex, event.shiftKey)]?.focus({ preventScroll: true })
}

function installDocumentHandler() {
  if (modalStack.length === 1) document.addEventListener('keydown', onDocumentKeydown)
}

function removeDocumentHandler() {
  if (!modalStack.length) document.removeEventListener('keydown', onDocumentKeydown)
}

const vModalFocus = {
  mounted(root, binding) {
    const normalized = normalizeModalBinding(binding.value)
    const state = {
      root,
      binding: normalized,
      restoreTarget: document.activeElement instanceof HTMLElement ? document.activeElement : null,
      inertElements: setBackgroundInert(root),
      focusTimer: 0,
    }
    root.setAttribute('role', 'dialog')
    root.setAttribute('aria-modal', 'true')
    root.setAttribute('tabindex', '-1')
    root.dataset.modalManaged = 'true'
    if (normalized.label && !root.hasAttribute('aria-labelledby')) root.setAttribute('aria-label', normalized.label)

    modalState.set(root, state)
    modalStack.push(state)
    lockBodyScroll()
    installDocumentHandler()
    state.focusTimer = requestAnimationFrame(() => focusInitial(root))
  },

  updated(root, binding) {
    const state = modalState.get(root)
    if (!state) return
    state.binding = normalizeModalBinding(binding.value)
    if (state.binding.label && !root.hasAttribute('aria-labelledby')) root.setAttribute('aria-label', state.binding.label)
  },

  unmounted(root) {
    const state = modalState.get(root)
    if (!state) return
    cancelAnimationFrame(state.focusTimer)
    const index = modalStack.indexOf(state)
    if (index >= 0) modalStack.splice(index, 1)
    releaseBackgroundInert(state.inertElements)
    unlockBodyScroll()
    modalState.delete(root)
    removeDocumentHandler()

    const restoreTarget = state.restoreTarget
    requestAnimationFrame(() => {
      if (restoreTarget?.isConnected && !restoreTarget.closest('[inert]')) {
        restoreTarget.focus({ preventScroll: true })
      } else {
        const parentModal = modalStack.at(-1)?.root
        if (parentModal) focusInitial(parentModal)
      }
    })
  },
}

export default vModalFocus
