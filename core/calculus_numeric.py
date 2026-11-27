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
from .expr_eval import preprocess


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
def _looks_divergent(f, lower, upper):
    """粗判有限端点处是否**真的**发散。

    不能用「函数值 > 某个绝对值」来判断 —— 幅值大与不可积没有等价关系：
    ``2e6*x`` 在 [0,1] 上积分就是 1e6，完全正常；``x**-0.9`` 在 0 处虽然
    趋向无穷，但积分收敛到 10（可积的端点奇异性）。

    这里看的是 ``f(bound + eps) * eps`` 随 eps -> 0 的走向：

        x**-0.9 : eps**0.1      -> 0     收敛
        1/x     : 1             -> 常数  对数发散（交给积分器 + 警告）
        x**-1.5 : eps**-0.5     -> inf   幂律发散

    只有「明显趋于无穷」才算确定发散；边界情况不拦，避免误拒正常积分。
    """
    for bound, direction in ((lower, 1), (upper, -1)):
        if not np.isfinite(bound):
            continue
        products = []
        for eps in (1e-3, 1e-4, 1e-5, 1e-6):
            x = bound + direction * eps
            try:
                v = abs(complex(f(x)))
            except Exception:                        # noqa: BLE001
                continue
            if not np.isfinite(v):
                return True
            products.append(v * eps)
        if len(products) < 3:
            continue
        # 单调增长且末尾比开头大一个量级以上 -> 幂律发散
        if products[-1] > products[0] * 10 and products[-1] > 1e-3:
            return True
    return False


def integrate(expr, lower, upper, x_name="x"):
    """定积分。返回 (值, 误差估计, 警告说明列表)。

    值可能是复数：若被积函数有虚部，则实部与虚部分别积分再合成。
    """
    compiled = CompiledExpression(expr, variables=[x_name])
    f = compiled.function(x_name) if compiled.variables else (lambda _x: compiled.evaluate())

    if _looks_divergent(f, lower, upper):
        raise CalculusError(
            "该积分在区间端点附近发散（被积函数趋于无穷），无法给出有限结果。"
            "若奇点在区间内部，请拆成两段分别积分。"
        )

    def _quad_scalar(g):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            val, err = _quad_mod.quad(g, lower, upper, limit=200)
            return val, err, [str(w.message) for w in caught]

    # 始终分别积分实部与虚部：scipy 的 quad 对复值函数会静默丢弃虚部，
    # 而「五点采样看虚部」并不可靠 —— 例如 i*sin(4*pi*x)^2 在
    # 0, 0.25, 0.5, 0.75, 1 上虚部全为 0，实际积分却是 0.5i。
    notes = []
    try:
        re_v, re_e, re_notes = _quad_scalar(lambda t: complex(f(t)).real)
        im_v, im_e, im_notes = _quad_scalar(lambda t: complex(f(t)).imag)
    except Exception as exc:
        raise CalculusError(f"Integration failed: {exc}") from exc

    value = complex(re_v, im_v)
    error = max(re_e, im_e)
    notes.extend(re_notes)
    notes.extend(im_notes)
    has_imag = abs(value.imag) > 1e-12 * max(1.0, abs(value.real))
    if has_imag:
        # 结果含实打实的虚部时才说明，避免对实数函数刷提示
        notes.insert(0, "被积函数是复值的，已对实部与虚部分别积分")

    if not np.isfinite(abs(value)):
        raise CalculusError(
            "积分结果为无穷大或无效值 —— 该积分很可能发散，或区间端点有奇点。"
        )
    if notes and not has_imag:
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
    base = (compiled.function(x_name) if compiled.variables
            else (lambda _x: compiled.evaluate()))

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
    # 先问符号层：它能给定论（可导 / 不可导）。数值判据只在它答不上来时
    # 兜底，而且只保留最保守的证据。
    # 注意把**实际自变量名**传下去：abs(t) 这类输入用默认的 x 会找不到符号。
    verdict = symbolic_differentiable(expr, x0, order, var=x_name)

    if order == 1:
        rich = (4 * d2 - d1) / 3
        err = abs(d3 - d2)
        if verdict is None:
            _kink_check(f, x0, h0 / 4, 1)
    else:
        # 二阶差分误差是 O(h^2)，外推系数同为一阶
        rich = (4 * d2 - d1) / 3
        err = abs(d3 - d2)
        # 二阶导存在的前提是一阶导存在：对**一阶**中心差分做折点检查。
        # |x| 在 0 的二阶差分本身会给出 2/h 这种巨大值，不是 0，
        # 所以不能直接拿 rich 去套一阶的判据。
        # 二阶导存在的前提是一阶导存在。必须**先**查一阶：
        # |x| 在 0 处的二阶单侧差商两边都恰好是 0（折点对称抵消），
        # 只看二阶会漏掉；而 x*abs(x) 一阶可导、二阶不可导，只有二阶查得出来。
        if verdict is None:
            _kink_check(f, x0, h0 / 4, 1)
            _kink_check(f, x0, h0 / 4, 2)
    if verdict is False:
        raise CalculusError(
            "Not differentiable at this point. The left and right difference "
            "quotients converge to different values (or diverge). A symmetric "
            "difference is their average, not a derivative.")
    if not np.isfinite(abs(rich)):
        raise CalculusError("Derivative is invalid (not differentiable here, or the function diverges)")
    return _realify(rich), float(abs(err))
_SYMPY_LOCALS = None


def _sympy_locals():
    """惰性构造，避免没装 sympy 时影响模块导入。"""
    global _SYMPY_LOCALS
    if _SYMPY_LOCALS is not None:
        return _SYMPY_LOCALS
    import sympy as sp

    _SYMPY_LOCALS = {
        "inf": sp.oo, "e": sp.E, "pi": sp.pi,
        "tau": getattr(sp, "tau", 2 * sp.pi), "i": sp.I,
        "abs": sp.Abs, "sqrt": sp.sqrt, "cbrt": sp.cbrt, "sign": sp.sign,
        "re": sp.re, "im": sp.im, "conj": sp.conjugate, "angle": sp.arg,
        "ceil": sp.ceiling, "floor": sp.floor,
        "sin": sp.sin, "cos": sp.cos, "tan": sp.tan,
        "asin": sp.asin, "acos": sp.acos, "atan": sp.atan,
        "sinh": sp.sinh, "cosh": sp.cosh, "tanh": sp.tanh,
        "exp": sp.exp, "expm1": lambda t: sp.exp(t) - 1,
        "log": sp.log, "log2": lambda t: sp.log(t, 2),
        "log10": lambda t: sp.log(t, 10),
        "log1p": lambda t: sp.log(1 + t),
    }
    return _SYMPY_LOCALS


def to_sympy(expr: str, var: str = "x"):
    """把内部表达式语法转成 sympy 表达式；转不了返回 None。

    先走 expr_eval 的预处理（隐式乘法 ``2x``、上标 ``x²``），再用
    ``rationalize`` 把小数常量变成**精确有理数**。

    最后一步是关键：``sqrt(x^2+0.1)`` 如果保留 ``Float(0.1)``，
    ``f(0) = sqrt(0.1)`` 就是近似值，差商 ``(f(h)-f(0))/h`` 带舍入残差，
    除以 h 后变成伪无穷。换成 ``Rational(1, 10)`` 后整条链路精确。
    """
    try:
        import sympy as sp
        from sympy.parsing.sympy_parser import (
            parse_expr, rationalize, standard_transformations,
        )
    except ImportError:
        return None
    try:
        raw = preprocess(expr)
    except Exception:                            # noqa: BLE001
        raw = expr
    try:
        source = raw.replace("^", "**")
        transforms = standard_transformations + (rationalize,)
        sym = parse_expr(source, local_dict=_sympy_locals(),
                         transformations=transforms)
    except Exception:                            # noqa: BLE001
        return None
    if not isinstance(sym, sp.Expr):
        return None
    return sym




def symbolic_differentiable(expr: str, x0: float, order: int = 1,
                            var: str = "x", *, max_ops: int = 400,
                            fixed: dict | None = None):
    """用左右差商的**极限**判断 x0 处 order 阶导数是否存在。

    返回 True（可导）/ False（不可导）/ None（判不了）。

    三个要点：

    1. 求的是**原始差商的极限**，不是形式导数在点上的值。``abs(x)`` 的
       形式导数是 ``sign(x)``，``sign(0) = 0``，代入会得出「导数为 0」的
       假结论；而左右差商分别是 -1 和 +1。

    2. **全程用精确有理数**：小数常量由 ``rationalize`` 变成 Rational，
       求值点由 ``Rational(str(x0))`` 给出。任何环节提前浮点化，舍入残差
       除以 h 都会变成伪无穷。

    3. **不引入任意符号占位**。曾经用 ``Symbol('_a')`` 表示求值点，
       结果 ``floor`` 这类函数在带正负号假设的任意 a 上求极限会失控
       （实测 8 秒不返回）。现在 a 是具体数字，差商直接围绕它展开。
    """
    try:
        import sympy as sp
    except ImportError:
        return None

    # 高阶导数存在的前提是低阶存在。|x| 的二阶差商两边都是 0（折点对称
    # 抵消），只查二阶会漏；x*abs(x) 一阶可导、二阶不可导，只有二阶查得出来。
    if 1 < order <= 2:
        lower = symbolic_differentiable(expr, x0, order - 1, var,
                                        max_ops=max_ops, fixed=fixed)
        if lower is not True:
            return lower

    sym = to_sympy(expr, var)
    if sym is None:
        return None
    try:
        if sp.count_ops(sym) > max_ops:
            return None
    except Exception:                            # noqa: BLE001
        return None

    # 按**名字**从表达式自己的 free_symbols 里取符号。
    # Symbol('y') 和 Symbol('y', real=True) 是两个不同对象，
    # 自己造一个新符号去 subs 会静默失败 —— 那样 y=0 根本不会生效。
    by_name = {}
    for symbol in sym.free_symbols:
        by_name[str(symbol)] = symbol
    target = by_name.get(var)
    if target is None:
        return None

    def exact(value):
        """转成精确有理数；0 直接用 Integer，避免 0.0 变 Float。"""
        number = float(value)
        if number == 0.0:
            return sp.Integer(0)
        return sp.Rational(str(number))

    try:
        subs_fixed = {}
        for key, value in (fixed or {}).items():
            if key == var:
                continue
            symbol = by_name.get(key)
            if symbol is not None:
                subs_fixed[symbol] = exact(value)

        def at(offset):
            value = sym.subs(target, offset)
            if subs_fixed:
                value = value.subs(subs_fixed)
            return value

        a = exact(x0)
        h = sp.Symbol("_h", positive=True)
        f0 = at(a)

        if order == 1:
            right_expr = (at(a + h) - f0) / h
            left_expr = (f0 - at(a - h)) / h
        else:
            right_expr = (at(a + 2 * h) - 2 * at(a + h) + f0) / h ** 2
            left_expr = (f0 - 2 * at(a - h) + at(a - 2 * h)) / h ** 2

        right = sp.limit(right_expr, h, 0, "+")
        left = sp.limit(left_expr, h, 0, "+")
    except Exception:                            # noqa: BLE001
        return None

    for value in (right, left):
        if value is None or value.has(sp.zoo, sp.nan):
            return False                         # 发散 -> 不可导
        if value.is_finite is False:
            return False

    try:
        if sp.simplify(right - left) == 0:
            return True
    except Exception:                            # noqa: BLE001
        return None

    try:
        if right.equals(left):
            return True
    except Exception:                            # noqa: BLE001
        return None
    return False




def _sided_quotients(f, x0, step, order):
    """返回 (左侧, 右侧) 的单侧差商。

    order=1 用前向/后向一阶差商；order=2 用三点的二阶单侧差商。
    """
    if order == 1:
        right = (f(x0 + step) - f(x0)) / step
        left = (f(x0) - f(x0 - step)) / step
    else:
        right = (f(x0 + 2 * step) - 2 * f(x0 + step) + f(x0)) / (step * step)
        left = (f(x0) - 2 * f(x0 - step) + f(x0 - 2 * step)) / (step * step)
    return left, right


def _kink_check(f, x0, step, order):
    """判断 x0 处 order 阶导数是否真的存在。

    核心思路：**看左右差商之差随步长缩小的行为**。

      * 光滑函数：两边都收敛到同一个导数，gap(h) ~ C*h，
        步长缩到 1/4，gap 也缩到约 1/4。
      * 折点：两边分别收敛到 f'_+ 和 f'_-，gap(h) -> |f'_+ - f'_-|，
        是个**非零常数**，步长再小也不缩。

    对固定步长比较左右差商是错的：光滑极值附近左右差商本来就不同
    （1000*x**2 在 0 处是 ∓2000h 量级），那不是折点，那是二阶导。

    只有 gap 明显不随步长收敛时才拒绝。取不到值（越界、求值失败）就
    放弃判断，交给调用方按原逻辑处理。
    """
    scales = [step / (4.0 ** i) for i in range(6)]
    gaps = []
    sample = None
    for scale_step in scales:
        try:
            left, right = _sided_quotients(f, x0, scale_step, order)
        except Exception:                        # noqa: BLE001
            return                                   # 取不到值就不判
        gaps.append(abs(left - right))
        if sample is None:
            sample = (left, right)

    # 两侧一上来就一致（浮点噪声量级）：没得判
    if gaps[0] <= 1e-9 * max(1.0, abs(sample[0]), abs(sample[1])):
        return

    # gap 必须**单调不增**（允许 10% 抖动）才可能是折点。
    # 变大说明步长还没解析到函数尺度 —— 例如 cos(200000*x) 在默认步长
    # 下 `[x0-h, x0+h]` 内振荡多次，那是采样不足，不是不可导。
    monotone = all(gaps[i + 1] <= gaps[i] * 1.1
                   for i in range(len(gaps) - 1))
    if not monotone:
        return

    # 数值判据只是**兜底**，用在符号算不出来的时候。
    #
    # 门槛 0.999、尺度跨 4^5 = 1024 倍：只有 gap 在这么宽的区间里几乎
    # 完全不变才算证据 —— 那是 |x| 的特征（gap 恒为 2）。
    #
    # 为什么不继续调比值：有限采样无法区分「gap 趋于 0」和「gap 趋于
    # 非零常数」。
    #   abs(x)+10000*x^2  gap = 2 + 20000h，比值 0.803，看着在收敛，
    #                     其实趋于 2 —— 门槛 ≤0.97 都会漏判。
    #   sqrt(x^2+1e-16)   gap 看着像平台，其实趋于 0 —— 会误拒。
    # 所以兜底只抓「完全不变」，其余交给符号层。
    if gaps[-1] > 0.999 * gaps[0]:
        left, right = sample
        raise CalculusError(
            "Not differentiable at this point: the one-sided difference "
            f"quotients converge to different values "
            f"({left:+.6g} from the left, {right:+.6g} from the right; "
            f"their gap stays near {gaps[-1]:+.6g} while the step shrinks "
            "by a factor of 64). A symmetric difference is an average of "
            "the two sides, not a derivative.")


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

    # 偏导同样先问符号层。fixed 让它先把其它变量代入，只剩 var 一个自由符号。
    verdict = symbolic_differentiable(expr, float(point[var]), order,
                                      var=var, fixed=dict(point))

    if order == 1:
        def val(step):
            up = {var: point[var] + step}
            dn = {var: point[var] - step}
            return (f(**up) - f(**dn)) / (2 * step)

        d1, d2 = val(h), val(h / 2)
        rich = (4 * d2 - d1) / 3
        if verdict is None:
            # 同一族问题：abs(x)+y 在原点的偏导也是对称差分的零点
            _kink_check(lambda t, _v=var: f(**{_v: t}), point[var], h / 4, 1)
        elif verdict is False:
            raise CalculusError(
                f"Partial derivative w.r.t. {var} does not exist at this "
                "point: the left and right difference quotients differ.")
        return _realify(rich), float(abs(val(h / 4) - d2))

    if order == 2 and cross_var is None:
        def val2(step):
            up = {var: point[var] + step}
            dn = {var: point[var] - step}
            return (f(**up) - 2 * f() + f(**dn)) / (step * step)

        d1, d2 = val2(h), val2(h / 2)
        rich = (4 * d2 - d1) / 3
        if verdict is None:
            # 二阶偏导同样：先查一阶存在，再查二阶收敛
            _kink_check(lambda t, _v=var: f(**{_v: t}), point[var], h / 4, 1)
            _kink_check(lambda t, _v=var: f(**{_v: t}), point[var], h / 4, 2)
        elif verdict is False:
            raise CalculusError(
                f"Second partial derivative w.r.t. {var} does not exist "
                "at this point.")
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
