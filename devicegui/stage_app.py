"""Stage Controller 的独立入口。

用法：
    python -m devicegui.stage_app

主 GUI 的 Device Interface 页面也是用这条命令把它拉起来的，
所以单独运行和从主界面启动看到的是同一个界面。
"""

import sys

from devicegui.base import run_device_app

if __name__ == "__main__":
    sys.exit(run_device_app("stage"))
