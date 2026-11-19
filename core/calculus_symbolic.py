"""符号微积分（B 版，基于 sympy）。

与 A 版（calculus_numeric）互补：
  * A 版给数值 + 误差估计，任何可数值求值的函数都能算
  * B 版给精确表达式（如 d/dx(x²sin x) = 2x·sin x + x²·cos x），能格式化输出

sympy 是可选依赖：没装时本模块的 available() 返回 False，
界面据此把符号结果区置灰，而不是让整个计算器崩掉。
"""

import ast
import math

from .expr_eval import ExpressionError, preprocess

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


def available():
    """sympy 是否可用。"""
    return _sp is not None


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
    "cbrt": lambda sp: sp.cbrt,
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


def _fmt(expr, pretty=True):
    """把 sympy 表达式转成可读字符串。"""
    sp = _require()
    try:
        if pretty:
            return sp.pretty(expr, use_unicode=True)
    except Exception:
        pass
    return str(expr)


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
    混叠。等距网格很容易和振荡函数的周期同步 —— 例如
    ``sin(800*pi*x/3)**2`` 在 [-1.5, 1.5] 上取 801 点，步长正好是半个
    周期，每个采样点都落在零点附近，于是报出一个大得离谱的区间。
    """
    import numpy as np

    base = np.linspace(center - span, center + span, count)
    if count < 2:
        return base
    step = base[1] - base[0]
    if phase == 0 or step <= 0:
        return base
    rng = np.random.default_rng(1234 + phase)
    jitter = rng.uniform(-0.45, 0.45, count) * step
    return np.clip(base + jitter, center - span, center + span)


def _hold_once(expr, var, poly, center, tolerance, xs):
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
    return (min(center - xs[left], xs[right] - center),
            float(xs[left]), float(xs[right]))


def expansion_holds(expr, var, poly, point, tolerance=1e-6,
                    span=None, samples=801, grids=4):
    """数值估计展开式在多大范围内还够准。

    返回 ``(radius, left, right, spread)``：

      * ``radius`` —— 从展开点向两侧走到误差超过 tolerance 的距离
      * ``left`` / ``right`` —— 对应的区间端点
      * ``spread`` —— 各张网格给出的半径之间的最大差值

    .. warning:: **这是采样估计，不是保证的上界。**

       高频函数可能被网格漏采。用多张网格（等距 + 确定性抖动）并取最
       保守的结果能大幅降低混叠，但严格的区间需要余项界 —— 这里给不了。
       ``spread`` 偏大就说明函数变化比采样快，结果只能当参考。
    """
    center = float(point)
    if span is None:
        span = max(1.0, abs(center)) * 1.5

    results = []
    for phase in range(max(1, grids)):
        count = samples + 8 * phase
        xs = _sampled_grid(center, span, count, phase)
        results.append(_hold_once(expr, var, poly, center, tolerance, xs))

    radius, left, right = min(results, key=lambda r: r[0])
    spread = max(r[0] for r in results) - min(r[0] for r in results)
    return radius, left, right, spread


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


# ---------------------------------------------------------------- LaTeX 记号构造
# 界面层只需要"拿来就渲染"的完整 LaTeX，所以把记号拼装放在这里，
# 避免 calculus.py 直接依赖 sympy。

def latex_expr(expr, variables=("x",), assumptions=None):
    """表达式本身的 LaTeX（失败时退回原字符串）。"""
    sp = _require()
    try:
        return sp.latex(_safe_sympify(expr, list(variables), assumptions))
    except Exception:
        return str(expr)


def latex_integral(expr, var="x", lower=None, upper=None, assumptions=None):
    """完整积分式子的 LaTeX（不含等号右侧）。

    定积分 -> \\int_{a}^{b} f \\, dx
    不定积分 -> \\int f \\, dx
    """
    sp = _require()
    body = latex_expr(expr, [var])
    dvar = f"\\, d{sp.latex(sp.Symbol(var, real=True))}"
    if lower is None or upper is None:
        return f"\\int {body}{dvar}"
    try:
        lo = sp.sympify(lower) if isinstance(lower, str) else sp.Integer(lower)
        hi = sp.sympify(upper) if isinstance(upper, str) else sp.Integer(upper)
        return f"\\int_{{{sp.latex(lo)}}}^{{{sp.latex(hi)}}} {body}{dvar}"
    except Exception:
        return f"\\int_{{{lower}}}^{{{upper}}} {body}{dvar}"


def latex_derivative(expr, var="x", order=1, at=None, assumptions=None):
    """导数式子的 LaTeX。

    at 为 None 时给记号形式，例如 \\frac{d}{dx} \\sin x；
    给了 at 则求值形式，例如 \\left. \\frac{d}{dx} \\sin x \\right|_{x=1}
    """
    sp = _require()
    body = latex_expr(expr, [var])
    v = sp.latex(sp.Symbol(var, real=True))
    if order == 2:
        op = f"\\frac{{d^{{2}}}}{{d{v}^{{2}}}}"
    else:
        op = f"\\frac{{d}}{{d{v}}}"
    core = f"{op} \\left( {body} \\right)"
    if at is None:
        return core
    return f"\\left. {core} \\right|_{{ {v}={sp.latex(_number_expr(at))} }}"


def latex_partial(expr, variables, var, order=1, cross_var=None, at=None, assumptions=None):
    """偏导式子的 LaTeX。"""
    sp = _require()
    body = latex_expr(expr, list(variables))
    v = sp.latex(sp.Symbol(var, real=True))
    if order == 2 and cross_var and cross_var != var:
        w = sp.latex(sp.Symbol(cross_var, real=True))
        op = f"\\frac{{\\partial^{{2}}}}{{\\partial {w} \\partial {v}}}"
    elif order == 2:
        op = f"\\frac{{\\partial^{{2}}}}{{\\partial {v}^{{2}}}}"
    else:
        op = f"\\frac{{\\partial}}{{\\partial {v}}}"
    core = f"{op} \\left( {body} \\right)"
    if at is None:
        return core
    subs = ",\\; ".join(
        f"{sp.latex(sp.Symbol(n, real=True))}={sp.latex(_number_expr(val))}"
        for n, val in at.items()
    )
    return f"\\left. {core} \\right|_{{ {subs} }}"


def _number_expr(value):
    """把数值转成"显示上合理"的 sympy 数值。

    关键教训：**不能对任意浮点做近似化简**。
      * nsimplify(v, tolerance=1e-12) 会把 2.2443637198591 变成
        2*2**(3/131)*3**(124/131)*5**(9/131)*7**(16/131)；
      * 把分母上限放宽到 1000 又会把 e 变成 1457/536、pi 变成 355/113。
    两者都是"数学上成立、人类读不懂"。

    所以两个判据分开，且都很保守：
      * 整数：相对容差 1e-6（数值微分/积分本身只有约 1e-7 的精度，
        3.999999966 这种就该显示 4）
      * 简单分数：分母上限 64 且相对误差 1e-9 以内
        （够覆盖 1/2、1/3、2/3、1/6 这类常见结果，
         又不足以把无理数误认成分数）
      * 其余一律给 6 位有效数字的小数
    """
    sp = _require()
    v = float(value)
    scale = max(1.0, abs(v))
    # 1) 接近整数
    if abs(v - round(v)) <= 1e-6 * scale:
        return sp.Integer(int(round(v)))
    # 2) 接近"分母很小"的简单分数
    from fractions import Fraction
    frac = Fraction(v).limit_denominator(64)
    if abs(float(frac) - v) <= 1e-9 * scale:
        return sp.Rational(frac.numerator, frac.denominator)
    # 3) 老老实实给小数（6 位有效数字足够显示）
    return sp.Float(f"{v:.6g}")


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


def latex_number(value):
    """把数值转成适合显示的 LaTeX。

    实数：整数 -> 整数；简单分数 -> \\frac{}{}；其余 -> 有限位小数。
    复数：实部与虚部分别格式化，输出 ``a + b i`` 形式。
    """
    sp = _require()
    try:
        z = complex(value)
    except Exception:
        try:
            return f"{float(value):.6g}"
        except Exception:
            return str(value)

    if _is_effectively_real(z):
        return sp.latex(_number_expr(z.real))

    re_expr = _number_expr(0.0 if abs(z.real) < 1e-12 * max(1.0, abs(z)) else z.real)
    im_expr = _number_expr(z.imag)
    return sp.latex(sp.Add(re_expr, sp.Mul(im_expr, sp.I)))

