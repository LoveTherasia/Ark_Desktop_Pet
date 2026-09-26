<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import AmiyaModel from './AmiyaModel.vue'

interface ModelInfo {
  key: string
  assetId: string
  name: string
  appellation: string
  type: string
  style: string
  skinGroupId: string
  skinGroupName: string
  sortTags: string[]
}

interface LocalModel {
  /** 相对 src/assets 的路径：<角色>/<皮肤> */
  name: string
  character: string
  skin: string
  skeleton: string
  atlas: string
  texture: string
  info: ModelInfo | null
}

defineProps<{
  /** 窗口拖动沿用 App.vue 的实现，与桌宠拖动共用同一套 IPC 与指针捕获逻辑。 */
  startDrag: (event: PointerEvent) => void
  moveDrag: (event: PointerEvent) => void
  endDrag: (event: PointerEvent) => void
}>()

const emit = defineEmits<{ close: [] }>()

const settingsTab = ref<'character' | 'skin' | 'preference'>('character')
const models = ref<LocalModel[]>([])
const currentModel = ref<LocalModel | null>(null)
const modelsLoading = ref(false)
const errorMessage = ref('')
const characterQuery = ref('')
const selectedCharacter = ref('')
const selectedSkin = ref('')
const switching = ref(false)

// ---- 偏好设置 ----
const activityBubbleEnabled = ref(true)
const preferenceSaving = ref(false)

async function loadSettings() {
  try {
    const settings = await window.ipcRenderer.invoke('settings-get') as { activityBubbleEnabled?: boolean }
    activityBubbleEnabled.value = settings?.activityBubbleEnabled !== false
  } catch {
    activityBubbleEnabled.value = true
  }
}

async function toggleActivityBubble() {
  if (preferenceSaving.value) return

  preferenceSaving.value = true
  const next = !activityBubbleEnabled.value
  activityBubbleEnabled.value = next
  try {
    await window.ipcRenderer.invoke('settings-set', { activityBubbleEnabled: next })
  } catch (error) {
    activityBubbleEnabled.value = !next
    errorMessage.value = error instanceof Error ? error.message : '保存设置失败'
  } finally {
    preferenceSaving.value = false
  }
}

const currentInfo = computed(() => currentModel.value?.info ?? null)

/** 角色列表（去重，沿用本地模型的拼音序），附带皮肤数量。 */
const characterList = computed(() => {
  const counts = new Map<string, number>()
  for (const model of models.value) {
    counts.set(model.character, (counts.get(model.character) ?? 0) + 1)
  }

  const query = characterQuery.value.trim().toLowerCase()
  return [...counts.entries()]
    .filter(([name]) => !query || name.toLowerCase().includes(query))
    .map(([name, skinCount]) => ({ name, skinCount }))
})

/** 当前角色的全部皮肤。 */
const skinList = computed(() => {
  const character = currentModel.value?.character ?? ''
  return models.value.filter((model) => model.character === character)
})

const typeLabel = computed(() => {
  const type = currentInfo.value?.type
  if (type === 'Operator') return '干员'
  if (type === 'Enemy') return '敌方单位'
  if (type === 'DynIllust') return '动态立绘'
  return type || '—'
})

const styleLabel = computed(() => {
  const style = currentInfo.value?.style
  if (style === 'BuildingDefault') return '基建默认'
  if (style === 'BuildingSkin') return '基建时装'
  return style || '—'
})

const rarityLabel = computed(() => {
  const tag = currentInfo.value?.sortTags.find((item) => item.startsWith('Rarity_'))
  return tag ? `${tag.replace('Rarity_', '')} 星` : '—'
})

async function refreshModels() {
  modelsLoading.value = true
  try {
    models.value = await window.ipcRenderer.invoke('list-local-models') as LocalModel[]
    currentModel.value = await window.ipcRenderer.invoke('current-model') as LocalModel | null
  } catch {
    models.value = []
    currentModel.value = null
  } finally {
    modelsLoading.value = false
  }
}

/** 两个分区共用的切换流程：主进程改写资源路径后会重新加载整个窗口。 */
async function applyModel(name: string, failureText: string) {
  if (!name || switching.value) return

  switching.value = true
  errorMessage.value = ''
  try {
    await window.ipcRenderer.invoke('activate-local-model', name)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : failureText
    switching.value = false
  }
}

function confirmCharacterChange() {
  const character = selectedCharacter.value
  if (!character) return

  // 换角色时默认回到该角色的默认皮肤
  const target = models.value.find((model) => model.character === character && model.skin.startsWith('默认服装'))
    ?? models.value.find((model) => model.character === character)
  if (target) void applyModel(target.name, '更换人物失败')
}

function confirmSkinChange() {
  const target = skinList.value.find((model) => model.skin === selectedSkin.value)
  if (target) void applyModel(target.name, '更换皮肤失败')
}

onMounted(() => {
  // 打开设置页时窗口需要接收鼠标事件并放大；关闭时在 onBeforeUnmount 里还原
  window.ipcRenderer?.send('set-ignore-mouse-events', false)
  window.ipcRenderer?.send('resize-window', 620, 620)
  void refreshModels()
  void loadSettings()
})

onBeforeUnmount(() => {
  window.ipcRenderer?.send('set-ignore-mouse-events', true)
  window.ipcRenderer?.send('resize-window', 320, 380)
})
</script>

<template>
  <div class="model-browser" role="dialog" aria-label="设置">
    <div class="model-browser-panel">
      <header
        class="model-browser-header"
        title="按住拖动可移动窗口"
        @pointerdown="startDrag"
        @pointermove="moveDrag"
        @pointerup="endDrag"
        @pointercancel="endDrag"
      >
        <div>
          <p class="model-browser-kicker">SETTINGS</p>
          <h1>设置</h1>
        </div>
        <button
          class="icon-button"
          type="button"
          aria-label="关闭设置"
          @pointerdown.stop
          @click="emit('close')"
        >×</button>
      </header>

      <section class="settings-summary">
        <div class="settings-preview">
          <div class="settings-preview-canvas">
            <AmiyaModel
              :interaction-key="0"
              :pointer-interaction-enabled="false"
              :walking="false"
              :facing-left="false"
              :canvas-width="168"
              :canvas-height="144"
            />
          </div>
          <p class="settings-preview-label">当前模型预览</p>
        </div>

        <div class="settings-info">
          <p class="model-browser-kicker">当前人物</p>
          <h2 class="settings-name">{{ currentModel?.character ?? (modelsLoading ? '读取中…' : '未知') }}</h2>
          <p class="settings-appellation">{{ currentInfo?.appellation || '—' }}</p>

          <dl class="settings-fields">
            <div><dt>皮肤</dt><dd>{{ currentModel?.skin ?? '—' }}</dd></div>
            <div><dt>皮肤组</dt><dd>{{ currentInfo?.skinGroupId || '—' }}</dd></div>
            <div><dt>类型</dt><dd>{{ typeLabel }}</dd></div>
            <div><dt>样式</dt><dd>{{ styleLabel }}</dd></div>
            <div><dt>稀有度</dt><dd>{{ rarityLabel }}</dd></div>
            <div><dt>资源</dt><dd class="settings-resource">{{ currentModel?.skeleton ?? '—' }}</dd></div>
          </dl>
        </div>
      </section>

      <nav class="settings-tabs" aria-label="设置分区">
        <button
          type="button"
          :class="{ active: settingsTab === 'character' }"
          @click="settingsTab = 'character'"
        >更换人物</button>
        <button
          type="button"
          :class="{ active: settingsTab === 'skin' }"
          @click="settingsTab = 'skin'"
        >更换皮肤</button>
        <button
          type="button"
          :class="{ active: settingsTab === 'preference' }"
          @click="settingsTab = 'preference'"
        >偏好</button>
      </nav>

      <p v-if="errorMessage" class="model-message-error">{{ errorMessage }}</p>

      <template v-if="settingsTab === 'preference'">
        <div class="settings-preference">
          <div class="settings-preference-text">
            <p class="settings-preference-title">桌面活动气泡</p>
            <p class="settings-preference-desc">
              切换应用时在人物旁提示当前应用类别。关闭后不再检测前台应用，也不会再弹出气泡。
            </p>
          </div>
          <button
            class="settings-switch"
            :class="{ on: activityBubbleEnabled }"
            type="button"
            role="switch"
            :aria-checked="activityBubbleEnabled"
            :aria-label="activityBubbleEnabled ? '关闭桌面活动气泡' : '开启桌面活动气泡'"
            :disabled="preferenceSaving"
            @click="toggleActivityBubble"
          >
            <span class="settings-switch-thumb" aria-hidden="true"></span>
          </button>
        </div>
        <p class="settings-preference-state">
          当前状态：<strong>{{ activityBubbleEnabled ? '已开启' : '已关闭' }}</strong>
        </p>
      </template>

      <template v-else-if="settingsTab === 'character'">
        <form class="model-search" @submit.prevent>
          <input
            v-model="characterQuery"
            type="search"
            placeholder="按角色名筛选，例如 阿米娅"
            aria-label="筛选角色"
          />
        </form>

        <p v-if="!models.length" class="model-message-success">src/assets 中还没有可用的模型文件夹。</p>
        <p v-else-if="!characterList.length" class="model-message-success">没有匹配的角色。</p>
        <div v-else class="model-results">
          <button
            v-for="character in characterList"
            :key="character.name"
            class="model-option"
            :class="{ selected: selectedCharacter === character.name }"
            type="button"
            @click="selectedCharacter = character.name"
          >
            <span class="model-option-name">
              {{ character.name }}
              <span v-if="character.name === currentModel?.character" class="settings-badge">当前</span>
            </span>
            <span class="model-option-meta">{{ character.skinCount }} 套皮肤</span>
          </button>
        </div>

        <div v-if="selectedCharacter" class="model-selection">
          <div>
            <p class="model-selection-label">确认更换为</p>
            <strong>{{ selectedCharacter }} / 默认服装</strong>
          </div>
          <button type="button" :disabled="switching" @click="confirmCharacterChange">
            {{ switching ? '修改中' : '确认更换' }}
          </button>
        </div>
      </template>

      <template v-else>
        <p class="settings-hint">
          当前角色：<strong>{{ currentModel?.character ?? '未知' }}</strong>，共 {{ skinList.length }} 套皮肤
        </p>

        <div class="model-results">
          <button
            v-for="model in skinList"
            :key="model.name"
            class="model-option"
            :class="{ selected: selectedSkin === model.skin }"
            type="button"
            @click="selectedSkin = model.skin"
          >
            <span class="model-option-name">
              {{ model.skin }}
              <span v-if="model.skin === currentModel?.skin" class="settings-badge">当前</span>
            </span>
            <span class="model-option-meta">{{ model.info?.skinGroupId || '—' }}</span>
          </button>
        </div>

        <div v-if="selectedSkin" class="model-selection">
          <div>
            <p class="model-selection-label">确认更换为</p>
            <strong>{{ currentModel?.character }} / {{ selectedSkin }}</strong>
          </div>
          <button type="button" :disabled="switching" @click="confirmSkinChange">
            {{ switching ? '修改中' : '确认更换' }}
          </button>
        </div>
      </template>
    </div>
  </div>
</template>
