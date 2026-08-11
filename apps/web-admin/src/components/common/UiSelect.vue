<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useId } from 'vue'
import { edgeOptionIndex, initialOptionIndex, nextOptionIndex } from './selectModel'

const props = defineProps({
  modelValue: { type: [String, Number, Boolean, null], default: '' },
  options: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '请选择' },
  placement: { type: String, default: 'bottom' }, // bottom | top
  ariaLabel: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue'])
const rootRef = ref(null)
const triggerRef = ref(null)
const optionRefs = ref([])
const open = ref(false)
const activeIndex = ref(-1)
const keyboardInteraction = ref(false)
const componentId = useId().replace(/[^a-zA-Z0-9_-]/g, '')
const triggerId = `ui-select-trigger-${componentId}`
const panelId = `ui-select-panel-${componentId}`

const normalizedOptions = computed(() =>
  (props.options || []).map((x) => {
    if (x && typeof x === 'object' && 'label' in x) return x
    return { label: String(x ?? ''), value: x }
  }),
)

const selectedLabel = computed(() => {
  const hit = normalizedOptions.value.find((x) => x.value === props.modelValue)
  return hit ? String(hit.label) : props.placeholder
})

function selectedIndex() {
  return normalizedOptions.value.findIndex((option) => option.value === props.modelValue)
}

function focusOption(index) {
  if (index < 0) return
  activeIndex.value = index
  nextTick(() => optionRefs.value[index]?.focus())
}

function openPanel({ direction = 1, keyboard = false, edge = '' } = {}) {
  if (props.disabled) return
  keyboardInteraction.value = keyboard
  open.value = true
  const index = edge
    ? edgeOptionIndex(normalizedOptions.value, edge)
    : initialOptionIndex(normalizedOptions.value, selectedIndex(), direction)
  focusOption(index)
}

function toggle() {
  if (props.disabled) return
  if (open.value) {
    close()
    return
  }
  openPanel({ keyboard: false })
}

function close({ restoreFocus = false, keyboard = keyboardInteraction.value } = {}) {
  keyboardInteraction.value = keyboard
  open.value = false
  activeIndex.value = -1
  if (restoreFocus) nextTick(() => triggerRef.value?.focus())
}

function choose(value, { keyboard = false } = {}) {
  emit('update:modelValue', value)
  close({ restoreFocus: true, keyboard })
}

function onDocPointerDown(event) {
  const el = rootRef.value
  if (!el) return
  if (el.contains(event.target)) return
  close({ keyboard: false })
}

function onTriggerKeydown(event) {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    openPanel({ direction: event.key === 'ArrowUp' ? -1 : 1, keyboard: true })
  } else if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault()
    openPanel({ keyboard: true, edge: event.key === 'End' ? 'end' : 'start' })
  } else if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    if (open.value) close({ restoreFocus: true, keyboard: true })
    else openPanel({ keyboard: true })
  } else if (event.key === 'Escape' && open.value) {
    event.preventDefault()
    close({ restoreFocus: true, keyboard: true })
  }
}

function onOptionKeydown(event, index) {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    keyboardInteraction.value = true
    focusOption(nextOptionIndex(normalizedOptions.value, index, event.key === 'ArrowUp' ? -1 : 1))
  } else if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault()
    keyboardInteraction.value = true
    focusOption(edgeOptionIndex(normalizedOptions.value, event.key === 'End' ? 'end' : 'start'))
  } else if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    if (!normalizedOptions.value[index]?.disabled) choose(normalizedOptions.value[index].value, { keyboard: true })
  } else if (event.key === 'Escape') {
    event.preventDefault()
    close({ restoreFocus: true, keyboard: true })
  } else if (event.key === 'Tab') {
    close({ keyboard: true })
  }
}

function onDocumentKeydown(event) {
  if (event.key === 'Escape' && open.value) close({ restoreFocus: true, keyboard: true })
}

onMounted(() => {
  document.addEventListener('pointerdown', onDocPointerDown, true)
  document.addEventListener('keydown', onDocumentKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocPointerDown, true)
  document.removeEventListener('keydown', onDocumentKeydown)
})
</script>

<template>
  <div ref="rootRef" class="ui-select" :class="{ open, disabled, 'opens-up': placement === 'top' }">
    <button
      :id="triggerId"
      ref="triggerRef"
      type="button"
      class="ui-select-trigger"
      :disabled="disabled"
      :aria-label="ariaLabel || undefined"
      aria-haspopup="listbox"
      :aria-expanded="open"
      :aria-controls="panelId"
      @click="toggle"
      @keydown="onTriggerKeydown"
    >
      <span class="ui-select-text">{{ selectedLabel }}</span>
      <span class="ui-select-arrow" aria-hidden="true"></span>
    </button>

    <transition name="ui-select-drop" :css="!keyboardInteraction">
      <div v-if="open" :id="panelId" class="ui-select-panel" role="listbox" :aria-labelledby="triggerId">
        <button
          v-for="(opt, idx) in normalizedOptions"
          :key="`${idx}-${String(opt.value)}`"
          type="button"
          class="ui-select-option"
          :class="{ active: opt.value === modelValue }"
          :disabled="disabled || opt.disabled"
          role="option"
          :aria-selected="opt.value === modelValue"
          :tabindex="idx === activeIndex ? 0 : -1"
          :ref="(element) => { optionRefs[idx] = element }"
          @click="choose(opt.value)"
          @focus="activeIndex = idx"
          @keydown="onOptionKeydown($event, idx)"
        >
          {{ opt.label }}
        </button>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.ui-select {
  position: relative;
  width: 100%;
  min-width: 0;
}

.ui-select-trigger {
  width: 100%;
  min-height: 32px;
  height: 32px;
  border: 1px solid var(--field-border-default, #c8d7eb);
  border-radius: 8px;
  background: var(--field-bg-default, #fff);
  padding: 0 28px 0 10px;
  text-align: left;
  font-size: 12px;
  color: #1f3653;
  cursor: pointer;
  position: relative;
  transition:
    border-color 0.2s cubic-bezier(0.22, 1, 0.36, 1),
    box-shadow 0.22s cubic-bezier(0.22, 1, 0.36, 1),
    background-color 0.2s ease;
}

.ui-select:not(.disabled) .ui-select-trigger:active {
  transform: scale(0.98);
}

@media (hover: hover) and (pointer: fine) {
  .ui-select:not(.disabled) .ui-select-trigger:hover {
    border-color: var(--field-border-hover, #9db8df);
    background: var(--field-bg-hover, #fcfeff);
  }
}

.ui-select.open .ui-select-trigger,
.ui-select:not(.disabled) .ui-select-trigger:focus-visible {
  outline: none;
  border-color: var(--field-border-focus, #5b8fdd);
  box-shadow: 0 0 0 2px var(--field-ring-focus, rgba(91, 143, 221, 0.2));
  background: var(--field-bg-focus, #f7fbff);
}

.ui-select-text {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ui-select-arrow {
  position: absolute;
  right: 10px;
  top: 50%;
  width: 6px;
  height: 6px;
  border-right: 2px solid #6c809b;
  border-bottom: 2px solid #6c809b;
  transform: translateY(-65%) rotate(45deg);
  transition: transform 0.2s ease;
}

.ui-select.open .ui-select-arrow {
  transform: translateY(-35%) rotate(-135deg);
}

.ui-select.opens-up .ui-select-arrow {
  transform: translateY(-35%) rotate(-135deg);
}

.ui-select.opens-up.open .ui-select-arrow {
  transform: translateY(-65%) rotate(45deg);
}

.ui-select-panel {
  position: absolute;
  z-index: 50;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  display: grid;
  grid-auto-flow: row;
  gap: 2px;
  max-height: 240px;
  overflow: auto;
  border: 1px solid #cfe0f6;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 12px 24px rgba(24, 45, 73, 0.16);
  padding: 4px;
  white-space: normal;
}

.ui-select.opens-up .ui-select-panel {
  top: auto;
  bottom: calc(100% + 4px);
}

.ui-select.opens-up .ui-select-drop-enter-active,
.ui-select.opens-up .ui-select-drop-leave-active {
  transform-origin: bottom center;
}

.ui-select-option {
  display: block;
  width: 100%;
  border: 0;
  border-radius: 6px;
  background: transparent;
  padding: 7px 8px;
  font-size: 12px;
  text-align: left;
  color: #2a3f5a;
  cursor: pointer;
}

@media (hover: hover) and (pointer: fine) {
  .ui-select-option:hover { background: #f1f7ff; }
}

.ui-select-option:focus-visible {
  outline: 2px solid var(--field-border-focus, #5b8fdd);
  outline-offset: -2px;
  background: #f1f7ff;
}

.ui-select-option.active {
  background: #e7f1ff;
  color: #174b92;
  font-weight: 700;
}

.ui-select-drop-enter-active,
.ui-select-drop-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
  transform-origin: top center;
}

.ui-select-drop-enter-from,
.ui-select-drop-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.98);
}

@media (prefers-reduced-motion: reduce) {
  .ui-select-trigger,
  .ui-select-arrow,
  .ui-select-drop-enter-active,
  .ui-select-drop-leave-active {
    transition: border-color 100ms ease, box-shadow 100ms ease, background-color 100ms ease;
  }

  .ui-select:not(.disabled) .ui-select-trigger:active { transform: none; }
  .ui-select-drop-enter-from,
  .ui-select-drop-leave-to { transform: none; }
}
</style>
