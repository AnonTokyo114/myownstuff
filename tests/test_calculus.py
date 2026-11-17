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


if __name__ == "__main__":
    unittest.main()
