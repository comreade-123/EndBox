# -*- coding: utf-8 -*-
"""HUD 覆盖层：窗口本体 + 绘制画布 + "灵动岛"三段式动画编排。

HUD 只画 **账号 / 电池 / 时钟**；标题行显示 ``/// ENDBOX · HUD`` + Windows 用户名。
排轴运行态由排轴模块自己的提示条负责，两者互相独立：本文件不读、不订阅排轴数据。

动画骨骼移植自 EndfieldCharge（``zmd-charge-main``）的 Avalonia 灵动岛，
按终末地规则改写视觉：圆角胶囊 → 切角方框，发光波纹 → 1px 同心方框，
电量圆弧 → 10 格刻度条；没有渐变、投影、发光、圆角。

时间线（``curve_at`` 逐通道求值，总长约 720ms，单段 280–340ms 量级）::

    0.00–0.28  切角方块标记弹出（整体 scale 0.74→1，curve_at("snap")）
    0.18–0.52  面板撑高（mech）；标记从正中滑到左侧（ease）
    0.20–0.70  三圈同心切角方框波纹从标记处扩散并淡出（细线，不发光）
    0.24–0.44  "/// ENDBOX · HUD + 用户名" 淡入
    0.56–0.74  标题淡出（收回段开始）
    0.62–0.94  数据行淡入 → 稳定态
    退出：     数据先退场，面板收窄 + 整体缩小 + 淡出（mech，240ms）

两种布局：``compact``（560×64 单行）与 ``standard``（720×88 两行：账号+时钟 / 电池详情）。
防闪烁：画布用 ``state.render_key()`` 做内容指纹，只有真的变化才 ``update()``；
窗口只在尺寸 / 透明度真的变化时改 Win32 属性；动画按 24 帧量化，不 60fps 刷透明窗口。
"""
from __future__ import annotations

import os
from functools import lru_cache

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QWidget

from endbox.core.log import get_logger
from endbox.core.text import t
from endbox.modules.hud import state as S
from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.theme.motion import curve_at
from endbox.ui import glass
from endbox.ui.icons import paint_icon
from endbox.ui.kit.base import EWidget, pal

log = get_logger("hud.overlay")

# 每种模式的几何（设计坐标，缩放前）
GEOMETRY = {
    "compact": {
        "w": 560.0,
        "h": 76.0,
        "panel_a": 56.0,
        "panel_b": 76.0,
        "panel_c": 64.0,
        "cut": 9.0,
        "pad": 10.0,
        "marker": 30.0,
        "marker_gap": 8.0,
        "sep": 10.0,
    },
    "standard": {
        "w": 720.0,
        "h": 96.0,
        "panel_a": 80.0,
        "panel_b": 96.0,
        "panel_c": 88.0,
        "cut": 10.0,
        "pad": 12.0,
        "marker": 32.0,
        "marker_gap": 10.0,
        "sep": 14.0,
    },
}
MARGIN = 18           # 距离屏幕可用区顶部的间距
INTRO_MS = 720
OUTRO_MS = 240
ANIM_STEPS = 24       # 入场动画限帧（约 30fps）
OUTRO_STEPS = 8

TITLE_EYEBROW = t("hud.title")
EMPTY_TEXT = t("hud.overlay.empty")
BATTERY_TICK_W = 2.5
BATTERY_TICK_GAP = 1.5

DEFAULT_SHOW = S.DEFAULT_SHOW


# ---------------------------------------------------------------------------
# 曲线通道 / 几何
# ---------------------------------------------------------------------------

def _clamp01(value: float) -> float:
    return 0.0 if value <= 0.0 else (1.0 if value >= 1.0 else value)


def _seg(progress: float, start: float, end: float, kind: str = "mech") -> float:
    """把 0–1 的线性进度切出一段 [start, end] 并用指定曲线求值。"""
    if end <= start:
        return 1.0 if progress >= end else 0.0
    return curve_at(kind, _clamp01((progress - start) / (end - start)))


def choreography(progress: float, animations: bool = True) -> dict:
    """线性进度 → 各通道的缓动值。关掉动效时直接给稳定态。"""
    p = 1.0 if not animations else _clamp01(progress)
    appear = _seg(p, 0.0, 0.28, "snap")
    expand = _seg(p, 0.18, 0.52, "mech")
    settle = _seg(p, 0.62, 0.94, "mech")
    slide = _seg(p, 0.20, 0.55, "ease")
    title_in = _seg(p, 0.24, 0.44, "snap")
    title_out = _seg(p, 0.56, 0.74, "mech")
    data_in = _seg(p, 0.68, 0.96, "snap")
    ripple_grow = _seg(p, 0.20, 0.70, "mech")
    ripple_in = _seg(p, 0.20, 0.28, "snap")
    ripple_out = _seg(p, 0.62, 0.80, "mech")
    return {
        "p": p,
        "appear": appear,
        "expand": expand,
        "settle": settle,
        "slide": slide,
        "title_op": title_in * (1.0 - title_out),
        "data_op": data_in,
        "ripple": ripple_grow,
        "ripple_op": ripple_in * (1.0 - ripple_out),
    }


def design_size(mode: str) -> QSize:
    """设计尺寸（缩放前），单位是设计像素。"""
    geo = GEOMETRY[S.normalize_mode(mode)]
    return QSize(int(geo["w"]), int(geo["h"]))


def panel_height(progress: float, mode: str = "compact", animations: bool = True) -> float:
    geo = GEOMETRY[S.normalize_mode(mode)]
    ch = choreography(progress, animations)
    return geo["panel_a"] + (geo["panel_b"] - geo["panel_a"]) * ch["expand"] - (
        geo["panel_b"] - geo["panel_c"]
    ) * ch["settle"]


def compute_position(
    anchor: str,
    size: QSize,
    available: QRect,
    offset_x: int = 0,
    offset_y: int = 0,
) -> QPoint:
    """按锚点 + 像素偏移算覆盖层左上角（屏幕可用区内，顶部留 MARGIN）。"""
    anchor = S.normalize_anchor(anchor)
    if anchor == "top-left":
        x = available.x() + MARGIN
    elif anchor == "top-right":
        x = available.x() + available.width() - size.width() - MARGIN
    else:
        x = available.x() + (available.width() - size.width()) // 2
    y = available.y() + MARGIN
    return QPoint(
        int(x) + S.clamp_offset(offset_x, "x"),
        int(y) + S.clamp_offset(offset_y, "y"),
    )


def screen_by_name(name: str):
    """按 QScreen.name() 找屏幕；找不回来退主屏。没有屏幕时返回 None。"""
    screens = QGuiApplication.screens()
    for screen in screens:
        if name and screen.name() == name:
            return screen
    primary = QGuiApplication.primaryScreen()
    return primary or (screens[0] if screens else None)


def _island_path(rect: QRectF, cut: float) -> QPainterPath:
    """切角方框（左上 / 右下各切一刀），替代参考实现的全圆胶囊。"""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    cut = max(0.0, min(cut, min(w, h) / 3.0))
    path = QPainterPath()
    path.moveTo(x + cut, y)
    path.lineTo(x + w, y)
    path.lineTo(x + w, y + h - cut)
    path.lineTo(x + w - cut, y + h)
    path.lineTo(x, y + h)
    path.lineTo(x, y + cut)
    path.closeSubpath()
    return path


# ---------------------------------------------------------------------------
# 字体（设计坐标；QFont 隐式共享，缓存复用）
# ---------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _f_eyebrow() -> QFont:
    return F.eyebrow_font(9)


@lru_cache(maxsize=None)
def _f_battery() -> QFont:
    return F.number_font(14)


@lru_cache(maxsize=None)
def _f_value() -> QFont:
    return F.font(12, weight=QFont.Weight.DemiBold, cjk_first=True)


@lru_cache(maxsize=None)
def _f_note() -> QFont:
    return F.font(9, cjk_first=True)


@lru_cache(maxsize=None)
def _f_badge() -> QFont:
    return F.field_font(8)


@lru_cache(maxsize=None)
def _f_clock() -> QFont:
    return F.field_font(12)


@lru_cache(maxsize=None)
def _f_title() -> QFont:
    return F.display_font(22)


@lru_cache(maxsize=None)
def _f_empty() -> QFont:
    return F.font(11, cjk_first=True)


@lru_cache(maxsize=None)
def _f_initial() -> QFont:
    return F.font(8, weight=QFont.Weight.Bold, cjk_first=True)


# ---------------------------------------------------------------------------
# 头像（磁盘真实图片；缺图/坏图 → 空框，不画假头像）
# ---------------------------------------------------------------------------

_AVATAR_CACHE: dict[tuple, QPixmap | None] = {}


def _avatar_pixmap(path: str | None, size: float) -> QPixmap | None:
    if not path:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    key = (path, int(mtime), int(size))
    if key in _AVATAR_CACHE:
        return _AVATAR_CACHE[key]
    pixmap = QPixmap(path)
    if pixmap.isNull():
        _AVATAR_CACHE[key] = None
        return None
    target = max(8, int(size * 3))  # 3x 采样，缩放后仍清晰
    scaled = pixmap.scaled(
        target,
        target,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - target) // 2)
    y = max(0, (scaled.height() - target) // 2)
    cropped = scaled.copy(x, y, target, target)
    if len(_AVATAR_CACHE) > 64:
        _AVATAR_CACHE.clear()
    _AVATAR_CACHE[key] = cropped
    return cropped


def _clear_avatar_cache() -> None:
    _AVATAR_CACHE.clear()


# ---------------------------------------------------------------------------
# 画布
# ---------------------------------------------------------------------------

class HudCanvas(EWidget):
    """HUD 的一帧画面。

    覆盖层窗口和 HUD 控制页的预览共用这一个绘制实现；``set_snapshot``
    用 ``state.render_key`` 去重，内容没变时不会触发 ``update()``（防闪烁）。
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self._snapshot: dict = {}
        self._progress = 1.0
        self._closing = 0.0
        self._render_key: tuple | None = None

    # -- 输入 ---------------------------------------------------------------
    def set_snapshot(self, snapshot: dict | None) -> bool:
        """更新数据；返回是否真的需要重画。"""
        self._snapshot = dict(snapshot or {})
        key = (S.render_key(self._snapshot), pal().name)
        if key == self._render_key:
            return False
        self._render_key = key
        self.update()
        return True

    def set_progress(self, value: float) -> None:
        value = _clamp01(float(value))
        if value != self._progress:
            self._progress = value
            self.update()

    def set_closing(self, value: float) -> None:
        value = _clamp01(float(value))
        if value != self._closing:
            self._closing = value
            self.update()

    @property
    def snapshot(self) -> dict:
        return self._snapshot

    @property
    def progress(self) -> float:
        return self._progress

    @property
    def mode(self) -> str:
        return S.normalize_mode(self._snapshot.get("mode"))

    def _geo(self) -> dict:
        return GEOMETRY[self.mode]

    # -- 布局（供测试与绘制共用） -------------------------------------------
    def _show(self, key: str) -> bool:
        show = self._snapshot.get("show") or {}
        return bool(show.get(key, DEFAULT_SHOW.get(key, True)))

    def _cells(self, row: str) -> list[dict]:
        """某一行要画的显示项（已按开关过滤）。"""
        order = {
            "compact": ["account", "battery", "clock"],
            "top": ["account", "clock"],
            "main": ["battery"],
        }[row]
        cells: list[dict] = []
        for key in order:
            if not self._show(key):
                continue
            cells.append({"key": key, "eyebrow": self._eyebrow(key), "data": self._cell_data(key)})
        return cells

    @staticmethod
    def _eyebrow(key: str) -> str:
        return t(f"hud.overlay.eyebrows.{key}")

    def _cell_data(self, key: str) -> dict:
        st = self._snapshot
        if key == "account":
            return S.account_view(st.get("account"))
        if key == "battery":
            return S.battery_view(st.get("battery"))
        return S.clock_view(st.get("clock"))

    def _cell_width(self, cell: dict, mode: str) -> float:
        data = cell["data"]
        key = cell["key"]
        eyebrow_w = paint.text_width(_f_eyebrow(), cell["eyebrow"]) + 2.0
        if key == "account":
            if not data["connected"]:
                content = paint.text_width(_f_value(), data["text"]) + 12.0
            else:
                avatar = 18.0 if mode == "standard" else 15.0
                content = avatar + 6.0 + 6.0 + 6.0 + min(paint.text_width(_f_value(), data["text"]), 150.0)
        elif key == "battery":
            content = paint.text_width(_f_battery(), data["percent_text"]) + 4.0
            if data["badge"]:
                content += paint.text_width(_f_badge(), data["badge"]) + 4.0
            content += 5.0 + 10 * BATTERY_TICK_W + 9 * BATTERY_TICK_GAP
            if data["low"]:
                content += 8.0
            if mode == "standard":
                content += paint.text_width(_f_note(), data["mode_text"]) + 6.0
                content += paint.text_width(_f_note(), data["remaining_text"]) + 4.0
            content = min(content, 260.0)
        else:  # clock
            content = paint.text_width(_f_clock(), data["text"]) + 2.0
        return max(eyebrow_w, content)

    def _rows_layout(self) -> list[dict]:
        """行布局：每行的 y/height/可用区域与已分配好的单元格。"""
        geo = self._geo()
        mode = self.mode
        panel = self._panel_rect()
        left = geo["pad"] + geo["marker"] + geo["marker_gap"]
        right = geo["w"] - geo["pad"]
        width = max(0.0, right - left)
        if mode == "compact":
            rows = [{"name": "compact", "cells": self._cells("compact")}]
        else:
            rows = [
                {"name": "top", "cells": self._cells("top")},
                {"name": "main", "cells": self._cells("main")},
            ]
        rows = [row for row in rows if row["cells"]]
        if not rows:
            return []
        if len(rows) == 1:
            rows[0]["y"] = panel.y() + 8.0
            rows[0]["height"] = max(20.0, panel.height() - 16.0)
        else:
            gap = 2.0
            body = max(40.0, panel.height() - 20.0)
            row_h = (body - gap) / 2.0
            rows[0]["y"] = panel.y() + 6.0
            rows[0]["height"] = row_h
            rows[1]["y"] = panel.y() + 6.0 + row_h + gap
            rows[1]["height"] = row_h
        for row in rows:
            cells = row["cells"]
            sep = geo["sep"]
            available = max(0.0, width - sep * (len(cells) - 1))
            widths = _fit_widths([self._cell_width(cell, mode) for cell in cells], available)
            x = left
            for cell, cell_w in zip(cells, widths):
                cell["x"] = x
                cell["w"] = cell_w
                x += cell_w + sep
            row["x"] = left
            row["w"] = width
        return rows

    def group_layout(self) -> list[dict]:
        """扁平化的单元格矩形（设计坐标），测试用它检查不溢出。"""
        out: list[dict] = []
        for row in self._rows_layout():
            for cell in row["cells"]:
                out.append({**cell, "row": row["name"], "row_y": row["y"], "row_height": row["height"]})
        return out

    def _panel_rect(self) -> QRectF:
        geo = self._geo()
        height = panel_height(self._progress, self.mode)
        return QRectF(1.0, (geo["h"] - height) / 2.0, geo["w"] - 2.0, height)

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        try:
            w, h = self.width(), self.height()
            geo = self._geo()
            if w <= 4 or h <= 4:
                return
            scale = min(w / geo["w"], h / geo["h"])
            p.translate((w - geo["w"] * scale) / 2.0, (h - geo["h"] * scale) / 2.0)
            p.scale(scale, scale)
            self._draw(p)
        finally:
            p.end()

    def _draw(self, p: QPainter) -> None:
        colors = pal()
        geo = self._geo()
        ch = choreography(self._progress)
        q = _clamp01(self._closing)
        panel = self._panel_rect()
        scale = (0.74 + 0.26 * ch["appear"]) * (1.0 - 0.16 * q)
        alpha = ch["appear"] * (1.0 - q)
        if alpha <= 0.01:
            return

        path = _island_path(panel, geo["cut"])
        p.save()
        p.setOpacity(alpha)
        cx, cy = panel.center().x(), panel.center().y()
        p.translate(cx, cy)
        p.scale(scale, scale)
        p.translate(-cx, -cy)

        p.fillPath(path, colors.panel_surface)
        self._draw_ripples(p, panel, path, ch)
        marker_cx = geo["w"] / 2.0 + (geo["pad"] + geo["marker"] / 2.0 - geo["w"] / 2.0) * ch["slide"]
        self._draw_marker(p, marker_cx, panel)

        rows = self._rows_layout()
        has_content = bool(rows)
        if ch["data_op"] > 0.01:
            p.save()
            p.setOpacity(alpha * ch["data_op"])
            if has_content:
                self._draw_content(p, rows)
            else:
                self._draw_empty(p, panel)
            p.restore()
        if ch["title_op"] > 0.01:
            p.save()
            p.setOpacity(alpha * ch["title_op"])
            self._draw_title(p, panel)
            p.restore()

        p.strokePath(path, QPen(colors.rule, 1.0))
        p.restore()

    # -- 各部件 -------------------------------------------------------------
    def _draw_ripples(self, p: QPainter, panel: QRectF, panel_path: QPainterPath, ch: dict) -> None:
        """同心切角方框波纹：向外扩散并淡出，细线，不发光。"""
        if ch["ripple_op"] <= 0.01:
            return
        colors = pal()
        geo = self._geo()
        origin = QPointF(
            geo["w"] / 2.0 + (geo["pad"] + geo["marker"] / 2.0 - geo["w"] / 2.0) * ch["slide"],
            panel.center().y(),
        )
        p.save()
        p.setClipPath(panel_path)
        for side, peak in ((42.0, 0.50), (56.0, 0.34), (70.0, 0.22)):
            now = side * (0.28 + 0.72 * ch["ripple"])
            alpha = peak * ch["ripple_op"]
            if alpha <= 0.01:
                continue
            color = QColor(colors.ink2)
            color.setAlphaF(alpha)
            rect = QRectF(origin.x() - now / 2.0, origin.y() - now / 2.0, now, now)
            p.strokePath(_island_path(rect, min(10.0, now * 0.12)), QPen(color, 1.0))
        p.restore()

    def _draw_marker(self, p: QPainter, cx: float, panel: QRectF) -> None:
        """左侧黄色切角方块：覆盖层"在场"的唯一强调色填充。"""
        colors = pal()
        geo = self._geo()
        size = geo["marker"]
        rect = QRectF(cx - size / 2.0, panel.center().y() - size / 2.0, size, size)
        p.fillPath(paint.cut_rect(rect, 6.0, "br"), colors.signal)
        paint_icon(p, "hud", rect.adjusted(6.0, 6.0, -6.0, -6.0), colors.on_signal, stroke=1.8)

    def _draw_title(self, p: QPainter, panel: QRectF) -> None:
        """标题态：`/// ENDBOX · HUD` + 当前 Windows 用户名（取不到就写 —）。"""
        colors = pal()
        geo = self._geo()
        cy = panel.center().y()
        username = str(self._snapshot.get("username") or S.MISSING)
        paint.draw_text(
            p,
            QRectF(0, cy - 21, geo["w"], 12),
            TITLE_EYEBROW,
            _f_eyebrow(),
            colors.ink2,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
        )
        paint.draw_text(
            p,
            QRectF(0, cy - 7, geo["w"], 30),
            username,
            _f_title(),
            colors.ink,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            elide=True,
        )

    def _draw_empty(self, p: QPainter, panel: QRectF) -> None:
        colors = pal()
        geo = self._geo()
        x = geo["pad"] + geo["marker"] + geo["marker_gap"]
        rect = QRectF(x, panel.y(), geo["w"] - geo["pad"] - x, panel.height())
        paint.draw_text(p, rect, EMPTY_TEXT, _f_empty(), colors.ink2, elide=True)

    def _draw_content(self, p: QPainter, rows: list[dict]) -> None:
        colors = pal()
        sep = self._geo()["sep"]
        for row in rows:
            cells = row["cells"]
            for index, cell in enumerate(cells):
                self._draw_cell(p, row, cell, colors)
                if index:
                    x = cell["x"] - sep / 2.0
                    paint.hairline(p, x, row["y"] + 2, x, row["y"] + row["height"] - 2, colors.rule)
        if len(rows) == 2:
            y = rows[1]["y"] - 1.0
            paint.hairline(p, rows[0]["x"], y, rows[0]["x"] + rows[0]["w"], y, colors.rule)

    def _draw_cell(self, p: QPainter, row: dict, cell: dict, colors) -> None:
        x, w = cell["x"], cell["w"]
        y = row["y"]
        p.save()
        p.setClipRect(QRectF(x - 1.0, y - 2.0, w + 2.0, row["height"] + 4.0), Qt.ClipOperation.IntersectClip)
        paint.draw_text(p, QRectF(x, y, w, 11), cell["eyebrow"], _f_eyebrow(), colors.ink2, elide=True)
        inner = QRectF(x, y + 12.0, w, max(12.0, row["height"] - 12.0))
        key = cell["key"]
        if key == "account":
            self._draw_account(p, inner, cell["data"], colors)
        elif key == "battery":
            self._draw_battery(p, inner, cell["data"], colors, mode=self.mode)
        else:
            paint.draw_text(p, inner, cell["data"]["text"], _f_clock(), colors.ink, elide=True)
        p.restore()

    def _draw_avatar(self, p: QPainter, rect: QRectF, path: str | None, fallback: str, colors) -> None:
        pixmap = _avatar_pixmap(path, rect.width())
        if pixmap is not None:
            p.save()
            p.setClipPath(paint.cut_rect(rect, 3.0, "br"))
            p.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))
            p.restore()
        else:
            paint.stroke_cut(p, rect, colors.rule, 3.0, "br", 1.0)
            if fallback:
                paint.draw_text(p, rect, fallback, _f_initial(), colors.ink2, Qt.AlignmentFlag.AlignCenter)

    def _draw_account(self, p: QPainter, rect: QRectF, data: dict, colors) -> None:
        if not data["connected"]:
            self._draw_flag(p, rect, data["text"], _f_value(), colors)
            return
        size = 18.0 if self.mode == "standard" else 15.0
        cy = rect.center().y()
        self._draw_avatar(p, QRectF(rect.x(), cy - size / 2.0, size, size), data["avatar"], data["text"][:1], colors)
        x = rect.x() + size + 6.0
        online = data["online"]
        dot = QRectF(x, cy - 3.0, 6.0, 6.0)
        if online is True:
            p.fillRect(dot, colors.signal)
        elif online is False:
            p.setPen(QPen(colors.ink2, 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(dot.x() + 0.5, dot.y() + 0.5, 5.0, 5.0))
        x += 6.0 + 6.0
        color = colors.ink2 if data["text"] == S.MISSING else colors.ink
        paint.draw_text(
            p,
            QRectF(x, rect.y(), max(0.0, rect.right() - x), rect.height()),
            data["text"],
            _f_value(),
            color,
            elide=True,
        )

    def _draw_battery(self, p: QPainter, rect: QRectF, data: dict, colors, *, mode: str) -> None:
        x = rect.x()
        cy = rect.center().y()
        if data["low"]:
            p.fillRect(QRectF(x, cy - 2.0, 4.0, 4.0), colors.signal)
            x += 8.0
        percent_text = data["percent_text"]
        number_font = _f_battery()
        paint.draw_text(
            p,
            QRectF(x, rect.y(), paint.text_width(number_font, percent_text) + 2.0, rect.height()),
            percent_text,
            number_font,
            colors.ink if data["percent"] is not None else colors.ink2,
        )
        x += paint.text_width(number_font, percent_text) + 4.0
        if data["badge"]:
            badge = data["badge"]
            paint.draw_text(
                p,
                QRectF(x, rect.y() + 3.0, 40.0, rect.height() - 3.0),
                badge,
                _f_badge(),
                colors.ink2,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            )
            x += paint.text_width(_f_badge(), badge) + 4.0
        x += 5.0
        tick_w, tick_gap, tick_h = BATTERY_TICK_W, BATTERY_TICK_GAP, 11.0
        ticks = data["ticks"]
        if mode == "standard":
            tick_h = 13.0
        room = max(0.0, rect.right() - x)
        count = min(10, int((room + tick_gap) / (tick_w + tick_gap))) if room > tick_w else 0
        ticks_w = count * tick_w + max(0, count - 1) * tick_gap
        for index in range(count):
            rect_tick = QRectF(x + index * (tick_w + tick_gap), cy - tick_h / 2.0, tick_w, tick_h)
            if ticks is not None and index < ticks:
                p.fillRect(rect_tick, colors.signal if data["low"] else colors.ink)
            else:
                p.setPen(QPen(QColor(colors.rule), 1.0))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(
                    QRectF(rect_tick.x() + 0.5, rect_tick.y() + 0.5, rect_tick.width() - 1.0, rect_tick.height() - 1.0)
                )
        if mode == "standard":
            x += ticks_w + 6.0
            paint.draw_text(
                p,
                QRectF(x, rect.y(), max(0.0, rect.right() - x), rect.height()),
                data["detail_text"],
                _f_note(),
                colors.ink2,
                elide=True,
            )

    def _draw_flag(self, p: QPainter, rect: QRectF, text: str, font: QFont, colors) -> None:
        """黄色小旗 + 墨线：告警 / 未连接（不用红色）。"""
        p.fillRect(QRectF(rect.x(), rect.center().y() - 2.5, 5.0, 5.0), colors.signal)
        paint.draw_text(
            p,
            QRectF(rect.x() + 9.0, rect.y(), max(0.0, rect.width() - 9.0), rect.height()),
            text,
            font,
            colors.ink2,
            elide=True,
        )


def _fit_widths(widths: list[float], available: float, floor: float = 30.0) -> list[float]:
    """把若干测量宽度塞进 available；总和恒等于 available（不会溢出右边界）。"""
    count = len(widths)
    if count == 0:
        return []
    available = max(0.0, float(available))
    total = float(sum(widths))
    if total <= available:
        if total <= 0:
            return [available / count for _ in range(count)]
        extra = available - total
        return [width + extra / count for width in widths]
    floor = min(floor, available / count)
    if available <= floor * count:
        return [available / count for _ in range(count)]
    span = total - floor * count
    factor = (available - floor * count) / span if span > 0 else 1.0
    fitted = [floor + (width - floor) * factor for width in widths]
    scale = available / sum(fitted)
    return [width * scale for width in fitted]


# ---------------------------------------------------------------------------
# 覆盖层窗口
# ---------------------------------------------------------------------------

class HudWindow(QWidget):
    """无边框 / 置顶 / 不进任务栏 / 鼠标穿透 / 不抢焦点的覆盖层窗口。

    窗口只创建一次，反复 show/hide 复用同一个实例（由 HudService 持有）。
    防闪烁：固定尺寸（``setFixedSize``）、只在透明度真的变化时改窗口属性、
    内容指纹不变不重画、动画限帧。
    """

    dismissed = pyqtSignal()

    def __init__(self, service):
        super().__init__(None)
        self._service = service
        self.setObjectName("hud_overlay")
        self.setWindowTitle("EndBox HUD")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.canvas = HudCanvas(self)
        self.canvas.setGeometry(0, 0, self.width(), self.height())

        self._snapshot: dict = {}
        self._anchor = "top-center"
        self._offset_x = 0
        self._offset_y = 0
        self._screen_name = ""
        self._geometry_key: tuple | None = None
        self._opacity_applied: float | None = None
        self._native_attempted = False
        self._closing = False
        self._intro_step = -1
        self._outro_step = -1

        self._intro = QVariantAnimation(self)
        self._intro.setStartValue(0.0)
        self._intro.setEndValue(1.0)
        self._intro.setDuration(INTRO_MS)
        self._intro.valueChanged.connect(self._on_intro_value)
        self._intro.finished.connect(self._on_intro_finished)

        self._outro = QVariantAnimation(self)
        self._outro.setStartValue(0.0)
        self._outro.setEndValue(1.0)
        self._outro.setDuration(OUTRO_MS)
        self._outro.valueChanged.connect(self._on_outro_value)
        self._outro.finished.connect(self._on_outro_finished)

        self.apply_snapshot(service.snapshot() if service is not None else {})

    # -- 外部接口 -----------------------------------------------------------
    def apply_snapshot(self, snapshot: dict | None) -> bool:
        """应用新快照；只有几何 / 透明度 / 内容真的变化才动窗口。返回是否重画了画布。"""
        snap = dict(snapshot or {})
        self._snapshot = snap
        self._anchor = S.normalize_anchor(snap.get("anchor", self._anchor))
        self._offset_x = S.clamp_offset(snap.get("offset_x", self._offset_x), "x")
        self._offset_y = S.clamp_offset(snap.get("offset_y", self._offset_y), "y")
        self._screen_name = str(snap.get("screen") or "")
        mode = S.normalize_mode(snap.get("mode"))
        scale = S.clamp_scale(snap.get("scale", 1.0))
        design = design_size(mode)
        size = QSize(
            max(120, int(round(design.width() * scale))),
            max(24, int(round(design.height() * scale))),
        )
        geometry_key = (mode, size.width(), size.height())
        if geometry_key != self._geometry_key:
            self._geometry_key = geometry_key
            self.setFixedSize(size)
            self.canvas.setGeometry(0, 0, size.width(), size.height())
        opacity = S.clamp_opacity(snap.get("opacity", 0.96))
        if self._opacity_applied is None or abs(opacity - self._opacity_applied) > 1e-3:
            self._opacity_applied = opacity
            self.setWindowOpacity(opacity)
        repainted = self.canvas.set_snapshot(snap)
        if self.isVisible():
            self.place()
        return repainted

    def show_overlay(self, snapshot: dict | None = None) -> None:
        """显示并播放"出现→撑开→收回"三段式。重复调用 = 重播（不新建窗口）。"""
        if snapshot is not None:
            self.apply_snapshot(snapshot)
        self._closing = False
        self.canvas.set_closing(0.0)
        self.place()
        if not self.isVisible():
            self.show()
            self.raise_()
        self._apply_native()
        self.play()

    def hide_overlay(self, *, animate: bool = True) -> None:
        if not self.isVisible():
            return
        self._intro.stop()
        if not animate or not bool(self._animations_enabled(self._service)):
            self._finish_hide()
            return
        self._closing = True
        self._outro.stop()
        self._outro.setStartValue(0.0)
        self._outro.setEndValue(1.0)
        self._outro.start()

    def play(self) -> None:
        """重播入场动画；动效关闭时直接稳定态。"""
        self._outro.stop()
        self._closing = False
        self.canvas.set_closing(0.0)
        if not bool(self._animations_enabled(self._service)):
            self.canvas.set_progress(1.0)
            return
        self._intro.stop()
        self._intro_step = -1
        self.canvas.set_progress(0.0)
        self._intro.start()

    def stop(self) -> None:
        """彻底停止：停止动画、隐藏、删除延迟。由服务调用。"""
        self._intro.stop()
        self._outro.stop()
        self.hide()
        self.deleteLater()

    # -- 位置 / 原生属性 -----------------------------------------------------
    def place(self) -> None:
        screen = screen_by_name(self._screen_name)
        if screen is None:
            return
        target = compute_position(
            self._anchor,
            self.size(),
            screen.availableGeometry(),
            self._offset_x,
            self._offset_y,
        )
        if target != self.pos():
            self.move(target)

    def _apply_native(self) -> None:
        """双保险：Qt 的 WindowTransparentForInput + Win32 WS_EX_TRANSPARENT。"""
        if self._native_attempted:
            return
        self._native_attempted = True
        if not glass.set_click_through(self, True):
            log.debug("click-through native style not applied (window may be offscreen)")

    # -- 动画（限帧，避免 60fps 刷透明窗口） ---------------------------------
    def _on_intro_value(self, value) -> None:
        step = int(round(_clamp01(float(value)) * ANIM_STEPS))
        if step == self._intro_step:
            return
        self._intro_step = step
        self.canvas.set_progress(step / ANIM_STEPS)

    def _on_intro_finished(self) -> None:
        self._intro_step = ANIM_STEPS
        self.canvas.set_progress(1.0)

    def _on_outro_value(self, value) -> None:
        step = int(round(_clamp01(float(value)) * OUTRO_STEPS))
        if step == self._outro_step:
            return
        self._outro_step = step
        self.canvas.set_closing(step / OUTRO_STEPS)

    def _on_outro_finished(self) -> None:
        self._finish_hide()

    def _finish_hide(self) -> None:
        self.hide()
        self._closing = False
        self.canvas.set_closing(0.0)
        self.canvas.set_progress(1.0)
        self.dismissed.emit()

    # -- Qt 事件 ------------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.canvas.setGeometry(0, 0, self.width(), self.height())

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._apply_native)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._intro.stop()
        self._outro.stop()
        super().hideEvent(event)

    @staticmethod
    def _animations_enabled(service) -> bool:
        try:
            return bool(service.animations_enabled())
        except Exception:  # noqa: BLE001
            return True
