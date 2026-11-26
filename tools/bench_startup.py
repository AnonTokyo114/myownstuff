"""测量设备窗口「新进程启动 -> 可交互」的耗时。

用法
----
    python tools/bench_startup.py                 # 全部设备，各 5 次
    python tools/bench_startup.py --runs 9
    python tools/bench_startup.py --kinds stage daq --single-file

方法
----
父进程从 Popen 前开始计时，子进程启动窗口、处理首轮事件后打印 READY 并退出。
**先预热一轮**再采样，否则第一次的磁盘/字体缓存冷启动会把中位数拉偏。

结果只代表本机。绝对值受 CPU、磁盘缓存、系统负载影响很大；要比较改动前后，
应该在**同一台机器**上交替采样（改一次源码、测一遍，再换回去测一遍）。
"""

from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.as_posix()
PY = str(Path(sys.executable))


CHILD = r'''
import os
import sys

ROOT = r"@ROOT@"
os.environ.setdefault("QT_API", "pyside6")
sys.path.insert(0, ROOT)

kind = sys.argv[1]

from devicegui.base import DRAWS_PLOTS
if kind in DRAWS_PLOTS:
    try:
        import matplotlib
        matplotlib.use("QtAgg")
    except ImportError:
        pass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
app = QApplication(sys.argv[:1])

from devicegui.base import create_device_window
window = create_device_window(kind)
window.resize(1120, 840)
try:
    from ui.theme import apply_theme
    apply_theme(window)
except Exception:
    pass
window.show()
for _ in range(5):
    app.processEvents()

print(f"MATPLOTLIB {int('matplotlib' in sys.modules)}")
print("READY")
'''


def bench_device(kind: str, runs: int) -> dict | None:
    child = Path(ROOT) / "tools" / "_bench_child.py"
    child.write_text(CHILD.replace("@ROOT@", ROOT),
                     encoding="utf-8", newline="\n")
    env = {**os.environ, "PYTHONUTF8": "1", "QT_API": "pyside6",
           "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen")}

    def once() -> tuple[float, bool, bool]:
        t0 = time.perf_counter()
        proc = subprocess.run(
            [PY, str(child), kind], cwd=ROOT, capture_output=True,
            text=True, encoding="utf-8", errors="replace", env=env,
            timeout=180,
        )
        dt = time.perf_counter() - t0
        out = proc.stdout or ""
        return dt, proc.returncode == 0, "MATPLOTLIB 1" in out

    try:
        once()                                   # 预热
        times = []
        mpl = False
        for _ in range(runs):
            dt, ok, loaded = once()
            if ok:
                times.append(dt)
                mpl = mpl or loaded
        if not times:
            return None
        return {"median": statistics.median(times), "min": min(times),
                "max": max(times), "runs": len(times), "matplotlib": mpl}
    finally:
        child.unlink(missing_ok=True)


def bench_single_file(runs: int) -> dict | None:
    """stage_standalone.py 的启动耗时。"""
    standalone = Path(ROOT) / "stage_standalone.py"
    if not standalone.exists():
        return None
    here = standalone.parent
    env = {**os.environ, "PYTHONUTF8": "1", "QT_API": "pyside6",
           "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen")}
    child = here / "_bench_sa.py"
    child.write_text(f'''
import os, sys, importlib.util
os.environ.setdefault("QT_API", "pyside6")
path = r"{standalone}"
spec = importlib.util.spec_from_file_location("sa", path)
mod = importlib.util.module_from_spec(spec)
sys.modules["sa"] = mod
spec.loader.exec_module(mod)
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
app = QApplication(sys.argv[:1])
window = mod.StagePage()
window.show()
for _ in range(5):
    app.processEvents()
print("MATPLOTLIB " + str(int("matplotlib" in sys.modules)))
print("READY")
''', encoding="utf-8", newline="\n")

    def once():
        t0 = time.perf_counter()
        proc = subprocess.run([PY, str(child)], cwd=str(here),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env,
                              timeout=180)
        return (time.perf_counter() - t0, proc.returncode == 0,
                "MATPLOTLIB 1" in (proc.stdout or ""))

    try:
        once()
        times, mpl = [], False
        for _ in range(runs):
            dt, ok, loaded = once()
            if ok:
                times.append(dt)
                mpl = mpl or loaded
        if not times:
            return None
        return {"median": statistics.median(times), "min": min(times),
                "max": max(times), "runs": len(times), "matplotlib": mpl}
    finally:
        child.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--kinds", nargs="+",
                        default=["stage", "filterwheel", "daq"])
    parser.add_argument("--single-file", action="store_true",
                        help="顺带测 stage_standalone.py")
    args = parser.parse_args(argv)

    print(f"  解释器: {PY}")
    print(f"  每项 {args.runs} 次（另加 1 次预热）\n")

    for kind in args.kinds:
        r = bench_device(kind, args.runs)
        if r is None:
            print(f"  {kind:<14} 测量失败")
            continue
        mpl = "是" if r["matplotlib"] else "否"
        print(f"  {kind:<14} 中位 {r['median']*1000:7.0f} ms   "
              f"范围 {r['min']*1000:.0f}–{r['max']*1000:.0f} ms   "
              f"matplotlib: {mpl}")

    if args.single_file:
        r = bench_single_file(args.runs)
        if r is None:
            print(f"  {'single-file':<14} 测量失败")
        else:
            mpl = "是" if r["matplotlib"] else "否"
            print(f"  {'single-file':<14} 中位 {r['median']*1000:7.0f} ms   "
                  f"范围 {r['min']*1000:.0f}–{r['max']*1000:.0f} ms   "
                  f"matplotlib: {mpl}")

    print("\n  这些数字只代表本机；比较改动前后要在同一台机器上交替采样。")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
