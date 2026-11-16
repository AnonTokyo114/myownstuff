"""Oscilloscope 的独立入口（界面尚未实现，目前是说明页）。

用法：
    python -m devicegui.scope_app
"""

import sys

from devicegui.base import run_device_app

if __name__ == "__main__":
    sys.exit(run_device_app("scope"))
