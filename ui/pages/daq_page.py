"""Data Acquisition：NI-DAQ 模拟 DC 电压输出。

源起是一个控制 PCI-6229 + BNC-2120 输出 DC 电压（驱动激光功率）的小工具。
这里保留它最关键的部分 —— **安全确认** —— 并把它做成可扩展的基础：

  * 设备从下拉选（NI PCI-6229 / Placeholder），连接方式走统一的
    「Connection 按钮 + 对话框」
  * 电压范围 0–5 V，超过 1 V 时必须确认防护
  * 确认一次后不再重复弹窗，但电压降到阈值以下会**重新武装**
    （下次再升高还要再确认一次）
  * 所有 DAQ 调用在后台线程，界面不冻结
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import DeviceSelector
from ui.workers import run_async


class DaqPage(QWidget):
    page_title = "Data Acquisition"

    log_line = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._busy = False
        self._applying = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        title = QLabel("Data Acquisition")
        title.setObjectName("pageTitle")
        outer.addWidget(title)
        subtitle = QLabel(
            "Analog DC voltage output (NI PCI-6229 / BNC-2120). "
            "Above the safety threshold you must confirm eye protection first.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        body = QHBoxLayout()
        body.setSpacing(14)

        # ------------------------------------------------ 左：控制
        left = QVBoxLayout()
        left.setSpacing(8)

        self.selector = DeviceSelector(
            kind="daq",
            device_options=self._device_options,
            hint="Choose 'Placeholder device' to try the output flow without "
                 "hardware.",
        )
        left.addWidget(self.selector)

        # ---- 安全横幅（平时隐藏）----
        self.warn_banner = QFrame()
        self.warn_banner.setObjectName("dangerBanner")
        banner_layout = QHBoxLayout(self.warn_banner)
        banner_layout.setContentsMargins(12, 8, 12, 8)
        banner_layout.setSpacing(8)
        self.warn_icon = QLabel("⚠")
        self.warn_icon.setObjectName("dangerBannerIcon")
        banner_layout.addWidget(self.warn_icon)
        self.warn_text = QLabel("")
        self.warn_text.setObjectName("dangerBannerText")
        self.warn_text.setWordWrap(True)
        banner_layout.addWidget(self.warn_text, 1)
        self.warn_banner.setVisible(False)
        left.addWidget(self.warn_banner)

        # ---- 输出电压 ----
        out = QFrame()
        out.setObjectName("formBox")
        grid = QGridLayout(out)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setSpacing(8)

        head = QLabel("Analog output")
        head.setObjectName("fieldLabel")
        grid.addWidget(head, 0, 0, 1, 4)

        grid.addWidget(QLabel("Channel:"), 1, 0)
        self.channel_edit = QLineEdit("Dev1/ao2")
        self.channel_edit.setMinimumWidth(160)
        grid.addWidget(self.channel_edit, 1, 1, 1, 2)
        self.range_label = QLabel("0 – 5 V")
        self.range_label.setObjectName("pageSubtitle")
        grid.addWidget(self.range_label, 1, 3)

        grid.addWidget(QLabel("Voltage:"), 2, 0)
        self.volt_spin = QDoubleSpinBox()
        self.volt_spin.setRange(0.0, 5.0)
        self.volt_spin.setDecimals(3)
        self.volt_spin.setSingleStep(0.01)
        self.volt_spin.setSuffix(" V")
        self.volt_spin.setFixedWidth(120)
        grid.addWidget(self.volt_spin, 2, 1)

        self.volt_slider = QSlider(Qt.Orientation.Horizontal)
        self.volt_slider.setRange(0, 500)        # 0.01 V steps
        self.volt_slider.setValue(0)
        grid.addWidget(self.volt_slider, 2, 2, 1, 2)

        self.live_cb = QCheckBox("Live update while tuning")
        grid.addWidget(self.live_cb, 3, 0, 1, 4)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.apply_btn = QPushButton("Apply output")
        self.apply_btn.setObjectName("primary")
        self.stop_btn = QPushButton("Stop (0 V)")
        self.stop_btn.setObjectName("danger")
        btn_row.addWidget(self.apply_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch(1)
        btn_wrap = QWidget()
        btn_wrap.setLayout(btn_row)
        btn_row.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(btn_wrap, 4, 0, 1, 4)

        self.status_label = QLabel("Idle (not connected)")
        self.status_label.setObjectName("pageSubtitle")
        self.status_label.setWordWrap(True)
        grid.addWidget(self.status_label, 5, 0, 1, 4)
        left.addWidget(out)

        left.addStretch(1)
        body.addLayout(left, 0)

        # ------------------------------------------------ 右：日志
        right = QVBoxLayout()
        right.setSpacing(6)
        log_head = QHBoxLayout()
        lab = QLabel("Log")
        lab.setObjectName("fieldLabel")
        log_head.addWidget(lab)
        log_head.addStretch(1)
        self.clear_log_btn = QPushButton("Clear")
        self.clear_log_btn.setObjectName("ghost")
        self.clear_log_btn.setFixedWidth(70)
        log_head.addWidget(self.clear_log_btn)
        right.addLayout(log_head)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("numericBox")
        right.addWidget(self.log_view, 1)

        body.addLayout(right, 1)
        outer.addLayout(body, 1)

        # ------------------------------------------------ 信号
        self.log_line.connect(self._append_log)
        self.selector.log.connect(self._append_log)
        self.selector.connected.connect(self._on_connected)
        self.selector.disconnected.connect(self._on_disconnected)
        self.selector.failed.connect(
            lambda msg: QMessageBox.critical(self, "Connect failed", msg))

        self.volt_spin.valueChanged.connect(self._on_spin_changed)
        self.volt_slider.valueChanged.connect(self._on_slider_changed)
        self.live_cb.toggled.connect(self._on_live_toggled)
        self.apply_btn.clicked.connect(self.apply_output)
        self.stop_btn.clicked.connect(self.stop_output)
        self.clear_log_btn.clicked.connect(self.log_view.clear)

        self._update_controls()

    # ------------------------------------------------------------------ 工具
    @property
    def device(self):
        return self.selector.device

    def _device_options(self) -> dict:
        return {"log_callback": self.log_line.emit,
                "channel": self.channel_edit.text().strip() or None}

    def _append_log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {text}")

    # ------------------------------------------------------------------ 连接
    def _on_connected(self, device) -> None:
        # 用设备的默认通道覆盖输入框（设备的 resource 决定前缀）
        default_channel = getattr(device, "channel", None)
        if default_channel:
            self.channel_edit.setText(default_channel)
        self._update_controls()
        self._update_status()
        self._update_banner()

    def _on_disconnected(self) -> None:
        self._update_controls()
        self._update_status()
        self._update_banner()

    # ------------------------------------------------------------------ 电压
    def _on_slider_changed(self, raw: int) -> None:
        self.volt_spin.blockSignals(True)
        self.volt_spin.setValue(raw / 100.0)
        self.volt_spin.blockSignals(False)
        self._after_voltage_change()

    def _on_spin_changed(self, value: float) -> None:
        self.volt_slider.blockSignals(True)
        self.volt_slider.setValue(int(round(value * 100)))
        self.volt_slider.blockSignals(False)
        self._after_voltage_change()

    def _after_voltage_change(self) -> None:
        self._update_banner()
        if self.live_cb.isChecked():
            self.apply_output()

    def _on_live_toggled(self, on: bool) -> None:
        self._append_log("Live update " + ("on" if on else "off"))
        if on:
            self.apply_output()

    def _update_banner(self) -> None:
        """红色横幅：输出状态未知时最优先，其次是超过安全阈值。"""
        dev = self.device

        # 归零失败过 -> 端子可能还带电，这比阈值提示更要紧
        if dev is not None and getattr(dev, "last_applied", 0.0) is None:
            self.warn_text.setText(
                "⚠  OUTPUT STATE UNKNOWN — the last zeroing failed, so the "
                "channel may still be live. Check the hardware (or power down "
                "the laser) before touching anything.")
            self.warn_banner.setVisible(True)
            return

        threshold = getattr(dev, "WARN_THRESHOLD", 1.0) if dev else 1.0
        volts = self.volt_spin.value()
        if volts >= threshold:
            self.warn_text.setText(
                f"{volts:.3f} V is at or above the {threshold:.1f} V safety "
                "threshold — laser output can exceed 10 mW. Wear proper eye "
                "protection before applying.")
            self.warn_banner.setVisible(True)
        else:
            self.warn_banner.setVisible(False)

    def _update_status(self) -> None:
        dev = self.device
        if dev is None:
            self.status_label.setText("Idle (not connected)")
            return
        status_fn = getattr(dev, "status_text", None)
        if callable(status_fn):
            try:
                self.status_label.setText(status_fn())
                return
            except Exception:                        # noqa: BLE001
                pass
        self.status_label.setText("Connected")

    # ------------------------------------------------------------------ 安全
    def _confirm_laser_safety(self, volts: float) -> bool:
        dev = self.device
        warn_text = getattr(dev, "WARN_TEXT", "")
        question = getattr(dev, "PROTECTION_QUESTION",
                           "Did you wear proper protection?")
        threshold = getattr(dev, "WARN_THRESHOLD", 1.0)
        text = (f"Outputting {volts:.3f} V.\n\n"
                f"{warn_text}\n\n"
                f"Anything at or above {threshold:.1f} V can drive the laser "
                f"beyond 10 mW.\n\n"
                f"{question}")
        result = QMessageBox.warning(
            self, "LASER SAFETY",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return result == QMessageBox.StandardButton.Yes

    # ------------------------------------------------------------------ 输出
    def apply_output(self) -> None:
        if self._busy or self._applying:
            self._append_log("Busy — wait for the current operation to finish.")
            return
        dev = self.device
        if dev is None:
            QMessageBox.information(self, "Not connected",
                                    "Connect to the DAQ device first.")
            return

        volts = self.volt_spin.value()
        try:
            volts = dev.clamp(volts)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid voltage", str(exc))
            return

        # ---- 安全确认：首次超过阈值必须确认 ----
        needs = dev.needs_warning(volts) if hasattr(dev, "needs_warning") else False
        if needs:
            if not self._confirm_laser_safety(volts):
                self._append_log(f"Output of {volts:.3f} V cancelled by the user.")
                self._restore_last_value()
                return
            dev.acknowledge_warning()
            self._append_log("Safety acknowledgement recorded for this session.")
        if hasattr(dev, "reset_warning"):
            dev.reset_warning(volts)

        channel = self.channel_edit.text().strip() or getattr(dev, "channel", "")
        self._applying = True
        self._set_busy(True, f"Setting {volts:.3f} V on {channel}…")

        def work():
            writer = getattr(dev, "write_voltage", None)
            if callable(writer):
                return writer(volts)
            raise RuntimeError("device has no write_voltage()")

        def done(applied) -> None:
            self._applying = False
            self._sync_voltage(float(applied))
            self._set_busy(False, f"Output set to {float(applied):.3f} V.")
            self._update_status()

        def failed(msg: str) -> None:
            self._applying = False
            self._set_busy(False, f"Output failed: {msg}")
            QMessageBox.critical(self, "Output failed", msg)
            self._update_status()

        run_async(work, done, failed)

    def _restore_last_value(self) -> None:
        """用户拒绝安全确认时，把滑块退回上一次**确认过**的输出值。

        如果上一次归零失败，last_applied 是 None（输出状态未知），这时
        没有可信的数值可以回退 —— 保持当前显示，并明确提示未知，
        而不是 float(None) 崩掉、也不是假装回到某个电压。
        """
        dev = self.device
        last = getattr(dev, "last_applied", 0.0) if dev is not None else 0.0
        if last is None:
            self._append_log(
                "Output state is UNKNOWN (an earlier zeroing failed); "
                "the slider was left as-is. Check the hardware.")
            self._update_banner()
            self._update_status()
            return
        self._sync_voltage(float(last))

    def _sync_voltage(self, volts: float) -> None:
        self.volt_spin.blockSignals(True)
        self.volt_slider.blockSignals(True)
        self.volt_spin.setValue(volts)
        self.volt_slider.setValue(int(round(volts * 100)))
        self.volt_spin.blockSignals(False)
        self.volt_slider.blockSignals(False)
        self._update_banner()

    def stop_output(self) -> None:
        dev = self.device
        if dev is None:
            return
        if self._busy:
            self._append_log("Busy — wait for the current operation to finish.")
            return

        def work():
            stopper = getattr(dev, "stop", None)
            if callable(stopper):
                return stopper()
            return None

        def done(result) -> None:
            # stop() 返回 StopResult；老接口可能返回 None，按成功处理
            zeroed = getattr(result, "zeroed", True)
            if zeroed:
                self._sync_voltage(0.0)
                self._set_busy(False, "Stopped: 0 V confirmed, task released.")
            else:
                error = getattr(result, "error", None) or "unknown error"
                # 不要在这里把滑块归零：那会让横幅按「电压低于阈值」隐藏，
                # 而真实状态恰恰是未知。保留原值，只更新提示。
                self._set_busy(
                    False,
                    "⚠ Zeroing FAILED — output state UNKNOWN "
                    f"({error}). The channel may still be live.")
                QMessageBox.critical(
                    self, "Zeroing failed",
                    "Could not write 0 V to the DAQ channel.\n\n"
                    f"{error}\n\n"
                    "The output may still be live. Check the hardware (or power "
                    "down the laser) before continuing.")
            self._update_banner()
            self._update_status()

        def failed(msg: str) -> None:
            self._set_busy(False, f"Stop failed: {msg}")

        self._set_busy(True, "Stopping (0 V)…")
        run_async(work, done, failed)

    # ------------------------------------------------------------------ 状态
    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        if message:
            self._append_log(message)
        self._update_controls()

    def _update_controls(self) -> None:
        connected = self.device is not None
        idle = not self._busy
        self.apply_btn.setEnabled(connected and idle)
        self.stop_btn.setEnabled(connected)
        self.volt_spin.setEnabled(connected and idle)
        self.volt_slider.setEnabled(connected and idle)
        self.live_cb.setEnabled(connected and idle)
        # 连接后通道名不再允许修改（与源程序一致）
        self.channel_edit.setEnabled(not connected and idle)

    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """释放设备：先归零（force_disconnect 会先 halt 再 close），再断开。

        主窗口关闭时不会触发子页面的 closeEvent，所以由 MainWindow 统一
        调用这个方法（见 ui/main_window.py 的 closeEvent）。
        """
        try:
            self.selector.force_disconnect()
        except Exception:                            # noqa: BLE001
            pass

    def closeEvent(self, event) -> None:      # noqa: N802
        """关闭页面时先归零再断开，避免留下非零输出。"""
        self.cleanup()
        super().closeEvent(event)
