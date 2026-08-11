export function clampPage(page, pageCount) {
  const total = Math.max(1, Math.trunc(Number(pageCount) || 1))
  return Math.min(total, Math.max(1, Math.trunc(Number(page) || 1)))
}

export function pagerState({ page, pageCount, totalItems = null, startItem = null, endItem = null } = {}) {
  const totalPages = Math.max(1, Math.trunc(Number(pageCount) || 1))
  const currentPage = clampPage(page, totalPages)
  const total = totalItems == null ? null : Math.max(0, Math.trunc(Number(totalItems) || 0))
  const start = startItem == null ? null : Math.max(0, Math.trunc(Number(startItem) || 0))
  const end = endItem == null ? null : Math.max(0, Math.trunc(Number(endItem) || 0))
  const range = start != null && end != null ? `，显示 ${start}-${end}` : ''
  const count = total == null ? '' : `，共 ${total} 条`
  return {
    page: currentPage,
    pageCount: totalPages,
    canPrevious: currentPage > 1,
    canNext: currentPage < totalPages,
    statusText: `第 ${currentPage} / ${totalPages} 页${range}${count}`,
  }
}
