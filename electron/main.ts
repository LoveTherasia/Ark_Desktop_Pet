import { app, BrowserWindow, Menu, ipcMain, screen } from 'electron'
import { access, readFile, readdir, rename, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

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
type LocalModel = { name: string; skeleton: string; atlas: string; texture: string }

// ---- 自主走动：位移在主进程完成，边界一律以当前显示器的工作区为准 ----
const walkSpeedPxPerSecond = 60
const walkTickMs = 16
const walkMinDistancePx = 80
const walkMaxDistancePx = 320

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

async function listLocalModels(): Promise<LocalModel[]> {
  const assetsDirectory = path.join(process.env.APP_ROOT, 'src', 'assets')
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
        // name 就是相对 src/assets 的路径：<角色>/<皮肤>
        models.push({
          name: `${characterEntry.name}/${skinEntry.name}`,
          skeleton,
          atlas,
          texture,
        })
      }
    }
  }

  return models.sort((left, right) => left.name.localeCompare(right.name, 'zh-Hans-CN'))
}

ipcMain.handle('list-local-models', () => listLocalModels())

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
  targetWindow?.webContents.reload()
  return normalizedModel
})

ipcMain.on('show-context-menu', (event) => {
  const targetWindow = BrowserWindow.fromWebContents(event.sender)
  if (!targetWindow) return

  const contextMenu = Menu.buildFromTemplate([
    {
      label: '更换人物',
      click: () => targetWindow.webContents.send('open-character-browser'),
    },
    { type: 'separator' },
    {
      label: '关闭桌宠',
      click: () => app.quit(),
    },
  ])

  contextMenu.popup({ window: targetWindow })
})

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
    width: 320,
    height: 380,
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

app.whenReady().then(createWindow)
