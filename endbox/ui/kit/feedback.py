# -*- coding: utf-8 -*-
"""反馈类部件：提示条、Toast、处理中、告示、二维码画布。"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QFont, QPainter
from PyQt6.QtWidgets import QSizePolicy, QWidget

from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.theme.motion import curve_at
from endbox.theme.tokens import MOTION
from endbox.ui.icons import paint_icon
from endbox.ui.kit.base import EWidget, pal


class Toast(EWidget):
    """一条提示：板色细框，前面一面黄色小旗。"""

    def __init__(self, text: str, parent=None, *, kind: str = "info", width: int = 372):
        super().__init__(parent)
        self._text = text
        self._kind = kind
        self._width = width
        lines = max(1, (len(text) + 34) // 36)
        self.setFixedWidth(width)
        self.setFixedHeight(44 if lines == 1 else 44 + (lines - 1) * 18)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._opacity = 0.0

    def set_opacity(self, value: float) -> None:
        self._opacity = max(0.0, min(1.0, value))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        p.setOpacity(self._opacity)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        p.fillRect(rect, colors.panel_surface)
        p.setPen(colors.rule)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        p.fillRect(QRectF(0, 0, 4, self.height()), colors.signal)
        icon = "warning" if self._kind == "warn" else ("check" if self._kind == "ok" else "info")
        paint_icon(
            p, icon, QRectF(16, (self.height() - 16) / 2, 16, 16),
            colors.signal if self._kind == "warn" else colors.ink2, stroke=1.6,
        )
        paint.draw_text(
            p, QRectF(42, 0, self.width() - 58, self.height()), self._text,
            F.font(13, cjk_first=True), colors.ink, elide=True,
            align=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        p.end()


class ToastHost(QWidget):
    """Toast 层：叠在窗口右上角，鼠标穿透。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._toasts: list[Toast] = []
        self._anims: dict[Toast, QVariantAnimation] = {}

    def show_message(self, text: str, kind: str = "info", *, hold_ms: int = MOTION["toast_hold"]) -> None:
        toast = Toast(text, self, kind=kind)
        toast.show()
        self._toasts.append(toast)
        self._reposition()
        toast.set_opacity(0.0)
        start_x = self.width() - toast.width() - 18 + 10
        end_x = self.width() - toast.width() - 18
        anim = QVariantAnimation(self)
        anim.setDuration(MOTION["toast"])
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)

        def _on_value(v) -> None:
            eased = curve_at("mech", float(v))
            toast.set_opacity(eased)
            toast.move(int(start_x + (end_x - start_x) * eased), toast.y())

        anim.valueChanged.connect(_on_value)
        self._anims[toast] = anim
        anim.start()
        toast._hold_timer = QTimer(self)
        toast._hold_timer.setSingleShot(True)
        toast._hold_timer.timeout.connect(lambda: self._dismiss(toast))
        toast._hold_timer.start(hold_ms)

    def _dismiss(self, toast: Toast) -> None:
        if toast not in self._toasts:
            return
        anim = QVariantAnimation(self)
        anim.setDuration(MOTION["toast_out"])
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        current_x = toast.x()

        def _on_value(v) -> None:
            eased = float(v)
            toast.set_opacity(eased)
            toast.move(int(current_x + (1.0 - eased) * 8), toast.y())

        anim.valueChanged.connect(_on_value)
        anim.finished.connect(lambda: self._remove(toast))
        self._anims[toast] = anim
        anim.start()

    def _remove(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        self._anims.pop(toast, None)
        toast.deleteLater()
        self._reposition()

    def _reposition(self) -> None:
        y = 16
        x = self.width() - 18
        for toast in self._toasts:
            toast.move(x - toast.width(), y)
            y += toast.height() + 8

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._reposition()


class BusyBar(EWidget):
    """处理中：斜线纹来回扫，完成后停。每个动画对应一次操作。"""

    def __init__(self, parent=None, *, height: int = 4):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._phase += 0.03
        if self._phase > 1.0:
            self._phase = 0.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        colors = pal()
        p.fillRect(QRectF(0, 0, self.width(), self.height()), colors.chip)
        x = self.width() * self._phase
        width = self.width() * 0.28
        p.save()
        p.setClipRect(QRectF(0, 0, self.width(), self.height()))
        paint.paint_hatch(p, QRectF(x - width, 0, width, self.height()), colors.ink, alpha=0.6)
        p.restore()
        p.end()


class InfoFlag(EWidget):
    """提示：白底细框，前面一面黄色小旗。告警不用红色。"""

    def __init__(self, text: str, parent=None, *, kind: str = "info"):
        super().__init__(parent)
        self._text = text
        self._kind = kind
        self._font = F.font(12, cjk_first=True)
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def set_text(self, text: str) -> None:
        self._text = text
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        p.setPen(colors.rule)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        p.fillRect(QRectF(0, 0, 3, self.height()), colors.signal)
        paint_icon(
            p, "warning" if self._kind == "warn" else "info",
            QRectF(14, (self.height() - 14) / 2, 14, 14), colors.ink2, stroke=1.5,
        )
        paint.draw_text(p, QRectF(36, 0, self.width() - 48, self.height()), self._text, self._font, colors.ink2)
        p.end()


class QRCanvas(EWidget):
    """二维码画布：白底 + 墨色模块，直角。"""

    def __init__(self, parent=None, *, size: int = 208):
        super().__init__(parent)
        self._matrix: list[list[bool]] = []
        self._size = size
        self.setFixedSize(size, size)

    def set_matrix(self, matrix: list[list[bool]]) -> None:
        self._matrix = matrix
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        from PyQt6.QtGui import QColor

        # 二维码必须保持高对比：无论深浅主题都固定白底黑模块
        paper = QColor("#FFFFFF")
        ink = QColor("#161616")
        p.fillRect(QRectF(0, 0, self.width(), self.height()), paper)
        if not self._matrix:
            p.end()
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        n = len(self._matrix)
        quiet = 2
        cell = min(self.width(), self.height()) / (n + quiet * 2)
        offset = (self.width() - cell * n) / 2
        oy = (self.height() - cell * n) / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        for y, row in enumerate(self._matrix):
            for x, value in enumerate(row):
                if value:
                    p.drawRect(QRectF(offset + x * cell, oy + y * cell, cell + 0.35, cell + 0.35))
        p.end()
