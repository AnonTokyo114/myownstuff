"""Device Interface：设备 GUI 的分类启动入口。

这里**不内嵌**任何设备面板。每个设备都是独立的 GUI，可以：

  * 从本页面点 Launch 启动
  * 或者在终端里直接运行 ``python -m devicegui.stage_app``

**生命周期是解耦的**：设备窗口由它自己关闭，主窗口退出不会结束它们。
反过来，某个设备驱动崩了也带不走主界面。

为什么入口不提供 "Stop"：

    用 QProcess.terminate() 结束子进程时，Linux 上那是 SIGTERM ——
    进程直接死掉，Qt 根本收不到 closeEvent，于是设备的 halt()/stop()/
    close() 全都没机会执行。位移台可能还在动，DAQ 可能还带着电压。
    与其给一个看起来能停、实际绕过安全清理的按钮，不如让用户去关那个
    设备窗口本身（那条路径会走完整的清理流程）。

关于「收纳类」入口：

    有些设备不值得单独占一张卡片（例如滤光片轮）。它们在
    ``DEVICE_APPS`` 里被登记成某个入口的 ``members``，那张卡片上就会
    多一个下拉，选好再 Launch。设备本身仍然能独立运行。
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QProcess, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from devicegui.base import DEVICE_APPS, ROOT, DeviceApp, top_level_apps

#: 卡片状态
IDLE = "idle"
RUNNING = "running"
EXITED = "exited"

#: 多久检查一次子进程是否还活着（毫秒）
POLL_MS = 2000
#: 启动后多久做第一次存活检查
FIRST_CHECK_MS = 2500


def pid_alive(pid: int) -> bool:
    """跨平台判断 PID 是否还活着。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)          # 只探测，不真正发信号
        return True
    except OSError:
        return False


class DeviceCard(QFrame):
    """一个设备入口卡片。

    ``app.members`` 非空时（收纳类入口，如 Others），卡片上会多一个
    下拉用来挑具体启动哪个成员。
    """

    launch_requested = Signal(str)

    def __init__(self, app: DeviceApp, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("formBox")
        self.app = app
        self._state = IDLE

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        icon = QLabel(app.icon)
        icon.setObjectName("hubIcon")
        icon.setFixedWidth(26)
        head.addWidget(icon)

        title = QLabel(app.title)
        title.setObjectName("hubTitle")
        head.addWidget(title)
        head.addStretch(1)

        if not app.implemented and not app.members:
            tag = QLabel("placeholder")
            tag.setObjectName("hubTag")
            head.addWidget(tag)
        layout.addLayout(head)

        summary = QLabel(app.summary)
        summary.setObjectName("pageSubtitle")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        # ---- 成员下拉（只有收纳类入口才有）----
        self.member_box: QComboBox | None = None
        if app.members:
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(QLabel("Device:"))
            self.member_box = QComboBox()
            for key in app.members:
                member = DEVICE_APPS.get(key)
                if member is not None:
                    self.member_box.addItem(member.title, key)
            self.member_box.setMinimumWidth(240)
            self.member_box.currentIndexChanged.connect(
                self._on_member_changed)
            row.addWidget(self.member_box, 1)
            layout.addLayout(row)

        self.cmd_label = QLabel("")
        self.cmd_label.setObjectName("hubCommand")
        self.cmd_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.cmd_label.setToolTip(
            "Run this in a terminal to start the device on its own")
        layout.addWidget(self.cmd_label)
        self._refresh_command()

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.state_label = QLabel("● Not running")
        self.state_label.setObjectName("pageSubtitle")
        foot.addWidget(self.state_label, 1)

        self.action_btn = QPushButton("Launch")
        self.action_btn.setObjectName("primary")
        self.action_btn.setFixedWidth(110)
        self.action_btn.clicked.connect(self._emit_launch)
        foot.addWidget(self.action_btn)
        layout.addLayout(foot)

        self._apply_state(IDLE)

    # ------------------------------------------------------------------
    def current_key(self) -> str:
        """这张卡片当前指向的设备 key（收纳类入口看下拉）。"""
        if self.member_box is not None:
            data = self.member_box.currentData()
            if data:
                return str(data)
        return self.app.key

    def _refresh_command(self) -> None:
        key = self.current_key()
        app = DEVICE_APPS.get(key) or self.app
        self.cmd_label.setText(f"python -m {app.module}")

    def _on_member_changed(self, _index: int) -> None:
        self._refresh_command()
        # 换了设备，先回到"未运行"，接着由轮询刷新真实状态
        self._apply_state(IDLE)

    def _emit_launch(self) -> None:
        self.launch_requested.emit(self.current_key())

    # ------------------------------------------------------------------
    def _apply_state(self, state: str, detail: str = "") -> None:
        self._state = state
        colors = {
            IDLE: ("● Not running", "#9aa3ae"),
            RUNNING: ("● Running", "#2e7d32"),
            EXITED: ("● Exited", "#9aa3ae"),
        }
        text, color = colors.get(state, colors[IDLE])
        if detail:
            text = f"{text}  {detail}"
        self.state_label.setText(text)
        self.state_label.setStyleSheet(f"color:{color};")

        if state == RUNNING:
            self.action_btn.setText("Running")
            self.action_btn.setEnabled(False)
            self.action_btn.setToolTip(
                "Close that device window to stop it — the launcher never "
                "kills it, so the device gets a chance to halt and release.")
        else:
            self.action_btn.setText("Launch")
            self.action_btn.setEnabled(True)
            self.action_btn.setToolTip("Start this device in its own window")
        self.action_btn.setObjectName("primary")
        self.action_btn.style().unpolish(self.action_btn)
        self.action_btn.style().polish(self.action_btn)

    def set_state(self, state: str, detail: str = "") -> None:
        self._apply_state(state, detail)


class DeviceHubPage(QWidget):
    page_title = "Device Interface"

    def __init__(self) -> None:
        super().__init__()
        self._pids: dict[str, int] = {}
        self._cards: dict[str, DeviceCard] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        title = QLabel("Device Interface")
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        subtitle = QLabel(
            "Pick an instrument below. Each one opens in its own window and "
            "keeps running when this launcher is closed — close that window "
            "to stop the device, so it can halt and release the hardware "
            "properly. Every device can also be started on its own from a "
            "terminal; the command is on each card.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        grid = QGridLayout()
        grid.setSpacing(10)
        for index, app in enumerate(top_level_apps()):
            card = DeviceCard(app)
            card.launch_requested.connect(self.launch)
            grid.addWidget(card, index // 2, index % 2)
            self._cards[app.key] = card
        outer.addLayout(grid)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.summary_label = QLabel("No device windows running.")
        self.summary_label.setObjectName("pageSubtitle")
        foot.addWidget(self.summary_label, 1)
        outer.addLayout(foot)

        outer.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll_states)
        self._timer.start()

    # ------------------------------------------------------------------
    def _card_for(self, kind: str) -> DeviceCard | None:
        """找到某个设备所属的卡片（收纳类入口会代管它的成员）。"""
        card = self._cards.get(kind)
        if card is not None:
            return card
        for key, card in self._cards.items():
            app = DEVICE_APPS.get(key)
            if app is not None and kind in app.members:
                return card
        return None

    # ------------------------------------------------------------------
    def launch(self, kind: str) -> None:
        """用**脱离的**子进程启动设备 GUI。"""
        if kind in self._pids:
            return
        app = DEVICE_APPS.get(kind)
        card = self._card_for(kind)
        if app is None:
            return

        try:
            ok, pid = QProcess.startDetached(
                sys.executable, ["-m", app.module], str(ROOT))
        except Exception as exc:                     # noqa: BLE001
            if card is not None:
                card.set_state(EXITED, f"— {exc}")
            return

        if not ok:
            if card is not None:
                card.set_state(
                    EXITED,
                    "— could not start; run the command on the card to see why")
            return

        self._pids[kind] = int(pid)
        if card is not None:
            card.set_state(RUNNING, f"(pid {pid})")
        self._refresh_summary()

        QTimer.singleShot(FIRST_CHECK_MS, self._poll_states)

    # ------------------------------------------------------------------
    def _poll_states(self) -> None:
        changed = False
        for kind, pid in list(self._pids.items()):
            if pid_alive(pid):
                continue
            self._pids.pop(kind, None)
            card = self._card_for(kind)
            if card is not None:
                card.set_state(
                    EXITED,
                    "— window closed" if card._state == RUNNING
                    else "— exited quickly (run the command to see why)")
            changed = True
        if changed:
            self._refresh_summary()

    def _refresh_summary(self) -> None:
        running = len(self._pids)
        if running:
            self.summary_label.setText(
                f"{running} device window(s) running — they stay open "
                "independently of this launcher.")
        else:
            self.summary_label.setText("No device windows running.")

    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """主窗口关闭时：**不**结束设备窗口。

        设备 GUI 是独立进程，它的生命周期由那个窗口决定。从入口强行
        终止会绕过它的 closeEvent（Linux 上 SIGTERM 直接杀进程），
        设备就没机会 halt / 归零 —— 那比留着窗口危险得多。
        """
        try:
            self._timer.stop()
        except Exception:                            # noqa: BLE001
            pass
