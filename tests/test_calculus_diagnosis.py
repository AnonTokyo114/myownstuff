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


class TestExactDecimalConstants(unittest.TestCase):
    """表达式里的小数常量必须保持精确。

    to_sympy 如果留下 Float(0.1)，f(0) = sqrt(0.1) 就是近似值，
    差商 (f(h)-f(0))/h 带舍入残差，除以 h 变成伪无穷 ——
    sqrt(x^2+0.1) 在 0 会被误判成不可导。
    换成 1/10 就正常，说明差别只在精度，不在数学。
    """

    def test_decimal_and_fraction_agree(self):
        for expr in ("sqrt(x^2+0.1)", "sqrt(x^2+1/10)"):
            with self.subTest(expr=expr):
                value, _err = derivative(expr, 0.0)
                self.assertAlmostEqual(float(value), 0.0, places=4)

    def test_shifted_smooth_curve(self):
        """平移后的小尺度光滑函数同样不能误拒。"""
        value, _err = derivative("sqrt((x-2)^2+1e-16)", 2.0)
        self.assertAlmostEqual(float(value), 0.0, places=3)

    def test_shifted_with_decimal(self):
        value, _err = derivative("sqrt((x-3)^2+0.25)", 3.0)
        self.assertAlmostEqual(float(value), 0.0, places=4)


class TestFixedVariableIsSubstituted(unittest.TestCase):
    """固定变量必须真的代入。

    表达式里的符号是 Symbol('y')，如果代码自己造一个
    Symbol('y', real=True) 去 subs，会静默不替换 —— y=0 没生效，
    诊断还在比较含 y 的差商，于是误判。

    同一函数固定 y=0 和 y=1，结论必须不同，这样才能证明代入真的发生。
    """

    def test_y_zero_makes_it_smooth(self):
        value, _err = partial_derivative("y*abs(x)", ["x", "y"],
                                         {"x": 0.0, "y": 0.0}, "x")
        self.assertAlmostEqual(float(value), 0.0, places=6)

    def test_y_one_keeps_the_cusp(self):
        with self.assertRaises(CalculusError):
            partial_derivative("y*abs(x)", ["x", "y"],
                               {"x": 0.0, "y": 1.0}, "x")

    def test_away_from_the_cusp(self):
        value, _err = partial_derivative("y*abs(x)", ["x", "y"],
                                         {"x": 1.0, "y": 0.0}, "x")
        self.assertAlmostEqual(float(value), 0.0, places=6)

    def test_symbol_identity(self):
        """直接钉住 sympy 的符号相等性，解释为什么必须按名字取。"""
        import sympy as sp
        from core.calculus_numeric import to_sympy
        self.assertNotEqual(sp.Symbol("y"), sp.Symbol("y", real=True))
        sym = to_sympy("y*abs(x)")
        self.assertNotIn(sp.Symbol("y", real=True), sym.free_symbols)


class TestNoArbitrarySymbolPlaceholder(unittest.TestCase):
    """诊断不能在任意符号上求极限。

    上一版用 Symbol('_a') 代表求值点，floor 在带正负号假设的符号上
    求差商极限会失控（实测 8 秒不返回）。现在求值点是具体数字。
    """

    def test_floor_at_integer_is_rejected_quickly(self):
        import time
        start = time.perf_counter()
        with self.assertRaises(CalculusError):
            derivative("floor(x)", 1.0)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 5.0,
                        f"floor(x) 在 1 处诊断用了 {elapsed:.1f} 秒")

    def test_ceil_at_integer(self):
        with self.assertRaises(CalculusError):
            derivative("ceil(x)", 1.0)

    def test_floor_plus_slope(self):
        with self.assertRaises(CalculusError):
            derivative("floor(x)+x", 1.0)

    def test_floor_between_integers_is_differentiable(self):
        """整数之间 floor 局部恒定，导数为 0 —— 这个不能拒。"""
        value, _err = derivative("floor(x)", 0.5)
        self.assertAlmostEqual(float(value), 0.0, places=6)


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
