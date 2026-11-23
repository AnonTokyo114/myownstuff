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


class TestEscaping(unittest.TestCase):
    """名称里的引号、反斜杠不能破坏生成的源码。"""

    KIND = "escape_probe"

    def tearDown(self):
        for name in (f"core/{self.KIND}.py",
                     f"ui/pages/{self.KIND}_page.py",
                     f"devicegui/{self.KIND}_app.py"):
            path = ROOT / name
            if path.exists():
                path.unlink()

    def _generate(self, name):
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            code = nd.main([self.KIND, "--name", name,
                            "--class-name", "EscapeProbe"])
        self.assertEqual(code, 0, out.getvalue())
        return ROOT / "core" / f"{self.KIND}.py"

    def test_double_quotes_survive(self):
        name = 'ACME "Power" Meter'
        driver = self._generate(name)
        src = driver.read_text(encoding="utf-8")
        self.assertNotIn("@TITLE", src, "占位符没被替换")
        compile(src, str(driver), "exec")
        # 模块 docstring 里是完整名字
        self.assertIn(name, src.splitlines()[0])

        page = (ROOT / "ui" / "pages" / f"{self.KIND}_page.py").read_text(
            encoding="utf-8")
        self.assertIn('page_title = \'ACME "Power" Meter\'', page)

    def test_backslash_is_not_an_escape(self):
        driver = self._generate("Bench\\new meter")
        src = driver.read_text(encoding="utf-8")
        compile(src, str(driver), "exec")
        # 生成的是 \\ 转义形式，import 出来仍是原始一个反斜杠
        import importlib
        mod = importlib.import_module(f"core.{self.KIND}")
        importlib.reload(mod)
        self.assertIn("Bench\\new", mod.__doc__ or "")

    def test_unicode_name(self):
        driver = self._generate("功率计 µW")
        src = driver.read_text(encoding="utf-8")
        compile(src, str(driver), "exec")
        row = src.splitlines()[0]
        self.assertIn("功率计", row)


class TestRejectsBadIdentifiers(unittest.TestCase):
    """类名/模块名/方法名必须能当标识符，否则登记片段根本编译不过。"""

    def _run(self, argv):
        import io
        from contextlib import redirect_stdout
        out = io.StringIO()
        with redirect_stdout(out):
            code = nd.main(argv)
        return code

    def test_class_name_with_dash(self):
        self.assertNotEqual(self._run(["okname", "--class-name", "Bad-Class"]),
                            0)

    def test_class_name_with_space(self):
        self.assertNotEqual(self._run(["okname", "--class-name", "Bad Class"]),
                            0)

    def test_kind_that_is_a_keyword(self):
        # from core import class as class_mod  —— 根本编译不过
        for kw in ("class", "def", "import", "return"):
            self.assertNotEqual(self._run([kw]), 0, kw)

    def test_method_with_punctuation(self):
        self.assertNotEqual(self._run(["okname", "--method", "not-a-name!"]),
                            0)


class TestNothingWrittenOnFailure(unittest.TestCase):
    """生成失败不能留下半成品 —— 否则重试会被『已存在』挡住。"""

    def test_bad_input_leaves_no_files(self):
        import io
        from contextlib import redirect_stdout
        kind = "atomic_probe"
        targets = [ROOT / "core" / f"{kind}.py",
                   ROOT / "ui" / "pages" / f"{kind}_page.py",
                   ROOT / "devicegui" / f"{kind}_app.py"]
        for path in targets:
            self.assertFalse(path.exists(), "测试前就存在，先清理")

        out = io.StringIO()
        with redirect_stdout(out):
            code = nd.main([kind, "--class-name", "Bad-Class"])
        self.assertNotEqual(code, 0)

        for path in targets:
            self.assertFalse(path.exists(),
                             f"失败后仍写出了 {path.name}")

    def test_generated_page_uses_async_and_shows_failures(self):
        """模板必须走 run_async，并且把连接失败显示在界面上。"""
        import io
        from contextlib import redirect_stdout
        kind = "async_probe"
        out = io.StringIO()
        with redirect_stdout(out):
            code = nd.main([kind, "--class-name", "AsyncProbe"])
        self.assertEqual(code, 0, out.getvalue())
        try:
            page = (ROOT / "ui" / "pages" / f"{kind}_page.py").read_text(
                encoding="utf-8")
            # 读设备不能同步跑在槽里
            self.assertIn("from ui.workers import run_async", page)
            self.assertIn("run_async(work, done, failed)", page)
            # 连接失败要看得见（pythonw 下没有控制台）
            self.assertIn("selector.failed.connect", page)
            # 日志控件来自 LogPane（时间戳/上限/Clear 都在那里统一）
            self.assertIn("LogPane(height=110)", page)
            self.assertIn("self.log.append(text)", page)
            # 驱动通过 log_callback 发的消息也要落到这个控件里；
            # 少了这一条，Log 里只有 selector 的进度，没有协议诊断
            self.assertIn("self.log_line.connect(self._append_log)", page)
            self.assertNotIn('print(f"[', page)
            # 过期回调要作废
            self.assertIn("_generation", page)
            compile(page, "page", "exec")
        finally:
            for name in (f"core/{kind}.py", f"ui/pages/{kind}_page.py",
                         f"devicegui/{kind}_app.py"):
                path = ROOT / name
                if path.exists():
                    path.unlink()


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
        # 用 AST 取**真机类**的 TRANSPORTS，不绑死引号；占位类的
        # 连接方式更宽（含 gpib/local），不能拿它比对
        import ast
        tree = ast.parse(text)
        found = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "ProbeDevice":
                for item in node.body:
                    if (isinstance(item, ast.Assign)
                            and any(isinstance(t, ast.Name)
                                    and t.id == "TRANSPORTS"
                                    for t in item.targets)):
                        found = ast.literal_eval(item.value)
        self.assertEqual(found, ("serial", "lan"))
        self.assertIn("def read_reading(", text)
        self.assertIn("def halt(", text)
        self.assertIn("def clamp_bin(", text)

        page_text = page.read_text(encoding="utf-8")
        self.assertIn("class ProbeDevicePage", page_text)
        # 引号可能是单也可能是双（模板用 repr 生成），只查 kind 的值在
        self.assertIn("kind=", page_text)
        self.assertIn(self.KIND, page_text)
        self.assertIn("def cleanup(", page_text)

        app_text = app.read_text(encoding="utf-8")
        self.assertIn("run_device_app(", app_text)
        self.assertIn(self.KIND, app_text)

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
