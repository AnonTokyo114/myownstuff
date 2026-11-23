"""ASI LX-4000 Stage Controller —— 单文件版（自动生成，勿直接编辑）。

来源：https://github.com/AnonTokyo114/myownstuff
生成：python tools/build_stage_standalone.py

这是一个**自包含**的副本：把整个项目的依赖内联进了一个文件，
方便直接拷到别的机器上，不需要项目目录结构。

运行需要：
    pip install PySide6-Essentials pyserial

用法：
    python stage_standalone.py

与主项目的差异：只保留 Stage Controller，没有主 GUI 的其它页面。
改了主项目代码后请重新生成，不要在这里手改。
"""

from __future__ import annotations

import os
import sys

import select
import socket
import time
from abc import ABC, abstractmethod
import logging
import threading
from typing import Callable
from dataclasses import dataclass
from pathlib import Path
import sys

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import QSettings
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


# ====================================================================
#  传输层：Serial / LAN / GPIB / Local   （源：core/transport.py）
# ====================================================================
import select
import socket
import time
from abc import ABC, abstractmethod


class TransportError(RuntimeError):
    """传输层错误（打不开、断开、读超时等）。"""


def available_serial_ports() -> list[tuple[str, str]]:
    """返回 [(端口名, 描述)]；无串口时为空列表。"""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    out = []
    for port in list_ports.comports():
        out.append((port.device, port.description or ""))
    return out


# ======================================================================
# 基类
# ======================================================================
class Transport(ABC):
    """一条已配置但可能尚未打开的连接。"""

    #: 人类可读的类型名（"serial" / "lan" / "gpib"）
    kind = "transport"
    #: 用于日志/界面的简短描述
    description = ""

    @abstractmethod
    def open(self) -> None:
        ...

    @abstractmethod
    def close(self) -> None:
        ...

    @property
    @abstractmethod
    def is_open(self) -> bool:
        ...

    @abstractmethod
    def write(self, data: bytes) -> None:
        ...

    def flush(self) -> None:
        """默认无操作；串口会真正 flush。"""

    @abstractmethod
    def read_until(self, terminator: bytes) -> bytes:
        ...

    @abstractmethod
    def read(self, n: int) -> bytes:
        ...

    @property
    @abstractmethod
    def in_waiting(self) -> int:
        ...

    @abstractmethod
    def reset_buffers(self) -> None:
        ...

    def set_timeout(self, seconds: float) -> None:
        """调整读超时（秒）。

        默认实现假设子类有 ``self.timeout`` 字段；用别的单位的子类
        （例如 GPIB 用毫秒）需要覆盖这个方法。
        """
        self.timeout = float(seconds)

    def __repr__(self) -> str:              # pragma: no cover - 调试用
        return f"<{type(self).__name__} {self.description}>"


# ======================================================================
# 串口
# ======================================================================
class SerialTransport(Transport):
    kind = "serial"

    def __init__(self, port: str, baudrate: int = 115200,
                 timeout: float = 2.0, *, bytesize: int = 8,
                 parity: str = "N", stopbits: float = 1,
                 write_timeout: float = 2.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.write_timeout = write_timeout
        self.description = f"{port} @ {baudrate}"
        self._ser = None

    def open(self) -> None:
        try:
            import serial
        except ImportError as exc:                   # pragma: no cover
            raise TransportError(
                "pyserial is not installed (pip install pyserial)") from exc

        ser = serial.Serial()
        ser.port = self.port
        ser.baudrate = self.baudrate
        ser.bytesize = serial.EIGHTBITS
        ser.parity = serial.PARITY_NONE
        ser.stopbits = serial.STOPBITS_ONE
        ser.timeout = self.timeout
        ser.write_timeout = self.write_timeout
        ser.xonxoff = False
        ser.rtscts = False
        ser.dsrdtr = False
        try:
            ser.open()
        except Exception as exc:                     # noqa: BLE001
            raise TransportError(f"could not open port {self.port!r}: {exc}") from exc
        self._ser = ser

    def close(self) -> None:
        if self._ser is not None:
            try:
                if self._ser.is_open:
                    self._ser.close()
            except Exception:                        # noqa: BLE001
                pass
            self._ser = None

    @property
    def is_open(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def _require(self):
        if not self.is_open:
            raise TransportError(f"serial port {self.port!r} is not open")
        return self._ser

    def write(self, data: bytes) -> None:
        ser = self._require()
        try:
            ser.write(data)
        except Exception as exc:                     # noqa: BLE001
            raise TransportError(f"serial write failed: {exc}") from exc

    def flush(self) -> None:
        if self.is_open:
            try:
                self._ser.flush()
            except Exception:                        # noqa: BLE001
                pass

    def read_until(self, terminator: bytes) -> bytes:
        ser = self._require()
        try:
            return ser.read_until(terminator)
        except Exception as exc:                     # noqa: BLE001
            raise TransportError(f"serial read failed: {exc}") from exc

    def read(self, n: int) -> bytes:
        ser = self._require()
        try:
            return ser.read(n)
        except Exception as exc:                     # noqa: BLE001
            raise TransportError(f"serial read failed: {exc}") from exc

    @property
    def in_waiting(self) -> int:
        if not self.is_open:
            return 0
        try:
            return int(self._ser.in_waiting)
        except Exception:                            # noqa: BLE001
            return 0

    def set_timeout(self, seconds: float) -> None:
        """同时改我们记录的秒数和 pyserial 的实际读超时。"""
        self.timeout = float(seconds)
        if self._ser is not None:
            try:
                self._ser.timeout = float(seconds)
            except Exception:                        # noqa: BLE001
                pass

    def reset_buffers(self) -> None:
        if not self.is_open:
            return
        for name in ("reset_input_buffer", "reset_output_buffer"):
            fn = getattr(self._ser, name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:                    # noqa: BLE001
                    pass


# ======================================================================
# 网口（原始 TCP）
# ======================================================================
class LanTransport(Transport):
    kind = "lan"

    def __init__(self, host: str, port: int = 5025, timeout: float = 3.0):
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self.description = f"{host}:{self.port}"
        self._sock: socket.socket | None = None
        self._buf = b""

    def open(self) -> None:
        try:
            sock = socket.create_connection((self.host, self.port),
                                            timeout=self.timeout)
            sock.settimeout(self.timeout)
        except OSError as exc:
            raise TransportError(
                f"could not connect to {self.host}:{self.port}: {exc}") from exc
        self._sock = sock
        self._buf = b""

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._buf = b""

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    def _require(self):
        if self._sock is None:
            raise TransportError(f"LAN connection to {self.description} is not open")
        return self._sock

    def _pump(self, wait: float) -> bool:
        """把 socket 上可读的数据搬进内部缓冲；返回是否读到了东西。"""
        sock = self._require()
        try:
            ready, _, _ = select.select([sock], [], [], wait)
        except OSError as exc:
            raise TransportError(f"select failed: {exc}") from exc
        if not ready:
            return False
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            return False
        except OSError as exc:
            raise TransportError(f"recv failed: {exc}") from exc
        if not chunk:
            raise TransportError("connection closed by the peer")
        self._buf += chunk
        return True

    def write(self, data: bytes) -> None:
        sock = self._require()
        try:
            sock.sendall(data)
        except OSError as exc:
            raise TransportError(f"send failed: {exc}") from exc

    def read_until(self, terminator: bytes) -> bytes:
        self._require()
        deadline = time.monotonic() + self.timeout
        while terminator not in self._buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if not self._pump(min(0.05, remaining)):
                continue
        index = self._buf.find(terminator)
        if index == -1:
            out, self._buf = self._buf, b""
            return out
        end = index + len(terminator)
        out, self._buf = self._buf[:end], self._buf[end:]
        return out

    def read(self, n: int) -> bytes:
        self._require()
        deadline = time.monotonic() + self.timeout
        while len(self._buf) < n and time.monotonic() < deadline:
            self._pump(0.05)
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    @property
    def in_waiting(self) -> int:
        if self._sock is None:
            return 0
        try:
            self._pump(0)
        except TransportError:
            return len(self._buf)
        return len(self._buf)

    def reset_buffers(self) -> None:
        if self._sock is None:
            return
        self._buf = b""
        # 把对端已经发出来的数据也丢掉
        for _ in range(16):
            try:
                if not self._pump(0):
                    break
            except TransportError:
                break
        self._buf = b""


# ======================================================================
# GPIB / VISA
# ======================================================================
def visa_available() -> bool:
    """pyvisa 是否可用（不代表机器上有 GPIB 硬件）。"""
    try:
        import pyvisa  # noqa: F401
        return True
    except Exception:                                # pragma: no cover
        return False


def list_visa_resources() -> list[str]:
    """列出 VISA 资源；没有 pyvisa 或后端时报空列表。"""
    try:
        import pyvisa
    except ImportError:
        return []
    try:
        return list(pyvisa.ResourceManager().list_resources())
    except Exception:                                # noqa: BLE001
        return []


class GpibTransport(Transport):
    """通过 pyvisa 访问 GPIB 仪器（地址形如 GPIB0::12::INSTR）。"""

    kind = "gpib"

    def __init__(self, address: str, timeout_ms: int = 3000):
        self.address = address
        self.timeout_ms = int(timeout_ms)
        self.description = address
        self._res = None
        self._buf = b""

    @staticmethod
    def available() -> bool:
        return visa_available()

    def open(self) -> None:
        try:
            import pyvisa
        except ImportError as exc:
            raise TransportError(
                "pyvisa is not installed (pip install pyvisa pyvisa-py)") from exc
        try:
            rm = pyvisa.ResourceManager()
            res = rm.open_resource(self.address)
            # 多数 SCPI 仪器用 \n 结尾；这里不做假设，交给 default 设置
            try:
                res.read_termination = "\n"
            except Exception:                        # noqa: BLE001
                pass
            try:
                res.timeout = self.timeout_ms
            except Exception:                        # noqa: BLE001
                pass
        except Exception as exc:                     # noqa: BLE001
            raise TransportError(
                f"could not open VISA resource {self.address!r}: {exc}") from exc
        self._res = res
        self._buf = b""

    def close(self) -> None:
        if self._res is not None:
            try:
                self._res.close()
            except Exception:                        # noqa: BLE001
                pass
            self._res = None
        self._buf = b""

    @property
    def is_open(self) -> bool:
        return self._res is not None

    def _require(self):
        if self._res is None:
            raise TransportError(
                f"VISA resource {self.address!r} is not open")
        return self._res

    def write(self, data: bytes) -> None:
        res = self._require()
        try:
            res.write_raw(data)
        except Exception as exc:                     # noqa: BLE001
            raise TransportError(f"GPIB write failed: {exc}") from exc

    def read_until(self, terminator: bytes) -> bytes:
        """VISA 自己按终止符切分消息，这里拿到一条完整回复。"""
        res = self._require()
        try:
            raw = res.read_raw()
        except Exception as exc:                     # noqa: BLE001
            raise TransportError(f"GPIB read failed: {exc}") from exc
        return self._buf + raw

    def read(self, n: int) -> bytes:
        self._require()
        if not self._buf:
            try:
                self._buf = self._res.read_raw()
            except Exception as exc:                 # noqa: BLE001
                raise TransportError(f"GPIB read failed: {exc}") from exc
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    @property
    def in_waiting(self) -> int:
        if self._res is None:
            return len(self._buf)
        # 不同后端支持程度不同；取不到就当没有积压
        try:
            return int(getattr(self._res, "bytes_in_buffer", 0)) + len(self._buf)
        except Exception:                            # noqa: BLE001
            return len(self._buf)

    def set_timeout(self, seconds: float) -> None:
        """VISA 用毫秒，这里换算一下并同步到已打开的资源。"""
        self.timeout_ms = max(1, int(float(seconds) * 1000))
        if self._res is not None:
            try:
                self._res.timeout = self.timeout_ms
            except Exception:                        # noqa: BLE001
                pass

    def reset_buffers(self) -> None:
        self._buf = b""
        if self._res is None:
            return
        fn = getattr(self._res, "clear", None)
        if callable(fn):
            try:
                fn()
            except Exception:                        # noqa: BLE001
                pass


# ======================================================================
# 本地硬件（板卡驱动直接访问，不走字节流）
# ======================================================================
class LocalTransport(Transport):
    """本地板卡：不传输字节，只携带一个资源名（如 NI-DAQ 的 "Dev1"）。

    NI-DAQ / 采集卡这类设备由厂商驱动直接访问，没有串口/网口协议。
    这里用一个占位 transport，让它们也能套用「选设备 → 连接」的统一流程。
    """

    kind = "local"

    def __init__(self, resource: str = "Dev1"):
        self.resource = resource
        self.description = resource or "(default)"
        self._open = False

    def open(self) -> None:
        # 真正的可用性检查在设备自己的 connect() 里（要 import 厂商库）
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def _no_bytes(self, what: str):
        raise TransportError(
            f"local transport does not carry byte streams ({what} is not supported)")

    def write(self, data: bytes) -> None:
        self._no_bytes("write")

    def read_until(self, terminator: bytes) -> bytes:
        self._no_bytes("read_until")

    def read(self, n: int) -> bytes:
        self._no_bytes("read")

    @property
    def in_waiting(self) -> int:
        return 0

    def reset_buffers(self) -> None:
        return


# ======================================================================
# 工厂
# ======================================================================
def make_transport(kind: str, **params) -> Transport:
    """按连接方式构造 transport；未知类型抛 TransportError。"""
    kind = (kind or "").lower()
    if kind == "serial":
        return SerialTransport(
            port=params.get("port", "COM6"),
            baudrate=int(params.get("baudrate", 115200)),
            timeout=float(params.get("timeout", 2.0)),
        )
    if kind == "lan":
        return LanTransport(
            host=params.get("host", "127.0.0.1"),
            port=int(params.get("port", 5025)),
            timeout=float(params.get("timeout", 3.0)),
        )
    if kind == "gpib":
        return GpibTransport(
            address=params.get("address", "GPIB0::12::INSTR"),
            timeout_ms=int(params.get("timeout_ms", 3000)),
        )
    if kind == "local":
        return LocalTransport(resource=str(params.get("resource", "Dev1")))
    raise TransportError(f"unknown transport kind: {kind!r}")


# ====================================================================
#  ASI LX-4000 位移台驱动   （源：core/stage.py）
# ====================================================================
import logging
import threading
import time
from typing import Callable


log = logging.getLogger("asi_lx_4000")


class ASIError(RuntimeError):
    """ASI 位移台错误的基类。"""


class ASICommError(ASIError):
    """控制器无响应、回复乱码，或拒绝了命令。"""


class ASIFloodError(ASICommError):
    """控制器在持续推送未经请求的字节（例如 'F\\x15F\\x15...'）。"""


class ASIHalted(ASIError):
    """操作被 HALT 取消。

    单独成类，是为了让界面能把「用户主动急停」和「通讯/硬件错误」
    区分开：前者不该弹红色错误框，只需说明操作已取消。
    """


#: 默认连接参数（原 Parameter 定义，此处仅作默认值使用）
DEFAULT_SETTINGS = {
    "connection_type": "SERIAL",
    "connection_port": "COM6",
    "baud_rate": 115200,
    "bytesize": 8,
    "parity": "None",
    "stop_bits": "one",
    "timeout": 10,
    "unit": 0.1,          # 位置单位：0.1 µm
}


class ASI_lx_4000:
    ADDRESS_PREFIX = "2H "
    EXPECTED_WHO = "ASI-MS2000"        # WHO 回复里应包含的子串

    #: X/Y 软件限位（0.1 µm）
    NEG_LIMIT = -999999
    POS_LIMIT = 999999
    #: 实测中心位置（0.1 µm），center_and_zero 会用它
    CENTER_X = 451906
    CENTER_Y = 430490

    #: 界面用：这个设备支持哪些连接方式
    TRANSPORTS = ("serial",)

    #: 位置单位。这是 **这个控制器** 的属性，不是通用常量：
    #: ASI LX-4000 的一个计数就是 0.1 µm，换别的位移台未必如此。
    UNIT_LABEL = "0.1 µm"
    #: 一个计数等于多少微米；None 表示没有明确的物理换算
    UNIT_IN_UM = 0.1

    def __init__(self, transport: Transport, *,
                 settle_time: float = 1.5,
                 connect_attempts: int = 3,
                 verbose: bool = False,
                 auto_connect: bool = True,
                 log_callback: Callable[[str], None] | None = None):
        self.transport = transport
        self.settle_time = settle_time
        self.connect_attempts = connect_attempts
        self.verbose = verbose
        self._lock = threading.RLock()
        self._log_cb = log_callback
        #: HALT 置位；运动流程在每个关键点检查它并抛 ASIHalted
        self._abort = threading.Event()

        if auto_connect:
            self.connect()

    # ------------------------------------------------------------------
    # 取消（HALT）状态
    # ------------------------------------------------------------------
    def begin_operation(self) -> None:
        """用户发起一次新的运动操作：清掉上一次的取消标志。

        只由「用户主动开始」的入口调用（go_* / set_position / jog /
        center_and_zero），内部步骤不会清，否则 HALT 会被下一步抵消。
        """
        self._abort.clear()

    def _check_abort(self) -> None:
        if self._abort.is_set():
            raise ASIHalted("Operation cancelled by HALT.")

    @property
    def is_halted(self) -> bool:
        return self._abort.is_set()

    # ------------------------------------------------------------------
    # 日志
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
        return self.transport is not None and self.transport.is_open

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------
    def _handshake(self) -> None:
        last = ""
        for attempt in range(1, 6):
            self.transport.write(b"\r")   # 单独的 CR 冲掉半条未收完的命令
            self.transport.flush()
            time.sleep(0.3)
            self.clearbuffer(max_seconds=2.0)
            last = self._transact("WHO")
            self._log(f"Handshake attempt {attempt}: {last!r}")
            if self.EXPECTED_WHO in last:
                return
            time.sleep(0.5)
        raise ASICommError(f"Handshake failed. Last reply to WHO: {last!r}")

    def connect(self):
        """打开传输、握手、使能电机。失败抛 ASICommError。"""
        with self._lock:
            if self.is_connected:
                return self.transport

            self._log(f"Opening {self.transport.kind} transport "
                      f"({self.transport.description}) ...")
            if self.transport.kind == "serial":
                self._log("Make sure the controller is powered on and warmed up "
                          "(~60 seconds).")

            last_err = None
            for attempt in range(1, self.connect_attempts + 1):
                try:
                    self.transport.open()
                    time.sleep(self.settle_time)   # 适配器刚打开时容易抽一下
                    self.transport.reset_buffers()
                    self._handshake()
                    reply = self._transact("MC X+ Y+")     # 使能电机
                    self._check_ok(reply, "MC X+ Y+")
                    self._log("ASI LX 4000 initialized successfully.")
                    return self.transport
                except (ASICommError, TransportError) as exc:
                    last_err = exc
                    log.error("Connect attempt %d/%d failed: %s",
                              attempt, self.connect_attempts, exc)
                    self._log(f"Connect attempt {attempt}/"
                              f"{self.connect_attempts} failed: {exc}")
                    try:
                        self.transport.close()
                    except Exception:            # noqa: BLE001
                        pass
                time.sleep(2.0)

            raise ASICommError(
                f"Could not communicate with the ASI controller after "
                f"{self.connect_attempts} attempts: {last_err}\n"
                "Try: power-cycle the controller (wait 60 s), replug the cable, "
                "check the port / address, close other programs using it."
            )

    def _require_connection(self) -> Transport:
        if not self.is_connected:
            raise ASICommError(
                "No open connection to ASI LX-4000. Call connect() first.")
        return self.transport

    # ------------------------------------------------------------------
    # 底层通信
    # ------------------------------------------------------------------
    @staticmethod
    def _sanitize(raw: bytes) -> str:
        """只保留可打印 ASCII（丢掉 \\x15 / \\x17 / \\x07 / \\x03 / CR / LF 等）。"""
        return "".join(chr(b) for b in raw if 32 <= b < 127).strip()

    def clearbuffer(self, max_seconds: float = 1.0, quiet: float = 0.1) -> int:
        """丢弃待读数据，直到线路安静 `quiet` 秒。

        超过 `max_seconds` 仍有数据 -> ASIFloodError（原版这里会无限循环）。
        """
        transport = self._require_connection()
        deadline = time.monotonic() + max_seconds
        quiet_start = time.monotonic()
        total = 0
        last = b""
        while time.monotonic() < deadline:
            n = transport.in_waiting
            if n:
                last = transport.read(n)
                total += len(last)
                quiet_start = time.monotonic()
            elif time.monotonic() - quiet_start >= quiet:
                if total:
                    log.debug("Cleared %d stray bytes, last=%r", total, last)
                return total
            else:
                time.sleep(0.02)
        raise ASIFloodError(
            f"Controller is streaming unsolicited data ({total} bytes in "
            f"{max_seconds}s, last={last[-20:]!r}). "
            "Replug the cable / power-cycle the controller."
        )

    def _transact(self, command: str) -> str:
        """发一条命令，返回清洗后的回复行（可能为空串）。"""
        with self._lock:
            transport = self._require_connection()
            dropped = self.clearbuffer()
            transport.write(f"{self.ADDRESS_PREFIX}{command}\r".encode("ascii"))
            transport.flush()
            raw = transport.read_until(b"\n")    # 等完整一行（或超时）
            reply = self._sanitize(raw)
            log.debug("TX %r RX raw=%r clean=%r", command, raw, reply)
            if self.verbose:
                extra = f"  (dropped {dropped}B)" if dropped else ""
                self._log(f"TX {command!r}  RX {reply!r}"
                          f"  raw={raw!r}{extra}")
            return reply

    def query(self, original_input: str, retries: int = 2) -> str:
        """发命令（自动加 '2H ' 前缀），空回复重试，最终仍空则报错。"""
        last = ""
        for attempt in range(retries + 1):
            last = self._transact(original_input)
            if last:
                return last
            log.warning("Empty reply to %r (attempt %d/%d)",
                        original_input, attempt + 1, retries + 1)
            time.sleep(0.2)
        raise ASICommError(f"No valid reply to {original_input!r}")

    @staticmethod
    def _check_ok(reply: str, command: str) -> None:
        """':A' 表示接受，':N-x' 表示错误。"""
        if reply.startswith(":N"):
            raise ASICommError(f"Controller rejected {command!r}: {reply!r}")
        if not reply.startswith(":A"):
            raise ASICommError(f"Unexpected reply to {command!r}: {reply!r}")

    def halt(self) -> None:
        """急停：先置取消标志，再尽力发送 HALT。

        置标志在发命令之前，是为了保证即使命令没发出去（或等待中的流程
        刚好结束），后续步骤也不会继续运动。
        """
        self._abort.set()
        try:
            if self.is_connected:
                self.transport.write(f"{self.ADDRESS_PREFIX}HALT\r".encode("ascii"))
                self.transport.flush()
                log.warning("HALT sent (cancel flag set)")
                self._log("HALT sent — remaining motion steps will be cancelled")
        except Exception as exc:                     # noqa: BLE001
            log.error("HALT failed: %s", exc)

    # ------------------------------------------------------------------
    # 运动 / 位置
    # ------------------------------------------------------------------
    def wait_movement(self, timeout: float = 120.0, poll: float = 0.3,
                      honour_abort: bool = True) -> None:
        """等待 STATUS 变成 'N'（空闲）。回复异常或超时会抛错。

        honour_abort=True（默认）用于运动流程：HALT 后立刻抛 ASIHalted，
        不必等到超时。
        honour_abort=False 用于**只读**操作：HALT 之后仍然要能读当前位置，
        读位置既不该重启被取消的流程，也不该被它挡住。
        """
        deadline = time.monotonic() + timeout
        time.sleep(0.3)
        while time.monotonic() < deadline:
            if honour_abort and self._abort.is_set():
                raise ASIHalted("Motion was cancelled by HALT.")
            status = self.query("STATUS")
            token = status.replace(":A", "").strip()[:1]
            if self.verbose:
                self._log(f"STATUS: {status!r}")
            if token == "N":
                return
            if token != "B":
                raise ASICommError(f"Invalid STATUS reply: {status!r}")
            time.sleep(poll)
        raise TimeoutError(f"Stage did not stop moving within {timeout}s")

    def _parse_position(self, reply: str, axis: str) -> float:
        clean = reply.replace(":A", "").strip()
        if not clean:
            raise ASICommError(f"Empty reply from {axis} position query")
        try:
            return float(clean)
        except ValueError as exc:
            raise ASICommError(
                f"Could not parse {axis} position from: {reply!r}") from exc

    def _read_pos(self, axis: str) -> float:
        return self._parse_position(self.query(f"W {axis}"), axis)

    def _move_abs(self, axis: str, input_pos: float) -> float:
        if isinstance(input_pos, bool) or not isinstance(input_pos, (int, float)):
            raise TypeError(f"Input must be numeric, got {type(input_pos)}")

        if input_pos < self.NEG_LIMIT:
            self._log(f"Warning: input {input_pos} < {self.NEG_LIMIT} -> clamping")
            input_pos = self.NEG_LIMIT
        elif input_pos > self.POS_LIMIT:
            self._log(f"Warning: input {input_pos} > {self.POS_LIMIT} -> clamping")
            input_pos = self.POS_LIMIT

        self._check_abort()          # 发命令前
        self.wait_movement()
        self._check_abort()          # 等完之后（HALT 可能就发生在这期间）
        cmd = f"M {axis}={int(input_pos)}"
        self._check_ok(self.query(cmd), cmd)
        self.wait_movement()
        self._check_abort()          # 停下后再检查，避免序列继续
        return self._read_pos(axis)

    def get_x_pos(self) -> float:
        """当前 X 位置（0.1 µm）。只读，不受 HALT 影响。"""
        self.wait_movement(honour_abort=False)
        return self._read_pos("X")

    def get_y_pos(self) -> float:
        """当前 Y 位置（0.1 µm）。只读，不受 HALT 影响。"""
        self.wait_movement(honour_abort=False)
        return self._read_pos("Y")

    def go_x_pos(self, input_pos: float) -> float:
        self.begin_operation()
        return self._move_abs("X", input_pos)

    def go_y_pos(self, input_pos: float) -> float:
        self.begin_operation()
        return self._move_abs("Y", input_pos)

    # ------------------------------------------------------------------
    # 通用接口（界面只依赖这几个，便于换成其它设备）
    # ------------------------------------------------------------------
    def get_position(self, axis: str) -> float:
        axis = axis.lower()
        if axis == "x":
            return self.get_x_pos()
        if axis == "y":
            return self.get_y_pos()
        raise ValueError(
            f"ASI_lx_4000 only supports 'x' and 'y' axes, got '{axis}'")

    def set_position(self, axis: str, pos: float) -> None:
        self.begin_operation()
        axis_l = axis.lower()
        if axis_l == "x":
            self._move_abs("X", pos)
        elif axis_l == "y":
            self._move_abs("Y", pos)
        else:
            raise ValueError(
                f"ASI_lx_4000 only supports 'x' and 'y' axes, got '{axis}'")

    def jog(self, axis: str, delta: float) -> float:
        """相对移动：读当前位置再走 delta（0.1 µm），返回最终位置。"""
        self.begin_operation()
        axis_u = axis.lower()
        current = self.get_position(axis_u)
        self._check_abort()
        return self._move_abs(axis_u.upper(), current + delta)

    def _wait_stable(self, tolerance: float = 1.0, tries: int = 5) -> None:
        """要求相隔 0.5 s 的两次读数一致，才允许重定义原点。"""
        for _ in range(tries):
            x1, y1 = self.get_x_pos(), self.get_y_pos()
            time.sleep(0.5)
            x2, y2 = self.get_x_pos(), self.get_y_pos()
            if abs(x2 - x1) <= tolerance and abs(y2 - y1) <= tolerance:
                return
        raise ASIError("Stage position is not stable; refusing to redefine origin.")

    def def_origin(self) -> None:
        """把当前位置定义为原点。"""
        self._wait_stable()
        self._check_ok(self.query("Z"), "Z")

        xpos = self.get_x_pos()
        ypos = self.get_y_pos()

        if abs(xpos) > 0.05:
            self._log(f"Warning: zeroing failed for X. Current X = {xpos}")
        if abs(ypos) > 0.05:
            self._log(f"Warning: zeroing failed for Y. Current Y = {ypos}")

        if abs(xpos) <= 0.05 and abs(ypos) <= 0.05:
            self._log("Origin successfully set to (0, 0)")
        else:
            self._log("Origin definition completed with warnings.")

    def center_and_zero(self, prompt: bool = False) -> None:
        """负限位 -> 实测中心 -> 设为新原点。

        每次上电后只需跑一次；原点保持到断电。

        注意：内部一律走 _move_abs，不使用 go_* —— 后者会清除 HALT 标志，
        那样急停就只能停住当前一步，序列会接着往下走。
        """
        self.begin_operation()       # 清除上一次的取消状态
        self._log("=== WARNING ===")
        self._log("This will drive the stage to the negative hardware limits!")
        self._log("Make sure no microscope objectives will be hit!")
        self._log("===============")

        if prompt:
            input("Press Enter to continue (Ctrl+C to cancel)... ")

        self._log("Moving to negative limits...")
        x = self._move_abs("X", self.NEG_LIMIT)
        y = self._move_abs("Y", self.NEG_LIMIT)
        self._check_abort()
        self._log(f"Reached X={x}, Y={y}")

        self._log("Defining negative limit as temporary origin...")
        self.def_origin()
        self._check_abort()

        self._log("Moving to measured center...")
        self._move_abs("X", self.CENTER_X)
        self._move_abs("Y", self.CENTER_Y)
        self._check_abort()

        self._log("Defining center as new origin...")
        self.def_origin()

        self._log("Stage centered and origin redefined successfully.")

    # ------------------------------------------------------------------
    def close(self):
        """关闭传输。"""
        with self._lock:
            if self.is_connected:
                self.transport.close()
                self._log("Connection closed.")
            else:
                self.transport.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.halt()
        self.close()


# ====================================================================
#  NI-DAQ 模拟输出（Stage 页面不直接用，但设备注册表引用它）   （源：core/nidaq.py）
# ====================================================================
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
        #: 每 start 一次递增，用来标识「这次启动的那个任务」
        self._start_token = 0
        #: 最近一次 start 的 token（调用方用它做定向撤销）
        self.last_token = 0

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

    def start(self, counter: str | None = None,
              source: str | None = None) -> tuple[int, int]:
        """创建边沿计数任务并开始计数。

        返回 ``(起始累计值, token)``；token 标识这次启动的任务。
        """
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
            self._start_token += 1
            self.last_token = self._start_token
            token = self._start_token
        first = self.read()
        self._log(f"Counting on {self.counter}, source {self.source}")
        return first, token

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

    def stop_if_still(self, token: int) -> str | None:
        """只有当当前任务**还是** token 对应的那个时才停。

        取消一个过期的 start 时不能无条件 stop()：那会把后来者刚建好的
        任务一起停掉，而那个任务的界面还显示「在跑」。

        检查和停止必须在**同一把锁**里完成（_lock 是 RLock，可以重入）。
        分开写的话，中间新 start() 能抢到锁建好新任务，随后旧 stop()
        停的就是那个新任务 —— 这正是「已检查通过、尚未停止」的窗口。
        """
        with self._lock:
            if token != self._start_token:
                return None
            return self.stop()

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
        self._start_token = 0
        self.last_token = 0

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

    def start(self, counter: str | None = None,
              source: str | None = None) -> tuple[int, int]:
        import time as _time
        if counter:
            self.counter = counter.strip()
        if source:
            self.source = source.strip()
        self._t0 = _time.perf_counter()
        self._count = 0
        self._running = True
        self._start_token += 1
        self.last_token = self._start_token
        token = self._start_token
        self._log(f"Placeholder counting on {self.counter} "
                  f"(source {self.source}), ~{self.RATE:,.0f} cps")
        return 0, token

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

    def stop_if_still(self, token: int) -> str | None:
        if token != self._start_token:
            return None
        return self.stop()

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
        #: 每 start 一次递增，用来标识「这次启动的那个任务」
        self._start_token = 0
        #: 最近一次 start 的 token（调用方用它做定向撤销）
        self.last_token = 0

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

    def start(self, freq, duty,
              counter: str | None = None) -> tuple[float, float, int]:
        """启动脉冲串；已经在跑就先干净地重启。

        返回 ``(freq, duty, token)``。token 用来标识**这次**启动的任务，
        取消一个过期操作时靠它判断「该不该撤销」。
        """
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
            self._start_token += 1
            self.last_token = self._start_token
            token = self._start_token
        self.last_params = (f, d)
        self._log(f"Pulsing on {self.counter}: {f:g} Hz, {d:g} % duty")
        # token 必须随返回值一起给出：读 self.last_token 是不可靠的，
        # 另一个 start 可能已经把它改掉了（连续 Apply 时就会发生），
        # 于是「撤销自己」变成「撤销别人」。
        return f, d, token

    def stop_if_still(self, token: int) -> str | None:
        """只有当当前任务**还是** token 对应的那个时才停。

        连续 Apply 时，旧操作发现自己的编号过期了，于是「撤销」自己启动
        的任务 —— 但那时候新任务已经把它顶掉了，无条件 stop() 停掉的是
        新任务，界面却还显示在跑。

        检查和停止必须在**同一把锁**里完成（_lock 是 RLock，可以重入）。
        分开写的话，中间新 start() 能抢到锁建好新任务，随后旧 stop()
        停的就是那个新任务。
        """
        with self._lock:
            if token != self._start_token:
                return None
            return self.stop()

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
        self._start_token = 0
        self.last_token = 0

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

    def start(self, freq, duty,
              counter: str | None = None) -> tuple[float, float, int]:
        f, d = self.check_params(freq, duty)
        if counter:
            self.counter = counter.strip()
        self.last_params = (f, d)
        self._start_token += 1
        self.last_token = self._start_token
        token = self._start_token
        self._log(f"Placeholder pulsing on {self.counter}: {f:g} Hz, {d:g} % duty")
        return f, d, token

    def stop_if_still(self, token: int) -> str | None:
        if token != self._start_token:
            return None
        return self.stop()

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


# ====================================================================
#  设备注册表   （源：core/devices.py）
# ====================================================================
import time
from dataclasses import dataclass
from typing import Callable



# ======================================================================
# 占位设备
# ======================================================================
class PlaceholderDevice:
    """假设备：连接与操作都立即成功，位置在内存里加减。

    用途是让界面在无硬件时也能完整走一遍流程（选设备 → Connect → 移动）。
    """

    TRANSPORTS = ("serial", "lan", "gpib")

    #: 占位设备没有真实物理单位，用 counts 表示「计数」
    UNIT_LABEL = "counts"
    UNIT_IN_UM = None

    def __init__(self, transport: Transport, *,
                 log_callback: Callable[[str], None] | None = None,
                 **options):
        self.transport = transport
        self._log_cb = log_callback
        self._connected = False
        self._pos = {"x": 0.0, "y": 0.0}

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
        self._log(f"Placeholder device: pretending to connect over "
                  f"{self.transport.kind} ({self.transport.description})")
        self._connected = True
        self._log("Placeholder connected. No real hardware is being driven.")
        return self

    def close(self):
        if self._connected:
            self._log("Placeholder disconnected.")
        self._connected = False

    def _require(self):
        if not self._connected:
            raise RuntimeError("Placeholder device is not connected")

    # --- 位置接口（与真实设备一致）---
    def get_position(self, axis: str) -> float:
        self._require()
        return float(self._pos.get(axis.lower(), 0.0))

    def set_position(self, axis: str, pos: float) -> None:
        self._require()
        axis = axis.lower()
        self._log(f"Placeholder move {axis.upper()} -> {pos:.0f}")
        time.sleep(0.05)                             # 假装动了一下
        self._pos[axis] = float(pos)

    def jog(self, axis: str, delta: float) -> float:
        current = self.get_position(axis)
        self.set_position(axis, current + delta)
        return self.get_position(axis)

    def get_x_pos(self) -> float:
        return self.get_position("x")

    def get_y_pos(self) -> float:
        return self.get_position("y")

    def go_x_pos(self, pos: float) -> float:
        self.set_position("x", pos)
        return self.get_position("x")

    def go_y_pos(self, pos: float) -> float:
        self.set_position("y", pos)
        return self.get_position("y")

    def halt(self) -> None:
        self._log("Placeholder HALT (nothing is moving)")

    def def_origin(self) -> None:
        self._require()
        self._pos = {"x": 0.0, "y": 0.0}
        self._log("Placeholder origin defined (0, 0)")

    def center_and_zero(self, prompt: bool = False) -> None:
        self._require()
        self._pos = {"x": 0.0, "y": 0.0}
        self._log("Placeholder centre-and-zero complete (no motion)")


# ======================================================================
# 规格
# ======================================================================
@dataclass(frozen=True)
class DeviceSpec:
    key: str
    name: str
    #: 支持的连接方式，顺序即界面下拉顺序
    transports: tuple[str, ...]
    description: str
    #: (transport, **options) -> 设备对象
    factory: Callable[..., object]


def _make_stage(transport: Transport, **options):
    return ASI_lx_4000(transport, auto_connect=False, **options)


def _make_placeholder(transport: Transport, **options):
    return PlaceholderDevice(transport, **options)


def _make_filterwheel(transport: Transport, **options):
    return fw_mod.ThorlabsFW102C(transport, **options)


def _make_placeholder_fw(transport: Transport, **options):
    return fw_mod.PlaceholderFilterWheel(transport, **options)


def _make_nidaq(transport: Transport, **options):
    return NIDaqDevice(transport, **options)


def _make_placeholder_daq(transport: Transport, **options):
    return PlaceholderDaq(transport, **options)


#: 各个子类目下可选的设备。新增设备往这里加一条即可。
DEVICES: dict[str, list[DeviceSpec]] = {
    "stage": [
        DeviceSpec(
            key="asi_lx4000",
            name="ASI LX-4000",
            transports=("serial",),
            description="Two-axis translation stage, ASI MS-2000 controller.",
            factory=_make_stage,
        ),
        DeviceSpec(
            key="placeholder",
            name="Placeholder device",
            transports=("serial", "lan", "gpib"),
            description="No hardware. Lets you try the connect / move flow.",
            factory=_make_placeholder,
        ),
    ],
}

#: 其它子类目暂时只有占位设备
for _kind in ("camera", "laser", "scope", "others"):
    DEVICES.setdefault(_kind, [
        DeviceSpec(
            key="placeholder",
            name="Placeholder device",
            transports=("serial", "lan", "gpib"),
            description="No hardware. Lets you try the connect flow.",
            factory=_make_placeholder,
        ),
    ])

#: 数据采集：NI-DAQ 板卡用本地驱动，不走串口/网口
DEVICES["daq"] = [
    DeviceSpec(
        key="ni_pci6229",
        name="NI PCI-6229 (BNC-2120)",
        transports=("local",),
        description="Analog DC voltage output via NI-DAQmx. "
                    "Needs the NI-DAQmx driver installed.",
        factory=_make_nidaq,
    ),
    DeviceSpec(
        key="placeholder",
        name="Placeholder device",
        transports=("local",),
        description="No hardware. Lets you try the output flow safely.",
        factory=_make_placeholder_daq,
    ),
]


#: 滤光片轮：Thorlabs FW102C（串口）
DEVICES["filterwheel"] = [
    DeviceSpec(
        key="thorlabs_fw102c",
        name="Thorlabs FW102C",
        transports=("serial",),
        description="Motorized filter wheel, 6 or 12 positions. "
                    "Moves block until the wheel reports its prompt.",
        factory=_make_filterwheel,
    ),
    DeviceSpec(
        key="placeholder",
        name="Placeholder device",
        transports=("serial",),
        description="No hardware. Lets you try the move flow safely.",
        factory=_make_placeholder_fw,
    ),
]


def specs_for(kind: str) -> list[DeviceSpec]:
    """某个设备类目下可选的型号列表。"""
    return list(DEVICES.get(kind, []))


def find_spec(kind: str, key: str) -> DeviceSpec | None:
    for spec in DEVICES.get(kind, []):
        if spec.key == key:
            return spec
    return None


# ====================================================================
#  全局样式表（否则危险按钮没有样式）   （源：ui/theme.py）
# ====================================================================
from PySide6.QtWidgets import QWidget

#: 整个应用的样式表
STYLESHEET = """\

            QWidget {
                font-size: 13px;
                color: #1f2328;
            }
            #sidebar {
                background: #f7f8fa;
                border-right: 1px solid #e3e6ea;
            }
            #nav {
                background: transparent;
                outline: 0;
                border: 0;
            }
            #nav::item {
                border-radius: 6px;
                padding-left: 2px;
                color: #3a424d;
            }
            #nav::item:selected {
                background: #e4edfb;
                color: #1b4fa8;
                font-weight: 600;
            }
            #nav::item:hover:!selected {
                background: #eef1f5;
            }
            /* 这里刻意不写 ::branch 规则：
               一旦设 background 或 image，Qt 就不再画默认的展开箭头了。 */
            #hint {
                color: #9aa3ae;
                font-size: 11px;
            }
            #pageTitle {
                font-size: 21px;
                font-weight: 700;
                color: #14181d;
            }
            #pageSubtitle {
                color: #6b7480;
                font-size: 12px;
            }
            #fieldLabel {
                color: #47505b;
                font-size: 12px;
                font-weight: 600;
            }
            #formBox {
                background: #f7f8fa;
                border: 1px solid #e3e6ea;
                border-radius: 8px;
            }
            #latexBox {
                background: #ffffff;
                border: 1px solid #e3e6ea;
                border-radius: 8px;
            }
            #codeField, #matrixResult {
                font-family: Consolas, "Cascadia Mono", monospace;
                background: #f2f4f7;
                border: 1px solid #e3e6ea;
                border-radius: 5px;
                padding: 4px 6px;
                color: #2b3138;
            }
            #matrixResult {
                font-size: 15px;
                background: #ffffff;
                border: 0;
            }
            #numericBox {
                font-family: Consolas, "Cascadia Mono", monospace;
                background: #fbfcfd;
                border: 1px solid #e3e6ea;
                border-radius: 6px;
                color: #2b3138;
            }
            #mirrorish {
                color: #33404d;
                background: #f2f6fb;
                border: 1px solid #dde6f2;
                border-radius: 6px;
                padding: 7px 9px;
                font-size: 12px;
            }
            #exprField, #codeInput {
                font-family: Consolas, "Cascadia Mono", monospace;
            }
            /* ---------- 首页 ---------- */
            #homeTitle {
                font-size: 30px;
                font-weight: 700;
                color: #12202f;
            }
            #homeSubtitle {
                font-size: 13px;
                color: #6b7480;
            }
            #homeFooter {
                font-size: 11px;
                color: #9aa3ae;
            }
            /* ---------- 卡片 ---------- */
            #navCard, #planCard {
                background: #ffffff;
                border: 1px solid #e3e6ea;
                border-radius: 10px;
            }
            #navCard[hovered="true"] {
                background: #f4f8fe;
                border: 1px solid #9dbfe8;
            }
            #cardTitle {
                font-size: 14px;
                font-weight: 600;
                color: #1b3a5c;
            }
            #cardDesc {
                font-size: 11px;
                color: #6b7480;
            }
            /* ---------- 提示框 ---------- */
            #noticeBox {
                background: #fff8e6;
                border: 1px solid #f0dfae;
                border-radius: 8px;
            }
            #noticeTitle {
                font-size: 13px;
                font-weight: 600;
                color: #7a5a12;
            }
            #noticeBody {
                font-size: 12px;
                color: #6d5a2c;
            }
            /* ---------- Device Interface 入口卡片 ---------- */
            #hubIcon {
                font-size: 17px;
                color: #2b5ea8;
            }
            #hubTitle {
                font-size: 14px;
                font-weight: 600;
                color: #1b1f24;
            }
            #hubTag {
                font-size: 10px;
                color: #8a6d1f;
                background: #fdf6e3;
                border: 1px solid #efe0b8;
                border-radius: 8px;
                padding: 1px 7px;
            }
            #hubCommand {
                font-family: Consolas, "Cascadia Mono", monospace;
                font-size: 11px;
                color: #5b6470;
                background: #f4f6f9;
                border: 1px solid #e3e6ea;
                border-radius: 5px;
                padding: 3px 7px;
            }
            /* ---------- 安全警示横幅 ---------- */
            #dangerBanner {
                background: #fdecea;
                border: 1px solid #f3b8b2;
                border-radius: 8px;
            }
            #dangerBannerIcon {
                font-size: 20px;
                color: #b3261e;
            }
            #dangerBannerText {
                font-size: 12px;
                color: #7a1c16;
                font-weight: 500;
            }
            /* ---------- 通用控件 ---------- */
            QLineEdit, QPlainTextEdit, QComboBox, QSpinBox {
                background: #ffffff;
                border: 1px solid #d5dae1;
                border-radius: 5px;
                padding: 4px 6px;
                selection-background-color: #cfe0fb;
            }
            QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
                border: 1px solid #4c86d6;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #d5dae1;
                border-radius: 6px;
                padding: 5px 12px;
                color: #2b3138;
            }
            QPushButton:hover {
                background: #f2f5f9;
            }
            QPushButton#primary {
                background: #2f6fb0;
                border: 1px solid #2f6fb0;
                color: #ffffff;
                font-weight: 600;
            }
            QPushButton#primary:hover {
                background: #2a63a0;
            }
            QPushButton#ghost {
                background: transparent;
                border: 1px solid #d5dae1;
                color: #47505b;
            }
            QPushButton#ghost:hover {
                background: #f2f5f9;
            }
            QPushButton#danger {
                background: #b3261e;
                border: 1px solid #b3261e;
                color: #ffffff;
                font-weight: 700;
            }
            QPushButton#danger:hover {
                background: #9a2019;
            }
            QPushButton#danger:disabled {
                background: #e6c9c6;
                border: 1px solid #e6c9c6;
                color: #ffffff;
            }
            #monoBig {
                font-family: Consolas, "Cascadia Mono", monospace;
                font-size: 19px;
                font-weight: 600;
                color: #12202f;
            }
            QTableWidget {
                background: #ffffff;
                border: 1px solid #e3e6ea;
                border-radius: 6px;
                gridline-color: #eef1f5;
                selection-background-color: #dce9fa;
                selection-color: #14181d;
            }
            QHeaderView::section {
                background: #f4f6f9;
                border: 0;
                border-bottom: 1px solid #e3e6ea;
                padding: 5px 6px;
                color: #47505b;
                font-weight: 600;
            }
            QTabWidget::pane {
                border: 1px solid #e3e6ea;
                border-radius: 8px;
                background: #ffffff;
                top: -1px;
            }
            QTabBar::tab {
                background: transparent;
                padding: 7px 18px;
                margin-right: 2px;
                color: #5b6470;
                border: 1px solid transparent;
                border-top-left-radius: 7px;
                border-top-right-radius: 7px;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                border: 1px solid #e3e6ea;
                border-bottom-color: #ffffff;
                color: #1b4fa8;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected {
                color: #2b3138;
            }
            QStatusBar {
                color: #5b6470;
            }
            QSplitter::handle {
                background: #e3e6ea;
            }
            """


def apply_theme(widget: QWidget) -> None:
    """把统一样式应用到某个窗口（主窗口或独立设备窗口）。"""
    widget.setStyleSheet(STYLESHEET)


# ====================================================================
#  连接参数记忆（settings.ini）   （源：ui/settings.py）
# ====================================================================
from pathlib import Path

from PySide6.QtCore import QSettings

#: 项目根目录（ui/settings.py -> 项目根）
def _project_root() -> Path:
    """找出 settings.ini 应该放哪儿。

    正常情况：``ui/settings.py`` 的上两级就是项目根。

    单文件模式（``stage_standalone.py``）里本模块被内联，``__file__``
    指向那个单独的文件，旁边没有 ``ui/`` 目录 —— 这时用**当前工作目录**，
    也就是用户从哪儿启动就在哪儿存配置。
    """
    here = Path(__file__).resolve()
    candidate = here.parent.parent
    if (candidate / "ui").is_dir():
        return candidate
    return Path.cwd()


ROOT = _project_root()
#: 配置文件路径
PATH = ROOT / "settings.ini"


def settings() -> QSettings:
    """打开的 settings.ini（不存在时写入时自动创建）。"""
    return QSettings(str(PATH), QSettings.Format.IniFormat)


# ====================================================================
#  后台任务   （源：ui/workers.py）
# ====================================================================
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)


class _Worker(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self.signals = _Signals()
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            self.signals.done.emit(self._fn())
        except Exception as exc:                     # noqa: BLE001
            msg = (str(exc) if isinstance(exc, ValueError)
                   else f"{type(exc).__name__}: {exc}")
            self.signals.error.emit(msg)


#: 持有运行中的 worker，避免被回收；完成后移除
_ACTIVE: set = set()


def run_async(fn, on_done, on_error) -> None:
    """在后台执行 fn()；完成时调用 on_done(result)，失败调用 on_error(msg)。"""
    worker = _Worker(fn)
    _ACTIVE.add(worker)

    def _finish() -> None:
        _ACTIVE.discard(worker)

    def _done(result) -> None:
        _finish()
        on_done(result)

    def _error(msg) -> None:
        _finish()
        on_error(msg)

    worker.signals.done.connect(_done)
    worker.signals.error.connect(_error)
    QThreadPool.globalInstance().start(worker)


# ======================================================================
# 设备任务会话
# ======================================================================
#: work() 在发现会话已过期时返回这个哨兵，done 里据此跳过
_CANCELLED = object()


class DeviceTaskSession:
    """一次设备操作的会话守卫。

    设备页面反复写同一段东西：提交时记下「代次 + 设备」，执行前比一次，
    回调回来再比一次，不一致就丢弃。这段逻辑散在各页面里，每加一个操作
    就有一次漏掉某个分支的机会。

    这里把它收成一处。宿主（页面）只需要提供两个**现有**的真值：

    ``owner._session``
        整数代次。连接、断开、关闭窗口时递增 —— 递增之后所有在途任务
        自动过期，不需要额外通知。
    ``owner.device``
        当前绑定的设备，可能是 ``None``。

    用法::

        self._tasks = DeviceTaskSession(self)

        self._tasks.submit(
            lambda: dev.snapshot(),
            on_done=self._apply_state,
            on_failed=lambda msg: self._set_busy(False, msg),
        )

    ``on_stale`` 在任务过期时调用（可选）。**默认什么都不做** ——
    会话都换了，旧任务不应再去动界面或忙碌状态，那是新会话的事。
    """

    def __init__(self, owner) -> None:
        self._owner = owner

    # ------------------------------------------------------------------
    def generation(self) -> int:
        """当前代次（宿主递增它就作废在途任务）。"""
        return self._owner._session

    def _snapshot(self):
        return self._owner._session, self._owner.device

    def _still_valid(self, generation, device) -> bool:
        return (generation == self._owner._session
                and device is not None
                and device is self._owner.device)

    # ------------------------------------------------------------------
    def submit(self, work, *, on_done=None, on_failed=None,
               on_stale=None) -> None:
        """提交一次设备操作。

        ``work()`` 在后台线程跑（不许碰 Qt）；``on_done`` / ``on_failed``
        回到主线程。三者都会先校验会话，过期就静默丢弃。
        """
        generation, device = self._snapshot()

        def runner():
            # 执行前校验：排队期间可能已经断开 / 重连 / 关窗
            if not self._still_valid(generation, device):
                return _CANCELLED
            return work()

        def done(value) -> None:
            if value is _CANCELLED or not self._still_valid(generation, device):
                if on_stale is not None:
                    on_stale()
                return
            if on_done is not None:
                on_done(value)

        def failed(msg: str) -> None:
            if not self._still_valid(generation, device):
                if on_stale is not None:
                    on_stale()
                return
            if on_failed is not None:
                on_failed(msg)

        run_async(runner, done, failed)


# ====================================================================
#  统一的日志区（Stage / DAQ / FilterWheel 共用）   （源：ui/widgets/log_pane.py）
# ====================================================================
import time

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

#: 日志视图保留的最大行数。长采集时日志会无限增长，
#: 完整日志应该落盘，界面上留个窗口就够。
MAX_LINES = 3000


class LogPane(QWidget):
    """「Log」标题 + 可选按钮 + 只读文本框。

    参数
    ----
    height
        正数表示固定高度（DAQ 用 110）；``None`` 表示跟随布局伸展
        （Stage / FilterWheel 的日志是填满右侧那一栏的）。
    """

    def __init__(self, *, height: int | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        self.header = QHBoxLayout()
        self.header.setSpacing(6)
        title = QLabel("Log")
        title.setObjectName("fieldLabel")
        self.header.addWidget(title)
        self.header.addStretch(1)

        #: 清空按钮。想改文案就 setText，想禁用就 setEnabled。
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("ghost")
        self.clear_btn.setFixedWidth(70)
        self.header.addWidget(self.clear_btn)
        outer.addLayout(self.header)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setObjectName("numericBox")
        self.view.setMaximumBlockCount(MAX_LINES)
        if height is not None:
            self.view.setFixedHeight(height)
        outer.addWidget(self.view, 1)

        self.clear_btn.clicked.connect(self.clear)

    # ------------------------------------------------------------------
    def add_header_button(self, button: QWidget) -> None:
        """在 Clear **之前**插一个按钮（导出、折叠之类）。"""
        self.header.insertWidget(self.header.count() - 1, button)

    def append(self, text: str) -> None:
        """追加一行，自动加 ``[HH:MM:SS]`` 前缀。"""
        stamp = time.strftime("%H:%M:%S")
        self.view.appendPlainText(f"[{stamp}] {text}")

    def clear(self) -> None:
        self.view.clear()

    def text(self) -> str:
        """当前全部日志文本（导出用）。"""
        return self.view.toPlainText()

    def set_body_visible(self, visible: bool) -> None:
        """只显示/隐藏正文，头部按钮保持可见。

        DAQ 页用这个把日志收起来给实时曲线腾高度 —— 如果连头部一起
        隐藏，那个「Show log」按钮也会消失，就再也点不回来了。
        """
        self.view.setVisible(visible)

    def body_visible(self) -> bool:
        return self.view.isVisible()


# ====================================================================
#  设备选择 + 连接组件   （源：ui/widgets/device_selector.py）
# ====================================================================
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


#: 连接方式 -> 显示名
TRANSPORT_LABELS = {
    "serial": "Serial (COM)",
    "lan": "LAN (TCP/IP)",
    "gpib": "GPIB / VISA",
    "local": "Local board (NI-DAQmx)",
}


# ======================================================================
# 连接设置对话框
# ======================================================================
class ConnectionDialog(QDialog):
    """配置连接方式与参数。确定后通过 result_config() 取出结果。

    参数按连接方式分开保存（`configs["serial"]` / `["lan"]` / `["gpib"]`）：
    串口的 "port" 是 COM6，网口的 "port" 是 5025，同名不同义，混用会解析失败。
    """

    #: 各类连接方式的默认配置
    DEFAULT_CONFIGS = {
        "serial": {"port": "COM6", "baudrate": 115200},
        "lan": {"host": "192.168.1.100", "port": 5025},
        "gpib": {"address": "GPIB0::12::INSTR"},
        "local": {"resource": "Dev1"},
    }

    def __init__(self, transports: tuple[str, ...], *,
                 kind: str = "serial",
                 configs: dict[str, dict] | None = None,
                 device_name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connection settings")
        self.setMinimumWidth(520)
        self._configs = {k: dict(v) for k, v in self.DEFAULT_CONFIGS.items()}
        for k, v in (configs or {}).items():
            self._configs[k] = dict(v)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        if device_name:
            head = QLabel(device_name)
            head.setObjectName("fieldLabel")
            layout.addWidget(head)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("Method:"))
        self.method_box = QComboBox()
        self.method_box.setMinimumWidth(180)
        for t in transports:
            self.method_box.addItem(TRANSPORT_LABELS.get(t, t), t)
        index = self.method_box.findData(kind)
        self.method_box.setCurrentIndex(index if index >= 0 else 0)
        row.addWidget(self.method_box)
        row.addStretch(1)
        layout.addLayout(row)

        frame = QFrame()
        frame.setObjectName("formBox")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(12, 10, 12, 10)
        frame_layout.setSpacing(6)

        self.stack = QStackedWidget()
        self._serial_page = self._build_serial_page()
        self._lan_page = self._build_lan_page()
        self._gpib_page = self._build_gpib_page()
        self._local_page = self._build_local_page()
        for page in (self._serial_page, self._lan_page, self._gpib_page,
                     self._local_page):
            self.stack.addWidget(page)
        frame_layout.addWidget(self.stack)
        layout.addWidget(frame)

        self.note = QLabel("")
        self.note.setObjectName("pageSubtitle")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.method_box.currentIndexChanged.connect(self._on_method_changed)
        self._on_method_changed()

    # ------------------------------------------------------------------ 参数页
    def _build_serial_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("Port:"))
        self.port_box = QComboBox()
        self.port_box.setEditable(True)
        self.port_box.setMinimumWidth(170)
        row.addWidget(self.port_box)

        self.port_rescan_btn = QPushButton("↻")
        self.port_rescan_btn.setObjectName("ghost")
        self.port_rescan_btn.setFixedWidth(34)
        self.port_rescan_btn.setToolTip("Rescan serial ports")
        row.addWidget(self.port_rescan_btn)

        row.addWidget(QLabel("Baud:"))
        self.baud_box = QComboBox()
        self.baud_box.addItems(["115200", "57600", "38400", "19200", "9600"])
        self.baud_box.setFixedWidth(96)
        row.addWidget(self.baud_box)
        row.addStretch(1)
        outer.addLayout(row)

        # 恢复保存的波特率；列表里没有就补进去（用户可能设过自定义值）
        saved_baud = str(self._configs["serial"].get("baudrate", 115200))
        index = self.baud_box.findText(saved_baud)
        if index < 0:
            self.baud_box.addItem(saved_baud)
            index = self.baud_box.count() - 1
        self.baud_box.setCurrentIndex(index)

        self.port_rescan_btn.clicked.connect(self._rescan_serial_ports)
        self._rescan_serial_ports()
        return page

    def _build_lan_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("Host:"))
        lan_conf = self._configs["lan"]
        self.host_edit = QLineEdit(str(lan_conf.get("host", "192.168.1.100")))
        self.host_edit.setMinimumWidth(170)
        row.addWidget(self.host_edit)

        row.addWidget(QLabel("Port:"))
        self.lan_port_spin = QSpinBox()
        self.lan_port_spin.setRange(1, 65535)
        try:
            lan_port = int(lan_conf.get("port", 5025))
        except (TypeError, ValueError):
            lan_port = 5025
        self.lan_port_spin.setValue(lan_port)
        self.lan_port_spin.setFixedWidth(80)
        row.addWidget(self.lan_port_spin)
        row.addStretch(1)
        outer.addLayout(row)

        tip = QLabel("Raw TCP (SCPI over socket). 5025 is the usual SCPI port; "
                     "some instruments use 23 or a vendor-specific one.")
        tip.setObjectName("pageSubtitle")
        tip.setWordWrap(True)
        outer.addWidget(tip)
        return page

    def _build_gpib_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("Address:"))
        self.visa_box = QComboBox()
        self.visa_box.setEditable(True)
        self.visa_box.setMinimumWidth(210)
        self.visa_box.addItem(
            str(self._configs["gpib"].get("address", "GPIB0::12::INSTR")))
        row.addWidget(self.visa_box)

        self.visa_list_btn = QPushButton("List")
        self.visa_list_btn.setObjectName("ghost")
        self.visa_list_btn.setFixedWidth(52)
        self.visa_list_btn.setToolTip("List VISA resources")
        row.addWidget(self.visa_list_btn)
        row.addStretch(1)
        outer.addLayout(row)

        self.visa_note = QLabel("")
        self.visa_note.setObjectName("pageSubtitle")
        self.visa_note.setWordWrap(True)
        outer.addWidget(self.visa_note)

        self.visa_list_btn.clicked.connect(self._list_visa_resources)
        if not visa_available():
            self.visa_note.setText(
                "pyvisa is not installed. GPIB needs: pip install pyvisa pyvisa-py")
        else:
            self.visa_note.setText("Click List to enumerate VISA resources.")
        return page

    def _build_local_page(self) -> QWidget:
        """本地板卡：由厂商驱动直接访问，只需要一个设备资源名。"""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("Resource:"))
        self.resource_edit = QLineEdit(
            str(self._configs["local"].get("resource", "Dev1")))
        self.resource_edit.setMinimumWidth(140)
        self.resource_edit.setPlaceholderText("e.g. Dev1")
        row.addWidget(self.resource_edit)

        self.local_list_btn = QPushButton("List")
        self.local_list_btn.setObjectName("ghost")
        self.local_list_btn.setFixedWidth(52)
        self.local_list_btn.setToolTip("List local DAQ devices")
        row.addWidget(self.local_list_btn)
        row.addStretch(1)
        outer.addLayout(row)

        self.local_note = QLabel("")
        self.local_note.setObjectName("pageSubtitle")
        self.local_note.setWordWrap(True)
        outer.addWidget(self.local_note)

        self.local_list_btn.clicked.connect(self._list_local_devices)
        self._list_local_devices()
        return page

    def _list_local_devices(self) -> None:
        """试着列出 NI-DAQ 设备；没驱动时给出说明。"""
        try:
            from core import nidaq as ni_mod
        except Exception as exc:                     # noqa: BLE001
            self.local_note.setText(f"NI-DAQ module unavailable: {exc}")
            return

        if not ni_mod.NIDAQMX_AVAILABLE:
            self.local_note.setText(
                "nidaqmx is not installed. Local boards need: pip install nidaqmx")
            return
        if not ni_mod.driver_available():
            self.local_note.setText(
                "NI-DAQmx driver not found. Install it from National Instruments; "
                "the Python package alone is not enough.")
            return

        devices = ni_mod.list_devices()
        if devices:
            current = self.resource_edit.text().strip()
            self.resource_edit.setText(current or devices[0])
            self.local_note.setText("Found: " + ", ".join(devices))
        else:
            self.local_note.setText("Driver present but no DAQ devices detected.")

    # ------------------------------------------------------------------
    def _rescan_serial_ports(self) -> None:
        # 保留「用户当前看到并选择」的端口。
        #
        # 必须用 selected_port() 而不是 currentData()：手输 COM9 之后
        # currentData() 仍然返回**旧列表项**（COM7），拿它当 current 会让
        # 刷新把界面改回 COM7，接着点 OK 就保存了错的端口。
        # 同理，保存的端口不在扫描结果里时也要原样留住。
        current = (self.selected_port()
                   or str(self._configs["serial"].get("port", "COM6")))

        self.port_box.blockSignals(True)
        self.port_box.clear()
        ports = available_serial_ports()
        for name, desc in ports:
            self.port_box.addItem(f"{name} — {desc}" if desc else name, name)

        index = self.port_box.findData(current)
        if index >= 0:
            self.port_box.setCurrentIndex(index)
        else:
            # 保存的端口不在扫描结果里（或者压根没扫到）：
            # 直接把它显示在编辑框里，**不要**换成别的端口
            self.port_box.setCurrentText(current)
        self.port_box.blockSignals(False)

    def _list_visa_resources(self) -> None:
        resources = list_visa_resources()
        current = self.visa_box.currentText()
        self.visa_box.blockSignals(True)
        self.visa_box.clear()
        if resources:
            for r in resources:
                self.visa_box.addItem(r)
        else:
            self.visa_box.addItem(
                str(self._configs["gpib"].get("address", "GPIB0::12::INSTR")))
        if current:
            self.visa_box.setCurrentText(current)
        self.visa_box.blockSignals(False)
        self.visa_note.setText(
            f"{len(resources)} VISA resource(s)" if resources
            else "no VISA resources found (is NI-VISA or pyvisa-py installed?)")

    def _on_method_changed(self, *_args) -> None:
        kind = self.selected_kind()
        page = {"serial": self._serial_page,
                "lan": self._lan_page,
                "gpib": self._gpib_page,
                "local": self._local_page}.get(kind)
        if page is not None:
            self.stack.setCurrentWidget(page)
        if kind == "gpib" and not visa_available():
            self.note.setText("GPIB requires pyvisa; the Connect will fail until "
                              "it is installed.")
        else:
            self.note.setText("")

    # ------------------------------------------------------------------ 结果
    def selected_kind(self) -> str:
        return self.method_box.currentData() or "serial"

    def selected_port(self) -> str:
        """当前选中的端口名。

        必须区分「选已有项」和「手输」：下拉是可编辑的，用户打字之后
        currentData() 仍然返回**旧那一项**的端口，直接用它就会把界面
        上显示的 COM9 提交成 COM3。
        """
        text = self.port_box.currentText().strip()
        index = self.port_box.currentIndex()
        if index >= 0 and text == self.port_box.itemText(index):
            data = self.port_box.itemData(index)
            if data:
                return str(data)
        # 文字和当前项不一致（或者没有选中项）-> 按用户输入的文字算，
        # 去掉 "COM3 — USB Serial" 这种描述后缀
        return text.split(" — ")[0].strip()

    def selected_params(self) -> dict:
        kind = self.selected_kind()
        if kind == "serial":
            port = self.selected_port()
            return {"port": port or "COM6",
                    "baudrate": int(self.baud_box.currentText())}
        if kind == "lan":
            return {"host": self.host_edit.text().strip() or "127.0.0.1",
                    "port": int(self.lan_port_spin.value())}
        if kind == "local":
            return {"resource": self.resource_edit.text().strip() or "Dev1"}
        return {"address": self.visa_box.currentText().strip()}

    def result_config(self) -> tuple[str, dict]:
        return self.selected_kind(), self.selected_params()

    def summary(self) -> str:
        """一行摘要，给主界面的按钮用。"""
        kind, params = self.result_config()
        if kind == "serial":
            return f"Serial · {params.get('port', '?')} @ {params.get('baudrate', '?')}"
        if kind == "lan":
            return f"LAN · {params.get('host', '?')}:{params.get('port', '?')}"
        if kind == "local":
            return f"Local · {params.get('resource', '?')}"
        return f"GPIB · {params.get('address', '?')}"


# ======================================================================
# 主组件
# ======================================================================
class DeviceSelector(QFrame):
    """Device 下拉 + Connection 按钮 + Connect/Disconnect。"""

    connected = Signal(object)
    #: 连接参数（端口/地址/资源名）被改过
    connection_changed = Signal()
    failed = Signal(str)
    log = Signal(str)
    disconnected = Signal()

    def __init__(self, *,
                 kind: str,
                 device_options: Callable[[], dict] | None = None,
                 hint: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("formBox")
        self._kind = kind
        self._device_options = device_options or (lambda: {})
        self._device = None
        self._busy = False
        #: 外部（页面）正在执行耗时操作时置位，锁住连接控件
        self._locked = False

        #: 每种连接方式的最后一次配置，切换型号时保留。
        #: 初值从 settings.ini 读，所以改过一次端口之后就不用每次再选。
        self._configs: dict[str, dict] = self._load_configs()
        #: 当前使用的连接方式
        self._conn_kind = "serial"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        head = QLabel("Device")
        head.setObjectName("fieldLabel")
        layout.addWidget(head)

        # ---- 设备下拉 ----
        dev_row = QHBoxLayout()
        dev_row.setSpacing(6)
        self.device_box = QComboBox()
        self.device_box.setMinimumWidth(300)
        dev_row.addWidget(self.device_box, 1)
        self.rescan_btn = QPushButton("↻")
        self.rescan_btn.setObjectName("ghost")
        self.rescan_btn.setFixedWidth(34)
        self.rescan_btn.setToolTip("Rescan for devices")
        dev_row.addWidget(self.rescan_btn)
        layout.addLayout(dev_row)

        self.device_desc = QLabel("")
        self.device_desc.setObjectName("pageSubtitle")
        self.device_desc.setWordWrap(True)
        layout.addWidget(self.device_desc)

        # ---- 连接设置按钮 ----
        conn_row = QHBoxLayout()
        conn_row.setSpacing(6)
        self.conn_btn = QPushButton("Connection: Serial · COM6 @ 115200")
        self.conn_btn.setObjectName("ghost")
        self.conn_btn.setToolTip("Configure how this device is connected")
        conn_row.addWidget(self.conn_btn, 1)
        layout.addLayout(conn_row)

        # ---- 连接/断开 ----
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("primary")
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setObjectName("ghost")
        btn_row.addWidget(self.connect_btn, 1)
        btn_row.addWidget(self.disconnect_btn, 1)
        layout.addLayout(btn_row)

        self.status_label = QLabel("● Disconnected")
        self.status_label.setObjectName("pageSubtitle")
        layout.addWidget(self.status_label)

        if hint:
            tip = QLabel(hint)
            tip.setObjectName("pageSubtitle")
            tip.setWordWrap(True)
            layout.addWidget(tip)

        # ---- 信号 ----
        self.device_box.currentIndexChanged.connect(self._on_device_changed)
        self.rescan_btn.clicked.connect(self.rescan)
        self.conn_btn.clicked.connect(self.edit_connection)
        self.connect_btn.clicked.connect(self._on_connect)
        self.disconnect_btn.clicked.connect(self._on_disconnect)

        self.reload_devices()
        self._update_controls()

    # ==================================================================
    # 设备
    # ==================================================================
    def reload_devices(self) -> None:
        current_key = self.current_spec_key()
        self.device_box.blockSignals(True)
        self.device_box.clear()
        specs = specs_for(self._kind)
        for spec in specs:
            self.device_box.addItem(spec.name, spec.key)
        if current_key:
            index = self.device_box.findData(current_key)
            if index >= 0:
                self.device_box.setCurrentIndex(index)
        self.device_box.blockSignals(False)
        self.log.emit(f"{len(specs)} device model(s) available")
        self._on_device_changed()

    def rescan(self) -> None:
        self.reload_devices()

    def current_resource(self) -> str:
        """当前连接参数里的资源名（local 就是 "Dev1" 这种）。

        DeviceSpec 上没有这个字段 —— 它是连接参数的一部分，存在
        _configs 里。界面需要它来推导默认通道名前缀，所以开个口子。
        """
        params = self._configs.get(self._conn_kind, {})
        return str(params.get("resource", "") or "")

    def current_spec_key(self) -> str:
        return self.device_box.currentData() or ""

    def current_spec(self):
        return find_spec(self._kind, self.current_spec_key())

    def _on_device_changed(self, *_args) -> None:
        spec = self.current_spec()
        if spec is None:
            return
        self.device_desc.setText(spec.description)

        # 当前连接方式不受这个型号支持时，换成它支持的第一个
        if self._conn_kind not in spec.transports and spec.transports:
            self._conn_kind = spec.transports[0]

        self._refresh_conn_button()
        self._update_controls()

    # ==================================================================
    # 连接设置
    # ==================================================================
    def _refresh_conn_button(self) -> None:
        """按钮文字显示当前配置摘要；不支持的型号给出提示。"""
        spec = self.current_spec()
        label = TRANSPORT_LABELS.get(self._conn_kind, self._conn_kind)
        params = self._configs.get(self._conn_kind, {})
        if self._conn_kind == "serial":
            detail = f"{params.get('port', '?')} @ {params.get('baudrate', '?')}"
        elif self._conn_kind == "lan":
            detail = f"{params.get('host', '?')}:{params.get('port', '?')}"
        elif self._conn_kind == "local":
            detail = str(params.get("resource", "?"))
        else:
            detail = str(params.get("address", "?"))

        supports = spec is not None and self._conn_kind in spec.transports
        suffix = "" if supports else "  (not supported by this model)"
        self.conn_btn.setText(f"Connection: {label} · {detail}{suffix}")

    def edit_connection(self) -> None:
        """打开连接设置对话框。"""
        spec = self.current_spec()
        if spec is None:
            return
        if self._device is not None:
            self.log.emit("Disconnect before changing the connection settings.")
            return

        dialog = ConnectionDialog(
            spec.transports,
            kind=self._conn_kind,
            configs=self._configs,
            device_name=spec.name,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        kind, params = dialog.result_config()
        self._conn_kind = kind
        self._configs[kind] = params
        self._save_configs()          # 记住这次的选择，下次打开就是这个
        self._refresh_conn_button()
        # 通知外部：资源名/端口变了，依赖它的默认值该跟着更新
        self.connection_changed.emit()
        self.log.emit("Connection set to " + dialog.summary())

    # ==================================================================
    # 配置持久化
    # ==================================================================
    #: 各类连接方式的出厂默认值（settings.ini 里没有时用它）
    DEFAULT_CONFIGS = {
        "serial": {"port": "COM6", "baudrate": 115200},
        "lan": {"host": "192.168.1.100", "port": 5025},
        "gpib": {"address": "GPIB0::12::INSTR"},
        "local": {"resource": "Dev1"},
    }

    def _load_configs(self) -> dict[str, dict]:
        """从 settings.ini 读这个设备的连接参数。

        每个设备（按 kind 分组）各自记住自己的串口，所以 Stage 用 COM7、
        滤光片轮用 COM4 互不影响。
        """
        configs = {k: dict(v) for k, v in self.DEFAULT_CONFIGS.items()}
        store = settings()
        store.beginGroup(self._kind)
        for transport, conf in configs.items():
            store.beginGroup(transport)
            for key in list(conf):
                if not store.contains(key):
                    continue
                raw = store.value(key)
                try:
                    conf[key] = type(conf[key])(raw)
                except (TypeError, ValueError):
                    pass          # 文件被手改坏了就退回默认值
            store.endGroup()
        store.endGroup()
        return configs

    def _save_configs(self) -> None:
        """把当前连接参数写回 settings.ini。"""
        store = settings()
        store.beginGroup(self._kind)
        for transport, conf in self._configs.items():
            store.beginGroup(transport)
            for key, value in conf.items():
                store.setValue(key, value)
            store.endGroup()
        store.endGroup()
        store.sync()

    # ==================================================================
    # 连接 / 断开
    # ==================================================================
    def set_locked(self, locked: bool) -> None:
        """锁住连接类控件。

        页面在执行耗时操作（例如转位）时调用，避免用户在半路 Disconnect
        再重连 —— 那样旧操作的完成回调会写到新连接的界面上。
        """
        self._locked = bool(locked)
        self._update_controls()

    def _update_controls(self) -> None:
        has_device = self._device is not None
        # 自身忙（连接/断开中）或外部忙（页面在执行设备操作）都算不空闲
        idle = not self._busy and not self._locked
        spec = self.current_spec()
        supported = spec is not None and self._conn_kind in spec.transports

        self.connect_btn.setEnabled(
            not has_device and idle and spec is not None and supported)
        self.disconnect_btn.setEnabled(has_device and idle)
        for w in (self.device_box, self.rescan_btn, self.conn_btn):
            w.setEnabled(idle)

        if has_device:
            self.status_label.setText("● Connected")
            self.status_label.setStyleSheet("color:#2e7d32; font-weight:600;")
        else:
            self.status_label.setText("● Disconnected")
            self.status_label.setStyleSheet("color:#9aa3ae;")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        if message:
            self.log.emit(message)
        self._update_controls()

    def _on_connect(self) -> None:
        if self._busy or self._device is not None:
            return
        spec = self.current_spec()
        if spec is None:
            self.log.emit("No device model selected.")
            return
        if self._conn_kind not in spec.transports:
            self.log.emit(f"{spec.name} does not support "
                          f"{TRANSPORT_LABELS.get(self._conn_kind)}.")
            return
        if self._conn_kind == "gpib" and not visa_available():
            self.failed.emit("GPIB needs pyvisa: pip install pyvisa pyvisa-py")
            return

        params = dict(self._configs.get(self._conn_kind, {}))
        options = dict(self._device_options() or {})
        label = (f"{spec.name} via "
                 f"{TRANSPORT_LABELS.get(self._conn_kind, self._conn_kind)}")
        self._set_busy(True, f"Connecting to {label}…")

        def work():
            transport = make_transport(self._conn_kind, **params)
            device = spec.factory(transport, **options)
            connector = getattr(device, "connect", None)
            if callable(connector):
                connector()
            return device

        def done(device) -> None:
            self._device = device
            self._set_busy(False, "Connected.")
            self._update_controls()
            self.connected.emit(device)

        def failed(msg: str) -> None:
            self._set_busy(False, f"Connect failed: {msg}")
            self._update_controls()
            self.failed.emit(msg)

        run_async(work, done, failed)

    def _on_disconnect(self) -> None:
        if self._device is None:
            return
        device = self._device

        def work():
            closer = getattr(device, "close", None)
            if callable(closer):
                closer()
            return None

        def done(_) -> None:
            self._device = None
            self._set_busy(False, "Disconnected.")
            self._update_controls()
            self.disconnected.emit()

        def failed(msg: str) -> None:
            self._set_busy(False, f"Disconnect error: {msg}")

        self._set_busy(True, "Disconnecting…")
        run_async(work, done, failed)

    # ==================================================================
    @property
    def device(self):
        return self._device

    def force_disconnect(self) -> None:
        """页面/窗口关闭时兜底：先停住输出，再释放底层设备。

        顺序：halt（位移台急停）→ stop（DAQ 归零）→ close（释放资源）。
        这三个方法都幂等，重复调用无害；单个失败不影响其它步骤。
        """
        device, self._device = self._device, None
        if device is None:
            return
        for name in ("halt", "stop", "close"):
            fn = getattr(device, name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:                    # noqa: BLE001
                    pass


# ====================================================================
#  Stage Controller 页面   （源：ui/pages/stage_page.py）
# ====================================================================
import sys
import time

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)



class CenterZeroDialog(QDialog):
    """Center and zero 的二次确认。

    刻意用**下拉菜单**而不是单纯的 Yes/No 按钮：默认停在 No，
    必须主动切换成 Yes 才会执行。这个操作会把台子开到负限位，
    多一道需要"主动选择"的关卡，比顺手回车安全得多。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Center and zero")
        self.setMinimumWidth(540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        head = QLabel("⚠  This drives the stage to the negative hardware limits.")
        # 对话框不继承主窗口的 QSS，这里用内联样式保证警告足够醒目
        head.setStyleSheet(
            "color:#b3261e; font-weight:600; font-size:13px;")
        head.setWordWrap(True)
        layout.addWidget(head)

        body = QLabel(
            "What it does:\n"
            "   1. move X and Y to the negative hardware limits\n"
            "   2. define that position as the temporary origin\n"
            "   3. move to the measured centre (X=451906, Y=430490)\n"
            "   4. define the centre as the new origin\n"
            "\n"
            "Moving to the limit may damage the sample, and the stage may hit "
            "objectives or the holder on the way. Make sure the sample and every "
            "objective are clear of the full travel range before you proceed.")
        body.setWordWrap(True)
        layout.addWidget(body)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Proceed:"))
        self.choice = QComboBox()
        self.choice.addItem("No — do not move the stage", False)
        self.choice.addItem("Yes — drive to the limits", True)
        self.choice.setCurrentIndex(0)          # 默认 No
        self.choice.setMinimumWidth(300)
        row.addWidget(self.choice, 1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def confirmed(self) -> bool:
        """下拉框选的是 Yes 才返回 True。"""
        return bool(self.choice.currentData())


class StagePage(QWidget):
    page_title = "Stage Controller"

    #: 工作线程 -> UI 线程的日志通道（Qt 会自动排队）
    log_line = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        title = QLabel("Stage Controller")
        title.setObjectName("pageTitle")
        outer.addWidget(title)
        subtitle = QLabel("Two-axis stage. Pick the model, pick how it is "
                          "connected, then Connect. The position unit comes "
                          "from the device itself.")
        subtitle.setObjectName("pageSubtitle")
        outer.addWidget(subtitle)

        body = QHBoxLayout()
        body.setSpacing(14)

        # ------------------------------------------------ 左：控制
        left = QVBoxLayout()
        left.setSpacing(8)

        # ---- 设备选择（统一组件）----
        self.selector = DeviceSelector(
            kind="stage",
            device_options=self._device_options,
            hint="Both models can be tried without hardware: choose "
                 "'Placeholder device' and any connection type.",
        )
        self.verbose_box = QComboBox()
        self.verbose_box.addItems(["Log: normal", "Log: TX/RX"])
        self.verbose_box.setFixedWidth(118)
        left.addWidget(self.selector)

        # ---- 位置 ----
        pos = QFrame()
        pos.setObjectName("formBox")
        pos_grid = QGridLayout(pos)
        pos_grid.setContentsMargins(12, 10, 12, 10)
        pos_grid.setSpacing(6)

        # 单位由设备决定（连接后填），未连接时只写 "Position"
        self.pos_head = QLabel("Position")
        self.pos_head.setObjectName("fieldLabel")
        pos_grid.addWidget(self.pos_head, 0, 0, 1, 3)

        pos_grid.addWidget(QLabel("X:"), 1, 0)
        self.x_value = QLabel("—")
        self.x_value.setObjectName("monoBig")
        pos_grid.addWidget(self.x_value, 1, 1)

        pos_grid.addWidget(QLabel("Y:"), 2, 0)
        self.y_value = QLabel("—")
        self.y_value.setObjectName("monoBig")
        pos_grid.addWidget(self.y_value, 2, 1)

        self.refresh_pos_btn = QPushButton("Refresh")
        self.refresh_pos_btn.setObjectName("ghost")
        pos_grid.addWidget(self.refresh_pos_btn, 1, 2, 2, 1)

        self.verbose_cb_row = QHBoxLayout()
        self.verbose_cb_row.addWidget(self.verbose_box)
        self.verbose_cb_row.addStretch(1)
        pos_grid.addLayout(self.verbose_cb_row, 3, 0, 1, 3)
        left.addWidget(pos)

        # ---- 移动 ----
        move = QFrame()
        move.setObjectName("formBox")
        move_grid = QGridLayout(move)
        move_grid.setContentsMargins(12, 10, 12, 10)
        move_grid.setSpacing(6)

        move_head = QLabel("Move")
        move_head.setObjectName("fieldLabel")
        move_grid.addWidget(move_head, 0, 0, 1, 4)

        move_grid.addWidget(QLabel("X target:"), 1, 0)
        self.x_target = QDoubleSpinBox()
        self.x_target.setRange(-999999, 999999)
        self.x_target.setDecimals(0)
        self.x_target.setFixedWidth(130)
        move_grid.addWidget(self.x_target, 1, 1)
        self.go_x_btn = QPushButton("Go X")
        self.go_x_btn.setObjectName("ghost")
        move_grid.addWidget(self.go_x_btn, 1, 2)

        move_grid.addWidget(QLabel("Y target:"), 2, 0)
        self.y_target = QDoubleSpinBox()
        self.y_target.setRange(-999999, 999999)
        self.y_target.setDecimals(0)
        self.y_target.setFixedWidth(130)
        move_grid.addWidget(self.y_target, 2, 1)
        self.go_y_btn = QPushButton("Go Y")
        self.go_y_btn.setObjectName("ghost")
        move_grid.addWidget(self.go_y_btn, 2, 2)

        move_grid.addWidget(QLabel("Jog step:"), 3, 0)
        self.jog_step = QDoubleSpinBox()
        self.jog_step.setRange(1, 100000)
        self.jog_step.setDecimals(0)
        self.jog_step.setValue(1000)
        self.jog_step.setFixedWidth(130)
        move_grid.addWidget(self.jog_step, 3, 1)

        jog_row = QHBoxLayout()
        jog_row.setSpacing(4)
        self.jog_xm = QPushButton("X−")
        self.jog_xp = QPushButton("X+")
        self.jog_ym = QPushButton("Y−")
        self.jog_yp = QPushButton("Y+")
        for b in (self.jog_xm, self.jog_xp, self.jog_ym, self.jog_yp):
            b.setObjectName("ghost")
            b.setFixedWidth(48)
            jog_row.addWidget(b)
        jog_row.addStretch(1)
        jog_wrap = QWidget()
        jog_wrap.setLayout(jog_row)
        jog_row.setContentsMargins(0, 0, 0, 0)
        move_grid.addWidget(jog_wrap, 3, 2, 1, 2)
        left.addWidget(move)

        # ---- 动作 ----
        act = QFrame()
        act.setObjectName("formBox")
        act_box = QVBoxLayout(act)
        act_box.setContentsMargins(12, 10, 12, 10)
        act_box.setSpacing(6)

        act_head = QLabel("Actions")
        act_head.setObjectName("fieldLabel")
        act_box.addWidget(act_head)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.halt_btn = QPushButton("HALT")
        self.halt_btn.setObjectName("danger")
        self.origin_btn = QPushButton("Define origin")
        self.origin_btn.setObjectName("ghost")
        self.center_btn = QPushButton("Center and zero…")
        self.center_btn.setObjectName("ghost")
        for b in (self.halt_btn, self.origin_btn, self.center_btn):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        act_box.addLayout(btn_row)

        warn = QLabel("Center and zero drives the stage to the negative hardware "
                      "limits — make sure no objectives can be hit.")
        warn.setObjectName("pageSubtitle")
        warn.setWordWrap(True)
        act_box.addWidget(warn)
        left.addWidget(act)

        left.addStretch(1)
        body.addLayout(left, 0)

        # ------------------------------------------------ 右：日志
        right = QVBoxLayout()
        right.setSpacing(6)
        self.log = LogPane()                 # 跟随布局伸展
        self.save_log_btn = QPushButton("Save…")
        self.save_log_btn.setObjectName("ghost")
        self.save_log_btn.setFixedWidth(70)
        self.log.add_header_button(self.save_log_btn)   # 导出按钮在 Clear 左边
        right.addWidget(self.log, 1)

        # 兼容旧引用（_append_log / save_log 等还在用这两个名字）
        self.log_view = self.log.view
        self.clear_log_btn = self.log.clear_btn

        body.addLayout(right, 1)
        outer.addLayout(body, 1)

        # ------------------------------------------------ 信号
        self.log_line.connect(self._append_log)
        self.selector.log.connect(self._append_log)
        self.selector.connected.connect(self._on_connected)
        self.selector.disconnected.connect(self._on_disconnected)
        self.selector.failed.connect(
            lambda msg: QMessageBox.critical(self, "Connect failed", msg))

        self.refresh_pos_btn.clicked.connect(self.refresh_position)
        self.go_x_btn.clicked.connect(lambda: self._go("x"))
        self.go_y_btn.clicked.connect(lambda: self._go("y"))
        self.jog_xm.clicked.connect(lambda: self._jog("x", -1))
        self.jog_xp.clicked.connect(lambda: self._jog("x", +1))
        self.jog_ym.clicked.connect(lambda: self._jog("y", -1))
        self.jog_yp.clicked.connect(lambda: self._jog("y", +1))
        self.halt_btn.clicked.connect(self.do_halt)
        self.origin_btn.clicked.connect(self.do_define_origin)
        self.center_btn.clicked.connect(self.do_center_and_zero)
        self.save_log_btn.clicked.connect(self.save_log)
        self.verbose_box.currentIndexChanged.connect(self._on_verbose_changed)

        self._update_controls()

    # ------------------------------------------------------------------ 工具
    @property
    def device(self):
        return self.selector.device

    def _device_options(self) -> dict:
        """传给设备工厂的运行时参数（连接时读取）。"""
        return {
            "verbose": self.verbose_box.currentIndex() == 1,
            "log_callback": self.log_line.emit,
        }

    def _append_log(self, text: str) -> None:
        self.log.append(text)

    def _on_connected(self, _device) -> None:
        self._update_units()
        self._update_controls()
        self.refresh_position()

    def _on_disconnected(self) -> None:
        self.x_value.setText("—")
        self.y_value.setText("—")
        self._update_units()
        self._update_controls()

    def _update_units(self) -> None:
        """位置单位取自设备本身 —— 不同位移台的单位可能不同。

        未连接时不写单位，避免给出误导性的默认值。
        """
        dev = self.device
        unit = getattr(dev, "UNIT_LABEL", None) if dev is not None else None
        self.pos_head.setText(f"Position ({unit})" if unit else "Position")

    def _on_verbose_changed(self, index: int) -> None:
        dev = self.device
        if dev is not None and hasattr(dev, "verbose"):
            dev.verbose = index == 1
        self._append_log("Log TX/RX " + ("enabled" if index == 1 else "disabled"))

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        if message:
            self._append_log(message)
        self._update_controls()

    def _update_controls(self) -> None:
        connected = self.device is not None and getattr(
            self.device, "is_connected", True)
        idle = not self._busy

        # HALT 永远可用（急停不能被 busy 挡住）
        self.halt_btn.setEnabled(connected)
        for w in (self.refresh_pos_btn, self.go_x_btn, self.go_y_btn,
                  self.jog_xm, self.jog_xp, self.jog_ym, self.jog_yp,
                  self.origin_btn, self.center_btn):
            w.setEnabled(connected and idle)

    def _no_device(self) -> bool:
        if self.device is None:
            QMessageBox.information(self, "Not connected",
                                    "Connect to the stage first.")
            return True
        return False

    def _handle_failure(self, what: str, message: str) -> None:
        """统一处理后台操作失败：HALT 取消与真实错误区别对待。"""
        if "cancelled by HALT" in message or "cancelled by halt" in message:
            self._set_busy(False, f"{what} cancelled by HALT.")
            return
        self._set_busy(False, f"{what} failed: {message}")
        QMessageBox.critical(self, f"{what} failed", message)

    def _blocked(self) -> bool:
        """忙碌时给一句可见的解释 —— 否则用户会觉得按钮没反应。"""
        if self._busy:
            self._append_log("Busy — wait for the current operation to finish.")
            return True
        return False

    # ------------------------------------------------------------------ 位置
    def refresh_position(self) -> None:
        if self._no_device() or self._busy:
            return
        dev = self.device

        def work():
            return dev.get_position("x"), dev.get_position("y")

        def done(result) -> None:
            x, y = result
            self.x_value.setText(f"{x:.0f}")
            self.y_value.setText(f"{y:.0f}")
            self._set_busy(False)

        def failed(msg: str) -> None:
            self._set_busy(False, f"Read position failed: {msg}")

        self._set_busy(True, "Reading position…")
        run_async(work, done, failed)

    def _go(self, axis: str) -> None:
        if self._no_device() or self._blocked():
            return
        target = self.x_target.value() if axis == "x" else self.y_target.value()
        dev = self.device

        def work():
            dev.set_position(axis, target)
            return dev.get_position(axis)

        def done(final) -> None:
            self._show_one_axis(axis, final)
            self._set_busy(False, f"Moved {axis.upper()} to {final:.0f}")

        def failed(msg: str) -> None:
            self._handle_failure("Move", msg)

        self._set_busy(True, f"Moving {axis.upper()} to {target:.0f}…")
        run_async(work, done, failed)

    def _jog(self, axis: str, sign: int) -> None:
        if self._no_device() or self._blocked():
            return
        step = self.jog_step.value() * sign
        dev = self.device

        def work():
            jogger = getattr(dev, "jog", None)
            if callable(jogger):
                return jogger(axis, step)
            current = dev.get_position(axis)
            dev.set_position(axis, current + step)
            return dev.get_position(axis)

        def done(final) -> None:
            self._show_one_axis(axis, final)
            self._set_busy(False,
                           f"Jogged {axis.upper()} by {step:+.0f} → {final:.0f}")

        def failed(msg: str) -> None:
            self._handle_failure("Jog", msg)

        self._set_busy(True, f"Jogging {axis.upper()} {step:+.0f}…")
        run_async(work, done, failed)

    def _show_one_axis(self, axis: str, value: float) -> None:
        if axis == "x":
            self.x_value.setText(f"{value:.0f}")
        else:
            self.y_value.setText(f"{value:.0f}")

    # ------------------------------------------------------------------ 动作
    def do_halt(self) -> None:
        """急停：不受 busy 限制。"""
        dev = self.device
        if dev is None:
            return

        def work():
            halter = getattr(dev, "halt", None)
            if callable(halter):
                halter()
            return None

        run_async(work, lambda _r: self._append_log("HALT sent"),
                  lambda msg: self._append_log(f"HALT error: {msg}"))

    def do_define_origin(self) -> None:
        if self._no_device() or self._busy:
            return
        ok = QMessageBox.question(
            self, "Define origin",
            "Set the CURRENT position as the new origin (0, 0)?\n\n"
            "This does not move the stage, but all coordinates will shift.")
        if ok != QMessageBox.StandardButton.Yes:
            return
        dev = self.device

        def work():
            fn = getattr(dev, "def_origin", None)
            if callable(fn):
                fn()
            return dev.get_position("x"), dev.get_position("y")

        def done(result) -> None:
            x, y = result
            self.x_value.setText(f"{x:.0f}")
            self.y_value.setText(f"{y:.0f}")
            self._set_busy(False, "Origin defined.")

        def failed(msg: str) -> None:
            self._handle_failure("Define origin", msg)

        self._set_busy(True, "Defining origin…")
        run_async(work, done, failed)

    def do_center_and_zero(self) -> None:
        if self._no_device() or self._busy:
            return
        dialog = CenterZeroDialog(self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if not (accepted and dialog.confirmed()):
            self._append_log("Center and zero cancelled.")
            return

        dev = self.device

        def work():
            fn = getattr(dev, "center_and_zero", None)
            if callable(fn):
                fn(prompt=False)
            return dev.get_position("x"), dev.get_position("y")

        def done(result) -> None:
            x, y = result
            self.x_value.setText(f"{x:.0f}")
            self.y_value.setText(f"{y:.0f}")
            self._set_busy(False, "Center and zero finished.")

        def failed(msg: str) -> None:
            self._handle_failure("Center and zero", msg)

        self._set_busy(True, "Centering… (this can take a while)")
        run_async(work, done, failed)

    # ------------------------------------------------------------------ 日志
    def save_log(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        text = self.log_view.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "Nothing to save", "The log is empty.")
            return
        default = time.strftime("stage_log_%Y%m%d_%H%M%S.txt")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save log", default, "Text files (*.txt);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._append_log(f"Log saved to {path}")

    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """释放设备：先 HALT 再关串口。

        主窗口关闭不会触发子页面的 closeEvent，所以由 MainWindow 统一
        调用（见 ui/main_window.py 的 closeEvent）。
        """
        try:
            self.selector.force_disconnect()
        except Exception:                            # noqa: BLE001
            pass

    def closeEvent(self, event) -> None:      # noqa: N802
        self.cleanup()
        super().closeEvent(event)


# ======================================================================
#  入口
# ======================================================================
def main() -> int:
    os.environ.setdefault("QT_API", "pyside6")

    try:
        import matplotlib
        matplotlib.use("QtAgg")
    except ImportError:
        pass

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Personal Research")
    app.setOrganizationName("personal-research")

    window = StagePage()
    window.setWindowTitle("Stage Controller — Personal Research")
    window.resize(1120, 840)
    apply_theme(window)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
