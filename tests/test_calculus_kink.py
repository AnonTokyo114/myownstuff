"""不可导点的导数不能被报成零。

中心差分对折点**恒为零**：``|x|`` 在 0 处 ``f(+h) - f(-h)`` 永远是 0，
Richardson 外推也只会得到 0 和零误差。所以「导数 0、误差 0」这个组合
本身不能证明可导 —— 必须另查单侧差商。

这些用例都对应真实 GUI 上会显示的结果。
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
    """必须拒绝，而不是给一个看起来很确定的 0。"""

    def test_abs_at_zero_first_order(self):
        with self.assertRaises(CalculusError) as ctx:
            derivative("abs(x)", 0.0)
        message = str(ctx.exception)
        self.assertIn("one-sided", message)
        self.assertIn("not a derivative", message)

    def test_abs_at_zero_second_order(self):
        """二阶导的前提是一阶导存在。"""
        with self.assertRaises(CalculusError):
            derivative("abs(x)", 0.0, order=2)

    def test_shifted_cusp(self):
        with self.assertRaises(CalculusError):
            derivative("abs(x-2)", 2.0)

    def test_partial_derivative_at_cusp(self):
        with self.assertRaises(CalculusError):
            partial_derivative("abs(x)+y", ["x", "y"],
                               {"x": 0.0, "y": 1.0}, "x")


class TestDifferentiableStillWorks(unittest.TestCase):
    """折点检查不能误伤——包括「导数在 0 恰好是 0」的常见情况。"""

    def _value(self, expr, x0, order=1):
        value, _err = derivative(expr, x0, order=order)
        return float(value)

    def test_zero_derivative_at_zero(self):
        self.assertAlmostEqual(self._value("x**2", 0.0), 0.0, places=6)
        self.assertAlmostEqual(self._value("x**3", 0.0), 0.0, places=6)
        self.assertAlmostEqual(self._value("sin(x)", 0.0, order=2), 0.0,
                               places=4)

    def test_ordinary_points(self):
        self.assertAlmostEqual(self._value("x**2", 3.0), 6.0, places=4)
        self.assertAlmostEqual(self._value("sin(x)", 0.0), 1.0, places=6)
        self.assertAlmostEqual(self._value("exp(x)", 0.0), 1.0, places=6)
        self.assertAlmostEqual(self._value("x**2", 1.0, order=2), 2.0,
                               places=3)

    def test_steep_function_not_rejected(self):
        """导数很大时两侧差商的相对差很小，不该被判成折点。"""
        self.assertAlmostEqual(self._value("exp(100*x)", 0.0), 100.0,
                               delta=5.0)

    def test_abs_away_from_cusp(self):
        self.assertAlmostEqual(self._value("abs(x)", 2.0), 1.0, places=6)
        self.assertAlmostEqual(self._value("abs(x)", -2.0), -1.0, places=6)

    def test_partial_derivative_smooth(self):
        value, _err = partial_derivative("x*y", ["x", "y"],
                                         {"x": 2.0, "y": 3.0}, "x")
        self.assertAlmostEqual(float(value), 3.0, places=5)


class TestSymmetricZeroIsNotProof(unittest.TestCase):
    """把「对称差分为零」和「导数为零」区分开。"""

    def test_symmetric_difference_alone_would_be_zero(self):
        """直接算中心差分，确认它确实是 0 —— 这正是当初误判的来源。"""
        f = abs
        h = 1e-4
        symmetric = (f(0.0 + h) - f(0.0 - h)) / (2 * h)
        self.assertEqual(symmetric, 0.0)
        # 但左右差商分别是 -1、+1
        left = (f(0.0) - f(0.0 - h)) / h
        right = (f(0.0 + h) - f(0.0)) / h
        self.assertAlmostEqual(left, -1.0)
        self.assertAlmostEqual(right, 1.0)

    def test_derivative_rejects_instead_of_returning_zero(self):
        try:
            result = derivative("abs(x)", 0.0)
        except CalculusError:
            return
        self.fail(f"应当拒绝，却返回了 {result}")


if __name__ == "__main__":
    unittest.main()
