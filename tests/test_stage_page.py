"""Stage 页面的位置读取（DeviceTaskSession 迁移后）。

只测读取路径：运动、归零、HALT 保持原实现，不在本轮范围内。
"""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_API", "pyside6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
except Exception:                                    # noqa: BLE001
    QApplication = None


class FakeStage:
    """可控位移台：位置固定，或抛指定异常。"""

    UNIT_LABEL = "um"

    def __init__(self, x=10.0, y=20.0, error=None):
        self._x = x
        self._y = y
        self._error = error
        self.reads = 0

    def get_position(self, axis):
        self.reads += 1
        if self._error is not None:
            raise RuntimeError(self._error)
        return self._x if axis == "x" else self._y


@unittest.skipIf(QApplication is None, "PySide6 not available")
class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_page(self, device):
        from ui.pages.stage_page import StagePage
        page = StagePage()
        page.show()
        self.app.processEvents()
        page.selector._device = device
        page._session += 1
        page._busy = False
        return page

    def pump(self, seconds=5.0, until=None):
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.01)
        return until() if until is not None else False

    def capture(self):
        """拦下 run_async，手动决定何时完成。"""
        pending = []

        def fake(work, done, failed):
            pending.append((work, done, failed))

        return pending, fake


class TestRefreshPosition(_Base):
    def test_normal_read(self):
        device = FakeStage(x=120, y=340)
        page = self.make_page(device)
        try:
            page.refresh_position()
            self.assertTrue(page._busy, "提交后应进入忙碌")
            self.pump(until=lambda: not page._busy)
            self.assertFalse(page._busy)
            self.assertEqual(page.x_value.text(), "120")
            self.assertEqual(page.y_value.text(), "340")
            self.assertEqual(device.reads, 2)
        finally:
            page.cleanup()

    def test_failed_read_restores_ui(self):
        device = FakeStage(error="review: stage not responding")
        page = self.make_page(device)
        try:
            page.refresh_position()
            self.pump(until=lambda: not page._busy)
            self.assertFalse(page._busy, "失败后也要退出忙碌")
            log = page.log.text()
            self.assertIn("Read position failed", log)
            self.assertIn("not responding", log)
            self.assertTrue(page.refresh_pos_btn.isEnabled())
        finally:
            page.cleanup()

    def test_stale_result_after_reconnect_is_dropped(self):
        old = FakeStage(x=111, y=222)
        page = self.make_page(old)
        try:
            pending, fake = self.capture()
            with patch("ui.workers.run_async", fake):
                page.refresh_position()
            work, done, failed = pending[0]
            value = work()

            # 断开 -> 重连到另一台
            page._session += 1
            page.selector._device = FakeStage(x=777, y=888)
            page._session += 1
            page._busy = False

            before = (page.x_value.text(), page.y_value.text())
            done(value)
            self.app.processEvents()
            self.assertEqual((page.x_value.text(), page.y_value.text()),
                             before, "旧结果写进了坐标显示")
            self.assertFalse(page._busy)

            # 新会话读取正常
            pending.clear()
            with patch("ui.workers.run_async", fake):
                page.refresh_position()
            w2, d2, _f = pending[0]
            d2(w2())
            self.assertEqual(page.x_value.text(), "777")
        finally:
            page.cleanup()

    def test_late_callback_after_close_is_dropped(self):
        device = FakeStage(x=5, y=6)
        page = self.make_page(device)
        pending, fake = self.capture()
        with patch("ui.workers.run_async", fake):
            page.refresh_position()
        work, done, failed = pending[0]
        value = work()

        page.cleanup()
        page.selector._device = None
        page._on_disconnected()
        self.app.processEvents()
        before = (page.x_value.text(), page.y_value.text())

        done(value)
        failed("late boom")
        self.app.processEvents()

        self.assertEqual((page.x_value.text(), page.y_value.text()), before,
                         "关窗后旧结果写进了控件")
        self.assertFalse(page._busy)

    def test_queued_work_does_not_run_after_disconnect(self):
        device = FakeStage()
        page = self.make_page(device)
        try:
            pending, fake = self.capture()
            with patch("ui.workers.run_async", fake):
                page.refresh_position()
            work, done, _f = pending[0]

            page._session += 1
            page.selector._device = None
            page._busy = False

            result = work()
            self.assertEqual(device.reads, 0, "过期任务仍然读了设备")
            done(result)
            self.assertNotEqual(page.x_value.text(), "10")
        finally:
            page.cleanup()

    def test_auto_refresh_after_connect_uses_session(self):
        device = FakeStage(x=99, y=88)
        page = self.make_page(device)
        try:
            page.x_value.setText("—")
            page._busy = False
            page._on_connected(device)
            self.assertTrue(page._busy, "连接后应自动刷新")
            self.pump(until=lambda: not page._busy)
            self.assertEqual(page.x_value.text(), "99")
        finally:
            page.cleanup()


if __name__ == "__main__":
    unittest.main()
