"""测量设备窗口「新进程启动 -> 可交互」的耗时。

用法
----
    python tools/bench_startup.py                 # 全部设备，各 5 次
    python tools/bench_startup.py --runs 9
    python tools/bench_startup.py --kinds stage daq --single-file

测量协议
--------
子进程在**窗口显示、首轮事件处理完**之后打印一行 ``READY`` 并 flush；
父进程用 Popen 逐行读，读到 READY 时停表，**然后**才等进程退出并检查退出码。

这一点很关键：如果用 ``subprocess.run`` 等进程结束再停表，计时的就是**整个
进程生命周期**（解释器销毁、Qt 清理全算进去），而不是「到可交互」。

没收到 READY 一律算失败样本，不会当成成功收录。

绝对值
------
只代表本机。受 CPU、磁盘缓存、字体缓存、系统负载影响很大；比较改动前后要
在**同一台机器**上交替采样（改一次测一遍、换回去再测一遍），并先预热一轮。
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
PY = sys.executable

#: 读 READY 的超时（秒）。启动再慢也不该超过这个。
READY_TIMEOUT = 120.0


# ======================================================================
# 子进程源码
# ======================================================================
DEVICE_CHILD = r'''
import os
import sys

ROOT = r"@ROOT@"
os.environ.setdefault("QT_API", "pyside6")
sys.path.insert(0, ROOT)

kind = sys.argv[1]

# 和 run_device_app 完全一致地准备绘图后端
from devicegui.base import DRAWS_PLOTS
if kind in DRAWS_PLOTS:
    try:
        import matplotlib
        matplotlib.use("QtAgg")
    except ImportError:
        pass

from PySide6.QtCore import Qt, QTimer
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

# 首帧之后才算「可交互」：singleShot(0) 排在首轮事件处理之后，
# 此时窗口已经真正画过一帧。
def _ready():
    app.processEvents()
    print(f"MATPLOTLIB {int('matplotlib' in sys.modules)}", flush=True)
    print("READY", flush=True)
    app.quit()

QTimer.singleShot(0, _ready)
app.exec()
'''

SA_CHILD = r'''
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("QT_API", "pyside6")

path = os.path.join(HERE, "stage_standalone.py")
spec = importlib.util.spec_from_file_location("sa", path)
mod = importlib.util.module_from_spec(spec)
sys.modules["sa"] = mod
spec.loader.exec_module(mod)

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

# 包住真正的 StagePage：main() 的初始化（含它对 matplotlib 的处理）照旧。
# 探针挂在**子类重写的 showEvent** 上 —— 给实例赋 showEvent 在 PySide6 里
# 不可靠，虚函数只有子类重写才一定生效。
orig_page = mod.StagePage


class Probe(orig_page):
    _probe_armed = False

    def showEvent(self, event):
        super().showEvent(event)
        if Probe._probe_armed:
            return
        Probe._probe_armed = True
        from PySide6.QtCore import QTimer as _T
        from PySide6.QtWidgets import QApplication as _A
        app = _A.instance()

        def _ready():
            app.processEvents()
            print(f"MATPLOTLIB {int('matplotlib' in sys.modules)}", flush=True)
            print("READY", flush=True)
            app.quit()

        # 排到首轮事件之后：此时窗口已真正画过一帧
        _T.singleShot(0, _ready)


mod.StagePage = Probe
sys.argv = [path]
sys.exit(mod.main())
'''


# ======================================================================
def _measure(child_code: str, name: str, runs: int,
             cwd: str, extra_args: list[str] | None = None) -> dict | None:
    """跑 runs 次 + 1 次预热；返回中位/范围/是否加载 matplotlib。"""
    child_path = Path(cwd) / f"_bench_{name}.py"
    child_path.write_text(child_code, encoding="utf-8", newline="\n")
    env = {**os.environ, "PYTHONUTF8": "1", "QT_API": "pyside6",
           "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen")}
    cmd = [PY, str(child_path)] + (extra_args or [])

    def once() -> tuple[float | None, bool]:
        """返回 (到 READY 的秒数, 是否加载 matplotlib)；没 READY 则 None。"""
        t0 = time.perf_counter()
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=env,
        )
        deadline = t0 + READY_TIMEOUT
        elapsed = None
        mpl = False
        try:
            while True:
                if time.perf_counter() > deadline:
                    break
                line = proc.stdout.readline()
                if not line:
                    break
                if line.startswith("MATPLOTLIB"):
                    mpl = line.split()[-1] == "1"
                elif line.strip() == "READY":
                    elapsed = time.perf_counter() - t0
                    break
        finally:
            # 停表之后才等它清理；退出码也要看
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
            if proc.stdout:
                proc.stdout.close()
            if proc.stderr:
                proc.stderr.close()
        ok = proc.returncode == 0 and elapsed is not None
        return (elapsed if ok else None), mpl

    try:
        once()                                     # 预热
        times, mpl_seen = [], False
        for _ in range(runs):
            dt, mpl = once()
            if dt is not None:
                times.append(dt)
                mpl_seen = mpl_seen or mpl
        if not times:
            return None
        return {"median": statistics.median(times), "min": min(times),
                "max": max(times), "runs": len(times), "matplotlib": mpl_seen}
    finally:
        child_path.unlink(missing_ok=True)


def bench_device(kind: str, runs: int) -> dict | None:
    return _measure(DEVICE_CHILD.replace("@ROOT@", ROOT), f"dev_{kind}",
                    runs, cwd=ROOT, extra_args=[kind])


def bench_single_file(runs: int) -> dict | None:
    standalone = Path(ROOT) / "stage_standalone.py"
    if not standalone.exists():
        return None
    return _measure(SA_CHILD, "sa", runs, cwd=str(standalone.parent))


# ======================================================================
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--kinds", nargs="+",
                        default=["stage", "filterwheel", "daq"])
    parser.add_argument("--single-file", action="store_true",
                        help="顺带测 stage_standalone.py 的真实 main()")
    args = parser.parse_args(argv)

    print(f"  解释器: {PY}")
    print(f"  每项 {args.runs} 次（另加 1 次预热）；计时到子进程打印 READY\n")

    for kind in args.kinds:
        r = bench_device(kind, args.runs)
        if r is None:
            print(f"  {kind:<14} 测量失败（没收到 READY 或退出码非零）")
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
