"""Stage Controller：二维位移台（含 ASI LX-4000 与占位设备）。

设备选择遵循 Device Interface 的统一约定：
  下拉选**设备型号** → 下拉选**连接方式**（Serial / LAN / GPIB）→ Connect

本页只关心连上之后的运动控制；连接编排与找设备都在 DeviceSelector 里。
所有串口/网口操作都是阻塞的，一律丢到后台线程。
安全相关的两个设计：
  * HALT 不受忙碌状态限制（急停必须随时能按）
  * Center and zero 会驱动到负限位、可能撞镜头，必须二次确认
"""

from __future__ import annotations

import sys
import time

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import DeviceSelector, LogPane
from ui.workers import DeviceTaskSession, run_async


class CenterZeroDialog(QDialog):
    """Center and zero 的二次确认。

    刻意用**下拉菜单**而不是单纯的 Yes/No 按钮：默认停在 No，
    必须主动切换成 Yes 才会执行。这个操作会把台子开到负限位，
    多一道需要"主动选择"的关卡，比顺手回车安全得多。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Center and zero")
        self.setMinimumWidth(540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        head = QLabel("⚠  This drives the stage to the negative hardware limits.")
        # 对话框不继承主窗口的 QSS，这里用内联样式保证警告足够醒目
        head.setStyleSheet(
            "color:#b3261e; font-weight:600; font-size:13px;")
        head.setWordWrap(True)
        layout.addWidget(head)

        body = QLabel(
            "What it does:\n"
            "   1. move X and Y to the negative hardware limits\n"
            "   2. define that position as the temporary origin\n"
            "   3. move to the measured centre (X=451906, Y=430490)\n"
            "   4. define the centre as the new origin\n"
            "\n"
            "Moving to the limit may damage the sample, and the stage may hit "
            "objectives or the holder on the way. Make sure the sample and every "
            "objective are clear of the full travel range before you proceed.")
        body.setWordWrap(True)
        layout.addWidget(body)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Proceed:"))
        self.choice = QComboBox()
        self.choice.addItem("No — do not move the stage", False)
        self.choice.addItem("Yes — drive to the limits", True)
        self.choice.setCurrentIndex(0)          # 默认 No
        self.choice.setMinimumWidth(300)
        row.addWidget(self.choice, 1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def confirmed(self) -> bool:
        """下拉框选的是 Yes 才返回 True。"""
        return bool(self.choice.currentData())


class StagePage(QWidget):
    page_title = "Stage Controller"

    #: 工作线程 -> UI 线程的日志通道（Qt 会自动排队）
    log_line = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._busy = False
        #: 连接/断开/关窗时递增，作废在途的位置读取
        self._session = 0
        self._tasks = DeviceTaskSession(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        title = QLabel("Stage Controller")
        title.setObjectName("pageTitle")
        outer.addWidget(title)
        subtitle = QLabel("Two-axis stage. Pick the model, pick how it is "
                          "connected, then Connect. The position unit comes "
                          "from the device itself.")
        subtitle.setObjectName("pageSubtitle")
        outer.addWidget(subtitle)

        body = QHBoxLayout()
        body.setSpacing(14)

        # ------------------------------------------------ 左：控制
        left = QVBoxLayout()
        left.setSpacing(8)

        # ---- 设备选择（统一组件）----
        self.selector = DeviceSelector(
            kind="stage",
            device_options=self._device_options,
            hint="Both models can be tried without hardware: choose "
                 "'Placeholder device' and any connection type.",
        )
        self.verbose_box = QComboBox()
        self.verbose_box.addItems(["Log: normal", "Log: TX/RX"])
        self.verbose_box.setFixedWidth(118)
        left.addWidget(self.selector)

        # ---- 位置 ----
        pos = QFrame()
        pos.setObjectName("formBox")
        pos_grid = QGridLayout(pos)
        pos_grid.setContentsMargins(12, 10, 12, 10)
        pos_grid.setSpacing(6)

        # 单位由设备决定（连接后填），未连接时只写 "Position"
        self.pos_head = QLabel("Position")
        self.pos_head.setObjectName("fieldLabel")
        pos_grid.addWidget(self.pos_head, 0, 0, 1, 3)

        pos_grid.addWidget(QLabel("X:"), 1, 0)
        self.x_value = QLabel("—")
        self.x_value.setObjectName("monoBig")
        pos_grid.addWidget(self.x_value, 1, 1)

        pos_grid.addWidget(QLabel("Y:"), 2, 0)
        self.y_value = QLabel("—")
        self.y_value.setObjectName("monoBig")
        pos_grid.addWidget(self.y_value, 2, 1)

        self.refresh_pos_btn = QPushButton("Refresh")
        self.refresh_pos_btn.setObjectName("ghost")
        pos_grid.addWidget(self.refresh_pos_btn, 1, 2, 2, 1)

        self.verbose_cb_row = QHBoxLayout()
        self.verbose_cb_row.addWidget(self.verbose_box)
        self.verbose_cb_row.addStretch(1)
        pos_grid.addLayout(self.verbose_cb_row, 3, 0, 1, 3)
        left.addWidget(pos)

        # ---- 移动 ----
        move = QFrame()
        move.setObjectName("formBox")
        move_grid = QGridLayout(move)
        move_grid.setContentsMargins(12, 10, 12, 10)
        move_grid.setSpacing(6)

        move_head = QLabel("Move")
        move_head.setObjectName("fieldLabel")
        move_grid.addWidget(move_head, 0, 0, 1, 4)

        move_grid.addWidget(QLabel("X target:"), 1, 0)
        self.x_target = QDoubleSpinBox()
        self.x_target.setRange(-999999, 999999)
        self.x_target.setDecimals(0)
        self.x_target.setFixedWidth(130)
        move_grid.addWidget(self.x_target, 1, 1)
        self.go_x_btn = QPushButton("Go X")
        self.go_x_btn.setObjectName("ghost")
        move_grid.addWidget(self.go_x_btn, 1, 2)

        move_grid.addWidget(QLabel("Y target:"), 2, 0)
        self.y_target = QDoubleSpinBox()
        self.y_target.setRange(-999999, 999999)
        self.y_target.setDecimals(0)
        self.y_target.setFixedWidth(130)
        move_grid.addWidget(self.y_target, 2, 1)
        self.go_y_btn = QPushButton("Go Y")
        self.go_y_btn.setObjectName("ghost")
        move_grid.addWidget(self.go_y_btn, 2, 2)

        move_grid.addWidget(QLabel("Jog step:"), 3, 0)
        self.jog_step = QDoubleSpinBox()
        self.jog_step.setRange(1, 100000)
        self.jog_step.setDecimals(0)
        self.jog_step.setValue(1000)
        self.jog_step.setFixedWidth(130)
        move_grid.addWidget(self.jog_step, 3, 1)

        jog_row = QHBoxLayout()
        jog_row.setSpacing(4)
        self.jog_xm = QPushButton("X−")
        self.jog_xp = QPushButton("X+")
        self.jog_ym = QPushButton("Y−")
        self.jog_yp = QPushButton("Y+")
        for b in (self.jog_xm, self.jog_xp, self.jog_ym, self.jog_yp):
            b.setObjectName("ghost")
            b.setFixedWidth(48)
            jog_row.addWidget(b)
        jog_row.addStretch(1)
        jog_wrap = QWidget()
        jog_wrap.setLayout(jog_row)
        jog_row.setContentsMargins(0, 0, 0, 0)
        move_grid.addWidget(jog_wrap, 3, 2, 1, 2)
        left.addWidget(move)

        # ---- 动作 ----
        act = QFrame()
        act.setObjectName("formBox")
        act_box = QVBoxLayout(act)
        act_box.setContentsMargins(12, 10, 12, 10)
        act_box.setSpacing(6)

        act_head = QLabel("Actions")
        act_head.setObjectName("fieldLabel")
        act_box.addWidget(act_head)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.halt_btn = QPushButton("HALT")
        self.halt_btn.setObjectName("danger")
        self.origin_btn = QPushButton("Define origin")
        self.origin_btn.setObjectName("ghost")
        self.center_btn = QPushButton("Center and zero…")
        self.center_btn.setObjectName("ghost")
        for b in (self.halt_btn, self.origin_btn, self.center_btn):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        act_box.addLayout(btn_row)

        warn = QLabel("Center and zero drives the stage to the negative hardware "
                      "limits — make sure no objectives can be hit.")
        warn.setObjectName("pageSubtitle")
        warn.setWordWrap(True)
        act_box.addWidget(warn)
        left.addWidget(act)

        left.addStretch(1)
        body.addLayout(left, 0)

        # ------------------------------------------------ 右：日志
        right = QVBoxLayout()
        right.setSpacing(6)
        self.log = LogPane()                 # 跟随布局伸展
        self.save_log_btn = QPushButton("Save…")
        self.save_log_btn.setObjectName("ghost")
        self.save_log_btn.setFixedWidth(70)
        self.log.add_header_button(self.save_log_btn)   # 导出按钮在 Clear 左边
        right.addWidget(self.log, 1)

        # 兼容旧引用（_append_log / save_log 等还在用这两个名字）
        self.log_view = self.log.view
        self.clear_log_btn = self.log.clear_btn

        body.addLayout(right, 1)
        outer.addLayout(body, 1)

        # ------------------------------------------------ 信号
        self.log_line.connect(self._append_log)
        self.selector.log.connect(self._append_log)
        self.selector.connected.connect(self._on_connected)
        self.selector.disconnected.connect(self._on_disconnected)
        self.selector.failed.connect(
            lambda msg: QMessageBox.critical(self, "Connect failed", msg))

        self.refresh_pos_btn.clicked.connect(self.refresh_position)
        self.go_x_btn.clicked.connect(lambda: self._go("x"))
        self.go_y_btn.clicked.connect(lambda: self._go("y"))
        self.jog_xm.clicked.connect(lambda: self._jog("x", -1))
        self.jog_xp.clicked.connect(lambda: self._jog("x", +1))
        self.jog_ym.clicked.connect(lambda: self._jog("y", -1))
        self.jog_yp.clicked.connect(lambda: self._jog("y", +1))
        self.halt_btn.clicked.connect(self.do_halt)
        self.origin_btn.clicked.connect(self.do_define_origin)
        self.center_btn.clicked.connect(self.do_center_and_zero)
        self.save_log_btn.clicked.connect(self.save_log)
        self.verbose_box.currentIndexChanged.connect(self._on_verbose_changed)

        self._update_controls()

    # ------------------------------------------------------------------ 工具
    @property
    def device(self):
        return self.selector.device

    def _device_options(self) -> dict:
        """传给设备工厂的运行时参数（连接时读取）。"""
        return {
            "verbose": self.verbose_box.currentIndex() == 1,
            "log_callback": self.log_line.emit,
        }

    def _append_log(self, text: str) -> None:
        self.log.append(text)

    def _on_connected(self, _device) -> None:
        # 新会话：之前那次连接留下的在途读取到此作废
        self._session += 1
        self._update_units()
        self._update_controls()
        self.refresh_position()

    def _on_disconnected(self) -> None:
        # 断开后不存在「进行中的读取」：作废在途任务并清掉忙状态，
        # 否则新连接的首次自动刷新会被旧 _busy 挡掉。
        self._session += 1
        self._busy = False
        self.x_value.setText("—")
        self.y_value.setText("—")
        self._update_units()
        self._update_controls()

    def _update_units(self) -> None:
        """位置单位取自设备本身 —— 不同位移台的单位可能不同。

        未连接时不写单位，避免给出误导性的默认值。
        """
        dev = self.device
        unit = getattr(dev, "UNIT_LABEL", None) if dev is not None else None
        self.pos_head.setText(f"Position ({unit})" if unit else "Position")

    def _on_verbose_changed(self, index: int) -> None:
        dev = self.device
        if dev is not None and hasattr(dev, "verbose"):
            dev.verbose = index == 1
        self._append_log("Log TX/RX " + ("enabled" if index == 1 else "disabled"))

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        if message:
            self._append_log(message)
        self._update_controls()

    def _update_controls(self) -> None:
        connected = self.device is not None and getattr(
            self.device, "is_connected", True)
        idle = not self._busy

        # HALT 永远可用（急停不能被 busy 挡住）
        self.halt_btn.setEnabled(connected)
        for w in (self.refresh_pos_btn, self.go_x_btn, self.go_y_btn,
                  self.jog_xm, self.jog_xp, self.jog_ym, self.jog_yp,
                  self.origin_btn, self.center_btn):
            w.setEnabled(connected and idle)

    def _no_device(self) -> bool:
        if self.device is None:
            QMessageBox.information(self, "Not connected",
                                    "Connect to the stage first.")
            return True
        return False

    def _handle_failure(self, what: str, message: str) -> None:
        """统一处理后台操作失败：HALT 取消与真实错误区别对待。"""
        if "cancelled by HALT" in message or "cancelled by halt" in message:
            self._set_busy(False, f"{what} cancelled by HALT.")
            return
        self._set_busy(False, f"{what} failed: {message}")
        QMessageBox.critical(self, f"{what} failed", message)

    def _blocked(self) -> bool:
        """忙碌时给一句可见的解释 —— 否则用户会觉得按钮没反应。"""
        if self._busy:
            self._append_log("Busy — wait for the current operation to finish.")
            return True
        return False

    # ------------------------------------------------------------------ 位置
    def refresh_position(self) -> None:
        """读一次 XY 位置。

        走 DeviceTaskSession：执行前和回调前各校验一次会话，断开 / 重连 /
        关窗之后旧结果不会再写进坐标显示。运动、归零和 HALT 仍走原来的
        实现 —— 那几处要的是硬件侧的取消语义，不是回调守卫。
        """
        if self._no_device() or self._busy:
            return
        dev = self.device

        def on_done(result) -> None:
            x, y = result
            self.x_value.setText(f"{x:.0f}")
            self.y_value.setText(f"{y:.0f}")
            self._set_busy(False)

        def on_failed(msg: str) -> None:
            self._set_busy(False, f"Read position failed: {msg}")

        self._set_busy(True, "Reading position…")
        self._tasks.submit(
            lambda: (dev.get_position("x"), dev.get_position("y")),
            on_done=on_done, on_failed=on_failed)

    def _go(self, axis: str) -> None:
        if self._no_device() or self._blocked():
            return
        target = self.x_target.value() if axis == "x" else self.y_target.value()
        dev = self.device

        def work():
            dev.set_position(axis, target)
            return dev.get_position(axis)

        def done(final) -> None:
            self._show_one_axis(axis, final)
            self._set_busy(False, f"Moved {axis.upper()} to {final:.0f}")

        def failed(msg: str) -> None:
            self._handle_failure("Move", msg)

        self._set_busy(True, f"Moving {axis.upper()} to {target:.0f}…")
        run_async(work, done, failed)

    def _jog(self, axis: str, sign: int) -> None:
        if self._no_device() or self._blocked():
            return
        step = self.jog_step.value() * sign
        dev = self.device

        def work():
            jogger = getattr(dev, "jog", None)
            if callable(jogger):
                return jogger(axis, step)
            current = dev.get_position(axis)
            dev.set_position(axis, current + step)
            return dev.get_position(axis)

        def done(final) -> None:
            self._show_one_axis(axis, final)
            self._set_busy(False,
                           f"Jogged {axis.upper()} by {step:+.0f} → {final:.0f}")

        def failed(msg: str) -> None:
            self._handle_failure("Jog", msg)

        self._set_busy(True, f"Jogging {axis.upper()} {step:+.0f}…")
        run_async(work, done, failed)

    def _show_one_axis(self, axis: str, value: float) -> None:
        if axis == "x":
            self.x_value.setText(f"{value:.0f}")
        else:
            self.y_value.setText(f"{value:.0f}")

    # ------------------------------------------------------------------ 动作
    def do_halt(self) -> None:
        """急停：不受 busy 限制。"""
        dev = self.device
        if dev is None:
            return

        def work():
            halter = getattr(dev, "halt", None)
            if callable(halter):
                halter()
            return None

        run_async(work, lambda _r: self._append_log("HALT sent"),
                  lambda msg: self._append_log(f"HALT error: {msg}"))

    def do_define_origin(self) -> None:
        if self._no_device() or self._busy:
            return
        ok = QMessageBox.question(
            self, "Define origin",
            "Set the CURRENT position as the new origin (0, 0)?\n\n"
            "This does not move the stage, but all coordinates will shift.")
        if ok != QMessageBox.StandardButton.Yes:
            return
        dev = self.device

        def work():
            fn = getattr(dev, "def_origin", None)
            if callable(fn):
                fn()
            return dev.get_position("x"), dev.get_position("y")

        def done(result) -> None:
            x, y = result
            self.x_value.setText(f"{x:.0f}")
            self.y_value.setText(f"{y:.0f}")
            self._set_busy(False, "Origin defined.")

        def failed(msg: str) -> None:
            self._handle_failure("Define origin", msg)

        self._set_busy(True, "Defining origin…")
        run_async(work, done, failed)

    def do_center_and_zero(self) -> None:
        if self._no_device() or self._busy:
            return
        dialog = CenterZeroDialog(self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if not (accepted and dialog.confirmed()):
            self._append_log("Center and zero cancelled.")
            return

        dev = self.device

        def work():
            fn = getattr(dev, "center_and_zero", None)
            if callable(fn):
                fn(prompt=False)
            return dev.get_position("x"), dev.get_position("y")

        def done(result) -> None:
            x, y = result
            self.x_value.setText(f"{x:.0f}")
            self.y_value.setText(f"{y:.0f}")
            self._set_busy(False, "Center and zero finished.")

        def failed(msg: str) -> None:
            self._handle_failure("Center and zero", msg)

        self._set_busy(True, "Centering… (this can take a while)")
        run_async(work, done, failed)

    # ------------------------------------------------------------------ 日志
    def save_log(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        text = self.log_view.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "Nothing to save", "The log is empty.")
            return
        default = time.strftime("stage_log_%Y%m%d_%H%M%S.txt")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save log", default, "Text files (*.txt);;All files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._append_log(f"Log saved to {path}")

    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """释放设备：先 HALT 再关串口。

        主窗口关闭不会触发子页面的 closeEvent，所以由 MainWindow 统一
        调用（见 ui/main_window.py 的 closeEvent）。
        """
        # 先作废在途的读取：关窗之后回包不能再写控件
        self._session += 1
        try:
            self.selector.force_disconnect()
        except Exception:                            # noqa: BLE001
            pass

    def closeEvent(self, event) -> None:      # noqa: N802
        self.cleanup()
        super().closeEvent(event)
