import { app, BrowserWindow, Menu, ipcMain, powerMonitor, screen } from 'electron'
import { access, mkdir, readFile, readdir, rename, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { startActivityWatcher, type ActivityWatcher } from './activity'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// The built directory structure
//
// ├─┬─┬ dist
// │ │ └── index.html
// │ │
// │ ├─┬ dist-electron
// │ │ ├── main.js
// │ │ └── preload.mjs
// │
process.env.APP_ROOT = path.join(__dirname, '..')

// 🚧 Use ['ENV_NAME'] avoid vite:define plugin - Vite@2.x
export const VITE_DEV_SERVER_URL = process.env['VITE_DEV_SERVER_URL']
export const MAIN_DIST = path.join(process.env.APP_ROOT, 'dist-electron')
export const RENDERER_DIST = path.join(process.env.APP_ROOT, 'dist')

process.env.VITE_PUBLIC = VITE_DEV_SERVER_URL ? path.join(process.env.APP_ROOT, 'public') : RENDERER_DIST

let win: BrowserWindow | null
const windowDragOrigins = new Map<number, { pointerX: number; pointerY: number; windowX: number; windowY: number }>()

/** models_data.json 中的角色/皮肤元数据，用于设置页展示与分组。 */
type ModelInfo = {
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
type LocalModel = {
  /** 相对 src/assets 的路径：<角色>/<皮肤> */
  name: string
  character: string
  skin: string
  skeleton: string
  atlas: string
  texture: string
  info: ModelInfo | null
}

// ---- 桌宠右侧的聊天气泡：显示时把窗口横向加宽，桌宠本身在屏幕上不动 ----
let bubbleSide: 'right' | 'left' | null = null
let bubblePointerSyncTimer: ReturnType<typeof setInterval> | null = null
let activityWatcher: ActivityWatcher | null = null

// ---- 用户偏好：持久化到 userData/settings.json ----
type AppSettings = {
  /** 桌面活动气泡（含前台应用检测）开关 */
  activityBubbleEnabled: boolean
}

const defaultSettings: AppSettings = { activityBubbleEnabled: true }
let appSettings: AppSettings = { ...defaultSettings }

function settingsFilePath() {
  return path.join(app.getPath('userData'), 'settings.json')
}

async function loadSettings() {
  const raw = await readFile(settingsFilePath(), 'utf8').catch(() => '')
  if (!raw) return

  try {
    const parsed = JSON.parse(raw) as Partial<AppSettings>
    appSettings = { ...defaultSettings, ...parsed }
  } catch {
    // 配置损坏时回退默认值，不影响启动
    appSettings = { ...defaultSettings }
  }
}

async function saveSettings() {
  try {
    await mkdir(path.dirname(settingsFilePath()), { recursive: true })
    await writeFile(settingsFilePath(), JSON.stringify(appSettings, null, 2), 'utf8')
  } catch {
    // 写入失败只影响持久化，本次会话仍按内存中的设置运行
  }
}

function notifySettings() {
  for (const target of BrowserWindow.getAllWindows()) {
    if (!target.isDestroyed()) target.webContents.send('settings-changed', appSettings)
  }
}

/** 关闭该功能时直接结束检测用的 PowerShell 进程，而不是只隐藏气泡。 */
function applyActivitySetting() {
  if (appSettings.activityBubbleEnabled) {
    startActivityDetection()
  } else {
    activityWatcher?.stop()
    activityWatcher = null
  }
}

function startActivityDetection() {
  if (activityWatcher) return

  activityWatcher = startActivityWatcher(
    [path.basename(process.execPath, '.exe'), 'ark-desktop-pet'],
    (snapshot) => {
      // 前台应用变化时推一条气泡；后续功能可以复用同一个 pushBubble 入口
      pushBubble({
        source: 'activity',
        icon: snapshot.icon,
        title: snapshot.label,
        detail: snapshot.processName,
      })
    },
    () => powerMonitor.getSystemIdleTime(),
  )
}

type BubblePayload = {
  source: string
  icon: string
  title: string
  detail?: string
}

/** 通用气泡推送入口：活动检测只是第一个生产者，后续功能可直接复用。 */
function pushBubble(payload: BubblePayload) {
  if (!win || win.isDestroyed()) return
  win.webContents.send('bubble-show', payload)
}

/** 渲染层页面：设置页是独立窗口，走自己的 HTML 入口。 */
function loadRendererPage(targetWindow: BrowserWindow, page: 'index.html' | 'settings.html') {
  if (VITE_DEV_SERVER_URL) {
    // dev server 地址末尾自带斜杠，去掉后拼接页面路径
    targetWindow.loadURL(`${VITE_DEV_SERVER_URL.replace(/\/$/, '')}/${page}`)
  } else {
    targetWindow.loadFile(path.join(RENDERER_DIST, page))
  }
}

// ---- 设置窗口：与桌宠窗口相互独立，拖动它不会带动桌宠 ----
let settingsWindow: BrowserWindow | null = null

function openSettingsWindow() {
  if (settingsWindow && !settingsWindow.isDestroyed()) {
    settingsWindow.show()
    settingsWindow.focus()
    // 让已打开的设置窗口重新读取当前模型与偏好
    settingsWindow.webContents.send('settings-refresh')
    return
  }

  const settingsWidth = 620
  // 高度给足，让“确认更换”按钮与列表同屏可见，不需要滚动；屏幕不够高时按工作区收窄
  const maxSettingsHeight = 780
  const gap = 16

  const petBounds = win && !win.isDestroyed() ? win.getBounds() : null
  const display = petBounds ? screen.getDisplayMatching(petBounds) : screen.getPrimaryDisplay()
  const area = display.workArea
  const settingsHeight = Math.max(480, Math.min(maxSettingsHeight, area.height - 40))

  // 默认贴着桌宠右侧打开，右侧放不下就放左侧，再不行才居中：
  // 避免设置窗口正好压在桌宠上（两者都被系统居中创建时几乎完全重叠）
  let x = petBounds
    ? petBounds.x + petBounds.width + gap
    : area.x + Math.round((area.width - settingsWidth) / 2)
  if (petBounds && x + settingsWidth > area.x + area.width) {
    x = petBounds.x - settingsWidth - gap
  }
  x = Math.max(area.x, Math.min(x, area.x + area.width - settingsWidth))

  // 竖直方向在工作区内居中
  let y = area.y + Math.round((area.height - settingsHeight) / 2)
  y = Math.max(area.y, Math.min(y, area.y + area.height - settingsHeight))

  settingsWindow = new BrowserWindow({
    width: settingsWidth,
    height: settingsHeight,
    x,
    y,
    // 透明无边框窗口在 Windows 上有时仍会被系统当作可调整大小：
    // 把 min/max 锁成同一尺寸，从根上杜绝拖动过程中右/下边框被拉动。
    minWidth: settingsWidth,
    maxWidth: settingsWidth,
    minHeight: settingsHeight,
    maxHeight: settingsHeight,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.mjs'),
    },
  })

  settingsWindow.once('ready-to-show', () => settingsWindow?.show())
  settingsWindow.on('closed', () => {
    settingsWindow = null
  })

  settingsWindow.webContents.on('did-finish-load', () => {
    console.log('[ArkPet] 设置窗口页面加载完成')
  })
  settingsWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    console.error('[ArkPet] 设置窗口页面加载失败:', errorCode, errorDescription, validatedURL)
  })
  settingsWindow.webContents.on('render-process-gone', (_event, details) => {
    console.error('[ArkPet] 设置窗口渲染进程退出:', details.reason)
  })
  settingsWindow.webContents.on('console-message', (_event, _level, message, line, sourceId) => {
    if (!message.includes('[ArkPet]')) return
    console.log(`[ArkPet 设置窗口] ${message} (${sourceId}:${line})`)
  })

  // 设置窗口的渲染页面本身就是面板（铺满整窗），不需要鼠标穿透逻辑；
  // 这样桌宠窗口仍是最轻量、也最容易出问题的那一个，避免两个窗口同时使用
  // ignoreMouseEvents 的 forward 转发而互相干扰。
  loadRendererPage(settingsWindow, 'settings.html')
}

function setBubbleWidth(visible: boolean): { side: 'right' | 'left' } {
  if (!win || win.isDestroyed()) return { side: bubbleSide ?? 'right' }

  const bounds = win.getBounds()

  if (!visible) {
    if (bubbleSide) {
      // 收回时把窗口还原到桌宠尺寸，桌宠在屏幕上的位置保持不变
      const restoredX = bubbleSide === 'left' ? bounds.x + bubbleWindowExtraWidth : bounds.x
      win.setBounds({ x: restoredX, y: bounds.y, width: petWindowWidth, height: bounds.height })
      bubbleSide = null
      stopBubblePointerSync()
      notifyPointerPosition()
    }
    return { side: 'right' }
  }

  if (bubbleSide) return { side: bubbleSide }

  const { workArea } = screen.getDisplayMatching(bounds)
  const rightRoom = workArea.x + workArea.width - (bounds.x + petWindowWidth)
  const leftRoom = bounds.x - workArea.x

  let side: 'right' | 'left' = 'right'
  let x = bounds.x

  if (rightRoom >= bubbleWindowExtraWidth) {
    side = 'right'
  } else if (leftRoom >= bubbleWindowExtraWidth) {
    // 右侧放不下就翻到左边，桌宠本身仍在原位置
    side = 'left'
    x = bounds.x - bubbleWindowExtraWidth
  } else {
    // 两侧都放不下（工作区比桌宠+气泡还窄）：优先保证桌宠不动，只把桌宠本身夹在工作区内，
    // 气泡允许溢出屏幕边缘
    side = 'right'
    x = Math.max(workArea.x, Math.min(bounds.x, workArea.x + workArea.width - petWindowWidth))
  }

  bubbleSide = side
  win.setBounds({ x, y: bounds.y, width: petWindowWidth + bubbleWindowExtraWidth, height: bounds.height })
  startBubblePointerSync()
  return { side }
}

/** 窗口尺寸或位置变化后，强制渲染层按最新窗口坐标重新判断鼠标穿透。 */
function notifyPointerPosition() {
  if (!win || win.isDestroyed()) return

  const cursor = screen.getCursorScreenPoint()
  const bounds = win.getBounds()
  win.webContents.send('bubble-pointer-position', {
    clientX: cursor.x - bounds.x,
    clientY: cursor.y - bounds.y,
  })
}

function startBubblePointerSync() {
  stopBubblePointerSync()
  notifyPointerPosition()
  // 气泡展开后窗口新增区域可能没有新的 mousemove，持续校准可避免透明区域卡住底层点击。
  bubblePointerSyncTimer = setInterval(notifyPointerPosition, 50)
}

function stopBubblePointerSync() {
  if (bubblePointerSyncTimer !== null) {
    clearInterval(bubblePointerSyncTimer)
    bubblePointerSyncTimer = null
  }
}

// ---- 自主走动：位移在主进程完成，边界一律以当前显示器的工作区为准 ----
const petWindowWidth = 320
const petWindowHeight = 380
/** 显示气泡时窗口横向加宽的像素数（气泡列宽 + 间距），需与 style.css 中的气泡尺寸保持一致。 */
const bubbleWindowExtraWidth = 172
// 走得慢、走得近：只在小范围里挪动，不打扰桌面上的其他工作
const walkSpeedPxPerSecond = 40
const walkTickMs = 16
const walkMinDistancePx = 24
const walkMaxDistancePx = 96

type WalkSession = {
  timer: ReturnType<typeof setInterval>
  fromX: number
  toX: number
  y: number
  startedAt: number
  durationMs: number
}
let walkSession: WalkSession | null = null

function endWalk(notify: boolean) {
  if (!walkSession) return

  clearInterval(walkSession.timer)
  walkSession = null
  if (notify && win && !win.isDestroyed()) {
    win.webContents.send('walk-end')
  }
}

/**
 * 在工作区内随机走一小段，返回朝向与时长给渲染层用于播放 Move 动画。
 * 距离会被左右剩余空间夹住，因此窗口不会走出屏幕。
 */
function beginWalk(): { direction: 'left' | 'right'; durationMs: number } | null {
  if (!win || win.isDestroyed() || walkSession) return null

  const bounds = win.getBounds()
  const { workArea } = screen.getDisplayMatching(bounds)
  const maxX = workArea.x + workArea.width - bounds.width
  if (maxX <= workArea.x) return null

  const leftRoom = bounds.x - workArea.x
  const rightRoom = maxX - bounds.x

  let direction: 'left' | 'right'
  if (leftRoom >= walkMinDistancePx && rightRoom >= walkMinDistancePx) {
    direction = Math.random() < 0.5 ? 'left' : 'right'
  } else {
    direction = rightRoom > leftRoom ? 'right' : 'left'
  }

  const room = direction === 'left' ? leftRoom : rightRoom
  const wanted = walkMinDistancePx + Math.random() * (walkMaxDistancePx - walkMinDistancePx)
  const distance = Math.round(Math.min(room, wanted))
  if (distance <= 0) return null

  const fromX = bounds.x
  const y = bounds.y
  const toX = Math.round(direction === 'left' ? fromX - distance : fromX + distance)
  const durationMs = Math.max(1, Math.round((distance / walkSpeedPxPerSecond) * 1000))
  const startedAt = Date.now()

  const timer = setInterval(() => {
    if (!win || win.isDestroyed()) {
      endWalk(false)
      return
    }

    const progress = Math.min(1, (Date.now() - startedAt) / durationMs)
    win.setPosition(Math.round(fromX + (toX - fromX) * progress), y)
    if (progress >= 1) endWalk(true)
  }, walkTickMs)

  walkSession = { timer, fromX, toX, y, startedAt, durationMs }
  return { direction, durationMs }
}

// 下载时对文件/目录名做过不安全字符替换，比对 models_data.json 时要套用同一规则
const unsafeModelNamePattern = /[\\/:*?"<>|#%\u0000-\u001f]/g
function sanitizeModelName(value: string) {
  return String(value ?? '').replace(unsafeModelNamePattern, '_').replace(/\s+/g, ' ').trim()
}
function stripExtension(value: string) {
  return value.replace(/\.[a-z0-9]+$/i, '')
}

let modelInfoIndex: Map<string, ModelInfo> | null = null

/** 以「骨架资源名（去扩展名，忽略大小写）」为键索引 models_data.json。 */
async function getModelInfoIndex(): Promise<Map<string, ModelInfo>> {
  if (modelInfoIndex) return modelInfoIndex

  const index = new Map<string, ModelInfo>()
  const indexPath = path.join(process.env.APP_ROOT, 'src', 'assets', 'models_data.json')
  const raw = await readFile(indexPath, 'utf8').catch(() => '')

  if (raw) {
    try {
      const parsed = JSON.parse(raw) as { data?: Record<string, Record<string, unknown>> }
      for (const [key, meta] of Object.entries(parsed.data ?? {})) {
        const info: ModelInfo = {
          key,
          assetId: String(meta.assetId ?? ''),
          name: String(meta.name ?? ''),
          appellation: String(meta.appellation ?? ''),
          type: String(meta.type ?? ''),
          style: String(meta.style ?? ''),
          skinGroupId: String(meta.skinGroupId ?? ''),
          skinGroupName: String(meta.skinGroupName ?? ''),
          sortTags: Array.isArray(meta.sortTags) ? meta.sortTags.map(String) : [],
        }
        for (const fileName of Object.values((meta.assetList ?? {}) as Record<string, string>)) {
          index.set(stripExtension(sanitizeModelName(fileName)).toLowerCase(), info)
        }
      }
    } catch {
      // 索引损坏时退化为不显示元数据，不影响模型切换
    }
  }

  modelInfoIndex = index
  return index
}

async function listLocalModels(): Promise<LocalModel[]> {
  const assetsDirectory = path.join(process.env.APP_ROOT, 'src', 'assets')
  const infoIndex = await getModelInfoIndex()
  const models: LocalModel[] = []

  const characterEntries = await readdir(assetsDirectory, { withFileTypes: true }).catch(() => [])
  for (const characterEntry of characterEntries) {
    if (!characterEntry.isDirectory()) continue

    const characterDirectory = path.join(assetsDirectory, characterEntry.name)
    const skinEntries = await readdir(characterDirectory, { withFileTypes: true }).catch(() => [])
    for (const skinEntry of skinEntries) {
      if (!skinEntry.isDirectory()) continue

      const files = await readdir(path.join(characterDirectory, skinEntry.name)).catch(() => [])
      const skeleton = files.find((file) => /\.(skel|json)$/i.test(file))
      const atlas = files.find((file) => file.toLowerCase().endsWith('.atlas'))
      const texture = files.find((file) => file.toLowerCase().endsWith('.png'))
      if (skeleton && atlas && texture) {
        models.push({
          name: `${characterEntry.name}/${skinEntry.name}`,
          character: characterEntry.name,
          skin: skinEntry.name,
          skeleton,
          atlas,
          texture,
          info: infoIndex.get(stripExtension(sanitizeModelName(skeleton)).toLowerCase()) ?? null,
        })
      }
    }
  }

  return models.sort((left, right) => left.name.localeCompare(right.name, 'zh-Hans-CN'))
}

/** 当前生效的模型由 AmiyaModel.vue 顶部三行导入决定，这里反解出 <角色>/<皮肤>。 */
async function readActiveModelName(): Promise<string> {
  const componentPath = path.join(process.env.APP_ROOT, 'src', 'components', 'AmiyaModel.vue')
  const source = await readFile(componentPath, 'utf8').catch(() => '')
  const matched = source.match(/import skeletonUrl from '\.\.\/assets\/(.+)\/[^/']+\?url'/)
  return matched ? matched[1] : ''
}

ipcMain.handle('list-local-models', () => listLocalModels())

ipcMain.handle('current-model', async () => {
  const activeName = await readActiveModelName()
  if (!activeName) return null

  const models = await listLocalModels()
  return models.find((model) => model.name === activeName) ?? null
})

ipcMain.handle('activate-local-model', async (event, modelName: string) => {
  const models = await listLocalModels()
  const selectedModel = models.find((model) => model.name === modelName)
  if (!selectedModel) throw new Error('找不到选中的本地模型')

  const normalizedModel = { ...selectedModel }
  const modelDirectory = path.join(process.env.APP_ROOT, 'src', 'assets', selectedModel.name)
  for (const key of ['skeleton', 'atlas', 'texture'] as const) {
    const originalName = normalizedModel[key]
    const safeName = originalName.replace(/[#?%]/g, '_')
    if (safeName === originalName) continue

    const originalPath = path.join(modelDirectory, originalName)
    const safePath = path.join(modelDirectory, safeName)
    try {
      await access(safePath)
      normalizedModel[key] = safeName
    } catch {
      await rename(originalPath, safePath)
      normalizedModel[key] = safeName
    }
  }

  const componentPath = path.join(process.env.APP_ROOT, 'src', 'components', 'AmiyaModel.vue')
  const source = await readFile(componentPath, 'utf8')
  const folderName = normalizedModel.name
  const importPaths = {
    skeletonUrl: `../assets/${folderName}/${normalizedModel.skeleton}?url`,
    atlasUrl: `../assets/${folderName}/${normalizedModel.atlas}?url`,
    textureUrl: `../assets/${folderName}/${normalizedModel.texture}?url`,
  }
  const updatedSource = source
    .replace(/import skeletonUrl from ['"][^'"]+['"]/, `import skeletonUrl from '${importPaths.skeletonUrl}'`)
    .replace(/import atlasUrl from ['"][^'"]+['"]/, `import atlasUrl from '${importPaths.atlasUrl}'`)
    .replace(/import textureUrl from ['"][^'"]+['"]/, `import textureUrl from '${importPaths.textureUrl}'`)

  if (updatedSource === source) throw new Error('没有找到模型路径配置')
  await writeFile(componentPath, updatedSource, 'utf8')

  const targetWindow = BrowserWindow.fromWebContents(event.sender)
  if (targetWindow) {
    // 模型切换后整页重载才能用上新的资源导入：
    // 桌宠窗口必须重载；设置窗口也重载，让预览与信息卡片同步到新模型。
    setBubbleWidth(false)
    if (win && !win.isDestroyed()) {
      // 重载后渲染层的“可交互”初值与主进程保持一致（默认不穿透）
      win.setIgnoreMouseEvents(false)
      win.webContents.reload()
    }
    if (settingsWindow && !settingsWindow.isDestroyed()) {
      settingsWindow.webContents.reload()
    }
  }
  return normalizedModel
})

ipcMain.on('show-context-menu', (event) => {
  const targetWindow = BrowserWindow.fromWebContents(event.sender)
  if (!targetWindow) return

  const contextMenu = Menu.buildFromTemplate([
    {
      label: '设置',
      click: () => openSettingsWindow(),
    },
    { type: 'separator' },
    {
      label: '关闭桌宠',
      click: () => app.quit(),
    },
  ])

  contextMenu.popup({ window: targetWindow })
})

ipcMain.handle('bubble-resize', (_event, visible: boolean) => setBubbleWidth(Boolean(visible)))

ipcMain.handle('walk-start', () => beginWalk())

ipcMain.on('walk-stop', () => endWalk(false))

ipcMain.on('window-drag-start', (event, pointerX: number, pointerY: number) => {
  const targetWindow = BrowserWindow.fromWebContents(event.sender)
  if (!targetWindow) return

  // 用户接管拖拽时立刻停止自主走动，位置基准才不会被走动带偏
  endWalk(true)

  const [windowX, windowY] = targetWindow.getPosition()
  windowDragOrigins.set(event.sender.id, { pointerX, pointerY, windowX, windowY })
})

ipcMain.on('window-drag-move', (event, pointerX: number, pointerY: number) => {
  const targetWindow = BrowserWindow.fromWebContents(event.sender)
  const origin = windowDragOrigins.get(event.sender.id)
  if (!targetWindow || !origin) return

  const nextX = Math.round(origin.windowX + pointerX - origin.pointerX)
  const nextY = Math.round(origin.windowY + pointerY - origin.pointerY)
  const [currentX, currentY] = targetWindow.getPosition()

  // 位置没变化就不要调用 setPosition：在缩放显示器上反复移动透明窗口会让尺寸
  // 产生 1px 级抖动，静止按住时会表现为窗口不断变大。
  if (nextX === currentX && nextY === currentY) return

  targetWindow.setPosition(nextX, nextY)

  // 兜底：部分缩放比例下 setPosition 会顺带把窗口尺寸带偏，这里立刻还原。
  // 显示气泡时窗口本来就是加宽状态，跳过检查避免把气泡挤掉。
  if (bubbleSide === null) {
    const [width, height] = targetWindow.getSize()
    if (width !== petWindowWidth || height !== petWindowHeight) {
      targetWindow.setSize(petWindowWidth, petWindowHeight)
    }
  }
})

ipcMain.on('window-drag-end', (event) => {
  windowDragOrigins.delete(event.sender.id)
})

ipcMain.on('set-ignore-mouse-events', (event, ignore: boolean) => {
  const targetWindow = BrowserWindow.fromWebContents(event.sender)
  targetWindow?.setIgnoreMouseEvents(ignore, { forward: true })
})

// 设置页已改为独立窗口，桌宠窗口不再需要为弹窗改变尺寸

function createWindow() {
  win = new BrowserWindow({
    width: petWindowWidth,
    height: petWindowHeight,
    // 高度锁死、宽度只留出气泡加宽的空间：避免在缩放显示器上被系统悄悄改变尺寸
    minWidth: petWindowWidth,
    maxWidth: petWindowWidth + bubbleWindowExtraWidth,
    minHeight: petWindowHeight,
    maxHeight: petWindowHeight,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.mjs'),
    },
  })

  win.once('ready-to-show', () => win?.show())
  // 桌宠是本应用的主窗口：它关闭时整个应用退出（设置窗口可能还开着）
  win.on('closed', () => {
    win = null
    app.quit()
  })

  // 桌宠窗口必须始终可用：默认保持“可交互”，只有当渲染层确认指针不在人物身上时
  // 才切成穿透。反过来（默认穿透）一旦页面/模型/命中检测任一环出问题，窗口就会永久
  // 失去鼠标事件，表现为既拖不动也右键不了。
  win.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    console.error('[ArkPet] 桌宠页面加载失败:', errorCode, errorDescription, validatedURL)
  })
  win.webContents.on('render-process-gone', (_event, details) => {
    console.error('[ArkPet] 桌宠渲染进程退出:', details.reason)
  })
  win.webContents.on('console-message', (_event, _level, message, line, sourceId) => {
    if (!message.includes('[ArkPet]')) return
    console.log(`[ArkPet 渲染层] ${message} (${sourceId}:${line})`)
  })

  loadRendererPage(win, 'index.html')
}

// Quit when all windows are closed, except on macOS. There, it's common
// for applications and their menu bar to stay active until the user quits
// explicitly with Cmd + Q.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
    win = null
  }
})

app.on('activate', () => {
  // On OS X it's common to re-create a window in the app when the
  // dock icon is clicked and there are no other windows open.
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow()
  }
})

app.whenReady().then(async () => {
  createWindow()

  await loadSettings()
  applyActivitySetting()
})

ipcMain.handle('settings-get', () => appSettings)

ipcMain.handle('settings-set', async (_event, patch: Partial<AppSettings>) => {
  appSettings = { ...appSettings, ...patch }
  await saveSettings()
  applyActivitySetting()
  notifySettings()
  return appSettings
})

app.on('will-quit', () => {
  activityWatcher?.stop()
  activityWatcher = null
})
