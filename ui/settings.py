"""用户配置：每台设备上次用的连接参数。

存成项目根的 ``settings.ini``（不是注册表），理由：

  * 文件就在那儿，用户可以直接看、直接改
  * 拷到别的机器时能一起带（但默认被 .gitignore 挡住，不污染仓库）
  * 串口是会变的（USB 转串口每次插拔可能换号），所以记在本地最合适

结构示例::

    [devices/stage/serial]
    port=COM7
    baudrate=115200

    [devices/filterwheel/serial]
    port=COM4
    baudrate=115200
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

#: 项目根目录（ui/settings.py -> 项目根）
ROOT = Path(__file__).resolve().parent.parent
#: 配置文件路径
PATH = ROOT / "settings.ini"


def settings() -> QSettings:
    """打开的 settings.ini（不存在时写入时自动创建）。"""
    return QSettings(str(PATH), QSettings.Format.IniFormat)
