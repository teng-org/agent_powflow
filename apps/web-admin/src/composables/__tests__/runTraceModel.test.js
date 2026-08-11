import { describe, expect, it } from 'vitest'
import { buildNodeTrace, runStatusTone } from '../runTraceModel'

describe('runTraceModel', () => {
  it('groups node events in execution order and calculates duration', () => {
    const nodes = buildNodeTrace([
      { event_type: 'NODE_STARTED', created_at: '2026-08-03T00:00:00.000Z', payload: { node_id: 'b', node_type: 'end', ordinal: 1 } },
      { event_type: 'NODE_COMPLETED', created_at: '2026-08-03T00:00:00.250Z', payload: { node_id: 'a', node_type: 'start', ordinal: 0, details: { ok: true } } },
      { event_type: 'NODE_STARTED', created_at: '2026-08-03T00:00:00.100Z', payload: { node_id: 'a', node_type: 'start', ordinal: 0 } },
    ])
    expect(nodes.map((node) => node.node_id)).toEqual(['a', 'b'])
    expect(nodes[0].status).toBe('COMPLETED')
    expect(nodes[0].duration_ms).toBe(150)
    expect(nodes[1].status).toBe('RUNNING')
  })

  it('maps terminal and active statuses to stable tones', () => {
    expect(runStatusTone('SUCCEEDED')).toBe('success')
    expect(runStatusTone('FAILED')).toBe('danger')
    expect(runStatusTone('RUNNING')).toBe('active')
    expect(runStatusTone('PENDING')).toBe('pending')
  })

  it('keeps failed node details and calculates its terminal duration', () => {
    const nodes = buildNodeTrace([
      { event_type: 'NODE_STARTED', created_at: '2026-08-03T00:00:00.000Z', payload: { node_id: 'tool', node_type: 'tool.http', ordinal: 2 } },
      { event_type: 'NODE_FAILED', created_at: '2026-08-03T00:00:00.425Z', payload: { node_id: 'tool', node_type: 'tool.http', ordinal: 2, details: { error_message: 'request failed' } } },
    ])

    expect(nodes).toHaveLength(1)
    expect(nodes[0]).toMatchObject({
      node_id: 'tool',
      status: 'FAILED',
      duration_ms: 425,
      details: { error_message: 'request failed' },
    })
  })

  it('reconstructs ordered retry attempts for legacy event-only traces', () => {
    const base = { node_id: 'tool', node_type: 'tool.http', ordinal: 1 }
    const nodes = buildNodeTrace([
      { event_type: 'NODE_STARTED', created_at: '2026-08-03T00:00:00.000Z', payload: base },
      { event_type: 'NODE_ATTEMPT_STARTED', created_at: '2026-08-03T00:00:00.010Z', payload: { ...base, details: { attempt_no: 1 } } },
      { event_type: 'NODE_ATTEMPT_FAILED', created_at: '2026-08-03T00:00:00.110Z', payload: { ...base, details: { attempt_no: 1, retryable: true, retry_delay_ms: 50, error_message: 'timeout' } } },
      { event_type: 'NODE_ATTEMPT_STARTED', created_at: '2026-08-03T00:00:00.160Z', payload: { ...base, details: { attempt_no: 2 } } },
      { event_type: 'NODE_ATTEMPT_SUCCEEDED', created_at: '2026-08-03T00:00:00.200Z', payload: { ...base, details: { attempt_no: 2 } } },
      { event_type: 'NODE_COMPLETED', created_at: '2026-08-03T00:00:00.205Z', payload: { ...base, details: { attempt_count: 2 } } },
    ])

    expect(nodes[0].attempts.map((item) => item.status)).toEqual(['FAILED', 'SUCCEEDED'])
    expect(nodes[0].attempts[0]).toMatchObject({ duration_ms: 100, retryable: true, retry_delay_ms: 50, error_message: 'timeout' })
    expect(nodes[0].attempts[1].duration_ms).toBe(40)
  })
})
