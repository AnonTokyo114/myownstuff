"""理想薄透镜的界面。

只暴露薄透镜模型真正需要的四个量：到下一片的距离、焦距、通光口径。
曲率和折射率不在这里 —— 那是 Surface Ray Tracer 的事。

摘要里明确写 "ideal paraxial, no aberrations or diffraction"，
因为这个模型的光斑在焦面上严格为零，容易被误读成"完美系统"。
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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

from core.optics import thin
from ui.theme import apply_theme

#: 默认：经典 4f 系统（物在前焦面 -> L1 -> 2f -> L2 -> 后焦面）
DEFAULTS = [
    # (label, distance_to_next, focal, aperture)
    ("Lens 1", 200.0, 100.0, 12.5),
    ("Lens 2", 100.0, 100.0, 12.5),
]


class ThinLensPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        title = QLabel("Paraxial / Thin Lens")
        title.setStyleSheet("font-size: 17px; font-weight: 700;")
        outer.addWidget(title)

        note = QLabel("理想近轴模型 —— 无像差、无衍射；焦面上光斑严格为零。")
        note.setStyleSheet("color: #5b6470;")
        outer.addWidget(note)

        # ---- 物距 ----
        params = QHBoxLayout()
        params.addWidget(QLabel("Object distance"))
        self.object_distance = QLineEdit("100")
        self.object_distance.setFixedWidth(110)
        self.object_distance.setToolTip(
            "物面到第一片透镜的距离（mm）。物在无穷远时填 inf。")
        params.addWidget(self.object_distance)
        params.addWidget(QLabel("mm"))

        params.addSpacing(16)
        params.addWidget(QLabel("Spot rays"))
        self.n_rays = QComboBox()
        self.n_rays.addItems(["1", "3", "5", "9", "21"])
        self.n_rays.setCurrentText("9")
        self.n_rays.setFixedWidth(70)
        params.addWidget(self.n_rays)

        self.compute_btn = QPushButton("Compute")
        self.compute_btn.setObjectName("primary")
        self.compute_btn.setFixedWidth(110)
        params.addWidget(self.compute_btn)
        params.addStretch(1)
        outer.addLayout(params)

        # ---- 主体：左表格 右图 ----
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        left_layout.addWidget(QLabel("Lenses"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Label", "To next (mm)", "Focal (mm)", "Aperture (mm)"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.setFixedWidth(430)
        left_layout.addWidget(self.table, 1)

        rows = QHBoxLayout()
        add_btn = QPushButton("Add lens")
        add_btn.clicked.connect(self.add_row)
        remove_btn = QPushButton("Remove last")
        remove_btn.clicked.connect(self.remove_last)
        rows.addWidget(add_btn)
        rows.addWidget(remove_btn)
        rows.addStretch(1)
        left_layout.addLayout(rows)

        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setObjectName("mirrorish")
        self.summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        right_layout.addWidget(self.summary)

        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        self.figure = Figure(figsize=(6.4, 4.2), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        right_layout.addWidget(self.canvas, 1)

        splitter.addWidget(right)
        splitter.setSizes([430, 660])
        outer.addWidget(splitter, 1)

        self.compute_btn.clicked.connect(self.recompute)
        self.object_distance.returnPressed.connect(self.recompute)
        self.n_rays.currentIndexChanged.connect(lambda _i: self.recompute())

        apply_theme(self)
        self.load_defaults()

    # ------------------------------------------------------------------
    def load_defaults(self) -> None:
        self.table.setRowCount(0)
        for row in DEFAULTS:
            self._append(row)
        self.recompute()

    def _append(self, values) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, value in enumerate(values):
            text = value if isinstance(value, str) else f"{value:g}"
            self.table.setItem(row, column, QTableWidgetItem(text))

    def add_row(self) -> None:
        self._append(("Lens", 100.0, 100.0, 12.5))
        self.recompute()

    def remove_last(self) -> None:
        if self.table.rowCount() > 1:
            self.table.removeRow(self.table.rowCount() - 1)
            self.recompute()

    # ------------------------------------------------------------------
    def _read_system(self) -> thin.ThinSystem:
        lenses = []
        for row in range(self.table.rowCount()):
            def cell(column: int) -> str:
                item = self.table.item(row, column)
                return item.text().strip() if item else ""

            label = cell(0) or f"Lens {row + 1}"
            try:
                distance = float(cell(1) or 0.0)
                focal = float(cell(2))
            except ValueError as exc:
                raise ValueError(
                    f"第 {row + 1} 行的距离/焦距不是数字：{exc}") from exc
            aperture_text = cell(3)
            aperture = float(aperture_text) if aperture_text else None
            lenses.append(thin.LensSpec(label, focal, aperture, distance))

        text = self.object_distance.text().strip().lower()
        if text in ("inf", "infinity", "∞"):
            object_distance = float("inf")
        else:
            try:
                object_distance = float(text)
            except ValueError as exc:
                raise ValueError(
                    f"物距 {self.object_distance.text()!r} 不是数字，"
                    f"也不是 inf。") from exc
        return thin.ThinSystem(object_distance, lenses)

    # ------------------------------------------------------------------
    def recompute(self) -> None:
        try:
            system = self._read_system()
            system.validate()
            gap = thin.image_distance(system)
            mag = thin.magnification(system)
            image_z = thin.image_plane_z(system)
            profile = thin.spot(system, n_rays=int(self.n_rays.currentText()))
        except Exception as exc:                 # noqa: BLE001
            self.summary.setText(f"<b>Cannot compute:</b> {exc}")
            self.figure.clear()
            self.canvas.draw_idle()
            return

        parts = [
            f"image distance = {gap:.4f} mm" if math.isfinite(gap)
            else "image distance = ∞ (afocal)",
            f"image plane z = {image_z:.3f} mm",
            f"magnification = {mag:.5f}" if math.isfinite(mag)
            else "magnification = n/a (object at infinity)",
            f"spot RMS = {profile['rms'] * 1000:.3f} µm "
            f"(PV {profile['pv'] * 1000:.3f} µm, "
            f"{profile['n_reached']}/{profile['n_requested']} rays)",
            "<i>ideal paraxial model — no aberrations, no diffraction</i>",
        ]
        if not math.isfinite(gap):
            parts.append("<b>afocal system:</b> output is collimated.")
        elif gap < 0:
            parts.append("<b>virtual image</b> (negative distance).")
        self.summary.setText(" &nbsp;|&nbsp; ".join(parts))

        self._render(system, gap, image_z, profile)

    # ------------------------------------------------------------------
    def _render(self, system, gap, image_z, profile) -> None:
        self.figure.clear()
        ax = self.figure.add_subplot(111)

        # 光轴
        limit = max(10.0, profile["radius"] * 1.6)
        z_min = -5.0
        z_max = max(image_z, 0.0) + 10.0
        if not math.isfinite(z_max):
            z_max = 200.0
        ax.plot([z_min, z_max], [0, 0], color="#999", linewidth=0.6)

        # 透镜：竖线 + 口径
        z = float(system.object_distance) if math.isfinite(
            system.object_distance) else 0.0
        last = len(system.lenses) - 1
        for index, lens in enumerate(system.lenses):
            half = lens.aperture if lens.aperture is not None else limit
            ax.plot([z, z], [-half, half], color="#2f6fb0", linewidth=3.0,
                    solid_capstyle="butt", alpha=0.85)
            ax.text(z, half * 1.06, lens.label, ha="center", va="bottom",
                    fontsize=8, color="#2f6fb0")
            if index < last:
                z += lens.distance_to_next

        # 光线
        heights = ([0.0] if profile["n_requested"] == 1 else
                   list(np.linspace(-profile["radius"], profile["radius"],
                                    profile["n_requested"])))
        for h in heights:
            path = thin.trace(system, h, 0.0, image_gap=gap)["path"]
            ax.plot([p[0] for p in path], [p[1] for p in path],
                    color="#c0392b", linewidth=0.8, alpha=0.7)

        # 像面
        if math.isfinite(image_z):
            ax.axvline(image_z, color="#1a7f37", linestyle="--",
                       linewidth=1.2)
            ax.text(image_z, limit * 0.92, "image", color="#1a7f37",
                    ha="center", va="top", fontsize=9)

        ax.set_xlim(z_min, z_max)
        ax.set_ylim(-limit * 1.15, limit * 1.15)
        ax.set_xlabel("z (mm)")
        ax.set_ylabel("y (mm)")
        ax.grid(True, alpha=0.25)
        ax.set_title("Thin-lens layout (ideal paraxial)", fontsize=10)
        self.canvas.draw_idle()

    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """窗口关闭时的钩子；这里没有需要释放的设备。"""
