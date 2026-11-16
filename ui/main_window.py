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
PAGES = [
    ("home", "Home", "🏠", "ui.pages.home_page:HomePage", []),
    ("calculus", "Calculus", "∫", "ui.pages.calculus_page:CalculusPage", []),
    ("formula", "Formula Renderer", "∑", "ui.pages.formula_page:FormulaPage", []),
    ("ray", "Ray Tracer", "◇", "ui.pages.ray_page:RayPage", []),
    ("matrix", "Matrix Calculator", "▦",
     "ui.pages.matrix_page:MatrixCalculatorPage", []),
    ("plotter", "Plotter", "📈", "ui.pages.plotter_page:PlotterPage", []),
    ("device", "Device Interface", "⚙",
     "ui.pages.placeholder_page:create_device_page", [
         ("device.stage", "Stage Controller", "⇔",
          "ui.pages.stage_page:StagePage", []),
         ("device.camera", "Camera", "◉",
          "ui.pages.placeholder_page:create_device_subpage:camera", []),
         ("device.laser", "Laser", "✳",
          "ui.pages.placeholder_page:create_device_subpage:laser", []),
         ("device.scope", "Oscilloscope", "∿",
          "ui.pages.placeholder_page:create_device_subpage:scope", []),
         ("device.daq", "Data Acquisition", "⧉",
          "ui.pages.daq_page:DaqPage", []),
         ("device.others", "Others", "⋯",
          "ui.pages.placeholder_page:create_device_subpage:others", []),
     ]),
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

        hint = QLabel("v0.3 · PySide6")
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
        for key, title, icon, spec, children in PAGES:
            # 不要在这里补前导空格：缩进完全交给 QTreeWidget 的 indentation，
            # 否则父子项左边缘会几乎对齐，看不出层级。
            item = QTreeWidgetItem([f"{icon}   {title}"])
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            item.setSizeHint(0, QSize(0, 36))
            self._tree.addTopLevelItem(item)
            self._items[key] = item
            self._specs[key] = spec
            self._titles[key] = title

            for ckey, ctitle, cicon, cspec, _ in children:
                child = QTreeWidgetItem([f"{cicon}  {ctitle}"])
                child.setData(0, Qt.ItemDataRole.UserRole, ckey)
                child.setSizeHint(0, QSize(0, 30))
                item.addChild(child)
                self._items[ckey] = child
                self._specs[ckey] = cspec
                self._titles[ckey] = ctitle

            if children:
                item.setExpanded(True)

    def _ensure_page(self, key: str) -> QWidget:
        if key in self._pages:
            return self._pages[key]

        spec = self._specs[key]
        parts = spec.split(":")
        module = importlib.import_module(parts[0])
        factory = getattr(module, parts[1])
        page = factory(*parts[2:]) if len(parts) > 2 else factory()

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

        hook = getattr(page, "on_activated", None)
        if callable(hook):
            try:
                hook()
            except Exception as exc:                 # 钩子失败不该拖垮导航
                self._status.showMessage(f"Page initialization error: {exc}")

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
        self.setStyleSheet(
            """
            QWidget {
                font-size: 13px;
                color: #1f2328;
            }
            #sidebar {
                background: #f7f8fa;
                border-right: 1px solid #e3e6ea;
            }
            #nav {
                background: transparent;
                outline: 0;
                border: 0;
            }
            #nav::item {
                border-radius: 6px;
                padding-left: 2px;
                color: #3a424d;
            }
            #nav::item:selected {
                background: #e4edfb;
                color: #1b4fa8;
                font-weight: 600;
            }
            #nav::item:hover:!selected {
                background: #eef1f5;
            }
            /* 这里刻意不写 ::branch 规则：
               一旦设 background 或 image，Qt 就不再画默认的展开箭头了。 */
            #hint {
                color: #9aa3ae;
                font-size: 11px;
            }
            #pageTitle {
                font-size: 21px;
                font-weight: 700;
                color: #14181d;
            }
            #pageSubtitle {
                color: #6b7480;
                font-size: 12px;
            }
            #fieldLabel {
                color: #47505b;
                font-size: 12px;
                font-weight: 600;
            }
            #formBox {
                background: #f7f8fa;
                border: 1px solid #e3e6ea;
                border-radius: 8px;
            }
            #latexBox {
                background: #ffffff;
                border: 1px solid #e3e6ea;
                border-radius: 8px;
            }
            #codeField, #matrixResult {
                font-family: Consolas, "Cascadia Mono", monospace;
                background: #f2f4f7;
                border: 1px solid #e3e6ea;
                border-radius: 5px;
                padding: 4px 6px;
                color: #2b3138;
            }
            #matrixResult {
                font-size: 15px;
                background: #ffffff;
                border: 0;
            }
            #numericBox {
                font-family: Consolas, "Cascadia Mono", monospace;
                background: #fbfcfd;
                border: 1px solid #e3e6ea;
                border-radius: 6px;
                color: #2b3138;
            }
            #mirrorish {
                color: #33404d;
                background: #f2f6fb;
                border: 1px solid #dde6f2;
                border-radius: 6px;
                padding: 7px 9px;
                font-size: 12px;
            }
            #exprField, #codeInput {
                font-family: Consolas, "Cascadia Mono", monospace;
            }
            /* ---------- 首页 ---------- */
            #homeTitle {
                font-size: 30px;
                font-weight: 700;
                color: #12202f;
            }
            #homeSubtitle {
                font-size: 13px;
                color: #6b7480;
            }
            #homeFooter {
                font-size: 11px;
                color: #9aa3ae;
            }
            /* ---------- 卡片 ---------- */
            #navCard, #planCard {
                background: #ffffff;
                border: 1px solid #e3e6ea;
                border-radius: 10px;
            }
            #navCard[hovered="true"] {
                background: #f4f8fe;
                border: 1px solid #9dbfe8;
            }
            #cardTitle {
                font-size: 14px;
                font-weight: 600;
                color: #1b3a5c;
            }
            #cardDesc {
                font-size: 11px;
                color: #6b7480;
            }
            /* ---------- 提示框 ---------- */
            #noticeBox {
                background: #fff8e6;
                border: 1px solid #f0dfae;
                border-radius: 8px;
            }
            #noticeTitle {
                font-size: 13px;
                font-weight: 600;
                color: #7a5a12;
            }
            #noticeBody {
                font-size: 12px;
                color: #6d5a2c;
            }
            /* ---------- 安全警示横幅 ---------- */
            #dangerBanner {
                background: #fdecea;
                border: 1px solid #f3b8b2;
                border-radius: 8px;
            }
            #dangerBannerIcon {
                font-size: 20px;
                color: #b3261e;
            }
            #dangerBannerText {
                font-size: 12px;
                color: #7a1c16;
                font-weight: 500;
            }
            /* ---------- 通用控件 ---------- */
            QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {
                background: #ffffff;
                border: 1px solid #d5dae1;
                border-radius: 5px;
                padding: 4px 6px;
                selection-background-color: #cfe0fb;
            }
            QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
                border: 1px solid #4c86d6;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #d5dae1;
                border-radius: 6px;
                padding: 5px 12px;
                color: #2b3138;
            }
            QPushButton:hover {
                background: #f2f5f9;
            }
            QPushButton#primary {
                background: #2f6fb0;
                border: 1px solid #2f6fb0;
                color: #ffffff;
                font-weight: 600;
            }
            QPushButton#primary:hover {
                background: #2a63a0;
            }
            QPushButton#ghost {
                background: transparent;
                border: 1px solid #d5dae1;
                color: #47505b;
            }
            QPushButton#ghost:hover {
                background: #f2f5f9;
            }
            QPushButton#danger {
                background: #b3261e;
                border: 1px solid #b3261e;
                color: #ffffff;
                font-weight: 700;
            }
            QPushButton#danger:hover {
                background: #9a2019;
            }
            QPushButton#danger:disabled {
                background: #e6c9c6;
                border: 1px solid #e6c9c6;
                color: #ffffff;
            }
            #monoBig {
                font-family: Consolas, "Cascadia Mono", monospace;
                font-size: 19px;
                font-weight: 600;
                color: #12202f;
            }
            QTableWidget {
                background: #ffffff;
                border: 1px solid #e3e6ea;
                border-radius: 6px;
                gridline-color: #eef1f5;
                selection-background-color: #dce9fa;
                selection-color: #14181d;
            }
            QHeaderView::section {
                background: #f4f6f9;
                border: 0;
                border-bottom: 1px solid #e3e6ea;
                padding: 5px 6px;
                color: #47505b;
                font-weight: 600;
            }
            QTabWidget::pane {
                border: 1px solid #e3e6ea;
                border-radius: 8px;
                background: #ffffff;
                top: -1px;
            }
            QTabBar::tab {
                background: transparent;
                padding: 7px 18px;
                margin-right: 2px;
                color: #5b6470;
                border: 1px solid transparent;
                border-top-left-radius: 7px;
                border-top-right-radius: 7px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                border: 1px solid #e3e6ea;
                border-bottom-color: #ffffff;
                color: #1b4fa8;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected {
                color: #2b3138;
            }
            QStatusBar {
                color: #5b6470;
            }
            QSplitter::handle {
                background: #e3e6ea;
            }
            """
        )
