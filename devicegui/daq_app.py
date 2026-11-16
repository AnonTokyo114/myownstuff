"""Data Acquisition 的独立入口。

用法：
    python -m devicegui.daq_app
"""

import sys

from devicegui.base import run_device_app

if __name__ == "__main__":
    sys.exit(run_device_app("daq"))
