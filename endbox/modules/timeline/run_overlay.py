# -*- coding: utf-8 -*-
"""运行态提示条（按键提示式横向步骤卡片条）：置顶、不抢焦点、可拖动。

- 一排步骤卡片：已过步骤变暗，当前步骤突出（聚焦/框选/兼容三种样式），
  后面显示接下来 N 格（默认 2，可设）。
- 顶部信息：当前干员 · 技能 · 第 N 段 · 进度 · 反复圈数（∞ 显示「第 N 遍」）。
- 可按住拖动（位置保存到 settings["timeline.run"]["pos"]，跨屏生效）；
  右键菜单：下一段 / 上一段 / 回到开头 / 打开主界面 / 关闭运行条。
- F6/F7 改透明度时，条上闪一条黄色提示带（不用红色）。


"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from endbox.core.log import get_logger
from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.theme.tokens import theme
from endbox.core.text import t as tr

log = get_logger("modules.timeline.overlay")

RUN_SETTINGS_DEFAULTS = {
    "style": "focus",          # focus（聚焦）| box（框选）| compat（兼容）
    "anchor": "top-center",    # top-center | top-left | top-right
    "scale": 1.0,
    "opacity": 0.94,
    "preview": True,
    "progress": True,
    "minimize": True,
    "gamepad": "off",
    "hold_ms": 280,
    "card_count": 5,           # 提示条显示长度：一共显示几张卡片（含当前）
    "next_count": 3,           # 后面几张（= card_count - 2，旧字段兼容）
    "repeat_all": True,        # 整轴反复：开始运行后一直循环，不结束
    "input_display": "kb",
    "pos": None,
}

INFO_H = 28.0
CARD_W = 62.0
CARD_H = 62.0
GAP = 6.0
PAD = 10.0
BAR_H = 3.0
BASE_W = 560.0
BASE_H = INFO_H + CARD_H + PAD * 2 + BAR_H + 2

STYLE_ALIASES = {"standard": "focus", "compact": "box"}


def normalize_run_settings(raw) -> dict:
    data = dict(RUN_SETTINGS_DEFAULTS)
    if isinstance(raw, dict):
        style = STYLE_ALIASES.get(str(raw.get("style") or "").strip().lower(), str(raw.get("style") or "").strip().lower())
        if style in ("focus", "box", "compat"):
            data["style"] = style
        anchor = str(raw.get("anchor") or "").strip().lower()
        if anchor in ("top-center", "top-left", "top-right"):
            data["anchor"] = anchor
        for key, low, high in (("scale", 0.6, 1.5), ("opacity", 0.30, 1.0)):
            try:
                data[key] = max(low, min(high, float(raw.get(key, data[key]))))
            except (TypeError, ValueError):
                pass
        for key in ("preview", "progress", "minimize"):
            if key in raw:
                data[key] = bool(raw.get(key))
        gamepad = str(raw.get("gamepad") or "").strip().lower()
        if gamepad in ("off", "xbox", "ds"):
            data["gamepad"] = gamepad
        try:
            data["hold_ms"] = max(80, min(2000, int(raw.get("hold_ms", data["hold_ms"]))))
        except (TypeError, ValueError):
            pass
        try:
            data["next_count"] = max(0, min(7, int(raw.get("next_count", data["next_count"]))))
        except (TypeError, ValueError):
            pass
        try:
            if raw.get("card_count") is not None:
                data["card_count"] = max(3, min(9, int(raw.get("card_count"))))
            else:
                # 兼容只存了 next_count 的旧设置
                data["card_count"] = max(3, min(9, int(data["next_count"]) + 2))
        except (TypeError, ValueError):
            pass
        data["next_count"] = max(0, data["card_count"] - 2)
        if "repeat_all" in raw:
            data["repeat_all"] = bool(raw.get("repeat_all"))
        data["input_display"] = "pad" if str(raw.get("input_display") or "kb").lower() == "pad" else "kb"
        position = raw.get("pos")
        if isinstance(position, (list, tuple)) and len(position) == 2:
            try:
                data["pos"] = [int(position[0]), int(position[1])]
            except (TypeError, ValueError):
                data["pos"] = None
    return data


def _rgba(color: QColor, alpha: int) -> QColor:
    out = QColor(color)
    out.setAlpha(alpha)
    return out


class RunOverlay(QWidget):
    """横向步骤卡片提示条。"""

    action = pyqtSignal(str)
    position_changed = pyqtSignal(int, int)

    def __init__(self, settings: dict | None = None, parent=None):
        super().__init__(parent)
        self._settings = normalize_run_settings(settings)
        self._state: dict = {}
        self._drag_offset: QPoint | None = None
        self._dragging = False
        self._flash_text = ""
        self._pix_cache: dict = {}
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.setInterval(1200)
        self._flash_timer.timeout.connect(self._clear_flash)
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowOpacity(self._settings["opacity"])
        self._apply_geometry()
        self.hide()

    # -- 设置 / 状态 --------------------------------------------------------
    def set_settings(self, settings: dict) -> None:
        new = normalize_run_settings(settings)
        old_opacity = float(self._settings.get("opacity", 0.94))
        self._settings = new
        self._apply_geometry()
        self.setWindowOpacity(new["opacity"])
        if abs(new["opacity"] - old_opacity) > 1e-6:
            self._flash_text = tr("timeline.overlay.opacity_flash", percent=new["opacity"] * 100)
            self._flash_timer.start()
        if self.isVisible():
            self._reposition()
        self.update()

    @property
    def settings(self) -> dict:
        return dict(self._settings)

    def apply_state(self, state: dict | None) -> None:
        self._state = dict(state or {})
        self._apply_geometry()
        if self.isVisible():
            self._reposition()
        self.update()

    def show_overlay(self) -> None:
        if not self.isVisible():
            self._apply_geometry()
            self._reposition()
            self.show()
        self.raise_()
        self.update()

    def hide_overlay(self) -> None:
        self._flash_timer.stop()
        self.hide()

    # -- 几何 ---------------------------------------------------------------
    def _apply_geometry(self) -> None:
        scale = float(self._settings["scale"])
        cards = self._state.get("cards") or []
        count = len(cards) if cards else 3
        width = PAD * 2 + count * CARD_W + max(0, count - 1) * GAP
        width = max(240.0, width)
        self.setFixedSize(max(220, int(width * scale)), max(50, int(BASE_H * scale)))

    def _screen_geometry(self):
        screen = QApplication.screenAt(self.geometry().center()) if self.isVisible() else None
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return None
        return screen.availableGeometry()

    def _reposition(self) -> None:
        position = self._settings.get("pos")
        screen = QApplication.screenAt(QPoint(int(position[0]), int(position[1]))) if position else None
        geometry = screen.availableGeometry() if screen is not None else self._screen_geometry()
        if geometry is None:
            return
        if position:
            x = max(geometry.x(), min(geometry.x() + geometry.width() - self.width(), int(position[0])))
            y = max(geometry.y(), min(geometry.y() + geometry.height() - self.height(), int(position[1])))
            self.move(x, y)
            return
        margin = int(22 * float(self._settings["scale"]))
        anchor = self._settings["anchor"]
        if anchor == "top-left":
            x = geometry.x() + margin
        elif anchor == "top-right":
            x = geometry.right() - self.width() - margin
        else:
            x = geometry.x() + (geometry.width() - self.width()) // 2
        self.move(int(x), int(geometry.y() + margin))

    def _clear_flash(self) -> None:
        self._flash_text = ""
        self.update()

    # -- 交互：拖动 + 右键菜单 ---------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragging = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._settings["pos"] = [self.x(), self.y()]
            self.position_changed.emit(self.x(), self.y())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        actions = (
            (tr("timeline.overlay.menu_next_segment"), "next_segment"),
            (tr("timeline.overlay.menu_prev_segment"), "prev_segment"),
            (tr("timeline.overlay.menu_restart"), "restart"),
            (tr("timeline.overlay.menu_open_main"), "open_main"),
            (tr("timeline.overlay.menu_close"), "close"),
        )
        for text, key in actions:
            menu.addAction(text, lambda k=key: self.action.emit(k))
        menu.exec(event.globalPos())
        event.accept()

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        colors = theme.p
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.scale(float(self._settings["scale"]), float(self._settings["scale"]))
        cards = self._state.get("cards") or []
        width = PAD * 2 + max(1, len(cards)) * CARD_W + max(0, len(cards) - 1) * GAP
        width = max(240.0, width)
        height = BASE_H
        rect = QRectF(0.5, 0.5, width - 1.0, height - 1.0)
        p.fillRect(rect, colors.panel)
        p.setPen(colors.rule)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        p.fillRect(QRectF(0, 0, 3, height), colors.signal)
        state = self._state
        if state.get("finished"):
            paint.draw_text(
                p, QRectF(PAD, INFO_H + 8, width - PAD * 2, 24),
                str(state.get("finish_text") or tr("timeline.overlay.finished")),
                F.display_font(17), colors.ink, Qt.AlignmentFlag.AlignCenter,
            )
            paint.draw_text(
                p, QRectF(PAD, INFO_H + 34, width - PAD * 2, 16),
                tr("timeline.overlay.finish_hint"),
                F.font(11, cjk_first=True), colors.ink2, Qt.AlignmentFlag.AlignCenter,
            )
            self._paint_progress(p, colors, QRectF(PAD, height - BAR_H - 2, width - PAD * 2, BAR_H))
            p.end()
            return
        step = state.get("step") or {}
        segment = state.get("segment") or {}
        loop = state.get("loop") or {}
        info = f"{step.get('operator_name') or '—'} · {step.get('kind_label') or '—'}"
        paint.draw_text(p, QRectF(PAD + 2, 4, width * 0.5, 16), info,
                        F.font(12, weight=QFont.Weight.Bold, cjk_first=True), colors.ink, elide=True)
        right = tr("timeline.overlay.segment", index=int(segment.get("index", 0)) + 1)
        if state.get("total"):
            right += f" · {state.get('progress_text') or ''}"
        duration = float(step.get("duration") or 0.0)
        if duration:
            right += " · " + tr("timeline.run.duration_estimate", seconds=duration)
        if loop.get("text"):
            right += f" · {loop.get('text')}"
        paint.draw_text(p, QRectF(width * 0.38, 4, width * 0.62 - PAD, 16), right,
                        F.field_font(9), colors.ink2, Qt.AlignmentFlag.AlignRight, elide=True)
        hint = str(state.get("hint") or "")
        if hint:
            p.fillRect(QRectF(PAD + 2, 20, 3, 5), colors.signal)
            paint.draw_text(p, QRectF(PAD + 8, 17, width - PAD * 2 - 10, 10), hint,
                            F.font(8, cjk_first=True), colors.ink, elide=True)
        # 卡片
        x = PAD
        y = INFO_H + 2
        style = self._settings["style"]
        for card in cards:
            card_state = card.get("state")
            selected = card_state == "current"
            card_rect = QRectF(x, y, CARD_W, CARD_H)
            if selected:
                if style in ("box", "compat"):
                    paint.fill_cut(p, card_rect, _rgba(colors.signal, 235), 5, "br")
                else:
                    paint.fill_cut(p, card_rect, colors.chip, 5, "br")
            else:
                paint.fill_cut(p, card_rect, _rgba(colors.chip, 190), 5, "br")
            paint.stroke_cut(p, card_rect, colors.signal if selected and style != "box" else colors.rule, 5, "br", 1.0 if not selected else 1.6)
            dim = card_state == "past" or (style in ("focus", "compat") and not selected)
            p.setOpacity(0.45 if dim else 1.0)
            key = card.get("key_text") or "—"
            if self._state.get("display") == "pad" and card.get("pad_key"):
                key = str(card["pad_key"])
            ink = colors.on_signal if selected and style in ("box", "compat") else colors.ink
            kind = str(card.get("kind") or "")
            # 主视觉：技能类型图标（普攻/战技/连携/终结/重击），切换干员画箭头
            icon = self._pix(
                card.get("icon"),
                26,
                tint=colors.on_signal if selected and style in ("box", "compat") else None,
            )
            icon_rect = QRectF(card_rect.center().x() - 13, card_rect.y() + 7, 26, 26)
            if icon is not None:
                p.drawPixmap(icon_rect, icon, QRectF(icon.rect()))
            elif kind == "switch":
                self._draw_switch_glyph(p, icon_rect.adjusted(5, 6, -5, -6), ink)
            else:
                paint.draw_text(
                    p,
                    QRectF(card_rect.x() + 4, card_rect.y() + 13, card_rect.width() - 8, 16),
                    str(card.get("label") or card.get("kind_label") or ""),
                    F.font(8, cjk_first=True),
                    colors.ink2,
                    Qt.AlignmentFlag.AlignCenter,
                    elide=True,
                )
            # 干员头像退成左上角小徽标（一眼知道是谁的技能）
            avatar = self._pix(card.get("avatar"), 14)
            if avatar is not None:
                badge = QRectF(card_rect.x() + 4, card_rect.y() + 4, 14, 14)
                p.drawPixmap(badge, avatar, QRectF(avatar.rect()))
            paint.draw_text(
                p,
                QRectF(card_rect.x() + 3, card_rect.bottom() - 16, card_rect.width() - 6, 14),
                key,
                F.field_font(10),
                ink,
                Qt.AlignmentFlag.AlignCenter,
            )
            p.setOpacity(1.0)
            if selected:
                paint.stroke_cut(p, card_rect.adjusted(1, 1, -1, -1), colors.trace, 5, "br", 1.8)
            x += CARD_W + GAP
        if state.get("warning"):
            p.fillRect(QRectF(PAD + 2, y + CARD_H - 8, 3, 5), colors.signal)
        self._paint_progress(p, colors, QRectF(PAD, height - BAR_H - 2, width - PAD * 2, BAR_H))
        if self._flash_text:
            band = QRectF(width - 96, height - 18, 90, 14)
            paint.fill_cut(p, band, colors.signal, 4, "br")
            paint.draw_text(p, band, self._flash_text, F.field_font(9), colors.on_signal,
                            Qt.AlignmentFlag.AlignCenter)
        p.end()

    def _draw_switch_glyph(self, p: QPainter, rect: QRectF, color) -> None:
        """切换干员：两条反向箭头（没有现成素材，用矢量画）。"""
        p.save()
        pen = QPen(color)
        pen.setWidthF(1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        top = y + h * 0.30
        bottom = y + h * 0.72
        p.drawLine(QPointF(x + w * 0.10, top), QPointF(x + w * 0.88, top))
        p.drawLine(QPointF(x + w * 0.64, top - h * 0.22), QPointF(x + w * 0.88, top))
        p.drawLine(QPointF(x + w * 0.88, top), QPointF(x + w * 0.64, top + h * 0.22))
        p.drawLine(QPointF(x + w * 0.12, bottom), QPointF(x + w * 0.90, bottom))
        p.drawLine(QPointF(x + w * 0.36, bottom - h * 0.22), QPointF(x + w * 0.12, bottom))
        p.drawLine(QPointF(x + w * 0.12, bottom), QPointF(x + w * 0.36, bottom + h * 0.22))
        p.restore()

    def _pix(self, path, size: int, tint=None):
        if not path:
            return None
        key = (str(path), int(size), tint.name() if tint else "")
        if key in self._pix_cache:
            return self._pix_cache[key]
        pix = QPixmap(str(path))
        if pix.isNull():
            self._pix_cache[key] = None
            return None
        pix = pix.scaled(int(size), int(size), Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        if tint is not None:
            tinted = QPixmap(pix.size()); tinted.fill(Qt.GlobalColor.transparent)
            painter = QPainter(tinted); painter.drawPixmap(0, 0, pix)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(tinted.rect(), tint); painter.end()
            pix = tinted
        self._pix_cache[key] = pix
        return pix

    def _paint_progress(self, p: QPainter, colors, rect: QRectF) -> None:
        total = int(self._state.get("total") or 0)
        index = int(self._state.get("index") or 0)
        ratio = (index + 1) / total if total else 0.0
        p.fillRect(rect, colors.chip)
        p.fillRect(QRectF(rect.x(), rect.y(), rect.width() * max(0.0, min(1.0, ratio)), rect.height()), colors.signal)
