"""微积分页：定/不定积分、导数、偏导。

界面只负责收集参数与呈现结果；所有数学都在 core 里（已与 myapp 逐位对齐）。
三个标签页共用同一个「结果面板」，保证 LaTeX、源码、数值三处表现一致。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core import calculus_numeric as cn
from core import calculus_symbolic as cs
from core import latex_render as lr

# 画图是可选的：没装 matplotlib 时 Taylor 页照样能算，只是不出图
try:
    import numpy as np
    from matplotlib.backends.backend_qtagg import (
        FigureCanvasQTAgg as FigureCanvas,
        NavigationToolbar2QT as NavigationToolbar,
    )
    from matplotlib.figure import Figure
    HAS_PLOT = True
except Exception:                                # noqa: BLE001
    HAS_PLOT = False

#: 变量假设：(中文, core 里用的键)
ASSUMPTIONS = [
    ("Real (default)", None),
    ("Integer", "integer"),
    ("Positive", "positive"),
    ("Non-negative", "nonnegative"),
    ("Complex", "complex"),
]


class ResultPanel(QWidget):
    """Result view: LaTeX image + source + numeric text."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # ---- LaTeX 图 ----
        self._image_holder = QFrame()
        self._image_holder.setObjectName("latexBox")
        self._image_holder.setMinimumHeight(120)
        holder_layout = QVBoxLayout(self._image_holder)
        holder_layout.setContentsMargins(12, 10, 12, 10)
        self._image = QLabel("(the result will appear here)")
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image.setWordWrap(True)
        holder_layout.addWidget(self._image)
        layout.addWidget(self._image_holder)

        # ---- LaTeX 源码 ----
        src_row = QHBoxLayout()
        src_row.setSpacing(6)
        src_row.addWidget(QLabel("LaTeX："))
        self._source = QLineEdit()
        self._source.setReadOnly(True)
        self._source.setObjectName("codeField")
        src_row.addWidget(self._source, 1)
        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setObjectName("ghost")
        self._copy_btn.setFixedWidth(64)
        self._copy_btn.clicked.connect(self._copy_source)
        src_row.addWidget(self._copy_btn)
        layout.addLayout(src_row)

        # ---- 数值/说明 ----
        self._numeric = QPlainTextEdit()
        self._numeric.setReadOnly(True)
        self._numeric.setObjectName("numericBox")
        self._numeric.setFixedHeight(92)
        layout.addWidget(self._numeric)

    # ------------------------------------------------------------------
    def show_result(self, *, latex: str = "", text: str = "",
                    numeric: str = "", error: str = "") -> None:
        if error:
            self._image.setPixmap(QPixmap())
            self._image.setText(f"⚠ {error}")
            self._image.setStyleSheet("color:#b3261e;")
            self._source.setText("")
            self._numeric.setPlainText("")
            return

        self._image.setStyleSheet("")
        self._source.setText(latex or "")
        self._numeric.setPlainText(numeric or text or "")

        if latex:
            png = None
            render_error = ""
            try:
                png = lr.render_latex(latex, fontsize=13)
            except Exception as exc:             # noqa: BLE001
                # mathtext 只支持数学模式子集：cases / aligned 这类 LaTeX
                # 会抛 LatexError。预览失败不该把已经算好的数值和源码吞掉。
                render_error = str(exc)
            if png:
                pix = QPixmap()
                if pix.loadFromData(png):
                    # 宽图按可用宽度缩放，避免撑破窗口
                    max_w = max(200, self._image_holder.width() - 40)
                    if pix.width() > max_w:
                        pix = pix.scaledToWidth(max_w, Qt.TransformationMode.SmoothTransformation)
                    self._image.setPixmap(pix)
                    return
            elif render_error:
                self._image.setPixmap(QPixmap())
                self._image.setStyleSheet("color:#8a6d1f;")
                self._image.setText(
                    "(LaTeX preview unavailable — the result contains syntax "
                    "mathtext cannot draw, such as a Piecewise/cases expression. "
                    "The source and numbers below are still valid.)\n\n"
                    + render_error)
                return
        self._image.setText(text or "(no LaTeX result)")

    def _copy_source(self) -> None:
        text = self._source.text()
        if text:
            QGuiApplication.clipboard().setText(text)


class _BaseTab(QWidget):
    """Shared skeleton for the three tabs: form + buttons + result panel."""

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        form_box = QFrame()
        form_box.setObjectName("formBox")
        self.form = QFormLayout(form_box)
        self.form.setContentsMargins(14, 12, 14, 12)
        self.form.setSpacing(8)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(form_box)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.run_btn = QPushButton("Compute")
        self.run_btn.setObjectName("primary")
        self.run_btn.setFixedWidth(96)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("ghost")
        self.clear_btn.setFixedWidth(72)
        btn_row.addWidget(self.run_btn)
        btn_row.addWidget(self.clear_btn)
        btn_row.addStretch(1)
        outer.addLayout(btn_row)

        self.result = ResultPanel()
        outer.addWidget(self.result, 1)

        self.run_btn.clicked.connect(self.compute)
        self.clear_btn.clicked.connect(self.clear)

    # 子类实现
    def compute(self) -> None:            # pragma: no cover - 由子类覆盖
        raise NotImplementedError

    def clear(self) -> None:              # pragma: no cover
        self.result.show_result()


def _expr_field(placeholder: str) -> QLineEdit:
    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    field.setObjectName("exprField")
    return field


def _var_field(placeholder: str = "x") -> QLineEdit:
    field = QLineEdit(placeholder)
    field.setFixedWidth(90)
    return field


def _assumption_combo() -> QComboBox:
    combo = QComboBox()
    for label, _key in ASSUMPTIONS:
        combo.addItem(label)
    combo.setFixedWidth(140)
    return combo


def _assumptions_for(combo: QComboBox, variables) -> dict[str, str] | None:
    """把下拉选择转成核心层要的 ``{变量名: 假设}`` 映射。

    核心的 ``_make_symbol`` 期望按**变量名**索引的字典（内部用
    ``assumptions.get(name)``）；直接传一个字符串会在那里抛
    ``AttributeError: 'str' object has no attribute 'get'``。
    返回 None 表示用默认假设（real）。
    """
    key = ASSUMPTIONS[combo.currentIndex()][1]
    if not key:
        return None
    names = [str(v).strip() for v in variables]
    return {name: key for name in names if name}


# ====================================================================== 积分
class IntegralTab(_BaseTab):
    def __init__(self) -> None:
        super().__init__()
        self.expr = _expr_field("e.g.  x^2   or  e^(i*x)   or  sin(x)/x")
        self.var = _var_field("x")
        self.assume = _assumption_combo()
        self.lower = QLineEdit()
        self.lower.setPlaceholderText("leave empty for indefinite")
        self.upper = QLineEdit()
        self.upper.setPlaceholderText("leave empty for indefinite")

        self.form.addRow("Integrand", self.expr)
        var_row = QHBoxLayout()
        var_row.setSpacing(8)
        var_row.addWidget(self.var)
        var_row.addWidget(QLabel("Assumption"))
        var_row.addWidget(self.assume)
        var_row.addStretch(1)
        self.form.addRow("Variable", _wrap(var_row))
        bounds = QHBoxLayout()
        bounds.setSpacing(8)
        bounds.addWidget(self.lower)
        bounds.addWidget(QLabel("→"))
        bounds.addWidget(self.upper)
        self.form.addRow("Bounds", _wrap(bounds))

    def compute(self) -> None:
        expr = self.expr.text().strip()
        if not expr:
            self.result.show_result(error="Please enter an integrand first")
            return
        var = self.var.text().strip() or "x"
        lo_text = self.lower.text().strip()
        hi_text = self.upper.text().strip()

        assumptions = _assumptions_for(self.assume, [var])

        if not lo_text and not hi_text:
            # ---- 不定积分 ----
            try:
                txt, latex, notes = cs.integrate_symbolic(
                    expr, var, assumptions=assumptions)
            except Exception as exc:
                self.result.show_result(error=f"{type(exc).__name__}: {exc}")
                return
            self.result.show_result(
                latex=latex, text=txt,
                numeric="\n".join(notes) if notes else "indefinite integral")
            return

        if not lo_text or not hi_text:
            self.result.show_result(error="A definite integral needs both bounds")
            return

        # 数值和符号各算各的：一边失败不该丢掉另一边已经算好的结果。
        lines: list[str] = []
        warns: list[str] = []
        notes: list[str] = []
        numeric_value = None
        symbolic_text = symbolic_latex = None

        try:
            lo = cn.parse_bound(lo_text)
            hi = cn.parse_bound(hi_text)
            numeric_value, err, warns = cn.integrate(expr, lo, hi, x_name=var)
            lines.append(f"Numeric result: {numeric_value}")
            lines.append(f"Error estimate: ±{err:.3e}")
        except Exception as exc:
            lines.append(f"Numeric failed: {type(exc).__name__}: {exc}")

        try:
            # 传原始文本而不是 float：符号层自己解析，pi 这类值不会被截成 3
            symbolic_text, symbolic_latex, notes = cs.integrate_symbolic(
                expr, var, lower=lo_text, upper=hi_text,
                assumptions=assumptions)
            lines.append(f"Symbolic result: {symbolic_text}")
        except Exception as exc:
            lines.append(f"Symbolic failed: {type(exc).__name__}: {exc}")

        for w in (warns or []) + (notes or []):
            lines.append(f"Note: {w}")

        if numeric_value is None and symbolic_text is None:
            self.result.show_result(error="\n".join(lines))
            return
        self.result.show_result(latex=symbolic_latex, text=symbolic_text,
                                numeric="\n".join(lines))


# ====================================================================== 导数
class DerivativeTab(_BaseTab):
    def __init__(self) -> None:
        super().__init__()
        self.expr = _expr_field("e.g.  sin(x)*x   or  exp(-x^2)")
        self.var = _var_field("x")
        self.assume = _assumption_combo()
        self.point = QLineEdit("")
        self.point.setMinimumWidth(240)
        self.point.setPlaceholderText("leave empty = expression only")
        self.order = QSpinBox()
        self.order.setRange(1, 2)
        self.order.setValue(1)
        self.order.setFixedWidth(70)

        self.form.addRow("Function", self.expr)
        var_row = QHBoxLayout()
        var_row.setSpacing(8)
        var_row.addWidget(self.var)
        var_row.addWidget(QLabel("Assumption"))
        var_row.addWidget(self.assume)
        var_row.addStretch(1)
        self.form.addRow("Variable", _wrap(var_row))
        pt_row = QHBoxLayout()
        pt_row.setSpacing(8)
        pt_row.addWidget(self.point)
        pt_row.addWidget(QLabel("Order"))
        pt_row.addWidget(self.order)
        pt_row.addStretch(1)
        self.form.addRow("Evaluate at (optional)", _wrap(pt_row))

    def compute(self) -> None:
        expr = self.expr.text().strip()
        if not expr:
            self.result.show_result(error="Please enter a function first")
            return
        var = self.var.text().strip() or "x"
        order = self.order.value()
        assumptions = _assumptions_for(self.assume, [var])

        # 求值点是可选的：留空就只给符号表达式
        point_text = self.point.text().strip()
        x0 = None
        if point_text:
            try:
                x0 = float(point_text)
            except ValueError:
                self.result.show_result(
                    error="The evaluation point must be a number "
                          "(leave it empty to get the expression only)")
                return

        lines: list[str] = []
        symbolic_text = symbolic_latex = None
        numeric_ok = False

        # 数值结果（只有填了求值点才算）
        if x0 is not None:
            try:
                value, err = cn.derivative(expr, x0, order=order, x_name=var)
                lines.append(
                    f"{'second' if order == 2 else 'first'} derivative at "
                    f"{var} = {x0}: {value}")
                lines.append(f"Error estimate: ±{err:.3e}")
                numeric_ok = True
            except Exception as exc:                 # noqa: BLE001
                lines.append(f"Numeric failed: {type(exc).__name__}: {exc}")

        # 符号结果（总是尝试）
        try:
            symbolic_text, symbolic_latex = cs.differentiate(
                expr, var, order=order, assumptions=assumptions)
            lines.append(f"Derivative: {symbolic_text}")
        except Exception as exc:                     # noqa: BLE001
            lines.append(f"Symbolic failed: {type(exc).__name__}: {exc}")

        if symbolic_text is None and not numeric_ok:
            self.result.show_result(error="\n".join(lines))
            return
        self.result.show_result(latex=symbolic_latex, text=symbolic_text,
                                numeric="\n".join(lines))


# ====================================================================== 偏导
class PartialTab(_BaseTab):
    def __init__(self) -> None:
        super().__init__()
        self.expr = _expr_field("e.g.  x^2*y + sin(z)")
        self.vars = QLineEdit("x, y")
        self.vars.setPlaceholderText("comma-separated variable names")
        self.assume = _assumption_combo()
        self.point = QLineEdit("")
        self.point.setMinimumWidth(240)
        self.point.setPlaceholderText("x=1, y=2 — or leave empty")
        self.which = QLineEdit("x")
        self.which.setFixedWidth(70)
        self.order = QSpinBox()
        self.order.setRange(1, 2)
        self.order.setValue(1)
        self.order.setFixedWidth(70)

        self.form.addRow("Function", self.expr)
        v_row = QHBoxLayout()
        v_row.setSpacing(8)
        v_row.addWidget(self.vars)
        v_row.addWidget(QLabel("Assumption"))
        v_row.addWidget(self.assume)
        v_row.addStretch(1)
        self.form.addRow("Variables", _wrap(v_row))
        p_row = QHBoxLayout()
        p_row.setSpacing(8)
        p_row.addWidget(self.point)
        p_row.addWidget(QLabel("w.r.t."))
        p_row.addWidget(self.which)
        p_row.addWidget(QLabel(", order"))
        p_row.addWidget(self.order)
        p_row.addStretch(1)
        self.form.addRow("Evaluate at (optional)", _wrap(p_row))

    def compute(self) -> None:
        expr = self.expr.text().strip()
        if not expr:
            self.result.show_result(error="Please enter a function first")
            return
        variables = [v.strip() for v in self.vars.text().split(",") if v.strip()]
        if not variables:
            self.result.show_result(error="At least one variable is required")
            return
        var = self.which.text().strip() or variables[0]
        if var not in variables:
            self.result.show_result(error=f"Differentiation variable {var} is not in the variable list")
            return

        # 求值点是可选的：留空就只给符号表达式
        point: dict[str, float] = {}
        raw_point = self.point.text().strip()
        if raw_point:
            for piece in raw_point.split(","):
                piece = piece.strip()
                if not piece:
                    continue
                if "=" not in piece:
                    self.result.show_result(
                        error=f"Evaluation point \"{piece}\" should be written "
                              f"as name=value (or leave the field empty)")
                    return
                name, _, value = piece.partition("=")
                try:
                    point[name.strip()] = float(value.strip())
                except ValueError:
                    self.result.show_result(error=f"\"{value.strip()}\" is not a number")
                    return

        order = self.order.value()
        assume = _assumptions_for(self.assume, variables)

        lines: list[str] = []
        symbolic_text = symbolic_latex = None
        numeric_ok = False

        # 数值结果：只有在给出**完整**求值点时才计算
        if point:
            missing = [v for v in variables if v not in point]
            if missing:
                lines.append("Numeric skipped — no value given for "
                             + ", ".join(missing))
            else:
                try:
                    value, err = cn.partial_derivative(
                        expr, variables, point, var, order=order)
                    lines.append(f"∂/∂{var} at {point}: {value}")
                    lines.append(f"Error estimate: ±{err:.3e}")
                    numeric_ok = True
                except Exception as exc:             # noqa: BLE001
                    lines.append(
                        f"Numeric failed: {type(exc).__name__}: {exc}")

        # 符号结果：总是尝试
        try:
            symbolic_text, symbolic_latex = cs.partial(
                expr, variables, var, order=order, assumptions=assume)
            lines.append(f"Partial derivative: {symbolic_text}")
        except Exception as exc:                     # noqa: BLE001
            lines.append(f"Symbolic failed: {type(exc).__name__}: {exc}")

        if symbolic_text is None and not numeric_ok:
            self.result.show_result(error="\n".join(lines))
            return
        self.result.show_result(latex=symbolic_latex, text=symbolic_text,
                                numeric="\n".join(lines))


# ====================================================================== 泰勒展开
class TaylorTab(_BaseTab):
    """在一点附近把函数展开成多项式。"""

    def __init__(self) -> None:
        super().__init__()
        self.expr = _expr_field("e.g.  sin(x)   or  exp(x)   or  1/(1-x)")
        self.var = _var_field("x")
        self.assume = _assumption_combo()

        self.center = QLineEdit("0")
        self.center.setPlaceholderText("e.g. 0, pi/2, 1")
        self.center.setMinimumWidth(150)

        self.order = QSpinBox()
        self.order.setRange(0, 20)
        self.order.setValue(5)
        self.order.setFixedWidth(70)

        self.check = QLineEdit("")
        self.check.setPlaceholderText(
            "optional — compare f and the expansion at this x")
        self.check.setMinimumWidth(250)

        self.form.addRow("Function", self.expr)

        var_row = QHBoxLayout()
        var_row.setSpacing(8)
        var_row.addWidget(self.var)
        var_row.addWidget(QLabel("Assumption"))
        var_row.addWidget(self.assume)
        var_row.addStretch(1)
        self.form.addRow("Variable", _wrap(var_row))

        self.tol_box = QComboBox()
        for label, value in (("1e-2", 1e-2), ("1e-3", 1e-3),
                             ("1e-4", 1e-4), ("1e-6", 1e-6)):
            self.tol_box.addItem(label, value)
        self.tol_box.setCurrentIndex(1)
        self.tol_box.setFixedWidth(78)

        pt_row = QHBoxLayout()
        pt_row.setSpacing(8)
        pt_row.addWidget(self.center)
        pt_row.addWidget(QLabel("Order"))
        pt_row.addWidget(self.order)
        pt_row.addWidget(QLabel("Tolerance"))
        pt_row.addWidget(self.tol_box)
        pt_row.addStretch(1)
        self.form.addRow("Expand at", _wrap(pt_row))

        chk_row = QHBoxLayout()
        chk_row.setSpacing(8)
        chk_row.addWidget(self.check)
        chk_row.addStretch(1)
        self.form.addRow("Check at (optional)", _wrap(chk_row))

        # ---- 图：f 和 P_n 叠在一起，下面配误差曲线 ----
        self.figure = None
        self.canvas = None
        if HAS_PLOT:
            self.figure = Figure(figsize=(7.4, 3.6), dpi=100)
            self.canvas = FigureCanvas(self.figure)
            self.canvas.setMinimumHeight(300)
            self.toolbar = NavigationToolbar(self.canvas, self)
            box = QFrame()
            box.setObjectName("formBox")
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(6, 6, 6, 4)
            box_layout.setSpacing(2)
            box_layout.addWidget(self.canvas, 1)
            box_layout.addWidget(self.toolbar)
            # form(0) -> buttons(1) -> 图(2) -> 结果(3)
            self.layout().insertWidget(2, box)

        # 逐项列表有 order+1 行，默认高度只够看两行，往上调一点
        # （其余标签页的默认高度保持不变）
        try:
            self.result._numeric.setMinimumHeight(260)
        except Exception:                        # noqa: BLE001
            pass

    def clear(self) -> None:
        super().clear()
        if self.figure is not None:
            self.figure.clear()
            self.canvas.draw_idle()

    def _draw(self, expr, var, poly, center_value, tol, order) -> list[str]:
        """画 f 与 P_n，并在下面画误差曲线。返回要追加到结果里的说明。"""
        if self.figure is None:
            return []

        span = max(1.0, abs(center_value)) * 1.5
        xs = np.linspace(center_value - span, center_value + span, 601)

        compiled = cn.CompiledExpression(expr, variables=[var])
        fn = (compiled.function(var) if compiled.variables
              else (lambda _x: compiled.evaluate()))
        try:
            f_vals = np.asarray(fn(xs), dtype=complex)
        except Exception:                        # noqa: BLE001
            f_vals = np.array([complex(fn(x)) for x in xs], dtype=complex)
        # 常数函数返回标量，要铺成和 xs 一样的形状，否则画图报
        # "x and y must have same first dimension"
        if f_vals.shape != xs.shape:
            f_vals = np.broadcast_to(f_vals, xs.shape)

        p_vals = np.asarray(cs.lambdify_of(poly)(xs), dtype=complex)
        if p_vals.shape != xs.shape:
            p_vals = np.broadcast_to(p_vals, xs.shape)
        err = np.abs(f_vals - p_vals)

        fig = self.figure
        fig.clear()
        ax1 = fig.add_subplot(2, 1, 1)
        ax2 = fig.add_subplot(2, 1, 2)

        ax1.plot(xs, f_vals.real, linewidth=2, label=f"f({var})")
        ax1.plot(xs, p_vals.real, "--", linewidth=2, label=f"P_{order}({var})")
        ax1.axvline(center_value, color="gray", linestyle=":", linewidth=1)
        ax1.set_ylabel("value")
        ax1.set_title(
            f"Taylor expansion about {var} = {center_value:g}", fontsize=9)
        ax1.legend(loc="best", fontsize=8)
        ax1.grid(alpha=0.3)

        # 误差为 0 的点在对数轴上画不出来，换成 NaN 跳过
        ax2.semilogy(xs, np.where(err > 0, err, np.nan),
                     color="crimson", linewidth=1.4)
        ax2.axhline(tol, color="gray", linestyle="--", linewidth=1,
                    label=f"tolerance {tol:g}")
        ax2.set_xlabel(var)
        ax2.set_ylabel("|f - P|")
        ax2.legend(loc="best", fontsize=8)
        ax2.grid(alpha=0.3)

        fig.tight_layout()
        self.canvas.draw_idle()

        notes: list[str] = []
        try:
            radius, lo, hi, spread, step = cs.expansion_holds(
                expr, var, poly, center_value, tolerance=tol)
            if radius > 0:
                notes.append(
                    f"sampled: error < {tol:g} for "
                    f"|{var} - {center_value:g}| < {radius:.3g}  "
                    f"({var} in [{lo:.3g}, {hi:.3g}])")
            else:
                # 半径 0 有两种可能，不能一律说成「紧挨着就超差」
                notes.append(
                    f"sampled: no sampled point within {step:.2g} of the "
                    f"expansion point stays under {tol:g} — the valid window "
                    "is either narrower than the sampling step or the error "
                    "really does exceed the tolerance right away")
            # 各网格给出的半径差得远，说明函数变化比采样快，只能当参考
            if spread > max(0.05, 0.3 * max(radius, 1e-12)):
                notes.append(
                    "\u26a0 the estimated range varies between sample grids — "
                    "this function changes faster than the sampling, so the "
                    "interval above is indicative only (a guaranteed bound "
                    "would need a remainder estimate)")
        except Exception as exc:                 # noqa: BLE001
            notes.append(f"Range estimate failed: {type(exc).__name__}: {exc}")
        return notes

    def compute(self) -> None:
        expr = self.expr.text().strip()
        if not expr:
            self.result.show_result(error="Please enter a function first")
            return

        var = self.var.text().strip() or "x"
        center_text = self.center.text().strip() or "0"
        order = self.order.value()
        assumptions = _assumptions_for(self.assume, [var])

        lines: list[str] = []
        symbolic_text = symbolic_latex = None
        poly = None

        try:
            poly, notes = cs.taylor_poly(
                expr, var, point=center_text, order=order,
                assumptions=assumptions)
            symbolic_text = str(poly)
            symbolic_latex = cs.latex_of(poly)
            lines.append(f"P_{order}({var}) = {symbolic_text}")
            for note in notes:
                lines.append(f"Note: {note}")

            # 累进式：每一阶都在前一阶基础上多一项，看得出收敛过程。
            # 最后再给带 O() 的完整写法，明确截断在哪里。
            try:
                coeffs = cs.coefficient_list(poly, order, center_text)
                raw_center = center_text.strip()
                basis = (var if raw_center in ("", "0", "0.0", "+0")
                         else f"({var} - {raw_center})")

                lines.append("")
                lines.append("progressive (one more term each order):")
                for k in range(order + 1):
                    partial = cs.format_polynomial(coeffs[:k + 1], basis)
                    lines.append(f"  P_{k}({var}) = {partial}")

                lines.append("")
                lines.append(
                    f"f({var}) = {cs.format_polynomial(coeffs, basis)}"
                    f" + O({basis}**{order + 1})")
            except Exception as exc:             # noqa: BLE001
                lines.append(
                    f"Term breakdown failed: {type(exc).__name__}: {exc}")
        except Exception as exc:                 # noqa: BLE001
            lines.append(f"Symbolic failed: {type(exc).__name__}: {exc}")

        # 可选的数值对比：原函数和展开式在同一点的差
        check_text = self.check.text().strip()
        if check_text and poly is not None:
            lines.extend(self._compare(expr, var, poly, check_text))

        # 画图 + 有效区间
        if self.figure is not None:
            if poly is None:
                self.figure.clear()
                self.canvas.draw_idle()
            else:
                try:
                    center_value = cs.constant_float(center_text)
                    tol = float(self.tol_box.currentData())
                    lines.extend([""])
                    lines.extend(self._draw(expr, var, poly, center_value,
                                            tol, order))
                except Exception as exc:         # noqa: BLE001
                    lines.append(f"Plot failed: {type(exc).__name__}: {exc}")

        # 按内容行数调数值区高度：累进式是 order+3 行上下，固定高度会把
        # 最后一行（带 O() 的完整式子）挤到内部滚动区外看不见。
        # 上限 420px，再多就交给内部滚动条。
        try:
            self.result._numeric.setMinimumHeight(
                min(420, max(220, 21 * len(lines))))
        except Exception:                        # noqa: BLE001
            pass

        if symbolic_text is None:
            self.result.show_result(error="\n".join(lines))
            return
        self.result.show_result(latex=symbolic_latex, text=symbolic_text,
                                numeric="\n".join(lines))

    @staticmethod
    def _compare(expr: str, var: str, poly, point_text: str) -> list[str]:
        """把 f 和展开式在给定点各算一遍。"""
        try:
            x0 = float(point_text)
        except ValueError:
            return [f"Check failed: {point_text!r} is not a number"]

        try:
            compiled = cn.CompiledExpression(expr, variables=[var])
            fn = (compiled.function(var) if compiled.variables
                  else (lambda _x: compiled.evaluate()))
            actual = complex(fn(x0))
        except Exception as exc:                 # noqa: BLE001
            return [f"Check failed (evaluating the function): "
                    f"{type(exc).__name__}: {exc}"]

        try:
            syms = poly.free_symbols
            if len(syms) > 1:
                return [f"Check skipped: the expansion still contains {syms}"]
            value = poly if not syms else poly.subs(next(iter(syms)), x0)
            approx = complex(value)
        except Exception as exc:                 # noqa: BLE001
            return [f"Check failed (evaluating the expansion): "
                    f"{type(exc).__name__}: {exc}"]

        def fmt(z: complex) -> str:
            if abs(z.imag) <= 1e-12 * max(1.0, abs(z)):
                return f"{z.real:.12g}"
            return f"{z.real:.12g} {z.imag:+.6g}i"

        return [
            "",
            f"f({x0:g})        = {fmt(actual)}",
            f"P({x0:g})        = {fmt(approx)}",
            f"|difference|   = {abs(actual - approx):.3e}",
        ]


# ====================================================================== 页面
def _wrap(layout: QHBoxLayout) -> QWidget:
    """Wrap a QHBoxLayout in a widget so QFormLayout.addRow can take it."""
    holder = QWidget()
    holder.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    return holder


class CalculusPage(QWidget):
    page_title = "Calculus"

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        title = QLabel("Calculus")
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        subtitle = QLabel("Numeric and symbolic solutions together, with LaTeX and numeric output")
        subtitle.setObjectName("pageSubtitle")
        outer.addWidget(subtitle)

        tabs = QTabWidget()
        tabs.setObjectName("mainTabs")
        self.integral_tab = IntegralTab()
        self.derivative_tab = DerivativeTab()
        self.partial_tab = PartialTab()
        self.taylor_tab = TaylorTab()
        tabs.addTab(self.integral_tab, "∫  Integral")
        tabs.addTab(self.derivative_tab, "d/dx  Derivative")
        tabs.addTab(self.partial_tab, "∂  Partial")
        tabs.addTab(self.taylor_tab, "≈  Taylor")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        # 底部留一点空：滚到底时最后一行的下沿不会被窗口边缘裁掉
        holder_layout.setContentsMargins(0, 0, 0, 14)
        holder_layout.addWidget(tabs)
        scroll.setWidget(holder)

        outer.addWidget(scroll, 1)
        self._tabs = tabs


def _noop() -> None:
    return None
