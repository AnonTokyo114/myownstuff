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
                png = lr.render_latex(latex)
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
        self.point = QLineEdit("1")
        self.point.setPlaceholderText("value of x to evaluate at")
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
        self.form.addRow("Evaluate at", _wrap(pt_row))

    def compute(self) -> None:
        expr = self.expr.text().strip()
        if not expr:
            self.result.show_result(error="Please enter a function first")
            return
        var = self.var.text().strip() or "x"
        try:
            x0 = float(self.point.text().strip())
        except ValueError:
            self.result.show_result(error="The evaluation point must be a number")
            return

        assumptions = _assumptions_for(self.assume, [var])
        try:
            order = self.order.value()
            # 直接对原式求 order 阶导，不把一阶结果重新解析一遍
            txt, latex = cs.differentiate(expr, var, order=order,
                                          assumptions=assumptions)
            value, err = cn.derivative(expr, x0, order=order, x_name=var)

            lines = [f"{'second' if order == 2 else 'first'} derivative at {var} = {x0}: {value}",
                     f"Error estimate: ±{err:.3e}",
                     f"Derivative: {txt}"]
            self.result.show_result(latex=latex, text=txt, numeric="\n".join(lines))
        except Exception as exc:
            self.result.show_result(error=f"{type(exc).__name__}: {exc}")


# ====================================================================== 偏导
class PartialTab(_BaseTab):
    def __init__(self) -> None:
        super().__init__()
        self.expr = _expr_field("e.g.  x^2*y + sin(z)")
        self.vars = QLineEdit("x, y")
        self.vars.setPlaceholderText("comma-separated variable names")
        self.assume = _assumption_combo()
        self.point = QLineEdit("x=1, y=2")
        self.point.setPlaceholderText("x=1, y=2")
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
        self.form.addRow("Evaluate at", _wrap(p_row))

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

        point: dict[str, float] = {}
        for piece in self.point.text().split(","):
            piece = piece.strip()
            if not piece:
                continue
            if "=" not in piece:
                self.result.show_result(error=f"Evaluation point \"{piece}\" should be written as name=value")
                return
            name, _, value = piece.partition("=")
            try:
                point[name.strip()] = float(value.strip())
            except ValueError:
                self.result.show_result(error=f"\"{value.strip()}\" is not a number")
                return

        try:
            assume = _assumptions_for(self.assume, variables)
            txt, latex = cs.partial(expr, variables, var,
                                    order=self.order.value(), assumptions=assume)
            value, err = cn.partial_derivative(expr, variables, point, var,
                                               order=self.order.value())
            lines = [f"∂/∂{var} at {point}: {value}",
                     f"Error estimate: ±{err:.3e}",
                     f"Partial derivative: {txt}"]
            self.result.show_result(latex=latex, text=txt, numeric="\n".join(lines))
        except Exception as exc:
            self.result.show_result(error=f"{type(exc).__name__}: {exc}")


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
        tabs.addTab(self.integral_tab, "∫  Integral")
        tabs.addTab(self.derivative_tab, "d/dx  Derivative")
        tabs.addTab(self.partial_tab, "∂  Partial")

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addWidget(tabs)
        scroll.setWidget(holder)

        outer.addWidget(scroll, 1)
        self._tabs = tabs


def _noop() -> None:
    return None
