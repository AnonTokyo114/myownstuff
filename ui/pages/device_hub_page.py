"""Device Interface：设备 GUI 的分类启动入口。

这里**不内嵌**任何设备面板。每个设备都是独立的 GUI，可以：

  * 从本页面点 Launch 启动（子进程，互不影响，可同时开多个）
  * 或者在终端里直接运行 ``python -m devicegui.stage_app``

所以主窗口关了，已经启动的设备窗口仍然在跑；反过来某个设备驱动崩了，
也带不走主界面。
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, Signal
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
STARTING = "starting"
RUNNING = "running"
EXITED = "exited"


class DeviceCard(QFrame):
    """一个设备入口卡片：图标/标题、说明、运行状态、启动按钮。"""

    launch_requested = Signal(str)
    stop_requested = Signal(str)

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
        layout.addWidget(cmd)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.state_label = QLabel("● Not running")
        self.state_label.setObjectName("pageSubtitle")
        foot.addWidget(self.state_label, 1)

        self.action_btn = QPushButton("Launch")
        self.action_btn.setObjectName("primary")
        self.action_btn.setFixedWidth(96)
        self.action_btn.clicked.connect(self._on_action)
        foot.addWidget(self.action_btn)
        layout.addLayout(foot)

        self._apply_state(IDLE)

    # ------------------------------------------------------------------
    def _on_action(self) -> None:
        if self._state in (STARTING, RUNNING):
            self.stop_requested.emit(self.app.key)
        else:
            self.launch_requested.emit(self.app.key)

    def _apply_state(self, state: str, detail: str = "") -> None:
        self._state = state
        colors = {
            IDLE: ("● Not running", "#9aa3ae"),
            STARTING: ("● Starting…", "#b26a00"),
            RUNNING: ("● Running", "#2e7d32"),
            EXITED: ("● Exited", "#9aa3ae"),
        }
        text, color = colors.get(state, colors[IDLE])
        if detail:
            text = f"{text}  {detail}"
        self.state_label.setText(text)
        self.state_label.setStyleSheet(f"color:{color};")

        if state in (STARTING, RUNNING):
            self.action_btn.setText("Stop")
            self.action_btn.setObjectName("ghost")
            self.action_btn.setEnabled(state == RUNNING)
        else:
            self.action_btn.setText("Launch")
            self.action_btn.setObjectName("primary")
            self.action_btn.setEnabled(True)
        # 改了 objectName 需要重新应用样式
        self.action_btn.style().unpolish(self.action_btn)
        self.action_btn.style().polish(self.action_btn)

    def set_state(self, state: str, detail: str = "") -> None:
        self._apply_state(state, detail)


class DeviceHubPage(QWidget):
    page_title = "Device Interface"

    def __init__(self) -> None:
        super().__init__()
        self._procs: dict[str, QProcess] = {}
        self._cards: dict[str, DeviceCard] = {}
        #: 正在由本页主动停止的 kind（用来区分「主动停」和「意外退出」）
        self._stopping: set[str] = set()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(10)

        title = QLabel("Device Interface")
        title.setObjectName("pageTitle")
        outer.addWidget(title)

        subtitle = QLabel(
            "Pick an instrument below. Each one opens in its own window, and "
            "every device GUI can also be started on its own from a terminal — "
            "the command is shown on each card.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)

        grid = QGridLayout()
        grid.setSpacing(10)
        for index, app in enumerate(DEVICE_APPS.values()):
            card = DeviceCard(app)
            card.launch_requested.connect(self.launch)
            card.stop_requested.connect(self.stop)
            grid.addWidget(card, index // 2, index % 2)
            self._cards[app.key] = card
        outer.addLayout(grid)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.summary_label = QLabel("No device windows running.")
        self.summary_label.setObjectName("pageSubtitle")
        foot.addWidget(self.summary_label, 1)

        self.stop_all_btn = QPushButton("Stop all")
        self.stop_all_btn.setObjectName("ghost")
        self.stop_all_btn.setEnabled(False)
        self.stop_all_btn.clicked.connect(self.stop_all)
        foot.addWidget(self.stop_all_btn)
        outer.addLayout(foot)

        outer.addStretch(1)

    # ------------------------------------------------------------------
    # 启动 / 停止
    # ------------------------------------------------------------------
    def launch(self, kind: str) -> None:
        """用子进程启动一个设备 GUI。"""
        if kind in self._procs:
            return
        app = DEVICE_APPS.get(kind)
        if app is None:
            return

        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments(["-m", app.module])
        proc.setWorkingDirectory(str(ROOT))

        env = QProcessEnvironment.systemEnvironment()
        env.insert("QT_API", "pyside6")
        proc.setProcessEnvironment(env)

        proc.started.connect(lambda k=kind: self._on_started(k))
        proc.errorOccurred.connect(lambda err, k=kind: self._on_error(k, err))
        proc.finished.connect(
            lambda code, status, k=kind: self._on_finished(k, code, status))

        self._procs[kind] = proc
        self._cards[kind].set_state(STARTING)
        self._refresh_summary()
        proc.start()

    def stop(self, kind: str) -> None:
        """请求子进程退出；不响应则强杀。"""
        proc = self._procs.get(kind)
        if proc is None:
            return
        if proc.state() == QProcess.ProcessState.NotRunning:
            self._on_finished(kind, proc.exitCode(), proc.exitStatus())
            return
        self._stopping.add(kind)
        proc.terminate()
        if not proc.waitForFinished(2500):
            proc.kill()
            proc.waitForFinished(1500)

    def stop_all(self) -> None:
        for kind in list(self._procs):
            self.stop(kind)

    # ------------------------------------------------------------------
    def _on_started(self, kind: str) -> None:
        proc = self._procs.get(kind)
        pid = int(proc.processId()) if proc is not None else 0
        self._cards[kind].set_state(RUNNING, f"(pid {pid})" if pid else "")
        self._refresh_summary()

    def _on_error(self, kind: str, error) -> None:
        card = self._cards.get(kind)
        if card is None:
            return
        if error == QProcess.ProcessError.FailedToStart:
            card.set_state(EXITED, "— failed to start")
            self._procs.pop(kind, None)
            self._refresh_summary()

    def _on_finished(self, kind: str, code: int, _status) -> None:
        self._procs.pop(kind, None)
        stopped_by_us = kind in self._stopping
        self._stopping.discard(kind)
        card = self._cards.get(kind)
        if card is not None:
            if stopped_by_us:
                card.set_state(EXITED, "— stopped")
            elif code:
                card.set_state(EXITED, f"— exit code {code}")
            else:
                card.set_state(EXITED)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        running = len([p for p in self._procs.values()
                       if p.state() != QProcess.ProcessState.NotRunning])
        if running:
            self.summary_label.setText(f"{running} device window(s) running.")
            self.stop_all_btn.setEnabled(True)
        else:
            self.summary_label.setText("No device windows running.")
            self.stop_all_btn.setEnabled(False)

    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        """主窗口关闭时，结束所有由本页启动的设备窗口。

        这些窗口本身是独立进程，不主动收掉会变成孤儿。想让它继续跑的话
        可以在终端里单独启动 —— 那条路不经过这里。
        """
        self.stop_all()
