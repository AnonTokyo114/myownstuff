"""三物点光线扇的验收测试。

期望值来自近轴公式，不用实现自己的输出当期望：

    有限物距  y_image = m · y_object      （m 是横向放大率）
    4f        m = -1，所以 -1/0/+1 -> +1/0/-1
    每条光线只要没被挡，都汇聚到同一个共轭像点
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from core.optics import thin  # noqa: E402


def four_f(f: float = 100.0, aperture: float | None = 12.5):
    return thin.ThinSystem(
        object_distance=f,
        lenses=[
            thin.LensSpec("L1", f, aperture, distance_to_next=2 * f),
            thin.LensSpec("L2", f, aperture, distance_to_next=f),
        ],
    )


def fans_at(heights, centre=0.0, count=3):
    return [thin.ObjectFan(h, centre, count, f"P{i + 1}")
            for i, h in enumerate(heights)]


class TestFanAngles(unittest.TestCase):
    def test_one_ray_is_just_the_centre(self):
        fan = thin.ObjectFan(0.0, 2.0, 1)
        self.assertEqual(fan.angles_deg(0.5), [2.0])

    def test_three_rays_symmetric(self):
        fan = thin.ObjectFan(0.0, 0.0, 3)
        self.assertEqual(fan.angles_deg(0.5), [-0.5, 0.0, 0.5])

    def test_five_rays_symmetric(self):
        fan = thin.ObjectFan(0.0, 0.0, 5)
        self.assertEqual(fan.angles_deg(0.5), [-1.0, -0.5, 0.0, 0.5, 1.0])

    def test_centre_offset_applies(self):
        fan = thin.ObjectFan(0.0, 1.5, 3)
        self.assertEqual(fan.angles_deg(0.5), [1.0, 1.5, 2.0])

    def test_unsupported_counts_are_rejected(self):
        """只支持 1/3/5；别的数量没有唯一的对称展开，应当报错。"""
        for count in (0, 2, 4, 6):
            with self.subTest(count=count):
                fan = thin.ObjectFan(0.0, 0.0, count)
                with self.assertRaises(ValueError):
                    fan.angles_deg(0.5)

    def test_count_must_be_a_whole_supported_number(self):
        """3.5 不能被静默截成 3；inf 不能被 int() 吃掉变成 OverflowError。"""
        for raw in (1, 3, 5, 3.0, 5.0):
            with self.subTest(accept=raw):
                thin.ObjectFan(0.0, 0.0, raw).angles_deg(0.5)
        for raw in (3.5, 1.9, 4, 0, 2, 6, float("inf"), float("nan"),
                    "3", True, None):
            with self.subTest(reject=raw):
                with self.assertRaises(ValueError):
                    thin.ObjectFan(0.0, 0.0, raw).angles_deg(0.5)

    def test_non_finite_inputs_are_rejected(self):
        system = thin.ThinSystem(
            float("inf"), [thin.LensSpec("L", 100.0, 12.5)])
        cases = [
            ("infinite height", thin.ObjectFan(float("inf"), 0.0, 3), 0.5),
            ("nan height", thin.ObjectFan(float("nan"), 0.0, 3), 0.5),
            ("infinite angle", thin.ObjectFan(0.0, float("inf"), 3), 0.5),
            ("nan angle", thin.ObjectFan(0.0, float("nan"), 3), 0.5),
            ("infinite interval", thin.ObjectFan(0.0, 0.0, 3), float("inf")),
            ("nan interval", thin.ObjectFan(0.0, 0.0, 3), float("nan")),
        ]
        for label, fan, interval in cases:
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    thin.sample_fans(system, [fan], interval)


class TestDefaultFourF(unittest.TestCase):
    def test_three_points_image_to_negated_heights(self):
        """默认 4f：-1/0/+1 -> +1/0/-1。"""
        system = four_f()
        heights = thin.object_heights(0.0, 1.0, 1.0)
        self.assertEqual(heights, (-1.0, 0.0, 1.0))
        result = thin.sample_fans(system, fans_at(heights), 0.5)
        centroids = [g["centroid"] for g in result["groups"]]
        for got, want in zip(centroids, (1.0, 0.0, -1.0)):
            self.assertAlmostEqual(got, want, places=9)

    def test_all_rays_reach(self):
        system = four_f()
        result = thin.sample_fans(
            system, fans_at(thin.object_heights(0.0, 1.0, 1.0)), 0.5)
        for group in result["groups"]:
            self.assertEqual(group["n_reached"], 3)
            self.assertTrue(group["sufficient"])

    def test_each_group_has_zero_rms(self):
        """理想薄透镜：共轭像点上所有光线严格汇聚。"""
        system = four_f()
        result = thin.sample_fans(
            system, fans_at(thin.object_heights(0.0, 1.0, 1.0)), 0.5)
        for group in result["groups"]:
            self.assertLess(group["rms"], 1e-12)

    def test_groups_are_not_mixed(self):
        """三个像点分开统计：RMS 不能是"三个点之间的距离"。"""
        system = four_f()
        result = thin.sample_fans(
            system, fans_at(thin.object_heights(0.0, 1.0, 1.0)), 0.5)
        centroids = [g["centroid"] for g in result["groups"]]
        self.assertAlmostEqual(centroids[0] - centroids[1], 1.0, places=9)
        combined = float(np.std(centroids))
        self.assertGreater(combined, 0.5,
                           "三个像点如果混在一起算，RMS 会明显非零")


class TestIntervalAndCount(unittest.TestCase):
    def test_interval_does_not_move_the_image(self):
        """改变 Δθ 后，未被遮挡的同一物点光线仍汇聚到共轭像点。"""
        system = four_f(aperture=40.0)   # 口径足够大，所有角度都通
        for interval in (0.1, 0.5, 1.5, 3.0):
            with self.subTest(interval=interval):
                result = thin.sample_fans(
                    system, fans_at([-1.0, 0.0, 1.0]), interval)
                for got, want in zip(
                        [g["centroid"] for g in result["groups"]],
                        (1.0, 0.0, -1.0)):
                    self.assertAlmostEqual(got, want, places=9)

    def test_ray_counts(self):
        system = four_f(aperture=40.0)
        for count in (1, 3, 5):
            with self.subTest(count=count):
                result = thin.sample_fans(
                    system, fans_at([0.0], count=count), 0.5)
                group = result["groups"][0]
                self.assertEqual(group["n_requested"], count)
                self.assertEqual(len(group["rays"]), count)

    def test_single_ray_is_insufficient_for_rms(self):
        """一条光线无法定义 RMS —— 要明确标采样不足，而不是给 0。"""
        system = four_f()
        result = thin.sample_fans(system, fans_at([0.0], count=1), 0.5)
        group = result["groups"][0]
        self.assertFalse(group["sufficient"])
        self.assertIsNone(group["rms"])
        self.assertIsNone(group["centroid"])


class TestUnequalSpacing(unittest.TestCase):
    def test_object_heights_from_centre_and_gaps(self):
        self.assertEqual(thin.object_heights(0.0, 2.0, 0.5),
                         (-2.0, 0.0, 0.5))
        self.assertEqual(thin.object_heights(5.0, 1.0, 3.0),
                         (4.0, 5.0, 8.0))

    def test_unequal_spacing_images_correctly(self):
        system = four_f()
        result = thin.sample_fans(system, fans_at([-2.0, 0.0, 0.5]), 0.5)
        for got, want in zip(
                [g["centroid"] for g in result["groups"]],
                (2.0, 0.0, -0.5)):
            self.assertAlmostEqual(got, want, places=9)


class TestVignettingPerGroup(unittest.TestCase):
    def test_one_group_blocked_others_survive(self):
        """一组全被挡，其他组照常显示。"""
        # 物距 100，L1 口径 2 mm；离轴 1 mm、角度 5° 的扇会被挡
        system = thin.ThinSystem(
            100.0,
            [thin.LensSpec("L1", 100.0, 2.0, distance_to_next=200.0),
             thin.LensSpec("L2", 100.0, None, distance_to_next=100.0)],
        )
        fans = [
            thin.ObjectFan(0.0, 0.0, 3, "axial"),
            thin.ObjectFan(40.0, 8.0, 3, "far"),
        ]
        result = thin.sample_fans(system, fans, 0.5)
        axial, far = result["groups"]
        self.assertGreater(axial["n_reached"], 0)
        self.assertEqual(far["n_reached"], 0)
        self.assertTrue(far["has_image"])       # 有像面，只是这组全挡
        self.assertIsNone(far["rms"])

    def test_blocked_rays_keep_stop_position(self):
        """被挡光线的 path 终点 = 第一片处的解析高度。

        物点 y=40、中心角 8°，物距 100：
            y_at_L1 = 40 + 100 * radians(8) = 53.957...
        近轴模型用 θ 本身，不是 tan θ。
        """
        import math

        system = thin.ThinSystem(
            100.0,
            [thin.LensSpec("L1", 100.0, 2.0, distance_to_next=200.0),
             thin.LensSpec("L2", 100.0, None, distance_to_next=100.0)],
        )
        result = thin.sample_fans(
            system, [thin.ObjectFan(40.0, 8.0, 3, "far")], 0.5)
        blocked = [r for r in result["groups"][0]["rays"]
                   if r["blocked_at"] is not None]
        self.assertGreater(len(blocked), 0, "这组应当全部被 L1 挡住")
        for ray in blocked:
            # 每条光线用自己的角度算解析高度，不能固定用中心角
            expected_y = 40.0 + 100.0 * math.radians(ray["theta_deg"])
            self.assertEqual(ray["blocked_at"], "L1")
            self.assertAlmostEqual(ray["path"][-1][0], 100.0, places=9)
            self.assertAlmostEqual(ray["path"][-1][1], expected_y, places=9)


class TestNoFiniteImage(unittest.TestCase):
    def test_afocal_reports_no_image(self):
        """s = f -> 无焦，不给任何像面统计。"""
        system = thin.ThinSystem(100.0, [thin.LensSpec("L", 100.0, 12.5)])
        result = thin.sample_fans(system, fans_at([-1.0, 0.0, 1.0]), 0.5)
        self.assertFalse(result["has_image"])
        for group in result["groups"]:
            self.assertFalse(group["has_image"])
            self.assertFalse(group["sufficient"])
            self.assertIsNone(group["rms"])

    def test_infinite_object_uses_parallel_incidence(self):
        """inf 模式：一个物点 = 一个入射方向，扫描入口高度。

        同一方向的平行光在理想焦面上汇聚到同一点，所以 RMS 是 0；
        如果在一组里换方向，得到的"RMS"其实是视场间距。
        """
        system = thin.ThinSystem(
            float("inf"), [thin.LensSpec("L", 100.0, 12.5)])
        result = thin.sample_fans(
            system, [thin.ObjectFan(0.0, 1.0, 3, "tilted")], 0.5)
        group = result["groups"][0]
        # 同一方向
        self.assertEqual({r["theta_deg"] for r in group["rays"]}, {1.0})
        # 入口高度铺开
        entries = [r["entry"] for r in group["rays"]]
        self.assertEqual(len(set(entries)), 3)
        self.assertAlmostEqual(min(entries), -12.5, places=9)
        self.assertAlmostEqual(max(entries), 12.5, places=9)
        # 平行光汇聚到 f·θ（近轴用 θ 本身）
        expected = 100.0 * math.radians(1.0)
        for ray in group["rays"]:
            self.assertAlmostEqual(ray["y_image"], expected, places=9)
        self.assertLess(group["rms"], 1e-12)
        self.assertTrue(group["sufficient"])

    def test_infinite_object_field_is_set_by_centre_angle(self):
        """换中心角就是换视场方向，焦面位置随之移动。"""
        system = thin.ThinSystem(
            float("inf"), [thin.LensSpec("L", 100.0, 12.5)])
        result = thin.sample_fans(
            system, [thin.ObjectFan(0.0, 2.0, 3, "tilted")], 0.5)
        group = result["groups"][0]
        expected = 100.0 * math.radians(2.0)
        for ray in group["rays"]:
            self.assertAlmostEqual(ray["y_image"], expected, places=9)


if __name__ == "__main__":
    unittest.main()
