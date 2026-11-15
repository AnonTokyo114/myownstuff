"""符号微积分（B 版，基于 sympy）。

与 A 版（calculus_numeric）互补：
  * A 版给数值 + 误差估计，任何可数值求值的函数都能算
  * B 版给精确表达式（如 d/dx(x²sin x) = 2x·sin x + x²·cos x），能格式化输出

sympy 是可选依赖：没装时本模块的 available() 返回 False，
界面据此把符号结果区置灰，而不是让整个计算器崩掉。
"""

import ast

from .expr_eval import ExpressionError, preprocess

try:  # pragma: no cover - 取决于环境
    import sympy as _sp
except Exception:  # noqa: BLE001
    _sp = None


class SymbolicError(ValueError):
    """符号计算失败（无法解析、无法积分等）。"""


def available():
    """sympy 是否可用。"""
    return _sp is not None


def _require():
    if _sp is None:
        raise SymbolicError("sympy is not installed; symbolic features are unavailable (pip install sympy)")
    return _sp


#: 允许出现的符号函数名（与 expr_eval 的白名单保持一致）
_ALLOWED_FUNCS = {
    "sin", "cos", "tan", "asin", "acos", "atan",
    "sinh", "cosh", "tanh", "exp", "log", "log10", "log2",
    "sqrt", "cbrt", "Abs",
    # 复数
    "re", "im", "conjugate", "arg",
}

_NAME_MAP = {"abs": "Abs"}


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
            fname = _NAME_MAP.get(node.func.id, node.func.id)
            if fname not in _ALLOWED_FUNCS:
                raise SymbolicError(f"Symbolic computation does not support function: {node.func.id}")
            args = [walk(a) for a in node.args]
            if node.keywords:
                raise SymbolicError("Keyword arguments are not supported")
            return getattr(sp, fname)(*args)
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


def differentiate(expr, var="x", assumptions=None):
    """符号求导，返回 (结果字符串, LaTeX 字符串)。"""
    sp = _require()
    expr_s = _safe_sympify(expr, [var], assumptions)
    symbol = _make_symbol(var, assumptions)
    result = sp.diff(expr_s, symbol)
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
    try:
        lo = sp.sympify(lower) if isinstance(lower, str) else sp.Integer(lower)
        hi = sp.sympify(upper) if isinstance(upper, str) else sp.Integer(upper)
    except Exception as exc:
        raise SymbolicError(f"Cannot parse bounds: {exc}") from exc
    result = sp.integrate(expr_s, (symbol, lo, hi))
    if result.has(sp.Integral):
        raise SymbolicError("sympy could not find a closed form for this integral")
    simplified = sp.simplify(result)
    return str(simplified), sp.latex(simplified), notes


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

