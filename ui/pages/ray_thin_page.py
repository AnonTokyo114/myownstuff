"""理想薄透镜的界面。

只暴露薄透镜模型真正需要的四个量：到下一片的距离、焦距、通光口径。
曲率和折射率不在这里 —— 那是 Surface Ray Tracer 的事。

绘图和摘要用**同一组**采样光线（``thin.sample_rays`` 的 ``rays``），
避免出现"摘要说光斑为零、图上却有 25 mm 宽的线束"这种自相矛盾。

摘要里明确写 "ideal paraxial, no aberrations or diffraction"，
因为这个模型的光斑在焦面上严格为零，容易被误读成"完美系统"。
"""

from __future__ import annotations

import math

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

        # ---- 参数行 ----
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

        # ---- 左表格 / 右图 ----
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
        if text in ("inf", "infinity", "∞", "+inf"):
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
        # 第一步：成像求解 —— 这一部分不依赖口径
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

        # 第二步：光斑 —— 没有口径或没有有限像面时跳过，但不影响上面的结果
        profile = None
        profile_error = None
        try:
            profile = thin.sample_rays(system,
                                       n_rays=int(self.n_rays.currentText()))
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
            parts.append("magnification = n/a (object at infinity)")
        else:
            parts.append("magnification = n/a (no finite image)")

        if profile is not None and profile["has_image"]:
            parts.append(
                f"spot RMS = {profile['rms'] * 1000:.3f} µm "
                f"(PV {profile['pv'] * 1000:.3f} µm, "
                f"{profile['n_reached']}/{profile['n_requested']} rays)")
        elif profile_error is not None:
            parts.append(f"<i>spot not available: {profile_error}</i>")

        parts.append("<i>ideal paraxial model — no aberrations, "
                     "no diffraction</i>")

        if math.isfinite(gap) and gap < 0:
            parts.append("<b>virtual image</b> (negative distance).")
        self.summary.setText(" &nbsp;|&nbsp; ".join(parts))

    # ------------------------------------------------------------------
    def _render(self, system, image_z, profile) -> None:
        self.figure.clear()
        ax = self.figure.add_subplot(111)

        positions = system.lens_z()
        radii = [lens.aperture for lens in system.lenses]
        default_half = max((r for r in radii if r is not None), default=10.0)

        # 画图范围由**实际存在的**坐标决定：物面、每片透镜、像面、
        # 所有光线路径。虚像时像面在负 z，不能被裁掉。
        zs: list[float] = [0.0, *positions]
        ys: list[float] = [0.0]
        for lens, z in zip(system.lenses, positions):
            half = lens.aperture if lens.aperture is not None else default_half
            ys.extend([half, -half])
        if image_z is not None:
            zs.append(image_z)

        if profile is not None:
            for ray in profile["rays"]:
                for z, y in ray["path"]:
                    zs.append(z)
                    ys.append(y)

        z_min = min(zs)
        z_max = max(zs)
        span = z_max - z_min
        pad = max(span * 0.06, 5.0)
        y_abs = max(abs(min(ys)), abs(max(ys)), default_half) * 1.15

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
            ax.text(0.0, y_abs * 0.92, "object", color="#8a6d3b",
                    ha="center", va="top", fontsize=8)
        if image_z is not None:
            ax.axvline(image_z, color="#1a7f37", linestyle="--",
                       linewidth=1.2)
            ax.text(image_z, y_abs * 0.92, "image", color="#1a7f37",
                    ha="center", va="top", fontsize=9)

        # 光线：和摘要用同一组
        if profile is not None:
            for ray in profile["rays"]:
                path = ray["path"]
                blocked = ray["blocked_at"] is not None
                if len(path) >= 2:
                    ax.plot([p[0] for p in path], [p[1] for p in path],
                            color="#c0392b" if not blocked else "#b3261e",
                            linewidth=0.8, alpha=0.7,
                            linestyle="-" if not blocked else ":")
                # 叉号和"有没有线段可画"是两件事：第一片就被挡的光线
                # path 只有起点，仍然要标出它停在哪个口径上。
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
