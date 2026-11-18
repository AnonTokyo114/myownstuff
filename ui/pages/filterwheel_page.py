"""Filter Wheel：Thorlabs FW102C 电动滤光片轮。

结构与其它设备页一致：走 ``DeviceSelector`` 的「选设备 → 配连接 → Connect」，
连上之后才出现操作面板。

两点需要注意：
  * ``pos=N`` 会阻塞到转位完成（最长 10 秒），必须放后台线程
  * 转位会改变光路 —— 如果激光开着，某个位置可能是空的，光会直射出去
"""

from __future__ import annotations

import time

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import DeviceSelector
from ui.workers import run_async

#: 设置项的选项表：(显示文字, 送给设备的值)
TRIG_OPTIONS = [("Output — pulse when the move finishes", 1),
                ("Input — external pulse advances the wheel", 0)]
SPEED_OPTIONS = [("Fast", 1), ("Slow", 0)]
SENSOR_OPTIONS = [("Off when the wheel is idle (less stray light)", 0),
                  ("Always on", 1)]


class FilterWheelPage(QWidget):
    page_title = "Filter Wheel"

    log_line = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._busy = False
        #: 最近一次从设备读回的状态
        self._state: dict = {}
        #: 避免连接后自动刷新与用户操作打架
        self._loading = False
        #: 连接会话号。每次连接/断开都 +1；后台回调对不上号就丢弃，
        #: 避免旧设备的完成结果写到新连接的界面上。
        self._session = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        title = QLabel("Filter Wheel")
        title.setObjectName("pageTitle")
        outer.addWidget(title)
        subtitle = QLabel(
            "Thorlabs FW102C motorized filter wheel (6 or 12 positions). "
            "Changing filters can redirect the beam — make sure the laser is "
            "off or blocked before you move the wheel.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        body = QHBoxLayout()
        body.setSpacing(14)

        # ------------------------------------------------ 左：控制
        left = QVBoxLayout()
        left.setSpacing(8)

        self.selector = DeviceSelector(
            kind="filterwheel",
            device_options=self._device_options,
            hint="Choose 'Placeholder device' to try the move flow without "
                 "hardware.",
        )
        left.addWidget(self.selector)

        # ---- 当前位置 ----
        wheel = QFrame()
        wheel.setObjectName("formBox")
        grid = QGridLayout(wheel)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setSpacing(8)

        head = QLabel("Wheel")
        head.setObjectName("fieldLabel")
        grid.addWidget(head, 0, 0, 1, 4)

        grid.addWidget(QLabel("Identity:"), 1, 0)
        self.identity_label = QLabel("—")
        self.identity_label.setObjectName("pageSubtitle")
        self.identity_label.setWordWrap(True)
        grid.addWidget(self.identity_label, 1, 1, 1, 3)

        grid.addWidget(QLabel("Slots:"), 2, 0)
        # 这个既是「查看」也是「设置」：连接后自动显示设备上报的位数，
        # 改了再点 Set 就写回控制器（pcount=N）。
        self.slots_box = QComboBox()
        self.slots_box.addItem("6", 6)
        self.slots_box.addItem("12", 12)
        self.slots_box.setFixedWidth(80)
        grid.addWidget(self.slots_box, 2, 1)

        self.set_slots_btn = QPushButton("Set")
        self.set_slots_btn.setObjectName("ghost")
        self.set_slots_btn.setFixedWidth(60)
        self.set_slots_btn.setToolTip(
            "Tell the controller which wheel is installed (must match the hardware)")
        grid.addWidget(self.set_slots_btn, 2, 2)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("ghost")
        grid.addWidget(self.refresh_btn, 2, 3)

        grid.addWidget(QLabel("Current:"), 3, 0)
        self.position_value = QLabel("—")
        self.position_value.setObjectName("monoBig")
        grid.addWidget(self.position_value, 3, 1)

        grid.addWidget(QLabel("Move to:"), 4, 0)
        self.pos_box = QComboBox()
        self.pos_box.setFixedWidth(90)
        grid.addWidget(self.pos_box, 4, 1)

        self.move_btn = QPushButton("Move")
        self.move_btn.setObjectName("primary")
        grid.addWidget(self.move_btn, 4, 2, 1, 2)
        left.addWidget(wheel)

        # ---- 设置 ----
        settings = QFrame()
        settings.setObjectName("formBox")
        sgrid = QGridLayout(settings)
        sgrid.setContentsMargins(12, 10, 12, 10)
        sgrid.setSpacing(8)

        shead = QLabel("Settings")
        shead.setObjectName("fieldLabel")
        sgrid.addWidget(shead, 0, 0, 1, 3)

        sgrid.addWidget(QLabel("Trigger:"), 1, 0)
        self.trig_box = QComboBox()
        for label, value in TRIG_OPTIONS:
            self.trig_box.addItem(label, value)
        sgrid.addWidget(self.trig_box, 1, 1, 1, 2)

        sgrid.addWidget(QLabel("Speed:"), 2, 0)
        self.speed_box = QComboBox()
        for label, value in SPEED_OPTIONS:
            self.speed_box.addItem(label, value)
        sgrid.addWidget(self.speed_box, 2, 1, 1, 2)

        sgrid.addWidget(QLabel("Sensors:"), 3, 0)
        self.sensor_box = QComboBox()
        for label, value in SENSOR_OPTIONS:
            self.sensor_box.addItem(label, value)
        sgrid.addWidget(self.sensor_box, 3, 1, 1, 2)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.apply_btn = QPushButton("Apply settings")
        self.apply_btn.setObjectName("primary")
        self.save_btn = QPushButton("Save as power-up defaults")
        self.save_btn.setObjectName("ghost")
        btn_row.addWidget(self.apply_btn)
        btn_row.addWidget(self.save_btn)
        btn_row.addStretch(1)
        btn_wrap = QWidget()
        btn_wrap.setLayout(btn_row)
        btn_row.setContentsMargins(0, 0, 0, 0)
        sgrid.addWidget(btn_wrap, 4, 0, 1, 3)
        left.addWidget(settings)

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

        self.refresh_btn.clicked.connect(self.refresh_state)
        self.set_slots_btn.clicked.connect(self.set_slots)
        self.move_btn.clicked.connect(self.move_to)
        self.apply_btn.clicked.connect(self.apply_settings)
        self.save_btn.clicked.connect(self.save_defaults)
        self.clear_log_btn.clicked.connect(self.log_view.clear)

        self._update_controls()

    # ------------------------------------------------------------------ 工具
    @property
    def device(self):
        return self.selector.device

    def _device_options(self) -> dict:
        return {"log_callback": self.log_line.emit}

    def _append_log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {text}")

    # ------------------------------------------------------------------ 连接
    def _on_connected(self, device) -> None:
        self._session += 1
        ident = getattr(device, "identity", "")
        if ident:
            self.identity_label.setText(ident)
        self._update_controls()
        self.refresh_state()

    def _on_disconnected(self) -> None:
        self._session += 1
        # 断开之后就不存在「进行中的操作」了：清掉忙状态，
        # 否则新连接的首次状态刷新会被旧的 _busy 跳过。
        self._busy = False
        self.selector.set_locked(False)
        self._state = {}
        self.identity_label.setText("—")
        self.position_value.setText("—")
        self.pos_box.clear()
        self._update_controls()

    # ------------------------------------------------------------------ 状态
    def refresh_state(self) -> None:
        dev = self.device
        if dev is None or self._busy:
            return

        session = self._session

        def work():
            return dev.snapshot()

        def done(state) -> None:
            if session != self._session:
                self._append_log(
                    "State read finished after the connection changed — "
                    "result ignored.")
                return
            self._apply_state(state)
            self._set_busy(False)

        def failed(msg: str) -> None:
            if session != self._session:
                self._append_log(f"State read failed after switching: {msg}")
                return
            self._set_busy(False, f"Read state failed: {msg}")

        self._set_busy(True, "Reading wheel state…")
        run_async(work, done, failed)

    def _apply_state(self, state: dict) -> None:
        """把从设备读回的状态铺到界面上（Refresh 和 Set slots 共用）。"""
        self._loading = True
        try:
            self._state = dict(state)
            pcount = int(state.get("pcount", 6))
            position = int(state.get("position", 1))

            self._select(self.slots_box, pcount)
            self.position_value.setText(str(position))
            self._rebuild_positions(pcount, position)

            self._select(self.trig_box, state.get("trig"))
            self._select(self.speed_box, state.get("speed"))
            self._select(self.sensor_box, state.get("sensors"))
        finally:
            self._loading = False

    def set_slots(self) -> None:
        """把界面选的位数写回控制器（pcount=N）。

        这个值**必须**和实际装的轮子一致：设小了会转不到位，设大了会
        试图转到不存在的槽位。所以改之前一定要确认。
        """
        dev = self.device
        if dev is None or self._busy:
            return

        target = self.slots_box.currentData()
        if target is None:
            return
        target = int(target)
        current = int(self._state.get("pcount", 0) or 0)

        if current and target == current:
            self._append_log(f"Wheel is already configured for {target} slots.")
            return

        detail = (f"The controller is currently set to {current} slots.\n\n"
                  if current else "")
        ok = QMessageBox.question(
            self, "Change wheel type",
            detail + f"Set it to {target} slots?\n\n"
            "This must match the wheel actually installed. If it does not, "
            "the wheel may try to move to a slot that does not exist.")
        if ok != QMessageBox.StandardButton.Yes:
            self._append_log("Wheel type change cancelled.")
            self._select(self.slots_box, current)      # 下拉复位
            return

        session = self._session

        def work():
            dev.set_wheel_type(target)
            return dev.snapshot()

        def done(state) -> None:
            if session != self._session:
                self._append_log(
                    "Wheel type change finished after the connection changed "
                    "— result ignored.")
                return
            self._apply_state(state)

            # 「命令发出去了」不等于「设备采纳了」：必须用回读值确认。
            try:
                actual = int(state.get("pcount", target))
            except (TypeError, ValueError):
                actual = target
            if actual == target:
                self._set_busy(False, f"Wheel type set to {target} slots.")
            else:
                self._set_busy(
                    False,
                    f"⚠ Requested {target} slots but the controller "
                    f"reports {actual} — the setting was not applied.")
                QMessageBox.warning(
                    self, "Wheel type not applied",
                    f"Requested {target} slots, but reading it back gives "
                    f"{actual}.\n\nThe controller did not accept the "
                    "setting. Check which wheel is actually installed.")

        def failed(msg: str) -> None:
            if session != self._session:
                self._append_log(
                    f"Wheel type change failed after switching: {msg}")
                return

            # set_wheel_type() 可能**已经成功**，只是后面的 snapshot 读失败。
            # 这时设备到底是什么状态是未知的，必须把缓存里的 pcount 丢掉：
            # 留着旧值 -> 下次改回原值会被 "already configured" 跳过，
            # 而设备的实际值已经不是它了（审查 #1）。
            self._state.pop("pcount", None)
            self._set_busy(
                False,
                "Set wheel type may have taken effect, but reading the "
                f"state failed: {msg}. Press Refresh to see the current "
                "slot count.")
            QMessageBox.warning(
                self, "Wheel type state unknown",
                "The command was sent, but reading the wheel state failed, "
                "so it is not known whether the change took effect.\n\n"
                f"{msg}\n\n"
                "Press Refresh to read the current slot count.")

        self._set_busy(True, f"Setting wheel type to {target} slots…")
        run_async(work, done, failed)

    def _rebuild_positions(self, pcount: int, current: int) -> None:
        if (self.pos_box.count() == pcount
                and self.pos_box.currentIndex() + 1 == current):
            return
        self.pos_box.blockSignals(True)
        self.pos_box.clear()
        for n in range(1, pcount + 1):
            self.pos_box.addItem(str(n), n)
        index = max(0, min(current - 1, pcount - 1))
        self.pos_box.setCurrentIndex(index)
        self.pos_box.blockSignals(False)

    @staticmethod
    def _select(box: QComboBox, value) -> None:
        if value is None:
            return
        index = box.findData(int(value))
        if index >= 0:
            box.blockSignals(True)
            box.setCurrentIndex(index)
            box.blockSignals(False)

    # ------------------------------------------------------------------ 动作
    def move_to(self) -> None:
        dev = self.device
        if dev is None or self._busy:
            return

        target = self.pos_box.currentData()
        if target is None:
            return
        target = int(target)
        session = self._session

        def work():
            return dev.set_pos(target)

        def done(actual) -> None:
            if session != self._session:
                self._append_log(
                    f"Move to {target} finished after the connection changed "
                    "— result ignored.")
                return
            self.position_value.setText(str(actual))
            self._set_busy(False, f"Moved to position {actual}.")

        def failed(msg: str) -> None:
            if session != self._session:
                self._append_log(
                    f"Move to {target} failed after the connection changed: "
                    f"{msg}")
                return
            self._set_busy(False, f"Move failed: {msg}")
            QMessageBox.critical(self, "Move failed", msg)

        self._set_busy(True, f"Turning to position {target}… "
                             "(the wheel reports back when done)")
        run_async(work, done, failed)

    def apply_settings(self) -> None:
        dev = self.device
        if dev is None or self._busy:
            return

        trig = int(self.trig_box.currentData())
        speed = int(self.speed_box.currentData())
        sensors = int(self.sensor_box.currentData())

        session = self._session

        def work():
            dev.set_trig_mode(trig)
            dev.set_speed_mode(speed)
            dev.set_sensor_mode(sensors)
            return trig, speed, sensors

        def done(_result) -> None:
            if session != self._session:
                self._append_log(
                    "Settings applied after the connection changed — "
                    "result ignored.")
                return
            self._set_busy(
                False,
                f"Settings applied: trigger={trig}, speed={speed}, "
                f"sensors={sensors}.")

        def failed(msg: str) -> None:
            if session != self._session:
                self._append_log(
                    f"Apply settings failed after switching: {msg}")
                return
            self._set_busy(False, f"Apply settings failed: {msg}")
            QMessageBox.critical(self, "Apply settings failed", msg)

        self._set_busy(True, "Applying settings…")
        run_async(work, done, failed)

    def save_defaults(self) -> None:
        dev = self.device
        if dev is None or self._busy:
            return

        ok = QMessageBox.question(
            self, "Save as power-up defaults",
            "Store the current settings as the values the wheel uses when it "
            "is powered on?")
        if ok != QMessageBox.StandardButton.Yes:
            return

        session = self._session

        def work():
            dev.save_settings()

        def done(_result) -> None:
            if session != self._session:
                self._append_log(
                    "Save finished after the connection changed — ignored.")
                return
            self._set_busy(False, "Saved as power-up defaults.")

        def failed(msg: str) -> None:
            if session != self._session:
                self._append_log(f"Save failed after switching: {msg}")
                return
            self._set_busy(False, f"Save failed: {msg}")
            QMessageBox.critical(self, "Save failed", msg)

        self._set_busy(True, "Saving settings…")
        run_async(work, done, failed)

    # ------------------------------------------------------------------ 状态
    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        # 忙的时候锁住连接控件：中途 Disconnect 再重连，旧操作的完成
        # 回调会把结果写到新连接的界面上（审查 #1）。
        self.selector.set_locked(busy)
        if message:
            self._append_log(message)
        self._update_controls()

    def _update_controls(self) -> None:
        connected = self.device is not None
        idle = not self._busy
        self.refresh_btn.setEnabled(connected and idle)
        self.move_btn.setEnabled(connected and idle and self.pos_box.count() > 0)
        self.apply_btn.setEnabled(connected and idle)
        self.save_btn.setEnabled(connected and idle)
        self.set_slots_btn.setEnabled(connected and idle)
        for box in (self.slots_box, self.pos_box, self.trig_box,
                    self.speed_box, self.sensor_box):
            box.setEnabled(connected and idle)

    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """主窗口关闭 / 独立窗口关闭时调用。"""
        try:
            self.selector.force_disconnect()
        except Exception:                            # noqa: BLE001
            pass

    def closeEvent(self, event) -> None:      # noqa: N802
        self.cleanup()
        super().closeEvent(event)
