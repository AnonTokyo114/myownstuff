"""项目回归测试。

跑法（在项目根）：

    .venv\\Scripts\\python.exe -X faulthandler -m unittest discover -s tests -v

只依赖标准库的 unittest，不需要额外装 pytest。
GUI 相关的测试会在没有图形环境时跳过（见各文件里的 skipUnless）。

崩溃栈
------
报告记录过一次全仓测试进程以 139（SIGSEGV）退出，当时没开 faulthandler，
日志只有最后一个测试名，没有崩溃栈。

这里在导入包时打开它，但**这条路径在默认入口下不会执行**：
``python -m unittest discover -s tests`` 把 ``test_*.py`` 当顶层模块导入，
不导入 ``tests`` 包本身。所以上面的推荐入口显式加了 ``-X faulthandler``
（解释器启动即生效），``-t .`` 也可以让包先被导入。
两种方式都能覆盖"测试导入阶段"。

线程池与主线程回调
------------------
``wait_idle()`` 做两件事，缺一不可：

  1. 等 QThreadPool 里的任务结束
  2. 把排到主线程的事件处理掉 —— 任务的信号是 queued 连接，
     线程结束不等于回调已经执行，``_ACTIVE`` 可能还有引用

只等 (1) 会在"线程已结束、回调未交付"的窗口里返回 True，那不是清理完成。
"""

from __future__ import annotations

import faulthandler
import sys
import time

faulthandler.enable(file=sys.stderr, all_threads=True)


def wait_idle(timeout_ms: int = 5000) -> bool:
    """等后台任务结束**并且**主线程回调处理完。

    返回 True 表示在超时前两者都清空了；False 表示仍然有任务或回调
    没处理完 —— 调用方应当把它当作失败信号，而不是忽略。
    """
    try:
        from PySide6.QtCore import QThreadPool
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return True

    pool = QThreadPool.globalInstance()
    if not pool.waitForDone(timeout_ms):
        return False

    app = QApplication.instance()
    if app is None:
        return pool.activeThreadCount() == 0

    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        if pool.activeThreadCount() == 0:
            # 事件已处理一轮；再处理一次确认没有新排进来的
            app.processEvents()
            if pool.activeThreadCount() == 0:
                return True
        time.sleep(0.005)
    return False
