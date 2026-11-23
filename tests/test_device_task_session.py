"""ui/workers.py 的 DeviceTaskSession 回归测试。

这一层只负责「抓快照 + 执行前校验 + 回调前校验」，不碰设备本身，
所以用一个假的 owner 就能测全。
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_API", "pyside6")

from ui.workers import _CANCELLED, DeviceTaskSession


class _Owner:
    """最小宿主：只有 _session 和 device 两个真值。"""

    def __init__(self, device=None, session=0):
        self._session = session
        self.device = device

    def bind(self, device):
        """连接 / 断开：换设备并作废在途任务。"""
        self._session += 1
        self.device = device

    def invalidate(self):
        self._session += 1
        self.device = None


class TestDeviceTaskSession(unittest.TestCase):
    """每条用例都拦下 run_async，手动控制「什么时候完成」。"""

    def _submit(self, session, work, **kwargs):
        captured = {}

        def fake_run(fn, done, failed):
            captured.update(fn=fn, done=done, failed=failed)

        with patch("ui.workers.run_async", fake_run):
            session.submit(work, **kwargs)
        return captured

    # ------------------------------------------------------------------
    def test_normal_flow(self):
        owner = _Owner(device=object())
        session = DeviceTaskSession(owner)
        results = []

        cap = self._submit(session, lambda: 42, on_done=results.append)
        value = cap["fn"]()
        cap["done"](value)

        self.assertEqual(results, [42])

    def test_failure_reported(self):
        owner = _Owner(device=object())
        session = DeviceTaskSession(owner)
        errors = []

        def work():
            raise RuntimeError("boom")

        cap = self._submit(session, work, on_failed=errors.append)
        try:
            cap["fn"]()
        except RuntimeError:
            cap["failed"]("boom")

        self.assertEqual(errors, ["boom"])

    # ------------------------------------------------------------------
    def test_stale_before_execution_skips_work(self):
        """排队期间断开：work 根本不该跑。"""
        owner = _Owner(device=object())
        session = DeviceTaskSession(owner)
        calls = []

        cap = self._submit(session, lambda: calls.append(1) or 1)
        owner.invalidate()                      # 排队时断开

        self.assertIs(cap["fn"](), _CANCELLED)
        self.assertEqual(calls, [], "过期的任务仍然执行了 work")

    def test_stale_after_execution_drops_result(self):
        """work 跑完了但会话已变：结果要丢弃。"""
        owner = _Owner(device=object())
        session = DeviceTaskSession(owner)
        results = []

        cap = self._submit(session, lambda: 7, on_done=results.append)
        value = cap["fn"]()
        owner.invalidate()                      # 回调前断开
        cap["done"](value)

        self.assertEqual(results, [])

    def test_reconnect_with_new_device_drops_old_result(self):
        """重连到**另一台**设备：旧结果不能落到新连接上。"""
        first = object()
        owner = _Owner(device=first)
        session = DeviceTaskSession(owner)
        results = []

        cap = self._submit(session, lambda: "from-old", on_done=results.append)
        value = cap["fn"]()

        owner.bind(object())                    # 换设备
        cap["done"](value)

        self.assertEqual(results, [])

    def test_stale_failure_is_dropped(self):
        owner = _Owner(device=object())
        session = DeviceTaskSession(owner)
        errors = []

        cap = self._submit(session, lambda: 1, on_failed=errors.append)
        owner.invalidate()
        cap["failed"]("late error")

        self.assertEqual(errors, [])

    # ------------------------------------------------------------------
    def test_on_stale_callback(self):
        owner = _Owner(device=object())
        session = DeviceTaskSession(owner)
        stale = []

        cap = self._submit(session, lambda: 1,
                           on_done=lambda v: None,
                           on_stale=lambda: stale.append(1))
        value = cap["fn"]()
        owner.invalidate()
        cap["done"](value)

        self.assertEqual(stale, [1])

    def test_no_device_means_cancelled(self):
        owner = _Owner(device=None)
        session = DeviceTaskSession(owner)

        cap = self._submit(session, lambda: 1)
        self.assertIs(cap["fn"](), _CANCELLED)

    def test_owner_swap_with_same_generation_is_dropped(self):
        """代次没变但设备对象换了（理论上不该发生）也要拦住。"""
        owner = _Owner(device=object())
        session = DeviceTaskSession(owner)

        cap = self._submit(session, lambda: 1)
        value = cap["fn"]()
        owner.device = object()                 # 直接换，不递增代次
        results = []
        cap["done"](value)

        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
