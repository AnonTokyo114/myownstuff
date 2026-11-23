"""ui/widgets/log_pane.py 的回归测试。

只测组件自己的契约：时间戳、行数上限、清空、头部按钮、折叠。
三个设备页面的用法由各自页面验证。
"""

from __future__ import annotations

import os
import re
import unittest

os.environ.setdefault("QT_API", "pyside6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QPushButton
    from ui.widgets.log_pane import MAX_LINES, LogPane
except Exception:                                    # noqa: BLE001
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 not available")
class TestLogPane(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.pane = LogPane()
        # isVisible() 对没 show() 过的控件恒为 False，折叠测试需要它
        self.pane.show()
        self.app.processEvents()

    def tearDown(self):
        self.pane.deleteLater()

    # ------------------------------------------------------------------
    def test_timestamp_prefix(self):
        self.pane.append("hello")
        line = self.pane.text().strip()
        self.assertIsNotNone(re.fullmatch(r"\[\d{2}:\d{2}:\d{2}\] hello", line),
                             line)

    def test_append_keeps_order(self):
        for i in range(5):
            self.pane.append(f"line {i}")
        lines = self.pane.text().strip().splitlines()
        self.assertEqual(len(lines), 5)
        for i, line in enumerate(lines):
            self.assertTrue(line.endswith(f"line {i}"), line)

    def test_limit_is_3000(self):
        self.assertEqual(MAX_LINES, 3000)
        self.assertEqual(self.pane.view.maximumBlockCount(), MAX_LINES)

    def test_clear(self):
        self.pane.append("x")
        self.assertNotEqual(self.pane.text().strip(), "")
        self.pane.clear()
        self.assertEqual(self.pane.text().strip(), "")

    def test_clear_button_is_wired(self):
        self.pane.append("x")
        self.pane.clear_btn.click()
        self.assertEqual(self.pane.text().strip(), "")

    # ------------------------------------------------------------------
    def test_fixed_height(self):
        fixed = LogPane(height=110)
        try:
            self.assertEqual(fixed.view.maximumHeight(), 110)
            self.assertEqual(fixed.view.minimumHeight(), 110)
        finally:
            fixed.deleteLater()

    def test_stretchable_by_default(self):
        # 不传 height 时不限制最大高度，由布局决定
        self.assertEqual(self.pane.view.minimumHeight(), 0)
        self.assertEqual(self.pane.view.maximumHeight(), 16777215)

    # ------------------------------------------------------------------
    def test_header_button_goes_before_clear(self):
        extra = QPushButton("Export")
        self.pane.add_header_button(extra)
        layout = self.pane.header
        self.assertEqual(layout.itemAt(layout.count() - 1).widget(),
                         self.pane.clear_btn,
                         "Clear 应该始终是最后一个")

    def test_body_visible_keeps_header(self):
        """折叠只藏正文 —— 否则「Show log」按钮会跟着消失。"""
        self.pane.set_body_visible(False)
        self.assertFalse(self.pane.body_visible())
        self.assertFalse(self.pane.clear_btn.isHidden(),
                         "Clear 按钮不应跟着隐藏")
        self.pane.set_body_visible(True)
        self.assertTrue(self.pane.body_visible())

    def test_body_content_survives_collapse(self):
        self.pane.append("keep me")
        self.pane.set_body_visible(False)
        self.assertIn("keep me", self.pane.text())


if __name__ == "__main__":
    unittest.main()
