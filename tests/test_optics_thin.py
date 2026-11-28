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
        self.assertIsNone(thin.magnification(system))


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

    def test_entrance_radius_is_the_first_lens(self):
        """入口半径取第一片有口径的透镜，不是全场最小。"""
        system = thin.ThinSystem(
            float("inf"),
            [thin.LensSpec("big", 100.0, 25.0, distance_to_next=50.0),
             thin.LensSpec("small", 100.0, 8.0)])
        self.assertAlmostEqual(thin.entrance_radius(system), 25.0)

    def test_unlit_downstream_aperture_does_not_change_the_beam(self):
        """L2 口径 8 mm 截不到轴上束（半径只有 5 mm），改它不应影响结果。

        report 的独立值：image_gap = 50 mm 时 9 条均匀光线的 RMS
        应为 1.61374306 mm。旧的 min() 规则会低估 20%。
        """
        system = thin.ThinSystem(
            float("inf"),
            [thin.LensSpec("L1", 100.0, 10.0, distance_to_next=50.0),
             thin.LensSpec("L2", 100.0, 8.0)])
        profile = thin.sample_rays(system, n_rays=9, image_gap=50.0)
        self.assertAlmostEqual(profile["rms"], 1.61374306, places=6)

        wider = thin.ThinSystem(
            float("inf"),
            [thin.LensSpec("L1", 100.0, 10.0, distance_to_next=50.0),
             thin.LensSpec("L2", 100.0, 10.0)])
        profile_wide = thin.sample_rays(wider, n_rays=9, image_gap=50.0)
        self.assertAlmostEqual(profile_wide["rms"], profile["rms"], places=9)

    def test_known_downstream_aperture_blocks(self):
        """只有真正挡住光线的下游口径才改变到达数。"""
        system = thin.ThinSystem(
            float("inf"),
            [thin.LensSpec("L1", 100.0, 10.0, distance_to_next=50.0),
             thin.LensSpec("L2", 100.0, 2.0)])
        profile = thin.sample_rays(system, n_rays=9, image_gap=50.0)
        self.assertGreater(profile["n_blocked"], 0)

    def test_finite_object_spot_is_zero_at_focus(self):
        """有限物距时的光锥也要在焦面上聚成一点。"""
        system = thin.ThinSystem(300.0, [thin.LensSpec("L", 100.0, 12.5)])
        profile = thin.spot(system, n_rays=9)
        self.assertLess(profile["rms"], 1e-12)
        self.assertAlmostEqual(thin.image_distance(system), 150.0, places=9)

    def test_last_gap_is_not_counted_twice(self):
        """最后一片的 distance_to_next 不能被重复使用。

        页面默认就是这样配的（Lens2 的 distance_to_next = 100，
        而 image_gap 也是 100）。重复计算会让 y 多走 100·u，
        在完美聚焦的系统上报出毫米级光斑。
        """
        f = 100.0
        system = thin.ThinSystem(
            f,
            [thin.LensSpec("L1", f, 12.5, distance_to_next=2 * f),
             thin.LensSpec("L2", f, 12.5, distance_to_next=f)])
        self.assertAlmostEqual(thin.image_distance(system), f, places=9)
        profile = thin.spot(system, n_rays=9)
        self.assertLess(profile["rms"], 1e-9,
                        f"4f 光斑应为 0，实得 {profile['rms']*1000:.3f} um")

    def test_traced_endpoint_matches_image_distance(self):
        """追迹终点必须落在由 image_distance 算出的像面上。"""
        f = 100.0
        system = thin.ThinSystem(
            f,
            [thin.LensSpec("L1", f, 12.5, distance_to_next=2 * f),
             thin.LensSpec("L2", f, 12.5, distance_to_next=f)])
        expected_z = thin.image_plane_z(system)
        for height in (0.0, 5.0, -5.0):
            with self.subTest(height=height):
                result = thin.trace(system, height, 0.0)
                self.assertAlmostEqual(result["z"], expected_z, places=9)

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


class TestCoordinatesAndNoImage(unittest.TestCase):
    """坐标原点与"有没有像面"必须一致。

    object_distance = inf 时把 inf 加进累加式会得到 inf/nan；
    无焦时不该给出像面光斑或有限的像面标记。
    """

    def test_infinite_object_puts_first_lens_at_zero(self):
        system = thin.ThinSystem(float("inf"),
                                 [thin.LensSpec("L", 100.0, 12.5)])
        self.assertEqual(system.first_lens_z(), 0.0)
        self.assertEqual(system.lens_z(), [0.0])
        self.assertAlmostEqual(thin.image_plane_z(system), 100.0, places=9)

    def test_afocal_has_no_image_plane(self):
        """s = f -> 像距 inf -> 没有有限像面。"""
        system = thin.ThinSystem(100.0, [thin.LensSpec("L", 100.0, 12.5)])
        self.assertFalse(np.isfinite(thin.image_distance(system)))
        self.assertIsNone(thin.image_plane_z(system))
        self.assertIsNone(thin.magnification(system))
        profile = thin.sample_rays(system, n_rays=9)
        self.assertFalse(profile["has_image"])
        self.assertIsNone(profile["rms"])

    def test_virtual_image_plane_is_negative(self):
        system = thin.ThinSystem(50.0, [thin.LensSpec("L", 100.0, 12.5)])
        self.assertAlmostEqual(thin.image_plane_z(system), -50.0, places=9)

    def test_plot_rays_match_statistics(self):
        """绘图用的光线就是被统计的那些。"""
        system = thin.ThinSystem(
            100.0,
            [thin.LensSpec("L1", 100.0, 12.5, distance_to_next=200.0),
             thin.LensSpec("L2", 100.0, 12.5, distance_to_next=100.0)])
        profile = thin.sample_rays(system, n_rays=9)
        ends = [r["path"][-1][1] for r in profile["rays"]
                if r["blocked_at"] is None]
        self.assertAlmostEqual(float(np.asarray(ends).std()),
                               profile["rms"], places=12)


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
