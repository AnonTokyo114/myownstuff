"""æ•°å€¼å¾®ç§¯åˆ†ï¼ˆA ç‰ˆï¼‰ã€‚

å·¥å…·ï¼š
  * integrate      å®šç§¯åˆ†ï¼ˆæ”¯æŒ Â±infï¼‰ï¼Œç”¨ scipy.integrate.quad
  * derivative     å•å˜é‡å¯¼æ•°ï¼ˆä¸­å¿ƒå·®åˆ† + Richardson å¤–æŽ¨ï¼‰
  * partial        å¤šå˜é‡åå¯¼ï¼ˆå«æ··åˆäºŒé˜¶ï¼‰
  * å°ç»“é‡Œçš„è¯¯å·®ä¼°è®¡éƒ½å¦‚å®žç»™å‡ºï¼Œä¸å‡è£…ç²¾ç¡®

è®¾è®¡ä¸Šåˆ»æ„ä¸ä¾èµ– sympyï¼šæ²¡è£…ä¹Ÿèƒ½ç”¨ï¼ˆB ç‰ˆç¬¦å·è®¡ç®—åœ¨ calculus_symbolic.pyï¼‰ã€‚
"""

import ast
import math
import operator
import warnings

import numpy as np
from scipy import integrate as _quad_mod

from .expr_eval import CompiledExpression, ExpressionError  # noqa: F401


class CalculusError(ValueError):
    """è®¡ç®—å¤±è´¥ï¼ˆä¸å¯ç§¯ã€å‘æ•£ã€ç¼ºå˜é‡ç­‰ï¼‰ã€‚"""


# ---------------------------------------------------------------- ç§¯åˆ†

#: ç§¯åˆ†è¾¹ç•Œé‡Œå…è®¸å‡ºçŽ°çš„å¸¸é‡å
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
    """æŠŠå·²é€šè¿‡ç™½åå•çš„ AST èŠ‚ç‚¹ç®—æˆä¸€ä¸ªæ•°ã€‚

    è¿™é‡Œ**ä¸ä½¿ç”¨ eval**ï¼šå³ä½¿æŠŠ ``__builtins__`` æ¸…ç©ºï¼Œä»èƒ½ç»ç”±å±žæ€§è®¿é—®
    ï¼ˆå¦‚ ``().__class__.__base__.__subclasses__()``ï¼‰å–å›žå†…å»ºå¯¹è±¡ï¼Œä»Žè€Œ
    æ‰§è¡Œä»»æ„ä»£ç ã€‚è¾¹ç•Œè¡¨è¾¾å¼å¯èƒ½æ¥è‡ªç²˜è´´çš„æ–‡æœ¬ï¼Œæ‰€ä»¥åªå…è®¸æ•°å­—å¸¸é‡ã€
    å‡ ä¸ªå·²çŸ¥å¸¸é‡åï¼Œä»¥åŠ + - * / ** % ã€‚
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
    """è§£æžç§¯åˆ†ä¸Šä¸‹é™ï¼Œæ”¯æŒ inf / -inf / pi ç­‰å†™æ³•ã€‚

    æ±‚å€¼èµ° AST ç™½åå•ï¼Œä¸è°ƒç”¨ eval â€”â€” è§ _eval_bound_node çš„è¯´æ˜Žã€‚
    """
    if text is None:
        return default
    s = str(text).strip().lower()
    if not s:
        return default
    s = (s.replace("âˆž", "inf")
          .replace("Ï€", "pi")
          .replace("âˆ’", "-"))       # U+2212 å‡å·
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
    """æŠŠæ±‚å€¼ç»“æžœè½¬æˆ Python å¤æ•°ï¼›æ”¯æŒæ ‡é‡ä¸Ž 0 ç»´æ•°ç»„ã€‚"""
    try:
        arr = np.asarray(v)
        if arr.ndim == 0:
            return complex(arr.item())
        return complex(arr.reshape(-1)[0])
    except Exception:
        return complex(v)


def _realify(z, tol=1e-12):
    """è™šéƒ¨å¯å¿½ç•¥æ—¶æ”¶å›žå®žæ•°ï¼Œé¿å…ç•Œé¢ä¸Šå‡ºçŽ° 6+0j è¿™ç§éš¾çœ‹çš„è¾“å‡ºã€‚"""
    try:
        z = complex(z)
    except Exception:
        return z
    if abs(z.imag) <= tol * max(1.0, abs(z)):
        return z.real
    return z
def _looks_divergent(f, lower, upper):
    """ç²—åˆ¤æœ‰é™ç«¯ç‚¹å¤„æ˜¯å¦**çœŸçš„**å‘æ•£ã€‚

    ä¸èƒ½ç”¨ã€Œå‡½æ•°å€¼ > æŸä¸ªç»å¯¹å€¼ã€æ¥åˆ¤æ–­ â€”â€” å¹…å€¼å¤§ä¸Žä¸å¯ç§¯æ²¡æœ‰ç­‰ä»·å…³ç³»ï¼š
    ``2e6*x`` åœ¨ [0,1] ä¸Šç§¯åˆ†å°±æ˜¯ 1e6ï¼Œå®Œå…¨æ­£å¸¸ï¼›``x**-0.9`` åœ¨ 0 å¤„è™½ç„¶
    è¶‹å‘æ— ç©·ï¼Œä½†ç§¯åˆ†æ”¶æ•›åˆ° 10ï¼ˆå¯ç§¯çš„ç«¯ç‚¹å¥‡å¼‚æ€§ï¼‰ã€‚

    è¿™é‡Œçœ‹çš„æ˜¯ ``f(bound + eps) * eps`` éš eps -> 0 çš„èµ°å‘ï¼š

        x**-0.9 : eps**0.1      -> 0     æ”¶æ•›
        1/x     : 1             -> å¸¸æ•°  å¯¹æ•°å‘æ•£ï¼ˆäº¤ç»™ç§¯åˆ†å™¨ + è­¦å‘Šï¼‰
        x**-1.5 : eps**-0.5     -> inf   å¹‚å¾‹å‘æ•£

    åªæœ‰ã€Œæ˜Žæ˜¾è¶‹äºŽæ— ç©·ã€æ‰ç®—ç¡®å®šå‘æ•£ï¼›è¾¹ç•Œæƒ…å†µä¸æ‹¦ï¼Œé¿å…è¯¯æ‹’æ­£å¸¸ç§¯åˆ†ã€‚
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
        # å•è°ƒå¢žé•¿ä¸”æœ«å°¾æ¯”å¼€å¤´å¤§ä¸€ä¸ªé‡çº§ä»¥ä¸Š -> å¹‚å¾‹å‘æ•£
        if products[-1] > products[0] * 10 and products[-1] > 1e-3:
            return True
    return False


def integrate(expr, lower, upper, x_name="x"):
    """å®šç§¯åˆ†ã€‚è¿”å›ž (å€¼, è¯¯å·®ä¼°è®¡, è­¦å‘Šè¯´æ˜Žåˆ—è¡¨)ã€‚

    å€¼å¯èƒ½æ˜¯å¤æ•°ï¼šè‹¥è¢«ç§¯å‡½æ•°æœ‰è™šéƒ¨ï¼Œåˆ™å®žéƒ¨ä¸Žè™šéƒ¨åˆ†åˆ«ç§¯åˆ†å†åˆæˆã€‚
    """
    compiled = CompiledExpression(expr, variables=[x_name])
    f = compiled.function(x_name) if compiled.variables else (lambda _x: compiled.evaluate())

    if _looks_divergent(f, lower, upper):
        raise CalculusError(
            "è¯¥ç§¯åˆ†åœ¨åŒºé—´ç«¯ç‚¹é™„è¿‘å‘æ•£ï¼ˆè¢«ç§¯å‡½æ•°è¶‹äºŽæ— ç©·ï¼‰ï¼Œæ— æ³•ç»™å‡ºæœ‰é™ç»“æžœã€‚"
            "è‹¥å¥‡ç‚¹åœ¨åŒºé—´å†…éƒ¨ï¼Œè¯·æ‹†æˆä¸¤æ®µåˆ†åˆ«ç§¯åˆ†ã€‚"
        )

    def _quad_scalar(g):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            val, err = _quad_mod.quad(g, lower, upper, limit=200)
            return val, err, [str(w.message) for w in caught]

    # å§‹ç»ˆåˆ†åˆ«ç§¯åˆ†å®žéƒ¨ä¸Žè™šéƒ¨ï¼šscipy çš„ quad å¯¹å¤å€¼å‡½æ•°ä¼šé™é»˜ä¸¢å¼ƒè™šéƒ¨ï¼Œ
    # è€Œã€Œäº”ç‚¹é‡‡æ ·çœ‹è™šéƒ¨ã€å¹¶ä¸å¯é  â€”â€” ä¾‹å¦‚ i*sin(4*pi*x)^2 åœ¨
    # 0, 0.25, 0.5, 0.75, 1 ä¸Šè™šéƒ¨å…¨ä¸º 0ï¼Œå®žé™…ç§¯åˆ†å´æ˜¯ 0.5iã€‚
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
        # ç»“æžœå«å®žæ‰“å®žçš„è™šéƒ¨æ—¶æ‰è¯´æ˜Žï¼Œé¿å…å¯¹å®žæ•°å‡½æ•°åˆ·æç¤º
        notes.insert(0, "è¢«ç§¯å‡½æ•°æ˜¯å¤å€¼çš„ï¼Œå·²å¯¹å®žéƒ¨ä¸Žè™šéƒ¨åˆ†åˆ«ç§¯åˆ†")

    if not np.isfinite(abs(value)):
        raise CalculusError(
            "ç§¯åˆ†ç»“æžœä¸ºæ— ç©·å¤§æˆ–æ— æ•ˆå€¼ â€”â€” è¯¥ç§¯åˆ†å¾ˆå¯èƒ½å‘æ•£ï¼Œæˆ–åŒºé—´ç«¯ç‚¹æœ‰å¥‡ç‚¹ã€‚"
        )
    if notes and not has_imag:
        notes.insert(0, "ç§¯åˆ†å™¨æŠ¥å‘Šäº†å›°éš¾ï¼ˆç»“æžœå¯èƒ½ä¸å¯é ï¼Œå¸¸è§äºŽå¥‡ç‚¹æˆ–å¼ºæŒ¯è¡ï¼‰")
    if notes and abs(error) > max(1.0, abs(value)) * 1e-3:
        notes.insert(0, f"è¯¯å·®ä¼°è®¡åå¤§ï¼ˆ{error:.3g}ï¼‰ï¼Œå»ºè®®æ‹†åˆ†åŒºé—´æˆ–æ¢å†™æ³•")
    return value, float(abs(error)), notes


# ---------------------------------------------------------------- å¯¼æ•°

def _central_diff(f, x0, h):
    """ä¸­å¿ƒå·®åˆ†ï¼Œè¿”å›ž (ä¸€é˜¶, äºŒé˜¶) è¿‘ä¼¼ã€‚"""
    fp = f(x0 + h)
    fm = f(x0 - h)
    f0 = f(x0)
    first = (fp - fm) / (2 * h)
    second = (fp - 2 * f0 + fm) / (h * h)
    return first, second


def derivative(expr, x0, order=1, x_name="x", h=None):
    """åœ¨ x0 å¤„æ±‚ä¸€é˜¶/äºŒé˜¶å¯¼æ•°ï¼Œç”¨ Richardson å¤–æŽ¨æé«˜ç²¾åº¦ã€‚

    è¿”å›ž (å¯¼æ•°å€¼, ç²—ç•¥è¯¯å·®ä¼°è®¡)ã€‚
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

    # æ­¥é•¿æŒ‰é‡çº§é€‰ï¼Œè¿‡å°ä¼šè¢«æµ®ç‚¹è¯¯å·®æ·¹æ²¡ï¼Œè¿‡å¤§åˆ™æˆªæ–­è¯¯å·®æ˜¾è‘—
    scale = max(1.0, abs(float(x0)))
    h0 = h if h else scale * 1e-4

    def estimate(step):
        first, second = _central_diff(f, x0, step)
        return first if order == 1 else second

    # Richardson: D(h) ä¸Ž D(h/2) ç»„åˆï¼Œæ¶ˆåŽ» O(h^2) é¡¹
    d1 = estimate(h0)
    d2 = estimate(h0 / 2)
    d3 = estimate(h0 / 4)
    # å…ˆé—®ç¬¦å·å±‚ï¼šå®ƒèƒ½ç»™å®šè®ºï¼ˆå¯å¯¼ / ä¸å¯å¯¼ï¼‰ï¼Œæ•°å€¼åˆ¤æ®åªåœ¨å®ƒç­”ä¸ä¸Šæ¥æ—¶å…œåº•ï¼Œ
    # è€Œä¸”åªä¿ç•™æœ€ä¿å®ˆçš„è¯æ®ã€‚
    verdict = symbolic_differentiable(expr, x0, order)

    if order == 1:
        rich = (4 * d2 - d1) / 3
        err = abs(d3 - d2)
        if verdict is None:
            _kink_check(f, x0, h0 / 4, 1)
    else:
        # äºŒé˜¶å·®åˆ†è¯¯å·®æ˜¯ O(h^2)ï¼Œå¤–æŽ¨ç³»æ•°åŒä¸ºä¸€é˜¶
        rich = (4 * d2 - d1) / 3
        err = abs(d3 - d2)
        # äºŒé˜¶å¯¼å­˜åœ¨çš„å‰ææ˜¯ä¸€é˜¶å¯¼å­˜åœ¨ï¼šå¯¹**ä¸€é˜¶**ä¸­å¿ƒå·®åˆ†åšæŠ˜ç‚¹æ£€æŸ¥ã€‚
        # |x| åœ¨ 0 çš„äºŒé˜¶å·®åˆ†æœ¬èº«ä¼šç»™å‡º 2/h è¿™ç§å·¨å¤§å€¼ï¼Œä¸æ˜¯ 0ï¼Œ
        # æ‰€ä»¥ä¸èƒ½ç›´æŽ¥æ‹¿ rich åŽ»å¥—ä¸€é˜¶çš„åˆ¤æ®ã€‚
        # äºŒé˜¶å¯¼å­˜åœ¨çš„å‰ææ˜¯ä¸€é˜¶å¯¼å­˜åœ¨ã€‚å¿…é¡»**å…ˆ**æŸ¥ä¸€é˜¶ï¼š
        # |x| åœ¨ 0 å¤„çš„äºŒé˜¶å•ä¾§å·®å•†ä¸¤è¾¹éƒ½æ°å¥½æ˜¯ 0ï¼ˆæŠ˜ç‚¹å¯¹ç§°æŠµæ¶ˆï¼‰ï¼Œ
        # åªçœ‹äºŒé˜¶ä¼šæ¼æŽ‰ï¼›è€Œ x*abs(x) ä¸€é˜¶å¯å¯¼ã€äºŒé˜¶ä¸å¯å¯¼ï¼Œåªæœ‰äºŒé˜¶æŸ¥å¾—å‡ºæ¥ã€‚
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
#: è¡¨è¾¾å¼é‡Œä¼šå‡ºçŽ°çš„åå­— -> sympy å¯¹è±¡ã€‚åªåœ¨ç¬¦å·è¯Šæ–­é‡Œç”¨ã€‚
_SYMPY_LOCALS = None


def _sympy_locals():
    """æƒ°æ€§æž„é€ ï¼Œé¿å…æ²¡è£… sympy æ—¶å½±å“æ¨¡å—å¯¼å…¥ã€‚"""
    global _SYMPY_LOCALS
    if _SYMPY_LOCALS is not None:
        return _SYMPY_LOCALS
    import sympy as sp

    _SYMPY_LOCALS = {
        "inf": sp.oo, "e": sp.E, "pi": sp.pi, "tau": getattr(sp, "tau", 2 * sp.pi), "i": sp.I,
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
    """æŠŠå†…éƒ¨è¡¨è¾¾å¼è¯­æ³•è½¬æˆ sympy è¡¨è¾¾å¼ï¼›è½¬ä¸äº†è¿”å›ž Noneã€‚"""
    try:
        import sympy as sp
    except ImportError:
        return None
    try:
        text = expr.replace("^", "**")
        sym = sp.sympify(text, locals=_sympy_locals())
    except Exception:                            # noqa: BLE001
        return None
    if not isinstance(sym, sp.Expr):
        return None
    return sym


def symbolic_differentiable(expr: str, x0: float, order: int = 1,
                            var: str = "x", *, max_ops: int = 400,
                            fixed: dict | None = None):
    """ç”¨å·¦å³å·®å•†çš„**æžé™**åˆ¤æ–­ x0 å¤„ order é˜¶å¯¼æ•°æ˜¯å¦å­˜åœ¨ã€‚

    è¿”å›ž Trueï¼ˆå¯å¯¼ï¼‰/ Falseï¼ˆä¸å¯å¯¼ï¼‰/ Noneï¼ˆåˆ¤ä¸äº†ï¼‰ã€‚

    ä¸ºä»€ä¹ˆä¸ç”¨å½¢å¼å¯¼æ•°åœ¨ç‚¹ä¸Šçš„å€¼ï¼š``abs(x)`` çš„å½¢å¼å¯¼æ•°æ˜¯ ``sign(x)``ï¼Œ
    ``sign(0) = 0``ï¼Œä»£å…¥ä¼šå¾—å‡ºã€Œå¯¼æ•°ä¸º 0ã€çš„å‡ç»“è®ºï¼›è€Œå·¦å³å·®å•†åˆ†åˆ«æ˜¯
    -1 å’Œ +1ã€‚æ‰€ä»¥è¿™é‡Œæ±‚çš„æ˜¯
        lim_{h->0+} [f(a+h) - f(a)] / h
        lim_{h->0+} [f(a) - f(a-h)] / h
    ä¸¤ä¸ªæžé™éƒ½å­˜åœ¨ä¸”ç›¸ç­‰æ‰ç®—å¯å¯¼ã€‚

    ``max_ops`` ç»™ç¬¦å·è¿ç®—ä¸€ä¸ªè§„æ¨¡ä¸Šé™ï¼Œè¶…äº†å°±æ”¾å¼ƒï¼ˆè¿”å›ž Noneï¼‰ï¼Œ
    ç”±è°ƒç”¨æ–¹æŒ‰ã€Œæ— æ³•ç¡®è®¤ã€å¤„ç†ã€‚
    """
    try:
        import sympy as sp
    except ImportError:
        return None

    # é«˜é˜¶å¯¼æ•°å­˜åœ¨çš„å‰ææ˜¯ä½Žé˜¶å­˜åœ¨ã€‚|x| çš„äºŒé˜¶å·®å•†å·¦å³æžé™éƒ½æ˜¯ 0
    # ï¼ˆæŠ˜ç‚¹å¯¹ç§°æŠµæ¶ˆï¼‰ï¼ŒåªæŸ¥äºŒé˜¶ä¼šæ¼ï¼›x*abs(x) æ°æ°ç›¸å â€”â€” ä¸€é˜¶å¯å¯¼ã€
    # äºŒé˜¶ä¸å¯å¯¼ã€‚æ‰€ä»¥ä¸¤è€…éƒ½è¦æŸ¥ã€‚
    if order > 1 and order <= 2:
        lower = symbolic_differentiable(expr, x0, order - 1, var,
                                       max_ops=max_ops)
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

    # åå¯¼æ—¶å…¶å®ƒå˜é‡æ˜¯å¸¸æ•°ï¼šå…ˆæŠŠå®ƒä»¬ä»£è¿›åŽ»ï¼Œå‰©ä¸‹çš„åªå‰© var
    if fixed:
        try:
            subs_map = {sp.Symbol(k): sp.Float(float(v))
                        for k, v in fixed.items() if k != var}
            if subs_map:
                sym = sym.subs(subs_map)
        except Exception:                        # noqa: BLE001
            return None

    # å¿…é¡»ç”¨**è¡¨è¾¾å¼è‡ªå·±çš„ç¬¦å·**åš subsï¼šSymbol('x') å’Œ
    # Symbol('x', real=True) åœ¨ sympy é‡Œæ˜¯ä¸¤ä¸ªä¸åŒçš„å¯¹è±¡ï¼Œ
    # ç”¨åŽè€…åŽ» subs ä¼šé™é»˜ä¸æ›¿æ¢ï¼Œå·¦å³æžé™éƒ½ç®—æˆ 0ã€‚
    free = sym.free_symbols
    x = free.pop() if len(free) == 1 else None
    if x is None:
        return None
    if str(x) != var:
        return None
    try:
        a = sp.Integer(0) if float(x0) == 0.0 else sp.Float(x0)
        h = sp.Symbol("h", positive=True)
        fa = sym.subs(x, a)
        fp = sym.subs(x, a + h)
        fm = sym.subs(x, a - h)

        if order == 1:
            right_expr = (fp - fa) / h
            left_expr = (fa - fm) / h
        else:
            f2p = sym.subs(x, a + 2 * h)
            f2m = sym.subs(x, a - 2 * h)
            right_expr = (f2p - 2 * fp + fa) / h ** 2
            left_expr = (fa - 2 * fm + f2m) / h ** 2

        right = sp.limit(right_expr, h, 0, "+")
        left = sp.limit(left_expr, h, 0, "+")
    except Exception:                            # noqa: BLE001
        return None

    for value in (right, left):
        if value is None or value.has(sp.zoo, sp.nan):
            return False                         # å‘æ•£ -> ä¸å¯å¯¼
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
    """è¿”å›ž (å·¦ä¾§, å³ä¾§) çš„å•ä¾§å·®å•†ã€‚

    order=1 ç”¨å‰å‘/åŽå‘ä¸€é˜¶å·®å•†ï¼›order=2 ç”¨ä¸‰ç‚¹çš„äºŒé˜¶å•ä¾§å·®å•†ã€‚
    """
    if order == 1:
        right = (f(x0 + step) - f(x0)) / step
        left = (f(x0) - f(x0 - step)) / step
    else:
        right = (f(x0 + 2 * step) - 2 * f(x0 + step) + f(x0)) / (step * step)
        left = (f(x0) - 2 * f(x0 - step) + f(x0 - 2 * step)) / (step * step)
    return left, right


def _kink_check(f, x0, step, order):
    """åˆ¤æ–­ x0 å¤„ order é˜¶å¯¼æ•°æ˜¯å¦çœŸçš„å­˜åœ¨ã€‚

    æ ¸å¿ƒæ€è·¯ï¼š**çœ‹å·¦å³å·®å•†ä¹‹å·®éšæ­¥é•¿ç¼©å°çš„è¡Œä¸º**ã€‚

      * å…‰æ»‘å‡½æ•°ï¼šä¸¤è¾¹éƒ½æ”¶æ•›åˆ°åŒä¸€ä¸ªå¯¼æ•°ï¼Œgap(h) ~ C*hï¼Œ
        æ­¥é•¿ç¼©åˆ° 1/4ï¼Œgap ä¹Ÿç¼©åˆ°çº¦ 1/4ã€‚
      * æŠ˜ç‚¹ï¼šä¸¤è¾¹åˆ†åˆ«æ”¶æ•›åˆ° f'_+ å’Œ f'_-ï¼Œgap(h) -> |f'_+ - f'_-|ï¼Œ
        æ˜¯ä¸ª**éžé›¶å¸¸æ•°**ï¼Œæ­¥é•¿å†å°ä¹Ÿä¸ç¼©ã€‚

    å¯¹å›ºå®šæ­¥é•¿æ¯”è¾ƒå·¦å³å·®å•†æ˜¯é”™çš„ï¼šå…‰æ»‘æžå€¼é™„è¿‘å·¦å³å·®å•†æœ¬æ¥å°±ä¸åŒ
    ï¼ˆ1000*x**2 åœ¨ 0 å¤„æ˜¯ âˆ“2000h é‡çº§ï¼‰ï¼Œé‚£ä¸æ˜¯æŠ˜ç‚¹ï¼Œé‚£æ˜¯äºŒé˜¶å¯¼ã€‚

    åªæœ‰ gap æ˜Žæ˜¾ä¸éšæ­¥é•¿æ”¶æ•›æ—¶æ‰æ‹’ç»ã€‚å–ä¸åˆ°å€¼ï¼ˆè¶Šç•Œã€æ±‚å€¼å¤±è´¥ï¼‰å°±
    æ”¾å¼ƒåˆ¤æ–­ï¼Œäº¤ç»™è°ƒç”¨æ–¹æŒ‰åŽŸé€»è¾‘å¤„ç†ã€‚
    """
    scales = [step / (4.0 ** i) for i in range(6)]
    gaps = []
    sample = None
    for scale_step in scales:
        try:
            left, right = _sided_quotients(f, x0, scale_step, order)
        except Exception:                        # noqa: BLE001
            return                                   # å–ä¸åˆ°å€¼å°±ä¸åˆ¤
        gaps.append(abs(left - right))
        if sample is None:
            sample = (left, right)

    # ä¸¤ä¾§ä¸€ä¸Šæ¥å°±ä¸€è‡´ï¼ˆæµ®ç‚¹å™ªå£°é‡çº§ï¼‰ï¼šæ²¡å¾—åˆ¤
    if gaps[0] <= 1e-9 * max(1.0, abs(sample[0]), abs(sample[1])):
        return

    # gap å¿…é¡»**å•è°ƒä¸å¢ž**ï¼ˆå…è®¸ 10% æŠ–åŠ¨ï¼‰æ‰å¯èƒ½æ˜¯æŠ˜ç‚¹ã€‚
    # å˜å¤§è¯´æ˜Žæ­¥é•¿è¿˜æ²¡è§£æžåˆ°å‡½æ•°å°ºåº¦ â€”â€” ä¾‹å¦‚ cos(200000*x) åœ¨é»˜è®¤æ­¥é•¿
    # ä¸‹ `[x0-h, x0+h]` å†…æŒ¯è¡å¤šæ¬¡ï¼Œé‚£æ˜¯é‡‡æ ·ä¸è¶³ï¼Œä¸æ˜¯ä¸å¯å¯¼ã€‚
    monotone = all(gaps[i + 1] <= gaps[i] * 1.1
                   for i in range(len(gaps) - 1))
    if not monotone:
        return

    # æ•°å€¼åˆ¤æ®åªæ˜¯**å…œåº•**ï¼Œç”¨åœ¨ç¬¦å·ç®—ä¸å‡ºæ¥çš„æ—¶å€™ã€‚
    #
    # é—¨æ§›æåˆ° 0.999ã€å°ºåº¦æ‰©åˆ°è·¨ 4^5 = 1024 å€ï¼šåªæœ‰ gap åœ¨è¿™ä¹ˆå®½çš„
    # åŒºé—´é‡Œ**å®Œå…¨ä¸å˜**æ‰ç®—è¯æ®ã€‚è¿™æ­£æ˜¯ |x| çš„ç‰¹å¾ï¼ˆgap æ’ä¸º 2ï¼‰ã€‚
    #
    # ä¸ºä»€ä¹ˆä¸å†ç”¨ 0.5 / 0.8 / 0.97ï¼šæœ‰é™é‡‡æ ·æ— æ³•åŒºåˆ†ã€Œgap è¶‹äºŽ 0ã€å’Œ
    # ã€Œgap è¶‹äºŽéžé›¶å¸¸æ•°ã€ã€‚
    #   abs(x)+10000*x^2  gap = 2 + 20000hï¼Œè·¨ 64 å€æ¯”å€¼ 0.803ï¼Œçœ‹ä¼¼åœ¨
    #                     æ”¶æ•›ï¼Œå…¶å®žè¶‹äºŽ 2 â€”â€” ä»»ä½• â‰¤0.97 çš„é—¨æ§›éƒ½ä¼šæ¼åˆ¤ã€‚
    #   sqrt(x^2+1e-16)   gap å…ˆåƒå¹³å°ï¼Œå…¶å®žè¶‹äºŽ 0 â€”â€” ä¼šè¯¯æ‹’ã€‚
    # æ‰€ä»¥è¿™é‡ŒåªæŠ“ã€Œå®Œå…¨ä¸å˜ã€ï¼Œå…¶ä½™äº¤ç»™ç¬¦å·å±‚ã€‚
    if gaps[-1] > 0.999 * gaps[0]:
        left, right = sample
        raise CalculusError(
            "Not differentiable at this point: the one-sided difference "
            f"quotients converge to different values "
            f"({left:+.6g} from the left, {right:+.6g} from the right; "
            f"their gap stays near {gaps[-1]:+.6g} while the step shrinks "
            "by a factor of 64). A symmetric difference is an average of "
            "the two sides, not a derivative.")


# ---------------------------------------------------------------- åå¯¼

def partial_derivative(expr, variables, point, var, order=1, cross_var=None):
    """åœ¨ point å¤„å¯¹ var æ±‚åå¯¼ï¼ˆorder=1/2ï¼›ç»™ cross_var åˆ™ä¸ºæ··åˆäºŒé˜¶ï¼‰ã€‚

    point æ˜¯ {å˜é‡å: æ•°å€¼}ã€‚
    è¿”å›ž (åå¯¼å€¼, è¯¯å·®ä¼°è®¡)ã€‚
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
            # åŒä¸€æ—é—®é¢˜ï¼šabs(x)+y åœ¨åŽŸç‚¹çš„åå¯¼ä¹Ÿæ˜¯å¯¹ç§°å·®åˆ†çš„é›¶ç‚¹
            _kink_check(lambda t, _v=var: f(**{_v: t}), point[var], h / 4, 1)
        elif verdict is False:
            raise CalculusError(
                f"Partial derivative w.r.t. {var} does not exist at this "
                "point (left and right difference quotients differ).")
        return _realify(rich), float(abs(val(h / 4) - d2))

    if order == 2 and cross_var is None:
        def val2(step):
            up = {var: point[var] + step}
            dn = {var: point[var] - step}
            return (f(**up) - 2 * f() + f(**dn)) / (step * step)

        d1, d2 = val2(h), val2(h / 2)
        rich = (4 * d2 - d1) / 3
        if verdict is None:
            # äºŒé˜¶åå¯¼åŒæ ·ï¼šå…ˆæŸ¥ä¸€é˜¶å­˜åœ¨ï¼Œå†æŸ¥äºŒé˜¶æ”¶æ•›
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


# ---------------------------------------------------------------- è¾…åŠ©
