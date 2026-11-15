# -*- coding: utf-8 -*-
"""顶栏：模块标题 + 全局设置 + 最小化 / 最大化 / 关闭。"""
from __future__ import annotations

import os

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QPainter
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget

from endbox.app_context import AppContext
from endbox.core.text import t
from endbox.modules.update.badge import UpdateHint
from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.theme.tokens import theme
from endbox.ui.kit import FieldText, Hairline, IconButton
from endbox.ui.kit.base import EWidget, pal

TOPBAR_H = 54


class TopBar(EWidget):
    """窗口顶栏（无边框窗口的拖动区）。"""

    settings_clicked = pyqtSignal()
    theme_toggled = pyqtSignal()

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setFixedHeight(TOPBAR_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._eyebrow = t("shell.topbar.default_eyebrow", "ENDBOX")
        self._title = t("shell.topbar.default_title", "终末地工具箱")
        self._icon = "grid"
        self._fields: list[str] = []
        self._actions: list[QWidget] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 0, 10, 0)
        layout.setSpacing(10)

        self._title_host = _TitleBlock(self)
        layout.addWidget(self._title_host, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)

        self.fields = FieldText([], self)
        layout.addWidget(self.fields, 0, Qt.AlignmentFlag.AlignVCenter)

        # 低干扰更新提示：有更新且未忽略时才出现；截图/测试环境关闭自动检查
        delay = -1 if os.environ.get("ENDBOX_NO_UPDATE_CHECK") else 4000
        self.update_hint = UpdateHint(self.ctx, self, delay_ms=delay)
        layout.addWidget(self.update_hint, 0, Qt.AlignmentFlag.AlignVCenter)

        self._actions_host = QWidget(self)
        self._actions_layout = QHBoxLayout(self._actions_host)
        self._actions_layout.setContentsMargins(0, 0, 0, 0)
        self._actions_layout.setSpacing(6)
        layout.addWidget(self._actions_host, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(Hairline(self, horizontal=False), 0)
        layout.addSpacing(4)

        self.theme_btn = IconButton("sun", t("shell.topbar.theme_tooltip", "切换深浅主题"), self, size=30, icon_size=15)
        self.theme_btn.clicked.connect(self.theme_toggled.emit)
        layout.addWidget(self.theme_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.settings_btn = IconButton("settings", t("shell.topbar.settings_tooltip", "设置"), self, size=30, icon_size=15)
        self.settings_btn.clicked.connect(self.settings_clicked.emit)
        layout.addWidget(self.settings_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addSpacing(6)

        self.min_btn = IconButton("min", t("shell.topbar.minimize", "最小化"), self, size=30, icon_size=13)
        self.max_btn = IconButton("max", t("shell.topbar.maximize", "最大化"), self, size=30, icon_size=13)
        self.close_btn = IconButton("close", t("shell.topbar.close", "关闭"), self, size=30, icon_size=13)
        for btn in (self.min_btn, self.max_btn, self.close_btn):
            layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)

        theme.changed.connect(lambda _mode: self._sync_theme_button())
        self._sync_theme_button()

    # -- 内容 ---------------------------------------------------------------
    def set_view(self, eyebrow: str, title: str) -> None:
        self._eyebrow = eyebrow or "ENDBOX"
        self._title = title or "终末地工具箱"
        self._title_host.set_text(self._eyebrow, self._title, self._icon)
        self._title_host.update()

    def set_icon(self, icon: str) -> None:
        self._icon = icon
        self._title_host.set_icon(icon)

    def set_fields(self, fields: list[str]) -> None:
        self.fields.set_fields(fields)

    def set_actions(self, widgets: list[QWidget]) -> None:
        for widget in self._actions:
            self._actions_layout.removeWidget(widget)
            widget.setParent(None)
        self._actions = list(widgets)
        for widget in self._actions:
            self._actions_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)

    def _sync_theme_button(self) -> None:
        self.theme_btn.set_icon_name("sun" if theme.mode == "dark" else "moon")

    # -- 无边框窗口拖动 -----------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            handle = win.windowHandle()
            if getattr(win, "_maximized", False):
                # 最大化时拖动顶栏：先还原，再接着拖（和系统窗口一致）
                restore = getattr(win, "restore_for_drag", None)
                if callable(restore):
                    restore(event.globalPosition().toPoint())
                if handle is not None:
                    handle.startSystemMove()
                return
            if handle is not None:
                handle.startSystemMove()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        win = self.window()
        if hasattr(win, "toggle_maximize"):
            win.toggle_maximize()
        super().mouseDoubleClickEvent(event)

    # -- 绘制：透明，底板由主显示面板负责 ------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        colors = pal()
        # 只有一条极淡的细线把标题区和内容区分开
        paint.hairline(p, 18, self.height() - 1, self.width() - 18, self.height() - 1, colors.rule)
        p.end()


class _TitleBlock(EWidget):
    """`// ACCOUNT` 小字 + 中文粗体标题。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._eyebrow = t("shell.topbar.default_eyebrow", "ENDBOX")
        self._title = t("shell.topbar.default_title", "终末地工具箱")
        self._icon = "grid"
        self.setFixedHeight(44)
        self.setMinimumWidth(160)

    def set_text(self, eyebrow: str, title: str, icon: str) -> None:
        self._eyebrow = eyebrow
        self._title = title
        self._icon = icon
        self.update()

    def set_icon(self, icon: str) -> None:
        self._icon = icon
        self.update()

    # -- 拖动 / 双击最大化 ---------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            handle = win.windowHandle()
            if getattr(win, "_maximized", False):
                restore = getattr(win, "restore_for_drag", None)
                if callable(restore):
                    restore(event.globalPosition().toPoint())
                if handle is not None:
                    handle.startSystemMove()
                return
            if handle is not None:
                handle.startSystemMove()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        win = self.window()
        if hasattr(win, "toggle_maximize"):
            win.toggle_maximize()
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        paint.draw_text(
            p, QRectF(0, 0, self.width(), 14), f"// {self._eyebrow.upper()}",
            F.eyebrow_font(10), colors.ink2, elide=True,
        )
        paint.draw_text(
            p, QRectF(0, 14, self.width(), 28), self._title,
            F.font(19, weight=QFont.Weight.Bold, wide=True), colors.ink, elide=True,
        )
        p.end()
