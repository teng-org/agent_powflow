export function enabledOptionIndexes(options = []) {
  return options.reduce((indexes, option, index) => {
    if (!option?.disabled) indexes.push(index)
    return indexes
  }, [])
}

export function initialOptionIndex(options = [], selectedIndex = -1, direction = 1) {
  const enabled = enabledOptionIndexes(options)
  if (!enabled.length) return -1
  if (enabled.includes(selectedIndex)) return selectedIndex
  return direction < 0 ? enabled.at(-1) : enabled[0]
}

export function nextOptionIndex(options = [], currentIndex = -1, direction = 1) {
  const enabled = enabledOptionIndexes(options)
  if (!enabled.length) return -1
  const position = enabled.indexOf(currentIndex)
  if (position < 0) return direction < 0 ? enabled.at(-1) : enabled[0]
  return enabled[(position + (direction < 0 ? -1 : 1) + enabled.length) % enabled.length]
}

export function edgeOptionIndex(options = [], edge = 'start') {
  const enabled = enabledOptionIndexes(options)
  if (!enabled.length) return -1
  return edge === 'end' ? enabled.at(-1) : enabled[0]
}
