"""设备 GUI 的公共骨架。

设计目标（用户定的架构）：

  1. 主 GUI 是**分类入口**，不再把设备面板内嵌在自己的窗口里
  2. 每个设备 GUI 都能**独立运行**：``python -m devicegui.stage_app``
  3. 主 GUI 通过**子进程**启动它们，所以多个设备窗口可以同时开着，
     一个驱动崩了也不会带走主界面

这里集中三件事：
  * DEVICE_APPS  —— 设备清单（标题、说明、入口模块名）
  * create_device_window(kind) —— 构造界面控件（主 GUI 与独立运行共用）
  * run_device_app(kind)       —— 独立运行的入口逻辑
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

#: 项目根目录（devicegui/base.py -> 项目根）
ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class DeviceApp:
    """一个可独立启动的设备 GUI。"""

    key: str
    title: str
    icon: str
    summary: str
    #: python -m <module> 的模块路径
    module: str
    #: False 表示界面还没做好（占位），仍可启动但只是说明页
    implemented: bool = True


#: 设备清单。加新设备：在 devicegui/ 下加一个 xxx_app.py，然后在这里登记。
DEVICE_APPS: dict[str, DeviceApp] = {
    "stage": DeviceApp(
        key="stage",
        title="Stage Controller",
        icon="⇔",
        summary="Two-axis translation stage over serial (ASI LX-4000).",
        module="devicegui.stage_app",
    ),
    "camera": DeviceApp(
        key="camera",
        title="Camera",
        icon="◉",
        summary="Area-scan camera for imaging / beam profiling.",
        module="devicegui.camera_app",
        implemented=False,
    ),
    "laser": DeviceApp(
        key="laser",
        title="Laser",
        icon="✳",
        summary="Laser source: power, wavelength, shutter control.",
        module="devicegui.laser_app",
        implemented=False,
    ),
    "scope": DeviceApp(
        key="scope",
        title="Oscilloscope",
        icon="∿",
        summary="Waveform capture and triggering.",
        module="devicegui.scope_app",
        implemented=False,
    ),
    "daq": DeviceApp(
        key="daq",
        title="Data Acquisition",
        icon="⧉",
        summary="Analog DC voltage output via NI-DAQmx (PCI-6229 / BNC-2120).",
        module="devicegui.daq_app",
    ),
    "filterwheel": DeviceApp(
        key="filterwheel",
        title="Filter Wheel",
        icon="◍",
        summary="Thorlabs FW102C motorized filter wheel (6 or 12 positions).",
        module="devicegui.filterwheel_app",
    ),
    "others": DeviceApp(
        key="others",
        title="Other instruments",
        icon="⋯",
        summary="Room for anything that does not fit the categories above.",
        module="devicegui.others_app",
        implemented=False,
    ),
}


def create_device_window(kind: str):
    """按 kind 构建设备界面控件。

    主 GUI 的子进程和独立运行都走这条路径，保证两边是同一个界面。
    """
    if kind == "stage":
        from ui.pages.stage_page import StagePage
        return StagePage()
    if kind == "daq":
        from ui.pages.daq_page import DaqPage
        return DaqPage()

    if kind == "filterwheel":
        from ui.pages.filterwheel_page import FilterWheelPage
        return FilterWheelPage()

    from ui.pages.placeholder_page import create_device_subpage
    return create_device_subpage(kind)


def run_device_app(kind: str) -> int:
    """独立运行一个设备 GUI 的入口。

    必须在这里锁定 QT_API —— 与 main.py 同样的理由：本机同时装了
    PyQt5 和 PySide6，不锁会撞到 access violation。

    matplotlib 是**可选**的：stage / DAQ 这类设备页面根本不画图，
    强制依赖它会让「只装 PySide6 + pyserial」的最小安装跑不起来。
    """
    os.environ.setdefault("QT_API", "pyside6")

    try:
        import matplotlib
        matplotlib.use("QtAgg")
    except ImportError:
        pass          # 不画图的设备照常启动

    # 保证从任意工作目录启动都能 import core / ui
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    app_text = DEVICE_APPS.get(kind)
    if app_text is None:
        print(f"Unknown device app: {kind!r}. Known: {', '.join(DEVICE_APPS)}",
              file=sys.stderr)
        return 2

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Personal Research")
    app.setOrganizationName("personal-research")

    window = create_device_window(kind)
    window.setWindowTitle(f"{app_text.title} — Personal Research")
    window.resize(1120, 840)

    # 独立窗口必须自己套主题：QSS 不会从主窗口继承过来，
    # 否则 #danger（HALT / Stop）之类的样式全部失效。
    try:
        from ui.theme import apply_theme
        apply_theme(window)
    except Exception:                            # noqa: BLE001
        pass

    window.show()
    return app.exec()
