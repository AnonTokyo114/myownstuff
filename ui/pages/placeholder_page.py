"""占位页：菜单项先占好位置，具体功能等代码接进来。

现在的用途：
  * Device Interface 的概览页
  * 它下面的 6 个子分类（Stage / Camera / Laser / Oscilloscope / DAQ / Others）

每个子页都写成"这类设备通常需要什么"，等确认仪器型号和命令集后替换成真实实现。
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class PlaceholderPage(QWidget):
    """标题 + 说明 + 计划中的能力列表。"""

    def __init__(self, *, page_title: str, subtitle: str, note_title: str,
                 note: str, planned: list[tuple[str, str]]) -> None:
        super().__init__()
        self.page_title = page_title

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        title = QLabel(page_title)
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("pageSubtitle")
            sub.setWordWrap(True)
            outer.addWidget(sub)

        # ---- 状态提示 ----
        status = QFrame()
        status.setObjectName("noticeBox")
        status_layout = QVBoxLayout(status)
        status_layout.setContentsMargins(16, 12, 16, 12)
        status_layout.setSpacing(4)

        head = QLabel(note_title)
        head.setObjectName("noticeTitle")
        status_layout.addWidget(head)

        body = QLabel(note)
        body.setObjectName("noticeBody")
        body.setWordWrap(True)
        status_layout.addWidget(body)
        outer.addWidget(status)

        # ---- 计划中的能力 ----
        if planned:
            outer.addSpacing(4)
            cap_title = QLabel("Planned for this section")
            cap_title.setObjectName("fieldLabel")
            outer.addWidget(cap_title)

            for name, desc in planned:
                card = QFrame()
                card.setObjectName("planCard")
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(14, 10, 14, 10)
                card_layout.setSpacing(2)

                name_label = QLabel(f"·  {name}")
                name_label.setObjectName("cardTitle")
                card_layout.addWidget(name_label)

                desc_label = QLabel(desc)
                desc_label.setObjectName("cardDesc")
                desc_label.setWordWrap(True)
                card_layout.addWidget(desc_label)

                outer.addWidget(card)

        outer.addStretch(1)


# ======================================================================
# Device Interface
# ======================================================================
def create_device_page() -> PlaceholderPage:
    """Device Interface 的总览页（点父节点时显示）。"""
    return PlaceholderPage(
        page_title="Device Interface",
        subtitle=("Instrument control, grouped by device class. Pick a "
                  "sub-entry in the left menu."),
        note_title="All sub-sections are placeholders",
        note=("The menu structure is in place. Tell me the actual instruments "
              "for a given category (model, connection type, commands you "
              "send) and I will build that page for real."),
        planned=[
            ("Stage Controller",
             "Translation / rotation stages: move to position, jog, home, limits."),
            ("Camera",
             "Image acquisition: exposure, gain, live preview, snapshot."),
            ("Laser",
             "Source control: on/off, power or current setpoint, temperature readback."),
            ("Oscilloscope",
             "Waveform capture: timebase, range, trigger, save trace."),
            ("Data Acquisition",
             "Multi-channel voltage / sensor logging with live plotting."),
            ("Others",
             "Anything that does not fit the categories above."),
        ],
    )


#: 每个 device 子分类：(标题, 一句话说明, 计划能力列表)
DEVICE_SECTIONS = {
    "stage": (
        "Stage Controller",
        "Motion control for translation and rotation stages.",
        [
            ("Connection",
             "Pick the serial port (or vendor SDK) and connect / disconnect."),
            ("Move & jog",
             "Absolute and relative moves, step jogging, velocity and acceleration."),
            ("Home & limits",
             "Homing routine, soft-limit configuration, current position readback."),
            ("Position logging",
             "Record a position-vs-time trace and export it as CSV."),
        ],
    ),
    "camera": (
        "Camera",
        "Image acquisition from a camera or frame grabber.",
        [
            ("Live preview",
             "Continuous preview with adjustable exposure and gain."),
            ("Snapshot",
             "Capture a single frame and save it as PNG / TIFF."),
            ("Sequences",
             "Timed or triggered capture into a folder or a stack."),
            ("Basic analysis",
             "Roi statistics, intensity profile, background subtraction."),
        ],
    ),
    "laser": (
        "Laser",
        "Laser source control and monitoring.",
        [
            ("On / off & interlocks",
             "State control with an explicit safety confirmation."),
            ("Power or current",
             "Set the output level; read back the actual value."),
            ("Monitoring",
             "Temperature, current and status readback over time."),
            ("Modulation",
             "External or internal modulation settings, if the driver supports it."),
        ],
    ),
    "scope": (
        "Oscilloscope",
        "Waveform capture from an oscilloscope.",
        [
            ("Channel setup",
             "Coupling, range, probe attenuation per channel."),
            ("Timebase & trigger",
             "Time per division, edge / level trigger configuration."),
            ("Single acquisition",
             "Fetch one trace into the plotter view."),
            ("Save & export",
             "Store traces as CSV with metadata."),
        ],
    ),
    "daq": (
        "Data Acquisition",
        "Multi-channel voltage / sensor logging.",
        [
            ("Channel mapping",
             "Name each input channel and set its range."),
            ("Sampling",
             "Sample rate, duration, or free-running acquisition."),
            ("Live plotting",
             "Stream into the Plotter-style chart while recording."),
            ("Recording",
             "Write to CSV as it runs, so a crash does not lose the data."),
        ],
    ),
    "others": (
        "Others",
        "Devices that do not fit the categories above.",
        [
            ("Generic serial console",
             "Send and receive raw text / binary commands."),
            ("Custom scripts",
             "Small per-device command snippets you can reuse."),
            ("Notes",
             "Keep the wiring and command reference next to the tool."),
        ],
    ),
}


def create_device_subpage(kind: str) -> PlaceholderPage:
    """按分类键生成子页；未知键也给一个可读的兜底页。"""
    if kind not in DEVICE_SECTIONS:
        return PlaceholderPage(
            page_title=kind or "Unknown section",
            subtitle="",
            note_title="Unknown device section",
            note=f"No definition for section '{kind}'.",
            planned=[],
        )
    title, subtitle, planned = DEVICE_SECTIONS[kind]
    return PlaceholderPage(
        page_title=title,
        subtitle=subtitle,
        note_title="Not implemented yet",
        note=("This entry exists so the menu structure is complete. When "
              "implemented it will follow the same pattern as Stage Controller: "
              "pick the device from a drop-down, then press Connect. Give me the "
              "instrument model and the commands it accepts, and I will build it."),
        planned=planned,
    )
