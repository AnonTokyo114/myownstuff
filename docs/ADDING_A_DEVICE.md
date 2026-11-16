# 新增一个设备 GUI

以加一台「功率计 Power Meter」为例，走完一遍需要碰 **5 个文件**。
其中两个是纯登记（各一行），真正要写的是驱动和界面。

```
core/powermeter.py            ← 1. 驱动：怎么和仪器说话
core/devices.py               ← 2. 登记型号（下拉里出现哪个名字）
ui/pages/powermeter_page.py   ← 3. 界面：用 DeviceSelector + 你自己的控件
devicegui/powermeter_app.py   ← 4. 独立入口（3 行）
devicegui/base.py             ← 5. 登记到 DEVICE_APPS + 告诉它怎么造界面
```

做完之后：

- 主 GUI 的 Device Interface 会自动多一张卡片
- `python -m devicegui.powermeter_app` 也能单独运行
- `device.bat powermeter` 一样能用

---

## 1. 写驱动：`core/powermeter.py`

**只依赖 `Transport`，不要直接碰 pyserial / socket** —— 这样串口、网口、GPIB
都能用同一套代码。

```python
"""Thorlabs PM100 功率计。

只写协议，不管底下是串口还是网口 —— 连接方式由 Transport 注入。
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("powermeter")


class PowerMeterError(RuntimeError):
    """设备层可读错误。"""


class PowerMeterDevice:
    #: 这个型号支持哪些连接方式（会出现在 Connection 对话框里）
    TRANSPORTS = ("serial", "lan")

    def __init__(self, transport, *,
                 log_callback: Callable[[str], None] | None = None,
                 **options):
        self.transport = transport
        self._log_cb = log_callback
        self._connected = False

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

    def connect(self):
        """打开传输并握手。失败请抛异常，界面会显示原因。"""
        self.transport.open()
        self.transport.write(b"*IDN?\n")
        reply = self.transport.read_until(b"\n")
        if b"PM100" not in reply:
            self.transport.close()
            raise PowerMeterError(f"unexpected identity: {reply!r}")
        self._connected = True
        self._log(f"connected: {reply.decode(errors='replace').strip()}")
        return self

    def close(self):
        """释放资源。主窗口关闭时会调用。"""
        self._connected = False
        try:
            self.transport.close()
        except Exception:                        # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 功能
    def read_power(self) -> float:
        """读当前功率（W）。"""
        if not self._connected:
            raise PowerMeterError("not connected")
        self.transport.write(b"MEAS:POW?\n")
        reply = self.transport.read_until(b"\n")
        try:
            return float(reply.strip())
        except ValueError as exc:
            raise PowerMeterError(f"bad reply: {reply!r}") from exc
```

**顺便建议**：如果这台仪器有"危险状态"（激光、高压），把
`halt()` / `stop()` 这类**安全收尾方法**也写上 —— 关闭窗口时会自动调用
`halt → stop → close`。

---

## 2. 登记型号：`core/devices.py`

在 `DEVICES` 字典里加一类（或往已有的类别里加一项）：

```python
from core import powermeter as pm_mod


def _make_powermeter(transport, **options):
    return pm_mod.PowerMeterDevice(transport, **options)


DEVICES = {
    # ... 已有的 stage / daq / camera ...
    "powermeter": [
        DeviceSpec(
            key="thorlabs_pm100",
            name="Thorlabs PM100",
            transports=("serial", "lan"),      # 必须和驱动的 TRANSPORTS 一致
            description="Optical power meter (USB / Ethernet).",
            factory=_make_powermeter,
        ),
        DeviceSpec(
            key="placeholder",
            name="Placeholder device",
            transports=("serial", "lan", "gpib"),
            description="No hardware. Lets you try the connect flow.",
            factory=_make_placeholder,          # 已有的通用占位设备
        ),
    ],
}
```

`key` 是内部标识，`name` 才是下拉里显示的文字。

---

## 3. 写界面：`ui/pages/powermeter_page.py`

界面骨架几乎都落在 `DeviceSelector` 上，你只需要加自己的控件。

```python
"""Power Meter 页面。"""

from __future__ import annotations

import time

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QVBoxLayout, QWidget,
)

from ui.widgets import DeviceSelector


class PowerMeterPage(QWidget):
    page_title = "Power Meter"
    log_line = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        title = QLabel("Power Meter")
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        # 统一组件：设备下拉 + Connection + Connect
        self.selector = DeviceSelector(
            kind="powermeter",                   # 对应 DEVICES 的键
            device_options=self._device_options,
            hint="Pick the meter, then Connect.",
        )
        self.selector.log.connect(self._append_log)
        self.selector.connected.connect(self._on_connected)
        self.selector.disconnected.connect(self._on_disconnected)
        outer.addWidget(self.selector)

        # ---------------- 你自己的一块 ----------------
        box = QFrame()
        box.setObjectName("formBox")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(12, 10, 12, 10)

        self.reading = QLabel("— W")
        self.reading.setObjectName("monoBig")
        box_layout.addWidget(self.reading)

        self.read_btn = QPushButton("Read power")
        self.read_btn.setObjectName("primary")
        self.read_btn.setEnabled(False)
        self.read_btn.clicked.connect(self.read_power)
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
        self.reading.setText("— W")

    def read_power(self) -> None:
        dev = self.selector.device
        if dev is None:
            return
        try:
            value = dev.read_power()
            self.reading.setText(f"{value:.6g} W")
        except Exception as exc:                 # noqa: BLE001
            QMessageBox.critical(self, "Read failed", str(exc))

    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """主窗口关闭 / 独立窗口关闭时调用，**必须实现**。"""
        try:
            self.selector.force_disconnect()
        except Exception:                        # noqa: BLE001
            pass

    def closeEvent(self, event) -> None:         # noqa: N802
        self.cleanup()
        super().closeEvent(event)
```

**必须实现 `cleanup()`** —— 主窗口关闭时靠它收尾（子页面不会收到 `closeEvent`）。

---

## 4. 独立入口：`devicegui/powermeter_app.py`

```python
"""Power Meter 的独立入口。

用法：python -m devicegui.powermeter_app
"""

import sys

from devicegui.base import run_device_app

if __name__ == "__main__":
    sys.exit(run_device_app("powermeter"))
```

---

## 5. 两处登记：`devicegui/base.py`

**a) 加进 `DEVICE_APPS`**（决定卡片显示什么）：

```python
DEVICE_APPS = {
    # ... 已有 ...
    "powermeter": DeviceApp(
        key="powermeter",
        title="Power Meter",
        icon="◐",
        summary="Optical power measurement (Thorlabs PM100).",
        module="devicegui.powermeter_app",
    ),
}
```

**b) 让 `create_device_window` 认识它**（决定点 Launch 时造哪个界面）：

```python
def create_device_window(kind: str):
    if kind == "stage":
        from ui.pages.stage_page import StagePage
        return StagePage()
    if kind == "daq":
        from ui.pages.daq_page import DaqPage
        return DaqPage()
    if kind == "powermeter":                     # ← 加这两行
        from ui.pages.powermeter_page import PowerMeterPage
        return PowerMeterPage()

    from ui.pages.placeholder_page import create_device_subpage
    return create_device_subpage(kind)
```

---

## 检查清单

- [ ] `python -m devicegui.powermeter_app` 能独立打开窗口
- [ ] 主 GUI → Device Interface 出现新卡片，Launch 能启动
- [ ] 连接 → 断开 → 再连接，不会残留进程/串口
- [ ] 关闭窗口时设备被释放（有 `halt`/`stop` 的会被调用）
- [ ] 无硬件时用 `Placeholder device` 也能走通界面流程
- [ ] 如果改动了 `core/` 或 `stage_page.py`，重新跑
      `python tools/build_stage_standalone.py`

---

## 只填已有分类时更简单

`camera` / `laser` / `scope` / `others` 这四个**已经登记好了**，只是界面还是占位页。
如果你要做的正是其中之一，**不需要**新建 `*_app.py`，也不需要改 `DEVICE_APPS`：

1. 在 `ui/pages/` 下写 `camera_page.py`
2. 在 `devicegui/base.py` 的 `create_device_window` 里加一个分支
3. 把 `DEVICE_APPS["camera"].implemented` 改成 `True`（去掉 placeholder 标记）
4. 在 `core/devices.py` 的 `DEVICES["camera"]` 里填上真实型号

---

## 常见问题

**Q: 我的仪器没有「连接方式」怎么办（比如就是本地 USB 板卡）？**

用 `transports=("local",)`。`LocalTransport` 只携带一个资源名（如 `Dev1`），
不需要波特率或 IP。NI-DAQ 就是这么接的。

**Q: 仪器协议是「发一行、收一行」怎么办？**

看 `core/stage.py` 的 `_transact()` —— 它封装了"清理残留 → 写 → 读到换行 →
清洗不可打印字符"这套流程，可以直接照抄。

**Q: 需要长时间采集（几秒钟以上）？**

用 `ui/workers.py` 的 `run_async(work, on_done, on_error)`，别在界面线程里
`sleep` —— 否则窗口会假死。`stage_page.py` 的每个操作都是这么写的。

**Q: 仪器有危险状态，怎么保证安全？**

参考 `stage_page.py` / `daq_page.py`：
- 急停按钮不受忙碌状态限制
- 危险操作配一个专门确认对话框（默认选项永远是"不做"）
- 设备的 `halt()` / `stop()` 会被关闭流程自动调用
