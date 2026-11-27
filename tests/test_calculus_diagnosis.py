"""五个 P2 的回归：每一类都在真实 GUI 上显示过错误的数。

P2-1  非零求值点提前浮点化 -> sin(x) 在 0.4 伪发散
P2-2  derivative 没把 x_name 传给诊断 -> abs(t) 漏判
P2-3  to_sympy 没复用 preprocess -> 隐式乘法漏判
P2-4  二阶偏导递归丢 fixed -> 二阶偏导给错值
P2-5  编码损坏 -> π / ∞ / Unicode 负号全失效
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.calculus_numeric import (  # noqa: E402
    CalculusError, derivative, integrate, parse_bound, partial_derivative,
)


class TestNonZeroEvaluationPoint(unittest.TestCase):
    """求值点必须保持符号，极限算完才代入数值。

    直接把 x0 转成 Float，会在 (sin(0.4+h) - sin(0.4))/h 里留下有限的
    舍入残差；除以 h 后变成伪无穷，sin(x) 在 0.4 就被判成不可导。
    """

    def test_sine_at_nonzero(self):
        value, _err = derivative("sin(x)", 0.4)
        self.assertAlmostEqual(float(value), 0.9210609940028851, places=9)

    def test_exp_second_order(self):
        value, _err = derivative("exp(x)", 0.3, order=2)
        # 数值差分本身只有 7~8 位有效数字，不要要求到机器精度
        self.assertAlmostEqual(float(value), 1.3498588075760032, places=6)

    def test_cosine_at_one(self):
        value, _err = derivative("cos(x)", 1.0)
        self.assertAlmostEqual(float(value), -0.8414709848078965, places=9)

    def test_log_at_two(self):
        value, _err = derivative("log(x)", 2.0)
        self.assertAlmostEqual(float(value), 0.5, places=9)

    def test_second_derivative_of_sine(self):
        value, _err = derivative("sin(x)", 0.4, order=2)
        self.assertAlmostEqual(float(value), -0.3894183423086505, places=7)


class TestCustomVariableName(unittest.TestCase):
    """诊断必须用实际自变量名。

    传默认的 x 会让 abs(t) 找不到符号，返回 None，退回仍会漏判的数值路径。
    """

    def test_cusp_with_t(self):
        with self.assertRaises(CalculusError):
            derivative("abs(t)+10000*t^2", 0.0, x_name="t")

    def test_cusp_with_u(self):
        with self.assertRaises(CalculusError):
            derivative("abs(u)+10000*u^2", 0.0, x_name="u")

    def test_smooth_with_t(self):
        value, _err = derivative("sin(t)", 0.4, x_name="t")
        self.assertAlmostEqual(float(value), 0.9210609940028851, places=9)


class TestInputSyntaxMatchesNumericPath(unittest.TestCase):
    """符号转换要复用 expr_eval 的预处理。

    CompiledExpression 接受隐式乘法和上标；to_sympy 只替换 ^ 的话，
    这些合法写法会转换成 None，退回数值路径漏判。
    """

    def test_implicit_multiplication(self):
        with self.assertRaises(CalculusError):
            derivative("abs(x)+10000x^2", 0.0)

    def test_superscript(self):
        with self.assertRaises(CalculusError):
            derivative("abs(x)+10000*x²", 0.0)

    def test_plain_form_still_works(self):
        with self.assertRaises(CalculusError):
            derivative("abs(x)+10000*x^2", 0.0)

    def test_implicit_multiplication_smooth(self):
        value, _err = derivative("2x", 3.0)
        self.assertAlmostEqual(float(value), 2.0, places=6)


class TestSecondPartialKeepsFixed(unittest.TestCase):
    """二阶偏导递归检查一阶时必须保留 fixed。

    丢了 fixed 后其它变量没代入，一阶诊断看到两个自由符号返回 None，
    二阶提前退出，数值兜底又检测不到尖点。
    """

    def test_cusp_plus_quadratic(self):
        with self.assertRaises(CalculusError):
            partial_derivative("abs(x)+10000*x^2+y", ["x", "y"],
                               {"x": 0.0, "y": 1.0}, "x", order=2)

    def test_x_abs_x_second_partial(self):
        with self.assertRaises(CalculusError):
            partial_derivative("x*abs(x)+y", ["x", "y"],
                               {"x": 0.0, "y": 1.0}, "x", order=2)

    def test_smooth_second_partial(self):
        value, _err = partial_derivative("x**2+y", ["x", "y"],
                                         {"x": 1.0, "y": 0.0}, "x", order=2)
        self.assertAlmostEqual(float(value), 2.0, places=4)

    def test_second_partial_with_other_variable(self):
        value, _err = partial_derivative("x*y**2", ["x", "y"],
                                         {"x": 2.0, "y": 3.0}, "x", order=2)
        self.assertAlmostEqual(float(value), 0.0, places=4)


class TestUnicodeBounds(unittest.TestCase):
    """边界解析必须支持 π / ∞ / Unicode 负号。

    这些字面量曾经因为文件编码损坏全部失效 —— 不只是注释显示问题，
    连字符串常量里的字符都被换掉了。
    """

    def test_pi(self):
        self.assertAlmostEqual(float(parse_bound("π")), 3.141592653589793,
                               places=12)

    def test_infinity(self):
        self.assertEqual(float(parse_bound("∞")), float("inf"))

    def test_unicode_minus(self):
        self.assertAlmostEqual(float(parse_bound("−1")), -1.0, places=12)

    def test_unicode_negative_infinity(self):
        self.assertEqual(float(parse_bound("−∞")), float("-inf"))

    def test_ascii_forms_still_work(self):
        self.assertAlmostEqual(float(parse_bound("-1")), -1.0, places=12)
        self.assertEqual(float(parse_bound("inf")), float("inf"))

    def test_integral_to_pi(self):
        """GUI 里从 0 到 π 积 sin(x)，数值分支曾经直接失败。"""
        value, _err, _warn = integrate("sin(x)", parse_bound("0"),
                                       parse_bound("π"))
        self.assertAlmostEqual(complex(value).real, 2.0, places=6)


class TestSourceEncodingIsIntact(unittest.TestCase):
    """直接检查源文件里没有 mojibake。

    上一轮的编码损坏是靠功能才发现的；这条在读取层面就拦住。
    """

    def test_no_mojibake_markers(self):
        path = ROOT / "core" / "calculus_numeric.py"
        text = path.read_text(encoding="utf-8")
        for marker in ("â", "Ï", "Ã", "Â", "å", "æ", "ç", "è", "é", "ð"):
            self.assertNotIn(marker, text,
                             f"源文件里出现 mojibake 字符 {marker!r}")

    def test_unicode_bounds_present(self):
        path = ROOT / "core" / "calculus_numeric.py"
        text = path.read_text(encoding="utf-8")
        for char in ("π", "∞", "−"):
            self.assertIn(char, text, f"源文件里找不到 {char!r}")


if __name__ == "__main__":
    unittest.main()
