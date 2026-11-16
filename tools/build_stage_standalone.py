"""把 Stage Controller 的依赖内联成单个文件，方便拷到别的机器。

生成 stage_standalone.py：一个文件，包含
  core/transport.py, core/stage.py, core/nidaq.py, core/devices.py,
  ui/workers.py, ui/widgets/device_selector.py, ui/pages/stage_page.py
以及一个 main 入口。

用法（在项目根）：
    python tools/build_stage_standalone.py

产出的文件依赖仍然只是 PySide6 + pyserial，但不再需要项目结构。
注意：它是**生成物**，不要直接改；改完主项目代码后重新跑一次。
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 按依赖顺序排列（后面的可以用前面的名字）
MODULES = [
    ("core/transport.py", "传输层：Serial / LAN / GPIB / Local"),
    ("core/stage.py", "ASI LX-4000 位移台驱动"),
    ("core/nidaq.py", "NI-DAQ 模拟输出（Stage 页面不直接用，但设备注册表引用它）"),
    ("core/devices.py", "设备注册表"),
    ("ui/theme.py", "全局样式表（否则危险按钮没有样式）"),
    ("ui/workers.py", "后台任务"),
    ("ui/widgets/device_selector.py", "设备选择 + 连接组件"),
    ("ui/pages/stage_page.py", "Stage Controller 页面"),
]

#: 这些模块前缀的导入在合并后必须去掉（名字已经在同一文件里）
INTERNAL_PREFIXES = ("core", "ui", "devicegui", "")

#: 模块名前缀替换（合并后不再有 core.xxx / ui.xxx 命名空间）
REPLACEMENTS = [
    ("stage_mod.ASI_lx_4000", "ASI_lx_4000"),
    ("nidaq_mod.NIDaqDevice", "NIDaqDevice"),
    ("nidaq_mod.PlaceholderDaq", "PlaceholderDaq"),
    ("nidaq_mod.NIDAQMX_AVAILABLE", "NIDAQMX_AVAILABLE"),
    ("nidaq_mod.driver_available", "driver_available"),
    ("nidaq_mod.list_devices", "list_devices"),
    ("tr_mod.make_transport", "make_transport"),
    ("tr_mod.available_serial_ports", "available_serial_ports"),
    ("tr_mod.list_visa_resources", "list_visa_resources"),
    ("tr_mod.visa_available", "visa_available"),
    ("tr_mod.TransportError", "TransportError"),
    ("tr_mod.LocalTransport", "LocalTransport"),
]

HEADER = '''"""ASI LX-4000 Stage Controller —— 单文件版（自动生成，勿直接编辑）。

来源：https://github.com/AnonTokyo114/myownstuff
生成：python tools/build_stage_standalone.py

这是一个**自包含**的副本：把整个项目的依赖内联进了一个文件，
方便直接拷到别的机器上，不需要项目目录结构。

运行需要：
    pip install PySide6-Essentials pyserial

用法：
    python stage_standalone.py

与主项目的差异：只保留 Stage Controller，没有主 GUI 的其它页面。
改了主项目代码后请重新生成，不要在这里手改。
"""

from __future__ import annotations

import os
import sys

'''

FOOTER = '''

# ======================================================================
#  入口
# ======================================================================
def main() -> int:
    os.environ.setdefault("QT_API", "pyside6")

    try:
        import matplotlib
        matplotlib.use("QtAgg")
    except ImportError:
        pass

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Personal Research")
    app.setOrganizationName("personal-research")

    window = StagePage()
    window.setWindowTitle("Stage Controller — Personal Research")
    window.resize(1120, 840)
    apply_theme(window)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
'''

STDLIB = {
    "os", "sys", "re", "time", "math", "socket", "select", "threading",
    "logging", "dataclasses", "pathlib", "typing", "abc", "importlib",
    "functools", "itertools", "collections", "csv", "json", "io", "copy",
    "random", "string", "warnings", "datetime", "subprocess", "shutil",
    "tempfile", "traceback", "unicodedata",
}


def _is_internal(module: str | None, level: int) -> bool:
    if level and level > 0:
        return True                       # 相对导入，合并后无效
    if not module:
        return False
    return module.split(".")[0] in INTERNAL_PREFIXES


def analyse(text: str):
    """返回 (要保留的导入块列表, 要删除的行号集合)。"""
    tree = ast.parse(text)
    lines = text.splitlines()

    keep: list[str] = []
    drop_lines: set[int] = set()

    # 只处理**模块顶层**的 import —— 函数体里的 import 保持原样，
    # 否则把它提到文件头部会破坏缩进。
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        start = node.lineno - 1
        end = node.end_lineno or node.lineno
        block = "\n".join(lines[start:end])

        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            # HEADER 里已经有一份，重复出现会报「must occur at the beginning」
            drop_lines.update(range(start, end))
            continue

        if _is_internal(getattr(node, "module", None),
                        getattr(node, "level", 0) or 0):
            drop_lines.update(range(start, end))
        else:
            keep.append(block)

    return keep, drop_lines


def strip_imports(text: str, drop_lines: set[int]) -> str:
    return "\n".join(
        line for i, line in enumerate(text.splitlines()) if i not in drop_lines)


def import_kind(block: str) -> str:
    m = re.match(r"^(?:import|from)\s+([\w.]+)", block)
    if not m:
        return "third"
    return "std" if m.group(1).split(".")[0] in STDLIB else "third"


def main() -> int:
    chunks = []
    std_blocks: list[str] = []
    third_blocks: list[str] = []

    for rel, title in MODULES:
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")

        keep, drop = analyse(text)
        for block in keep:
            (std_blocks if import_kind(block) == "std"
             else third_blocks).append(block)

        body = strip_imports(text, drop)
        # 去掉原文件顶部的模块 docstring
        body = re.sub(r'^"""[\s\S]*?"""\s*', "", body, count=1)

        for old, new in REPLACEMENTS:
            body = body.replace(old, new)
        # 兜底：所有 `xxx_mod.` 别名前缀都去掉（合并后名字已在同一命名空间）
        body = re.sub(r"\b(?:dev_mod|tr_mod|stage_mod|nidaq_mod|st)\.", "", body)

        bar = "=" * 68
        chunks.append(
            f"\n# {bar}\n#  {title}   （源：{rel}）\n# {bar}\n"
            + body.strip("\n") + "\n")

    def dedupe(blocks: list[str]) -> list[str]:
        seen, out = set(), []
        for b in blocks:
            key = b.strip()
            if key in seen:
                continue
            seen.add(key)
            out.append(b)
        return out

    std = dedupe(std_blocks)
    third = dedupe(third_blocks)

    import_block = ""
    if std:
        import_block += "\n".join(std) + "\n"
    if third:
        import_block += "\n" + "\n".join(third) + "\n"

    output = HEADER + import_block + "\n" + "\n".join(chunks) + FOOTER

    target = ROOT / "stage_standalone.py"
    target.write_text(output, encoding="utf-8", newline="\n")
    print(f"  已生成 {target}")
    print(f"  {len(output.splitlines())} 行, "
          f"{len(output.encode('utf-8')) / 1024:.0f} KB")
    print(f"  导入: {len(std)} 标准库, {len(third)} 第三方")
    return 0


if __name__ == "__main__":
    sys.exit(main())
