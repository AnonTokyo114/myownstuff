# 新增一个设备 GUI

以加一台「功率计 Power Meter」为例，走完一遍需要碰 **5 个文件**。
其中两个是纯登记（各一行），真正要写的是驱动和界面。

---

## 快速开始：先用脚手架生成骨架

```bash
python tools/new_device.py powermeter \
    --name "Thorlabs PM100" \
    --class-name PowerMeter \
    --transports serial,lan \
    --method power
```

它会生成三个**能直接 import、能构造**的文件：

```
core/powermeter.py            驱动骨架（含 Placeholder 变体）
ui/pages/powermeter_page.py   页面骨架（DeviceSelector + 一个按钮）
devicegui/powermeter_app.py   独立入口
```

每个文件都带 `TODO:` 标记，把命令填进去即可。

然后脚本会打印**还需要手工登记的两处**（`core/devices.py` 和
`devicegui/base.py`）—— 这两处是静态表，脚本故意不自动改，
避免词表结构变化时写坏。

常用参数：

| 参数 | 说明 |
|---|---|
| `--name` | 下拉/卡片上显示的名字 |
| `--class-name` | 类名。`powermeter` 这种连写词建议显式给 `PowerMeter` |
| `--transports` | `serial,lan,gpib,local`，默认 `serial` |
| `--method` | 生成 `read_<method>()`，默认 `value` |
| `--dry-run` | 只打印不写文件 |
| `--force` | 覆盖已存在的文件 |

生成的代码**自检通过**：脚本写完会立刻 `compile()` 一次，
语法错误当场报出来，不会留给用户。

下面的章节是**每一步在做什么、为什么这么写**，脚手架跳过不看也能读。

---

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


class PlaceholderPowerMeter:
    """无硬件时用的占位驱动。

    方法名和返回值形状必须和真机**一致** —— 下游页面只会调
    ``read_power()``，不会为「占位还是真机」写分支。

    注意别拿 ``core.devices.PlaceholderDevice`` 顶替：那个模拟的是
    位移台的位置接口（get_position / set_position），没有 ``read_power``，
    页面一读就 AttributeError。
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

    def connect(self):
        self._connected = True
        self._log("Placeholder powermeter connected (no hardware)")
        return self

    def close(self) -> None:
        self._connected = False

    def read_power(self) -> float:
        if not self._connected:
            raise PowerMeterError("not connected")
        return 1.0
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


def _make_placeholder_powermeter(transport, **options):
    return pm_mod.PlaceholderPowerMeter(transport, **options)


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
            # 必须用**接口一致**的占位驱动：通用的 _make_placeholder
            # 返回的是位移台占位设备，没有 read_power，页面一读就崩
            factory=_make_placeholder_powermeter,
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
    QFrame, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from ui.widgets import DeviceSelector, LogPane
from ui.workers import DeviceTaskSession


class PowerMeterPage(QWidget):
    page_title = "Power Meter"
    log_line = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._busy = False
        #: 连接 / 断开 / 关窗时递增 —— 递增之后在途任务自动作废
        self._session = 0
        #: 设备操作的会话守卫：执行前和回调前各校验一次
        self._tasks = DeviceTaskSession(self)

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
        # 连接失败必须**看得见**：pythonw 下没有控制台，只 print 等于没提示
        self.selector.failed.connect(self._on_connect_failed)
        outer.addWidget(self.selector)

        # ---------------- 你自己的一块 ----------------
        box = QFrame()
        box.setObjectName("formBox")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(12, 10, 12, 10)

        self.reading = QLabel("— W")
        self.reading.setObjectName("monoBig")
        box_layout.addWidget(self.reading)

        self.status_label = QLabel("Not connected")
        self.status_label.setObjectName("pageSubtitle")
        self.status_label.setWordWrap(True)
        box_layout.addWidget(self.status_label)

        self.read_btn = QPushButton("Read power")
        self.read_btn.setObjectName("primary")
        self.read_btn.setEnabled(False)
        self.read_btn.clicked.connect(self.read_power)
        box_layout.addWidget(self.read_btn)

        outer.addWidget(box)

        # 可见的日志区：设备说什么、失败原因是什么，都在这里
        # （时间戳、3000 行上限、Clear 都由 LogPane 负责）
        self.log = LogPane(height=110)
        outer.addWidget(self.log)

        # 驱动通过 log_callback 发出来的消息（握手、协议响应、诊断）
        # 也要落到这个日志框 —— _device_options 里把它们转成了 log_line，
        # 这里再把 log_line 接到写控件的那一端。
        self.log_line.connect(self._append_log)

    # ------------------------------------------------------------------
    @property
    def device(self):
        """当前绑定的设备（DeviceTaskSession 靠它判断会话是否已变）。"""
        return self.selector.device

    def _device_options(self) -> dict:
        return {"log_callback": self.log_line.emit}

    def _on_connect_failed(self, msg: str) -> None:
        self.status_label.setText(f"Connect failed: {msg}")
        self._append_log(f"Connect failed: {msg}")
        QMessageBox.critical(self, "Connect failed", msg)

    def _append_log(self, text: str) -> None:
        self.log.append(text)

    def _on_connected(self, device) -> None:
        # 新会话：上一次连接留下的在途读取到此作废
        self._session += 1
        self.read_btn.setEnabled(True)
        self.status_label.setText("Connected")

    def _on_disconnected(self) -> None:
        # 断开后不存在「进行中的读取」：作废在途任务并清掉忙状态
        self._session += 1
        self._busy = False
        self.read_btn.setEnabled(False)
        self.reading.setText("— W")
        self.status_label.setText("Disconnected")

    def read_power(self) -> None:
        """读一次：设备 I/O 放后台，控件只在主线程更新。

        串口默认读超时 2 秒、LAN 3 秒 —— 直接写在槽里会把窗口卡住那么久。
        """
        dev = self.selector.device
        if dev is None or self._busy:
            return

        self._busy = True
        self.read_btn.setEnabled(False)
        self.status_label.setText("Reading…")

        def on_done(value) -> None:
            self._busy = False
            self.read_btn.setEnabled(True)
            self.reading.setText(f"{value:.6g} W")
            self.status_label.setText("Connected")

        def on_failed(msg: str) -> None:
            self._busy = False
            self.read_btn.setEnabled(True)
            self.status_label.setText(f"Read failed: {msg}")
            self._append_log(f"Read failed: {msg}")
            QMessageBox.critical(self, "Read failed", msg)

        # 执行前和回调前都会校验会话：断开 / 重连 / 关窗之后
        # 旧结果不会写进控件，旧任务也不会再去碰设备
        self._tasks.submit(lambda: dev.read_power(),
                           on_done=on_done, on_failed=on_failed)

    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """主窗口关闭 / 独立窗口关闭时调用，**必须实现**。"""
        self._session += 1                       # 让在途读取作废
        try:
            self.selector.force_disconnect()
        except Exception:                        # noqa: BLE001
            pass

    def closeEvent(self, event) -> None:         # noqa: N802
        self.cleanup()
        super().closeEvent(event)
```

**必须实现 `cleanup()`** —— 主窗口关闭时靠它收尾（子页面不会收到 `closeEvent`）。

**读设备务必走 `DeviceTaskSession`** —— 上面 `read_power` 就是模板。它在
**执行前**和**回调前**各校验一次「会话有没有变」（代次 + 设备身份）：

* 断开、重连、关窗之后，旧结果不会写进控件
* 排队期间就断开的任务，连 `work()` 都不会执行

它**不**负责中断已经在硬件上跑起来的动作 —— 需要真正取消硬件时（比如让位移台
停下、让输出归零），还得靠设备自己的 token 和驱动内的锁，那是另一层。

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
