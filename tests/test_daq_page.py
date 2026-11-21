"""DAQ 页的回归测试：通道保留、清图基线、断开重连。

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_daq_page -v
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


def setUpModule():
    if not QT_AVAILABLE:
        raise unittest.SkipTest("PySide6 not available")


@unittest.skipUnless(QT_AVAILABLE, "PySide6 not available")
class DaqPageTest(unittest.TestCase):
    """共用一个页面：连接/断开在各用例里自己控制。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from ui.pages.daq_page import DaqPage
        self.page = DaqPage()
        self._placeholder_index = [
            i for i in range(self.page.selector.device_box.count())
            if "Placeholder" in self.page.selector.device_box.itemText(i)
        ][0]

    def tearDown(self):
        try:
            self.page.cleanup()
        finally:
            self.page.deleteLater()
            self.app.processEvents()

    # ------------------------------------------------------------------
    def pump(self, seconds=15.0, until=None):
        deadline = time.time() + seconds
        while time.time() < deadline:
            self.app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.02)
        return until() if until is not None else False

    def connect(self):
        page = self.page
        page.selector.device_box.setCurrentIndex(self._placeholder_index)
        self.app.processEvents()
        seen = {}
        page.selector.connected.connect(lambda d: seen.setdefault("d", d))
        page.selector._on_connect()
        self.pump(20.0, lambda: "d" in seen)
        self.assertIn("d", seen, "Placeholder 没连上")
        return page

    # ------------------------------------------------------------------
    def test_custom_channels_are_preserved(self):
        """连接前填的通道不能被默认值覆盖。

        之前 _device_options 从 DeviceSpec 上取 resource（该字段不存在），
        传进去的是 None，于是 Dev9/ao0 被 ao2 顶掉；第二路更是写死 ao3。
        """
        page = self.page
        page.analog_a.channel_edit.setText("Dev9/ao0")
        page.analog_b.channel_edit.setText("Dev9/ao1")
        self.connect()

        self.assertEqual(page._main_device.channel, "Dev9/ao0")
        self.assertEqual(page.analog_a.channel_edit.text(), "Dev9/ao0")
        self.assertEqual(getattr(page._second_device, "channel", None), "Dev9/ao1")
        self.assertEqual(page.analog_b.channel_edit.text(), "Dev9/ao1")

    def test_blank_channels_get_defaults(self):
        """空着的输入框才补默认值。"""
        page = self.page
        page.analog_a.channel_edit.setText("")
        page.analog_b.channel_edit.setText("")
        self.connect()
        self.assertTrue(page._main_device.channel.endswith("/ao2"),
                        page._main_device.channel)
        self.assertTrue(
            getattr(page._second_device, "channel", "").endswith("/ao3"),
            getattr(page._second_device, "channel", None))

    def test_clear_plot_keeps_baseline(self):
        """运行中清图必须重建基线，不能把绝对 perf_counter 当 elapsed。

        旧实现把 _t0 / _prev_time / _prev_count 全部归零，下一条会算出
        elapsed≈几万秒、rate≈0.36 cps，而且错误基线一路进 CSV。
        """
        page = self.page
        ph = page.photon
        ph.counter_edit.setText("Dev1/ctr0")
        ph.source_edit.setText("/Dev1/PFI0")
        self.connect()

        ph.bin_spin.setValue(0.1)
        ph.start()
        self.pump(15.0, lambda: ph._running)
        self.pump(5.0, lambda: len(ph._log) >= 3)
        self.assertGreaterEqual(len(ph._log), 3)

        ph.clear_plot()
        self.app.processEvents()
        self.assertNotEqual(ph._t0, 0.0, "运行中清图不该把 t0 归零")
        self.assertNotEqual(ph._prev_time, 0.0)

        self.pump(6.0, lambda: len(ph._log) >= 2)
        self.assertGreaterEqual(len(ph._log), 2)
        _ts, elapsed, dt, counts, _rate = ph._log[0]
        self.assertLess(elapsed, 30.0, f"elapsed 应该是相对时间，得到 {elapsed}")
        self.assertLess(dt, 1.0, f"dt 应该是采样间隔，得到 {dt}")
        self.assertLess(counts, 10000, f"counts 应该是增量，得到 {counts}")
        ph.stop()

    def test_disconnect_stops_counting(self):
        """计数运行中断开：定时器要停、运行标志要清。"""
        page = self.page
        ph = page.photon
        self.connect()
        ph.bin_spin.setValue(60.0)
        ph.start()
        self.pump(15.0, lambda: ph._running)
        self.assertTrue(ph._running)
        self.assertTrue(ph._timer.isActive())

        page.selector._on_disconnect()
        self.pump(15.0, lambda: page.selector.device is None)
        self.assertFalse(ph._running, "断开后 _running 还在")
        self.assertFalse(ph._timer.isActive(), "断开后定时器还在跑")
        self.assertIsNone(ph._device)

    def test_reconnect_allows_immediate_start(self):
        """重连后 Start 必须可用，不能因为旧的 _running 一直灰着。"""
        page = self.page
        ph = page.photon
        self.connect()
        ph.bin_spin.setValue(60.0)
        ph.start()
        self.pump(15.0, lambda: ph._running)

        page.selector._on_disconnect()
        self.pump(15.0, lambda: page.selector.device is None)
        self.connect()

        self.assertFalse(ph._running)
        self.assertTrue(ph.start_btn.isEnabled(), "重连后 Start 还是灰的")

        ph.bin_spin.setValue(0.1)
        ph.start()
        self.pump(15.0, lambda: ph._running)
        self.assertTrue(ph._running, "重连后无法开始计数")
        ph.stop()

    def test_dual_outputs_apply(self):
        page = self.page
        self.connect()
        a, b = page.analog_a, page.analog_b
        a.volt_spin.setValue(0.4)
        b.volt_spin.setValue(0.6)
        with patch.object(QMessageBox, "warning",
                          return_value=QMessageBox.StandardButton.Yes):
            a.apply_output()
            self.pump(10.0, lambda: not a._busy)
            b.apply_output()
            self.pump(10.0, lambda: not b._busy)
        self.assertAlmostEqual(getattr(page._main_device, "last_applied", -1),
                               0.4, places=9)
        self.assertAlmostEqual(getattr(page._second_device, "last_applied", -1),
                               0.6, places=9)


if __name__ == "__main__":
    unittest.main()
