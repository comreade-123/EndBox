# -*- coding: utf-8 -*-
"""按钮类部件：切角按钮、图标按钮、侧边栏导航项、分段选择、开关。"""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QFont, QPainter, QPen
from PyQt6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.theme.motion import curve_at
from endbox.theme.tokens import CUT_BUTTON, CUT_SMALL, MOTION
from endbox.core.text import t
from endbox.ui.icons import has_icon, paint_icon
from endbox.ui.kit.base import EWidget, pal


class CutButton(QAbstractButton):
    """切角按钮。悬停时强调色从左擦入；主要按钮静止是墨底，触屏上静止就是黄底。

    variant:
      default  浅填充（chip），墨字
      primary  墨底反白字，悬停/按下时黄色擦入
      ghost    无填充 + 1px 细线
      danger   仅用于删除
      chip     筛选块：小巧、选中为黄底
    """

    VARIANTS = ("default", "primary", "ghost", "danger", "chip")

    def __init__(
        self,
        text: str = "",
        variant: str = "default",
        parent: QWidget | None = None,
        *,
        icon: str | None = None,
        checkable: bool = False,
        cut: int = CUT_BUTTON,
        height: int = 32,
        compact: bool = False,
    ):
        super().__init__(parent)
        self._text = text
        self._variant = variant if variant in self.VARIANTS else "default"
        self._icon = icon
        self._cut = cut
        self._height = height
        self._compact = compact
        self._wipe = 0.0
        self._pressed_offset = 0
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(MOTION["wipe"])
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_wipe)
        self._anim_target = 0.0

    # -- 属性 ---------------------------------------------------------------
    def setText(self, text: str) -> None:  # noqa: N802
        self._text = text
        self.updateGeometry()
        self.update()

    def text(self) -> str:  # noqa: D401
        return self._text

    def set_icon(self, name: str | None) -> None:
        self._icon = name
        self.updateGeometry()
        self.update()

    def set_variant(self, variant: str) -> None:
        if variant in self.VARIANTS:
            self._variant = variant
        self.update()

    def set_checked_color(self) -> None:
        self.update()

    def _on_wipe(self, value) -> None:
        # 动画值本身就是从当前值到目标值（0→1 或 1→0），只需缓动一次；
        # 之前反向时又取了一次反，导致离开后黄色停在 1（用户反馈的 BUG）。
        self._wipe = curve_at("mech", float(value))
        self.update()

    def _animate_wipe(self, on: bool) -> None:
        if self._anim.state() == QVariantAnimation.State.Running:
            self._anim.stop()
        start = self._wipe
        target = 1.0 if on else 0.0
        if abs(start - target) < 0.01:
            self._wipe = target
            self.update()
            return
        self._anim_target = target
        self._anim.setStartValue(start)
        self._anim.setEndValue(target)
        self._anim.start()

    # -- 事件 ---------------------------------------------------------------
    def enterEvent(self, event) -> None:  # noqa: N802
        self._animate_wipe(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._animate_wipe(False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._pressed_offset = 1
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._pressed_offset = 0
        self.update()
        super().mouseReleaseEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802
        pad = 18 if not self._compact else 12
        width = 0.0
        if self._icon and has_icon(self._icon):
            width += 18
        if self._text:
            font = self._font()
            width += paint.text_width(font, self._text)
        width += pad * 2
        return QSize(max(self._height, int(width + self._cut)), self._height)

    def _font(self) -> QFont:
        size = 12 if self._compact else 13
        return F.font(size, weight=QFont.Weight.DemiBold, cjk_first=not self._compact)

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        path = paint.cut_rect(rect, self._cut, "br")
        checked = self.isChecked()
        variant = self._variant

        # 底
        if variant == "primary":
            bg = colors.ink
        elif variant == "danger":
            bg = colors.danger
        elif variant == "ghost":
            bg = None
        elif checked:
            bg = colors.signal
        else:
            bg = colors.chip
        if bg is not None:
            p.fillPath(path, bg)

        # 悬停擦入的强调色
        wipe = self._wipe
        if wipe > 0.001 and not checked:
            p.save()
            p.setClipPath(path)
            p.setClipRect(QRectF(0, 0, self.width() * wipe, self.height()), Qt.ClipOperation.IntersectClip)
            p.fillRect(QRectF(0, 0, self.width(), self.height()), colors.signal)
            p.restore()

        # 边线（ghost / 焦点）
        if variant == "ghost":
            paint.stroke_cut(p, rect, colors.rule, self._cut, "br", 1.0)
        if self.hasFocus():
            paint.stroke_cut(p, rect.adjusted(1.5, 1.5, -1.5, -1.5), colors.trace, self._cut * 0.8, "br", 1.5)

        # 文字颜色：被黄色盖住的部分用墨色
        if variant == "danger":
            base_fg = colors.paper
        elif variant == "primary":
            base_fg = colors.paper
        elif checked:
            base_fg = colors.on_signal
        else:
            base_fg = colors.ink
        if variant in ("default", "ghost", "chip") and wipe > 0.5:
            base_fg = colors.on_signal
        elif variant == "primary" and wipe > 0.5:
            base_fg = colors.on_signal
        elif variant == "danger" and wipe > 0.5:
            base_fg = colors.ink

        font = self._font()
        content_w = self.width()
        icon_w = 18 if (self._icon and has_icon(self._icon)) else 0
        text_w = paint.text_width(font, self._text) if self._text else 0
        total = icon_w + text_w + (4 if icon_w and text_w else 0)
        x = (content_w - total) / 2
        dy = self._pressed_offset
        if icon_w:
            paint_icon(
                p, self._icon, QRectF(x, (self.height() - 14) / 2 + dy, 14, 14),
                base_fg, stroke=1.4,
            )
            x += icon_w + 4
        if self._text:
            paint.draw_text(
                p, QRectF(x, dy, text_w + 2, self.height()), self._text, font, base_fg,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            )
        p.end()


class IconButton(CutButton):
    """方形图标按钮（顶栏、窗口控制、工具条）。"""

    def __init__(
        self,
        icon: str,
        tooltip: str = "",
        parent=None,
        *,
        size: int = 32,
        icon_size: int = 16,
        cut: int = CUT_SMALL,
        variant: str = "ghost",
        accent_close: bool = False,
    ):
        super().__init__("", variant, parent, icon=icon, cut=cut, height=size)
        self._icon_name = icon
        self._icon_size = icon_size
        self._accent_close = accent_close
        self.setFixedSize(size, size)
        self.setToolTip(tooltip)

    def set_icon_name(self, name: str) -> None:
        self._icon_name = name
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._height, self._height)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        path = paint.cut_rect(rect, self._cut, "br")
        if self.isChecked():
            p.fillPath(path, colors.signal)
        elif self._wipe > 0.001:
            p.save()
            p.setClipPath(path)
            p.setClipRect(QRectF(0, 0, self.width() * self._wipe, self.height()), Qt.ClipOperation.IntersectClip)
            fill = colors.signal
            p.fillRect(QRectF(0, 0, self.width(), self.height()), fill)
            p.restore()
        if self.hasFocus():
            paint.stroke_cut(p, rect.adjusted(1.5, 1.5, -1.5, -1.5), colors.trace, self._cut * 0.8, "br", 1.5)
        fg = colors.on_signal if (self.isChecked() or self._wipe > 0.5) else colors.ink
        if self._accent_close:
            fg = colors.on_signal if (self.isChecked() or self._wipe > 0.5) else colors.ink
        size = self._icon_size
        paint_icon(
            p, self._icon_name,
            QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size),
            fg, stroke=1.5,
        )
        p.end()


class NavItem(QAbstractButton):
    """侧边栏功能按钮：图标 + 小字。当前位置整格黄块。"""

    def __init__(self, icon: str, label: str, parent=None, *, order_hint: str = ""):
        super().__init__(parent)
        self._icon = icon
        self._label = label
        self._order_hint = order_hint
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(48)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(label)
        self._hover = False

    def set_label(self, label: str) -> None:
        self._label = label
        self.setToolTip(label)
        self.update()

    def set_icon_name(self, icon: str) -> None:
        self._icon = icon
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 2.0)
        checked = self.isChecked()
        if checked:
            p.fillPath(paint.cut_rect(rect, CUT_SMALL, "br"), colors.signal)
            fg = colors.on_signal
        elif self._hover:
            p.fillPath(paint.cut_rect(rect, CUT_SMALL, "br"), colors.chip)
            fg = colors.ink
        else:
            fg = colors.ink2
        if self.hasFocus():
            paint.stroke_cut(p, rect.adjusted(1.5, 1.5, -1.5, -1.5), colors.trace, CUT_SMALL * 0.8, "br", 1.5)
        paint_icon(
            p, self._icon, QRectF((self.width() - 18) / 2, 7, 18, 18), fg, stroke=1.6,
        )
        paint.draw_text(
            p, QRectF(2, 28, self.width() - 4, 14), self._label,
            F.font(10, weight=QFont.Weight.DemiBold, cjk_first=True), fg,
            Qt.AlignmentFlag.AlignCenter, elide=True,
        )
        p.end()


class Segmented(EWidget):
    """分段选择：当前段墨底反白（触屏与桌面一致）。"""

    changed = pyqtSignal(int)

    def __init__(self, items: list[str] | None = None, parent=None, *, index: int = 0, height: int = 28):
        super().__init__(parent)
        self._items = list(items or [])
        self._index = index
        self._height = height
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hover = -1
        self.setMouseTracking(True)

    def set_items(self, items: list[str]) -> None:
        self._items = list(items)
        self._index = min(self._index, max(0, len(items) - 1))
        self.updateGeometry()
        self.update()

    def set_index(self, index: int, *, emit: bool = False) -> None:
        if not self._items:
            return
        index = max(0, min(len(self._items) - 1, index))
        if index == self._index:
            return
        self._index = index
        self.update()
        if emit:
            self.changed.emit(index)

    @property
    def index(self) -> int:
        return self._index

    def _font(self) -> QFont:
        return F.font(12, weight=QFont.Weight.DemiBold, cjk_first=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        font = self._font()
        width = sum(paint.text_width(font, item) + 24 for item in self._items)
        return QSize(max(60, int(width)), self._height)

    def _cell_width(self, i: int) -> float:
        widths = [paint.text_width(self._font(), item) + 24 for item in self._items]
        total = sum(widths) or 1.0
        return self.width() * (widths[i] / total)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        x = 0.0
        font = self._font()
        for i, item in enumerate(self._items):
            w = self._cell_width(i)
            rect = QRectF(x, 0.5, w, self.height() - 1.0)
            if i == self._index:
                p.fillPath(paint.cut_rect(rect, CUT_SMALL, "br"), colors.ink)
                fg = colors.paper
            elif i == self._hover:
                p.fillPath(paint.cut_rect(rect, CUT_SMALL, "br"), colors.chip)
                fg = colors.ink
            else:
                fg = colors.ink2
            paint.draw_text(p, rect, item, font, fg, Qt.AlignmentFlag.AlignCenter)
            if i not in (0, self._index):
                paint.hairline(p, x, 6, x, self.height() - 6, colors.rule)
            x += w
        p.end()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        x = event.position().x()
        acc = 0.0
        old = self._hover
        self._hover = -1
        for i in range(len(self._items)):
            w = self._cell_width(i)
            if acc <= x < acc + w:
                self._hover = i
                break
            acc += w
        if old != self._hover:
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = -1
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        x = event.position().x()
        acc = 0.0
        for i in range(len(self._items)):
            w = self._cell_width(i)
            if acc <= x < acc + w:
                self.set_index(i, emit=True)
                break
            acc += w
        super().mousePressEvent(event)


class Toggle(QAbstractButton):
    """开 / 关：墨色实心 = 生效，虚线框 = 未启用（触屏也够大）。"""

    def __init__(self, text: str = "", parent=None, *, checked: bool = False):
        super().__init__(parent)
        self._text = text
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(30)

    def setText(self, text: str) -> None:  # noqa: N802
        self._text = text
        self.updateGeometry()
        self.update()

    def text(self) -> str:  # noqa: D401
        return self._text

    def sizeHint(self) -> QSize:  # noqa: N802
        font = F.font(13, cjk_first=True)
        return QSize(int(paint.text_width(font, self._text) + 78), 30)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        font = F.font(13, cjk_first=True)
        box = QRectF(0.5, 3.5, 44, 22)
        if self.isChecked():
            p.fillPath(paint.cut_rect(box, CUT_SMALL, "br"), colors.signal)
            state_color = colors.on_signal
            state_text = t("common.toggle_on", "开")
        else:
            paint.stroke_cut(p, box, colors.ink2, CUT_SMALL, "br", 1.0)
            state_color = colors.ink2
            state_text = t("common.toggle_off", "关")
        paint.draw_text(
            p, box, state_text, F.font(11, weight=QFont.Weight.Bold, cjk_first=True),
            state_color, Qt.AlignmentFlag.AlignCenter,
        )
        paint.draw_text(
            p, QRectF(52, 0, self.width() - 52, self.height()), self._text, font, colors.ink,
        )
        if self.hasFocus():
            paint.paint_focus(p, QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), colors.trace)
        p.end()
