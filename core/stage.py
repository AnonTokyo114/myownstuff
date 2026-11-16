"""ASI LX-4000 二维位移台控制。

与最初版本的区别：
  * 不直接持有 pyserial 对象，而是持有一个 `Transport`（串口 / 网口 / GPIB 都可以）
  * 因此这个类只关心 ASI 协议本身，连接方式由外部注入
  * 去掉了实验室框架的 `Device` / `Parameter` 基类依赖
  * 所有 `print()` 改成 `self._log()`，通过回调送到界面

协议要点（沿用原实现）：
  * 每条命令前缀 "2H "，以 \\r 结尾；回复以 \\n 结束
  * ":A..." 表示接受，":N..." 表示拒绝
  * STATUS 返回 'N' = 空闲，'B' = 运动中
  * 位置单位是 0.1 µm（tenth of a micron）
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from core.transport import Transport, TransportError

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
    def wait_movement(self, timeout: float = 120.0, poll: float = 0.3) -> None:
        """等待 STATUS 变成 'N'（空闲）。回复异常或超时会抛错。

        若期间按了 HALT，会立刻抛 ASIHalted，不必等到超时。
        """
        deadline = time.monotonic() + timeout
        time.sleep(0.3)
        while time.monotonic() < deadline:
            if self._abort.is_set():
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
        """当前 X 位置（0.1 µm）。"""
        self.wait_movement()
        return self._read_pos("X")

    def get_y_pos(self) -> float:
        """当前 Y 位置（0.1 µm）。"""
        self.wait_movement()
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
