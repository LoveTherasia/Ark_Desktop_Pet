<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as PIXI from 'pixi.js'
import { Spine } from 'pixi-spine'
import skeletonUrl from '../assets/amiya/build_char_002_amiya.skel?url'
import atlasUrl from '../assets/amiya/build_char_002_amiya.atlas?url'
import textureUrl from '../assets/amiya/build_char_002_amiya.png?url'

const canvasHost = ref<HTMLDivElement | null>(null)
const props = defineProps<{ interactionKey: number }>()
const loading = ref(true)
const errorMessage = ref('')

let app: PIXI.Application | null = null
let model: Spine | null = null
let tickerUpdate: (() => void) | null = null
let mouseEventsIgnored = true

const defaultAnimationNames = ['Sit', 'Relax', 'Move', 'Default', 'Sleep']
const modelScaleFactor = 0.3
const modelOffsetY = 50

function getAnimationName(preferredNames: string[]) {
  const availableNames = model?.spineData.animations.map((animation) => animation.name) ?? []
  return preferredNames.find((name) => availableNames.includes(name)) ?? availableNames[0]
}

function playInteraction() {
  if (!model) return

  const interactionName = getAnimationName(['Interact', 'Move', 'Relax'])
  const defaultName = getAnimationName(defaultAnimationNames)
  if (!interactionName) return

  model.state.setAnimation(0, interactionName, false)
  if (defaultName) {
    model.state.addAnimation(0, defaultName, true, 0)
  }
}

function fitModel() {
  if (!app || !model) return

  const bounds = new PIXI.Rectangle()
  model.getLocalBounds(bounds)
  const availableWidth = app.screen.width * 0.82
  const availableHeight = app.screen.height * 0.9
  const scale = Math.min(availableWidth / bounds.width, availableHeight / bounds.height) * modelScaleFactor

  model.scale.set(scale)
  model.x = app.screen.width / 2 - (bounds.x + bounds.width / 2) * scale
  model.y = app.screen.height / 2 - (bounds.y + bounds.height / 2) * scale + modelOffsetY
}

function syncMouseEvents(event: MouseEvent) {
  if (!app || !model || event.buttons !== 0) return

  const canvasBounds = app.view.getBoundingClientRect()
  const scaleX = app.screen.width / canvasBounds.width
  const scaleY = app.screen.height / canvasBounds.height
  const point = new PIXI.Point(
    (event.clientX - canvasBounds.left) * scaleX,
    (event.clientY - canvasBounds.top) * scaleY,
  )
  const overModel = model.getBounds().contains(point.x, point.y)
  const shouldIgnore = !overModel

  if (shouldIgnore !== mouseEventsIgnored) {
    mouseEventsIgnored = shouldIgnore
    window.ipcRenderer?.send('set-ignore-mouse-events', shouldIgnore)
  }
}

onMounted(() => {
  if (!canvasHost.value) return

  window.addEventListener('mousemove', syncMouseEvents)

  app = new PIXI.Application({
    width: 280,
    height: 240,
    antialias: true,
    transparent: true,
    resolution: window.devicePixelRatio || 1,
    autoDensity: true,
  })
  canvasHost.value.appendChild(app.view as HTMLCanvasElement)

  const loader = new PIXI.Loader()
  loader.add('amiya', skeletonUrl, {
    metadata: {
      spineAtlasFile: atlasUrl,
      image: PIXI.BaseTexture.from(textureUrl),
    },
  })
  loader.load((_loader, resources) => {
    const resource = resources.amiya
    if (resource?.error || !resource?.spineData || !app) {
      errorMessage.value = '阿米娅模型加载失败'
      loading.value = false
      return
    }

    model = new Spine(resource.spineData)
    model.autoUpdate = false
    app.stage.addChild(model)
    tickerUpdate = () => {
      model?.update(app?.ticker.deltaMS ? app.ticker.deltaMS / 1000 : 0)
    }
    app.ticker.add(tickerUpdate)
    const animationName = getAnimationName(defaultAnimationNames)
    if (animationName) {
      model.state.setAnimation(0, animationName, true)
    }
    fitModel()
    loading.value = false
  })
})

watch(() => props.interactionKey, playInteraction)

onBeforeUnmount(() => {
  if (app && tickerUpdate) {
    app.ticker.remove(tickerUpdate)
  }
  model?.destroy({ children: true })
  app?.destroy(true, { children: true, texture: false, baseTexture: false })
  model = null
  app = null
  tickerUpdate = null
  window.removeEventListener('mousemove', syncMouseEvents)
  mouseEventsIgnored = true
})
</script>

<template>
  <div ref="canvasHost" class="amiya-model" aria-label="阿米娅模型">
    <span v-if="loading" class="model-message">模型加载中...</span>
    <span v-else-if="errorMessage" class="model-message model-error">{{ errorMessage }}</span>
  </div>
</template>

<style scoped>
.amiya-model {
  position: relative;
  width: 100%;
  height: 240px;
}

.amiya-model :deep(canvas) {
  display: block;
  width: 100%;
  height: 100%;
}

.model-message {
  position: absolute;
  inset: 50% auto auto 50%;
  color: #647060;
  font-family: 'Trebuchet MS', sans-serif;
  font-size: 12px;
  transform: translate(-50%, -50%);
  white-space: nowrap;
}

.model-error {
  color: #a05248;
}
</style>
