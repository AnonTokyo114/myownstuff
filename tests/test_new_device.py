"""tools/new_device.py 的回归测试。

脚手架生成的代码是给人当起点用的，所以这里只校验**结构**对不对：
类名、方法名、TRANSPORTS、能不能 import / 构造，不校验业务逻辑。
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import new_device as nd  # noqa: E402


class TestClassName(unittest.TestCase):
    def test_plain_word(self):
        self.assertEqual(nd.snake_to_class("powermeter"), "Powermeter")

    def test_snake_case(self):
        self.assertEqual(nd.snake_to_class("power_meter"), "PowerMeter")

    def test_dashes_and_spaces(self):
        self.assertEqual(nd.snake_to_class("power-meter"), "PowerMeter")
        self.assertEqual(nd.snake_to_class("power meter"), "PowerMeter")

    def test_empty(self):
        self.assertEqual(nd.snake_to_class(""), "")


class TestValidation(unittest.TestCase):
    def _run(self, argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = nd.main(argv)
        return code, out.getvalue()

    def test_rejects_bad_kind(self):
        for bad in ("9abc", "has space", "with-dash", "_leading"):
            code, _ = self._run([bad, "--dry-run"])
            self.assertNotEqual(code, 0, f"{bad!r} 应该被拒绝")

    def test_uppercase_kind_is_normalised(self):
        """UPPER 会被转成小写接受（大小写不敏感），这是有意的。"""
        code, out = self._run(["UPPER", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("upper", out)

    def test_rejects_unknown_transport(self):
        code, _ = self._run(["demo", "--transports", "serial,usb",
                             "--dry-run"])
        self.assertNotEqual(code, 0)

    def test_dry_run_writes_nothing(self):
        code, out = self._run(["dryonly", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("core", out)
        self.assertIn("devicegui", out)
        self.assertFalse((ROOT / "core" / "dryonly.py").exists())


class TestGenerate(unittest.TestCase):
    """真实生成一次，验证三个文件都能 import 并用。"""

    KIND = "scaffold_probe"

    def tearDown(self):
        for name in (f"core/{self.KIND}.py",
                     f"ui/pages/{self.KIND}_page.py",
                     f"devicegui/{self.KIND}_app.py"):
            path = ROOT / name
            if path.exists():
                path.unlink()

    def test_generates_three_importable_files(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = nd.main([self.KIND, "--name", "Probe Device",
                            "--class-name", "ProbeDevice",
                            "--transports", "serial,lan",
                            "--method", "reading"])
        self.assertEqual(code, 0, out.getvalue())

        driver = ROOT / "core" / f"{self.KIND}.py"
        page = ROOT / "ui" / "pages" / f"{self.KIND}_page.py"
        app = ROOT / "devicegui" / f"{self.KIND}_app.py"
        for path in (driver, page, app):
            self.assertTrue(path.exists(), f"{path} 没生成")
            # 能编译
            compile(path.read_text(encoding="utf-8"), str(path), "exec")

        text = driver.read_text(encoding="utf-8")
        self.assertIn("class ProbeDevice", text)
        self.assertIn("class PlaceholderProbeDevice", text)
        # 模板写成 (a, b,)，尾随逗号是单元素元组也合法的那种写法
        self.assertIn('TRANSPORTS = ("serial", "lan",)', text)
        self.assertIn("def read_reading(", text)
        self.assertIn("def halt(", text)
        self.assertIn("def clamp_bin(", text)

        page_text = page.read_text(encoding="utf-8")
        self.assertIn("class ProbeDevicePage", page_text)
        self.assertIn(f'kind="{self.KIND}"', page_text)
        self.assertIn("def cleanup(", page_text)

        app_text = app.read_text(encoding="utf-8")
        self.assertIn(f'run_device_app("{self.KIND}")', app_text)

        # 驱动真的能实例化并调用
        import importlib
        mod = importlib.import_module(f"core.{self.KIND}")
        from core.transport import LocalTransport

        dev = mod.PlaceholderProbeDevice(LocalTransport("probe"))
        dev.connect()
        self.assertTrue(dev.is_connected)
        self.assertIsInstance(dev.read_reading(), float)
        dev.halt()
        dev.close()
        self.assertFalse(dev.is_connected)

    def test_does_not_overwrite_without_force(self):
        target = ROOT / "core" / f"{self.KIND}.py"
        target.write_text("# 手工写的，别覆盖\n", encoding="utf-8")
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                code = nd.main([self.KIND])
            self.assertNotEqual(code, 0, "已存在的文件应该拒绝覆盖")
            self.assertEqual(target.read_text(encoding="utf-8"),
                             "# 手工写的，别覆盖\n")
        finally:
            if target.exists():
                target.unlink()


if __name__ == "__main__":
    unittest.main()
