"""配置记忆的回归测试。

覆盖三轮审查里确认过的配置问题：

  * 打开 Connection 对话框直接确定，不能改掉已保存的端口/波特率
  * 保存的端口不在扫描结果里时，仍要显示它，不能换成别的端口
  * 手输端口后，提交的必须是输入框里的文字（不是下拉旧项的数据）

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_settings -v
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
class TestSettingsPersistence(unittest.TestCase):
    """每个设备各自记住自己的串口。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)
        import ui.settings as settings_mod
        cls.settings_mod = settings_mod
        cls._orig_path = settings_mod.PATH
        cls._orig_root = settings_mod.ROOT
        cls.tmpdir = tempfile.mkdtemp(prefix="cfgtest-")
        settings_mod.PATH = Path(cls.tmpdir) / "settings.ini"
        settings_mod.ROOT = Path(cls.tmpdir)

    @classmethod
    def tearDownClass(cls):
        cls.settings_mod.PATH = cls._orig_path
        cls.settings_mod.ROOT = cls._orig_root
        import shutil
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def _selector(self, kind: str):
        from ui.widgets.device_selector import DeviceSelector
        return DeviceSelector(kind=kind)

    def test_roundtrip(self):
        sel = self._selector("stage")
        sel._configs["serial"] = {"port": "COM7", "baudrate": 57600}
        sel._save_configs()

        again = self._selector("stage")
        self.assertEqual(again._configs["serial"]["port"], "COM7")
        self.assertEqual(again._configs["serial"]["baudrate"], 57600)

    def test_kinds_are_independent(self):
        stage = self._selector("stage")
        stage._configs["serial"] = {"port": "COM7", "baudrate": 115200}
        stage._save_configs()

        fw = self._selector("filterwheel")
        fw._configs["serial"] = {"port": "COM4", "baudrate": 115200}
        fw._save_configs()

        self.assertEqual(self._selector("stage")._configs["serial"]["port"], "COM7")
        self.assertEqual(self._selector("filterwheel")._configs["serial"]["port"],
                         "COM4")


@unittest.skipUnless(QT_AVAILABLE, "PySide6 not available")
class TestConnectionDialog(unittest.TestCase):
    """对话框必须恢复保存值，且手输要生效。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from core import transport as tr_mod
        self.tr = tr_mod
        self._orig = tr_mod.available_serial_ports
        tr_mod.available_serial_ports = lambda: [
            ("COM3", "USB Serial"), ("COM7", "FTDI"), ("COM4", "CH340")]

    def tearDown(self):
        self.tr.available_serial_ports = self._orig

    def _dialog(self, port="COM7", baud=57600):
        from ui.widgets.device_selector import ConnectionDialog
        configs = {
            "serial": {"port": port, "baudrate": baud},
            "lan": {"host": "192.168.1.100", "port": 5025},
            "gpib": {"address": "GPIB0::12::INSTR"},
            "local": {"resource": "Dev1"},
        }
        return ConnectionDialog(("serial",), kind="serial", configs=configs)

    def test_restores_saved_port_and_baud(self):
        dlg = self._dialog()
        self.assertEqual(dlg.selected_port(), "COM7")
        self.assertEqual(dlg.baud_box.currentText(), "57600")

    def test_accept_without_changes_keeps_values(self):
        """这是审查里的核心复现：打开就确定，不能把 COM7 改成 COM3。"""
        dlg = self._dialog()
        _kind, params = dlg.result_config()
        self.assertEqual(params, {"port": "COM7", "baudrate": 57600})

    def test_port_not_in_scan_is_preserved(self):
        self.tr.available_serial_ports = lambda: [("COM3", "USB")]
        dlg = self._dialog(port="COM9")
        self.assertEqual(dlg.selected_port(), "COM9")

    def test_no_ports_scanned(self):
        self.tr.available_serial_ports = lambda: []
        dlg = self._dialog(port="COM9")
        self.assertEqual(dlg.selected_port(), "COM9")

    def test_typed_port_is_submitted(self):
        """可编辑下拉里打字后，currentData() 仍是旧项 —— 不能被骗。"""
        dlg = self._dialog(port="COM7")
        dlg.port_box.setCurrentText("COM9")
        self.app.processEvents()
        self.assertEqual(dlg.selected_port(), "COM9")
        self.assertEqual(dlg.result_config()[1]["port"], "COM9")

    def test_selecting_list_item_uses_its_data(self):
        dlg = self._dialog(port="COM7")
        dlg.port_box.setCurrentIndex(dlg.port_box.findData("COM4"))
        self.app.processEvents()
        self.assertEqual(dlg.selected_port(), "COM4")

    # ---- 刷新端口列表不能改变当前选择 ----
    def test_rescan_keeps_typed_port(self):
        """手输之后点刷新，不能被改回列表里的旧项。

        currentData() 在手输后仍返回旧列表项，直接拿它当 current 会
        把 COM9 改回 COM7，接着 OK 就保存了错的端口。
        """
        dlg = self._dialog(port="COM7")
        dlg.port_box.setCurrentText("COM9")
        self.app.processEvents()
        self.assertEqual(dlg.selected_port(), "COM9")

        dlg._rescan_serial_ports()
        self.app.processEvents()
        self.assertEqual(dlg.selected_port(), "COM9")
        self.assertEqual(dlg.port_box.currentText().strip(), "COM9")

    def test_rescan_keeps_saved_port_not_in_scan(self):
        """保存的端口不在扫描结果里，刷新也不该换成别的端口。"""
        self.tr.available_serial_ports = lambda: [
            ("COM3", "USB"), ("COM7", "FTDI")]
        dlg = self._dialog(port="COM9")
        dlg._rescan_serial_ports()
        self.app.processEvents()
        self.assertEqual(dlg.selected_port(), "COM9")

    def test_rescan_keeps_selected_list_item(self):
        dlg = self._dialog(port="COM7")
        dlg.port_box.setCurrentIndex(dlg.port_box.findData("COM4"))
        self.app.processEvents()
        dlg._rescan_serial_ports()
        self.app.processEvents()
        self.assertEqual(dlg.selected_port(), "COM4")


if __name__ == "__main__":
    unittest.main()
