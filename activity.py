"""前台活动检测（Windows），对齐旧版 legacy/electron/activity.ts。

旧版用常驻 PowerShell 子进程轮询 GetForegroundWindow；Python 直接用 ctypes
调 Win32 API，无需子进程，更轻量。分类表与旧版保持一致。

检测结果先按进程名归类，再与上一次比较，只有"应用发生变化"时才回调，
避免同一应用持续轮询造成重复提示。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes


# ---- 进程分类（与旧版 activity.ts 的 categoryDefinitions 一致） ----
CATEGORY_DEFINITIONS: list[dict] = [
    {"id": "browser", "label": "浏览器", "icon": "🌐", "processes": [
        "chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "arc", "iexplore",
        "360se", "360chrome", "qqbrowser", "sogouexplorer", "maxthon"]},
    {"id": "editor", "label": "编辑器", "icon": "💻", "processes": [
        "code", "code - insiders", "cursor", "devenv", "sublime_text", "notepad++", "notepad",
        "idea64", "pycharm64", "webstorm64", "goland64", "clion64", "rider64", "phpstorm64",
        "hbuilderx", "zed", "vim", "nvim", "emacs"]},
    {"id": "terminal", "label": "终端", "icon": "⌨️", "processes": [
        "windowsterminal", "wt", "powershell", "pwsh", "cmd", "conhost", "alacritty",
        "wezterm", "mintty", "bash", "ubuntu", "kali"]},
    {"id": "chat", "label": "聊天", "icon": "💬", "processes": [
        "wechat", "weixin", "qq", "tim", "dingtalk", "telegram", "discord", "slack", "zoom",
        "teams", "ms-teams", "feishu", "lark", "skype", "whatsapp"]},
    {"id": "media", "label": "影音", "icon": "🎬", "processes": [
        "potplayer", "potplayermini64", "vlc", "mpc-hc64", "mpc-be64", "bilibili",
        "cloudmusic", "qqmusic", "spotify", "kugou", "kwmusic", "migu"]},
    {"id": "game", "label": "游戏", "icon": "🎮", "processes": [
        "steam", "steamwebhelper", "leagueclient", "riotclientservices", "dota2", "cs2",
        "valorant", "genshinimpact", "starrail", "bh3", "endfield", "arknights",
        "epicgameslauncher", "battle.net"]},
    {"id": "emulator", "label": "模拟器", "icon": "📱", "processes": [
        "mumuplayer", "mumunb", "nox", "noxvmhandle", "ldvboxheadless", "dnplayer",
        "hd-player", "bluestacks", "memu"]},
    {"id": "office", "label": "办公文档", "icon": "📄", "processes": [
        "winword", "excel", "powerpnt", "onenote", "outlook", "wps", "et", "wpp", "acrobat",
        "acrord32", "sumatrapdf", "foxitpdfreader", "notion", "obsidian"]},
    {"id": "file", "label": "文件管理", "icon": "📁", "processes": [
        "explorer", "totalcmd64", "everything", "winrar", "7zfm", "bandizip"]},
    {"id": "design", "label": "设计", "icon": "🎨", "processes": [
        "photoshop", "illustrator", "figma", "blender", "krita", "sai2", "clipstudiopaint",
        "afterfx", "premiere"]},
]

_PROCESS_CATEGORY = {
    process: definition
    for definition in CATEGORY_DEFINITIONS
    for process in definition["processes"]
}

FALLBACK_CATEGORY = {"id": "other", "label": "其他应用", "icon": "🪟", "processes": []}

# 轮询间隔，与旧版 activityPollIntervalMs 一致。
ACTIVITY_POLL_INTERVAL_MS = 3000

# AI 回复触发概率：不是每次切换应用都问 AI（会太频繁、太贵），
# 只有随机命中且冷却结束才会请求。0.2 = 每五次切换约触发一次。
AI_REPLY_CHANCE = 0.2


def classify_activity(process_name: str, self_process_names: list[str] | None = None) -> dict:
    """把进程名归类；桌宠自身单独归为 self，便于调用方忽略。"""
    normalized = process_name.lower()
    if normalized and self_process_names and any(
        name.lower() == normalized for name in self_process_names
    ):
        return {"id": "self", "label": "桌宠", "icon": "🐾", "processes": []}
    direct = _PROCESS_CATEGORY.get(normalized)
    if direct:
        return direct
    # 处理带后缀/变体的进程名（例如 msedgewebview2）；短名字容易误伤，
    # 因此只对 5 个字符以上的名字做包含匹配（对齐旧版注释）。
    for process, definition in _PROCESS_CATEGORY.items():
        if len(process) >= 5 and process in normalized:
            return definition
    return FALLBACK_CATEGORY


def get_foreground_process_name() -> str:
    """当前前台窗口所属进程名；取不到时返回空字符串。"""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    handle = user32.GetForegroundWindow()
    if not handle:
        return ""
    process_id = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
    if not process_id.value:
        return ""
    # QueryFullProcessImageNameW 拿到完整路径后取文件名（不含 .exe）。
    size = wintypes.DWORD(1024)
    buffer = ctypes.create_unicode_buffer(size.value)
    # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    process = kernel32.OpenProcess(0x1000, False, process_id.value)
    if not process:
        return ""
    try:
        if not kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return ""
        full_path = buffer.value
    finally:
        kernel32.CloseHandle(process)
    name = full_path.rsplit("\\", 1)[-1]
    return name[:-4] if name.lower().endswith(".exe") else name


class ActivityWatcher:
    """轮询前台应用，变化时回调。由 QTimer 驱动（见 pet.py 的接线）。"""

    def __init__(self, on_change, self_process_names: list[str] | None = None) -> None:
        """on_change(snapshot)：snapshot = {category, label, icon, process_name, idle_seconds}。"""
        self._on_change = on_change
        self._self_process_names = self_process_names or []
        self._last_key = ""
        # 切换计数：用于概率触发 AI 回复（不是每次切换都问）。
        self._switch_count = 0

    def poll(self) -> None:
        """由外部定时器调用；前台应用变化时触发回调。"""
        process_name = get_foreground_process_name()
        if not process_name:
            return
        category = classify_activity(process_name, self._self_process_names)
        # 前台是桌宠自己（用户正在点它）时不打断上一次的真实活动。
        if category["id"] == "self":
            return
        key = f"{category['id']}:{process_name.lower()}"
        if key == self._last_key:
            return
        self._last_key = key
        self._switch_count += 1
        # 空闲秒数只是快照附带数据（供后续"离开/回来"类功能），这里从 pet 延迟导入
        # 避免 pet -> activity 的循环导入。
        from pet import get_system_idle_seconds
        snapshot = {
            "category": category["id"],
            "label": category["label"],
            "icon": category["icon"],
            "process_name": process_name,
            "idle_seconds": get_system_idle_seconds(),
            "switch_count": self._switch_count,
        }
        self._on_change(snapshot)
