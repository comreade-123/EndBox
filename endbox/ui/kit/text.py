# -*- coding: utf-8 -*-
"""文本类部件：标签、段落头、字段、标签块、大数字。"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QLabel, QSizePolicy

from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.ui.kit.base import EWidget, pal


class Label(QLabel):
    """带角色的文字标签，刷新主题时自动换色换字。"""

    ROLES = {
        "eyebrow": (lambda: F.eyebrow_font(11), "ink2"),
        "field": (lambda: F.field_font(13), "ink2"),
        "title": (lambda: F.font(20, weight=QFont.Weight.Bold, wide=True), "ink"),
        "h2": (lambda: F.font(17, weight=QFont.Weight.Bold, cjk_first=True), "ink"),
        "h1": (lambda: F.display_font(27), "ink"),
        "body": (lambda: F.font(14, cjk_first=True), "ink"),
        "muted": (lambda: F.font(13, cjk_first=True), "ink2"),
        "small": (lambda: F.font(12, cjk_first=True), "ink2"),
        "num": (lambda: F.number_font(26), "ink"),
        "ink2": (lambda: F.font(13, cjk_first=True), "ink2"),
        "mono": (lambda: F.mono_font(12), "ink2"),
    }

    def __init__(
        self,
        text: str = "",
        role: str = "body",
        parent=None,
        *,
        wrap: bool = False,
        align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    ):
        super().__init__(text, parent)
        self._role = role
        self._align = align
        self.setWordWrap(wrap)
        self.setAlignment(align)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.refresh_theme()

    def set_role(self, role: str) -> None:
        self._role = role
        self.refresh_theme()

    def refresh_theme(self) -> None:
        p = pal()
        make_font, color_name = self.ROLES.get(self._role, self.ROLES["body"])
        self.setFont(make_font())
        color = getattr(p, color_name)
        self.setStyleSheet(f"color: {color.name()}; background: transparent;")


class SectionHead(EWidget):
    """`// EYEBROW` 在上，中文粗体标题在下。"""

    def __init__(self, eyebrow: str, title: str, parent=None, *, size: int = 20):
        super().__init__(parent)
        self._eyebrow = eyebrow
        self._title = title
        self._size = size
        self.setFixedHeight(size + 24)

    def set_title(self, title: str) -> None:
        self._title = title
        self.update()

    def set_eyebrow(self, eyebrow: str) -> None:
        self._eyebrow = eyebrow
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = paint.QPainter(self)
        p.setRenderHint(paint.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(paint.QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        w = self.width()
        paint.draw_text(
            p, QRectF(0, 0, w, 14), f"// {self._eyebrow.upper()}",
            F.eyebrow_font(11), colors.ink2, elide=True,
        )
        paint.draw_text(
            p, QRectF(0, 15, w, self.height() - 15), self._title,
            F.font(self._size, weight=QFont.Weight.Bold, wide=True), colors.ink, elide=True,
        )
        p.end()


class FieldText(EWidget):
    """字段：`2026.11.13 │ 6,380`，宽体小字，竖线分隔。"""

    def __init__(self, fields: list[str] | None = None, parent=None):
        super().__init__(parent)
        self._fields = list(fields or [])
        self.setFixedHeight(16)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

    def set_fields(self, fields: list[str]) -> None:
        self._fields = list(fields)
        self.updateGeometry()
        self.update()

    def _font(self):
        return F.field_font(12)

    def sizeHint(self) -> QSize:  # noqa: N802
        font = self._font()
        text = "  │  ".join(self._fields)
        return QSize(int(paint.text_width(font, text)) + 2, 16)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = paint.QPainter(self)
        p.setRenderHint(paint.QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        font = self._font()
        x = 0.0
        sep = "  │  "
        for i, field in enumerate(self._fields):
            if i:
                paint.draw_text(p, QRectF(x, 0, 200, self.height()), sep, font, colors.rule)
                x += paint.text_width(font, sep)
            paint.draw_text(p, QRectF(x, 0, 400, self.height()), field, font, colors.ink2)
            x += paint.text_width(font, field)
        p.end()


class Badge(EWidget):
    """墨底白字 / 黄底的编号标签，右下切角。"""

    def __init__(self, text: str = "", parent=None, *, signal: bool = False):
        super().__init__(parent)
        self._text = text
        self._signal = signal
        self.setFixedHeight(20)

    def set_text(self, text: str) -> None:
        self._text = text
        self.updateGeometry()
        self.update()

    def set_signal(self, signal: bool) -> None:
        self._signal = signal
        self.update()

    def _font(self):
        return F.field_font(11)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(int(paint.text_width(self._font(), self._text) + 16), 20)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = paint.QPainter(self)
        p.setRenderHint(paint.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(paint.QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        bg = colors.signal if self._signal else colors.tag_bg
        fg = colors.on_signal if self._signal else colors.tag_fg
        paint.fill_cut(p, QRectF(0, 0, self.width(), self.height()), bg, 6, "br")
        paint.draw_text(
            p, QRectF(6, 0, self.width() - 12, self.height()), self._text,
            self._font(), fg, Qt.AlignmentFlag.AlignCenter,
        )
        p.end()


class NumberStat(EWidget):
    """大数字 + 小标签（看板用）。数字必须是真的。"""

    def __init__(self, label: str, value: str = "—", parent=None, *, size: int = 34, unit: str = ""):
        super().__init__(parent)
        self._label = label
        self._value = value
        self._unit = unit
        self._size = size
        self.setMinimumHeight(size + 26)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:  # noqa: N802
        font = F.number_font(self._size)
        label_font = F.font(12, cjk_first=True)
        width = max(paint.text_width(font, self._value), paint.text_width(label_font, self._label))
        return QSize(int(width) + 12, self._size + 30)

    def set_value(self, value: str) -> None:
        if value != self._value:
            self._value = value
            self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = paint.QPainter(self)
        p.setRenderHint(paint.QPainter.RenderHint.Antialiasing)
        p.setRenderHint(paint.QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        font = F.number_font(self._size)
        value_w = paint.text_width(font, self._value)
        paint.draw_text(p, QRectF(0, 0, value_w + 4, self._size + 8), self._value, font, colors.ink)
        if self._unit:
            paint.draw_text(
                p, QRectF(value_w + 6, self._size - 8, 70, 18), self._unit,
                F.field_font(12), colors.ink2,
            )
        paint.draw_text(
            p, QRectF(0, self._size + 10, self.width(), 16), self._label,
            F.font(12, cjk_first=True), colors.ink2,
        )
        p.end()
