export function useChatAdmin({ fetchJson, chatSettingState, ui, normalizeSessionId }) {
  async function loadChatConfig() {
    chatSettingState.loading = true
    chatSettingState.error = ''
    try {
      const data = await fetchJson('chat', '/api/v1/admin/chat/config')
      chatSettingState.config.chat_system_prompt = data?.chat_system_prompt || ''
      chatSettingState.config.chat_default_top_k = data?.chat_default_top_k ?? 6
      chatSettingState.config.chat_default_rank_method = data?.chat_default_rank_method || 'vector'
    } catch (error) {
      chatSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      chatSettingState.loading = false
    }
  }

  async function saveChatConfig() {
    chatSettingState.loading = true
    chatSettingState.error = ''
    try {
      await fetchJson('chat', '/api/v1/admin/chat/config', {
        method: 'PATCH',
        body: {
          chat_system_prompt: chatSettingState.config.chat_system_prompt,
          chat_default_top_k: Number(chatSettingState.config.chat_default_top_k) || 6,
          chat_default_rank_method: chatSettingState.config.chat_default_rank_method || 'vector',
        },
      })
      ui.message = 'Chat 配置已保存'
    } catch (error) {
      chatSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      chatSettingState.loading = false
    }
  }

  async function loadChatSessions() {
    chatSettingState.loading = true
    chatSettingState.error = ''
    try {
      const data = await fetchJson('chat', '/api/v1/chat/sessions?limit=100')
      chatSettingState.sessions = data?.items || []
      if (!chatSettingState.selectedSessionId && chatSettingState.sessions.length) {
        await selectSession(chatSettingState.sessions[0])
      }
    } catch (error) {
      chatSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      chatSettingState.loading = false
    }
  }

  async function createChatSession() {
    chatSettingState.loading = true
    chatSettingState.error = ''
    try {
      const kbIds = String(chatSettingState.create.kb_ids_csv || '')
        .split(',')
        .map((x) => x.trim())
        .filter(Boolean)
      const data = await fetchJson('chat', '/api/v1/chat/sessions', {
        method: 'POST',
        body: {
          title: chatSettingState.create.title.trim() || null,
          kb_ids: kbIds.length ? kbIds : [],
        },
      })
      chatSettingState.create.title = ''
      chatSettingState.create.kb_ids_csv = ''
      await loadChatSessions()
      if (data) {
        const sid = normalizeSessionId(data)
        if (sid) {
          chatSettingState.selectedSessionId = sid
          await loadChatMessages()
        }
      }
      ui.message = '会话已创建'
    } catch (error) {
      chatSettingState.error = error instanceof Error ? error.message : String(error)
    } finally {
      chatSettingState.loading = false
    }
  }

  async function selectSession(item) {
    chatSettingState.selectedSessionId = normalizeSessionId(item)
    await loadChatMessages()
  }

  async function loadChatMessages() {
    if (!chatSettingState.selectedSessionId) return
    chatSettingState.loading = true
    chatSettingState.error = ''
    try {
      const data = await fetchJson('chat', `/api/v1/chat/sessions/${encodeURIComponent(chatSettingState.selectedSessionId)}/messages?limit=200`)
      chatSettingState.messages = data?.items || []
    } catch (error) {
      chatSettingState.error = error instanceof Error ? error.message : String(error)
      chatSettingState.messages = []
    } finally {
      chatSettingState.loading = false
    }
  }

  async function queryRunStatus() {
    const runId = chatSettingState.run.runId.trim()
    if (!runId) return
    chatSettingState.loading = true
    chatSettingState.run.error = ''
    try {
      chatSettingState.run.detail = await fetchJson('chat', `/api/v1/chat/runs/${encodeURIComponent(runId)}`)
    } catch (error) {
      chatSettingState.run.error = error instanceof Error ? error.message : String(error)
    } finally {
      chatSettingState.loading = false
    }
  }

  async function cancelRunStatus() {
    const runId = chatSettingState.run.runId.trim()
    if (!runId) return
    chatSettingState.loading = true
    chatSettingState.run.error = ''
    try {
      await fetchJson('chat', `/api/v1/chat/runs/${encodeURIComponent(runId)}/cancel`, {
        method: 'POST',
        body: {},
      })
      ui.message = '已请求取消 Run'
      await queryRunStatus()
    } catch (error) {
      chatSettingState.run.error = error instanceof Error ? error.message : String(error)
    } finally {
      chatSettingState.loading = false
    }
  }

  return {
    loadChatConfig,
    saveChatConfig,
    loadChatSessions,
    createChatSession,
    selectSession,
    loadChatMessages,
    queryRunStatus,
    cancelRunStatus,
  }
}
