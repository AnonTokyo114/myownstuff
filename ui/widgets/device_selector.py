"""Device Interface 各子页共用的「选设备 → 配连接 → Connect」组件。

交互（按用户的要求收敛成三步）：
  1. **Device** 下拉选设备型号（同一类目下可以有多种）
  2. **Connection** 按钮打开对话框配置连接方式与参数
     （Serial / LAN / GPIB，只列出该设备支持的那几种）
     按钮上直接显示当前配置摘要，例如 "Serial · COM6 @ 115200"
  3. 点 **Connect** 建立连接

相比把参数摊在主界面上，这样做主界面只有一行控件，连接参数（不常改）
收进对话框，视野更干净。
"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core import devices as dev_mod
from core import transport as tr_mod
from ui.settings import settings
from ui.workers import run_async

#: 模块级 logger：对象销毁后仍要能记录收尾过程
log = logging.getLogger("device_selector")

#: 连接方式 -> 显示名
TRANSPORT_LABELS = {
    "serial": "Serial (COM)",
    "lan": "LAN (TCP/IP)",
    "gpib": "GPIB / VISA",
    "local": "Local board (NI-DAQmx)",
}


# ======================================================================
# 连接设置对话框
# ======================================================================
class ConnectionDialog(QDialog):
    """配置连接方式与参数。确定后通过 result_config() 取出结果。

    参数按连接方式分开保存（`configs["serial"]` / `["lan"]` / `["gpib"]`）：
    串口的 "port" 是 COM6，网口的 "port" 是 5025，同名不同义，混用会解析失败。
    """

    #: 各类连接方式的默认配置
    DEFAULT_CONFIGS = {
        "serial": {"port": "COM6", "baudrate": 115200},
        "lan": {"host": "192.168.1.100", "port": 5025},
        "gpib": {"address": "GPIB0::12::INSTR"},
        "local": {"resource": "Dev1"},
    }

    def __init__(self, transports: tuple[str, ...], *,
                 kind: str = "serial",
                 configs: dict[str, dict] | None = None,
                 device_name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Connection settings")
        self.setMinimumWidth(520)
        self._configs = {k: dict(v) for k, v in self.DEFAULT_CONFIGS.items()}
        for k, v in (configs or {}).items():
            self._configs[k] = dict(v)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        if device_name:
            head = QLabel(device_name)
            head.setObjectName("fieldLabel")
            layout.addWidget(head)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("Method:"))
        self.method_box = QComboBox()
        self.method_box.setMinimumWidth(180)
        for t in transports:
            self.method_box.addItem(TRANSPORT_LABELS.get(t, t), t)
        index = self.method_box.findData(kind)
        self.method_box.setCurrentIndex(index if index >= 0 else 0)
        row.addWidget(self.method_box)
        row.addStretch(1)
        layout.addLayout(row)

        frame = QFrame()
        frame.setObjectName("formBox")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(12, 10, 12, 10)
        frame_layout.setSpacing(6)

        self.stack = QStackedWidget()
        self._serial_page = self._build_serial_page()
        self._lan_page = self._build_lan_page()
        self._gpib_page = self._build_gpib_page()
        self._local_page = self._build_local_page()
        for page in (self._serial_page, self._lan_page, self._gpib_page,
                     self._local_page):
            self.stack.addWidget(page)
        frame_layout.addWidget(self.stack)
        layout.addWidget(frame)

        self.note = QLabel("")
        self.note.setObjectName("pageSubtitle")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.method_box.currentIndexChanged.connect(self._on_method_changed)
        self._on_method_changed()

    # ------------------------------------------------------------------ 参数页
    def _build_serial_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("Port:"))
        self.port_box = QComboBox()
        self.port_box.setEditable(True)
        self.port_box.setMinimumWidth(170)
        row.addWidget(self.port_box)

        self.port_rescan_btn = QPushButton("↻")
        self.port_rescan_btn.setObjectName("ghost")
        self.port_rescan_btn.setFixedWidth(34)
        self.port_rescan_btn.setToolTip("Rescan serial ports")
        row.addWidget(self.port_rescan_btn)

        row.addWidget(QLabel("Baud:"))
        self.baud_box = QComboBox()
        self.baud_box.addItems(["115200", "57600", "38400", "19200", "9600"])
        self.baud_box.setFixedWidth(96)
        row.addWidget(self.baud_box)
        row.addStretch(1)
        outer.addLayout(row)

        # 恢复保存的波特率；列表里没有就补进去（用户可能设过自定义值）
        saved_baud = str(self._configs["serial"].get("baudrate", 115200))
        index = self.baud_box.findText(saved_baud)
        if index < 0:
            self.baud_box.addItem(saved_baud)
            index = self.baud_box.count() - 1
        self.baud_box.setCurrentIndex(index)

        self.port_rescan_btn.clicked.connect(self._rescan_serial_ports)
        self._rescan_serial_ports()
        return page

    def _build_lan_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("Host:"))
        lan_conf = self._configs["lan"]
        self.host_edit = QLineEdit(str(lan_conf.get("host", "192.168.1.100")))
        self.host_edit.setMinimumWidth(170)
        row.addWidget(self.host_edit)

        row.addWidget(QLabel("Port:"))
        self.lan_port_spin = QSpinBox()
        self.lan_port_spin.setRange(1, 65535)
        try:
            lan_port = int(lan_conf.get("port", 5025))
        except (TypeError, ValueError):
            lan_port = 5025
        self.lan_port_spin.setValue(lan_port)
        self.lan_port_spin.setFixedWidth(80)
        row.addWidget(self.lan_port_spin)
        row.addStretch(1)
        outer.addLayout(row)

        tip = QLabel("Raw TCP (SCPI over socket). 5025 is the usual SCPI port; "
                     "some instruments use 23 or a vendor-specific one.")
        tip.setObjectName("pageSubtitle")
        tip.setWordWrap(True)
        outer.addWidget(tip)
        return page

    def _build_gpib_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("Address:"))
        self.visa_box = QComboBox()
        self.visa_box.setEditable(True)
        self.visa_box.setMinimumWidth(210)
        self.visa_box.addItem(
            str(self._configs["gpib"].get("address", "GPIB0::12::INSTR")))
        row.addWidget(self.visa_box)

        self.visa_list_btn = QPushButton("List")
        self.visa_list_btn.setObjectName("ghost")
        self.visa_list_btn.setFixedWidth(52)
        self.visa_list_btn.setToolTip("List VISA resources")
        row.addWidget(self.visa_list_btn)
        row.addStretch(1)
        outer.addLayout(row)

        self.visa_note = QLabel("")
        self.visa_note.setObjectName("pageSubtitle")
        self.visa_note.setWordWrap(True)
        outer.addWidget(self.visa_note)

        self.visa_list_btn.clicked.connect(self._list_visa_resources)
        if not tr_mod.visa_available():
            self.visa_note.setText(
                "pyvisa is not installed. GPIB needs: pip install pyvisa pyvisa-py")
        else:
            self.visa_note.setText("Click List to enumerate VISA resources.")
        return page

    def _build_local_page(self) -> QWidget:
        """本地板卡：由厂商驱动直接访问，只需要一个设备资源名。"""
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("Resource:"))
        self.resource_edit = QLineEdit(
            str(self._configs["local"].get("resource", "Dev1")))
        self.resource_edit.setMinimumWidth(140)
        self.resource_edit.setPlaceholderText("e.g. Dev1")
        row.addWidget(self.resource_edit)

        self.local_list_btn = QPushButton("List")
        self.local_list_btn.setObjectName("ghost")
        self.local_list_btn.setFixedWidth(52)
        self.local_list_btn.setToolTip("List local DAQ devices")
        row.addWidget(self.local_list_btn)
        row.addStretch(1)
        outer.addLayout(row)

        self.local_note = QLabel("")
        self.local_note.setObjectName("pageSubtitle")
        self.local_note.setWordWrap(True)
        outer.addWidget(self.local_note)

        self.local_list_btn.clicked.connect(self._list_local_devices)
        self._list_local_devices()
        return page

    def _list_local_devices(self) -> None:
        """试着列出 NI-DAQ 设备；没驱动时给出说明。"""
        try:
            from core import nidaq as ni_mod
        except Exception as exc:                     # noqa: BLE001
            self.local_note.setText(f"NI-DAQ module unavailable: {exc}")
            return

        if not ni_mod.NIDAQMX_AVAILABLE:
            self.local_note.setText(
                "nidaqmx is not installed. Local boards need: pip install nidaqmx")
            return
        if not ni_mod.driver_available():
            self.local_note.setText(
                "NI-DAQmx driver not found. Install it from National Instruments; "
                "the Python package alone is not enough.")
            return

        devices = ni_mod.list_devices()
        if devices:
            current = self.resource_edit.text().strip()
            self.resource_edit.setText(current or devices[0])
            self.local_note.setText("Found: " + ", ".join(devices))
        else:
            self.local_note.setText("Driver present but no DAQ devices detected.")

    # ------------------------------------------------------------------
    def _rescan_serial_ports(self) -> None:
        # 保留「用户当前看到并选择」的端口。
        #
        # 必须用 selected_port() 而不是 currentData()：手输 COM9 之后
        # currentData() 仍然返回**旧列表项**（COM7），拿它当 current 会让
        # 刷新把界面改回 COM7，接着点 OK 就保存了错的端口。
        # 同理，保存的端口不在扫描结果里时也要原样留住。
        current = (self.selected_port()
                   or str(self._configs["serial"].get("port", "COM6")))

        self.port_box.blockSignals(True)
        self.port_box.clear()
        ports = tr_mod.available_serial_ports()
        for name, desc in ports:
            self.port_box.addItem(f"{name} — {desc}" if desc else name, name)

        index = self.port_box.findData(current)
        if index >= 0:
            self.port_box.setCurrentIndex(index)
        else:
            # 保存的端口不在扫描结果里（或者压根没扫到）：
            # 直接把它显示在编辑框里，**不要**换成别的端口
            self.port_box.setCurrentText(current)
        self.port_box.blockSignals(False)

    def _list_visa_resources(self) -> None:
        resources = tr_mod.list_visa_resources()
        current = self.visa_box.currentText()
        self.visa_box.blockSignals(True)
        self.visa_box.clear()
        if resources:
            for r in resources:
                self.visa_box.addItem(r)
        else:
            self.visa_box.addItem(
                str(self._configs["gpib"].get("address", "GPIB0::12::INSTR")))
        if current:
            self.visa_box.setCurrentText(current)
        self.visa_box.blockSignals(False)
        self.visa_note.setText(
            f"{len(resources)} VISA resource(s)" if resources
            else "no VISA resources found (is NI-VISA or pyvisa-py installed?)")

    def _on_method_changed(self, *_args) -> None:
        kind = self.selected_kind()
        page = {"serial": self._serial_page,
                "lan": self._lan_page,
                "gpib": self._gpib_page,
                "local": self._local_page}.get(kind)
        if page is not None:
            self.stack.setCurrentWidget(page)
        if kind == "gpib" and not tr_mod.visa_available():
            self.note.setText("GPIB requires pyvisa; the Connect will fail until "
                              "it is installed.")
        else:
            self.note.setText("")

    # ------------------------------------------------------------------ 结果
    def selected_kind(self) -> str:
        return self.method_box.currentData() or "serial"

    def selected_port(self) -> str:
        """当前选中的端口名。

        必须区分「选已有项」和「手输」：下拉是可编辑的，用户打字之后
        currentData() 仍然返回**旧那一项**的端口，直接用它就会把界面
        上显示的 COM9 提交成 COM3。
        """
        text = self.port_box.currentText().strip()
        index = self.port_box.currentIndex()
        if index >= 0 and text == self.port_box.itemText(index):
            data = self.port_box.itemData(index)
            if data:
                return str(data)
        # 文字和当前项不一致（或者没有选中项）-> 按用户输入的文字算，
        # 去掉 "COM3 — USB Serial" 这种描述后缀
        return text.split(" — ")[0].strip()

    def selected_params(self) -> dict:
        kind = self.selected_kind()
        if kind == "serial":
            port = self.selected_port()
            return {"port": port or "COM6",
                    "baudrate": int(self.baud_box.currentText())}
        if kind == "lan":
            return {"host": self.host_edit.text().strip() or "127.0.0.1",
                    "port": int(self.lan_port_spin.value())}
        if kind == "local":
            return {"resource": self.resource_edit.text().strip() or "Dev1"}
        return {"address": self.visa_box.currentText().strip()}

    def result_config(self) -> tuple[str, dict]:
        return self.selected_kind(), self.selected_params()

    def summary(self) -> str:
        """一行摘要，给主界面的按钮用。"""
        kind, params = self.result_config()
        if kind == "serial":
            return f"Serial · {params.get('port', '?')} @ {params.get('baudrate', '?')}"
        if kind == "lan":
            return f"LAN · {params.get('host', '?')}:{params.get('port', '?')}"
        if kind == "local":
            return f"Local · {params.get('resource', '?')}"
        return f"GPIB · {params.get('address', '?')}"


# ======================================================================
# 主组件
# ======================================================================
class DeviceSelector(QFrame):
    """Device 下拉 + Connection 按钮 + Connect/Disconnect。"""

    connected = Signal(object)
    #: 连接参数（端口/地址/资源名）被改过
    connection_changed = Signal()
    failed = Signal(str)
    log = Signal(str)
    disconnected = Signal()

    def __init__(self, *,
                 kind: str,
                 device_options: Callable[[], dict] | None = None,
                 hint: str = "",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("formBox")
        self._kind = kind
        self._device_options = device_options or (lambda: {})
        self._device = None
        #: 连接请求代次。关闭 / 断开时递增，让在途的 Connect 作废 ——
        #: 只靠上层页面的任务守卫是不够的：那次连接仍会在后台建好设备。
        self._connect_request = 0
        self._busy = False
        #: 外部（页面）正在执行耗时操作时置位，锁住连接控件
        self._locked = False

        #: 每种连接方式的最后一次配置，切换型号时保留。
        #: 初值从 settings.ini 读，所以改过一次端口之后就不用每次再选。
        self._configs: dict[str, dict] = self._load_configs()
        #: 当前使用的连接方式
        self._conn_kind = "serial"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        head = QLabel("Device")
        head.setObjectName("fieldLabel")
        layout.addWidget(head)

        # ---- 设备下拉 ----
        dev_row = QHBoxLayout()
        dev_row.setSpacing(6)
        self.device_box = QComboBox()
        self.device_box.setMinimumWidth(300)
        dev_row.addWidget(self.device_box, 1)
        self.rescan_btn = QPushButton("↻")
        self.rescan_btn.setObjectName("ghost")
        self.rescan_btn.setFixedWidth(34)
        self.rescan_btn.setToolTip("Rescan for devices")
        dev_row.addWidget(self.rescan_btn)
        layout.addLayout(dev_row)

        self.device_desc = QLabel("")
        self.device_desc.setObjectName("pageSubtitle")
        self.device_desc.setWordWrap(True)
        layout.addWidget(self.device_desc)

        # ---- 连接设置按钮 ----
        conn_row = QHBoxLayout()
        conn_row.setSpacing(6)
        self.conn_btn = QPushButton("Connection: Serial · COM6 @ 115200")
        self.conn_btn.setObjectName("ghost")
        self.conn_btn.setToolTip("Configure how this device is connected")
        conn_row.addWidget(self.conn_btn, 1)
        layout.addLayout(conn_row)

        # ---- 连接/断开 ----
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("primary")
        self.disconnect_btn = QPushButton("Disconnect")
        self.disconnect_btn.setObjectName("ghost")
        btn_row.addWidget(self.connect_btn, 1)
        btn_row.addWidget(self.disconnect_btn, 1)
        layout.addLayout(btn_row)

        self.status_label = QLabel("● Disconnected")
        self.status_label.setObjectName("pageSubtitle")
        layout.addWidget(self.status_label)

        if hint:
            tip = QLabel(hint)
            tip.setObjectName("pageSubtitle")
            tip.setWordWrap(True)
            layout.addWidget(tip)

        # ---- 信号 ----
        self.device_box.currentIndexChanged.connect(self._on_device_changed)
        self.rescan_btn.clicked.connect(self.rescan)
        self.conn_btn.clicked.connect(self.edit_connection)
        self.connect_btn.clicked.connect(self._on_connect)
        self.disconnect_btn.clicked.connect(self._on_disconnect)

        self.reload_devices()
        self._update_controls()

    # ==================================================================
    # 设备
    # ==================================================================
    def reload_devices(self) -> None:
        current_key = self.current_spec_key()
        self.device_box.blockSignals(True)
        self.device_box.clear()
        specs = dev_mod.specs_for(self._kind)
        for spec in specs:
            self.device_box.addItem(spec.name, spec.key)
        if current_key:
            index = self.device_box.findData(current_key)
            if index >= 0:
                self.device_box.setCurrentIndex(index)
        self.device_box.blockSignals(False)
        self.log.emit(f"{len(specs)} device model(s) available")
        self._on_device_changed()

    def rescan(self) -> None:
        self.reload_devices()

    def current_resource(self) -> str:
        """当前连接参数里的资源名（local 就是 "Dev1" 这种）。

        DeviceSpec 上没有这个字段 —— 它是连接参数的一部分，存在
        _configs 里。界面需要它来推导默认通道名前缀，所以开个口子。
        """
        params = self._configs.get(self._conn_kind, {})
        return str(params.get("resource", "") or "")

    def current_spec_key(self) -> str:
        return self.device_box.currentData() or ""

    def current_spec(self):
        return dev_mod.find_spec(self._kind, self.current_spec_key())

    def _on_device_changed(self, *_args) -> None:
        spec = self.current_spec()
        if spec is None:
            return
        self.device_desc.setText(spec.description)

        # 当前连接方式不受这个型号支持时，换成它支持的第一个
        if self._conn_kind not in spec.transports and spec.transports:
            self._conn_kind = spec.transports[0]

        self._refresh_conn_button()
        self._update_controls()

    # ==================================================================
    # 连接设置
    # ==================================================================
    def _refresh_conn_button(self) -> None:
        """按钮文字显示当前配置摘要；不支持的型号给出提示。"""
        spec = self.current_spec()
        label = TRANSPORT_LABELS.get(self._conn_kind, self._conn_kind)
        params = self._configs.get(self._conn_kind, {})
        if self._conn_kind == "serial":
            detail = f"{params.get('port', '?')} @ {params.get('baudrate', '?')}"
        elif self._conn_kind == "lan":
            detail = f"{params.get('host', '?')}:{params.get('port', '?')}"
        elif self._conn_kind == "local":
            detail = str(params.get("resource", "?"))
        else:
            detail = str(params.get("address", "?"))

        supports = spec is not None and self._conn_kind in spec.transports
        suffix = "" if supports else "  (not supported by this model)"
        self.conn_btn.setText(f"Connection: {label} · {detail}{suffix}")

    def edit_connection(self) -> None:
        """打开连接设置对话框。"""
        spec = self.current_spec()
        if spec is None:
            return
        if self._device is not None:
            self.log.emit("Disconnect before changing the connection settings.")
            return

        dialog = ConnectionDialog(
            spec.transports,
            kind=self._conn_kind,
            configs=self._configs,
            device_name=spec.name,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        kind, params = dialog.result_config()
        self._conn_kind = kind
        self._configs[kind] = params
        self._save_configs()          # 记住这次的选择，下次打开就是这个
        self._refresh_conn_button()
        # 通知外部：资源名/端口变了，依赖它的默认值该跟着更新
        self.connection_changed.emit()
        self.log.emit("Connection set to " + dialog.summary())

    # ==================================================================
    # 配置持久化
    # ==================================================================
    #: 各类连接方式的出厂默认值（settings.ini 里没有时用它）
    DEFAULT_CONFIGS = {
        "serial": {"port": "COM6", "baudrate": 115200},
        "lan": {"host": "192.168.1.100", "port": 5025},
        "gpib": {"address": "GPIB0::12::INSTR"},
        "local": {"resource": "Dev1"},
    }

    def _load_configs(self) -> dict[str, dict]:
        """从 settings.ini 读这个设备的连接参数。

        每个设备（按 kind 分组）各自记住自己的串口，所以 Stage 用 COM7、
        滤光片轮用 COM4 互不影响。
        """
        configs = {k: dict(v) for k, v in self.DEFAULT_CONFIGS.items()}
        store = settings()
        store.beginGroup(self._kind)
        for transport, conf in configs.items():
            store.beginGroup(transport)
            for key in list(conf):
                if not store.contains(key):
                    continue
                raw = store.value(key)
                try:
                    conf[key] = type(conf[key])(raw)
                except (TypeError, ValueError):
                    pass          # 文件被手改坏了就退回默认值
            store.endGroup()
        store.endGroup()
        return configs

    def _save_configs(self) -> None:
        """把当前连接参数写回 settings.ini。"""
        store = settings()
        store.beginGroup(self._kind)
        for transport, conf in self._configs.items():
            store.beginGroup(transport)
            for key, value in conf.items():
                store.setValue(key, value)
            store.endGroup()
        store.endGroup()
        store.sync()

    # ==================================================================
    # 连接 / 断开
    # ==================================================================
    def set_locked(self, locked: bool) -> None:
        """锁住连接类控件。

        页面在执行耗时操作（例如转位）时调用，避免用户在半路 Disconnect
        再重连 —— 那样旧操作的完成回调会写到新连接的界面上。
        """
        self._locked = bool(locked)
        self._update_controls()

    def _update_controls(self) -> None:
        has_device = self._device is not None
        # 自身忙（连接/断开中）或外部忙（页面在执行设备操作）都算不空闲
        idle = not self._busy and not self._locked
        spec = self.current_spec()
        supported = spec is not None and self._conn_kind in spec.transports

        self.connect_btn.setEnabled(
            not has_device and idle and spec is not None and supported)
        self.disconnect_btn.setEnabled(has_device and idle)
        for w in (self.device_box, self.rescan_btn, self.conn_btn):
            w.setEnabled(idle)

        if has_device:
            self.status_label.setText("● Connected")
            self.status_label.setStyleSheet("color:#2e7d32; font-weight:600;")
        else:
            self.status_label.setText("● Disconnected")
            self.status_label.setStyleSheet("color:#9aa3ae;")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        if message:
            self.log.emit(message)
        self._update_controls()

    def _on_connect(self) -> None:
        if self._busy or self._device is not None:
            return
        spec = self.current_spec()
        if spec is None:
            self.log.emit("No device model selected.")
            return
        if self._conn_kind not in spec.transports:
            self.log.emit(f"{spec.name} does not support "
                          f"{TRANSPORT_LABELS.get(self._conn_kind)}.")
            return
        if self._conn_kind == "gpib" and not tr_mod.visa_available():
            self.failed.emit("GPIB needs pyvisa: pip install pyvisa pyvisa-py")
            return

        params = dict(self._configs.get(self._conn_kind, {}))
        options = dict(self._device_options() or {})
        label = (f"{spec.name} via "
                 f"{TRANSPORT_LABELS.get(self._conn_kind, self._conn_kind)}")
        self._set_busy(True, f"Connecting to {label}…")

        self._connect_request += 1
        request = self._connect_request

        def work():
            transport = tr_mod.make_transport(self._conn_kind, **params)
            device = spec.factory(transport, **options)
            connector = getattr(device, "connect", None)
            if callable(connector):
                connector()
            return device

        def done(device) -> None:
            if request != self._connect_request:
                # 连接期间页面已经关了 / 断开了。设备是**真的连上了**，
                # 不能只丢回调 —— 那会把串口和输出留在后台。
                self._release_device(device)
                # 这里**不能**用 self.log.emit：selector 可能已经被
                # deleteLater 销毁，碰 Qt 信号会抛
                # RuntimeError: Signal source has been deleted
                log.info("Connect finished after the session ended - "
                         "the device was released.")
                return
            self._device = device
            self._set_busy(False, "Connected.")
            self._update_controls()
            self.connected.emit(device)

        def failed(msg: str) -> None:
            if request != self._connect_request:
                return                      # 会话已结束，不再打扰用户
            self._set_busy(False, f"Connect failed: {msg}")
            self._update_controls()
            self.failed.emit(msg)

        run_async(work, done, failed)

    def _on_disconnect(self) -> None:
        if self._device is None:
            return
        device = self._device

        def work():
            closer = getattr(device, "close", None)
            if callable(closer):
                closer()
            return None

        def done(_) -> None:
            self._device = None
            self._set_busy(False, "Disconnected.")
            self._update_controls()
            self.disconnected.emit()

        def failed(msg: str) -> None:
            self._set_busy(False, f"Disconnect error: {msg}")

        self._set_busy(True, "Disconnecting…")
        run_async(work, done, failed)

    # ==================================================================
    @property
    def device(self):
        return self._device

    @staticmethod
    def _release_device(device) -> None:
        """把一台设备按 halt → stop → close 收掉。

        三个方法都幂等，重复调用无害；单个失败不影响其它步骤。
        """
        if device is None:
            return
        for name in ("halt", "stop", "close"):
            fn = getattr(device, name, None)
            if callable(fn):
                try:
                    fn()
                except Exception:                    # noqa: BLE001
                    pass

    def force_disconnect(self) -> None:
        """页面/窗口关闭时兜底：先停住输出，再释放底层设备。

        **即使当前还没有设备也要作废在途连接** —— Connect 可能正卡在
        驱动里，等它回来时页面已经关了；那时它仍会建好设备并 emit
        connected，于是「关掉的窗口又绑上了设备」。已完成的连接由
        done 里的请求校验负责释放。
        """
        self._connect_request += 1
        device, self._device = self._device, None
        self._release_device(device)
