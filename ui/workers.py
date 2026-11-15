"""后台任务工具：把阻塞操作放到线程池，结果回到 Qt 主线程。

给 matrix_page 和 stage_page 共用。

关键点（踩过的坑）：QRunnable 默认 autoDelete=True，线程池会在 run() 结束后
删除它；而 signals 是它的成员，一旦被删回调就再也收不到，界面会永远停在
"Computing…"。所以这里关闭 autoDelete，并在完成前一直持有 worker 的引用。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)


class _Worker(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self.signals = _Signals()
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            self.signals.done.emit(self._fn())
        except Exception as exc:                     # noqa: BLE001
            msg = (str(exc) if isinstance(exc, ValueError)
                   else f"{type(exc).__name__}: {exc}")
            self.signals.error.emit(msg)


#: 持有运行中的 worker，避免被回收；完成后移除
_ACTIVE: set = set()


def run_async(fn, on_done, on_error) -> None:
    """在后台执行 fn()；完成时调用 on_done(result)，失败调用 on_error(msg)。"""
    worker = _Worker(fn)
    _ACTIVE.add(worker)

    def _finish() -> None:
        _ACTIVE.discard(worker)

    def _done(result) -> None:
        _finish()
        on_done(result)

    def _error(msg) -> None:
        _finish()
        on_error(msg)

    worker.signals.done.connect(_done)
    worker.signals.error.connect(_error)
    QThreadPool.globalInstance().start(worker)
