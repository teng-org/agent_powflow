<script setup>
import { nextTick, ref } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  type: { type: String, default: 'date' }, // date | time
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '' },
})

const emit = defineEmits(['update:modelValue'])

const open = ref(false)
const inputRef = ref(null)

function openPanel() {
  if (props.disabled) return
  if (open.value) return
  open.value = true
  nextTick(() => {
    const el = inputRef.value
    if (!el) return
    el.focus()
    if (typeof el.showPicker === 'function') {
      try {
        el.showPicker()
      } catch {
        // noop
      }
    }
  })
}

function closePanel() {
  open.value = false
}

function togglePanel() {
  if (props.disabled) return
  if (open.value) {
    closePanel()
    return
  }
  openPanel()
}

function onInput(event) {
  emit('update:modelValue', event?.target?.value || '')
}

function displayText() {
  if (props.modelValue) return props.modelValue
  if (props.placeholder) return props.placeholder
  return props.type === 'time' ? '--:--' : 'yyyy/mm/dd'
}
</script>

<template>
  <div
    class="ui-picker"
    :class="{ open, disabled }"
    @mouseenter="openPanel"
    @mouseleave="closePanel"
  >
    <button type="button" class="ui-picker-trigger" :disabled="disabled" @click="togglePanel">
      <span class="ui-picker-text">{{ displayText() }}</span>
      <span class="ui-picker-arrow" aria-hidden="true"></span>
    </button>

    <transition name="ui-picker-drop">
      <div v-if="open" class="ui-picker-panel">
        <input
          ref="inputRef"
          class="ui-picker-input"
          :type="type"
          :value="modelValue"
          :disabled="disabled"
          @input="onInput"
          @change="onInput"
        />
      </div>
    </transition>
  </div>
</template>

<style scoped>
.ui-picker {
  position: relative;
  width: 100%;
  min-width: 0;
}

.ui-picker-trigger {
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

.ui-picker:not(.disabled) .ui-picker-trigger:hover {
  border-color: var(--field-border-hover, #9db8df);
  background: var(--field-bg-hover, #fcfeff);
}

.ui-picker.open .ui-picker-trigger,
.ui-picker:not(.disabled) .ui-picker-trigger:focus-visible {
  outline: none;
  border-color: var(--field-border-focus, #5b8fdd);
  box-shadow: 0 0 0 2px var(--field-ring-focus, rgba(91, 143, 221, 0.2));
  background: var(--field-bg-focus, #f7fbff);
}

.ui-picker-text {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ui-picker-arrow {
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

.ui-picker.open .ui-picker-arrow {
  transform: translateY(-35%) rotate(-135deg);
}

.ui-picker-panel {
  position: absolute;
  z-index: 52;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  border: 1px solid #cfe0f6;
  border-radius: 8px;
  background: #fff;
  box-shadow: 0 12px 24px rgba(24, 45, 73, 0.16);
  padding: 6px;
}

.ui-picker-input {
  width: 100%;
  min-height: 32px;
  height: 32px;
  border: 1px solid var(--field-border-default, #c8d7eb);
  border-radius: 8px;
  padding: 4px 8px;
  font-size: 12px;
  box-sizing: border-box;
}

.ui-picker-drop-enter-active,
.ui-picker-drop-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
  transform-origin: top center;
}

.ui-picker-drop-enter-from,
.ui-picker-drop-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.98);
}
</style>
