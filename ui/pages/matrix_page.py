"""Matrix Calculator：特征值 / 特征函数 / 矩阵运算 / 参数扫描。

计算全部在 core/eigencalc.py（无 GUI 依赖，已对解析解验证）。
本页只负责交互：四个标签页，对应原 eigencalc_gui.py 的四个 tab。

与原 tkinter 版的差异：
  * 异步计算改用 QThreadPool（原来用 threading + Tk 的 after 轮询）
  * MatrixPanel 基于 QTableWidget，多单元格粘贴手写实现
  * matplotlib 用 QtAgg 画布
"""

from __future__ import annotations

import csv
import re

import numpy as np
import sympy as sp
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtCore import Signal
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core import eigencalc as ec
from ui.workers import run_async

# ======================================================================
# 结果框
# ======================================================================
class ResultBox(QWidget):
    def __init__(self, height: int = 200) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        bar = QHBoxLayout()
        bar.addStretch(1)
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setObjectName("ghost")
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("ghost")
        bar.addWidget(self.copy_btn)
        bar.addWidget(self.clear_btn)
        layout.addLayout(bar)

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setObjectName("numericBox")
        self.text.setMinimumHeight(height)
        layout.addWidget(self.text, 1)

        self.copy_btn.clicked.connect(self._copy)
        self.clear_btn.clicked.connect(lambda: self.text.setPlainText(""))

    def set(self, txt: str) -> None:
        self.text.setPlainText(txt)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text.toPlainText())


# ======================================================================
# 矩阵表格（支持多单元格粘贴）
# ======================================================================
class MatrixTable(QTableWidget):
    """可编辑的矩阵网格；粘贴整块文本时会自动扩展尺寸并填充。"""

    max_size = 8

    #: 表格自行调整尺寸后通知宿主（宿主据此同步 rows/cols 控件）
    size_changed = Signal(int, int)
    #: 需要明确告诉用户的事（例如粘贴块超出上限被截断）
    notice = Signal(str)

    def __init__(self, rows: int, cols: int) -> None:
        super().__init__(rows, cols)
        self.setVerticalHeaderLabels([str(i + 1) for i in range(rows)])
        self.setHorizontalHeaderLabels([chr(65 + j) for j in range(cols)])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setDefaultSectionSize(26)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                             | QAbstractItemView.EditTrigger.SelectedClicked)
        for i in range(rows):
            for j in range(cols):
                self.setItem(i, j, QTableWidgetItem(""))
        self.fit_height()

    # ------------------------------------------------------------------
    def set_size(self, rows: int, cols: int) -> None:
        """调整尺寸，已填内容尽量保留。"""
        rows = max(1, min(rows, self.max_size))
        cols = max(1, min(cols, self.max_size))
        old = self.values()
        self.setRowCount(rows)
        self.setColumnCount(cols)
        self.setVerticalHeaderLabels([str(i + 1) for i in range(rows)])
        self.setHorizontalHeaderLabels([chr(65 + j) for j in range(cols)])
        for i in range(rows):
            for j in range(cols):
                value = ""
                if i < len(old) and j < len(old[i]):
                    value = old[i][j]
                self.setItem(i, j, QTableWidgetItem(value))
        self.fit_height()

    def fit_height(self) -> None:
        """表格高度贴合行数，避免小矩阵下面拖一大片空白。

        不要用 horizontalHeader().height() —— 首次布局前它可能是 0，
        会让最后一行被裁掉。这里按 26px 行高 + 表头余量估算。
        """
        self.setFixedHeight(26 * self.rowCount() + 36)

    def values(self) -> list[list[str]]:
        out = []
        for i in range(self.rowCount()):
            row = []
            for j in range(self.columnCount()):
                item = self.item(i, j)
                row.append((item.text() if item else "").strip())
            out.append(row)
        return out

    def set_values(self, grid: list[list[str]]) -> None:
        for i, row in enumerate(grid):
            for j, value in enumerate(row):
                if i < self.rowCount() and j < self.columnCount():
                    self.setItem(i, j, QTableWidgetItem(str(value)))

    # ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:         # noqa: N802
        if event.matches(QKeySequence.StandardKey.Paste):
            text = QGuiApplication.clipboard().text()
            if re.search(r"[\t\n;]", text):
                self._paste_grid(text)
                return
        super().keyPressEvent(event)

    def _paste_grid(self, text: str) -> None:
        """把 '1 2; 3 4' 或 Excel 的多行多列文本铺到网格上。

        粘贴起点可能不在左上角，所以扩容必须按「偏移 + 块尺寸」算；
        超出 max_size 的部分不能静默丢弃，要通过 notice 告知用户。
        """
        grid = []
        for line in re.split(r"[\n;]+", text):
            line = line.strip("\r")
            if not line.strip():
                continue
            parts = (line.split("\t") if "\t" in line
                     else re.split(r"[,\s]+", line.strip()))
            grid.append([p.strip() for p in parts])
        if not grid:
            return

        start_r = max(0, self.currentRow())
        start_c = max(0, self.currentColumn())

        need_r = start_r + len(grid)
        need_c = start_c + max(len(row) for row in grid)
        rows = min(max(self.rowCount(), need_r), self.max_size)
        cols = min(max(self.columnCount(), need_c), self.max_size)
        if (rows, cols) != (self.rowCount(), self.columnCount()):
            self.set_size(rows, cols)
            self.size_changed.emit(rows, cols)

        written = 0
        dropped = 0
        for di, row in enumerate(grid):
            for dj, value in enumerate(row):
                i, j = start_r + di, start_c + dj
                if i < self.rowCount() and j < self.columnCount():
                    self.setItem(i, j, QTableWidgetItem(value))
                    written += 1
                else:
                    dropped += 1

        if dropped:
            self.notice.emit(
                f"Pasted {written} of {written + dropped} values — the block "
                f"does not fit in a {self.max_size}×{self.max_size} grid.")


# ======================================================================
# 矩阵面板（尺寸控制 + 表格）
# ======================================================================
class MatrixPanel(QFrame):
    """mode: "matrix" / "square" / "vector"。"""

    def __init__(self, title: str, *, mode: str = "matrix", rows: int = 2,
                 cols: int = 2, max_size: int = 8) -> None:
        super().__init__()
        self.setObjectName("formBox")
        self.title = title
        self.mode = mode
        self.max_size = max_size

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("fieldLabel")
        head.addWidget(label)

        head.addWidget(QLabel("size:" if mode == "square" else "rows:"))
        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(1, max_size)
        self.rows_spin.setValue(rows)
        self.rows_spin.setFixedWidth(72)
        head.addWidget(self.rows_spin)

        if mode == "matrix":
            head.addWidget(QLabel("cols:"))
            self.cols_spin = QSpinBox()
            self.cols_spin.setRange(1, max_size)
            self.cols_spin.setValue(cols)
            self.cols_spin.setFixedWidth(72)
            head.addWidget(self.cols_spin)
        else:
            self.cols_spin = None

        head.addStretch(1)
        layout.addLayout(head)

        self.table = MatrixTable(rows, 1 if mode == "vector" else cols)
        self.table.max_size = max_size
        layout.addWidget(self.table)

        self.rows_spin.valueChanged.connect(self._rebuild)
        if self.cols_spin is not None:
            self.cols_spin.valueChanged.connect(self._rebuild)

        # 表格可能因粘贴自行扩容；同步控件并保持方阵/向量约束
        self.table.size_changed.connect(self._on_table_resized)
        self.table.notice.connect(self._show_notice)

    def _on_table_resized(self, rows: int, cols: int) -> None:
        """粘贴导致表格扩容后，把 size 控件和方阵/向量约束拉回一致。"""
        if self.mode == "vector":
            cols = 1
        elif self.mode == "square":
            rows = cols = max(rows, cols)

        # 约束修正后可能又需要改表尺寸；值相同则不会再触发，不会递归
        if (rows, cols) != (self.table.rowCount(), self.table.columnCount()):
            self.table.set_size(rows, cols)

        for spin, value in ((self.rows_spin, rows), (self.cols_spin, cols)):
            if spin is None:
                continue
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _show_notice(self, text: str) -> None:
        QMessageBox.warning(self, "Paste", text)

    def _rebuild(self) -> None:
        r = self.rows_spin.value()
        if self.mode == "vector":
            c = 1
        elif self.mode == "square":
            c = r
            self.rows_spin.setValue(r)
        else:
            c = self.cols_spin.value()
        self.table.set_size(r, c)

    def parse(self) -> tuple[sp.Matrix, int]:
        """返回 (sympy Matrix, 空单元格数量)。空单元格按 0 处理。"""
        grid = self.table.values()
        blanks = 0
        rows = []
        for i, row in enumerate(grid):
            cells = []
            for j, text in enumerate(row):
                if not text:
                    blanks += 1
                    text = "0"
                try:
                    cells.append(ec.parse_math(text))
                except Exception as exc:
                    raise ValueError(
                        f"{self.title}({i + 1},{j + 1}) = {text!r}: {exc}") from exc
            rows.append(cells)
        return sp.Matrix(rows), blanks


# ======================================================================
# 符号值输入
# ======================================================================
class SymbolInputs(QFrame):
    """矩阵里出现符号时，动态生成 'u = ___' 输入框。"""

    def __init__(self, note: str) -> None:
        super().__init__()
        self.setObjectName("formBox")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        title = QLabel(note)
        title.setObjectName("fieldLabel")
        title.setWordWrap(True)
        layout.addWidget(title)

        self.row = QHBoxLayout()
        self.row.setSpacing(10)
        self.hint = QLabel("(none)")
        self.hint.setObjectName("pageSubtitle")
        self.row.addWidget(self.hint)
        self.row.addStretch(1)
        layout.addLayout(self.row)

        self._edits: dict[str, QLineEdit] = {}
        self._symbols: dict[str, sp.Symbol] = {}

    def refresh(self, symbols) -> None:
        by_name = {s.name: s for s in symbols}
        if by_name == self._symbols:
            return
        # 清掉不再需要的
        for name in list(self._edits):
            if name not in by_name:
                edit = self._edits.pop(name)
                edit.parentWidget().deleteLater() if edit.parentWidget() else None
        # 重建（简单起见整体重来，避免布局残留）
        while self.row.count():
            item = self.row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._edits.clear()
        self._symbols = by_name

        if not by_name:
            self.hint = QLabel("(none)")
            self.hint.setObjectName("pageSubtitle")
            self.row.addWidget(self.hint)
            self.row.addStretch(1)
            return

        for name in sorted(by_name):
            lab = QLabel(f"{name} =")
            lab.setObjectName("pageSubtitle")
            self.row.addWidget(lab)
            edit = QLineEdit()
            edit.setFixedWidth(96)
            edit.setPlaceholderText("")
            self.row.addWidget(edit)
            self._edits[name] = edit
        self.row.addStretch(1)

    def values(self) -> dict[str, str]:
        return {name: edit.text() for name, edit in self._edits.items()}

    def substitutions(self, symbols) -> dict:
        return ec.collect_substitutions(self.values(), symbols)


# ======================================================================
# Tab 1：矩阵特征值
# ======================================================================
class MatrixEigenTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        tip = QLabel("Cells accept  2*pi, sqrt(3), x, y, sin(x), 2x, x^2 …   "
                     "(paste a block from Excel into any cell)")
        tip.setObjectName("pageSubtitle")
        layout.addWidget(tip)

        body = QHBoxLayout()
        body.setSpacing(12)

        left = QVBoxLayout()
        left.setSpacing(6)
        self.panel = MatrixPanel("Matrix A", mode="square", rows=2, max_size=8)
        left.addWidget(self.panel)

        btn_row = QHBoxLayout()
        self.num_btn = QPushButton("Compute numeric")
        self.num_btn.setObjectName("primary")
        self.sym_btn = QPushButton("Compute symbolic")
        self.sym_btn.setObjectName("ghost")
        btn_row.addWidget(self.num_btn)
        btn_row.addWidget(self.sym_btn)
        btn_row.addStretch(1)
        left.addLayout(btn_row)
        left.addStretch(1)
        body.addLayout(left, 0)

        right = QVBoxLayout()
        right.setSpacing(6)
        self.symbols = SymbolInputs(
            "Symbol values — numeric: all required; symbolic: blank keeps the symbol")
        right.addWidget(self.symbols)
        self.result = ResultBox(260)
        right.addWidget(self.result, 1)
        body.addLayout(right, 1)

        layout.addLayout(body, 1)

        self.num_btn.clicked.connect(self.compute_numeric)
        self.sym_btn.clicked.connect(self.compute_symbolic)
        self.panel.table.itemChanged.connect(lambda *_: self._refresh_symbols())

    def _refresh_symbols(self) -> None:
        try:
            M, _blanks = self.panel.parse()
            self.symbols.refresh(M.free_symbols)
        except ValueError:
            pass

    def _parse_with_subs(self):
        M, blanks = self.panel.parse()
        self.symbols.refresh(M.free_symbols)
        syms = M.free_symbols
        subs = self.symbols.substitutions(syms)
        return (M.subs(subs) if subs else M), syms, subs, blanks

    def compute_numeric(self) -> None:
        try:
            M, syms, subs, blanks = self._parse_with_subs()
        except ValueError as exc:
            QMessageBox.warning(self, "Input error", str(exc))
            return
        missing = sorted(s.name for s in syms if s not in subs)
        if missing:
            self.result.set("Please provide numeric values for: " + ", ".join(missing)
                            + "\n(fill the fields above, then click Compute numeric again)")
            return
        if M.free_symbols:
            self.result.set("After substitution, symbols remain: "
                            + ", ".join(sorted(map(str, M.free_symbols))))
            return
        try:
            A = ec.to_np_complex(M)
            w, V, herm = ec.eigen_numeric(A)
        except Exception as exc:
            QMessageBox.critical(self, "Eigen error", f"{type(exc).__name__}: {exc}")
            return

        n = len(w)
        lines = [f"Eigenvalues ({n})"
                 + ("  [Hermitian: real eigenvalues, orthonormal eigenvectors]"
                    if herm else "") + ":"]
        for i, val in enumerate(w):
            lines.append(f"  lambda_{i + 1} = {ec.fmt_complex(val)}")
        lines += ["", "Eigenvectors (columns;  A v = lambda v):"]
        for i in range(n):
            vec = ", ".join(ec.fmt_complex(V[j, i]) for j in range(n))
            lines.append(f"  v_{i + 1} = [ {vec} ]")
        try:
            resid = max(float(np.linalg.norm(A @ V[:, i] - w[i] * V[:, i]))
                        for i in range(n))
            lines += ["", f"max ||A v_i - lambda_i v_i|| = {resid:.3e}"]
        except Exception:
            pass
        if not herm and n > 1:
            try:
                cond = np.linalg.cond(V)
                if cond > 1e8:
                    lines += ["", f"WARNING: eigenvector matrix is nearly singular "
                                  f"(cond = {cond:.2e}). A is probably defective, so "
                                  "the eigenvectors above are not linearly independent."]
            except Exception:
                pass
        if blanks:
            lines += ["", f"(note: {blanks} blank cell(s) treated as 0)"]
        self.result.set("\n".join(lines))

    def compute_symbolic(self) -> None:
        try:
            M, _syms, _subs, blanks = self._parse_with_subs()
        except ValueError as exc:
            QMessageBox.warning(self, "Input error", str(exc))
            return
        n = M.shape[0]
        if n > 3:
            ok = QMessageBox.question(
                self, "Symbolic may be slow",
                f"Symbolic eigenvalues of a {n}x{n} matrix can be very slow or "
                "produce unwieldy expressions.\n\nContinue anyway?")
            if ok != QMessageBox.StandardButton.Yes:
                return

        self.result.set("Computing symbolic eigenvalues…")

        def done(report: str) -> None:
            if blanks:
                report += f"\n\n(note: {blanks} blank cell(s) treated as 0)"
            self.result.set(report)

        def failed(msg: str) -> None:
            QMessageBox.critical(self, "Symbolic error", msg)
            self.result.set("")

        run_async(lambda: ec.symbolic_eigen_report(M), done, failed)


# ======================================================================
# Tab 2：矩阵运算
# ======================================================================
class OpsTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._last_op: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        panels = QHBoxLayout()
        panels.setSpacing(10)
        self.panel_a = MatrixPanel("A", mode="matrix", rows=2, cols=2)
        self.panel_b = MatrixPanel("B", mode="matrix", rows=2, cols=2)
        self.panel_v = MatrixPanel("v (column)", mode="vector", rows=2)
        panels.addWidget(self.panel_a)
        panels.addWidget(self.panel_b)
        panels.addWidget(self.panel_v)
        panels.addStretch(1)
        layout.addLayout(panels)

        btns = QHBoxLayout()
        btns.setSpacing(4)
        for key, (label, _needs) in ec.OPS.items():
            b = QPushButton(label)
            b.setObjectName("ghost")
            b.setFixedWidth(92)
            b.clicked.connect(lambda _c=False, k=key: self.do_op(k))
            btns.addWidget(b)
        btns.addStretch(1)
        layout.addLayout(btns)

        opts = QHBoxLayout()
        self.decimal_cb = QCheckBox("decimal result")
        self.simplify_cb = QCheckBox("simplify symbolic result")
        opts.addWidget(self.decimal_cb)
        opts.addWidget(self.simplify_cb)
        opts.addStretch(1)
        layout.addLayout(opts)

        self.symbols = SymbolInputs(
            "Symbol values — leave blank to keep a symbol in the result")
        layout.addWidget(self.symbols)

        self.result = ResultBox(220)
        layout.addWidget(self.result, 1)

        self._panels = {"A": self.panel_a, "B": self.panel_b, "v": self.panel_v}
        for p in self._panels.values():
            p.table.itemChanged.connect(lambda *_: self._refresh_symbols())

    def _refresh_symbols(self) -> None:
        syms = set()
        for p in self._panels.values():
            try:
                M, _b = p.parse()
                syms |= M.free_symbols
            except ValueError:
                pass
        self.symbols.refresh(syms)

    def do_op(self, which: str) -> None:
        self._last_op = which
        title, needed = ec.OPS[which]

        mats = {}
        blanks_by_name = {}
        try:
            for name in needed:
                M, blanks = self._panels[name].parse()
                mats[name] = M
                blanks_by_name[name] = blanks
            syms = set()
            for M in mats.values():
                syms |= M.free_symbols
            self.symbols.refresh(syms)
            subs = self.symbols.substitutions(syms)
        except ValueError as exc:
            QMessageBox.warning(self, "Input error", str(exc))
            return
        if subs:
            mats = {k: M.subs(subs) for k, M in mats.items()}

        notes = [f"note: {blanks_by_name[n]} blank cell(s) in {n} treated as 0"
                 for n in needed if blanks_by_name[n]]
        decimal = self.decimal_cb.isChecked()
        simplify = self.simplify_cb.isChecked()

        def work():
            result = ec.compute_operation(which, mats)
            if simplify:
                result = (result.applyfunc(sp.simplify)
                          if isinstance(result, sp.MatrixBase) else sp.simplify(result))
            if decimal:
                result = result.evalf(15)
            return result

        def done(result) -> None:
            lines = [f"=== {title} ===", ""]
            if isinstance(result, sp.MatrixBase):
                r, c = result.shape
                lines += [f"Result: {r} × {c}", "", ec.format_matrix(result)]
            else:
                lines.append("Result: " + ec.format_value(result))
            if notes:
                lines += [""] + [f"({n})" for n in notes]
            self.result.set("\n".join(lines))

        def failed(msg: str) -> None:
            self.result.set("Error: " + msg)

        self.result.set("Computing…")
        run_async(work, done, failed)


# ======================================================================
# Tab 3：一维特征函数
# ======================================================================
class OperatorTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        form = QFrame()
        form.setObjectName("formBox")
        grid = QGridLayout(form)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setSpacing(6)

        grid.addWidget(QLabel("Potential V(x):"), 0, 0)
        self.v_edit = QLineEdit("x^2")
        grid.addWidget(self.v_edit, 0, 1, 1, 3)
        hint = QLabel("e.g.  x^2 , 0 , abs(x) , sin(x)    (solves -u'' + V u = lambda u)")
        hint.setObjectName("pageSubtitle")
        grid.addWidget(hint, 0, 4)

        grid.addWidget(QLabel("a:"), 1, 0)
        self.a_edit = QLineEdit("-6")
        self.a_edit.setFixedWidth(90)
        grid.addWidget(self.a_edit, 1, 1)

        grid.addWidget(QLabel("b:"), 1, 2)
        self.b_edit = QLineEdit("6")
        self.b_edit.setFixedWidth(90)
        grid.addWidget(self.b_edit, 1, 3)

        grid.addWidget(QLabel("n (grid points):"), 2, 0)
        self.n_edit = QLineEdit("800")
        self.n_edit.setFixedWidth(90)
        grid.addWidget(self.n_edit, 2, 1)

        grid.addWidget(QLabel("k (lowest):"), 2, 2)
        self.k_edit = QLineEdit("5")
        self.k_edit.setFixedWidth(90)
        grid.addWidget(self.k_edit, 2, 3)

        grid.addWidget(QLabel("plot amplitude (blank = auto):"), 3, 0)
        self.amp_edit = QLineEdit("")
        self.amp_edit.setFixedWidth(90)
        grid.addWidget(self.amp_edit, 3, 1)

        self.run_btn = QPushButton("Compute eigenfunctions")
        self.run_btn.setObjectName("primary")
        grid.addWidget(self.run_btn, 4, 0, 1, 3)
        layout.addWidget(form)

        self.figure = Figure(figsize=(6, 3.6), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas, 1)

        self.result = ResultBox(110)
        layout.addWidget(self.result)

        self.run_btn.clicked.connect(self.compute)
        for edit in (self.v_edit, self.a_edit, self.b_edit, self.n_edit,
                     self.k_edit, self.amp_edit):
            edit.returnPressed.connect(self.compute)

    def compute(self) -> None:
        try:
            vexpr = ec.parse_math(self.v_edit.text())
            a = float(ec.parse_math(self.a_edit.text()))
            b = float(ec.parse_math(self.b_edit.text()))
            n = int(self.n_edit.text())
            k = int(self.k_edit.text())
            amp_text = self.amp_edit.text().strip()
            amp_user = float(ec.parse_math(amp_text)) if amp_text else None
        except Exception as exc:
            QMessageBox.warning(self, "Input error", str(exc))
            return

        if b <= a:
            QMessageBox.warning(self, "Input error", "b must be greater than a")
            return
        if n < 4:
            QMessageBox.warning(self, "Input error", "n must be at least 4")
            return
        if k < 1:
            QMessageBox.warning(self, "Input error", "k must be at least 1")
            return
        extra = vexpr.free_symbols - {ec.X}
        if extra:
            QMessageBox.warning(self, "Input error",
                                "Potential may only depend on x; found: "
                                + ", ".join(sorted(map(str, extra))))
            return
        try:
            f = sp.lambdify(ec.X, vexpr, "numpy")
        except Exception as exc:
            QMessageBox.warning(self, "Lambdify error", str(exc))
            return

        def vcall(xx):
            try:
                val = np.asarray(f(xx), dtype=float)
            except TypeError as exc:
                raise ValueError("V(x) must be real-valued on the interval") from exc
            return val * np.ones_like(xx)

        def work():
            with np.errstate(all="ignore"):
                x, lam, U = ec.operator_eigenfunctions(vcall, a, b, n, k)
                vx = vcall(x)
            return x, lam, U, vx

        def done(res) -> None:
            self._show(res, a, b, n, amp_user)

        def failed(msg: str) -> None:
            QMessageBox.critical(self, "Error", msg)
            self.result.set("")

        self.result.set("Solving…")
        run_async(work, done, failed)

    def _show(self, res, a, b, n, amp_user) -> None:
        x, lam, U, vx = res
        solver = ("SciPy tridiagonal" if ec.HAS_SCIPY
                  else "dense NumPy (install SciPy for speed)")
        lines = [f"Grid: {n} points on [{a:g}, {b:g}],  h = {(b - a) / (n - 1):.4e}"
                 f"   solver: {solver}"]
        for i, ev in enumerate(lam):
            lines.append(f"  lambda_{i + 1} = {ev:.10g}")
        self.result.set("\n".join(lines))

        spacing = (float(np.min(np.diff(lam))) if len(lam) > 1
                   else max(1.0, 0.5 * abs(lam[0])))
        amp = (amp_user if amp_user is not None
               else 0.8 * spacing / float(np.max(np.abs(U))))

        self.figure.clear()
        ax = self.figure.add_subplot(111)
        vp = np.where(np.isfinite(vx), vx, np.nan)
        ax.plot(x, vp, color="black", lw=1.5, label="V(x)")
        for i in range(U.shape[1]):
            line, = ax.plot(x, lam[i] + amp * U[:, i],
                            label=f"n={i + 1}, lam={lam[i]:.4g}")
            ax.axhline(lam[i], color=line.get_color(), lw=0.6, ls="--", alpha=0.5)
        finite = vp[np.isfinite(vp)]
        bottom = lam[0] - 0.5 * spacing
        if finite.size:
            bottom = min(bottom, float(finite.min()))
        ax.set_ylim(bottom, lam[-1] + spacing)
        ax.set_xlabel("x")
        ax.set_ylabel("energy")
        ax.set_title("Eigenfunctions in V(x)  (u_n scaled and shifted by lambda_n)")
        ax.legend(fontsize=8, loc="upper right")
        ax.grid(alpha=0.25)
        self.figure.tight_layout()
        self.canvas.draw_idle()


# ======================================================================
# Tab 4：特征值随参数变化
# ======================================================================
class SweepTab(QWidget):
    SHOW = ("Eigenvalues", "Eigenvector weights")

    def __init__(self) -> None:
        super().__init__()
        self._last = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(12)

        self.panel = MatrixPanel("Matrix A", mode="square", rows=2, max_size=8)
        top.addWidget(self.panel, 0)

        ctl = QFrame()
        ctl.setObjectName("formBox")
        grid = QGridLayout(ctl)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setSpacing(6)

        grid.addWidget(QLabel("parameter:"), 0, 0)
        self.param_box = QComboBox()
        self.param_box.setFixedWidth(90)
        grid.addWidget(self.param_box, 0, 1)

        grid.addWidget(QLabel("from:"), 1, 0)
        self.min_edit = QLineEdit("0")
        self.min_edit.setFixedWidth(90)
        grid.addWidget(self.min_edit, 1, 1)

        grid.addWidget(QLabel("to:"), 1, 2)
        self.max_edit = QLineEdit("1")
        self.max_edit.setFixedWidth(90)
        grid.addWidget(self.max_edit, 1, 3)

        grid.addWidget(QLabel("points:"), 2, 0)
        self.npts_edit = QLineEdit("300")
        self.npts_edit.setFixedWidth(90)
        grid.addWidget(self.npts_edit, 2, 1)

        grid.addWidget(QLabel("show:"), 3, 0)
        self.show_box = QComboBox()
        self.show_box.addItems(list(self.SHOW))
        grid.addWidget(self.show_box, 3, 1, 1, 2)

        grid.addWidget(QLabel("state k:"), 3, 3)
        self.state_edit = QLineEdit("1")
        self.state_edit.setFixedWidth(60)
        grid.addWidget(self.state_edit, 3, 4)

        btn_row = QHBoxLayout()
        self.run_btn = QPushButton("Compute sweep")
        self.run_btn.setObjectName("primary")
        self.csv_btn = QPushButton("Save CSV…")
        self.csv_btn.setObjectName("ghost")
        btn_row.addWidget(self.run_btn)
        btn_row.addWidget(self.csv_btn)
        btn_row.addStretch(1)
        grid.addLayout(btn_row, 4, 0, 1, 5)

        note = QLabel("'Eigenvector weights' plots |component|² of state k on each "
                      "basis vector (levels sorted ascending).")
        note.setObjectName("pageSubtitle")
        note.setWordWrap(True)
        grid.addWidget(note, 5, 0, 1, 5)

        top.addWidget(ctl, 1)
        layout.addLayout(top)

        self.symbols = SymbolInputs(
            "Values for the OTHER symbols (held fixed during the sweep)")
        layout.addWidget(self.symbols)

        self.figure = Figure(figsize=(6, 3.4), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas, 1)

        self.result = ResultBox(110)
        layout.addWidget(self.result)

        self.run_btn.clicked.connect(self.compute)
        self.csv_btn.clicked.connect(self.save_csv)
        self.show_box.currentIndexChanged.connect(lambda _i: self._plot())
        self.param_box.currentIndexChanged.connect(lambda _i: self._refresh_symbols())
        self.panel.table.itemChanged.connect(lambda *_: self._refresh_symbols())
        self.state_edit.returnPressed.connect(self._plot)

    # ------------------------------------------------------------------
    def _refresh_symbols(self) -> None:
        try:
            M, _b = self.panel.parse()
        except ValueError:
            return
        names = sorted(s.name for s in M.free_symbols)
        current = self.param_box.currentText()
        self.param_box.blockSignals(True)
        self.param_box.clear()
        self.param_box.addItems(names)
        if current in names:
            self.param_box.setCurrentText(current)
        self.param_box.blockSignals(False)

        others = {s for s in M.free_symbols
                  if s.name != self.param_box.currentText()}
        self.symbols.refresh(others)

    def compute(self) -> None:
        try:
            M, _blanks = self.panel.parse()
            self._refresh_symbols()
            name = self.param_box.currentText()
            if not name:
                raise ValueError("The matrix has no symbol to sweep. "
                                 "Put a symbol such as u in some cells.")
            param = next(s for s in M.free_symbols if s.name == name)
            t0 = float(ec.parse_math(self.min_edit.text()))
            t1 = float(ec.parse_math(self.max_edit.text()))
            npts = int(self.npts_edit.text())
            if not t1 > t0:
                raise ValueError("'to' must be greater than 'from'")
            if not 2 <= npts <= 20000:
                raise ValueError("points must be between 2 and 20000")
            others = set(M.free_symbols) - {param}
            fixed = self.symbols.substitutions(others)
            missing = sorted(s.name for s in others if s not in fixed)
            if missing:
                raise ValueError("Give values for: " + ", ".join(missing))
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Input error", str(exc))
            return

        ts = np.linspace(t0, t1, npts)
        M = M.applyfunc(lambda e: sp.nsimplify(e, rational=True)
                        if e.has(sp.Float) else e)

        def work():
            W, V, herm = ec.eigen_sweep(M, param, ts, fixed)
            return ts, W, V, herm

        def done(res) -> None:
            t, W, V, herm = res
            self._last = (name, t, W, V, herm)
            self.result.set(ec.sweep_summary(name, t, W, herm))
            self._plot()

        def failed(msg: str) -> None:
            QMessageBox.critical(self, "Error", msg)
            self.result.set("")

        self.result.set("Sweeping…")
        run_async(work, done, failed)

    # ------------------------------------------------------------------
    def _plot(self) -> None:
        if not self._last:
            return
        name, ts, W, V, herm = self._last
        n = W.shape[1]
        weights = self.show_box.currentText() == self.SHOW[1]
        self.figure.clear()

        has_imag = (not herm) and np.max(np.abs(W.imag)) > 1e-9 and not weights
        if has_imag:
            ax = self.figure.add_subplot(211)
            ax2 = self.figure.add_subplot(212, sharex=ax)
        else:
            ax, ax2 = self.figure.add_subplot(111), None

        if weights:
            try:
                k = int(self.state_edit.text())
                if not 1 <= k <= n:
                    raise ValueError
            except ValueError:
                k = 1
                self.state_edit.setText("1")
            for j in range(n):
                ax.plot(ts, np.abs(V[:, j, k - 1]) ** 2, label=f"basis {j + 1}")
            ax.set_ylim(-0.02, 1.05)
            ax.set_ylabel("|component|^2")
            ax.set_title(f"Composition of eigenstate {k} vs {name}")
            ax.legend(fontsize=8, ncol=2, loc="upper right")
        else:
            for i in range(n):
                ax.plot(ts, W[:, i].real, label=f"lambda_{i + 1}")
                if ax2 is not None:
                    ax2.plot(ts, W[:, i].imag)
            ax.set_ylabel("Re(lambda)" if has_imag else "eigenvalue")
            ax.set_title(f"Eigenvalues vs {name}")
            if n <= 10:
                ax.legend(fontsize=8, ncol=2, loc="best")
            if ax2 is not None:
                ax2.set_ylabel("Im(lambda)")

        (ax2 or ax).set_xlabel(name)
        ax.grid(alpha=0.25)
        if ax2 is not None:
            ax2.grid(alpha=0.25)
        self.figure.tight_layout()
        self.canvas.draw_idle()

    # ------------------------------------------------------------------
    def save_csv(self) -> None:
        if not self._last:
            QMessageBox.information(self, "Nothing to save", "Run a sweep first.")
            return
        name, ts, W, V, herm = self._last
        path, _ = QFileDialog.getSaveFileName(self, "Save sweep as CSV", "",
                                              "CSV (*.csv)")
        if not path:
            return
        n = W.shape[1]
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                head = [name] + [f"lambda_{i + 1}" for i in range(n)]
                if not herm:
                    head += [f"imag_lambda_{i + 1}" for i in range(n)]
                writer.writerow(head)
                for k, t in enumerate(ts):
                    row = [repr(float(t))] + [repr(float(W[k, i].real)) for i in range(n)]
                    if not herm:
                        row += [repr(float(W[k, i].imag)) for i in range(n)]
                    writer.writerow(row)
        except OSError as exc:
            QMessageBox.critical(self, "Save error", str(exc))
            return
        self.result.set(self.result.text.toPlainText() + f"\n\n(saved to {path})")


# ======================================================================
# 页面
# ======================================================================
class MatrixCalculatorPage(QWidget):
    page_title = "Matrix Calculator"

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("Matrix Calculator")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "Eigen-decomposition, matrix arithmetic, 1D eigenfunctions and "
            "parameter sweeps. Numeric and symbolic (SymPy).")
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(subtitle)

        tabs = QTabWidget()
        tabs.setObjectName("mainTabs")
        tabs.addTab(MatrixEigenTab(), "  Matrix eigenvalues  ")
        tabs.addTab(OpsTab(), "  Matrix operations  ")
        tabs.addTab(OperatorTab(), "  1D eigenfunctions  ")
        tabs.addTab(SweepTab(), "  Eigenvalues vs parameter  ")
        layout.addWidget(tabs, 1)
        self.tabs = tabs
