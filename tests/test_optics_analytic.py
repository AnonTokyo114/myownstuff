"""光学模块的独立解析基准。

期望值全部来自解析公式，**不**拿本实现的输出当期望。

注意本模块的约定：光线方向是 **二维子午向量 (y, z)**，不是三维。
``_unit`` 用 ``hypot(d[0], d[1])`` 归一化，传三维向量会被错误缩放。

覆盖：
  * Snell 定律与全反射
  * 主平面：单球面（非空气介质的两个方向）
  * 空气系统不退化
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from core import optics as o  # noqa: E402


def _ray(angle: float) -> np.ndarray:
    """沿 +z 前进、倾斜 angle 的二維方向向量。"""
    return np.array([math.sin(angle), math.cos(angle)])


class TestSnell(unittest.TestCase):
    def test_snell_relation(self):
        """n1 sin θ1 == n2 sin θ2。"""
        n1, n2, theta = 1.0, 1.5, 0.3
        normal = np.array([0.0, -1.0])        # 指向入射侧
        out, reflected = o.snell_or_reflect(_ray(theta), normal, n1, n2)
        self.assertFalse(reflected)
        sin_out = abs(float(out[0]))          # 出射沿 +z，y 分量即 sin
        self.assertAlmostEqual(n1 * math.sin(theta),
                               n2 * sin_out, places=10)

    def test_normal_incidence(self):
        normal = np.array([0.0, -1.0])
        out, reflected = o.snell_or_reflect(np.array([0.0, 1.0]), normal,
                                            1.0, 1.5)
        self.assertFalse(reflected)
        self.assertAlmostEqual(float(out[0]), 0.0, places=12)
        self.assertAlmostEqual(abs(float(out[1])), 1.0, places=12)

    def test_outgoing_is_unit_vector(self):
        normal = np.array([0.0, -1.0])
        out, _ = o.snell_or_reflect(_ray(0.4), normal, 1.0, 1.5)
        self.assertAlmostEqual(float(np.linalg.norm(out)), 1.0, places=12)

    def test_total_internal_reflection(self):
        """n1 > n2 且超过临界角时必须反射。"""
        n1, n2 = 1.5, 1.0
        critical = math.asin(n2 / n1)
        normal = np.array([0.0, -1.0])
        out, reflected = o.snell_or_reflect(_ray(critical + 0.05), normal,
                                            n1, n2)
        self.assertTrue(reflected, "超过临界角应全反射")
        self.assertAlmostEqual(float(np.linalg.norm(out)), 1.0, places=12)

    def test_below_critical_refracts(self):
        n1, n2 = 1.5, 1.0
        critical = math.asin(n2 / n1)
        normal = np.array([0.0, -1.0])
        _out, reflected = o.snell_or_reflect(_ray(critical - 0.05), normal,
                                             n1, n2)
        self.assertFalse(reflected)


class TestPrincipalPlanes(unittest.TestCase):
    """主平面：单球面时两主平面都应在顶点。

    旧实现用 efl=-1/C 去算后主平面，得到 h_back=50 mm；
    正确做法是分别用物方/像方焦距。
    """

    def _single(self, n_in: float, n_out: float, R: float):
        surfaces = [
            o.Surface("object", float("inf"), 0.0, n_in),
            o.Surface("refract", R, 0.0, n_out),
            o.Surface("image", float("inf"), 0.0, n_out),
        ]
        return o.focal_properties(surfaces, z=[0.0, 0.0, 0.0])

    def test_air_to_glass(self):
        fp = self._single(1.0, 1.5, 50.0)
        self.assertAlmostEqual(fp["matrix"][1, 0], -0.01, places=12)
        self.assertAlmostEqual(fp["f_front"], 100.0, places=9)
        self.assertAlmostEqual(fp["f_back"], 150.0, places=9)
        self.assertAlmostEqual(fp["h_front"], 0.0, places=9)
        self.assertAlmostEqual(fp["h_back"], 0.0, places=9)

    def test_glass_to_air(self):
        fp = self._single(1.5, 1.0, -50.0)
        self.assertAlmostEqual(fp["f_front"], 150.0, places=9)
        self.assertAlmostEqual(fp["f_back"], 100.0, places=9)
        self.assertAlmostEqual(fp["h_front"], 0.0, places=9)
        self.assertAlmostEqual(fp["h_back"], 0.0, places=9)

    def test_air_system_unchanged(self):
        """全空气系统里 n_in == n_out，两种写法应当重合。"""
        surfaces = [
            o.Surface("object", float("inf"), 0.0, 1.0),
            o.Surface("L1", 60.0, 8.0, 1.5),
            o.Surface("L2", -60.0, 0.0, 1.0),
            o.Surface("image", float("inf"), 0.0, 1.0),
        ]
        fp = o.focal_properties(surfaces, z=[0.0, 0.0, 8.0, 8.0])
        self.assertAlmostEqual(fp["f_front"], fp["f_back"], places=12)
        self.assertAlmostEqual(fp["h_front"],
                               fp["efl"] - fp["ffl"], places=9)
        self.assertGreater(fp["h_front"], 0.0)
        self.assertLess(fp["h_front"], 8.0)

    def test_power_matches_reduced_focal_length(self):
        fp = self._single(1.0, 1.5, 50.0)
        self.assertAlmostEqual(fp["power"] * fp["efl"], 1.0, places=9)


class TestFocusScanAndOffAxis(unittest.TestCase):
    """像质评估的两处定量问题。

    来自对光学报告的核对（code_artifact.html + 审阅）：

    best_focus 原来在 [0.55, 1.55]×像距上做 21 点均匀扫描，40 mm 时步长
    2 mm，直接跨过真实焦面 38.92 mm，报 38.00 mm；若像面口径收紧，
    严重离焦处只剩中心光线，RMS 归零会被误当成最优。

    spot_analysis 原来在 z=0 铺开 [-pmax, pmax] 并把 y0=0 当主光线；
    离轴时整束光到第一面会平移 z_first·tan(θ)，于是 t0 一变 RMS 就变
    —— 无穷远物点的成像结果不该依赖 t0。
    """

    @staticmethod
    def _build(rows):
        from core import optics as o
        surfaces = []
        for name, R, t, n, semi in rows:
            surfaces.append(o.Surface(
                name,
                float("inf") if R in ("inf", "") else float(R),
                float(t), float(n),
                None if semi in ("", None) else float(semi)))
        z, acc = [], 0.0
        for surf in surfaces:
            z.append(acc)
            acc += surf.t
        return surfaces, z

    @classmethod
    def _defaults(cls):
        from ui.pages.ray_page import DEFAULTS
        return cls._build(DEFAULTS)

    def test_best_focus_finds_true_minimum(self):
        """细扫最优约 38.92 mm / 120 µm；粗扫曾经给 38.00 / 164.5。"""
        from core import optics as o
        surfaces, z = self._defaults()
        result = o.best_focus(surfaces, z, field_deg=0.0, n_scan=21, n_pupil=9)
        self.assertAlmostEqual(result["best_gap"], 38.925, delta=0.05)
        self.assertAlmostEqual(result["best_rms"] * 1000, 119.96, delta=1.0)

    def test_best_focus_rejects_vignetted_optimum(self):
        """像面半口径 0.1 mm 时不能报 RMS=0 的假优。"""
        from core import optics as o
        from ui.pages.ray_page import DEFAULTS
        rows = list(DEFAULTS)
        name, R, t, n, _semi = rows[-1]
        rows[-1] = (name, R, t, n, "0.1")
        surfaces, z = self._build(rows)
        with self.assertRaises(ValueError):
            o.best_focus(surfaces, z, field_deg=0.0, n_scan=21, n_pupil=9)

    def test_off_axis_independent_of_object_distance(self):
        """5° 离轴时，改变 t0 不应改变 RMS 或幸存光线数。"""
        from core import optics as o
        from ui.pages.ray_page import DEFAULTS
        results = []
        for t0 in (1.0, 50.0, 140.0):
            rows = list(DEFAULTS)
            rows[0] = (rows[0][0], rows[0][1], str(t0),
                       rows[0][3], rows[0][4])
            surfaces, z = self._build(rows)
            prof = o.spot_analysis(surfaces, z, 5.0, n_pupil=21,
                                   image_gap=38.925)
            results.append((prof["rms"], prof["n_reached"]))
        rms_values = [r for r, _n in results]
        reached = [n for _r, n in results]
        self.assertEqual(len(set(reached)), 1,
                         f"幸存光线数随 t0 变化：{reached}")
        spread = (max(rms_values) - min(rms_values)) / max(rms_values)
        self.assertLess(spread, 0.02,
                        f"RMS 随 t0 变化 {spread*100:.1f}%：{rms_values}")

    def test_on_axis_unchanged(self):
        """轴上结果不受本次改动影响。"""
        from core import optics as o
        surfaces, z = self._defaults()
        prof = o.spot_analysis(surfaces, z, 0.0, n_pupil=9,
                               image_gap=38.925)
        self.assertEqual(prof["n_reached"], 9)
        self.assertAlmostEqual(prof["rms"] * 1000, 119.96, delta=1.0)

    def test_ray_page_has_equal_aspect_toggle(self):
        """光线图要有等比例开关，且默认关闭（光路通常又长又窄）。"""
        import os
        os.environ.setdefault("QT_API", "pyside6")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from ui.pages.ray_page import RayPage
        page = RayPage()
        self.assertFalse(page.equal_aspect.isChecked())
        page.equal_aspect.setChecked(True)
        app.processEvents()
        page.deleteLater()


class TestFocusFeasibilityAndChiefRay(unittest.TestCase):
    """对焦可行性判定与中心光线识别。

    P2-1  粗网格没命中完整通光区，不代表不存在。
          像面半口径 0.2 mm 时 21 个粗点最多透过 7 条，
          但 38.93 mm 附近确实有 9 条全通 —— 不能提前拒绝。

    P2-2  chief 原来用 abs(y0)<1e-12 定位，离轴时入口中心样本的
          发射高度已经平移成 -z_first*tan(θ)，永远匹配不到。
          改用入口采样序号（trace_rays 的 group）。
    """

    @staticmethod
    def _build(rows):
        from core import optics as o
        surfaces = []
        for name, R, t, n, semi in rows:
            surfaces.append(o.Surface(
                name,
                float("inf") if R in ("inf", "") else float(R),
                float(t), float(n),
                None if semi in ("", None) else float(semi)))
        z, acc = [], 0.0
        for surf in surfaces:
            z.append(acc)
            acc += surf.t
        return surfaces, z

    @classmethod
    def _with_image_semi(cls, semi):
        from ui.pages.ray_page import DEFAULTS
        rows = list(DEFAULTS)
        name, R, t, n, _old = rows[-1]
        rows[-1] = (name, R, t, n, semi)
        return cls._build(rows)

    # ---- P2-1 --------------------------------------------------------
    def test_feasible_focus_with_tight_aperture(self):
        """0.2 mm 半口径仍有 9 条全通的焦面，不能被粗网格否掉。"""
        from core import optics as o
        surfaces, z = self._with_image_semi("0.2")
        result = o.best_focus(surfaces, z, field_deg=0.0, n_scan=21,
                              n_pupil=9)
        self.assertEqual(result["n_reached"], 9)
        self.assertAlmostEqual(result["best_gap"], 38.927, delta=0.1)
        self.assertAlmostEqual(result["best_rms"] * 1000, 119.96, delta=1.0)

    def test_infeasible_focus_still_rejected(self):
        """0.1 mm 是真的不可行，必须拒绝。"""
        from core import optics as o
        surfaces, z = self._with_image_semi("0.1")
        with self.assertRaises(ValueError):
            o.best_focus(surfaces, z, field_deg=0.0, n_scan=21, n_pupil=9)

    def test_fixed_vignetting_allows_comparison(self):
        """5° 离轴时元件固定挡掉 1 条，存活集合不随像距变，可以比较。"""
        from core import optics as o
        from ui.pages.ray_page import DEFAULTS
        surfaces, z = self._build(DEFAULTS)
        result = o.best_focus(surfaces, z, field_deg=5.0, n_scan=21,
                              n_pupil=21)
        self.assertEqual(result["n_reached"], 20)

    # ---- P2-2 --------------------------------------------------------
    def test_chief_ray_found_off_axis(self):
        """5° 离轴时 chief 必须找得到，值要等于直接追迹的结果。"""
        from core import optics as o
        from ui.pages.ray_page import DEFAULTS
        surfaces, z = self._build(DEFAULTS)
        prof = o.spot_analysis(surfaces, z, 5.0, n_pupil=21,
                               image_gap=38.9)
        self.assertIsNotNone(prof["chief"])
        self.assertAlmostEqual(prof["chief"], 5.809844351727504, delta=1e-9)
        self.assertIsNotNone(prof["rms_about_chief"])
        self.assertAlmostEqual(prof["rms_about_chief"],
                               0.2118778277380495, delta=1e-9)

    def test_single_ray_branch_has_t0_invariance(self):
        """n_pupil<2 的分支也要做反投影；否则又失去平移不变性。"""
        from core import optics as o
        from ui.pages.ray_page import DEFAULTS
        centroids = []
        for t0 in (1.0, 50.0, 140.0):
            rows = list(DEFAULTS)
            rows[0] = (rows[0][0], rows[0][1], str(t0),
                       rows[0][3], rows[0][4])
            surfaces, z = self._build(rows)
            prof = o.spot_analysis(surfaces, z, 5.0, n_pupil=1,
                                   image_gap=38.9)
            centroids.append(prof["centroid"])
        self.assertLess(max(centroids) - min(centroids), 1e-9,
                        f"像面位置随 t0 变化：{centroids}")


class TestSingleRayFocusAndEvenSampling(unittest.TestCase):
    """焦面可行性的两个边界。

    P2-1  像面半口径 0.01 mm 时每个候选都只剩那条轴上光线：数量恒定，
          但它在所有像距上都在 y=0，RMS 恒为 0 —— 扫描下界会被当成焦面。
          数量相同也不等于同一批光线，必须比入口索引集合。

    P2-2  偶数入瞳采样没有 0（10 点是 ±1.39, ±4.17 …），n//2 指向一条
          偏轴光线，轴上系统的 chief 算成 0.0476 而不是 0。
    """

    @staticmethod
    def _build(rows):
        from core import optics as o
        surfaces = []
        for name, R, t, n, semi in rows:
            surfaces.append(o.Surface(
                name,
                float("inf") if R in ("inf", "") else float(R),
                float(t), float(n),
                None if semi in ("", None) else float(semi)))
        z, acc = [], 0.0
        for surf in surfaces:
            z.append(acc)
            acc += surf.t
        return surfaces, z

    @classmethod
    def _with_semi(cls, semi):
        from ui.pages.ray_page import DEFAULTS
        rows = list(DEFAULTS)
        name, R, t, n, _old = rows[-1]
        rows[-1] = (name, R, t, n, semi)
        return cls._build(rows)

    # ---- P2-1 --------------------------------------------------------
    def test_single_survivor_is_not_a_focus(self):
        """0.01 mm 半口径只剩轴上一条，RMS 恒 0，不能报焦面。"""
        from core import optics as o
        for semi in ("0.01", "0.001"):
            with self.subTest(semi=semi):
                surfaces, z = self._with_semi(semi)
                with self.assertRaises(ValueError):
                    o.best_focus(surfaces, z, field_deg=0.0, n_scan=21,
                                 n_pupil=9)

    def test_survivor_identity_recorded(self):
        """spot_analysis 要给出幸存光线的入口索引，而不只是数量。"""
        from core import optics as o
        surfaces, z = self._build(__import__("ui.pages.ray_page",
                                             fromlist=["DEFAULTS"]).DEFAULTS)
        prof = o.spot_analysis(surfaces, z, 0.0, n_pupil=9,
                               image_gap=38.925)
        self.assertIn("survivor_groups", prof)
        self.assertEqual(len(prof["survivor_groups"]), prof["n_reached"])

    def test_vignetting_identity_changes_with_spacing(self):
        """0.1 mm 时幸存集合确实随像距变 —— 这正是必须拒绝的原因。"""
        from core import optics as o
        surfaces, z = self._with_semi("0.1")
        sets = set()
        for gap in (22.0, 38.0, 38.9, 42.0, 50.0):
            prof = o.spot_analysis(surfaces, z, 0.0, n_pupil=9,
                                   image_gap=gap)
            sets.add(tuple(prof["survivor_groups"]))
        self.assertGreater(len(sets), 1,
                           f"集合应该随像距变化，实际只有 {sets}")

    # ---- P2-2 --------------------------------------------------------
    def test_even_sampling_chief_is_zero_on_axis(self):
        """轴上系统：任意采样数下 chief 都必须是 0。"""
        from core import optics as o
        from ui.pages.ray_page import DEFAULTS
        surfaces, z = self._build(DEFAULTS)
        for n_pupil in (9, 10, 20, 21):
            with self.subTest(n_pupil=n_pupil):
                prof = o.spot_analysis(surfaces, z, 0.0, n_pupil=n_pupil,
                                       image_gap=38.9)
                self.assertIsNotNone(prof["chief"])
                self.assertAlmostEqual(prof["chief"], 0.0, places=12)

    def test_even_sampling_chief_off_axis(self):
        """离轴时偶数采样也要给出和直接追迹一致的中心参考。"""
        from core import optics as o
        from ui.pages.ray_page import DEFAULTS
        surfaces, z = self._build(DEFAULTS)
        expected = 5.809844351727504
        for n_pupil in (20, 21):
            with self.subTest(n_pupil=n_pupil):
                prof = o.spot_analysis(surfaces, z, 5.0, n_pupil=n_pupil,
                                       image_gap=38.9)
                self.assertAlmostEqual(prof["chief"], expected, delta=1e-9)


if __name__ == "__main__":
    unittest.main()
