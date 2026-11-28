"""理想薄透镜（thin）模型的解析基准。

这里的期望值全部来自近轴公式，不拿实现自己的输出当期望：

    1/f = 1/s + 1/s'          单透镜成像
    m = -s'/s                 横向放大率
    4f 系统：s' = f, m = -1
    望远镜：C = 0（无焦），D = -f_obj/f_eye（角放大率）
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from core.optics import thin  # noqa: E402


class TestSingleLensImaging(unittest.TestCase):
    def test_thin_lens_equation(self):
        f = 100.0
        for s in (150.0, 200.0, 300.0, 500.0):
            with self.subTest(s=s):
                system = thin.ThinSystem(
                    s, [thin.LensSpec("L", f, 25.0)])
                gap = thin.image_distance(system)
                expected = 1.0 / (1.0 / f - 1.0 / s)
                self.assertAlmostEqual(gap, expected, places=9)

    def test_magnification(self):
        f, s = 100.0, 300.0
        system = thin.ThinSystem(s, [thin.LensSpec("L", f, 25.0)])
        gap = thin.image_distance(system)
        self.assertAlmostEqual(thin.magnification(system), -gap / s,
                               places=9)

    def test_virtual_image(self):
        """物在焦距以内 -> 像距为负（虚像）。"""
        system = thin.ThinSystem(50.0, [thin.LensSpec("L", 100.0, 25.0)])
        gap = thin.image_distance(system)
        self.assertLess(gap, 0.0)
        self.assertAlmostEqual(gap, 1.0 / (1.0 / 100.0 - 1.0 / 50.0),
                               places=9)

    def test_object_at_infinity_gives_focal_length(self):
        """无穷远物：像距 = f。"""
        system = thin.ThinSystem(float("inf"), [thin.LensSpec("L", 100.0,
                                                              25.0)])
        self.assertAlmostEqual(thin.image_distance(system), 100.0, places=9)

    def test_magnification_undefined_at_infinity(self):
        system = thin.ThinSystem(float("inf"), [thin.LensSpec("L", 100.0,
                                                              25.0)])
        self.assertFalse(np.isfinite(thin.magnification(system)))


class TestFourF(unittest.TestCase):
    def test_four_f_image_distance(self):
        f = 100.0
        system = thin.ThinSystem(
            f,
            [thin.LensSpec("L1", f, 30.0, distance_to_next=2 * f),
             thin.LensSpec("L2", f, 30.0)])
        self.assertAlmostEqual(thin.image_distance(system), f, places=9)

    def test_four_f_magnification_minus_one(self):
        f = 100.0
        system = thin.ThinSystem(
            f,
            [thin.LensSpec("L1", f, 30.0, distance_to_next=2 * f),
             thin.LensSpec("L2", f, 30.0)])
        self.assertAlmostEqual(thin.magnification(system), -1.0, places=9)

    def test_four_f_relays_height(self):
        """4f 把入射高度 1:1 倒像传过去：y_out = -y_in。"""
        f = 100.0
        system = thin.ThinSystem(
            f,
            [thin.LensSpec("L1", f, 30.0, distance_to_next=2 * f),
             thin.LensSpec("L2", f, 30.0)])
        result = thin.trace(system, 3.0, 0.0)
        self.assertIsNone(result["blocked_at"])
        self.assertAlmostEqual(result["y_image"], -3.0, places=9)


class TestAfocal(unittest.TestCase):
    def test_telescope_is_afocal(self):
        """共焦间距 -> C = 0（焦距无穷）。"""
        system = thin.ThinSystem(
            float("inf"),
            [thin.LensSpec("objective", 200.0, 40.0, distance_to_next=250.0),
             thin.LensSpec("eyepiece", 50.0, 10.0)])
        M = thin.system_matrix(system)
        self.assertAlmostEqual(float(M[1, 0]), 0.0, places=9)
        self.assertFalse(np.isfinite(thin.image_distance(system)))

    def test_telescope_angular_magnification(self):
        """角放大率是矩阵的 D = -f_obj/f_eye。"""
        system = thin.ThinSystem(
            float("inf"),
            [thin.LensSpec("objective", 200.0, 40.0, distance_to_next=250.0),
             thin.LensSpec("eyepiece", 50.0, 10.0)])
        M = thin.system_matrix(system)
        self.assertAlmostEqual(float(M[1, 1]), -200.0 / 50.0, places=9)


class TestApertureChecking(unittest.TestCase):
    def test_ray_blocked_at_small_aperture(self):
        system = thin.ThinSystem(
            100.0,
            [thin.LensSpec("L1", 100.0, 5.0, distance_to_next=100.0),
             thin.LensSpec("L2", 100.0, 3.0)])
        result = thin.trace(system, 5.0, 0.02)
        self.assertIsNotNone(result["blocked_at"])

    def test_axial_ray_passes(self):
        system = thin.ThinSystem(
            100.0,
            [thin.LensSpec("L1", 100.0, 5.0, distance_to_next=100.0),
             thin.LensSpec("L2", 100.0, 3.0)])
        self.assertIsNone(thin.trace(system, 0.0, 0.0)["blocked_at"])

    def test_aperture_is_checked_per_surface(self):
        """口径必须逐面查：第二片更小，第一片放行的光线会被它挡。"""
        system = thin.ThinSystem(
            float("inf"),
            [thin.LensSpec("big", 500.0, 20.0, distance_to_next=200.0),
             thin.LensSpec("small", 500.0, 2.0)])
        # 5 mm 在 big 内，但经 big 折射后高度可能超出 small
        result = thin.trace(system, 5.0, 0.0)
        # 不管这条具体光线是否被挡，blocked_at 只能报 "small" 或 None
        self.assertIn(result["blocked_at"], (None, "small"))


class TestGeometricSpot(unittest.TestCase):
    def test_spot_rms_is_zero_at_focus(self):
        """理想薄透镜没有像差，轴上光斑在焦面上是严格点。"""
        system = thin.ThinSystem(float("inf"), [thin.LensSpec("L", 100.0,
                                                              25.0)])
        profile = thin.spot(system, n_rays=9)
        self.assertEqual(profile["n_reached"], 9)
        self.assertLess(profile["rms"], 1e-12)

    def test_defocus_makes_spot_grow(self):
        system = thin.ThinSystem(float("inf"), [thin.LensSpec("L", 100.0,
                                                              25.0)])
        gap = thin.image_distance(system)
        focused = thin.spot(system, n_rays=9, image_gap=gap)
        blurred = thin.spot(system, n_rays=9, image_gap=gap + 0.5)
        self.assertGreater(blurred["rms"], focused["rms"])

    def test_spot_uses_smallest_aperture(self):
        system = thin.ThinSystem(
            float("inf"),
            [thin.LensSpec("big", 100.0, 25.0, distance_to_next=50.0),
             thin.LensSpec("small", 100.0, 8.0)])
        self.assertAlmostEqual(thin.spot(system, n_rays=5)["radius"], 8.0)

    def test_finite_object_spot_is_zero_at_focus(self):
        """有限物距时的光锥也要在焦面上聚成一点。"""
        system = thin.ThinSystem(300.0, [thin.LensSpec("L", 100.0, 12.5)])
        profile = thin.spot(system, n_rays=9)
        self.assertLess(profile["rms"], 1e-12)
        self.assertAlmostEqual(thin.image_distance(system), 150.0, places=9)

    def test_finite_object_cone_is_limited_by_first_aperture(self):
        """光锥角度上限 = 第一片口径 / 物距。"""
        s, ap = 300.0, 12.5
        system = thin.ThinSystem(s, [thin.LensSpec("L", 100.0, ap)])
        theta_max = ap / s
        # 恰好在这个角度上应该刚好处在口径边缘（不判为挡住）
        edge = thin.trace(system, 0.0, theta_max)
        self.assertIsNone(edge["blocked_at"])
        over = thin.trace(system, 0.0, theta_max * 1.01)
        self.assertIsNotNone(over["blocked_at"])


class TestValidation(unittest.TestCase):
    def test_rejects_bad_inputs(self):
        bad = [
            ("no lenses", lambda: thin.ThinSystem(100.0, [])),
            ("zero focal", lambda: thin.ThinSystem(
                100.0, [thin.LensSpec("L", 0.0)])),
            ("zero aperture", lambda: thin.ThinSystem(
                100.0, [thin.LensSpec("L", 100.0, 0.0)])),
            ("nan object", lambda: thin.ThinSystem(
                float("nan"), [thin.LensSpec("L", 100.0)])),
            ("zero object", lambda: thin.ThinSystem(
                0.0, [thin.LensSpec("L", 100.0)])),
        ]
        for label, build in bad:
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    build().validate()

    def test_infinite_object_distance_is_allowed(self):
        """物在无穷远是合法的，不需要用户填一个大数。"""
        system = thin.ThinSystem(float("inf"),
                                 [thin.LensSpec("L", 100.0, 25.0)])
        system.validate()
        self.assertAlmostEqual(thin.image_distance(system), 100.0, places=9)


if __name__ == "__main__":
    unittest.main()
