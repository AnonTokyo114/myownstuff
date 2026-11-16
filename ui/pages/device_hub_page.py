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
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QProcess, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from devicegui.base import DEVICE_APPS, ROOT, DeviceApp

#: 卡片状态
IDLE = "idle"
RUNNING = "running"
EXITED = "exited"

#: 多久检查一次子进程是否还活着（毫秒）
POLL_MS = 2000
#: 启动后多久做第一次存活检查（太短会把还在初始化的进程误判成已退出）
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
    """一个设备入口卡片：图标/标题、说明、运行状态、启动按钮。"""

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

        if not app.implemented:
            tag = QLabel("placeholder")
            tag.setObjectName("hubTag")
            head.addWidget(tag)
        layout.addLayout(head)

        summary = QLabel(app.summary)
        summary.setObjectName("pageSubtitle")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        cmd = QLabel(f"python -m {app.module}")
        cmd.setObjectName("hubCommand")
        cmd.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        cmd.setToolTip("Run this in a terminal to start the device on its own")
        layout.addWidget(cmd)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.state_label = QLabel("● Not running")
        self.state_label.setObjectName("pageSubtitle")
        foot.addWidget(self.state_label, 1)

        self.action_btn = QPushButton("Launch")
        self.action_btn.setObjectName("primary")
        self.action_btn.setFixedWidth(110)
        self.action_btn.clicked.connect(
            lambda: self.launch_requested.emit(self.app.key))
        foot.addWidget(self.action_btn)
        layout.addLayout(foot)

        self._apply_state(IDLE)

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
        for index, app in enumerate(DEVICE_APPS.values()):
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

        # 定时检查 PID：设备窗口被关掉后卡片要自己恢复成 Not running
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll_states)
        self._timer.start()

    # ------------------------------------------------------------------
    # 启动
    # ------------------------------------------------------------------
    def launch(self, kind: str) -> None:
        """用**脱离的**子进程启动设备 GUI。

        用 startDetached 而不是 QProcess.start()：QProcess 是 QObject，
        主窗口销毁时它也会被销毁，而 QProcess 析构时会杀掉仍在运行的子
        进程 —— 那等于在退出路径上偷偷 terminate，正是要避免的行为。
        """
        if kind in self._pids:
            return
        app = DEVICE_APPS.get(kind)
        if app is None:
            return

        try:
            ok, pid = QProcess.startDetached(
                sys.executable, ["-m", app.module], str(ROOT))
        except Exception as exc:                     # noqa: BLE001
            self._cards[kind].set_state(EXITED, f"— {exc}")
            return

        if not ok:
            self._cards[kind].set_state(
                EXITED, "— could not start; run the command on the card to see why")
            return

        self._pids[kind] = int(pid)
        self._cards[kind].set_state(RUNNING, f"(pid {pid})")
        self._refresh_summary()

        # 有些失败是"python 起来了但模块不存在"，进程会很快退出。
        # 稍等一下再看一眼，别把这种情况显示成 Running。
        QTimer.singleShot(FIRST_CHECK_MS, self._poll_states)

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    def _poll_states(self) -> None:
        changed = False
        for kind, pid in list(self._pids.items()):
            if pid_alive(pid):
                continue
            self._pids.pop(kind, None)
            card = self._cards.get(kind)
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

        这个页面关闭时只停掉自己的轮询定时器。
        """
        try:
            self._timer.stop()
        except Exception:                            # noqa: BLE001
            pass
