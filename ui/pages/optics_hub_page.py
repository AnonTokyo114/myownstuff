"""光学工具的选择页：三套模型，各开独立窗口。

三者是**互补模型**，不是同一个东西的三档精度：

  Paraxial / Thin Lens   理想薄透镜，只看距离、焦距、口径
  Surface Ray Tracer     球面曲率 + 精确 Snell，有球差和渐晕
  Fourier Optics         复光场 + 衍射（尚未实现）

它们不能混用：薄透镜模型没有像差，曲面模型没有衍射，
Fourier 模型里的"透镜"是相位掩模而不是折射面。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ToolCard(QFrame):
    """一张工具卡片：标题 + 说明 + 打开按钮。"""

    def __init__(self, title: str, subtitle: str, detail: str,
                 button_text: str, *, enabled: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("navCard")
        self.setMinimumHeight(132)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(6)

        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)

        tagline = QLabel(subtitle)
        tagline.setWordWrap(True)
        tagline.setStyleSheet("color: #2f6fb0; font-weight: 600;")
        layout.addWidget(tagline)

        body = QLabel(detail)
        body.setWordWrap(True)
        body.setObjectName("cardDesc")
        layout.addWidget(body)

        self.button = QPushButton(button_text)
        self.button.setObjectName("primary")
        self.button.setFixedWidth(160)
        self.button.setEnabled(enabled)
        layout.addWidget(self.button, alignment=Qt.AlignmentFlag.AlignLeft)


class OpticsHubPage(QWidget):
    """三张卡片，点击后各开一个独立窗口。

    窗口引用存在 ``self._windows`` 里 —— 不保存的话,
    Python 端对象会被回收，窗口刚开就消失。
    """

    def __init__(self) -> None:
        super().__init__()
        self._windows: list[QWidget] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)

        heading = QLabel("Optics")
        heading.setStyleSheet("font-size: 20px; font-weight: 700;")
        outer.addWidget(heading)

        intro = QLabel(
            "三个互补的光学模型。每个在自己的窗口里运行，"
            "互不影响；参数各自保留。")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #5b6470;")
        outer.addWidget(intro)

        self.thin_card = ToolCard(
            "Paraxial / Thin Lens",
            "理想薄透镜 · 近轴",
            "输入：到下一片的距离、焦距、通光口径。\n"
            "用途：快速核算成像位置、放大率、4f、中继、扩束。\n"
            "忽略像差与衍射 —— 轴上光斑在焦面上严格为零。",
            "Open thin lens",
        )
        outer.addWidget(self.thin_card)

        self.surface_card = ToolCard(
            "Surface Ray Tracer",
            "球面 · 精确追迹",
            "输入：曲率半径、厚度、折射率、口径。\n"
            "用途：实际曲面设计，看球差、渐晕、最佳焦面。\n"
            "几何光学，无衍射；像质是子午采样指标。",
            "Open surface tracer",
        )
        outer.addWidget(self.surface_card)

        self.fourier_card = ToolCard(
            "Fourier Optics",
            "复光场 · 衍射",
            "输入：波长、采样网格、孔径与相位。\n"
            "用途：实空间 / k 空间成像、空间滤波、PSF。\n"
            "尚未实现 —— 需要新的复场传播内核。",
            "Not available yet",
            enabled=False,
        )
        outer.addWidget(self.fourier_card)

        outer.addStretch(1)

        self.thin_card.button.clicked.connect(self._open_thin)
        self.surface_card.button.clicked.connect(self._open_surface)

    # ------------------------------------------------------------------
    def _open_thin(self) -> None:
        from ui.pages.ray_window import ThinLensWindow
        self._show(ThinLensWindow())

    def _open_surface(self) -> None:
        from ui.pages.ray_window import SurfaceWindow
        self._show(SurfaceWindow())

    def _show(self, window: QWidget) -> None:
        window.show()
        self._windows.append(window)
        window.destroyed.connect(
            lambda *_: self._windows.remove(window)
            if window in self._windows else None)

    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """主窗口关闭时收掉所有子窗口。"""
        for window in list(self._windows):
            try:
                window.close()
            except Exception:                    # noqa: BLE001
                pass
        self._windows.clear()
