"""Plotter：读 CSV/TSV 并画图。

计算与绘制在 core/csvdata.py（无 GUI 依赖，已用 Agg 后端验证）。
本页只负责把控件值填进 PlotSpec。

左侧两个标签页（Data & Series / Plot Settings），右侧 matplotlib 画布
带完整导航工具栏（缩放、平移、保存图片）。
"""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("QtAgg")

from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg as FigureCanvas,
    NavigationToolbar2QT as NavigationToolbar,
)
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core import csvdata as cd


def _field(text: str, width: int = 10) -> QLineEdit:
    edit = QLineEdit()
    edit.setFixedWidth(width)
    edit.setPlaceholderText(text)
    return edit


def _label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("fieldLabel")
    return lab


# ======================================================================
# 一行 X/Y 对
# ======================================================================
class PairRow(QFrame):
    def __init__(self, columns: list[str], x_default: str, y_default: str,
                 on_remove) -> None:
        super().__init__()
        self.setObjectName("formBox")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        self.x_box = QComboBox()
        self.x_box.addItems(columns)
        if x_default in columns:
            self.x_box.setCurrentText(x_default)
        self.x_box.setFixedWidth(130)
        layout.addWidget(self.x_box)

        sep = QLabel("vs")
        sep.setObjectName("pageSubtitle")
        layout.addWidget(sep)

        self.y_box = QComboBox()
        self.y_box.addItems(columns)
        if y_default in columns:
            self.y_box.setCurrentText(y_default)
        self.y_box.setFixedWidth(130)
        layout.addWidget(self.y_box)

        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("legend label (optional)")
        layout.addWidget(self.label_edit, 1)

        remove = QPushButton("✕")
        remove.setObjectName("ghost")
        remove.setFixedWidth(32)
        remove.clicked.connect(lambda: on_remove(self))
        layout.addWidget(remove)

    def set_columns(self, columns: list[str]) -> None:
        for box in (self.x_box, self.y_box):
            current = box.currentText()
            box.blockSignals(True)
            box.clear()
            box.addItems(columns)
            if current in columns:
                box.setCurrentText(current)
            box.blockSignals(False)

    def values(self) -> tuple[str, str, str]:
        return self.x_box.currentText(), self.y_box.currentText(), \
            self.label_edit.text()


# ======================================================================
# 页面
# ======================================================================
class PlotterPage(QWidget):
    page_title = "Plotter"

    def __init__(self) -> None:
        super().__init__()
        self.df = None
        self.columns: list[str] = []
        self._pair_rows: list[PairRow] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        title = QLabel("Plotter")
        title.setObjectName("pageTitle")
        outer.addWidget(title)
        subtitle = QLabel(
            "Load a CSV / TSV file and plot its columns. Delimiter is detected "
            "automatically; .tsv is read as tab-separated.")
        subtitle.setObjectName("pageSubtitle")
        outer.addWidget(subtitle)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---------------------------------------------- 左：控制面板
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        self.notebook = QTabWidget()
        self.notebook.setObjectName("mainTabs")
        self.notebook.addTab(self._build_data_tab(), "  Data && Series  ")
        self.notebook.addTab(self._build_settings_tab(), "  Plot Settings  ")
        left_layout.addWidget(self.notebook, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.plot_btn = QPushButton("Plot")
        self.plot_btn.setObjectName("primary")
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("ghost")
        btn_row.addWidget(self.plot_btn, 1)
        btn_row.addWidget(self.clear_btn, 1)
        left_layout.addLayout(btn_row)

        left.setMinimumWidth(430)
        splitter.addWidget(left)

        # ---------------------------------------------- 右：画布
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)

        self.figure = Figure(figsize=(8, 6), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        right_layout.addWidget(self.canvas, 1)

        self.toolbar = NavigationToolbar(self.canvas, right)
        right_layout.addWidget(self.toolbar)

        splitter.addWidget(right)
        splitter.setSizes([460, 900])
        outer.addWidget(splitter, 1)

        # ---------------------------------------------- 信号
        self.plot_btn.clicked.connect(self.plot_data)
        self.clear_btn.clicked.connect(self.clear_plot)
        self.browse_btn.clicked.connect(self.browse_file)
        self.path_edit.returnPressed.connect(self.load_file)
        self.load_btn.clicked.connect(self.load_file)
        self.mode_one_x.toggled.connect(self._on_mode_change)
        self.add_pair_btn.clicked.connect(lambda: self.add_pair_row())
        self.sharex_cb.setEnabled(False)
        self.layout_single.toggled.connect(self._on_layout_change)

    # ------------------------------------------------------------------ UI
    def _build_data_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # ---- 文件 ----
        file_box = QFrame()
        file_box.setObjectName("formBox")
        file_layout = QVBoxLayout(file_box)
        file_layout.setContentsMargins(10, 8, 10, 8)
        file_layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("no file selected")
        row.addWidget(self.path_edit, 1)
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.setObjectName("ghost")
        self.load_btn = QPushButton("Load")
        self.load_btn.setObjectName("ghost")
        row.addWidget(self.browse_btn)
        row.addWidget(self.load_btn)
        file_layout.addLayout(row)

        self.info_label = QLabel("No file loaded")
        self.info_label.setObjectName("pageSubtitle")
        file_layout.addWidget(self.info_label)
        layout.addWidget(file_box)

        # ---- 模式 ----
        mode_box = QFrame()
        mode_box.setObjectName("formBox")
        mode_layout = QVBoxLayout(mode_box)
        mode_layout.setContentsMargins(10, 8, 10, 8)
        mode_layout.setSpacing(4)

        mode_layout.addWidget(_label("Mode"))
        self.mode_one_x = QCheckBox("One X  →  many Y")
        self.mode_one_x.setChecked(True)
        mode_layout.addWidget(self.mode_one_x)
        self.mode_pairs = QCheckBox("Multiple X/Y pairs")
        mode_layout.addWidget(self.mode_pairs)
        # 两个复选互斥（用 QCheckBox 是为了和整体样式一致）
        self.mode_pairs.toggled.connect(
            lambda on: self.mode_one_x.setChecked(not on) if on else None)
        layout.addWidget(mode_box)

        # ---- one_x 面板 ----
        self.onex_panel = QFrame()
        self.onex_panel.setObjectName("formBox")
        onex = QVBoxLayout(self.onex_panel)
        onex.setContentsMargins(10, 8, 10, 8)
        onex.setSpacing(6)

        x_row = QHBoxLayout()
        x_row.setSpacing(6)
        x_row.addWidget(_label("X column"))
        self.x_combo = QComboBox()
        x_row.addWidget(self.x_combo, 1)
        onex.addLayout(x_row)

        onex.addWidget(_label("Y columns (Ctrl / ⌘-click for multiple)"))
        self.y_list = QListWidget()
        self.y_list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection)
        self.y_list.setMinimumHeight(150)
        onex.addWidget(self.y_list, 1)
        layout.addWidget(self.onex_panel, 1)

        # ---- pairs 面板 ----
        self.pairs_panel = QFrame()
        self.pairs_panel.setObjectName("formBox")
        pairs = QVBoxLayout(self.pairs_panel)
        pairs.setContentsMargins(10, 8, 10, 8)
        pairs.setSpacing(6)

        pairs.addWidget(_label("X column   /   Y column   /   legend label"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        self.pairs_layout = QVBoxLayout(holder)
        self.pairs_layout.setContentsMargins(0, 0, 0, 0)
        self.pairs_layout.setSpacing(4)
        self.pairs_layout.addStretch(1)
        scroll.setWidget(holder)
        pairs.addWidget(scroll, 1)

        self.add_pair_btn = QPushButton("+ Add X/Y pair")
        self.add_pair_btn.setObjectName("ghost")
        pairs.addWidget(self.add_pair_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.pairs_panel.setVisible(False)
        layout.addWidget(self.pairs_panel, 1)

        return page

    def _build_settings_tab(self) -> QWidget:
        page = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # ---- 布局 ----
        lay = QFrame()
        lay.setObjectName("formBox")
        lay_box = QVBoxLayout(lay)
        lay_box.setContentsMargins(10, 8, 10, 8)
        lay_box.setSpacing(4)
        lay_box.addWidget(_label("Layout"))
        self.layout_single = QCheckBox("Everything in one plot")
        self.layout_single.setChecked(True)
        lay_box.addWidget(self.layout_single)
        self.layout_separate = QCheckBox("Separate subplots (stacked)")
        lay_box.addWidget(self.layout_separate)
        self.layout_separate.toggled.connect(
            lambda on: self.layout_single.setChecked(not on) if on else None)
        self.sharex_cb = QCheckBox("Subplots share X axis")
        self.sharex_cb.setChecked(True)
        lay_box.addWidget(self.sharex_cb)
        layout.addWidget(lay)

        # ---- 标题与标签 ----
        lab = QFrame()
        lab.setObjectName("formBox")
        lab_grid = QGridLayout(lab)
        lab_grid.setContentsMargins(10, 8, 10, 8)
        lab_grid.setSpacing(6)
        lab_grid.addWidget(_label("Title && labels"), 0, 0, 1, 2)
        self.title_edit = QLineEdit()
        self.xlabel_edit = QLineEdit()
        self.ylabel_edit = QLineEdit()
        for row, (text, edit) in enumerate(
                [("Title:", self.title_edit),
                 ("X label:", self.xlabel_edit),
                 ("Y label:", self.ylabel_edit)], start=1):
            lab_grid.addWidget(QLabel(text), row, 0)
            lab_grid.addWidget(edit, row, 1)
        hint = QLabel("(leave blank to use column names)")
        hint.setObjectName("pageSubtitle")
        lab_grid.addWidget(hint, 4, 0, 1, 2)
        layout.addWidget(lab)

        # ---- 范围与刻度 ----
        lim = QFrame()
        lim.setObjectName("formBox")
        lim_grid = QGridLayout(lim)
        lim_grid.setContentsMargins(10, 8, 10, 8)
        lim_grid.setSpacing(6)
        lim_grid.addWidget(_label("Axis limits && tick spacing (blank = auto)"),
                           0, 0, 1, 4)

        self.xmin_edit = _field("X min")
        self.xmax_edit = _field("X max")
        self.ymin_edit = _field("Y min")
        self.ymax_edit = _field("Y max")
        self.xtick_edit = _field("X tick")
        self.ytick_edit = _field("Y tick")

        lim_grid.addWidget(QLabel("X min:"), 1, 0)
        lim_grid.addWidget(self.xmin_edit, 1, 1)
        lim_grid.addWidget(QLabel("X max:"), 1, 2)
        lim_grid.addWidget(self.xmax_edit, 1, 3)
        lim_grid.addWidget(QLabel("Y min:"), 2, 0)
        lim_grid.addWidget(self.ymin_edit, 2, 1)
        lim_grid.addWidget(QLabel("Y max:"), 2, 2)
        lim_grid.addWidget(self.ymax_edit, 2, 3)
        lim_grid.addWidget(QLabel("X tick:"), 3, 0)
        lim_grid.addWidget(self.xtick_edit, 3, 1)
        lim_grid.addWidget(QLabel("Y tick:"), 3, 2)
        lim_grid.addWidget(self.ytick_edit, 3, 3)

        self.logx_cb = QCheckBox("Log X")
        self.logy_cb = QCheckBox("Log Y")
        lim_grid.addWidget(self.logx_cb, 4, 0, 1, 2)
        lim_grid.addWidget(self.logy_cb, 4, 2, 1, 2)
        layout.addWidget(lim)

        # ---- 外观 ----
        app = QFrame()
        app.setObjectName("formBox")
        app_grid = QGridLayout(app)
        app_grid.setContentsMargins(10, 8, 10, 8)
        app_grid.setSpacing(6)
        app_grid.addWidget(_label("Appearance"), 0, 0, 1, 4)

        self.plottype_box = QComboBox()
        self.plottype_box.addItems(cd.PLOT_TYPES)
        app_grid.addWidget(QLabel("Plot type:"), 1, 0)
        app_grid.addWidget(self.plottype_box, 1, 1)

        self.markersize_edit = QLineEdit("6")
        self.markersize_edit.setFixedWidth(64)
        app_grid.addWidget(QLabel("Marker size:"), 1, 2)
        app_grid.addWidget(self.markersize_edit, 1, 3)

        self.grid_cb = QCheckBox("Grid")
        self.grid_cb.setChecked(True)
        app_grid.addWidget(self.grid_cb, 2, 0, 1, 2)

        self.legend_cb = QCheckBox("Legend")
        self.legend_cb.setChecked(True)
        app_grid.addWidget(self.legend_cb, 2, 2, 1, 2)

        self.legend_loc_box = QComboBox()
        self.legend_loc_box.addItems(cd.LEGEND_LOCS)
        app_grid.addWidget(QLabel("Location:"), 3, 0)
        app_grid.addWidget(self.legend_loc_box, 3, 1)

        self.marker_box = QComboBox()
        self.marker_box.addItems([m or "(none)" for m in cd.MARKERS])
        app_grid.addWidget(QLabel("Marker:"), 3, 2)
        app_grid.addWidget(self.marker_box, 3, 3)

        self.linestyle_box = QComboBox()
        self.linestyle_box.addItems(cd.LINESTYLES)
        app_grid.addWidget(QLabel("Line:"), 4, 0)
        app_grid.addWidget(self.linestyle_box, 4, 1)

        size_row = QHBoxLayout()
        self.figw_edit = QLineEdit("9")
        self.figw_edit.setFixedWidth(56)
        self.figh_edit = QLineEdit("6")
        self.figh_edit.setFixedWidth(56)
        size_row.addWidget(self.figw_edit)
        size_row.addWidget(QLabel("×"))
        size_row.addWidget(self.figh_edit)
        size_row.addStretch(1)
        size_wrap = QWidget()
        size_wrap.setLayout(size_row)
        size_row.setContentsMargins(0, 0, 0, 0)
        app_grid.addWidget(QLabel("Figure size (in):"), 4, 2)
        app_grid.addWidget(size_wrap, 4, 3)
        layout.addWidget(app)

        layout.addStretch(1)
        scroll.setWidget(holder)
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return page

    # ------------------------------------------------------------------ 交互
    def _on_mode_change(self, _checked=None) -> None:
        one_x = self.mode_one_x.isChecked()
        self.mode_pairs.setChecked(not one_x)
        self.onex_panel.setVisible(one_x)
        self.pairs_panel.setVisible(not one_x)

    def _on_layout_change(self, _checked=None) -> None:
        single = self.layout_single.isChecked()
        self.layout_separate.setChecked(not single)
        self.sharex_cb.setEnabled(not single)

    # ------------------------------------------------------------------ 文件
    def browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a data file", "",
            "CSV / TSV files (*.csv *.tsv *.txt);;All files (*)")
        if path:
            self.path_edit.setText(path)
            self.load_file()

    def load_file(self) -> None:
        path = self.path_edit.text().strip()
        if not path:
            return
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Load error",
                                f"File not found:\n{path}")
            return
        try:
            self.df = cd.read_table(path)
        except ValueError as exc:
            QMessageBox.critical(self, "Load error", str(exc))
            return

        self.columns = cd.column_names(self.df)
        self.info_label.setText(cd.summary(self.df) + " loaded")
        self._populate_columns()

    def _populate_columns(self) -> None:
        cols = self.columns

        self.x_combo.blockSignals(True)
        self.x_combo.clear()
        self.x_combo.addItems(cols)
        self.x_combo.blockSignals(False)

        self.y_list.clear()
        for c in cols:
            QListWidgetItem(c, self.y_list)
        for c in cd.default_y_candidates(self.df):
            if c in cols:
                self.y_list.item(cols.index(c)).setSelected(True)

        for row in list(self._pair_rows):
            row.setParent(None)
            row.deleteLater()
        self._pair_rows.clear()

        if len(cols) >= 2:
            self.add_pair_row(cols[0], cols[1])
        if len(cols) >= 3:
            self.add_pair_row(cols[0], cols[2])

    # ------------------------------------------------------------------ 配对行
    def add_pair_row(self, x_default: str | None = None,
                     y_default: str | None = None) -> None:
        if not self.columns:
            QMessageBox.information(self, "No data", "Load a CSV file first.")
            return
        x_default = x_default or self.columns[0]
        y_default = y_default or (self.columns[1] if len(self.columns) > 1
                                  else self.columns[0])
        row = PairRow(self.columns, x_default, y_default, self.remove_pair_row)
        # 插在末尾的 stretch 之前
        self.pairs_layout.insertWidget(self.pairs_layout.count() - 1, row)
        self._pair_rows.append(row)

    def remove_pair_row(self, row: PairRow) -> None:
        if row in self._pair_rows:
            self._pair_rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    # ------------------------------------------------------------------ 绘图
    def _collect_series(self):
        if self.df is None:
            raise ValueError("Load a CSV file first.")
        if self.mode_one_x.isChecked():
            y_cols = [item.text() for item in self.y_list.selectedItems()]
            return cd.build_series(self.df, mode="one_x",
                                   x_col=self.x_combo.currentText(),
                                   y_cols=y_cols)
        pairs = [_row.values() for _row in self._pair_rows]
        return cd.build_series(self.df, mode="pairs", pairs=pairs)

    def _build_spec(self) -> cd.PlotSpec:
        def f(edit: QLineEdit):
            return cd.parse_float(edit.text())

        try:
            figw = f(self.figw_edit) or 9.0
            figh = f(self.figh_edit) or 6.0
            try:
                markersize = float(self.markersize_edit.text())
            except ValueError:
                markersize = 6.0
            marker_text = self.marker_box.currentText()
            return cd.PlotSpec(
                title=self.title_edit.text().strip(),
                xlabel=self.xlabel_edit.text().strip(),
                ylabel=self.ylabel_edit.text().strip(),
                xmin=f(self.xmin_edit), xmax=f(self.xmax_edit),
                ymin=f(self.ymin_edit), ymax=f(self.ymax_edit),
                xtick=f(self.xtick_edit), ytick=f(self.ytick_edit),
                logx=self.logx_cb.isChecked(),
                logy=self.logy_cb.isChecked(),
                grid=self.grid_cb.isChecked(),
                legend=self.legend_cb.isChecked(),
                legend_loc=self.legend_loc_box.currentText(),
                plot_type=self.plottype_box.currentText(),
                marker=None if marker_text in ("", "(none)") else marker_text,
                linestyle=self.linestyle_box.currentText(),
                markersize=markersize,
                figsize=(figw, figh),
                separate=self.layout_separate.isChecked(),
                sharex=self.sharex_cb.isChecked(),
            )
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    def plot_data(self) -> None:
        try:
            series = self._collect_series()
            spec = self._build_spec()
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot plot", str(exc))
            return

        try:
            cd.render(self.figure, series, spec)
        except Exception as exc:
            QMessageBox.critical(self, "Plot error",
                                 f"Could not plot the selected data:\n\n{exc}")
            self.figure.clear()
            self.canvas.draw_idle()
            return
        self.canvas.draw_idle()

    def clear_plot(self) -> None:
        self.figure.clear()
        self.canvas.draw_idle()
