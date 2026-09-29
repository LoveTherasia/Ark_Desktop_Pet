"""AI 回复：用户切换前台应用时，让桌宠用 LLM 生成一句台词显示在气泡里。

使用 OpenAI 兼容 API（/v1/chat/completions）：API 地址、密钥、模型名都可在
设置窗口配置。请求在 QThread 里执行，不阻塞 UI；失败静默（桌宠不该因为
网络问题弹错误框）。

系统提示词把桌宠定位成"明日方舟角色"，回复限制在一句以内，适合气泡展示。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from PySide6.QtCore import QObject, QThread, Signal


# 请求超时（秒）。桌宠的回复不需要等太久，超时就放弃这次。
AI_REQUEST_TIMEOUT_SECONDS = 15

# 系统提示词：约束回复风格（短、口语、角色扮演）。
SYSTEM_PROMPT = (
    "你是一个明日方舟桌面宠物角色，正在用户的电脑桌面上陪伴用户工作。"
    "用户刚刚切换到了一个新的应用程序，请以角色的口吻对这件事做一句简短的评论或吐槽。"
    "要求：只用中文回复；不超过 30 个字；像聊天一样自然，不要加引号和任何前缀；"
    "可以适当俏皮但不要过分打扰。"
)

# 单次请求的最大 token 数，防止长回复撑爆气泡。
AI_MAX_TOKENS = 60


class AIReplyWorker(QObject):
    """在后台线程里调用 chat/completions，完成后发信号。"""

    finished = Signal(str)   # 成功：回复文本
    failed = Signal(str)     # 失败：错误信息（只写日志，不弹窗）

    def __init__(self, api_base: str, api_key: str, model: str, user_message: str) -> None:
        super().__init__()
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.user_message = user_message

    def run(self) -> None:
        try:
            reply = self._request()
            self.finished.emit(reply)
        except (OSError, ValueError, urllib.error.URLError) as error:
            self.failed.emit(str(error))

    def _request(self) -> str:
        """同步执行一次 chat/completions 请求，返回回复文本。"""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self.user_message},
            ],
            "max_tokens": AI_MAX_TOKENS,
            "temperature": 0.9,
        }
        request = urllib.request.Request(
            f"{self.api_base}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=AI_REQUEST_TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        # 有些模型会在外面套引号或换行，清理一下（含中英文引号）。
        return content.strip().strip('"“”「」『』').strip()


class AIReplyService:
    """管理后台线程：同一时间只允许一个请求在跑（避免频繁切换应用时排队轰炸）。"""

    def __init__(self) -> None:
        self._thread: QThread | None = None
        self._worker: AIReplyWorker | None = None
        # 简单冷却：上次请求时间。切换应用很频繁，即使概率命中也不该连着请求。
        self._last_request_at = 0.0
        self.cooldown_seconds = 60.0

    @property
    def busy(self) -> bool:
        # deleteLater 销毁 C++ 对象后 Python 引用仍可能短暂存在（cleanup 排在其后），
        # isRunning() 会抛 RuntimeError；捕获后视为空闲。
        try:
            return self._thread is not None and self._thread.isRunning()
        except RuntimeError:
            return False

    def request(
        self,
        api_base: str,
        api_key: str,
        model: str,
        user_message: str,
        on_success,
        on_error=None,
    ) -> bool:
        """发起一次回复请求。返回 False 表示当前忙/冷却中/配置不全，直接放弃。"""
        import time

        if self.busy:
            return False
        if time.monotonic() - self._last_request_at < self.cooldown_seconds:
            return False
        if not api_base or not api_key or not model:
            return False

        self._last_request_at = time.monotonic()
        self._thread = QThread()
        self._worker = AIReplyWorker(api_base, api_key, model, user_message)
        self._worker.moveToThread(self._thread)

        def cleanup() -> None:
            self._thread = None
            self._worker = None

        self._worker.finished.connect(on_success)
        if on_error is not None:
            self._worker.failed.connect(on_error)
        self._thread.started.connect(self._worker.run)
        # run() 只是线程事件循环里的一个槽调用，返回后线程不会自己退出，
        # 必须显式 quit()；否则线程永远 busy，后续请求全被拒。
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        # 线程结束后清理引用。cleanup 必须先于 deleteLater 连接（先触发），
        # 否则 cleanup 里访问 self._thread 时 C++ 对象可能已被销毁。
        self._thread.finished.connect(cleanup)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        return True
