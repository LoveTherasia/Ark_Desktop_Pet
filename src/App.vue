<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import AmiyaModel from './components/AmiyaModel.vue'

interface ModelFile {
  name: string
  size: number
}

interface ModelOption {
  name: string
  files: ModelFile[]
}

interface LocalModel {
  name: string
  skeleton: string
  atlas: string
  texture: string
}

const interactionCount = ref(0)
const mood = ref('待机中')
const modelBrowserOpen = ref(false)
const modelQuery = ref('')
const modelResults = ref<ModelOption[]>([])
const selectedModel = ref<ModelOption | null>(null)
const modelLoading = ref(false)
const modelDownloading = ref(false)
const modelMessage = ref('')
const modelError = ref('')
const characterBrowserOpen = ref(false)
const localModels = ref<LocalModel[]>([])
const selectedCharacter = ref<LocalModel | null>(null)
const characterLoading = ref(false)

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

  if (walking.value || modelBrowserOpen.value || characterBrowserOpen.value) {
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

function resizeWindow(width: number, height: number) {
  window.ipcRenderer?.send('resize-window', width, height)
}

function setMouseEventsIgnored(ignore: boolean) {
  window.ipcRenderer?.send('set-ignore-mouse-events', ignore)
}

function openModelBrowser() {
  stopWalking()
  modelBrowserOpen.value = true
  modelMessage.value = ''
  modelError.value = ''
  setMouseEventsIgnored(false)
  resizeWindow(620, 620)
}

function closeModelBrowser() {
  modelBrowserOpen.value = false
  selectedModel.value = null
  setMouseEventsIgnored(true)
  resizeWindow(320, 380)
  scheduleWalk()
}

async function refreshLocalModels() {
  try {
    localModels.value = await window.ipcRenderer.invoke('list-local-models') as LocalModel[]
  } catch {
    localModels.value = []
  }
}

function openCharacterBrowser() {
  stopWalking()
  characterBrowserOpen.value = true
  selectedCharacter.value = null
  modelError.value = ''
  modelMessage.value = ''
  setMouseEventsIgnored(false)
  resizeWindow(620, 620)
  void refreshLocalModels()
}

function closeCharacterBrowser() {
  characterBrowserOpen.value = false
  selectedCharacter.value = null
  setMouseEventsIgnored(true)
  resizeWindow(320, 380)
  scheduleWalk()
}

async function confirmCharacterChange() {
  if (!selectedCharacter.value) return

  characterLoading.value = true
  modelError.value = ''
  try {
    await window.ipcRenderer.invoke('activate-local-model', selectedCharacter.value.name)
  } catch (error) {
    modelError.value = error instanceof Error ? error.message : '更换人物失败'
    characterLoading.value = false
  }
}

async function searchModels() {
  const query = modelQuery.value.trim()
  if (!query) return

  modelLoading.value = true
  modelError.value = ''
  modelMessage.value = ''
  selectedModel.value = null
  try {
    modelResults.value = await window.ipcRenderer.invoke('search-models', query) as ModelOption[]
    if (!modelResults.value.length) modelMessage.value = '没有找到匹配的模型文件夹'
  } catch (error) {
    modelError.value = error instanceof Error ? error.message : '查询模型失败'
  } finally {
    modelLoading.value = false
  }
}

async function downloadSelectedModel() {
  if (!selectedModel.value) return

  modelDownloading.value = true
  modelError.value = ''
  modelMessage.value = ''
  try {
    const result = await window.ipcRenderer.invoke(
      'download-model',
      selectedModel.value.name,
      modelQuery.value.trim(),
    ) as { directoryName: string; fileCount: number }
    modelMessage.value = `已保存到 src/assets/${result.directoryName}，共 ${result.fileCount} 个文件`
  } catch (error) {
    modelError.value = error instanceof Error ? error.message : '下载模型失败'
  } finally {
    modelDownloading.value = false
  }
}

function handleOpenModelBrowser() {
  openModelBrowser()
}

function handleOpenCharacterBrowser() {
  openCharacterBrowser()
}

onMounted(() => {
  window.ipcRenderer?.on('open-model-browser', handleOpenModelBrowser)
  window.ipcRenderer?.on('open-character-browser', handleOpenCharacterBrowser)
  window.ipcRenderer?.on('walk-end', handleWalkEnd)
  scheduleWalk(walkFirstDelayMs)
})

onBeforeUnmount(() => {
  window.ipcRenderer?.off('open-model-browser', handleOpenModelBrowser)
  window.ipcRenderer?.off('open-character-browser', handleOpenCharacterBrowser)
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
          :pointer-interaction-enabled="!modelBrowserOpen && !characterBrowserOpen"
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

    <div v-if="modelBrowserOpen" class="model-browser" role="dialog" aria-label="获取模型">
      <div class="model-browser-panel">
        <header
          class="model-browser-header"
          title="按住拖动可移动窗口"
          @pointerdown="startWindowDrag"
          @pointermove="moveWindow"
          @pointerup="endWindowDrag"
          @pointercancel="endWindowDrag"
        >
          <div>
            <p class="model-browser-kicker">ARK MODELS</p>
            <h1>获取桌宠模型</h1>
          </div>
          <button
            class="icon-button"
            type="button"
            aria-label="关闭模型获取窗口"
            @pointerdown.stop
            @click="closeModelBrowser"
          >×</button>
        </header>

        <form class="model-search" @submit.prevent="searchModels">
          <input v-model="modelQuery" type="search" placeholder="输入文件夹名称，例如 amiya" aria-label="模型文件夹名称" />
          <button type="submit" :disabled="modelLoading">{{ modelLoading ? '查询中' : '查询' }}</button>
        </form>

        <p v-if="modelError" class="model-message-error">{{ modelError }}</p>
        <p v-else-if="modelMessage" class="model-message-success">{{ modelMessage }}</p>

        <div v-if="modelResults.length" class="model-results">
          <button
            v-for="modelOption in modelResults"
            :key="modelOption.name"
            class="model-option"
            :class="{ selected: selectedModel?.name === modelOption.name }"
            type="button"
            @click="selectedModel = modelOption"
          >
            <span class="model-option-name">{{ modelOption.name }}</span>
            <span class="model-option-meta">{{ modelOption.files.length }} 个文件</span>
          </button>
        </div>

        <div v-if="selectedModel" class="model-selection">
          <div>
            <p class="model-selection-label">已选择</p>
            <strong>{{ selectedModel.name }}</strong>
          </div>
          <button type="button" :disabled="modelDownloading" @click="downloadSelectedModel">
            {{ modelDownloading ? '下载中' : '下载到 assets' }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="characterBrowserOpen" class="model-browser" role="dialog" aria-label="更换人物">
      <div class="model-browser-panel">
        <header
          class="model-browser-header"
          title="按住拖动可移动窗口"
          @pointerdown="startWindowDrag"
          @pointermove="moveWindow"
          @pointerup="endWindowDrag"
          @pointercancel="endWindowDrag"
        >
          <div>
            <p class="model-browser-kicker">LOCAL MODELS</p>
            <h1>更换人物</h1>
          </div>
          <button
            class="icon-button"
            type="button"
            aria-label="关闭人物选择窗口"
            @pointerdown.stop
            @click="closeCharacterBrowser"
          >×</button>
        </header>

        <p v-if="!localModels.length" class="model-message-success">src/assets 中还没有可用的模型文件夹。</p>
        <div v-else class="model-results">
          <button
            v-for="model in localModels"
            :key="model.name"
            class="model-option"
            :class="{ selected: selectedCharacter?.name === model.name }"
            type="button"
            @click="selectedCharacter = model"
          >
            <span class="model-option-name">{{ model.name }}</span>
            <span class="model-option-meta">{{ selectedCharacter?.name === model.name ? '已选择' : '选择' }}</span>
          </button>
        </div>

        <div v-if="selectedCharacter" class="model-selection">
          <div>
            <p class="model-selection-label">确认更换为</p>
            <strong>{{ selectedCharacter.name }}</strong>
          </div>
          <button type="button" :disabled="characterLoading" @click="confirmCharacterChange">
            {{ characterLoading ? '修改中' : '确认更换' }}
          </button>
        </div>
      </div>
    </div>

  </main>
</template>
