<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import AmiyaModel from './components/AmiyaModel.vue'
import SettingsPanel from './components/SettingsPanel.vue'

const interactionCount = ref(0)
const mood = ref('待机中')

// ---- 设置页：只负责开关，页面内部逻辑见 SettingsPanel.vue ----
const settingsOpen = ref(false)

/** 自主走动：空闲一段时间后随机走一小段；距离与朝向由主进程按屏幕工作区计算。 */
const walkFirstDelayMs = 5000
const walkIntervalMinMs = 9000
const walkIntervalMaxMs = 22000

const walking = ref(false)
const facingLeft = ref(false)
let walkScheduleTimer: number | null = null
let walkFallbackTimer: number | null = null

let hasDragged = false
let dragPointerId: number | null = null
let dragStartX = 0
let dragStartY = 0

function clearWalkTimers() {
  if (walkScheduleTimer !== null) {
    window.clearTimeout(walkScheduleTimer)
    walkScheduleTimer = null
  }
  if (walkFallbackTimer !== null) {
    window.clearTimeout(walkFallbackTimer)
    walkFallbackTimer = null
  }
}

function randomWalkDelay() {
  return walkIntervalMinMs + Math.random() * (walkIntervalMaxMs - walkIntervalMinMs)
}

function scheduleWalk(delay = randomWalkDelay()) {
  if (walkScheduleTimer !== null) window.clearTimeout(walkScheduleTimer)
  walkScheduleTimer = window.setTimeout(() => { void tryWalk() }, delay)
}

/** 走动自然结束（或兜底超时）：复位状态并安排下一次。 */
function finishWalk() {
  if (walkFallbackTimer !== null) {
    window.clearTimeout(walkFallbackTimer)
    walkFallbackTimer = null
  }
  walking.value = false
  scheduleWalk()
}

/** 用户介入（点击、拖拽、右键菜单、打开弹窗）：立刻停下脚步。 */
function stopWalking() {
  if (walkFallbackTimer !== null) {
    window.clearTimeout(walkFallbackTimer)
    walkFallbackTimer = null
  }
  if (walking.value) {
    walking.value = false
    window.ipcRenderer?.send('walk-stop')
  }
  scheduleWalk()
}

async function tryWalk() {
  walkScheduleTimer = null

  if (walking.value || settingsOpen.value) {
    scheduleWalk()
    return
  }

  try {
    const started = await window.ipcRenderer.invoke('walk-start') as
      { direction: 'left' | 'right'; durationMs: number } | null
    if (!started) {
      scheduleWalk()
      return
    }

    facingLeft.value = started.direction === 'left'
    walking.value = true
    // 兜底：万一 walk-end 没送达，走动时长结束后自行复位
    walkFallbackTimer = window.setTimeout(finishWalk, started.durationMs + 500)
  } catch {
    scheduleWalk()
  }
}

function handleWalkEnd() {
  finishWalk()
}

function interact() {
  if (hasDragged) {
    hasDragged = false
    return
  }

  // 被摸到时先停下脚步，避免 Move 与交互动画抢占同一条轨道
  stopWalking()
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
  stopWalking()
  window.ipcRenderer?.send('show-context-menu')
}

function openSettings() {
  stopWalking()
  settingsOpen.value = true
}

function closeSettings() {
  settingsOpen.value = false
  scheduleWalk()
}

function handleOpenSettings() {
  openSettings()
}

onMounted(() => {
  window.ipcRenderer?.on('open-settings', handleOpenSettings)
  window.ipcRenderer?.on('walk-end', handleWalkEnd)
  scheduleWalk(walkFirstDelayMs)
})

onBeforeUnmount(() => {
  window.ipcRenderer?.off('open-settings', handleOpenSettings)
  window.ipcRenderer?.off('walk-end', handleWalkEnd)
  clearWalkTimers()
  if (walking.value) window.ipcRenderer?.send('walk-stop')
})

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
        <AmiyaModel
          :interaction-key="interactionCount"
          :pointer-interaction-enabled="!settingsOpen"
          :walking="walking"
          :facing-left="facingLeft"
        />
      </button>

      <div class="pet-info">
        <div>
          <p class="pet-label">当前状态</p>
          <strong>{{ walking ? '散步中' : mood }}</strong>
        </div>
        <span class="interaction-count">互动 {{ interactionCount }}</span>
      </div>
    </section>

    <SettingsPanel
      v-if="settingsOpen"
      :start-drag="startWindowDrag"
      :move-drag="moveWindow"
      :end-drag="endWindowDrag"
      @close="closeSettings"
    />

  </main>
</template>
