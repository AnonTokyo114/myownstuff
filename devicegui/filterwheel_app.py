"""Thorlabs FW102C 滤光片轮的独立入口。

用法：
    python -m devicegui.filterwheel_app
"""

import sys

from devicegui.base import run_device_app

if __name__ == "__main__":
    sys.exit(run_device_app("filterwheel"))
