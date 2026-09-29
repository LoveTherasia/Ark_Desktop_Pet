"""可复用的 Spine OpenGL 渲染控件与模型发现工具。

这个模块被两处使用：
  - pet.py 的桌宠主窗口（透明、可拖动、带右键菜单）
  - pet.py 的设置窗口预览（固定尺寸、不交互）

把渲染逻辑集中在这里，避免预览再抄一份着色器和批绘制代码。
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import struct
import sys
import time
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QImage, QSurfaceFormat, QVector2D, QVector4D
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLTexture,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget


# 版本号。发布时同步更新 package.json 与 CHANGELOG.md。
APP_VERSION = "0.7.2"

# 目录约定：这些文件都位于仓库根目录，模型资源在 <root>/src/assets，
# 编译产物在 <root>/.build，Spine C Runtime 源码在 <root>/vendor。
APP_ROOT = Path(__file__).resolve().parent
ROOT = APP_ROOT
BRIDGE_SOURCE = APP_ROOT / "spine_bridge.cpp"
BRIDGE_HASH = hashlib.sha256(BRIDGE_SOURCE.read_bytes()).hexdigest()[:12]
LIBRARY = APP_ROOT / ".build" / f"arkpet_spine_{BRIDGE_HASH}.dll"
ASSETS = APP_ROOT / "src" / "assets"


# =============================================================================
# 可调参数
# =============================================================================
# 动画定时器间隔（毫秒）。16 ≈ 60 FPS。调大可以省一点 CPU，代价是动画不那么顺。
FRAME_INTERVAL_MS = 16

# 人物相对窗口的显示比例。1.0 = 铺满可用区域，>1 会顶到边缘，<1 会留更多空白。
DEFAULT_DISPLAY_SCALE = 1.0
DISPLAY_SCALE_STEP = 0.15
DISPLAY_SCALE_MIN = 0.5
DISPLAY_SCALE_MAX = 2.0

# 自适应缩放：可用区域占窗口的比例，再乘一个安全系数，避免贴边。
FIT_WIDTH_RATIO = 0.86
FIT_HEIGHT_RATIO = 0.90
FIT_SCALE = 0.80
# 采样整段动画时的步长与步数；每步 1/60 秒。该开销只在启动时发生一次。
FIT_SAMPLE_STEP_SECONDS = 1.0 / 60.0
FIT_SAMPLE_STEPS = 120

# 原生桥接层每个顶点输出位置、UV、RGBA；每个三角形末尾另带一个混合模式值。
FLOATS_PER_VERTEX = 8
FLOATS_PER_TRIANGLE = 3 * FLOATS_PER_VERTEX + 1
BYTES_PER_FLOAT = ctypes.sizeof(ctypes.c_float)
BYTES_PER_TRIANGLE = FLOATS_PER_TRIANGLE * BYTES_PER_FLOAT


class SpineBridge:
    """Spine C Runtime 的 ctypes 封装：解析骨骼/图集、推进动画、导出顶点流。"""

    def __init__(self, library_path: Path, skeleton_path: Path, atlas_path: Path) -> None:
        self.library = ctypes.CDLL(str(library_path))
        # Declare the C ABI explicitly so ctypes preserves native pointers and argument widths.
        self.library.pet_create.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        self.library.pet_create.restype = ctypes.c_void_p
        self.library.pet_destroy.argtypes = [ctypes.c_void_p]
        self.library.pet_last_error.restype = ctypes.c_char_p
        self.library.pet_animation_count.argtypes = [ctypes.c_void_p]
        self.library.pet_animation_count.restype = ctypes.c_int
        self.library.pet_animation_name.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.library.pet_animation_name.restype = ctypes.c_char_p
        self.library.pet_set_animation.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        self.library.pet_set_animation.restype = ctypes.c_int
        self.library.pet_update.argtypes = [ctypes.c_void_p, ctypes.c_float]
        self.library.pet_triangle_float_count.argtypes = [ctypes.c_void_p]
        self.library.pet_triangle_float_count.restype = ctypes.c_int
        self.library.pet_clipping_attachment_count.argtypes = [ctypes.c_void_p]
        self.library.pet_clipping_attachment_count.restype = ctypes.c_int
        self.library.pet_copy_triangles.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int,
        ]
        self.library.pet_copy_triangles.restype = ctypes.c_int

        # 将骨骼与图集路径交给 Spine C Runtime，返回的句柄由 close() 负责释放。
        self.handle = self.library.pet_create(os.fsencode(skeleton_path), os.fsencode(atlas_path))
        if not self.handle:
            message = self.library.pet_last_error()
            raise RuntimeError(message.decode("utf-8", errors="replace") if message else "Spine model load failed")

        self.animations = [
            self.library.pet_animation_name(self.handle, index).decode("utf-8", errors="replace")
            for index in range(self.library.pet_animation_count(self.handle))
        ]

    def set_animation(self, name: str, loop: bool = True) -> bool:
        """切换动画，返回是否成功。"""
        return bool(self.library.pet_set_animation(self.handle, name.encode("utf-8"), int(loop)))

    def require_animation(self, name: str, loop: bool = True) -> None:
        if not self.set_animation(name, loop):
            raise RuntimeError(f"Could not start Spine animation: {name}")

    def update(self, delta: float) -> None:
        self.library.pet_update(self.handle, ctypes.c_float(delta))

    def triangles(self) -> bytes:
        # 以原始字节交给 GPU，避免把每个顶点逐项转换成 Python 浮点数。
        count = self.library.pet_triangle_float_count(self.handle)
        if count <= 0:
            return b""
        buffer = (ctypes.c_float * count)()
        copied = self.library.pet_copy_triangles(self.handle, buffer, count)
        return ctypes.string_at(buffer, copied * BYTES_PER_FLOAT)

    def clipping_attachment_count(self) -> int:
        return self.library.pet_clipping_attachment_count(self.handle)

    def close(self) -> None:
        if self.handle:
            self.library.pet_destroy(self.handle)
            self.handle = None


# --------------------------------------------------------------------- 模型发现
class LocalModel:
    """src/assets/<角色>/<皮肤> 下一个可用的 Spine 模型。"""

    __slots__ = ("name", "character", "skin", "skeleton", "atlas", "texture", "directory")

    def __init__(self, directory: Path, skeleton: Path) -> None:
        self.directory = directory
        self.character = directory.parent.name
        self.skin = directory.name
        self.name = f"{self.character}/{self.skin}"
        self.skeleton = skeleton
        self.atlas = skeleton.with_suffix(".atlas")
        self.texture = skeleton.with_suffix(".png")

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<LocalModel {self.name}>"


def discover_models(assets: Path | None = None) -> list[LocalModel]:
    """扫描 src/assets，返回所有 .skel/.atlas/.png 齐备的模型。

    目录结构沿用 Vue 版本：assets/<角色>/<皮肤>/<资源>。同名三件套缺一不可，
    否则加载到一半才会报错。
    """
    assets = assets or ASSETS
    models: list[LocalModel] = []
    if not assets.is_dir():
        return models
    for character_directory in sorted(assets.iterdir(), key=lambda item: item.name):
        if not character_directory.is_dir():
            continue
        for skin_directory in sorted(character_directory.iterdir(), key=lambda item: item.name):
            if not skin_directory.is_dir():
                continue
            for skeleton in sorted(skin_directory.glob("*.skel")):
                atlas = skeleton.with_suffix(".atlas")
                texture = skeleton.with_suffix(".png")
                if atlas.is_file() and texture.is_file():
                    models.append(LocalModel(skin_directory, skeleton))
                    break
    return models


def find_sample_skeleton() -> Path:
    """挑一个默认模型：优先仓库里那个三丽鸥阿米娅/艾雅法拉模型。"""
    assets = ASSETS
    current_model = next(assets.rglob("build_char_180_amgoat_sanrio_2.skel"), None)
    if current_model and current_model.with_suffix(".atlas").is_file() and current_model.with_suffix(".png").is_file():
        return current_model
    for skeleton in sorted(assets.rglob("*.skel")):
        if skeleton.with_suffix(".atlas").is_file() and skeleton.with_suffix(".png").is_file():
            return skeleton
    raise FileNotFoundError(f"No local .skel/.atlas/.png model triplet found under {assets}")


# ------------------------------------------------------------------ 几何与适配
def bounds_from_triangles(values: bytes, visible_only: bool = True) -> tuple[float, float, float, float] | None:
    """从当前姿态的顶点计算包围盒，用于让不同体型的模型自动适配画布。

    默认只统计“画得出来”的三角形。Spine 工程里普遍存在 alpha 为 0 或缩放为 0 的
    附件（隐藏配件、占位图形、已停用的分支），它们仍然会出现在顶点流里。若把它们
    算进包围盒，包围盒会被拉宽、中心会整体偏移，自适应缩放就会同时算错比例和位置：
    人物偏小且偏离画布中心。渲染时这些三角形本来就不可见，因此排除它们不会改变画面。
    """
    left = bottom = float("inf")
    right = top = float("-inf")
    for start in range(0, len(values) - BYTES_PER_TRIANGLE + 1, BYTES_PER_TRIANGLE):
        if visible_only and struct.unpack_from("<f", values, start + 7 * BYTES_PER_FLOAT)[0] <= 0.0:
            continue
        triangle_left = triangle_bottom = float("inf")
        triangle_right = triangle_top = float("-inf")
        for vertex in range(3):
            offset = start + vertex * FLOATS_PER_VERTEX * BYTES_PER_FLOAT
            x, y = struct.unpack_from("<ff", values, offset)
            triangle_left, triangle_right = min(triangle_left, x), max(triangle_right, x)
            triangle_bottom, triangle_top = min(triangle_bottom, y), max(triangle_top, y)
        # 完全退化的三角形（三个顶点重合）不会画出任何像素，同样不该参与包围盒。
        if triangle_right <= triangle_left and triangle_top <= triangle_bottom:
            continue
        left, right = min(left, triangle_left), max(right, triangle_right)
        bottom, top = min(bottom, triangle_bottom), max(top, triangle_top)
    if right < left or top < bottom:
        return None
    return left, bottom, right - left, top - bottom


def union_bounds(
    first: tuple[float, float, float, float] | None,
    second: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    """合并两个包围盒，用于把多个动画姿态的可见范围取并集。"""
    if first is None:
        return second
    if second is None:
        return first
    left = min(first[0], second[0])
    bottom = min(first[1], second[1])
    right = max(first[0] + first[2], second[0] + second[2])
    top = max(first[1] + first[3], second[1] + second[3])
    return left, bottom, right - left, top - bottom


def triangle_batches(values: bytes) -> list[tuple[int, int, int]]:
    """Return consecutive (first triangle, count, blend mode) runs without reordering Spine slots."""
    triangle_count = len(values) // BYTES_PER_TRIANGLE
    batches: list[tuple[int, int, int]] = []
    for triangle in range(triangle_count):
        offset = triangle * BYTES_PER_TRIANGLE + BYTES_PER_TRIANGLE - BYTES_PER_FLOAT
        mode = round(struct.unpack_from("<f", values, offset)[0])
        if batches and batches[-1][2] == mode:
            first, count, _ = batches[-1]
            batches[-1] = (first, count + 1, mode)
        else:
            batches.append((triangle, 1, mode))
    return batches


def pack_triangle_vertices(values: bytes) -> tuple[bytes, list[tuple[int, int, int]]]:
    # Remove the per-triangle blend tag so OpenGL can consume a compact, regular vertex stride.
    vertices = bytearray()
    for triangle in range(len(values) // BYTES_PER_TRIANGLE):
        start = triangle * BYTES_PER_TRIANGLE
        vertices.extend(values[start : start + BYTES_PER_TRIANGLE - BYTES_PER_FLOAT])
    batches = [(first * 3, count * 3, mode) for first, count, mode in triangle_batches(values)]
    return bytes(vertices), batches


def _point_in_triangle(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> bool:
    """叉积法判断点是否在三角形内（含边界）。"""
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    d1 = cross(point, a, b)
    d2 = cross(point, b, c)
    d3 = cross(point, c, a)
    has_neg = d1 < 0 or d2 < 0 or d3 < 0
    has_pos = d1 > 0 or d2 > 0 or d3 > 0
    return not (has_neg and has_pos)


def fit_rect_from_bounds(
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    display_scale: float = 1.0,
    scale_override: float | None = None,
) -> tuple[float, float, float] | None:
    """把模型包围盒映射为画布内的居中缩放矩形，返回 (center_x, center_y, scale)。

    保持单一实现，避免初始化和重新适配两处各算一套而互相漂移。
    display_scale 是用户可调的显示比例（设置窗口里的「大小」），
    它只改变人物在窗口里的大小，不改变窗口本身。
    scale_override 非空时直接使用该比例，跳过"按画布适配"的计算——
    桌宠的自动画布模式用它来精确控制人物大小（见 pet.PetWindow）。
    """
    bounds_x, bounds_y, bounds_width, bounds_height = bounds
    if bounds_width <= 0 or bounds_height <= 0 or width <= 0 or height <= 0:
        return None
    if scale_override is not None:
        scale = scale_override * display_scale
    else:
        scale = min(
            (width * FIT_WIDTH_RATIO) / bounds_width,
            (height * FIT_HEIGHT_RATIO) / bounds_height,
        ) * FIT_SCALE * display_scale
    if scale <= 0:
        return None
    return (
        bounds_x + bounds_width / 2,
        bounds_y + bounds_height / 2,
        scale,
    )


def animation_bounds_union(
    bridge: SpineBridge,
    names: list[str],
) -> tuple[float, float, float, float] | None:
    """对给定动画整段采样，返回“画得出来”的几何范围并集。

    只按某一帧定标会随动画摆动而抖动，只按某一帧固定又会裁掉后续动作，因此对整段
    采样取并集。采样结束后播放头会停在被采样的地方，由调用方负责复位。
    """
    overall: tuple[float, float, float, float] | None = None
    for name in names:
        if not bridge.set_animation(name, loop=True):
            continue
        bridge.update(0.0)
        animation_bounds: tuple[float, float, float, float] | None = None
        for _ in range(FIT_SAMPLE_STEPS):
            bridge.update(FIT_SAMPLE_STEP_SECONDS)
            animation_bounds = union_bounds(
                animation_bounds, bounds_from_triangles(bridge.triangles())
            )
        overall = union_bounds(overall, animation_bounds)
    return overall


def preferred_idle_animation(animations: list[str]) -> str | None:
    """在模型提供的动画里挑一个待机动作。"""
    for name in ("Relax", "Sit", "Move", "Default", "Sleep"):
        if name in animations:
            return name
    return animations[0] if animations else None


# ------------------------------------------------------------------- GL 渲染控件
class SpineGLView(QOpenGLWidget):
    """把 Spine 顶点流画到透明背景上的 OpenGL 控件。

    只负责“画”和“适配”；拖动窗口、右键菜单等交互由子类或外部补上，
    这样设置窗口的预览可以直接复用它。
    """

    def __init__(
        self,
        bridge: SpineBridge,
        texture_path: Path,
        fit_mode: str = "idle",
        display_scale: float = DEFAULT_DISPLAY_SCALE,
        interactive: bool = True,
        background: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
    ) -> None:
        super().__init__()
        self.bridge = bridge
        self.texture_path = Path(texture_path)
        # "idle"：只用待机类动作定标（默认，人物更大）；"all"：所有动画取并集，任何动作都不越界。
        self.fit_mode = fit_mode
        self.display_scale = display_scale
        self.interactive = interactive
        self.background = background
        self.facing_left = False
        self.idle_animations: list[str] = []
        # 非空时直接使用该比例绘制，跳过"按画布适配"；桌宠的自动画布用它固定人物大小。
        self.fit_scale_override: float | None = None
        # 每个动画的可见范围并集缓存（键为动画名），切动画时避免重复采样。
        self._animation_bounds_cache: dict[str, tuple[float, float, float, float] | None] = {}

        texture_image = QImage(str(self.texture_path)).convertToFormat(QImage.Format.Format_RGBA8888)
        if texture_image.isNull():
            raise RuntimeError(f"Could not load Spine texture: {self.texture_path}")
        self.texture_image = texture_image

        # The alpha channel is needed for the window to stay see-through outside the model.
        surface_format = QSurfaceFormat()
        surface_format.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
        surface_format.setVersion(2, 1)
        surface_format.setProfile(QSurfaceFormat.OpenGLContextProfile.NoProfile)
        surface_format.setAlphaBufferSize(8)
        self.setFormat(surface_format)
        if interactive:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)

        self.drag_position: QPoint | None = None
        self.last_frame = time.perf_counter()
        self.texture: QOpenGLTexture | None = None
        self.vertex_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self.program: QOpenGLShaderProgram | None = None
        self.attribute_locations: tuple[int, int, int] | None = None
        self.uniform_locations: tuple[int, int, int] | None = None
        self.render_error = ""
        # (center_x, center_y, scale)：由 model_bounds 与控件尺寸推导，resize 时刷新。
        self.model_bounds: tuple[float, float, float, float] | None = None
        self.fit_center_scale: tuple[float, float, float] | None = None

        idle = preferred_idle_animation(bridge.animations)
        if idle:
            bridge.require_animation(idle)
            bridge.update(0)
        self.current_animation = idle
        self.idle_animations = [name for name in ("Relax", "Move", "Default") if name in bridge.animations]
        self.model_bounds = self.measure_fit_bounds(bridge)
        if not self.model_bounds:
            # 没有可用动画时退回到当前姿态，至少保证画面可见。
            self.model_bounds = bounds_from_triangles(bridge.triangles())
        if not self.model_bounds:
            raise RuntimeError("Spine model has no supported region or mesh attachments")
        # 采样把播放头留在了最后一个动画上，复位回待机动作。
        if idle:
            bridge.require_animation(idle)
            bridge.update(0)
        self.refit()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance_frame)
        self.timer.start(FRAME_INTERVAL_MS)

    # ------------------------------------------------------------ 适配与动画状态
    def measure_fit_bounds(self, bridge: SpineBridge) -> tuple[float, float, float, float] | None:
        """决定用哪些动画的可见范围来定标。

        默认只用待机类动作（Relax/Move/Default）：这些才是平时看得见的姿态，用它们
        定标人物大小最自然。个别动作（例如带远程特效的 Special）可见范围远大于其他
        动作，一旦参与定标就会把人物整体压小；它们播放时可能有小部分伸出画布，这是
        刻意的取舍。--fit all 则对所有动画取并集，保证任何动作都不越界。
        """
        names = list(bridge.animations) if self.fit_mode == "all" else list(self.idle_animations)
        if not names:
            names = list(bridge.animations)
        if not names:
            return None
        return animation_bounds_union(bridge, names)

    def animation_bounds(self, name: str) -> tuple[float, float, float, float] | None:
        """单个动画整段采样的可见范围并集，结果会缓存。

        桌宠的自动画布按"当前动画"定尺寸，而各个动画的可见范围差别很大
        （例如 Relax 286x472、Move 314x503、Sleep 466x233），
        缓存后切动画只多花一次采样。
        """
        if name in self._animation_bounds_cache:
            return self._animation_bounds_cache[name]
        bounds = animation_bounds_union(self.bridge, [name])
        self._animation_bounds_cache[name] = bounds
        return bounds

    def refit(self) -> None:
        """按当前控件尺寸和显示比例重新计算居中缩放矩形。

        控件改尺寸或用户调整「大小」后都必须重算，否则人物仍按旧参数定标，
        会偏小或越界。
        """
        if not self.model_bounds:
            self.fit_center_scale = None
            return
        self.fit_center_scale = fit_rect_from_bounds(
            self.model_bounds,
            self.width(),
            self.height(),
            self.display_scale,
            self.fit_scale_override,
        )

    def set_display_scale(self, scale: float) -> None:
        """调整人物在画布中的显示比例（设置窗口里的「大小」）。"""
        self.display_scale = max(DISPLAY_SCALE_MIN, min(DISPLAY_SCALE_MAX, scale))
        self.refit()
        self.update()

    def set_animation(self, name: str) -> None:
        self.bridge.set_animation(name)
        self.current_animation = name

    def set_facing_left(self, facing_left: bool) -> None:
        self.facing_left = facing_left
        self.update()

    def hit_test(self, position: QPoint) -> bool:
        """判断控件坐标 position 是否落在人物**可见几何**上（用于光标反馈与点击判定）。

        与渲染用同一套变换（fit_center_scale + facing_left），把顶点流里的可见
        三角形映射到控件坐标后做点在三角形内测试。透明区域不算命中——
        这正是"只统计可见几何"策略的另一个受益点。
        """
        if self.fit_center_scale is None:
            return False
        center_x, center_y, scale = self.fit_center_scale
        facing = -1.0 if self.facing_left else 1.0
        px, py = position.x(), position.y()
        # 控件坐标 → 模型坐标（顶点着色器的逆变换；y 轴方向一致，无需翻转）。
        mx = px / (scale * facing) + center_x if scale > 0 else 0.0
        my = py / scale + center_y if scale > 0 else 0.0

        values = self.bridge.triangles()
        triangle_count = len(values) // BYTES_PER_TRIANGLE
        for triangle in range(triangle_count):
            start = triangle * BYTES_PER_TRIANGLE
            # 与 bounds_from_triangles 一致：任一顶点 alpha <= 0 的三角形不可见，跳过。
            vertices = []
            visible = True
            for v in range(3):
                offset = start + v * FLOATS_PER_VERTEX * BYTES_PER_FLOAT
                x, y, _, _, _, _, a, _ = struct.unpack_from("<ffffffff", values, offset)
                if a <= 0.0:
                    visible = False
                    break
                vertices.append((x, y))
            if not visible:
                continue
            if _point_in_triangle((mx, my), vertices[0], vertices[1], vertices[2]):
                return True
        return False

    def set_model(self, bridge: SpineBridge, texture_path: Path) -> None:
        """换一个模型。GL 纹理必须在当前上下文中销毁后再重建。"""
        self.timer.stop()
        try:
            if self.texture is not None and self.context() and self.context().isValid():
                self.makeCurrent()
                self.texture.destroy()
                self.doneCurrent()
            self.texture = None

            self.bridge = bridge
            self.texture_path = Path(texture_path)
            texture_image = QImage(str(self.texture_path)).convertToFormat(QImage.Format.Format_RGBA8888)
            if texture_image.isNull():
                raise RuntimeError(f"Could not load Spine texture: {self.texture_path}")
            self.texture_image = texture_image

            idle = preferred_idle_animation(bridge.animations)
            if idle:
                bridge.require_animation(idle)
                bridge.update(0)
            self.current_animation = idle
            self.idle_animations = [n for n in ("Relax", "Move", "Default") if n in bridge.animations]
            # 换了模型，之前缓存的各动画可见范围全部失效。
            self._animation_bounds_cache.clear()
            self.model_bounds = self.measure_fit_bounds(bridge) or bounds_from_triangles(bridge.triangles())
            if not self.model_bounds:
                raise RuntimeError("Spine model has no supported region or mesh attachments")
            if idle:
                bridge.require_animation(idle)
                bridge.update(0)
            self.refit()
            # 纹理在 initializeGL 里创建；这里若已有上下文需要立刻补上。
            if self.context() and self.context().isValid():
                self.makeCurrent()
                self.texture = QOpenGLTexture(self.texture_image)
                self.texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)
                self.texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
                self.texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
                self.doneCurrent()
        finally:
            self.timer.start(FRAME_INTERVAL_MS)
        self.update()

    # ------------------------------------------------------------------ GL 生命周期
    def initializeGL(self) -> None:
        functions = self.context().functions()
        functions.initializeOpenGLFunctions()
        # 背景色：默认全透明，桌宠窗口之外的桌面才能透出来。
        functions.glClearColor(*self.background)
        functions.glEnable(0x0BE2)

        program = QOpenGLShaderProgram(self)
        vertex_shader = """
            #version 120
            attribute vec2 a_position;
            attribute vec2 a_uv;
            attribute vec4 a_color;
            uniform vec2 u_viewport;
            uniform vec4 u_transform;
            varying vec2 v_uv;
            varying vec4 v_color;
            void main() {
                // Spine 坐标原点移到模型中心，再映射到 OpenGL 的标准化设备坐标。
                float x = (a_position.x - u_transform.x) * u_transform.z * u_transform.w;
                float y = (a_position.y - u_transform.y) * u_transform.z;
                gl_Position = vec4(x * 2.0 / u_viewport.x, y * 2.0 / u_viewport.y, 0.0, 1.0);
                v_uv = a_uv;
                v_color = a_color;
            }
        """
        fragment_shader = """
            #version 120
            uniform sampler2D u_texture;
            varying vec2 v_uv;
            varying vec4 v_color;
            void main() {
                // 按 Spine UV 采样图集，并乘上骨架、插槽和附件合成后的颜色。
                gl_FragColor = texture2D(u_texture, v_uv) * v_color;
            }
        """
        if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex_shader):
            self.render_error = program.log()
        elif not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fragment_shader):
            self.render_error = program.log()
        elif not program.link():
            self.render_error = program.log()
        else:
            self.program = program
            self.attribute_locations = tuple(
                program.attributeLocation(name) for name in ("a_position", "a_uv", "a_color")
            )
            self.uniform_locations = tuple(
                program.uniformLocation(name) for name in ("u_viewport", "u_transform", "u_texture")
            )
            self.vertex_buffer.setUsagePattern(QOpenGLBuffer.UsagePattern.DynamicDraw)
            self.vertex_buffer.create()
            self.texture = QOpenGLTexture(self.texture_image)
            self.texture.setMinificationFilter(QOpenGLTexture.Filter.Linear)
            self.texture.setMagnificationFilter(QOpenGLTexture.Filter.Linear)
            self.texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)

        if self.render_error:
            # 不再往窗口里画状态文字（那会在人物旁边留下一块可见背景），改为只写 stderr。
            print(f"[ArkPet Python] OpenGL initialization failed: {self.render_error}", file=sys.stderr)

        self.context().aboutToBeDestroyed.connect(self.cleanup_gl)

    def resizeGL(self, width: int, height: int) -> None:
        # Qt 的尺寸是逻辑像素，OpenGL 视口需要换算为显示器实际像素。
        ratio = self.devicePixelRatioF()
        self.context().functions().glViewport(0, 0, round(width * ratio), round(height * ratio))

    def resizeEvent(self, event) -> None:
        self.refit()
        super().resizeEvent(event)

    def advance_frame(self) -> None:
        """定时器回调：推进 Spine 动画并请求重绘。

        注意这里刻意不显示 FPS / 动画名——窗口是全透明或浅色面板，
        任何状态文字都会留下不需要的视觉噪音。
        """
        now = time.perf_counter()
        self.bridge.update(min(now - self.last_frame, 0.1))
        self.last_frame = now
        self.update()

    def paintGL(self) -> None:
        functions = self.context().functions()
        # 每帧先清除上一姿态，但保留背景（桌宠是透明，预览面板是浅色）。
        functions.glClear(0x00004000)
        if (
            not self.program
            or not self.texture
            or not self.vertex_buffer.isCreated()
            or not self.attribute_locations
            or not self.uniform_locations
        ):
            return

        values = self.bridge.triangles()
        if not values:
            return
        vertices, batches = pack_triangle_vertices(values)

        # 缩放与居中来自缓存的适配矩形；尺寸变化时由 resizeEvent/refit 更新，
        # 因此这里不再每帧重算包围盒，也不会因为姿态摆动而抖动。
        if self.fit_center_scale is None:
            self.refit()
            if self.fit_center_scale is None:
                return
        center_x, center_y, scale = self.fit_center_scale
        # Positions and fit scale are in widget coordinates; Qt scales the GL framebuffer for HiDPI.
        viewport_width = self.width()
        viewport_height = self.height()
        self.program.bind()
        self.program.setUniformValue(self.uniform_locations[0], QVector2D(viewport_width, viewport_height))
        self.program.setUniformValue(
            self.uniform_locations[1],
            QVector4D(center_x, center_y, scale, -1.0 if self.facing_left else 1.0),
        )
        self.program.setUniformValue(self.uniform_locations[2], 0)
        self.texture.bind(0)
        self.vertex_buffer.bind()
        # 一次上传整副骨架的顶点；之后每种相邻混合模式只提交一次绘制调用。
        self.vertex_buffer.allocate(vertices, len(vertices))

        self.program.enableAttributeArray(self.attribute_locations[0])
        self.program.enableAttributeArray(self.attribute_locations[1])
        self.program.enableAttributeArray(self.attribute_locations[2])
        blend_functions = {
            0: (0x0302, 0x0303),
            1: (0x0302, 0x0001),
            2: (0x0306, 0x0303),
            3: (0x0001, 0x0301),
        }
        # Only adjacent equal blend modes are grouped; changing slot order would alter compositing.
        for first_vertex, vertex_count, blend_mode in batches:
            byte_offset = first_vertex * FLOATS_PER_VERTEX * BYTES_PER_FLOAT
            self.program.setAttributeBuffer(
                self.attribute_locations[0], 0x1406, byte_offset, 2, FLOATS_PER_VERTEX * BYTES_PER_FLOAT
            )
            self.program.setAttributeBuffer(
                self.attribute_locations[1],
                0x1406,
                byte_offset + 2 * BYTES_PER_FLOAT,
                2,
                FLOATS_PER_VERTEX * BYTES_PER_FLOAT,
            )
            self.program.setAttributeBuffer(
                self.attribute_locations[2],
                0x1406,
                byte_offset + 4 * BYTES_PER_FLOAT,
                4,
                FLOATS_PER_VERTEX * BYTES_PER_FLOAT,
            )
            functions.glBlendFunc(*blend_functions.get(blend_mode, blend_functions[0]))
            functions.glDrawArrays(0x0004, 0, vertex_count)

        self.vertex_buffer.release()
        self.texture.release()
        self.program.release()

    def cleanup_gl(self) -> None:
        if not self.context() or not self.context().isValid():
            return
        # GL resources must be released while their owning context is current.
        self.makeCurrent()
        if self.texture:
            self.texture.destroy()
            self.texture = None
        if self.vertex_buffer.isCreated():
            self.vertex_buffer.destroy()
        self.doneCurrent()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        """关闭时先停掉定时器并在上下文仍有效时释放 GL 资源。

        否则 QOpenGLTexture 会在没有当前上下文的情况下被析构，
        运行结束时会刷出 “destroy() called without a current context” 警告。
        """
        self.timer.stop()
        self.cleanup_gl()
        super().closeEvent(event)

    # ------------------------------------------------------------------ 鼠标交互
    def mousePressEvent(self, event) -> None:
        if not self.interactive:
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            # 记录按下位置相对窗口左上角的偏移，拖动时避免窗口突然跳到指针位置。
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if not self.interactive or self.drag_position is None:
            super().mouseMoveEvent(event)
            return
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = None
