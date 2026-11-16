"""独立运行的设备 GUI 集合。

每个 ``*_app.py`` 都是 ``python -m devicegui.<name>`` 的入口，
主 GUI 也用同样的方式把它们拉起来（见 ui/pages/device_hub_page.py）。
"""

from devicegui.base import DEVICE_APPS, DeviceApp, create_device_window, run_device_app

__all__ = ["DEVICE_APPS", "DeviceApp", "create_device_window", "run_device_app"]
