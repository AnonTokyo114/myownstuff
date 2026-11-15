"""传输层：把「设备怎么连」与「设备说什么协议」分开。

三种连接方式统一的接口（与 pyserial 的用法对齐，方便原代码迁移）：

    open() / close() / is_open
    write(bytes) / flush()
    read_until(terminator) / read(n)
    in_waiting
    reset_buffers()

这样 ASI 那类"发一行、收一行"的设备代码不需要知道底下是串口、网口还是 GPIB。

  SerialTransport  —— pyserial（必装）
  LanTransport     —— 原始 TCP socket（如 SCPI over TCP/IP 的 5025 端口）
  GpibTransport    —— pyvisa（可选；需要 NI-VISA 或 pyvisa-py 后端）

所有异常统一成 TransportError，调用方只处理一种类型。
"""

from __future__ import annotations

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
