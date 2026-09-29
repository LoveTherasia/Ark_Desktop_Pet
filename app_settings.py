"""桌宠状态持久化：把用户偏好与窗口位置存到 JSON 文件。

旧版 Electron 用 settings.json（electron-store 风格），这里对齐同样的字段：
  - character / skin：上次使用的角色与皮肤（按 LocalModel 的目录名）
  - display_scale：人物大小
  - position：桌宠窗口位置 [x, y]
  - activity_bubble_enabled：桌面活动气泡开关

文件放在用户目录 %APPDATA%/ArkPet/settings.json，不污染仓库。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


DEFAULT_SETTINGS = {
    "character": None,
    "skin": None,
    "display_scale": 1.0,
    "position": None,
    "activity_bubble_enabled": True,
    # AI 回复配置（OpenAI 兼容 API）。
    "ai_enabled": False,
    "ai_api_base": "https://api.openai.com/v1",
    "ai_api_key": "",
    "ai_model": "gpt-4o-mini",
}


def settings_path() -> Path:
    """设置文件路径：%APPDATA%/ArkPet/settings.json（非 Windows 退回 ~/.config）。"""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "ArkPet" / "settings.json"


def load_settings() -> dict:
    """读取设置；文件缺失或损坏时返回默认值（不抛异常，启动不能被它卡住）。"""
    path = settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULT_SETTINGS)
    if not isinstance(raw, dict):
        return dict(DEFAULT_SETTINGS)
    # 只保留已知字段，未知字段忽略（向前兼容）。
    merged = dict(DEFAULT_SETTINGS)
    for key in DEFAULT_SETTINGS:
        if key in raw:
            merged[key] = raw[key]
    return merged


def save_settings(settings: dict) -> None:
    """写入设置；失败只写 stderr，不影响运行。"""
    path = settings_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: settings.get(key, default) for key, default in DEFAULT_SETTINGS.items()}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as error:
        print(f"[ArkPet] save settings failed: {error}", file=sys.stderr)
