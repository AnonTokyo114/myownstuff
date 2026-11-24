"""DAQ 页用到的三个面板：DC 输出、光子计数、脉冲输出。

抽成独立控件是为了让 DAQ 页能用 QTabWidget 组织它们，同时**每一路
DC 输出都有自己的一份状态**（横幅、安全确认、未知状态提示），不会
两路互相串味。

光子计数的取数、差分、画图都在这里；`core.nidaq.PhotonCounter` 只负责
配好计数器并返回原始累计值。
"""

from __future__ import annotations

import csv
import statistics
import time
from collections import deque
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui.workers import DeviceTaskSession, run_async

# 实时曲线是可选功能：没装 matplotlib 时面板照样能读数和存 CSV
try:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    HAS_PLOT = True
except Exception:                                    # noqa: BLE001
    HAS_PLOT = False


# ======================================================================
# 一路 DC 输出
# ======================================================================
class AnalogChannelPanel(QFrame):
    """一路模拟输出：通道名、电压、滑块、应用/停止、状态与安全横幅。"""

    log_line = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, title: str, default_channel: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("formBox")
        self.title = title
        self._device = None
        self._busy = False
        self._applying = False

        grid = QGridLayout(self)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setSpacing(8)

        head = QLabel(title)
        head.setObjectName("fieldLabel")
        grid.addWidget(head, 0, 0, 1, 4)

        grid.addWidget(QLabel("Channel:"), 1, 0)
        self.channel_edit = QLineEdit(default_channel)
        self.channel_edit.setMinimumWidth(140)
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
        self.volt_spin.setFixedWidth(110)
        grid.addWidget(self.volt_spin, 2, 1)

        self.volt_slider = QSlider(Qt.Orientation.Horizontal)
        self.volt_slider.setRange(0, 500)          # 0.01 V steps
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
        wrap = QWidget()
        wrap.setLayout(btn_row)
        btn_row.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(wrap, 4, 0, 1, 4)

        # ---- 安全横幅（平时隐藏）----
        self.warn_banner = QFrame()
        self.warn_banner.setObjectName("dangerBanner")
        banner_layout = QHBoxLayout(self.warn_banner)
        banner_layout.setContentsMargins(10, 6, 10, 6)
        banner_layout.setSpacing(6)
        icon = QLabel("⚠")
        icon.setObjectName("dangerBannerIcon")
        banner_layout.addWidget(icon)
        self.warn_text = QLabel("")
        self.warn_text.setObjectName("dangerBannerText")
        self.warn_text.setWordWrap(True)
        banner_layout.addWidget(self.warn_text, 1)
        self.warn_banner.setVisible(False)
        grid.addWidget(self.warn_banner, 5, 0, 1, 4)

        self.status_label = QLabel("Idle (not connected)")
        self.status_label.setObjectName("pageSubtitle")
        self.status_label.setWordWrap(True)
        grid.addWidget(self.status_label, 6, 0, 1, 4)

        self.volt_spin.valueChanged.connect(self._on_spin_changed)
        self.volt_slider.valueChanged.connect(self._on_slider_changed)
        self.live_cb.toggled.connect(self._on_live_toggled)
        self.apply_btn.clicked.connect(self.apply_output)
        self.stop_btn.clicked.connect(self.stop_output)

        self._update_controls()

    # ------------------------------------------------------------------
    def set_device(self, device) -> None:
        """由页面在连接/断开时调用。"""
        self._device = device
        if device is not None:
            channel = getattr(device, "channel", None)
            if channel:
                self.channel_edit.setText(channel)
        else:
            self._sync_voltage(0.0)
        self._update_controls()
        self._update_status()
        self._update_banner()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_controls()

    # ------------------------------------------------------------------
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
        self.log_line.emit("Live update " + ("on" if on else "off"))
        if on:
            self.apply_output()

    # ------------------------------------------------------------------
    def _update_banner(self) -> None:
        """红色横幅：输出状态未知优先，其次是超过安全阈值。"""
        dev = self._device
        if dev is not None and getattr(dev, "last_applied", 0.0) is None:
            self.warn_text.setText(
                "OUTPUT STATE UNKNOWN — the last zeroing failed, so this "
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
        dev = self._device
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

    def _confirm_laser_safety(self, volts: float) -> bool:
        dev = self._device
        warn_text = getattr(dev, "WARN_TEXT", "")
        question = getattr(dev, "PROTECTION_QUESTION",
                           "Did you wear proper protection?")
        threshold = getattr(dev, "WARN_THRESHOLD", 1.0)
        text = (f"Outputting {volts:.3f} V on {self.channel_edit.text().strip()}.\n\n"
                f"{warn_text}\n\n"
                f"Anything at or above {threshold:.1f} V can drive the laser "
                f"beyond 10 mW.\n\n"
                f"{question}")
        result = QMessageBox.warning(
            self, "LASER SAFETY", text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return result == QMessageBox.StandardButton.Yes

    # ------------------------------------------------------------------
    def apply_output(self) -> None:
        if self._busy or self._applying:
            self.log_line.emit("Busy — wait for the current operation to finish.")
            return
        dev = self._device
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

        needs = (dev.needs_warning(volts)
                 if hasattr(dev, "needs_warning") else False)
        if needs:
            if not self._confirm_laser_safety(volts):
                self.log_line.emit(
                    f"{self.title}: output of {volts:.3f} V cancelled by the user.")
                self._restore_last_value()
                return
            dev.acknowledge_warning()
            self.log_line.emit(
                f"{self.title}: safety acknowledgement recorded for this session.")
        if hasattr(dev, "reset_warning"):
            dev.reset_warning(volts)

        channel = self.channel_edit.text().strip() or getattr(dev, "channel", "")
        self._applying = True
        self._set_busy(True, f"{self.title}: setting {volts:.3f} V on {channel}…")

        def work():
            writer = getattr(dev, "write_voltage", None)
            if callable(writer):
                return writer(volts)
            raise RuntimeError("device has no write_voltage()")

        def done(applied) -> None:
            self._applying = False
            self._sync_voltage(float(applied))
            self._set_busy(False, f"{self.title}: output set to "
                                  f"{float(applied):.3f} V.")
            self._update_status()

        def failed(msg: str) -> None:
            self._applying = False
            self._set_busy(False, f"{self.title}: output failed: {msg}")
            QMessageBox.critical(self, f"{self.title} failed", msg)
            self._update_status()

        run_async(work, done, failed)

    def _restore_last_value(self) -> None:
        """拒绝安全确认时退回上一次**确认过**的值。

        上次归零失败时 last_applied 是 None（未知），没有可信值可退回，
        保持当前显示并提示未知，而不是 float(None) 崩掉。
        """
        dev = self._device
        last = getattr(dev, "last_applied", 0.0) if dev is not None else 0.0
        if last is None:
            self.log_line.emit(
                f"{self.title}: output state is UNKNOWN (an earlier zeroing "
                "failed); the slider was left as-is. Check the hardware.")
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
        dev = self._device
        if dev is None:
            return
        if self._busy:
            self.log_line.emit("Busy — wait for the current operation to finish.")
            return

        def work():
            stopper = getattr(dev, "stop", None)
            if callable(stopper):
                return stopper()
            return None

        def done(result) -> None:
            zeroed = getattr(result, "zeroed", True)
            if zeroed:
                self._sync_voltage(0.0)
                self._set_busy(False, f"{self.title}: stopped — 0 V confirmed, "
                                      "task released.")
            else:
                error = getattr(result, "error", None) or "unknown error"
                # 不要在这里归零滑块：那会让横幅按「低于阈值」隐藏，
                # 而真实状态恰恰是未知
                self._set_busy(
                    False,
                    f"{self.title}: zeroing FAILED — output state UNKNOWN "
                    f"({error}). The channel may still be live.")
                QMessageBox.critical(
                    self, f"{self.title}: zeroing failed",
                    "Could not write 0 V to the DAQ channel.\n\n"
                    f"{error}\n\n"
                    "The output may still be live. Check the hardware (or power "
                    "down the laser) before continuing.")
            self._update_banner()
            self._update_status()

        def failed(msg: str) -> None:
            self._set_busy(False, f"{self.title}: stop failed: {msg}")

        self._set_busy(True, f"{self.title}: stopping (0 V)…")
        run_async(work, done, failed)

    # ------------------------------------------------------------------
    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        if message:
            self.log_line.emit(message)
        self._update_controls()
        self.busy_changed.emit(busy)

    def _update_controls(self) -> None:
        connected = self._device is not None
        idle = not self._busy
        self.apply_btn.setEnabled(connected and idle)
        self.stop_btn.setEnabled(connected)
        self.volt_spin.setEnabled(connected and idle)
        self.volt_slider.setEnabled(connected and idle)
        self.live_cb.setEnabled(connected and idle)
        self.channel_edit.setEnabled(not connected and idle)

    def interrupt(self) -> None:
        """页面关闭时调用：安静地把这一路归零。"""
        dev = self._device
        if dev is None:
            return
        try:
            dev.stop()
        except Exception:                            # noqa: BLE001
            pass


# ======================================================================
# 独立的实时曲线窗口
# ======================================================================
class PlotWindow(QDialog):
    """把实时曲线放进一个独立、可最大化的窗口。

    和主面板**共享同一个 Figure**：弹出期间主面板的 canvas 只是隐藏，
    数据照采，tick 继续往这个 figure 上喂点，所以窗口里看到的是实时的。
    这样不需要搬数据，也不会出现「两个图各画各的」。
    """

    closed = Signal()

    def __init__(self, figure, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Live photon count rate")
        self.resize(1100, 680)
        self.setSizeGripEnabled(True)
        #: closed 只发一次（Esc 和关闭按钮可能都会走到）
        self._notified = False

        self.canvas = FigureCanvas(figure)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)
        layout.addWidget(self.canvas, 1)

        # 缩放 / 平移 / 存图 —— 长时间跑的时候很有用
        try:
            from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
            layout.addWidget(NavigationToolbar2QT(self.canvas, self))
        except Exception:                        # noqa: BLE001
            pass

    def _notify_closed(self) -> None:
        """通知一次「我关了」，重复调用无效。

        QDialog 按 Esc 走 reject() -> done()，**不经过 closeEvent**；
        所以这里同时覆盖两条路径，否则 Esc 关闭后主面板的 canvas 永远
        不会恢复。
        """
        if not self._notified:
            self._notified = True
            self.closed.emit()

    def done(self, result: int) -> None:
        self._notify_closed()
        super().done(result)

    def closeEvent(self, event) -> None:         # noqa: N802
        self._notify_closed()
        super().closeEvent(event)


# ======================================================================
# 光子计数
# ======================================================================
class PhotonPanel(QWidget):
    """SPCM TTL 计数：轮询累计数、差分出计数率、实时曲线、导出 CSV。"""

    log_line = Signal(str)
    busy_changed = Signal(bool)

    DEFAULT_BIN = 1.0
    DEFAULT_WINDOW = 60.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._device = None
        self._running = False
        self._saved = True
        #: Pop out 出来的独立图窗口（None 表示还在主面板里）
        self._popout = None

        self._times: deque[float] = deque()
        self._rates: deque[float] = deque()
        self._log: list[tuple] = []
        #: 窗口内计数率的增量统计（避免每 tick 遍历几万个点）
        self._rate_sum = 0.0
        self._rate_sum_sq = 0.0
        self._dirty = False
        self._prev_count = 0
        self._prev_time = 0.0
        self._t0 = 0.0

        self._timer = QTimer(self)
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self._tick)
        # 重绘单独限速：采样可以 20 Hz，但没必要每采一个点就重画一次 ——
        # 72000 点时一次重绘要 60 ms，会超过最短的 50 ms 采样周期。
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(False)
        self._repaint_timer.setInterval(125)      # 8 帧/秒
        self._repaint_timer.timeout.connect(self._flush_repaint)
        #: 操作序号。Stop 递增它，让「已经点过但还没执行」的旧 Start
        #: 立刻失效，否则旧 Start 可能在 Stop 之后又把任务重新启动起来。
        self._generation = 0

        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        form = QFrame()
        form.setObjectName("formBox")
        grid = QGridLayout(form)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setSpacing(8)

        head = QLabel("Photon counting (TTL edges on a counter input)")
        head.setObjectName("fieldLabel")
        grid.addWidget(head, 0, 0, 1, 6)

        grid.addWidget(QLabel("Counter:"), 1, 0)
        self.counter_edit = QLineEdit("Dev1/ctr0")
        self.counter_edit.setMinimumWidth(130)
        grid.addWidget(self.counter_edit, 1, 1)

        grid.addWidget(QLabel("Source terminal:"), 1, 2)
        self.source_edit = QLineEdit("/Dev1/PFI0")
        self.source_edit.setMinimumWidth(130)
        grid.addWidget(self.source_edit, 1, 3)

        grid.addWidget(QLabel("Integration (s):"), 1, 4)
        self.bin_spin = QDoubleSpinBox()
        self.bin_spin.setRange(0.05, 60.0)
        self.bin_spin.setDecimals(2)
        self.bin_spin.setSingleStep(0.1)
        self.bin_spin.setValue(self.DEFAULT_BIN)
        self.bin_spin.setFixedWidth(90)
        grid.addWidget(self.bin_spin, 1, 5)

        grid.addWidget(QLabel("Window (s):"), 2, 0)
        self.window_spin = QDoubleSpinBox()
        self.window_spin.setRange(5.0, 3600.0)
        self.window_spin.setDecimals(0)
        self.window_spin.setSingleStep(10.0)
        self.window_spin.setValue(self.DEFAULT_WINDOW)
        self.window_spin.setFixedWidth(90)
        grid.addWidget(self.window_spin, 2, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("primary")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("danger")
        self.save_btn = QPushButton("Save CSV…")
        self.save_btn.setObjectName("ghost")
        self.clear_btn = QPushButton("Clear plot")
        self.clear_btn.setObjectName("ghost")
        self.popout_btn = QPushButton("Pop out plot")
        self.popout_btn.setObjectName("ghost")
        self.popout_btn.setToolTip(
            "Open the live plot in its own resizable window (maximise or "
            "full-screen it for a long run)")
        for b in (self.start_btn, self.stop_btn, self.save_btn,
                  self.clear_btn, self.popout_btn):
            btn_row.addWidget(b)
        btn_row.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(btn_row)
        btn_row.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(wrap, 3, 0, 1, 6)

        # ---- 读数 ----
        readout = QHBoxLayout()
        readout.setSpacing(18)
        self.rate_label = QLabel("Rate: —")
        self.rate_label.setObjectName("monoBig")
        self.stats_label = QLabel("Mean: —   Std: —")
        self.stats_label.setObjectName("pageSubtitle")
        self.total_label = QLabel("Total counts: —")
        self.total_label.setObjectName("pageSubtitle")
        readout.addWidget(self.rate_label)
        readout.addWidget(self.stats_label)
        readout.addWidget(self.total_label)
        readout.addStretch(1)
        grid.addLayout(readout, 4, 0, 1, 6)

        self.status_label = QLabel("Idle (not connected)")
        self.status_label.setObjectName("pageSubtitle")
        self.status_label.setWordWrap(True)
        grid.addWidget(self.status_label, 5, 0, 1, 6)

        outer.addWidget(form)

        # ---- 曲线（可选）----
        self.figure = None
        self.canvas = None
        if HAS_PLOT:
            box = QFrame()
            box.setObjectName("formBox")
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(6, 6, 6, 6)
            # constrained 布局：每次 draw 自动为标题/坐标轴标签留空间。
            # 用 tight_layout() 只在创建时算一次，窗口一缩标签就会被裁。
            self.figure = Figure(figsize=(7.6, 3.4), dpi=100,
                                 layout="constrained")
            self._ax = self.figure.add_subplot(111)
            self._ax.set_xlabel("Elapsed (s)")
            self._ax.set_ylabel("Count rate (counts/s)")
            self._ax.set_title("Live photon count rate", fontsize=9)
            self._ax.grid(alpha=0.3)
            (self._line,) = self._ax.plot([], [], lw=1.4)
            self._ax.set_xlim(0, self.DEFAULT_WINDOW)
            self._ax.set_ylim(0, 1)
            self.canvas = FigureCanvas(self.figure)
            # 给一个够大的下限：宁可让页面滚动，也不要把图压到看不见
            # x 轴标签。外层是 QScrollArea，所以这里可以直接要高度。
            self.canvas.setMinimumHeight(460)
            box_layout.addWidget(self.canvas, 1)
            outer.addWidget(box, 1)
        else:
            note = QLabel("matplotlib is not installed — the live plot is "
                          "unavailable, but counting and CSV export still work.")
            note.setObjectName("pageSubtitle")
            note.setWordWrap(True)
            outer.addWidget(note)
            outer.addStretch(1)

        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        self.save_btn.clicked.connect(self.save_csv)
        self.clear_btn.clicked.connect(self.clear_plot)
        self.popout_btn.clicked.connect(self.toggle_popout)
        self._update_controls()

    # ------------------------------------------------------------------
    def set_device(self, device) -> None:
        """绑定/解绑设备。

        解绑时必须**先停掉自己在跑的东西**：旧定时器如果还活着，会在
        新连接建立后继续对新设备发起 read，而那个设备上的任务根本
        没启动过；同时 _running 残留会让 Start 按钮一直灰着，新连接
        没法马上开始计数。
        """
        # 连接会话变了：在途的异步操作必须全部作废，否则它们的回调会把
        # 旧设备的结果写到新连接的界面上（旧 Apply 显示 Pulsing，而新
        # 设备其实 Idle）。
        self._generation += 1
        if device is None and (self._running or self._timer.isActive()):
            self.interrupt()
        self._device = device
        if device is not None:
            counter = getattr(device, "counter", None)
            source = getattr(device, "source", None)
            if counter:
                self.counter_edit.setText(counter)
            if source:
                self.source_edit.setText(source)
            # 新连接：状态归零，按钮按「未运行」重新启用
            self._running = False
            self._timer.stop()
        self._update_controls()
        self._update_status()

    def set_busy(self, busy: bool) -> None:
        self._update_controls()

    # ------------------------------------------------------------------
    @property
    def device(self):
        """当前绑定的设备（DeviceTaskSession 靠它判断会话是否已变）。"""
        return self._device

    def start(self) -> None:
        dev = self._device
        if dev is None:
            QMessageBox.information(self, "Not connected",
                                    "Connect to the DAQ device first.")
            return
        if self._running:
            return
        if self._log and not self._saved:
            answer = QMessageBox.question(
                self, "Unsaved data",
                "Starting a new run will discard the current data. Continue?")
            if answer != QMessageBox.StandardButton.Yes:
                return

        try:
            bin_time = float(dev.clamp_bin(self.bin_spin.value()))
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid value", str(exc))
            return

        self._generation += 1
        generation = self._generation
        bound = dev                              # 这次操作绑定的设备

        def work():
            if generation != self._generation or bound is not self._device:
                return None                      # 已被 Stop / 解绑取消
            started = dev.start(
                counter=self.counter_edit.text().strip() or None,
                source=self.source_edit.text().strip() or None)
            # start() 返回 (起始值, token)：token 必须来自返回值，读
            # dev.last_token 会被别的 start 改掉
            first, token = started
            if generation != self._generation:
                # 启动过程中被 Stop 了：撤销，但**只撤销自己启动的那个**。
                # 无条件 stop() 会把后来者刚建好的任务一起停掉，而那个
                # 任务的界面还显示「在跑」。
                stopper = getattr(dev, "stop_if_still", None)
                if callable(stopper):
                    try:
                        stopper(token)
                    except Exception:            # noqa: BLE001
                        pass
                return None
            return first

        def done(first) -> None:
            if (generation != self._generation or first is None
                    or bound is not self._device):
                return                           # 过期 / 已取消 / 换了设备
            self._reset_data()
            self._prev_count = int(first)
            self._t0 = self._prev_time = time.perf_counter()
            self._running = True
            self._saved = True
            self.status_label.setText(
                f"Counting on {self.counter_edit.text().strip()} "
                f"(source {self.source_edit.text().strip()}), "
                f"{bin_time:g} s integration")
            self.log_line.emit(
                f"Photon counting started: {self.counter_edit.text().strip()} "
                f"<- {self.source_edit.text().strip()}")
            self._update_controls()
            self._timer.start(max(50, int(bin_time * 1000)))
            self._repaint_timer.start()

        def failed(msg: str) -> None:
            if generation != self._generation or bound is not self._device:
                return                           # 过期失败，不打扰用户
            self.status_label.setText(f"Error (counting not running): {msg}")
            self.log_line.emit(f"Photon counting failed: {msg}")
            QMessageBox.critical(self, "Counting failed", msg)
            self._update_controls()

        self.status_label.setText("Starting counter…")
        self.busy_changed.emit(True)
        run_async(work, done, failed)

    def _flush_repaint(self) -> None:
        """重绘节拍：只有攒了新点才真正画一次。"""
        if not self._dirty or self.figure is None:
            return
        self._dirty = False
        self._draw_curve()

    def _tick(self) -> None:
        dev = self._device
        if dev is None or not self._running:
            return
        try:
            count = int(dev.read())
        except Exception as exc:                     # noqa: BLE001
            self.log_line.emit(f"Photon count read failed: {exc}")
            self.status_label.setText(f"Error (counting stopped): {exc}")
            self.stop()
            return

        now = time.perf_counter()
        dt = now - self._prev_time
        counts = self._delta(self._prev_count, count)
        rate = counts / dt if dt > 0 else 0.0
        elapsed = now - self._t0

        self._log.append((
            datetime.now().isoformat(timespec="milliseconds"),
            elapsed, dt, counts, rate))
        self._saved = False
        self._times.append(elapsed)
        self._rates.append(rate)

        window = max(5.0, self.window_spin.value())
        # 滑出窗口的点要同时从增量统计里减掉
        while self._times and self._times[0] < elapsed - window:
            self._times.popleft()
            dropped = self._rates.popleft()
            self._rate_sum -= dropped
            self._rate_sum_sq -= dropped * dropped

        self._prev_count = count
        self._prev_time = now
        self._update_readout(elapsed, window, rate, count)
        # 只标记「有新数据」，真正重绘交给 8 Hz 的节拍
        self._dirty = True

        # 用设定的积分时间重新起表，抵消读数耗时
        try:
            bin_ms = max(1, int((self.bin_spin.value()
                                 - (time.perf_counter() - now)) * 1000))
        except Exception:                            # noqa: BLE001
            bin_ms = 1000
        self._timer.start(max(10, bin_ms))

    def _delta(self, previous: int, current: int) -> int:
        """两次读数之差，处理 32 位回绕。"""
        delta = current - previous
        if delta < 0:
            delta += 2 ** 32
        return delta

    def _update_readout(self, elapsed: float, window: float,
                        rate: float, total: int) -> None:
        """只更新文本和统计，不碰画布。

        统计用增量维护的 sum / sum_sq：窗口里可能有 72000 个点，每 tick
        遍历一次光统计就要几十毫秒。
        """
        self._rate_sum += rate
        self._rate_sum_sq += rate * rate

        self.rate_label.setText(f"Rate: {rate:,.0f} cps")
        self.total_label.setText(f"Total counts: {total:,}")

        n = len(self._rates)
        if n >= 2:
            mean = self._rate_sum / n
            variance = max(0.0, (self._rate_sum_sq - n * mean * mean)
                           / (n - 1))
            self.stats_label.setText(
                f"Mean: {mean:,.0f}   Std: {variance ** 0.5:,.0f}  (window)")

    def _draw_curve(self) -> None:
        """真正画一次（由 _repaint_timer 或显式重绘调用）。"""
        if self.figure is None or self.canvas is None:
            return
        self._line.set_data(list(self._times), list(self._rates))
        if self._times:
            window = max(5.0, self.window_spin.value())
            latest = self._times[-1]
            self._ax.set_xlim(max(0.0, latest - window),
                              max(window, latest))
        if self._rates:
            lo, hi = min(self._rates), max(self._rates)
            if lo == hi:
                lo, hi = lo - 1.0, hi + 1.0
            else:
                margin = 0.05 * (hi - lo)
                lo, hi = lo - margin, hi + margin
            self._ax.set_ylim(max(0.0, lo), hi)
        self._repaint()

    def _repaint(self) -> None:
        """重画曲线：只画**当前可见**的那个 canvas。

        判断依据必须是「弹出窗口是否真的显示着」，不能是「_popout 指针
        是否非空」—— 为了复用，收回之后指针仍然保留，用非空判断会一直
        往一个隐藏的画布上画，可见的主图就再也不更新了。
        """
        popout_visible = (self._popout is not None
                          and self._popout.isVisible())
        target = self._popout.canvas if popout_visible else self.canvas
        if target is not None:
            target.draw_idle()

    def toggle_popout(self) -> None:
        """把实时曲线在主面板和独立窗口之间来回搬。"""
        if self.figure is None or self.canvas is None:
            return
        # 窗口只建一次，之后反复显隐 —— 每次新建会让隐藏窗口（连同
        # 它的 RGBA 缓冲）留在父对象下面，反复开关就一路涨内存。
        if self._popout is None:
            # parent=None：不挂在面板下面，避免被父对象持有
            self._popout = PlotWindow(self.figure)
            self._popout.closed.connect(self._on_popout_closed)

        if self._popout.isVisible():
            self._popout.close()          # 走统一恢复路径
            return

        # 复用同一个窗口：重置「已通知」标志，否则第二次 Esc 就不再发
        # closed 信号，主面板的 canvas 会一直藏着
        self._popout._notified = False
        # 主面板的 canvas 收起来；数据仍在采，figure 仍然在更新
        self.canvas.setVisible(False)
        self._popout.show()
        self._popout.raise_()
        self.popout_btn.setText("Dock plot")
        self._repaint()

    def _on_popout_closed(self) -> None:
        """弹出窗口关闭 / Esc：恢复主面板（可重复调用）。"""
        if self.canvas is not None:
            self.canvas.setVisible(True)
            # Figure.canvas 在弹出时被绑到了弹窗的画布上。收回来必须
            # 重新绑回主画布，否则这个指针会一直指着弹窗的 canvas，
            # 弹窗一销毁就悬空（figure.canvas.isValid() == False）。
            try:
                self.figure.canvas = self.canvas
            except Exception:                    # noqa: BLE001
                pass
            self.canvas.draw_idle()
        self.popout_btn.setText("Pop out plot")
        if self._popout is not None:
            self._popout.hide()           # 保留对象，下次复用

    def release_popout(self) -> None:
        """彻底释放弹出窗口（断开设备 / 销毁页面时调用）。

        不能只 hide + deleteLater：那会绕开 _on_popout_closed，主画布
        一直藏着、按钮还写着 "Dock plot"、Figure.canvas 指向被删掉的
        画布。所以先走完整的收回流程，再销毁对象。
        """
        if self._popout is None:
            return
        window = self._popout
        self._on_popout_closed()          # 恢复可见性 / 按钮 / canvas 绑定
        window.deleteLater()
        self._popout = None

    def stop(self) -> None:
        # 递增编号：在途的 start 回调到这里就作废了。不加这一步的话，
        # 一个还没返回的 start 会在 stop 之后执行 done，把定时器又启起来，
        # 于是「停采后点了 Clear，过一会儿又冒出一个点」。
        self._generation += 1
        self._timer.stop()
        self._repaint_timer.stop()
        self._running = False
        # 停采后再补画一次，让最后几个点也上屏
        if self._dirty:
            self._dirty = False
            self._draw_curve()
        dev = self._device
        if dev is not None:
            try:
                dev.stop()
            except Exception as exc:                 # noqa: BLE001
                self.log_line.emit(f"Photon counter stop failed: {exc}")
        suffix = ""
        if self._log and not self._saved:
            suffix = " (unsaved data — use Save CSV)"
        self.status_label.setText("Stopped" + suffix)
        self.log_line.emit("Photon counting stopped")
        self._update_controls()

    def _reset_data(self) -> None:
        """开始新一轮采集 / 清图时的完整重置。

        增量统计（_rate_sum / _rate_sum_sq）必须一起清 —— 否则第二轮
        会接着上一轮的总和算均值，Mean 直接翻倍。之前只清了队列，靠
        「先点 Clear」才碰巧正确，正常 Stop -> Start 就会露馅。
        """
        self._times.clear()
        self._rates.clear()
        self._log = []
        self._prev_count = 0
        self._prev_time = 0.0
        self._t0 = 0.0
        self._rate_sum = 0.0
        self._rate_sum_sq = 0.0
        self._dirty = False
        if self.figure is not None and self.canvas is not None:
            self._line.set_data([], [])
            self._repaint()

    def clear_plot(self) -> None:
        """清掉已采的数据。

        运行中清图**必须**重建采样基线：计数器还在跑，如果把 _t0 /
        _prev_time 归零，下一条 _tick 会把绝对 perf_counter（几万秒）
        当成 elapsed 和 dt，算出 0.36 cps 这种荒唐的速率，而且这个
        错误基线会一路带进 CSV。

        所以运行中用「此刻的时间 + 此刻的累计计数」重建基线，让新一段
        从 0 秒、0 个计数重新开始；没在运行时才彻底清零。
        """
        # 先把基线确定下来，再清显示数据：读失败时应该**什么都不动**，
        # 连已经画出来的曲线也保留，否则用户点了清图却只丢数据不清基线。
        if self._running and self._device is not None:
            try:
                count = int(self._device.read())
            except Exception as exc:                 # noqa: BLE001
                # 读不到基线时**不能清**：把「读不到」当成「累计数是 0」，
                # 下一条就会把整段累计（可能是上百万）当成新增计数，
                # 伪造出一个几千万 cps 的假峰，还会进 CSV。
                self.log_line.emit(
                    f"Clear plot skipped: could not read the current count "
                    f"({exc}). The plot and baseline were left untouched.")
                return
            now = time.perf_counter()
            self._prev_count = count
            self._prev_time = now
            self._t0 = now
        else:
            self._prev_count = 0
            self._prev_time = 0.0
            self._t0 = 0.0

        # 基线确定之后才清显示数据
        self._times.clear()
        self._rates.clear()
        self._log = []
        self._rate_sum = 0.0
        self._rate_sum_sq = 0.0
        self._dirty = False
        if self.figure is not None and self.canvas is not None:
            self._line.set_data([], [])
            self._repaint()               # 画**当前可见**的那个画布
        self.rate_label.setText("Rate: —")
        self.stats_label.setText("Mean: —   Std: —")
        self.total_label.setText("Total counts: —")

    def save_csv(self) -> None:
        if not self._log:
            QMessageBox.information(self, "No data", "Nothing to save yet.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save photon counts",
            f"photon_counts_{datetime.now():%Y%m%d_%H%M%S}.csv",
            "CSV files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["timestamp", "elapsed_s", "dt_s",
                                 "counts", "rate_cps"])
                writer.writerows(self._log)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._saved = True
        self.status_label.setText(f"Saved {len(self._log)} points to {path}")
        self.log_line.emit(f"Photon counts saved: {path}")

    # ------------------------------------------------------------------
    def _update_status(self) -> None:
        if self._device is None:
            self.status_label.setText("Idle (not connected)")
        elif not self._running:
            self.status_label.setText("Idle (counter not running)")

    def _update_controls(self) -> None:
        connected = self._device is not None
        running = self._running
        self.start_btn.setEnabled(connected and not running)
        self.stop_btn.setEnabled(running)
        self.save_btn.setEnabled(bool(self._log))
        self.bin_spin.setEnabled(connected and not running)
        self.window_spin.setEnabled(True)
        self.counter_edit.setEnabled(connected and not running)
        self.source_edit.setEnabled(connected and not running)

    def interrupt(self) -> None:
        self._generation += 1
        self._timer.stop()
        self._repaint_timer.stop()
        self._running = False
        # 弹出窗口没有父对象（为了不被父容器持有），页面销毁时不会
        # 连带关掉它 —— 关掉 DAQ 主界面后它会留在屏幕上，而且因为
        # quitOnLastWindowClosed 的存在，独立进程还会一直活着。
        # release_popout 会先走完整收回流程（恢复主画布与按钮），再销毁。
        self.release_popout()
        if self._device is not None:
            try:
                self._device.stop()
            except Exception:                        # noqa: BLE001
                pass


# ======================================================================
# 脉冲输出
# ======================================================================
class PulsePanel(QWidget):
    """计数器脉冲串：频率 + 占空比，连续输出。"""

    log_line = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._device = None
        self._running = False
        #: 操作序号。每次 Start / Stop 都递增，让尚未执行的旧操作失效。
        #: 这一层**不能**用连接会话号代替：同一连接里连按 Apply 两次，
        #: 会话号不会变，但旧 Stop 的清理动作绝不能落到新任务上。
        self._generation = 0
        #: 连接会话。断开 / 重连时递增 —— 与累计到面板的 _generation 是
        #: 两个正交的维度，DeviceTaskSession 管前者。
        self._session = 0
        self._tasks = DeviceTaskSession(self)
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        form = QFrame()
        form.setObjectName("formBox")
        grid = QGridLayout(form)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setSpacing(8)

        head = QLabel("Pulse generator (counter output)")
        head.setObjectName("fieldLabel")
        grid.addWidget(head, 0, 0, 1, 4)

        grid.addWidget(QLabel("Counter:"), 1, 0)
        self.counter_edit = QLineEdit("Dev1/ctr0")
        self.counter_edit.setMinimumWidth(140)
        grid.addWidget(self.counter_edit, 1, 1)

        self.channel_label = QLabel("Continuous pulse train on the counter output")
        self.channel_label.setObjectName("pageSubtitle")
        grid.addWidget(self.channel_label, 1, 2, 1, 2)

        grid.addWidget(QLabel("Frequency (Hz):"), 2, 0)
        self.freq_spin = QDoubleSpinBox()
        self.freq_spin.setRange(1.0, 1_000_000.0)
        self.freq_spin.setDecimals(3)
        self.freq_spin.setSingleStep(100.0)
        self.freq_spin.setValue(1000.0)
        self.freq_spin.setFixedWidth(140)
        grid.addWidget(self.freq_spin, 2, 1)

        grid.addWidget(QLabel("Duty cycle (%):"), 3, 0)
        self.duty_spin = QDoubleSpinBox()
        self.duty_spin.setRange(0.1, 99.9)
        self.duty_spin.setDecimals(1)
        self.duty_spin.setSingleStep(1.0)
        self.duty_spin.setValue(25.0)
        self.duty_spin.setFixedWidth(140)
        grid.addWidget(self.duty_spin, 3, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.start_btn = QPushButton("Start / Apply")
        self.start_btn.setObjectName("primary")
        self.stop_btn = QPushButton("Stop pulse")
        self.stop_btn.setObjectName("danger")
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.stop_btn)
        btn_row.addStretch(1)
        wrap = QWidget()
        wrap.setLayout(btn_row)
        btn_row.setContentsMargins(0, 0, 0, 0)
        grid.addWidget(wrap, 4, 0, 1, 4)

        self.status_label = QLabel("Idle (not connected)")
        self.status_label.setObjectName("pageSubtitle")
        self.status_label.setWordWrap(True)
        grid.addWidget(self.status_label, 5, 0, 1, 4)

        outer.addWidget(form)
        outer.addStretch(1)

        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        self._update_controls()

    # ------------------------------------------------------------------
    def set_device(self, device) -> None:
        """绑定/解绑设备；解绑时清掉陈旧的运行标志。"""
        # 连接会话变了：在途操作全部作废（会话号 + 操作序号一起递增，
        # 这样无论旧任务是被哪一层拦下都成立）
        self._session += 1
        self._generation += 1
        if device is None and self._running:
            self.interrupt()
        self._device = device
        if device is not None:
            counter = getattr(device, "counter", None)
            if counter:
                self.counter_edit.setText(counter)
            self._running = False
        self._update_controls()
        self._update_status()

    def set_busy(self, busy: bool) -> None:
        self._update_controls()

    # ------------------------------------------------------------------
    @property
    def device(self):
        """当前绑定的设备（DeviceTaskSession 靠它判断会话是否已变）。"""
        return self._device

    def start(self) -> None:
        dev = self._device
        if dev is None:
            QMessageBox.information(self, "Not connected",
                                    "Connect to the DAQ device first.")
            return
        try:
            freq, duty = dev.check_params(self.freq_spin.value(),
                                          self.duty_spin.value())
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid value", str(exc))
            return

        self._generation += 1
        generation = self._generation

        def work():
            # 走到这里说明会话和操作序号都还是本次的（submit 已校验）
            f_out, d_out, token = dev.start(
                freq, duty,
                counter=self.counter_edit.text().strip() or None)
            if generation != self._generation:
                # 启动期间又被 Stop / 新 Apply 顶替了：只撤销**自己启动的
                # 那个**任务。无条件 stop() 停的是后来者的任务。
                stopper = getattr(dev, "stop_if_still", None)
                if callable(stopper):
                    try:
                        stopper(token)
                    except Exception:            # noqa: BLE001
                        pass
                return None
            return (f_out, d_out)

        def done(params) -> None:
            if params is None:
                return                           # work 里判定过期，自行撤销了
            f, d = params
            self._running = True
            self.status_label.setText(
                f"Pulsing on {self.counter_edit.text().strip()}: "
                f"{f:g} Hz, {d:g} % duty")
            self.log_line.emit(
                f"Pulse started on {self.counter_edit.text().strip()}: "
                f"{f:g} Hz, {d:g} % duty")
            self._update_controls()

        def failed(msg: str) -> None:
            self._running = False
            self.status_label.setText(f"Error (pulse not running): {msg}")
            self.log_line.emit(f"Pulse start failed: {msg}")
            QMessageBox.critical(self, "Pulse failed", msg)
            self._update_controls()

        self.status_label.setText(
            f"Starting pulse train: {freq:g} Hz, {duty:g} %…")
        self.busy_changed.emit(True)
        # still_valid 是第二层：同一连接内又按了一次 Start/Stop 就作废。
        # 会话层（断开/重连）由 DeviceTaskSession 自己管。
        self._tasks.submit(
            work, on_done=done, on_failed=failed,
            still_valid=lambda: generation == self._generation)

    def stop(self) -> None:
        dev = self._device
        if dev is None:
            return

        # 递增序号：所有「已提交但还没跑」的 Start 到这里就作废了
        self._generation += 1
        generation = self._generation
        # 捕获**提交时**正在输出的那个任务。work 真正执行时可能已经有
        # 更新的 Apply 顶替了它，那时不能停 —— 只过滤完成回调是不够的，
        # 硬件已经被停了，界面再对也只是表面一致。
        token = getattr(dev, "last_token", None)

        def work():
            # token 是提交时捕获的：只停**那个**任务，不是「当前任务」
            if token is not None and hasattr(dev, "stop_if_still"):
                return dev.stop_if_still(token)
            return dev.stop()

        def done(error) -> None:
            # 能走到这里就说明这次 Stop 仍是最新操作：界面可以改成已停
            self._running = False
            if error:
                self.status_label.setText(f"Stop reported an error: {error}")
                self.log_line.emit(f"Pulse stop error: {error}")
            else:
                self.status_label.setText("Stopped (pulse task released)")
                self.log_line.emit("Pulse train stopped")
            self._update_controls()

        def failed(msg: str) -> None:
            self._running = False
            self.status_label.setText(f"Stop failed: {msg}")
            self.log_line.emit(f"Pulse stop failed: {msg}")
            self._update_controls()

        self.busy_changed.emit(True)
        self._tasks.submit(
            work, on_done=done, on_failed=failed,
            still_valid=lambda: generation == self._generation)

    # ------------------------------------------------------------------
    def _update_status(self) -> None:
        if self._device is None:
            self.status_label.setText("Idle (not connected)")
            return
        status_fn = getattr(self._device, "status_text", None)
        if callable(status_fn):
            try:
                self.status_label.setText(status_fn())
                return
            except Exception:                        # noqa: BLE001
                pass
        self.status_label.setText("Idle (pulse not running)")

    def _update_controls(self) -> None:
        connected = self._device is not None
        self.start_btn.setEnabled(connected)
        self.stop_btn.setEnabled(connected and self._running)
        self.freq_spin.setEnabled(connected)
        self.duty_spin.setEnabled(connected)
        self.counter_edit.setEnabled(connected and not self._running)

    def interrupt(self) -> None:
        # 关窗：会话作废，迟到的回调不再碰控件
        self._session += 1
        self._running = False
        if self._device is not None:
            try:
                self._device.stop()
            except Exception:                        # noqa: BLE001
                pass
