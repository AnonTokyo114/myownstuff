"""连接生命周期：关窗后迟到的 Connect 不能重新绑定设备。

复现要点：Connect 的 work 必须真的进入驱动（用 Event 扣住），
关窗（force_disconnect）后再放行，检查
  * selector.device 仍为 None
  * 迟到的设备被**主动释放**（halt/stop/close 至少 close 被调用）
  * 没有 emit connected
"""

import io
import os
import sys
import threading
import time
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

ROOT = r"C:\myownstuff"
os.environ.setdefault("QT_API", "pyside6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, ROOT)

from PySide6.QtWidgets import QApplication

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class SlowDevice:
    """connect() 会卡住，直到测试放行；可以指定放行后抛异常。"""

    def __init__(self, gate, *, fail_with=None):
        self._gate = gate
        self._fail_with = fail_with
        self.connect_called = threading.Event()
        self.released = []
        self.connected = False

    def connect(self):
        self.connect_called.set()
        self._gate.wait(10.0)
        if self._fail_with is not None:
            # 真正的连接失败：走 _on_connect 的 failed 分支
            raise RuntimeError(self._fail_with)
        self.connected = True
        return self

    def halt(self):
        self.released.append("halt")

    def stop(self):
        self.released.append("stop")

    def close(self):
        self.released.append("close")
        self.connected = False


class TestConnectLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_selector(self, gate, *, fail_with=None):
        from core.devices import DeviceSpec
        from ui.widgets.device_selector import DeviceSelector

        device = SlowDevice(gate, fail_with=fail_with)
        spec = DeviceSpec(
            key="slow_probe",
            name="Slow Probe",
            transports=("local",),
            description="test",
            factory=lambda transport, **options: device,
        )
        selector = DeviceSelector(kind="stage", device_options=lambda: {})
        # 让连接方式和假 spec 一致，否则 _on_connect 会在 transport
        # 检查那一行就返回，根本到不了 factory
        selector._conn_kind = "local"
        selector._configs["local"] = {"resource": "Dev1"}
        selector.show()
        self.app.processEvents()
        # 用假 spec 绕开真实注册表
        patcher = patch.object(selector, "current_spec", return_value=spec)
        patcher.start()
        self.addCleanup(patcher.stop)
        return selector, device

    def _pump(self, seconds=6.0, until=None):
        end = time.time() + seconds
        while time.time() < end:
            self.app.processEvents()
            if until is not None and until():
                return True
            time.sleep(0.02)
        return until() if until is not None else False

    # ------------------------------------------------------------------
    def test_late_connect_after_close_is_released(self):
        gate = threading.Event()
        selector, device = self._make_selector(gate)
        connected_events = []
        selector.connected.connect(lambda d: connected_events.append(d))

        selector._on_connect()
        self.assertTrue(device.connect_called.wait(5.0),
                        "connect() 没有被调用")

        # 关窗：此时 _device 还是 None
        selector.force_disconnect()
        self.assertIsNone(selector.device)

        # 放行迟到的连接
        gate.set()
        self._pump(6.0, lambda: "close" in device.released)

        self.assertIsNone(selector.device,
                          "迟到的连接重新绑定了设备")
        self.assertFalse(device.connected,
                         "迟到的设备没有被释放")
        self.assertIn("close", device.released,
                      f"设备没有走 close：{device.released}")
        self.assertEqual(connected_events, [],
                         "迟到的连接仍然 emit 了 connected")

    def test_force_disconnect_without_pending_connect(self):
        """没有在途连接时，force_disconnect 不应报错、也不影响正常路径。"""
        gate = threading.Event()
        selector, device = self._make_selector(gate)
        gate.set()
        selector._on_connect()
        self._pump(6.0, lambda: selector.device is not None)
        self.assertIsNotNone(selector.device)

        selector.force_disconnect()
        self.assertIsNone(selector.device)
        self.assertIn("close", device.released)

    def test_late_connect_after_selector_destroyed(self):
        """selector 被销毁之后，迟到完成的连接不能再碰 Qt 对象。

        force_disconnect 已经把请求作废、设备也会被释放，但 done() 仍在
        主线程被调用。那时如果去 emit Qt 信号，会抛
        RuntimeError: Signal source has been deleted —— 设备收尾反而
        被这个异常打断。这里先真正销毁 selector，再看有没有该异常。
        """
        gate = threading.Event()
        selector, device = self._make_selector(gate)
        selector._on_connect()
        self.assertTrue(device.connect_called.wait(5.0),
                        "connect() 没有被调用")

        selector.force_disconnect()
        selector.deleteLater()
        # 投递 DeferredDelete，让 C++ 对象真的销毁
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(
            None, QEvent.Type.DeferredDelete.value)

        captured = io.StringIO()
        with redirect_stderr(captured):
            gate.set()
            self._pump(4.0, lambda: "close" in device.released)
            self.app.processEvents()

        err = captured.getvalue()
        self.assertIn("close", device.released,
                      f"设备没有被释放：{device.released}")
        self.assertNotIn("Signal source has been deleted", err,
                         f"销毁后仍在碰 Qt 信号：{err[-300:]}")
        self.assertNotIn("RuntimeError", err, f"有未捕获异常：{err[-300:]}")

    def test_late_connect_failure_is_silent(self):
        """关窗之后才**失败**的连接，不该再弹错给用户。

        这里必须真的抛异常 —— 如果 connect() 只是正常返回，走的还是成功
        分支，这条测试就保护不了 failed 里的请求校验。
        """
        gate = threading.Event()
        selector, device = self._make_selector(
            gate, fail_with="review: delayed refusal")
        failures = []
        selector.failed.connect(lambda m: failures.append(m))

        selector._on_connect()
        self.assertTrue(device.connect_called.wait(5.0),
                        "connect() 没有被调用")

        # 等 worker 真的进入 connect 之后再关窗
        selector.force_disconnect()
        self.assertIsNone(selector.device)

        gate.set()
        # 等这次 worker 结束：busy 标志在迟到分支里不会被动，
        # 所以用「failed 是否发出」和超时共同判定
        self._pump(6.0, lambda: len(failures) > 0)
        self.assertEqual(failures, [],
                         f"迟到失败仍然打扰了用户：{failures}")


if __name__ == "__main__":
    unittest.main()
