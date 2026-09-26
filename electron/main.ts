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
  if (win && !win.isDestroyed()) win.webContents.send('settings-changed', appSettings)
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

function setBubbleWidth(visible: boolean): { side: 'right' | 'left' } {
  if (!win || win.isDestroyed()) return { side: bubbleSide ?? 'right' }

  const bounds = win.getBounds()

  if (!visible) {
    if (bubbleSide) {
      // 收回时把窗口还原到桌宠尺寸，桌宠在屏幕上的位置保持不变
      const restoredX = bubbleSide === 'left' ? bounds.x + bubbleWindowExtraWidth : bounds.x
      win.setBounds({ x: restoredX, y: bounds.y, width: petWindowWidth, height: bounds.height })
      bubbleSide = null
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
  return { side }
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
    // 重新加载前先把窗口恢复到桌宠态：渲染进程重启后内部状态是“小窗 + 穿透”，
    // 若保留弹窗期的 620×620 与关闭穿透，人物会被横向拉伸且透明区域会挡住桌面点击。
    targetWindow.setIgnoreMouseEvents(true, { forward: true })
    targetWindow.setSize(petWindowWidth, petWindowHeight)
    targetWindow.webContents.reload()
  }
  return normalizedModel
})

ipcMain.on('show-context-menu', (event) => {
  const targetWindow = BrowserWindow.fromWebContents(event.sender)
  if (!targetWindow) return

  const contextMenu = Menu.buildFromTemplate([
    {
      label: '设置',
      click: () => targetWindow.webContents.send('open-settings'),
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

  targetWindow.setPosition(
    Math.round(origin.windowX + pointerX - origin.pointerX),
    Math.round(origin.windowY + pointerY - origin.pointerY),
  )
})

ipcMain.on('window-drag-end', (event) => {
  windowDragOrigins.delete(event.sender.id)
})

ipcMain.on('set-ignore-mouse-events', (event, ignore: boolean) => {
  const targetWindow = BrowserWindow.fromWebContents(event.sender)
  targetWindow?.setIgnoreMouseEvents(ignore, { forward: true })
})

ipcMain.on('resize-window', (event, width: number, height: number) => {
  const targetWindow = BrowserWindow.fromWebContents(event.sender)
  if (!targetWindow) return

  endWalk(true)
  targetWindow.setSize(Math.round(width), Math.round(height))

  // 放大到弹窗尺寸后可能越出屏幕，夹回当前显示器的工作区
  const bounds = targetWindow.getBounds()
  const { workArea } = screen.getDisplayMatching(bounds)
  const maxX = Math.max(workArea.x, workArea.x + workArea.width - bounds.width)
  const maxY = Math.max(workArea.y, workArea.y + workArea.height - bounds.height)
  const clampedX = Math.min(Math.max(bounds.x, workArea.x), maxX)
  const clampedY = Math.min(Math.max(bounds.y, workArea.y), maxY)

  if (clampedX !== bounds.x || clampedY !== bounds.y) {
    targetWindow.setPosition(clampedX, clampedY)
  }
})

function createWindow() {
  win = new BrowserWindow({
    width: petWindowWidth,
    height: petWindowHeight,
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
  win.setIgnoreMouseEvents(true, { forward: true })

  if (VITE_DEV_SERVER_URL) {
    win.loadURL(VITE_DEV_SERVER_URL)
  } else {
    // win.loadFile('dist/index.html')
    win.loadFile(path.join(RENDERER_DIST, 'index.html'))
  }
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
