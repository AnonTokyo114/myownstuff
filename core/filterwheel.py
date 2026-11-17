"""Thorlabs FW102C 电动滤光片轮。

协议要点（来自原实现，已在新机上验证过）：

  * 命令以 ``\\r`` 结尾，设备用 ``>`` 作提示符
  * ``*idn?`` 取标识，``pcount?`` 取轮子位数（6 或 12）
  * ``pos?`` / ``pos=N`` 读写位置
  * ``trig`` / ``speed`` / ``sensors`` 各是 0/1
  * ``pos=N`` 会**阻塞到转位结束**才回 ``>``，所以要临时把读超时调长

与原始代码的差异：
  * 不再继承实验室框架的 ``Device`` / ``Parameter``，只依赖 ``Transport``，
    因此串口 / 网口都能套（FW102C 本身只有串口）
  * ``print`` 改成 ``log_callback``，日志能进界面
  * 连接时顺带读回标识和位数，界面不用再问一次
"""

from __future__ import annotations

import logging
import time
from typing import Callable

log = logging.getLogger("filterwheel")


class FW102CError(RuntimeError):
    """滤光片轮报错或没有应答。"""


class ThorlabsFW102C:
    #: 这个型号只有串口
    TRANSPORTS = ("serial",)

    #: 转位要等提示符，给足时间（慢速模式 + 12 位轮可能更久）
    MOVE_TIMEOUT = 10.0
    #: 读标识/位置这类短命令
    QUERY_TIMEOUT = 2.0

    #: 合法的轮子位数
    WHEEL_TYPES = (6, 12)

    def __init__(self, transport, *,
                 log_callback: Callable[[str], None] | None = None,
                 **options):
        self.transport = transport
        self._log_cb = log_callback
        self._connected = False
        self._identity = ""
        self._pcount = 6

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

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def position_count(self) -> int:
        """轮子上有几个位置（6 或 12）。"""
        return self._pcount

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------
    def connect(self):
        """打开端口，读回标识与位数。

        失败时关掉端口 —— 否则 COM 口会一直占着，下一次尝试直接报
        "already in use"。
        """
        self.transport.open()
        try:
            self._identity = self.get_id()
            self._pcount = self.get_wheel_type()
            if self._pcount not in self.WHEEL_TYPES:
                raise FW102CError(
                    f"Device reports {self._pcount} positions; expected one of "
                    f"{self.WHEEL_TYPES}. Check which wheel is installed.")
        except Exception:
            try:
                self.transport.close()
            except Exception:                        # noqa: BLE001
                pass
            raise
        self._connected = True
        self._log(f"connected: {self._identity} ({self._pcount} positions)")
        return self

    def close(self):
        self._connected = False
        try:
            self.transport.close()
        except Exception:                            # noqa: BLE001
            pass

    def _require(self):
        """只要传输层打开就能收发。

        不能用 ``self._connected`` 判断：``connect()`` 里要先读标识和位数，
        那时 ``_connected`` 还没置位。
        """
        if not getattr(self.transport, "is_open", False):
            raise FW102CError("No open connection to the filter wheel.")
        return self.transport

    # ------------------------------------------------------------------
    # 底层收发
    # ------------------------------------------------------------------
    def send_cmd(self, cmd: str, timeout: float | None = None) -> str:
        """发一条命令，读到 ``>`` 提示符，返回去掉回显后的正文。"""
        transport = self._require()
        old_timeout = getattr(transport, "timeout", None)
        if timeout is not None:
            try:
                transport.set_timeout(timeout)
            except Exception:                        # noqa: BLE001
                pass
        try:
            transport.reset_buffers()
            transport.write((cmd + "\r").encode("ascii"))
            raw = transport.read_until(b">").decode("ascii", errors="replace")
        finally:
            if timeout is not None and old_timeout is not None:
                try:
                    transport.set_timeout(old_timeout)
                except Exception:                    # noqa: BLE001
                    pass

        if not raw.endswith(">"):
            raise FW102CError(
                f"No prompt after {cmd!r} (timeout?). Got: {raw!r}")

        # 按 CR/LF 切行，丢掉空行和提示符
        lines = [ln.strip()
                 for ln in raw.replace("\r", "\n").split("\n")]
        lines = [ln for ln in lines if ln and ln != ">"]

        # 有些固件会回显命令本身
        if lines and lines[0].lower() == cmd.lower():
            lines = lines[1:]

        resp = " ".join(lines).strip()
        if resp.endswith(">"):
            resp = resp[:-1].strip()

        # 设备用错误字符串报错，例如 "Command error CMD_NOT_DEFINED"
        if "error" in resp.lower():
            raise FW102CError(f"{cmd!r} -> {resp}")
        return resp

    def _query_int(self, cmd: str) -> int:
        resp = self.send_cmd(cmd)
        try:
            return int(resp)
        except ValueError as exc:
            raise FW102CError(
                f"{cmd!r} returned non-integer reply: {resp!r}") from exc

    @staticmethod
    def _check_01(name: str, n: int) -> None:
        if n not in (0, 1):
            raise ValueError(f"{name} must be 0 or 1, got {n}")

    # ------------------------------------------------------------------
    # 标识 / 轮子类型
    # ------------------------------------------------------------------
    def get_id(self) -> str:
        return self.send_cmd("*idn?", timeout=self.QUERY_TIMEOUT)

    def get_wheel_type(self) -> int:
        """返回轮子位数（6 或 12）。"""
        return self._query_int("pcount?")

    def set_wheel_type(self, n: int) -> None:
        """告诉控制器装的是哪种轮子。必须和实际硬件一致。"""
        if n not in self.WHEEL_TYPES:
            raise ValueError(
                f"Wheel type must be one of {self.WHEEL_TYPES}, got {n}")
        self.send_cmd(f"pcount={n}")
        self._pcount = n

    # ------------------------------------------------------------------
    # 位置
    # ------------------------------------------------------------------
    def get_pos(self) -> int:
        return self._query_int("pos?")

    def set_pos(self, n: int, verify: bool = True) -> int:
        """转到第 n 个位置（1..pcount），返回最终位置。

        设备要等转位完成才回 ``>``，所以这里用 MOVE_TIMEOUT 而不是固定
        sleep。**会阻塞**，不要放在界面线程里调用。
        """
        pcount = self.get_wheel_type()
        if not 1 <= n <= pcount:
            raise ValueError(f"Position must be 1..{pcount}, got {n}")

        self.send_cmd(f"pos={n}", timeout=self.MOVE_TIMEOUT)

        if verify:
            actual = self.get_pos()
            if actual != n:
                raise FW102CError(
                    f"Move to {n} not confirmed: wheel reports position "
                    f"{actual}.")
            self._log(f"reached position {actual}")
            return actual
        return n

    # ------------------------------------------------------------------
    # 触发 / 速度 / 传感器
    # ------------------------------------------------------------------
    def set_trig_mode(self, n: int) -> None:
        """0 = 输入（外部脉冲推进），1 = 输出（转位完成后发脉冲）。"""
        self._check_01("trigger mode", n)
        self.send_cmd(f"trig={n}")

    def get_trig_mode(self) -> int:
        return self._query_int("trig?")

    def set_speed_mode(self, n: int) -> None:
        """0 = 慢速，1 = 快速。"""
        self._check_01("speed mode", n)
        self.send_cmd(f"speed={n}")

    def get_speed_mode(self) -> int:
        return self._query_int("speed?")

    def set_sensor_mode(self, n: int) -> None:
        """0 = 空闲时关传感器（减少杂散光），1 = 常开。"""
        self._check_01("sensor mode", n)
        self.send_cmd(f"sensors={n}")

    def get_sensor_mode(self) -> int:
        return self._query_int("sensors?")

    def set_callback_mode(self, n: int) -> None:
        """posevent：转位完成后是否主动发 "Move success"。"""
        self._check_01("callback mode", n)
        self.send_cmd(f"posevent={n}")

    def get_callback_mode(self) -> int:
        return self._query_int("posevent?")

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def save_settings(self) -> None:
        """把当前设置存成上电默认值。"""
        self.send_cmd("save")

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        """一次把界面需要的状态读齐（阻塞，放后台线程用）。"""
        return {
            "position": self.get_pos(),
            "pcount": self.get_wheel_type(),
            "trig": self.get_trig_mode(),
            "speed": self.get_speed_mode(),
            "sensors": self.get_sensor_mode(),
        }


# ======================================================================
# 占位设备（无硬件时走通界面流程）
# ======================================================================
class PlaceholderFilterWheel:
    """模拟一个滤光片轮：位置在内存里变，不发任何真实命令。"""

    TRANSPORTS = ("serial",)
    MOVE_TIMEOUT = 10.0
    WHEEL_TYPES = (6, 12)

    def __init__(self, transport, *,
                 log_callback: Callable[[str], None] | None = None,
                 **options):
        self.transport = transport
        self._log_cb = log_callback
        self._connected = False
        self._identity = "Placeholder FW102C"
        self._pcount = 6
        self._position = 1
        self._trig = 1
        self._speed = 1
        self._sensors = 0

    def _log(self, msg: str) -> None:
        if self._log_cb is not None:
            try:
                self._log_cb(msg)
            except Exception:                        # noqa: BLE001
                pass

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def identity(self) -> str:
        return self._identity

    @property
    def position_count(self) -> int:
        return self._pcount

    def connect(self):
        # 占位设备**不碰**真实硬件：连 transport 都不打开。
        # 这样即使选的是 COM99 这种不存在的端口，也能走通界面流程。
        self._connected = True
        self._log("Placeholder filter wheel connected. No hardware is driven.")
        return self

    def close(self):
        self._connected = False
        try:
            self.transport.close()
        except Exception:                            # noqa: BLE001
            pass

    def _require(self):
        if not self._connected:
            raise FW102CError("Placeholder filter wheel is not connected.")

    def get_id(self) -> str:
        return self._identity

    def get_wheel_type(self) -> int:
        return self._pcount

    def set_wheel_type(self, n: int) -> None:
        if n not in self.WHEEL_TYPES:
            raise ValueError(f"Wheel type must be one of {self.WHEEL_TYPES}")
        self._pcount = n
        self._position = min(self._position, n)

    def get_pos(self) -> int:
        self._require()
        return self._position

    def set_pos(self, n: int, verify: bool = True) -> int:
        self._require()
        if not 1 <= n <= self._pcount:
            raise ValueError(f"Position must be 1..{self._pcount}, got {n}")
        self._log(f"Placeholder turning to position {n}…")
        time.sleep(0.3)                              # 假装转了一下
        self._position = n
        self._log(f"reached position {n}")
        return n

    def set_trig_mode(self, n: int) -> None:
        self._require()
        ThorlabsFW102C._check_01("trigger mode", n)
        self._trig = n

    def get_trig_mode(self) -> int:
        return self._trig

    def set_speed_mode(self, n: int) -> None:
        self._require()
        ThorlabsFW102C._check_01("speed mode", n)
        self._speed = n

    def get_speed_mode(self) -> int:
        return self._speed

    def set_sensor_mode(self, n: int) -> None:
        self._require()
        ThorlabsFW102C._check_01("sensor mode", n)
        self._sensors = n

    def get_sensor_mode(self) -> int:
        return self._sensors

    def set_callback_mode(self, n: int) -> None:
        self._require()
        ThorlabsFW102C._check_01("callback mode", n)

    def get_callback_mode(self) -> int:
        return 0

    def save_settings(self) -> None:
        self._require()
        self._log("Placeholder settings saved (nothing written).")

    def snapshot(self) -> dict:
        self._require()
        return {
            "position": self._position,
            "pcount": self._pcount,
            "trig": self._trig,
            "speed": self._speed,
            "sensors": self._sensors,
        }
