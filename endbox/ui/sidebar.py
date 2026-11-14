# -*- coding: utf-8 -*-
"""左侧边栏：顶部用户中心，下面是功能按钮列表。"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QPainter
from PyQt6.QtWidgets import QAbstractButton, QSizePolicy, QVBoxLayout, QWidget

from endbox import __version__
from endbox.app_context import AppContext
from endbox.core.event_bus import Topics, bus
from endbox.core.text import t
from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.theme.tokens import CUT_SMALL, theme
from endbox.ui.kit import Hairline, NavItem, ScrollArea
from endbox.ui.kit.base import EWidget, pal
from endbox.ui.views.base import ViewRegistry

SIDEBAR_W = 92
SIDEBAR_RADIUS = 12


class AccountTile(QAbstractButton):
    """顶部用户中心：头像 + 登录状态点。点击进入账号中心。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedSize(SIDEBAR_W - 20, 78)
        self.setToolTip(t("shell.sidebar.account_tooltip", "账号中心"))
        self._avatar_path: str | None = None
        self._initials = "—"
        self._online = False
        self._account_name = t("shell.sidebar.not_logged_in", "未登录")

    def set_account(self, nickname: str, *, online: bool, avatar_path: str | None = None) -> None:
        self._account_name = nickname or t("shell.sidebar.not_logged_in", "未登录")
        self._online = online
        self._avatar_path = avatar_path
        self._initials = (self._account_name[:1] if self._account_name else "—")
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        colors = pal()
        size = 44
        x = (self.width() - size) / 2
        rect = QRectF(x, 4, size, size)
        path = paint.cut_rect(rect, 8, "br")
        p.fillPath(path, colors.chip)
        if self._avatar_path:
            from PyQt6.QtGui import QPixmap

            pix = QPixmap(self._avatar_path)
            if not pix.isNull():
                p.save()
                p.setClipPath(path)
                scaled = pix.scaled(
                    int(rect.width()), int(rect.height()),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                p.drawPixmap(rect, scaled, QRectF(scaled.rect()))
                p.restore()
        else:
            paint.draw_text(
                p, rect, self._initials,
                F.font(18, weight=QFont.Weight.Bold, wide=True), colors.ink2,
                Qt.AlignmentFlag.AlignCenter,
            )
        paint.stroke_cut(p, rect.adjusted(0.5, 0.5, -0.5, -0.5), colors.rule, 8, "br", 1.0)
        # 登录状态：实心黄块 = 已连接，虚线框 = 未连接
        dot = QRectF(x + size - 10, 4 + size - 10, 12, 12)
        if self._online:
            p.fillPath(paint.cut_rect(dot, 3, "br"), colors.signal)
        else:
            paint.dashed_rect(p, dot, colors.ink2, 1.0)
        paint.draw_text(
            p, QRectF(0, 56, self.width(), 14), self._account_name,
            F.font(10, weight=QFont.Weight.Medium, cjk_first=True),
            colors.ink if self._online else colors.ink2,
            Qt.AlignmentFlag.AlignCenter, elide=True,
        )
        if self.hasFocus():
            paint.paint_focus(p, QRectF(0.5, 0.5, self.width() - 1, self.height() - 2), colors.trace)
        p.end()


class Sidebar(EWidget):
    """导航栏。按住顶部空白区域可以拖动窗口（由主窗口处理）。"""

    view_selected = pyqtSignal(str)

    def __init__(self, ctx: AppContext, registry: ViewRegistry, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.registry = registry
        self.setFixedWidth(SIDEBAR_W)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._items: dict[str, NavItem] = {}
        self._current = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 14, 10, 12)
        layout.setSpacing(0)

        brand = _BrandBlock(self)
        layout.addWidget(brand, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(8)

        self.account_tile = AccountTile(self)
        self.account_tile.clicked.connect(self._open_account)
        layout.addWidget(self.account_tile, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(6)
        layout.addWidget(Hairline(self))
        layout.addSpacing(8)

        self._scroll = ScrollArea(self)
        self._scroll.setStyleSheet("background: transparent; border: none;")
        self._nav_host = QWidget()
        self._nav_host.setStyleSheet("background: transparent;")
        self._nav_layout = QVBoxLayout(self._nav_host)
        self._nav_layout.setContentsMargins(0, 0, 0, 0)
        self._nav_layout.setSpacing(6)
        self._scroll.set_content(self._nav_host)
        layout.addWidget(self._scroll, 1)

        layout.addWidget(Hairline(self))
        layout.addSpacing(8)
        self._footer = _FooterLabel(self)
        layout.addWidget(self._footer, 0, Qt.AlignmentFlag.AlignHCenter)

        self.registry.on_changed(self.rebuild)
        self.rebuild()
        bus.subscribe(Topics.ACCOUNT_STATE, self._on_account_state)

    # -- 绘制 ---------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = pal()
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        paint.fill_round(p, rect, colors.sidebar, SIDEBAR_RADIUS)
        paint.stroke_round(p, rect, colors.rule, SIDEBAR_RADIUS, 1.0)
        p.end()

    # -- 导航 ---------------------------------------------------------------
    def _sections(self) -> list[tuple[str, list[type]]]:
        classes = self.registry.classes()
        core = [c for c in classes if c.section == "core"]
        plugins = [c for c in classes if c.section == "plugin"]
        out: list[tuple[str, list[type]]] = []
        if core:
            out.append((t("shell.sidebar.core", "核心"), core))
        if plugins:
            out.append((t("shell.sidebar.plugins", "插件"), plugins))
        return out

    def rebuild(self) -> None:
        # 清空旧按钮
        while self._nav_layout.count():
            item = self._nav_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._items.clear()
        first_key = ""
        for section_title, classes in self._sections():
            label = _SectionLabel(section_title, self)
            self._nav_layout.addWidget(label)
            for view_cls in classes:
                label = view_cls.sidebar_label or view_cls.title or view_cls.key
                item = NavItem(view_cls.icon, label, self._nav_host)
                item.setChecked(view_cls.key == self._current)
                item.clicked.connect(lambda _checked=False, key=view_cls.key: self._select(key))
                self._nav_layout.addWidget(item)
                self._items[view_cls.key] = item
                if not first_key:
                    first_key = view_cls.key
            self._nav_layout.addSpacing(6)
        self._nav_layout.addStretch(1)
        self._footer.set_text(t("shell.footer.version", "v{version}", version=__version__))

    def _select(self, key: str) -> None:
        self.set_current(key)
        self.view_selected.emit(key)

    def set_current(self, key: str) -> None:
        self._current = key
        for item_key, item in self._items.items():
            item.setChecked(item_key == key)

    def _open_account(self) -> None:
        self._select("account")

    # -- 账号状态 -----------------------------------------------------------
    def _on_account_state(self, **payload) -> None:
        nickname = str(payload.get("nickname") or t("shell.sidebar.not_logged_in", "未登录"))
        online = bool(payload.get("online"))
        avatar = payload.get("avatar_path")
        self.account_tile.set_account(nickname, online=online, avatar_path=avatar)


class _BrandBlock(EWidget):
    """侧边栏字标：应用图标 + 宽体 ENDBOX。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(SIDEBAR_W - 20, 30)
        from endbox.ui.branding import logo_pixmap

        self._logo = logo_pixmap(24)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        icon_rect = QRectF(0, (self.height() - 22) / 2, 22, 22)
        p.save()
        p.setClipPath(paint.cut_rect(icon_rect, 5, "br"))
        p.drawPixmap(icon_rect, self._logo, QRectF(self._logo.rect()))
        p.restore()
        paint.stroke_cut(p, icon_rect.adjusted(0.5, 0.5, -0.5, -0.5), colors.rule, 5, "br", 1.0)
        paint.draw_text(
            p, QRectF(28, 0, self.width() - 28, self.height()), "ENDBOX",
            F.font(9, weight=QFont.Weight.Bold, wide=True, letter_spacing=0.6),
            colors.ink2,
        )
        p.end()


class _SectionLabel(EWidget):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._text = text
        self.setFixedHeight(18)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        colors = pal()
        paint.draw_text(
            p, QRectF(3, 0, self.width() - 6, self.height()),
            f"// {self._text}", F.eyebrow_font(9), colors.ink2,
        )
        p.end()


class _FooterLabel(EWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = "v0.1.0"
        self.setFixedSize(SIDEBAR_W - 20, 16)

    def set_text(self, text: str) -> None:
        self._text = text
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        colors = pal()
        paint.draw_text(
            p, QRectF(0, 0, self.width(), self.height()), self._text,
            F.field_font(9), colors.ink2, Qt.AlignmentFlag.AlignCenter,
        )
        paint.tri_marker(p, self.width() / 2, 3, 3, colors.signal, up=False)
        p.end()
