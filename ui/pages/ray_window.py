"""把现有的 RayPage 包成一个独立窗口。

仓库里已经有 ``devicegui`` 那套"每个工具一个独立窗口"的模式；
光学工具复用同样的做法，只是这里不需要独立进程 —— 计算是毫秒到
百毫秒级，放进后台线程反而增加复杂度。

窗口关闭时调用页面的 ``cleanup``（如果有），保证和主窗口里的行为一致。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from ui.theme import apply_theme


class _ToolWindow(QMainWindow):
    """所有光学工具窗口的公共外壳：标题、尺寸、主题、清理。"""

    def __init__(self, page, title: str, size: tuple[int, int],
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(*size)
        self._page = page
        self.setCentralWidget(page)
        # 关闭即销毁。默认 close() 只隐藏，hub 的列表会一直累积，
        # 每次重开都新建一整套窗口和画布。
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        apply_theme(self)

    def closeEvent(self, event) -> None:      # noqa: N802 - Qt 命名
        cleanup = getattr(self._page, "cleanup", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:                    # noqa: BLE001
                pass
        super().closeEvent(event)


class SurfaceWindow(_ToolWindow):
    """曲面追迹（现有的 RayPage）。"""

    def __init__(self, parent=None) -> None:
        from ui.pages.ray_page import RayPage
        super().__init__(RayPage(),
                         "Surface Ray Tracer — Personal Research",
                         (1180, 820), parent)


class ThinLensWindow(_ToolWindow):
    """理想薄透镜（core.optics.thin）。"""

    def __init__(self, parent=None) -> None:
        from ui.pages.ray_thin_page import ThinLensPage
        super().__init__(ThinLensPage(),
                         "Paraxial / Thin Lens — Personal Research",
                         (1120, 760), parent)
