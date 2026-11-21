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

    # ---- #1 启停时序 ----
    def test_stop_cancels_pending_start(self):
        """Stop 之后，尚未执行的旧 Apply 不能把脉冲重新启动起来。

        复现路径：启动脉冲 → 提交一个 Apply（后台还没跑）→ 点 Stop。
        Stop 释放了旧任务，但那个 Apply 之后仍然调用 dev.start 建了新任务。
        """
        from ui.widgets.daq_panels import PulsePanel

        class FakePulse:
            def __init__(self):
                self.started = []
                self.last_params = None
                self.counter = "Dev1/ctr0"

            def check_params(self, f, d):
                return float(f), float(d)

            def start(self, f, d, counter=None):
                self.started.append((f, d))
                self.last_params = (f, d)
                return f, d

            def stop(self):
                self.last_params = None
                return None

            def status_text(self):
                return "fake"

        panel = PulsePanel()
        fake = FakePulse()
        panel.set_device(fake)
        panel.freq_spin.setValue(1000.0)
        panel.duty_spin.setValue(25.0)

        pending = []

        def fake_run_async(work, done, failed):
            pending.append((work, done, failed))

        with patch("ui.widgets.daq_panels.run_async", fake_run_async):
            panel.start()          # 提交但未执行
            panel.stop()           # 让上面那个失效

        self.assertEqual(len(pending), 2)

        # Stop 先完成
        _w, stop_done, _f = pending[1]
        stop_done(None)

        # 再执行被挂起的旧 Apply
        old_work, old_done, _f = pending[0]
        old_done(old_work())
        self.app.processEvents()

        self.assertEqual(fake.started, [], "旧 Apply 又启动了脉冲")
        self.assertIsNone(fake.last_params)
        self.assertNotIn("Pulsing", panel.status_label.text())
        panel.interrupt()

    def test_normal_start_still_works(self):
        """加了序号校验之后，正常启动不能被误伤。"""
        from ui.widgets.daq_panels import PulsePanel

        class FakePulse:
            def __init__(self):
                self.started = []
                self.last_params = None
                self.counter = "Dev1/ctr0"

            def check_params(self, f, d):
                return float(f), float(d)

            def start(self, f, d, counter=None):
                self.started.append((f, d))
                self.last_params = (f, d)
                return f, d

            def stop(self):
                self.last_params = None
                return None

            def status_text(self):
                return "fake"

        panel = PulsePanel()
        fake = FakePulse()
        panel.set_device(fake)
        pending = []

        def fake_run_async(work, done, failed):
            pending.append((work, done, failed))

        with patch("ui.widgets.daq_panels.run_async", fake_run_async):
            panel.start()

        work, done, _f = pending[0]
        done(work())
        self.app.processEvents()

        self.assertEqual(len(fake.started), 1)
        self.assertIn("Pulsing", panel.status_label.text())
        panel.interrupt()

    # ---- #2 清图读失败 ----
    def test_clear_plot_read_failure_keeps_baseline(self):
        """读不到基线时不能当成 0 —— 那会伪造出几千万 cps 的假峰。"""

        class FlakyCounter:
            def __init__(self):
                self.count = 1_000_000
                self.fail_next = False
                self.counter = "Dev1/ctr0"
                self.source = "/Dev1/PFI0"

            def read(self):
                if self.fail_next:
                    self.fail_next = False
                    raise RuntimeError("injected read failure")
                return self.count

            def clamp_bin(self, v):
                return float(v)

            def start(self, counter=None, source=None):
                return 0

            def stop(self):
                return None

        from ui.widgets.daq_panels import PhotonPanel

        panel = PhotonPanel()
        flaky = FlakyCounter()
        panel.set_device(flaky)
        panel._running = True
        panel._prev_count = 1_000_000
        panel._prev_time = 1234.5
        panel._t0 = 1000.0
        panel._times.append(1.0)
        panel._rates.append(12000.0)

        flaky.fail_next = True
        panel.clear_plot()
        self.app.processEvents()

        self.assertEqual(panel._prev_count, 1_000_000, "基线被清零了")
        self.assertEqual(panel._prev_time, 1234.5, "时间基线被改了")
        self.assertEqual(len(panel._times), 1, "数据被清了（应该原样保留）")
        self.assertEqual(panel._delta(panel._prev_count, 1_000_100), 100)
        panel._running = False

    # ---- #3 默认通道跟随板卡 ----
    def test_defaults_follow_resource(self):
        """选 Dev9 时默认通道要变成 Dev9/*，不能还指着 Dev1。"""
        page = self.page
        page.selector._configs["local"]["resource"] = "Dev9"
        page._sync_defaults()
        self.app.processEvents()

        self.assertEqual(page.analog_a.channel_edit.text(), "Dev9/ao2")
        self.assertEqual(page.analog_b.channel_edit.text(), "Dev9/ao3")
        self.assertEqual(page.photon.counter_edit.text(), "Dev9/ctr0")
        self.assertEqual(page.photon.source_edit.text(), "/Dev9/PFI0")

    def test_manual_channel_survives_resource_change(self):
        """用户手改过的通道不能被「同步默认值」覆盖。"""
        page = self.page
        page.analog_a.channel_edit.setText("Dev9/ao0")
        page._sync_defaults()
        self.app.processEvents()
        self.assertEqual(page.analog_a.channel_edit.text(), "Dev9/ao0")

    def test_connected_device_uses_resource_channels(self):
        """连接后设备的 channel 必须来自当前资源。"""
        page = self.page
        page.analog_a.channel_edit.setText("")
        page.analog_b.channel_edit.setText("")
        page.selector._configs["local"]["resource"] = "Dev9"
        page._sync_defaults(force=True)
        self.connect()
        self.assertTrue(page._main_device.channel.startswith("Dev9/"),
                        page._main_device.channel)
        self.assertTrue(
            getattr(page._second_device, "channel", "").startswith("Dev9/"),
            getattr(page._second_device, "channel", None))

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
