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
    from nidaqmx.constants import AcquisitionType, Edge
    from nidaqmx.errors import DaqError
    NIDAQMX_AVAILABLE = True
except Exception:                                    # pragma: no cover
    nidaqmx = None
    DaqError = Exception
    AcquisitionType = None
    Edge = None
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
            elif self.last_applied is None:
                # task 已经没了，但上一次归零**失败**过 —— 端子状态仍然未知。
                # 「task 已释放」不等于「输出为 0 V」，不能据此报成功。
                zeroed = False
                error = ("unknown output state from an earlier failed zeroing; "
                         "the DAQ task is gone, so it cannot be re-driven here")
                self._log("WARNING: output state still UNKNOWN from an earlier "
                          "failed zeroing")
            else:
                # 从未写过非零值（或上次已成功归零），可以认为端子上没有输出
                zeroed = True
            released = self._task is None

        if zeroed:
            self.last_applied = 0.0
            self.warned = False
        else:
            # 保持未知，直到有一次**成功的硬件写零**为止
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


# ======================================================================
# 光子计数：计数器输入
# ======================================================================
class PhotonCounter:
    """在计数器上统计 TTL 上升沿，界面按积分时间轮询读累计数。

    设计上**只负责读数**：差分、计数率、画图、CSV 都在界面层。这样
    「计数器怎么配」和「怎么显示」互不纠缠，前者可以单独测。

    计数器是 32 位的，长时间运行会回绕；read() 返回原始累计值，
    差分时由调用方处理（见 `count_delta`）。
    """

    TRANSPORTS = ("local",)
    #: 32 位计数器满量程，回绕时差分要加回这个数
    COUNTER_MAX = 2 ** 32
    #: 积分时间允许范围（秒）
    BIN_MIN, BIN_MAX = 0.05, 60.0
    READ_TIMEOUT = 2.0

    def __init__(self, transport, *, log_callback: Callable[[str], None] | None = None,
                 counter: str | None = None, source: str | None = None):
        self.transport = transport
        self.resource = getattr(transport, "resource", "Dev1")
        self.counter = counter or f"{self.resource}/ctr0"
        self.source = source or f"/{self.resource}/PFI0"
        self._log_cb = log_callback
        self._lock = threading.RLock()
        self._task = None
        self._connected = False

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
    def connect(self):
        """只检查驱动并打开 transport；真正的 task 在 start() 里建。"""
        if not NIDAQMX_AVAILABLE:
            raise NIError("nidaqmx is not installed (pip install nidaqmx)")
        if not driver_available():
            raise NIError(
                "NI-DAQmx driver not found. Install it from National Instruments "
                "(the nidaqmx Python package alone is not enough), then replug "
                "the card / restart the machine.")
        self.transport.open()
        self._connected = True
        self._log(f"Photon counter ready: {self.counter}, source {self.source}")
        return self

    def close(self):
        self.stop()
        try:
            self.transport.close()
        except Exception:                            # noqa: BLE001
            pass
        self._connected = False

    # ------------------------------------------------------------------
    def clamp_bin(self, value) -> float:
        """把积分时间夹到允许范围；非数字抛 ValueError。"""
        try:
            seconds = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Not a valid integration time: {value!r}") from exc
        return min(max(seconds, self.BIN_MIN), self.BIN_MAX)

    def _close_task(self) -> None:
        if self._task is not None:
            try:
                self._task.close()
            except Exception:                        # noqa: BLE001
                pass
            self._task = None
            self._log("Counter task released")

    def start(self, counter: str | None = None, source: str | None = None) -> int:
        """创建边沿计数任务并开始计数，返回起始累计值。"""
        if not self._connected:
            raise NIError("Photon counter is not connected")
        with self._lock:
            self._close_task()
            if counter:
                self.counter = counter.strip()
            if source:
                self.source = source.strip()
            try:
                task = nidaqmx.Task()
            except Exception as exc:                 # noqa: BLE001
                raise NIError(f"could not create counter task: {exc}") from exc
            try:
                task.ci_channels.add_ci_count_edges_chan(
                    counter=self.counter, edge=Edge.RISING, initial_count=0)
                task.ci_channels.all.ci_count_edges_term = self.source
                task.start()
            except DaqError as exc:
                try:
                    task.close()
                except Exception:                    # noqa: BLE001
                    pass
                raise NIError(
                    f"could not start counting on {self.counter} "
                    f"(source {self.source}): {exc}") from exc
            self._task = task
        first = self.read()
        self._log(f"Counting on {self.counter}, source {self.source}")
        return first

    def read(self) -> int:
        """读当前累计边沿数（未处理的原始值）。"""
        with self._lock:
            if self._task is None:
                raise NIError("Counter task is not running")
            try:
                value = self._task.read(timeout=self.READ_TIMEOUT)
            except DaqError as exc:
                raise NIError(f"read failed: {exc}") from exc
        if isinstance(value, (list, tuple)):
            value = value[0]
        return int(value)

    @staticmethod
    def count_delta(previous: int, current: int) -> int:
        """两次读数之间的计数差，处理 32 位回绕。"""
        delta = current - previous
        if delta < 0:
            delta += PhotonCounter.COUNTER_MAX
        return delta

    def stop(self) -> str | None:
        """停止计数并释放 task；返回错误信息（没有则 None）。"""
        error: str | None = None
        with self._lock:
            if self._task is not None:
                try:
                    self._task.stop()
                except Exception as exc:             # noqa: BLE001
                    error = str(exc)
                    log.warning("failed to stop counter task: %s", exc)
                self._close_task()
        return error


class PlaceholderPhotonCounter:
    """模拟一个光子计数器：按固定平均速率生成泊松计数，不碰硬件。"""

    TRANSPORTS = ("local",)
    COUNTER_MAX = PhotonCounter.COUNTER_MAX
    BIN_MIN, BIN_MAX = PhotonCounter.BIN_MIN, PhotonCounter.BIN_MAX

    #: 模拟的平均计数率（cps）
    RATE = 12000.0

    def __init__(self, transport, *, log_callback: Callable[[str], None] | None = None,
                 counter: str | None = None, source: str | None = None):
        self.transport = transport
        self.resource = getattr(transport, "resource", "Dev1")
        self.counter = counter or f"{self.resource}/ctr0"
        self.source = source or f"/{self.resource}/PFI0"
        self._log_cb = log_callback
        self._connected = False
        self._running = False
        self._t0 = 0.0
        self._count = 0

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
        self._connected = True
        self._log(f"Placeholder photon counter ready ({self.counter}). "
                  "No hardware is driven.")
        return self

    def close(self):
        self.stop()
        self._connected = False

    def clamp_bin(self, value) -> float:
        try:
            seconds = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Not a valid integration time: {value!r}") from exc
        return min(max(seconds, self.BIN_MIN), self.BIN_MAX)

    def start(self, counter: str | None = None, source: str | None = None) -> int:
        import time as _time
        if counter:
            self.counter = counter.strip()
        if source:
            self.source = source.strip()
        self._t0 = _time.perf_counter()
        self._count = 0
        self._running = True
        self._log(f"Placeholder counting on {self.counter} "
                  f"(source {self.source}), ~{self.RATE:,.0f} cps")
        return 0

    def read(self) -> int:
        import random
        import time as _time
        if not self._running:
            raise NIError("Counter task is not running")
        elapsed = _time.perf_counter() - self._t0
        # 按平均速率累积，带一点泊松起伏让曲线看起来真实
        expected = self.RATE * elapsed
        jitter = random.gauss(0.0, (expected ** 0.5) if expected > 0 else 0.0)
        self._count = max(0, int(expected + jitter))
        return self._count

    @staticmethod
    def count_delta(previous: int, current: int) -> int:
        return PhotonCounter.count_delta(previous, current)

    def stop(self) -> str | None:
        self._running = False
        self._log("Placeholder counter stopped")
        return None


# ======================================================================
# 脉冲输出：计数器输出
# ======================================================================
class PulseGenerator:
    """在计数器上产生连续脉冲串（频率 + 占空比）。"""

    TRANSPORTS = ("local",)
    FREQ_MIN, FREQ_MAX = 1.0, 1_000_000.0
    DUTY_MIN, DUTY_MAX = 0.1, 99.9

    def __init__(self, transport, *, log_callback: Callable[[str], None] | None = None,
                 counter: str | None = None):
        self.transport = transport
        self.resource = getattr(transport, "resource", "Dev1")
        self.counter = counter or f"{self.resource}/ctr0"
        self._log_cb = log_callback
        self._lock = threading.RLock()
        self._task = None
        self._connected = False
        #: 当前正在输出的参数；None 表示没在输出
        self.last_params: tuple[float, float] | None = None

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

    def connect(self):
        if not NIDAQMX_AVAILABLE:
            raise NIError("nidaqmx is not installed (pip install nidaqmx)")
        if not driver_available():
            raise NIError(
                "NI-DAQmx driver not found. Install it from National Instruments "
                "(the nidaqmx Python package alone is not enough), then replug "
                "the card / restart the machine.")
        self.transport.open()
        self._connected = True
        self._log(f"Pulse generator ready: {self.counter}")
        return self

    def close(self):
        self.stop()
        try:
            self.transport.close()
        except Exception:                            # noqa: BLE001
            pass
        self._connected = False

    # ------------------------------------------------------------------
    def check_params(self, freq, duty) -> tuple[float, float]:
        """校验频率与占空比；越界或非数字抛 ValueError。"""
        try:
            f = float(freq)
            d = float(duty)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Frequency and duty must be numbers, got {freq!r}, {duty!r}"
            ) from exc
        if not (self.FREQ_MIN <= f <= self.FREQ_MAX):
            raise ValueError(
                f"Frequency must be {self.FREQ_MIN:g} to {self.FREQ_MAX:g} Hz, "
                f"got {f:g}")
        if not (self.DUTY_MIN <= d <= self.DUTY_MAX):
            raise ValueError(
                f"Duty cycle must be {self.DUTY_MIN:g} to {self.DUTY_MAX:g} %, "
                f"got {d:g}")
        return f, d

    def _close_task(self) -> None:
        if self._task is not None:
            try:
                self._task.close()
            except Exception:                        # noqa: BLE001
                pass
            self._task = None
            self._log("Pulse task released")

    def start(self, freq, duty, counter: str | None = None) -> tuple[float, float]:
        """启动脉冲串；已经在跑就先干净地重启。返回实际采用的参数。"""
        if not self._connected:
            raise NIError("Pulse generator is not connected")
        f, d = self.check_params(freq, duty)
        with self._lock:
            self._close_task()
            if counter:
                self.counter = counter.strip()
            try:
                task = nidaqmx.Task()
            except Exception as exc:                 # noqa: BLE001
                raise NIError(f"could not create pulse task: {exc}") from exc
            try:
                task.co_channels.add_co_pulse_chan_freq(
                    self.counter, freq=f, duty_cycle=d / 100.0)
                task.timing.cfg_implicit_timing(
                    sample_mode=AcquisitionType.CONTINUOUS)
                task.start()
            except DaqError as exc:
                try:
                    task.close()
                except Exception:                    # noqa: BLE001
                    pass
                raise NIError(
                    f"could not start pulses on {self.counter}: {exc}") from exc
            self._task = task
        self.last_params = (f, d)
        self._log(f"Pulsing on {self.counter}: {f:g} Hz, {d:g} % duty")
        return f, d

    def stop(self) -> str | None:
        """停止脉冲串；返回错误信息（没有则 None）。"""
        error: str | None = None
        with self._lock:
            if self._task is not None:
                try:
                    self._task.stop()
                    self._log("Pulse train stopped")
                except Exception as exc:             # noqa: BLE001
                    error = str(exc)
                    log.warning("failed to stop pulse task: %s", exc)
                self._close_task()
        self.last_params = None
        return error

    def status_text(self) -> str:
        if not self._connected:
            return "Idle (not connected)"
        if self.last_params is None or self._task is None:
            return "Idle (pulse not running)"
        f, d = self.last_params
        return f"Pulsing on {self.counter}: {f:g} Hz, {d:g} % duty"


class PlaceholderPulseGenerator:
    """模拟脉冲发生器：只记录参数，不碰硬件。"""

    TRANSPORTS = ("local",)
    FREQ_MIN, FREQ_MAX = PulseGenerator.FREQ_MIN, PulseGenerator.FREQ_MAX
    DUTY_MIN, DUTY_MAX = PulseGenerator.DUTY_MIN, PulseGenerator.DUTY_MAX

    def __init__(self, transport, *, log_callback: Callable[[str], None] | None = None,
                 counter: str | None = None):
        self.transport = transport
        self.resource = getattr(transport, "resource", "Dev1")
        self.counter = counter or f"{self.resource}/ctr0"
        self._log_cb = log_callback
        self._connected = False
        self.last_params: tuple[float, float] | None = None

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
        self._connected = True
        self._log(f"Placeholder pulse generator ready ({self.counter}). "
                  "No hardware is driven.")
        return self

    def close(self):
        self.stop()
        self._connected = False

    def check_params(self, freq, duty) -> tuple[float, float]:
        return PulseGenerator.check_params(self, freq, duty)

    def start(self, freq, duty, counter: str | None = None) -> tuple[float, float]:
        f, d = self.check_params(freq, duty)
        if counter:
            self.counter = counter.strip()
        self.last_params = (f, d)
        self._log(f"Placeholder pulsing on {self.counter}: {f:g} Hz, {d:g} % duty")
        return f, d

    def stop(self) -> str | None:
        self.last_params = None
        self._log("Placeholder pulse stopped")
        return None

    def status_text(self) -> str:
        if not self._connected:
            return "Idle (not connected)"
        if self.last_params is None:
            return "Idle (pulse not running)"
        f, d = self.last_params
        return f"Pulsing on {self.counter}: {f:g} Hz, {d:g} % duty"
