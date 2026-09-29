"""桌宠聊天气泡：无边框透明小窗口，显示在桌宠旁边，几秒后自动消失。

样式对齐旧版 legacy/src/style.css 的 .pet-bubble：
  160px 宽、圆角 11px、#fffdf5 底、带指向桌宠的小尖角。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

# 气泡显示时长（毫秒），与旧版 App.vue 的 bubbleVisibleMs 一致。
BUBBLE_VISIBLE_MS = 8000
BUBBLE_WIDTH = 160
BUBBLE_GAP = 12          # 气泡与桌宠窗口的间距
BUBBLE_MARGIN_TOP = 34   # 气泡顶部相对桌宠窗口顶部的偏移（对齐旧版 margin-top）
BUBBLE_MAX_HEIGHT = 120


class BubbleWindow(QWidget):
    """一个聊天气泡。调用 show_bubble() 显示，超时自动隐藏。"""

    def __init__(self, pet_window: QWidget) -> None:
        super().__init__(None)
        self.pet_window = pet_window
        self.setWindowTitle("ArkPet Bubble")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput  # 气泡不拦截鼠标
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide_bubble)

        # 跟随定时器：气泡显示期间桌宠会移动（拖动、自主走动），气泡要贴着它走。
        # 用轮询而不是事件：桌宠的移动来自 move()（拖动/走动 tick），没有统一的
        # 信号可接；16ms 轮询与渲染帧率一致，开销可忽略。
        self._follow_timer = QTimer(self)
        self._follow_timer.setInterval(16)
        self._follow_timer.timeout.connect(self._reposition)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(7)
        self.icon_label = QLabel("")
        self.icon_label.setStyleSheet("font-size:15px; background:transparent;")
        self.icon_label.setFixedWidth(20)
        row.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)

        text_column = QHBoxLayout()
        text_column.setSpacing(0)
        self.text_label = QLabel("")
        self.text_label.setWordWrap(True)
        self.text_label.setStyleSheet(
            "color:#252b36; font-size:13px; font-weight:600; background:transparent;"
        )
        text_column.addWidget(self.text_label)
        row.addLayout(text_column)

        self.setFixedWidth(BUBBLE_WIDTH)
        self._side = "right"

    # ------------------------------------------------------------------ 显示/隐藏
    def show_bubble(self, icon: str, title: str, detail: str = "") -> None:
        """显示气泡并重新计时。气泡自动出现在桌宠旁边空间较多的一侧。"""
        text = title if not detail else f"{title}\n{detail}"
        self.icon_label.setText(icon)
        self.text_label.setText(text)
        self.adjustSize()
        if self.height() > BUBBLE_MAX_HEIGHT:
            self.setFixedHeight(BUBBLE_MAX_HEIGHT)
        self._reposition()
        self.show()
        self.raise_()
        self._timer.start(BUBBLE_VISIBLE_MS)
        self._follow_timer.start()

    def hide_bubble(self) -> None:
        self._timer.stop()
        self._follow_timer.stop()
        self.hide()

    def _reposition(self) -> None:
        """贴着桌宠窗口摆放：优先放右侧，屏幕放不下时放左侧。"""
        pet = self.pet_window
        pet_x, pet_y = pet.x(), pet.y()
        screen = pet.screen() or self.screen()
        if screen is not None:
            area = screen.availableGeometry()
        else:
            area = None

        width, height = self.width(), self.height()
        right_x = pet_x + pet.width() + BUBBLE_GAP
        left_x = pet_x - width - BUBBLE_GAP
        y = pet_y + BUBBLE_MARGIN_TOP

        side = "right"
        if area is not None and right_x + width > area.x() + area.width():
            side = "left"
        x = right_x if side == "right" else left_x
        if area is not None:
            x = max(area.x(), min(x, area.x() + area.width() - width))
        self._side = side
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    # ------------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """画圆角面板 + 指向桌宠的小尖角（对齐旧版 CSS 的 ::before/::after）。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        body = self.rect().adjusted(0, 0, -1, -1)
        radius = 11

        # 阴影（简单径向渐变近似旧版 box-shadow）。
        shadow = QRadialGradient(body.center(), max(body.width(), body.height()))
        shadow.setColorAt(0.85, QColor(30, 38, 48, 0))
        shadow.setColorAt(1.0, QColor(30, 38, 48, 40))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawRoundedRect(body, radius, radius)

        # 面板底色 + 边框。
        painter.setBrush(QColor(255, 253, 245))
        painter.setPen(QPen(QColor(47, 58, 74, 36), 1))
        painter.drawRoundedRect(body, radius, radius)

        # 尖角：指向桌宠一侧（气泡在右侧时角朝左，反之朝右）。
        arrow_y = 18
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 253, 245))
        if self._side == "right":
            points = [(0, arrow_y - 7), (0, arrow_y + 7), (7, arrow_y)]
        else:
            points = [(body.width(), arrow_y - 7), (body.width(), arrow_y + 7), (body.width() - 7, arrow_y)]
        from PySide6.QtGui import QPolygonF
        from PySide6.QtCore import QPointF
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
