import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { useServiceClient } from '../useServiceClient'

describe('useServiceClient', () => {
  const cfg = {
    executionBaseUrl: 'http://execution:8091',
    chatBaseUrl: 'http://chat:8092',
    controlPlaneBaseUrl: 'http://control:8099',
    execution: { token: 'execution-token' },
    control: { token: 'control-token' },
  }

  beforeEach(() => {
    global.fetch = vi.fn()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('adds bearer token and parses json', async () => {
    const { fetchJson } = useServiceClient(cfg)
    global.fetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      text: async () => '{"ok":true}',
    })

    const data = await fetchJson('execution', '/api/v1/admin/kbs')
    expect(data.ok).toBe(true)
    const [url, req] = global.fetch.mock.calls[0]
    expect(url).toBe('http://execution:8091/api/v1/admin/kbs')
    expect(req.headers.Authorization).toBe('Bearer execution-token')
  })

  it('sends json body when content type omitted', async () => {
    const { fetchJson } = useServiceClient(cfg)
    global.fetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      text: async () => '{"id":1}',
    })

    await fetchJson('control', '/api/v1/auth/login', { method: 'POST', body: { a: 1 } })
    const [, req] = global.fetch.mock.calls[0]
    expect(req.headers['Content-Type']).toBe('application/json')
    expect(req.body).toBe('{"a":1}')
  })

  it('throws on non-2xx response', async () => {
    const { fetchJson } = useServiceClient(cfg)
    global.fetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      text: async () => 'bad token',
    })
    await expect(fetchJson('chat', '/x')).rejects.toThrow('401 Unauthorized')
  })
})
