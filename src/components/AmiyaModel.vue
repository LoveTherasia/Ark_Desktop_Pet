<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as PIXI from 'pixi.js'
import { Spine } from 'pixi-spine'
import skeletonUrl from '../assets/艾雅法拉/三丽鸥家族_II/build_char_180_amgoat_sanrio_2.skel?url'
import atlasUrl from '../assets/艾雅法拉/三丽鸥家族_II/build_char_180_amgoat_sanrio_2.atlas?url'
import textureUrl from '../assets/艾雅法拉/三丽鸥家族_II/build_char_180_amgoat_sanrio_2.png?url'

const canvasHost = ref<HTMLDivElement | null>(null)
const props = withDefaults(defineProps<{
  interactionKey: number
  pointerInteractionEnabled?: boolean
  walking?: boolean
  relaxing?: boolean
  facingLeft?: boolean
  /** 画布尺寸，设置页的预览会传入更小的值；默认与桌宠一致。 */
  canvasWidth?: number
  canvasHeight?: number
}>(), {
  canvasWidth: 280,
  canvasHeight: 240,
})
const loading = ref(true)
const errorMessage = ref('')

let app: PIXI.Application | null = null
let model: Spine | null = null
let tickerUpdate: (() => void) | null = null
/** 与主进程的窗口状态保持一致：默认“可交互”，只有确认指针离开人物才切穿透 */
let mouseEventsIgnored = false
let pendingRefit = false
let baseScale = 1
let fitBounds: PIXI.Rectangle | null = null
let playingAnimation = ''

const defaultAnimationNames = ['Sit', 'Relax', 'Move', 'Default', 'Sleep']
/** 走动时循环播放的动画；Move 的默认朝向是向右，向左走时用水平翻转。 */
const walkAnimationName = 'Move'

/** 模型在可用区域内的占比，数值越大人物越大（1 表示撑满可用区域）。 */
const modelFillRatio = 0.8
/** 垂直微调像素，0 表示在舞台内居中。 */
const modelOffsetY = 0
/**
 * 个别角色骨骼单位与其他角色差异较大时，可按 assets 下的「角色/皮肤」路径单独调整倍率。
 * 键名即下面三行资源路径里的「角色/皮肤」，例如 { '斯卡蒂/珊瑚海岸_III': 1.15 }。
 */
const modelScaleOverrides: Record<string, number> = {}

function currentModelKey() {
  const matched = skeletonUrl.match(/assets\/(.+)\/[^/]+$/)
  if (!matched) return ''

  try {
    return decodeURIComponent(matched[1])
  } catch {
    return matched[1]
  }
}

function currentModelScale() {
  return modelScaleOverrides[currentModelKey()] ?? 1
}

function getAnimationName(preferredNames: string[]) {
  const availableNames = model?.spineData.animations.map((animation) => animation.name) ?? []
  return preferredNames.find((name) => availableNames.includes(name)) ?? availableNames[0]
}

function hasAnimation(name: string) {
  return model?.spineData.animations.some((animation) => animation.name === name) ?? false
}

function setAnimation(name: string, loop: boolean) {
  if (!model) return

  model.state.setAnimation(0, name, loop)
  playingAnimation = name
}

function playIdleAnimation() {
  const animationName = getAnimationName(defaultAnimationNames)
  if (animationName) setAnimation(animationName, true)
}

function playWalkAnimation() {
  if (!hasAnimation(walkAnimationName)) {
    playIdleAnimation()
    return
  }

  setAnimation(walkAnimationName, true)
}

function playRelaxAnimation() {
  if (!hasAnimation('Relax')) {
    playIdleAnimation()
    return
  }

  setAnimation('Relax', true)
}

function applyAnimationState() {
  if (props.walking) playWalkAnimation()
  else if (props.relaxing) playRelaxAnimation()
  else playIdleAnimation()
}

function playInteraction() {
  if (!model) return

  const interactionName = getAnimationName(['Interact', 'Move', 'Relax'])
  const defaultName = getAnimationName(defaultAnimationNames)
  if (!interactionName) return

  setAnimation(interactionName, false)
  const followUpName = props.walking && hasAnimation(walkAnimationName) ? walkAnimationName : defaultName
  if (followUpName) {
    model.state.addAnimation(0, followUpName, true, 0)
  }
}

/**
 * 水平翻转用负的 scale.x 实现；定位公式同样使用带符号的缩放，
 * 这样镜像之后人物中心依旧落在画布中心，不会跑偏。
 */
function applyModelTransform() {
  if (!app || !model || !fitBounds) return

  const signedScale = props.facingLeft ? -baseScale : baseScale
  model.scale.set(signedScale, baseScale)
  model.x = app.screen.width / 2 - (fitBounds.x + fitBounds.width / 2) * signedScale
  model.y = app.screen.height / 2 - (fitBounds.y + fitBounds.height / 2) * baseScale + modelOffsetY
}

function fitModel() {
  if (!app || !model) return
  // 先把当前动画姿态、骨骼矩阵和显示对象变换刷新一遍再测量边界。
  // 否则量到的是骨架尚未更新时的残留边界，不同模型偏差方向不同，
  // 会算出完全错误的缩放比例（例如斯卡蒂被缩到只剩几十像素）。
  model.update(0)
  model.scale.set(1)
  model.position.set(0, 0)
  model.updateTransform()

  const bounds = new PIXI.Rectangle()
  model.getLocalBounds(bounds)
  if (
    !Number.isFinite(bounds.width) || !Number.isFinite(bounds.height)
    || bounds.width <= 0 || bounds.height <= 0
  ) {
    return
  }

  const availableWidth = app.screen.width * 0.82
  const availableHeight = app.screen.height * 0.9
  const fitScale = Math.min(availableWidth / bounds.width, availableHeight / bounds.height)
  baseScale = fitScale * modelFillRatio * currentModelScale()
  fitBounds = bounds

  applyModelTransform()
}

/** 模型每次加载完成后打印一次边界，便于排查命中检测/显示异常。 */
function logModelReady() {
  if (!model || !app) return

  const bounds = model.getBounds()
  console.log(
    `[ArkPet] 模型就绪 bounds=${bounds.x.toFixed(0)},${bounds.y.toFixed(0)},${bounds.width.toFixed(0)}x${bounds.height.toFixed(0)}`,
    `canvas=${app.screen.width}x${app.screen.height}`,
    `dpr=${window.devicePixelRatio}`,
    `ipcRenderer=${window.ipcRenderer ? '可用' : '不可用!'}`,
  )
}

function syncMouseEvents(event: MouseEvent) {  if (props.pointerInteractionEnabled === false || !app || event.buttons !== 0) return

  const canvasBounds = app.view.getBoundingClientRect()
  const scaleX = app.screen.width / canvasBounds.width
  const scaleY = app.screen.height / canvasBounds.height
  const point = new PIXI.Point(
    (event.clientX - canvasBounds.left) * scaleX,
    (event.clientY - canvasBounds.top) * scaleY,
  )

  // 判定“指针是否落在人物身上”。这里必须保证：只要指针确实在人物可见范围内，
  // 窗口就一定可交互，否则用户会既拖不动也右键不了。
  // 1) 模型已加载且边界有效时，用模型包围盒（放宽 8px，避免边缘抖动）；
  // 2) 否则退化为整块画布——画布范围基本就是人物所在区域，宁可多挡一点也不能失去操作入口。
  const modelBounds = model && model.visible ? model.getBounds() : null
  const modelBoundsReady = !!modelBounds && modelBounds.width > 1 && modelBounds.height > 1
  const hitPadding = 8

  const overModel = modelBoundsReady
    ? (
      point.x >= modelBounds!.x - hitPadding
      && point.x <= modelBounds!.x + modelBounds!.width + hitPadding
      && point.y >= modelBounds!.y - hitPadding
      && point.y <= modelBounds!.y + modelBounds!.height + hitPadding
    )
    : (point.x >= -hitPadding && point.x <= app.screen.width + hitPadding
      && point.y >= -hitPadding && point.y <= app.screen.height + hitPadding)

  const shouldIgnore = !overModel

  if (shouldIgnore !== mouseEventsIgnored) {
    mouseEventsIgnored = shouldIgnore
    window.ipcRenderer?.send('set-ignore-mouse-events', shouldIgnore)
  }
}

function loadModel() {
  if (!app) return

  model?.parent?.removeChild(model)
  model?.destroy({ children: true })
  model = null
  loading.value = true
  errorMessage.value = ''
  pendingRefit = false
  fitBounds = null
  playingAnimation = ''

  const loader = new PIXI.Loader()
  loader.add('model', skeletonUrl, {
    metadata: {
      spineAtlasFile: atlasUrl,
      image: PIXI.BaseTexture.from(textureUrl),
    },
  })
  loader.load((_loader, resources) => {
    const resource = resources.model
    if (resource?.error || !resource?.spineData || !app) {
      errorMessage.value = '人物模型加载失败，请确认文件完整'
      loading.value = false
      return
    }

    model = new Spine(resource.spineData)
    model.autoUpdate = false
    app.stage.addChild(model)
    fitModel()
    applyAnimationState()
    // 下一帧动画姿态真实推进后再校准一次，避免用第 0 帧姿态定死缩放。
    pendingRefit = true
    loading.value = false
  })
}

onMounted(() => {
  if (!canvasHost.value) return

  window.addEventListener('mousemove', syncMouseEvents)

  app = new PIXI.Application({
    width: props.canvasWidth,
    height: props.canvasHeight,
    antialias: true,
    transparent: true,
    resolution: window.devicePixelRatio || 1,
    autoDensity: true,
  })
  canvasHost.value.appendChild(app.view as HTMLCanvasElement)

  tickerUpdate = () => {
    if (!model) return

    model.update(app?.ticker.deltaMS ? app.ticker.deltaMS / 1000 : 0)
    if (pendingRefit) {
      pendingRefit = false
      fitModel()
      logModelReady()
    }
  }
  app.ticker.add(tickerUpdate)
  loadModel()
})

watch(() => props.interactionKey, playInteraction)

watch(() => props.walking, (isWalking) => {
  if (isWalking) {
    playWalkAnimation()
    return
  }

  if (props.relaxing) {
    playRelaxAnimation()
    return
  }

  // 走动结束时回到待机；若此刻正在播交互动作则不要抢占轨道
  if (playingAnimation === walkAnimationName) playIdleAnimation()
})

watch(() => props.relaxing, (isRelaxing) => {
  if (isRelaxing && !props.walking) {
    playRelaxAnimation()
    return
  }

  if (!props.walking && playingAnimation === 'Relax') playIdleAnimation()
})

watch(() => props.facingLeft, applyModelTransform)

onBeforeUnmount(() => {
  if (app && tickerUpdate) {
    app.ticker.remove(tickerUpdate)
  }
  model?.destroy({ children: true })
  app?.destroy(true, { children: true, texture: false, baseTexture: false })
  model = null
  app = null
  tickerUpdate = null
  pendingRefit = false
  window.removeEventListener('mousemove', syncMouseEvents)
  mouseEventsIgnored = false
})
</script>

<template>
  <div ref="canvasHost" class="amiya-model" :style="{ height: `${props.canvasHeight}px` }" aria-label="桌宠模型">
    <span v-if="loading" class="model-message">模型加载中...</span>
    <span v-else-if="errorMessage" class="model-message model-error">{{ errorMessage }}</span>
  </div>
</template>

<style scoped>
.amiya-model {
  position: relative;
  width: 100%;
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
