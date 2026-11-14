# -*- coding: utf-8 -*-
"""LaTeX 公式渲染部件：把算式直接排版成图形（mathtext），而不是显示原始 TeX。

依赖 matplotlib 的 mathtext（随包 vendor 在 ``vendor/``，打包时一起带上）；
缺失或公式超出 mathtext 支持范围时自动退回等宽文本，不让界面出错。

用法::

    from endbox.ui.kit import LaTeXView
    view.set_latex(r"\\frac{172+3}{5} \\times 4 = 325", fontsize=16)

注意：mathtext 支持 \\frac、\\int、\\sum、\\lim、\\sqrt、上下标等常用写法，但不支持
\\begin{matrix} 之类的环境；生成侧应避免这类写法（或让本部件退化为文本）。
"""
from __future__ import annotations

import io
import sys
from functools import lru_cache

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QSizePolicy

from endbox import config
from endbox.core.log import get_logger
from endbox.theme import fonts as F
from endbox.ui.kit.base import EWidget, pal

log = get_logger("ui.latex")

_matplotlib = None
_failed = False


def _ensure_matplotlib():
    """懒加载 matplotlib；优先 vendor 目录（不污染全局 site-packages）。"""
    global _matplotlib, _failed
    if _matplotlib is not None:
        return _matplotlib
    if _failed:
        return None
    try:
        import matplotlib  # noqa: PLC0415
    except ImportError:
        vendor = config.ROOT / "vendor"
        if vendor.is_dir() and str(vendor) not in sys.path:
            sys.path.insert(0, str(vendor))
        try:
            import matplotlib  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            _failed = True
            log.warning("matplotlib 不可用，LaTeX 公式将退回文本渲染")
            return None
    try:
        matplotlib.use("Agg")
        from matplotlib import mathtext  # noqa: F401, PLC0415
    except Exception:  # noqa: BLE001
        _failed = True
        log.exception("mathtext 初始化失败")
        return None
    _matplotlib = matplotlib
    return _matplotlib


def has_math_renderer() -> bool:
    return _ensure_matplotlib() is not None


@lru_cache(maxsize=96)
def render_latex(latex: str, dpi: int = 180, ink: str = "#161616") -> QPixmap | None:
    """把 LaTeX（不带 $ 定界符也可）渲染成透明背景的 QPixmap；失败返回 None。"""
    if not latex.strip():
        return None
    if _ensure_matplotlib() is None:
        return None
    text = latex.strip()
    if not text.startswith("$"):
        text = f"${text}$"
    try:
        from matplotlib import mathtext  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        buf = io.BytesIO()
        mathtext.math_to_image(text, buf, dpi=dpi, format="png", color=(0, 0, 0))
        buf.seek(0)
        source = Image.open(buf).convert("L")
        # 白底黑字 → 用亮度反推 alpha，再染成当前主题的墨色
        alpha = Image.eval(source, lambda value: 255 - value)
        rgba = Image.new("RGBA", source.size, _rgb(ink) + (0,))
        rgba.putalpha(alpha)
        out = io.BytesIO()
        rgba.save(out, format="PNG")
        image = QImage.fromData(out.getvalue(), "PNG")
        if image.isNull():
            return None
        return QPixmap.fromImage(image)
    except Exception:  # noqa: BLE001 - 公式无法渲染时退回文本
        log.warning("LaTeX 渲染失败：%s", text[:120], exc_info=True)
        return None


def _rgb(hex_color: str) -> tuple[int, int, int]:
    color = QColor(hex_color)
    return color.red(), color.green(), color.blue()


class LaTeXView(EWidget):
    """公式视图：优先图形排版；渲染失败时退回可读文本（含 \"=\" 断行）。"""

    def __init__(self, parent=None, *, fontsize: int = 16, fallback: bool = True):
        super().__init__(parent)
        self._latex = ""
        self._fontsize = max(8, int(fontsize))
        self._fallback_text = ""
        self._fallback = fallback
        self._pixmap: QPixmap | None = None
        self._rendered_for: tuple[str, str] | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(48)

    # -- 设置 ---------------------------------------------------------------
    def set_latex(self, latex: str, *, text: str = "", fontsize: int | None = None) -> None:
        self._latex = (latex or "").strip()
        self._fallback_text = (text or "").strip() or self._latex
        if fontsize:
            self._fontsize = max(8, int(fontsize))
        self._pixmap = None
        self._rendered_for = None
        self._refresh_pixmap()
        self.updateGeometry()
        self.update()

    @property
    def latex(self) -> str:
        return self._latex

    def clear(self) -> None:
        self.set_latex("", text="")

    def _ink(self) -> str:
        return pal().ink.name()

    def _refresh_pixmap(self) -> None:
        if not self._latex:
            self._pixmap = None
            return
        key = (self._latex, self._ink())
        if self._rendered_for == key:
            return
        # 字号 → DPI 近似：mathtext 以 10pt 为基准，dpi 控制像素大小
        dpi = max(90, int(self._fontsize * 10))
        pix = render_latex(self._latex, dpi=dpi, ink=self._ink())
        self._pixmap = pix
        self._rendered_for = key

    def refresh_theme(self) -> None:
        self._rendered_for = None
        self._refresh_pixmap()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        self._refresh_pixmap()
        if self._pixmap is not None:
            return QSize(min(self._pixmap.width(), 1400), max(48, self._pixmap.height() + 8))
        return QSize(320, 48)

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        self._refresh_pixmap()
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self._pixmap is not None and not self._pixmap.isNull():
            source = QRectF(self._pixmap.rect())
            scale = min(1.0, rect.width() / max(1, source.width()),
                        rect.height() / max(1, source.height()))
            width = source.width() * scale
            height = source.height() * scale
            target = QRectF(rect.center().x() - width / 2, rect.center().y() - height / 2, width, height)
            p.drawPixmap(target, self._pixmap, source)
        elif self._fallback and self._fallback_text:
            font = F.mono_font(max(11, self._fontsize - 2))
            p.setFont(font)
            p.setPen(colors.ink)
            p.drawText(
                rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap),
                self._fallback_text,
            )
        p.end()
