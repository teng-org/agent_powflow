import { describe, expect, it } from 'vitest'
import { edgeOptionIndex, enabledOptionIndexes, initialOptionIndex, nextOptionIndex } from '../selectModel'

const options = [
  { label: '禁用开头', value: 'a', disabled: true },
  { label: '第一项', value: 'b' },
  { label: '禁用中间', value: 'c', disabled: true },
  { label: '第二项', value: 'd' },
]

describe('select keyboard model', () => {
  it('exposes only enabled option indexes', () => {
    expect(enabledOptionIndexes(options)).toEqual([1, 3])
  })

  it('selects the current enabled item or the directional edge', () => {
    expect(initialOptionIndex(options, 3, 1)).toBe(3)
    expect(initialOptionIndex(options, 0, 1)).toBe(1)
    expect(initialOptionIndex(options, 0, -1)).toBe(3)
  })

  it('wraps in both directions while skipping disabled items', () => {
    expect(nextOptionIndex(options, 1, 1)).toBe(3)
    expect(nextOptionIndex(options, 3, 1)).toBe(1)
    expect(nextOptionIndex(options, 1, -1)).toBe(3)
  })

  it('resolves Home and End and fails safely for an empty list', () => {
    expect(edgeOptionIndex(options, 'start')).toBe(1)
    expect(edgeOptionIndex(options, 'end')).toBe(3)
    expect(nextOptionIndex([], -1, 1)).toBe(-1)
  })
})
