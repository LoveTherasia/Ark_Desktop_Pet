import { app, BrowserWindow, Menu, ipcMain } from 'electron'
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

ipcMain.on('show-context-menu', (event) => {
  const targetWindow = BrowserWindow.fromWebContents(event.sender)
  if (!targetWindow) return

  const contextMenu = Menu.buildFromTemplate([
    {
      label: '关闭桌宠',
      click: () => app.quit(),
    },
  ])

  contextMenu.popup({ window: targetWindow })
})

ipcMain.on('window-drag-start', (event, pointerX: number, pointerY: number) => {
  const targetWindow = BrowserWindow.fromWebContents(event.sender)
  if (!targetWindow) return

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
