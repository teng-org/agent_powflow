export function buildNodeTrace(events = []) {
  const byNode = new Map()
  for (const event of events) {
    if (!['NODE_STARTED', 'NODE_COMPLETED', 'NODE_FAILED', 'NODE_ATTEMPT_STARTED', 'NODE_ATTEMPT_SUCCEEDED', 'NODE_ATTEMPT_FAILED'].includes(event?.event_type)) continue
    const nodeId = String(event?.payload?.node_id || '')
    if (!nodeId) continue
    const current = byNode.get(nodeId) || {
      node_id: nodeId,
      node_type: String(event?.payload?.node_type || ''),
      ordinal: Number(event?.payload?.ordinal || 0),
      status: 'PENDING',
      started_at: null,
      completed_at: null,
      duration_ms: null,
      details: {},
      attempts: [],
    }
    if (event.event_type.startsWith('NODE_ATTEMPT_')) {
      const details = event?.payload?.details || {}
      const attemptNo = Number(details.attempt_no || 0)
      if (attemptNo > 0) {
        const attempt = current.attempts.find((item) => item.attempt_no === attemptNo) || {
          node_attempt_id: `${nodeId}:${attemptNo}`,
          attempt_no: attemptNo,
          status: 'RUNNING',
          started_at: null,
          completed_at: null,
          duration_ms: null,
          retryable: false,
          retry_delay_ms: 0,
          error_type: '',
          error_message: '',
          details: {},
        }
        if (event.event_type === 'NODE_ATTEMPT_STARTED') attempt.started_at = event.created_at || null
        else {
          attempt.status = event.event_type === 'NODE_ATTEMPT_SUCCEEDED' ? 'SUCCEEDED' : 'FAILED'
          attempt.completed_at = event.created_at || null
          attempt.duration_ms = details.duration_ms ?? (attempt.started_at && attempt.completed_at ? Math.max(0, new Date(attempt.completed_at).getTime() - new Date(attempt.started_at).getTime()) : null)
          attempt.retryable = Boolean(details.retryable)
          attempt.retry_delay_ms = Number(details.retry_delay_ms || 0)
          attempt.error_type = String(details.error_type || '')
          attempt.error_message = String(details.error_message || '')
          attempt.details = details
        }
        if (!current.attempts.some((item) => item.attempt_no === attemptNo)) current.attempts.push(attempt)
        current.attempts.sort((a, b) => a.attempt_no - b.attempt_no)
      }
    } else if (event.event_type === 'NODE_STARTED') {
      if (current.status !== 'COMPLETED') current.status = 'RUNNING'
      current.started_at = event.created_at || null
    } else if (event.event_type === 'NODE_COMPLETED') {
      current.status = 'COMPLETED'
      current.completed_at = event.created_at || null
      current.details = event?.payload?.details || {}
    } else {
      current.status = 'FAILED'
      current.completed_at = event.created_at || null
      current.details = event?.payload?.details || {}
    }
    if (current.started_at && current.completed_at) {
      current.duration_ms = Math.max(0, new Date(current.completed_at).getTime() - new Date(current.started_at).getTime())
    }
    byNode.set(nodeId, current)
  }
  return [...byNode.values()].sort((a, b) => a.ordinal - b.ordinal)
}

export function runStatusTone(status) {
  if (status === 'SUCCEEDED') return 'success'
  if (status === 'FAILED') return 'danger'
  if (status === 'CANCELLED') return 'muted'
  if (status === 'RUNNING') return 'active'
  return 'pending'
}
