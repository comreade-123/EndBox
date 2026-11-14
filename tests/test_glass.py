# -*- coding: utf-8 -*-
"""实时毛玻璃底图（GlassField）的裁剪与缩放。"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtGui import QColor, QImage


def _field_image(width: int = 20, height: int = 20) -> QImage:
    """左半红、右半蓝的小图，方便断言裁剪方向。"""
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    for x in range(width):
        color = QColor(200, 30, 30) if x < width // 2 else QColor(30, 30, 200)
        for y in range(height):
            image.setPixelColor(x, y, color)
    return image


def test_frame_returns_requested_size(qapp):
    from endbox.ui.glass import GlassField

    field = GlassField(_field_image(), QPoint(0, 0), QSize(120, 120))
    frame = field.frame(QRect(0, 0, 120, 120), QSize(120, 120))
    assert frame is not None and not frame.isNull()
    assert frame.size() == QSize(120, 120)


def test_frame_crops_the_right_half(qapp):
    from endbox.ui.glass import GlassField

    field = GlassField(_field_image(), QPoint(0, 0), QSize(120, 120))
    frame = field.frame(QRect(60, 0, 60, 120), QSize(60, 120))
    assert frame is not None
    color = frame.toImage().pixelColor(30, 60)
    assert color.blue() > color.red(), color.name()


def test_frame_follows_window_offset(qapp):
    from endbox.ui.glass import GlassField

    # 抓图区域从 (0,0) 开始；窗口挪到 (100,0) 时要拿到右半部分
    field = GlassField(_field_image(), QPoint(0, 0), QSize(240, 120))
    left = field.frame(QRect(0, 0, 120, 120), QSize(20, 20))
    right = field.frame(QRect(120, 0, 120, 120), QSize(20, 20))
    assert left is not None and right is not None
    left_color = left.toImage().pixelColor(10, 10)
    right_color = right.toImage().pixelColor(10, 10)
    assert left_color.blue() < right_color.blue()


def test_frame_outside_field_returns_none(qapp):
    from endbox.ui.glass import GlassField

    field = GlassField(_field_image(), QPoint(0, 0), QSize(120, 120))
    assert field.frame(QRect(900, 900, 120, 120), QSize(120, 120)) is None


def test_frame_still_works_when_partially_outside(qapp):
    from endbox.ui.glass import GlassField

    field = GlassField(_field_image(), QPoint(0, 0), QSize(120, 120))
    frame = field.frame(QRect(100, 100, 120, 120), QSize(120, 120))
    assert frame is not None and not frame.isNull()


def test_empty_field_returns_none(qapp):
    from endbox.ui.glass import GlassField

    field = GlassField(None, QPoint(0, 0), QSize(120, 120))
    assert field.frame(QRect(0, 0, 120, 120), QSize(120, 120)) is None


def test_capture_without_screens_is_none():
    from endbox.ui.glass import GlassField

    assert GlassField.capture([]) is None
    assert GlassField.capture(None) is None


def test_glass_follow_default_on():
    from endbox import config

    assert config.DEFAULTS["app"]["glass_follow"] is True
