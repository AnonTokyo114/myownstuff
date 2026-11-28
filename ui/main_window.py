"""主窗口：左侧树形导航 + 右侧页面堆栈。

首页（Home）是启动时的默认视图，同时也是导航的第一项，方便从别的页面回来。
HomePage 通过 `navigate` 信号请求跳转，主窗口负责把它翻译成"选中哪一项"。

导航用 QTreeWidget 而不是 QListWidget：Device Interface 下面有 6 个子分类，
需要可折叠的层级。子项同样是懒加载页面。
"""

from __future__ import annotations

import importlib
import logging

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont
from ui.version import SIDEBAR_HINT
from ui.theme import apply_theme
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

#: (键, 显示名, 图标, 构造函数说明, 子项列表)
#: 构造函数说明格式 "模块路径:类名或工厂[工厂参数]"——
#:   "ui.pages.calculus_page:CalculusPage"                 无参类
#:   "ui.pages.placeholder_page:create_device_subpage:stage"  带参数的工厂
#: 子项列表目前都为空：设备不再是内嵌子页，而是由 Device Interface
#: 页面启动的独立 GUI（见 devicegui/）。
#: (key, 标题, 图标, "模块:类") —— 全部是平级页面，没有子菜单。
PAGES = [
    ("home", "Home", "🏠", "ui.pages.home_page:HomePage"),
    ("calculus", "Calculus", "∫", "ui.pages.calculus_page:CalculusPage"),
    ("formula", "Formula Renderer", "∑", "ui.pages.formula_page:FormulaPage"),
    ("optics", "Optics", "◇", "ui.pages.optics_hub_page:OpticsHubPage"),
    ("matrix", "Matrix Calculator", "▦",
     "ui.pages.matrix_page:MatrixCalculatorPage"),
    ("plotter", "Plotter", "📈", "ui.pages.plotter_page:PlotterPage"),
    # 设备不再是内嵌子页：这里只是分类入口，点 Launch 会启动独立的设备 GUI
    ("device", "Device Interface", "⚙",
     "ui.pages.device_hub_page:DeviceHubPage"),
]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Personal Research GUI and Calculators")
        self.resize(1320, 860)

        self._pages: dict[str, QWidget] = {}
        self._specs: dict[str, str] = {}
        self._titles: dict[str, str] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        self._stack = QStackedWidget()
        self._status = QStatusBar()

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ---------------- 侧栏 ----------------
        sidebar = QWidget()
        sidebar.setFixedWidth(228)
        sidebar.setObjectName("sidebar")
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(10, 14, 10, 14)
        side_layout.setSpacing(8)

        brand = QLabel("Personal\nResearch")
        brand_font = QFont()
        brand_font.setPointSize(13)
        brand_font.setBold(True)
        brand.setFont(brand_font)
        brand.setContentsMargins(6, 0, 0, 10)
        side_layout.addWidget(brand)

        self._tree = QTreeWidget()
        self._tree.setObjectName("nav")
        self._tree.setHeaderHidden(True)
        self._tree.setFrameShape(QFrame.Shape.NoFrame)
        self._tree.setIndentation(16)
        self._tree.setExpandsOnDoubleClick(False)
        self._tree.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._build_tree()
        side_layout.addWidget(self._tree, 1)

        hint = QLabel(SIDEBAR_HINT)
        hint.setObjectName("hint")
        side_layout.addWidget(hint)

        layout.addWidget(sidebar)
        layout.addWidget(self._stack, 1)
        self.setCentralWidget(central)
        self.setStatusBar(self._status)

        self._tree.currentItemChanged.connect(self._on_item_changed)
        self._apply_style()

        # 启动落在首页
        self._tree.setCurrentItem(self._items["home"])
        self._status.showMessage("Ready")

    # ------------------------------------------------------------------ 导航
    def _build_tree(self) -> None:
        for key, title, icon, spec in PAGES:
            item = QTreeWidgetItem([f"{icon}   {title}"])
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(0, QSize(0, 36))
            self._tree.addTopLevelItem(item)
            self._items[key] = item
            self._specs[key] = spec
            self._titles[key] = title

    def _ensure_page(self, key: str) -> QWidget:
        if key in self._pages:
            return self._pages[key]

        module_name, _, class_name = self._specs[key].partition(":")
        module = importlib.import_module(module_name)
        page = getattr(module, class_name)()

        # 首页的卡片点击 -> 切换导航
        navigate = getattr(page, "navigate", None)
        if navigate is not None:
            navigate.connect(self.goto_key)

        self._pages[key] = page
        self._stack.addWidget(page)
        return page

    def goto_key(self, key: str) -> None:
        """按页面键选中对应导航项（首页卡片用）。"""
        item = self._items.get(key)
        if item is not None:
            self._tree.setCurrentItem(item)

    def _on_item_changed(self, current: QTreeWidgetItem | None,
                         _previous: QTreeWidgetItem | None) -> None:
        if current is None:
            return
        key = current.data(0, Qt.ItemDataRole.UserRole)
        if not key:
            return
        page = self._ensure_page(key)
        self._stack.setCurrentWidget(page)
        self._status.showMessage(f"Current: {self._titles.get(key, key)}")

    # ------------------------------------------------------------------ 退出
    def closeEvent(self, event) -> None:      # noqa: N802
        """关闭主窗口前，统一清理所有已构造页面的设备。

        子页面放在 QStackedWidget 里，主窗口关闭**不会**触发它们的
        closeEvent，所以必须在这里主动收尾：DAQ 归零、位移台停止、
        串口/网口释放。未创建过的页面（懒加载）没有设备，自然跳过。

        清理失败只记录，不阻止退出 —— 否则窗口关不掉。
        """
        for key, page in list(self._pages.items()):
            cleanup = getattr(page, "cleanup", None)
            if not callable(cleanup):
                continue
            try:
                cleanup()
            except Exception as exc:                 # noqa: BLE001
                logging.getLogger("research").warning(
                    "cleanup failed for page %r: %s", key, exc)
        super().closeEvent(event)

    # ------------------------------------------------------------------ 样式
    def _apply_style(self) -> None:
        apply_theme(self)
