"""全局样式表。

抽出来的原因：QSS 原先只设置在 MainWindow 上，独立运行的设备 GUI
（`python -m devicegui.stage_app`）根本没有样式，HALT / Stop 这类
危险按钮会退化成普通灰按钮。现在主程序、独立设备入口、单文件版
都调用同一个 apply_theme()。
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

#: 整个应用的样式表
STYLESHEET = """\

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
            /* ---------- Device Interface 入口卡片 ---------- */
            #hubIcon {
                font-size: 17px;
                color: #2b5ea8;
            }
            #hubTitle {
                font-size: 14px;
                font-weight: 600;
                color: #1b1f24;
            }
            #hubTag {
                font-size: 10px;
                color: #8a6d1f;
                background: #fdf6e3;
                border: 1px solid #efe0b8;
                border-radius: 8px;
                padding: 1px 7px;
            }
            #hubCommand {
                font-family: Consolas, "Cascadia Mono", monospace;
                font-size: 11px;
                color: #5b6470;
                background: #f4f6f9;
                border: 1px solid #e3e6ea;
                border-radius: 5px;
                padding: 3px 7px;
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


def apply_theme(widget: QWidget) -> None:
    """把统一样式应用到某个窗口（主窗口或独立设备窗口）。"""
    widget.setStyleSheet(STYLESHEET)
