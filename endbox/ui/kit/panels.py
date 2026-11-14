# -*- coding: utf-8 -*-
"""容器类部件：面板、细线、滚动区、空状态、头像、进度条。"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QFont, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.theme.tokens import CUT_PANEL, CUT_SMALL
from endbox.ui.kit.base import EWidget, pal
from endbox.ui.kit.text import Label, SectionHead


class Hairline(EWidget):
    """1px 细线分区。"""

    def __init__(self, parent=None, *, horizontal: bool = True, color_role: str = "rule"):
        super().__init__(parent)
        self._horizontal = horizontal
        self._role = color_role
        if horizontal:
            self.setFixedHeight(1)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setFixedWidth(1)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        color = getattr(pal(), self._role)
        if self._horizontal:
            paint.hairline(p, 0, 0, self.width(), 0, color)
        else:
            paint.hairline(p, 0, 0, 0, self.height(), color)
        p.end()


class Spacer(EWidget):
    def __init__(self, parent=None, *, w: int = 0, h: int = 0, expand: bool = False):
        super().__init__(parent)
        if w:
            self.setFixedWidth(w)
        if h:
            self.setFixedHeight(h)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding if expand else QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Expanding if expand else QSizePolicy.Policy.Fixed,
        )


class Panel(EWidget):
    """一块板：细线边框 + 板色底。标题用 SectionHead，标题右侧放操作。"""

    def __init__(
        self,
        title: str = "",
        eyebrow: str = "",
        parent=None,
        *,
        padding: int = 16,
        spacing: int = 12,
        plain: bool = False,
    ):
        super().__init__(parent)
        self._title = title
        self._eyebrow = eyebrow
        self._padding = padding
        self._plain = plain
        self._accent = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(padding, padding, padding, padding)
        outer.setSpacing(spacing)
        self._outer = outer

        self.header: QWidget | None = None
        self.head: SectionHead | None = None
        self.header_actions: QHBoxLayout | None = None
        if title or eyebrow:
            self.header = QWidget(self)
            hl = QHBoxLayout(self.header)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(8)
            self.head = SectionHead(eyebrow or title, title or eyebrow, self.header)
            hl.addWidget(self.head, 1)
            self.header_actions = QHBoxLayout()
            self.header_actions.setContentsMargins(0, 0, 0, 0)
            self.header_actions.setSpacing(8)
            hl.addLayout(self.header_actions)
            self._header_layout = hl
            outer.addWidget(self.header)

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(spacing)
        outer.addLayout(self.body, 1)

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.body.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout, stretch: int = 0):
        self.body.addLayout(layout, stretch)
        return layout

    def add_header_action(self, widget: QWidget) -> QWidget:
        if self.header_actions is not None:
            self.header_actions.addWidget(widget)
        return widget

    def set_accent(self, enabled: bool) -> None:
        self._accent = enabled
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        if not self._plain:
            p.fillRect(rect, colors.panel_surface)
            p.setPen(colors.rule)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect)
        if self._accent:
            p.fillRect(QRectF(0, 0, 4, self.height()), colors.signal)
        p.end()


class ScrollArea(QScrollArea):
    """细线滚动区：垂直滚动，内容透明。"""

    def __init__(self, parent=None, *, horizontal: bool = False):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if horizontal else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet("QScrollArea { background: transparent; }")
        self.viewport().setAutoFillBackground(False)

    def set_content(self, widget: QWidget) -> QWidget:
        self.setWidget(widget)
        return widget


class HatchBand(EWidget):
    """45° 斜线纹带：空状态、标题带、画框影子。"""

    def __init__(self, parent=None, *, height: int = 56):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        colors = pal()
        paint.paint_hatch(p, QRectF(0, 0, self.width(), self.height()), colors.ink)
        p.end()


class EmptyState(EWidget):
    """空状态：一句完整的话说明这里将来会出现什么。"""

    def __init__(self, title: str, description: str = "", parent=None, *, action: QWidget | None = None):
        super().__init__(parent)
        self._title = title
        self._description = description
        self._hatch_h = 56
        self._action = action
        height = 56 + 30 + 26 + (44 if action else 0)
        self.setMinimumHeight(height)
        if action is not None:
            action.setParent(self)
            action.adjustSize()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._action is not None:
            self._action.move(0, 130)
            self._action.adjustSize()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        w = self.width()
        paint.paint_hatch(p, QRectF(0, 0, w, self._hatch_h), colors.ink, alpha=0.10)
        paint.hairline(p, 0, self._hatch_h, w, self._hatch_h, colors.rule)
        paint.draw_text(
            p, QRectF(0, self._hatch_h + 18, w, 24), self._title,
            F.font(16, weight=QFont.Weight.Bold, cjk_first=True), colors.ink,
        )
        paint.draw_text(
            p, QRectF(0, self._hatch_h + 46, w, 20), self._description,
            F.font(13, cjk_first=True), colors.ink2, elide=True,
        )
        if self._action is not None:
            paint.tri_marker(p, 7, self._hatch_h + 76, 4, colors.signal)
        p.end()


class Avatar(EWidget):
    """头像：方形切角，右下切掉；没有图时用编号（真实数据）。"""

    def __init__(self, parent=None, *, size: int = 44, cut: int = 8):
        super().__init__(parent)
        self._size = size
        self._cut = cut
        self._pixmap: QPixmap | None = None
        self._initials = ""
        self.setFixedSize(size, size)

    def set_initials(self, text: str) -> None:
        self._initials = text
        self.update()

    def set_pixmap(self, pix: QPixmap | None) -> None:
        self._pixmap = pix
        self.update()

    def set_image(self, path: str) -> bool:
        pix = QPixmap(path)
        if pix.isNull():
            return False
        self._pixmap = pix
        self.update()
        return True

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        colors = pal()
        rect = QRectF(0, 0, self.width(), self.height())
        path = paint.cut_rect(rect, self._cut, "br")
        if self._pixmap is not None and not self._pixmap.isNull():
            p.save()
            p.setClipPath(path)
            scaled = self._pixmap.scaled(
                int(rect.width()), int(rect.height()),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            p.drawPixmap(rect, scaled, QRectF(scaled.rect()))
            p.restore()
        else:
            p.fillPath(path, colors.chip)
            paint.draw_text(
                p, rect, self._initials or "—",
                F.font(max(12, self._size // 3), weight=QFont.Weight.Bold, wide=True), colors.ink2,
                Qt.AlignmentFlag.AlignCenter,
            )
        paint.stroke_cut(p, QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), colors.rule, self._cut, "br", 1.0)
        p.end()


class ProgressBar(EWidget):
    """细线进度：chip 轨 + 强调色填充（数据是真的才显示）。"""

    def __init__(self, parent=None, *, height: int = 4):
        super().__init__(parent)
        self._ratio = 0.0
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_ratio(self, ratio: float) -> None:
        ratio = max(0.0, min(1.0, float(ratio)))
        if abs(ratio - self._ratio) > 1e-4:
            self._ratio = ratio
            self.update()

    @property
    def ratio(self) -> float:
        return self._ratio

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        colors = pal()
        p.fillRect(QRectF(0, 0, self.width(), self.height()), colors.chip)
        p.fillRect(QRectF(0, 0, self.width() * self._ratio, self.height()), colors.signal)
        p.end()
