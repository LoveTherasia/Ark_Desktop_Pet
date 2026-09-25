<script setup lang="ts">
import { ref } from 'vue'
import AmiyaModel from './components/AmiyaModel.vue'

const interactionCount = ref(0)
const mood = ref('待机中')
let hasDragged = false
let dragPointerId: number | null = null
let dragStartX = 0
let dragStartY = 0

function interact() {
  if (hasDragged) {
    hasDragged = false
    return
  }

  interactionCount.value += 1
  mood.value = interactionCount.value % 2 === 0 ? '心情不错' : '被摸到了'
}

function startWindowDrag(event: PointerEvent) {
  if (event.button !== 0) return

  dragPointerId = event.pointerId
  dragStartX = event.screenX
  dragStartY = event.screenY
  hasDragged = false
  window.ipcRenderer?.send('window-drag-start', event.screenX, event.screenY)

  const target = event.currentTarget
  if (target instanceof HTMLElement) {
    target.setPointerCapture(event.pointerId)
  }
}

function moveWindow(event: PointerEvent) {
  if (event.pointerId !== dragPointerId) return

  const movedX = event.screenX - dragStartX
  const movedY = event.screenY - dragStartY
  if (Math.abs(movedX) > 3 || Math.abs(movedY) > 3) {
    hasDragged = true
  }

  if (hasDragged) {
    window.ipcRenderer?.send('window-drag-move', event.screenX, event.screenY)
  }
}

function endWindowDrag(event: PointerEvent) {
  if (event.pointerId !== dragPointerId) return

  window.ipcRenderer?.send('window-drag-end')
  dragPointerId = null
  const target = event.currentTarget
  if (target instanceof HTMLElement && target.hasPointerCapture(event.pointerId)) {
    target.releasePointerCapture(event.pointerId)
  }
}

function openContextMenu() {
  window.ipcRenderer?.send('show-context-menu')
}

void interact
void openContextMenu
</script>

<template>
  <main class="pet-shell" @contextmenu.prevent="openContextMenu">
    <section class="pet-card" aria-label="明日方舟桌宠">
      <header class="pet-header">
        <span class="status-dot" aria-hidden="true"></span>
        <span>ARK PET</span>
      </header>

      <button
        class="pet-stage"
        type="button"
        aria-label="和阿米娅互动"
        @click="interact"
        @pointerdown="startWindowDrag"
        @pointermove="moveWindow"
        @pointerup="endWindowDrag"
        @pointercancel="endWindowDrag"
      >
        <AmiyaModel :interaction-key="interactionCount" />
      </button>

      <div class="pet-info">
        <div>
          <p class="pet-label">当前状态</p>
          <strong>{{ mood }}</strong>
        </div>
        <span class="interaction-count">互动 {{ interactionCount }}</span>
      </div>
    </section>
  </main>
</template>
