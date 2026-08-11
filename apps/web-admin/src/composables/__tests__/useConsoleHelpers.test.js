import { describe, expect, it } from 'vitest'
import { useConsoleHelpers } from '../useConsoleHelpers'

describe('useConsoleHelpers', () => {
  const h = useConsoleHelpers()

  it('normalizes ids and names', () => {
    expect(h.getKbId({ kb_id: 'k1' })).toBe('k1')
    expect(h.getKbName({ name: 'kb' })).toBe('kb')
    expect(h.getDocId({ docId: 'd1' })).toBe('d1')
    expect(h.getChunkId({ id: 'c1' })).toBe('c1')
    expect(h.getTaskId({ taskId: 't1' })).toBe('t1')
    expect(h.normalizeSessionId({ session_id: 's1' })).toBe('s1')
    expect(h.endpointIdOf({ endpointId: 'e1' })).toBe('e1')
  })

  it('formats json and null safely', () => {
    expect(h.formatJson(null)).toBe('-')
    expect(h.formatJson({ a: 1 })).toContain('"a": 1')
  })
})
