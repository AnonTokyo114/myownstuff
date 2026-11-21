"""设备层回归测试：Stage / DAQ / Filter Wheel。

这些用例来自历次审查报告里确认过的缺陷，改代码后跑一遍就能
确认没有把它们改回去。

跑法：
    .venv\\Scripts\\python.exe -m unittest tests.test_devices -v
"""

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.filterwheel import (          # noqa: E402
    FW102CError,
    PlaceholderFilterWheel,
    ThorlabsFW102C,
)
from core.nidaq import (  # noqa: E402
    NIError,
    NIDaqDevice,
    PhotonCounter,
    PlaceholderDaq,
    PlaceholderPhotonCounter,
    PlaceholderPulseGenerator,
    PulseGenerator,
    StopResult,
)
from core.stage import ASIHalted, ASI_lx_4000  # noqa: E402
from core.transport import LocalTransport, Transport  # noqa: E402


# ======================================================================
# 假的 ASI 控制器
# ======================================================================
class FakeAsiTransport(Transport):
    """按 ASI 协议应答，用来在没有硬件时验证取消逻辑。"""

    kind = "serial"
    description = "fake ASI"

    def __init__(self):
        self._open = False
        self.on_command = None
        self.sent = []
        self._pending = ""

    def open(self):
        self._open = True

    def close(self):
        self._open = False

    @property
    def is_open(self):
        return self._open

    def write(self, data):
        cmd = data.decode("ascii").strip()
        self.sent.append(cmd)
        body = cmd[3:] if cmd.startswith("2H ") else cmd
        self._pending = body
        if self.on_command:
            self.on_command(body)

    def flush(self):
        pass

    def read_until(self, terminator):
        body = self._pending
        if body == "WHO":
            return b":A ASI-MS2000\r\n"
        if body == "STATUS":
            return b":AN\r\n"
        if body.startswith("W "):
            return b":A 1234\r\n"
        return b":A\r\n"

    def read(self, n):
        return b""

    @property
    def in_waiting(self):
        return 0

    def reset_buffers(self):
        pass


# ======================================================================
# Stage
# ======================================================================
class TestStageHalt(unittest.TestCase):
    """审查 #2：HALT 必须中断整段运动流程。"""

    def setUp(self):
        self.transport = FakeAsiTransport()
        self.dev = ASI_lx_4000(self.transport, auto_connect=False)
        self.dev.connect()

    def tearDown(self):
        self.dev.close()

    def test_halt_aborts_move(self):
        self.dev._abort.set()
        with self.assertRaises(ASIHalted):
            self.dev._move_abs("X", 100)

    def test_center_and_zero_stops_after_halt(self):
        """第一条 M X 时模拟用户按 HALT，后续移动命令不应发出。"""

        def halt_on_first_move(body):
            if body.startswith("M X"):
                self.dev.halt()

        self.transport.on_command = halt_on_first_move
        self.transport.sent.clear()
        with self.assertRaises(ASIHalted):
            self.dev.center_and_zero()

        moves = [c for c in self.transport.sent if "M " in c]
        self.assertLessEqual(len(moves), 1, f"还在继续移动: {moves}")


class TestStageReadAfterHalt(unittest.TestCase):
    """审查 #5：HALT 之后仍然要能读位置。"""

    def setUp(self):
        self.transport = FakeAsiTransport()
        self.dev = ASI_lx_4000(self.transport, auto_connect=False)
        self.dev.connect()
        self.dev.halt()

    def tearDown(self):
        self.dev.close()

    def test_read_position_after_halt(self):
        self.assertTrue(self.dev.is_halted)
        self.assertEqual(self.dev.get_x_pos(), 1234.0)

    def test_motion_still_blocked(self):
        with self.assertRaises(ASIHalted):
            self.dev._move_abs("X", 100)


# ======================================================================
# DAQ
# ======================================================================
class FailingTask:
    """写 0 V 一定失败的假 task。"""

    def close(self):
        pass

    def write(self, value):
        raise RuntimeError("simulated hardware refusal")


def _make_daq(task):
    dev = NIDaqDevice.__new__(NIDaqDevice)
    dev.transport = LocalTransport("Dev1")
    dev.resource = "Dev1"
    dev.channel = "Dev1/ao2"
    dev._log_cb = None
    dev._lock = threading.RLock()
    dev._task = task
    dev._connected = True
    dev.last_applied = 3.0
    dev.warned = False
    return dev


class TestPhotonCounter(unittest.TestCase):
    """计数器回绕与积分时间夹取。"""

    def test_count_delta_basic(self):
        self.assertEqual(PhotonCounter.count_delta(100, 150), 50)

    def test_count_delta_rollover(self):
        """32 位计数器回绕：2**32-5 -> 3 应该是 8，不是负数。"""
        self.assertEqual(PhotonCounter.count_delta(2 ** 32 - 5, 3), 8)

    def test_count_delta_zero(self):
        self.assertEqual(PhotonCounter.count_delta(1000, 1000), 0)

    def test_bin_clamped(self):
        pc = PlaceholderPhotonCounter(LocalTransport("Dev1"))
        pc.connect()
        self.assertEqual(pc.clamp_bin(0.001), PhotonCounter.BIN_MIN)
        self.assertEqual(pc.clamp_bin(1e6), PhotonCounter.BIN_MAX)
        self.assertEqual(pc.clamp_bin(1.0), 1.0)
        with self.assertRaises(ValueError):
            pc.clamp_bin("abc")
        pc.close()

    def test_placeholder_counts_up(self):
        """占位计数器要真的累积，否则界面永远画不出曲线。"""
        import time
        pc = PlaceholderPhotonCounter(LocalTransport("Dev1"))
        pc.connect()
        pc.start()
        first = pc.read()
        time.sleep(0.2)
        second = pc.read()
        self.assertGreater(second, first)
        pc.close()

    def test_read_before_start_raises(self):
        pc = PlaceholderPhotonCounter(LocalTransport("Dev1"))
        pc.connect()
        with self.assertRaises(NIError):
            pc.read()
        pc.close()


class TestPulseGenerator(unittest.TestCase):
    """脉冲参数校验与占位行为。"""

    def test_valid_params(self):
        pg = PlaceholderPulseGenerator(LocalTransport("Dev1"))
        pg.connect()
        self.assertEqual(pg.check_params(1000, 25), (1000.0, 25.0))
        pg.close()

    def test_frequency_bounds(self):
        pg = PlaceholderPulseGenerator(LocalTransport("Dev1"))
        pg.connect()
        with self.assertRaises(ValueError):
            pg.check_params(0, 25)
        with self.assertRaises(ValueError):
            pg.check_params(2_000_000, 25)
        pg.close()

    def test_duty_bounds(self):
        pg = PlaceholderPulseGenerator(LocalTransport("Dev1"))
        pg.connect()
        for bad in (0, 100, -5, 101):
            with self.assertRaises(ValueError, msg=str(bad)):
                pg.check_params(1000, bad)
        pg.close()

    def test_start_stop_status(self):
        pg = PlaceholderPulseGenerator(LocalTransport("Dev1"))
        pg.connect()
        self.assertIn("not running", pg.status_text())
        pg.start(2500, 30)
        self.assertEqual(pg.last_params, (2500.0, 30.0))
        self.assertIn("2500", pg.status_text())
        self.assertIn("30", pg.status_text())
        self.assertIsNone(pg.stop())
        self.assertIsNone(pg.last_params)
        self.assertIn("not running", pg.status_text())
        pg.close()

    def test_non_numeric_params_rejected(self):
        pg = PlaceholderPulseGenerator(LocalTransport("Dev1"))
        pg.connect()
        with self.assertRaises(ValueError):
            pg.check_params("abc", 25)
        with self.assertRaises(ValueError):
            pg.check_params(1000, None)
        pg.close()


class TestDaqUnknownState(unittest.TestCase):
    """审查 #2（上一轮）：写零失败后不能谎报 0 V。"""

    def test_first_stop_reports_failure(self):
        dev = _make_daq(FailingTask())
        result = dev.stop()
        self.assertIsInstance(result, StopResult)
        self.assertFalse(result.zeroed)
        self.assertIsNone(dev.last_applied)

    def test_second_stop_still_reports_failure(self):
        """task 已释放不等于端子是 0 V。"""
        dev = _make_daq(FailingTask())
        dev.stop()
        again = dev.stop()
        self.assertFalse(again.zeroed, "第二次不该报成功")
        self.assertIsNone(dev.last_applied)

    def test_status_text_says_unknown(self):
        dev = _make_daq(FailingTask())
        dev.stop()
        self.assertIn("UNKNOWN", dev.status_text())

    def test_placeholder_stop_succeeds(self):
        dev = PlaceholderDaq(LocalTransport("Dev1"))
        dev.connect()
        dev.write_voltage(2.0)
        result = dev.stop()
        self.assertTrue(result.zeroed)
        self.assertEqual(dev.last_applied, 0.0)


# ======================================================================
# Filter Wheel
# ======================================================================
class FakeFWTransport(Transport):
    """模拟 FW102C：回显命令、用 '>' 作提示符。"""

    kind = "serial"
    description = "fake FW102C"

    def __init__(self):
        self.timeout = 2.0
        self._pos = 1
        self._pcount = 6
        self._trig = 1
        self._speed = 1
        self._sensors = 0
        self.sent = []
        self.timeouts = []

    # ---- Transport 接口 ----
    def open(self):
        pass

    def close(self):
        pass

    @property
    def is_open(self):
        return True

    def set_timeout(self, seconds):
        self.timeout = seconds
        self.timeouts.append(seconds)

    def reset_buffers(self):
        pass

    def write(self, data):
        self.sent.append(data.decode("ascii").strip())

    def read(self, n):
        return b""

    @property
    def in_waiting(self):
        return 0

    def read_until(self, terminator):
        cmd = self.sent[-1] if self.sent else ""
        if cmd == "*idn?":
            body = "FW102C"
        elif cmd == "pcount?":
            body = str(self._pcount)
        elif cmd.startswith("pcount="):
            self._pcount = int(cmd.split("=")[1])
            self._pos = min(self._pos, self._pcount)
            body = ""
        elif cmd == "pos?":
            body = str(self._pos)
        elif cmd.startswith("pos="):
            self._pos = int(cmd.split("=")[1])
            body = ""
        elif cmd == "trig?":
            body = str(self._trig)
        elif cmd.startswith("trig="):
            self._trig = int(cmd.split("=")[1])
            body = ""
        elif cmd == "speed?":
            body = str(self._speed)
        elif cmd.startswith("speed="):
            self._speed = int(cmd.split("=")[1])
            body = ""
        elif cmd == "sensors?":
            body = str(self._sensors)
        elif cmd.startswith("sensors="):
            self._sensors = int(cmd.split("=")[1])
            body = ""
        elif cmd == "save":
            body = ""
        else:
            body = "Command error CMD_NOT_DEFINED"
        payload = f"{cmd}\r\n{body}\r\n>" if body else f"{cmd}\r\n>"
        return payload.encode("ascii")


class TestFilterWheelProtocol(unittest.TestCase):
    def setUp(self):
        self.transport = FakeFWTransport()
        self.drv = ThorlabsFW102C(self.transport)
        self.drv.connect()

    def tearDown(self):
        self.drv.close()

    def test_identify_and_pcount(self):
        self.assertEqual(self.drv.identity, "FW102C")
        self.assertEqual(self.drv.position_count, 6)

    def test_echo_and_prompt_stripped(self):
        self.assertEqual(self.drv.get_pos(), 1)

    def test_move_and_verify(self):
        self.assertEqual(self.drv.set_pos(4), 4)
        self.assertEqual(self.drv.get_pos(), 4)

    def test_distance_out_of_range(self):
        with self.assertRaises(ValueError):
            self.drv.set_pos(9)

    def test_error_string_raises(self):
        with self.assertRaises(FW102CError):
            self.drv.send_cmd("bogus?")

    def test_move_uses_long_timeout_then_restores(self):
        self.drv.set_pos(3)
        self.assertIn(self.drv.MOVE_TIMEOUT, self.transport.timeouts)
        self.assertEqual(self.transport.timeout, 2.0)

    def test_settings_roundtrip(self):
        self.drv.set_speed_mode(0)
        self.assertEqual(self.drv.get_speed_mode(), 0)
        self.drv.set_sensor_mode(1)
        self.assertEqual(self.drv.get_sensor_mode(), 1)

    def test_snapshot_has_all_fields(self):
        state = self.drv.snapshot()
        self.assertEqual(set(state),
                         {"position", "pcount", "trig", "speed", "sensors"})


class TestFilterWheelPlaceholder(unittest.TestCase):
    def test_connect_move_close(self):
        fw = PlaceholderFilterWheel(LocalTransport("COM7"))
        fw.connect()
        self.assertTrue(fw.is_connected)
        self.assertEqual(fw.set_pos(6), 6)
        self.assertEqual(fw.get_pos(), 6)
        fw.close()
        self.assertFalse(fw.is_connected)

    def test_placeholder_does_not_touch_hardware(self):
        """占位设备不能真的去开串口，否则没有硬件的机器就进不了界面。"""
        fw = PlaceholderFilterWheel(LocalTransport("DOES_NOT_EXIST"))
        fw.connect()
        self.assertTrue(fw.is_connected)
        fw.close()


class TestFilterWheelSlots(unittest.TestCase):
    """轮子位数（6/12）既要能读回，也要能设置。"""

    def test_default_is_six(self):
        fw = PlaceholderFilterWheel(LocalTransport("COM7"))
        fw.connect()
        self.assertEqual(fw.position_count, 6)
        fw.close()

    def test_set_and_read_back(self):
        drv = ThorlabsFW102C(FakeFWTransport())
        drv.connect()
        self.assertEqual(drv.position_count, 6)
        drv.set_wheel_type(12)
        self.assertEqual(drv.position_count, 12)
        self.assertEqual(drv.get_wheel_type(), 12)
        drv.close()

    def test_protocol_sends_pcount(self):
        transport = FakeFWTransport()
        drv = ThorlabsFW102C(transport)
        drv.connect()
        drv.set_wheel_type(12)
        self.assertIn("pcount=12", transport.sent)
        drv.close()

    def test_invalid_type_rejected(self):
        drv = ThorlabsFW102C(FakeFWTransport())
        drv.connect()
        with self.assertRaises(ValueError):
            drv.set_wheel_type(8)
        drv.close()

    def test_shrinking_clamps_position(self):
        """12 位轮在第 10 位时改成 6 位，位置要收回到范围内。"""
        fw = PlaceholderFilterWheel(LocalTransport("COM7"))
        fw.connect()
        fw.set_wheel_type(12)
        fw.set_pos(10)
        fw.set_wheel_type(6)
        self.assertEqual(fw.position_count, 6)
        self.assertLessEqual(fw.get_pos(), 6)
        fw.close()

    def test_snapshot_reports_pcount(self):
        drv = ThorlabsFW102C(FakeFWTransport())
        drv.connect()
        drv.set_wheel_type(12)
        self.assertEqual(drv.snapshot()["pcount"], 12)
        drv.close()


if __name__ == "__main__":
    unittest.main()
