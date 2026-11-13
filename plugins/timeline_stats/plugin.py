# -*- coding: utf-8 -*-
"""示例插件「排轴统计」。

演示：申请 ``timeline:read`` 权限、读 ``ctx.timeline_snapshot()``、订阅白名单事件
``timeline.state``，并用真实数字展示统计。排轴服务缺失时优雅降级（显示「—」+ 说明）。
"""
from __future__ import annotations

import time

from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from endbox.plugins.api import EndBoxPlugin
from endbox.core.text import t
from endbox.ui.kit import (
    FieldText,
    Hairline,
    InfoFlag,
    Label,
    NumberStat,
    Panel,
    ScrollArea,
)
from endbox.ui.views.base import BaseView

KIND_LABELS = {
    "skill": t("plugins.timeline_stats.kind.skill"),
    "link": t("plugins.timeline_stats.kind.link"),
    "ult": t("plugins.timeline_stats.kind.ult"),
}


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


def _merged_coverage(intervals: list[tuple[float, float]], duration: float) -> float:
    """把 Buff 区间合并后求覆盖秒数（真实计算，不估算）。"""
    if duration <= 0:
        return 0.0
    spans: list[tuple[float, float]] = []
    for start, end in intervals:
        start = max(0.0, min(duration, float(start)))
        end = max(0.0, min(duration, float(end)))
        if end > start:
            spans.append((start, end))
    if not spans:
        return 0.0
    spans.sort()
    covered = 0.0
    current_start, current_end = spans[0]
    for start, end in spans[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            covered += current_end - current_start
            current_start, current_end = start, end
    covered += current_end - current_start
    return covered


class TimelineStatsView(BaseView):
    key = "timeline_stats"
    eyebrow = "TIMELINE STATS"
    title = t("plugins.timeline_stats.title")
    icon = "timeline"
    order = 20
    sidebar_label = t("plugins.timeline_stats.sidebar_label")
    description = t("plugins.timeline_stats.description")

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

        self._flag_info = InfoFlag(t("plugins.timeline_stats.loading"), kind="info")
        self._flag_warn = InfoFlag("", kind="warn")
        self._flag_warn.hide()
        layout.addWidget(self._flag_info)
        layout.addWidget(self._flag_warn)

        overview = Panel(t("plugins.timeline_stats.panel.overview"), "OVERVIEW", padding=16, spacing=10)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(18)
        for key, label, unit in (
            ("duration", t("plugins.timeline_stats.stat.duration"), "s"),
            ("events", t("plugins.timeline_stats.stat.events"), ""),
            ("team", t("plugins.timeline_stats.stat.team"), ""),
            ("coverage", t("plugins.timeline_stats.stat.coverage"), "%"),
        ):
            stat = NumberStat(label, "—", unit=unit)
            stat.setMinimumWidth(140)
            row_layout.addWidget(stat, 1)
            self._stats[key] = stat
        overview.add(row)
        self._meta = FieldText(["—"])
        overview.add(self._meta)
        layout.addWidget(overview)

        operators = Panel(t("plugins.timeline_stats.panel.operators"), "OPERATORS", padding=16, spacing=8)
        self._op_host = QWidget()
        self._op_layout = QVBoxLayout(self._op_host)
        self._op_layout.setContentsMargins(0, 0, 0, 0)
        self._op_layout.setSpacing(6)
        operators.add(self._op_host)
        layout.addWidget(operators)

        kinds = Panel(t("plugins.timeline_stats.panel.kinds"), "EVENTS", padding=16, spacing=8)
        self._kind_host = QWidget()
        self._kind_layout = QVBoxLayout(self._kind_host)
        self._kind_layout.setContentsMargins(0, 0, 0, 0)
        self._kind_layout.setSpacing(6)
        kinds.add(self._kind_host)
        layout.addWidget(kinds)

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
            self._show_warn(t("plugins.timeline_stats.error.plugin_missing"))
            self._reset("—")
            return
        try:
            snapshot = plugin.ctx.timeline_snapshot()
        except PermissionError as exc:
            self._show_warn(t("plugins.timeline_stats.error.permission", error=exc))
            self._reset("—")
            return
        except Exception as exc:  # noqa: BLE001
            self._show_warn(
                t("plugins.timeline_stats.error.read", error=f"{type(exc).__name__}: {exc}")
            )
            self._reset("—")
            return

        if not snapshot:
            self._show_warn(
                t("plugins.timeline_stats.warn.service")
            )
            self._reset("—")
            return

        team = snapshot.get("team") or []
        events = [event for event in (snapshot.get("events") or []) if isinstance(event, dict)]
        buffs = [buff for buff in (snapshot.get("buffs") or []) if isinstance(buff, dict)]
        try:
            duration = float(snapshot.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0

        self._show_info(
            t(
                "plugins.timeline_stats.info.loaded",
                team=len(team),
                events=len(events),
                duration=f"{duration:.1f}",
            )
        )

        covered = _merged_coverage(
            [
                (float(buff.get("start", 0.0)), float(buff.get("end", 0.0)))
                for buff in buffs
                if buff.get("start") is not None and buff.get("end") is not None
            ],
            duration,
        )
        coverage = (covered / duration * 100.0) if duration > 0 else 0.0

        self._stats["duration"].set_value(f"{duration:.1f}")
        self._stats["events"].set_value(str(len(events)))
        self._stats["team"].set_value(str(len(team)))
        self._stats["coverage"].set_value(f"{coverage:.1f}")

        last_state = (
            time.strftime("%H:%M:%S", time.localtime(plugin.last_state_at))
            if getattr(plugin, "last_state_at", 0)
            else t("plugins.timeline_stats.meta.not_received")
        )
        self._meta.set_fields(
            [
                t("plugins.timeline_stats.meta.snapshot", time=time.strftime("%H:%M:%S")),
                t("plugins.timeline_stats.meta.last_state", time=last_state),
                t("plugins.timeline_stats.meta.buff", count=len(buffs), seconds=f"{covered:.1f}"),
                t("plugins.timeline_stats.meta.playing_yes")
                if snapshot.get("playing")
                else t("plugins.timeline_stats.meta.playing_no"),
            ]
        )

        self._fill_operators(team, events)
        self._fill_kinds(events)

    def _fill_operators(self, team: list, events: list[dict]) -> None:
        _clear_layout(self._op_layout)
        names: list[str] = []
        for index, member in enumerate(team):
            name = str(
                (member or {}).get("name")
                or t("plugins.timeline_stats.row.empty_slot", index=index + 1)
            )
            names.append(name)
        if not team:
            self._op_layout.addWidget(Label(t("plugins.timeline_stats.team_empty"), "muted"))
            return

        counts = [[0, 0, 0] for _ in team]
        extra: dict[str, list[int]] = {}
        kind_order = ["skill", "link", "ult"]
        for event in events:
            try:
                operator = int(event.get("operator", -1))
            except (TypeError, ValueError):
                operator = -1
            kind = str(event.get("kind") or "")
            if 0 <= operator < len(team) and kind in kind_order:
                counts[operator][kind_order.index(kind)] += 1
            else:
                row = extra.setdefault(kind or t("plugins.timeline_stats.row.unknown"), [0, 0, 0])
                if kind in kind_order:
                    row[kind_order.index(kind)] += 1
                else:
                    row[0] += 1

        for index, name in enumerate(names):
            skill, link, ult = counts[index]
            total = skill + link + ult
            self._add_row(
                self._op_layout,
                name,
                t("plugins.timeline_stats.row.counts", skill=skill, link=link, ult=ult),
                t("plugins.timeline_stats.row.total", count=total),
            )
        for kind, values in extra.items():
            self._add_row(
                self._op_layout,
                t("plugins.timeline_stats.row.unassigned", kind=kind),
                t(
                    "plugins.timeline_stats.row.counts",
                    skill=values[0],
                    link=values[1],
                    ult=values[2],
                ),
                t("plugins.timeline_stats.row.total", count=sum(values)),
            )

    def _fill_kinds(self, events: list[dict]) -> None:
        _clear_layout(self._kind_layout)
        totals = {kind: {"count": 0, "duration": 0.0} for kind in KIND_LABELS}
        unknown = 0
        for event in events:
            kind = str(event.get("kind") or "")
            try:
                duration = float(event.get("duration") or 0.0)
            except (TypeError, ValueError):
                duration = 0.0
            if kind in totals:
                totals[kind]["count"] += 1
                totals[kind]["duration"] += duration
            else:
                unknown += 1
        for kind, label in KIND_LABELS.items():
            data = totals[kind]
            self._add_row(
                self._kind_layout,
                label,
                t(
                    "plugins.timeline_stats.row.kind_counts",
                    count=data["count"],
                    duration=f"{data['duration']:.1f}",
                ),
                t("plugins.timeline_stats.row.average", average=f"{data['duration'] / data['count']:.2f}")
                if data["count"]
                else t("plugins.timeline_stats.row.average_none"),
            )
        if unknown:
            self._add_row(
                self._kind_layout,
                t("plugins.timeline_stats.row.unknown_kind"),
                t("plugins.timeline_stats.row.kind_counts", count=unknown, duration="—"),
                "—",
            )

    @staticmethod
    def _add_row(layout: QVBoxLayout, left: str, middle: str, right: str) -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        left_label = Label(left, "body")
        left_label.setMinimumWidth(110)
        row_layout.addWidget(left_label, 0)
        row_layout.addWidget(Label(middle, "field"), 1)
        row_layout.addWidget(Label(right, "small"), 0)
        layout.addWidget(row)
        layout.addWidget(Hairline())

    # -- 状态 ---------------------------------------------------------------
    def _show_info(self, text: str | None = None) -> None:
        if text:
            self._flag_info.set_text(text)
        self._flag_info.show()
        self._flag_warn.hide()

    def _show_warn(self, text: str) -> None:
        self._flag_warn.set_text(text)
        self._flag_warn.show()
        self._flag_info.hide()

    def _reset(self, text: str) -> None:
        for stat in self._stats.values():
            stat.set_value(text)
        self._meta.set_fields(
            [
                t("plugins.timeline_stats.reset.no_snapshot"),
                t("plugins.timeline_stats.reset.last_state"),
                t("plugins.timeline_stats.reset.buff"),
            ]
        )
        _clear_layout(self._op_layout)
        _clear_layout(self._kind_layout)
        self._op_layout.addWidget(Label(t("plugins.timeline_stats.empty.data_unavailable"), "muted"))
        self._kind_layout.addWidget(Label(t("plugins.timeline_stats.empty.data_unavailable"), "muted"))


class Plugin(EndBoxPlugin):
    """订阅 timeline.state（白名单事件）记录最近一次状态变化时间。"""

    def on_load(self) -> None:
        self.last_state_at = 0.0
        TimelineStatsView.PLUGIN = self
        self.ctx.event_bus.subscribe("timeline.state", self._on_timeline_state)
        self.ctx.register_view(
            TimelineStatsView,
            sidebar_icon="timeline",
            sidebar_label=t("plugins.timeline_stats.sidebar_label"),
            order=20,
        )
        self.ctx.logger.info("timeline_stats ready")

    def on_enable(self) -> None:
        self.ctx.logger.info("timeline_stats enabled")

    def on_unload(self) -> None:
        TimelineStatsView.PLUGIN = None

    def _on_timeline_state(self, **_payload) -> None:
        self.last_state_at = time.time()
