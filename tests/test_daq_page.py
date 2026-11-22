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
                self._start_token = 0

            def check_params(self, f, d):
                return float(f), float(d)

            def start(self, f, d, counter=None):
                self.started.append((f, d))
                self.last_params = (f, d)
                self._start_token += 1
                return f, d, self._start_token

            def stop_if_still(self, token):
                if token != self._start_token:
                    return None
                self.last_params = None
                return None

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
                self._start_token = 0

            def check_params(self, f, d):
                return float(f), float(d)

            def start(self, f, d, counter=None):
                self.started.append((f, d))
                self.last_params = (f, d)
                self._start_token += 1
                return f, d, self._start_token

            def stop_if_still(self, token):
                if token != self._start_token:
                    return None
                self.last_params = None
                return None

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

    # ---- #1 连接设置改变要同步默认通道 ----
    def test_connection_dialog_updates_defaults(self):
        """走真实 edit_connection 改资源，界面默认通道要跟着变。

        之前只监听了型号下拉，连接设置里改 local resource 不触发它；
        而 _on_connected 末尾才同步，那时设备已经用旧通道建好了。
        """
        from unittest.mock import patch as _patch
        from PySide6.QtWidgets import QDialog
        from ui.widgets.device_selector import ConnectionDialog

        page = self.page
        page.selector.device_box.setCurrentIndex(self._placeholder_index)
        self.app.processEvents()

        with _patch.object(ConnectionDialog, "exec",
                           return_value=QDialog.DialogCode.Accepted), \
             _patch.object(ConnectionDialog, "result_config",
                           return_value=("local", {"resource": "Dev9"})):
            page.selector.edit_connection()
        self.app.processEvents()

        self.assertEqual(page.selector.current_resource(), "Dev9")
        self.assertEqual(page.analog_a.channel_edit.text(), "Dev9/ao2")
        self.assertEqual(page.analog_b.channel_edit.text(), "Dev9/ao3")
        self.assertEqual(page.photon.counter_edit.text(), "Dev9/ctr0")

    def test_connected_channel_matches_display(self):
        """设备通道与界面显示必须一致（不能界面 Dev9、设备 Dev1）。"""
        from unittest.mock import patch as _patch
        from PySide6.QtWidgets import QDialog
        from ui.widgets.device_selector import ConnectionDialog

        page = self.page
        page.selector.device_box.setCurrentIndex(self._placeholder_index)
        self.app.processEvents()

        with _patch.object(ConnectionDialog, "exec",
                           return_value=QDialog.DialogCode.Accepted), \
             _patch.object(ConnectionDialog, "result_config",
                           return_value=("local", {"resource": "Dev9"})):
            page.selector.edit_connection()
        self.app.processEvents()

        self.connect()
        self.assertEqual(page._main_device.channel,
                         page.analog_a.channel_edit.text())
        self.assertEqual(getattr(page._second_device, "channel", None),
                         page.analog_b.channel_edit.text())
        self.assertTrue(page._main_device.channel.startswith("Dev9/"),
                        page._main_device.channel)

    # ---- #2 任务所有权 ----
    def test_stop_if_still_ignores_stale_token(self):
        """过期 token 不能停掉后来者启动的任务。"""
        from core.nidaq import PlaceholderPulseGenerator
        from core.transport import LocalTransport

        dev = PlaceholderPulseGenerator(LocalTransport("Dev1"))
        dev.connect()
        dev.start(1000, 25)              # token 1
        dev.start(2000, 25)              # token 2
        stale = dev.last_token
        dev.start(3000, 25)              # token 3

        dev.stop_if_still(stale)
        self.assertEqual(dev.last_params, (3000.0, 25.0),
                         "过期 token 把新任务停了")
        self.assertIn("Pulsing", dev.status_text())

        dev.stop_if_still(dev.last_token)
        self.assertIsNone(dev.last_params)
        dev.close()

    def test_start_returns_its_own_token(self):
        """start() 必须把 token 直接返回，不能靠共享的 last_token。

        连续 Apply 时 last_token 已经被后来的 start 改掉了，读它会
        「撤销自己」变成「撤销别人」。
        """
        from core.nidaq import PlaceholderPulseGenerator
        from core.transport import LocalTransport

        dev = PlaceholderPulseGenerator(LocalTransport("Dev1"))
        dev.connect()
        _f, _d, first_token = dev.start(1000, 25)
        _f2, _d2, second_token = dev.start(2000, 25)
        self.assertGreater(second_token, first_token)
        dev.close()

    def test_stale_ui_start_does_not_stop_newer_task(self):
        """旧 start 已建立任务、检查尚未执行时，新任务不能被它停掉。"""
        import threading
        from unittest.mock import patch as _patch
        from ui.widgets.daq_panels import PulsePanel

        class GatedPulse:
            def __init__(self):
                self.last_params = None
                self.counter = "Dev1/ctr0"
                self._start_token = 0
                self.last_token = 0
                self.stopped_by = []
                self.entered = threading.Event()
                self.gate = None

            def check_params(self, f, d):
                return float(f), float(d)

            def start(self, f, d, counter=None):
                self._start_token += 1
                self.last_token = self._start_token
                self.last_params = (f, d)
                token = self._start_token
                if self.gate is not None:
                    self.entered.set()
                    self.gate.wait(10)     # 卡在「启动完成、检查未做」
                return f, d, token

            def stop_if_still(self, token):
                self.stopped_by.append(token)
                if token != self._start_token:
                    return None
                self.last_params = None
                return None

            def stop(self):
                self.last_params = None
                return None

            def status_text(self):
                if self.last_params is None:
                    return "Idle (pulse not running)"
                f, d = self.last_params
                return f"Pulsing on {self.counter}: {f:g} Hz, {d:g} % duty"

        panel = PulsePanel()
        fake = GatedPulse()
        panel.set_device(fake)
        pending = []

        def fake_run_async(work, done, failed):
            pending.append((work, done, failed))

        with _patch("ui.widgets.daq_panels.run_async", fake_run_async):
            panel.freq_spin.setValue(2000.0)
            panel.start()
        old_work, old_done, _f = pending[0]

        fake.gate = threading.Event()
        thread = threading.Thread(target=lambda: old_done(old_work()),
                                  daemon=True)
        thread.start()
        self.assertTrue(fake.entered.wait(5), "旧操作没进到 start")

        pending.clear()
        with _patch("ui.widgets.daq_panels.run_async", fake_run_async):
            panel.freq_spin.setValue(3000.0)
            panel.start()
        new_work, new_done, _f = pending[0]
        new_done(new_work())
        self.app.processEvents()
        self.assertEqual(fake.last_params, (3000.0, 25.0))

        fake.gate.set()
        thread.join(6)
        self.app.processEvents()

        self.assertEqual(fake.stopped_by, [1], "旧操作没有用自己的 token")
        self.assertEqual(fake.last_params, (3000.0, 25.0),
                         "旧操作的撤销停掉了新任务")
        self.assertIn("Pulsing", panel.status_label.text())
        panel.interrupt()

    # ---- #1 stop_if_still 的检查与停止必须原子 ----
    def test_stop_if_still_is_atomic_under_concurrency(self):
        """撤销旧 token 与新 start 并发时，新任务不能被误停。

        旧实现先取锁检查、放锁、再 stop()；中间新 start() 能抢到锁建好
        新任务，随后旧 stop() 停的就是它。
        """
        import threading
        from core.nidaq import PlaceholderPulseGenerator
        from core.transport import LocalTransport

        bad = 0
        for _ in range(50):
            dev = PlaceholderPulseGenerator(LocalTransport("Dev1"))
            dev.connect()
            dev.start(1000, 25)
            stale = dev.last_token
            barrier = threading.Barrier(2)

            def stopper():
                barrier.wait()
                dev.stop_if_still(stale)

            def starter():
                barrier.wait()
                dev.start(2000, 25)

            t1 = threading.Thread(target=stopper, daemon=True)
            t2 = threading.Thread(target=starter, daemon=True)
            t1.start(); t2.start()
            t1.join(5); t2.join(5)
            if dev.last_params != (2000.0, 25.0):
                bad += 1
            dev.close()
        self.assertEqual(bad, 0, f"{bad} 轮里新任务被旧撤销停掉了")

    # ---- #2 断开重连要使在途 Apply 失效 ----
    def test_reconnect_invalidates_pending_apply(self):
        """断连后在途 Apply 的完成回调不能改写新连接的界面。"""
        from ui.widgets.daq_panels import PulsePanel

        class Gated:
            def __init__(self):
                self.last_params = None
                self.counter = "Dev1/ctr0"
                self._start_token = 0
                self.last_token = 0

            def check_params(self, f, d):
                return float(f), float(d)

            def start(self, f, d, counter=None):
                self._start_token += 1
                self.last_token = self._start_token
                self.last_params = (f, d)
                return f, d, self._start_token

            def stop_if_still(self, token):
                if token != self._start_token:
                    return None
                self.last_params = None
                return None

            def stop(self):
                self.last_params = None
                return None

            def status_text(self):
                if self.last_params is None:
                    return "Idle (pulse not running)"
                f, d = self.last_params
                return f"Pulsing on {self.counter}: {f:g} Hz, {d:g} % duty"

        page = self.page
        self.connect()
        gated = Gated()
        page.pulse.set_device(gated)
        page.pulse.freq_spin.setValue(2000.0)

        pending = []

        def fake_run_async(work, done, failed):
            pending.append((work, done, failed))

        with patch("ui.widgets.daq_panels.run_async", fake_run_async):
            page.pulse.start()

        old_work, old_done, _f = pending[0]
        result = old_work()          # 旧设备已启动，回调还没跑
        self.app.processEvents()
        self.assertEqual(gated.last_params, (2000.0, 25.0))
        self.assertNotIn("Pulsing", page.pulse.status_label.text())

        # 断开 -> 重连（设备换了，会话编号也变了）
        page.selector._on_disconnect()
        self.pump(15.0, lambda: page.selector.device is None)
        self.connect()

        old_generation = page.pulse._generation
        old_done(result)             # 旧回调现在才来
        self.app.processEvents()

        self.assertNotIn("Pulsing", page.pulse.status_label.text(),
                         "旧结果污染了重连后的界面")
        self.assertFalse(page.pulse._running)
        self.assertIsNone(getattr(page._pulse_device, "last_params", None))

    def test_pending_apply_still_works_without_reconnect(self):
        """没断开时，回调要正常生效（别把修复做成一律丢弃）。"""
        from ui.widgets.daq_panels import PulsePanel

        class Simple:
            def __init__(self):
                self.last_params = None
                self.counter = "Dev1/ctr0"
                self._start_token = 0
                self.last_token = 0

            def check_params(self, f, d):
                return float(f), float(d)

            def start(self, f, d, counter=None):
                self._start_token += 1
                self.last_token = self._start_token
                self.last_params = (f, d)
                return f, d, self._start_token

            def stop_if_still(self, token):
                return None

            def stop(self):
                self.last_params = None
                return None

            def status_text(self):
                return "fake"

        panel = PulsePanel()
        dev = Simple()
        panel.set_device(dev)
        pending = []

        def fake_run_async(work, done, failed):
            pending.append((work, done, failed))

        with patch("ui.widgets.daq_panels.run_async", fake_run_async):
            panel.start()

        work, done, _f = pending[0]
        done(work())
        self.app.processEvents()
        self.assertTrue(panel._running)
        self.assertIn("Pulsing", panel.status_label.text())
        panel.interrupt()

    # ---- Stop 回调也要校验会话 ----
    def test_stale_stop_callback_ignored(self):
        """Stop 的回调如果在更新的 start 之后才到，不能把界面改成 Stopped。"""
        from ui.widgets.daq_panels import PulsePanel

        class Fake:
            def __init__(self):
                self.last_params = None
                self.counter = "Dev1/ctr0"
                self._start_token = 0

            def check_params(self, f, d):
                return float(f), float(d)

            def start(self, f, d, counter=None):
                self._start_token += 1
                self.last_params = (f, d)
                return f, d, self._start_token

            def stop_if_still(self, token):
                return None

            def stop(self):
                self.last_params = None
                return None

            def status_text(self):
                return "fake"

        panel = PulsePanel()
        fake = Fake()
        panel.set_device(fake)
        pending = []

        def fake_run_async(work, done, failed):
            pending.append((work, done, failed))

        with patch("ui.widgets.daq_panels.run_async", fake_run_async):
            panel.start()
        start_work, start_done, _f = pending[0]
        start_done(start_work())
        self.app.processEvents()
        self.assertTrue(panel._running)

        # 提交 Stop（回调暂缓）
        pending.clear()
        with patch("ui.widgets.daq_panels.run_async", fake_run_async):
            panel.stop()
        stop_work, stop_done, _f = pending[0]

        # Stop 还没回调时又 Apply 了一次
        pending.clear()
        with patch("ui.widgets.daq_panels.run_async", fake_run_async):
            panel.freq_spin.setValue(2500.0)
            panel.start()
        new_work, new_done, _f = pending[0]
        new_done(new_work())
        self.app.processEvents()
        self.assertTrue(panel._running)

        # 旧的 Stop 回调现在才来
        stop_work()
        stop_done(None)
        self.app.processEvents()

        self.assertTrue(panel._running, "旧 Stop 回调把新任务标记成停止了")
        self.assertIn("Pulsing", panel.status_label.text())
        panel.interrupt()

    def test_pending_stop_does_not_stop_newer_task(self):
        """Stop 的 work（不是回调）也不能停掉更新的任务。

        只过滤完成回调是不够的：硬件已经被停了，界面再一致也只是表面。
        """
        from ui.widgets.daq_panels import PulsePanel

        class Fake:
            def __init__(self):
                self.last_params = None
                self.counter = "Dev1/ctr0"
                self._start_token = 0
                self.last_token = 0

            def check_params(self, f, d):
                return float(f), float(d)

            def start(self, f, d, counter=None):
                self._start_token += 1
                self.last_token = self._start_token
                self.last_params = (f, d)
                return f, d, self._start_token

            def stop_if_still(self, token):
                if token != self._start_token:
                    return None
                self.last_params = None
                return None

            def stop(self):
                self.last_params = None
                return None

            def status_text(self):
                return "fake"

        panel = PulsePanel()
        fake = Fake()
        panel.set_device(fake)
        pending = []

        def fake_run_async(work, done, failed):
            pending.append((work, done, failed))

        # a. 启动
        with patch("ui.widgets.daq_panels.run_async", fake_run_async):
            panel.freq_spin.setValue(1000.0)
            panel.start()
        w, d, _f = pending[0]
        d(w())
        self.app.processEvents()

        # b. Stop 提交但 work 暂缓
        pending.clear()
        with patch("ui.widgets.daq_panels.run_async", fake_run_async):
            panel.stop()
        stop_work, stop_done, _f = pending[0]

        # c. 新 Apply 3000 先跑完
        pending.clear()
        with patch("ui.widgets.daq_panels.run_async", fake_run_async):
            panel.freq_spin.setValue(3000.0)
            panel.start()
        nw, nd, _f = pending[0]
        nd(nw())
        self.app.processEvents()
        self.assertEqual(fake.last_params, (3000.0, 25.0))

        # d. 旧 Stop 的 work 现在才执行
        stop_work()
        stop_done(None)
        self.app.processEvents()

        self.assertEqual(fake.last_params, (3000.0, 25.0),
                         "旧 Stop 的 work 把新任务停了")
        self.assertTrue(panel._running)

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
