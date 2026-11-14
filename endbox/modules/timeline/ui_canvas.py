# -*- coding: utf-8 -*-
"""技能轴序列画布（按键提示式）：按顺序排列的步骤块，没有时间刻度。

一行放不下自动换行；分段显示为「第 N 段」分隔条；换行/注释/反复显示为细标记。
点击选中（Ctrl 多选）、双击改注释/分段名/反复次数、Delete 删除、Ctrl+Z/Y 撤销重做。
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import QSizePolicy

from endbox.modules.timeline.model import KIND_LABELS
from endbox.modules.timeline.runtime import pad_key_text, switch_key
from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.ui.kit.base import EWidget, pal
from endbox.core.text import t as tr

PAD = 12.0
CELL_W = 96.0
CELL_H = 96.0
GAP = 10.0
ROW_GAP = 12.0
MARK_H = 42.0
BAR_H = 30.0


def _rgba(color, alpha: int):
    out = QColor(color)
    out.setAlpha(alpha)
    return out


class PixmapCache:
    def __init__(self) -> None:
        self._cache: dict = {}

    def clear(self) -> None:
        self._cache.clear()

    def avatar(self, path, size: int) -> QPixmap | None:
        key = ("a", str(path), int(size))
        if key not in self._cache:
            pix = QPixmap(str(path)) if path else QPixmap()
            if pix.isNull():
                self._cache[key] = None
            else:
                scaled = pix.scaled(
                    int(size), int(size),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = max(0, (scaled.width() - size) // 2)
                y = max(0, (scaled.height() - size) // 2)
                self._cache[key] = scaled.copy(x, y, int(size), int(size))
        return self._cache[key]

    def icon(self, path, size: int, color=None) -> QPixmap | None:
        key = ("i", str(path), int(size), color.name() if color else "")
        if key not in self._cache:
            pix = QPixmap(str(path)) if path else QPixmap()
            if pix.isNull():
                self._cache[key] = None
            else:
                pix = pix.scaled(
                    int(size), int(size),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                if color is not None:
                    tinted = QPixmap(pix.size())
                    tinted.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(tinted)
                    painter.drawPixmap(0, 0, pix)
                    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
                    painter.fillRect(tinted.rect(), color)
                    painter.end()
                    pix = tinted
                self._cache[key] = pix
        return self._cache[key]


class SequenceCanvas(EWidget):
    """自绘步骤序列画布。"""

    selection_changed = pyqtSignal()
    label_edit_requested = pyqtSignal(object)
    changed = pyqtSignal()

    def __init__(self, service, cache: PixmapCache, parent=None):
        super().__init__(parent)
        self.service = service
        self.cache = cache
        self.selection: list = []
        self._hover = None
        self._rects: list[tuple] = []
        self._width = 600
        self._clipboard: list[dict] = []
        self._drag_step = None
        self._drag_press = None
        self._drag_hover = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setMinimumHeight(int(CELL_H + PAD * 2))

    # -- 布局 ---------------------------------------------------------------
    def step_key_text(self, step) -> str:
        if step.kind == "heavy":
            return tr("timeline.run.heavy_key")
        if not step.is_block:
            return ""
        settings = self.service.run_settings()
        if str(settings.get("input_display") or "kb") == "pad":
            mode = str(settings.get("gamepad") or "xbox")
            return pad_key_text(step.kind, step.operator, mode) or self.step_key_text_kb(step)
        return self.step_key_text_kb(step)

    def step_key_text_kb(self, step) -> str:
        keys = self.service.keys()
        if step.kind == "skill":
            return str(int(step.operator) + 1)
        if step.kind == "link":
            raw = str(keys.get("link", "E") or "E").replace("+", "").strip()
            return raw.upper()
        if step.kind == "ult":
            return tr("timeline.run.hold_key", digit=int(step.operator) + 1)
        if step.kind == "switch":
            return switch_key(keys, int(step.operator))
        return ""

    def _layout(self, width: float) -> float:
        self._width = max(320, int(width))
        self._rects = []
        content_w = self._width - PAD * 2
        selected_loop_ranges: set[int] = set()
        for loop in self.service.model.loops():
            marker = self.service.model.steps[loop["start"]] if loop["start"] < len(self.service.model.steps) else None
            if any(step is marker for step in self.selection):
                selected_loop_ranges.update(range(loop["start"], loop["end"] + 1))
        x = PAD
        y = PAD
        row_h = 0.0
        number = 0
        for index, step in enumerate(self.service.model.steps):
            if step.kind == "segment":
                if x > PAD:
                    y += row_h + ROW_GAP
                    row_h = 0.0
                    x = PAD
                rect = QRectF(PAD, y, content_w, BAR_H)
                self._rects.append((step, rect))
                y += BAR_H + ROW_GAP
                x = PAD
                continue
            if step.kind == "break":
                rect = QRectF(x, y, 84.0, MARK_H)
                if x + rect.width() > PAD + content_w:
                    y += row_h + ROW_GAP
                    row_h = 0.0
                    x = PAD
                    rect = QRectF(x, y, 84.0, MARK_H)
                self._rects.append((step, rect))
                y += max(row_h, MARK_H) + ROW_GAP
                row_h = 0.0
                x = PAD
                continue
            if step.kind == "comment":
                width_ = min(240.0, max(140.0, 40.0 + len(step.label or tr("timeline.kind.comment")) * 12))
                rect = QRectF(x, y, width_, MARK_H)
                if x + width_ > PAD + content_w:
                    y += row_h + ROW_GAP
                    row_h = 0.0
                    x = PAD
                    rect = QRectF(x, y, width_, MARK_H)
                self._rects.append((step, rect))
                x += width_ + GAP
                continue
            if step.kind in ("loop_start", "loop_end"):
                width_ = 100.0
                rect = QRectF(x, y, width_, MARK_H)
                if x + width_ > PAD + content_w:
                    y += row_h + ROW_GAP
                    row_h = 0.0
                    x = PAD
                    rect = QRectF(x, y, width_, MARK_H)
                self._rects.append((step, rect))
                x += width_ + GAP
                continue
            rect = QRectF(x, y, CELL_W, CELL_H)
            if x + CELL_W > PAD + content_w:
                y += row_h + ROW_GAP
                row_h = 0.0
                x = PAD
                rect = QRectF(x, y, CELL_W, CELL_H)
            self._rects.append((step, rect, index in selected_loop_ranges, number))
            number += 1
            x += CELL_W + GAP
            row_h = max(row_h, CELL_H)
        return y + max(row_h, CELL_H) + PAD

    def relayout(self, width: float) -> float:
        height = self._layout(width)
        self.setFixedHeight(int(max(CELL_H + PAD * 2, height)))
        self.update()
        return height

    def content_height(self, width: float | None = None) -> int:
        return int(self._layout(width if width is not None else self._width))

    # -- 选中 ---------------------------------------------------------------
    def _alive(self, step) -> bool:
        return any(item is step for item in self.service.model.steps)

    def selected_steps(self) -> list:
        return [step for step in self.selection if self._alive(step)]

    def select_step(self, step, *, additive: bool = False) -> None:
        if not self._alive(step):
            return
        if additive:
            if any(item is step for item in self.selection):
                self.selection = [item for item in self.selection if item is not step]
            else:
                self.selection.append(step)
        else:
            self.selection = [step]
        self.update()
        self.selection_changed.emit()

    def clear_selection(self) -> None:
        if not self.selection:
            return
        self.selection = []
        self.update()
        self.selection_changed.emit()

    def set_selection(self, steps) -> None:
        self.selection = [step for step in (steps or []) if self._alive(step)]
        self.update()
        self.selection_changed.emit()

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        colors = pal()
        p.fillRect(QRectF(0, 0, self.width(), self.height()), colors.panel_surface)
        p.setPen(colors.rule)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0))
        for item in list(self._rects):
            step, rect = item[0], item[1]
            selected = any(step is s for s in self.selection)
            hovered = step is self._hover
            if step.kind == "segment":
                p.fillRect(rect, _rgba(colors.chip, 190))
                p.fillRect(QRectF(rect.x(), rect.y(), 3.0, rect.height()), colors.signal)
                paint.draw_text(
                    p, rect.adjusted(10, 0, -8, 0), step.label or tr("timeline.kind.segment"),
                    F.font(12, weight=QFont.Weight.Bold, cjk_first=True), colors.ink, elide=True,
                )
            elif step.kind == "break":
                paint.dashed_rect(p, rect, colors.ink2)
                paint.draw_text(p, rect, tr("timeline.canvas.break_text"), F.font(11, cjk_first=True), colors.ink2,
                                Qt.AlignmentFlag.AlignCenter)
            elif step.kind == "comment":
                p.fillPath(paint.cut_rect(rect, 5, "br"), _rgba(colors.chip, 220))
                paint.stroke_cut(p, rect, colors.rule, 5, "br", 1.0)
                paint.draw_text(p, rect.adjusted(8, 0, -8, 0), step.label or tr("timeline.kind.comment"),
                                F.font(11, cjk_first=True), colors.ink2, elide=True)
            elif step.kind in ("loop_start", "loop_end"):
                p.fillPath(paint.cut_rect(rect, 5, "br"), _rgba(colors.chip, 220))
                paint.stroke_cut(p, rect, colors.ink2 if hovered else colors.rule, 5, "br", 1.0)
                text = tr("timeline.canvas.loop_end_text")
                if step.kind == "loop_start":
                    count = "∞" if step.loop_count < 0 else f"×{step.loop_count}"
                    text = tr("timeline.canvas.loop_start_text", count=count)
                paint.draw_text(p, rect, text, F.field_font(10), colors.ink, Qt.AlignmentFlag.AlignCenter)
            else:
                if len(item) > 2 and item[2]:
                    p.fillRect(rect.adjusted(-3, -3, 3, 3), _rgba(colors.signal, 26))
                self._paint_block(p, step, rect, colors, selected, item[3] if len(item) > 3 else 0)
            if selected and step.kind != "segment":
                paint.stroke_cut(p, rect.adjusted(1, 1, -1, -1), colors.trace, 6, "br", 1.6)
        if not self.service.model.steps:
            self._paint_empty(p, colors)
        if self.hasFocus():
            paint.paint_focus(p, QRectF(1, 1, self.width() - 2, self.height() - 2), colors.trace)
        p.end()

    def _paint_block(self, p, step, rect, colors, selected, number) -> None:
        fill = colors.ink if step.kind in ("skill", "ult") else colors.chip
        text = colors.paper if step.kind in ("skill", "ult") else colors.ink
        path = paint.cut_rect(rect, 6, "br")
        p.fillPath(path, fill)
        if step.kind == "ult":
            p.save()
            p.setClipPath(path)
            p.fillRect(QRectF(rect.x(), rect.y(), 4.0, rect.height()), _rgba(colors.signal, 220))
            p.restore()
        if step.kind == "heavy":
            p.save()
            p.setClipPath(path)
            paint.paint_hatch(p, rect, colors.ink, alpha=0.20)
            p.restore()
        operator = self.service.assets.get(self.service.team.name(step.operator))
        avatar = self.cache.avatar(operator.avatar, 34) if operator else None
        box = QRectF(rect.x() + 8, rect.y() + 8, 34, 34)
        clip = paint.cut_rect(box, 4, "br")
        p.save()
        p.setClipPath(clip)
        if avatar is not None and not avatar.isNull():
            p.drawPixmap(box, avatar, QRectF(avatar.rect()))
        else:
            p.fillPath(clip, _rgba(colors.panel, 180))
        p.restore()
        paint.draw_text(
            p, QRectF(rect.x() + 8, rect.y() + 46, rect.width() - 16, 18),
            operator.name if operator else tr("timeline.defaults.slot_two_digit", slot=step.operator + 1),
            F.font(11, weight=QFont.Weight.Bold, cjk_first=True), text, elide=True,
        )
        paint.draw_text(
            p, QRectF(rect.x() + 48, rect.y() + 12, rect.width() - 52, 14),
            KIND_LABELS.get(step.kind, step.kind),
            F.font(10, cjk_first=True), text, elide=True,
        )
        glyph_path = None
        if operator is not None:
            glyph_path = operator.asset(step.kind) if step.kind in ("skill", "link", "ult") else None
        if glyph_path is None and step.kind == "heavy":
            candidate = self.service.assets.root / "glyph" / "heavy.png"
            glyph_path = candidate if candidate.is_file() else None
        glyph = self.cache.icon(glyph_path, 16, text) if glyph_path is not None else None
        if glyph is not None and not glyph.isNull():
            p.drawPixmap(QRectF(rect.right() - 26, rect.y() + 8, 16, 16), glyph, QRectF(glyph.rect()))
        key_text = self.step_key_text(step)
        if key_text:
            badge = QRectF(rect.right() - 36, rect.y() + 42, 30, 22)
            paint.fill_cut(p, badge, colors.signal if step.kind != "heavy" else colors.chip, 4, "br")
            paint.draw_text(
                p, badge, key_text, F.field_font(10),
                colors.on_signal if step.kind != "heavy" else colors.ink2,
                Qt.AlignmentFlag.AlignCenter,
            )
        if number:
            paint.draw_text(
                p, QRectF(rect.x() + 6, rect.bottom() - 16, 30, 12), f"{number:02d}",
                F.field_font(8), text,
            )

    def _paint_empty(self, p, colors) -> None:
        area = QRectF(PAD, PAD, self.width() - PAD * 2, 150)
        paint.paint_hatch(p, area, colors.ink, alpha=0.05)
        paint.draw_text(
            p, QRectF(area.x(), area.y() + 48, area.width(), 20), tr("timeline.canvas.empty_eyebrow"),
            F.eyebrow_font(11), colors.ink2, Qt.AlignmentFlag.AlignCenter,
        )
        paint.draw_text(
            p, QRectF(area.x() + 20, area.y() + 72, area.width() - 40, 22),
            tr("timeline.canvas.empty_text"),
            F.font(12, cjk_first=True), colors.ink2, Qt.AlignmentFlag.AlignCenter, elide=True,
        )

    # -- 交互 ---------------------------------------------------------------
    def _hit(self, pos: QPointF):
        for item in self._rects:
            if item[1].contains(pos):
                return item[0]
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus()
        step = self._hit(event.position())
        if step is None:
            self.clear_selection()
            return
        self.select_step(step, additive=bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier))
        self._drag_step = step
        self._drag_press = event.position()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        if self._drag_step is not None and self._drag_press is not None:
            if (pos - self._drag_press).manhattanLength() > 8:
                self._drag_hover = self._hit(pos)
                self._hover = self._drag_hover
                self.update()
            return
        step = self._hit(pos)
        if step is not self._hover:
            self._hover = step
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._drag_step is not None:
            source = self._drag_step
            target = self._drag_hover
            self._drag_step = None
            self._drag_press = None
            self._drag_hover = None
            if target is not None and target is not source:
                steps = self.selected_steps() or [source]
                if not any(step is source for step in steps):
                    steps = [source]
                try:
                    index = self.service.model.steps.index(target)
                except ValueError:
                    index = -1
                if index >= 0 and self.service.reorder_steps(steps, index):
                    self.changed.emit()
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            step = self._hit(event.position())
            if step is not None and (step.kind in ("comment", "segment", "loop_start", "loop_end") or step.is_key):
                self.label_edit_requested.emit(step)
                return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        mods = event.modifiers()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self.clear_selection()
            event.accept()
            return
        if mods & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_Z:
            self.service.redo() if mods & Qt.KeyboardModifier.ShiftModifier else self.service.undo()
            self._sync_selection()
            event.accept()
            return
        if mods & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_Y:
            self.service.redo()
            self._sync_selection()
            event.accept()
            return
        if mods & Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_D:
            pasted = self.service.duplicate_steps(self.selection)
            if pasted:
                self.set_selection(pasted)
            event.accept()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right) and self.selection:
            self.service.move_steps(self.selection, -1 if key == Qt.Key.Key_Left else 1)
            event.accept()
            return
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            steps = self.service.model.steps
            if steps:
                index = 0
                if self.selection:
                    try:
                        index = steps.index(self.selection[0])
                    except ValueError:
                        index = 0
                index = max(0, min(len(steps) - 1, index + (-1 if key == Qt.Key.Key_Up else 1)))
                self.select_step(steps[index])
            event.accept()
            return
        super().keyPressEvent(event)

    def _sync_selection(self) -> None:
        self.selection = [step for step in self.selection if self._alive(step)]
        self.update()
        self.selection_changed.emit()

    def delete_selected(self) -> None:
        if not self.selection:
            return
        self.service.remove_steps(self.selection)
        self.selection = []
        self.update()
        self.selection_changed.emit()
        self.changed.emit()


# 兼容旧导入名
TimelineCanvas = SequenceCanvas
