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
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
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
        #: 每个通道输入框「上次自动填的值」，用来区分自动内容与用户输入
        self._auto_defaults: dict = {}

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

        # 标签页套滚动区：窗口变小、或者内容（比如光子计数的高图）超过
        # 可用高度时**滚动**，而不是把图压扁 / 裁掉坐标轴标签。
        # 之前直接把 QTabWidget 塞进布局，超高就只会裁。
        self.tab_scroll = QScrollArea()
        self.tab_scroll.setWidgetResizable(True)
        self.tab_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.tab_scroll.setWidget(self.tabs)
        outer.addWidget(self.tab_scroll, 1)

        # ---- 日志（可折叠：给图腾出高度）----
        log_head = QHBoxLayout()
        log_head.setSpacing(6)
        lab = QLabel("Log")
        lab.setObjectName("fieldLabel")
        log_head.addWidget(lab)
        log_head.addStretch(1)
        self.log_toggle_btn = QPushButton("Hide log")
        self.log_toggle_btn.setObjectName("ghost")
        self.log_toggle_btn.setFixedWidth(90)
        self.log_toggle_btn.setToolTip(
            "Hide the log to give the live plot more vertical space")
        log_head.addWidget(self.log_toggle_btn)
        self.clear_log_btn = QPushButton("Clear")
        self.clear_log_btn.setObjectName("ghost")
        self.clear_log_btn.setFixedWidth(70)
        log_head.addWidget(self.clear_log_btn)
        outer.addLayout(log_head)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("numericBox")
        # 只保留最近几千行：长采集时日志会无限增长，
        # 完整日志应该落盘，界面上留个窗口就够
        self.log_view.setMaximumBlockCount(3000)
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
        self.log_toggle_btn.clicked.connect(self._toggle_log)

        # 设备下拉一换（比如 Dev1 -> Dev9），自动填的通道名要跟着变
        self.selector.device_box.currentIndexChanged.connect(
            lambda *_: self._sync_defaults())
        # 连接设置里改资源名不会触发型号下拉，要单独听
        self.selector.connection_changed.connect(self._sync_defaults)
        self._sync_defaults(force=True)

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
        # **先**把默认通道同步成当前资源，再读输入框。之前是在设备建好
        # 之后才同步，结果 device.channel 停在旧的 Dev1/ao2，而界面已经
        # 显示 Dev9/ao2 —— 看起来对，实际建在别的板卡上。
        self._sync_defaults()
        channel = self.analog_a.channel_edit.text().strip()
        return {"log_callback": self.log_line.emit,
                "channel": channel or None}

    def _toggle_log(self) -> None:
        """收起/展开日志区，把高度让给上面的图。

        窗口不大的时候，日志那 110px 往往正是图缺的高度。
        """
        visible = self.log_view.isVisible()
        self.log_view.setVisible(not visible)
        self.log_toggle_btn.setText("Show log" if visible else "Hide log")

    def _append_log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {text}")

    def _default_entries(self, resource: str) -> list:
        return [
            (self.analog_a.channel_edit, f"{resource}/ao2"),
            (self.analog_b.channel_edit, f"{resource}/{SECOND_CHANNEL_SUFFIX}"),
            (self.photon.counter_edit, f"{resource}/ctr0"),
            (self.photon.source_edit, f"/{resource}/PFI0"),
            (self.pulse.counter_edit, f"{resource}/ctr0"),
        ]

    def _sync_defaults(self, force: bool = False) -> None:
        """把「自动填的」默认通道同步成当前板卡名。

        需要区分两种内容：

          * 程序自己填的默认值（比如 Dev1/ao2）—— 换板卡时**应该**
            跟着变成 Dev9/ao2
          * 用户手改过的值（Dev9/ao0）—— 必须保留

        只看「输入框是否为空」是不够的：构造时已经把 Dev1/ao2 填进去
        了，它非空，于是换到 Dev9 之后仍然往 Dev1 上输出 —— 界面上写
        着 Dev9，task 建在 Dev1，这比直接报错更危险。

        所以记下每个框上次自动填的值，只有「当前值 == 上次自动值」才
        认为是自动内容、可以覆盖。
        """
        resource = self._resource()
        if not resource:
            return
        for edit, default in self._default_entries(resource):
            last_auto = self._auto_defaults.get(edit)
            current = edit.text().strip()
            if force or not current or current == last_auto:
                edit.setText(default)
                self._auto_defaults[edit] = default

    def _resource(self) -> str:
        """当前板卡名。

        连接后用设备的；没连接时**必须**读 selector 里选的资源 ——
        之前直接返回 "Dev1"，于是选了 Dev9 之后默认通道还是 Dev1/ao2，
        task 就真的建在 Dev1 上了。
        """
        device = self._main_device
        if device is not None:
            return getattr(device, "resource", "Dev1")
        try:
            return self.selector.current_resource() or "Dev1"
        except Exception:                            # noqa: BLE001
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
        # 再同步一次：第二路要用和主设备同一份配置快照
        self._sync_defaults()
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

        self._sync_defaults()

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
