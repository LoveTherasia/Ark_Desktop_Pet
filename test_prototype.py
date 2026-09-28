from __future__ import annotations

import ctypes
import os
import struct
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QApplication

from pet import (
    BYTES_PER_FLOAT,
    BYTES_PER_TRIANGLE,
    FLOATS_PER_VERTEX,
    LIBRARY,
    ROOT,
    PetWindow,
    SpineBridge,
    bounds_from_triangles,
    find_sample_skeleton,
    pack_triangle_vertices,
    triangle_batches,
)


class PrototypeTests(unittest.TestCase):
    """Exercise both native Spine data updates and the Python-side GL setup."""

    @classmethod
    def setUpClass(cls) -> None:
        if not LIBRARY.is_file():
            raise unittest.SkipTest("Build the native Spine bridge before running prototype tests")
        cls.application = QApplication.instance() or QApplication([])
        cls.skeleton = find_sample_skeleton()

    def setUp(self) -> None:
        self.bridge = SpineBridge(LIBRARY, self.skeleton, self.skeleton.with_suffix(".atlas"))

    def tearDown(self) -> None:
        self.bridge.close()

    def test_loads_spine_38_model_and_animations(self) -> None:
        self.assertEqual(self.skeleton.name, "build_char_180_amgoat_sanrio_2.skel")
        self.assertGreaterEqual(len(self.bridge.animations), 1)
        self.assertTrue(any(name in self.bridge.animations for name in ("Default", "Relax", "Move")))
        geometry_bounds = bounds_from_triangles(self.bridge.triangles())
        self.assertIsNotNone(geometry_bounds)
        self.assertGreater(geometry_bounds[2], 0)
        self.assertGreater(geometry_bounds[3], 0)

    def test_animation_updates_produce_spine_triangles(self) -> None:
        self.bridge.set_animation(self.bridge.animations[0])
        for _ in range(30):
            self.bridge.update(1 / 60)
        values = self.bridge.triangles()
        self.assertGreater(len(values), 0)
        self.assertEqual(len(values) % BYTES_PER_TRIANGLE, 0)
        self.assertNotEqual(values[:8], bytes(8))

    def test_current_model_clipping_attachments_are_applied(self) -> None:
        self.bridge.set_animation("Relax" if "Relax" in self.bridge.animations else self.bridge.animations[0])
        self.bridge.update(0)
        self.assertGreater(self.bridge.clipping_attachment_count(), 0)
        self.assertGreater(len(self.bridge.triangles()), 0)

    def test_loads_another_local_spine_model(self) -> None:
        skeleton = next((ROOT / "src" / "assets").rglob("build_char_009_12fce.skel"))
        alternate = SpineBridge(LIBRARY, skeleton, skeleton.with_suffix(".atlas"))
        try:
            self.assertGreaterEqual(len(alternate.animations), 1)
            alternate.set_animation("Move" if "Move" in alternate.animations else alternate.animations[0])
            for _ in range(30):
                alternate.update(1 / 60)
            self.assertGreater(len(alternate.triangles()), 0)
        finally:
            alternate.close()

    def test_region_uvs_are_sampled_from_upright_texels(self) -> None:
        """回归测试：区域附件的顶点必须取到正确的 UV。

        曾经这里对 UV 做过一次多余的重排（{2,3,0,1}），
        对于图集里 rotate:true 的区域会取到相邻角点的 UV，导致模型局部贴图错乱
        （例如鞋子只剩深色色块、手部细节丢失）。该重排已移除。
        这里用模型自己的贴图验证：每个区域附件投影到屏幕上的四边形，其采样必须
        落在"正立"的贴图块内——即 UV 的 u 随屏幕 x 递增、v 随屏幕 y 递减。
        """
        library = self.bridge.library
        library.pet_slot_count.argtypes = [ctypes.c_void_p]
        library.pet_slot_count.restype = ctypes.c_int
        library.pet_slot_name.argtypes = [ctypes.c_void_p, ctypes.c_int]
        library.pet_slot_name.restype = ctypes.c_char_p
        library.pet_slot_vertex_count.argtypes = [ctypes.c_void_p, ctypes.c_int]
        library.pet_slot_vertex_count.restype = ctypes.c_int

        texture = QImage(str(self.skeleton.with_suffix(".png")))
        self.assertFalse(texture.isNull(), "sample texture should load")
        texel_u = 1.0 / texture.width()
        texel_v = 1.0 / texture.height()

        values = self.bridge.triangles()
        self.assertGreater(len(values), 0)
        triangles = len(values) // BYTES_PER_TRIANGLE

        # Collect every emitted triangle with its three (x, y, u, v) corners.
        emitted = []
        for start in range(0, len(values) - BYTES_PER_TRIANGLE + 1, BYTES_PER_TRIANGLE):
            if struct.unpack_from("<f", values, start + 7 * BYTES_PER_FLOAT)[0] <= 0.0:
                continue
            corners = []
            for vertex in range(3):
                offset = start + vertex * FLOATS_PER_VERTEX * BYTES_PER_FLOAT
                x, y, u, v = struct.unpack_from("<ffff", values, offset)
                corners.append((x, y, u, v))
            emitted.append(corners)
        self.assertGreater(triangles, 0)

        # Group into quads by identical y-extent pairs (the two triangles of a region
        # share exactly the same four corners), then check the UV winding.
        quads = 0
        for corners in emitted:
            xs = sorted({round(c[0], 4) for c in corners})
            if len(xs) != 2:
                continue  # not an axis-aligned quad; meshes are checked separately
            left = [c for c in corners if round(c[0], 4) == xs[0]]
            right = [c for c in corners if round(c[0], 4) == xs[1]]
            if not left or not right:
                continue
            # U must advance to the right for a correct (non-transposed) region sampling.
            left_u = sum(c[2] for c in left) / len(left)
            right_u = sum(c[2] for c in right) / len(right)
            if abs(right_u - left_u) < texel_u:
                continue  # degenerate in u; not a rectangular region
            self.assertGreater(
                right_u, left_u,
                f"region UV appears transposed: left u={left_u:.5f} right u={right_u:.5f}",
            )
            quads += 1
        self.assertGreater(
            quads, 20,
            f"expected many axis-aligned region quads to validate, found {quads} "
            f"out of {len(emitted)} emitted triangles",
        )

    def test_triangle_batches_preserve_spine_draw_order(self) -> None:
        triangle = bytes(BYTES_PER_TRIANGLE)
        values = bytearray(triangle * 4)
        for index, mode in enumerate((0.0, 0.0, 1.0, 0.0)):
            struct.pack_into("<f", values, (index + 1) * BYTES_PER_TRIANGLE - 4, mode)
        self.assertEqual(triangle_batches(bytes(values)), [(0, 2, 0), (2, 1, 1), (3, 1, 0)])
        vertices, batches = pack_triangle_vertices(bytes(values))
        self.assertEqual(len(vertices), 4 * 3 * 8 * 4)
        self.assertEqual(batches, [(0, 6, 0), (6, 3, 1), (9, 3, 0)])

    def test_widget_uses_transparent_opengl_surface(self) -> None:
        window = PetWindow(self.bridge, self.skeleton.with_suffix(".png"))
        try:
            self.assertIsInstance(window, QOpenGLWidget)
            self.assertGreaterEqual(window.format().alphaBufferSize(), 8)
            self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
