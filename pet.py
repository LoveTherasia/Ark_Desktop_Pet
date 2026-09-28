"""ArkPet 桌宠的 Python 原型。

职责划分：
  spine_view.py      —— Spine C Runtime 封装、几何/适配计算、可复用的 GL 渲染控件
  settings_window.py —— 独立设置窗口（对齐 Vue 版本的 SettingsPanel.vue）
  pet.py（本文件）  —— 桌宠主窗口、右键菜单、命令行入口

渲染本身不在这个文件里，桌宠主窗口和设置窗口的预览共用 spine_view.SpineGLView。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication, QMenu

from spine_view import (
    APP_ROOT,
    APP_VERSION,
    ASSETS,
    BRIDGE_HASH,
    BRIDGE_SOURCE,
    BYTES_PER_FLOAT,
    BYTES_PER_TRIANGLE,
    DEFAULT_DISPLAY_SCALE,
    DISPLAY_SCALE_MAX,
    DISPLAY_SCALE_MIN,
    DISPLAY_SCALE_STEP,
    FIT_HEIGHT_RATIO,
    FIT_SAMPLE_STEP_SECONDS,
    FIT_SAMPLE_STEPS,
    FIT_SCALE,
    FIT_WIDTH_RATIO,
    FLOATS_PER_TRIANGLE,
    FLOATS_PER_VERTEX,
    FRAME_INTERVAL_MS,
    LIBRARY,
    LocalModel,
    SpineBridge,
    SpineGLView,
    animation_bounds_union,
    bounds_from_triangles,
    discover_models,
    find_sample_skeleton,
    fit_rect_from_bounds,
    pack_triangle_vertices,
    preferred_idle_animation,
    triangle_batches,
    union_bounds,
)
from settings_window import SettingsWindow

# 向后兼容：旧代码（以及 .build 下的工具脚本）通过 pet.ROOT 取仓库根目录。
ROOT = APP_ROOT


# =============================================================================
# 可调参数
# =============================================================================
# 桌宠窗口初始尺寸（逻辑像素）。窗口是无边框透明的，人物“占多大地方”由它决定：
#   - 调大 → 透明区域也变大，会挡住更多桌面点击
#   - 调小 → 更紧凑，人物也会跟着变小
# 运行时可用设置窗口里的「人物大小」缩放人物，但不会改变窗口本身。
# 命令行会覆盖这里的值：--size 360x450
DEFAULT_WINDOW_WIDTH = 320
DEFAULT_WINDOW_HEIGHT = 400

# 界面文字（想换语言时改这里即可）
APP_TITLE = "ArkPet Python prototype"
MENU_LABEL_SETTINGS = "设置"
MENU_LABEL_CLOSE = "关闭桌宠"

# 窗口背景。桌宠必须全透明，否则人物周围会出现一块色块。
PET_BACKGROUND = (0.0, 0.0, 0.0, 0.0)


class PetWindow(SpineGLView):
    """透明、置顶、无边框的桌宠窗口：渲染在 SpineGLView 里，这里只加交互。"""

    def __init__(
        self,
        bridge: SpineBridge,
        texture_path: Path,
        fit_mode: str = "idle",
        display_scale: float = DEFAULT_DISPLAY_SCALE,
        window_size: tuple[int, int] = (DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT),
    ) -> None:
        super().__init__(
            bridge,
            texture_path,
            fit_mode=fit_mode,
            display_scale=display_scale,
            interactive=True,
            background=PET_BACKGROUND,
        )
        self.setWindowTitle(APP_TITLE)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        # 窗口尺寸见文件顶部的“可调参数”：DEFAULT_WINDOW_WIDTH / DEFAULT_WINDOW_HEIGHT。
        self.resize(window_size[0], window_size[1])
        # 由 main() 注入；右键菜单用它打开设置窗口。
        self.settings: SettingsWindow | None = None

    # ------------------------------------------------------------------ 右键菜单
    def build_context_menu(self) -> QMenu:
        """桌宠右键菜单，条目与 Vue 版本保持一致：设置 + 分隔线 + 关闭桌宠。

        Vue 版本（electron/main.ts 的 show-context-menu）只有两项：
            '设置' → 打开独立的设置窗口
            '关闭桌宠' → app.quit()
        「设置」现在也打开本原型的独立设置窗口（settings_window.py），
        人物大小等调节项都在那个窗口里，不再塞进右键菜单。
        """
        menu = QMenu(self)
        menu.addAction(MENU_LABEL_SETTINGS, self.open_settings)
        menu.addSeparator()
        menu.addAction(MENU_LABEL_CLOSE, QApplication.quit)
        return menu

    def show_context_menu(self, position) -> None:
        self.build_context_menu().exec(position)

    def open_settings(self) -> None:
        if self.settings is not None:
            self.settings.show()
            self.settings.raise_()
            self.settings.activateWindow()

    def mousePressEvent(self, event) -> None:
        # 右键弹出菜单；左键拖动窗口由父类处理。
        if event.button() == Qt.MouseButton.RightButton:
            self.show_context_menu(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)


def parse_size_option(value: str) -> tuple[int, int]:
    """解析 --size 参数，格式为 宽x高（例如 320x400）。"""
    width, separator, height = value.lower().partition("x")
    if not separator or not width.strip().isdigit() or not height.strip().isdigit():
        raise argparse.ArgumentTypeError(f"expected WIDTHxHEIGHT, got {value!r}")
    return int(width), int(height)


def resolve_skeleton(value: str | None) -> Path:
    """验证骨骼、图集与贴图同名配套，防止加载一半才报资源错误。"""
    skeleton = Path(value) if value else find_sample_skeleton()
    if not skeleton.is_absolute():
        # 相对路径按仓库根目录解析，也兼容直接写 "src/assets/..." 的用法。
        candidate = APP_ROOT / skeleton
        skeleton = candidate if candidate.exists() else ASSETS / skeleton
    skeleton = skeleton.resolve()
    if skeleton.suffix.lower() != ".skel":
        raise ValueError(f"Expected a .skel file: {skeleton}")
    if not skeleton.is_file():
        raise FileNotFoundError(f"Spine skeleton does not exist: {skeleton}")
    if not skeleton.with_suffix(".atlas").is_file():
        raise FileNotFoundError(f"Matching atlas does not exist: {skeleton.with_suffix('.atlas')}")
    texture = skeleton.with_suffix(".png")
    if not texture.is_file():
        raise FileNotFoundError(f"Matching texture does not exist: {texture}")
    return skeleton


def main() -> int:
    parser = argparse.ArgumentParser(description="ArkPet 桌宠（Python + PySide6 + Spine C Runtime）")
    parser.add_argument("--version", action="version", version=f"ArkPet {APP_VERSION}")
    parser.add_argument("--skeleton", help="Path to a local Spine .skel file with matching .atlas and .png files")
    parser.add_argument(
        "--fit",
        choices=("idle", "all"),
        default="idle",
        help="idle: scale by idle animations only (larger character, default). "
             "all: union of every animation (never clipped, but smaller).",
    )
    parser.add_argument(
        "--size",
        type=parse_size_option,
        default=(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT),
        help="Window size in logical pixels, as WIDTHxHEIGHT "
             f"(default {DEFAULT_WINDOW_WIDTH}x{DEFAULT_WINDOW_HEIGHT}). "
             "Overrides DEFAULT_WINDOW_WIDTH / DEFAULT_WINDOW_HEIGHT.",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=DEFAULT_DISPLAY_SCALE,
        help="Character display scale inside the window "
             f"(default {DEFAULT_DISPLAY_SCALE}). Also adjustable in the settings window.",
    )
    parser.add_argument(
        "--animation",
        default=None,
        help="Animation to start with. Defaults to the first idle animation the model has. "
             "The right-click menu intentionally exposes no animation list, matching the Vue app.",
    )
    arguments = parser.parse_args()

    if not LIBRARY.is_file():
        print("Spine bridge DLL is missing. Build it first with: python build_runtime.py", file=sys.stderr)
        return 2

    bridge: SpineBridge | None = None
    try:
        skeleton = resolve_skeleton(arguments.skeleton)
        bridge = SpineBridge(LIBRARY, skeleton, skeleton.with_suffix(".atlas"))
        # 在创建 QApplication 前指定透明 OpenGL 表面格式，确保窗口上下文带 Alpha 通道。
        surface_format = QSurfaceFormat()
        surface_format.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
        surface_format.setVersion(2, 1)
        surface_format.setAlphaBufferSize(8)
        QSurfaceFormat.setDefaultFormat(surface_format)
        application = QApplication(sys.argv)

        window = PetWindow(
            bridge,
            skeleton.with_suffix(".png"),
            fit_mode=arguments.fit,
            display_scale=arguments.scale,
            window_size=arguments.size,
        )
        # --animation 只在启动时指定一次；之后不再通过菜单切换，与 Vue 版本一致。
        if arguments.animation:
            bridge.require_animation(arguments.animation)
            bridge.update(0)
            window.current_animation = arguments.animation
            window.refit()

        # 扫描 src/assets 里所有可用模型，供设置窗口「更换人物 / 更换皮肤」使用。
        try:
            models = discover_models()
        except OSError as error:
            print(f"[ArkPet Python] model scan failed: {error}", file=sys.stderr)
            models = []
        install_settings(window, bridge, skeleton, models)

        window.show()
        window.raise_()
        return application.exec()
    except (OSError, RuntimeError, ValueError) as error:
        if bridge:
            bridge.close()
        print(f"Python Spine prototype failed: {error}", file=sys.stderr)
        return 1


def install_settings(
    window: PetWindow,
    bridge: SpineBridge,
    skeleton: Path,
    models: list[LocalModel],
) -> SettingsWindow:
    """构建设置窗口，并把「打开设置 / 切换模型 / 人物大小」三处接起来。"""
    current = next((m for m in models if m.skeleton == skeleton), None)

    # 预览需要独立实例：它要自己推进动画，与主窗口共享 bridge 会互相抢播放头。
    preview_bridge: SpineBridge | None = None
    try:
        preview_bridge = SpineBridge(LIBRARY, skeleton, skeleton.with_suffix(".atlas"))
    except (OSError, RuntimeError) as error:
        print(f"[ArkPet Python] preview unavailable: {error}", file=sys.stderr)

    # 预览实例登记在这里，退出时统一释放。切换时不能立刻 close，
    # 因为 GL 可能还在用它绘制；但也不能无限累积，所以只保留最近两个。
    preview_bridges: list[SpineBridge] = [preview_bridge] if preview_bridge else []
    active_bridge = bridge
    MAX_RETAINED_PREVIEWS = 2

    def activate_model(model: LocalModel) -> None:
        nonlocal active_bridge, preview_bridge
        new_bridge = SpineBridge(LIBRARY, model.skeleton, model.atlas)
        try:
            window.set_model(new_bridge, model.texture)
        except Exception:
            new_bridge.close()
            raise
        old_bridge = active_bridge
        active_bridge = new_bridge

        # 预览同步切到新模型。
        try:
            new_preview = SpineBridge(LIBRARY, model.skeleton, model.atlas)
        except (OSError, RuntimeError) as error:
            print(f"[ArkPet Python] preview reload failed: {error}", file=sys.stderr)
        else:
            preview_bridges.append(new_preview)
            preview_bridge = new_preview
            settings.acquire_preview(new_preview, model.texture)
            # 预览已经切走了，可以安全回收更早的实例。
            while len(preview_bridges) > MAX_RETAINED_PREVIEWS:
                stale = preview_bridges.pop(0)
                try:
                    stale.close()
                except Exception:  # noqa: BLE001 - 回收失败不影响切换
                    pass

        # 旧的主窗口实例现在可以安全释放。
        old_bridge.close()

    settings = SettingsWindow(
        models=models,
        current=current,
        current_scale=window.display_scale,
        preview_bridge=preview_bridge,
        preview_texture=skeleton.with_suffix(".png"),
        on_activate_model=activate_model,
    )
    settings.size_changed.connect(window.set_display_scale)
    # 设置窗口只是隐藏，真正销毁（app 退出）时才回收预览实例。
    settings.destroyed.connect(lambda _obj=None: release_bridges(preview_bridges))
    window.settings = settings
    return settings


def release_bridges(bridges: list[SpineBridge]) -> None:
    """释放一组 bridge 实例，单个失败不影响其它。"""
    while bridges:
        item = bridges.pop()
        try:
            item.close()
        except Exception:  # noqa: BLE001 - 释放失败不应影响退出流程
            pass


if __name__ == "__main__":
    raise SystemExit(main())
