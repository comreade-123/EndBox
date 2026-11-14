# -*- coding: utf-8 -*-
"""绘制工具：切角、细线、斜线纹、等高线、三色条、文本。

所有部件的形状都从这里生成，保证"切角 = 可点击、直角 = 展示"的一致性。
"""
from __future__ import annotations

import math
from functools import lru_cache

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)

from endbox.theme import tokens


def cut_rect(rect: QRectF, cut: float, corner: str = "br") -> QPainterPath:
    """直角矩形切掉一个角。corner: tl / tr / bl / br（默认右下）。"""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    cut = max(0.0, min(cut, min(w, h) / 2))
    path = QPainterPath()
    if corner == "tl":
        path.moveTo(x, y + cut)
        path.lineTo(x + cut, y)
        path.lineTo(x + w, y)
        path.lineTo(x + w, y + h)
        path.lineTo(x, y + h)
    elif corner == "tr":
        path.moveTo(x, y)
        path.lineTo(x + w - cut, y)
        path.lineTo(x + w, y + cut)
        path.lineTo(x + w, y + h)
        path.lineTo(x, y + h)
    elif corner == "bl":
        path.moveTo(x, y)
        path.lineTo(x + w, y)
        path.lineTo(x + w, y + h)
        path.lineTo(x + cut, y + h)
        path.lineTo(x, y + h - cut)
    else:  # br
        path.moveTo(x, y)
        path.lineTo(x + w, y)
        path.lineTo(x + w, y + h - cut)
        path.lineTo(x + w - cut, y + h)
        path.lineTo(x, y + h)
    path.closeSubpath()
    return path


def fill_cut(p: QPainter, rect: QRectF, color: QColor, cut: float, corner: str = "br") -> None:
    p.fillPath(cut_rect(rect, cut, corner), QBrush(color))


def stroke_cut(
    p: QPainter, rect: QRectF, color: QColor, cut: float, corner: str = "br", width: float = 1.0
) -> None:
    p.strokePath(cut_rect(rect, cut, corner), QPen(color, width))


def fill_rect(p: QPainter, rect: QRectF, color: QColor) -> None:
    p.fillRect(rect, QBrush(color))


# ---- 圆角：只用于窗口外框与面板壳层（用户指定参考鹰角启动器）。
# 内部可点击元素仍然用切角，正文容器仍然直角。

def round_rect(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    r = max(0.0, min(radius, min(rect.width(), rect.height()) / 2))
    path.addRoundedRect(rect, r, r)
    return path


def fill_round(p: QPainter, rect: QRectF, color: QColor, radius: float) -> None:
    p.fillPath(round_rect(rect, radius), QBrush(color))


def stroke_round(
    p: QPainter, rect: QRectF, color: QColor, radius: float, width: float = 1.0
) -> None:
    p.strokePath(round_rect(rect, radius), QPen(color, width))


def window_mask(width: int, height: int, radius: float):
    """窗口圆角遮罩（QRegion）：让毛玻璃、阴影和内容一起被裁圆。"""
    from PyQt6.QtGui import QRegion

    path = round_rect(QRectF(0, 0, width, height), radius)
    return QRegion(path.toFillPolygon().toPolygon())


@lru_cache(maxsize=64)
def hatch_brush(ink_hex: str, alpha: float, period: float = 6.5, line: float = 1.5) -> QBrush:
    """45° 斜线纹（/）：一块 9×9 的贴图平铺。"""
    ink = QColor(ink_hex)
    ink.setAlphaF(alpha)
    # 周期沿 x 方向 = period * sqrt(2)
    step = max(4, int(round(period * math.sqrt(2))))
    pix = QPixmap(step, step)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    pen = QPen(ink, line)
    painter.setPen(pen)
    for offset in (0, step, -step):
        painter.drawLine(offset, step, offset + step, 0)
    painter.end()
    return QBrush(pix)


def paint_hatch(p: QPainter, rect: QRectF, ink: QColor, alpha: float | None = None) -> None:
    a = ink.alphaF() if alpha is None else alpha
    p.fillRect(rect, hatch_brush(ink.name(), a))


def paint_contours(p: QPainter, rect: QRectF, ink: QColor, seed: int = 7) -> None:
    """两角各一小片不闭合等高线，只用于背景质感。"""
    p.save()
    p.setClipRect(rect)
    pen = QPen(ink, 1.0)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    w, h = rect.width(), rect.height()
    # 左上角
    for i in range(4):
        r = QRectF(-w * 0.18 + i * 7, -h * 0.30 + i * 7, w * 0.42 - i * 14, h * 0.66 - i * 14)
        path = _wobbly_ellipse(r, seed + i)
        p.drawPath(path)
    # 右下角
    for i in range(3):
        r = QRectF(w * 0.62 + i * 8, h * 0.52 + i * 8, w * 0.46 - i * 16, h * 0.56 - i * 16)
        if r.width() <= 0 or r.height() <= 0:
            continue
        p.drawPath(_wobbly_ellipse(r, seed * 3 + i))
    p.restore()


def _wobbly_ellipse(rect: QRectF, seed: int) -> QPainterPath:
    path = QPainterPath()
    cx, cy = rect.center().x(), rect.center().y()
    rx, ry = rect.width() / 2, rect.height() / 2
    steps = 72
    for i in range(steps + 1):
        t = 2 * math.pi * i / steps
        wobble = 1 + 0.035 * math.sin(t * 3 + seed) + 0.02 * math.sin(t * 7 + seed * 2)
        x = cx + math.cos(t) * rx * wobble
        y = cy + math.sin(t) * ry * wobble
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    return path


def paint_tri_strip(p: QPainter, rect: QRectF, pal) -> None:
    """3px 三色条：屏幕/页面开始处，详情标题下方。"""
    a, b, c = pal.tri
    third = rect.width() / 3
    p.fillRect(QRectF(rect.x(), rect.y(), third, rect.height()), a)
    p.fillRect(QRectF(rect.x() + third, rect.y(), third, rect.height()), b)
    p.fillRect(QRectF(rect.x() + third * 2, rect.y(), rect.width() - third * 2, rect.height()), c)


def hairline(p: QPainter, x1: float, y1: float, x2: float, y2: float, color: QColor) -> None:
    p.setPen(QPen(color, 1))
    # 对齐到像素中线，细线才不会糊成 2px
    if y1 == y2:
        y = round(y1) + 0.5
        p.drawLine(QPointF(x1, y), QPointF(x2, y))
    elif x1 == x2:
        x = round(x1) + 0.5
        p.drawLine(QPointF(x, y1), QPointF(x, y2))
    else:
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def dashed_rect(p: QPainter, rect: QRectF, color: QColor, width: float = 1.0) -> None:
    pen = QPen(color, width, Qt.PenStyle.DashLine)
    pen.setDashPattern([4, 3])
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))


def draw_text(
    p: QPainter,
    rect: QRectF,
    text: str,
    font: QFont,
    color: QColor,
    align: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
    *,
    elide: bool = False,
) -> None:
    p.setFont(font)
    p.setPen(QPen(color))
    if elide:
        fm = QFontMetricsF(font)
        text = fm.elidedText(text, Qt.TextElideMode.ElideRight, rect.width())
    p.drawText(rect, int(align), text)


def text_width(font: QFont, text: str) -> float:
    return QFontMetricsF(font).horizontalAdvance(text)


def paint_focus(p: QPainter, rect: QRectF, color: QColor) -> None:
    """键盘焦点：trace 色细框，绝不隐藏。"""
    pen = QPen(color, 1.5, Qt.PenStyle.SolidLine)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(rect.adjusted(1, 1, -1, -1))


def arrow(p: QPainter, x: float, y: float, size: float, color: QColor, direction: str = "down") -> None:
    pen = QPen(color, 1.6)
    pen.setCapStyle(Qt.PenCapStyle.SquareCap)
    p.setPen(pen)
    path = QPainterPath()
    if direction == "down":
        path.moveTo(x - size, y - size / 2)
        path.lineTo(x, y + size / 2)
        path.lineTo(x + size, y - size / 2)
    elif direction == "up":
        path.moveTo(x - size, y + size / 2)
        path.lineTo(x, y - size / 2)
        path.lineTo(x + size, y + size / 2)
    elif direction == "right":
        path.moveTo(x - size / 2, y - size)
        path.lineTo(x + size / 2, y)
        path.lineTo(x - size / 2, y + size)
    else:
        path.moveTo(x + size / 2, y - size)
        path.lineTo(x - size / 2, y)
        path.lineTo(x + size / 2, y + size)
    p.drawPath(path)


def tri_marker(p: QPainter, x: float, y: float, size: float, color: QColor, up: bool = True) -> None:
    poly = QPolygonF()
    if up:
        poly << QPointF(x, y) << QPointF(x + size, y + size) << QPointF(x - size, y + size)
    else:
        poly << QPointF(x, y) << QPointF(x + size, y - size) << QPointF(x - size, y - size)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(color))
    p.drawPolygon(poly)


def small_chevron(p: QPainter, rect: QRectF, color: QColor, direction: str = "down") -> None:
    arrow(p, rect.center().x(), rect.center().y(), min(rect.width(), rect.height()) / 2.5, color, direction)
