export function useConsoleHelpers() {
  function getKbId(item) {
    return item?.kb_id || item?.kbId || item?.id || ''
  }

  function getKbName(item) {
    return item?.name || item?.kb_name || item?.title || getKbId(item)
  }

  function getDocId(item) {
    return item?.doc_id || item?.docId || item?.id || ''
  }

  function getDocName(item) {
    return item?.filename || item?.name || getDocId(item)
  }

  function getChunkId(item) {
    return item?.chunk_id || item?.chunkId || item?.id || ''
  }

  function getTaskId(item) {
    return item?.task_id || item?.taskId || item?.id || ''
  }

  function normalizeSessionId(item) {
    return item?.session_id || item?.sessionId || item?.id || ''
  }

  function endpointIdOf(item) {
    return item?.endpoint_id || item?.endpointId || item?.id || ''
  }

  function formatJson(value) {
    if (value === null || value === undefined) return '-'
    try {
      return JSON.stringify(value, null, 2)
    } catch {
      return String(value)
    }
  }

  function downloadBlob(content, filename, contentType = 'text/plain;charset=utf-8') {
    const blob = new Blob([content], { type: contentType })
    const href = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = href
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(href)
  }

  return {
    getKbId,
    getKbName,
    getDocId,
    getDocName,
    getChunkId,
    getTaskId,
    normalizeSessionId,
    endpointIdOf,
    formatJson,
    downloadBlob,
  }
}
