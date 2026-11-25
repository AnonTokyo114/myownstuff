"""把 LaTeX 数学公式渲染成 PNG（供 flet 的 ft.Image 显示）。

为什么用 matplotlib 的 mathtext 而不是完整 LaTeX：
  * 不依赖 TeX 发行版（MiKTeX/TeX Live 动辄数百 MB）
  * 跨平台、纯 Python 依赖（matplotlib 已经在用）
  * 渲染结果可完全在无 GUI 环境验证（生成 PNG 后能检查尺寸/字节数）

代价：mathtext **只支持数学模式子集**，不支持 \\begin{align}、\\usepackage、
\\textcolor、\\begin{matrix} 这类完整文档/宏包语法。界面里要如实说明这一点。
"""

import io
import threading
from collections import OrderedDict
import re

#: 单次渲染允许的最大字符数（防止超长输入卡住）
MAX_LATEX_LEN = 2000


class LatexError(ValueError):
    """LaTeX 无法渲染（语法不支持、含非数学内容等）。"""


_HINTS = (
    ("\\begin{", "不支持 \\begin{...} 环境（如 align/matrix/cases），"
                 "mathtext 只解析数学模式子集"),
    ("\\usepackage", "不支持 \\usepackage：这里是数学公式渲染，不是完整 LaTeX 文档"),
    ("\\textcolor", "不支持 \\textcolor（颜色由界面主题决定）"),
    ("\\documentclass", "不支持完整文档语法，请只输入公式本身"),
    ("\\newcommand", "不支持自定义宏"),
)

#: 中日韩文字与全角标点。mathtext 没有这些字形，会为每个字刷一条
#: "Font 'rm' does not have a glyph" 警告并画成占位方框，所以提前拦下。
_CJK_RE = re.compile(r'[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff'
                     r'\uac00-\ud7af\u3040-\u30ff\uff00-\uffef]')


def strip_delimiters(text):
    """去掉 $...$、$$...$$、\\( \\)、\\[ \\] 这类包装，只留公式本体。"""
    s = (text or "").strip()
    for left, right in (("$$", "$$"), ("$", "$"), ("\\(", "\\)"), ("\\[", "\\]")):
        if s.startswith(left) and s.endswith(right) and len(s) > len(left) + len(right):
            s = s[len(left):-len(right)].strip()
            break
    return s


def _friendly_error(exc):
    """把 mathtext 的 ValueError 转成人能看懂的原因。"""
    msg = str(exc)
    if "Unknown symbol" in msg or "ParseException" in msg or "Expected" in msg:
        return f"公式语法无法解析（mathtext 只支持数学模式子集）：{msg[:160]}"
    return f"渲染失败：{msg[:200]}"


#: 渲染结果缓存（有界 LRU）。key = (公式, 字号, dpi, 颜色)。
#:
#: 一次渲染 20 阶展开式要 ~200 ms，而重复渲染同一公式（切标签页、
#: 重复点 Compute、切换主题再切回来）非常常见。缓存按渲染配置分键，
#: 所以字号/DPI/颜色变了自然会 miss，不会拿到错图。
_CACHE: "OrderedDict[tuple, bytes]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
#: 缓存条数上限（每条几十 KB，64 条完全够用）
CACHE_SIZE = 64


def render_latex(latex, *, fontsize=16, dpi=180, color="black"):
    """把 LaTeX 数学公式渲染成 PNG 字节（带缓存）。

    参数
    ----
    latex : str      公式本体（可带 $ 包装，会被去掉）
    fontsize : int   字号
    dpi : int        清晰度，越高越清晰但字节更多
    color : str      文字颜色（配合界面主题，深色主题传 "white"）

    返回
    ----
    bytes   PNG 数据，可直接给 QPixmap / ft.Image
    """
    plain = strip_delimiters(latex)
    key = (plain, int(fontsize), int(dpi), str(color))
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
            return hit

    data = _render_latex_uncached(plain, fontsize=fontsize, dpi=dpi,
                                  color=color)
    with _CACHE_LOCK:
        _CACHE[key] = data
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)
    return data


def clear_cache() -> None:
    """清空渲染缓存（字体或主题整体切换后可以调一下）。"""
    with _CACHE_LOCK:
        _CACHE.clear()


def _render_latex_uncached(s: str, *, fontsize=16, dpi=180, color="black"):
    """真正的渲染过程；输入已去掉 $ 包装。"""
    if not s:
        raise LatexError("Formula is empty")
    if len(s) > MAX_LATEX_LEN:
        raise LatexError(f"Formula too long (over {MAX_LATEX_LEN} characters)")

    for token, hint in _HINTS:
        if token in s:
            raise LatexError(hint)

    cjk = _CJK_RE.findall(s)
    if cjk:
        sample = "".join(dict.fromkeys(cjk))[:8]
        raise LatexError(
            f"不支持中文字符（{sample}）：mathtext 只能渲染数学符号。"
            "中文标签请写在公式外面。")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # pragma: no cover
        raise LatexError(f"matplotlib unavailable: {exc}") from exc

    fig = None
    try:
        fig = plt.figure(figsize=(0.01, 0.01))
        # mathtext 要求公式被 $...$ 包住；这里统一加上
        fig.text(0, 0, f"${s}$", fontsize=fontsize, color=color)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi,
                    bbox_inches="tight", pad_inches=0.06,
                    transparent=True)
        plt.close(fig)
        fig = None
        data = buf.getvalue()
        if not data.startswith(b"\x89PNG"):
            raise LatexError("Rendered output is not a valid PNG")
        return data
    except LatexError:
        raise
    except Exception as exc:
        raise LatexError(_friendly_error(exc)) from exc
    finally:
        if fig is not None:
            try:
                plt.close(fig)
            except Exception:
                pass
#: 常见可用语法示例，供界面做"支持哪些写法"的说明
EXAMPLES = [
    (r"x^{2} + 2x + 1", "多项式"),
    (r"\frac{x^{3}}{3} + C", "分式"),
    (r"\int_{0}^{\pi} \sin x\, dx = 2", "定积分"),
    (r"\frac{\partial^{2} f}{\partial x \partial y}", "偏导"),
    (r"\sum_{n=1}^{\infty} \frac{1}{n^{2}} = \frac{\pi^{2}}{6}", "级数"),
    (r"\lim_{x \to 0} \frac{\sin x}{x} = 1", "极限"),
    (r"\sqrt[3]{x} \quad \alpha \beta \Delta", "根式与希腊字母"),
]
