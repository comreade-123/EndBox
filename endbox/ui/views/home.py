# -*- coding: utf-8 -*-
"""首页 / 启动问候：首次启动、还没点开任何工具时看到的那一屏。"""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QRectF, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QPainter
from PyQt6.QtWidgets import QAbstractButton, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from endbox.app_context import AppContext
from endbox.core.event_bus import Topics, bus
from endbox.core.greeting import time_fields, welcome_greeting, welcome_subline
from endbox.core.text import t
from endbox.theme import fonts as F
from endbox.theme import paint
from endbox.theme.tokens import CUT_SMALL
from endbox.ui.icons import paint_icon
from endbox.ui.kit import (
    CutButton,
    FieldText,
    Hairline,
    InfoFlag,
    Label,
    NumberStat,
    Panel,
    ScrollArea,
    SectionHead,
)
from endbox.ui.kit.base import EWidget, pal
from endbox.ui.views.base import BaseView


class GreetingBlock(EWidget):
    """大问候语 + 副标题（按本地时间变化，名字来自绑定账号）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._greeting = ""
        self._subline = ""
        self.setMinimumHeight(118)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._timer = QTimer(self)
        self._timer.setInterval(30_000)
        self._timer.timeout.connect(self.refresh_text)
        self._timer.start()

    def set_text(self, greeting: str, subline: str) -> None:
        if (greeting, subline) != (self._greeting, self._subline):
            self._greeting = greeting
            self._subline = subline
            self.update()

    def refresh_text(self) -> None:
        from endbox.core.event_bus import bus
        from endbox.services import services
        from endbox.core.greeting import welcome_greeting, welcome_subline

        account = services.account
        state = getattr(account, "state", None) if account is not None else None
        self.set_text(welcome_greeting(state), welcome_subline())

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        paint.draw_text(
            p, QRectF(0, 0, self.width(), 14), "// " + t("home.eyebrow", "WELCOME"),
            F.eyebrow_font(11), colors.ink2,
        )
        font = F.display_font(40)
        paint.draw_text(p, QRectF(0, 18, self.width(), 52), self._greeting, font, colors.ink, elide=True)
        paint.draw_text(
            p, QRectF(0, 76, self.width(), 28), self._subline,
            F.font(18, weight=QFont.Weight.DemiBold, cjk_first=True), colors.ink2, elide=True,
        )
        p.end()


class ModuleRow(QAbstractButton):
    """模块入口行：图标 + 中文名 + 真实状态 + 箭头。按下时左边落下强调色条。"""

    def __init__(self, icon: str, eyebrow: str, title: str, status: str, key: str, parent=None):
        super().__init__(parent)
        self._icon = icon
        self._eyebrow = eyebrow
        self._title = title
        self._status = status
        self.target_key = key
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(58)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(f"{title} · {status}")

    def set_status(self, status: str) -> None:
        if status != self._status:
            self._status = status
            self.setToolTip(f"{self._title} · {status}")
            self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        colors = pal()
        shift = 6 if self._hover else 0
        if self._hover:
            p.fillRect(QRectF(0, 2, 3, self.height() - 4), colors.signal)
        rect = QRectF(shift, 0, self.width() - shift, self.height())
        paint_icon(
            p, self._icon, QRectF(rect.x() + 12, rect.y() + (rect.height() - 18) / 2, 18, 18),
            colors.ink if self._hover else colors.ink2, stroke=1.6,
        )
        paint.draw_text(
            p, QRectF(rect.x() + 44, rect.y() + 9, rect.width() - 190, 24), self._title,
            F.font(15, weight=QFont.Weight.Bold, cjk_first=True), colors.ink, elide=True,
        )
        paint.draw_text(
            p, QRectF(rect.x() + 44, rect.y() + 31, rect.width() - 190, 16), self._status,
            F.font(11, cjk_first=True), colors.ink2, elide=True,
        )
        paint.draw_text(
            p, QRectF(rect.right() - 128, rect.y() + 9, 96, 16),
            f"// {self._eyebrow}", F.field_font(9), colors.ink2,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, elide=True,
        )
        paint.draw_text(
            p, QRectF(rect.right() - 26, rect.y() + 6, 26, 30), "›",
            F.font(20, wide=True), colors.ink2,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        paint.hairline(p, 0, self.height() - 1, self.width(), self.height() - 1, colors.rule)
        if self.hasFocus():
            paint.paint_focus(p, QRectF(1.5, 1.5, self.width() - 3, self.height() - 4), colors.trace)
        p.end()


class HomeView(BaseView):
    key = "home"
    eyebrow = "HOME"
    title = "终末地工具箱"
    icon = "grid"
    order = 5
    sidebar_label = "首页"

    def build(self) -> None:
        self._account_state: dict | None = None
        scroll = ScrollArea(self)
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        top = QHBoxLayout()
        top.setSpacing(28)
        left = QVBoxLayout()
        left.setSpacing(12)
        self._greeting = GreetingBlock(host)
        left.addWidget(self._greeting)
        self._fields = FieldText(time_fields())
        left.addWidget(self._fields)
        self._account_hint = Label(t("home.bind.hint_unbound", ""), "muted", wrap=True)
        left.addWidget(self._account_hint)
        left.addStretch(1)
        top.addLayout(left, 3)

        self._modules_panel = Panel(padding=0, spacing=0)
        head_host = QWidget()
        head_layout = QVBoxLayout(head_host)
        head_layout.setContentsMargins(16, 14, 16, 6)
        head_layout.setSpacing(0)
        head_layout.addWidget(SectionHead(t("home.modules.eyebrow", "MODULES"), t("home.modules.title", "模块")))
        self._modules_panel.add(head_host)
        self._rows: dict[str, ModuleRow] = {}
        specs = [
            ("account", "account", "home.modules.eyebrow_account", "home.modules.account"),
            ("timeline", "timeline", "home.modules.eyebrow_timeline", "home.modules.timeline"),
            ("hud", "hud", "home.modules.eyebrow_hud", "home.modules.hud"),
            ("calc325", "calc", "home.modules.eyebrow_calc", "home.modules.calc"),
        ]
        for key, icon, eyebrow_key, title_key in specs:
            row = ModuleRow(
                icon, t(eyebrow_key), t(title_key), t("common.placeholder", "—"), key, self._modules_panel
            )
            row.clicked.connect(lambda _checked=False, k=key: self.ctx.open_view(k))
            self._modules_panel.add(row)
            self._rows[key] = row
        top.addWidget(self._modules_panel, 2)
        layout.addLayout(top, 1)

        layout.addWidget(Hairline(host))
        stats = QHBoxLayout()
        stats.setSpacing(28)
        self._stat_events = NumberStat(t("home.stats.events", "排轴事件"), t("common.placeholder", "—"), unit=t("home.stats.unit_events", "条"))
        self._stat_team = NumberStat(t("home.stats.team", "队伍人数"), t("common.placeholder", "—"), unit=t("home.stats.unit_people", "人"))
        self._stat_roles = NumberStat(t("home.stats.roles", "绑定角色"), t("common.placeholder", "—"), unit=t("home.stats.unit_roles", "个"))
        stats.addWidget(self._stat_events)
        stats.addWidget(Hairline(host, horizontal=False))
        stats.addWidget(self._stat_team)
        stats.addWidget(Hairline(host, horizontal=False))
        stats.addWidget(self._stat_roles)
        stats.addStretch(1)
        layout.addLayout(stats)

        layout.addSpacing(4)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        btn_timeline = CutButton(t("home.actions.start_timeline", "开始排轴"), "primary", icon="timeline")
        btn_timeline.clicked.connect(lambda: self.ctx.open_view("timeline"))
        btn_hud = CutButton(t("home.actions.hud_settings", "打开 HUD 设置"), "default", icon="hud")
        btn_hud.clicked.connect(lambda: self.ctx.open_view("hud"))
        btn_calc = CutButton(t("home.actions.calc", "生成 325 算式"), "ghost", icon="calc")
        btn_calc.clicked.connect(lambda: self.ctx.open_view("calc325"))
        btn_account = CutButton(t("home.actions.account", "账号中心"), "ghost", icon="account")
        btn_account.clicked.connect(lambda: self.ctx.open_view("account"))
        for button in (btn_timeline, btn_hud, btn_calc, btn_account):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self._bind_flag = InfoFlag(t("home.bind.flag_unbound", ""), kind="info")
        layout.addWidget(self._bind_flag)
        layout.addStretch(1)
        from endbox import __author__

        layout.addWidget(Label(t("home.author", "EndBox · 作者 {author}", author=__author__), "small"))

        scroll.set_content(host)
        self._root.addWidget(scroll)
        bus.subscribe(Topics.ACCOUNT_STATE, self._on_account_state)
        self._greeting.refresh_text()
        self._fields.set_fields(time_fields())

    # -- 数据 ---------------------------------------------------------------
    def _on_account_state(self, **payload) -> None:
        self._account_state = dict(payload)
        self._greeting.refresh_text()
        self._refresh_hint()

    def _refresh_hint(self) -> None:
        bound = bool(self._account_state and self._account_state.get("online"))
        if bound:
            provider = {
                "skland": t("home.bind.provider_skland", "森空岛"),
                "bilibili": t("home.bind.provider_bilibili", "B站账号"),
            }.get(str(self._account_state.get("provider") or ""), t("home.bind.provider_fallback", "账号"))
            name = self._account_state.get("nickname") or t("common.placeholder", "—")
            self._account_hint.setText(t("home.bind.hint_bound", "已绑定{provider}：{name}。", provider=provider, name=name))
            self._bind_flag.set_text(t("home.bind.flag_bound", "已绑定{provider}，首页问候使用该账号昵称。", provider=provider))
        else:
            self._account_hint.setText(t("home.bind.hint_unbound", "账号未绑定：前往账号中心绑定森空岛或 B 站账号。"))
            self._bind_flag.set_text(t("home.bind.flag_unbound", "账号未绑定：绑定后首页会叫你的名字，未绑定时显示「管理员」。"))

    def refresh(self) -> None:
        self._greeting.refresh_text()
        self._fields.set_fields(time_fields())
        self._refresh_hint()
        self._refresh_status()
        self._refresh_stats()

    def _refresh_status(self) -> None:
        services = self.ctx.services
        account = services.account
        timeline = services.timeline
        hud = services.hud

        account_status = t("home.modules.status.unloaded", "未载入")
        if account is not None:
            state = getattr(account, "state", None) or {}
            if state.get("demo"):
                account_status = t("home.modules.status.demo", "演示数据")
            elif state.get("online"):
                account_status = str(state.get("nickname") or t("home.modules.status.bound", "已绑定 {name}", name=""))
                account_status = account_status.replace("已绑定 ", t("home.modules.status.bound", "已绑定 {name}", name="")).strip()
            else:
                account_status = t("home.modules.status.unbound", "未绑定")
        self._rows["account"].set_status(account_status)

        timeline_status = t("home.modules.status.unloaded", "未载入")
        if timeline is not None:
            try:
                snapshot = timeline.snapshot()
                events = len(snapshot.get("events") or [])
                team = len(snapshot.get("team") or [])
                timeline_status = t("home.modules.status.timeline", "{events} 事件 · {team} 人队伍", events=events, team=team)
            except Exception:  # noqa: BLE001
                timeline_status = t("home.modules.status.load_failed", "读取失败")
        self._rows["timeline"].set_status(timeline_status)

        hud_status = t("home.modules.status.unloaded", "未载入")
        if hud is not None:
            hud_status = t("home.modules.status.hud_on", "已开启") if getattr(hud, "enabled", False) else t("home.modules.status.hud_off", "已关闭")
        self._rows["hud"].set_status(hud_status)

        seed = str(self.ctx.settings.get("calc325.last_seed", "") or "")
        self._rows["calc325"].set_status(
            t("home.modules.status.calc_seed", "上次种子 {seed}", seed=seed)
            if seed else t("home.modules.status.calc", "结果恒为 325")
        )

    def _refresh_stats(self) -> None:
        services = self.ctx.services
        try:
            snapshot = services.timeline.snapshot() if services.timeline is not None else None
        except Exception:  # noqa: BLE001
            snapshot = None
        if snapshot:
            self._stat_events.set_value(str(len(snapshot.get("events") or [])))
            self._stat_team.set_value(str(len(snapshot.get("team") or [])))
        else:
            self._stat_events.set_value("—")
            self._stat_team.set_value("—")
        account = services.account
        state = getattr(account, "state", None) if account is not None else None
        roles = (state or {}).get("roles") if state else None
        self._stat_roles.set_value(str(len(roles)) if roles else "—")

    # -- 顶栏 ---------------------------------------------------------------
    def topbar_fields(self) -> list[str]:
        from endbox import __version__

        now = datetime.now()
        return [
            now.strftime("%Y.%m.%d"),
            now.strftime("%H:%M"),
            f"v{__version__}",
        ]

    def on_show(self) -> None:
        self.refresh()
