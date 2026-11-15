# -*- coding: utf-8 -*-
"""毛玻璃 / 窗口特效接口检查（真实模糊由 DWM 合成，无法在离屏环境断言像素）。"""
from __future__ import annotations


def test_glass_constants():
    from endbox.ui import glass

    assert glass.DWMSBT_MAINWINDOW == 2
    assert glass.DWMSBT_TRANSIENTWINDOW == 3
    assert glass.DWMSBT_NONE == 1


def test_enable_realtime_glass_without_window_returns_false():
    from endbox.ui import glass

    assert glass.enable_realtime_glass(None) is False
    glass.disable_realtime_glass(None)   # 不抛异常


def test_click_through_without_window_is_false():
    from endbox.ui import glass

    assert glass.set_click_through(None) is False
    glass.set_dark_titlebar(None, True)  # 不抛异常
