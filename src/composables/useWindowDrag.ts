/**
 * 无边框窗口的拖动。
 *
 * 桌宠窗口与设置窗口是两个独立的 BrowserWindow，各自需要一个“按住某处拖动整个窗口”
 * 的能力，因此把这段逻辑抽成组合式函数共用；主进程侧仍然只有一套
 * `window-drag-start` / `window-drag-move` / `window-drag-end` 通道，
 * 依靠 `BrowserWindow.fromWebContents(event.sender)` 自动作用于发起拖动的那个窗口。
 */

export function useWindowDrag() {
  let dragPointerId: number | null = null
  let dragStartX = 0
  let dragStartY = 0
  let dragged = false
  // 窗口在静止的光标下移动时，系统仍会持续投递 mousemove（screenX/screenY 其实没变）。
  // 这里记住最后一次下发的坐标，坐标没变就不再请求移动，避免重复 setPosition —— 在
  // 缩放显示器上反复 setPosition 会让窗口尺寸产生 1px 级抖动，表现为“按住不动却在变大”。
  let lastSentX = Number.NaN
  let lastSentY = Number.NaN

  function startWindowDrag(event: PointerEvent) {
    if (event.button !== 0) return

    dragPointerId = event.pointerId
    dragStartX = event.screenX
    dragStartY = event.screenY
    dragged = false
    lastSentX = Number.NaN
    lastSentY = Number.NaN
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
      dragged = true
    }

    if (!dragged) return
    if (event.screenX === lastSentX && event.screenY === lastSentY) return

    lastSentX = event.screenX
    lastSentY = event.screenY
    window.ipcRenderer?.send('window-drag-move', event.screenX, event.screenY)
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

  /** 读取并清除“刚刚是否发生了拖动”，用于让短按点击不被拖动误触发。 */
  function consumeDragged() {
    const value = dragged
    dragged = false
    return value
  }

  return { startWindowDrag, moveWindow, endWindowDrag, consumeDragged }
}
