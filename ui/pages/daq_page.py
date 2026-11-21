"""Data Acquisition 页：双路 DC 输出 + 光子计数 + 脉冲输出。

三个功能各自独立：DC 输出有**两路**（每路一个 task），光子计数和脉冲
输出分别用一块卡的计数器输入/输出。它们都从同一次「选设备 → 连接」
派生而来，但各自持有自己的 transport，互不影响关闭顺序。

为什么分成标签页而不是堆在一列：
    光子计数带一条实时曲线，脉冲参数又不多。挤在一列里会让左边控件栏
    长到必须滚动，而滚动条一出现「Apply」按钮就跑到视野外，误操作风险
    比切标签页高得多。
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.nidaq import (
    PhotonCounter,
    PlaceholderDaq,
    PlaceholderPhotonCounter,
    PlaceholderPulseGenerator,
    PulseGenerator,
)
from core.transport import LocalTransport
from ui.widgets.daq_panels import AnalogChannelPanel, PhotonPanel, PulsePanel
from ui.widgets.device_selector import DeviceSelector

#: 第二路 DC 输出的默认通道后缀（第一路由设备自己决定）
SECOND_CHANNEL_SUFFIX = "ao3"


class DaqPage(QWidget):
    page_title = "Data Acquisition"

    log_line = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._main_device = None
        self._second_device = None
        self._photon_device = None
        self._pulse_device = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        title = QLabel("Data Acquisition")
        title.setObjectName("pageTitle")
        outer.addWidget(title)
        subtitle = QLabel(
            "Two analog DC outputs, a photon counter input and a counter pulse "
            "output on NI-DAQ hardware (PCI-6229 / BNC-2120). Above the safety "
            "threshold you must confirm eye protection first.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        self.selector = DeviceSelector(
            kind="daq",
            device_options=self._device_options,
            hint="Choose 'Placeholder device' to try the flows without hardware.",
        )
        outer.addWidget(self.selector)

        # ---- 三个功能各占一个标签页 ----
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")

        analog = QWidget()
        analog_layout = QHBoxLayout(analog)
        analog_layout.setContentsMargins(10, 10, 10, 10)
        analog_layout.setSpacing(12)
        self.analog_a = AnalogChannelPanel("Analog output A", "Dev1/ao2")
        self.analog_b = AnalogChannelPanel("Analog output B", "Dev1/ao3")
        for panel in (self.analog_a, self.analog_b):
            panel.log_line.connect(self._append_log)
            # 顶部对齐：面板按内容高度，不然两路会被拉到整个标签页那么高，
            # 中间留出一大片空白，看起来像界面坏了
            analog_layout.addWidget(panel, 1, Qt.AlignmentFlag.AlignTop)
        self.tabs.addTab(analog, "Analog outputs")

        self.photon = PhotonPanel()
        self.photon.log_line.connect(self._append_log)
        self.tabs.addTab(self.photon, "Photon counting")

        self.pulse = PulsePanel()
        self.pulse.log_line.connect(self._append_log)
        self.tabs.addTab(self.pulse, "Pulse generator")

        outer.addWidget(self.tabs, 1)

        # ---- 日志 ----
        log_head = QHBoxLayout()
        log_head.setSpacing(6)
        lab = QLabel("Log")
        lab.setObjectName("fieldLabel")
        log_head.addWidget(lab)
        log_head.addStretch(1)
        self.clear_log_btn = QPushButton("Clear")
        self.clear_log_btn.setObjectName("ghost")
        self.clear_log_btn.setFixedWidth(70)
        log_head.addWidget(self.clear_log_btn)
        outer.addLayout(log_head)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("numericBox")
        self.log_view.setFixedHeight(110)
        outer.addWidget(self.log_view)

        # ---- 信号 ----
        self.log_line.connect(self._append_log)
        self.selector.log.connect(self._append_log)
        self.selector.connected.connect(self._on_connected)
        self.selector.disconnected.connect(self._on_disconnected)
        self.selector.failed.connect(
            lambda msg: QApplicationCritical(self, msg))
        self.clear_log_btn.clicked.connect(self.log_view.clear)

        self._update_titles()

    # ------------------------------------------------------------------ 工具
    @property
    def device(self):
        return self.selector.device

    def _device_options(self) -> dict:
        """建主设备时的参数。

        通道取**面板上用户填的那个**：之前误以为 DeviceSpec 上有
        resource 字段，结果永远是 None，用户写的 Dev9/ao0 就被 ao2
        覆盖了。
        """
        channel = self.analog_a.channel_edit.text().strip()
        return {"log_callback": self.log_line.emit,
                "channel": channel or None}

    def _append_log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {text}")

    def _update_titles(self) -> None:
        """给**空着的**输入框补默认值。

        只在空时补：用户可能已经写了 Dev9/ao0 或 Dev9/ctr1，连接时
        把它们覆盖成 ao2/ctr0 就是「界面上显示的和实际用的不一样」。
        """
        resource = self._resource()
        if not resource:
            return
        defaults = [
            (self.analog_a.channel_edit, f"{resource}/ao2"),
            (self.analog_b.channel_edit, f"{resource}/{SECOND_CHANNEL_SUFFIX}"),
            (self.photon.counter_edit, f"{resource}/ctr0"),
            (self.photon.source_edit, f"/{resource}/PFI0"),
            (self.pulse.counter_edit, f"{resource}/ctr0"),
        ]
        for edit, default in defaults:
            if not edit.text().strip():
                edit.setText(default)

    def _resource(self) -> str:
        device = self._main_device
        if device is not None:
            return getattr(device, "resource", "Dev1")
        return "Dev1"

    def _is_placeholder(self) -> bool:
        """连接的是占位设备吗？

        占位模式下派生设备也必须用占位版：真实版的 connect() 会调
        driver_available()，那是要碰 NI 驱动的，在没有驱动的机器上会
        卡很久（甚至卡死），完全违背「用占位设备先跑通流程」的初衷。
        """
        return isinstance(self._main_device, PlaceholderDaq)

    def _spawn(self, cls, resource: str, **kwargs):
        """在同一块卡上新建一个独立设备（用自己的 transport）。"""
        transport = LocalTransport(resource)
        device = cls(transport, log_callback=self.log_line.emit, **kwargs)
        device.connect()
        return device

    # ------------------------------------------------------------------ 连接
    def _on_connected(self, device) -> None:
        self._main_device = device
        resource = getattr(device, "resource", "Dev1")

        # 第一路：面板上填的就是要用的通道；空着才退回默认
        channel_a = (self.analog_a.channel_edit.text().strip()
                     or f"{resource}/ao2")
        device.channel = channel_a
        self.analog_a.channel_edit.setText(channel_a)
        self.analog_a.set_device(device)

        # ---- 第二路 DC 输出：同理，尊重用户填的通道 ----
        channel_b = (self.analog_b.channel_edit.text().strip()
                     or f"{resource}/{SECOND_CHANNEL_SUFFIX}")
        try:
            self._second_device = self._spawn(
                type(device), resource, channel=channel_b)
            self.analog_b.channel_edit.setText(channel_b)
            self.analog_b.set_device(self._second_device)
            self._append_log(
                f"Second analog channel ready: {self._second_device.channel}")
        except Exception as exc:                     # noqa: BLE001
            self._second_device = None
            self.analog_b.set_device(None)
            self._append_log(f"Second analog channel unavailable: {exc}")

        # ---- 光子计数 ----
        try:
            photon_cls = (PlaceholderPhotonCounter if self._is_placeholder()
                          else PhotonCounter)
            self._photon_device = self._spawn(photon_cls, resource)
            self.photon.set_device(self._photon_device)
            self._append_log("Photon counter ready.")
        except Exception as exc:                     # noqa: BLE001
            self._photon_device = None
            self.photon.set_device(None)
            self._append_log(f"Photon counter unavailable: {exc}")

        # ---- 脉冲输出 ----
        try:
            pulse_cls = (PlaceholderPulseGenerator if self._is_placeholder()
                         else PulseGenerator)
            self._pulse_device = self._spawn(pulse_cls, resource)
            self.pulse.set_device(self._pulse_device)
            self._append_log("Pulse generator ready.")
        except Exception as exc:                     # noqa: BLE001
            self._pulse_device = None
            self.pulse.set_device(None)
            self._append_log(f"Pulse generator unavailable: {exc}")

        self._update_titles()

    def _on_disconnected(self) -> None:
        self.analog_a.set_device(None)
        self.analog_b.set_device(None)
        self.photon.set_device(None)
        self.pulse.set_device(None)

        for attr in ("_second_device", "_photon_device", "_pulse_device"):
            device = getattr(self, attr)
            if device is not None:
                try:
                    device.close()
                except Exception:                    # noqa: BLE001
                    pass
                setattr(self, attr, None)
        self._main_device = None

    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """主窗口关闭时调用：把所有输出停下来再断开。

        DC 输出归零、脉冲停止、计数停止 —— 顺序无所谓，但一个都不能漏。
        主窗口关闭不会触发子页面的 closeEvent，所以由 MainWindow 统一调。
        """
        for panel in (self.analog_a, self.analog_b):
            try:
                panel.interrupt()
            except Exception:                        # noqa: BLE001
                pass
        for panel in (self.photon, self.pulse):
            try:
                panel.interrupt()
            except Exception:                        # noqa: BLE001
                pass
        try:
            self.selector.force_disconnect()
        except Exception:                            # noqa: BLE001
            pass

    def closeEvent(self, event) -> None:         # noqa: N802
        self.cleanup()
        super().closeEvent(event)


def QApplicationCritical(parent, message: str) -> None:
    """把 QMessageBox.critical 包一层，便于在信号里直接连。"""
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.critical(parent, "Connect failed", message)
