import { describe, expect, it, vi } from 'vitest'
import { isRowActivationKey, normalizeInteractiveRowBinding, shouldActivateRow } from '../interactiveRowModel'

describe('interactiveRowModel', () => {
  it('accepts only the native activation keys', () => {
    expect(isRowActivationKey('Enter')).toBe(true)
    expect(isRowActivationKey(' ')).toBe(true)
    expect(isRowActivationKey('Spacebar')).toBe(false)
    expect(isRowActivationKey('ArrowDown')).toBe(false)
  })

  it('normalizes row state without inventing an action', () => {
    const activate = vi.fn()
    expect(normalizeInteractiveRowBinding({ activate, label: '  Run 42  ', current: 1 })).toEqual({
      activate,
      label: 'Run 42',
      current: true,
      disabled: false,
    })
    expect(normalizeInteractiveRowBinding({ activate: 'invalid', disabled: 1 })).toEqual({
      activate: null,
      label: '',
      current: false,
      disabled: true,
    })
  })

  it('does not hijack keys from controls nested inside a row', () => {
    expect(shouldActivateRow({ key: 'Enter', directTarget: true })).toBe(true)
    expect(shouldActivateRow({ key: ' ', directTarget: true })).toBe(true)
    expect(shouldActivateRow({ key: 'Enter', directTarget: false })).toBe(false)
    expect(shouldActivateRow({ key: 'Enter', directTarget: true, disabled: true })).toBe(false)
  })
})
