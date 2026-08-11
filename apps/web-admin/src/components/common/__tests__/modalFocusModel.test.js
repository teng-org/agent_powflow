import { describe, expect, it, vi } from 'vitest'
import { nextTabStopIndex, normalizeModalBinding } from '../modalFocusModel'

describe('modalFocusModel', () => {
  it('wraps forward and backward tab navigation', () => {
    expect(nextTabStopIndex(3, 0)).toBe(1)
    expect(nextTabStopIndex(3, 2)).toBe(0)
    expect(nextTabStopIndex(3, 0, true)).toBe(2)
    expect(nextTabStopIndex(3, 2, true)).toBe(1)
  })

  it('recovers when focus starts outside the dialog', () => {
    expect(nextTabStopIndex(3, -1)).toBe(0)
    expect(nextTabStopIndex(3, -1, true)).toBe(2)
    expect(nextTabStopIndex(0, -1)).toBe(-1)
  })

  it('normalizes function and object bindings', () => {
    const close = vi.fn()
    expect(normalizeModalBinding(close)).toEqual({ close, label: '' })
    expect(normalizeModalBinding({ close, label: '  Details  ' })).toEqual({ close, label: 'Details' })
    expect(normalizeModalBinding({ close: 'nope' })).toEqual({ close: null, label: '' })
  })
})
