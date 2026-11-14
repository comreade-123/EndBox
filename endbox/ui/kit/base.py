# -*- coding: utf-8 -*-
"""部件基础设施：主题访问、主题切换刷新。"""
from __future__ import annotations

from PyQt6.QtWidgets import QWidget

from endbox.theme.tokens import Palette, theme


def pal() -> Palette:
    return theme.p


class EWidget(QWidget):
    """自绘部件的共同基类：不填充背景，背景由父容器或自绘逻辑负责。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAutoFillBackground(False)

    def refresh_theme(self) -> None:
        self.update()


def restyle(root: QWidget) -> None:
    """主题切换后刷新整棵控件树（含自定义的 refresh_theme 钩子）。"""
    targets = [root] + list(root.findChildren(QWidget))
    for child in targets:
        child.update()
        hook = getattr(child, "refresh_theme", None)
        if callable(hook):
            try:
                hook()
            except Exception:  # noqa: BLE001 - 单个部件刷新失败不影响整体
                pass
