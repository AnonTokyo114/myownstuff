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


if __name__ == "__main__":
    unittest.main()
