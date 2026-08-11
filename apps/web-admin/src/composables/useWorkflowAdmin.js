import { computed, reactive } from 'vue'

export const DEFAULT_WORKFLOW_DSL = {
  entry_node_id: 'start-1',
  nodes: [
    { id: 'start-1', type: 'start', config: {} },
    { id: 'llm-1', type: 'llm.generate', config: {} },
    { id: 'end-1', type: 'end', config: {} },
  ],
  edges: [
    { id: 'edge-1', source_node_id: 'start-1', target_node_id: 'llm-1', condition_label: '' },
    { id: 'edge-2', source_node_id: 'llm-1', target_node_id: 'end-1', condition_label: '' },
  ],
}

function pretty(value) {
  return JSON.stringify(value ?? {}, null, 2)
}

export function parseWorkflowJson(text, label) {
  let value
  try {
    value = JSON.parse(String(text || ''))
  } catch (error) {
    throw new Error(`${label} 不是有效 JSON：${error.message}`)
  }
  if (!value || Array.isArray(value) || typeof value !== 'object') {
    throw new Error(`${label} 必须是 JSON 对象`)
  }
  return value
}

export function useWorkflowAdmin(fetchJson) {
  const state = reactive({
    items: [],
    selectedId: '',
    item: null,
    versions: [],
    selectedVersion: null,
    validation: null,
    loading: false,
    saving: false,
    action: '',
    error: '',
    message: '',
    editor: { name: '', description: '', dslText: pretty(DEFAULT_WORKFLOW_DSL), layoutText: '{}' },
  })

  const selectedIsArchived = computed(() => state.item?.status === 'ARCHIVED')
  const canPublish = computed(() => Boolean(state.item && !selectedIsArchived.value && state.validation?.valid))

  function clearNotice() {
    state.error = ''
    state.message = ''
  }

  function applyItem(item, validation = null) {
    state.item = item || null
    state.selectedId = item?.workflow_id || ''
    state.editor.name = item?.name || ''
    state.editor.description = item?.description || ''
    state.editor.dslText = pretty(item?.draft_dsl || DEFAULT_WORKFLOW_DSL)
    state.editor.layoutText = pretty(item?.draft_layout || {})
    state.validation = validation
    state.selectedVersion = null
  }

  async function loadList({ preserveNotice = false } = {}) {
    state.loading = true
    if (!preserveNotice) clearNotice()
    try {
      const response = await fetchJson('control', '/api/v1/admin/workflows')
      state.items = response?.items || []
      return state.items
    } catch (error) {
      state.error = error?.message || String(error)
      throw error
    } finally {
      state.loading = false
    }
  }

  async function select(workflowId, { preserveNotice = false } = {}) {
    const id = String(workflowId || '').trim()
    if (!id) return null
    state.loading = true
    if (!preserveNotice) clearNotice()
    try {
      const [detail, versions] = await Promise.all([
        fetchJson('control', `/api/v1/admin/workflows/${encodeURIComponent(id)}`),
        fetchJson('control', `/api/v1/admin/workflows/${encodeURIComponent(id)}/versions`),
      ])
      applyItem(detail?.item, detail?.validation || null)
      state.versions = versions?.items || []
      return state.item
    } catch (error) {
      state.error = error?.message || String(error)
      throw error
    } finally {
      state.loading = false
    }
  }

  function newDraft() {
    clearNotice()
    applyItem(null)
    state.versions = []
    state.editor.name = '未命名工作流'
  }

  function editorPayload() {
    const name = state.editor.name.trim()
    if (!name) throw new Error('工作流名称不能为空')
    return {
      name,
      description: state.editor.description.trim(),
      dsl: parseWorkflowJson(state.editor.dslText, 'DSL'),
      layout: parseWorkflowJson(state.editor.layoutText, 'Layout'),
    }
  }

  async function save() {
    clearNotice()
    state.saving = true
    try {
      const body = editorPayload()
      let response
      if (state.item) {
        response = await fetchJson('control', `/api/v1/admin/workflows/${encodeURIComponent(state.item.workflow_id)}/draft`, {
          method: 'PUT',
          body: { ...body, expected_revision: state.item.revision },
        })
      } else {
        response = await fetchJson('control', '/api/v1/admin/workflows', { method: 'POST', body })
      }
      applyItem(response?.item, response?.validation || null)
      state.message = state.validation?.valid ? '草稿已保存并通过校验。' : '草稿已保存，但仍有校验错误。'
      await loadList({ preserveNotice: true })
      return state.item
    } catch (error) {
      state.error = error?.message || String(error)
      throw error
    } finally {
      state.saving = false
    }
  }

  async function validate() {
    if (!state.item) throw new Error('请先保存草稿')
    clearNotice()
    state.action = 'validate'
    try {
      const response = await fetchJson('control', `/api/v1/admin/workflows/${encodeURIComponent(state.item.workflow_id)}/validate`, {
        method: 'POST', body: {},
      })
      state.validation = response
      state.message = response?.valid ? '校验通过，可以发布。' : '校验未通过，请按问题列表修正 DSL。'
      return response
    } catch (error) {
      state.error = error?.message || String(error)
      throw error
    } finally {
      state.action = ''
    }
  }

  async function publish() {
    if (!state.item) throw new Error('请先保存草稿')
    clearNotice()
    state.action = 'publish'
    try {
      const response = await fetchJson('control', `/api/v1/admin/workflows/${encodeURIComponent(state.item.workflow_id)}/publish`, {
        method: 'POST', body: { expected_revision: state.item.revision },
      })
      const versionNo = response?.item?.version_no
      state.message = `已发布不可变版本 v${versionNo}。`
      await select(state.item.workflow_id, { preserveNotice: true })
      await loadList({ preserveNotice: true })
      return response?.item
    } catch (error) {
      state.error = error?.message || String(error)
      throw error
    } finally {
      state.action = ''
    }
  }

  async function archive() {
    if (!state.item) return
    clearNotice()
    state.action = 'archive'
    try {
      await fetchJson('control', `/api/v1/admin/workflows/${encodeURIComponent(state.item.workflow_id)}/archive`, {
        method: 'POST', body: { expected_revision: state.item.revision },
      })
      state.message = '工作流已归档；历史版本仍可查看，不能再编辑或发布。'
      await select(state.item.workflow_id, { preserveNotice: true })
      await loadList({ preserveNotice: true })
    } catch (error) {
      state.error = error?.message || String(error)
      throw error
    } finally {
      state.action = ''
    }
  }

  async function remove() {
    if (!state.item || state.item.status !== 'ARCHIVED') throw new Error('只能删除已归档工作流')
    clearNotice()
    state.action = 'delete'
    const id = state.item.workflow_id
    try {
      await fetchJson('control', `/api/v1/admin/workflows/${encodeURIComponent(id)}`, { method: 'DELETE' })
      applyItem(null)
      state.versions = []
      state.message = '已永久删除归档工作流。'
      await loadList({ preserveNotice: true })
    } catch (error) {
      state.error = error?.message || String(error)
      throw error
    } finally {
      state.action = ''
    }
  }

  async function viewVersion(versionNo) {
    if (!state.item) return null
    clearNotice()
    state.action = `version-${versionNo}`
    try {
      const response = await fetchJson(
        'control',
        `/api/v1/admin/workflows/${encodeURIComponent(state.item.workflow_id)}/versions/${encodeURIComponent(versionNo)}`,
      )
      state.selectedVersion = response?.item || null
      return state.selectedVersion
    } catch (error) {
      state.error = error?.message || String(error)
      throw error
    } finally {
      state.action = ''
    }
  }

  return {
    state,
    selectedIsArchived,
    canPublish,
    loadList,
    select,
    newDraft,
    save,
    validate,
    publish,
    archive,
    remove,
    viewVersion,
  }
}
