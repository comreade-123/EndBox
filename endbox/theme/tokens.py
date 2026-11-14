# -*- coding: utf-8 -*-
"""EndBox 设计 token。

变体卡：夜班（黄 #FFFA00 / 中性纸 / standard 切角 / 等高线底纹 / compact 密度 / calm 动效），默认深色。
主视觉：排轴时间轴 + 干员编号墙。
页面类型：主窗口壳是控制台，账号与设置页是阅读页。

固定规则（来自 endfield-ui）：
  · 只有一种强调色，只表示当前位置 / 可点击 / 需要留意，只做填充；
  · 切掉一个角 = 可以按，正文与容器都是直角，圆角一律 0；
  · 红色只给删除；
  · 细线分区，不用投影、发光和装饰性渐变；
  · 数字都是真实数据，宽体大写英文小字配粗体中文。
"""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor

from endbox.core.event_bus import Topics, bus


def qc(hex_color: str, alpha: int = 255) -> QColor:
    """#RRGGBB → QColor（可附加 alpha）。"""
    color = QColor(hex_color)
    color.setAlpha(alpha)
    return color


@dataclass(frozen=True)
class _PaletteData:
    name: str
    paper: str
    panel: str
    ink: str
    ink2: str
    rule: str
    chip: str
    signal: str
    on_signal: str
    trace: str
    tag_bg: str
    tag_fg: str
    danger: str
    scrim: str
    window: str
    sidebar: str
    sidebar_alpha: int
    topbar_alpha: int
    content_alpha: int
    panel_alpha: int
    input_bg: str
    hover: str
    hatch_alpha: float
    contour_alpha: float
    glass_tint: str


DARK = _PaletteData(
    name="dark",
    paper="#191919",
    panel="#222222",
    ink="#EDEDED",
    ink2="#A3A3A3",
    rule="#353535",
    chip="#2E2E2E",
    signal="#FFFA00",
    on_signal="#161616",
    trace="#FFFA00",
    tag_bg="#3A3A3A",
    tag_fg="#EDEDED",
    danger="#FF8A7A",
    scrim="#000000A0",
    window="#1B1B1B",
    sidebar="#141414",
    sidebar_alpha=176,
    topbar_alpha=110,
    content_alpha=238,
    panel_alpha=232,
    input_bg="#202020",
    hover="#2A2A2A",
    hatch_alpha=0.22,
    contour_alpha=0.09,
    glass_tint="#8C161616",
)

LIGHT = _PaletteData(
    name="light",
    paper="#F4F4F2",
    panel="#FFFFFF",
    ink="#161616",
    ink2="#5C5C5C",
    rule="#D9D9D6",
    chip="#E4E4E0",
    signal="#FFFA00",
    on_signal="#161616",
    trace="#161616",
    tag_bg="#161616",
    tag_fg="#FFFFFF",
    danger="#B3261E",
    scrim="#16161680",
    window="#F2F2EF",
    sidebar="#EDEDEA",
    sidebar_alpha=214,
    topbar_alpha=150,
    content_alpha=250,
    panel_alpha=246,
    input_bg="#FFFFFF",
    hover="#E9E9E5",
    hatch_alpha=0.22,
    contour_alpha=0.09,
    glass_tint="#D0F4F4F2",
)


class Palette:
    """把原始色值包成现成的 QColor 属性，供绘制代码直接使用。"""

    def __init__(self, data: _PaletteData):
        self._data = data
        for field in (
            "paper", "panel", "ink", "ink2", "rule", "chip", "signal", "on_signal",
            "trace", "tag_bg", "tag_fg", "danger", "scrim", "window", "input_bg", "hover",
        ):
            setattr(self, field, qc(getattr(data, field)))

        # 毛玻璃表面：底色 + alpha
        self.sidebar = qc(data.sidebar, data.sidebar_alpha)
        self.topbar = qc(data.paper, data.topbar_alpha)
        self.content = qc(data.paper, data.content_alpha)
        self.panel_surface = qc(data.panel, data.panel_alpha)
        self.glass = qc(data.glass_tint)
        self.hatch = qc(data.ink)
        self.hatch.setAlphaF(data.hatch_alpha)
        self.contour = qc(data.ink)
        self.contour.setAlphaF(data.contour_alpha)
        self.is_dark = data.name == "dark"
        self.name = data.name

    def alpha(self, color: QColor, value: int) -> QColor:
        out = QColor(color)
        out.setAlpha(value)
        return out

    @property
    def data(self) -> _PaletteData:
        return self._data

    @property
    def tri(self) -> tuple[QColor, QColor, QColor]:
        """三色条：品红 / 强调色 / 薄荷绿。"""
        return qc("#FF00F0"), qc(self._data.signal), qc("#00FFA2")


# 圆角一律 0；切角尺寸
CUT_SMALL = 7
CUT_BUTTON = 8
CUT_PANEL = 26
CUT_FRAME = 52
HAIRLINE = 1
STRIP = 3
LINE_TAB = 5
TRACE_BAR = 4

# 间距 / 密度（compact）
GAP = 16
PAD = 16
ROW = 38
TYPE = {
    "eyebrow": 11,
    "field": 13,
    "ui": 14,
    "body": 15,
    "h2": 20,
    "title": 27,
    "h1": 36,
    "num": 54,
}

# 动效（毫秒与曲线）
MOTION = {
    "press": 100,
    "row": 200,
    "wipe": 280,
    "edge": 300,
    "view": 320,
    "sheet": 360,
    "toast": 280,
    "toast_hold": 2200,
    "toast_out": 160,
}

MODE_COMMAND_BAR = "#161616"


class ThemeManager(QObject):
    """主题切换单例：视图订阅 changed 后重建自己的样式。"""

    changed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._mode = "dark"
        self.dark = Palette(DARK)
        self.light = Palette(LIGHT)

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def p(self) -> Palette:
        return self.dark if self._mode == "dark" else self.light

    def set_mode(self, mode: str, *, emit: bool = True) -> None:
        mode = "light" if mode == "light" else "dark"
        if mode == self._mode:
            return
        self._mode = mode
        if emit:
            self.changed.emit(mode)
            bus.emit(Topics.THEME_CHANGED, mode=mode)

    def toggle(self) -> str:
        self.set_mode("light" if self._mode == "dark" else "dark")
        return self._mode


theme = ThemeManager()
