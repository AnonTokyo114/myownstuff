"""计算层回归测试：积分边界、可选求值点、复值积分、矩阵粘贴。

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_calculus -v
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from core import calculus_numeric as cn  # noqa: E402
from core import calculus_symbolic as cs  # noqa: E402


class TestBounds(unittest.TestCase):
    """审查 #15 / #4：边界解析不能执行代码，也不能把小数截成整数。"""

    def test_plain_numbers(self):
        self.assertEqual(cn.parse_bound("2.5"), 2.5)
        self.assertAlmostEqual(cn.parse_bound("pi"), np.pi)
        self.assertAlmostEqual(cn.parse_bound("-pi/2"), -np.pi / 2)
        self.assertEqual(cn.parse_bound("inf"), float("inf"))
        self.assertEqual(cn.parse_bound("-inf"), float("-inf"))
        self.assertIsNone(cn.parse_bound(""))

    def test_rejects_code(self):
        payloads = [
            "__import__('sys')",
            "().__class__.__base__.__subclasses__()",
            "exec('x=1')",
            "lambda: 1",
            "[1,2][0]",
            "__builtins__",
        ]
        for payload in payloads:
            with self.assertRaises(cn.CalculusError, msg=payload):
                cn.parse_bound(payload)

    def test_symbolic_keeps_decimals(self):
        txt, _, _ = cs.integrate_symbolic("x", "x", lower="0", upper="0.5")
        self.assertIn("1/8", txt)


class TestIntegrate(unittest.TestCase):
    """审查 #6 / #8：幅值不等于发散；复值积分要算对。"""

    def test_large_values_are_not_divergence(self):
        value, _err, _notes = cn.integrate("2000000*x", 0, 1)
        self.assertAlmostEqual(value.real if isinstance(value, complex) else value,
                               1e6, delta=1)

    def test_integrable_singularity(self):
        value, _err, _notes = cn.integrate("x^(-0.9)", 0, 1)
        self.assertAlmostEqual(value.real if isinstance(value, complex) else value,
                               10.0, places=4)

    def test_complex_integral(self):
        value, _err, _notes = cn.integrate("i*sin(4*pi*x)^2", 0, 1)
        self.assertAlmostEqual(complex(value).imag, 0.5, places=6)


class TestVariableName(unittest.TestCase):
    """审查 #7：自定义变量名要能用。"""

    def test_derivative_with_t(self):
        value, _err = cn.derivative("t^2", 2.0, order=1, x_name="t")
        self.assertAlmostEqual(value.real if isinstance(value, complex) else value,
                               4.0, places=3)

    def test_integral_with_u(self):
        value, _err, _notes = cn.integrate("u^2", 0, 3, x_name="u")
        self.assertAlmostEqual(value.real if isinstance(value, complex) else value,
                               9.0, places=8)


class TestSymbolicFunctions(unittest.TestCase):
    """审查 #9：log10 / log2 在 sympy 里不存在，必须显式映射。"""

    def test_log10(self):
        txt, _ = cs.differentiate("log10(x)", "x")
        self.assertTrue(txt)

    def test_log2(self):
        txt, _ = cs.differentiate("log2(x)", "x")
        self.assertTrue(txt)

    def test_second_derivative_of_abs(self):
        txt, _ = cs.differentiate("abs(x)", "x", order=2)
        self.assertTrue(txt)

    def test_assumption_mapping(self):
        txt, _ = cs.differentiate("n^2", "n", assumptions={"n": "integer"})
        self.assertEqual(txt, "2*n")


class TestTaylor(unittest.TestCase):
    """泰勒展开。"""

    def test_maclaurin_sin(self):
        txt, latex, _notes = cs.taylor("sin(x)", "x", point=0, order=5)
        self.assertIn("x**5/120", txt)
        self.assertIn("x**3/6", txt)
        self.assertTrue(latex)

    def test_expansion_point_stays_symbolic(self):
        """在 pi/2 展开必须保留 (x - pi/2)。

        之前在组合常量（"pi/2"）上退回浮点，series() 算出 0 —— 这条
        用例就是挡那个回归的。
        """
        txt, _latex, _notes = cs.taylor("sin(x)", "x", point="pi/2", order=4)
        self.assertIn("pi/2", txt)
        self.assertNotEqual(txt.strip(), "0", "展开不能退化成 0")

    def test_nonzero_center_kept(self):
        txt, _latex, _notes = cs.taylor("exp(x)", "x", point=1, order=3)
        self.assertIn("x - 1", txt)

    def test_zero_center_is_expanded_not_factored(self):
        """在 0 处要整理成和式，不能留下 x*(...) 这种因式分解形式。"""
        txt, _latex, _notes = cs.taylor("log(1+x)", "x", point=0, order=4)
        self.assertNotIn("*(", txt, f"看起来仍是被分解过的: {txt}")

    def test_early_termination_note(self):
        """sin 在 0 处偶次项全为 0，展开到 6 阶实际只到 5 阶。"""
        _txt, _latex, notes = cs.taylor("sin(x)", "x", point=0, order=6)
        self.assertTrue(any("stops at degree" in n for n in notes), notes)

    def test_infinite_center_rejected(self):
        with self.assertRaises(cs.SymbolicError):
            cs.taylor("sin(x)", "x", point="inf", order=3)

    def test_geometric_series(self):
        txt, _latex, _notes = cs.taylor("1/(1-x)", "x", point=0, order=5)
        terms = {t.strip() for t in txt.split("+")}
        self.assertEqual(terms, {"x**5", "x**4", "x**3", "x**2", "x", "1"},
                         f"意外的项: {terms}")

    def test_pi_bound_stays_exact(self):
        """回归：组合常量转 sympy 时要保持精确，不能变浮点。"""
        txt, _latex, _notes = cs.integrate_symbolic(
            "cos(x)", "x", lower="0", upper="pi/2")
        self.assertEqual(txt, "1")

    def test_two_pi_bound(self):
        txt, _latex, _notes = cs.integrate_symbolic(
            "sin(x)", "x", lower="0", upper="2*pi")
        self.assertEqual(txt, "0")


if __name__ == "__main__":
    unittest.main()
