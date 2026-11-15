"""Personal Research GUI —— 入口。

注意 Qt 后端：本机同时装了 PyQt5 和 PySide6，matplotlib 的 qt_compat 默认
可能先探到 PyQt5 并与之冲突（历史上出现过 access violation）。必须在导入
任何 Qt / matplotlib 之前锁定 QT_API，并显式使用 QtAgg。
"""

import os
import sys

# 必须在 import PySide6 / matplotlib 之前设置
os.environ["QT_API"] = "pyside6"

import matplotlib

matplotlib.use("QtAgg")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main() -> int:
    # 高 DPI 缩放（Qt6 默认已开启，这里只保证四舍五入策略一致）
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Personal Research")
    app.setOrganizationName("personal-research")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
