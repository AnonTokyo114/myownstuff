"""项目回归测试。

跑法（在项目根）：

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

只依赖标准库的 unittest，不需要额外装 pytest。
GUI 相关的测试会在没有图形环境时跳过（见各文件里的 skipUnless）。

崩溃栈
------
报告记录过一次全仓测试进程以 139（SIGSEGV）退出，当时没开 faulthandler，
日志只有最后一个测试名，没有崩溃栈。这里在导入测试包时无条件打开它：
只在收到致命信号时才输出，正常运行没有额外开销。

线程池
------
后台任务池里还有任务就退出解释器，是这类"偶发 139"的常见来源之一。
tearDown 里可以用 wait_idle() 等它清空。
"""

from __future__ import annotations

import faulthandler
import sys

faulthandler.enable(file=sys.stderr, all_threads=True)


def wait_idle(timeout_ms: int = 5000) -> bool:
    """等 QThreadPool 里的任务结束。返回 True 表示在超时前清空。"""
    try:
        from PySide6.QtCore import QThreadPool
    except ImportError:
        return True
    return QThreadPool.globalInstance().waitForDone(timeout_ms)
