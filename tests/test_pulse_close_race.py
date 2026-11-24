"""关窗竞态：迟到的 Start 不能把脉冲任务留在硬件上。

这条回归的门放在**驱动内部、任务创建之前**（PulseGenerator.check_params）。
只延迟调度前的 runner、或只延迟任务建立之后的回调，都覆盖不到这个窗口。

变异已验证：把 interrupt() 里的 `self._generation += 1` 去掉，本文件的
两个用例都会失败（dev._task 非空）。恢复后通过。
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_API", "pyside6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, ROOT)

try:
    from PySide6.QtWidgets import QApplication
except Exception:                                    # noqa: BLE001
    QApplication = None


# ======================================================================
# 假 NI-DAQmx：只够 PulseGenerator 用
# ======================================================================
class _FakeChannel:
    def __init__(self, task):
        self._task = task

    def add_co_pulse_chan_freq(self, counter, freq, duty_cycle):
        self._task.counter = counter
        self._task.freq = freq
        self._task.duty = duty_cycle


class _FakeTiming:
    def cfg_implicit_timing(self, sample_mode=None):
        pass


class FakeTask:
    def __init__(self):
        self.running = False
        self.closed = False
        self.counter = None
        self.freq = None
        self.duty = None
        self.co_channels = _FakeChannel(self)
        self.timing = _FakeTiming()

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def close(self):
        self.closed = True
        self.running = False


class _FakeNidaqmx:
    Task = FakeTask
    DaqError = Exception


class _FakeAcquisitionType:
    """core.nidaq 里 AcquisitionType 是单独 import 的名字。

    patch 'core.nidaq.nidaqmx' **不会**替换它 —— 没有装 nidaqmx 包时它是
    None，PulseGenerator.start 里 `AcquisitionType.CONTINUOUS` 会直接抛
    AttributeError。所以这个也必须一起假掉，测试才能在任何机器上自足。
    """

    CONTINUOUS = "CONTINUOUS"
    FINITE = "FINITE"


def fake_backend():
    """覆盖整个用例（含预先建立输出的那次 dev.start）。

    只包住第二个 with 块是不够的：情形 B 的前置输出也在 patch 范围之外。
    """
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("core.nidaq.nidaqmx", _FakeNidaqmx))
    stack.enter_context(
        patch("core.nidaq.AcquisitionType", _FakeAcquisitionType))
    return stack


@unittest.skipIf(QApplication is None, "PySide6 not available")
class TestLateStartAfterClose(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make(self):
        from core.nidaq import PulseGenerator
        from core.transport import LocalTransport
        from ui.widgets.daq_panels import PulsePanel

        dev = PulseGenerator(LocalTransport("Dev1"))
        # 本机没装 NI-DAQmx 驱动时 connect() 会直接拒绝；这里只需要能
        # 调 start/stop 的对象
        dev._connected = True
        panel = PulsePanel()
        panel.show()
        self.app.processEvents()
        panel.set_device(dev)
        return panel, dev

    def _run_case(self, preexisting_output):
        panel, dev = self._make()
        entered = threading.Event()
        release = threading.Event()
        real_check = dev.check_params
        calls = {"n": 0}

        def gated_check(freq, duty):
            calls["n"] += 1
            if calls["n"] == 1:
                # 第一次是 panel.start() 在主线程的预检查，不能拦
                return real_check(freq, duty)
            # 第二次来自工作线程、已在驱动内部、尚未创建任务
            entered.set()
            release.wait(5.0)
            return real_check(freq, duty)

        try:
            if preexisting_output:
                with fake_backend():
                    dev.start(1000.0, 25.0)
                panel._running = True

            panel.freq_spin.setValue(2000.0)
            panel.duty_spin.setValue(25.0)

            # 用 Event 跟踪**这个 worker 的完成**，而不是观察 dev._task。
            # 只看 dev._task is None 会在「任务还没创建」时也成立，于是
            # 断言可能在 worker 还在跑的时候就执行 —— 那是假通过窗口。
            worker_done = threading.Event()
            real_run_async = __import__("ui.workers",
                                        fromlist=["run_async"]).run_async

            def tracked_run_async(fn, on_done, on_error):
                def finish(call):
                    def wrapper(arg):
                        try:
                            return call(arg)
                        finally:
                            worker_done.set()
                    return wrapper

                return real_run_async(fn, finish(on_done), finish(on_error))

            with fake_backend(), \
                 patch.object(dev, "check_params", side_effect=gated_check), \
                 patch("ui.workers.run_async", tracked_run_async), \
                 patch("ui.widgets.daq_panels.QMessageBox.critical",
                       return_value=None):
                panel.start()
                self.assertTrue(entered.wait(5.0),
                                "工作线程没有进入驱动")

                # 关窗：会话与操作序号都必须作废
                panel.interrupt()
                self.assertFalse(panel._running)

                release.set()
                # 等 worker 的 done/error 真的被调用；期间持续泵 Qt 事件，
                # 而且**保持 backend patch 有效**
                deadline = time.time() + 5.0
                while not worker_done.is_set() and time.time() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
                self.app.processEvents()
                self.assertTrue(worker_done.is_set(),
                                "worker 没有在 5 秒内结束")

            self.assertIsNone(dev._task,
                              "关窗后仍留下了脉冲任务")
            self.assertIsNone(dev.last_params,
                              "关窗后仍留下了运行参数")
        finally:
            with fake_backend():
                try:
                    dev.close()
                except Exception:                    # noqa: BLE001
                    pass
            panel.interrupt()

    def test_first_start_interrupted_by_close(self):
        self._run_case(preexisting_output=False)

    def test_restart_interrupted_by_close(self):
        self._run_case(preexisting_output=True)


if __name__ == "__main__":
    unittest.main()
