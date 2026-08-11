export function usePlatformSettingsAdmin({ fetchJson, isExecutionLoggedIn, platformSettingState, overviewState, ui }) {
  function makeRequestId() {
    return `web-admin-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
  }

  function makeIdempotencyKey() {
    return `cfg-publish-${Date.now()}-${Math.random().toString(16).slice(2, 12)}`
  }

  function parseConflictDetailFromErrorMessage(messageText) {
    const text = String(messageText || '')
    if (!text) return null
    const idx = text.indexOf(': {')
    if (idx < 0) return null
    const jsonText = text.slice(idx + 2).trim()
    try {
      const obj = JSON.parse(jsonText)
      const detail = obj?.detail && typeof obj.detail === 'object' ? obj.detail : obj
      const expected = Number(detail?.expected_latest_release_id)
      const actual = Number(detail?.actual_latest_release_id)
      if (!Number.isFinite(expected) && !Number.isFinite(actual)) return null
      return {
        expected_latest_release_id: Number.isFinite(expected) ? expected : null,
        actual_latest_release_id: Number.isFinite(actual) ? actual : null,
        message: String(detail?.message || ''),
      }
    } catch {
      return null
    }
  }

  function getLatestReleaseIdFromState() {
    const rows = Array.isArray(platformSettingState.configHistory) ? platformSettingState.configHistory : []
    let maxId = 0
    for (const row of rows) {
      const id = Number(row?.release_id || 0)
      if (Number.isFinite(id) && id > maxId) maxId = id
    }
    return maxId
  }

  async function loadRuntimeConfig() {
    if (!isExecutionLoggedIn.value) return
    platformSettingState.loading = true
    platformSettingState.error = ''
    platformSettingState.runtimeConfigValidationErrors = []
    try {
      let item = {}
      let defaults = {}
      try {
        const effective = await fetchJson('execution', '/api/v1/admin/config/effective?scope_type=PLATFORM&scope_id=platform&mask_secret=false')
        item = effective?.item || {}
      } catch (err) {
        // fallback to legacy runtime-config endpoint
      }
      const data = await fetchJson('execution', '/api/v1/admin/system/runtime-config')
      let schemaItems = []
      try {
        const schema = await fetchJson('execution', '/api/v1/admin/config/schema')
        schemaItems = Array.isArray(schema?.items) ? schema.items : []
      } catch {
        schemaItems = []
      }
      defaults = data?.defaults || {}
      if (!item || Object.keys(item).length === 0) {
        item = data?.item || {}
      }
      platformSettingState.runtimeConfig = item
      platformSettingState.runtimeConfigDefaults = defaults
      platformSettingState.runtimeConfigSchema = schemaItems
      platformSettingState.runtimeConfigText = JSON.stringify(item, null, 2)
      if (overviewState) {
        overviewState.runtimeConfig = item
      }
    } catch (err) {
      platformSettingState.error = String(err.message || err)
    } finally {
      platformSettingState.loading = false
    }
  }

  function formatRuntimeConfigText() {
    try {
      const parsed = JSON.parse(platformSettingState.runtimeConfigText || '{}')
      platformSettingState.runtimeConfigText = JSON.stringify(parsed, null, 2)
      platformSettingState.error = ''
    } catch (err) {
      platformSettingState.error = `JSON 格式错误: ${String(err.message || err)}`
    }
  }

  async function saveRuntimeConfig(nextConfig = null, options = {}) {
    if (!isExecutionLoggedIn.value) return
    platformSettingState.loading = true
    platformSettingState.error = ''
    platformSettingState.runtimeConfigValidationErrors = []
    platformSettingState.runtimeConfigConflict = null
    let ok = true
    try {
      const payload =
        nextConfig && typeof nextConfig === 'object'
          ? nextConfig
          : JSON.parse(platformSettingState.runtimeConfigText || '{}')
      const note = String(options?.note || '').trim()
      const versioned = options?.versioned !== false
      const publishNote = note || 'console platform settings save'
      if (versioned && publishNote.length < 8) {
        throw new Error('发布备注至少 8 个字符，请补充变更原因。')
      }
      const expectedLatestReleaseId = Number(options?.expectedLatestReleaseId ?? getLatestReleaseIdFromState())
      const validation = await fetchJson('execution', '/api/v1/admin/config/validate', {
        method: 'POST',
        body: {
          scope_type: 'PLATFORM',
          scope_id: 'platform',
          updates: payload,
        },
      })
      const validationErrors = Array.isArray(validation?.errors) ? validation.errors : []
      if (validation?.valid === false || validationErrors.length > 0) {
        platformSettingState.runtimeConfigValidationErrors = validationErrors
        const firstMsg = validationErrors[0]?.message || '参数校验失败'
        const fieldList = validationErrors.map((e) => e?.key).filter(Boolean)
        throw new Error(fieldList.length ? `${firstMsg}（字段: ${fieldList.join(', ')}）` : firstMsg)
      }
      let savedViaConfigCenter = false
      if (versioned) {
        try {
          await fetchJson('execution', '/api/v1/admin/config/draft', {
            method: 'PATCH',
            body: {
              scope_type: 'PLATFORM',
              scope_id: 'platform',
              updates: payload,
              expected_latest_release_id: expectedLatestReleaseId,
            },
          })
          await fetchJson('execution', '/api/v1/admin/config/publish', {
            method: 'POST',
            body: {
              scope_type: 'PLATFORM',
              scope_id: 'platform',
              changed_keys: Object.keys(payload || {}),
              note: publishNote,
              expected_latest_release_id: expectedLatestReleaseId,
              idempotency_key: makeIdempotencyKey(),
              source_page: 'platform-settings',
              client_version: 'web-admin',
              request_id: makeRequestId(),
            },
          })
          savedViaConfigCenter = true
        } catch (err) {
          savedViaConfigCenter = false
        }
      }

      if (!savedViaConfigCenter) {
        await fetchJson('execution', '/api/v1/admin/system/runtime-config', {
          method: 'PATCH',
          body: { updates: payload, versioned, expected_latest_release_id: expectedLatestReleaseId },
        })
      }

      await loadRuntimeConfig()
      await loadConfigHistory(50)
      if (overviewState) {
        overviewState.runtimeConfig = platformSettingState.runtimeConfig
      }
      if (!versioned) {
        ui.message = '偏好配置已更新（非版本化）'
      } else {
        ui.message = savedViaConfigCenter ? '平台参数已更新（配置中心）' : '平台参数已更新（兼容模式）'
      }
    } catch (err) {
      const msg = String(err.message || err)
      const conflictDetail = parseConflictDetailFromErrorMessage(msg)
      if (
        msg.includes('409') ||
        msg.includes('release conflict') ||
        msg.includes('stale baseline') ||
        msg.includes('expected_latest_release_id')
      ) {
        platformSettingState.runtimeConfigConflict = conflictDetail || {
          expected_latest_release_id: null,
          actual_latest_release_id: null,
          message: 'config release conflict',
        }
        platformSettingState.error = '配置冲突：当前页面基线版本已过期，请先刷新发布历史后重试。'
      } else if (msg.includes('publish lock is held')) {
        platformSettingState.error = '发布锁占用中：已有其他请求正在发布，请稍后重试。'
      } else if (msg.includes('idempotency key reused')) {
        platformSettingState.error = '重复请求键冲突：同一幂等键不能用于不同内容。'
      } else if (msg.includes('publish note too short')) {
        platformSettingState.error = '发布备注过短：请填写至少 8 个字符。'
      } else if (msg.includes('apply failed')) {
        platformSettingState.error = '发布已写入，但运行时应用失败，请检查服务日志并重试同步。'
      } else {
        platformSettingState.runtimeConfigConflict = null
        platformSettingState.error = msg
      }
      ok = false
    } finally {
      platformSettingState.loading = false
    }
    return ok
  }

  async function previewRuntimeConfigImpact(nextConfig = null) {
    if (!isExecutionLoggedIn.value) return null
    const payload = nextConfig && typeof nextConfig === 'object' ? nextConfig : {}
    const data = await fetchJson('execution', '/api/v1/admin/config/impact', {
      method: 'POST',
      body: {
        scope_type: 'PLATFORM',
        scope_id: 'platform',
        updates: payload,
      },
    })
    return data || null
  }

  async function loadConfigHistory(limit = 50) {
    if (!isExecutionLoggedIn.value) return
    try {
      const data = await fetchJson(
        'execution',
        `/api/v1/admin/config/history?scope_type=PLATFORM&scope_id=platform&limit=${Math.max(1, Math.min(500, Number(limit || 50)))}`,
      )
      platformSettingState.configHistory = Array.isArray(data?.items) ? data.items : []
    } catch (err) {
      platformSettingState.error = String(err.message || err)
      platformSettingState.configHistory = []
    }
  }

  async function loadConfigDiff(releaseFrom, releaseTo) {
    if (!isExecutionLoggedIn.value) return null
    const fromId = Number(releaseFrom || 0)
    const toId = Number(releaseTo || 0)
    if (!fromId || !toId) {
      platformSettingState.error = '请选择两个发布版本进行对比'
      return null
    }
    try {
      const data = await fetchJson(
        'execution',
        `/api/v1/admin/config/diff?release_from=${encodeURIComponent(String(fromId))}&release_to=${encodeURIComponent(String(toId))}`,
      )
      platformSettingState.configDiff = data?.item || null
      return platformSettingState.configDiff
    } catch (err) {
      platformSettingState.error = String(err.message || err)
      platformSettingState.configDiff = null
      return null
    }
  }

  async function loadConfigAudit(options = {}) {
    if (!isExecutionLoggedIn.value) return
    const limit = Math.max(1, Math.min(2000, Number(options?.limit || 200)))
    const query = new URLSearchParams({
      scope_type: 'PLATFORM',
      scope_id: 'platform',
      limit: String(limit),
    })
    if (options?.config_key) query.set('config_key', String(options.config_key))
    if (options?.actor_id) query.set('actor_id', String(options.actor_id))
    if (options?.action) query.set('action', String(options.action))
    if (Number(options?.release_id || 0) > 0) query.set('release_id', String(Number(options.release_id)))
    if (options?.trace_id) query.set('trace_id', String(options.trace_id))
    if (options?.from_ts) query.set('from_ts', String(options.from_ts))
    if (options?.to_ts) query.set('to_ts', String(options.to_ts))
    try {
      const data = await fetchJson('execution', `/api/v1/admin/config/audit?${query.toString()}`)
      platformSettingState.configAuditItems = Array.isArray(data?.items) ? data.items : []
    } catch (err) {
      platformSettingState.error = String(err.message || err)
      platformSettingState.configAuditItems = []
    }
  }

  async function rollbackConfigRelease(releaseId) {
    if (!isExecutionLoggedIn.value) return false
    const id = Number(releaseId || 0)
    if (!id) {
      platformSettingState.error = 'release_id 无效'
      return false
    }
    platformSettingState.loading = true
    platformSettingState.error = ''
    try {
      await fetchJson('execution', '/api/v1/admin/config/rollback', {
        method: 'POST',
        body: { release_id: id },
      })
      await Promise.all([loadRuntimeConfig(), loadConfigHistory(50)])
      ui.message = `已切换到版本 #${id}`
      return true
    } catch (err) {
      platformSettingState.error = String(err.message || err)
      return false
    } finally {
      platformSettingState.loading = false
    }
  }

  async function switchDefaultConfig() {
    if (!isExecutionLoggedIn.value) return false
    platformSettingState.loading = true
    platformSettingState.error = ''
    try {
      await fetchJson('execution', '/api/v1/admin/config/switch-default', {
        method: 'POST',
        body: {
          scope_type: 'PLATFORM',
          scope_id: 'platform',
        },
      })
      await Promise.all([loadRuntimeConfig(), loadConfigHistory(80)])
      ui.message = '已切换到默认版本'
      return true
    } catch (err) {
      platformSettingState.error = String(err.message || err)
      return false
    } finally {
      platformSettingState.loading = false
    }
  }

  async function deleteConfigRelease(releaseId) {
    if (!isExecutionLoggedIn.value) return false
    const id = Number(releaseId || 0)
    if (!id) {
      platformSettingState.error = 'release_id 无效'
      return false
    }
    platformSettingState.loading = true
    platformSettingState.error = ''
    try {
      await fetchJson(
        'execution',
        `/api/v1/admin/config/history/${encodeURIComponent(String(id))}?scope_type=PLATFORM&scope_id=platform`,
        { method: 'DELETE' },
      )
      await loadConfigHistory(80)
      if (platformSettingState?.configDiff) {
        const fromId = Number(platformSettingState.configDiff.from_release_id || 0)
        const toId = Number(platformSettingState.configDiff.to_release_id || 0)
        if (fromId === id || toId === id) {
          platformSettingState.configDiff = null
        }
      }
      ui.message = `已删除版本 #${id}`
      return true
    } catch (err) {
      platformSettingState.error = String(err.message || err)
      return false
    } finally {
      platformSettingState.loading = false
    }
  }

  return {
    loadRuntimeConfig,
    formatRuntimeConfigText,
    saveRuntimeConfig,
    previewRuntimeConfigImpact,
    loadConfigHistory,
    loadConfigAudit,
    loadConfigDiff,
    rollbackConfigRelease,
    switchDefaultConfig,
    deleteConfigRelease,
  }
}
