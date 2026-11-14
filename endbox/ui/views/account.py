# -*- coding: utf-8 -*-
"""账号中心视图：连接状态 + 森空岛扫码 + B 服扫码 + 玩家资料。

设计规则见 docs/CONTRACT.md §1：
  · 内部容器直角 + 细线；只有可点击的按钮切右下角；
  · 唯一黄色只做填充（状态 / 演示提示 / 焦点），红色只给「退出登录」；
  · 数字都是真实数据（同步时间、角色数、扫码剩余时间），没有就显示 —。

服务缺失（ctx.services.account is None）时本视图仍然能打开并提示"模块未载入"。
"""
from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from endbox.app_context import AppContext
from endbox.core.event_bus import Topics
from endbox.core.text import t
from endbox.core.workers import Poller
from endbox.modules.account import bilibili as bili
from endbox.modules.account import qr as qr_mod
from endbox.modules.account import skland
from endbox.modules.account.models import (
    PROVIDER_BILIBILI,
    PROVIDER_LABELS,
    PROVIDER_SKLAND,
    AccountError,
    format_sync,
    format_timestamp,
)
from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.ui.kit import (
    Avatar,
    Badge,
    BusyBar,
    CutButton,
    EmptyState,
    FieldText,
    Hairline,
    IconButton,
    InfoFlag,
    Label,
    NumberStat,
    Panel,
    ProgressBar,
    QRCanvas,
    ScrollArea,
    SectionHead,
    Segmented,
    Spacer,
    Toggle,
    confirm,
)
from endbox.ui.kit.base import EWidget, pal
from endbox.ui.views.base import BaseView

QR_SIZE = 160
LEFT_COLUMN_WIDTH = 470
POLL_INTERVAL_MS = 2000
MAX_POLL_ERRORS = 5
MAX_AUTO_REFRESH = 3
STAMINA_TICK_MS = 1000


def _provider_short(provider: str) -> str:
    return {
        PROVIDER_SKLAND: t("account.provider.skland_short"),
        PROVIDER_BILIBILI: t("account.provider.bili_short"),
    }.get(provider, t("common.placeholder"))


def _provider_badge_text(provider: str, demo: bool) -> str:
    if demo:
        return t("account.provider.demo_badge")
    return {
        PROVIDER_SKLAND: t("account.provider.skland_badge"),
        PROVIDER_BILIBILI: t("account.provider.bili_badge"),
    }.get(provider, t("common.placeholder"))


def _mmss(seconds: int) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _field_sync(ts: Any) -> str:
    return t("account.status.field_sync", time=format_sync(ts))


def _field_roles(count: Any) -> str:
    return t("account.status.field_roles", count=count)


def _field_source(source: Any) -> str:
    return t("account.status.field_source", source=source)


def _field_state(state: Any) -> str:
    return t("account.scan.field_state", state=state)


def _field_remaining(remaining: Any) -> str:
    return t("account.scan.field_remaining", time=remaining)


def _text_or_dash(value: Any) -> str:
    if value is None or value == "":
        return t("common.placeholder")
    return str(value)


def _countdown(seconds: Any) -> str:
    """秒 → `1:23:45` / `23:45`；None/非法 → —。"""
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return t("common.placeholder")
    total = max(0, total)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _ratio(value: Any, maximum: Any) -> float:
    try:
        current = float(value)
        top = float(maximum)
    except (TypeError, ValueError):
        return 0.0
    if top <= 0:
        return 0.0
    return max(0.0, min(1.0, current / top))


OPERATOR_COLUMNS = (
    ("potential_level", 54, t("account.operators.column_potential")),
    ("evolve_phase", 54, t("account.operators.column_evolve")),
    ("level", 66, t("account.operators.column_level")),
    ("element", 64, t("account.operators.column_element")),
    ("profession", 64, t("account.operators.column_profession")),
    ("rarity", 52, t("account.operators.column_rarity")),
)
OPERATOR_AVATAR = 38


def _operator_columns_start(width: int) -> int:
    return width - sum(item[1] for item in OPERATOR_COLUMNS) - 4


def _operator_cell_text(key: str, value: Any) -> str:
    if value is None or value == "":
        return t("common.placeholder")
    if key == "potential_level":
        return t("account.operators.potential_value", value=value)
    if key == "evolve_phase":
        return t("account.operators.evolve_value", value=value)
    if key == "level":
        return t("account.operators.level_value", value=value)
    if key == "rarity":
        return t("account.operators.rarity_value", value=value)
    return str(value)


class _OperatorHeader(EWidget):
    """干员列表表头。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(20)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        font = F.field_font(10)
        paint.draw_text(
            p,
            QRectF(OPERATOR_AVATAR + 10, 0, 120, self.height()),
            t("account.operators.column_operator"),
            font,
            colors.ink2,
        )
        x = _operator_columns_start(self.width())
        for _key, width, label in OPERATOR_COLUMNS:
            paint.draw_text(
                p,
                QRectF(x, 0, width, self.height()),
                label,
                font,
                colors.ink2,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
            x += width
        paint.hairline(p, 0, self.height() - 1, self.width(), self.height() - 1, colors.rule)
        p.end()


class _OperatorRow(EWidget):
    """一行干员：头像 / 名称 / 稀有度 / 职业 / 元素 / 等级 / 精英化 / 潜能。"""

    def __init__(self, operator: dict[str, Any], parent=None):
        super().__init__(parent)
        self._operator = dict(operator)
        self._pixmap: QPixmap | None = None
        avatar_path = operator.get("avatar_path")
        if avatar_path:
            pix = QPixmap(str(avatar_path))
            if not pix.isNull():
                self._pixmap = pix
        self.setFixedHeight(46)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        weapon = " ".join(
            part
            for part in (
                _text_or_dash(operator.get("weapon_name")),
                (
                    _operator_cell_text("level", operator.get("weapon_level"))
                    if operator.get("weapon_level") is not None
                    else ""
                ),
            )
            if part and part != t("common.placeholder")
        )
        if weapon:
            self.setToolTip(weapon)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        colors = pal()
        height = self.height()
        avatar_rect = QRectF(0, (height - OPERATOR_AVATAR) / 2, OPERATOR_AVATAR, OPERATOR_AVATAR)
        avatar_path = paint.cut_rect(avatar_rect, 8, "br")
        if self._pixmap is not None:
            p.save()
            p.setClipPath(avatar_path)
            scaled = self._pixmap.scaled(
                OPERATOR_AVATAR,
                OPERATOR_AVATAR,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            p.drawPixmap(avatar_rect, scaled, QRectF(scaled.rect()))
            p.restore()
        else:
            p.fillPath(avatar_path, colors.chip)
            paint.draw_text(
                p,
                avatar_rect,
                str(self._operator.get("name") or t("common.placeholder"))[:1],
                F.font(15, weight=QFont.Weight.Bold, wide=True),
                colors.ink2,
                Qt.AlignmentFlag.AlignCenter,
            )
        paint.stroke_cut(p, avatar_rect.adjusted(0.5, 0.5, -0.5, -0.5), colors.rule, 8, "br", 1.0)

        start = _operator_columns_start(self.width())
        name_rect = QRectF(OPERATOR_AVATAR + 10, 0, max(0, start - OPERATOR_AVATAR - 16), height)
        name = str(self._operator.get("name") or t("common.placeholder"))
        paint.draw_text(
            p,
            name_rect,
            name,
            F.font(14, weight=QFont.Weight.DemiBold, cjk_first=True),
            colors.ink,
            elide=True,
        )

        x = start
        for key, width, _label in OPERATOR_COLUMNS:
            value = self._operator.get(key)
            if key == "rarity" and isinstance(value, int) and value >= 6:
                chip = QRectF(x + 6, (height - 20) / 2, width - 12, 20)
                paint.fill_cut(p, chip, colors.signal, 6, "br")
                paint.draw_text(
                    p,
                    chip,
                    _operator_cell_text(key, value),
                    F.field_font(11),
                    colors.on_signal,
                    Qt.AlignmentFlag.AlignCenter,
                )
            else:
                paint.draw_text(
                    p,
                    QRectF(x, 0, width, height),
                    _operator_cell_text(key, value),
                    F.font(12, cjk_first=True),
                    colors.ink2,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    elide=True,
                )
            x += width
        paint.hairline(p, 0, height - 1, self.width(), height - 1, colors.rule)
        p.end()


class _DomainRow(EWidget):
    """一格地区探索：名称 / 等级 / 据点 + 收集计数（缺值 —）。"""

    def __init__(self, domain: dict[str, Any], parent=None):
        super().__init__(parent)
        self._domain = dict(domain)
        self.setFixedHeight(56)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        paint.draw_text(
            p,
            QRectF(0, 0, 150, 28),
            str(self._domain.get("name") or t("common.placeholder")),
            F.font(14, weight=QFont.Weight.DemiBold, cjk_first=True),
            colors.ink,
            elide=True,
        )
        level = self._domain.get("level")
        paint.draw_text(
            p,
            QRectF(150, 0, 74, 28),
            t("account.exploration.area_level", value=_text_or_dash(level)),
            F.font(12, cjk_first=True),
            colors.ink2,
            elide=True,
        )
        settlements = self._domain.get("settlements")
        max_level = self._domain.get("settlement_max_level")
        paint.draw_text(
            p,
            QRectF(228, 0, max(0, self.width() - 232), 28),
            t(
                "account.exploration.settlements",
                count=_text_or_dash(settlements),
                max_level=_text_or_dash(max_level),
            ),
            F.font(12, cjk_first=True),
            colors.ink2,
            elide=True,
        )
        collections = t(
            "account.exploration.collections",
            puzzle=_text_or_dash(self._domain.get("puzzle")),
            chest=_text_or_dash(self._domain.get("chest")),
            piece=_text_or_dash(self._domain.get("piece")),
            blackbox=_text_or_dash(self._domain.get("blackbox")),
        )
        paint.draw_text(
            p,
            QRectF(0, 28, self.width(), 28),
            collections,
            F.font(12, cjk_first=True),
            colors.ink2,
            elide=True,
        )
        paint.hairline(p, 0, self.height() - 1, self.width(), self.height() - 1, colors.rule)
        p.end()


class _ScannableQR(QRCanvas):
    """扫码用的二维码画布：始终白底黑模块，保证手机能扫（深色主题也不反色）。

    部件库的 QRCanvas 用主题色绘制；深色主题下会变成浅色模块 / 深色底，
    部分扫码器无法识别，所以这里按 QR 的物理要求固定黑白，仍复用 set_matrix()。
    """

    def has_matrix(self) -> bool:
        return bool(getattr(self, "_qr_matrix", None))

    def set_matrix(self, matrix: list[list[bool]]) -> None:
        self._qr_matrix = matrix
        super().set_matrix(matrix)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(QRectF(0, 0, self.width(), self.height()), QColor("#FFFFFF"))
        matrix = getattr(self, "_qr_matrix", None) or []
        if not matrix:
            p.end()
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        n = len(matrix)
        quiet = 4
        cell = min(self.width(), self.height()) / (n + quiet * 2)
        offset = (self.width() - cell * n) / 2
        oy = (self.height() - cell * n) / 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#111111"))
        for y, row in enumerate(matrix):
            for x, value in enumerate(row):
                if value:
                    p.drawRect(QRectF(offset + x * cell, oy + y * cell, cell + 0.35, cell + 0.35))
        p.end()


class _QrPlaceholder(EWidget):
    """二维码未生成时的占位：斜线纹 + 两句说明。"""

    def __init__(self, parent=None, *, size: int = QR_SIZE):
        super().__init__(parent)
        self._size = size
        self._eyebrow = t("account.scan.placeholder_eyebrow")
        self._desc = t("account.scan.not_generated")
        self.setFixedSize(size, size)

    def set_texts(self, eyebrow: str, desc: str) -> None:
        self._eyebrow = eyebrow
        self._desc = desc
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        p.fillRect(rect, colors.chip)
        paint.paint_hatch(p, rect, colors.ink, alpha=0.08)
        p.setPen(colors.rule)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        paint.draw_text(
            p,
            QRectF(0, self.height() / 2 - 22, self.width(), 20),
            self._eyebrow,
            F.eyebrow_font(11),
            colors.ink2,
            Qt.AlignmentFlag.AlignCenter,
        )
        paint.draw_text(
            p,
            QRectF(0, self.height() / 2 + 2, self.width(), 20),
            self._desc,
            F.font(13, cjk_first=True),
            colors.ink2,
            Qt.AlignmentFlag.AlignCenter,
            elide=True,
        )
        p.end()


class _ScanCard(Panel):
    """扫码卡：二维码占位 / 画布 + 状态 + 剩余时间 + 刷新 / 取消 + 告警。"""

    def __init__(
        self,
        title: str,
        eyebrow: str,
        caption: str,
        guide: str,
        parent=None,
        *,
        qr_size: int = QR_SIZE,
    ):
        super().__init__(title, eyebrow, parent, padding=16, spacing=10)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)

        slot = QWidget()
        slot.setFixedSize(qr_size, qr_size)
        slot.setStyleSheet("background: transparent;")
        stack = QStackedLayout(slot)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setStackingMode(QStackedLayout.StackingMode.StackOne)
        self._placeholder = _QrPlaceholder(size=qr_size)
        self._canvas = _ScannableQR(size=qr_size)
        stack.addWidget(self._placeholder)
        stack.addWidget(self._canvas)
        self._stack = stack
        body.addWidget(slot, 0, Qt.AlignmentFlag.AlignTop)

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(6)
        info.addWidget(Label(caption, "body"))
        guide_label = Label(guide, "muted", wrap=True)
        info.addWidget(guide_label)
        self._status = Label(t("account.scan.status_idle"), "muted", wrap=True)
        info.addWidget(self._status)
        self._fields = FieldText(
            [
                _field_state(t("account.scan.state_not_started")),
                _field_remaining(t("common.placeholder")),
            ]
        )
        info.addWidget(self._fields)
        info.addStretch(1)
        body.addLayout(info, 1)
        self.add_layout(body)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        self.primary = CutButton(t("account.scan.button_generate"), "default", icon="qr")
        buttons.addWidget(self.primary)
        self.cancel = CutButton(t("common.cancel"), "ghost", icon="close")
        self.cancel.hide()
        buttons.addWidget(self.cancel)
        buttons.addStretch(1)
        self.add_layout(buttons)

        self._warn = InfoFlag("", kind="warn")
        self._warn.hide()
        self.add(self._warn)

    # -- 状态展示 -----------------------------------------------------------
    def show_matrix(self, matrix: list[list[bool]]) -> None:
        self._canvas.set_matrix(matrix)
        self._stack.setCurrentWidget(self._canvas)

    def show_placeholder(self, eyebrow: str | None = None, desc: str | None = None) -> None:
        eyebrow = eyebrow if eyebrow is not None else t("account.scan.placeholder_eyebrow")
        desc = desc if desc is not None else t("account.scan.not_generated")
        self._canvas.set_matrix([])
        self._placeholder.set_texts(eyebrow, desc)
        self._stack.setCurrentWidget(self._placeholder)

    def reset_idle(self) -> None:
        self.show_placeholder(
            t("account.scan.placeholder_eyebrow"), t("account.scan.not_generated")
        )
        self.set_status(t("account.scan.status_idle"))
        self.set_fields(
            [
                _field_state(t("account.scan.state_not_started")),
                _field_remaining(t("common.placeholder")),
            ]
        )
        self.set_primary(t("account.scan.button_generate"), "qr")
        self.set_cancel_visible(False)
        self.clear_warn()

    def show_linked(self, desc: str | None = None) -> None:
        self.show_placeholder(
            t("account.scan.eyebrow_linked"),
            desc if desc is not None else t("account.scan.linked_desc"),
        )
        self.set_status(t("account.scan.status_linked"))
        self.set_fields(
            [
                _field_state(t("account.scan.state_logged_in")),
                _field_remaining(t("common.placeholder")),
            ]
        )
        self.set_primary(t("account.scan.button_rescan"), "refresh")
        self.set_cancel_visible(False)
        self.clear_warn()

    def set_status(self, text: str) -> None:
        self._status.setText(text)

    def set_fields(self, fields: list[str]) -> None:
        self._fields.set_fields(fields)

    def set_primary(self, text: str, icon: str | None = None) -> None:
        self.primary.setText(text)
        if icon is not None:
            self.primary.set_icon(icon)

    def set_cancel_visible(self, visible: bool) -> None:
        self.cancel.setVisible(visible)

    def set_enabled(self, enabled: bool) -> None:
        self.primary.setEnabled(enabled)
        self.cancel.setEnabled(enabled)

    def warn(self, text: str, *, limit: int = 54) -> None:
        display = text if len(text) <= limit else text[: limit - 1] + "…"
        self._warn.set_text(display)
        self._warn.setToolTip(text)
        self._warn.show()

    def clear_warn(self) -> None:
        self._warn.hide()


# 角色列表的列（从右往左排）：(键, 宽度, 表头)
ROLE_COLUMNS = (
    ("server", 74, t("account.profile.column_server")),
    ("klass", 64, t("account.profile.column_class")),
    ("element", 70, t("account.profile.column_element")),
    ("level", 44, t("account.profile.column_level")),
)


def _column_start(width: int) -> int:
    return width - sum(item[1] for item in ROLE_COLUMNS) - 8


class _RoleHeader(EWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(20)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        font = F.field_font(10)
        paint.draw_text(
            p,
            QRectF(0, 0, 26, self.height()),
            t("account.profile.column_index"),
            font,
            colors.ink2,
        )
        paint.draw_text(
            p,
            QRectF(32, 0, max(0, _column_start(self.width()) - 40), self.height()),
            t("account.profile.column_operator"),
            font,
            colors.ink2,
        )
        x = _column_start(self.width())
        for _key, width, label in ROLE_COLUMNS:
            paint.draw_text(p, QRectF(x, 0, width, self.height()), label, font, colors.ink2)
            x += width
        paint.hairline(p, 0, self.height() - 1, self.width(), self.height() - 1, colors.rule)
        p.end()


class _RoleRow(EWidget):
    """一行角色：编号 + 昵称 + 服务器 / 职业 / 属性 / 等级。缺值显示 —。"""

    def __init__(self, index: int, role: dict[str, Any], parent=None):
        super().__init__(parent)
        self._index = index
        self._role = dict(role)
        self.setFixedHeight(34)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        height = self.height()
        paint.draw_text(
            p, QRectF(0, 0, 26, height), f"{self._index:02d}", F.field_font(11), colors.ink2
        )
        start = _column_start(self.width())
        name_width = max(0, start - 40)
        paint.draw_text(
            p,
            QRectF(32, 0, name_width, height),
            str(self._role.get("name") or t("common.placeholder")),
            F.font(14, weight=QFont.Weight.DemiBold, cjk_first=True),
            colors.ink,
            elide=True,
        )
        x = start
        for key, width, _label in ROLE_COLUMNS:
            # 契约里的字段名是 "class"（Python 关键字），行模型里用 klass 承载
            value = self._role.get("class") if key == "klass" else self._role.get(key)
            if value is None and key == "klass":
                value = self._role.get("klass")
            text = t("common.placeholder") if value in (None, "") else str(value)
            paint.draw_text(
                p,
                QRectF(x, 0, width, height),
                text,
                F.font(12, cjk_first=True),
                colors.ink2,
                elide=True,
            )
            x += width
        paint.hairline(p, 0, height - 1, self.width(), height - 1, colors.rule)
        p.end()


class AccountView(BaseView):
    key = "account"
    eyebrow = t("account.eyebrow")
    title = t("account.view_title")
    icon = "account"
    order = 10
    sidebar_label = t("account.sidebar_label")
    description = t("account.description")

    # -- 构建 ---------------------------------------------------------------
    def build(self) -> None:
        self._state: dict[str, Any] | None = None
        self._last_available: bool | None = None
        self._was_online = False

        # 森空岛扫码状态机
        self._skland_scan_id: str | None = None
        self._skland_started = 0.0
        self._skland_polling = False
        self._skland_generating = False
        self._skland_confirming = False
        self._skland_errors = 0
        self._skland_auto_refresh = 0
        self._skland_auto_pending = False
        self._skland_state_text = t("account.scan.state_not_started")

        # B 服扫码状态机
        self._bili_key: str | None = None
        self._bili_started = 0.0
        self._bili_polling = False
        self._bili_generating = False
        self._bili_confirming = False
        self._bili_errors = 0
        self._bili_state_text = t("account.scan.state_not_started")

        # 2026-11 真实玩家数据（card/detail）
        self._players: list[dict[str, Any]] = []
        self._operators: list[dict[str, Any]] = []
        self._domains: list[dict[str, Any]] = []
        self._stamina_full_ts: int | None = None
        self._last_operator_signature: tuple | None = None

        scroll = ScrollArea(self)
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._busy = BusyBar()
        self._busy.hide()
        layout.addWidget(self._busy)

        self._missing_flag = InfoFlag(t("account.flags.missing"), kind="warn")
        self._missing_flag.hide()
        layout.addWidget(self._missing_flag)

        layout.addWidget(self._build_status_band())
        layout.addWidget(Hairline())

        self._demo_flag = InfoFlag(t("account.flags.demo"), kind="warn")
        self._demo_flag.hide()
        layout.addWidget(self._demo_flag)
        self._error_flag = InfoFlag("", kind="warn")
        self._error_flag.hide()
        layout.addWidget(self._error_flag)

        # -- 在线：真实玩家数据 -------------------------------------------------
        self._online_host = QWidget()
        self._online_host.setStyleSheet("background: transparent;")
        online = QVBoxLayout(self._online_host)
        online.setContentsMargins(0, 0, 0, 0)
        online.setSpacing(14)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(14)
        top_row.addWidget(self._build_profile_panel(), 3)
        top_row.addWidget(self._build_stamina_panel(), 2)
        top_row.addWidget(self._build_scale_panel(), 1)
        online.addLayout(top_row)
        online.addWidget(self._build_operators_panel())

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(14)
        bottom_row.addWidget(self._build_exploration_panel(), 3)
        bottom_row.addWidget(self._build_progress_panel(), 2)
        online.addLayout(bottom_row)
        self._online_host.hide()
        layout.addWidget(self._online_host)

        # -- 离线 / 未载入：扫码登录 -------------------------------------------
        self._offline_host = QWidget()
        self._offline_host.setStyleSheet("background: transparent;")
        offline = QHBoxLayout(self._offline_host)
        offline.setContentsMargins(0, 0, 0, 0)
        offline.setSpacing(14)

        left = QWidget()
        left.setStyleSheet("background: transparent;")
        left.setFixedWidth(LEFT_COLUMN_WIDTH)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(14)
        left_layout.addWidget(self._build_skland_card())
        left_layout.addWidget(self._build_bili_card())
        left_layout.addStretch(1)
        offline.addWidget(left)

        right = QWidget()
        right.setStyleSheet("background: transparent;")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(14)
        right_layout.addWidget(self._build_offline_profile_panel())
        right_layout.addStretch(1)
        offline.addWidget(right, 1)
        layout.addWidget(self._offline_host)

        layout.addWidget(self._build_notes_panel())
        layout.addStretch(1)
        scroll.set_content(host)
        self._root.addWidget(scroll)

        self._skland_poller = Poller(POLL_INTERVAL_MS, self._skland_tick, self)
        self._bili_poller = Poller(POLL_INTERVAL_MS, self._bili_tick, self)
        self._skland_auto_timer = QTimer(self)
        self._skland_auto_timer.setSingleShot(True)
        self._skland_auto_timer.timeout.connect(self._start_skland_qr_auto)
        self._stamina_timer = QTimer(self)
        self._stamina_timer.setInterval(STAMINA_TICK_MS)
        self._stamina_timer.timeout.connect(self._on_stamina_tick)

        self.ctx.bus.subscribe(Topics.ACCOUNT_UPDATED, self._on_account_updated)
        self.ctx.bus.subscribe(Topics.ACCOUNT_STATE, self._on_account_state)
        self.refresh()

    # -- 连接状态带 ---------------------------------------------------------
    def _build_status_band(self) -> QWidget:
        band = QWidget()
        band.setStyleSheet("background: transparent;")
        outer = QVBoxLayout(band)
        outer.setContentsMargins(0, 0, 0, 4)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        head = SectionHead(t("account.status.eyebrow"), t("account.status.title"))
        head.setFixedSize(200, 44)
        row.addWidget(head, 0, Qt.AlignmentFlag.AlignVCenter)
        self._status_badge = Badge(t("account.status.badge_off"))
        row.addWidget(self._status_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self._status_label = Label(t("account.status.offline"), "h2")
        row.addWidget(self._status_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._provider_label = Label(t("account.status.unbound"), "muted")
        row.addWidget(self._provider_label, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self._refresh_btn = IconButton(
            "refresh", t("account.status.refresh_tooltip"), size=32, icon_size=15
        )
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)
        row.addWidget(self._refresh_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self._logout_btn = CutButton(
            t("account.status.logout"), "danger", icon="unlock", height=32
        )
        self._logout_btn.clicked.connect(self._on_logout)
        self._logout_btn.hide()
        row.addWidget(self._logout_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(row)

        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(12)
        self._status_fields = FieldText(
            [_field_sync(0), _field_roles(0), _field_source(t("common.placeholder"))]
        )
        row2.addWidget(self._status_fields, 0, Qt.AlignmentFlag.AlignVCenter)
        row2.addStretch(1)
        self._demo_toggle = Toggle(t("account.status.demo_toggle"))
        self._demo_toggle.clicked.connect(self._on_demo_toggled)
        row2.addWidget(self._demo_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(row2)
        return band

    # -- 扫码卡 -------------------------------------------------------------
    def _build_skland_card(self) -> _ScanCard:
        card = _ScanCard(
            t("account.skland.title"),
            t("account.skland.eyebrow"),
            t("account.skland.caption"),
            t("account.skland.guide"),
        )
        card.primary.clicked.connect(self._start_skland_qr)
        card.cancel.clicked.connect(self._cancel_skland_qr)
        self._skland_card = card
        return card

    def _build_bili_card(self) -> _ScanCard:
        card = _ScanCard(
            t("account.bili.title"),
            t("account.bili.eyebrow"),
            t("account.bili.caption"),
            t("account.bili.guide"),
        )
        card.primary.clicked.connect(self._start_bili_qr)
        card.cancel.clicked.connect(self._cancel_bili_qr)
        self._bili_card = card
        return card

    # -- 玩家卡 -------------------------------------------------------------
    def _build_profile_panel(self) -> Panel:
        panel = Panel(
            t("account.profile.title"), t("account.profile.eyebrow"), padding=16, spacing=10
        )
        self._provider_badge = Badge(t("common.placeholder"))
        panel.add_header_action(self._provider_badge)
        self._role_selector = Segmented([], height=26)
        self._role_selector.changed.connect(self._on_role_changed)
        self._role_selector.hide()

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(14)
        self._player_avatar = Avatar(size=64, cut=12)
        top.addWidget(self._player_avatar, 0, Qt.AlignmentFlag.AlignTop)
        identity = QVBoxLayout()
        identity.setContentsMargins(0, 0, 0, 0)
        identity.setSpacing(6)
        self._player_name = Label(t("common.placeholder"), "h2")
        identity.addWidget(self._player_name)
        self._player_fields = FieldText(
            [
                t("account.profile.field_uid", uid=t("common.placeholder")),
                _field_source(t("common.placeholder")),
            ]
        )
        identity.addWidget(self._player_fields)
        self._player_sync = Label(
            t("account.profile.sync", time=format_sync(0)), "muted"
        )
        identity.addWidget(self._player_sync)
        identity.addStretch(1)
        top.addLayout(identity, 1)
        panel.add_layout(top)

        role_row = QHBoxLayout()
        role_row.setContentsMargins(0, 0, 0, 0)
        role_row.setSpacing(10)
        self._role_label = Label(t("account.profile.role_select"), "small")
        role_row.addWidget(self._role_label, 0, Qt.AlignmentFlag.AlignVCenter)
        role_row.addWidget(self._role_selector, 0, Qt.AlignmentFlag.AlignVCenter)
        role_row.addStretch(1)
        panel.add_layout(role_row)
        self._role_label.hide()

        self._player_note = Label("", "muted", wrap=True)
        self._player_note.hide()
        panel.add(self._player_note)

        stats = QHBoxLayout()
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setSpacing(20)
        self._stat_player_level = NumberStat(
            t("account.profile.stat_level"), t("common.placeholder"), size=28
        )
        self._stat_world_level = NumberStat(
            t("account.profile.stat_world_level"), t("common.placeholder"), size=28
        )
        self._stat_exp = NumberStat(
            t("account.profile.stat_exp"), t("common.placeholder"), size=28
        )
        for stat in (self._stat_player_level, self._stat_world_level, self._stat_exp):
            stats.addWidget(stat)
        stats.addStretch(1)
        panel.add_layout(stats)

        self._player_meta = FieldText(
            [
                t("account.profile.field_last_login", time=t("common.placeholder")),
                t("account.profile.field_create_time", time=t("common.placeholder")),
            ]
        )
        panel.add(self._player_meta)
        self._player_mission = Label("", "small", wrap=True)
        self._player_mission.hide()
        panel.add(self._player_mission)

        # 旧版「绑定角色列表」保留为降级视图：完整数据失败时仍能看角色名/服务器
        self._binding_fallback = QWidget()
        self._binding_fallback.setStyleSheet("background: transparent;")
        binding_layout = QVBoxLayout(self._binding_fallback)
        binding_layout.setContentsMargins(0, 0, 0, 0)
        binding_layout.setSpacing(0)
        self._binding_header = _RoleHeader()
        binding_layout.addWidget(self._binding_header)
        self._binding_host = QWidget()
        self._binding_host.setStyleSheet("background: transparent;")
        self._binding_rows = QVBoxLayout(self._binding_host)
        self._binding_rows.setContentsMargins(0, 0, 0, 0)
        self._binding_rows.setSpacing(0)
        binding_layout.addWidget(self._binding_host)
        self._binding_note = Label("", "muted", wrap=True)
        self._binding_note.hide()
        binding_layout.addWidget(self._binding_note)
        self._binding_fallback.hide()
        panel.add(self._binding_fallback)
        return panel

    # -- 理智卡 -------------------------------------------------------------
    def _build_stamina_panel(self) -> Panel:
        panel = Panel(
            t("account.stamina.title"), t("account.stamina.eyebrow"), padding=16, spacing=10
        )
        self._stamina_badge = Badge(t("common.placeholder"))
        panel.add_header_action(self._stamina_badge)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self._stamina_value = NumberStat(
            t("account.stamina.current"), t("common.placeholder"), size=40
        )
        row.addWidget(self._stamina_value)
        self._stamina_cap = Label(
            t("account.stamina.cap", value=t("common.placeholder")), "muted"
        )
        row.addWidget(self._stamina_cap, 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch(1)
        panel.add_layout(row)
        self._stamina_progress = ProgressBar(height=5)
        panel.add(self._stamina_progress)
        self._stamina_countdown = Label(
            t("account.stamina.full_in", time=t("common.placeholder")), "muted"
        )
        panel.add(self._stamina_countdown)
        self._stamina_hint = Label("", "small", wrap=True)
        self._stamina_hint.hide()
        panel.add(self._stamina_hint)
        return panel

    # -- 规模统计 -----------------------------------------------------------
    def _build_scale_panel(self) -> Panel:
        panel = Panel(
            t("account.scale.title"), t("account.scale.eyebrow"), padding=16, spacing=10
        )
        self._stat_operators = NumberStat(
            t("account.scale.operators"), t("common.placeholder"), size=26
        )
        self._stat_weapons = NumberStat(
            t("account.scale.weapons"), t("common.placeholder"), size=26
        )
        self._stat_docs = NumberStat(
            t("account.scale.documents"), t("common.placeholder"), size=26
        )
        for stat in (self._stat_operators, self._stat_weapons, self._stat_docs):
            panel.add(stat)
        self._scale_note = Label("", "muted", wrap=True)
        self._scale_note.hide()
        panel.add(self._scale_note)
        panel.add(Spacer(h=0, expand=True))
        return panel

    # -- 干员列表 -----------------------------------------------------------
    def _build_operators_panel(self) -> Panel:
        panel = Panel(
            t("account.operators.title"), t("account.operators.eyebrow"), padding=16, spacing=10
        )
        self._operator_badge = Badge(t("common.placeholder"))
        panel.add_header_action(self._operator_badge)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(10)
        controls.addWidget(Label(t("account.operators.sort"), "small"))
        self._operator_sort = Segmented(
            [
                t("account.operators.sort_level"),
                t("account.operators.sort_rarity"),
                t("account.operators.sort_potential"),
            ],
            height=26,
        )
        self._operator_sort.changed.connect(lambda _i: self._rebuild_operators())
        controls.addWidget(self._operator_sort)
        controls.addSpacing(8)
        controls.addWidget(Label(t("account.operators.filter"), "small"))
        self._operator_filter = Segmented(
            [
                t("account.operators.filter_all"),
                t("account.operators.filter_six"),
                t("account.operators.filter_five"),
                t("account.operators.filter_low"),
            ],
            height=26,
        )
        self._operator_filter.changed.connect(lambda _i: self._rebuild_operators())
        controls.addWidget(self._operator_filter)
        controls.addStretch(1)
        panel.add_layout(controls)

        self._operator_header = _OperatorHeader()
        panel.add(self._operator_header)
        self._operator_host = QWidget()
        self._operator_host.setStyleSheet("background: transparent;")
        self._operator_rows = QVBoxLayout(self._operator_host)
        self._operator_rows.setContentsMargins(0, 0, 0, 0)
        self._operator_rows.setSpacing(0)
        panel.add(self._operator_host)
        self._operator_note = Label("", "muted", wrap=True)
        self._operator_note.hide()
        panel.add(self._operator_note)
        return panel

    # -- 地区探索 -----------------------------------------------------------
    def _build_exploration_panel(self) -> Panel:
        panel = Panel(
            t("account.exploration.title"),
            t("account.exploration.eyebrow"),
            padding=16,
            spacing=10,
        )
        self._domain_host = QWidget()
        self._domain_host.setStyleSheet("background: transparent;")
        self._domain_rows = QVBoxLayout(self._domain_host)
        self._domain_rows.setContentsMargins(0, 0, 0, 0)
        self._domain_rows.setSpacing(0)
        panel.add(self._domain_host)
        self._domain_note = Label("", "muted", wrap=True)
        self._domain_note.hide()
        panel.add(self._domain_note)
        return panel

    # -- 通行证 / 每日任务 / 签到 -------------------------------------------
    def _build_progress_panel(self) -> Panel:
        panel = Panel(
            t("account.progress.title"), t("account.progress.eyebrow"), padding=16, spacing=10
        )
        self._bp_label = Label(
            t("account.progress.bp", cur=t("common.placeholder"), max=t("common.placeholder")),
            "body",
        )
        panel.add(self._bp_label)
        self._bp_bar = ProgressBar(height=5)
        panel.add(self._bp_bar)
        self._daily_label = Label(
            t(
                "account.progress.daily",
                cur=t("common.placeholder"),
                max=t("common.placeholder"),
            ),
            "body",
        )
        panel.add(self._daily_label)
        self._daily_bar = ProgressBar(height=5)
        panel.add(self._daily_bar)
        self._achieve_label = Label(
            t("account.progress.achieve", count=t("common.placeholder")), "muted"
        )
        panel.add(self._achieve_label)

        sign_row = QHBoxLayout()
        sign_row.setContentsMargins(0, 0, 0, 0)
        sign_row.setSpacing(10)
        self._sign_btn = CutButton(t("account.sign.button"), "default", icon="check", height=30)
        self._sign_btn.clicked.connect(self._on_sign_clicked)
        sign_row.addWidget(self._sign_btn)
        self._sign_label = Label(t("account.sign.hint"), "small", wrap=True)
        sign_row.addWidget(self._sign_label, 1)
        panel.add_layout(sign_row)
        return panel

    # -- 离线空状态卡 -------------------------------------------------------
    def _build_offline_profile_panel(self) -> Panel:
        panel = Panel(
            t("account.profile.title"), t("account.profile.eyebrow"), padding=16, spacing=10
        )
        self._profile_empty = EmptyState(
            t("account.profile.empty_title"),
            t("account.profile.empty_description"),
        )
        self._profile_empty.setMinimumHeight(190)
        panel.add(self._profile_empty)
        self._profile_missing = EmptyState(
            t("account.profile.missing_title"),
            t("account.profile.missing_description"),
        )
        self._profile_missing.setMinimumHeight(190)
        self._profile_missing.hide()
        panel.add(self._profile_missing)
        return panel

    # -- 说明 ---------------------------------------------------------------
    def _build_notes_panel(self) -> Panel:
        panel = Panel(
            t("account.notes.title"), t("account.notes.eyebrow"), padding=16, spacing=10
        )
        panel.add(Label(t("account.notes.skland"), "small", wrap=True))
        panel.add(Label(t("account.notes.card_detail"), "small", wrap=True))
        panel.add(Label(t("account.notes.security"), "small", wrap=True))
        return panel

    # -- 数据刷新 -----------------------------------------------------------
    def refresh(self) -> None:
        if not hasattr(self, "_status_label"):
            return
        service = self.ctx.services.account
        if service is None:
            self._apply_service_state(None, None)
            return
        state = service.snapshot()
        if service.demo_enabled() and not state.get("demo"):
            service.refresh()
            state = service.snapshot()
        self._apply_service_state(service, state)

    def topbar_fields(self) -> list[str]:
        service = self.ctx.services.account
        if service is None:
            return [t("account.status.unloaded")]
        state = service.snapshot()
        if state.get("demo"):
            return [t("account.status.demo"), t("account.status.demo_note")]
        if state.get("online"):
            provider = _provider_short(str(state.get("provider") or ""))
            fields = [
                provider,
                _field_sync(state.get("last_sync")),
                _field_roles(len(state.get("roles") or [])),
            ]
            stamina = ((state.get("player") or {}).get("stamina")) or {}
            if stamina.get("cur") is not None and stamina.get("max") is not None:
                fields.append(
                    t(
                        "account.status.field_stamina",
                        cur=stamina.get("cur"),
                        max=stamina.get("max"),
                    )
                )
            return fields
        return [t("account.status.offline"), _field_sync(state.get("last_sync"))]

    def on_show(self) -> None:
        self.refresh()
        service = self.ctx.services.account
        if service is None:
            return
        if (
            service.has_credentials()
            and not service.demo_enabled()
            and not service.is_busy()
            and bool(self.ctx.settings.get("account.auto_refresh", True))
        ):
            self._begin_refresh(notify=False)
        self._resume_skland_qr()
        self._resume_bili_qr()

    def on_hide(self) -> None:
        self._skland_poller.stop()
        self._bili_poller.stop()
        self._stamina_timer.stop()
        self._skland_polling = False
        self._bili_polling = False
        self._busy.stop()
        if self._skland_auto_pending:
            self._skland_auto_timer.stop()
            self._skland_auto_pending = False
            self._skland_card.set_status(t("account.scan.paused_refresh"))
        elif self._skland_scan_id:
            self._skland_card.set_status(t("account.scan.paused_resume"))
        if self._bili_key:
            self._bili_card.set_status(t("account.scan.paused_resume"))

    # -- 状态 → 界面 --------------------------------------------------------
    def _apply_service_state(self, service, state: dict[str, Any] | None) -> None:
        self._state = state or None
        missing = service is None
        demo = bool(state.get("demo")) if state else False
        online = bool(state.get("online")) if state else False
        provider = str(state.get("provider") or "") if state else ""
        error = state.get("error") if state else None
        roles = list(state.get("roles") or []) if state else []
        player = (state.get("player") or None) if state else None
        operators = list(state.get("operators") or []) if state else []
        data_error = state.get("data_error") if state else None

        if missing:
            self._status_badge.set_text(t("account.status.badge_off"))
            self._status_badge.set_signal(False)
            self._status_label.setText(t("account.status.unloaded"))
            self._provider_label.setText(t("common.placeholder"))
            self._status_fields.set_fields(
                [
                    t("account.status.field_sync", time=t("common.placeholder")),
                    t("account.status.field_roles", count=t("common.placeholder")),
                    _field_source(t("common.placeholder")),
                ]
            )
        elif demo:
            self._status_badge.set_text(t("account.status.badge_demo"))
            self._status_badge.set_signal(True)
            self._status_label.setText(t("account.status.demo"))
            self._provider_label.setText(t("account.status.demo_provider"))
            self._status_fields.set_fields(
                [
                    _field_sync(state.get("last_sync")),
                    _field_roles(len(roles)),
                    _field_source(t("account.status.local_demo")),
                ]
            )
        elif online:
            self._status_badge.set_text(t("account.status.badge_on"))
            self._status_badge.set_signal(True)
            self._status_label.setText(t("account.status.online"))
            self._provider_label.setText(
                PROVIDER_LABELS.get(provider, t("common.placeholder"))
            )
            self._status_fields.set_fields(
                [
                    _field_sync(state.get("last_sync")),
                    _field_roles(len(roles)),
                    _field_source(_provider_short(provider)),
                ]
            )
        else:
            self._status_badge.set_text(t("account.status.badge_off"))
            self._status_badge.set_signal(False)
            self._status_label.setText(t("account.status.offline"))
            self._provider_label.setText(
                PROVIDER_LABELS.get(provider, t("account.status.unbound"))
                if provider
                else t("account.status.unbound")
            )
            self._status_fields.set_fields(
                [
                    _field_sync(state.get("last_sync") if state else 0),
                    _field_roles(0),
                    _field_source(
                        _provider_short(provider)
                        if provider
                        else t("common.placeholder")
                    ),
                ]
            )

        self._demo_toggle.setChecked(demo)
        available = service is not None
        self._logout_btn.setVisible(
            available and (online or demo or (_has_credentials(service) if service else False))
        )
        self._missing_flag.setVisible(missing)
        self._demo_flag.setVisible(demo)
        self._error_flag.setVisible(bool(error) and not demo)
        if error and not demo:
            self._set_flag_text(self._error_flag, str(error))

        if available != self._last_available:
            self._last_available = available
            self._refresh_btn.setEnabled(available)
            self._demo_toggle.setEnabled(available)
            self._skland_card.set_enabled(available)
            self._bili_card.set_enabled(available)
            if missing:
                self._skland_card.show_placeholder(
                    t("account.scan.eyebrow_off"), t("account.status.unloaded")
                )
                self._skland_card.set_status(t("account.scan.service_unloaded"))
                self._skland_card.set_fields(
                    [
                        _field_state(t("common.placeholder")),
                        _field_remaining(t("common.placeholder")),
                    ]
                )
                self._bili_card.show_placeholder(
                    t("account.scan.eyebrow_off"), t("account.status.unloaded")
                )
                self._bili_card.set_status(t("account.scan.service_unloaded"))
                self._bili_card.set_fields(
                    [
                        _field_state(t("common.placeholder")),
                        _field_remaining(t("common.placeholder")),
                    ]
                )

        self._provider_badge.set_text(_provider_badge_text(provider, demo))
        self._provider_badge.set_signal(bool(demo))
        self._provider_badge.setVisible(online or demo)

        # B 服只完成 B 站身份：保持扫码布局，让用户能继续扫森空岛拿终末地数据
        show_data = demo or (online and provider == PROVIDER_SKLAND)
        self._online_host.setVisible(show_data)
        self._offline_host.setVisible(not show_data)
        if missing:
            self._profile_missing.show()
            self._profile_empty.hide()
        elif not show_data:
            self._profile_missing.hide()
            self._profile_empty.show()
        else:
            self._profile_missing.hide()
            self._profile_empty.hide()

        if not show_data:
            self._was_online = False
            self._players = []
            self._operators = []
            self._domains = []
            self._reset_player_data()
            if online:
                self._sync_cards_online(provider, demo)
            else:
                self._sync_cards_offline()
            return

        self._players = roles
        self._operators = operators
        self._domains = list(player.get("domains") or []) if player else []
        self._apply_player(player, state, demo, provider, data_error)
        self._apply_stamina(player)
        self._apply_scale(player)
        self._rebuild_operators()
        self._rebuild_domains()
        self._apply_progress(player, provider, demo)
        self._bind_role_selector(roles, state.get("role_index"), provider, demo)
        self._sync_cards_online(provider, demo)
        self._was_online = not demo

    # -- 在线数据渲染 -------------------------------------------------------
    def _apply_player(
        self,
        player: dict[str, Any] | None,
        state: dict[str, Any],
        demo: bool,
        provider: str,
        data_error: Any,
    ) -> None:
        nickname = str(
            (player.get("nickname") if player else None)
            or state.get("nickname")
            or t("common.placeholder")
        )
        self._player_name.setText(nickname)
        uid_masked = str(
            (player.get("uid_masked") if player else None)
            or state.get("uid_masked")
            or t("common.placeholder")
        )
        server = player.get("server_name") if player else None
        source = t("account.status.local_demo") if demo else _provider_short(provider)
        self._player_fields.set_fields(
            [
                t("account.profile.field_uid", uid=uid_masked),
                t("account.profile.field_server", server=_text_or_dash(server)),
                _field_source(source),
            ]
        )
        self._player_sync.setText(
            t("account.profile.sync", time=format_sync(state.get("last_sync")))
        )

        avatar_path = state.get("avatar_path")
        shown = False
        if avatar_path:
            shown = self._player_avatar.set_image(str(avatar_path))
        if not shown:
            self._player_avatar.set_pixmap(None)
            self._player_avatar.set_initials(nickname[:1] or t("common.placeholder"))

        self._stat_player_level.set_value(_text_or_dash(player.get("level") if player else None))
        self._stat_world_level.set_value(
            _text_or_dash(player.get("world_level") if player else None)
        )
        self._stat_exp.set_value(_text_or_dash(player.get("exp") if player else None))
        self._player_meta.set_fields(
            [
                t(
                    "account.profile.field_last_login",
                    time=format_timestamp(player.get("last_login_time") if player else None),
                ),
                t(
                    "account.profile.field_create_time",
                    time=format_timestamp(player.get("create_time") if player else None),
                ),
            ]
        )
        mission = player.get("main_mission") if player else None
        if mission:
            self._player_mission.setText(
                t("account.profile.mission", text=mission)
            )
            self._player_mission.show()
        else:
            self._player_mission.hide()

        note = ""
        if not player:
            note = t("account.data.partial")
            if data_error:
                note = f"{note} {data_error}"
        elif data_error:
            note = str(data_error)
        if note:
            self._player_note.setText(note)
            self._player_note.show()
        else:
            self._player_note.hide()

        if not player and self._players:
            self._rebuild_binding_roles(self._players)
            self._binding_fallback.show()
        else:
            self._clear_binding_roles()
            self._binding_fallback.hide()

    def _apply_stamina(self, player: dict[str, Any] | None) -> None:
        stamina = (player.get("stamina") if player else None) or {}
        cur = stamina.get("cur")
        maximum = stamina.get("max")
        full_ts = stamina.get("full_ts")
        self._stamina_value.set_value(_text_or_dash(cur))
        self._stamina_cap.setText(t("account.stamina.cap", value=_text_or_dash(maximum)))
        self._stamina_progress.set_ratio(_ratio(cur, maximum))
        try:
            self._stamina_full_ts = int(full_ts) if full_ts is not None else None
        except (TypeError, ValueError):
            self._stamina_full_ts = None
        try:
            full = cur is not None and maximum is not None and int(cur) >= int(maximum)
        except (TypeError, ValueError):
            full = False
        self._stamina_badge.set_text(
            t("account.stamina.badge_full" if full else "account.stamina.badge_normal")
        )
        self._stamina_badge.set_signal(bool(full))
        if self._stamina_full_ts and not full:
            self._stamina_timer.start()
        else:
            self._stamina_timer.stop()
        self._stamina_hint.setVisible(not player)
        if not player:
            self._stamina_hint.setText(t("account.stamina.no_data"))
        self._on_stamina_tick()

    def _on_stamina_tick(self) -> None:
        if not hasattr(self, "_stamina_countdown"):
            return
        if not self._stamina_full_ts:
            self._stamina_countdown.setText(
                t("account.stamina.full_in", time=t("common.placeholder"))
            )
            self._stamina_timer.stop()
            return
        remaining = self._stamina_full_ts - int(time.time())
        if remaining <= 0:
            self._stamina_countdown.setText(t("account.stamina.full_now"))
            self._stamina_timer.stop()
            return
        self._stamina_countdown.setText(t("account.stamina.full_in", time=_countdown(remaining)))

    def _apply_scale(self, player: dict[str, Any] | None) -> None:
        self._stat_operators.set_value(
            _text_or_dash(player.get("char_num") if player else None)
        )
        self._stat_weapons.set_value(
            _text_or_dash(player.get("weapon_num") if player else None)
        )
        self._stat_docs.set_value(_text_or_dash(player.get("doc_num") if player else None))
        self._scale_note.setVisible(not player)
        if not player:
            self._scale_note.setText(t("account.scale.no_data"))

    def _rebuild_operators(self) -> None:
        self._clear_layout(self._operator_rows)
        items = [op for op in self._operators if self._operator_matches(op)]
        items.sort(key=self._operator_sort_key)
        for operator in items:
            self._operator_rows.addWidget(_OperatorRow(operator, self._operator_host))
        self._operator_header.setVisible(bool(self._operators))
        self._operator_badge.set_text(
            t("account.operators.count", count=len(self._operators))
        )
        if self._operators:
            self._operator_note.hide()
        else:
            self._operator_note.setText(t("account.operators.empty"))
            self._operator_note.show()

    def _operator_matches(self, operator: dict[str, Any]) -> bool:
        index = self._operator_filter.index
        rarity = operator.get("rarity")
        try:
            rarity = int(rarity) if rarity is not None else None
        except (TypeError, ValueError):
            rarity = None
        if index == 1:
            return rarity == 6
        if index == 2:
            return rarity == 5
        if index == 3:
            return rarity is not None and rarity <= 4
        return True

    def _operator_sort_key(self, operator: dict[str, Any]):
        def number(key: str) -> int:
            try:
                value = operator.get(key)
                return int(value) if value is not None else 0
            except (TypeError, ValueError):
                return 0

        index = self._operator_sort.index
        name = str(operator.get("name") or "")
        if index == 1:
            return (-number("rarity"), -number("level"), name)
        if index == 2:
            return (-number("potential_level"), -number("rarity"), name)
        return (-number("level"), -number("rarity"), name)

    def _rebuild_domains(self) -> None:
        self._clear_layout(self._domain_rows)
        for domain in self._domains:
            self._domain_rows.addWidget(_DomainRow(domain, self._domain_host))
        if self._domains:
            self._domain_note.hide()
        else:
            self._domain_note.setText(t("account.exploration.empty"))
            self._domain_note.show()

    def _apply_progress(self, player: dict[str, Any] | None, provider: str, demo: bool) -> None:
        bp = (player.get("bp") if player else None) or {}
        daily = (player.get("daily") if player else None) or {}
        self._bp_label.setText(
            t(
                "account.progress.bp",
                cur=_text_or_dash(bp.get("cur")),
                max=_text_or_dash(bp.get("max")),
            )
        )
        self._bp_bar.set_ratio(_ratio(bp.get("cur"), bp.get("max")))
        self._daily_label.setText(
            t(
                "account.progress.daily",
                cur=_text_or_dash(daily.get("cur")),
                max=_text_or_dash(daily.get("max")),
            )
        )
        self._daily_bar.set_ratio(_ratio(daily.get("cur"), daily.get("max")))
        self._achieve_label.setText(
            t(
                "account.progress.achieve",
                count=_text_or_dash(player.get("achieve_count") if player else None),
            )
        )
        can_sign = provider == PROVIDER_SKLAND and not demo and player is not None
        self._sign_btn.setEnabled(can_sign)
        self._sign_btn.setVisible(provider == PROVIDER_SKLAND)
        self._sign_label.setText(
            t("account.sign.demo_disabled")
            if demo
            else (t("account.sign.no_data") if not can_sign else t("account.sign.hint"))
        )

    def _bind_role_selector(
        self,
        roles: list[dict[str, Any]],
        role_index: Any,
        provider: str,
        demo: bool,
    ) -> None:
        show = provider == PROVIDER_SKLAND and len(roles) > 1
        self._role_selector.setVisible(show)
        self._role_label.setVisible(show)
        if not show:
            return
        names = [str(role.get("name") or t("common.placeholder")) for role in roles]
        if getattr(self, "_role_names", None) != names:
            self._role_names = names
            self._role_selector.set_items(names)
        try:
            index = int(role_index or 0)
        except (TypeError, ValueError):
            index = 0
        self._role_selector.set_index(index, emit=False)
        self._role_selector.setEnabled(not demo)

    def _reset_player_data(self) -> None:
        self._player_name.setText(t("common.placeholder"))
        self._player_fields.set_fields(
            [
                t("account.profile.field_uid", uid=t("common.placeholder")),
                t("account.profile.field_server", server=t("common.placeholder")),
                _field_source(t("common.placeholder")),
            ]
        )
        self._player_sync.setText(t("account.profile.sync", time=format_sync(0)))
        self._player_note.hide()
        self._player_mission.hide()
        self._player_avatar.set_pixmap(None)
        self._player_avatar.set_initials(t("common.placeholder"))
        for stat in (self._stat_player_level, self._stat_world_level, self._stat_exp):
            stat.set_value(t("common.placeholder"))
        self._stat_operators.set_value(t("common.placeholder"))
        self._stat_weapons.set_value(t("common.placeholder"))
        self._stat_docs.set_value(t("common.placeholder"))
        self._stamina_value.set_value(t("common.placeholder"))
        self._stamina_cap.setText(t("account.stamina.cap", value=t("common.placeholder")))
        self._stamina_progress.set_ratio(0.0)
        self._stamina_full_ts = None
        self._stamina_timer.stop()
        self._stamina_countdown.setText(
            t("account.stamina.full_in", time=t("common.placeholder"))
        )
        self._stamina_hint.hide()
        self._bp_bar.set_ratio(0.0)
        self._daily_bar.set_ratio(0.0)
        self._bp_label.setText(
            t("account.progress.bp", cur=t("common.placeholder"), max=t("common.placeholder"))
        )
        self._daily_label.setText(
            t("account.progress.daily", cur=t("common.placeholder"), max=t("common.placeholder"))
        )
        self._achieve_label.setText(
            t("account.progress.achieve", count=t("common.placeholder"))
        )
        self._role_selector.hide()
        self._role_label.hide()
        self._binding_fallback.hide()
        self._clear_binding_roles()
        self._clear_layout(self._operator_rows)
        self._clear_layout(self._domain_rows)
        self._operator_note.hide()
        self._domain_note.hide()
        self._scale_note.hide()
        self._sign_btn.setEnabled(False)
        self._sign_label.setText(t("account.sign.hint"))

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _sync_cards_offline(self) -> None:
        if self._was_online:
            if not self._skland_active():
                self._skland_card.reset_idle()
            if not self._bili_active():
                self._bili_card.reset_idle()

    def _sync_cards_online(self, provider: str, demo: bool) -> None:
        if demo:
            return
        if provider == PROVIDER_SKLAND and not self._skland_active():
            self._skland_card.show_linked(t("account.scan.linked_desc"))
        elif provider == PROVIDER_BILIBILI and not self._bili_active():
            self._bili_card.show_linked(t("account.scan.linked_bili"))
            if not self._skland_active():
                self._skland_card.set_status(t("account.scan.need_skland"))

    def _skland_active(self) -> bool:
        return bool(
            self._skland_scan_id
            or self._skland_generating
            or self._skland_confirming
            or self._skland_auto_pending
        )

    def _bili_active(self) -> bool:
        return bool(self._bili_key or self._bili_generating or self._bili_confirming)

    # -- 绑定角色降级列表（完整数据失败时）----------------------------------
    def _clear_binding_roles(self) -> None:
        while self._binding_rows.count():
            item = self._binding_rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild_binding_roles(self, roles: list[dict[str, Any]]) -> None:
        self._clear_binding_roles()
        for index, role in enumerate(roles, start=1):
            self._binding_rows.addWidget(_RoleRow(index, role, self._binding_host))
        self._binding_note.setVisible(not roles)
        self._binding_note.setText(t("account.profile.roles_note"))

    # -- 刷新 ---------------------------------------------------------------
    def _on_refresh_clicked(self) -> None:
        self._begin_refresh(notify=True)

    def _on_role_changed(self, index: int) -> None:
        service = self.ctx.services.account
        if service is None or bool((self._state or {}).get("demo")):
            return
        current = int((self._state or {}).get("role_index") or 0)
        if index == current:
            return
        self._role_selector.setEnabled(False)

        def done(_state) -> None:
            self._role_selector.setEnabled(True)
            self.refresh()

        def failed(exc: Exception) -> None:
            self._role_selector.setEnabled(True)
            self.ctx.notify(self._human(exc), "warn")
            self.refresh()

        service.select_role(index, on_done=done, on_error=failed)

    def _on_sign_clicked(self) -> None:
        service = self.ctx.services.account
        if service is None:
            self.ctx.notify(t("account.notify.sign_unavailable"), "warn")
            return
        if not self._sign_btn.isEnabled():
            return
        self._sign_btn.setEnabled(False)
        self._sign_label.setText(t("account.sign.running"))

        def done(payload: dict[str, Any]) -> None:
            self._sign_btn.setEnabled(True)
            awards = int(payload.get("awards") or 0)
            self._sign_label.setText(t("account.sign.done", count=awards))
            self.ctx.notify(t("account.notify.sign_ok", count=awards), "ok")

        def failed(exc: Exception) -> None:
            self._sign_btn.setEnabled(True)
            message = self._human(exc)
            self._sign_label.setText(t("account.sign.failed", error=message))
            self.ctx.notify(t("account.notify.sign_failed", error=message), "warn")

        service.skland_sign_attendance(on_done=done, on_error=failed)

    def _begin_refresh(self, *, notify: bool) -> None:
        service = self.ctx.services.account
        if service is None:
            self.ctx.notify(t("account.notify.refresh_unavailable"), "warn")
            return
        if service.is_busy():
            return
        self._busy.start()
        self._refresh_btn.setEnabled(False)

        def done(_state) -> None:
            self._busy.stop()
            self._refresh_btn.setEnabled(True)
            self.refresh()
            if notify:
                self.ctx.notify(t("account.notify.refreshed"), "ok")

        def failed(exc: Exception) -> None:
            self._busy.stop()
            self._refresh_btn.setEnabled(True)
            self.refresh()
            self.ctx.notify(self._human(exc), "warn")

        service.refresh(on_done=done, on_error=failed)

    # -- 森空岛扫码 ---------------------------------------------------------
    def _start_skland_qr(self) -> None:
        self._begin_skland_qr(auto=False)

    def _start_skland_qr_auto(self) -> None:
        if not self._skland_auto_pending:
            return
        self._skland_auto_pending = False
        self._begin_skland_qr(auto=True)

    def _begin_skland_qr(self, *, auto: bool) -> None:
        service = self.ctx.services.account
        if service is None:
            self.ctx.notify(t("account.notify.scan_unavailable"), "warn")
            return
        if self._skland_generating or self._skland_confirming:
            return
        if not auto:
            self._skland_auto_refresh = 0
        self._skland_poller.stop()
        self._skland_auto_timer.stop()
        self._skland_auto_pending = False
        self._skland_scan_id = None
        self._skland_polling = False
        self._skland_errors = 0
        self._skland_generating = True
        card = self._skland_card
        card.clear_warn()
        card.set_status(t("account.scan.requesting"))
        card.set_fields(
            [
                _field_state(t("account.scan.state_requesting")),
                _field_remaining(t("common.placeholder")),
            ]
        )
        card.set_primary(t("account.scan.button_generating"), "qr")
        card.set_cancel_visible(False)
        card.primary.setEnabled(False)
        self._busy.start()

        def done(qr) -> None:
            self._busy.stop()
            self._skland_generating = False
            card.primary.setEnabled(self.ctx.services.account is not None)
            try:
                matrix = qr_mod.make_matrix(qr.scan_url)
            except AccountError as exc:
                self._skland_failed(exc)
                return
            self._skland_scan_id = qr.scan_id
            self._skland_started = time.monotonic()
            self._skland_state_text = t("account.scan.state_waiting")
            self._skland_errors = 0
            card.show_matrix(matrix)
            card.set_status(t("account.scan.waiting"))
            card.set_fields(
                [
                    _field_state(t("account.scan.state_waiting")),
                    _field_remaining(_mmss(skland.SCAN_TTL_SECONDS)),
                ]
            )
            card.set_cancel_visible(True)
            card.set_primary(t("account.scan.button_refresh"), "refresh")
            card.primary.setEnabled(True)
            self._skland_poller.start()
            self._skland_tick()

        def failed(exc: Exception) -> None:
            self._busy.stop()
            self._skland_generating = False
            card.primary.setEnabled(self.ctx.services.account is not None)
            self._skland_failed(exc)

        service.skland_scan_start(on_done=done, on_error=failed)

    def _skland_failed(self, exc: Exception) -> None:
        message = self._human(exc)
        card = self._skland_card
        card.show_placeholder(
            t("account.scan.eyebrow_error"), t("account.scan.gen_failed_title")
        )
        card.set_status(t("account.scan.gen_failed_status"))
        card.set_fields(
            [
                _field_state(t("account.scan.state_failed")),
                _field_remaining(t("common.placeholder")),
            ]
        )
        card.set_primary(t("common.retry"), "refresh")
        card.set_cancel_visible(False)
        card.warn(message)
        self._ctx_notify(message, "warn")

    def _skland_tick(self) -> None:
        service = self.ctx.services.account
        if service is None or self._skland_scan_id is None:
            self._skland_poller.stop()
            return
        remaining = max(0, skland.SCAN_TTL_SECONDS - int(time.monotonic() - self._skland_started))
        self._skland_card.set_fields(
            [
                _field_state(self._skland_state_text),
                _field_remaining(_mmss(remaining)),
            ]
        )
        if remaining <= 0:
            self._expire_skland_qr()
            return
        if self._skland_polling:
            return
        self._skland_polling = True
        scan_id = self._skland_scan_id

        def done(result) -> None:
            self._skland_polling = False
            self._on_skland_status(result)

        def failed(exc: Exception) -> None:
            self._skland_polling = False
            self._on_skland_poll_error(exc)

        service.skland_scan_poll(scan_id, on_done=done, on_error=failed)

    def _on_skland_status(self, result) -> None:
        self._skland_errors = 0
        card = self._skland_card
        st = getattr(result, "status", skland.SCAN_FAILED)
        if st == skland.SCAN_WAITING:
            self._skland_state_text = t("account.scan.state_waiting")
            card.set_status(t("account.scan.waiting"))
            card.set_fields(
                [
                    _field_state(t("account.scan.state_waiting")),
                    _field_remaining(
                        _mmss(
                            skland.SCAN_TTL_SECONDS
                            - int(time.monotonic() - self._skland_started)
                        )
                    ),
                ]
            )
        elif st == skland.SCAN_SCANNED:
            self._skland_state_text = t("account.scan.state_scanned")
            card.set_status(t("account.scan.scanned_skland"))
            card.set_fields(
                [
                    _field_state(t("account.scan.state_scanned")),
                    _field_remaining(
                        _mmss(
                            skland.SCAN_TTL_SECONDS
                            - int(time.monotonic() - self._skland_started)
                        )
                    ),
                ]
            )
        elif st == skland.SCAN_EXPIRED:
            self._expire_skland_qr()
        elif st == skland.SCAN_CANCELED:
            self._finish_skland_qr(
                t("account.scan.eyebrow_canceled"), t("account.scan.canceled")
            )
            card.set_primary(t("account.scan.button_refresh"), "refresh")
        elif st == skland.SCAN_CONFIRMED:
            self._confirm_skland_login(result.scan_code)
        else:
            self._expire_skland_qr()

    def _on_skland_poll_error(self, exc: Exception) -> None:
        self._skland_errors += 1
        card = self._skland_card
        if self._skland_errors >= MAX_POLL_ERRORS:
            self._finish_skland_qr(
                t("account.scan.eyebrow_error"), t("account.scan.poll_failed")
            )
            card.set_primary(t("account.scan.button_refresh"), "refresh")
            card.warn(self._human(exc))
            self._ctx_notify(self._human(exc), "warn")
            return
        card.set_status(
            t(
                "account.scan.retrying",
                count=self._skland_errors,
                max=MAX_POLL_ERRORS,
            )
        )

    def _confirm_skland_login(self, scan_code: str) -> None:
        service = self.ctx.services.account
        if service is None:
            return
        self._skland_poller.stop()
        self._skland_scan_id = None
        self._skland_polling = False
        self._skland_confirming = True
        card = self._skland_card
        card.set_cancel_visible(False)
        card.set_status(t("account.scan.confirming"))
        card.set_fields(
            [
                _field_state(t("account.scan.state_confirming")),
                _field_remaining(t("common.placeholder")),
            ]
        )
        card.set_primary(t("account.scan.button_working"), "refresh")
        card.primary.setEnabled(False)
        self._busy.start()

        def done(_state) -> None:
            self._busy.stop()
            self._skland_confirming = False
            card.primary.setEnabled(True)
            card.show_linked(t("account.scan.linked_desc"))
            self.refresh()
            self.ctx.notify(t("account.notify.skland_ok"), "ok")

        def failed(exc: Exception) -> None:
            self._busy.stop()
            self._skland_confirming = False
            card.primary.setEnabled(True)
            card.show_placeholder(
                t("account.scan.eyebrow_error"),
                t("account.scan.credential_failed_title"),
            )
            card.set_status(t("account.scan.credential_failed_status"))
            card.set_fields(
                [
                    _field_state(t("account.scan.state_failed")),
                    _field_remaining(t("common.placeholder")),
                ]
            )
            card.set_primary(t("account.scan.button_rescan"), "refresh")
            card.warn(self._human(exc))
            self.refresh()
            self._ctx_notify(self._human(exc), "warn")

        service.skland_scan_confirm(scan_code, on_done=done, on_error=failed)

    def _expire_skland_qr(self) -> None:
        self._skland_poller.stop()
        self._skland_scan_id = None
        self._skland_polling = False
        self._skland_errors = 0
        card = self._skland_card
        card.show_placeholder(
            t("account.scan.eyebrow_expired"), t("account.scan.expired")
        )
        card.set_cancel_visible(False)
        if self._skland_auto_refresh < MAX_AUTO_REFRESH:
            self._skland_auto_refresh += 1
            self._skland_auto_pending = True
            card.set_status(
                t(
                    "account.scan.expired_auto",
                    count=self._skland_auto_refresh,
                    max=MAX_AUTO_REFRESH,
                )
            )
            card.set_fields(
                [
                    _field_state(t("account.scan.state_expired")),
                    _field_remaining(t("common.placeholder")),
                ]
            )
            self._skland_auto_timer.start(600)
        else:
            card.set_status(t("account.scan.expired_manual"))
            card.set_fields(
                [
                    _field_state(t("account.scan.state_expired")),
                    _field_remaining(t("common.placeholder")),
                ]
            )
            card.set_primary(t("account.scan.button_refresh"), "refresh")

    def _finish_skland_qr(self, eyebrow: str, message: str) -> None:
        self._skland_poller.stop()
        self._skland_auto_timer.stop()
        self._skland_auto_pending = False
        self._skland_scan_id = None
        self._skland_polling = False
        self._skland_errors = 0
        card = self._skland_card
        card.show_placeholder(eyebrow, message)
        card.set_status(message)
        card.set_fields(
            [
                _field_state(t("account.scan.state_finished")),
                _field_remaining(t("common.placeholder")),
            ]
        )
        card.set_cancel_visible(False)
        card.set_primary(t("account.scan.button_generate"), "qr")
        card.primary.setEnabled(self.ctx.services.account is not None)

    def _cancel_skland_qr(self) -> None:
        self._finish_skland_qr(
            t("account.scan.eyebrow_canceled"), t("account.scan.canceled_desc")
        )
        self._skland_card.set_primary(t("account.scan.button_generate"), "qr")

    def _resume_skland_qr(self) -> None:
        if self._skland_scan_id is None or self._skland_confirming:
            return
        remaining = skland.SCAN_TTL_SECONDS - int(time.monotonic() - self._skland_started)
        if remaining <= 0:
            self._expire_skland_qr()
            return
        self._skland_card.set_status(t("account.scan.resume_waiting"))
        self._skland_poller.start()
        self._skland_tick()

    # -- B 服扫码 -----------------------------------------------------------
    def _start_bili_qr(self) -> None:
        service = self.ctx.services.account
        if service is None:
            self.ctx.notify(t("account.notify.scan_unavailable"), "warn")
            return
        if self._bili_generating or self._bili_confirming:
            return
        self._bili_poller.stop()
        self._bili_key = None
        self._bili_polling = False
        self._bili_errors = 0
        self._bili_generating = True
        card = self._bili_card
        card.clear_warn()
        card.set_status(t("account.scan.requesting"))
        card.set_fields(
            [
                _field_state(t("account.scan.state_requesting")),
                _field_remaining(t("common.placeholder")),
            ]
        )
        card.set_primary(t("account.scan.button_generating"), "qr")
        card.set_cancel_visible(False)
        card.primary.setEnabled(False)
        self._busy.start()

        def done(qr) -> None:
            self._busy.stop()
            self._bili_generating = False
            card.primary.setEnabled(self.ctx.services.account is not None)
            try:
                matrix = qr_mod.make_matrix(qr.url)
            except AccountError as exc:
                self._bili_failed(exc)
                return
            self._bili_key = qr.qrcode_key
            self._bili_started = time.monotonic()
            self._bili_state_text = t("account.scan.state_waiting")
            self._bili_errors = 0
            card.show_matrix(matrix)
            card.set_status(t("account.scan.waiting"))
            card.set_fields(
                [
                    _field_state(t("account.scan.state_waiting")),
                    _field_remaining(_mmss(bili.QR_TTL_SECONDS)),
                ]
            )
            card.set_cancel_visible(True)
            card.set_primary(t("account.scan.button_refresh"), "refresh")
            card.primary.setEnabled(True)
            self._bili_poller.start()
            self._bili_tick()

        def failed(exc: Exception) -> None:
            self._busy.stop()
            self._bili_generating = False
            card.primary.setEnabled(self.ctx.services.account is not None)
            self._bili_failed(exc)

        service.bili_generate(on_done=done, on_error=failed)

    def _bili_failed(self, exc: Exception) -> None:
        message = self._human(exc)
        card = self._bili_card
        card.show_placeholder(
            t("account.scan.eyebrow_error"), t("account.scan.gen_failed_title")
        )
        card.set_status(t("account.scan.gen_failed_status"))
        card.set_fields(
            [
                _field_state(t("account.scan.state_failed")),
                _field_remaining(t("common.placeholder")),
            ]
        )
        card.set_primary(t("common.retry"), "refresh")
        card.set_cancel_visible(False)
        card.warn(message)
        self._ctx_notify(message, "warn")

    def _bili_tick(self) -> None:
        service = self.ctx.services.account
        if service is None or self._bili_key is None:
            self._bili_poller.stop()
            return
        remaining = max(0, bili.QR_TTL_SECONDS - int(time.monotonic() - self._bili_started))
        self._bili_card.set_fields(
            [
                _field_state(self._bili_state_text),
                _field_remaining(_mmss(remaining)),
            ]
        )
        if remaining <= 0:
            self._finish_bili_qr(
                t("account.scan.eyebrow_expired"), t("account.scan.expired_refresh")
            )
            self._bili_card.set_primary(t("account.scan.button_refresh"), "refresh")
            return
        if self._bili_polling:
            return
        self._bili_polling = True
        key = self._bili_key

        def done(result) -> None:
            self._bili_polling = False
            self._on_bili_status(result)

        def failed(exc: Exception) -> None:
            self._bili_polling = False
            self._on_bili_poll_error(exc)

        service.bili_poll(key, on_done=done, on_error=failed)

    def _on_bili_status(self, result) -> None:
        self._bili_errors = 0
        card = self._bili_card
        status = getattr(result, "status", bili.QR_FAILED)
        if status == bili.QR_WAITING:
            self._bili_state_text = t("account.scan.state_waiting")
            card.set_status(t("account.scan.waiting"))
        elif status == bili.QR_SCANNED:
            self._bili_state_text = t("account.scan.state_scanned")
            card.set_status(t("account.scan.scanned_bili"))
        elif status == bili.QR_EXPIRED:
            self._bili_state_text = t("account.scan.state_expired")
            self._finish_bili_qr(
                t("account.scan.eyebrow_expired"), t("account.scan.expired_refresh")
            )
            card.set_primary(t("account.scan.button_refresh"), "refresh")
        elif status == bili.QR_OK:
            self._bili_state_text = t("account.scan.state_login_success")
            self._bili_poller.stop()
            self._bili_key = None
            self._bili_polling = False
            card.show_placeholder(
                t("account.scan.eyebrow_wait"), t("account.scan.reading_bili_desc")
            )
            card.set_status(t("account.scan.reading_bili"))
            card.set_fields(
                [
                    _field_state(t("account.scan.state_login_success")),
                    _field_remaining(t("common.placeholder")),
                ]
            )
            card.set_cancel_visible(False)
            card.set_primary(t("account.scan.button_working"), "refresh")
            card.primary.setEnabled(False)
            self._confirm_bili_login(result.cookies)
        else:
            message = getattr(result, "message", "") or t("account.scan.login_failed")
            self._finish_bili_qr(t("account.scan.eyebrow_error"), message)
            card.set_primary(t("account.scan.button_refresh"), "refresh")

    def _on_bili_poll_error(self, exc: Exception) -> None:
        self._bili_errors += 1
        card = self._bili_card
        if self._bili_errors >= MAX_POLL_ERRORS:
            self._finish_bili_qr(
                t("account.scan.eyebrow_error"), t("account.scan.poll_failed")
            )
            card.set_primary(t("account.scan.button_refresh"), "refresh")
            card.warn(self._human(exc))
            self._ctx_notify(self._human(exc), "warn")
            return
        card.set_status(
            t(
                "account.scan.retrying",
                count=self._bili_errors,
                max=MAX_POLL_ERRORS,
            )
        )

    def _confirm_bili_login(self, cookies: dict[str, str]) -> None:
        service = self.ctx.services.account
        if service is None:
            return
        self._bili_confirming = True
        self._busy.start()

        def done(_state) -> None:
            self._busy.stop()
            self._bili_confirming = False
            card = self._bili_card
            card.primary.setEnabled(True)
            card.show_linked(t("account.scan.linked_bili"))
            self.refresh()
            self.ctx.notify(t("account.notify.bili_ok"), "ok")

        def failed(exc: Exception) -> None:
            self._busy.stop()
            self._bili_confirming = False
            card = self._bili_card
            card.primary.setEnabled(True)
            card.show_placeholder(
                t("account.scan.eyebrow_error"),
                t("account.scan.bili_profile_failed_title"),
            )
            card.set_status(t("account.scan.bili_profile_failed_status"))
            card.set_fields(
                [
                    _field_state(t("account.scan.state_failed")),
                    _field_remaining(t("common.placeholder")),
                ]
            )
            card.set_primary(t("account.scan.button_rescan"), "refresh")
            card.warn(self._human(exc))
            self.refresh()
            self._ctx_notify(self._human(exc), "warn")

        service.commit_bili_login(cookies, on_done=done, on_error=failed)

    def _finish_bili_qr(self, eyebrow: str, message: str) -> None:
        self._bili_poller.stop()
        self._bili_key = None
        self._bili_polling = False
        self._bili_errors = 0
        card = self._bili_card
        card.show_placeholder(eyebrow, message)
        card.set_status(message)
        card.set_fields(
            [
                _field_state(t("account.scan.state_finished")),
                _field_remaining(t("common.placeholder")),
            ]
        )
        card.set_cancel_visible(False)

    def _cancel_bili_qr(self) -> None:
        self._finish_bili_qr(
            t("account.scan.eyebrow_canceled"), t("account.scan.canceled_desc")
        )
        self._bili_card.set_primary(t("account.scan.button_generate"), "qr")

    def _resume_bili_qr(self) -> None:
        if self._bili_key is None or self._bili_confirming:
            return
        remaining = bili.QR_TTL_SECONDS - int(time.monotonic() - self._bili_started)
        if remaining <= 0:
            self._finish_bili_qr(
                t("account.scan.eyebrow_expired"), t("account.scan.expired_refresh")
            )
            self._bili_card.set_primary(t("account.scan.button_refresh"), "refresh")
            return
        self._bili_card.set_status(t("account.scan.resume_waiting"))
        self._bili_poller.start()
        self._bili_tick()

    # -- 演示 / 退出 --------------------------------------------------------
    def _on_demo_toggled(self) -> None:
        service = self.ctx.services.account
        if service is None:
            return
        enabled = bool(self._demo_toggle.isChecked())
        self._cancel_skland_qr()
        self._cancel_bili_qr()
        service.set_demo_mode(enabled)
        self.refresh()
        self.ctx.notify(
            t("account.notify.demo_on" if enabled else "account.notify.demo_off"),
            "warn" if enabled else "ok",
        )

    def _on_logout(self) -> None:
        service = self.ctx.services.account
        if service is None:
            return
        if not confirm(
            self,
            title=t("account.confirm.logout_title"),
            message=t("account.confirm.logout_message"),
            action_text=t("account.confirm.logout_action"),
            danger=True,
        ):
            return
        self._cancel_skland_qr()
        self._cancel_bili_qr()
        service.logout()
        self.refresh()
        self.ctx.notify(t("account.notify.logged_out"), "ok")

    # -- 事件 / 工具 --------------------------------------------------------
    def _on_account_updated(self, snapshot=None, **_payload) -> None:
        if isinstance(snapshot, dict):
            self._apply_service_state(self.ctx.services.account, snapshot)
        else:
            self.refresh()

    def _on_account_state(self, **_payload) -> None:
        service = self.ctx.services.account
        if service is not None:
            self._apply_service_state(service, service.snapshot())

    @staticmethod
    def _set_flag_text(flag: InfoFlag, text: str, limit: int = 90) -> None:
        display = text if len(text) <= limit else text[: limit - 1] + "…"
        flag.set_text(display)
        flag.setToolTip(text)

    @staticmethod
    def _human(exc: Exception) -> str:
        if isinstance(exc, AccountError):
            return exc.display()
        return f"{exc}"

    def _ctx_notify(self, text: str, kind: str) -> None:
        try:
            self.ctx.notify(text, kind)
        except Exception:  # noqa: BLE001
            pass


def _has_credentials(service) -> bool:
    try:
        return bool(service.has_credentials())
    except Exception:  # noqa: BLE001
        return False
