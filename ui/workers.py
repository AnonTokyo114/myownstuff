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


# ======================================================================
# 设备任务会话
# ======================================================================
#: work() 在发现会话已过期时返回这个哨兵，done 里据此跳过
_CANCELLED = object()


class DeviceTaskSession:
    """一次设备操作的会话守卫。

    设备页面反复写同一段东西：提交时记下「代次 + 设备」，执行前比一次，
    回调回来再比一次，不一致就丢弃。这段逻辑散在各页面里，每加一个操作
    就有一次漏掉某个分支的机会。

    这里把它收成一处。宿主（页面）只需要提供两个**现有**的真值：

    ``owner._session``
        整数代次。连接、断开、关闭窗口时递增 —— 递增之后所有在途任务
        自动过期，不需要额外通知。
    ``owner.device``
        当前绑定的设备，可能是 ``None``。

    用法::

        self._tasks = DeviceTaskSession(self)

        self._tasks.submit(
            lambda: dev.snapshot(),
            on_done=self._apply_state,
            on_failed=lambda msg: self._set_busy(False, msg),
        )

    ``on_stale`` 在任务过期时调用（可选）。**默认什么都不做** ——
    会话都换了，旧任务不应再去动界面或忙碌状态，那是新会话的事。
    """

    def __init__(self, owner) -> None:
        self._owner = owner

    # ------------------------------------------------------------------
    def generation(self) -> int:
        """当前代次（宿主递增它就作废在途任务）。"""
        return self._owner._session

    def _snapshot(self):
        return self._owner._session, self._owner.device

    def _still_valid(self, generation, device) -> bool:
        return (generation == self._owner._session
                and device is not None
                and device is self._owner.device)

    # ------------------------------------------------------------------
    def submit(self, work, *, on_done=None, on_failed=None,
               on_stale=None, still_valid=None) -> None:
        """提交一次设备操作。

        ``work()`` 在后台线程跑（不许碰 Qt）；``on_done`` / ``on_failed``
        回到主线程。三者都会先校验会话，过期就静默丢弃。

        ``still_valid``
            调用方自己的额外校验（返回 bool）。有些面板除了「连接有没有变」
            还要管「同一连接内操作有没有变」—— 例如脉冲输出每次
            Start / Stop 都递增一个操作序号，旧 Stop 的回包晚于新 Start
            时不能把界面改成 Stopped。会话号只在连接变化时递增，覆盖不了
            这一层，所以留这个口子。

            注意它只管**回调**和**执行前**的有效性；已经在硬件上跑起来的
            动作要靠设备自己的 token 去取消，不是这里能拦住的。
        """
        generation, device = self._snapshot()

        def still_ok() -> bool:
            if not self._still_valid(generation, device):
                return False
            if still_valid is not None and not still_valid():
                return False
            return True

        def runner():
            # 执行前校验：排队期间可能已经断开 / 重连 / 关窗 / 又按了一次
            if not still_ok():
                return _CANCELLED
            return work()

        def done(value) -> None:
            if value is _CANCELLED or not still_ok():
                if on_stale is not None:
                    on_stale()
                return
            if on_done is not None:
                on_done(value)

        def failed(msg: str) -> None:
            if not still_ok():
                if on_stale is not None:
                    on_stale()
                return
            if on_failed is not None:
                on_failed(msg)

        run_async(runner, done, failed)
