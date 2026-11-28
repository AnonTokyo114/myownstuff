"""首页：标题 + 各功能入口卡片。

首页不是导航栏的一项，而是启动时的默认视图；点击卡片等同于点左侧菜单。
用 Signal 把跳转请求交给 MainWindow，页面自己不认识导航控件。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from ui.version import HOME_FOOTER


class NavCard(QFrame):
    """可点击的功能卡片：图标 + 标题 + 一句说明。"""

    clicked = Signal(str)

    def __init__(self, key: str, icon: str, title: str, desc: str) -> None:
        super().__init__()
        self._key = key
        self.setObjectName("navCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 用最小高度而不是固定高度：窄窗口里说明可能换到 4~5 行，
        # 固定 104 会把文字压扁（实测 1024 宽时 Device 那条需要 98px，
        # 只给到 59px）。QGridLayout 会让同一行的卡片取相同高度。
        self.setMinimumHeight(104)
        self.setMinimumWidth(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)

        head = QLabel(f"{icon}  {title}")
        head.setObjectName("cardTitle")
        layout.addWidget(head)

        body = QLabel(desc)
        body.setObjectName("cardDesc")
        body.setWordWrap(True)
        layout.addWidget(body)

    def mousePressEvent(self, event) -> None:      # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._key)
        super().mousePressEvent(event)

    def enterEvent(self, event) -> None:           # noqa: N802
        self.setProperty("hovered", True)
        self.style().unpolish(self)
        self.style().polish(self)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:           # noqa: N802
        self.setProperty("hovered", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().leaveEvent(event)


class HomePage(QWidget):
    page_title = "Home"

    #: 请求跳转到某个页面（参数是 PAGES 里的 key）
    navigate = Signal(str)

    def __init__(self) -> None:
        super().__init__()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 44, 40, 32)
        outer.setSpacing(6)

        title = QLabel("Personal Research GUI and Calculators")
        title.setObjectName("homeTitle")
        title.setWordWrap(True)
        outer.addWidget(title)

        subtitle = QLabel(
            "A local-first toolkit for optics, calculus and linear algebra. "
            "Everything runs on this machine — no network, no accounts."
        )
        subtitle.setObjectName("homeSubtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        outer.addSpacing(24)

        grid = QGridLayout()
        grid.setSpacing(14)
        grid.setContentsMargins(0, 0, 0, 0)

        cards = [
            ("calculus", "∫", "Calculus",
             "Definite / indefinite integrals, derivatives, partial derivatives "
             "and Taylor expansions — numeric and symbolic, with LaTeX output."),
            ("formula", "∑", "Formula Renderer",
             "Turn LaTeX source into a transparent PNG for slides and reports."),
            ("optics", "◇", "Optics",
             "Three optical models in separate windows: ideal thin lens "
             "(quick layout arithmetic), surface ray tracing (EFL/BFL, spot "
             "size, best focus, spherical aberration), and Fourier optics "
             "(planned)."),
            ("matrix", "▦", "Matrix Calculator",
             "Eigen-decomposition, matrix arithmetic, 1D eigenfunctions and "
             "parameter sweeps — numeric and symbolic."),
            ("plotter", "📈", "Plotter",
             "Load a CSV/TSV and plot its columns: line, scatter, bar, step or "
             "stem, single or stacked subplots."),
            ("device", "⚙", "Device Interface",
             "Launch the laboratory instruments. Stage (ASI LX-4000), filter "
             "wheel (Thorlabs FW102C) and NI-DAQ (analog outputs, photon "
             "counting, pulse generator) are implemented; camera, laser and "
             "oscilloscope are placeholders. Each opens in its own window and "
             "can also run standalone from a terminal."),
        ]

        for index, (key, icon, name, desc) in enumerate(cards):
            card = NavCard(key, icon, name, desc)
            card.clicked.connect(self.navigate.emit)
            grid.addWidget(card, index // 3, index % 3)

        outer.addLayout(grid)
        outer.addStretch(1)

        footer = QLabel(HOME_FOOTER)
        footer.setObjectName("homeFooter")
        outer.addWidget(footer)
