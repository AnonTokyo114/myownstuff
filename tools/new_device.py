#!/usr/bin/env python
"""新增设备的脚手架：生成驱动 / 界面 / 独立入口三个文件。

用法
----
    python tools/new_device.py powermeter --name "Thorlabs PM100"
    python tools/new_device.py powermeter --name "Thorlabs PM100" --transports serial,lan
    python tools/new_device.py powermeter --dry-run        # 只看会生成什么

生成之后脚本会打印**还需手工登记的两处**（core/devices.py 与
devicegui/base.py）。那两处不自动改，因为它们是静态表，结构可能变，
让脚本去猜反而容易写坏。

设计约定（照抄现有设备就是对的）
--------------------------------
* 驱动只依赖 Transport，不直接碰 pyserial / socket
* 必须提供 connect / close / is_connected / _log
* 有危险动作的设备要有 halt() / stop()，关闭窗口时会被自动调用
* 页面必须实现 cleanup()（主窗口关闭时只有它会调）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

#: 项目根（tools/new_device.py -> 项目根）
ROOT = Path(__file__).resolve().parent.parent


# ======================================================================
# 模板
# ======================================================================
DRIVER_TEMPLATE = '''"""@TITLE@ 驱动。

只写协议，不管底下是串口还是网口 —— 连接方式由 Transport 注入。
（这是一份骨架：把 TODO 换成真正的命令，其余结构照抄即可。）
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("@KIND@")


class @CLASS@Error(RuntimeError):
    """设备层可读错误：界面会直接把 str(exc) 显示给用户。"""


class @CLASS@:
    #: 这个型号支持哪些连接方式（会出现在 Connection 对话框里）
    TRANSPORTS = (@TRANSPORTS@,)

    def __init__(self, transport, *,
                 log_callback: Callable[[str], None] | None = None,
                 **options):
        self.transport = transport
        self._log_cb = log_callback
        self._connected = False
        # TODO: 设备相关的状态放这里（量程、单位、通道……）

    # ---------------------------------------------------------------- 日志
    def _log(self, msg: str) -> None:
        if self._log_cb is not None:
            try:
                self._log_cb(msg)
            except Exception:                    # noqa: BLE001
                pass
        log.info(msg)

    # ---------------------------------------------------------------- 连接
    @property
    def is_connected(self) -> bool:
        return self._connected

    @classmethod
    def clamp_bin(cls, value: float) -> float:
        """把界面给的值夹到设备能接受的范围内。

        没有约束就把原值返回 —— 页面会调用它，所以必须存在。
        """
        return float(value)

    def connect(self):
        """打开传输并握手。失败请抛异常，界面会显示原因。"""
        self.transport.open()
        try:
            # TODO: 握手 / *IDN? 之类的身份校验
            # self.transport.write(b"*IDN?\\n")
            # reply = self.transport.read_until(b"\\n")
            # if b"@EXPECT@ " not in reply:
            #     raise @CLASS@Error(f"unexpected identity: {reply!r}")
            pass
        except Exception:
            self.transport.close()
            raise
        self._connected = True
        self._log("@TITLE@ connected")
        return self

    def close(self) -> None:
        """释放资源。主窗口关闭 / 断开连接时会调用。"""
        self._connected = False
        try:
            self.transport.close()
        except Exception:                        # noqa: BLE001
            pass
        self._log("@TITLE@ closed")

    # ---------------------------------------------------------------- 功能
    def read_@METHOD@(self) -> float:
        """TODO: 读一个值。返回 float，界面会直接显示。"""
        if not self._connected:
            raise @CLASS@Error("not connected")
        # self.transport.write(b"MEAS?\\n")
        # reply = self.transport.read_until(b"\\n")
        # try:
        #     return float(reply.strip())
        # except ValueError as exc:
        #     raise @CLASS@Error(f"bad reply: {reply!r}") from exc
        raise @CLASS@Error("read_@METHOD@() is not implemented yet")

    # ---------------------------------------------------------------- 安全
    def halt(self) -> None:
        """紧急停止（可选）。有运动 / 高功率输出的设备一定要实现。

        关闭窗口时会按 halt -> stop -> close 的顺序调用。
        """
        self._log("@TITLE@ HALT")

    def stop(self) -> None:
        """停止当前动作但保持连接（可选）。"""
        if self._connected:
            self._log("@TITLE@ stop")


class Placeholder@CLASS@:
    """无硬件时用这个走通界面流程。

    行为要**和真机一致**：同样的方法名、同样的返回值形状，
    只是不发真实命令。这样界面代码不需要为占位设备写分支。
    """

    TRANSPORTS = ("serial", "lan", "gpib", "local")

    def __init__(self, transport, *,
                 log_callback: Callable[[str], None] | None = None,
                 **options):
        self.transport = transport
        self._log_cb = log_callback
        self._connected = False

    def _log(self, msg: str) -> None:
        if self._log_cb is not None:
            try:
                self._log_cb(msg)
            except Exception:                    # noqa: BLE001
                pass

    @property
    def is_connected(self) -> bool:
        return self._connected

    @classmethod
    def clamp_bin(cls, value: float) -> float:
        return float(value)

    def connect(self):
        self._connected = True
        self._log("Placeholder @TITLE@ connected (no hardware)")
        return self

    def close(self) -> None:
        self._connected = False

    def _require(self) -> None:
        if not self._connected:
            raise RuntimeError("Placeholder device is not connected")

    def read_@METHOD@(self) -> float:
        self._require()
        # 固定值便于肉眼判断界面有没有真的更新
        return 1.0

    def halt(self) -> None:
        self._log("Placeholder HALT (nothing is driven)")

    def stop(self) -> None:
        pass
'''


PAGE_TEMPLATE = '''"""@TITLE@ 页面。

骨架几乎都落在 DeviceSelector 上，这里只加自己的控件。
"""

from __future__ import annotations

import time

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import DeviceSelector


class @CLASS@Page(QWidget):
    page_title = "@TITLE@"
    log_line = Signal(str)

    def __init__(self) -> None:
        super().__init__()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        title = QLabel("@TITLE@")
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        # 统一组件：设备下拉 + Connection + Connect
        self.selector = DeviceSelector(
            kind="@KIND@",
            device_options=self._device_options,
            hint="Pick the instrument, then Connect.",
        )
        self.selector.log.connect(self._append_log)
        self.selector.connected.connect(self._on_connected)
        self.selector.disconnected.connect(self._on_disconnected)
        outer.addWidget(self.selector)

        # ---------------- 自己的控件 ----------------
        box = QFrame()
        box.setObjectName("formBox")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(12, 10, 12, 10)
        box_layout.setSpacing(8)

        self.reading = QLabel("—")
        self.reading.setObjectName("monoBig")
        box_layout.addWidget(self.reading)

        self.read_btn = QPushButton("Read")
        self.read_btn.setObjectName("primary")
        self.read_btn.setEnabled(False)
        self.read_btn.clicked.connect(self.read_value)
        box_layout.addWidget(self.read_btn)

        outer.addWidget(box)
        outer.addStretch(1)

    # ------------------------------------------------------------------
    def _device_options(self) -> dict:
        return {"log_callback": self.log_line.emit}

    def _append_log(self, text: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {text}")

    def _on_connected(self, _device) -> None:
        self.read_btn.setEnabled(True)

    def _on_disconnected(self) -> None:
        self.read_btn.setEnabled(False)
        self.reading.setText("—")

    def read_value(self) -> None:
        dev = self.selector.device
        if dev is None:
            return
        try:
            value = dev.read_@METHOD@()
        except Exception as exc:                 # noqa: BLE001
            QMessageBox.critical(self, "Read failed", str(exc))
            return
        self.reading.setText(f"{value:.6g}")

    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """主窗口关闭 / 独立窗口关闭时调用，**必须实现**。

        子页面收不到 closeEvent，主窗口靠这个方法收尾。
        """
        try:
            self.selector.force_disconnect()
        except Exception:                        # noqa: BLE001
            pass

    def closeEvent(self, event) -> None:         # noqa: N802
        self.cleanup()
        super().closeEvent(event)


def create_page() -> QWidget:
    """工厂函数（PAGES 用 "module:factory" 形式时可以指向它）。"""
    return @CLASS@Page()
'''


APP_TEMPLATE = '''"""@TITLE@ 的独立入口。

用法：python -m devicegui.@KIND@_app
"""

import sys

from devicegui.base import run_device_app

if __name__ == "__main__":
    sys.exit(run_device_app("@KIND@"))
'''


# ======================================================================
# 登记提示
# ======================================================================
def registration_hint(kind: str, title: str, transports: tuple[str, ...],
                      cls: str) -> str:
    transports_py = ", ".join(f'"{t}"' for t in transports)
    return f'''
======================================================================
还差两处**手工登记**（脚本不自动改，避免写坏静态表）
======================================================================

① core/devices.py —— 在 DEVICES 里加一个 @KIND@ 类目：

    from core import {kind} as {kind}_mod


    def _make_{kind}(transport, **options):
        return {kind}_mod.{cls}(transport, **options)


    def _make_placeholder_{kind}(transport, **options):
        return {kind}_mod.Placeholder{cls}(transport, **options)


    DEVICES["{kind}"] = [
        DeviceSpec(
            key="{kind}_default",
            name="{title}",
            transports=({transports_py},),
            description="TODO: one line about this instrument.",
            factory=_make_{kind},
        ),
        DeviceSpec(
            key="placeholder",
            name="Placeholder device",
            transports=({transports_py},),
            description="No hardware. Lets you try the connect flow.",
            factory=_make_placeholder_{kind},
        ),
    ]

② devicegui/base.py —— 两小步：

    a) DEVICE_APPS 里加一项：

    "{kind}": DeviceApp(
        key="{kind}",
        title="{title}",
        icon="◐",
        summary="TODO: one line for the Device Interface card.",
        module="devicegui.{kind}_app",
    ),

    b) create_device_window() 里加一个分支：

    if kind == "{kind}":
        from ui.pages.{kind}_page import {cls}Page
        return {cls}Page()

③ 跑一遍检查：

    python -m devicegui.{kind}_app          # 独立窗口能开
    python -m unittest discover -s tests    # 没弄坏既有测试
    python tools/build_stage_standalone.py  # 如果改了 core/ 或 stage_page

完成后这个设备会：
  * 在 Device Interface 上占一张卡片（Launch 可启动）
  * 能用 `device.bat {kind}` 单独启动
  * 主 GUI 里通过子进程打开，不会拖累主界面

详细说明见 docs/ADDING_A_DEVICE.md
'''


# ======================================================================
def snake_to_class(name: str) -> str:
    """powermeter -> PowerMeter，power_meter -> PowerMeter。"""
    parts = re.split(r"[_\-\s]+", name.strip())
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scaffold a new device (driver + page + standalone entry).")
    parser.add_argument("kind", help="设备类目，同时用作模块名，如 powermeter")
    parser.add_argument("--name", default=None,
                        help="显示名称，如 'Thorlabs PM100'（默认用 kind）")
    parser.add_argument("--transports", default="serial",
                        help="逗号分隔：serial,lan,gpib,local（默认 serial）")
    parser.add_argument("--method", default="value",
                        help="驱动里那个『读一个值』的方法名，"
                             "最终生成 read_<method>()（默认 value）")
    parser.add_argument("--class-name", default=None, dest="class_name",
                        help="生成的类名（默认由 kind 推导）。"
                             "powermeter 这种连写的词建议显式给 PowerMeter")
    parser.add_argument("--expect", default=None,
                        help="握手时期望在 *IDN? 回包里出现的字样"
                             "（默认用 kind 的大写）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印会生成什么，不写文件")
    parser.add_argument("--force", action="store_true",
                        help="覆盖已存在的文件")
    args = parser.parse_args(argv)

    kind = args.kind.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", kind):
        print(f"  ✗ kind 只能是小写字母/数字/下划线，且以字母开头：{kind!r}",
              file=sys.stderr)
        return 2

    transports = tuple(t.strip() for t in args.transports.split(",") if t.strip())
    allowed = {"serial", "lan", "gpib", "local"}
    bad = [t for t in transports if t not in allowed]
    if bad:
        print(f"  ✗ 未知的连接方式 {bad}；可用：{sorted(allowed)}",
              file=sys.stderr)
        return 2
    if not transports:
        transports = ("serial",)

    title = (args.name or kind).strip()
    cls = (args.class_name or snake_to_class(kind)).strip()
    expect = (args.expect or kind).upper()
    method = args.method.strip().lower().replace("-", "_")

    files: list[tuple[Path, str]] = [
        (ROOT / "core" / f"{kind}.py",
         DRIVER_TEMPLATE
         .replace("@TITLE@", title).replace("@CLASS@", cls)
         .replace("@KIND@", kind).replace("@METHOD@", method)
         .replace("@TRANSPORTS@", ", ".join(f'"{t}"' for t in transports))
         .replace("@EXPECT@", expect)),
        (ROOT / "ui" / "pages" / f"{kind}_page.py",
         PAGE_TEMPLATE
         .replace("@TITLE@", title).replace("@CLASS@", cls)
         .replace("@KIND@", kind).replace("@METHOD@", method)),
        (ROOT / "devicegui" / f"{kind}_app.py",
         APP_TEMPLATE
         .replace("@TITLE@", title)
         .replace("@KIND@", kind)),
    ]

    print()
    print(f"  设备类目: {kind}")
    print(f"  显示名称: {title}")
    print(f"  类       名: {cls}")
    print(f"  连接方式: {', '.join(transports)}")
    print(f"  读取方法: read_{method}()")
    print()

    existing = [p for p, _ in files if p.exists()]
    if existing and not args.force:
        for path in existing:
            print(f"  ✗ 已存在，未覆盖（用 --force 强制）：{path}",
                  file=sys.stderr)
        print("  什么都没写。", file=sys.stderr)
        return 1

    if args.dry_run:
        for path, content in files:
            print(f"  --- {path} ---")
            print(content)
        print(registration_hint(kind, title, transports, cls))
        return 0

    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        # 立刻编译一次，别把语法错误留给用户
        try:
            compile(content, str(path), "exec")
        except SyntaxError as exc:
            print(f"  ✗ 生成的 {path} 有语法错误：{exc}", file=sys.stderr)
            return 1
        print(f"  ✓ 写入 {path.relative_to(ROOT)}")

    print(registration_hint(kind, title, transports, cls))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
