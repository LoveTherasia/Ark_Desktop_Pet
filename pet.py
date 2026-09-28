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
# 桌宠窗口默认按人物自动定尺寸（见下方 PET_CANVAS_MARGIN_PX 与 apply_canvas_size），
# 这两个值只在用 --size 明确指定固定窗口时才会用到。
DEFAULT_WINDOW_WIDTH = 320
DEFAULT_WINDOW_HEIGHT = 400

# 界面文字（想换语言时改这里即可）
APP_TITLE = "ArkPet Python prototype"
MENU_LABEL_SETTINGS = "设置"
MENU_LABEL_CLOSE = "关闭桌宠"

# 窗口背景。桌宠必须全透明，否则人物周围会出现一块色块。
PET_BACKGROUND = (0.0, 0.0, 0.0, 0.0)

# ---- 自动画布 ----
# 窗口比人物实际占的像素大出多少（宽高各自，四周均分 → 每边 PET_CANVAS_MARGIN_PX/2）。
# 单位是**物理像素**（屏幕真实像素），因此在高 DPI 缩放的显示器上留白看起来一致。
# 想让人物周围留白更多就调大，想更贴边就调小（例如 20）。
# 运行时可用 --margin 覆盖，所以这里是可变的模块级变量而不是常量。
PET_CANVAS_MARGIN_PX = 50
# 安全系数：渲染时人物是按 model_bounds（待机动画并集）居中的，而画布尺寸按
# **当前动画**的可见范围算，两者中心略有差异；另外某个姿态也可能比整段并集更靠外。
# 留一点余量避免这几种情况贴边（实测个别动作会正好压到底边）。
# 1.0 = 严格 MARGIN，1.15 = 实际留白比目标多 15%。
PET_CANVAS_SAFETY = 1.15
# 画布上限，避免把「人物大小」拉到很大或在大屏上得到夸张的窗口尺寸。
PET_CANVAS_MAX_WIDTH = 1600
PET_CANVAS_MAX_HEIGHT = 1600

# 自动画布的比例系数 r。推导：
#   自动画布模式下绘制比例由窗口尺寸决定（沿用 FIT_* 那套适配公式）：
#       scale = min(W*0.86/bw, H*0.90/bh) * 0.80      (W/H 为逻辑像素)
#   要让"人物 = 窗口内缩 MARGIN"成立，即
#       W = drawn_w + MARGIN/dpr,  H = drawn_h + MARGIN/dpr
#       且 drawn_w = bw*scale/dpr, drawn_h = bh*scale/dpr
#   把 W、H 代回 min() 的两个分支，令两支恰好相等（这是唯一自洽解），得到
#       scale = 0.80 / (1 - 0.86) * ... 整理后：
#       drawn = 0.688/(1-0.688) * MARGIN ≈ 2.205 * MARGIN   （人物像素 ≈ 2.2 倍留白）
#   所以人物会占据窗口的绝大部分，四周各留 MARGIN/2。留白想更宽松就调大 MARGIN。
def _canvas_ratio() -> float:
    """返回 r = FIT_WIDTH_RATIO * FIT_SCALE。"""
    return FIT_WIDTH_RATIO * FIT_SCALE


PET_CANVAS_RATIO = _canvas_ratio()


class PetWindow(SpineGLView):
    """透明、置顶、无边框的桌宠窗口：渲染在 SpineGLView 里，这里只加交互。"""

    def __init__(
        self,
        bridge: SpineBridge,
        texture_path: Path,
        fit_mode: str = "idle",
        display_scale: float = DEFAULT_DISPLAY_SCALE,
        window_size: tuple[int, int] | None = None,
    ) -> None:
        """window_size 为 None（默认）时窗口按人物自动定尺寸；传入具体尺寸则用固定窗口。"""
        # 必须在调用父类构造之前设好这两个属性：父类 __init__ 里就会调用 refit()，
        # 而 refit() 依赖固定窗口模式并写入 fit_scale_override。若留到之后再设，
        # 那一轮 refit 会读到 SpineGLView 的类属性（None），导致固定窗口下
        # 比例算错、人物被画得很小。
        self.fixed_window_size: tuple[int, int] | None = window_size
        self.fit_scale_override: float | None = None
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
        # 由 main() 注入；右键菜单用它打开设置窗口。
        self.settings: SettingsWindow | None = None
        if window_size is not None:
            # 固定窗口：沿用父类的"按窗口适配"，先按目标尺寸重算一次。
            self.resize(window_size[0], window_size[1])
            self.refit()
            return
        # 自动画布：窗口尺寸由当前动画的可见范围决定。
        self.resize(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        self.apply_canvas_size(reposition=False)

    # ------------------------------------------------------------------ 自适应画布
    def current_animation_bounds(self) -> tuple[float, float, float, float] | None:
        """当前动画整段的可见范围（带缓存）；取不到时退回整体包围盒。"""
        name = self.current_animation
        if name:
            bounds = self.animation_bounds(name)
            if bounds:
                return bounds
        return self.model_bounds

    def canvas_size_for_bounds(self) -> tuple[int, int] | None:
        """按当前动画的可见范围算出让"窗口比人物大 PET_CANVAS_MARGIN_PX"的窗口尺寸。

        单位说明：包围盒与窗口尺寸都是**逻辑像素**（Qt 尺寸），而
        PET_CANVAS_MARGIN_PX 是**物理像素**，这里先折成逻辑像素再参与计算。
        着色器收到逻辑尺寸、OpenGL 视口是物理尺寸，把留白定义在物理像素上，
        "人物比窗口小 MARGIN"在任何 DPI 缩放下都成立。

        关键点：绘制比例由窗口尺寸经适配公式决定，而窗口尺寸又取决于绘制比例，
        是个不动点问题。适配公式里的 min() 让宽高两轴耦合：

            scale = min(W*0.86/bw, H*0.90/bh) * 0.80

        代入 W = bw*scale + m、H = bh*scale + m 后按 scale 迭代，收敛很快
        （压缩系数就是 r = 0.688）。解出来的窗口能让束缚轴恰好留出 m，
        另一轴留得更多，人物不会被裁。
        """
        current = self.current_animation_bounds()
        if not current:
            return None
        _, _, bounds_width, bounds_height = current
        if bounds_width <= 0 or bounds_height <= 0:
            return None
        dpr = self.devicePixelRatioF() or 1.0
        r = PET_CANVAS_RATIO
        # 留白固定为 PET_CANVAS_MARGIN_PX 物理像素（换算成逻辑像素）。
        # 注意不要在这里乘 display_scale：下面解算出的 scale 已经是
        # "含 display_scale 的有效比例"，而 fit_rect_from_bounds 还会再乘一次
        # display_scale，所以这里必须让它等于 base_scale * display_scale 才行。
        margin = PET_CANVAS_MARGIN_PX * PET_CANVAS_SAFETY * self.display_scale / dpr
        if margin <= 0:
            return None
        # 不动点迭代求自洽的绘制比例：窗口 = 人物像素 + 留白。
        scale = margin / min(bounds_width, bounds_height) / (1.0 - r)
        for _ in range(24):
            logical_width = bounds_width * scale + margin
            logical_height = bounds_height * scale + margin
            scale = min(
                (logical_width * FIT_WIDTH_RATIO) / bounds_width,
                (logical_height * FIT_HEIGHT_RATIO) / bounds_height,
            ) * FIT_SCALE
        # 窗口 = 人物像素 + 留白（逻辑像素），并留 1px 余量避免取整后贴边。
        logical_width = bounds_width * scale + margin + 1
        logical_height = bounds_height * scale + margin + 1
        width = max(1, min(round(logical_width), round(PET_CANVAS_MAX_WIDTH / dpr)))
        height = max(1, min(round(logical_height), round(PET_CANVAS_MAX_HEIGHT / dpr)))
        return width, height

    def current_fit_scale(self) -> float:
        """当前窗口下"让窗口恰好比人物大 MARGIN"的绘制比例。

        PET_CANVAS_MARGIN_PX 是物理像素，所以比例与窗口的物理尺寸挂钩；
        窗口尺寸变了就要重算（见 apply_canvas_size）。
        """
        current = self.current_animation_bounds()
        if not current:
            return 0.0
        bounds_width, bounds_height = current[2], current[3]
        if bounds_width <= 0 or bounds_height <= 0:
            return 0.0
        dpr = self.devicePixelRatioF() or 1.0
        r = PET_CANVAS_RATIO
        if r <= 0:
            return 0.0
        margin = PET_CANVAS_MARGIN_PX * PET_CANVAS_SAFETY * self.display_scale / dpr
        if margin <= 0:
            return 0.0
        scale = margin / min(bounds_width, bounds_height) / (1.0 - r)
        for _ in range(24):
            logical_width = bounds_width * scale + margin
            logical_height = bounds_height * scale + margin
            scale = min(
                (logical_width * FIT_WIDTH_RATIO) / bounds_width,
                (logical_height * FIT_HEIGHT_RATIO) / bounds_height,
            ) * FIT_SCALE
        return scale

    def apply_canvas_size(self, reposition: bool = True) -> None:
        """按模型、当前动画与「人物大小」重算窗口尺寸。

        换模型（set_model）、切动画（set_animation）和拖动「人物大小」滑块
        （set_display_scale）后都会调用，所以画布会随人物一起变化。
        用 --size 指定了固定窗口时不生效。
        """
        if self.fixed_window_size is not None:
            return
        target = self.canvas_size_for_bounds()
        if target is None or target == (self.width(), self.height()):
            return
        # 缩放时保持人物脚底不动：窗口高度变了就把窗口整体下移同样的量。
        # 窗口还没显示过时不移动，避免在屏幕左上角乱跳。
        if reposition and self.isVisible():
            bottom = self.y() + self.height()
            self.resize(target[0], target[1])
            self.move(self.x(), bottom - target[1])
        else:
            self.resize(target[0], target[1])
        # 尺寸变了要重算适配矩形；resizeEvent 通常已处理，这里兜底。
        self.refit()

    def refit(self) -> None:
        """重算居中缩放矩形。

        自动画布模式下比例由"当前窗口尺寸"反解（见 current_fit_scale），
        这样无论窗口、动画还是「人物大小」怎么变，人物始终比窗口小 MARGIN。
        因为走的是 scale_override 通道，窗口尺寸与比例不会互相追着放大。

        注意：父类 __init__ 里就会调用本方法，那时 fixed_window_size 还没赋值，
        所以用 getattr 兜底。
        """
        if getattr(self, "fixed_window_size", None) is None:
            # fit_rect_from_bounds 会把这个值再乘一次 display_scale，
            # 而 current_fit_scale() 返回的已经是最终有效比例，这里先除掉。
            display = self.display_scale if self.display_scale > 0 else 1.0
            self.fit_scale_override = self.current_fit_scale() / display
        super().refit()

    # ------------------------------------------------------------------ 画布联动
    def set_display_scale(self, scale: float) -> None:
        """「人物大小」变化时同步重算画布，让人物周围始终只留 MARGIN 的余量。"""
        super().set_display_scale(scale)
        self.apply_canvas_size()

    def set_animation(self, name: str) -> None:
        """切动画后按该动画的可见范围重算画布（各动画胖瘦差别很大）。"""
        super().set_animation(name)
        self.apply_canvas_size()

    def set_model(self, bridge: SpineBridge, texture_path: Path) -> None:
        """换模型后按新模型的包围盒重算画布。"""
        super().set_model(bridge, texture_path)
        self.apply_canvas_size()

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
        default=None,
        help="Use a FIXED window size as WIDTHxHEIGHT instead of the automatic canvas. "
             "By default the window auto-fits the character plus a small margin.",
    )
    parser.add_argument(
        "--margin",
        type=int,
        default=None,
        help="Override PET_CANVAS_MARGIN_PX: how many pixels wider/taller the window is "
             f"than the character (default {PET_CANVAS_MARGIN_PX}).",
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

    # 允许从命令行覆盖画布留白（必须在建窗口前生效）。
    if arguments.margin is not None:
        if arguments.margin < 0:
            print("--margin must be >= 0", file=sys.stderr)
            return 2
        # 用 globals() 赋值而不是 global 语句：本函数上方（--margin 的帮助文本里）
        # 已经引用了这个模块级名字，global 声明会触发 SyntaxError。
        globals()["PET_CANVAS_MARGIN_PX"] = arguments.margin

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
