"""安全表达式求值（微积分计算器的公共基础）。

绝不直接 eval 用户输入。做法：
  1. 预处理：把数学写法转成 Python 能解析的形式（`^` → `**`、隐式乘法、`π`/`√`）
  2. ast.parse 得到语法树
  3. 只允许白名单节点与函数名，其余一律拒绝（属性访问、导入、推导式、lambda…）
  4. 用 numpy 求值，支持变量逐个传入

限制：表达式长度、节点数、幂指数（防止 10**10**10 这类卡死）。
"""

import ast
import math
import re

import numpy as np

#: 允许出现的数学函数 / 常量
CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
    #: 虚数单位。注意：因此 i / I 不能用作变量名（数学语境下这是常规约定）。
    "i": 1j,
    "I": 1j,
}

def _sqrt(x):
    """平方根：对负数返回复数结果（数学上 sqrt(-1)=i）。

    numpy 的 sqrt(-1.0) 会返回 nan 并警告，在支持虚数单位 i 之后这就不合适了。
    """
    a = np.asarray(x)
    if np.isrealobj(a) and np.any(a < 0):
        a = a.astype(complex)
    return np.sqrt(a)


FUNCTIONS = {
    # 三角
    "sin": np.sin, "cos": np.cos, "tan": np.tan,
    "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan,
    "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh,
    # 指数对数
    "exp": np.exp, "log": np.log, "log10": np.log10, "log2": np.log2,
    "sqrt": _sqrt, "cbrt": np.cbrt,
    # 其他
    "abs": np.abs, "floor": np.floor, "ceil": np.ceil,
    "sign": np.sign, "expm1": np.expm1, "log1p": np.log1p,
    # 复数
    "re": np.real, "im": np.imag, "conj": np.conj, "angle": np.angle,
}

#: 允许的运算符
ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod)
ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)

#: 安全上限
MAX_LENGTH = 500
MAX_NODES = 400
MAX_ABS_EXPONENT = 1000


class ExpressionError(ValueError):
    """表达式不合法（语法/未授权节点/超限）。"""


# ---------------------------------------------------------------- 预处理

_SUPERSCRIPTS = {"²": "^2", "³": "^3", "⁴": "^4"}

#: 分词：** 必须排在 * 前面（否则会被拆成两个乘号）
_TOKEN_RE = re.compile(
    r"\s*(\*\*|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[A-Za-z_]\w*|[()\[\]]|[-+*/%])"
)


def _tokenize(s):
    tokens = []
    pos = 0
    while pos < len(s):
        m = _TOKEN_RE.match(s, pos)
        if not m:
            chunk = s[pos:].strip()
            if not chunk:
                break
            raise ExpressionError(f"Unrecognized character: {chunk[:10]}")
        tokens.append(m.group(1))
        pos = m.end()
    return tokens


def _is_operand_end(tok):
    """这个 token 是否是"值"的结尾（可以紧接下一个值，形成隐式乘法）。"""
    if not tok:
        return False
    if tok in (")", "]"):
        return True
    if tok[0].isdigit():
        return True
    # 标识符：只有不是函数名/常量时才算普通值（sin 后面接 "(" 是函数调用）
    if tok[0].isalpha() or tok[0] == "_":
        return tok not in FUNCTIONS and tok not in CONSTANTS
    return False


def _is_operand_start(tok):
    """这个 token 是否是一个值的开头。"""
    if not tok:
        return False
    if tok in ("(", "[") or tok[0].isdigit():
        return True
    if tok[0].isalpha() or tok[0] == "_":
        return True
    return False


def preprocess(expr):
    """把常见的数学写法归一化成 Python 表达式。

    用分词重建而不是正则替换：正则替换一旦顺序没排好就会把 `sin(x)` 变成
    `sin*(x)`（我踩过）。逐 token 扫描时按"值/函数/运算符"分类判断，
    "函数名 + 左括号"不会被误插乘号。
    """
    if expr is None:
        raise ExpressionError("Expression is empty")
    s = str(expr).strip()
    if not s:
        raise ExpressionError("Expression is empty")
    if len(s) > MAX_LENGTH:
        raise ExpressionError(f"Expression too long (over {MAX_LENGTH} characters)")

    # 字符级替换（不产生歧义的）
    s = s.replace("，", ",").replace("×", "*").replace("÷", "/")
    s = s.replace("−", "-").replace("–", "-")
    for sup, repl in _SUPERSCRIPTS.items():
        s = s.replace(sup, repl)
    s = s.replace("π", "pi").replace("√", "sqrt")
    s = s.replace(",", "")
    s = s.replace("^", "**")

    tokens = [t for t in _tokenize(s) if t != " "]
    out = []
    for i, tok in enumerate(tokens):
        prev = out[-1] if out else None
        if prev is not None and _is_operand_end(prev) and _is_operand_start(tok):
            out.append("*")
        out.append(tok)
    result = "".join(out)

    # "sqrtx" 这种（√ 直接跟变量）补成 sqrt(x)
    result = re.sub(r"\bsqrt\b(?!\s*\()([A-Za-z_]\w*)", r"sqrt(\1)", result)
    return result


# ---------------------------------------------------------------- 校验

_ALLOWED_NODE_TYPES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Call,
    ast.Load,
    # 运算符节点（漏了它们会导致所有算术被误拒）
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
    ast.UAdd, ast.USub,
)


def _validate(tree, declared=None):
    """逐节点检查白名单；返回表达式中出现的自由变量集合。

    declared 为空时表示"自动识别变量"，此时不拒绝未知名字；
    给出了 declared 则要求表达式的变量必须都在其中。
    """
    declared = set(declared or ())
    names = set()
    count = 0
    for node in ast.walk(tree):
        count += 1
        if count > MAX_NODES:
            raise ExpressionError("Expression too complex")
        if not isinstance(node, _ALLOWED_NODE_TYPES):
            raise ExpressionError(f"Unsupported syntax: {type(node).__name__}")
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise ExpressionError("Only numeric constants are allowed")
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, ALLOWED_BINOPS):
                raise ExpressionError("Unsupported operator")
            if isinstance(node.op, ast.Pow):
                _check_exponent(node.right)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, ALLOWED_UNARYOPS):
                raise ExpressionError("Unsupported unary operator")
        elif isinstance(node, ast.Name):
            if node.id in FUNCTIONS or node.id in CONSTANTS:
                continue
            if not node.id.isidentifier():
                raise ExpressionError(f"Invalid name: {node.id}")
            names.add(node.id)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ExpressionError("Only direct function calls are allowed")
            if node.func.id not in FUNCTIONS:
                raise ExpressionError(f"Unknown function: {node.func.id}")
            if node.keywords:
                raise ExpressionError("Keyword arguments are not supported")
    if declared:
        extra = names - declared
        if extra:
            raise ExpressionError(f"Undeclared variable(s): {', '.join(sorted(extra))}")
    return names


def _fold_constant(node):
    """尝试把纯常量算术折叠成数值；做不到返回 None。

    只用于"指数到底多大"的判定，覆盖加减乘除幂即可。
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        inner = _fold_constant(node.operand)
        if inner is None:
            return None
        return -inner if isinstance(node.op, ast.USub) else inner
    if isinstance(node, ast.BinOp):
        left = _fold_constant(node.left)
        right = _fold_constant(node.right)
        if left is None or right is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Pow):
                # 指数本身就超限时不再真去算（避免 10**(10**10) 卡死）
                if isinstance(right, (int, float)) and abs(right) > MAX_ABS_EXPONENT:
                    return float("inf")
                return left ** right
        except (OverflowError, ZeroDivisionError, MemoryError):
            return float("inf")
    return None


def _check_exponent(node):
    """拦掉 10**10**10 之类的爆炸指数。

    只逐节点看常量不够：`10**10**10` 的指数部分是 `10**10`，本身就是个幂，
    必须先折叠成数值（10_000_000_000）再比上限。
    """
    value = _fold_constant(node)
    if value is not None:
        if value != value or abs(float(value)) > MAX_ABS_EXPONENT:   # NaN / 超限
            raise ExpressionError(f"Exponent too large (limit {MAX_ABS_EXPONENT})")
        return

    # 折叠不出来（含变量或结构复杂）时用 sympy 试算一次；不可用就放行。
    try:
        import sympy as sp
    except Exception:
        return
    try:
        expr = sp.sympify(ast.unparse(node), evaluate=True)
        if expr.free_symbols:
            return
        if expr.is_number:
            approx = float(expr)
            if approx != approx or abs(approx) > MAX_ABS_EXPONENT:
                raise ExpressionError(f"Exponent too large (limit {MAX_ABS_EXPONENT})")
    except ExpressionError:
        raise
    except Exception:
        return


# ---------------------------------------------------------------- 求值

class CompiledExpression:
    """编译后的表达式：可反复求值，变量名固定。"""

    def __init__(self, expr, variables=None):
        self.source = str(expr)
        self.python_expr = preprocess(expr)
        try:
            tree = ast.parse(self.python_expr, mode="eval")
        except SyntaxError as exc:
            raise ExpressionError(f"Syntax error: {exc.msg}") from exc

        declared = set(variables) if variables else set()
        # _validate 返回的是"不在 FUNCTIONS/CONSTANTS 里的自由名字"，即变量
        self.variables = _validate(tree, declared)
        if declared:
            extra = self.variables - declared
            if extra:
                raise ExpressionError(f"Undeclared variable(s): {', '.join(sorted(extra))}")
        self._code = compile(tree, "<expr>", "eval")

    def evaluate(self, **kwargs):
        """按变量求值；返回 numpy 数组或标量。"""
        if self.variables and not kwargs:
            raise ExpressionError(f"Missing value for variable(s): {', '.join(sorted(self.variables))}")
        env = dict(CONSTANTS)
        env.update(FUNCTIONS)
        env.update(kwargs)
        with np.errstate(all="ignore"):
            result = eval(self._code, {"__builtins__": {}}, env)   # noqa: S307 - 已白名单校验
        return result

    def function(self):
        """把表达式包装成可被 scipy 调用的单变量函数 f(x)。"""
        if self.variables and self.variables != {"x"}:
            raise ExpressionError(f"Expression contains variables {sorted(self.variables)}; it is not a function of a single variable x")
        return lambda x: self.evaluate(x=x) if self.variables else self.evaluate()

    def multivariable(self):
        """包装成 f(x, y, ...) 可调用对象（用于偏导）。"""
        names = sorted(self.variables)

        def f(*args, **kwargs):
            env = dict(zip(names, args)) if args else {}
            env.update(kwargs)
            return self.evaluate(**env)

        return f
