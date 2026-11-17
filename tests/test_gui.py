"""GUI 回归测试：入口状态、滤光片轮的会话切换。

需要 Qt。没有图形环境时用 offscreen 平台跑；如果连 PySide6 都没有
就整体跳过。

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_gui -v
"""

import os
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在导入 Qt 之前设置
os.environ.setdefault("QT_API", "pyside6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    QT_AVAILABLE = True
except Exception:                                    # noqa: BLE001
    QT_AVAILABLE = False


def setUpModule():
    if not QT_AVAILABLE:
        raise unittest.SkipTest("PySide6 not available")


@unittest.skipUnless(QT_AVAILABLE, "PySide6 not available")
class TestHubHelpers(unittest.TestCase):
    """审查 #2（本轮）：Linux 僵尸进程不能被当作 Running。"""

    def test_proc_state_parsing(self):
        from ui.pages.device_hub_page import proc_state

        self.assertEqual(proc_state(b"1234 (python) S 1 2 3"), "S")
        self.assertEqual(proc_state(b"1234 (python) Z 1 2 3"), "Z")
        # 进程名里带括号和空格也要正确切分
        self.assertEqual(proc_state(b"999 (my (weird) name) S 1"), "S")
        self.assertIsNone(proc_state(b"garbage"))
        self.assertIsNone(proc_state(b"1234 ()"))

    def test_pid_alive_basics(self):
        from ui.pages.device_hub_page import pid_alive

        self.assertTrue(pid_alive(os.getpid()))
        self.assertFalse(pid_alive(999999))
        self.assertFalse(pid_alive(0))
        self.assertFalse(pid_alive(-1))


@unittest.skipUnless(QT_AVAILABLE, "PySide6 not available")
class TestFilterWheelSession(unittest.TestCase):
    """审查 #1（本轮）：转位中不允许断开/重连，旧回调也不能污染新连接。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)
        from ui.pages.filterwheel_page import FilterWheelPage
        cls.Page = FilterWheelPage

    def _connected_page(self):
        page = self.Page()
        page.selector.device_box.setCurrentIndex(
            [i for i in range(page.selector.device_box.count())
             if "Placeholder" in page.selector.device_box.itemText(i)][0])
        got = {}
        page.selector.connected.connect(lambda d: got.setdefault("device", d))
        page.selector._on_connect()
        deadline = 30
        while deadline > 0 and "device" not in got:
            self.app.processEvents()
            threading.Event().wait(0.02)
            deadline -= 0.02
        self.assertIn("device", got, "Placeholder 没连上")
        # 等首次状态读取结束
        while page._busy:
            self.app.processEvents()
            threading.Event().wait(0.02)
        return page

    def test_disconnect_locked_while_busy(self):
        page = self._connected_page()
        self.assertTrue(page.selector.disconnect_btn.isEnabled())

        page._set_busy(True, "simulated long operation")
        self.assertFalse(page.selector.disconnect_btn.isEnabled(),
                         "忙碌时不该允许断开")

        page._set_busy(False)
        self.assertTrue(page.selector.disconnect_btn.isEnabled())
        page.cleanup()

    def test_session_increments_on_connect_and_disconnect(self):
        page = self._connected_page()
        s0 = page._session
        page.selector._on_disconnect()
        while page.selector.device is not None:
            self.app.processEvents()
            threading.Event().wait(0.02)
        self.assertGreater(page._session, s0, "断开后会话号应该增加")
        page.cleanup()

    def test_disconnect_clears_busy(self):
        """旧 _busy 不清，新连接的状态刷新会被跳过。"""
        page = self._connected_page()
        page._busy = True
        page._on_disconnected()
        self.assertFalse(page._busy)
        page.cleanup()

    def test_stale_callback_is_ignored(self):
        """旧会话的回调不能改写新连接的界面。"""
        page = self._connected_page()
        page.position_value.setText("1")

        stale_session = page._session
        page._session += 100          # 模拟期间断开又重连

        # 直接运行一个「旧会话」的完成回调
        from ui.workers import run_async
        done = threading.Event()

        def work():
            return 2                  # 假装旧设备转到了 2

        def on_done(actual):
            if stale_session != page._session:
                done.set()
                return
            page.position_value.setText(str(actual))
            done.set()

        run_async(work, on_done, lambda msg: done.set())
        done.wait(5)
        self.app.processEvents()

        self.assertEqual(page.position_value.text(), "1",
                         "旧会话的结果覆盖了新连接的界面")
        page.cleanup()


@unittest.skipUnless(QT_AVAILABLE, "PySide6 not available")
class TestHubLayout(unittest.TestCase):
    """滤光片轮应该收在 Others 里，不单独占卡片。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_top_level_apps_excludes_members(self):
        from devicegui.base import DEVICE_APPS, top_level_apps

        tops = [a.key for a in top_level_apps()]
        self.assertNotIn("filterwheel", tops)
        self.assertIn("others", tops)
        self.assertIn("filterwheel", DEVICE_APPS["others"].members)

    def test_hub_cards(self):
        from ui.main_window import MainWindow

        win = MainWindow()
        win.goto_key("device")
        self.app.processEvents()
        hub = win._pages["device"]
        self.assertEqual(len(hub._cards), 6, list(hub._cards))
        self.assertNotIn("filterwheel", hub._cards)
        others = hub._cards["others"]
        self.assertIsNotNone(others.member_box)
        self.assertEqual(others.current_key(), "filterwheel")
        win.close()


if __name__ == "__main__":
    unittest.main()
