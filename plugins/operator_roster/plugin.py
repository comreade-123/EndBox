# -*- coding: utf-8 -*-
"""示例插件「干员练度」。

演示：申请 ``account:read`` 权限，读账号快照里的角色列表，统计干员数量、
平均等级、最高等级和服务器，再逐行展示。没有数据时显示「—」并说明原因，
不编造任何数字。
"""
from __future__ import annotations

import time

from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from endbox.core.text import t
from endbox.plugins.api import EndBoxPlugin
from endbox.ui.kit import (
    CutButton,
    EmptyState,
    FieldText,
    Hairline,
    InfoFlag,
    Label,
    NumberStat,
    Panel,
    ScrollArea,
)
from endbox.ui.views.base import BaseView


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


class OperatorRosterView(BaseView):
    key = "operator_roster"
    eyebrow = "OPERATOR ROSTER"
    title = t("plugins.operator_roster.title")
    icon = "user"
    order = 25
    sidebar_label = t("plugins.operator_roster.sidebar_label")
    description = t("plugins.operator_roster.description")

    #: 由插件在 on_load() 里注入
    PLUGIN: "Plugin | None" = None

    def build(self) -> None:
        self._stats: dict[str, NumberStat] = {}

        scroll = ScrollArea(self)
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._flag = InfoFlag(t("plugins.operator_roster.loading"), kind="info")
        layout.addWidget(self._flag)

        overview = Panel(
            t("plugins.operator_roster.panel.overview"), "OVERVIEW", padding=16, spacing=10
        )
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(18)
        for key, label in (
            ("operators", t("plugins.operator_roster.stat.operators")),
            ("average", t("plugins.operator_roster.stat.average")),
            ("max", t("plugins.operator_roster.stat.max")),
            ("server", t("plugins.operator_roster.stat.server")),
        ):
            stat = NumberStat(label, "—")
            stat.setMinimumWidth(130)
            row_layout.addWidget(stat, 1)
            self._stats[key] = stat
        overview.add(row)
        self._meta = FieldText(["—"])
        overview.add(self._meta)
        refresh_btn = CutButton(
            t("plugins.operator_roster.refresh"), "ghost", icon="refresh", compact=True
        )
        refresh_btn.clicked.connect(self.refresh)
        overview.add_header_action(refresh_btn)
        layout.addWidget(overview)

        roster = Panel(
            t("plugins.operator_roster.panel.roster"), "ROSTER", padding=16, spacing=8
        )
        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(0)
        roster.add(self._rows_host)
        self._empty = EmptyState(
            t("plugins.operator_roster.empty.title"),
            t("plugins.operator_roster.empty.desc"),
        )
        self._empty.setFixedHeight(126)
        roster.add(self._empty)
        layout.addWidget(roster)
        layout.addStretch(1)
        scroll.set_content(host)
        self._root.addWidget(scroll)
        self.refresh()

    # -- 数据 ---------------------------------------------------------------
    def refresh(self) -> None:
        if not hasattr(self, "_stats"):
            return
        plugin = self.PLUGIN
        if plugin is None:
            self._show_warn(t("plugins.operator_roster.warn.plugin_missing"))
            self._reset("—")
            return
        try:
            snapshot = plugin.ctx.account_snapshot()
        except PermissionError as exc:
            self._show_warn(t("plugins.operator_roster.warn.permission", error=exc))
            self._reset("—")
            return
        except Exception as exc:  # noqa: BLE001
            self._show_warn(
                t("plugins.operator_roster.warn.read", error=f"{type(exc).__name__}: {exc}")
            )
            self._reset("—")
            return

        roles = [
            role
            for role in (snapshot.get("roles") or [])
            if isinstance(role, dict) and str(role.get("name") or "").strip()
        ]
        if not snapshot:
            self._show_warn(t("plugins.operator_roster.warn.service"))
            self._reset("—")
            return
        if not roles:
            self._show_warn(t("plugins.operator_roster.empty.no_data"))
            self._reset("—")
            self._meta.set_fields(
                [
                    t("plugins.operator_roster.meta.snapshot", time=time.strftime("%H:%M:%S")),
                    t("plugins.operator_roster.meta.source_demo")
                    if snapshot.get("demo")
                    else t("plugins.operator_roster.meta.source_account"),
                ]
            )
            return

        levels: list[int] = []
        for role in roles:
            try:
                levels.append(int(role.get("level")))
            except (TypeError, ValueError):
                continue
        servers = {
            str(role.get("server")) for role in roles if str(role.get("server") or "").strip()
        }
        average = sum(levels) / len(levels) if levels else None
        self._show_info(
            t("plugins.operator_roster.info.loaded", count=len(roles))
        )
        self._stats["operators"].set_value(str(len(roles)))
        self._stats["average"].set_value(f"{average:.1f}" if average is not None else "—")
        self._stats["max"].set_value(str(max(levels)) if levels else "—")
        self._stats["server"].set_value(str(len(servers)) if servers else "—")
        self._meta.set_fields(
            [
                t("plugins.operator_roster.meta.snapshot", time=time.strftime("%H:%M:%S")),
                t("plugins.operator_roster.meta.source_demo")
                if snapshot.get("demo")
                else t("plugins.operator_roster.meta.source_account"),
                t("plugins.operator_roster.meta.levels", known=len(levels), total=len(roles)),
            ]
        )
        self._fill_rows(roles)

    def _fill_rows(self, roles: list[dict]) -> None:
        _clear_layout(self._rows_layout)
        ordered = sorted(
            roles,
            key=lambda role: (
                -_level_value(role),
                str(role.get("name") or "").lower(),
            ),
        )
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        for label, width in (
            (t("plugins.operator_roster.table.name"), 150),
            (t("plugins.operator_roster.table.level"), 70),
            (t("plugins.operator_roster.table.server"), 130),
            (t("plugins.operator_roster.table.class"), 90),
            (t("plugins.operator_roster.table.element"), 90),
        ):
            cell = Label(label, "eyebrow")
            if width:
                cell.setMinimumWidth(width)
            header_layout.addWidget(cell, 1 if label == t("plugins.operator_roster.table.name") else 0)
        self._rows_layout.addWidget(header)
        for role in ordered:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 5, 0, 5)
            row_layout.setSpacing(8)
            name = Label(str(role.get("name") or "—"), "body")
            name.setMinimumWidth(150)
            row_layout.addWidget(name, 1)
            level = Label(
                str(role.get("level")) if role.get("level") is not None else "—", "field"
            )
            level.setMinimumWidth(70)
            row_layout.addWidget(level, 0)
            server = Label(str(role.get("server") or "—"), "small")
            server.setMinimumWidth(130)
            row_layout.addWidget(server, 0)
            klass = Label(str(role.get("class") or "—"), "small")
            klass.setMinimumWidth(90)
            row_layout.addWidget(klass, 0)
            element = Label(str(role.get("element") or "—"), "small")
            element.setMinimumWidth(90)
            row_layout.addWidget(element, 0)
            self._rows_layout.addWidget(row)
            self._rows_layout.addWidget(Hairline())
        self._rows_host.show()
        self._empty.hide()

    # -- 状态 ---------------------------------------------------------------
    def _show_info(self, text: str | None = None) -> None:
        self._flag._kind = "info"
        if text:
            self._flag.set_text(text)
        self._flag.show()

    def _show_warn(self, text: str) -> None:
        self._flag._kind = "warn"
        self._flag.set_text(text)
        self._flag.show()

    def _reset(self, text: str) -> None:
        for stat in self._stats.values():
            stat.set_value(text)
        _clear_layout(self._rows_layout)
        self._rows_host.hide()
        self._empty.show()
        self._meta.set_fields([t("plugins.operator_roster.meta.unavailable")])


def _level_value(role: dict) -> int:
    try:
        return int(role.get("level"))
    except (TypeError, ValueError):
        return -1


class Plugin(EndBoxPlugin):
    def on_load(self) -> None:
        OperatorRosterView.PLUGIN = self
        self.ctx.register_view(
            OperatorRosterView,
            sidebar_icon="user",
            sidebar_label=t("plugins.operator_roster.sidebar_label"),
            order=25,
        )
        self.ctx.logger.info("operator_roster ready")

    def on_unload(self) -> None:
        OperatorRosterView.PLUGIN = None
