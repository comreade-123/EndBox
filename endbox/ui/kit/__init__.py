# -*- coding: utf-8 -*-
"""EndBox 自绘部件套件。

规则（来自 endfield-ui，所有部件共享）：
  · 切掉一个角 = 可以按；正文与容器是直角，圆角一律 0；
  · 唯一强调色只做填充；红色只给删除；
  · 细线分区；不用阴影、发光、渐变；
  · 字母拉宽、中文加粗；数字宽体等宽。
"""
from __future__ import annotations

from endbox.ui.kit.base import EWidget, restyle, pal
from endbox.ui.kit.text import Badge, FieldText, Label, NumberStat, SectionHead
from endbox.ui.kit.buttons import CutButton, IconButton, NavItem, Segmented, Toggle
from endbox.ui.kit.panels import (
    Avatar,
    EmptyState,
    Hairline,
    HatchBand,
    Panel,
    ProgressBar,
    ScrollArea,
    Spacer,
)
from endbox.ui.kit.feedback import BusyBar, InfoFlag, QRCanvas, Toast, ToastHost
from endbox.ui.kit.mathview import LaTeXView, has_math_renderer, render_latex
from endbox.ui.kit.dialog import ConfirmDialog, confirm

__all__ = [
    "EWidget",
    "restyle",
    "pal",
    "Label",
    "SectionHead",
    "FieldText",
    "Badge",
    "NumberStat",
    "CutButton",
    "IconButton",
    "NavItem",
    "Segmented",
    "Toggle",
    "Panel",
    "Hairline",
    "ScrollArea",
    "EmptyState",
    "HatchBand",
    "Avatar",
    "ProgressBar",
    "Spacer",
    "Toast",
    "ToastHost",
    "BusyBar",
    "InfoFlag",
    "QRCanvas",
    "LaTeXView",
    "render_latex",
    "has_math_renderer",
    "ConfirmDialog",
    "confirm",
]
