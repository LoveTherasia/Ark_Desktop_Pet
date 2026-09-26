<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import AmiyaModel from './components/AmiyaModel.vue'
import PetBubble, { type BubbleMessage } from './components/PetBubble.vue'
import { useWindowDrag } from './composables/useWindowDrag'

const interactionCount = ref(0)
const mood = ref('待机中')

// 桌宠窗口的拖动（设置窗口是独立的 BrowserWindow，用自己的那份拖动逻辑）
const { startWindowDrag, moveWindow, endWindowDrag, consumeDragged } = useWindowDrag()

// ---- 聊天气泡：消息由主进程推送，这里只负责展示与窗口加宽 ----
const bubbleVisibleMs = 8000
const bubble = ref<BubbleMessage | null>(null)
const bubbleSide = ref<'right' | 'left'>('right')
/** 由主进程的设置决定；关闭后即使收到推送也不再显示 */
const activityBubbleEnabled = ref(true)
let bubbleTimer: number | null = null

async function showBubble(message: BubbleMessage) {
  if (!activityBubbleEnabled.value) return

  bubble.value = message
  const result = await window.ipcRenderer.invoke('bubble-resize', true) as { side: 'right' | 'left' }
  bubbleSide.value = result?.side ?? 'right'

  if (bubbleTimer !== null) window.clearTimeout(bubbleTimer)
  bubbleTimer = window.setTimeout(() => { void hideBubble() }, bubbleVisibleMs)
}

async function hideBubble() {
  if (bubbleTimer !== null) {
    window.clearTimeout(bubbleTimer)
    bubbleTimer = null
  }
  if (!bubble.value) return

  bubble.value = null
  await window.ipcRenderer.invoke('bubble-resize', false)
}

function handleBubbleShow(_event: unknown, message: BubbleMessage) {
  void showBubble(message)
}

function handleSettingsChanged(_event: unknown, settings: { activityBubbleEnabled?: boolean }) {
  activityBubbleEnabled.value = settings?.activityBubbleEnabled !== false
  // 刚被关闭时立刻收起已经显示的气泡
  if (!activityBubbleEnabled.value) void hideBubble()
}

async function loadSettings() {
  try {
    const settings = await window.ipcRenderer.invoke('settings-get') as { activityBubbleEnabled?: boolean }
    activityBubbleEnabled.value = settings?.activityBubbleEnabled !== false
  } catch {
    activityBubbleEnabled.value = true
  }
}

/**
 * 自主走动：空闲一段时间后随机走一小段；距离与朝向由主进程按屏幕工作区计算。
 * 频率刻意放得很低，避免频繁移动打扰日常使用。
 */
const walkFirstDelayMs = 20000
const walkIntervalMinMs = 60000
const walkIntervalMaxMs = 180000

const walking = ref(false)
const facingLeft = ref(false)
let walkScheduleTimer: number | null = null
let walkFallbackTimer: number | null = null

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

  if (walking.value) {
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
  // 拖动过就不算点击（避免拖窗口时误触互动）
  if (consumeDragged()) return

  // 被摸到时先停下脚步，避免 Move 与交互动画抢占同一条轨道
  stopWalking()
  interactionCount.value += 1
  mood.value = interactionCount.value % 2 === 0 ? '心情不错' : '被摸到了'
}

function openContextMenu() {
  stopWalking()
  window.ipcRenderer?.send('show-context-menu')
}

onMounted(() => {
  window.ipcRenderer?.on('bubble-show', handleBubbleShow)
  window.ipcRenderer?.on('settings-changed', handleSettingsChanged)
  window.ipcRenderer?.on('walk-end', handleWalkEnd)
  void loadSettings()
  scheduleWalk(walkFirstDelayMs)
})

onBeforeUnmount(() => {
  window.ipcRenderer?.off('bubble-show', handleBubbleShow)
  window.ipcRenderer?.off('settings-changed', handleSettingsChanged)
  window.ipcRenderer?.off('walk-end', handleWalkEnd)
  clearWalkTimers()
  if (bubbleTimer !== null) window.clearTimeout(bubbleTimer)
  if (walking.value) window.ipcRenderer?.send('walk-stop')
})

void interact
void openContextMenu
</script>

<template>
  <main
    class="pet-shell"
    :class="{ 'shell-bubble-left': bubbleSide === 'left' }"
    @contextmenu.prevent="openContextMenu"
  >
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

    <Transition name="pet-bubble">
      <PetBubble v-if="bubble" :message="bubble" />
    </Transition>
  </main>
</template>
