"""统一的日志区：时间戳 + 行数上限 + 清空。

三个设备页面（Stage / DAQ / FilterWheel）原本各写了一遍同样的东西：
只读文本框、`setMaximumBlockCount(3000)`、`[HH:MM:SS] ...` 前缀、
一个 Clear 按钮。这里收成一处。

各页面的**额外**头部按钮（Stage 的导出、DAQ 的折叠）通过
`add_header_button()` 插进来，所以那些功能不受影响。
"""

from __future__ import annotations

import time

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

#: 日志视图保留的最大行数。长采集时日志会无限增长，
#: 完整日志应该落盘，界面上留个窗口就够。
MAX_LINES = 3000


class LogPane(QWidget):
    """「Log」标题 + 可选按钮 + 只读文本框。

    参数
    ----
    height
        正数表示固定高度（DAQ 用 110）；``None`` 表示跟随布局伸展
        （Stage / FilterWheel 的日志是填满右侧那一栏的）。
    """

    def __init__(self, *, height: int | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self.header = QHBoxLayout()
        self.header.setSpacing(6)
        title = QLabel("Log")
        title.setObjectName("fieldLabel")
        self.header.addWidget(title)
        self.header.addStretch(1)

        #: 清空按钮。想改文案就 setText，想禁用就 setEnabled。
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("ghost")
        self.clear_btn.setFixedWidth(70)
        self.header.addWidget(self.clear_btn)
        outer.addLayout(self.header)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setObjectName("numericBox")
        self.view.setMaximumBlockCount(MAX_LINES)
        if height is not None:
            self.view.setFixedHeight(height)
        outer.addWidget(self.view, 1)

        self.clear_btn.clicked.connect(self.clear)

    # ------------------------------------------------------------------
    def add_header_button(self, button: QWidget) -> None:
        """在 Clear **之前**插一个按钮（导出、折叠之类）。"""
        self.header.insertWidget(self.header.count() - 1, button)

    def append(self, text: str) -> None:
        """追加一行，自动加 ``[HH:MM:SS]`` 前缀。"""
        stamp = time.strftime("%H:%M:%S")
        self.view.appendPlainText(f"[{stamp}] {text}")

    def clear(self) -> None:
        self.view.clear()

    def text(self) -> str:
        """当前全部日志文本（导出用）。"""
        return self.view.toPlainText()

    def set_body_visible(self, visible: bool) -> None:
        """只显示/隐藏正文，头部按钮保持可见。

        DAQ 页用这个把日志收起来给实时曲线腾高度 —— 如果连头部一起
        隐藏，那个「Show log」按钮也会消失，就再也点不回来了。
        """
        self.view.setVisible(visible)

    def body_visible(self) -> bool:
        return self.view.isVisible()
