"""设备注册表：Device Interface 下拉菜单里的每一项。

加新设备只需要在 DEVICES 里加一条 DeviceSpec：
  * key / name       —— 下拉里显示什么
  * transports       —— 这个设备支持哪些连接方式（serial / lan / gpib）
  * factory          —— 用 Transport 构造出设备对象

界面拿到的是 DeviceSpec 列表，所以"同一类目下支持多种型号"是天然支持的
（例如 Stage Controller 将来可以同时列 ASI LX-4000 和别的位移台）。

另外提供一个 PlaceholderDevice：不发任何真实命令，只在内存里模拟，
用来在没接硬件时走通"选设备 → Connect → 操作"的完整流程。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from core import nidaq as nidaq_mod
from core import stage as stage_mod
from core.transport import Transport


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
    return stage_mod.ASI_lx_4000(transport, auto_connect=False, **options)


def _make_placeholder(transport: Transport, **options):
    return PlaceholderDevice(transport, **options)


def _make_nidaq(transport: Transport, **options):
    return nidaq_mod.NIDaqDevice(transport, **options)


def _make_placeholder_daq(transport: Transport, **options):
    return nidaq_mod.PlaceholderDaq(transport, **options)


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


def specs_for(kind: str) -> list[DeviceSpec]:
    """某个设备类目下可选的型号列表。"""
    return list(DEVICES.get(kind, []))


def find_spec(kind: str, key: str) -> DeviceSpec | None:
    for spec in DEVICES.get(kind, []):
        if spec.key == key:
            return spec
    return None
