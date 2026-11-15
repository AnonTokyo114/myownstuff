"""公式渲染页：把 LaTeX 源码渲染成图片，可复制源码 / 另存 PNG。

对应 myapp 里「∑ 公式渲染」那部分（QuickLaTeX 风格）。
"""

from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core import latex_render as lr

EXAMPLES = [
    r"\int_0^{\infty} e^{-x^2}\,dx = \frac{\sqrt{\pi}}{2}",
    r"\nabla \times \mathbf{E} = -\frac{\partial \mathbf{B}}{\partial t}",
    r"\sum_{n=1}^{\infty} \frac{1}{n^2} = \frac{\pi^2}{6}",
    r"\hat{H}\psi = E\psi",
    r"\frac{\partial^2 u}{\partial t^2} = c^2 \nabla^2 u",
    r"e^{i\pi} + 1 = 0",
]


class FormulaPage(QWidget):
    page_title = "Formula Renderer"

    def __init__(self) -> None:
        super().__init__()
        self._png: bytes | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)

        title = QLabel("Formula Renderer")
        title.setObjectName("pageTitle")
        outer.addWidget(title)
        subtitle = QLabel("Paste LaTeX and get a transparent PNG, ready for slides and reports")
        subtitle.setObjectName("pageSubtitle")
        outer.addWidget(subtitle)

        # ---- 输入 ----
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText(r"e.g. \int_0^1 x^2\,dx = \frac{1}{3}")
        self.input.setPlainText(EXAMPLES[0])
        self.input.setFixedHeight(96)
        self.input.setObjectName("codeInput")
        outer.addWidget(self.input)

        # ---- 控制栏 ----
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)

        self.render_btn = QPushButton("Render")
        self.render_btn.setObjectName("primary")
        self.render_btn.setFixedWidth(88)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setObjectName("ghost")
        self.clear_btn.setFixedWidth(64)
        ctrl.addWidget(self.render_btn)
        ctrl.addWidget(self.clear_btn)

        ctrl.addSpacing(12)
        ctrl.addWidget(QLabel("Size"))
        self.font_size = QSpinBox()
        self.font_size.setRange(8, 60)
        self.font_size.setValue(18)
        self.font_size.setFixedWidth(64)
        ctrl.addWidget(self.font_size)

        ctrl.addWidget(QLabel("Color"))
        self.color = QComboBox()
        self.color.addItem("Dark (light backgrounds)", "#1f2328")
        self.color.addItem("Pure black", "#000000")
        self.color.addItem("Light (dark backgrounds)", "#f0f3f7")
        self.color.setFixedWidth(150)
        ctrl.addWidget(self.color)

        ctrl.addSpacing(12)
        ctrl.addWidget(QLabel("Example"))
        self.example = QComboBox()
        self.example.setFixedWidth(260)
        for e in EXAMPLES:
            self.example.addItem(e[:48] + ("…" if len(e) > 48 else ""))
        ctrl.addWidget(self.example)
        ctrl.addStretch(1)
        outer.addLayout(ctrl)

        # ---- 预览 ----
        self.preview_box = QFrame()
        self.preview_box.setObjectName("latexBox")
        self.preview_box.setMinimumHeight(240)
        box_layout = QVBoxLayout(self.preview_box)
        box_layout.setContentsMargins(12, 12, 12, 12)
        self.preview = QLabel("(click Render to preview)")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box_layout.addWidget(self.preview)
        outer.addWidget(self.preview_box, 1)

        # ---- 底部 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.source = QLabel("")
        self.source.setObjectName("codeField")
        self.source.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.source.setWordWrap(True)
        bottom.addWidget(self.source, 1)

        self.copy_btn = QPushButton("Copy source")
        self.copy_btn.setObjectName("ghost")
        self.copy_btn.clicked.connect(self._copy)
        self.save_btn = QPushButton("Save PNG")
        self.save_btn.setObjectName("ghost")
        self.save_btn.clicked.connect(self._save)
        bottom.addWidget(self.copy_btn)
        bottom.addWidget(self.save_btn)
        outer.addLayout(bottom)

        self.status = QLabel("")
        self.status.setObjectName("pageSubtitle")
        outer.addWidget(self.status)

        # ---- 信号 ----
        self.render_btn.clicked.connect(self.render)
        self.clear_btn.clicked.connect(self._clear)
        self.example.currentIndexChanged.connect(self._pick_example)
        self.font_size.valueChanged.connect(lambda _v: self.render())
        self.color.currentIndexChanged.connect(lambda _i: self.render())

    # ------------------------------------------------------------------
    def _pick_example(self, index: int) -> None:
        if 0 <= index < len(EXAMPLES):
            self.input.setPlainText(EXAMPLES[index])

    def _clear(self) -> None:
        self.input.clear()
        self._png = None
        self.preview.setPixmap(QPixmap())
        self.preview.setText("(click Render to preview)")
        self.source.setText("")
        self.status.setText("")

    def render(self) -> None:
        latex = self.input.toPlainText().strip()
        if not latex:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("(click Render to preview)")
            self._png = None
            return

        color = self.color.currentData()
        png = lr.render_latex(latex, fontsize=self.font_size.value(),
                              color=color)
        if not png:
            self._png = None
            self.preview.setPixmap(QPixmap())
            self.preview.setText("⚠ Rendering failed — check the LaTeX syntax")
            self.status.setText("")
            return

        self._png = png
        pix = QPixmap()
        if pix.loadFromData(png):
            max_w = max(240, self.preview_box.width() - 40)
            if pix.width() > max_w:
                pix = pix.scaledToWidth(max_w,
                                        Qt.TransformationMode.SmoothTransformation)
            self.preview.setPixmap(pix)
        self.source.setText(latex)
        self.status.setText(f"Rendered {len(png)} bytes · {self.font_size.value()}pt")

    def _copy(self) -> None:
        text = self.input.toPlainText().strip()
        if text:
            QGuiApplication.clipboard().setText(text)
            self.status.setText("Source copied to clipboard")

    def _save(self) -> None:
        if not self._png:
            self.status.setText("Nothing to save yet")
            return
        default = os.path.join(
            os.path.expanduser("~"), "Desktop",
            f"formula_{datetime.now():%Y%m%d_%H%M%S}.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save PNG", default, "PNG image (*.png)")
        if not path:
            return
        try:
            with open(path, "wb") as fh:
                fh.write(self._png)
            self.status.setText(f"Saved to: {path}")
        except OSError as exc:
            self.status.setText(f"Save failed: {exc}")
