import { spawn, type ChildProcess } from 'node:child_process'

/**
 * 前台活动检测（Windows）。
 *
 * Electron 没有提供“当前前台窗口属于哪个应用”的 API，这里用一个常驻的
 * PowerShell 子进程调用 Win32 `GetForegroundWindow` / `GetWindowThreadProcessId`，
 * 每 `pollIntervalMs` 输出一行 JSON（只取进程名，不读取窗口标题）。
 *
 * 检测结果会先按进程名归类，再与上一次比较，只有“应用发生变化”时才回调，
 * 避免同一应用持续轮询造成重复提示。后续要扩展新的活动来源，只要复用
 * `ActivitySnapshot` 这个结构即可。
 */

export type ActivityCategoryId =
  | 'browser'
  | 'editor'
  | 'terminal'
  | 'chat'
  | 'media'
  | 'game'
  | 'emulator'
  | 'office'
  | 'file'
  | 'design'
  | 'self'
  | 'other'

export type ActivitySnapshot = {
  category: ActivityCategoryId
  /** 类别中文名，例如“浏览器” */
  label: string
  /** 类别图标，供气泡直接使用 */
  icon: string
  /** 前台进程名，例如 chrome */
  processName: string
  /** 系统空闲秒数，便于后续做“离开/回来”类功能 */
  idleSeconds: number
}

type CategoryDefinition = {
  id: ActivityCategoryId
  label: string
  icon: string
  processes: string[]
}

const categoryDefinitions: CategoryDefinition[] = [
  {
    id: 'browser',
    label: '浏览器',
    icon: '🌐',
    processes: ['chrome', 'msedge', 'firefox', 'brave', 'opera', 'vivaldi', 'arc', 'iexplore', '360se', '360chrome', 'qqbrowser', 'sogouexplorer', 'maxthon'],
  },
  {
    id: 'editor',
    label: '编辑器',
    icon: '💻',
    processes: ['code', 'code - insiders', 'cursor', 'devenv', 'sublime_text', 'notepad++', 'notepad', 'idea64', 'pycharm64', 'webstorm64', 'goland64', 'clion64', 'rider64', 'phpstorm64', 'hbuilderx', 'zed', 'vim', 'nvim', 'emacs'],
  },
  {
    id: 'terminal',
    label: '终端',
    icon: '⌨️',
    processes: ['windowsterminal', 'wt', 'powershell', 'pwsh', 'cmd', 'conhost', 'alacritty', 'wezterm', 'mintty', 'bash', 'ubuntu', 'kali'],
  },
  {
    id: 'chat',
    label: '聊天',
    icon: '💬',
    processes: ['wechat', 'weixin', 'qq', 'tim', 'dingtalk', 'telegram', 'discord', 'slack', 'zoom', 'teams', 'ms-teams', 'feishu', 'lark', 'skype', 'whatsapp'],
  },
  {
    id: 'media',
    label: '影音',
    icon: '🎬',
    processes: ['potplayer', 'potplayermini64', 'vlc', 'mpc-hc64', 'mpc-be64', 'bilibili', 'cloudmusic', 'qqmusic', 'spotify', 'kugou', 'kwmusic', 'migu'],
  },
  {
    id: 'game',
    label: '游戏',
    icon: '🎮',
    processes: ['steam', 'steamwebhelper', 'leagueclient', 'riotclientservices', 'dota2', 'cs2', 'valorant', 'genshinimpact', 'starrail', 'bh3', 'endfield', 'arknights', 'epicgameslauncher', 'battle.net'],
  },
  {
    id: 'emulator',
    label: '模拟器',
    icon: '📱',
    processes: ['mumuplayer', 'mumunb', 'nox', 'noxvmhandle', 'ldvboxheadless', 'dnplayer', 'hd-player', 'bluestacks', 'memu'],
  },
  {
    id: 'office',
    label: '办公文档',
    icon: '📄',
    processes: ['winword', 'excel', 'powerpnt', 'onenote', 'outlook', 'wps', 'et', 'wpp', 'acrobat', 'acrord32', 'sumatrapdf', 'foxitpdfreader', 'notion', 'obsidian'],
  },
  {
    id: 'file',
    label: '文件管理',
    icon: '📁',
    processes: ['explorer', 'totalcmd64', 'everything', 'winrar', '7zfm', 'bandizip'],
  },
  {
    id: 'design',
    label: '设计',
    icon: '🎨',
    processes: ['photoshop', 'illustrator', 'figma', 'blender', 'krita', 'sai2', 'clipstudiopaint', 'afterfx', 'premiere'],
  },
]

const processCategory = new Map<string, CategoryDefinition>()
for (const definition of categoryDefinitions) {
  for (const process of definition.processes) {
    processCategory.set(process.toLowerCase(), definition)
  }
}

const fallbackCategory: CategoryDefinition = { id: 'other', label: '其他应用', icon: '🪟', processes: [] }

export const activityPollIntervalMs = 3000

/** 把进程名归类；桌宠自身单独归为 self，便于调用方忽略。 */
export function classifyActivity(processName: string, selfProcessNames: string[] = []): CategoryDefinition {
  const normalized = processName.toLowerCase()

  if (normalized && selfProcessNames.some((name) => name.toLowerCase() === normalized)) {
    return { id: 'self', label: '桌宠', icon: '🐾', processes: [] }
  }

  const direct = processCategory.get(normalized)
  if (direct) return direct

  // 处理带后缀/变体的进程名（例如 msedgewebview2）；短名字容易误伤（arc 会命中 SearchApp、
  // et 会命中 InternetDownloadManager），因此这里只对 5 个字符以上的名字做包含匹配。
  for (const [process, definition] of processCategory) {
    if (process.length >= 5 && normalized.includes(process)) return definition
  }

  return fallbackCategory
}

/** 常驻 helper：只编译一次 P/Invoke，之后按间隔输出 { pid, process }。 */
const helperScript = `
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class ArkPetForeground {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
}
"@
while ($true) {
  $handle = [ArkPetForeground]::GetForegroundWindow()
  $processId = [uint32]0
  [void][ArkPetForeground]::GetWindowThreadProcessId($handle, [ref]$processId)
  $name = ''
  try { $name = (Get-Process -Id $processId).ProcessName } catch { $name = '' }
  [Console]::Out.WriteLine('{"process":"' + $name + '"}')
  [Console]::Out.Flush()
  Start-Sleep -Milliseconds ${activityPollIntervalMs}
}
`

export type ActivityWatcher = {
  stop: () => void
  /** 最近一次检测到的前台应用（桌宠自身除外） */
  current: () => ActivitySnapshot | null
}

/**
 * 启动活动检测。`onChange` 只在前台应用发生变化时触发（首次检测也会触发一次）。
 * `getIdleSeconds` 由调用方注入（主进程传 `powerMonitor.getSystemIdleTime`），
 * 这样本模块不依赖 Electron，便于单独测试。
 */
export function startActivityWatcher(
  selfProcessNames: string[],
  onChange: (snapshot: ActivitySnapshot) => void,
  getIdleSeconds: () => number = () => 0,
): ActivityWatcher {
  let child: ChildProcess | null = null
  let stopped = false
  let restartTimer: ReturnType<typeof setTimeout> | null = null
  let buffer = ''
  let latest: ActivitySnapshot | null = null
  let lastKey = ''

  const encoded = Buffer.from(helperScript, 'utf16le').toString('base64')

  function handleLine(line: string) {
    let processName = ''
    try {
      processName = String((JSON.parse(line) as { process?: string }).process ?? '')
    } catch {
      return
    }
    if (!processName) return

    const category = classifyActivity(processName, selfProcessNames)
    // 前台是桌宠自己（用户正在点它）时不打断上一次的真实活动
    if (category.id === 'self') return

    const key = `${category.id}:${processName.toLowerCase()}`
    if (key === lastKey) return
    lastKey = key

    const idleSeconds = getIdleSeconds()
    latest = {
      category: category.id,
      label: category.label,
      icon: category.icon,
      processName,
      idleSeconds,
    }
    onChange(latest)
  }

  function start() {
    if (stopped) return

    child = spawn(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
      { windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] },
    )

    child.stdout?.setEncoding('utf8')
    child.stdout?.on('data', (chunk: string) => {
      buffer += chunk
      let index = buffer.indexOf('\n')
      while (index >= 0) {
        const line = buffer.slice(0, index).trim()
        buffer = buffer.slice(index + 1)
        if (line) handleLine(line)
        index = buffer.indexOf('\n')
      }
    })

    child.on('exit', () => {
      child = null
      if (stopped) return
      // helper 意外退出时自动拉起，5 秒后重试
      restartTimer = setTimeout(start, 5000)
    })

    child.on('error', () => {
      child = null
    })
  }

  start()

  return {
    stop() {
      stopped = true
      if (restartTimer) clearTimeout(restartTimer)
      restartTimer = null
      child?.kill()
      child = null
    },
    current: () => latest,
  }
}
