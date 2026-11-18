"""滤光片轮槽位设置的异常路径回归。

两种「命令发出去了，但设备状态没确认」的情况：

  #1 set_wheel_type 成功、随后的 snapshot 读失败
     -> 必须把缓存里的 pcount 丢掉，否则下次改回原值会被
        "already configured" 跳过，而设备早已不是那个值了

  #2 snapshot 读成功，但回读的 pcount 不等于目标
     -> 不能报 "set to N slots"，要给出请求值 / 实际值

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_filterwheel_page -v
"""

import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_API", "pyside6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
    QT_AVAILABLE = True
except Exception:                                    # noqa: BLE001
    QT_AVAILABLE = False

STATE_6 = {"pcount": 6, "position": 1, "trig": 1, "speed": 1, "sensors": 0}


class FlakyDevice:
    """set_wheel_type 生效，但 snapshot 抛错。"""

    def __init__(self):
        self.pcount = 6
        self.calls = []
        self.is_connected = True

    def set_wheel_type(self, n):
        self.pcount = n
        self.calls.append(n)

    def snapshot(self):
        raise RuntimeError("simulated serial timeout while reading state")

    def get_wheel_type(self):
        return self.pcount


class IgnoringDevice:
    """接受 pcount=N 命令，但设备不采纳。"""

    def __init__(self):
        self.pcount = 6
        self.calls = []
        self.is_connected = True

    def set_wheel_type(self, n):
        self.calls.append(n)

    def snapshot(self):
        return dict(STATE_6)


@unittest.skipUnless(QT_AVAILABLE, "PySide6 not available")
class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def pump(self, seconds=8.0, until=None):
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.03)
        return until() if until is not None else False

    def make_page(self, device):
        from ui.pages.filterwheel_page import FilterWheelPage
        page = FilterWheelPage()
        page.selector._device = device
        page._state = dict(STATE_6)
        return page


class TestSlotsAfterReadFailure(_Base):
    """#1：写入成功但读回失败，缓存不能继续被相信。"""

    def test_cache_cleared_and_retry_sends_command(self):
        device = FlakyDevice()
        page = self.make_page(device)

        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes), \
             patch.object(QMessageBox, "warning",
                          return_value=QMessageBox.StandardButton.Ok) as warn:
            page.slots_box.setCurrentIndex(page.slots_box.findData(12))
            page.set_slots()
            self.pump(until=lambda: not page._busy)

        self.assertIn(12, device.calls)
        self.assertEqual(device.pcount, 12, "set_wheel_type 应该已经生效")
        self.assertIsNone(page._state.get("pcount"),
                          "读回失败后不能继续相信旧的 pcount")
        self.assertEqual(warn.call_count, 1, "应该提示状态未知")

        # 再请求改回 6：不能被旧缓存当成「已经配置」
        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes), \
             patch.object(QMessageBox, "warning",
                          return_value=QMessageBox.StandardButton.Ok):
            page.slots_box.setCurrentIndex(page.slots_box.findData(6))
            page.set_slots()
            self.pump(until=lambda: not page._busy)

        self.assertIn(6, device.calls, "改回原值必须真的发命令")
        self.assertNotIn(
            "already configured", page.log_view.toPlainText().lower(),
            "不能因为旧缓存跳过写入")


class TestSlotsNotApplied(_Base):
    """#2：回读值不等于目标时不能报成功。"""

    def test_mismatch_is_reported(self):
        device = IgnoringDevice()
        page = self.make_page(device)

        with patch.object(QMessageBox, "question",
                          return_value=QMessageBox.StandardButton.Yes), \
             patch.object(QMessageBox, "warning",
                          return_value=QMessageBox.StandardButton.Ok) as warn:
            page.slots_box.setCurrentIndex(page.slots_box.findData(12))
            page.set_slots()
            self.pump(until=lambda: not page._busy)

        self.assertIn(12, device.calls)
        self.assertEqual(warn.call_count, 1, "回读不一致要报警")

        log = page.log_view.toPlainText()
        self.assertIn("not applied", log)
        self.assertNotIn("Wheel type set to 12 slots", log,
                         "没生效就不能说设置成功")
        # 界面按实际回读值显示
        self.assertEqual(page.slots_box.currentData(), 6)


if __name__ == "__main__":
    unittest.main()
