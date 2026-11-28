"""理想薄透镜的界面：三物点、多发射光线。

只暴露薄透镜模型真正需要的量：到下一片的距离、焦距、通光口径；
以及物点的高度、中心发射角、条数。

绘图和摘要用**同一组**采样光线（``thin.sample_fans`` 的 ``groups``），
每个物点的光线用一个固定颜色。三组分别统计，绝不把三个像点混在一起
算 RMS —— 那算的是像点间距，不是光斑。

摘要明确写 "ideal paraxial, no aberrations or diffraction"：
这个模型的光斑在焦面上严格为零，容易被误读成"完美系统"。
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
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

#: 三个物点的默认中心发射角与条数
DEFAULT_CENTRE_DEG = 0.0
DEFAULT_INTERVAL_DEG = 0.5
DEFAULT_RAY_COUNT = 3


class ThinLensPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._suspend_link = False
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(8)

        title = QLabel("Paraxial / Thin Lens")
        title.setStyleSheet("font-size: 17px; font-weight: 700;")
        outer.addWidget(title)

        note = QLabel(
            "Ideal paraxial model - no aberrations, no diffraction; "
            "the on-axis spot at focus is exactly zero.")
        note.setStyleSheet("color: #5b6470;")
        outer.addWidget(note)

        # ---- 第一行：物距 / 角度间隔 / 计算 ----
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Object distance"))
        self.object_distance = QLineEdit("100")
        self.object_distance.setFixedWidth(90)
        self.object_distance.setToolTip(
            "Object plane to the first lens, in mm. Use inf for an object "
            "at infinity (parallel incidence).")
        row1.addWidget(self.object_distance)
        row1.addWidget(QLabel("mm"))

        row1.addSpacing(14)
        row1.addWidget(QLabel("Ray interval"))
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.0, 45.0)
        self.interval.setDecimals(3)
        self.interval.setSingleStep(0.1)
        self.interval.setValue(DEFAULT_INTERVAL_DEG)
        self.interval.setFixedWidth(80)
        self.interval.setToolTip("Angular spacing between rays of one fan.")
        row1.addWidget(self.interval)
        row1.addWidget(QLabel("deg"))

        self.compute_btn = QPushButton("Compute")
        self.compute_btn.setObjectName("primary")
        self.compute_btn.setFixedWidth(100)
        row1.addWidget(self.compute_btn)
        row1.addStretch(1)
        outer.addLayout(row1)

        # ---- 第二行：物点高度与间距 ----
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Centre y"))
        self.centre_y = QDoubleSpinBox()
        self.centre_y.setRange(-500.0, 500.0)
        self.centre_y.setDecimals(3)
        self.centre_y.setSingleStep(0.5)
        self.centre_y.setFixedWidth(90)
        row2.addWidget(self.centre_y)
        row2.addWidget(QLabel("mm"))

        row2.addSpacing(12)
        row2.addWidget(QLabel("d12"))
        self.d12 = QDoubleSpinBox()
        self.d12.setRange(0.0, 500.0)
        self.d12.setDecimals(3)
        self.d12.setSingleStep(0.5)
        self.d12.setValue(1.0)
        self.d12.setFixedWidth(90)
        row2.addWidget(self.d12)

        row2.addSpacing(8)
        row2.addWidget(QLabel("d23"))
        self.d23 = QDoubleSpinBox()
        self.d23.setRange(0.0, 500.0)
        self.d23.setDecimals(3)
        self.d23.setSingleStep(0.5)
        self.d23.setValue(1.0)
        self.d23.setFixedWidth(90)
        row2.addWidget(self.d23)

        self.link_spacing = QCheckBox("Equal spacing")
        self.link_spacing.setChecked(True)
        self.link_spacing.setToolTip(
            "When on, changing one spacing updates the other.")
        row2.addWidget(self.link_spacing)
        row2.addStretch(1)
        outer.addLayout(row2)

        # ---- 主体 ----
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
        self.table.setMinimumHeight(96)
        left_layout.addWidget(self.table)

        lens_rows = QHBoxLayout()
        add_btn = QPushButton("Add lens")
        add_btn.clicked.connect(self.add_row)
        remove_btn = QPushButton("Remove last")
        remove_btn.clicked.connect(self.remove_last)
        lens_rows.addWidget(add_btn)
        lens_rows.addWidget(remove_btn)
        lens_rows.addStretch(1)
        left_layout.addLayout(lens_rows)

        left_layout.addWidget(QLabel("Object points"))
        self.fan_table = QTableWidget(3, 3)
        self.fan_table.setHorizontalHeaderLabels(
            ["Point", "Height (mm)", "Centre angle (deg)"])
        self.fan_table.verticalHeader().setVisible(False)
        self.fan_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.fan_table.setFixedWidth(430)
        self.fan_table.setMinimumHeight(110)
        left_layout.addWidget(self.fan_table)

        fan_rows = QHBoxLayout()
        fan_rows.addWidget(QLabel("Rays per point"))
        self.ray_count = QComboBox()
        self.ray_count.addItems(["1", "3", "5"])
        self.ray_count.setCurrentText(str(DEFAULT_RAY_COUNT))
        self.ray_count.setFixedWidth(70)
        fan_rows.addWidget(self.ray_count)
        fan_rows.addStretch(1)
        left_layout.addLayout(fan_rows)

        left_layout.addStretch(1)
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
        self.figure = Figure(figsize=(6.4, 4.4), layout="constrained")
        self.canvas = FigureCanvasQTAgg(self.figure)
        right_layout.addWidget(self.canvas, 1)

        splitter.addWidget(right)
        splitter.setSizes([430, 660])
        outer.addWidget(splitter, 1)

        # ---- 信号 ----
        self.compute_btn.clicked.connect(self.recompute)
        self.object_distance.returnPressed.connect(self.recompute)
        self.interval.valueChanged.connect(lambda _v: self.recompute())
        self.centre_y.valueChanged.connect(self._on_layout_changed)
        self.d12.valueChanged.connect(self._on_d12)
        self.d23.valueChanged.connect(self._on_d23)
        self.link_spacing.toggled.connect(lambda _c: self._on_layout_changed())
        self.ray_count.currentIndexChanged.connect(lambda _i: self.recompute())
        self.fan_table.itemChanged.connect(lambda _i: self.recompute())

        apply_theme(self)
        self.load_defaults()

    # ------------------------------------------------------------------
    def _on_layout_changed(self, _value=None) -> None:
        """中心高度或间距变了：按 y1=y2-d12 / y3=y2+d23 重建三行。

        只在布局控件变化时重建，手动改表格不会被覆盖。
        """
        self._fill_fans()
        self.recompute()

    def _on_d12(self, value: float) -> None:
        if self.link_spacing.isChecked() and not self._suspend_link:
            self._suspend_link = True
            self.d23.setValue(value)
            self._suspend_link = False
        self._on_layout_changed(value)

    def _on_d23(self, value: float) -> None:
        if self.link_spacing.isChecked() and not self._suspend_link:
            self._suspend_link = True
            self.d12.setValue(value)
            self._suspend_link = False
        self._on_layout_changed(value)

    # ------------------------------------------------------------------
    def load_defaults(self) -> None:
        self.table.setRowCount(0)
        for row in DEFAULTS:
            self._append(row)
        self._fill_fans()
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
    def _fan_specs(self) -> list[tuple[str, float, float]]:
        """从表格读 (label, height, centre_deg)。"""
        specs = []
        for row in range(self.fan_table.rowCount()):
            def cell(column: int) -> str:
                item = self.fan_table.item(row, column)
                return item.text().strip() if item else ""

            label = cell(0) or f"P{row + 1}"
            try:
                height = float(cell(1) or 0.0)
                centre = float(cell(2) or 0.0)
            except ValueError as exc:
                raise ValueError(
                    f"Point row {row + 1}: height/angle is not a number "
                    f"({exc}).") from exc
            specs.append((label, height, centre))
        return specs

    def _fill_fans(self) -> None:
        """按中心高度和两个间距重建三行物点。"""
        heights = thin.object_heights(self.centre_y.value(),
                                      self.d12.value(),
                                      self.d23.value())
        self.fan_table.blockSignals(True)
        for row, height in enumerate(heights):
            self.fan_table.setItem(row, 0, QTableWidgetItem(f"P{row + 1}"))
            self.fan_table.setItem(row, 1, QTableWidgetItem(f"{height:.3f}"))
            item = self.fan_table.item(row, 2)
            if item is None:
                self.fan_table.setItem(
                    row, 2, QTableWidgetItem(f"{DEFAULT_CENTRE_DEG:g}"))
        self.fan_table.blockSignals(False)

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
                    f"Row {row + 1}: spacing/focal is not a number "
                    f"({exc}).") from exc
            aperture_text = cell(3)
            aperture = float(aperture_text) if aperture_text else None
            lenses.append(thin.LensSpec(label, focal, aperture, distance))

        text = self.object_distance.text().strip().lower()
        if text in ("inf", "infinity", "∞", "+inf"):
            object_distance = float("inf")
        else:
            try:
                object_distance = float(text)
            except ValueError as exc:
                raise ValueError(
                    f"Object distance {self.object_distance.text()!r} is "
                    f"neither a number nor inf.") from exc
        return thin.ThinSystem(object_distance, lenses)

    # ------------------------------------------------------------------
    def recompute(self) -> None:
        try:
            system = self._read_system()
            system.validate()
            gap = thin.image_distance(system)
            mag = thin.magnification(system)
            image_z = thin.image_plane_z(system)
        except Exception as exc:                 # noqa: BLE001
            self.summary.setText(f"<b>Cannot compute:</b> {exc}")
            self.figure.clear()
            self.canvas.draw_idle()
            return

        fans = []
        count = int(self.ray_count.currentText())
        try:
            specs = self._fan_specs()
        except ValueError as exc:
            self.summary.setText(f"<b>Cannot compute:</b> {exc}")
            self.figure.clear()
            self.canvas.draw_idle()
            return
        for index, (label, height, centre) in enumerate(specs):
            fans.append(thin.ObjectFan(
                height=height, centre_deg=centre, count=count,
                label=label, colour=thin.FAN_COLOURS[index % 3]))

        profile = None
        profile_error = None
        try:
            profile = thin.sample_fans(system, fans, self.interval.value())
        except Exception as exc:                 # noqa: BLE001
            profile_error = str(exc)

        self._render_summary(system, gap, mag, image_z, profile,
                             profile_error)
        self._render(system, image_z, profile)

    # ------------------------------------------------------------------
    def _render_summary(self, system, gap, mag, image_z, profile,
                        profile_error) -> None:
        parts = []
        if math.isfinite(gap):
            parts.append(f"image distance = {gap:.4f} mm")
        else:
            parts.append("<b>no finite image</b> (afocal: output collimated)")

        if image_z is not None:
            parts.append(f"image plane z = {image_z:.3f} mm")

        if mag is not None:
            parts.append(f"magnification = {mag:.5f}")
        elif system.object_at_infinity:
            parts.append("magnification = n/a (parallel incidence)")
        else:
            parts.append("magnification = n/a (no finite image)")

        parts.append("<i>ideal paraxial model — no aberrations, "
                     "no diffraction</i>")

        lines = [" &nbsp;|&nbsp; ".join(parts)]

        if profile is not None:
            lines.append("<b>Per object point</b>")
            for group in profile["groups"]:
                colour = group["colour"]
                head = (f"<span style='color:{colour}'>&#9632;</span> "
                        f"{group['label']} (y = {group['height']:+.3f} mm)")
                if not group["has_image"]:
                    lines.append(f"{head}: no finite image plane")
                    continue
                if group["n_reached"] == 0:
                    lines.append(f"{head}: all {group['n_requested']} rays "
                                 f"blocked")
                    continue
                if not group["sufficient"]:
                    lines.append(
                        f"{head}: <b>insufficient sampling</b> "
                        f"({group['n_reached']}/{group['n_requested']} rays "
                        f"reached; need 2 for an RMS)")
                    continue
                lines.append(
                    f"{head}: image {group['centroid']:+.6f} mm, "
                    f"RMS {group['rms'] * 1000:.4f} µm, "
                    f"PV {group['pv'] * 1000:.4f} µm, "
                    f"{group['n_reached']}/{group['n_requested']} rays")
        elif profile_error is not None:
            lines.append(f"<i>rays not available: {profile_error}</i>")

        self.summary.setText("<br>".join(lines))

    # ------------------------------------------------------------------
    def _render(self, system, image_z, profile) -> None:
        self.figure.clear()
        ax = self.figure.add_subplot(111)

        positions = system.lens_z()
        radii = [lens.aperture for lens in system.lenses]
        default_half = max((r for r in radii if r is not None), default=10.0)

        zs: list[float] = [0.0, *positions]
        ys: list[float] = [0.0]
        for lens, z in zip(system.lenses, positions):
            half = lens.aperture if lens.aperture is not None else default_half
            ys.extend([half, -half])
        if image_z is not None:
            zs.append(image_z)

        if profile is not None:
            for group in profile["groups"]:
                for ray in group["rays"]:
                    for z, y in ray["path"]:
                        zs.append(z)
                        ys.append(y)

        z_min = min(zs)
        z_max = max(zs)
        span = z_max - z_min
        pad = max(span * 0.06, 5.0)
        y_abs = max(abs(min(ys)), abs(max(ys)), default_half) * 1.18

        ax.plot([z_min - pad, z_max + pad], [0, 0], color="#999",
                linewidth=0.6)

        # 透镜
        for lens, z in zip(system.lenses, positions):
            half = lens.aperture if lens.aperture is not None else default_half
            ax.plot([z, z], [-half, half], color="#2f6fb0", linewidth=3.0,
                    solid_capstyle="butt", alpha=0.85)
            ax.text(z, half * 1.06, lens.label, ha="center", va="bottom",
                    fontsize=8, color="#2f6fb0")

        # 物面 / 像面
        if not system.object_at_infinity:
            ax.axvline(0.0, color="#8a6d3b", linestyle=":", linewidth=1.0)
            ax.text(0.0, y_abs * 0.94, "object", color="#8a6d3b",
                    ha="center", va="top", fontsize=8)
        if image_z is not None:
            ax.axvline(image_z, color="#1a7f37", linestyle="--",
                       linewidth=1.2)
            ax.text(image_z, y_abs * 0.94, "image", color="#1a7f37",
                    ha="center", va="top", fontsize=9)

        # 光线：和摘要用同一组；每个物点一个颜色
        virtual = image_z is not None and image_z < positions[-1] - 1e-9
        if profile is not None:
            for group in profile["groups"]:
                colour = group["colour"]
                for ray in group["rays"]:
                    path = ray["path"]
                    blocked = ray["blocked_at"] is not None
                    if len(path) >= 2:
                        if virtual and not blocked:
                            ax.plot([p[0] for p in path[:-1]],
                                    [p[1] for p in path[:-1]],
                                    color=colour, linewidth=0.8, alpha=0.75)
                            ax.plot([path[-2][0], path[-1][0]],
                                    [path[-2][1], path[-1][1]],
                                    color=colour, linewidth=0.8, alpha=0.5,
                                    linestyle="--")
                        else:
                            ax.plot([p[0] for p in path],
                                    [p[1] for p in path],
                                    color=colour, linewidth=0.8,
                                    alpha=0.75,
                                    linestyle="-" if not blocked else ":")
                    if blocked and path:
                        ax.plot([path[-1][0]], [path[-1][1]], marker="x",
                                color="#b3261e", markersize=5)

        ax.set_xlim(z_min - pad, z_max + pad)
        ax.set_ylim(-y_abs, y_abs)
        ax.set_xlabel("z (mm)")
        ax.set_ylabel("y (mm)")
        ax.grid(True, alpha=0.25)
        ax.set_title("Thin-lens layout (ideal paraxial)", fontsize=10)
        self.canvas.draw_idle()

    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """窗口关闭时的钩子；这里没有需要释放的设备。"""
