import { normalizeInteractiveRowBinding, shouldActivateRow } from './interactiveRowModel'

const rowState = new WeakMap()

function applyState(row, state) {
  row.tabIndex = state.disabled ? -1 : 0
  row.dataset.interactiveRow = 'true'
  if (state.label) row.setAttribute('aria-label', state.label)
  else row.removeAttribute('aria-label')
  if (state.current) row.setAttribute('aria-current', 'true')
  else row.removeAttribute('aria-current')
  if (state.disabled) row.setAttribute('aria-disabled', 'true')
  else row.removeAttribute('aria-disabled')
}

function onRowKeydown(event) {
  const row = event.currentTarget
  const state = rowState.get(row)
  if (!state || !shouldActivateRow({ key: event.key, directTarget: event.target === row, disabled: state.disabled })) return
  event.preventDefault()
  state.activate?.()
}

const vInteractiveRow = {
  mounted(row, binding) {
    const state = normalizeInteractiveRowBinding(binding.value)
    rowState.set(row, state)
    applyState(row, state)
    row.addEventListener('keydown', onRowKeydown)
  },

  updated(row, binding) {
    const state = normalizeInteractiveRowBinding(binding.value)
    rowState.set(row, state)
    applyState(row, state)
  },

  unmounted(row) {
    row.removeEventListener('keydown', onRowKeydown)
    rowState.delete(row)
  },
}

export default vInteractiveRow
