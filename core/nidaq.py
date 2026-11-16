"""NI-DAQmx 模拟输出（DC 电压）。

源起：PCI-6229 + BNC-2120 输出 DC 电压驱动激光功率的简单 GUI。
这里把它做成可扩展的基础：

  * 电压范围、警告阈值、警告文案都是类常量，方便以后扩展成多通道 / 多设备
  * 安全警告是**纯逻辑**（needs_warning / acknowledge / reset），
    界面只负责弹框，所以这套判定可以单独测试
  * 与其它设备一样通过 Transport 构造（LocalTransport 携带 "Dev1"），
    这样能复用 Device Interface 的「选设备 → 连接」流程

注意：没有安装 NI-DAQmx 驱动时，创建 nidaqmx.Task() 会抛 DaqNotFoundError，
这里统一翻译成 NIError，界面给人话提示。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("nidaq")

#: 无法导入 nidaqmx 时置 False（构造设备时才检查，import 本模块不报错）
try:
    import nidaqmx
    from nidaqmx.errors import DaqError
    NIDAQMX_AVAILABLE = True
except Exception:                                    # pragma: no cover
    nidaqmx = None
    DaqError = Exception
    NIDAQMX_AVAILABLE = False


class NIError(RuntimeError):
    """NI-DAQ 相关的可读错误。"""


@dataclass(frozen=True)
class StopResult:
    """stop() 的结果。

    「驱动 0 V」和「释放 DAQ task」是两件事：释放总能做到，但写 0 V 可能
    因为驱动报错或连接已断而失败。失败时输出可能仍然带电，调用方**必须**
    据此显示「状态未知」，不能假装已经归零。
    """

    #: 是否确认成功写入 0 V
    zeroed: bool
    #: task 是否已释放
    released: bool
    #: 归零失败时的错误信息
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.zeroed and self.released


def driver_available() -> bool:
    """NI-DAQmx 驱动是否可用（不只是 Python 包）。"""
    if not NIDAQMX_AVAILABLE:
        return False
    try:
        import nidaqmx.system
        nidaqmx.system.System.local().devices
        return True
    except Exception:                                # noqa: BLE001
        return False


def list_devices() -> list[str]:
    """列出本机 NI-DAQ 设备名（如 ['Dev1']）；不可用时返回空列表。"""
    if not driver_available():
        return []
    try:
        import nidaqmx.system
        return [d.name for d in nidaqmx.system.System.local().devices]
    except Exception:                                # noqa: BLE001
        return []


# ======================================================================
# 真实设备
# ======================================================================
class NIDaqDevice:
    """单个模拟输出通道的 DC 电压源。"""

    TRANSPORTS = ("local",)

    #: 输出范围与安全阈值（源程序里的取值）
    V_MIN = 0.0
    V_MAX = 5.0
    WARN_THRESHOLD = 1.0          # 伏特；超过需确认防护
    WARN_TEXT = (
        "This voltage would lead to laser output power greater than 10 mW. "
        "Wear proper protection before proceeding."
    )
    PROTECTION_QUESTION = "Did you wear proper protection?"

    DEFAULT_CHANNEL = "Dev1/ao2"

    def __init__(self, transport, *, log_callback: Callable[[str], None] | None = None,
                 channel: str | None = None, **options):
        self.transport = transport
        self.resource = getattr(transport, "resource", "Dev1")
        self.channel = channel or f"{self.resource}/ao2"
        self._log_cb = log_callback
        self._lock = threading.RLock()
        self._task = None
        self._connected = False
        self.last_applied = 0.0
        #: 用户是否已确认过安全警告；电压降到阈值以下会重新武装
        self.warned = False

    # ------------------------------------------------------------------
    def _log(self, msg: str) -> None:
        if self._log_cb is not None:
            try:
                self._log_cb(msg)
            except Exception:                        # noqa: BLE001
                pass
        log.info(msg)

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------
    def connect(self):
        """检查 Python 包与驱动，然后打开 transport。"""
        if not NIDAQMX_AVAILABLE:
            raise NIError("nidaqmx is not installed (pip install nidaqmx)")
        if not driver_available():
            raise NIError(
                "NI-DAQmx driver not found. Install it from National Instruments "
                "(nidaqmx Python package alone is not enough), then replug the "
                "PCI card / restart the machine.")
        self.transport.open()
        self._connected = True
        self._log(f"NI-DAQ device ready: {self.resource}, channel {self.channel}")
        return self

    def close(self):
        """停止输出并释放 task。"""
        self.stop()
        try:
            self.transport.close()
        except Exception:                            # noqa: BLE001
            pass
        self._connected = False

    # ------------------------------------------------------------------
    # 安全警告（纯逻辑，便于测试）
    # ------------------------------------------------------------------
    def needs_warning(self, volts: float) -> bool:
        """电压达到阈值且用户还没确认过 -> 需要弹警告。"""
        return volts >= self.WARN_THRESHOLD and not self.warned

    def acknowledge_warning(self) -> None:
        self.warned = True

    def reset_warning(self, volts: float) -> None:
        """电压回到阈值以下时重新武装（下次升高还要再确认一次）。"""
        if volts < self.WARN_THRESHOLD:
            self.warned = False

    # ------------------------------------------------------------------
    # 输出
    # ------------------------------------------------------------------
    def clamp(self, volts: float) -> float:
        """把电压夹到允许范围；非数字抛 ValueError。"""
        try:
            value = float(volts)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Not a valid voltage: {volts!r}") from exc
        return min(max(value, self.V_MIN), self.V_MAX)

    def _ensure_task(self):
        if self._task is not None:
            return self._task
        try:
            task = nidaqmx.Task()
        except Exception as exc:                     # noqa: BLE001
            raise NIError(f"could not create DAQ task: {exc}") from exc
        try:
            task.ao_channels.add_ao_voltage_chan(
                self.channel, min_val=self.V_MIN, max_val=self.V_MAX)
        except DaqError as exc:
            try:
                task.close()
            except Exception:                        # noqa: BLE001
                pass
            raise NIError(f"could not add channel {self.channel!r}: {exc}") from exc
        self._task = task
        self._log(f"Opened DAQ task on {self.channel}")
        return task

    def _close_task(self) -> None:
        if self._task is not None:
            try:
                self._task.close()
            except Exception:                        # noqa: BLE001
                pass
            self._task = None
            self._log("DAQ task released")

    def write_voltage(self, volts: float) -> float:
        """输出一个 DC 电压（on-demand write，保持到下次写入或关闭）。

        调用方应当先检查 needs_warning() 并取得用户确认。
        返回实际写入的电压（已夹到范围内）。
        """
        if not self._connected:
            raise NIError("NI-DAQ device is not connected")
        value = self.clamp(volts)
        with self._lock:
            task = self._ensure_task()
            try:
                task.write(value)
            except DaqError as exc:
                self._close_task()
                raise NIError(f"write failed: {exc}") from exc
        self.last_applied = value
        self._log(f"Output {value:.3f} V on {self.channel}")
        return value

    def stop(self) -> StopResult:
        """驱动 0 V，然后释放 task。

        归零失败时**不**把 last_applied 记成 0，而是记为 None（未知），
        让界面显示「输出状态未知」而不是「已停止」。
        """
        error: str | None = None
        zeroed = False
        with self._lock:
            if self._task is not None:
                try:
                    self._task.write(0.0)
                    zeroed = True
                    self._log("Set 0 V before releasing the task")
                except Exception as exc:             # noqa: BLE001
                    error = str(exc)
                    log.warning("failed to write 0 V: %s", exc)
                    self._log(
                        f"WARNING: could NOT set 0 V — the output may still be "
                        f"live ({exc})")
                self._close_task()
            else:
                # 本来就没有 task 在跑，可以认为端子上没有本程序施加的输出
                zeroed = True
            released = self._task is None

        if zeroed:
            self.last_applied = 0.0
            self.warned = False
        else:
            self.last_applied = None
        return StopResult(zeroed=zeroed, released=released, error=error)

    def status_text(self) -> str:
        if not self._connected:
            return "Idle (not connected)"
        if self.last_applied is None:
            return (f"Output state UNKNOWN on {self.channel} — "
                    "zeroing failed, check the hardware")
        if self._task is None:
            return "Idle (output not running)"
        return f"Outputting {self.last_applied:.3f} V on {self.channel}"


# ======================================================================
# 占位设备（无硬件时走通界面流程）
# ======================================================================
class PlaceholderDaq:
    """模拟一个 DC 电压源：记录最后写入的值，不碰任何硬件。"""

    TRANSPORTS = ("local",)
    V_MIN = NIDaqDevice.V_MIN
    V_MAX = NIDaqDevice.V_MAX
    WARN_THRESHOLD = NIDaqDevice.WARN_THRESHOLD
    WARN_TEXT = NIDaqDevice.WARN_TEXT
    PROTECTION_QUESTION = NIDaqDevice.PROTECTION_QUESTION

    def __init__(self, transport, *, log_callback: Callable[[str], None] | None = None,
                 channel: str | None = None, **options):
        self.transport = transport
        self.resource = getattr(transport, "resource", "Dev1")
        self.channel = channel or f"{self.resource}/ao2"
        self._log_cb = log_callback
        self._connected = False
        self.last_applied = 0.0
        self.warned = False

    def _log(self, msg: str) -> None:
        if self._log_cb is not None:
            try:
                self._log_cb(msg)
            except Exception:                        # noqa: BLE001
                pass

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self):
        self.transport.open()
        self._connected = True
        self._log("Placeholder DAQ connected. No real hardware is being driven.")
        return self

    def close(self):
        self.stop()
        self._connected = False
        self._log("Placeholder DAQ disconnected.")

    def needs_warning(self, volts: float) -> bool:
        return volts >= self.WARN_THRESHOLD and not self.warned

    def acknowledge_warning(self) -> None:
        self.warned = True

    def reset_warning(self, volts: float) -> None:
        if volts < self.WARN_THRESHOLD:
            self.warned = False

    def clamp(self, volts: float) -> float:
        try:
            value = float(volts)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Not a valid voltage: {volts!r}") from exc
        return min(max(value, self.V_MIN), self.V_MAX)

    def write_voltage(self, volts: float) -> float:
        if not self._connected:
            raise NIError("Placeholder DAQ is not connected")
        value = self.clamp(volts)
        self.last_applied = value
        self._log(f"Placeholder output {value:.3f} V on {self.channel}")
        return value

    def stop(self) -> StopResult:
        self.last_applied = 0.0
        self.warned = False
        self._log("Placeholder output set to 0 V")
        return StopResult(zeroed=True, released=True)

    def status_text(self) -> str:
        if not self._connected:
            return "Idle (not connected)"
        if self.last_applied is None:
            return f"Placeholder: output state UNKNOWN on {self.channel}"
        return f"Placeholder: {self.last_applied:.3f} V on {self.channel}"
