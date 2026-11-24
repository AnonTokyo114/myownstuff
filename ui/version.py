"""应用版本号 —— 只在这里改。

侧栏和 Home 页脚都从这里取，避免再出现 v0.2 / v0.3 打架的情况。
"""

from __future__ import annotations

#: 主版本号。改这里就够了，界面各处引用同一个常量。
APP_VERSION = "0.4"

#: 侧栏底部那行
SIDEBAR_HINT = f"v{APP_VERSION} · PySide6"

#: Home 页脚（顺便说明数据不出本机）
HOME_FOOTER = f"v{APP_VERSION} · PySide6 · all computation local"
