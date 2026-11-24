"""脚手架**生成出来的页面**的行为测试。

test_new_device.py 查的是模板源码字符串；这里真的生成一个页面、接上假设备、
驱动它的读取路径，验证 DeviceTaskSession 的会话边界确实生效。
"""

from __future__ import annotations

import io
import os
import sys
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_API", "pyside6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

try:
    from PySide6.QtWidgets import QApplication
except Exception:                                    # noqa: BLE001
    QApplication = None

KIND = "probe_gen_behaviour"
CLASS = "ProbeGenBehaviour"


def _remove_generated():
    for name in (f"core/{KIND}.py", f"ui/pages/{KIND}_page.py",
                 f"devicegui/{KIND}_app.py"):
        path = os.path.join(ROOT, name)
        if os.path.exists(path):
            os.remove(path)


class FakeDevice:
    def __init__(self, value=1.0, error=None, delay=0.0):
        self._value = value
        self._error = error
        self._delay = delay
        self.reads = 0

    def read_reading(self):
        self.reads += 1
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise RuntimeError(self._error)
        return self._value


@unittest.skipIf(QApplication is None, "PySide6 not available")
class TestGeneratedPageBehaviour(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        import new_device as nd

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = nd.main([KIND, "--name", "Probe Gen Behaviour",
                            "--class-name", CLASS, "--method", "reading"])
        assert code == 0, buf.getvalue()
        import importlib
        cls.module = importlib.import_module(f"ui.pages.{KIND}_page")

    @classmethod
    def tearDownClass(cls):
        _remove_generated()

    # ------------------------------------------------------------------
    def make_page(self, device):
        page = self.module.ProbeGenBehaviourPage()
        page.show()
        self.app.processEvents()
        page.selector._device = device
        page._on_connected(device)
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
        pending = []

        def fake(work, done, failed):
            pending.append((work, done, failed))

        return pending, fake

    # ------------------------------------------------------------------
    def test_generated_page_really_uses_the_guard(self):
        page = self.make_page(FakeDevice())
        try:
            self.assertTrue(hasattr(page, "_tasks"))
            self.assertTrue(hasattr(page, "_session"))
            self.assertFalse(hasattr(page, "_generation"))
            self.assertFalse(hasattr(page, "_bound"))
            self.assertIsNotNone(page.device)
        finally:
            page.cleanup()

    def test_normal_read(self):
        device = FakeDevice(2.5)
        page = self.make_page(device)
        no_modal = patch.object(
            self.module.QMessageBox, "critical", return_value=None)
        try:
            with no_modal:
                page.read_value()
                self.assertTrue(page._busy)
                self.pump(until=lambda: not page._busy)
            self.assertFalse(page._busy)
            self.assertEqual(page.reading.text(), "2.5")
            self.assertEqual(device.reads, 1)
        finally:
            page.cleanup()

    def test_failed_read_restores_and_reports(self):
        device = FakeDevice(error="review: read timeout")
        page = self.make_page(device)
        no_modal = patch.object(
            self.module.QMessageBox, "critical", return_value=None)
        try:
            with no_modal:
                page.read_value()
                self.pump(until=lambda: not page._busy)
            self.assertFalse(page._busy)
            self.assertIn("timeout", page.status_label.text())
            self.assertIn("timeout", page.log.text())
            self.assertTrue(page.read_btn.isEnabled())
        finally:
            page.cleanup()

    def test_stale_result_after_reconnect_is_dropped(self):
        old = FakeDevice(111.0)
        page = self.make_page(old)
        no_modal = patch.object(
            self.module.QMessageBox, "critical", return_value=None)
        try:
            with no_modal:
                pending, fake = self.capture()
                with patch("ui.workers.run_async", fake):
                    page.read_value()
                work, done, failed = pending[0]
                value = work()

                # 走真实的断开/重连钩子
                page._on_disconnected()
                page.selector._device = FakeDevice(777.0)
                page._on_connected(page.selector._device)
                page._busy = False

                before = page.reading.text()
                done(value)
                failed("late")
                self.app.processEvents()
                self.assertEqual(page.reading.text(), before,
                                 "旧结果写进了读数")
                self.assertFalse(page._busy)

                pending.clear()
                with patch("ui.workers.run_async", fake):
                    page.read_value()
                w2, d2, _f = pending[0]
                d2(w2())
                self.assertEqual(page.reading.text(), "777")
        finally:
            page.cleanup()

    def test_late_callback_after_close_is_dropped(self):
        page = self.make_page(FakeDevice(9.0))
        pending, fake = self.capture()
        with patch("ui.workers.run_async", fake):
            page.read_value()
        work, done, failed = pending[0]
        value = work()

        page.cleanup()
        page.selector._device = None
        page._on_disconnected()
        self.app.processEvents()
        before = page.reading.text()

        done(value)
        failed("late boom")
        self.app.processEvents()

        self.assertEqual(page.reading.text(), before,
                         "关窗后旧结果写进了控件")
        self.assertFalse(page._busy)

    def test_queued_work_does_not_run_after_disconnect(self):
        device = FakeDevice()
        page = self.make_page(device)
        try:
            pending, fake = self.capture()
            with patch("ui.workers.run_async", fake):
                page.read_value()
            work, done, _failed = pending[0]

            page._on_disconnected()
            page.selector._device = None

            result = work()
            self.assertEqual(device.reads, 0, "过期任务仍然读了设备")
            done(result)
            self.app.processEvents()
            self.assertEqual(page.reading.text(), "—")
        finally:
            page.cleanup()


if __name__ == "__main__":
    unittest.main()
