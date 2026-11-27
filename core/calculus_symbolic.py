"""符号微积分（B 版，基于 sympy）。

与 A 版（calculus_numeric）互补：
  * A 版给数值 + 误差估计，任何可数值求值的函数都能算
  * B 版给精确表达式（如 d/dx(x²sin x) = 2x·sin x + x²·cos x），能格式化输出

sympy 是可选依赖：没装时本模块的 available() 返回 False，
界面据此把符号结果区置灰，而不是让整个计算器崩掉。
"""

import ast
import math

from .expr_eval import preprocess

try:  # pragma: no cover - 取决于环境
    import sympy as _sp
except Exception:  # noqa: BLE001
    _sp = None


class SymbolicError(ValueError):
    """符号计算失败（无法解析、无法积分等）。"""


#: 边界里常见的符号常量（先查表，保住 pi 这类值的精确性）
_BOUND_SYMBOLS = {
    "pi": lambda sp: sp.pi, "π": lambda sp: sp.pi,
    "e": lambda sp: sp.E,
    "inf": lambda sp: sp.oo, "+inf": lambda sp: sp.oo,
    "infinity": lambda sp: sp.oo, "∞": lambda sp: sp.oo,
    "-inf": lambda sp: -sp.oo, "-infinity": lambda sp: -sp.oo,
    "-∞": lambda sp: -sp.oo,
    "oo": lambda sp: sp.oo, "-oo": lambda sp: -sp.oo,
}


def _sympy_constant(value):
    """把常量表达式解析成 sympy 对象，**保留精确性**。

    ``"0.5"`` -> ``1/2``、``"pi/2"`` -> ``pi/2``、``"2*pi"`` -> ``2*pi``。

    之前对 "pi/2" 这种组合表达式会退回浮点（``1.5707963...``），
    拿它当 ``series()`` 的展开点会算出错误结果（sin 在 pi/2 展开成 0）。
    所以这里用受限 AST 求值，把 pi / e 映射到 sympy 的精确常量。

    不用 ``sympify()``：它会 eval 用户输入。
    """
    sp = _require()

    if not isinstance(value, str):
        if isinstance(value, bool):
            raise SymbolicError("A boolean is not a valid constant")
        if isinstance(value, float):
            if math.isinf(value):
                return sp.oo if value > 0 else -sp.oo
            if math.isnan(value):
                raise SymbolicError("NaN is not a valid constant")
            return sp.Float(value)
        return sp.Integer(int(value))

    raw = value.strip()
    if not raw:
        raise SymbolicError("Empty constant")

    # 先查表：处理 "∞" 这种 AST 解析不了（或者很容易搞错减号）的写法
    table = _BOUND_SYMBOLS.get(raw.lower())
    if table is not None:
        return table(sp)

    import ast as _ast

    try:
        tree = _ast.parse(raw, mode="eval")
    except SyntaxError as exc:
        raise SymbolicError(
            f"Cannot parse constant {value!r}: {exc}") from exc

    def walk(node):
        if isinstance(node, _ast.Expression):
            return walk(node.body)
        if isinstance(node, _ast.Constant):
            v = node.value
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise SymbolicError("Only numbers are allowed in constants")
            if isinstance(v, int):
                return sp.Integer(v)
            # 用**原始源文本**转有理数。ast.parse 已经把
            # 1.0000000000000001 变成了 float 1.0，从 node.value 里拿不回
            # 原来的位数 —— 那会让积分边界这类输入悄悄丢精度。
            segment = _ast.get_source_segment(raw, node)
            if segment:
                try:
                    return sp.Rational(segment)
                except Exception:                # noqa: BLE001
                    pass
            return sp.Rational(str(v))
        if isinstance(node, _ast.Name):
            builder = _BOUND_SYMBOLS.get(node.id.lower())
            if builder is None:
                raise SymbolicError(f"Unknown name in constant: {node.id!r}")
            return builder(sp)
        if isinstance(node, _ast.BinOp):
            left, right = walk(node.left), walk(node.right)
            op = type(node.op)
            if op is _ast.Add:
                return left + right
            if op is _ast.Sub:
                return left - right
            if op is _ast.Mult:
                return left * right
            if op is _ast.Div:
                return left / right
            if op is _ast.Pow:
                return left ** right
            raise SymbolicError("Unsupported operator in constant")
        if isinstance(node, _ast.UnaryOp):
            operand = walk(node.operand)
            if isinstance(node.op, _ast.UAdd):
                return operand
            if isinstance(node.op, _ast.USub):
                return -operand
            raise SymbolicError("Unsupported operator in constant")
        raise SymbolicError(
            f"Unsupported syntax in constant: {type(node).__name__}")

    return walk(tree)


def _sympy_bound(value):
    """积分上下限 / 泰勒展开点的解析入口。

    保留这个名字是因为 calculus_symbolic 里多处调用；实现已统一到
    ``_sympy_constant``（精确常量解析）。
    """
    return _sympy_constant(value)
def _require():
    if _sp is None:
        raise SymbolicError("sympy is not installed; symbolic features are unavailable (pip install sympy)")
    return _sp


#: 允许出现的符号函数：名字 -> 如何从 sympy 取到（必要时包一层）。
#:
#: 不能直接 ``getattr(sp, name)`` —— sympy 里没有 ``log10`` / ``log2``
#: 这两个属性（只有 ``log(x, base)``），那样调用会抛 AttributeError。
#: 显式列出来也顺便对齐了数值层的命名（abs/sign/re/im/conjugate/arg）。
_FUNC_BUILDERS = {
    "sin": lambda sp: sp.sin,
    "cos": lambda sp: sp.cos,
    "tan": lambda sp: sp.tan,
    "asin": lambda sp: sp.asin,
    "acos": lambda sp: sp.acos,
    "atan": lambda sp: sp.atan,
    "sinh": lambda sp: sp.sinh,
    "cosh": lambda sp: sp.cosh,
    "tanh": lambda sp: sp.tanh,
    "exp": lambda sp: sp.exp,
    "log": lambda sp: sp.log,
    "log10": lambda sp: (lambda x: sp.log(x, 10)),
    "log2": lambda sp: (lambda x: sp.log(x, 2)),
    "sqrt": lambda sp: sp.sqrt,
    # 与 np.cbrt 保持一致：实立方根，不是复主值根
    "cbrt": lambda sp: (lambda t: sp.real_root(t, 3)),
    "abs": lambda sp: sp.Abs,
    "Abs": lambda sp: sp.Abs,
    # sign 由 abs 求导自然产生，放开它，二阶导数才不会卡住
    "sign": lambda sp: sp.sign,
    "re": lambda sp: sp.re,
    "im": lambda sp: sp.im,
    "conjugate": lambda sp: sp.conjugate,
    "arg": lambda sp: sp.arg,
}


def _make_symbol(name, assumptions=None):
    """按假设创建 sympy 符号。

    假设很重要：``exp(2*I*pi*n)`` 只有 n 是**整数**时才能化简为 1；
    若 n 只是实数或不加假设，sympy 会保留原式（这是正确的）。
    """
    sp = _require()
    kind = (assumptions or {}).get(name, "real")
    kind = (kind or "real").lower()
    if kind == "integer":
        return sp.Symbol(name, integer=True)
    if kind == "positive":
        return sp.Symbol(name, positive=True)
    if kind == "nonnegative":
        return sp.Symbol(name, nonnegative=True)
    if kind == "complex":
        return sp.Symbol(name, complex=True)
    if kind == "real":
        return sp.Symbol(name, real=True)
    return sp.Symbol(name, real=True)


def _safe_sympify(expr, variables, assumptions=None):
    """把表达式安全地转成 sympy 表达式。

    不直接 sympify 用户字符串（sympify 会 eval）。做法：
      1. 用 expr_eval 的白名单校验 + 预处理
      2. ast 解析后，只把白名单里的名字映射到 sympy 符号/函数

    assumptions: {变量名: 'real'|'integer'|'positive'|'nonnegative'|'complex'}
    """
    sp = _require()
    py = preprocess(expr)
    try:
        tree = ast.parse(py, mode="eval")
    except SyntaxError as exc:
        raise SymbolicError(f"Syntax error: {exc.msg}") from exc

    symbols = {name: _make_symbol(name, assumptions) for name in variables}

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise SymbolicError("Only numeric constants are allowed")
            return sp.Integer(node.value) if isinstance(node.value, int) else sp.Float(node.value)
        if isinstance(node, ast.Name):
            if node.id in symbols:
                return symbols[node.id]
            if node.id == "pi":
                return sp.pi
            if node.id == "e":
                return sp.E
            if node.id in ("i", "I"):
                return sp.I
            raise SymbolicError(f"Unknown symbol: {node.id}")
        if isinstance(node, ast.UnaryOp):
            val = walk(node.operand)
            return -val if isinstance(node.op, ast.USub) else val
        if isinstance(node, ast.BinOp):
            left, right = walk(node.left), walk(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Pow):
                return left ** right
            if isinstance(node.op, ast.Mod):
                raise SymbolicError("Symbolic computation does not support %")
            raise SymbolicError("Unsupported operator")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise SymbolicError("Only direct function calls are allowed")
            fname = node.func.id
            builder = _FUNC_BUILDERS.get(fname)
            if builder is None:
                raise SymbolicError(
                    f"Symbolic computation does not support function: {fname}")
            args = [walk(a) for a in node.args]
            if node.keywords:
                raise SymbolicError("Keyword arguments are not supported")
            return builder(sp)(*args)
        raise SymbolicError(f"Unsupported syntax: {type(node).__name__}")

    return walk(tree)
def differentiate(expr, var="x", order=1, assumptions=None):
    """符号求导，返回 (结果字符串, LaTeX 字符串)。

    order 直接作用在**原表达式**上（``sp.diff(expr, sym, order)``），
    而不是把一阶结果转成字符串再解析一遍 —— 后者会遇到 ``abs(x)`` 的导数
    ``sign(x)`` 这类不在输入白名单里的符号，从而在二阶求导出错。
    """
    sp = _require()
    expr_s = _safe_sympify(expr, [var], assumptions)
    symbol = _make_symbol(var, assumptions)
    order = max(1, int(order))
    result = sp.diff(expr_s, symbol, order)
    result = sp.simplify(result)
    return str(result), sp.latex(result)


def integrate_symbolic(expr, var="x", lower=None, upper=None, assumptions=None):
    """符号积分。lower/upper 为 None 时求不定积分。

    返回 (结果字符串, LaTeX 字符串, 说明列表)。
    """
    sp = _require()
    expr_s = _safe_sympify(expr, [var], assumptions)
    symbol = _make_symbol(var, assumptions)
    notes = []

    if lower is None and upper is None:
        result = sp.integrate(expr_s, symbol)
        if result.has(sp.Integral):
            notes.append("sympy could not find an elementary antiderivative; the result is kept as an unevaluated integral")
        result = sp.simplify(result)
        return str(result) + " + C", sp.latex(result), notes

    if lower is None or upper is None:
        raise SymbolicError("A definite integral requires both bounds")
    lo = _sympy_bound(lower)
    hi = _sympy_bound(upper)
    result = sp.integrate(expr_s, (symbol, lo, hi))
    if result.has(sp.Integral):
        raise SymbolicError("sympy could not find a closed form for this integral")
    simplified = sp.simplify(result)
    return str(simplified), sp.latex(simplified), notes


def latex_of(expr) -> str:
    """把 sympy 表达式转成 LaTeX。

    界面层刻意不 import sympy（没装时也要能给出提示而不是崩），所以走
    这个函数；转换失败时退回普通字符串，不抛异常。
    """
    sp = _require()
    try:
        return sp.latex(expr)
    except Exception:                            # noqa: BLE001
        return str(expr)


def constant_of(value):
    """把常量表达式解析成 sympy 对象。

    界面用它构造平移基底：展开点写 "1+1" 时必须得到 2，直接拼原始
    字符串会拼出 (x - 1+1) —— 那等于 x，不是 x-2。
    """
    return _sympy_constant(value)


def basis_text(var, center_expr):
    """构造平移基底 ``(var - center)`` 的字符串。

    展开点本身可能是加法或带负号（``pi/2 + 1``、``pi/2 - 1``、
    ``-pi/2 - 1``）。靠字符串判断和删负号并不等于对整体取负：
    ``str(pi/2 - 1)`` 是 ``"-1 + pi/2"``，删掉开头那个负号会得到
    ``x + 1 + pi/2``，而正确的基底是 ``x + 1 - pi/2``。

    所以直接做符号减法，让 sympy 归一化，再把结果整体括起来。
    """
    if center_expr == 0:
        return var
    sp = _require()
    try:
        symbol = sp.Symbol(var)
        shifted = sp.simplify(symbol - center_expr)
        return f"({shifted})"
    except Exception:                            # noqa: BLE001
        # 兜底：整体加括号，至少不会算错
        return f"({var} - ({center_expr}))"


def constant_float(value) -> float:
    """把常量表达式（"pi/2"、"0.5"…）转成 Python 浮点。

    界面画图要知道展开点在哪，但不应直接碰 _sympy_constant。
    """
    return float(_sympy_constant(value))


def lambdify_of(expr):
    """把 sympy 表达式转成能用 numpy 数组调用的函数。

    界面画曲线用：逐个 ``subs()`` 求值太慢，lambdify 之后一次算一整条。
    没有自由符号（纯常数）时返回一个忽略参数的函数。
    """
    sp = _require()
    syms = sorted(getattr(expr, "free_symbols", ()), key=str)
    if not syms:
        value = complex(expr)
        return lambda *args, **kwargs: value
    return sp.lambdify(syms, expr, modules="numpy")


def _error_curve(expr, var, poly, xs):
    """在给定点上算 ``|f - P|``。

    标量结果（常数函数 / 常数多项式）要广播到 xs 的形状，否则绘图和
    误差比较会因为 (601,) 与 () 维度不一致而报错。
    """
    import numpy as np

    from .expr_eval import CompiledExpression

    compiled = CompiledExpression(expr, variables=[var])
    fn = (compiled.function(var) if compiled.variables
          else (lambda _x: compiled.evaluate()))
    try:
        f_vals = np.asarray(fn(xs), dtype=complex)
    except Exception:                            # noqa: BLE001
        f_vals = np.array([complex(fn(x)) for x in xs], dtype=complex)
    if f_vals.shape != xs.shape:
        f_vals = np.broadcast_to(f_vals, xs.shape)

    if getattr(poly, "free_symbols", None):
        p_vals = np.asarray(lambdify_of(poly)(xs), dtype=complex)
        if p_vals.shape != xs.shape:
            p_vals = np.broadcast_to(p_vals, xs.shape)
    else:
        p_vals = np.full(xs.shape, complex(poly), dtype=complex)

    return np.abs(f_vals - p_vals)


def _sampled_grid(center, span, count, phase):
    """第 phase 张采样网格。

    phase 0 是纯等距；其它相位加**确定性抖动**（可复现），用来打破周期性
    混叠 —— 等距网格很容易和振荡函数的周期同步。

    每张网格都**显式包含精确的展开点**：抖动会把最近的采样点推离
    center，两侧端点就可能都落在同一侧，算出负的半径。
    """
    import numpy as np

    base = np.linspace(center - span, center + span, count)
    if count >= 2 and phase > 0:
        step = base[1] - base[0]
        if step > 0:
            rng = np.random.default_rng(1234 + phase)
            jitter = rng.uniform(-0.45, 0.45, count) * step
            base = np.clip(base + jitter, center - span, center + span)
    return np.unique(np.concatenate(([center], base)))


def _hold_once(expr, var, poly, center, tolerance, xs, span):
    """在一张网格上，从展开点向两边走到误差超限为止。"""
    import numpy as np

    err = _error_curve(expr, var, poly, xs)
    index = int(np.argmin(np.abs(xs - center)))
    if not np.isfinite(err[index]) or err[index] >= tolerance:
        return 0.0, center, center

    left = index
    while left > 0 and np.isfinite(err[left - 1]) and err[left - 1] < tolerance:
        left -= 1
    right = index
    while (right < len(xs) - 1 and np.isfinite(err[right + 1])
           and err[right + 1] < tolerance):
        right += 1

    # 端点必须分居展开点两侧。不分居说明最近的可行采样点被推到一边了，
    # 那是分辨率不够，不能当成「紧挨着就超差」。
    if not (xs[left] <= center <= xs[right]):
        return 0.0, center, center

    radius = min(center - xs[left], xs[right] - center)
    if radius > 0:
        return max(0.0, float(radius)), float(xs[left]), float(xs[right])

    # 半径 0 有两种可能：误差真的紧挨着就超了，或者有效窗口比采样步长
    # 还窄。在中心附近逐级加密再试 —— 能分辨这两种情况，也能测出
    # exp(x) 展开到 0 阶这种「窗口只有 ±0.001」的例子。
    for fraction in (0.25, 0.02, 0.002, 0.0002):
        width = span * fraction
        if width <= 0:
            continue
        fine = np.unique(np.concatenate(
            ([center], np.linspace(center - width, center + width, 401))))
        fine_err = _error_curve(expr, var, poly, fine)
        k = int(np.argmin(np.abs(fine - center)))
        if not np.isfinite(fine_err[k]) or fine_err[k] >= tolerance:
            break
        lo_i = k
        while (lo_i > 0 and np.isfinite(fine_err[lo_i - 1])
               and fine_err[lo_i - 1] < tolerance):
            lo_i -= 1
        hi_i = k
        while (hi_i < len(fine) - 1 and np.isfinite(fine_err[hi_i + 1])
               and fine_err[hi_i + 1] < tolerance):
            hi_i += 1
        if not (fine[lo_i] <= center <= fine[hi_i]):
            continue
        fine_radius = max(0.0, min(center - fine[lo_i], fine[hi_i] - center))
        if fine_radius > 0:
            return float(fine_radius), float(fine[lo_i]), float(fine[hi_i])

    return 0.0, center, center


def expansion_holds(expr, var, poly, point, tolerance=1e-6,
                    span=None, samples=801, grids=4):
    """数值估计展开式在多大范围内还够准。

    返回 ``(radius, left, right, spread, step)``：

      * ``radius`` —— 从展开点向两侧走到误差超过 tolerance 的距离（>= 0）
      * ``left`` / ``right`` —— 对应的区间端点，保证 ``left <= point <= right``
      * ``spread`` —— 各张网格给出的半径之间的最大差值
      * ``step`` —— 最细网格的采样间距，用来判断分辨率

    .. warning:: **这是采样估计，不是保证的上界。**

       高频函数可能被网格漏采。用多张网格（等距 + 确定性抖动）并取最
       保守的结果能大幅降低混叠，但严格的区间需要余项界 —— 这里给不了。
       ``spread`` 偏大就说明函数变化比采样快，结果只能当参考。
    """
    center = float(point)
    if span is None:
        span = max(1.0, abs(center)) * 1.5

    results = []
    steps = []
    for phase in range(max(1, grids)):
        count = samples + 8 * phase
        xs = _sampled_grid(center, span, count, phase)
        steps.append(float(xs[1] - xs[0]) if len(xs) > 1 else float(span))
        results.append(_hold_once(expr, var, poly, center, tolerance, xs, span))

    radius, left, right = min(results, key=lambda r: r[0])
    spread = max(r[0] for r in results) - min(r[0] for r in results)
    return radius, left, right, spread, (min(steps) if steps else float(span))


def format_polynomial(coeffs, basis="x"):
    """把系数列表拼成**升幂**的多项式字符串。

    ``[1, 1, 1/2]`` -> ``"1 + x + x**2/2"``。

    零系数跳过（`sin` 的偶次项不该写成 ``0*x**2``）；系数为 1 时省略；
    负数用 ``" - "`` 连接，不会出现 ``"+ -1/6*x**3"``。
    """
    parts: list[tuple[bool, str]] = []
    for power, coeff in enumerate(coeffs):
        if coeff == 0:
            continue
        negative = bool(getattr(coeff, "is_negative", False))
        magnitude = -coeff if negative else coeff
        if power == 0:
            body = str(magnitude)
        else:
            variable = basis if power == 1 else f"{basis}**{power}"
            if magnitude == 1:
                body = variable
            else:
                coeff_text = str(magnitude)
                # 系数本身是加法时必须整体括起来：exp(x)+x 在 1 处的一阶
                # 系数是 1+E，不括起来会拼成 "1 + E + 1 + E*(x - 1)"，
                # 在 x=1 处算出来是 2+E，而不是正确的 1+E。
                if " " in coeff_text or "+" in coeff_text:
                    coeff_text = f"({coeff_text})"
                body = f"{coeff_text}*{variable}"
        parts.append((negative, body))

    if not parts:
        return "0"
    first_negative, first_body = parts[0]
    text = ("-" + first_body) if first_negative else first_body
    for negative, body in parts[1:]:
        text += (" - " if negative else " + ") + body
    return text


def coefficient_list(poly, degree, point=0):
    """把展开式按 ``(x - point)`` 的幂次拆成系数，**索引就是幂次**。

    界面要「每一项都写出来」时用这个：符号表达式会省掉零项、还按降幂
    排，看不出「第 k 阶对应什么系数」。

    例如 sin(x) 展开到 5 阶返回
    ``[0, 1, 0, -1/6, 0, 1/120]``，长度是 ``degree + 1``。
    """
    sp = _require()
    degree = max(0, int(degree))

    symbols = sorted(getattr(poly, "free_symbols", ()), key=str)
    if not symbols:                      # 常数多项式：只有 0 次项
        return [poly] + [sp.Integer(0)] * degree

    symbol = symbols[0]
    try:
        # 允许直接传界面里的字符串（"pi/2" 之类），保持精确
        shift = (_sympy_constant(point) if isinstance(point, str)
                 else sp.sympify(point))
    except Exception:                    # noqa: BLE001
        shift = sp.Integer(0)

    # 换成 t = x - point 再取系数，非零展开点也能直接读出每一项
    t = sp.Symbol("_t")
    try:
        shifted = sp.expand(poly.subs(symbol, t + shift))
        poly_obj = sp.Poly(shifted, t)
    except Exception:                    # noqa: BLE001
        return [sp.Integer(0)] * (degree + 1)
    return [poly_obj.coeff_monomial(t ** power) for power in range(degree + 1)]


def _stable_sign_at(arg, symbol, center):
    """确认 arg 在 center 附近保持同号；返回 1 / -1 / None。

    只看点值是错的：``sign(x)+1`` 在 0 处等于 1，负侧邻域里却恒为 0。

    但**有限采样同样证明不了**。``sign(x)-sign(10000000*x+1)+2`` 在
    0 处是 1，在 ±1e-2 / ±1e-4 / ±1e-6 处都是 2，偏偏在 (-1e-7, 0) 内
    等于 0 —— 任何固定的一组探测点都可能漏掉这种窄分支。

    所以这里只对**多项式**给结论：多项式的零点有限且可求，能确证
    center 不是零点、最近的实零点也隔着一段距离。含 sign / Abs 等
    分支构造的表达式一律返回 None，保留原始的双侧存在性检查。

    采样距离以 ``|center|`` 为尺度（不是 max(1, |center|)）：
    ``cbrt(x)`` 在 -0.001 展开时，±0.01 的探测点会跨过 x=0，
    把本来严格为负的邻域误判成不稳。
    """
    sp = _require()

    try:
        if not arg.is_polynomial(symbol):
            return None                          # 分支可能藏在任意窄区间
    except Exception:                            # noqa: BLE001
        return None

    try:
        at_center = complex(arg.subs(symbol, center))
    except Exception:                            # noqa: BLE001
        return None
    if abs(at_center.imag) > 1e-12:
        return None
    value = at_center.real
    if abs(value) <= 1e-12 * max(1.0, abs(value)):
        return None                              # 点上就是 0

    # 多项式求根，确认 center 到最近实零点有正距离
    try:
        poly = sp.Poly(arg, symbol)
        if poly.degree() < 1:
            return 1 if value > 0 else -1        # 常数，必然同号
        roots = poly.nroots()
    except Exception:                            # noqa: BLE001
        return None

    center_real = float(center)
    nearest_gap = None
    for root in roots:
        try:
            root_complex = complex(root)
        except Exception:                        # noqa: BLE001
            return None
        if abs(root_complex.imag) > 1e-9:
            continue                             # 复根不影响实轴符号
        gap = abs(root_complex.real - center_real)
        # 离中心的相对距离太小，说明分支离得太近，不化简
        if gap <= 1e-12 * max(1.0, abs(center_real)):
            return None
        if nearest_gap is None or gap < nearest_gap:
            nearest_gap = gap

    # 补一次采样作为数值交叉检查，防止 nroots 有误差。
    #
    # 探测距离必须以**最近根**为上限：nroots 已经知道根在哪里，
    # 采样越过它就是采样自己错了。x-1 在 0.999 处，根在 1（距 0.001），
    # 若按 scale=|center|≈1 取 ±0.01，探测点会跨过 1，把本来稳定的
    # 负分支误判成不稳。
    scale = abs(center_real) if center_real != 0 else 1.0
    ceiling = nearest_gap / 4.0 if nearest_gap is not None else scale
    for magnitude in (1e-2, 1e-4, 1e-6):
        delta = min(scale * magnitude, ceiling)
        if delta <= 0:
            continue
        for offset in (-delta, delta):
                try:
                    probe = complex(arg.subs(symbol, center_real + offset))
                except Exception:                # noqa: BLE001
                    return None
                if abs(probe.imag) > 1e-9 * max(1.0, abs(probe)):
                    return None
                real = probe.real
                if abs(real) <= 1e-12 * max(1.0, abs(real)):
                    return None
                if (1 if real > 0 else -1) != (1 if value > 0 else -1):
                    return None

    return 1 if value > 0 else -1


def _localize_at_center(expr_s, symbol, center):
    """按展开点把 Abs / sign / 实根的分支固定下来。

    ``real_root(x, 3)`` 会展开成 ``Abs(x)**(1/3)*sign(x)``，对它逐阶求导
    再求左右极限极慢（不化简时 ``cbrt(x)`` 在 -1 的校验 30 秒都跑不完）。

    需要看的是 **Abs/sign 内部那个表达式**的符号，不是自变量本身：
    ``cbrt(x-1)`` 在 0 附近展开时 x 可正可负，但 x-1 始终为负。

    但**点值不等于局部符号**。``x*sign(sign(x)+1)`` 的外层 sign 参数
    ``sign(x)+1`` 在 0 处是 1，负侧却恒为 0；若据此化简，原函数会被洗成
    ``x``，真正的一阶不可导就查不出来了。

    所以每个替选条件都要先通过 :func:`_stable_sign_at` 的两侧多尺度确认；
    确认不了就保持原样，让双侧存在性检查照常进行。
    """
    sp = _require()

    conditions = []
    try:
        nodes = list(sp.preorder_traversal(expr_s))
    except Exception:                            # noqa: BLE001
        return expr_s

    for node in nodes:
        is_abs = isinstance(node, sp.Abs)
        is_sign = isinstance(node, sp.sign)
        if not (is_abs or is_sign) or not getattr(node, "args", None):
            continue
        argument = node.args[0]
        if symbol not in argument.free_symbols:
            continue
        verdict = _stable_sign_at(argument, symbol, center)
        if verdict is None:
            continue                             # 分支不稳，保留原式
        conditions.append(sp.Q.positive(argument) if verdict > 0
                          else sp.Q.negative(argument))

    if not conditions:
        return expr_s

    try:
        refined = sp.refine(expr_s, sp.And(*conditions))
    except Exception:                            # noqa: BLE001
        return expr_s
    if refined is None or refined.has(sp.zoo, sp.nan):
        return expr_s
    return refined


def _validate_taylor(expr_s, symbol, poly, center, order):
    """确认 poly 真的是 expr 在 center 处的 order 阶泰勒多项式。

    单看「是不是多项式」不够：sympy 对 ``abs(x)`` 在 0 处会给出 ``x``，
    那是**单侧**展开，不是泰勒多项式。所以还要数值比一下展开点两侧。

    拒绝的情况：

      * 结果不是多项式（``1/x``、``log(x)``、``sqrt(x)`` —— 极点或分支点）
      * 幂次不是非负整数
      * 只在展开点一侧吻合（``abs(x)`` 这类不可导点）
    """
    sp = _require()

    # 先把展开点邻域内的分段结构化简掉：real_root 产生的
    # Abs/sign 会让后面每一阶的符号极限运算失控（详见 _localize_at_center）。
    expr_s = _localize_at_center(expr_s, symbol, center)

    if not poly.is_polynomial(symbol):
        raise SymbolicError(
            "No Taylor polynomial exists at this point: the expansion is not "
            "a polynomial (the function likely has a pole or a branch point "
            "there).")

    try:
        poly_obj = sp.Poly(poly, symbol)
    except Exception as exc:                     # noqa: BLE001
        raise SymbolicError(
            f"Cannot interpret the expansion as a polynomial: {exc}") from exc
    for monom in poly_obj.monoms():
        exponent = monom[0] if monom else 0
        if not isinstance(exponent, int) or exponent < 0:
            raise SymbolicError(
                "No Taylor polynomial exists at this point: the expansion "
                "contains a negative or fractional power.")

    # 零阶条件：f 在展开点必须**连续**，且 f(a) 等于极限值。
    #
    # sign(x)**2 是个反例：x≠0 时恒为 1，f(0)=0。左右极限都是 1，
    # 只比较左右极限会放它过关，但那个点根本不连续，谈不上泰勒多项式。
    suspicious = (sp.Abs, sp.sign, sp.Piecewise, sp.Min, sp.Max,
                  sp.floor, sp.ceiling)

    try:
        at_center = expr_s.subs(symbol, center)
    except Exception:                            # noqa: BLE001
        at_center = None

    if at_center is not None:
        if at_center.has(sp.zoo) or at_center.has(sp.nan):
            raise SymbolicError(
                "f is undefined at the expansion point, so there is no "
                "Taylor polynomial there.")
        if at_center.is_finite is False:
            raise SymbolicError(
                "f is infinite at the expansion point, so there is no "
                "Taylor polynomial there.")

    if at_center is not None and (expr_s.has(*suspicious)
                                  or at_center.has(*suspicious)):
        try:
            limit_value = sp.limit(expr_s, symbol, center)
        except Exception:                        # noqa: BLE001
            limit_value = None
        if limit_value is not None:
            if (limit_value.has(sp.zoo) or limit_value.has(sp.nan)
                    or limit_value.is_finite is False):
                raise SymbolicError(
                    "f has no finite limit at the expansion point, so there "
                    "is no Taylor polynomial there.")
            try:
                continuous = sp.simplify(at_center - limit_value) == 0
            except Exception:                    # noqa: BLE001
                continuous = at_center == limit_value
            if not continuous:
                raise SymbolicError(
                    f"f is not continuous at the expansion point "
                    f"(f({center}) = {at_center} but the limit is "
                    f"{limit_value}). A Taylor polynomial requires a "
                    "continuous function there.")

    # 导数存在性：用**符号求导**逐阶核验，而不是固定步长的差商。
    #
    # 差商在浮点下不可靠：步长大时高阶项盖过一阶项，步长小时被舍入
    # 误差淹没，而且它只能"看到一个"，没法按所需阶数逐一确认。
    #
    # 判据：f^(k)(center) 对 k = 0..order 必须有限；遇到 abs/sign 这类
    # 分段函数时再比较**左右极限**（abs 在 0 处左导 -1、右导 +1）。
    # 无法判定时明确说"无法确认"，不武断声称不存在。
    suspicious = (sp.Abs, sp.sign, sp.Piecewise, sp.Min, sp.Max,
                  sp.floor, sp.ceiling)
    for k in range(max(1, order + 1)):
        try:
            deriv = sp.diff(expr_s, symbol, k)
        except Exception as exc:                 # noqa: BLE001
            raise SymbolicError(
                f"Cannot determine whether the {k}-th derivative exists at "
                f"this point: differentiation failed ({exc}).") from exc

        at_point = None
        try:
            at_point = deriv.subs(symbol, center)
        except Exception:                        # noqa: BLE001
            at_point = None

        if at_point is not None:
            if at_point.has(sp.zoo) or at_point.has(sp.nan):
                raise SymbolicError(
                    f"No Taylor polynomial exists at this point: the {k}-th "
                    "derivative is undefined here.")
            if at_point.is_finite is False:
                raise SymbolicError(
                    f"No Taylor polynomial exists at this point: the {k}-th "
                    "derivative is infinite here.")

        # 含分段函数 / 绝对值时，单点取值可能是 sympy 的约定值
        # （sign(0) = 0），必须靠左右极限确认
        needs_limit = (
            at_point is None
            or at_point.has(*suspicious)
            or deriv.has(*suspicious)
        )
        if not needs_limit:
            continue

        try:
            left = sp.limit(deriv, symbol, center, "-")
            right = sp.limit(deriv, symbol, center, "+")
        except Exception as exc:                 # noqa: BLE001
            raise SymbolicError(
                f"Cannot confirm the {k}-th derivative at this point: the "
                f"one-sided limits could not be computed ({exc}). Refusing "
                "to claim a Taylor polynomial exists.") from exc

        if left is None or right is None:
            raise SymbolicError(
                f"Cannot confirm the {k}-th derivative at this point: the "
                "one-sided limits are unavailable.")
        if not (left.is_finite and right.is_finite):
            raise SymbolicError(
                f"No Taylor polynomial exists at this point: the {k}-th "
                "derivative is infinite on at least one side.")
        try:
            same = sp.simplify(left - right) == 0
        except Exception:                        # noqa: BLE001
            same = (left == right)
        if not same:
            raise SymbolicError(
                f"No Taylor polynomial exists at this point: the {k}-th "
                f"derivative has different one-sided limits "
                f"({left} on the left, {right} on the right)."
            )


def taylor_poly(expr, var="x", point=0, order=5, assumptions=None):
    """在 point 处把 expr 展开成 order 次多项式。

    返回 ``(sympy 多项式, 说明列表)``。单独暴露这个是为了让界面能拿到
    表达式对象做数值比较（不必把字符串再解析一遍）。

    point 只支持**常量**（数字、pi、e 之类）—— 符号展开点写出来的式子
    通常又长又没用，这里不做。
    """
    sp = _require()
    expr_s = _safe_sympify(expr, [var], assumptions)
    symbol = _make_symbol(var, assumptions)
    order = max(0, int(order))

    center = _sympy_bound(point)
    if center.is_finite is False:
        raise SymbolicError(
            f"Taylor expansion needs a finite point, got {point!r}")

    try:
        series = sp.series(expr_s, symbol, center, order + 1)
    except Exception as exc:                     # noqa: BLE001
        raise SymbolicError(
            f"Could not expand {expr!r} at {point!r}: {exc}") from exc

    # removeO() 去掉尾部的 O(x**n)。
    #
    # 在 0 处展开时用 expand 整理成 x 的降幂和式 —— 否则 sympy 可能给出
    # x*(-x**3/4 + x**2/3 - x/2 + 1) 这种因式分解形式，看起来不像泰勒多项式。
    #
    # 非零展开点则**不展开**：series() 给出的是 (x - a) 的幂次，展开成
    # x 的多项式会糊掉「在 a 处展开」这件事，式子也变得又长又难读。
    poly = series.removeO()
    if center == 0:
        poly = sp.expand(poly)

    _validate_taylor(expr_s, symbol, poly, center, order)

    notes = []
    if poly.has(sp.Integral) or poly.has(sp.Derivative):
        raise SymbolicError(
            "sympy could not produce a finite expansion for this function")
    # 多项式提前终止（例如 sin 的偶次项都是 0）时说明一下，
    # 免得用户以为漏算了。
    # 注意 sp.degree() 返回的是 sympy 的 Integer，不是 Python int，
    # 用 isinstance(degree, int) 判断会永远为假（这个坑踩过一次）。
    try:
        raw_degree = sp.degree(poly, symbol)
        degree = int(raw_degree) if raw_degree is not None else None
    except Exception:                            # noqa: BLE001
        degree = None
    if degree is not None and degree < order:
        notes.append(
            f"the series stops at degree {degree} "
            "(the next derivative vanishes at this point)")
    if center != 0:
        notes.append(f"expanded about {var} = {center}")
    return poly, notes


def taylor(expr, var="x", point=0, order=5, assumptions=None):
    """泰勒展开，返回 (多项式字符串, LaTeX, 说明列表)。"""
    sp = _require()
    poly, notes = taylor_poly(expr, var, point, order, assumptions)
    return str(poly), sp.latex(poly), notes


def partial(expr, variables, var, order=1, cross_var=None, assumptions=None):
    """符号偏导（含混合二阶）。返回 (结果字符串, LaTeX 字符串)。"""
    sp = _require()
    expr_s = _safe_sympify(expr, list(variables), assumptions)
    symbols = {v: _make_symbol(v, assumptions) for v in variables}
    if var not in symbols:
        raise SymbolicError(f"Expression has no variable {var}")
    result = expr_s
    if order == 2 and cross_var:
        if cross_var not in symbols:
            raise SymbolicError(f"Expression has no variable {cross_var}")
        result = sp.diff(result, symbols[var], symbols[cross_var])
    elif order == 2:
        result = sp.diff(result, symbols[var], 2)
    else:
        result = sp.diff(result, symbols[var])
    result = sp.simplify(result)
    return str(result), sp.latex(result)


def numeric_value(expr, variables, point, assumptions=None):
    """把符号表达式在给定点求值（用于和数值版互相对照）。"""
    sp = _require()
    expr_s = _safe_sympify(expr, list(variables), assumptions)
    subs = {_make_symbol(v, assumptions): point[v] for v in variables if v in point}
    try:
        value = expr_s.subs(subs)
        if value.free_symbols:
            raise SymbolicError("Some variables remain unsubstituted")
        return float(value)
    except Exception as exc:
        raise SymbolicError(f"Evaluation failed: {exc}") from exc
def _is_effectively_real(value, tol=1e-12):
    """判断复数是否可视为实数（虚部可忽略）。"""
    try:
        z = complex(value)
    except Exception:
        return True
    return abs(z.imag) <= tol * max(1.0, abs(z))


def format_value(value):
    """把数值格式化成好读的纯文本（供 ft.Text 显示）。

    复数 -> "a + bi"；整数原样；小数保留 10 位有效数字。
    """
    try:
        z = complex(value)
    except Exception:
        return str(value)
    if _is_effectively_real(z):
        v = z.real
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.10g}"
    re_part = 0.0 if abs(z.real) < 1e-12 * max(1.0, abs(z)) else z.real
    im_part = z.imag
    sign = "+" if im_part >= 0 else "-"
    im_abs = abs(im_part)
    im_tex = str(int(im_abs)) if im_abs == int(im_abs) else f"{im_abs:.10g}"
    # 系数 1 省略：写 i 而不是 1i
    im_tex = "" if im_tex == "1" else im_tex
    if re_part == 0:
        return f"{'' if sign == '+' else '-'}{im_tex}i"
    re_tex = str(int(re_part)) if re_part == int(re_part) else f"{re_part:.10g}"
    return f"{re_tex} {sign} {im_tex}i"
