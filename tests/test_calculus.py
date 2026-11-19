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

    def test_lambdify_matches_direct_evaluation(self):
        """画图用 lambdify，必须和逐点求值一致。"""
        import numpy as np
        poly, _notes = cs.taylor_poly("sin(x)", "x", point=0, order=5)
        fn = cs.lambdify_of(poly)
        xs = np.array([0.0, 0.5, 1.0])
        got = fn(xs)
        # sin 的 5 阶展开在 x=1 处是 1 - 1/6 + 1/120
        self.assertAlmostEqual(got[2], 1 - 1/6 + 1/120, places=12)

    def test_expansion_holds_higher_order_wins(self):
        """阶数越高，满足同一容差的区间越大。"""
        low, _ = cs.taylor_poly("sin(x)", "x", point=0, order=1)
        high, _ = cs.taylor_poly("sin(x)", "x", point=0, order=5)
        r_low = cs.expansion_holds("sin(x)", "x", low, 0, tolerance=1e-2)[0]
        r_high = cs.expansion_holds("sin(x)", "x", high, 0, tolerance=1e-2)[0]
        self.assertGreater(r_high, r_low)
        # sin x ≈ x 在一阶、1% 容差下大约 0.39
        self.assertGreater(r_low, 0.2)
        self.assertLess(r_low, 0.7)

    def test_expansion_holds_tolerance_monotonic(self):
        """容差放宽，有效区间不会变小。"""
        poly, _ = cs.taylor_poly("exp(x)", "x", point=0, order=3)
        tight = cs.expansion_holds("exp(x)", "x", poly, 0, tolerance=1e-5)[0]
        loose = cs.expansion_holds("exp(x)", "x", poly, 0, tolerance=1e-2)[0]
        self.assertGreaterEqual(loose, tight)

    def test_constant_float(self):
        self.assertAlmostEqual(cs.constant_float("pi/2"), 1.5707963267948966)
        self.assertAlmostEqual(cs.constant_float("0.5"), 0.5)
        self.assertAlmostEqual(cs.constant_float("2*pi"), 6.283185307179586)

    # ---- 采样区间：混叠与常数 ----
    def test_aliasing_is_caught(self):
        """高速振荡函数不能被等距采样骗过。

        x + sin(800*pi*x/3)**2 在 [-1.5, 1.5] 上取 801 点，步长正好是
        振荡的半个周期，每个采样点都落在零点附近，单张等距网格会报出
        radius=1.5 —— 而 x=3/1600 处误差其实是 1。多网格 + 抖动之后
        半径必须明显收缩。
        """
        expr = "x + sin(800*pi*x/3)**2"
        poly, _notes = cs.taylor_poly(expr, "x", point=0, order=1)
        radius, _lo, _hi, spread, _step = cs.expansion_holds(
            expr, "x", poly, 0, tolerance=1e-3, grids=2)
        self.assertLess(radius, 1.0, f"半径不该是 {radius}（混叠没修好）")
        self.assertGreater(spread, 0.05,
                           "各网格结果应该明显不一致，才说明有混叠风险")

    def test_constant_function_gives_constant_polynomial(self):
        poly, _notes = cs.taylor_poly("exp(x)", "x", point=0, order=0)
        self.assertEqual(str(poly), "1")

    def test_error_curve_handles_constants(self):
        """常数函数 / 常数多项式的结果要铺成和采样网格一样的形状。"""
        import numpy as np
        xs = np.linspace(-1, 1, 21)
        for expr, order in (("3", 5), ("0", 5), ("exp(x)", 0)):
            poly, _notes = cs.taylor_poly(expr, "x", point=0, order=order)
            err = cs._error_curve(expr, "x", poly, xs)
            self.assertEqual(err.shape, xs.shape, f"{expr} 的形状不对")
            self.assertTrue(np.all(np.isfinite(err)))

    def test_constant_expansion_holds_everywhere(self):
        """零误差的展开式，整个采样范围都算有效。"""
        poly, _notes = cs.taylor_poly("3", "x", point=0, order=5)
        radius, _lo, _hi, _spread, _step = cs.expansion_holds(
            "3", "x", poly, 0, tolerance=1e-3,
            samples=201, grids=1)
        self.assertGreater(radius, 1.0)

    # ---- 泰勒多项式的合法性 ----
    def test_rejects_pole_and_branch_point(self):
        """极点 / 分支点处的「展开」其实还是原函数，必须拒绝。"""
        for expr in ("1/x", "log(x)", "sqrt(x)"):
            with self.assertRaises(cs.SymbolicError, msg=expr):
                cs.taylor(expr, "x", point=0, order=5)

    def test_rejects_abs_at_zero(self):
        """abs(x) 在 0 处不可导，sympy 会给出单侧的 x，不是泰勒多项式。"""
        with self.assertRaises(cs.SymbolicError):
            cs.taylor("abs(x)", "x", point=0, order=5)

    def test_abs_away_from_zero_is_fine(self):
        """abs(x) 在 1 附近就是 x，展开完全合法。"""
        txt, _latex, _notes = cs.taylor("abs(x)", "x", point=1, order=3)
        self.assertTrue(txt)

    def test_oscillatory_function_is_still_valid(self):
        """高频函数不能被合法性检查误伤（它的 1 阶展开确实是 x）。"""
        txt, _latex, _notes = cs.taylor(
            "x + sin(800*pi*x/3)^2", "x", point=0, order=1)
        self.assertEqual(txt, "x")

    def test_high_order_standard_functions_pass(self):
        """高阶正常函数不能被浮点噪声误判成单侧展开。"""
        for expr, order in (("1/(1-x)", 5), ("exp(x)", 3), ("cos(x)", 6),
                            ("tan(x)", 5), ("3", 5)):
            txt, _latex, _notes = cs.taylor(expr, "x", point=0, order=order)
            self.assertTrue(txt, f"{expr} 应该有结果")

    # ---- 逐项系数 ----
    def test_coefficient_list_sin(self):
        """sin(x) 展开到 5 阶：偶次项系数必须是 0（不能因为省掉就不写）。"""
        poly, _notes = cs.taylor_poly("sin(x)", "x", point=0, order=5)
        coeffs = cs.coefficient_list(poly, 5, 0)
        self.assertEqual([str(c) for c in coeffs],
                         ["0", "1", "0", "-1/6", "0", "1/120"])

    def test_coefficient_list_exp(self):
        poly, _notes = cs.taylor_poly("exp(x)", "x", point=0, order=4)
        coeffs = cs.coefficient_list(poly, 4, 0)
        self.assertEqual([str(c) for c in coeffs],
                         ["1", "1", "1/2", "1/6", "1/24"])

    def test_coefficient_list_nonzero_center(self):
        """非零展开点：系数对应的是 (x - a)^k，不是 x^k。"""
        poly, _notes = cs.taylor_poly("sin(x)", "x", point="pi/2", order=4)
        coeffs = cs.coefficient_list(poly, 4, "pi/2")
        self.assertEqual([str(c) for c in coeffs],
                         ["1", "0", "-1/2", "0", "1/24"])

    def test_coefficient_list_constant(self):
        poly, _notes = cs.taylor_poly("3", "x", point=0, order=2)
        coeffs = cs.coefficient_list(poly, 2, 0)
        self.assertEqual([str(c) for c in coeffs], ["3", "0", "0"])

    def test_coefficient_list_length(self):
        """长度固定是 order+1，界面靠它按阶数逐行打印。"""
        for order in (0, 1, 3, 7):
            poly, _notes = cs.taylor_poly("exp(x)", "x", point=0, order=order)
            coeffs = cs.coefficient_list(poly, order, 0)
            self.assertEqual(len(coeffs), order + 1)

    # ---- 导数存在性：按阶数核验，不当成"一律拒绝" ----
    def test_x_abs_x_order_1_ok(self):
        """x|x| 在 0 处一阶可导（展开是 0），二阶不可导。"""
        txt, _latex, _notes = cs.taylor("x*abs(x)", "x", point=0, order=1)
        self.assertEqual(txt, "0")
        with self.assertRaises(cs.SymbolicError):
            cs.taylor("x*abs(x)", "x", point=0, order=2)

    def test_abs_rejected_at_any_order(self):
        """abs 在 0 处连一阶导数都没有。"""
        for order in (1, 2, 5):
            with self.assertRaises(cs.SymbolicError, msg=str(order)):
                cs.taylor("abs(x)", "x", point=0, order=order)

    def test_smooth_functions_pass(self):
        """正例：平滑函数不能因为校验过严被拒。"""
        for expr in ("x**2", "sin(x)", "exp(x)", "1/(1-x)", "sqrt(1+x)",
                     "tan(x)", "exp(x)+x"):
            txt, _latex, _notes = cs.taylor(expr, "x", point=0, order=3)
            self.assertTrue(txt, f"{expr} 应该有结果")

    def test_abs_at_one_still_fine(self):
        """abs 在 1 附近是光滑的，展开合法。"""
        txt, _latex, _notes = cs.taylor("abs(x)", "x", point=1, order=3)
        self.assertTrue(txt)

    # ---- 复合系数加括号 ----
    def test_sum_coefficient_gets_parentheses(self):
        """exp(x)+x 在 1 处的一阶系数是 1+E，必须整体括起来。

        不括起来会拼成 "1 + E + 1 + E*(x - 1)"，在 x=1 处算出 2+E，
        而正确值是 1+E。
        """
        poly, _notes = cs.taylor_poly("exp(x)+x", "x", point=1, order=1)
        coeffs = cs.coefficient_list(poly, 1, 1)
        self.assertEqual([str(c) for c in coeffs], ["1 + E", "1 + E"])
        text = cs.format_polynomial(coeffs, "(x - 1)")
        self.assertIn("(1 + E)*(x - 1)", text)
        self.assertNotIn("E*(x - 1) + 1 + E", text)

    def test_formatted_text_is_algebraically_equivalent(self):
        """格式化结果必须和多项式本身代数等价（不能只看字符串）。"""
        sp = cs._require()
        symbol = sp.Symbol("x")
        for expr, center, order in (("exp(x)+x", 1, 1), ("sin(x)", 0, 5),
                                    ("exp(x)", 1, 3), ("1/(1+x)", 0, 4)):
            poly, _notes = cs.taylor_poly(expr, "x", point=center, order=order)
            text = cs.format_polynomial(
                cs.coefficient_list(poly, order, center),
                "x" if not center else "(x - %s)" % center)
            parsed = sp.sympify(text)
            expected = sp.expand(poly)
            # 用几个数值点比较，避免依赖 simplify 的符号等价判断
            # poly 里的符号带 assumptions，和 sympify 出来的普通 x 不是
            # 同一个对象 —— 替换要各用各的，否则 subs 不生效
            inner = next(iter(poly.free_symbols))
            for probe in (0.37, 1.0, 2.13):
                got = complex(parsed.subs(symbol, probe))
                want = complex(expected.subs(inner, probe))
                self.assertAlmostEqual(abs(got - want), 0.0, places=9,
                                       msg=f"{expr}: {text}")

    # ---- 展开点写成算式 ----
    def test_center_expression_parsed(self):
        self.assertEqual(str(cs.constant_of("1+1")), "2")
        self.assertEqual(str(cs.constant_of("pi/2")), "pi/2")
        self.assertEqual(str(cs.constant_of("0")), "0")

    def test_center_expression_taylor_matches_parsed_value(self):
        """展开点 "1+1" 和 "2" 必须给出同一个展开。"""
        a = cs.taylor("exp(x)", "x", point="1+1", order=2)[0]
        b = cs.taylor("exp(x)", "x", point="2", order=2)[0]
        self.assertEqual(a, b)

    # ---- 平移基底 ----
    def test_basis_text(self):
        sp = cs._require()
        self.assertEqual(cs.basis_text("x", sp.Integer(0)), "x")
        self.assertEqual(cs.basis_text("x", sp.Integer(2)), "(x - 2)")
        self.assertEqual(cs.basis_text("x", sp.pi / 2), "(x - pi/2)")
        # 关键回归：展开点自身是加法时必须整体加括号
        self.assertEqual(cs.basis_text("x", sp.pi / 2 + 1),
                         "(x - (1 + pi/2))")
        self.assertEqual(cs.basis_text("x", -sp.E), "(x + E)")

    def test_sum_center_series_matches_numeric_center(self):
        """展开点 "pi/2+1" 和它的数值形式必须给出同一个展开。"""
        a = cs.taylor("exp(x)", "x", point="pi/2+1", order=1)[0]
        b = cs.taylor("exp(x)", "x", point="1+pi/2", order=1)[0]
        self.assertEqual(a, b)

    # ---- 升幂格式化 ----
    def test_format_polynomial_ascending(self):
        poly, _notes = cs.taylor_poly("exp(x)", "x", point=0, order=4)
        text = cs.format_polynomial(cs.coefficient_list(poly, 4, 0))
        self.assertEqual(text, "1 + x + 1/2*x**2 + 1/6*x**3 + 1/24*x**4")

    def test_format_polynomial_skips_zeros_and_signs(self):
        """零项不写；负项用 " - " 连接，不能出现 "+ -"。"""
        poly, _notes = cs.taylor_poly("sin(x)", "x", point=0, order=5)
        text = cs.format_polynomial(cs.coefficient_list(poly, 5, 0))
        self.assertEqual(text, "x - 1/6*x**3 + 1/120*x**5")
        self.assertNotIn("+ -", text)
        # 零项不能出现（用恰好相等的断言就够了，额外查子串会被
        # "1/120*x**5" 里的 "20*x" 误伤）
        self.assertNotIn("*x**2", text)
        self.assertNotIn("1*x", text)

    def test_format_polynomial_constant(self):
        poly, _notes = cs.taylor_poly("3", "x", point=0, order=2)
        self.assertEqual(cs.format_polynomial(cs.coefficient_list(poly, 2, 0)),
                         "3")

    def test_format_polynomial_nonzero_basis(self):
        poly, _notes = cs.taylor_poly("sin(x)", "x", point="pi/2", order=4)
        text = cs.format_polynomial(
            cs.coefficient_list(poly, 4, "pi/2"), "(x - pi/2)")
        self.assertIn("(x - pi/2)**2", text)
        self.assertTrue(text.startswith("1 "))

    def test_progressive_prefixes_make_sense(self):
        """累进式：P_k 的项数不超过 P_(k+1)。"""
        poly, _notes = cs.taylor_poly("exp(x)", "x", point=0, order=4)
        coeffs = cs.coefficient_list(poly, 4, 0)
        lengths = [
            len(cs.format_polynomial(coeffs[:k + 1])) for k in range(5)
        ]
        self.assertEqual(lengths, sorted(lengths))

    # ---- 采样区间的数值卫生 ----
    def test_radius_never_negative(self):
        """抖动采样曾经算出负半径，把展开点排除在区间外。"""
        poly, _notes = cs.taylor_poly("exp(x)", "x", point=0, order=0)
        radius, _lo, _hi, _spread, step = cs.expansion_holds(
            "exp(x)", "x", poly, 0, tolerance=1e-3)
        self.assertGreaterEqual(radius, 0.0, f"半径不该是 {radius}")
        self.assertGreater(step, 0.0)

    def test_radius_brackets_the_center(self):
        """端点必须分居展开点两侧。"""
        for expr, order, tol in (("sin(x)", 5, 1e-3), ("exp(x)", 3, 1e-3),
                                 ("3", 5, 1e-3)):
            poly, _notes = cs.taylor_poly(expr, "x", point=0, order=order)
            _r, lo, hi, _spread, _step = cs.expansion_holds(
                expr, "x", poly, 0, tolerance=tol)
            self.assertLessEqual(lo, 0.0, f"{expr}: 左端点在展开点右侧")
            self.assertGreaterEqual(hi, 0.0, f"{expr}: 右端点在展开点左侧")

    def test_narrow_window_is_resolved(self):
        """exp(x) 展开到 0 阶时，有效窗口只有 ±0.001，比默认步长还窄。

        早期实现会算成 0（「紧挨着就超差」），其实只是网格太粗。
        """
        poly, _notes = cs.taylor_poly("exp(x)", "x", point=0, order=0)
        radius, _lo, _hi, _spread, _step = cs.expansion_holds(
            "exp(x)", "x", poly, 0, tolerance=1e-3)
        self.assertGreater(radius, 1e-4, f"应该能测出窄窗口，得到 {radius}")
        self.assertLess(radius, 1e-2)

    # ---- 常量精度 ----
    def test_decimal_precision_preserved(self):
        """ast.parse 会把 1.0000000000000001 变成 1.0，必须用源文本转有理数。"""
        value = cs._sympy_constant("1.0000000000000001")
        self.assertEqual(str(value), "10000000000000001/10000000000000000")

    def test_tiny_interval_integral(self):
        """回归：这段极小区间的积分曾经因为精度丢失算成 0。"""
        txt, _latex, _notes = cs.integrate_symbolic(
            "1", "x", lower="1", upper="1.0000000000000001")
        self.assertEqual(txt, "1/10000000000000000")

    def test_two_pi_bound(self):
        txt, _latex, _notes = cs.integrate_symbolic(
            "sin(x)", "x", lower="0", upper="2*pi")
        self.assertEqual(txt, "0")


if __name__ == "__main__":
    unittest.main()
