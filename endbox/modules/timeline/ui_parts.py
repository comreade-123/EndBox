# -*- coding: utf-8 -*-
"""排轴界面的自绘部件：队伍槽位、干员池、干员详情、轴库/分享/文字编辑对话框。"""
from __future__ import annotations

import time

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from endbox.core.text import t as tr
from endbox.modules.timeline.model import SLOT_COUNT
from endbox.modules.timeline.run_overlay import RUN_SETTINGS_DEFAULTS
from endbox.modules.timeline.runtime import DEFAULT_KEYS, KEY_FIELDS, switch_key
from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.theme.tokens import CUT_BUTTON, CUT_SMALL
from endbox.ui.icons import paint_icon
from endbox.ui.kit import (
    CutButton,
    confirm,
    FieldText,
    IconButton,
    Label,
    ScrollArea,
    SectionHead,
    Segmented,
    Toggle,
)
from endbox.ui.kit.base import EWidget, pal

TEXT_FALLBACK = "—"


def _text(value) -> str:
    text = str(value or "").strip()
    return text if text else TEXT_FALLBACK


# ---------------------------------------------------------------------------
# 队伍槽位
# ---------------------------------------------------------------------------


class TeamSlots(EWidget):
    """4 个队伍槽位：头像 / 名字 / 职业 / 属性；点击打开干员池。"""

    slot_clicked = pyqtSignal(int)
    active_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slots: list[dict] = []
        self._active = 0
        self._hover = -1
        self.setFixedHeight(64)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_data(self, slots: list[dict], active: int) -> None:
        self._slots = list(slots)
        self._active = int(max(0, min(SLOT_COUNT - 1, active)))
        self.update()

    def _rects(self) -> list[QRectF]:
        width = max(1.0, self.width())
        gap = 10.0
        cell = max(120.0, (width - gap * (SLOT_COUNT - 1)) / SLOT_COUNT)
        return [QRectF(i * (cell + gap), 0.0, cell, self.height()) for i in range(SLOT_COUNT)]

    def _index_at(self, x: float) -> int:
        for index, rect in enumerate(self._rects()):
            if rect.contains(QRectF(x, rect.center().y(), 1, 1).center()):
                return index
        return -1

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        hover = self._index_at(event.position().x())
        if hover != self._hover:
            self._hover = hover
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = -1
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            index = self._index_at(event.position().x())
            if index >= 0:
                self._active = index
                self.active_changed.emit(index)
                self.slot_clicked.emit(index)
                self.update()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = -1 if key == Qt.Key.Key_Left else 1
            self._active = (self._active + step) % SLOT_COUNT
            self.active_changed.emit(self._active)
            self.update()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.slot_clicked.emit(self._active)
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        colors = pal()
        for index, rect in enumerate(self._rects()):
            self._paint_slot(p, index, rect, colors)
        if self.hasFocus():
            rect = self._rects()[self._active].adjusted(1.0, 1.0, -1.0, -1.0)
            paint.stroke_cut(p, rect, colors.trace, CUT_BUTTON, "br", 1.5)
        p.end()

    def _paint_slot(self, p: QPainter, index: int, rect: QRectF, colors) -> None:
        rect = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        data = self._slots[index] if index < len(self._slots) else {}
        hovered = index == self._hover
        active = index == self._active
        path = paint.cut_rect(rect, CUT_BUTTON, "br")
        p.fillPath(path, colors.chip if hovered else colors.panel_surface)
        paint.stroke_cut(p, rect, colors.rule, CUT_BUTTON, "br", 1.0)
        if active:
            p.fillRect(QRectF(rect.x(), rect.y(), 3.0, rect.height()), colors.signal)
        name = str(data.get("name") or "")
        if not name:
            paint.dashed_rect(p, rect.adjusted(1.0, 1.0, -1.0, -1.0), colors.ink2, 1.0)
            paint_icon(p, "plus", QRectF(rect.x() + 16, rect.y() + 25, 15, 15), colors.ink2, stroke=1.5)
            paint.draw_text(
                p, QRectF(rect.x() + 40, rect.y() + 12, rect.width() - 52, 20),
                tr("timeline.pool.slot_empty", slot=index + 1), F.font(13, weight=QFont.Weight.Bold, cjk_first=True),
                colors.ink2, elide=True,
            )
            paint.draw_text(
                p, QRectF(rect.x() + 40, rect.y() + 34, rect.width() - 52, 17),
                tr("timeline.pool.empty_hint"), F.font(11, cjk_first=True), colors.ink2, elide=True,
            )
            return
        avatar: QPixmap | None = data.get("avatar")
        box = QRectF(rect.x() + 9, rect.y() + 12, 40, 40)
        clip = paint.cut_rect(box, 6, "br")
        p.save()
        p.setClipPath(clip)
        if avatar is not None and not avatar.isNull():
            p.drawPixmap(box, avatar, QRectF(avatar.rect()))
        else:
            p.fillPath(clip, colors.chip)
        p.restore()
        paint.stroke_cut(p, box, colors.rule, 6, "br", 1.0)
        text_x = rect.x() + 58
        text_w = rect.width() - 58 - 34
        paint.draw_text(
            p, QRectF(text_x, rect.y() + 5, text_w, 18), name,
            F.font(13, weight=QFont.Weight.Bold, cjk_first=True), colors.ink, elide=True,
        )
        class_icon = data.get("class_icon")
        element_icon = data.get("element_icon")
        row_y = rect.y() + 27
        x = text_x
        if class_icon is not None and not class_icon.isNull():
            p.drawPixmap(QRectF(x, row_y, 11, 11), class_icon, QRectF(class_icon.rect()))
        else:
            paint_icon(p, "user", QRectF(x, row_y, 11, 11), colors.ink2, stroke=1.3)
        x += 15
        class_name = str(data.get("class") or TEXT_FALLBACK)
        paint.draw_text(p, QRectF(x, row_y - 3, 42, 15), class_name, F.font(10, cjk_first=True), colors.ink2)
        x += paint.text_width(F.font(10, cjk_first=True), class_name) + 8
        if element_icon is not None and not element_icon.isNull():
            p.drawPixmap(QRectF(x, row_y, 11, 11), element_icon, QRectF(element_icon.rect()))
        else:
            paint_icon(p, "spark", QRectF(x, row_y, 11, 11), colors.ink2, stroke=1.2)
        x += 15
        paint.draw_text(
            p, QRectF(x, row_y - 3, max(14.0, rect.right() - x - 2), 15),
            str(data.get("element") or TEXT_FALLBACK), F.font(10, cjk_first=True), colors.ink2, elide=True,
        )
        rarity = data.get("rarity")
        if rarity:
            paint.draw_text(
                p, QRectF(rect.right() - 34, rect.y() + 7, 26, 13), f"{int(rarity)}★",
                F.field_font(9), colors.ink2, Qt.AlignmentFlag.AlignRight,
            )
        paint.draw_text(
            p, QRectF(rect.right() - 30, rect.y() + 23, 22, 12), f"0{index + 1}",
            F.field_font(9), colors.ink2, Qt.AlignmentFlag.AlignRight,
        )



# ---------------------------------------------------------------------------
# 动作面板（按技能类型分行，点干员直接插入）
# ---------------------------------------------------------------------------


class ActionPanel(EWidget):
    """按键提示式动作面板：每行一种技能，行内直接给 4 个干员按钮。

    点一下 = 在光标处插入「该干员 · 该技能」，不再需要先选投放槽位。
    未选人的槽位置灰，点击只提示「先去编队选干员」。
    """

    insert_requested = pyqtSignal(str, int)
    empty_slot_clicked = pyqtSignal(str, int)
    target_changed = pyqtSignal(int)

    ROWS = ("skill", "link", "ult", "switch")
    ROW_H = 26.0
    ROW_GAP = 3.0
    LABEL_W = 92.0
    HEAVY_W = 168.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slots: list[dict] = []
        self._keys: dict = {}
        self._active = 0
        self._hover: tuple[str, int] | None = None
        self._hover_heavy = False
        rows = len(self.ROWS) + 1
        self.setFixedHeight(int(rows * self.ROW_H + (rows - 1) * self.ROW_GAP))
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    # -- 数据 ---------------------------------------------------------------
    def set_data(self, slots: list[dict], keys: dict, active: int = 0) -> None:
        self._slots = list(slots or [])
        self._keys = dict(keys or {})
        self._active = int(max(0, min(SLOT_COUNT - 1, active)))
        self.update()

    def _slot_data(self, slot: int) -> dict:
        return self._slots[slot] if 0 <= slot < len(self._slots) else {}

    def _name(self, slot: int) -> str:
        return str(self._slot_data(slot).get("name") or "")

    def _skill_name(self, kind: str, slot: int) -> str:
        """该槽位干员在 operators.json 里的技能名（缺失返回空，回退只显示类型）。"""
        skills = self._slot_data(slot).get("skills") or {}
        return str(skills.get(kind) or "")

    def _link_key(self) -> str:
        raw = str(self._keys.get("link", "E") or "E").replace("+", "").strip()
        return raw.upper()

    def _badge_text(self, kind: str, slot: int) -> str:
        """动作面板角标：短按数字 1~4 / 连携键 / 长按数字 / 切换键（F1-F4）/ 重击提示。"""
        if kind == "skill":
            return str(slot + 1)
        if kind == "link":
            return self._link_key()
        if kind == "ult":
            return tr("timeline.run.hold_key", digit=slot + 1)
        if kind == "switch":
            return switch_key(self._keys, slot)
        return tr("timeline.run.heavy_key")

    def _row_hint(self, kind: str) -> str:
        if kind == "skill":
            return tr("timeline.actions.key_range_skill")
        if kind == "link":
            return self._link_key()
        if kind == "ult":
            return tr("timeline.actions.key_range_ult")
        if kind == "switch":
            return tr(
                "timeline.actions.key_range_switch",
                first=switch_key(self._keys, 0),
                last=switch_key(self._keys, SLOT_COUNT - 1),
            )
        return ""

    @staticmethod
    def _kind_text(kind: str) -> str:
        if kind == "ult":
            return tr("timeline.actions.row_ult")
        if kind == "switch":
            return tr("timeline.actions.row_switch")
        if kind == "heavy":
            return tr("timeline.kind.heavy")
        return tr(f"timeline.kind.{kind}")

    # -- 几何 ---------------------------------------------------------------
    def _cell_rects(self):
        width = max(320.0, float(self.width()))
        cell_w = max(78.0, (width - self.LABEL_W) / SLOT_COUNT)
        rows = []
        for row, kind in enumerate(self.ROWS):
            y = row * (self.ROW_H + self.ROW_GAP)
            cells = []
            for slot in range(SLOT_COUNT):
                cells.append((slot, QRectF(self.LABEL_W + slot * cell_w, y, cell_w - 5.0, self.ROW_H)))
            rows.append((kind, QRectF(0.0, y, self.LABEL_W - 7.0, self.ROW_H), cells))
        heavy_y = len(self.ROWS) * (self.ROW_H + self.ROW_GAP)
        heavy = QRectF(self.LABEL_W, heavy_y, min(self.HEAVY_W, width - self.LABEL_W), self.ROW_H)
        return rows, heavy

    # -- 事件 ---------------------------------------------------------------
    def _set_hover(self, hover, heavy: bool) -> None:
        if hover == self._hover and heavy == self._hover_heavy:
            return
        self._hover = hover
        self._hover_heavy = heavy
        if hover is not None:
            kind, slot = hover
            name = self._name(slot) or tr("timeline.defaults.empty_slot")
            self.setToolTip(
                tr(
                    "timeline.actions.operator_tip",
                    operator=name,
                    kind=self._kind_text(kind),
                    hotkey=self._badge_text(kind, slot),
                )
            )
        elif heavy:
            self.setToolTip(tr("timeline.actions.heavy_tip"))
        else:
            self.setToolTip("")
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position()
        rows, heavy = self._cell_rects()
        hover = None
        for kind, _label, cells in rows:
            for slot, rect in cells:
                if rect.contains(pos):
                    hover = (kind, slot)
                    break
            if hover is not None:
                break
        self._set_hover(hover, heavy.contains(pos))
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._set_hover(None, False)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus()
        pos = event.position()
        rows, heavy = self._cell_rects()
        if heavy.contains(pos):
            self.insert_requested.emit("heavy", 0)
            event.accept()
            return
        for kind, _label, cells in rows:
            for slot, rect in cells:
                if not rect.contains(pos):
                    continue
                if self._name(slot):
                    self._active = slot
                    self.target_changed.emit(slot)
                    self.insert_requested.emit(kind, slot)
                else:
                    self.empty_slot_clicked.emit(kind, slot)
                event.accept()
                return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = -1 if key == Qt.Key.Key_Left else 1
            self._active = (self._active + step) % SLOT_COUNT
            self.target_changed.emit(self._active)
            self.update()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if self._name(self._active):
                self.insert_requested.emit("skill", self._active)
            else:
                self.empty_slot_clicked.emit("skill", self._active)
            return
        super().keyPressEvent(event)

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        colors = pal()
        rows, heavy = self._cell_rects()
        for kind, label_rect, cells in rows:
            self._paint_label(p, label_rect, kind, colors)
            for slot, rect in cells:
                self._paint_cell(p, rect, kind, slot, colors)
        self._paint_label(p, QRectF(0.0, heavy.y(), self.LABEL_W - 7.0, heavy.height()), "heavy", colors)
        self._paint_heavy(p, heavy, colors)
        if self.hasFocus():
            paint.paint_focus(p, QRectF(1, 1, self.width() - 2, self.height() - 2), colors.trace)
        p.end()

    def _paint_label(self, p, rect, kind, colors) -> None:
        paint.draw_text(
            p, QRectF(rect.x(), rect.y() + 2, rect.width(), 15), self._kind_text(kind),
            F.font(11, weight=QFont.Weight.Bold, cjk_first=True), colors.ink,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elide=True,
        )
        if kind == "heavy":
            return
        hint = self._row_hint(kind)
        paint.draw_text(
            p, QRectF(rect.x(), rect.y() + 16, rect.width(), 12), hint,
            F.field_font(8), colors.ink2, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            elide=True,
        )

    def _paint_cell(self, p, rect, kind, slot, colors) -> None:
        hovered = self._hover == (kind, slot)
        name = self._name(slot)
        enabled = bool(name)
        path = paint.cut_rect(rect, CUT_SMALL, "br")
        if enabled:
            p.fillPath(path, colors.chip if hovered else colors.panel_surface)
            paint.stroke_cut(p, rect, colors.signal if hovered else colors.rule, CUT_SMALL, "br",
                             1.5 if hovered else 1.0)
        else:
            p.fillPath(path, colors.panel_surface)
            paint.dashed_rect(p, rect.adjusted(2, 2, -2, -2), colors.rule, 1.0)
        if slot == self._active:
            p.fillRect(QRectF(rect.x(), rect.y(), 2.5, rect.height()), colors.signal)
        box = QRectF(rect.x() + 6, rect.y() + 6, rect.height() - 12, rect.height() - 12)
        avatar = self._slot_data(slot).get("avatar")
        clip = paint.cut_rect(box, 3, "br")
        p.save()
        p.setClipPath(clip)
        if avatar is not None and not avatar.isNull():
            p.drawPixmap(box, avatar, QRectF(avatar.rect()))
        else:
            p.fillPath(clip, colors.chip)
            paint.draw_text(
                p, box, name[:1] if name else "+",
                F.font(10, weight=QFont.Weight.Bold, cjk_first=True),
                colors.ink2 if enabled else colors.rule, Qt.AlignmentFlag.AlignCenter,
            )
        p.restore()
        paint.stroke_cut(p, box, colors.rule, 3, "br", 1.0)
        text_x = box.right() + 6
        text_w = max(10.0, rect.right() - text_x - 28)
        badge = QRectF(rect.right() - 36, rect.y() + 8, 32, 15)
        if enabled:
            paint.draw_text(
                p, QRectF(text_x, rect.y() + 3, text_w, 14), name,
                F.font(10, weight=QFont.Weight.Bold, cjk_first=True), colors.ink, elide=True,
            )
            paint.draw_text(
                p, QRectF(text_x, rect.y() + 16, text_w, 11), self._skill_name(kind, slot) or self._kind_text(kind),
                F.font(8, cjk_first=True), colors.ink2, elide=True,
            )
            paint.fill_cut(p, badge, colors.signal, 3, "br")
            paint.draw_text(p, badge, self._badge_text(kind, slot),
                            F.field_font(8), colors.on_signal, Qt.AlignmentFlag.AlignCenter)
            return
        paint.draw_text(
            p, QRectF(text_x, rect.y() + 3, text_w, 14), tr("timeline.pool.slot_empty", slot=slot + 1),
            F.font(10, weight=QFont.Weight.Bold, cjk_first=True), colors.ink2, elide=True,
        )
        paint.draw_text(
            p, QRectF(text_x, rect.y() + 16, text_w, 11), tr("timeline.actions.empty_hint"),
            F.font(8, cjk_first=True), colors.ink2, elide=True,
        )
        paint.draw_text(p, badge, self._badge_text(kind, slot),
                        F.field_font(8), colors.rule, Qt.AlignmentFlag.AlignCenter)

    def _paint_heavy(self, p, rect, colors) -> None:
        path = paint.cut_rect(rect, CUT_SMALL, "br")
        p.fillPath(path, colors.chip if self._hover_heavy else colors.panel_surface)
        paint.stroke_cut(p, rect, colors.signal if self._hover_heavy else colors.rule, CUT_SMALL, "br",
                         1.5 if self._hover_heavy else 1.0)
        p.save()
        p.setClipPath(path)
        paint.paint_hatch(p, rect, colors.ink, alpha=0.10)
        p.restore()
        paint.draw_text(
            p, QRectF(rect.x() + 10, rect.y() + 3, rect.width() - 44, 14), tr("timeline.kind.heavy"),
            F.font(10, weight=QFont.Weight.Bold, cjk_first=True), colors.ink, elide=True,
        )
        paint.draw_text(
            p, QRectF(rect.x() + 10, rect.y() + 16, rect.width() - 44, 11),
            tr("timeline.actions.heavy_short"), F.font(8, cjk_first=True), colors.ink2, elide=True,
        )
        badge = QRectF(rect.right() - 26, rect.y() + 8, 22, 15)
        paint.fill_cut(p, badge, colors.chip, 3, "br")
        paint.draw_text(p, badge, tr("timeline.run.heavy_key"), F.field_font(8), colors.ink,
                        Qt.AlignmentFlag.AlignCenter)


# ---------------------------------------------------------------------------
# 干员池
# ---------------------------------------------------------------------------


class PoolCell(EWidget):
    """干员池里的一格：头像 + 名字 + 职业/属性（+ 稀有度）。"""

    clicked = pyqtSignal(str)

    def __init__(self, operator, avatar, class_icon, element_icon, rarity=None, parent=None):
        super().__init__(parent)
        self.operator = operator
        self._avatar = avatar
        self._class_icon = class_icon
        self._element_icon = element_icon
        self._rarity = rarity
        self._current = False
        self._hover = False
        self.setFixedHeight(56)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_current(self, current: bool) -> None:
        if current != self._current:
            self._current = current
            self.update()

    def set_icons(self, class_icon, element_icon) -> None:
        self._class_icon = class_icon
        self._element_icon = element_icon
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.operator.name)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self.operator.name)
            return
        if event.key() == Qt.Key.Key_Escape:
            widget = self.parent()
            while widget is not None and not isinstance(widget, OperatorPool):
                widget = widget.parent()
            if isinstance(widget, OperatorPool):
                widget.close_pool()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        if self._current:
            p.fillRect(QRectF(rect.x(), rect.y(), 3.0, rect.height()), colors.signal)
        elif self._hover:
            p.fillRect(rect, colors.chip)
        if self.hasFocus():
            paint.stroke_cut(p, rect, colors.trace, CUT_SMALL, "br", 1.2)
        box = QRectF(rect.x() + 10, rect.y() + 10, 36, 36)
        clip = paint.cut_rect(box, 5, "br")
        p.save()
        p.setClipPath(clip)
        if self._avatar is not None and not self._avatar.isNull():
            p.drawPixmap(box, self._avatar, QRectF(self._avatar.rect()))
        else:
            p.fillPath(clip, colors.chip)
        p.restore()
        paint.stroke_cut(p, box, colors.rule, 5, "br", 1.0)
        text_x = box.right() + 9
        text_w = max(20.0, rect.right() - text_x - (22 if self._rarity else 4))
        paint.draw_text(
            p, QRectF(text_x, rect.y() + 8, text_w, 18), self.operator.name,
            F.font(13, weight=QFont.Weight.Bold, cjk_first=True), colors.ink, elide=True,
        )
        if self._rarity:
            paint.draw_text(
                p, QRectF(rect.right() - 26, rect.y() + 8, 20, 14), f"{int(self._rarity)}★",
                F.field_font(9), colors.ink2, Qt.AlignmentFlag.AlignRight,
            )
        row_y = rect.y() + 30
        x = text_x
        if self._class_icon is not None and not self._class_icon.isNull():
            p.drawPixmap(QRectF(x, row_y, 11, 11), self._class_icon, QRectF(self._class_icon.rect()))
        x += 15
        class_name = self.operator.class_label
        paint.draw_text(p, QRectF(x, row_y - 3, 34, 15), class_name, F.font(10, cjk_first=True), colors.ink2)
        x += paint.text_width(F.font(10, cjk_first=True), class_name) + 6
        if self._element_icon is not None and not self._element_icon.isNull():
            p.drawPixmap(QRectF(x, row_y, 11, 11), self._element_icon, QRectF(self._element_icon.rect()))
        x += 15
        paint.draw_text(
            p, QRectF(x, row_y - 3, max(14.0, rect.right() - x - 2), 15),
            self.operator.element_label, F.font(10, cjk_first=True), colors.ink2, elide=True,
        )
        p.end()


class OperatorPool(EWidget):
    """右侧浮层干员池：头像 + 名字 + 职业 + 属性，点选即换人。"""

    chosen = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, assets, cache, parent=None):
        super().__init__(parent)
        self.assets = assets
        self.cache = cache
        self._cells: list[PoolCell] = []
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        header = QWidget(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        head = SectionHead("OPERATOR POOL", tr("timeline.view.pool_button"), header, size=17)
        head.setMinimumWidth(168)
        header_layout.addWidget(head, 1)
        close_btn = IconButton("close", tr("timeline.pool.close_tip"), header, size=28, icon_size=13)
        close_btn.clicked.connect(self.close_pool)
        header_layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(header)

        self._target_label = Label("", "small", wrap=True)
        layout.addWidget(self._target_label)

        self._scroll = ScrollArea(self)
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for index, operator in enumerate(assets.operators):
            rarity = None
            extra = assets.extra_for(operator.name)
            if extra is not None:
                rarity = extra.rarity
            cell = PoolCell(
                operator,
                cache.avatar(operator.avatar, 36),
                cache.icon(operator.class_icon, 11, pal().ink2),
                cache.icon(operator.element_icon, 11),
                rarity,
                host,
            )
            cell.clicked.connect(self.chosen.emit)
            grid.addWidget(cell, index // 2, index % 2)
            self._cells.append(cell)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        if not self._cells:
            grid.addWidget(Label(tr("timeline.pool.empty"), "muted", wrap=True), 0, 0, 1, 2)
        self._scroll.set_content(host)
        layout.addWidget(self._scroll, 1)

        footer = Label(tr("timeline.pool.footer"), "small")
        layout.addWidget(footer)
        self.hide()

    def set_target(self, index: int, current_name: str) -> None:
        current = current_name or tr("timeline.defaults.empty_slot")
        self._target_label.setText(tr("timeline.pool.target", slot=index + 1, name=current))
        target_cell = None
        for cell in self._cells:
            is_current = bool(current_name) and cell.operator.name == current_name
            cell.set_current(is_current)
            if is_current:
                target_cell = cell
        if target_cell is not None:
            self._scroll.verticalScrollBar().setValue(max(0, target_cell.y() - 6))
        else:
            self._scroll.verticalScrollBar().setValue(0)

    def close_pool(self) -> None:
        if self.isVisible():
            self.hide()
            self.closed.emit()

    def refresh_theme(self) -> None:
        colors = pal()
        for cell in self._cells:
            cell.set_icons(
                self.cache.icon(cell.operator.class_icon, 11, colors.ink2),
                self.cache.icon(cell.operator.element_icon, 11),
            )
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        p.fillRect(rect, colors.panel)
        p.setPen(colors.rule)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        p.fillRect(QRectF(0, 0, 3, self.height()), colors.signal)
        p.end()


# ---------------------------------------------------------------------------
# 干员详情
# ---------------------------------------------------------------------------


class OperatorDetail(EWidget):
    """当前槽位/选中事件的干员详情（素材 + 可选 fz.wiki 离线补充资料）。"""

    def __init__(self, cache=None, parent=None):
        super().__init__(parent)
        self._operator = None
        self._extra = None
        self._slot = 0
        self._source = ""
        self._cache = cache
        self._skills: tuple = ()
        self._step = None
        self._duration = 0.0
        self.setFixedHeight(76)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_operator(self, operator, extra, slot: int, source: str = "", *,
                     skills: tuple = (), step=None, duration: float = 0.0) -> None:
        self._operator = operator
        self._extra = extra
        self._slot = int(slot)
        self._source = source
        self._skills = tuple(skills or ())
        self._step = step
        self._duration = float(duration or 0.0)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        p.fillRect(rect, colors.panel_surface)
        p.setPen(colors.rule)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        p.fillRect(QRectF(rect.x(), rect.y(), 3.0, rect.height()), _alpha(colors.signal, 210))
        paint.draw_text(
            p, QRectF(12, rect.y() + 6, 120, 12), tr("timeline.detail.eyebrow"),
            F.eyebrow_font(8), colors.ink2, elide=True,
        )
        if self._operator is None:
            paint.dashed_rect(p, QRectF(rect.x() + 8, rect.y() + 22, rect.width() - 16, rect.height() - 30), colors.ink2, 1.0)
            paint.draw_text(
                p, QRectF(rect.x() + 18, rect.y() + 38, rect.width() - 36, 20),
                tr("timeline.detail.empty"),
                F.font(12, cjk_first=True), colors.ink2, elide=True,
            )
            p.end()
            return
        operator = self._operator
        extra = self._extra
        box = QRectF(rect.x() + 12, rect.y() + 22, 44, 44)
        avatar = self._avatar_pixmap(box)
        clip = paint.cut_rect(box, 6, "br")
        p.save()
        p.setClipPath(clip)
        if avatar is not None and not avatar.isNull():
            p.drawPixmap(box, avatar, QRectF(avatar.rect()))
        else:
            p.fillPath(clip, colors.chip)
        p.restore()
        paint.stroke_cut(p, box, colors.rule, 6, "br", 1.0)
        paint.draw_text(
            p, QRectF(rect.x() + 66, rect.y() + 20, 120, 18), operator.name,
            F.font(14, weight=QFont.Weight.Bold, cjk_first=True), colors.ink,
        )
        if extra is not None and extra.name_en:
            paint.draw_text(
                p, QRectF(rect.x() + 66 + paint.text_width(F.font(14, weight=QFont.Weight.Bold, cjk_first=True), operator.name) + 8,
                          rect.y() + 23, 150, 13), extra.name_en,
                F.field_font(9), colors.ink2, elide=True,
            )
        # 稀有度 / 职业 / 属性
        rarity = extra.rarity if extra is not None else None
        line = tr("timeline.defaults.slot_label", slot=self._slot + 1)
        if rarity:
            line += f" · {rarity}★"
        line += f" · {operator.class_label} · {operator.element_label}"
        paint.draw_text(
            p, QRectF(rect.x() + 66, rect.y() + 39, 330, 14), line,
            F.field_font(10), colors.ink2, elide=True,
        )
        # 资料列
        col1_x = rect.x() + 410
        col_w = max(120.0, (rect.width() - col1_x - rect.x() - 180) / 2)
        faction = extra.faction if extra is not None else None
        weapon = extra.weapon_type if extra is not None else None
        race = extra.meta_value(tr("timeline.detail.race")) if extra is not None else None
        paint.draw_text(
            p, QRectF(col1_x, rect.y() + 18, col_w, 14),
            tr("timeline.detail.line", faction=_text(faction), weapon=_text(weapon), race=_text(race)),
            F.font(10, cjk_first=True), colors.ink2, elide=True,
        )
        tags = extra.tags if extra is not None else ()
        if extra is not None:
            profile = " ".join(extra.tags) if extra.tags else (extra.bio or "")
        else:
            profile = ""
        paint.draw_text(
            p, QRectF(col1_x, rect.y() + 38, rect.width() - 150 - col1_x, 14),
            tr("timeline.detail.tags", value=_text(profile) if profile else _text(None)),
            F.font(10, cjk_first=True), colors.ink2, elide=True,
        )
        skills = extra.skills if extra is not None else ()
        if self._step is not None and self._step.is_key:
            # 选中按键步骤：显示它的技能名 / 预计耗时（真数据 + 手填优先）
            skill_name = self._step.skill
            if not skill_name:
                info = next((item for item in self._skills if item.kind == self._step.kind), None)
                skill_name = info.name if info is not None else ""
            parts = [f"{self._step.kind_label} · {skill_name or TEXT_FALLBACK}"]
            if self._duration:
                parts.append(tr("timeline.run.duration_estimate", seconds=self._duration))
            skill_text = tr("timeline.detail.skills", value=" · ".join(parts))
        elif self._skills:
            names = " / ".join(
                f"{tr(f'timeline.kind.{item.kind}')} {item.name}"
                for item in self._skills
                if item.kind in ("skill", "link", "ult") and item.name
            )
            skill_text = tr("timeline.detail.skills", value=names or TEXT_FALLBACK)
        elif skills:
            names = " / ".join(str(s.get("name") or "") for s in skills[:3] if s.get("name"))
            skill_text = tr("timeline.detail.skills", value=names or TEXT_FALLBACK)
        else:
            skill_text = tr("timeline.detail.skills", value=TEXT_FALLBACK)
        paint.draw_text(
            p, QRectF(rect.x() + 410, rect.y() + 56, rect.width() - 420, 13),
            skill_text, F.font(9, cjk_first=True), colors.ink2, elide=True,
        )
        if extra is None:
            note = tr("timeline.detail.more_unloaded")
        else:
            note = tr(
                "timeline.detail.source_note",
                source=self._source or tr("timeline.detail.default_source"),
            )
        paint.draw_text(
            p, QRectF(rect.right() - 330, rect.y() + 6, 318, 12), note,
            F.font(8, cjk_first=True), colors.ink2, Qt.AlignmentFlag.AlignRight, elide=True,
        )
        p.end()

    def _avatar_pixmap(self, box: QRectF) -> QPixmap | None:
        if self._operator is None or self._operator.avatar is None:
            return None
        cache = self._cache
        if cache is None:
            from endbox.modules.timeline.ui_canvas import PixmapCache

            cache = PixmapCache()
            self._cache = cache
        return cache.avatar(self._operator.avatar, 44)


def _alpha(color, alpha: int):
    out = QColor(color)
    out.setAlpha(alpha)
    return out


# ---------------------------------------------------------------------------
# 对话框：文字编辑 / 反复次数 / 轴库 / 分享码
# ---------------------------------------------------------------------------


def _dialog_shell(title: str, width: int = 560, height: int = 420) -> tuple[QDialog, QVBoxLayout]:
    dialog = QDialog()
    dialog.setWindowTitle(title)
    dialog.resize(width, height)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 18, 18, 18)
    layout.setSpacing(10)
    return dialog, layout


def _button_row(*buttons) -> QWidget:
    host = QWidget()
    row = QHBoxLayout(host)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.addStretch(1)
    for button in buttons:
        row.addWidget(button)
    return host


class TextPromptDialog(QDialog):
    """编辑注释/备注/分段名；反复事件同时可改次数（-1 = 无限）。"""

    def __init__(self, title: str, text: str = "", *, count: int | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(460, 190 if count is None else 240)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        layout.addWidget(SectionHead("EDIT", title, self, size=17))
        self._input = QLineEdit(text or "")
        self._input.selectAll()
        layout.addWidget(self._input)
        self._count: QSpinBox | None = None
        if count is not None:
            layout.addWidget(Label(tr("timeline.keys.loop_count_label"), "small"))
            self._count = QSpinBox()
            self._count.setRange(-1, 99)
            self._count.setValue(int(count))
            self._count.setSpecialValueText(tr("timeline.keys.infinite_full"))
            layout.addWidget(self._count)
        layout.addStretch(1)
        cancel = CutButton(tr("timeline.common.cancel"), "ghost")
        ok = CutButton(tr("timeline.common.confirm"), "primary")
        cancel.clicked.connect(self.reject)
        ok.clicked.connect(self.accept)
        layout.addWidget(_button_row(cancel, ok))
        self._input.setFocus()

    @property
    def text_value(self) -> str:
        return self._input.text().strip()

    @property
    def count_value(self) -> int | None:
        return self._count.value() if self._count is not None else None


class _AxisRow(EWidget):
    """轴库里的一条：名称 / 编队 / 事件数 / 更新时间。"""

    clicked = pyqtSignal(str)
    double_clicked = pyqtSignal(str)

    def __init__(self, entry: dict, parent=None):
        super().__init__(parent)
        self.entry = entry
        self._selected = False
        self._hover = False
        self.setFixedHeight(44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def set_selected(self, selected: bool) -> None:
        if selected != self._selected:
            self._selected = selected
            self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(str(self.entry.get("id")))
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(str(self.entry.get("id")))
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.double_clicked.emit(str(self.entry.get("id")))
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        if self._selected:
            p.fillRect(QRectF(rect.x(), rect.y(), 3.0, rect.height()), colors.signal)
            p.fillRect(rect, colors.chip)
        elif self._hover:
            p.fillRect(rect, colors.chip)
        paint.hairline(p, 0, rect.bottom(), self.width(), rect.bottom(), colors.rule)
        entry = self.entry
        paint.draw_text(
            p, QRectF(10, rect.y() + 6, 220, 16), str(entry.get("name") or tr("timeline.defaults.unnamed_axis")),
            F.font(12, weight=QFont.Weight.Bold, cjk_first=True),
            colors.ink if self._selected else colors.ink, elide=True,
        )
        team = [n for n in (entry.get("team") or []) if n]
        paint.draw_text(
            p, QRectF(10, rect.y() + 23, 280, 13),
            tr(
                "timeline.library.row_team",
                team=" / ".join(team) if team else tr("timeline.library.row_empty_team"),
            ),
            F.font(9, cjk_first=True), colors.ink2, elide=True,
        )
        updated = entry.get("updated") or 0
        stamp = time.strftime("%m-%d %H:%M", time.localtime(int(updated))) if updated else "—"
        paint.draw_text(
            p, QRectF(rect.right() - 210, rect.y() + 7, 200, 13),
            tr(
                "timeline.library.row_meta",
                count=int(entry.get("event_count") or 0),
                duration=float(entry.get("duration") or 0),
                stamp=stamp,
            ),
            F.field_font(9), colors.ink2, Qt.AlignmentFlag.AlignRight,
        )
        p.end()


class AxisLibraryDialog(QDialog):
    """技能轴库：搜索 / 载入 / 另存为 / 删除。"""

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle(tr("timeline.library.title"))
        self.resize(660, 520)
        self._selected_id: str | None = None
        self._rows: list[_AxisRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        head = SectionHead("AXIS LIBRARY", tr("timeline.library.title"), header, size=18)
        head.setMinimumWidth(200)
        header_layout.addWidget(head, 1)
        header_layout.addWidget(
            FieldText([tr("timeline.library.count", count=len(self.service.library_list()))]),
            0,
            Qt.AlignmentFlag.AlignBottom,
        )
        layout.addWidget(header)

        search_row = QWidget()
        search_layout = QHBoxLayout(search_row)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("timeline.library.search_placeholder"))
        self._search.textChanged.connect(self._rebuild)
        search_layout.addWidget(self._search, 1)
        self._name = QLineEdit(self.service.axis_name or tr("timeline.defaults.unnamed_axis"))
        self._name.setPlaceholderText(tr("timeline.library.name_placeholder"))
        self._name.setFixedWidth(220)
        search_layout.addWidget(self._name)
        save_btn = CutButton(tr("timeline.library.save_as"), "primary", icon="save")
        save_btn.clicked.connect(self._save_as)
        search_layout.addWidget(save_btn)
        layout.addWidget(search_row)

        self._scroll = ScrollArea(self)
        self._host = QWidget()
        self._host.setStyleSheet("background: transparent;")
        self._grid = QVBoxLayout(self._host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(0)
        self._grid.addStretch(1)
        self._scroll.set_content(self._host)
        layout.addWidget(self._scroll, 1)

        self._empty = Label(tr("timeline.library.empty"), "muted", wrap=True)
        layout.addWidget(self._empty)
        self._status = Label("", "small", wrap=True)
        layout.addWidget(self._status)

        buttons = QWidget()
        button_row = QHBoxLayout(buttons)
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        load_btn = CutButton(tr("timeline.common.load_selected"), "primary")
        load_btn.clicked.connect(self._load_selected)
        delete_btn = CutButton(tr("timeline.common.delete_selected"), "danger", icon="trash")
        delete_btn.clicked.connect(self._delete_selected)
        close_btn = CutButton(tr("timeline.common.close"), "ghost")
        close_btn.clicked.connect(self.reject)
        button_row.addStretch(1)
        for button in (delete_btn, load_btn, close_btn):
            button_row.addWidget(button)
        layout.addWidget(buttons)
        self._rebuild()
        self.refresh_theme()

    def refresh_theme(self) -> None:
        self.update()

    def _rebuild(self) -> None:
        query = self._search.text() if hasattr(self, "_search") else ""
        entries = self.service.library_list(query)
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._rows = []
        for entry in entries:
            row = _AxisRow(entry, self._host)
            row.clicked.connect(self._select_row)
            row.double_clicked.connect(lambda entry_id: self._load(entry_id))
            self._grid.addWidget(row)
            self._rows.append(row)
            row.set_selected(entry.get("id") == self._selected_id)
        self._grid.addStretch(1)
        self._empty.setVisible(not entries)
        if not entries and query:
            self._empty.setText(tr("timeline.library.empty_search"))
        elif not entries:
            self._empty.setText(tr("timeline.library.empty"))

    def _select_row(self, entry_id: str) -> None:
        self._selected_id = entry_id
        for row in self._rows:
            row.set_selected(str(row.entry.get("id")) == entry_id)
        entry = self.service.library.get(entry_id)
        if entry is not None:
            self._name.setText(str(entry.get("name") or ""))

    def _save_as(self) -> None:
        name = self._name.text().strip() or (self.service.axis_name or tr("timeline.defaults.unnamed_axis"))
        entry = self.service.library_save(name)
        if entry is None:
            self._status.setText(self.service.library.last_error or tr("timeline.common.save_failed"))
            return
        self._selected_id = str(entry.get("id"))
        self._status.setText(tr("timeline.library.saved", name=entry.get("name")))
        self._rebuild()
        for row in self._rows:
            row.set_selected(str(row.entry.get("id")) == self._selected_id)

    def _load_selected(self) -> None:
        if not self._selected_id:
            self._status.setText(tr("timeline.library.need_select_load"))
            return
        self._load(self._selected_id)

    def _load(self, entry_id: str) -> None:
        if self.service.library_load(entry_id):
            self.accept()
        else:
            self._status.setText(tr("timeline.library.load_missing"))

    def _delete_selected(self) -> None:
        if not self._selected_id:
            self._status.setText(tr("timeline.library.need_select_delete"))
            return
        entry = self.service.library.get(self._selected_id) or {}
        if not confirm(self, title=tr("timeline.confirm.delete_axis.title"),
                       message=tr("timeline.confirm.delete_axis.message", name=entry.get("name") or tr("timeline.defaults.unnamed_axis")),
                       action_text=tr("timeline.confirm.delete_axis.action")):
            return
        if self.service.library_delete(self._selected_id):
            self._status.setText(tr("timeline.library.deleted"))
            self._selected_id = None
            self._rebuild()
        else:
            self._status.setText(self.service.library.last_error or tr("timeline.common.delete_failed"))


class ShareDialog(QDialog):
    """分享码导出 / 导入（Base64 + JSON，含轴名/队伍/事件/反复）。"""

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle(tr("timeline.view.buttons.share"))
        self.resize(680, 640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        head = SectionHead("SHARE CODE", tr("timeline.view.buttons.share"), self, size=18)
        head.setMinimumWidth(200)
        layout.addWidget(head)
        self._name = QLineEdit(service.axis_name or tr("timeline.defaults.unnamed_axis"))
        name_row = QWidget()
        name_layout = QHBoxLayout(name_row)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(8)
        name_layout.addWidget(Label(tr("timeline.share.axis_name"), "small"))
        name_layout.addWidget(self._name, 1)
        regenerate = CutButton(tr("timeline.share.regenerate"), "ghost", icon="refresh")
        regenerate.clicked.connect(self._regenerate)
        name_layout.addWidget(regenerate)
        layout.addWidget(name_row)

        layout.addWidget(Label(tr("timeline.share.export_hint"), "small"))
        self._code = QPlainTextEdit()
        self._code.setReadOnly(True)
        self._code.setFixedHeight(110)
        layout.addWidget(self._code)
        export_row = QWidget()
        export_layout = QHBoxLayout(export_row)
        export_layout.setContentsMargins(0, 0, 0, 0)
        export_layout.setSpacing(8)
        copy_btn = CutButton(tr("timeline.share.copy"), "primary", icon="copy")
        copy_btn.clicked.connect(self._copy_code)
        export_layout.addStretch(1)
        export_layout.addWidget(copy_btn)
        layout.addWidget(export_row)

        # 分享文案：一整段可直接粘到聊天里的话（干员列表 + 分享码）
        layout.addWidget(Label(tr("timeline.share.blurb_hint"), "small"))
        self._blurb = QPlainTextEdit()
        self._blurb.setReadOnly(True)
        self._blurb.setFixedHeight(84)
        layout.addWidget(self._blurb)
        blurb_row = QWidget()
        blurb_layout = QHBoxLayout(blurb_row)
        blurb_layout.setContentsMargins(0, 0, 0, 0)
        blurb_layout.setSpacing(8)
        copy_blurb = CutButton(tr("timeline.share.copy_blurb"), "default", icon="copy")
        copy_blurb.clicked.connect(self._copy_blurb)
        blurb_layout.addStretch(1)
        blurb_layout.addWidget(copy_blurb)
        layout.addWidget(blurb_row)

        layout.addWidget(Label(tr("timeline.share.import_hint"), "small"))
        self._import = QPlainTextEdit()
        self._import.setPlaceholderText(tr("timeline.share.import_placeholder"))
        self._import.setFixedHeight(110)
        layout.addWidget(self._import)
        self._status = Label("", "small", wrap=True)
        layout.addWidget(self._status)

        buttons = QWidget()
        button_row = QHBoxLayout(buttons)
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        import_btn = CutButton(tr("timeline.share.import"), "primary", icon="download")
        import_btn.clicked.connect(self._do_import)
        close_btn = CutButton(tr("timeline.common.close"), "ghost")
        close_btn.clicked.connect(self.reject)
        button_row.addStretch(1)
        button_row.addWidget(import_btn)
        button_row.addWidget(close_btn)
        layout.addWidget(buttons)
        self._regenerate()

    def _regenerate(self) -> None:
        name = self._name.text().strip()
        if name:
            self.service.set_axis_name(name)
        try:
            code = self.service.export_share()
            self._code.setPlainText(code)
            from endbox.modules.timeline.share import share_blurb

            self._blurb.setPlainText(share_blurb(self.service.team.to_list(), code))
            self._status.setText("")
        except Exception as exc:  # noqa: BLE001
            self._status.setText(tr("timeline.share.generated_failed", error=exc))

    def _copy_code(self) -> None:
        from PyQt6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._code.toPlainText())
        self._status.setText(tr("timeline.share.copied"))

    def _copy_blurb(self) -> None:
        from PyQt6.QtWidgets import QApplication

        QApplication.clipboard().setText(self._blurb.toPlainText())
        self._status.setText(tr("timeline.share.blurb_copied"))

    def _do_import(self) -> None:
        text = self._import.toPlainText()
        if not confirm(self, title=tr("timeline.confirm.import_share.title"),
                       message=tr("timeline.confirm.import_share.message"),
                       action_text=tr("timeline.confirm.import_share.action")):
            return
        try:
            result = self.service.import_share(text)
        except Exception as exc:  # noqa: BLE001 - ShareCodeError 也是 Exception
            self._status.setText(str(exc))
            return
        message = tr(
            "timeline.share.imported",
            name=result.get("name"),
            count=result.get("steps"),
        )
        if result.get("migrated"):
            message = f"{message} {tr('timeline.share.migrated')}"
        self._status.setText(message)
        self.accept()


# ---------------------------------------------------------------------------
# 运行提示条设置 / 键位配置
# ---------------------------------------------------------------------------


def _qt_key_name(key: int, text: str) -> str:
    special = {
        Qt.Key.Key_Alt: "ALT",
        Qt.Key.Key_Control: "CTRL",
        Qt.Key.Key_Shift: "SHIFT",
        Qt.Key.Key_Space: "SPACE",
        Qt.Key.Key_Tab: "TAB",
        Qt.Key.Key_Return: "ENTER",
        Qt.Key.Key_Enter: "ENTER",
        Qt.Key.Key_Escape: "ESC",
    }
    if key in special:
        return special[key]
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
        return f"F{int(key) - int(Qt.Key.Key_F1) + 1}"
    if text and len(text) == 1 and text.isalnum():
        return text.upper()
    return ""


class KeyCaptureButton(CutButton):
    """点一下进入"按新键"状态，再按一个键就记录下来。"""

    captured = pyqtSignal(str)

    def __init__(self, value: str = "Q", parent=None):
        super().__init__(value, "ghost", parent, height=32)
        self._value = str(value or "").upper()
        self._capturing = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumWidth(104)
        self.clicked.connect(self.begin_capture)

    @property
    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = str(value or "").upper() or self._value
        self._capturing = False
        self.setText(self._value)

    def begin_capture(self) -> None:
        self._capturing = True
        self.setText(tr("timeline.keys.capture_prompt"))
        self.setFocus()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if self._capturing:
            name = _qt_key_name(event.key(), event.text())
            if name:
                self._capturing = False
                self.setText(name)
                self._value = name
                self.captured.emit(name)
                event.accept()
                return
            event.accept()
            return
        super().keyPressEvent(event)


class KeyConfigDialog(QDialog):
    """键位配置：连携 E / 切换干员 F1-F4 / F6-F11 运行控制；数字短按/长按按实机键位固定。"""

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle(tr("timeline.keys.title"))
        self.resize(560, 620)
        self._buttons: dict[str, KeyCaptureButton] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        head = SectionHead("HOTKEYS", tr("timeline.keys.title"), self, size=18)
        head.setMinimumWidth(200)
        layout.addWidget(head)
        layout.addWidget(
            Label(
                tr("timeline.keys.intro"),
                "small",
                wrap=True,
            )
        )
        keys = service.keys()
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        for row, (field, label) in enumerate(KEY_FIELDS):
            grid.addWidget(Label(label, "body"), row, 0)
            button = KeyCaptureButton(keys.get(field, DEFAULT_KEYS.get(field, "")), grid_host)
            button.set_value(keys.get(field, DEFAULT_KEYS.get(field, "")))
            grid.addWidget(button, row, 1)
            self._buttons[field] = button
        grid.setColumnStretch(1, 1)
        layout.addWidget(grid_host)
        layout.addStretch(1)

        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        reset = CutButton(tr("timeline.keys.restore"), "ghost", icon="refresh")
        reset.clicked.connect(self._reset_defaults)
        cancel = CutButton(tr("timeline.common.cancel"), "ghost")
        cancel.clicked.connect(self.reject)
        save = CutButton(tr("timeline.common.save"), "primary", icon="save")
        save.clicked.connect(self._save)
        row.addStretch(1)
        for button in (reset, cancel, save):
            row.addWidget(button)
        layout.addWidget(buttons)

    def _reset_defaults(self) -> None:
        if not confirm(self, title=tr("timeline.confirm.reset_keys.title"),
                       message=tr("timeline.confirm.reset_keys.message"),
                       action_text=tr("timeline.confirm.reset_keys.action"), danger=False):
            return
        for field, button in self._buttons.items():
            button.set_value(str(DEFAULT_KEYS.get(field, "")))

    def _save(self) -> None:
        # 从现有键位出发再覆盖：旧设置里的 heavy 等废弃字段原样保留，不丢用户数据。
        mapping = dict(self.service.keys())
        for field, button in self._buttons.items():
            mapping[field] = button.value
        mapping["hold_number_ult"] = True
        self.service.set_keys(mapping)
        self.accept()


class StepTimingDialog(QDialog):
    """按键步骤的预计耗时 / 技能名（双击步骤打开；默认值来自 operators.json）。"""

    def __init__(self, step, *, suggested_duration: float = 0.0, suggested_skill: str = "",
                 data_available: bool = False, parent=None):
        super().__init__(parent)
        self.step = step
        self._suggested_duration = float(suggested_duration or 0.0)
        self._suggested_skill = str(suggested_skill or "")
        self.setWindowTitle(tr("timeline.step_timing.title"))
        self.resize(480, 320)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        head = SectionHead("STEP TIMING", tr("timeline.step_timing.heading", kind=step.kind_label), self, size=18)
        head.setMinimumWidth(220)
        layout.addWidget(head)
        layout.addWidget(Label(tr("timeline.step_timing.intro"), "small", wrap=True))

        layout.addWidget(Label(tr("timeline.step_timing.skill"), "small"))
        self._skill = QLineEdit(step.skill or self._suggested_skill)
        self._skill.setPlaceholderText(tr("timeline.step_timing.skill_placeholder"))
        layout.addWidget(self._skill)

        layout.addWidget(Label(tr("timeline.step_timing.duration"), "small"))
        self._duration = QDoubleSpinBox()
        self._duration.setRange(0.0, 60.0)
        self._duration.setDecimals(1)
        self._duration.setSingleStep(0.1)
        self._duration.setSuffix(tr("timeline.step_timing.seconds"))
        self._duration.setValue(float(step.duration or self._suggested_duration or 0.0))
        layout.addWidget(self._duration)
        hint = (
            tr("timeline.step_timing.data_hint", seconds=self._suggested_duration)
            if data_available
            else tr("timeline.step_timing.no_data_hint")
        )
        layout.addWidget(Label(hint, "small", wrap=True))
        layout.addStretch(1)

        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        reset = CutButton(tr("timeline.step_timing.reset"), "ghost", icon="refresh")
        reset.clicked.connect(self._reset)
        cancel = CutButton(tr("timeline.common.cancel"), "ghost")
        cancel.clicked.connect(self.reject)
        save = CutButton(tr("timeline.common.save"), "primary", icon="save")
        save.clicked.connect(self.accept)
        row.addStretch(1)
        for button in (reset, cancel, save):
            row.addWidget(button)
        layout.addWidget(buttons)

    def _reset(self) -> None:
        self._duration.setValue(self._suggested_duration)
        self._skill.setText(self._suggested_skill)

    @property
    def duration_value(self) -> float:
        return round(float(self._duration.value()), 3)

    @property
    def skill_value(self) -> str:
        return self._skill.text().strip()


class RunSettingsDialog(QDialog):
    """提示条自定义：样式 / 位置 / 缩放 / 透明度 / 下一步预览 / 进度 / 是否最小化主窗口。"""

    ANCHORS = ("top-center", "top-left", "top-right")

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle(tr("timeline.run_settings.title"))
        self.resize(520, 460)
        self._settings = service.run_settings()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        head = SectionHead("RUN OVERLAY", tr("timeline.run_settings.heading"), self, size=18)
        head.setMinimumWidth(220)
        layout.addWidget(head)

        style_row = QWidget()
        style_layout = QHBoxLayout(style_row)
        style_layout.setContentsMargins(0, 0, 0, 0)
        style_layout.addWidget(Label(tr("timeline.run_settings.style"), "body"))
        style_index = {"focus": 0, "box": 1, "compat": 2}.get(str(self._settings.get("style")), 0)
        self._style = Segmented([tr("timeline.run_settings.styles.focus"), tr("timeline.run_settings.styles.box"), tr("timeline.run_settings.styles.compat")], index=style_index)
        style_layout.addWidget(self._style)
        style_layout.addStretch(1)
        layout.addWidget(style_row)

        anchor_row = QWidget()
        anchor_layout = QHBoxLayout(anchor_row)
        anchor_layout.setContentsMargins(0, 0, 0, 0)
        anchor_layout.addWidget(Label(tr("timeline.run_settings.anchor"), "body"))
        try:
            anchor_index = self.ANCHORS.index(self._settings["anchor"])
        except ValueError:
            anchor_index = 0
        self._anchor = Segmented([tr("timeline.run_settings.anchors.top-center"), tr("timeline.run_settings.anchors.top-left"), tr("timeline.run_settings.anchors.top-right")], index=anchor_index)
        anchor_layout.addWidget(self._anchor)
        anchor_layout.addStretch(1)
        layout.addWidget(anchor_row)

        self._scale_value = float(self._settings["scale"])
        self._opacity_value = float(self._settings["opacity"])
        layout.addWidget(self._stepper(tr("timeline.run_settings.scale"), self._scale_value, 0.6, 1.4, 0.1, "_scale_value"))
        layout.addWidget(self._stepper(tr("timeline.run_settings.opacity"), self._opacity_value, 0.30, 1.0, 0.05, "_opacity_value"))

        options = QWidget()
        options_layout = QHBoxLayout(options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(16)
        self._preview = Toggle(tr("timeline.run_settings.preview"), checked=self._settings["preview"])
        self._progress = Toggle(tr("timeline.run_settings.progress"), checked=self._settings["progress"])
        self._minimize = Toggle(tr("timeline.run_settings.minimize"), checked=self._settings["minimize"])
        self._repeat = Toggle(tr("timeline.run_settings.repeat_all"), checked=bool(self._settings.get("repeat_all", True)))
        for widget in (self._preview, self._progress, self._minimize, self._repeat):
            options_layout.addWidget(widget)
        options_layout.addStretch(1)
        layout.addWidget(options)

        pad_row = QWidget()
        pad_layout = QHBoxLayout(pad_row)
        pad_layout.setContentsMargins(0, 0, 0, 0)
        pad_layout.addWidget(Label(tr("timeline.run_settings.gamepad"), "body"))
        pad_index = {"off": 0, "xbox": 1, "ds": 2}.get(str(self._settings.get("gamepad") or "off"), 0)
        self._gamepad = Segmented([tr("timeline.common.close"), "Xbox", "DS"], index=pad_index)
        pad_layout.addWidget(self._gamepad)
        pad_layout.addStretch(1)
        layout.addWidget(pad_row)
        if pad_index != 0:
            layout.addWidget(Label(tr("timeline.run_settings.pad_unsupported"), "small", wrap=True))
        self._hold_value = float(self._settings.get("hold_ms", 280))
        self._card_count_value = float(self._settings.get("card_count", 5))
        layout.addWidget(self._stepper(tr("timeline.run_settings.hold_ms"), self._hold_value, 80, 600, 20, "_hold_value"))
        layout.addWidget(self._stepper(tr("timeline.run_settings.card_count"), self._card_count_value, 3, 9, 2, "_card_count_value"))
        display_row = QWidget()
        display_layout = QHBoxLayout(display_row)
        display_layout.setContentsMargins(0, 0, 0, 0)
        display_layout.addWidget(Label(tr("timeline.run_settings.display"), "body"))
        self._display = Segmented([tr("timeline.run_settings.displays.kb"), tr("timeline.run_settings.displays.pad")], index=1 if str(self._settings.get("input_display") or "kb") == "pad" else 0)
        display_layout.addWidget(self._display)
        display_layout.addStretch(1)
        layout.addWidget(display_row)
        from endbox.core.elevation import admin_hint, is_admin
        admin_ok = bool(is_admin())
        admin_text = admin_hint()
        admin_row = QWidget()
        admin_layout = QHBoxLayout(admin_row)
        admin_layout.setContentsMargins(0, 0, 0, 0)
        admin_layout.setSpacing(8)
        admin_label = Label((tr("timeline.run_settings.admin_ok") if admin_ok else tr("timeline.run_settings.admin_no")) + " · " + admin_text, "small", wrap=True)
        admin_layout.addWidget(admin_label, 1)
        if not admin_ok:
            relaunch = CutButton(tr("timeline.run_settings.relaunch"), "default", icon="refresh", height=28, compact=True)

            def _relaunch() -> None:
                from endbox.core.elevation import relaunch_as_admin
                from PyQt6.QtWidgets import QApplication

                if relaunch_as_admin():
                    QApplication.instance().quit()

            relaunch.clicked.connect(_relaunch)
            admin_layout.addWidget(relaunch)
        layout.addWidget(admin_row)
        layout.addStretch(1)

        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        cancel = CutButton(tr("timeline.common.cancel"), "ghost")
        cancel.clicked.connect(self.reject)
        save = CutButton(tr("timeline.common.save"), "primary", icon="save")
        save.clicked.connect(self._save)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(save)
        layout.addWidget(buttons)

    def _stepper(self, title: str, value: float, low: float, high: float, step: float, attr: str) -> QWidget:
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(Label(title, "body"))
        minus = CutButton("−", "ghost", icon="minus", height=28, compact=True)
        plus = CutButton("+", "ghost", icon="plus", height=28, compact=True)
        field = FieldText([self._format(title, value)])
        field.setMinimumWidth(80)
        setattr(self, "_field_" + attr[1:], field)

        def change(delta: float) -> None:
            current = getattr(self, attr)
            current = max(low, min(high, round(current + delta, 3)))
            setattr(self, attr, current)
            field.set_fields([self._format(title, current)])

        minus.clicked.connect(lambda: change(-step))
        plus.clicked.connect(lambda: change(step))
        row.addWidget(minus)
        row.addWidget(field, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(plus)
        row.addStretch(1)
        return host

    @staticmethod
    def _format(title: str, value: float) -> str:
        if title == tr("timeline.run_settings.scale"):
            return f"{value:.2f}×"
        if title == tr("timeline.run_settings.card_count"):
            return tr("timeline.run_settings.card_count_value", count=int(round(value)))
        if title == tr("timeline.run_settings.hold_ms"):
            return tr("timeline.run_settings.hold_ms_value", value=int(round(value)))
        return f"{value * 100:.0f}%"

    def _save(self) -> None:
        self.service.set_run_settings(
            {
                "style": ("focus", "box", "compat")[max(0, min(2, self._style.index))],
                "anchor": self.ANCHORS[max(0, min(2, self._anchor.index))],
                "scale": round(self._scale_value, 3),
                "opacity": round(self._opacity_value, 3),
                "preview": self._preview.isChecked(),
                "progress": self._progress.isChecked(),
                "minimize": self._minimize.isChecked(),
                "gamepad": ("off", "xbox", "ds")[max(0, min(2, self._gamepad.index))],
                "hold_ms": int(self._hold_value),
                "card_count": int(self._card_count_value),
                "next_count": max(0, int(self._card_count_value) - 2),
                "repeat_all": self._repeat.isChecked(),
                "input_display": "pad" if self._display.index == 1 else "kb",
            }
        )
        self.accept()



class TeamLibraryDialog(QDialog):
    """编队库：保存当前 4 人、搜索、载入、删除。"""

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle(tr("timeline.view.team_library"))
        self.resize(560, 460)
        self._selected = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        head = SectionHead("TEAM LIBRARY", tr("timeline.view.team_library"), self, size=18)
        head.setMinimumWidth(200)
        layout.addWidget(head)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("timeline.team_library.search_placeholder"))
        self._search.textChanged.connect(self._rebuild)
        row_layout.addWidget(self._search, 1)
        self._name = QLineEdit(self.service.team_summary())
        self._name.setFixedWidth(180)
        row_layout.addWidget(self._name)
        save_btn = CutButton(tr("timeline.team_library.save_current"), "primary", icon="save", height=30, compact=True)
        save_btn.clicked.connect(self._save_current)
        row_layout.addWidget(save_btn)
        layout.addWidget(row)
        self._scroll = ScrollArea(self)
        self._host = QWidget()
        self._host.setStyleSheet("background: transparent;")
        self._grid = QVBoxLayout(self._host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(0)
        self._scroll.set_content(self._host)
        layout.addWidget(self._scroll, 1)
        self._empty = Label("", "small")
        layout.addWidget(self._empty)
        self._status = Label("", "small", wrap=True)
        layout.addWidget(self._status)
        buttons = QWidget()
        button_row = QHBoxLayout(buttons)
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        load_btn = CutButton(tr("timeline.common.load_selected"), "primary")
        load_btn.clicked.connect(self._load_selected)
        delete_btn = CutButton(tr("timeline.common.delete_selected"), "danger", icon="trash")
        delete_btn.clicked.connect(self._delete_selected)
        close_btn = CutButton(tr("timeline.common.close"), "ghost")
        close_btn.clicked.connect(self.reject)
        button_row.addStretch(1)
        for button in (delete_btn, load_btn, close_btn):
            button_row.addWidget(button)
        layout.addWidget(buttons)
        self._rebuild()

    def _rebuild(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        entries = self.service.team_library_list(self._search.text())
        for entry in entries:
            row = _AxisRow({"name": entry.get("name"), "team": entry.get("slots"),
                            "event_count": 0, "duration": 0, "updated": entry.get("updated"),
                            "id": entry.get("id")}, self._host)
            row.clicked.connect(self._select)
            row.double_clicked.connect(lambda _id: self._load(_id))
            self._grid.addWidget(row)
            if entry.get("id") == self._selected:
                row.set_selected(True)
        self._grid.addStretch(1)
        self._empty.setText(tr("timeline.team_library.empty") if not entries else "")

    def _select(self, entry_id: str) -> None:
        self._selected = entry_id
        for index in range(self._grid.count()):
            widget = self._grid.itemAt(index).widget()
            if isinstance(widget, _AxisRow):
                widget.set_selected(str(widget.entry.get("id")) == entry_id)

    def _save_current(self) -> None:
        entry = self.service.team_library_save(self._name.text().strip())
        if entry is None:
            self._status.setText(tr("timeline.common.save_failed"))
            return
        self._status.setText(tr("timeline.team_library.saved", name=entry.get("name")))
        self._rebuild()

    def _load_selected(self) -> None:
        if not self._selected:
            self._status.setText(tr("timeline.team_library.need_select"))
            return
        self._load(self._selected)

    def _load(self, entry_id: str) -> None:
        entry = self.service.library.team_get(entry_id) or {}
        if any(self.service.team.to_list()) and not confirm(
            self, title=tr("timeline.confirm.load_team.title"),
            message=tr("timeline.confirm.load_team.message", name=entry.get("name") or tr("timeline.defaults.unnamed_team")),
            action_text=tr("timeline.confirm.load_team.action"),
        ):
            return
        if self.service.team_library_load(entry_id):
            self.accept()
        else:
            self._status.setText(tr("timeline.team_library.load_failed"))

    def _delete_selected(self) -> None:
        if not self._selected:
            self._status.setText(tr("timeline.team_library.need_select"))
            return
        entry = self.service.library.team_get(self._selected) or {}
        if not confirm(self, title=tr("timeline.confirm.delete_team.title"),
                       message=tr("timeline.confirm.delete_team.message", name=entry.get("name") or tr("timeline.defaults.unnamed_team")),
                       action_text=tr("timeline.confirm.delete_team.action")):
            return
        if self.service.team_library_delete(self._selected):
            self._status.setText(tr("timeline.team_library.deleted"))
            self._selected = None
            self._rebuild()
        else:
            self._status.setText(tr("timeline.common.delete_failed"))
