"""CSV 绘图后端：读表、列信息、series 构造、绘制。

从 csv_plotter.py（tkinter 版）提取与界面无关的部分。
绘制函数直接接收 matplotlib 的 Figure/Axes，所以完全可以在没有 GUI 的
环境里测试（Agg 后端即可）。

原版把「用户选了什么」和「怎么画」揉在 plot_data 里；这里拆成
  PlotSpec  —— 所有绘图参数的纯数据容器
  render()  —— 按 spec 把 series 画到 Figure 上
界面只负责把控件值填进 PlotSpec。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from matplotlib.ticker import MultipleLocator

#: 支持的绘图类型
PLOT_TYPES = ["line", "scatter", "bar", "step", "stem"]

#: 标记与线型（与原版一致）
MARKERS = ["", ".", "o", "s", "^", "v", "D", "x", "+", "*"]
LINESTYLES = ["-", "--", "-.", ":", "None"]

#: 图例位置
LEGEND_LOCS = [
    "best", "upper right", "upper left", "lower right", "lower left",
    "upper center", "lower center", "center left", "center right", "center",
]


# ======================================================================
# 读表
# ======================================================================
#: 嗅探分隔符时读的字节数
SNIFF_BYTES = 8192
#: 候选分隔符（按常见程度排）
CANDIDATE_SEPS = (",", ";", "\t", "|")


def _count_unquoted(line: str, sep: str) -> int:
    """数一行里**引号外**的 sep 个数。

    直接 ``line.count(sep)`` 会数到引号里面的字符：
    ``"time,stamp";value`` 用逗号去数也会得到 1，于是把分号文件误判成
    逗号文件、读成 1 列而**不报错**（C 引擎照样"成功"）。所以这里按
    引号状态跳过字段内容，并把 ``""`` 当作转义引号。
    """
    count = 0
    in_quotes = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            if in_quotes and i + 1 < len(line) and line[i + 1] == '"':
                i += 2                    # 转义的双引号，留在字段里
                continue
            in_quotes = not in_quotes
        elif ch == sep and not in_quotes:
            count += 1
        i += 1
    return count


def _sniff_sep(path: str) -> str | None:
    """从文件开头猜分隔符；猜不出来返回 None。

    要求「前几行里每行**引号外**的该字符个数一致且非零」。跨行的
    引号字段会破坏这个前提，那种情况交给回退路径。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            sample = fh.read(SNIFF_BYTES)
    except OSError:
        return None
    if not sample.strip():
        return None
    lines = [ln for ln in sample.splitlines() if ln.strip()][:5]
    if not lines:
        return None

    # 引号没闭合（跨行字段）就别猜了
    if sample.count('"') % 2:
        return None

    best: str | None = None
    best_score = 0
    for sep in CANDIDATE_SEPS:
        counts = [_count_unquoted(ln, sep) for ln in lines]
        if counts[0] == 0 or len(set(counts)) != 1:
            continue                      # 没出现 / 每行个数不一致
        score = counts[0] * 10 + len(lines)
        if score > best_score:
            best, best_score = sep, score
    return best


def read_table(path: str) -> pd.DataFrame:
    """读 CSV/TSV。

    先用一小段样本嗅探分隔符，再用 **C 引擎**读主体。``sep=None`` 只能
    配 python 引擎，在 200k×4 的文件上大约慢一个数量级（~500 ms 对
    ~67 ms）。

    嗅探失败、或者 C 引擎读不动（引号里有分隔符之类），一律退回原来的
    ``sep=None + python`` 自动识别 —— 兼容性和以前一样，只是常见情况快了。
    """
    if path.lower().endswith(".tsv"):
        try:
            return pd.read_csv(path, sep="\t", engine="c")
        except Exception:                            # noqa: BLE001
            pass                                     # 退回通用路径

    sep = _sniff_sep(path)
    if sep is not None:
        try:
            return pd.read_csv(path, sep=sep, engine="c")
        except Exception:                            # noqa: BLE001
            pass                                     # 嗅探错了，退回自动识别

    try:
        return pd.read_csv(path, sep=None, engine="python")
    except Exception as exc:
        raise ValueError(f"Could not read the file:\n\n{exc}") from exc


def column_names(df: pd.DataFrame) -> list[str]:
    return [str(c) for c in df.columns]


def numeric_columns(df: pd.DataFrame) -> list[str]:
    return [str(c) for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def default_y_candidates(df: pd.DataFrame) -> list[str]:
    """默认勾选的 Y 列：数值列里去掉第一列（通常当 X）。"""
    cols = column_names(df)
    numeric = numeric_columns(df)
    first = cols[0] if cols else None
    candidates = [c for c in numeric if c != first]
    return candidates or numeric or cols[1:2]


def summary(df: pd.DataFrame) -> str:
    return (f"{len(df):,} rows  ×  {len(df.columns)} columns")


# ======================================================================
# 参数
# ======================================================================
@dataclass
class PlotSpec:
    """一次绘图需要的全部参数（界面 -> 画布的纯数据）。"""

    title: str = ""
    xlabel: str = ""
    ylabel: str = ""
    xmin: float | None = None
    xmax: float | None = None
    ymin: float | None = None
    ymax: float | None = None
    xtick: float | None = None
    ytick: float | None = None
    logx: bool = False
    logy: bool = False
    grid: bool = True
    legend: bool = True
    legend_loc: str = "best"
    plot_type: str = "line"
    marker: str | None = None
    linestyle: str | None = "-"
    markersize: float = 6.0
    figsize: tuple[float, float] = (9.0, 6.0)
    separate: bool = False
    sharex: bool = True


#: 一条曲线：(x 数据, y 数据, 图例文字, x 列名, y 列名)
Series = tuple


def build_series(df: pd.DataFrame, *,
                 mode: str = "one_x",
                 x_col: str | None = None,
                 y_cols: list[str] | None = None,
                 pairs: list[tuple[str, str, str]] | None = None) -> list[Series]:
    """把用户的选择变成可绘制的 series 列表。

    mode="one_x" ：一个 X，多列 Y
    mode="pairs" ：若干独立的 (X, Y, 图例) 对
    """
    out: list[Series] = []
    if mode == "one_x":
        if not x_col:
            raise ValueError("Please choose an X column.")
        y_cols = [c for c in (y_cols or []) if c]
        if not y_cols:
            raise ValueError("Please select at least one Y column.")
        for y_col in y_cols:
            out.append((df[x_col], df[y_col], f"{y_col} vs {x_col}",
                        x_col, y_col))
    else:
        for x_col, y_col, label in (pairs or []):
            if not x_col or not y_col:
                continue
            text = (label or "").strip() or f"{y_col} vs {x_col}"
            out.append((df[x_col], df[y_col], text, x_col, y_col))
        if not out:
            raise ValueError("Please add at least one valid X/Y pair.")
    return out


def parse_float(text: str) -> float | None:
    """空文本 -> None；非法 -> ValueError（消息可直接显示）。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f'"{text}" is not a valid number.') from exc


# ======================================================================
# 绘制
# ======================================================================
def draw_series(ax, x, y, label: str, plot_type: str,
                marker, linestyle, markersize: float) -> None:
    """按绘图类型分派到对应的 matplotlib 调用。"""
    if plot_type == "scatter":
        ax.scatter(x, y, marker=marker or "o",
                   s=markersize ** 2, alpha=0.85, label=label)
    elif plot_type == "bar":
        ax.bar(x, y, width=0.8, alpha=0.85, label=label)
    elif plot_type == "step":
        ax.step(x, y, where="mid",
                marker=marker or None,
                linestyle=linestyle or "-",
                markersize=markersize, label=label)
    elif plot_type == "stem":
        ax.stem(x, y, label=label)
    else:  # "line"
        ax.plot(x, y, marker=marker or None, linestyle=linestyle,
                markersize=markersize, label=label)


def render(fig, series: list[Series], spec: PlotSpec) -> None:
    """把 series 画到 fig 上（会先 clear）。

    单个系列出错时整体回滚并抛出 —— 半张图比报错更难排查。
    """
    fig.clear()
    fig.set_size_inches(*spec.figsize)

    separate = spec.separate
    sharex = spec.sharex and separate

    n = len(series) if separate else 1
    axes = []
    first = None
    for i in range(max(1, n)):
        ax = fig.add_subplot(n, 1, i + 1, sharex=first if sharex else None)
        if first is None:
            first = ax
        axes.append(ax)

    marker = spec.marker or None
    linestyle = None if spec.linestyle == "None" else spec.linestyle

    try:
        if separate:
            for ax, (x, y, label, xcol, ycol) in zip(axes, series):
                draw_series(ax, x, y, label, spec.plot_type,
                            marker, linestyle, spec.markersize)
                ax.set_title(label, fontsize=10)
                ax.set_xlabel(spec.xlabel or xcol)
                ax.set_ylabel(spec.ylabel or ycol)
        else:
            ax = axes[0]
            for x, y, label, _xcol, _ycol in series:
                draw_series(ax, x, y, label, spec.plot_type,
                            marker, linestyle, spec.markersize)
            default_ylabel = series[0][4] if len(series) == 1 else ""
            ax.set_xlabel(spec.xlabel or series[0][3])
            ax.set_ylabel(spec.ylabel or default_ylabel)
            if spec.title:
                ax.set_title(spec.title)

        for ax in axes:
            if spec.grid:
                ax.grid(True, alpha=0.3)
            if spec.logx:
                ax.set_xscale("log")
            if spec.logy:
                ax.set_yscale("log")

            kw = {}
            if spec.xmin is not None:
                kw["left"] = spec.xmin
            if spec.xmax is not None:
                kw["right"] = spec.xmax
            if kw:
                ax.set_xlim(**kw)

            kw = {}
            if spec.ymin is not None:
                kw["bottom"] = spec.ymin
            if spec.ymax is not None:
                kw["top"] = spec.ymax
            if kw:
                ax.set_ylim(**kw)

            # 自定义刻度间隔（对数轴上跳过，否则 MultipleLocator 会冲突）
            if spec.xtick is not None and not spec.logx:
                ax.xaxis.set_major_locator(MultipleLocator(spec.xtick))
            if spec.ytick is not None and not spec.logy:
                ax.yaxis.set_major_locator(MultipleLocator(spec.ytick))

            if spec.legend:
                ax.legend(loc=spec.legend_loc, fontsize=8)

    except Exception:
        fig.clear()
        raise

    try:
        if separate and spec.title:
            fig.suptitle(spec.title, fontsize=13)
            fig.tight_layout(rect=(0, 0, 1, 0.96))
        else:
            fig.tight_layout()
    except Exception:
        pass
