export function useServiceClient(cfg) {
  function withLocalProxyHint(message, path = '') {
    const text = String(message || '')
    const p = String(path || '')
    const isLocalApi = /localhost|127\.0\.0\.1/i.test(text) || /localhost|127\.0\.0\.1/i.test(baseUrlFor('execution'))
    const isGatewayOrTimeout = /502|503|504|timeout|超时/i.test(text)
    if (!isLocalApi || !isGatewayOrTimeout) return text
    if (text.includes('请检查系统代理')) return text
    return `${text}；请检查系统代理/抓包工具是否代理了 localhost/127.0.0.1（建议加入 NO_PROXY 或绕过列表），目标接口：${p}`
  }
  function baseUrlFor(key) {
    if (key === 'execution') return cfg.executionBaseUrl.trim().replace(/\/$/, '')
    if (key === 'chat') return cfg.chatBaseUrl.trim().replace(/\/$/, '')
    if (key === 'control') return cfg.controlPlaneBaseUrl.trim().replace(/\/$/, '')
    return ''
  }

  function candidateBasesFor(service) {
    const primary = baseUrlFor(service)
    const out = []
    if (primary) out.push(primary)
    try {
      const u = new URL(primary)
      const host = String(u.hostname || '').toLowerCase()
      if (host === '127.0.0.1') {
        u.hostname = 'localhost'
        out.push(u.toString().replace(/\/$/, ''))
      } else if (host === 'localhost') {
        u.hostname = '127.0.0.1'
        out.push(u.toString().replace(/\/$/, ''))
      }
    } catch {
      // ignore invalid base
    }
    if (typeof window !== 'undefined' && window.location?.origin) {
      const localProxyPrefix = {
        control: '/control',
        execution: '/execution',
        chat: '/chat',
      }[service]
      if (localProxyPrefix) out.push(`${window.location.origin}${localProxyPrefix}`)
    }
    return Array.from(new Set(out.filter(Boolean)))
  }

  function tokenFor(service) {
    if (service === 'execution') return cfg.execution.token
    if (service === 'control') return cfg.control.token
    if (service === 'chat') return cfg.control.token || cfg.execution.token
    return ''
  }

  async function fetchJson(service, path, options = {}) {
    const token = tokenFor(service)
    const headers = { ...(options.headers || {}) }
    if (token && !headers.Authorization) headers.Authorization = `Bearer ${token}`
    if (options.body !== undefined && options.body !== null && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json'
    }

    const timeoutMs = Math.max(1000, Number(options.timeoutMs || 15000))
    const candidates = candidateBasesFor(service)
    const errors = []
    let response = null
    let usedBase = ''
    for (let i = 0; i < candidates.length; i += 1) {
      const base = candidates[i]
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), timeoutMs)
      try {
        response = await fetch(`${base}${path}`, {
          method: options.method || 'GET',
          headers,
          signal: controller.signal,
          body:
            options.body === undefined || options.body === null
              ? undefined
              : headers['Content-Type'] === 'application/json'
                ? JSON.stringify(options.body)
                : options.body,
        })
        usedBase = base
      } catch (error) {
        const msg = error?.name === 'AbortError'
          ? `timeout ${timeoutMs}ms`
          : (error?.message || String(error))
        errors.push(`${base}${path} => ${msg}`)
        response = null
      } finally {
        clearTimeout(timer)
      }
      if (!response) continue
      // 针对本地链路偶发 502/503/504，尝试下一个候选 base
      if ([502, 503, 504].includes(Number(response.status)) && i < candidates.length - 1) {
        errors.push(`${base}${path} => ${response.status}`)
        response = null
        continue
      }
      break
    }
    if (!response) {
      throw new Error(withLocalProxyHint(`请求失败：${service}${path}；候选地址：${errors.join(' | ')}`, `${service}${path}`))
    }
    if (!response.ok) {
      const text = await response.text()
      throw new Error(withLocalProxyHint(`${response.status} ${response.statusText}: ${text} [base=${usedBase}]`, `${service}${path}`))
    }
    if (response.status === 204) return null
    const text = await response.text()
    if (!text) return null
    return JSON.parse(text)
  }

  async function fetchRaw(service, path, options = {}) {
    const token = tokenFor(service)
    const headers = { ...(options.headers || {}) }
    if (token && !headers.Authorization) headers.Authorization = `Bearer ${token}`
    const timeoutMs = Math.max(1000, Number(options.timeoutMs || 15000))
    const candidates = candidateBasesFor(service)
    const errors = []
    let response = null
    let usedBase = ''
    for (let i = 0; i < candidates.length; i += 1) {
      const base = candidates[i]
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), timeoutMs)
      try {
        response = await fetch(`${base}${path}`, {
          method: options.method || 'GET',
          headers,
          signal: controller.signal,
          body: options.body,
        })
        usedBase = base
      } catch (error) {
        const msg = error?.name === 'AbortError'
          ? `timeout ${timeoutMs}ms`
          : (error?.message || String(error))
        errors.push(`${base}${path} => ${msg}`)
        response = null
      } finally {
        clearTimeout(timer)
      }
      if (!response) continue
      if ([502, 503, 504].includes(Number(response.status)) && i < candidates.length - 1) {
        errors.push(`${base}${path} => ${response.status}`)
        response = null
        continue
      }
      break
    }
    if (!response) {
      throw new Error(withLocalProxyHint(`请求失败：${service}${path}；候选地址：${errors.join(' | ')}`, `${service}${path}`))
    }
    if (!response.ok) {
      const text = await response.text()
      throw new Error(withLocalProxyHint(`${response.status} ${response.statusText}: ${text} [base=${usedBase}]`, `${service}${path}`))
    }
    return response
  }

  return { baseUrlFor, tokenFor, fetchJson, fetchRaw }
}
