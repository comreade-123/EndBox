# -*- coding: utf-8 -*-
"""程序图标与品牌资源。

图标来自用户提供的 `icon.png`，由 `tools/make_icon.py` 裁边、缩放生成：
`assets/branding/endbox.png`（512）与 `assets/branding/endbox.ico`（多尺寸）。
缺失时退回运行时绘制的占位图标。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

from endbox import config

SIGNAL = QColor("#FFFA00")
INK = QColor("#161616")


def logo_path() -> Path | None:
    for name in ("endbox.png", "endbox.ico"):
        path = config.ASSETS / "branding" / name
        if path.exists():
            return path
    return None


@lru_cache(maxsize=8)
def logo_pixmap(size: int = 0) -> QPixmap:
    """应用图标位图；size=0 时返回原始 512。"""
    path = logo_path()
    if path is not None:
        pix = QPixmap(str(path))
        if not pix.isNull():
            return pix if not size else pix.scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
    return _paint_icon(max(32, size or 256))


def app_icon() -> QIcon:
    icon = QIcon()
    ico = config.ASSETS / "branding" / "endbox.ico"
    if ico.exists() and not QPixmap(str(ico)).isNull():
        icon.addFile(str(ico))
    png = config.ASSETS / "branding" / "endbox.png"
    if png.exists():
        base = QPixmap(str(png))
        for size in (16, 24, 32, 48, 64, 128, 256):
            icon.addPixmap(base.scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
    if icon.isNull():
        for size in (16, 24, 32, 48, 64, 128, 256):
            icon.addPixmap(_paint_icon(size))
    return icon


# -- 运行时绘制的兜底图标（品牌资源缺失时） --------------------------------
def _paint_icon(size: int) -> QPixmap:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    rect = QRectF(1, 1, size - 2, size - 2)
    cut = size * 0.26
    path = _cut(rect, cut)
    p.fillPath(path, INK)
    p.setPen(QColor("#2E2E2E"))
    p.drawPath(path)
    p.fillRect(QRectF(size * 0.14, size * 0.14, size * 0.18, 3), SIGNAL)
    font = QFont("Saira Wide")
    font.setPixelSize(int(size * 0.36))
    font.setWeight(QFont.Weight.Bold)
    p.setFont(font)
    p.setPen(SIGNAL)
    p.drawText(QRectF(0, size * 0.18, size, size * 0.6), int(Qt.AlignmentFlag.AlignCenter), "EB")
    p.end()
    return pix


def _cut(rect: QRectF, cut: float):
    from PyQt6.QtGui import QPainterPath

    path = QPainterPath()
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    path.moveTo(x, y)
    path.lineTo(x + w, y)
    path.lineTo(x + w, y + h - cut)
    path.lineTo(x + w - cut, y + h)
    path.lineTo(x, y + h)
    path.closeSubpath()
    return path
