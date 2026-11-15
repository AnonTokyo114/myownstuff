"""特征值 / 特征函数 / 矩阵运算 —— 纯计算层。

从 eigencalc_gui.py（tkinter 版）提取，只保留与界面无关的部分：
  * 解析：parse_math / sympy 变换与命名空间
  * 格式化：fmt_complex / format_value / format_matrix
  * 数值：eigen_numeric / to_np_complex
  * 符号：rationalize / symbolic_eigen_report
  * 一维特征函数：operator_eigenfunctions（有限差分 + Dirichlet）
  * 参数扫描：eigen_sweep / sweep_summary
  * 矩阵运算：compute_operation（原 OpsTab._compute）

与原版相比只改了两处：
  1. collect_substitutions 接受 {名字: 文本} 而不是 tkinter 的 StringVar 字典
  2. compute_operation 从静态方法提为模块函数

本模块无 GUI 依赖（numpy + sympy 必需，scipy 可选）。
"""

from __future__ import annotations

import numpy as np
import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    function_exponentiation,
    implicit_application,
    implicit_multiplication,
    parse_expr,
    standard_transformations,
)

try:
    from scipy.linalg import eigh_tridiagonal
    HAS_SCIPY = True
except Exception:                                   # pragma: no cover
    HAS_SCIPY = False


# ======================================================================
# 解析
# ======================================================================
X = sp.Symbol("x")

# 注意：这里故意不含 split_symbols，所以 "mass" 是一个符号而不是 m*a*s*s。
# convert_xor 让 ^ 表示乘方。
TRANSFORMS = standard_transformations + (
    convert_xor,
    implicit_multiplication,
    implicit_application,
    function_exponentiation,
)

# 最小全局命名空间。否则 parse_expr 会做 `from sympy import *`，
# 于是 beta / gamma / zeta / S / N / Q / O 会解析成 SymPy 对象而不是普通符号。
_PARSE_GLOBALS = {
    "Integer": sp.Integer, "Float": sp.Float, "Rational": sp.Rational,
    "Symbol": sp.Symbol, "Function": sp.Function, "Lambda": sp.Lambda,
    "factorial": sp.factorial, "factorial2": sp.factorial2,
}


def sympy_locals():
    return {
        "sin": sp.sin, "cos": sp.cos, "tan": sp.tan,
        "asin": sp.asin, "acos": sp.acos, "atan": sp.atan,
        "sinh": sp.sinh, "cosh": sp.cosh, "tanh": sp.tanh,
        "exp": sp.exp, "log": sp.log, "ln": sp.log, "sqrt": sp.sqrt,
        "pi": sp.pi, "E": sp.E, "I": sp.I, "oo": sp.oo,
        "Abs": sp.Abs, "abs": sp.Abs, "sign": sp.sign,
        "Heaviside": sp.Heaviside, "floor": sp.floor, "ceiling": sp.ceiling,
    }


def parse_math(txt: str):
    """把用户输入解析成一个标量 sympy 表达式。"""
    res = parse_expr(txt, transformations=TRANSFORMS,
                     local_dict=sympy_locals(), global_dict=dict(_PARSE_GLOBALS))
    if not isinstance(res, sp.Expr):
        raise ValueError("not a scalar expression")
    return res


def collect_substitutions(values: dict, symbols) -> dict:
    """{符号名: 文本} -> {符号: 解析后的值}；只收非空文本。

    原版直接读 tkinter StringVar；这里改成接受普通字典，便于测试与复用。
    """
    by_name = {}
    subs = {}
    for s in symbols:
        by_name[s.name] = s
    for name, text in (values or {}).items():
        text = (text or "").strip()
        if not text:
            continue
        sym = by_name.get(name)
        if sym is None:
            continue
        try:
            subs[sym] = parse_math(text)
        except Exception as exc:
            raise ValueError(f"Value for {name} = {text!r}: {exc}") from exc
    return subs


# ======================================================================
# 格式化
# ======================================================================
def fmt_complex(z, digits=6):
    z = complex(z)
    re_, im = z.real + 0.0, z.imag + 0.0        # "+ 0.0" 把 -0.0 变成 0.0
    if abs(im) < 1e-10 * max(1.0, abs(re_)):
        return f"{re_:.{digits}g}"
    if abs(re_) < 1e-10 * max(1.0, abs(im)):
        return f"{im:.{digits}g}j"
    sign = "+" if im >= 0 else "-"
    return f"{re_:.{digits}g} {sign} {abs(im):.{digits}g}j"


def format_value(v, digits=8):
    """精确值给精确文本；浮点值给紧凑小数。"""
    v = sp.sympify(v)
    if not v.free_symbols and v.has(sp.Float):
        try:
            return fmt_complex(complex(v), digits)
        except (TypeError, ValueError):
            pass
    return str(v)


def format_matrix(M, digits=8):
    M = sp.Matrix(M)
    n, m = M.shape
    cells = [[format_value(M[i, j], digits) for j in range(m)] for i in range(n)]
    widths = [max(len(cells[i][j]) for i in range(n)) for j in range(m)]
    return "\n".join(
        "  [ " + "  ".join(cells[i][j].rjust(widths[j]) for j in range(m)) + " ]"
        for i in range(n))


# ======================================================================
# 数值特征分解
# ======================================================================
def to_np_complex(M):
    n = M.shape[0]
    A = np.empty((n, n), dtype=complex)
    for i in range(n):
        for j in range(n):
            A[i, j] = complex(sp.N(M[i, j], 20))
    return A


def eigen_numeric(A):
    """返回 (特征值, 特征向量按列, 是否为 Hermitian)。"""
    if np.allclose(A, A.conj().T):
        w, V = np.linalg.eigh(A)                # 实特征值、正交归一向量
        return w.astype(complex), V.astype(complex), True
    w, V = np.linalg.eig(A)
    order = np.lexsort((w.imag, w.real))
    return w[order], V[:, order], False


def rationalize(M):
    """把 1.3 这类浮点变成精确有理数（13/10），好让 SymPy 因式分解。"""
    return M.applyfunc(lambda e: sp.nsimplify(e, rational=True))


# ======================================================================
# 符号特征值报告
# ======================================================================
def symbolic_eigen_report(M):
    n = M.shape[0]
    had_floats = M.has(sp.Float)
    if had_floats:
        M = rationalize(M)
    syms = M.free_symbols
    names = ", ".join(sorted(map(str, syms))) if syms else "none"
    lines = [f"Symbolic analysis of {n}x{n} matrix (free symbols: {names})"]
    if had_floats:
        lines.append("(decimals were converted to exact fractions, e.g. 1.3 -> 13/10)")
    lines.append("")

    lam = sp.Symbol("lambda")
    eigs = None                                 # list of (value, multiplicity)
    try:
        cp = sp.expand(M.charpoly(lam).as_expr())
        lines.append("Characteristic polynomial  det(lambda*I - A):")
        lines.append(f"  {cp}")
        const, factors = sp.factor_list(cp, lam)
        if len(factors) > 1 or factors[0][1] > 1 or factors[0][0] != cp:
            lines.append("Factored over the rationals:")
            for f, mult in factors:
                lines.append(f"  ({f}){'^' + str(mult) if mult > 1 else ''}")
        lines.append("")
        # 逐不可约因子求解：比解整个多项式干净得多（四次会拆成两个二次）
        eigs, ok = [], True
        for f, mult in factors:
            deg = sp.degree(f, lam)
            if deg == 0:
                continue
            if deg > 4:
                ok = False
                break
            for r, rm in sp.roots(sp.Poly(f, lam)).items():
                eigs.append((sp.simplify(r), rm * mult))
            if sum(rm for _, rm in eigs) == 0:
                ok = False
                break
        if not ok or sum(m for _, m in eigs) != n:
            eigs = None
    except Exception as e:
        lines += [f"(characteristic polynomial failed: {e})", ""]

    if eigs is None:
        vals = M.eigenvals()
        eigs = list(vals.items())
    if syms:
        eigs = sorted(eigs, key=lambda kv: (sp.count_ops(kv[0]), str(kv[0])))
    else:
        eigs = sorted(eigs, key=lambda kv: (float(sp.re(sp.N(kv[0]))),
                                            float(sp.im(sp.N(kv[0])))))
    lines.append("Eigenvalues:")
    for i, (val, mult) in enumerate(eigs):
        extra = f"   (multiplicity {mult})" if mult > 1 else ""
        lines.append(f"  lambda_{i + 1} = {val}{extra}")
    if sum(m for _, m in eigs) < n:
        lines.append("  (SymPy could not find all eigenvalues in closed form)")

    if n <= 2:
        lines += ["", "Symbolic eigenvectors:"]
        try:
            for val, _mult, vecs in M.eigenvects():
                for v in vecs:
                    vstr = "[" + ", ".join(str(c) for c in list(v)) + "]"
                    lines.append(f"  lambda = {val}:  v = {vstr}")
        except Exception as e:
            lines.append(f"  (failed: {e})")
    else:
        lines += ["", "(symbolic eigenvectors omitted for n>2 -- "
                      "fill in the symbol values and use Compute numeric)"]
    return "\n".join(lines)


# ======================================================================
# 1D 特征函数：-u'' + V(x)u = lam u，u(a)=u(b)=0
# ======================================================================
def operator_eigenfunctions(Vfun, a=0.0, b=1.0, n=400, k=5):
    """有限差分特征对。

    离散后的算子是三对角的，所以有 SciPy 时只算最低 k 个特征对（O(nk)）；
    没有 SciPy 时退化为稠密求解。
    返回 x, lam[:k], U[:, :k]，满足 integral(u^2)=1 且符号约定固定
    （绝对值最大的瓣为正）。
    """
    x = np.linspace(a, b, n)
    h = x[1] - x[0]
    xin = x[1:-1]
    m = xin.size
    Vin = np.asarray(Vfun(xin), dtype=float)
    bad = ~np.isfinite(Vin)
    if bad.any():
        raise ValueError(
            f"V(x) is not finite at {int(bad.sum())} interior grid point(s) "
            f"(first at x = {xin[bad][0]:g}). Choose another interval or potential.")
    k = max(1, min(k, m))
    d = 2.0 / h**2 + Vin
    e = -np.ones(m - 1) / h**2
    if HAS_SCIPY:
        lam, vecs = eigh_tridiagonal(d, e, select="i", select_range=(0, k - 1))
    else:
        if m > 3000:
            raise ValueError(
                "Without SciPy, n is limited to 3000 (dense solver).  "
                "Install SciPy (pip install scipy) for large grids.")
        H = np.diag(d) + np.diag(e, 1) + np.diag(e, -1)
        lam, vecs = np.linalg.eigh(H)
        lam, vecs = lam[:k], vecs[:, :k]
    U = np.zeros((n, k))
    U[1:-1, :] = vecs
    U /= np.sqrt(h * np.sum(U ** 2, axis=0))
    idx = np.argmax(np.abs(U), axis=0)
    signs = np.sign(U[idx, np.arange(k)])
    signs[signs == 0] = 1.0
    U *= signs
    return x, lam, U


# ======================================================================
# 参数扫描
# ======================================================================
def eigen_sweep(M, param, ts, fixed=None):
    """在每个 ts 上对 M(param) 做特征分解；其它符号必须已在 fixed 里给值。

    返回 (W, V, hermitian)：W[k, i] = ts[k] 处第 i 个特征值（升序），
    V[k, :, i] = 对应的单位特征向量。
    """
    if fixed:
        M = M.subs(fixed)
    extra = M.free_symbols - {param}
    if extra:
        raise ValueError("Values needed for: " + ", ".join(sorted(map(str, extra))))
    n = M.shape[0]
    f = sp.lambdify(param, M, "numpy")
    stack = np.empty((len(ts), n, n), dtype=complex)
    for k, t in enumerate(ts):
        stack[k] = np.array(f(float(t)), dtype=complex)
        if not np.all(np.isfinite(stack[k])):
            raise ValueError(f"matrix is not finite at {param} = {t:g}")
    herm = bool(np.allclose(stack, np.conj(np.swapaxes(stack, 1, 2))))
    if herm:
        W, V = np.linalg.eigh(stack)
        return W.astype(complex), V.astype(complex), True
    W = np.empty((len(ts), n), dtype=complex)
    V = np.empty((len(ts), n, n), dtype=complex)
    for k in range(len(ts)):
        w, v = np.linalg.eig(stack[k])
        order = np.lexsort((w.imag, w.real))
        W[k], V[k] = w[order], v[:, order]
    return W, V, False


def sweep_summary(name, ts, W, herm):
    n = W.shape[1]
    lines = [f"Sweep of {name} from {ts[0]:g} to {ts[-1]:g} ({len(ts)} points)"
             + ("  [Hermitian]" if herm else "  [non-Hermitian: real parts shown, "
                                              "levels sorted by real part]"),
             "", f"{'level':<9}{name + ' = ' + format(ts[0], 'g'):>20}"
                 f"{name + ' = ' + format(ts[-1], 'g'):>20}"]
    for i in range(n):
        lines.append(f"{'lambda_' + str(i + 1):<9}{fmt_complex(W[0, i]):>20}"
                     f"{fmt_complex(W[-1, i]):>20}")
    if n > 1:
        lines += ["", "Smallest gap between neighbouring levels (real parts):"]
        gaps = np.diff(W.real, axis=1)
        for i in range(n - 1):
            k = int(np.argmin(gaps[:, i]))
            lines.append(f"  lambda_{i + 1} / lambda_{i + 2}:  {gaps[k, i]:.6g}"
                         f"  at {name} = {ts[k]:.6g}")
    return "\n".join(lines)


# ======================================================================
# 矩阵运算
# ======================================================================
#: 运算 -> (显示名, 需要的操作数)
OPS = {
    "add":     ("A + B",        ("A", "B")),
    "sub":     ("A \u2212 B",   ("A", "B")),
    "mul":     ("A \u00b7 B",   ("A", "B")),
    "matvec":  ("A \u00b7 v",   ("A", "v")),
    "solve_v": ("A\u207b\u00b9 \u00b7 v", ("A", "v")),
    "solve_B": ("A\u207b\u00b9 \u00b7 B", ("A", "B")),
    "inv":     ("A\u207b\u00b9", ("A",)),
    "det":     ("det(A)",       ("A",)),
}


def compute_operation(which: str, m: dict):
    """按运算键计算结果（sympy 表达式）。形状不合法时抛 ValueError。"""
    A = m["A"]
    B = m.get("B")
    v = m.get("v")

    def need_same_shape():
        if A.shape != B.shape:
            raise ValueError(f"A is {A.shape}, B is {B.shape}; shapes must match")

    def need_square():
        if A.shape[0] != A.shape[1]:
            raise ValueError(f"A must be square, got {A.shape}")

    def need_invertible():
        need_square()
        if A.det() == 0:
            raise ValueError("A is singular (det(A) = 0); it has no inverse")

    if which == "add":
        need_same_shape()
        return A + B
    if which == "sub":
        need_same_shape()
        return A - B
    if which == "mul":
        if A.shape[1] != B.shape[0]:
            raise ValueError(f"A is {A.shape}, B is {B.shape}; A.cols "
                             f"({A.shape[1]}) must equal B.rows ({B.shape[0]})")
        return A * B
    if which == "matvec":
        if A.shape[1] != v.shape[0]:
            raise ValueError(f"A is {A.shape}, v is {v.shape}; A.cols "
                             f"({A.shape[1]}) must equal v.rows ({v.shape[0]})")
        return A * v
    if which == "solve_v":
        need_invertible()
        if A.shape[0] != v.shape[0]:
            raise ValueError(f"A is {A.shape}, v is {v.shape}; "
                             "A.rows must equal v.rows")
        return A.solve(v)
    if which == "solve_B":
        need_invertible()
        if A.shape[0] != B.shape[0]:
            raise ValueError(f"A is {A.shape}, B is {B.shape}; "
                             "A.rows must equal B.rows")
        return A.solve(B)
    if which == "inv":
        need_invertible()
        return A.inv()
    if which == "det":
        need_square()
        return A.det()
    raise ValueError(f"Unknown operation: {which}")
