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
    #: 收纳在这个入口下的其它设备（它们的 key）。
    #: 有 members 的入口会在卡片上多一个下拉，Launch 启动选中的那个；
    #: 被收纳的设备**不会**再单独占一张卡片。
    members: tuple[str, ...] = ()


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
    "others": DeviceApp(
        key="others",
        title="Other instruments",
        icon="⋯",
        summary="Anything that does not fit the categories above.",
        module="devicegui.others_app",
        # Others 是一个「收纳类」入口：卡片上会列出下面这些设备，
        # 选一个再 Launch。它们不单独占卡片。
        members=("filterwheel",),
    ),
    # 被 Others 收纳的设备：仍然要在这里登记（供启动与查找），
    # 但 top_level_apps() 会把它过滤掉，所以界面上只出现在 Others 的下拉里。
    "filterwheel": DeviceApp(
        key="filterwheel",
        title="Filter Wheel (Thorlabs FW102C)",
        icon="◍",
        summary="Motorized filter wheel, 6 or 12 positions.",
        module="devicegui.filterwheel_app",
    ),
}


def top_level_apps() -> list[DeviceApp]:
    """返回应该在 Device Interface 上单独占一张卡片的设备。

    被某个入口收编为 ``members`` 的设备不单独出现（例如滤光片轮归在
    Others 下面）；它仍然可以被单独启动，只是不占卡片。
    """
    owned: set[str] = set()
    for app in DEVICE_APPS.values():
        owned.update(app.members)
    return [app for app in DEVICE_APPS.values() if app.key not in owned]


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


#: 这些设备窗口会画图，启动时必须先配好 matplotlib 后端。
#: 其余的（stage / filterwheel / camera / laser / scope / others）不画图，
#: 导入 matplotlib 纯粹是浪费 —— 它占启动时间里相当一块。
#:
#: 注意 DAQ **要**画：光子计数的实时曲线用 matplotlib。
DRAWS_PLOTS = {"daq"}


def run_device_app(kind: str) -> int:
    """独立运行一个设备 GUI 的入口。

    必须在这里锁定 QT_API —— 与 main.py 同样的理由：本机同时装了
    PyQt5 和 PySide6，不锁会撞到 access violation。**这一步必须在任何
    Qt / matplotlib 导入之前**。

    matplotlib 只有真正画图的设备才导入：没装它时 stage / filterwheel
    这类窗口照常启动（「只装 PySide6 + pyserial」的最小安装也能用）。
    """
    os.environ.setdefault("QT_API", "pyside6")

    if kind in DRAWS_PLOTS:
        try:
            import matplotlib
            matplotlib.use("QtAgg")
        except ImportError:
            pass          # 没装 matplotlib：曲线画不出来，但窗口还能开

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
