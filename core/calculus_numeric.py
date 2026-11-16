"""数值微积分（A 版）。

工具：
  * integrate      定积分（支持 ±inf），用 scipy.integrate.quad
  * derivative     单变量导数（中心差分 + Richardson 外推）
  * partial        多变量偏导（含混合二阶）
  * 小结里的误差估计都如实给出，不假装精确

设计上刻意不依赖 sympy：没装也能用（B 版符号计算在 calculus_symbolic.py）。
"""

import ast
import math
import operator
import warnings

import numpy as np
from scipy import integrate as _quad_mod

from .expr_eval import CompiledExpression, ExpressionError  # noqa: F401


class CalculusError(ValueError):
    """计算失败（不可积、发散、缺变量等）。"""


# ---------------------------------------------------------------- 积分

#: 积分边界里允许出现的常量名
_BOUND_NAMES = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
    "nan": math.nan,
}
_BOUND_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_BOUND_UNARY = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval_bound_node(node):
    """把已通过白名单的 AST 节点算成一个数。

    这里**不使用 eval**：即使把 ``__builtins__`` 清空，仍能经由属性访问
    （如 ``().__class__.__base__.__subclasses__()``）取回内建对象，从而
    执行任意代码。边界表达式可能来自粘贴的文本，所以只允许数字常量、
    几个已知常量名，以及 + - * / ** % 。
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculusError("Bounds may only contain numbers.")
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id in _BOUND_NAMES:
            return _BOUND_NAMES[node.id]
        raise CalculusError(f"Unknown name in bounds: {node.id!r}")
    if isinstance(node, ast.BinOp):
        fn = _BOUND_BINOPS.get(type(node.op))
        if fn is None:
            raise CalculusError("Unsupported operator in bounds.")
        return fn(_eval_bound_node(node.left), _eval_bound_node(node.right))
    if isinstance(node, ast.UnaryOp):
        fn = _BOUND_UNARY.get(type(node.op))
        if fn is None:
            raise CalculusError("Unsupported operator in bounds.")
        return fn(_eval_bound_node(node.operand))
    raise CalculusError(
        "Bounds may only be numbers and the operators + - * / ** %.")


def parse_bound(text, default=None):
    """解析积分上下限，支持 inf / -inf / pi 等写法。

    求值走 AST 白名单，不调用 eval —— 见 _eval_bound_node 的说明。
    """
    if text is None:
        return default
    s = str(text).strip().lower()
    if not s:
        return default
    s = (s.replace("∞", "inf")
          .replace("π", "pi")
          .replace("−", "-"))       # U+2212 减号
    try:
        tree = ast.parse(s, mode="eval")
        return _eval_bound_node(tree.body)
    except CalculusError:
        raise
    except SyntaxError as exc:
        raise CalculusError(f"Cannot parse bounds: {text}") from exc
    except (ZeroDivisionError, OverflowError, ValueError) as exc:
        raise CalculusError(f"Cannot evaluate bounds {text!r}: {exc}") from exc


def _as_complex(v):
    """把求值结果转成 Python 复数；支持标量与 0 维数组。"""
    try:
        arr = np.asarray(v)
        if arr.ndim == 0:
            return complex(arr.item())
        return complex(arr.reshape(-1)[0])
    except Exception:
        return complex(v)


def _realify(z, tol=1e-12):
    """虚部可忽略时收回实数，避免界面上出现 6+0j 这种难看的输出。"""
    try:
        z = complex(z)
    except Exception:
        return z
    if abs(z.imag) <= tol * max(1.0, abs(z)):
        return z.real
    return z


def _is_meaningfully_complex(f, lower, upper):
    """探测被积函数是否真有虚部（避免对实函数白跑两遍）。

    注意：scipy 的 quad 对复值函数会**静默丢弃虚部**，所以必须先判断，
    再决定是否分实虚部积分。
    """
    lo = lower if np.isfinite(lower) else 1.0
    hi = upper if np.isfinite(upper) else 2.0
    if not (np.isfinite(lo) and np.isfinite(hi)) or lo >= hi:
        lo, hi = 0.0, 1.0
    for t in np.linspace(lo, hi, 5):
        try:
            v = _as_complex(f(float(t)))
        except Exception:
            continue
        if abs(v.imag) > 1e-12 * max(1.0, abs(v)):
            return True
    return False


def _looks_divergent(f, lower, upper):
    """粗判有限端点处是否有极点型发散。

    从端点向内取越来越近的点，看函数绝对值是否无界增长。quad 对 1/x 这类
    被积函数会返回一个看似正常的数（实测 145.6），仅靠它的警告不足以提醒用户。
    """
    for bound, direction in ((lower, 1), (upper, -1)):
        if not np.isfinite(bound):
            continue
        peak = 0.0
        for eps in (1e-2, 1e-4, 1e-6, 1e-8):
            x = bound + direction * eps
            try:
                v = abs(complex(f(x)))
            except Exception:
                continue
            if not np.isfinite(v):
                return True
            peak = max(peak, v)
        if peak > 1e6:
            return True
    return False


def integrate(expr, lower, upper, x_name="x"):
    """定积分。返回 (值, 误差估计, 警告说明列表)。

    值可能是复数：若被积函数有虚部，则实部与虚部分别积分再合成。
    """
    compiled = CompiledExpression(expr, variables=[x_name])
    f = compiled.function() if compiled.variables else (lambda _x: compiled.evaluate())

    if _looks_divergent(f, lower, upper):
        raise CalculusError(
            "该积分在区间端点附近发散（被积函数趋于无穷），无法给出有限结果。"
            "若奇点在区间内部，请拆成两段分别积分。"
        )

    complex_input = _is_meaningfully_complex(f, lower, upper)

    def _quad_scalar(g):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            val, err = _quad_mod.quad(g, lower, upper, limit=200)
            return val, err, [str(w.message) for w in caught]

    notes = []
    if complex_input:
        try:
            re_v, re_e, re_notes = _quad_scalar(lambda t: complex(f(t)).real)
            im_v, im_e, im_notes = _quad_scalar(lambda t: complex(f(t)).imag)
        except Exception as exc:
            raise CalculusError(f"Integration failed: {exc}") from exc
        value = complex(re_v, im_v)
        error = max(re_e, im_e)
        notes.extend(re_notes)
        notes.extend(im_notes)
        # 无条件说明：结果含虚部时，用户需要知道这是分实虚部积分得来的
        notes.insert(0, "被积函数是复值的，已对实部与虚部分别积分")
    else:
        try:
            value, error, got = _quad_scalar(f)
        except Exception as exc:
            raise CalculusError(f"Integration failed: {exc}") from exc
        notes.extend(got)

    if not np.isfinite(abs(value)):
        raise CalculusError(
            "积分结果为无穷大或无效值 —— 该积分很可能发散，或区间端点有奇点。"
        )
    if notes and not complex_input:
        notes.insert(0, "积分器报告了困难（结果可能不可靠，常见于奇点或强振荡）")
    if notes and abs(error) > max(1.0, abs(value)) * 1e-3:
        notes.insert(0, f"误差估计偏大（{error:.3g}），建议拆分区间或换写法")
    return value, float(abs(error)), notes


# ---------------------------------------------------------------- 导数

def _central_diff(f, x0, h):
    """中心差分，返回 (一阶, 二阶) 近似。"""
    fp = f(x0 + h)
    fm = f(x0 - h)
    f0 = f(x0)
    first = (fp - fm) / (2 * h)
    second = (fp - 2 * f0 + fm) / (h * h)
    return first, second


def derivative(expr, x0, order=1, x_name="x", h=None):
    """在 x0 处求一阶/二阶导数，用 Richardson 外推提高精度。

    返回 (导数值, 粗略误差估计)。
    """
    compiled = CompiledExpression(expr, variables=[x_name])
    base = compiled.function() if compiled.variables else (lambda _x: compiled.evaluate())

    def f(t):
        try:
            v = base(t)
            return _as_complex(v)
        except CalculusError:
            raise
        except Exception as exc:
            raise CalculusError(f"Evaluation failed at x={t}: {exc}") from exc

    order = int(order)
    if order not in (1, 2):
        raise CalculusError("Order must be 1 or 2")

    # 步长按量级选，过小会被浮点误差淹没，过大则截断误差显著
    scale = max(1.0, abs(float(x0)))
    h0 = h if h else scale * 1e-4

    def estimate(step):
        first, second = _central_diff(f, x0, step)
        return first if order == 1 else second

    # Richardson: D(h) 与 D(h/2) 组合，消去 O(h^2) 项
    d1 = estimate(h0)
    d2 = estimate(h0 / 2)
    d3 = estimate(h0 / 4)
    if order == 1:
        rich = (4 * d2 - d1) / 3
        err = abs(d3 - d2)
    else:
        # 二阶差分误差是 O(h^2)，外推系数同为一阶
        rich = (4 * d2 - d1) / 3
        err = abs(d3 - d2)
    if not np.isfinite(abs(rich)):
        raise CalculusError("Derivative is invalid (not differentiable here, or the function diverges)")
    return _realify(rich), float(abs(err))


def derivative_curve(expr, xs, order=1, x_name="x"):
    """在给定点集上算导数，用于画导函数曲线。"""
    compiled = CompiledExpression(expr, variables=[x_name])
    base = compiled.function() if compiled.variables else (lambda _x: compiled.evaluate())
    out = []
    for x in xs:
        try:
            out.append(derivative(expr, float(x), order=order, x_name=x_name)[0])
        except Exception:
            out.append(np.nan)
    return np.array(out, dtype=float)


# ---------------------------------------------------------------- 偏导

def partial_derivative(expr, variables, point, var, order=1, cross_var=None):
    """在 point 处对 var 求偏导（order=1/2；给 cross_var 则为混合二阶）。

    point 是 {变量名: 数值}。
    返回 (偏导值, 误差估计)。
    """
    compiled = CompiledExpression(expr, variables=list(variables))
    missing = set(variables) - set(point)
    if missing:
        raise CalculusError(f"Missing values for variable(s): {', '.join(sorted(missing))}")
    if var not in variables:
        raise CalculusError(f"Expression has no variable {var}")

    def f(**kw):
        env = dict(point)
        env.update(kw)
        return _as_complex(compiled.evaluate(**env))

    scale = max(1.0, abs(float(point[var])))
    h = scale * 1e-4

    if order == 1:
        def val(step):
            up = {var: point[var] + step}
            dn = {var: point[var] - step}
            return (f(**up) - f(**dn)) / (2 * step)

        d1, d2 = val(h), val(h / 2)
        rich = (4 * d2 - d1) / 3
        return _realify(rich), float(abs(val(h / 4) - d2))

    if order == 2 and cross_var is None:
        def val2(step):
            up = {var: point[var] + step}
            dn = {var: point[var] - step}
            return (f(**up) - 2 * f() + f(**dn)) / (step * step)

        d1, d2 = val2(h), val2(h / 2)
        rich = (4 * d2 - d1) / 3
        return _realify(rich), float(abs(val2(h / 4) - d2))

    if order == 2 and cross_var is not None:
        if cross_var not in variables:
            raise CalculusError(f"Expression has no variable {cross_var}")
        hv = max(1.0, abs(float(point[var]))) * 1e-4
        hw = max(1.0, abs(float(point[cross_var]))) * 1e-4

        def cross(step_v, step_w):
            def at(dv, dw):
                env = {var: point[var] + dv, cross_var: point[cross_var] + dw}
                return f(**env)

            return (at(step_v, step_w) - at(step_v, -step_w)
                    - at(-step_v, step_w) + at(-step_v, -step_w)) / (4 * step_v * step_w)

        d1 = cross(hv, hw)
        d2 = cross(hv / 2, hw / 2)
        rich = (4 * d2 - d1) / 3
        return _realify(rich), float(abs(cross(hv / 4, hw / 4) - d2))

    raise CalculusError("Order must be 1 or 2")


# ---------------------------------------------------------------- 辅助

def find_singularities(expr, lower, upper, x_name="x", samples=400):
    """粗略探测区间内的可疑点（函数值突变/变为非有限）——用于解释积分困难。"""
    compiled = CompiledExpression(expr, variables=[x_name])
    base = compiled.function() if compiled.variables else (lambda _x: compiled.evaluate())
    if not (np.isfinite(lower) and np.isfinite(upper)) or lower >= upper:
        return []
    xs = np.linspace(lower, upper, samples)
    suspects = []
    prev = None
    for x in xs:
        try:
            v = float(base(float(x)))
        except Exception:
            suspects.append(float(x))
            prev = None
            continue
        if not np.isfinite(v):
            suspects.append(float(x))
        elif prev is not None and abs(v) > 1e12 and abs(v) > abs(prev) * 100:
            suspects.append(float(x))
        prev = v
    # 采样点代表区间，去重并只保留有限区间内的
    return sorted(set(round(s, 6) for s in suspects))[:10]
