"""光线追迹页：面型表编辑 + 光线图 + 像质分析。

物理与绘图全部来自 core.optics（与 myapp 逐位一致），本页只做交互与呈现。
两个视图：
  * 光线图   —— draw_surfaces + 每条 ray 的 path
  * 像质分析 —— 球差曲线 + 焦距/主平面/光斑/最佳焦面
"""

from __future__ import annotations


import matplotlib

matplotlib.use("QtAgg")

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.optics import (
    Surface,
    best_focus,
    draw_surfaces,
    focal_properties,
    longitudinal_spherical,
    spot_analysis,
    trace_rays,
    validate_surfaces,
    vertex_positions,
)

#: 列名尽量短：左栏宽度有限，长列名会被表头裁掉
COLUMNS = ("Name", "R (mm)", "Spacing (mm)", "n", "Semi-ap.")

#: 与 myapp 的默认三片式一致
DEFAULTS = [
    ("Object plane", "inf", "50", "1.0", ""),
    ("First lens front", "50", "5", "1.519", "12.5"),
    ("First lens back", "-80", "20", "1.0", "12.5"),
    ("Second lens front", "-50", "5", "1.519", "12.5"),
    ("Second lens back", "-70", "40", "1.0", "12.5"),
    ("Image plane", "inf", "0", "1.0", ""),
]


def _setup_chinese_font() -> None:
    """让 matplotlib 能显示中文标签；找不到中文字体就退回英文。"""
    from matplotlib import font_manager, rcParams

    for name in ("Microsoft YaHei", "SimHei", "SimSun"):
        try:
            font_manager.findfont(name, fallback_to_default=False)
            rcParams["font.sans-serif"] = [name]
            rcParams["axes.unicode_minus"] = False
            return
        except Exception:
            continue


_setup_chinese_font()


class RayPage(QWidget):
    page_title = "Ray Tracer"

    def __init__(self) -> None:
        super().__init__()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        title = QLabel("Ray Tracer")
        title.setObjectName("pageTitle")
        outer.addWidget(title)
        subtitle = QLabel("Paraxial and exact meridional tracing, with spot size, best focus and spherical aberration")
        subtitle.setObjectName("pageSubtitle")
        outer.addWidget(subtitle)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # =============================================== 左：面型表
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        left_layout.addWidget(QLabel("Surface table"))
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        # 不要用 Stretch 等分：中文列名（"到下一面 (mm)"）会被截断。
        # 名称列给足，其余按内容宽度固定，可用拖动调整。
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0, 128)
        self.table.setColumnWidth(1, 62)
        self.table.setColumnWidth(2, 84)
        self.table.setColumnWidth(3, 52)
        self.table.setColumnWidth(4, 74)
        header.setStretchLastSection(True)
        left_layout.addWidget(self.table, 1)

        row_btns = QHBoxLayout()
        row_btns.setSpacing(6)
        for text, slot in (("+ Add", self.add_row),
                           ("− Delete last", self.delete_last),
                           ("Load defaults", self.load_defaults)):
            btn = QPushButton(text)
            btn.setObjectName("ghost")
            btn.clicked.connect(slot)
            row_btns.addWidget(btn)
        row_btns.addStretch(1)
        left_layout.addLayout(row_btns)
        splitter.addWidget(left)

        # =============================================== 右：视图
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        view_row = QHBoxLayout()
        view_row.setSpacing(6)
        self.btn_rays = QPushButton("Ray diagram")
        self.btn_rays.setObjectName("primary")
        self.btn_quals = QPushButton("Image quality")
        self.btn_quals.setObjectName("ghost")
        for b in (self.btn_rays, self.btn_quals):
            b.setFixedWidth(96)
            view_row.addWidget(b)
        view_row.addStretch(1)
        right_layout.addLayout(view_row)

        self.figure = Figure(figsize=(6.4, 5.2), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setSizePolicy(self.canvas.sizePolicy().horizontalPolicy(),
                                  self.canvas.sizePolicy().verticalPolicy())
        right_layout.addWidget(self.canvas, 1)

        # ---- 参数与结果 ----
        params = QFrame()
        params.setObjectName("formBox")
        params_layout = QHBoxLayout(params)
        params_layout.setContentsMargins(12, 8, 12, 8)
        params_layout.setSpacing(8)

        params_layout.addWidget(QLabel("Heights"))
        self.heights = QLineEdit("0, 5, 10")
        self.heights.setFixedWidth(110)
        params_layout.addWidget(self.heights)

        params_layout.addWidget(QLabel("Angles"))
        self.angles = QLineEdit("0, 1, 2")
        self.angles.setFixedWidth(100)
        params_layout.addWidget(self.angles)

        params_layout.addWidget(QLabel("Mode"))
        self.mode = QComboBox()
        self.mode.addItems(["Exact", "Paraxial"])
        self.mode.setFixedWidth(110)
        params_layout.addWidget(self.mode)

        self.trace_btn = QPushButton("Re-trace")
        self.trace_btn.setObjectName("primary")
        self.trace_btn.setFixedWidth(104)
        params_layout.addWidget(self.trace_btn)
        params_layout.addStretch(1)
        right_layout.addWidget(params)

        self.summary = QLabel("")
        self.summary.setObjectName("mirrorish")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        right_layout.addWidget(self.summary)

        splitter.addWidget(right)
        splitter.setSizes([460, 800])
        outer.addWidget(splitter, 1)

        # ---- 信号 ----
        self.trace_btn.clicked.connect(lambda: self.recompute())
        self.btn_rays.clicked.connect(lambda: self.recompute(view="rays"))
        self.btn_quals.clicked.connect(lambda: self.recompute(view="qual"))
        self.mode.currentIndexChanged.connect(lambda _i: self.recompute())

        self.load_defaults()

    # ================================================================ 表格
    def load_defaults(self) -> None:
        self.table.setRowCount(0)
        for row in DEFAULTS:
            self._append_row(row)
        self.recompute()

    def add_row(self) -> None:
        self._append_row(("Surface", "inf", "10", "1.0", ""))
        self.recompute()

    def _append_row(self, values) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c, value in enumerate(values):
            self.table.setItem(r, c, QTableWidgetItem(str(value)))

    def delete_last(self) -> None:
        if self.table.rowCount() <= 2:
            return
        self.table.removeRow(self.table.rowCount() - 1)
        self.recompute()

    def _read_surfaces(self) -> list[Surface]:
        surfaces = []
        for i in range(self.table.rowCount()):
            cells = []
            for c in range(len(COLUMNS)):
                item = self.table.item(i, c)
                cells.append((item.text() if item else "").strip())
            name, r_txt, t_txt, n_txt, ap_txt = cells
            try:
                R = float(r_txt) if r_txt else float("inf")
                t = float(t_txt) if t_txt else 0.0
                n = float(n_txt) if n_txt else 1.0
                ap = float(ap_txt) if ap_txt else None
            except ValueError as exc:
                raise ValueError(f"Invalid value in row {i + 1}: {exc}") from exc
            surfaces.append(Surface(name or f"Surface {i}", R, t, n, ap))
        return surfaces

    def _parse_list(self, text: str) -> list[float]:
        out = []
        for piece in text.split(","):
            piece = piece.strip()
            if piece:
                out.append(float(piece))
        return out

    # ================================================================ 计算
    def recompute(self, view: str | None = None) -> None:
        if view is None:
            view = "rays" if self.btn_rays.objectName() == "primary" else "qual"

        try:
            surfaces = self._read_surfaces()
            validate_surfaces(surfaces)
            z = vertex_positions(surfaces)
            heights = self._parse_list(self.heights.text())
            angles = self._parse_list(self.angles.text())
            if not heights:
                heights = [0.0]
            if not angles:
                angles = [0.0]

            rays, z_rays = trace_rays(surfaces, heights, angles,
                                      mode=self.mode.currentText())
            fp = focal_properties(surfaces, z)

            self._render(surfaces, z, fp, view)
            self._update_summary(surfaces, z, fp, rays)
        except Exception as exc:
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            ax.axis("off")
            ax.text(0.5, 0.5, f"⚠ {exc}", ha="center", va="center",
                    wrap=True, fontsize=10, color="#b3261e")
            self.canvas.draw_idle()
            self.summary.setText(f"⚠ {exc}")

        self._set_view_style(view)

    def _set_view_style(self, view: str) -> None:
        if view == "rays":
            self.btn_rays.setObjectName("primary")
            self.btn_quals.setObjectName("ghost")
        else:
            self.btn_rays.setObjectName("ghost")
            self.btn_quals.setObjectName("primary")
        for b in (self.btn_rays, self.btn_quals):
            b.style().unpolish(b)
            b.style().polish(b)

    # ================================================================ 绘图
    def _render(self, surfaces, z, fp, view: str) -> None:
        self.figure.clear()
        view_is_rays = (view == "rays")

        if view_is_rays:
            ax = self.figure.add_subplot(111)
            draw_surfaces(ax, surfaces, z)
            heights = self._parse_list(self.heights.text()) or [0.0]
            angles = self._parse_list(self.angles.text()) or [0.0]
            rays, _ = trace_rays(surfaces, heights, angles,
                                 mode=self.mode.currentText())
            for idx, ray in enumerate(rays):
                path = ray.get("path") or []
                if len(path) < 2:
                    continue
                zs = [p[0] for p in path]
                ys = [p[1] for p in path]
                colour = f"C{idx % 10}"
                ax.plot(zs, ys, color=colour, linewidth=0.8, alpha=0.85)
                if ray.get("status") != "Reached image plane":
                    ax.plot(zs[-1:], ys[-1:], marker="x", color="#b3261e",
                            markersize=4)
            ax.set_xlabel("z (mm)")
            ax.set_ylabel("y (mm)")
            ax.set_title("Ray diagram")
            ax.grid(True, alpha=0.25)
            self.figure.tight_layout()
        else:
            # ---- 像质：球差曲线 ----
            ax = self.figure.add_subplot(111)
            try:
                lsa = longitudinal_spherical(surfaces, z, n_samples=7) or []
            except Exception:
                lsa = []
            hs = [row["h"] for row in lsa if row.get("lsa") is not None]
            vs = [row["lsa"] for row in lsa if row.get("lsa") is not None]
            if hs:
                ax.plot(vs, hs, marker="o", color="#2f6fb0", linewidth=1.4,
                        markersize=5)
                ax.axvline(0, color="black", linewidth=0.6, linestyle="--")
                ax.set_xlabel("Longitudinal SA (mm)")
                ax.set_ylabel("Pupil height h (mm)")
                ax.set_title("Spherical aberration")
                ax.grid(True, alpha=0.25)
            else:
                ax.axis("off")
                ax.text(0.5, 0.5, "No usable spherical-aberration samples for this system",
                        ha="center", va="center", fontsize=10, color="#5b6470")
            self.figure.tight_layout()

        self.canvas.draw_idle()

    # ================================================================ 摘要
    def _update_summary(self, surfaces, z, fp, rays) -> None:
        parts = []
        efl = fp.get("efl")
        bfl = fp.get("bfl")
        ffl = fp.get("ffl")
        if efl is not None:
            parts.append(f"EFL = {efl:.4f} mm")
        if bfl is not None:
            parts.append(f"BFL = {bfl:.4f} mm")
        if ffl is not None:
            parts.append(f"FFL = {ffl:.4f} mm")
        hf, hb = fp.get("h_front"), fp.get("h_back")
        if hf is not None and hb is not None:
            parts.append(f"principal planes  front {hf:.3f} / rear {hb:.3f} mm")

        try:
            sp = spot_analysis(surfaces, z, 0.0, n_pupil=9)
            rms, pv = sp.get("rms"), sp.get("pv")
            if rms is not None:
                parts.append(f"on-axis spot RMS {rms * 1000:.2f} µm / PV {pv * 1000:.2f} µm")
        except Exception:
            pass

        try:
            bf = best_focus(surfaces, z, 0.0, n_scan=21, n_pupil=9)
            spacing = bf.get("best_gap")
            if spacing is not None:
                # best_gap 是「最后曲面到像面」的绝对间距；顺带给出相对
                # 当前像面的偏移，免得读者分不清这两个量。
                base = bf.get("base_gap", spacing)
                text = (f"best focus at spacing {spacing:.2f} mm "
                        f"(shift {spacing - base:+.2f} mm from current)")
                rms = bf.get("best_rms")
                if rms is not None:
                    text += f", RMS {rms * 1000:.1f} µm"
                parts.append(text)
        except Exception:
            pass

        bad = sum(1 for r in rays if r.get("status") != "Reached image plane")
        if bad:
            parts.append(f"{bad} ray(s) did not reach the image plane")
        else:
            parts.append(f"all {len(rays)} rays reached the image plane")

        self.summary.setText("   |   ".join(parts))
