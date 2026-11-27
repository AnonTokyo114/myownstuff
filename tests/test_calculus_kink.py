"""不可导点的导数不能被报成一个看起来确定的数。

这组用例来自两轮独立审查，每一轮的发现都对应真实 GUI 上的错误显示：

第一轮（原始 bug）
  中心差分对折点**恒为零**：``|x|`` 在 0 处 ``f(+h) - f(-h)`` 永远是 0，
  Richardson 外推也只会给出 0 和零误差，界面显示「导数 0，误差 0」。

第二轮（上一版修复引入的回归 + 漏判）
  用固定步长比较左右差商会把**光滑极值**误判成折点
  （``1000*x**2`` 在 0 处左右差商是 ∓2000h，差 0.05）；
  而要求「中心差分为零」又会让 ``abs(x)+x`` 整条跳过；
  固定阈值还会漏掉 ``0.001*abs(x)``。

现在的判据看的是**左右差商之差随步长是否收敛**：光滑时它 ∝ h，
折点时它趋于非零常数。下面的用例把这两类都钉住了。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.calculus_numeric import (  # noqa: E402
    CalculusError, derivative, partial_derivative,
)


class TestNonDifferentiablePoints(unittest.TestCase):
    """必须拒绝，而不是给一个看起来很确定的数。"""

    # ---- 原始反例 ---------------------------------------------------
    def test_abs_at_zero_first_order(self):
        with self.assertRaises(CalculusError) as ctx:
            derivative("abs(x)", 0.0)
        message = str(ctx.exception)
        self.assertIn("one-sided", message)
        self.assertIn("not a derivative", message)

    def test_abs_at_zero_second_order(self):
        """二阶导的前提是一阶导存在。

        |x| 在 0 处的二阶单侧差商两边都恰好是 0（折点对称抵消），
        所以只查二阶会漏掉 —— 必须一阶也查。
        """
        with self.assertRaises(CalculusError):
            derivative("abs(x)", 0.0, order=2)

    def test_shifted_cusp(self):
        with self.assertRaises(CalculusError):
            derivative("abs(x-2)", 2.0)

    def test_partial_derivative_at_cusp(self):
        with self.assertRaises(CalculusError):
            partial_derivative("abs(x)+y", ["x", "y"],
                               {"x": 0.0, "y": 1.0}, "x")

    # ---- 第二轮补上的漏判 -------------------------------------------
    def test_cusp_with_linear_term(self):
        """abs(x)+x：中心差分是 1（不为零），旧判据整条跳过。"""
        with self.assertRaises(CalculusError):
            derivative("abs(x)+x", 0.0)

    def test_scaled_cusp(self):
        """0.001*abs(x)：左右差商只差 0.002，旧阈值判不出来。"""
        with self.assertRaises(CalculusError):
            derivative("0.001*abs(x)", 0.0)

    def test_first_order_differentiable_second_not(self):
        """x*abs(x)：一阶可导（导数 0），二阶不可导（两侧 -2 / +2）。"""
        value, _err = derivative("x*abs(x)", 0.0)
        self.assertAlmostEqual(float(value), 0.0, places=4)
        with self.assertRaises(CalculusError):
            derivative("x*abs(x)", 0.0, order=2)


class TestDifferentiableStillWorks(unittest.TestCase):
    """不能因为「导数不连续」就误拒可导函数。"""

    def _value(self, expr, x0, order=1):
        value, _err = derivative(expr, x0, order=order)
        return float(value)

    def test_zero_derivative_at_zero(self):
        self.assertAlmostEqual(self._value("x**2", 0.0), 0.0, places=6)
        self.assertAlmostEqual(self._value("x**3", 0.0), 0.0, places=6)
        self.assertAlmostEqual(self._value("sin(x)", 0.0, order=2), 0.0,
                               places=4)

    def test_steep_quadratic_at_extremum(self):
        """上一版把 1000*x**2 在 0 误判成不可导 —— 那只是二阶导在起作用。"""
        self.assertAlmostEqual(self._value("1000*x**2", 0.0), 0.0, places=4)
        self.assertAlmostEqual(self._value("1000*x**2", 0.0, order=2),
                               2000.0, delta=20.0)

    def test_narrow_gaussian_at_peak(self):
        """exp(-1000*x**2) 在 0，上一版也被误拒。"""
        self.assertAlmostEqual(self._value("exp(-1000*x**2)", 0.0), 0.0,
                               places=4)

    def test_large_scale_quadratic(self):
        self.assertAlmostEqual(self._value("100000*x**2", 0.0), 0.0, places=2)

    def test_ordinary_points(self):
        self.assertAlmostEqual(self._value("x**2", 3.0), 6.0, places=4)
        self.assertAlmostEqual(self._value("sin(x)", 0.0), 1.0, places=6)
        self.assertAlmostEqual(self._value("exp(x)", 0.0), 1.0, places=6)
        self.assertAlmostEqual(self._value("x**2", 1.0, order=2), 2.0,
                               places=3)

    def test_steep_function_not_rejected(self):
        self.assertAlmostEqual(self._value("exp(100*x)", 0.0), 100.0,
                               delta=5.0)

    def test_abs_away_from_cusp(self):
        self.assertAlmostEqual(self._value("abs(x)", 2.0), 1.0, places=6)
        self.assertAlmostEqual(self._value("abs(x)", -2.0), -1.0, places=6)

    def test_partial_derivative_smooth(self):
        value, _err = partial_derivative("x*y", ["x", "y"],
                                         {"x": 2.0, "y": 3.0}, "x")
        self.assertAlmostEqual(float(value), 3.0, places=5)


class TestSlowlyConvergingIsStillDifferentiable(unittest.TestCase):
    """可导函数的差商可以**任意慢**地收敛，不能用固定速率当判据。

    |x|^p（1 < p < 2）在 0 处都可导、导数为 0，但
    gap(h) = 2*h^(p-1) 收敛得很慢：

      p = 4/3   缩 4 倍只降到 0.63
      p = 1.25  缩 4 倍只降到 0.71

    最初的判据要求「gap 至少减半」，于是把它们全判成了不可导。
    只有 p = 1（|x| 本身）的 gap 才是常数 2。
    """

    def test_fractional_powers(self):
        # p 越接近 1 收敛越慢：p=1.05 跨 64 倍只降到 0.81，
        # p=1.01 只降到 0.96 —— 门槛必须高于这些值才能不误拒。
        for expr in ("abs(x)^1.01", "abs(x)^1.05", "abs(x)^1.1",
                     "abs(x)^(4/3)", "abs(x)^1.25", "abs(x)^1.4",
                     "abs(x)^1.5", "abs(x)^2"):
            with self.subTest(expr=expr):
                value, _err = derivative(expr, 0.0)
                self.assertAlmostEqual(float(value), 0.0, places=4)

    def test_second_order_fractional(self):
        """x*|x|^(4/3) 的二阶导存在且为 0。"""
        value, _err = derivative("x*abs(x)^(4/3)", 0.0, order=2)
        self.assertAlmostEqual(float(value), 0.0, places=3)

    def test_partial_derivative_fractional(self):
        value, _err = partial_derivative("abs(x)^(4/3)+y", ["x", "y"],
                                         {"x": 0.0, "y": 1.0}, "x")
        self.assertAlmostEqual(float(value), 0.0, places=4)

    def test_second_partial_fractional(self):
        value, _err = partial_derivative("x*abs(x)^(4/3)+y", ["x", "y"],
                                         {"x": 0.0, "y": 1.0}, "x",
                                         order=2)
        self.assertAlmostEqual(float(value), 0.0, places=3)


class TestInsufficientSampling(unittest.TestCase):
    """默认步长解析不了函数尺度时，不能断言不可导。

    cos(200000*x) 在 0 处导数为 0；默认步长 1e-4 覆盖了上百个周期，
    左右差商完全失去意义。这属于数值精度问题，不是可导性问题 ——
    判据必须在 gap 反而变大时退让。
    """

    def test_high_frequency_cosine(self):
        value, _err = derivative("cos(200000*x)", 0.0)
        self.assertAlmostEqual(float(value), 0.0, delta=1e-2)

    def test_medium_frequency_cosine(self):
        value, _err = derivative("cos(100000*x)", 0.0)
        self.assertAlmostEqual(float(value), 0.0, delta=1e-2)


class TestGapBehaviour(unittest.TestCase):
    """判据依赖的性质本身也要钉住。"""

    def test_gap_is_constant_for_abs(self):
        """|x| 的 gap 恒为 2，不随步长变化 —— 这才是折点的标志。"""
        f = abs
        for h in (1e-3, 1e-4, 1e-5):
            gap = abs((f(0.0) - f(-h)) / h - (f(h) - f(0.0)) / h)
            self.assertAlmostEqual(gap, 2.0, places=6)

    def test_gap_decays_slowly_for_fractional_power(self):
        """|x|^(4/3) 的 gap 收敛得慢，但确实在降。"""
        f = lambda t: abs(t) ** (4.0 / 3.0)   # noqa: E731
        gaps = []
        for h in (1e-3, 1e-4, 1e-5):
            gaps.append(abs((f(0.0) - f(-h)) / h - (f(h) - f(0.0)) / h))
        self.assertGreater(gaps[0], gaps[1])
        self.assertGreater(gaps[1], gaps[2])
        # 步长从 1e-3 降到 1e-5（100 倍），gap 只降到 100^(-1/3) ≈ 0.215
        self.assertAlmostEqual(gaps[2] / gaps[0], 100 ** (-1 / 3), places=2)


class TestSymmetricZeroIsNotProof(unittest.TestCase):
    """把「对称差分为零」和「导数为零」区分开。"""

    def test_symmetric_difference_alone_would_be_zero(self):
        """直接算中心差分，确认它确实是 0 —— 这正是当初误判的来源。"""
        f = abs
        h = 1e-4
        symmetric = (f(0.0 + h) - f(0.0 - h)) / (2 * h)
        self.assertEqual(symmetric, 0.0)
        left = (f(0.0) - f(0.0 - h)) / h
        right = (f(0.0 + h) - f(0.0)) / h
        self.assertAlmostEqual(left, -1.0)
        self.assertAlmostEqual(right, 1.0)

    def test_gap_shrinks_for_smooth_functions(self):
        """判据的基础：光滑函数的左右差商之差随步长缩小。"""
        f = lambda t: 1000.0 * t * t   # noqa: E731
        for h in (1e-4, 1e-5):
            left = (f(0.0) - f(-h)) / h
            right = (f(h) - f(0.0)) / h
            gap = abs(left - right)
            self.assertAlmostEqual(gap / (2 * 1000.0 * h), 1.0, places=6)
        # 步长缩小 10 倍，gap 也缩小约 10 倍
        g1 = abs((f(0.0) - f(-1e-4)) / 1e-4 - (f(1e-4) - f(0.0)) / 1e-4)
        g2 = abs((f(0.0) - f(-1e-5)) / 1e-5 - (f(1e-5) - f(0.0)) / 1e-5)
        self.assertAlmostEqual(g2 / g1, 0.1, places=6)

    def test_gap_stays_for_cusp(self):
        """折点的左右差商之差不随步长缩小。"""
        f = abs
        g1 = abs((f(0.0) - f(-1e-4)) / 1e-4 - (f(1e-4) - f(0.0)) / 1e-4)
        g2 = abs((f(0.0) - f(-1e-5)) / 1e-5 - (f(1e-5) - f(0.0)) / 1e-5)
        self.assertAlmostEqual(g1, 2.0, places=6)
        self.assertAlmostEqual(g2, 2.0, places=6)
        self.assertAlmostEqual(g2 / g1, 1.0, places=6)

    def test_derivative_rejects_instead_of_returning_zero(self):
        try:
            result = derivative("abs(x)", 0.0)
        except CalculusError:
            return
        self.fail(f"应当拒绝，却返回了 {result}")


if __name__ == "__main__":
    unittest.main()
