"""独立的设置窗口，布局与交互对齐 Vue 版本的 SettingsPanel.vue。

Vue 版本结构（src/components/SettingsPanel.vue）：
    标题栏（SETTINGS / 设置 / ×）
    当前模型预览 + 模型信息
    分区标签（更换人物 / 更换皮肤 / 偏好）
    内容区（唯一滚动区域）
    固定底栏（确认更换）

本模块沿用同样的骨架，并按需求把「调节大小」放进了「偏好」分区。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from spine_view import (
    BASE_MODEL_SCALE,
    DEFAULT_DISPLAY_SCALE,
    DISPLAY_SCALE_MAX,
    DISPLAY_SCALE_MIN,
    DISPLAY_SCALE_STEP,
    LocalModel,
    SpineBridge,
    SpineGLView,
)


# =============================================================================
# 可调参数
# =============================================================================
SETTINGS_WINDOW_WIDTH = 620
# 高度要一次容下最多的那一页（更换人物：标题栏+信息区+标签+搜索框+提示+列表）。
# 高度不足时 Qt 会把控件压到最小高度以下，出现文字互相重叠。
SETTINGS_WINDOW_HEIGHT = 620
SETTINGS_PREVIEW_WIDTH = 168
SETTINGS_PREVIEW_HEIGHT = 144
# 角色 / 皮肤列表的最小高度（含搜索框与提示行后，整页最小高度约 200px，
# 正好放进窗口；窗口更高时列表会自动长高）。再小就会把提示行挤到列表上。
OPTION_LIST_MIN_HEIGHT = 120
# 预览里人物的显示比例（固定值，不跟随主窗口的「大小」）。
# 除以 BASE_MODEL_SCALE 抵消全局基准放大：预览框是固定尺寸，
# 人物跟着基准变大会超出预览框。
PREVIEW_DISPLAY_SCALE = 0.95 / BASE_MODEL_SCALE
# 设置面板配色，取自 Vue 版本 src/style.css
PANEL_BACKGROUND = "#fffdf5"
PANEL_BORDER = "rgba(47, 58, 74, 0.18)"
PREVIEW_BACKGROUND = "#f7f9f3"
INK = "#252b36"
MUTED = "#7d8478"
FAINT = "#647060"
ACCENT = "#c47758"
HAIRLINE = "#e0e5dc"
CHIP_BACKGROUND = "#e9eee5"
FIELD_BACKGROUND = "#f7f9f3"

# 滑块的整数刻度：0..100 线性映射到 DISPLAY_SCALE_MIN..DISPLAY_SCALE_MAX
_SLIDER_TICKS = 100


def scale_to_ticks(scale: float) -> int:
    span = DISPLAY_SCALE_MAX - DISPLAY_SCALE_MIN
    if span <= 0:
        return 0
    ratio = (scale - DISPLAY_SCALE_MIN) / span
    return max(0, min(_SLIDER_TICKS, round(ratio * _SLIDER_TICKS)))


def ticks_to_scale(ticks: int) -> float:
    span = DISPLAY_SCALE_MAX - DISPLAY_SCALE_MIN
    return DISPLAY_SCALE_MIN + (ticks / _SLIDER_TICKS) * span


PREVIEW_TRANSPARENT = (0.0, 0.0, 0.0, 0.0)


class DraggableHeader(QWidget):
    """标题栏：按住空白处可以拖动设置窗口（对应 Vue 的 -webkit-app-region: drag）。"""

    def __init__(self, parent: QWidget, window: QWidget) -> None:
        super().__init__(parent)
        self._window = window
        self._drag_offset: QPoint | None = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self._window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
        super().mouseReleaseEvent(event)


class SettingsWindow(QWidget):
    """设置窗口。模型切换通过 model_selected 回调交给主程序执行。"""

    size_changed = Signal(float)
    bubble_toggled = Signal(bool)
    ai_config_changed = Signal(bool, str, str, str)  # enabled, api_base, api_key, model

    def __init__(
        self,
        models: list[LocalModel],
        current: LocalModel | None,
        current_scale: float,
        preview_bridge: SpineBridge | None,
        preview_texture: Path | None,
        on_activate_model,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.models = models
        self.current_model = current
        self.on_activate_model = on_activate_model
        self._selected_character = ""
        self._selected_skin = ""
        self._preview_bridge = preview_bridge

        self.setWindowTitle("ArkPet 设置")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        # 固定尺寸：Vue 版本也把 min/max 锁成同一尺寸，避免拖动时边框被拉伸。
        self.setFixedSize(SETTINGS_WINDOW_WIDTH, SETTINGS_WINDOW_HEIGHT)

        self._build_ui(current_scale, preview_bridge, preview_texture)
        self._apply_theme()
        self._refresh_lists()

    # 版面节奏：内容总高度必须留足，否则 Qt 会把控件压到最小高度以下造成重叠。
    # 各段高度：标题栏 60 + 信息区 176 + 标签 36 + 搜索框 40 + 提示 16 + 列表 +
    #           间距 5×12 + 上下内边距 48。
    CONTENT_MARGIN = 24
    SECTION_SPACING = 12

    def _build_ui(
        self,
        current_scale: float,
        preview_bridge: SpineBridge | None,
        preview_texture: Path | None,
    ) -> None:
        panel = QFrame(self)
        panel.setObjectName("panel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)

        margin = self.CONTENT_MARGIN
        column = QVBoxLayout(panel)
        column.setContentsMargins(margin, margin, margin, margin)
        column.setSpacing(self.SECTION_SPACING)

        column.addWidget(self._build_header(panel))
        column.addWidget(self._build_summary(preview_bridge, preview_texture))
        column.addWidget(self._build_tabs())
        column.addWidget(self._build_error_label())
        column.addWidget(self._build_body(), stretch=1)
        # 底栏必须在 set_tab 之前建好：set_tab 会刷新底栏的可见性。
        column.addWidget(self._build_footer())
        self.set_tab(0)
        self._sync_preference_controls(current_scale)

    def _build_header(self, panel: QFrame) -> QWidget:
        header = DraggableHeader(panel, self)
        header.setObjectName("header")
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)

        titles = QVBoxLayout()
        titles.setSpacing(3)
        kicker = QLabel("SETTINGS")
        kicker.setObjectName("kicker")
        title = QLabel("设置")
        title.setObjectName("title")
        titles.addWidget(kicker)
        titles.addWidget(title)
        row.addLayout(titles)
        row.addStretch(1)

        close_button = QPushButton("×")
        close_button.setObjectName("closeButton")
        close_button.setFixedSize(32, 32)
        close_button.setToolTip("关闭设置")
        close_button.clicked.connect(self.hide)
        row.addWidget(close_button, alignment=Qt.AlignmentFlag.AlignTop)
        return header

    def _build_summary(self, preview_bridge: SpineBridge | None, preview_texture: Path | None) -> QWidget:
        summary = QWidget()
        row = QHBoxLayout(summary)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(18)

        # 左：模型预览。用和主窗口同一套渲染，只是固定尺寸、不交互、浅色底。
        preview_column = QVBoxLayout()
        preview_column.setSpacing(6)
        preview_frame = QFrame()
        preview_frame.setObjectName("previewFrame")
        preview_frame.setFixedSize(SETTINGS_PREVIEW_WIDTH, SETTINGS_PREVIEW_HEIGHT)
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        if preview_bridge is not None and preview_texture is not None:
            self.preview = SpineGLView(
                preview_bridge,
                preview_texture,
                fit_mode="idle",
                display_scale=PREVIEW_DISPLAY_SCALE,
                interactive=False,
                background=PREVIEW_TRANSPARENT,
            )
            preview_layout.addWidget(self.preview)
        else:
            self.preview = None
            fallback = QLabel("预览不可用")
            fallback.setAlignment(Qt.AlignmentFlag.AlignCenter)
            fallback.setObjectName("muted")
            preview_layout.addWidget(fallback)
        preview_column.addWidget(preview_frame)
        preview_label = QLabel("当前模型预览")
        preview_label.setObjectName("previewLabel")
        preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_column.addWidget(preview_label)
        preview_column.addStretch(1)
        row.addLayout(preview_column)

        # 右：模型信息
        info = QVBoxLayout()
        info.setSpacing(4)
        info_kicker = QLabel("当前人物")
        info_kicker.setObjectName("kicker")
        info.addWidget(info_kicker)
        self.name_label = QLabel("未知")
        self.name_label.setObjectName("name")
        info.addWidget(self.name_label)

        self.skin_label = QLabel("—")
        self.skin_label.setObjectName("muted")
        info.addWidget(self.skin_label)
        info.addSpacing(10)

        self.fields: dict[str, QLabel] = {}
        for key, title in (
            ("skin", "皮肤"),
            ("atlas", "图集"),
            ("skeleton", "骨骼"),
            ("type", "类型"),
            ("resource", "资源"),
        ):
            line = QWidget()
            line_layout = QHBoxLayout(line)
            line_layout.setContentsMargins(0, 0, 0, 4)
            key_label = QLabel(title)
            key_label.setObjectName("fieldKey")
            value_label = QLabel("—")
            value_label.setObjectName("fieldValue")
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            line_layout.addWidget(key_label)
            line_layout.addStretch(1)
            line_layout.addWidget(value_label)
            self.fields[key] = value_label
            info.addWidget(line)
        info.addStretch(1)
        row.addLayout(info, stretch=1)
        return summary

    def _build_tabs(self) -> QWidget:
        tabs = QWidget()
        tabs.setObjectName("tabs")
        row = QHBoxLayout(tabs)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.tab_buttons: list[QPushButton] = []
        for index, label in enumerate(("更换人物", "更换皮肤", "偏好")):
            button = QPushButton(label)
            button.setObjectName("tab")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, value=index: self.set_tab(value))
            row.addWidget(button)
            self.tab_buttons.append(button)
        row.addStretch(1)
        return tabs

    def _build_error_label(self) -> QWidget:
        self.error_label = QLabel("")
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        return self.error_label

    def _build_body(self) -> QWidget:
        self.stack = QStackedWidget()
        # 角色 / 皮肤页自带可滚动列表，不再套一层滚动区：
        # 嵌套滚动会让内层列表高度塌缩，而且外层滚动条会盖在搜索框上。
        self.stack.addWidget(self._build_character_tab())
        self.stack.addWidget(self._build_skin_tab())
        # 偏好页是普通表单，内容可能超出窗口，用滚动区兜底。
        self.stack.addWidget(self._wrap_scrollable(self._build_preference_tab()))
        return self.stack

    @staticmethod
    def _wrap_scrollable(page: QWidget) -> QWidget:
        """把内容包进滚动区（对应 Vue 版本唯一的滚动容器 .settings-body）。

        注意：只有不含内层滚动控件（如 QListWidget）的页面才适合套这一层。
        滚动区默认视口是深色的，所以底色必须在 _apply_theme 里显式刷成面板色。
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page.setAutoFillBackground(False)
        scroll.setWidget(page)
        return scroll

    def _build_character_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)

        self.character_query = QLineEdit()
        self.character_query.setPlaceholderText("按角色名筛选，例如 阿米娅")
        self.character_query.setClearButtonEnabled(True)
        self.character_query.textChanged.connect(lambda _text: self._refresh_lists())
        column.addWidget(self.character_query)

        # 可滚动的角色列表（对应 Vue 的 .settings-body > .model-results）。
        # Expanding 让它在窗口有多余高度时吃掉空隙；stretch=0 保证它不会去抢
        # 下方提示行的空间（那会造成文字重叠）。
        self.character_list = QListWidget()
        self.character_list.setObjectName("optionList")
        self.character_list.setUniformItemSizes(True)
        self.character_list.setMinimumHeight(OPTION_LIST_MIN_HEIGHT)
        self.character_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.character_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.character_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.character_list.currentItemChanged.connect(self._on_character_selected)
        column.addWidget(self.character_list, stretch=0)

        self.character_hint = QLabel("")
        self.character_hint.setObjectName("muted")
        self.character_hint.setWordWrap(True)
        column.addWidget(self.character_hint)
        column.addStretch(1)
        return page

    def _build_skin_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(10)

        self.skin_query = QLineEdit()
        self.skin_query.setPlaceholderText("按皮肤名筛选，例如 默认服装")
        self.skin_query.setClearButtonEnabled(True)
        self.skin_query.textChanged.connect(lambda _text: self._refresh_skin_list())
        column.addWidget(self.skin_query)

        self.skin_hint = QLabel("")
        self.skin_hint.setObjectName("muted")
        self.skin_hint.setWordWrap(True)
        column.addWidget(self.skin_hint)

        # 可滚动的皮肤列表。同样 Expanding 吃空隙、stretch=0 不抢提示行空间。
        self.skin_list = QListWidget()
        self.skin_list.setObjectName("optionList")
        self.skin_list.setUniformItemSizes(True)
        self.skin_list.setMinimumHeight(OPTION_LIST_MIN_HEIGHT)
        self.skin_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.skin_list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.skin_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.skin_list.currentItemChanged.connect(self._on_skin_selected)
        column.addWidget(self.skin_list, stretch=0)
        column.addStretch(0)
        return page

    def _build_preference_tab(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(12)

        # ---- 大小：人物在桌宠窗口里的显示比例 ----
        card = QFrame()
        card.setObjectName("card")
        card_column = QVBoxLayout(card)
        card_column.setContentsMargins(14, 12, 14, 12)
        card_column.setSpacing(8)

        title = QLabel("人物大小")
        title.setObjectName("cardTitle")
        card_column.addWidget(title)
        desc = QLabel("调整人物在桌宠窗口中的显示比例，实时生效，不改变窗口本身的大小。")
        desc.setObjectName("cardDesc")
        desc.setWordWrap(True)
        card_column.addWidget(desc)

        slider_row = QHBoxLayout()
        slider_row.setSpacing(10)
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(0, _SLIDER_TICKS)
        self.size_slider.setSingleStep(
            max(1, round(DISPLAY_SCALE_STEP / (DISPLAY_SCALE_MAX - DISPLAY_SCALE_MIN) * _SLIDER_TICKS))
        )
        self.size_slider.setPageStep(self.size_slider.singleStep() * 2)
        self.size_slider.setFixedHeight(22)
        self.size_slider.valueChanged.connect(self._on_slider_changed)
        slider_row.addWidget(self.size_slider, stretch=1)

        self.size_value = QLabel("")
        self.size_value.setObjectName("sizeValue")
        self.size_value.setFixedWidth(48)
        self.size_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider_row.addWidget(self.size_value)
        card_column.addLayout(slider_row)

        reset_button = QPushButton("恢复默认大小")
        reset_button.setObjectName("linkButton")
        reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        reset_button.clicked.connect(lambda: self.set_display_scale(DEFAULT_DISPLAY_SCALE))
        card_column.addWidget(reset_button, alignment=Qt.AlignmentFlag.AlignLeft)
        column.addWidget(card)

        # ---- 桌面活动气泡开关 ----
        other = QFrame()
        other.setObjectName("card")
        other_column = QVBoxLayout(other)
        other_column.setContentsMargins(14, 12, 14, 12)
        other_column.setSpacing(8)
        other_title = QLabel("桌面活动气泡")
        other_title.setObjectName("cardTitle")
        other_column.addWidget(other_title)
        other_desc = QLabel(
            "切换前台应用时在人物旁弹出气泡提示。关闭后不再弹出，同时停止前台应用检测。"
        )
        other_desc.setObjectName("cardDesc")
        other_desc.setWordWrap(True)
        other_column.addWidget(other_desc)

        toggle_row = QHBoxLayout()
        toggle_row.addStretch(1)
        self.bubble_toggle = QPushButton("已开启")
        self.bubble_toggle.setObjectName("linkButton")
        self.bubble_toggle.setCheckable(True)
        self.bubble_toggle.setChecked(True)
        self.bubble_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.bubble_toggle.toggled.connect(self._on_bubble_toggled)
        toggle_row.addWidget(self.bubble_toggle)
        other_column.addLayout(toggle_row)
        column.addWidget(other)

        # ---- AI 回复配置 ----
        ai_card = QFrame()
        ai_card.setObjectName("card")
        ai_column = QVBoxLayout(ai_card)
        ai_column.setContentsMargins(14, 12, 14, 12)
        ai_column.setSpacing(8)

        ai_title = QLabel("AI 智能回复")
        ai_title.setObjectName("cardTitle")
        ai_column.addWidget(ai_title)
        ai_desc = QLabel(
            "偶尔切换应用时，让 AI 以角色口吻评论一句并显示在气泡里。"
            "使用 OpenAI 兼容 API（/v1/chat/completions）。\n"
            "（该功能暂时下线调整中，配置保留但不会触发。）"
        )
        ai_desc.setObjectName("cardDesc")
        ai_desc.setWordWrap(True)
        ai_column.addWidget(ai_desc)

        self.ai_enabled_check = QCheckBox("启用 AI 回复")
        self.ai_enabled_check.toggled.connect(self._on_ai_changed)
        ai_column.addWidget(self.ai_enabled_check)

        def add_field(row_label: str, placeholder: str, echo_mode=None) -> QLineEdit:
            row = QHBoxLayout()
            row.setSpacing(10)
            label = QLabel(row_label)
            label.setFixedWidth(70)
            field = QLineEdit()
            field.setPlaceholderText(placeholder)
            if echo_mode is not None:
                field.setEchoMode(echo_mode)
            field.textChanged.connect(self._on_ai_changed)
            row.addWidget(label)
            row.addWidget(field, stretch=1)
            ai_column.addLayout(row)
            return field

        self.ai_base_field = add_field("API 地址", "https://api.openai.com/v1")
        self.ai_key_field = add_field("API 密钥", "sk-...", QLineEdit.EchoMode.Password)
        self.ai_model_field = add_field("模型", "gpt-4o-mini")
        column.addWidget(ai_card)

        # 底部留白，避免滚动到底时最后一行的边框贴着窗口边缘。
        column.addStretch(1)
        column.addSpacing(4)
        return page

    def _build_footer(self) -> QWidget:
        self.footer = QFrame()
        self.footer.setObjectName("footer")
        row = QHBoxLayout(self.footer)
        row.setContentsMargins(0, 12, 0, 0)
        row.setSpacing(12)

        text_column = QVBoxLayout()
        text_column.setSpacing(2)
        label = QLabel("确认更换为")
        label.setObjectName("kicker")
        text_column.addWidget(label)
        self.footer_target = QLabel("")
        self.footer_target.setObjectName("footerTarget")
        text_column.addWidget(self.footer_target)
        row.addLayout(text_column)
        row.addStretch(1)

        self.confirm_button = QPushButton("确认更换")
        self.confirm_button.setObjectName("primaryButton")
        self.confirm_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.confirm_button.clicked.connect(self._confirm_change)
        row.addWidget(self.confirm_button)

        self.footer.hide()
        return self.footer

    def _apply_theme(self) -> None:
        self.setStyleSheet(f"""
            QWidget {{
                font-family: 'Trebuchet MS', 'Microsoft YaHei UI', sans-serif;
                color: {INK};
            }}
            QFrame#panel {{
                background: {PANEL_BACKGROUND};
                border: 1px solid {PANEL_BORDER};
                border-radius: 14px;
            }}
            /* 滚动区默认视口是深色的，会把内容区画成黑块。
               这里显式把视口和列表底色刷成面板色，和四周保持一致。 */
            QScrollArea, QScrollArea > QWidget > QWidget {{
                background: {PANEL_BACKGROUND};
                border: 0;
            }}
            QStackedWidget, QStackedWidget > QWidget {{
                background: {PANEL_BACKGROUND};
                border: 0;
            }}
            QLabel {{
                background: transparent;
            }}
            QLabel#kicker {{
                color: {MUTED};
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 1.4px;
                /* 让 kicker 与下方标题之间留出足够间距，避免文字贴在一起 */
                margin-bottom: 2px;
            }}
            QLabel#title {{
                font-size: 26px;
                font-weight: 600;
            }}
            QLabel#name {{
                font-size: 22px;
                font-weight: 600;
            }}
            QLabel#muted, QLabel#previewLabel {{
                color: {MUTED};
                font-size: 11px;
            }}
            QLabel#fieldKey {{
                color: {MUTED};
                font-size: 11px;
            }}
            QLabel#fieldValue {{
                font-size: 11px;
            }}
            QLabel#error {{
                color: #b4462f;
                font-size: 12px;
            }}
            QLabel#cardTitle {{
                font-size: 14px;
                font-weight: 600;
            }}
            QLabel#cardDesc {{
                color: {MUTED};
                font-size: 11px;
            }}
            QLabel#sizeValue {{
                color: {ACCENT};
                font-size: 13px;
                font-weight: 700;
            }}
            QLabel#footerTarget {{
                font-size: 14px;
                font-weight: 600;
            }}
            /* 角色 / 皮肤列表：可滚动的内嵌窗口。
               padding 只用上下各 1px：QListWidget 的默认内边距会把第一行裁掉一截。 */
            QListWidget#optionList {{
                border: 1px solid {HAIRLINE};
                border-radius: 10px;
                background: {PANEL_BACKGROUND};
                outline: 0;
                padding: 1px 2px;
            }}
            QListWidget#optionList::item {{
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 0 10px;
                color: {INK};
            }}
            QListWidget#optionList::item:hover {{
                background: {CHIP_BACKGROUND};
            }}
            QListWidget#optionList::item:selected {{
                background: #f0e2da;
                border: 1px solid {ACCENT};
            }}
            /* 滚动条：默认样式在浅色面板上很突兀，换成细窄的浅色条 */
            QScrollBar:vertical {{
                border: 0;
                background: transparent;
                width: 10px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: #cbd3c6;
                border-radius: 5px;
                min-height: 28px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: #b3bcac;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
                border: 0;
                background: transparent;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent;
            }}
            QFrame#previewFrame {{
                background: {PREVIEW_BACKGROUND};
                border: 1px solid {HAIRLINE};
                border-radius: 10px;
            }}
            QFrame#card {{
                background: {FIELD_BACKGROUND};
                border: 1px solid {HAIRLINE};
                border-radius: 10px;
            }}
            QFrame#footer {{
                border-top: 1px solid {HAIRLINE};
            }}
            QPushButton#closeButton {{
                border: 0;
                border-radius: 16px;
                color: {FAINT};
                background: {CHIP_BACKGROUND};
                font-size: 20px;
            }}
            QPushButton#closeButton:hover {{
                background: #dde4d8;
            }}
            QPushButton#tab {{
                border: 0;
                border-bottom: 2px solid transparent;
                padding: 8px 14px;
                color: {MUTED};
                background: transparent;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton#tab:checked {{
                border-bottom-color: {ACCENT};
                color: {ACCENT};
            }}
            QPushButton#primaryButton {{
                border: 0;
                border-radius: 7px;
                padding: 9px 18px;
                color: {PANEL_BACKGROUND};
                background: {ACCENT};
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton#primaryButton:hover {{
                background: #b96a4c;
            }}
            QPushButton#primaryButton:disabled {{
                background: #d8c3b8;
            }}
            QPushButton#linkButton {{
                border: 0;
                padding: 2px 0;
                color: {ACCENT};
                background: transparent;
                font-size: 11px;
                text-align: left;
            }}
            QLineEdit {{
                border: 1px solid #cbd3c6;
                border-radius: 7px;
                padding: 9px 10px;
                background: #ffffff;
                font-size: 13px;
                min-height: 20px;
            }}
            QLineEdit:focus {{
                border-color: {ACCENT};
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                border-radius: 2px;
                background: #cbd3c6;
            }}
            QSlider::sub-page:horizontal {{
                height: 4px;
                border-radius: 2px;
                background: {ACCENT};
            }}
            QSlider::handle:horizontal {{
                width: 16px;
                margin: -6px 0;
                border-radius: 8px;
                background: {PANEL_BACKGROUND};
                border: 2px solid {ACCENT};
            }}
        """)

    # -------------------------------------------------------------------- 列表条目
    def _populate(self, widget: QListWidget, entries: list[tuple[str, str, object, bool, bool]]) -> None:
        """重建列表内容。entries 为 (主标题, 说明, 附带数据, 是否当前, 是否选中)。

        这里刻意使用 QListWidgetItem 自带的文本而不是 setItemWidget 自定义行控件：
        自定义行控件在"条目高度由 sizeHint 决定"的列表里经常与条目的矩形错位，
        表现为首行被裁掉半截、末行盖住下方控件。纯文本条目由 Qt 自己排版，稳定得多；
        右侧说明改用条目提示文本呈现。
        """
        widget.blockSignals(True)
        widget.clear()
        metrics = widget.fontMetrics()
        # 单行高度 = 文字行高 + 上下留白。夹在合理区间内，避免过大或过小。
        row_height = max(30, min(38, metrics.height() + 14))
        selected_row = -1
        for index, (primary, meta, payload, is_current, is_selected) in enumerate(entries):
            label = f"{primary}    当前" if is_current else primary
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, payload)
            item.setToolTip(f"{primary} · {meta}")
            item.setSizeHint(QSize(0, row_height))
            widget.addItem(item)
            if is_selected:
                selected_row = index
        if selected_row >= 0:
            widget.setCurrentRow(selected_row)
            # 角色列表有几百行，必须主动滚到选中项，否则列表停在顶部。
            widget.scrollToItem(widget.item(selected_row), QListWidget.ScrollHint.PositionAtCenter)
        widget.blockSignals(False)

    def _refresh_lists(self) -> None:
        """按筛选词重建角色列表。"""
        query = self.character_query.text().strip().lower()
        counts: dict[str, int] = {}
        for model in self.models:
            counts[model.character] = counts.get(model.character, 0) + 1
        self.characters = [
            (name, count) for name, count in sorted(counts.items()) if not query or query in name.lower()
        ]

        current_character = self.current_model.character if self.current_model else ""
        entries = [
            (
                name,
                f"{count} 套皮肤",
                name,
                name == current_character,
                name == self._selected_character,
            )
            for name, count in self.characters
        ]
        self._populate(self.character_list, entries)

        # 如果没有选中项，默认落在当前角色（或第一个）上。
        if self.character_list.currentRow() < 0 and entries:
            fallback = next(
                (i for i, entry in enumerate(entries) if entry[0] == current_character), 0
            )
            self.character_list.setCurrentRow(fallback)

        if not self.models:
            self.character_hint.setText("src/assets 中还没有可用的模型文件夹。")
        elif not self.characters:
            self.character_hint.setText("没有匹配的角色。")
        else:
            self.character_hint.setText(
                f"共 {len(self.characters)} 个角色，列表可滚动。"
                "选中后点右下角「确认更换」切到该角色的默认服装。"
            )

        self._on_character_selected()
        self._update_summary()

    def _on_character_selected(self, *_args) -> None:
        item = self.character_list.currentItem()
        self._selected_character = item.data(Qt.ItemDataRole.UserRole) if item else ""
        self._refresh_skin_list()

    def _refresh_skin_list(self) -> None:
        """按当前角色重建皮肤列表（可再按名称筛选）。"""
        skins = [m for m in self.models if m.character == self._selected_character]
        query = self.skin_query.text().strip().lower()

        # 同一个角色下可能出现重名皮肤（例如「默认服装」对应多个资源目录）。
        # 重名时补上目录名，否则两项完全一样、无法区分。
        name_counts: dict[str, int] = {}
        for model in skins:
            name_counts[model.skin] = name_counts.get(model.skin, 0) + 1
        duplicate_skin_names = {name for name, count in name_counts.items() if count > 1}

        visible = [m for m in skins if not query or query in m.skin.lower()]
        current_skeleton = self.current_model.skeleton if self.current_model else None
        entries = []
        for model in visible:
            primary = model.skin
            if model.skin in duplicate_skin_names:
                primary = f"{model.skin}（{model.directory.name}）"
            entries.append((
                primary,
                model.skeleton.name,
                model.name,
                model.skeleton == current_skeleton,
                model.name == self._selected_skin,
            ))
        self._populate(self.skin_list, entries)

        if not visible:
            self.skin_list.setCurrentRow(-1)
        elif self.skin_list.currentRow() < 0:
            # 换角色时默认指向该角色的默认服装
            fallback = next((i for i, m in enumerate(visible) if m.skin.startswith("默认服装")), 0)
            self.skin_list.setCurrentRow(fallback)
        else:
            # 选中项仍在可见列表里就保留，否则回到默认服装
            still_visible = any(entry[4] for entry in entries)
            if not still_visible:
                fallback = next((i for i, m in enumerate(visible) if m.skin.startswith("默认服装")), 0)
                self.skin_list.setCurrentRow(fallback)

        # 提示行同时显示资源路径：列表下方不再放任何控件，
        # 这样就不会有控件被压到滚动列表的内容上。
        hint = (
            f"当前角色：{self._selected_character or '未知'}，共 {len(skins)} 套皮肤"
            + (f"，显示 {len(visible)} 套" if query else "")
            + (f"（{len(duplicate_skin_names)} 个重名，已用目录名区分）" if duplicate_skin_names else "")
        )
        selected = self._model_by_name(self._selected_skin)
        if selected is not None:
            hint += f"　·　资源：{selected.skeleton.parent.name}/{selected.skeleton.name}"
        self.skin_hint.setText(hint)
        self._on_skin_selected()

    def _on_skin_selected(self, *_args) -> None:
        item = self.skin_list.currentItem()
        self._selected_skin = item.data(Qt.ItemDataRole.UserRole) if item else ""
        # 提示行由 _refresh_skin_list 统一负责，这里只更新底栏。
        self._update_footer()

    def _model_by_name(self, name: str) -> LocalModel | None:
        return next((m for m in self.models if m.name == name), None)

    def _update_footer(self) -> None:
        """底栏显示待确认的目标；标签页 2（偏好）没有待确认项，隐藏底栏。"""
        if self.stack.currentIndex() == 2:
            self.footer.hide()
            return
        if self.stack.currentIndex() == 0:
            # 「更换人物」固定指向该角色的默认服装，避免写成「角色 / 角色/皮肤」
            target = f"{self._selected_character} / 默认服装" if self._selected_character else ""
        else:
            # 「更换皮肤」用「角色 / 皮肤」，与 Vue 版本的确认文案一致
            character = self._selected_character or (
                self.current_model.character if self.current_model else ""
            )
            skin = self._model_by_name(self._selected_skin)
            target = f"{character} / {skin.skin}" if skin else ""
        if target:
            self.footer_target.setText(target)
            self.footer.show()
        else:
            self.footer.hide()

    def _update_summary(self) -> None:
        model = self.current_model
        if model is None:
            self.name_label.setText("未知")
            self.skin_label.setText("—")
            for label in self.fields.values():
                label.setText("—")
            return
        self.name_label.setText(model.character)
        self.skin_label.setText(model.skin)
        self.fields["skin"].setText(model.skin)
        self.fields["atlas"].setText(model.atlas.name)
        self.fields["skeleton"].setText(model.skeleton.name)
        self.fields["type"].setText("本地 Spine 模型")
        self.fields["resource"].setText(f"src/assets/{model.name}")

    # -------------------------------------------------------------------- 交互
    def set_tab(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for position, button in enumerate(self.tab_buttons):
            button.setChecked(position == index)
        self._update_footer()

    def set_display_scale(self, scale: float) -> None:
        """设置大小滑块（会被外部调用，例如「恢复默认大小」）。"""
        clamped = max(DISPLAY_SCALE_MIN, min(DISPLAY_SCALE_MAX, scale))
        ticks = scale_to_ticks(clamped)
        if self.size_slider.value() == ticks:
            self._on_slider_changed(ticks)
        else:
            self.size_slider.setValue(ticks)

    def _sync_preference_controls(self, scale: float) -> None:
        self.size_slider.blockSignals(True)
        self.size_slider.setValue(scale_to_ticks(scale))
        self.size_slider.blockSignals(False)
        self.size_value.setText(f"{scale * 100:.0f}%")

    def _on_slider_changed(self, ticks: int) -> None:
        scale = ticks_to_scale(ticks)
        self.size_value.setText(f"{scale * 100:.0f}%")
        self.size_changed.emit(scale)

    def set_bubble_enabled(self, enabled: bool) -> None:
        """同步气泡开关的选中态（不触发信号，供启动恢复时调用）。"""
        self.bubble_toggle.blockSignals(True)
        self.bubble_toggle.setChecked(enabled)
        self.bubble_toggle.setText("已开启" if enabled else "已关闭")
        self.bubble_toggle.blockSignals(False)

    def _on_bubble_toggled(self, checked: bool) -> None:
        self.bubble_toggle.setText("已开启" if checked else "已关闭")
        self.bubble_toggled.emit(checked)

    def set_ai_config(self, enabled: bool, api_base: str, api_key: str, model: str) -> None:
        """同步 AI 配置表单（不触发信号，供启动恢复时调用）。"""
        for field, value in (
            (self.ai_enabled_check, enabled),
            (self.ai_base_field, api_base),
            (self.ai_key_field, api_key),
            (self.ai_model_field, model),
        ):
            field.blockSignals(True)
            if isinstance(value, bool):
                field.setChecked(value)
            else:
                field.setText(value)
            field.blockSignals(False)

    def _on_ai_changed(self, *args) -> None:
        """任一 AI 配置项变化时广播（主程序负责保存与生效）。"""
        self.ai_config_changed.emit(
            self.ai_enabled_check.isChecked(),
            self.ai_base_field.text(),
            self.ai_key_field.text(),
            self.ai_model_field.text(),
        )

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def acquire_preview(self, bridge: SpineBridge, texture: Path) -> None:
        """把预览切到新的模型实例。

        预览控件在窗口第一次创建时就搭好了；这里只换数据源，
        旧的 bridge 由调用方（pet.py）登记后统一释放。
        """
        if self.preview is not None:
            self.preview.set_model(bridge, texture)
            self._preview_bridge = bridge

    def _confirm_change(self) -> None:
        if self.stack.currentIndex() == 0:
            target = next(
                (m for m in self.models
                 if m.character == self._selected_character and m.skin.startswith("默认服装")),
                None,
            ) or next((m for m in self.models if m.character == self._selected_character), None)
        else:
            target = self._model_by_name(self._selected_skin)
        if target is None:
            self.show_error("找不到选中的本地模型")
            return
        self.confirm_button.setEnabled(False)
        self.confirm_button.setText("修改中")
        try:
            self.on_activate_model(target)
            self.current_model = target
            self.show_error("")
            self._refresh_lists()
        except Exception as error:  # noqa: BLE001 - 要把失败原因展示给用户
            self.show_error(f"更换模型失败：{error}")
        finally:
            self.confirm_button.setEnabled(True)
            self.confirm_button.setText("确认更换")

    # -------------------------------------------------------------------- 生命周期
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        # 设置窗口只隐藏，不销毁：下次打开更快，也避免反复重建 GL 预览。
        self.hide()
        event.ignore()

    def shutdown(self) -> None:
        """真正退出时停止预览定时器（bridge 由 pet.py 统一释放）。"""
        if self.preview is not None:
            self.preview.timer.stop()
