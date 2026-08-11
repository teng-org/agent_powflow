import { describe, expect, it } from 'vitest'
import { clampPage, pagerState } from '../pagerModel'

describe('pagerModel', () => {
  it('clamps invalid page values', () => {
    expect(clampPage(0, 5)).toBe(1)
    expect(clampPage(9, 5)).toBe(5)
    expect(clampPage('3', 5)).toBe(3)
    expect(clampPage(undefined, 0)).toBe(1)
  })

  it('describes navigation boundaries', () => {
    expect(pagerState({ page: 1, pageCount: 3 })).toMatchObject({ canPrevious: false, canNext: true })
    expect(pagerState({ page: 3, pageCount: 3 })).toMatchObject({ canPrevious: true, canNext: false })
  })

  it('builds an atomic status announcement with range and total', () => {
    expect(pagerState({ page: 2, pageCount: 4, startItem: 21, endItem: 40, totalItems: 73 }).statusText)
      .toBe('第 2 / 4 页，显示 21-40，共 73 条')
  })
})
