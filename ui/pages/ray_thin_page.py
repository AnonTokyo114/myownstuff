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
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Label", "To next (mm)", "Focal (mm)",
             "Semi-dia (mm)", "Diameter (mm)"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.table.setFixedWidth(540)
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
        self.fan_table = QTableWidget(3, 4)
        self.fan_table.setHorizontalHeaderLabels(
            ["Point", "Height (mm)", "Angle (deg)", "Rays"])
        self.fan_table.verticalHeader().setVisible(False)
        self.fan_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.fan_table.setFixedWidth(430)
        self.fan_table.setMinimumHeight(110)
        left_layout.addWidget(self.fan_table)

        hint = QLabel(
            "Height is derived from Centre y and the two spacings.\n"
            "Finite object: the middle ray of each fan is parallel to the "
            "X axis; the other two are offset by Ray interval.\n"
            "Object at infinity: Centre angle sets the field direction.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #5b6470; font-size: 11px;")
        left_layout.addWidget(hint)

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
        splitter.setSizes([540, 620])
        outer.addWidget(splitter, 1)

        # ---- 信号 ----
        self.compute_btn.clicked.connect(self.recompute)
        self.object_distance.returnPressed.connect(self._on_object_distance)
        self.object_distance.editingFinished.connect(self._on_object_distance)
        self.interval.valueChanged.connect(lambda _v: self.recompute())
        self.centre_y.valueChanged.connect(self._on_layout_changed)
        self.d12.valueChanged.connect(self._on_d12)
        self.d23.valueChanged.connect(self._on_d23)
        self.link_spacing.toggled.connect(self._on_link_toggled)
        self.table.itemChanged.connect(self._on_lens_cell_changed)
        self.fan_table.itemChanged.connect(self._on_fan_table_changed)

        apply_theme(self)
        self.load_defaults()

    # ------------------------------------------------------------------
    def _on_object_distance(self) -> None:
        """物距在有限/无穷远之间切换时，刷新表格的可编辑状态。"""
        self._fill_fans()
        self.recompute()

    def _object_at_infinity(self) -> bool:
        """按当前输入框判断物距是否无穷远。"""
        return self.object_distance.text().strip().lower() in (
            "inf", "infinity", "∞", "+inf")

    def _on_link_toggled(self, checked: bool) -> None:
        """勾选联动时把 d23 同步成 d12，再重建物点。

        只切换复选框而不改数值，会留下"联动已开、间距却不等"的
        矛盾状态 —— 之前就是这个行为。
        """
        if checked:
            self._suspend_link = True
            self.d23.setValue(self.d12.value())
            self._suspend_link = False
        self._on_layout_changed()

    def _on_fan_table_changed(self, _item) -> None:
        """表格只允许改角度和条数；高度是派生值，改回去。"""
        self._fill_fan_heights()
        self.recompute()

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
        """values 是 (label, to_next, focal, semi_aperture)。"""
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.blockSignals(True)
        for column, value in enumerate(values):
            text = value if isinstance(value, str) else f"{value:g}"
            self.table.setItem(row, column, QTableWidgetItem(text))
        self._sync_diameter_from_aperture(row)
        self.table.blockSignals(False)

    def _sync_diameter_from_aperture(self, row: int) -> None:
        """半口径 -> 直径。空表示不限，另一列也留空。"""
        item = self.table.item(row, 3)
        raw = item.text().strip() if item else ""
        try:
            value = float(raw) if raw else None
        except ValueError:
            value = None
        text = "" if value is None else f"{value * 2:g}"
        self.table.setItem(row, 4, QTableWidgetItem(text))

    def _sync_aperture_from_diameter(self, row: int) -> None:
        """直径 -> 半口径。"""
        item = self.table.item(row, 4)
        raw = item.text().strip() if item else ""
        try:
            value = float(raw) if raw else None
        except ValueError:
            value = None
        text = "" if value is None else f"{value / 2:g}"
        self.table.setItem(row, 3, QTableWidgetItem(text))

    def _on_lens_cell_changed(self, item) -> None:
        """只在半口径/直径两列之间同步，避免递归。"""
        column = item.column()
        if column not in (3, 4):
            self.recompute()
            return
        self.table.blockSignals(True)
        try:
            if column == 3:
                self._sync_diameter_from_aperture(item.row())
            else:
                self._sync_aperture_from_diameter(item.row())
        finally:
            self.table.blockSignals(False)
        self.recompute()

    def add_row(self) -> None:
        self._append(("Lens", 100.0, 100.0, 12.5))
        self.recompute()

    def remove_last(self) -> None:
        if self.table.rowCount() > 1:
            self.table.removeRow(self.table.rowCount() - 1)
            self.recompute()

    # ------------------------------------------------------------------
    def _fan_specs(self) -> list[tuple[str, float, float, int]]:
        """从表格读 (label, height, centre_deg, count)。"""
        specs = []
        for row in range(self.fan_table.rowCount()):
            def cell(column: int) -> str:
                item = self.fan_table.item(row, column)
                return item.text().strip() if item else ""

            label = cell(0) or f"P{row + 1}"
            try:
                height = float(cell(1) or 0.0)
                centre = float(cell(2) or 0.0)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    f"Point row {row + 1}: height/angle is not a "
                    f"number ({exc}).") from exc
            widget = self.fan_table.cellWidget(row, 3)
            count = (int(widget.currentText())
                     if isinstance(widget, QComboBox)
                     else DEFAULT_RAY_COUNT)
            specs.append((label, height, centre, count))
        return specs

    def _fill_fans(self) -> None:
        """按中心高度和两个间距重建三行物点。

        高度列是**派生值**，只读；用户通过 Centre y / d12 / d23 控制它。
        角度和条数保留用户已填的内容。
        """
        heights = thin.object_heights(self.centre_y.value(),
                                      self.d12.value(),
                                      self.d23.value())
        self.fan_table.blockSignals(True)
        for row, height in enumerate(heights):
            label = QTableWidgetItem(f"P{row + 1}")
            label.setFlags(label.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.fan_table.setItem(row, 0, label)

            height_item = QTableWidgetItem(f"{height:.3f}")
            height_item.setFlags(
                height_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.fan_table.setItem(row, 1, height_item)

            centre_item = self.fan_table.item(row, 2)
            if centre_item is None:
                centre_item = QTableWidgetItem(f"{DEFAULT_CENTRE_DEG:g}")
                self.fan_table.setItem(row, 2, centre_item)
            # 有限物距下中心光线固定平行 X 轴；只有 inf 模式才让
            # 用户改视场方向。
            flags = centre_item.flags()
            if self._object_at_infinity():
                flags |= Qt.ItemFlag.ItemIsEditable
            else:
                flags &= ~Qt.ItemFlag.ItemIsEditable
                centre_item.setText(f"{DEFAULT_CENTRE_DEG:g}")
            centre_item.setFlags(flags)
            if self.fan_table.cellWidget(row, 3) is None:
                combo = QComboBox()
                combo.addItems([str(c) for c in thin.ObjectFan.ALLOWED_COUNTS])
                combo.setCurrentText(str(DEFAULT_RAY_COUNT))
                combo.setFixedWidth(60)
                combo.currentIndexChanged.connect(
                    lambda _i: self.recompute())
                self.fan_table.setCellWidget(row, 3, combo)
        self.fan_table.blockSignals(False)

    def _fill_fan_heights(self) -> None:
        """只刷新高度列；角度/条数由用户控制。"""
        heights = thin.object_heights(self.centre_y.value(),
                                      self.d12.value(),
                                      self.d23.value())
        self.fan_table.blockSignals(True)
        for row, height in enumerate(heights):
            item = QTableWidgetItem(f"{height:.3f}")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.fan_table.setItem(row, 1, item)
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
            # 索引 3 是半口径；索引 4 是派生的直径，不参与模型
            aperture_text = cell(3)
            try:
                aperture = float(aperture_text) if aperture_text else None
            except ValueError as exc:
                raise ValueError(
                    f"Row {row + 1}: aperture is not a number "
                    f"({exc}).") from exc
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
        try:
            specs = self._fan_specs()
        except ValueError as exc:
            self.summary.setText(f"<b>Cannot compute:</b> {exc}")
            self.figure.clear()
            self.canvas.draw_idle()
            return
        # 有限物距：中心光线固定平行 X 轴（θ = 0），只有间隔可调。
        # 无穷远：centre 是视场方向，按表格里的值走。
        at_infinity = system.object_at_infinity
        for index, (label, height, centre, count) in enumerate(specs):
            fans.append(thin.ObjectFan(
                height=height,
                centre_deg=centre if at_infinity else 0.0,
                count=count,
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
